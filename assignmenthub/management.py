"""Local operator workflows; no network-facing server-control endpoints."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation, localcontext
import ipaddress
import json
from pathlib import Path
import shutil
import socket
import urllib.request

import psutil

from assignmenthub.config import Config
from assignmenthub.launcher import (LaunchError, StorageLock, canonical_root, check_public_port,
                                    local_probe_host, owned_process, preflight, proxy_binary,
                                    status, url_host, write_json)

GIB = 1024**3


def gib_text(value: int) -> str:
    with localcontext() as ctx:
        ctx.prec = 50
        return format(Decimal(value) / GIB, "f").rstrip("0").rstrip(".") if value % GIB else str(value // GIB)


def gib_bytes(value: str, label: str, allow_zero: bool = False) -> int:
    try:
        with localcontext() as ctx:
            ctx.prec = 60
            amount = Decimal(value.strip()) * GIB
        if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
            raise ValueError
        if amount > 2**53 - 1 or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError(f"{label}: {'0 이상의' if allow_zero else '0보다 큰'} GiB 값을 입력하세요. 예: 2 또는 0.5") from None


def network_addresses() -> list[tuple[str, str]]:
    """Read local interfaces only; never query an external service or change routes."""
    addresses = []
    stats = psutil.net_if_stats()
    for name, values in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for item in values:
            if item.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(item.address)
            if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
                addresses.append((str(ip), name))
    return sorted(set(addresses), key=lambda value: (not ipaddress.ip_address(value[0]).is_private, value))


@dataclass
class Course:
    path: Path
    config: Config | None
    error: str = ""


class Catalog:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.registry = self.directory / ".manager-paths.json"

    def paths(self) -> list[Path]:
        paths = [p.resolve() for p in self.directory.glob("*.json") if not p.name.startswith(".")]
        if self.registry.exists():
            try:
                values = json.loads(self.registry.read_text(encoding="utf-8"))
                if not isinstance(values, list) or not all(isinstance(p, str) for p in values):
                    raise ValueError("목록 형식이 올바르지 않습니다.")
                paths.extend(Path(p).resolve() for p in values)
            except (ValueError, OSError) as exc:
                raise LaunchError(f"기존 설정 연결 목록을 읽지 못했습니다: {self.registry}\n{exc}") from exc
        return sorted(set(paths), key=lambda p: str(p).casefold())

    def courses(self) -> list[Course]:
        result = []
        for path in self.paths():
            try:
                result.append(Course(path, Config.load(path)))
            except (ValueError, OSError, TypeError, KeyError) as exc:
                result.append(Course(path, None, str(exc)))
        return result

    def check_unique(self, config: Config, exclude: Path | None = None):
        for course in self.courses():
            if not course.config or (exclude and course.path == exclude.resolve()):
                continue
            other = course.config
            if other.instance_id == config.instance_id:
                raise LaunchError(f"같은 과정 ID가 이미 등록되어 있습니다: {other.instance_id}")
            if canonical_root(other.root) == canonical_root(config.root):
                raise LaunchError(f"'{other.course_name}' 과정의 저장 폴더입니다. 별도 폴더를 선택하세요.")
            if other.port == config.port:
                raise LaunchError(f"포트 {config.port}는 '{other.course_name}' 과정에 지정되어 있습니다. 다른 포트를 입력하세요.")

    def add(self, path: Path) -> Config:
        path = path.resolve()
        config = Config.load(path)
        with StorageLock(self.directory):
            self.check_unique(config, path)
            values = self.paths()
            if path not in values:
                values.append(path)
            write_json(self.registry, [str(p) for p in values if p.parent != self.directory])
        return config

    def suggest(self) -> tuple[str, int]:
        configs = [c.config for c in self.courses() if c.config]
        ids, ports = {c.instance_id for c in configs}, {c.port for c in configs}
        number = 1
        while f"course_{number:02}" in ids:
            number += 1
        for port in range(8501, 65536):
            if port in ports:
                continue
            try:
                check_public_port("0.0.0.0", port)
                return f"course_{number:02}", port
            except LaunchError:
                continue
        raise LaunchError("사용 가능한 접속 포트를 찾지 못했습니다.")

    def create(self, config: Config, admin_id: str, password: str, confirm: str) -> Path:
        from assignmenthub.service import Service
        if not admin_id.strip() or len(admin_id.strip()) > 128:
            raise ValueError("관리자 ID를 1~128자로 입력하세요.")
        if password != confirm:
            raise ValueError("관리자 비밀번호 확인이 일치하지 않습니다.")
        if not 12 <= len(password) <= 128:
            raise ValueError("관리자 비밀번호를 12~128자로 입력하세요.")
        path = self.directory / f"{config.instance_id}.json"
        with StorageLock(self.directory, "다른 관리창에서 과정 설정을 변경 중입니다. 잠시 뒤 다시 시도하세요."):
            self.check_unique(config)
            if path.exists():
                raise LaunchError("설정 파일이 이미 있습니다. 기존 설정 추가를 사용하세요.")
            if config.root.exists() and any(p.name != ".run.lock" for p in config.root.iterdir()):
                raise LaunchError("새 과정은 비어 있는 저장 폴더가 필요합니다. 기존 과정은 '기존 설정 추가'로 연결하세요.")
            with StorageLock(config.root):
                preflight(config)
                Service(config).bootstrap_admin(admin_id.strip(), password)
                write_json(path, {**asdict(config), "storage_root": str(config.root)})
        return path

    def update(self, path: Path, **changes) -> Config:
        # Identity, storage root and TLS material stay under the existing config.
        allowed = {"course_name", "public_host", "port", "max_file_bytes", "user_quota_bytes",
                   "min_free_bytes", "max_files", "concurrent_uploads", "upload_ttl_hours"}
        if set(changes) - allowed:
            raise ValueError("관리창에서 변경할 수 없는 설정입니다.")
        with StorageLock(self.directory), StorageLock(Config.load(path).root):
            current = Config.load(path)
            updated = replace(current, **changes)
            self.check_unique(updated, path)
            preflight(updated)
            write_json(path, {**asdict(updated), "storage_root": str(updated.root)})
        return updated


def state_label(state: dict) -> str:
    if state.get("running"):
        return "실행 중"
    if state.get("stage") == "starting" and owned_process(state.get("supervisor", {})):
        return "시작 중"
    if state.get("stage") in ("failed", "running", "starting"):
        return "점검 필요"
    return "중지됨"


def diagnose(config: Config) -> list[tuple[str, str, str]]:
    findings = []
    current = status(config)
    findings.append(("실행 상태", state_label(current), current.get("error", "")))
    try:
        proxy_binary()
        findings.append(("실행 구성요소", "정상", "필요한 프록시가 설치되어 있습니다."))
    except LaunchError as exc:
        findings.append(("실행 구성요소", "조치 필요", str(exc)))
    root = config.root
    while not root.exists() and root != root.parent:
        root = root.parent
    free = shutil.disk_usage(root).free
    findings.append(("디스크 여유", "정상" if free >= config.min_free_bytes else "공간 부족",
                     f"여유 {free / GIB:,.2f} GiB / 최소 {config.min_free_bytes / GIB:,.2f} GiB"))
    marker = config.root / "instance.json"
    try:
        identity = json.loads(marker.read_text(encoding="utf-8"))
        matches = identity.get("instance_id") == config.instance_id
        findings.append(("저장 폴더", "정상" if matches else "조치 필요", str(config.root) if matches else "과정 ID와 저장 폴더의 식별 정보가 다릅니다."))
    except (OSError, ValueError):
        findings.append(("저장 폴더", "조치 필요", "과정 데이터가 없습니다. 설정과 백업 위치를 확인하세요."))
    if current.get("running"):
        host = config.public_host if config.secure_cookies else local_probe_host(config.bind_host)
        base = f"{'https' if config.secure_cookies else 'http'}://{url_host(host)}:{config.port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for name, suffix in (("업로드 API", "/api/health"), ("관리 화면", f"/ui/{config.instance_id}/_stcore/health")):
            try:
                with opener.open(base + suffix, timeout=3) as response:
                    ok = response.status == 200
                    if name == "업로드 API":
                        ok = ok and json.loads(response.read(4096)).get("instance_id") == config.instance_id
                findings.append((name, "정상" if ok else "조치 필요", "서버 PC에서 응답을 확인했습니다." if ok else "이 과정의 응답인지 확인할 수 없습니다."))
            except (OSError, ValueError) as exc:
                findings.append((name, "연결 실패", f"로그와 접속 설정을 확인하세요. {exc}"))
    else:
        try:
            check_public_port(config.bind_host, config.port)
            findings.append(("접속 포트", "사용 가능", str(config.port)))
        except LaunchError:
            findings.append(("접속 포트", "사용 중", f"다른 프로그램이 {config.port} 포트를 사용합니다. 설정에서 포트를 변경하세요."))
    if config.public_host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "::"):
        findings.append(("수강생 접속 주소", "주소 변경 필요", "다른 PC에서 접속하려면 설정에서 서버 PC의 네트워크 IP를 선택하세요."))
    else:
        findings.append(("수강생 접속 주소", "다른 PC에서 확인 필요", config.public_url + " — 서버 PC의 정상 응답만으로 교육장 방화벽 통과를 확인할 수는 없습니다."))
    return findings
