"""Test the ZIP after relocation, without system Python discovery or package downloads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
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
        "Service(c).bootstrap_admin('admin','Port1234')\n"
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
            from test_api import auth, begin, complete_one, login, student
            url = state["url"]
            assert client.get(url + "/api/health").status_code == 200
            assert client.get(url, follow_redirects=True).status_code == 200
            assert client.get(url + "/upload").status_code == 200
            for asset, media_type in (("upload.js", "text/javascript"), ("sha256.js", "text/javascript"),
                                      ("hash-worker.js", "text/javascript"), ("upload.css", "text/css")):
                resource = client.get(url + "/upload-static/" + asset)
                assert resource.status_code == 200
                assert resource.headers["content-type"].split(";")[0] == media_type
            admin = login(client, "admin", "Port1234")
            assignments = client.get("/api/assignments", headers=auth(admin)).json()
            default = assignments[0]
            assert "documents" in default["allowed_file_categories"]
            assert "other" not in default["allowed_file_categories"]
            assert default["video_audio_required"] is True
            created_assignment = client.post("/api/admin/assignments", headers=auth(admin), json={"title": "배포 기본 정책 확인"})
            assert created_assignment.status_code == 200, created_assignment.text
            assert created_assignment.json()["allowed_file_categories"] == default["allowed_file_categories"]
            roster = client.post("/api/admin/roster/preview", headers=auth(admin),
                                 content=(distribution / "templates" / "users_template.tsv").read_bytes())
            assert roster.status_code == 200 and roster.json()["valid"], roster.text
            assert [row["user_id"] for row in roster.json()["rows"]] == ["001", "002"]
            session = student(client, admin)
            opened = client.post("/upload", data={"token": session["token"]})
            assert opened.status_code == 200 and 'id="session-data"' in opened.text
            assert 'id="loading-panel"' in opened.text
            job_request = {"request_id": "portable_recoverable_roster",
                           "rows": [{"user_id": "002", "name": "공통 비밀번호 학생"}],
                           "common_temporary_password": "Class123"}
            accepted = client.post("/api/admin/roster/jobs", headers=auth(admin), json=job_request)
            assert accepted.status_code == 202, accepted.text
            job_id = accepted.json()["id"]
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = client.get(f"/api/admin/roster/jobs/{job_id}", headers=auth(admin))
                assert status.status_code == 200, status.text
                if status.json()["status"] == "completed":
                    break
                assert status.json()["status"] in ("queued", "running"), status.text
                time.sleep(0.05)
            else:
                pytest.fail("The bundled runtime did not complete its encrypted roster job")
            result = client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=auth(admin))
            assert result.status_code == 200, result.text
            receipt = result.json()
            assert receipt["created"][0]["temporary_password"] == "Class123"
            assert client.get(f"/api/admin/roster/jobs/{job_id}/result").status_code == 401
            store = tmp_path / "store"
            key_path = store / "roster-keys" / (job_id + ".key")
            assert key_path.is_file()
            for database_file in store.glob("assignmenthub.db*"):
                assert b"Class123" not in database_file.read_bytes()
            assert login(client, "002", "Class123")["must_change_password"]
            assert begin(client, session, [("unclassified.bin", b"test")]).status_code == 415
            payload = "내장 Python 전송 확인".encode("utf-8")
            completed = complete_one(client, session, payload, "내장 배포 결과.txt")
            file_id = completed["files"][0]["id"]
            assert "storage_path" not in completed["files"][0]
            dashboard = client.get("/api/admin/dashboard", headers=auth(admin), params={"assignment_id": default["id"]})
            assert dashboard.status_code == 200, dashboard.text
            stored_file = dashboard.json()["submissions"][0]["files"][0]
            location = Path(stored_file["storage_path"])
            assert location.resolve().is_relative_to((store / "submissions").resolve())
            assert "기본 과제" in str(location) and "홍길동_001" in str(location)
            assert location.name == "내장 배포 결과.txt" and location.read_bytes() == payload
            response = client.post(f"/api/files/{file_id}/ticket", headers=auth(session))
            assert response.status_code == 200, response.text
            downloaded = client.post("/api/downloads", data={"ticket": response.json()["ticket"]})
            assert downloaded.status_code == 200 and downloaded.content == payload
        # Restart the relocated bundled server with its original encrypted result
        # and receipt intact, then explicitly acknowledge/delete that result.
        bat(distribution, restricted_env, "manage.bat", "stop", "--config", str(config))
        state = json.loads(bat(distribution, restricted_env, "manage.bat", "start", "--config", str(config), "--json"))
        assert state["running"]
        with httpx.Client(base_url=state["url"], trust_env=False, timeout=15) as client:
            admin = login(client, "admin", "Port1234")
            visible = client.get("/api/admin/roster/jobs", headers=auth(admin))
            assert visible.status_code == 200 and any(job["id"] == job_id for job in visible.json())
            assert client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=auth(admin)).json() == receipt
            replay = client.post("/api/admin/roster/jobs", headers=auth(admin), json=job_request)
            assert replay.status_code == 202 and replay.json()["id"] == job_id
            assert replay.json()["created_count"] == 1
            assert client.get(f"/api/files/{file_id}/download", headers=auth(admin)).content == payload
            assert location.read_bytes() == payload
            assert login(client, "002", receipt["created"][0]["temporary_password"])["must_change_password"]
            acknowledged = client.delete(f"/api/admin/roster/jobs/{job_id}/result", headers=auth(admin))
            assert acknowledged.status_code == 200, acknowledged.text
            assert not key_path.exists()
            assert client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=auth(admin)).status_code == 410
            with sqlite3.connect(store / "assignmenthub.db") as database:
                assert database.execute("SELECT result FROM roster_jobs WHERE id=?", (job_id,)).fetchone()[0] is None
                assert database.execute("SELECT count(*) FROM users WHERE user_id='002'").fetchone()[0] == 1
    finally:
        bat(distribution, restricted_env, "manage.bat", "stop", "--config", str(config))
    state = json.loads(bat(distribution, restricted_env, "manage.bat", "status", "--config", str(config), "--json"))
    assert not state["running"]
