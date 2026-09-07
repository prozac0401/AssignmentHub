"""Actual Uvicorn TCP disconnect + process death + restarted server recovery.

Small payload tests establish crash-state behavior, not 2GiB load performance.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import time

import httpx
import pytest

from assignmenthub.config import Config
from assignmenthub.launcher import PROJECT_ROOT, internal_port, process_record, terminate_owned
from assignmenthub.service import Service


def boot(config_path, port, hook=""):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    log_path = config_path.with_suffix(".server.log")
    with log_path.open("ab") as output:
        process = subprocess.Popen([sys.executable, str(PROJECT_ROOT / "tests" / "_crash_server.py"),
                                    str(config_path), str(port), hook], cwd=PROJECT_ROOT,
                                   stdout=output, stderr=subprocess.STDOUT, **options)
    with httpx.Client(timeout=1, trust_env=False) as client:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"test TCP server exited before readiness: {process.returncode}: {log_path.read_text(errors='replace')}")
            try:
                if client.get(f"http://127.0.0.1:{port}/api/health").status_code == 200:
                    return process
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    terminate_owned([process_record(process)], timeout=2)
    raise AssertionError("test TCP server startup timed out: " + log_path.read_text(errors="replace"))


@pytest.mark.integration
@pytest.mark.parametrize("point", ["during_chunk", "chunk_before_commit", "after_move", "final_before_commit", "after_commit"])
def test_real_process_exit_recovery_and_idempotent_receipt(tmp_path, point):
    config = Config(instance_id="crash_" + point, course_name="강제 종료 검증", port=internal_port(),
                    storage_root=str(tmp_path / "store"), bind_host="127.0.0.1", public_host="127.0.0.1",
                    max_file_bytes=64, chunk_bytes=4, user_quota_bytes=1024, min_free_bytes=0)
    config_path = tmp_path / "config.json"
    config.save(config_path)
    service = Service(config)
    service.bootstrap_admin("admin", "Crash-admin-password-71")
    admin = service.login("admin", "Crash-admin-password-71", "local")["token"]
    roster = service.roster_apply(admin, [{"user_id": "0001", "name": "복구 검증", "group": ""}])
    temporary = roster["created"][0]["temporary_password"]
    restricted = service.login("0001", temporary, "local")["token"]
    service.change_password(restricted, temporary, "Crash-student-password-71", "Crash-student-password-71")
    token = service.login("0001", "Crash-student-password-71", "local")["token"]
    headers = {"Authorization": "Bearer " + token}
    with service.store.connect() as db:
        assignment_id = db.execute("select id from assignments").fetchone()[0]
    data = b"abcdefgh"
    sha = hashlib.sha256(data).hexdigest()
    upload = service.start_upload(token, assignment_id, "crash_request_01", [{"name": "복구.bin", "size": len(data), "sha256": sha}])
    upload_id, file_id = upload["id"], upload["files"][0]["id"]
    base = f"http://127.0.0.1:{config.port}"
    process = boot(config_path, config.port, point)
    try:
        with httpx.Client(timeout=20, trust_env=False) as client:
            if point in ("during_chunk", "chunk_before_commit"):
                with pytest.raises(httpx.TransportError):
                    client.patch(f"{base}/api/uploads/{upload_id}/files/{file_id}?offset=0", content=data[:4],
                                 headers={**headers, "X-Chunk-SHA256": hashlib.sha256(data[:4]).hexdigest()})
            else:
                for offset in (0, 4):
                    chunk = data[offset:offset+4]
                    response = client.patch(f"{base}/api/uploads/{upload_id}/files/{file_id}?offset={offset}", content=chunk,
                                            headers={**headers, "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()})
                    assert response.status_code == 200, response.text
                with pytest.raises(httpx.TransportError):
                    client.post(f"{base}/api/uploads/{upload_id}/complete", headers=headers)
        assert process.wait(20) == 73
        process = boot(config_path, config.port)
        with httpx.Client(timeout=20, trust_env=False) as client:
            response = client.get(f"{base}/api/uploads/{upload_id}", headers=headers)
            assert response.status_code == 200, response.text
            recovered = response.json()
            if point in ("during_chunk", "chunk_before_commit"):
                assert recovered["status"] == "paused"
                assert recovered["files"][0]["offset"] == 0
                for offset in (0, 4):
                    chunk = data[offset:offset+4]
                    response = client.patch(f"{base}/api/uploads/{upload_id}/files/{file_id}?offset={offset}", content=chunk,
                                            headers={**headers, "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()})
                    assert response.status_code == 200, response.text
            else:
                assert recovered["status"] == "completed"
            receipt = client.post(f"{base}/api/uploads/{upload_id}/complete", headers=headers)
            assert receipt.status_code == 200, receipt.text
            repeated = client.post(f"{base}/api/uploads/{upload_id}/complete", headers=headers)
            assert repeated.json()["submission_number"] == receipt.json()["submission_number"]
            assert receipt.json()["status"] == "completed"
            downloaded = client.get(f"{base}/api/files/{file_id}/download", headers=headers)
            assert downloaded.content == data and hashlib.sha256(downloaded.content).hexdigest() == sha
            quota = client.get(f"{base}/api/quota", headers=headers).json()
            assert quota["used_bytes"] == len(data) and quota["reserved_bytes"] == 0
    finally:
        if process.poll() is None:
            terminate_owned([process_record(process)], timeout=2)
        process.wait(10)
