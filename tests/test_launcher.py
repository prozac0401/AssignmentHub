from __future__ import annotations

from dataclasses import replace
from contextlib import suppress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import psutil
import pytest

from assignmenthub.config import Config
from assignmenthub.launcher import (LaunchError, PROJECT_ROOT, StorageLock, caddy_config,
                                    canonical_root, check_public_port, internal_port,
                                    owned_process, process_record, proxy_binary, start,
                                    status, stop, terminate_owned)


def make_config(root, name="launch_test"):
    return Config(instance_id=name, course_name="실행 도구 검증", port=internal_port(),
                  public_host="127.0.0.1", bind_host="127.0.0.1", storage_root=str(root),
                  min_free_bytes=0)


def sleeper():
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                               stdin=subprocess.PIPE, **options)
    process._test_owner = process_record(process)
    return process


def cleanup_test_process(process):
    """Close this test's pipe; an already exited child cannot mask assertions."""
    if process.stdin is not None:
        with suppress(OSError):
            process.stdin.close()
    with suppress(OSError, psutil.Error):
        terminate_owned([process._test_owner], timeout=2)
    with suppress(OSError, subprocess.TimeoutExpired):
        process.wait(10)


def test_storage_lock_canonical_alias_and_process_crash_release(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    code = ("from pathlib import Path; import sys; "
            "from assignmenthub.launcher import StorageLock; "
            "lock=StorageLock(Path(sys.argv[1])); lock.__enter__(); "
            "print('locked',flush=True); sys.stdin.buffer.read()")
    process = subprocess.Popen([sys.executable, "-c", code, str(root)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    owner = process_record(process)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(LaunchError, match="실행 중"):
            with StorageLock(root / ".." / "store"):
                pass
        assert canonical_root(root) == canonical_root(root / ".." / "store")
    finally:
        with suppress(OSError, psutil.Error):
            terminate_owned([owner], timeout=2)
        with suppress(OSError, subprocess.TimeoutExpired):
            process.wait(10)
        with suppress(OSError):
            process.stdin.close()
    with StorageLock(root):
        pass


def test_stop_owned_process_does_not_stop_other_python():
    target, unrelated = sleeper(), sleeper()
    try:
        record = target._test_owner
        stale = {**record, "created": record["created"] - 100}
        assert owned_process(stale) is None
        terminate_owned([stale], timeout=0.2)
        assert target.poll() is None
        terminate_owned([record], timeout=2)
        assert target.wait(5) is not None
        assert unrelated.poll() is None
    finally:
        for process in (target, unrelated):
            cleanup_test_process(process)


def test_public_port_collision_is_clear():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(LaunchError, match="접속 포트"):
            check_public_port("127.0.0.1", listener.getsockname()[1])


def test_caddy_configuration_is_valid_and_does_not_buffer_files(tmp_path):
    try:
        binary = proxy_binary()
    except LaunchError:
        pytest.skip("Pinned Caddy not installed; run scripts/install_proxy.py")
    config = make_config(tmp_path)
    rendered = caddy_config(config, 20111, 20112)
    path = tmp_path / "Caddyfile"
    path.write_text(rendered, encoding="utf-8")
    result = subprocess.run([str(binary), "adapt", "--config", str(path), "--adapter", "caddyfile"],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    raw = json.dumps(parsed)
    assert '"/api/*"' in raw
    assert '"/upload-static/*"' in raw
    assert "/ui/launch_test/" in raw
    assert "request_buffers" not in raw and "response_buffers" not in raw
    assert parsed["admin"]["disabled"] is True


def test_caddy_respects_instance_configuration_and_data_paths(tmp_path):
    try:
        binary = proxy_binary()
    except LaunchError:
        pytest.skip("Pinned Caddy not installed")
    environment = {"XDG_CONFIG_HOME": str(tmp_path / "proxy-config"), "XDG_DATA_HOME": str(tmp_path / "proxy-data")}
    if os.name == "nt":
        environment["SystemRoot"] = os.environ["SystemRoot"]
    result = subprocess.run([str(binary), "environ"], env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    normalized = result.stdout.replace("\\", "/").lower()
    assert str(tmp_path / "proxy-config" / "caddy").replace("\\", "/").lower() in normalized
    assert str(tmp_path / "proxy-data" / "caddy").replace("\\", "/").lower() in normalized


@pytest.mark.integration
def test_real_two_instance_start_stop_proxy_and_isolation(tmp_path):
    """Actual processes + HTTP gateway; distinct from full browser / 2GiB tests."""
    from assignmenthub.service import Service
    try:
        proxy_binary()
    except LaunchError:
        pytest.skip("Pinned Caddy not installed")
    first, second = make_config(tmp_path / "one", "launcher_one"), make_config(tmp_path / "two", "launcher_two")
    first_path, second_path = tmp_path / "one.json", tmp_path / "two.json"
    first.save(first_path)
    second.save(second_path)
    for config in (first, second):
        Service(config).bootstrap_admin("admin", "Launcher-test-password-51")
    unrelated = sleeper()
    try:
        result1, result2 = start(first_path), start(second_path)
        assert result1["running"] and result2["running"]
        assert result1["api_port"] != result2["api_port"]
        assert result1["ui_port"] != result2["ui_port"]
        with httpx.Client(timeout=10, trust_env=False) as client:
            assert client.get(first.public_url + "/api/health").status_code == 200
            page = client.get(first.public_url, follow_redirects=True)
            assert page.status_code == 200 and str(page.url).endswith("/ui/launcher_one/")
            assert "streamlit" in page.text.lower()
            assert client.get(first.public_url + "/ui/launcher_one/_stcore/health").status_code == 200
            assert client.get(first.public_url + "/upload").status_code == 200
            login = client.post(first.public_url + "/api/auth/login", json={"user_id": "admin", "password": "Launcher-test-password-51"})
            assert login.status_code == 200, login.text
            token = login.json()["token"]
            crossed = client.get(second.public_url + "/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert crossed.status_code == 401
            with pytest.raises(LaunchError, match="이미 실행"):
                start(first_path)
            alias = replace(first, instance_id="alias_instance", port=internal_port())
            alias_path = tmp_path / "alias.json"
            alias.save(alias_path)
            with pytest.raises(LaunchError, match="실행 중"):
                start(alias_path)
            duplicate_id = replace(first, storage_root=str(tmp_path / "duplicate-id"), port=internal_port())
            duplicate_path = tmp_path / "duplicate-id.json"
            duplicate_id.save(duplicate_path)
            with pytest.raises(LaunchError, match="instance_id"):
                start(duplicate_path)
            stop(first)
            assert not status(first)["running"]
            assert status(second)["running"]
            assert client.get(second.public_url + "/api/health").status_code == 200
            assert unrelated.poll() is None
    finally:
        stop(first)
        stop(second)
        cleanup_test_process(unrelated)


@pytest.mark.integration
def test_start_failure_cleans_only_own_processes(tmp_path):
    from assignmenthub.service import Service
    config = make_config(tmp_path / "failed", "failure_test")
    path = tmp_path / "failed.json"
    config.save(path)
    Service(config).bootstrap_admin("admin", "Launcher-test-password-52")
    unrelated = sleeper()
    with socket.socket() as listener:
        listener.bind((config.bind_host, config.port))
        listener.listen()
        try:
            with pytest.raises(LaunchError, match="접속 포트"):
                start(path)
            assert not status(config)["running"]
            assert unrelated.poll() is None
        finally:
            cleanup_test_process(unrelated)


@pytest.mark.integration
def test_supervisor_crash_then_stop_reaps_recorded_orphans(tmp_path):
    from assignmenthub.service import Service
    try:
        proxy_binary()
    except LaunchError:
        pytest.skip("Pinned Caddy not installed")
    config = make_config(tmp_path / "orphan", "launcher_orphan")
    path = tmp_path / "orphan.json"
    config.save(path)
    Service(config).bootstrap_admin("admin", "Launcher-test-password-53")
    unrelated = sleeper()
    try:
        state = start(path)
        recorded = state["children"] + state.get("descendants", [])
        supervisor = owned_process(state["supervisor"])
        assert supervisor is not None
        supervisor.kill()
        supervisor.wait(10)
        stop(config)
        assert all(owned_process(record) is None for record in recorded)
        assert unrelated.poll() is None
    finally:
        stop(config)
        cleanup_test_process(unrelated)
