"""Test the ZIP after relocation, without system Python discovery or package downloads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import zipfile

import httpx
import pytest

from assignmenthub.portable import sha256, verify_files


def test_integrity_detects_modified_missing_and_escaping_files(tmp_path):
    path = tmp_path / "payload.txt"
    path.write_text("original", encoding="utf-8")
    manifest = tmp_path / "distribution.json"
    manifest.write_text(json.dumps({"files": {path.name: sha256(path)}}))
    assert verify_files(tmp_path) == 1
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="payload.txt"):
        verify_files(tmp_path)
    path.unlink()
    with pytest.raises(ValueError, match="payload.txt"):
        verify_files(tmp_path)
    manifest.write_text(json.dumps({"files": {"../outside": "0" * 64}}))
    with pytest.raises(ValueError, match="outside"):
        verify_files(tmp_path)


@pytest.fixture(scope="module")
def distribution(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("Windows portable distribution")
    archive = os.environ.get("AH_PORTABLE_ZIP")
    if not archive:
        pytest.skip("Set AH_PORTABLE_ZIP to the built ZIP for actual distribution tests")
    parent = tmp_path_factory.mktemp("portable") / "한글 배포 & 공백!"
    parent.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(parent)
    roots = list(parent.glob("AssignmentHub-*"))
    assert len(roots) == 1
    root = roots[0]
    assert not (root / ".venv").exists()
    assert not (root / "instances").exists()
    assert not (root / "data").exists()
    assert not (root / "runtime" / "python" / "pyvenv.cfg").exists()
    return root


@pytest.fixture
def restricted_env(distribution, tmp_path):
    poison = tmp_path / "poison-python"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('External Python path used')\n")
    env = os.environ.copy()
    env.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
               PYTHONHOME=str(poison), PYTHONPATH=str(poison), PYTHONUSERBASE=str(poison),
               TCL_LIBRARY=str(poison), TK_LIBRARY=str(poison), AH_NO_PAUSE="1",
               HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
               ALL_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost,::1")
    return env


def bat(root, env, *args, expected=0):
    result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *args],
                            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", stdin=subprocess.DEVNULL, timeout=180)
    assert result.returncode == expected, result.stdout + result.stderr
    return result.stdout


@pytest.mark.integration
def test_relocated_setup_and_cli(distribution, restricted_env):
    output = bat(distribution, restricted_env, "setup.bat")
    assert '"isolated": true' in output
    assert '"verified_files":' in output
    assert "usage:" in bat(distribution, restricted_env, "manage.bat", "--help")
    bat(distribution, restricted_env, "manage.bat", "invalid-command", expected=2)


@pytest.mark.integration
def test_relocated_real_gui(distribution, restricted_env, tmp_path, monkeypatch):
    import test_manager_entrypoint
    monkeypatch.setenv("AH_DISTRIBUTION_ROOT", str(distribution))
    for key, value in restricted_env.items():
        monkeypatch.setenv(key, value)
    test_manager_entrypoint.test_start_bat_opens_owned_pythonw_manager(tmp_path)


@pytest.mark.integration
def test_relocated_server_lifecycle(distribution, restricted_env, tmp_path):
    config = tmp_path / "portable.json"
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(
        "from pathlib import Path\nimport sys, uuid\n"
        "from assignmenthub.config import Config\n"
        "from assignmenthub.launcher import internal_port\n"
        "from assignmenthub.service import Service\n"
        "c=Config(instance_id='portable_'+uuid.uuid4().hex[:10], course_name='내장 배포 검증', "
        "port=internal_port(), public_host='127.0.0.1', bind_host='127.0.0.1', "
        "storage_root=str(Path(sys.argv[1]).parent/'store'), min_free_bytes=0, chunk_bytes=8)\n"
        "Service(c).bootstrap_admin('admin','Portable-test-password-93')\n"
        "c.save(Path(sys.argv[1]))\n", encoding="utf-8")
    python = distribution / "runtime" / "python" / "python.exe"
    subprocess.run([str(python), "-X", "utf8", "-B", str(bootstrap), str(config)],
                   cwd=tmp_path, env=restricted_env, check=True, timeout=30)
    try:
        state = json.loads(bat(distribution, restricted_env, "manage.bat", "start", "--config", str(config), "--json"))
        assert state["running"]
        assert Path(state["supervisor"]["command"][0]).resolve() == python.resolve()
        for child in state["children"][:2]:
            assert Path(child["command"][0]).resolve() == python.resolve()
        with httpx.Client(base_url=state["url"], trust_env=False, timeout=15) as client:
            from test_api import auth, complete_one, login, student
            url = state["url"]
            assert client.get(url + "/api/health").status_code == 200
            assert client.get(url, follow_redirects=True).status_code == 200
            assert client.get(url + "/upload").status_code == 200
            admin = login(client, "admin", "Portable-test-password-93")
            session = student(client, admin)
            payload = "내장 Python 전송 확인".encode("utf-8")
            completed = complete_one(client, session, payload)
            file_id = completed["files"][0]["id"]
            response = client.post(f"/api/files/{file_id}/ticket", headers=auth(session))
            assert response.status_code == 200, response.text
            downloaded = client.post("/api/downloads", data={"ticket": response.json()["ticket"]})
            assert downloaded.status_code == 200 and downloaded.content == payload
    finally:
        bat(distribution, restricted_env, "manage.bat", "stop", "--config", str(config))
    state = json.loads(bat(distribution, restricted_env, "manage.bat", "status", "--config", str(config), "--json"))
    assert not state["running"]
