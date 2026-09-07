from dataclasses import replace
from pathlib import Path
import socket

import pytest

from assignmenthub.config import Config
from assignmenthub.launcher import LaunchError, StorageLock, internal_port
from assignmenthub.management import Catalog, diagnose, gib_bytes, gib_text, state_label


def config_at(root, name="manager_test"):
    return Config(name, "운영 관리 검증", internal_port(), str(root), public_host="127.0.0.1",
                  bind_host="127.0.0.1", min_free_bytes=0)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0", "0.0000000001", "99999999999999999"])
def test_invalid_human_capacity_cannot_reach_config(bad):
    with pytest.raises(ValueError, match="GiB"):
        gib_bytes(bad, "파일 크기")


def test_fractional_gib_round_trip_preserves_exact_bytes():
    for value in (1, 1048577, 2147483648, 2**53 - 1):
        assert gib_bytes(gib_text(value), "크기") == value
    assert gib_bytes("0.5", "크기") == 536870912
    assert gib_bytes("0", "여유", True) == 0


def test_new_course_creation_conflicts_and_external_catalog(tmp_path):
    from assignmenthub.service import Service
    catalog = Catalog(tmp_path / "instances")
    config = config_at(tmp_path / "data")
    password = "Local-manager-test-password-91"
    path = catalog.create(config, "admin", password, password)
    assert catalog.courses()[0].config.instance_id == config.instance_id
    assert Service(Config.load(path)).login("admin", password, "local-test")["user"]["role"] == "admin"
    with pytest.raises(LaunchError, match="포트"):
        catalog.create(replace(config, instance_id="manager_two", storage_root=str(tmp_path / "two")), "admin", password, password)
    assert not (tmp_path / "two").exists()
    external_catalog = Catalog(tmp_path / "other-instances")
    external_catalog.add(path)
    assert external_catalog.paths() == [path.resolve()]
    assert external_catalog.add(path).instance_id == config.instance_id
    assert len(external_catalog.courses()) == 1
    assert password not in external_catalog.registry.read_text(encoding="utf-8")
    broken = external_catalog.directory / "broken.json"
    broken.write_text("bad", encoding="utf-8")
    courses = external_catalog.courses()
    assert len(courses) == 2 and any(c.error for c in courses)


def test_create_retries_after_port_failure_without_overwriting_data(tmp_path):
    catalog = Catalog(tmp_path / "instances")
    config = config_at(tmp_path / "data")
    password = "Local-manager-test-password-92"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", config.port))
        listener.listen()
        with pytest.raises(LaunchError, match="접속 포트"):
            catalog.create(config, "admin", password, password)
        assert not list(catalog.directory.glob("*.json"))
    path = catalog.create(config, "admin", password, password)
    assert path.exists()
    occupied = tmp_path / "existing-files"
    occupied.mkdir()
    protected = occupied / "keep.txt"
    protected.write_text("preserve", encoding="utf-8")
    with pytest.raises(LaunchError, match="비어 있는"):
        catalog.create(config_at(occupied, "another"), "admin", password, password)
    assert protected.read_text() == "preserve"


def test_settings_preserve_identity_and_refuse_active_storage(tmp_path):
    from assignmenthub.service import Service
    catalog = Catalog(tmp_path / "instances")
    config = config_at(tmp_path / "data")
    password = "Local-manager-test-password-93"
    path = catalog.create(config, "admin", password, password)
    secret = (config.root / "auth.secret").read_bytes()
    original = path.read_bytes()
    with StorageLock(config.root):
        with pytest.raises(LaunchError):
            catalog.update(path, course_name="변경")
    assert path.read_bytes() == original
    updated = catalog.update(path, course_name="다음 수업", max_file_bytes=gib_bytes("0.5", "크기"))
    assert updated.instance_id == config.instance_id and updated.root == config.root
    assert (config.root / "auth.secret").read_bytes() == secret
    assert Service(updated).login("admin", password, "local-test")["user"]["role"] == "admin"
    with pytest.raises(ValueError):
        catalog.update(path, storage_root=str(tmp_path / "moved"))


def test_diagnostics_distinguish_local_connectivity_and_port_collision(tmp_path):
    config = config_at(tmp_path / "absent")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", config.port))
        listener.listen()
        findings = diagnose(config)
    assert ("접속 포트", "사용 중") in [(name, state) for name, state, _ in findings]
    assert any(name == "수강생 접속 주소" and result == "주소 변경 필요" for name, result, _ in findings)
    assert not config.root.exists(), "Read-only diagnostics must not initialize storage"
    assert state_label({"running": False, "stage": "running"}) == "점검 필요"


def test_suggestions_skip_configured_ports_even_while_stopped(tmp_path):
    catalog = Catalog(tmp_path / "instances")
    catalog.directory.mkdir()
    Config("course_01", "첫 과정", 8501, str(tmp_path / "one")).save(catalog.directory / "one.json")
    identity, port = catalog.suggest()
    assert identity == "course_02" and port >= 8502
