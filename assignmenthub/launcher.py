"""Local supervisor: one public Caddy gateway and two private application servers.

Runtime records never contain passwords, session tokens, or signing secrets. A live
OS lock on the canonical storage root is the authority for concurrent launches.
PID records additionally verify process creation time and command before stopping.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LaunchError(RuntimeError):
    pass


def canonical_root(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class StorageLock:
    """Cross-platform nonblocking OS lock; automatically released on process death."""

    def __init__(self, root: Path, message: str = "같은 저장 폴더를 사용하는 서버가 실행 중입니다."):
        self.root = Path(canonical_root(root))
        self.file = None
        self.message = message

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = (self.root / ".run.lock").open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise LaunchError(self.message) from exc
        return self

    def __exit__(self, *_):
        if self.file is not None:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


def instance_lock_root(instance_id: str) -> Path:
    """Only coordination locks live outside the per-instance data directory."""
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "state")))
    return base / "AssignmentHub" / "instance-locks" / instance_id


def process_record(process: subprocess.Popen | psutil.Process) -> dict:
    proc = psutil.Process(process.pid)
    return {"pid": proc.pid, "created": proc.create_time(), "command": proc.cmdline()}


def owned_process(record: dict) -> psutil.Process | None:
    try:
        process = psutil.Process(int(record["pid"]))
        if abs(process.create_time() - float(record["created"])) > 0.02:
            return None
        if process.cmdline() != record["command"] or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except (KeyError, ValueError, TypeError, psutil.Error):
        return None


def terminate_owned(records: list[dict], timeout: float = 10) -> None:
    """Never search by process name and never terminate a recycled PID."""
    targets = {}
    for record in records:
        process = owned_process(record)
        if process:
            with contextlib.suppress(psutil.Error):
                for child in process.children(recursive=True):
                    targets[child.pid] = child
            targets[process.pid] = process
    for process in reversed(list(targets.values())):
        with contextlib.suppress(psutil.Error):
            process.terminate()
    _, alive = psutil.wait_procs(list(targets.values()), timeout=timeout)
    for process in alive:
        with contextlib.suppress(psutil.Error):
            process.kill()
    psutil.wait_procs(alive, timeout=3)


def local_probe_host(bind_host: str) -> str:
    return "::1" if ":" in bind_host else "127.0.0.1" if bind_host in ("0.0.0.0", "") else bind_host


def url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def check_public_port(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            if os.name == "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, port))
    except OSError as exc:
        raise LaunchError(f"접속 포트 {host}:{port}를 사용할 수 없습니다: {exc}") from exc


def internal_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def proxy_binary() -> Path:
    binary = PROJECT_ROOT / "tools" / ("caddy.exe" if os.name == "nt" else "caddy")
    if not binary.is_file():
        raise LaunchError("프록시가 설치되지 않았습니다. setup.bat 또는 python scripts/install_proxy.py를 실행하세요.")
    return binary


def caddy_config(config, api_port: int, ui_port: int) -> str:
    # An instance-specific URL path scopes Streamlit's fixed-name XSRF cookie.
    # Caddy's RE2 replacement retains all other cookie attributes. Bearer auth
    # and user state are separately scoped by the application instance.
    base = f"/ui/{config.instance_id}"
    tls = ""
    if config.secure_cookies:
        cert = Path(config.tls_cert_file).resolve().as_posix()
        key = Path(config.tls_key_file).resolve().as_posix()
        tls = f'    tls "{cert}" "{key}"\n'
    cookie_attributes = "; SameSite=Lax" + ("; Secure" if config.secure_cookies else "")
    return f"""{{
    admin off
    auto_https off
}}
:{config.port} {{
    bind {config.bind_host}
{tls}\
    @api path /api/* /upload /upload-static/*
    handle @api {{
        reverse_proxy 127.0.0.1:{api_port}
    }}
    @root path /
    redir @root {base}/ 302
    handle {{
        reverse_proxy 127.0.0.1:{ui_port} {{
            header_down Set-Cookie "(?i)(;[ ]*path=)/([; ]|$)" "${{1}}{base}/${{2}}"
            header_down Set-Cookie "(_streamlit_xsrf=.*)" "$1{cookie_attributes}"
        }}
    }}
}}
"""


def preflight(config) -> None:
    config.root.mkdir(parents=True, exist_ok=True)
    probe = config.root / (".write-check-" + secrets.token_hex(8))
    try:
        with probe.open("xb") as output:
            output.write(b"write-check")
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        raise LaunchError(f"저장 폴더에 쓸 수 없습니다: {config.root}: {exc}") from exc
    finally:
        probe.unlink(missing_ok=True)
    free = shutil.disk_usage(config.root).free
    if free < config.min_free_bytes:
        raise LaunchError(f"디스크 여유 공간 부족: {free}바이트, 최소 {config.min_free_bytes}바이트")
    check_public_port(config.bind_host, config.port)


def _environment(config_path: Path, api_port: int, launch_id: str, root: Path) -> dict:
    env = os.environ.copy()
    env.update(AH_CONFIG=str(config_path.resolve()), AH_API_URL=f"http://127.0.0.1:{api_port}",
               AH_LAUNCH_ID=launch_id, AH_STOP_FILE=str(root / f".api-stop-{launch_id}"),
               PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    return env


def _spawn(command: list[str], log: Path, env: dict) -> subprocess.Popen:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log.open("ab", buffering=0) as output:
        return subprocess.Popen(command, cwd=PROJECT_ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                creationflags=flags)


def listening_owned(process: subprocess.Popen, port: int) -> bool:
    """Avoid mistaking a race winner on an ephemeral port for our child."""
    try:
        parent = psutil.Process(process.pid)
        family = [parent, *parent.children(recursive=True)]
        return any(connection.status == psutil.CONN_LISTEN and connection.laddr.port == port
                   for member in family for connection in member.net_connections(kind="tcp"))
    except psutil.Error:
        return False


def wait_http(url: str, process: subprocess.Popen, timeout: float = 90) -> bool:
    until = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < until:
        if process.poll() is not None:
            return False
        try:
            with opener.open(url, timeout=1) as result:
                if result.status == 200 and listening_owned(process, urlsplit(url).port):
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(0.15)
    return False


def status(config) -> dict:
    state = read_json(config.root / "runtime.json")
    supervisor = owned_process(state.get("supervisor", {}))
    children_alive = all(owned_process(child) is not None for child in state.get("children", []))
    running = bool(supervisor and children_alive and state.get("stage") == "running"
                   and state.get("instance_id") == config.instance_id
                   and state.get("storage_root") == canonical_root(config.root))
    return {**state, "running": running, "instance_id": config.instance_id,
            "url": config.public_url, "storage_root": canonical_root(config.root)}


def supervise(config_path: Path, launch_id: str) -> int:
    from assignmenthub.config import Config
    from assignmenthub.service import Service

    config = Config.load(config_path)
    handshake = config.root / f".launch-{launch_id}.json"
    state_path = config.root / "runtime.json"
    children: list[subprocess.Popen] = []
    records: list[dict] = []
    descendants: list[dict] = []
    state = {"instance_id": config.instance_id, "launch_id": launch_id,
             "storage_root": canonical_root(config.root), "url": config.public_url,
             "supervisor": process_record(psutil.Process()), "children": records, "descendants": descendants, "stage": "starting"}
    stop_requested = False

    def request_stop(*_):
        nonlocal stop_requested
        stop_requested = True

    def remember_descendants(process):
        # Windows venv's redirector creates a second Python process. Remember
        # both identities so cleanup still works if that redirector alone dies.
        with contextlib.suppress(psutil.Error):
            for child in psutil.Process(process.pid).children(recursive=True):
                record = process_record(child)
                if record not in descendants:
                    descendants.append(record)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        with StorageLock(config.root), StorageLock(instance_lock_root(config.instance_id), "동일 instance_id의 서버가 실행 중입니다."):
            previous = read_json(state_path)
            if previous.get("storage_root") == canonical_root(config.root):
                terminate_owned(previous.get("children", []) + previous.get("descendants", []), timeout=2)
            preflight(config)
            # Store checks the immutable storage identity; initialize/recovery is
            # performed again by API lifespan after the private port is ready.
            service = Service(config)
            if hasattr(service, "store"):
                service.store.initialize()
            binary = proxy_binary()
            logs = config.root / "logs"
            logs.mkdir(exist_ok=True)
            write_json(state_path, state)
            try:
                api_port = ui_port = 0
                for attempt in range(3):
                    api_port = internal_port()
                    env = _environment(config_path, api_port, launch_id, config.root)
                    api = _spawn([sys.executable, "-m", "assignmenthub.runtime", "api", "--port", str(api_port)], logs / "api.log", env)
                    children.append(api)
                    records.append(process_record(api))
                    write_json(state_path, state)
                    if wait_http(f"http://127.0.0.1:{api_port}/api/health", api):
                        remember_descendants(api)
                        break
                    terminate_owned([records.pop()], timeout=1)
                    children.pop()
                else:
                    raise LaunchError("API 시작에 실패했습니다. logs/api.log를 확인하세요.")

                for attempt in range(3):
                    ui_port = internal_port()
                    command = [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "assignmenthub" / "ui.py"),
                               "--server.address=127.0.0.1", f"--server.port={ui_port}",
                               f"--server.baseUrlPath=ui/{config.instance_id}", "--server.headless=true",
                               "--server.fileWatcherType=none", "--server.maxUploadSize=5", "--server.enableCORS=true",
                               "--server.enableXsrfProtection=true", "--browser.gatherUsageStats=false",
                               "--client.toolbarMode=minimal", "--theme.primaryColor=#17684f",
                               "--theme.backgroundColor=#f5f7f3", "--theme.secondaryBackgroundColor=#eef3ec",
                               "--theme.textColor=#183e2e",
                               f"--browser.serverAddress={config.public_host}", f"--browser.serverPort={config.port}"]
                    # Let Streamlit generate a private per-process random secret;
                    # no signing secret is exposed in command arguments or logs.
                    ui = _spawn(command, logs / "streamlit.log", env)
                    children.append(ui)
                    records.append(process_record(ui))
                    write_json(state_path, state)
                    if wait_http(f"http://127.0.0.1:{ui_port}/ui/{config.instance_id}/_stcore/health", ui):
                        remember_descendants(ui)
                        break
                    terminate_owned([records.pop()], timeout=1)
                    children.pop()
                else:
                    raise LaunchError("Streamlit 시작에 실패했습니다. logs/streamlit.log를 확인하세요.")

                gateway = config.root / "Caddyfile"
                gateway.write_text(caddy_config(config, api_port, ui_port), encoding="utf-8")
                proxy_env = {**env, "XDG_CONFIG_HOME": str(config.root / "proxy-config"),
                             "XDG_DATA_HOME": str(config.root / "proxy-data")}
                proxy = _spawn([str(binary), "run", "--config", str(gateway), "--adapter", "caddyfile"], logs / "proxy.log", proxy_env)
                children.append(proxy)
                records.append(process_record(proxy))
                state.update(api_port=api_port, ui_port=ui_port)
                write_json(state_path, state)
                probe_host = url_host(local_probe_host(config.bind_host))
                # HTTPS readiness is checked via TCP because an operator's local
                # certificate may not be in Python's CA store; client trust is
                # separately verified during browser deployment acceptance.
                ready = wait_tcp(probe_host.strip("[]"), config.port, proxy) if config.secure_cookies else wait_http(f"http://{probe_host}:{config.port}/api/health", proxy)
                if not ready:
                    raise LaunchError("단일 포트 프록시 시작에 실패했습니다. logs/proxy.log를 확인하세요.")
                state["stage"] = "running"
                write_json(state_path, state)
                write_json(handshake, {"ok": True, "url": config.public_url})
                stop_file = config.root / f".stop-{launch_id}"
                while not stop_requested and not stop_file.exists():
                    if any(child.poll() is not None for child in children):
                        raise LaunchError("서비스 프로세스가 종료되어 인스턴스를 안전하게 중지합니다. logs를 확인하세요.")
                    time.sleep(0.25)
                stop_file.unlink(missing_ok=True)
            finally:
                # First stop ingress, then ask Uvicorn to drain active requests.
                if len(records) >= 3:
                    terminate_owned([records[-1]], timeout=2)
                api_stop = config.root / f".api-stop-{launch_id}"
                api_stop.touch()
                if children:
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        children[0].wait(timeout=15)
                terminate_owned(records + descendants)
                api_stop.unlink(missing_ok=True)
                state["stage"] = "stopped"
                write_json(state_path, state)
    except Exception as exc:
        if read_json(state_path).get("launch_id") == launch_id:
            state.update(stage="failed", error=str(exc))
            write_json(state_path, state)
        write_json(handshake, {"ok": False, "error": str(exc)})
        print(f"시작/운영 오류: {exc}", file=sys.stderr)
        return 1
    return 0


def wait_tcp(host: str, port: int, process: subprocess.Popen, timeout: float = 30) -> bool:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=1):
                if listening_owned(process, port):
                    return True
        except OSError:
            time.sleep(0.15)
    return False


def start(config_path: Path) -> dict:
    from assignmenthub.config import Config
    config = Config.load(config_path)
    existing = status(config)
    if existing["running"]:
        raise LaunchError(f"이미 실행 중입니다: {config.public_url}")
    config.root.mkdir(parents=True, exist_ok=True)
    launch_id = secrets.token_hex(16)
    handshake = config.root / f".launch-{launch_id}.json"
    logs = config.root / "logs"
    logs.mkdir(exist_ok=True)
    command = [sys.executable, "-m", "assignmenthub.cli", "run", "--config", str(config_path.resolve()), "--launch-id", launch_id]
    env = os.environ.copy()
    env.update(PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    with (logs / "launcher.log").open("ab", buffering=0) as output:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, cwd=PROJECT_ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, close_fds=True, **options)
    deadline = time.monotonic() + 660
    while time.monotonic() < deadline:
        answer = read_json(handshake)
        if answer:
            handshake.unlink(missing_ok=True)
            if answer.get("ok"):
                return status(config)
            raise LaunchError(answer.get("error", "시작에 실패했습니다."))
        if process.poll() is not None:
            raise LaunchError("실행 도구가 종료되었습니다. logs/launcher.log를 확인하세요.")
        time.sleep(0.15)
    # Only the supervisor from this launch is interrupted; it owns child cleanup.
    (config.root / f".stop-{launch_id}").touch()
    raise LaunchError("시작 시간 초과. 중지를 요청했습니다. 상태와 logs/launcher.log를 확인하세요.")


def stop(config) -> dict:
    state = read_json(config.root / "runtime.json")
    if state.get("storage_root") != canonical_root(config.root) or state.get("instance_id") != config.instance_id:
        return {"running": False, "message": "실행 기록이 없습니다."}
    supervisor = owned_process(state.get("supervisor", {}))
    if supervisor:
        (config.root / f".stop-{state['launch_id']}").touch()
        try:
            supervisor.wait(timeout=35)
        except psutil.TimeoutExpired:
            raise LaunchError("정상 종료 대기 중입니다. 상태와 로그를 확인한 뒤 다시 중지하세요.")
    else:
        with StorageLock(config.root):
            terminate_owned(state.get("children", []) + state.get("descendants", []))
            state["stage"] = "stopped"
            write_json(config.root / "runtime.json", state)
    return {"running": False, "message": "중지했습니다.", "url": config.public_url}
