"""Real process death before/after the atomic roster account/result commit."""
import time

import httpx
import pytest

from assignmenthub.config import Config
from assignmenthub.launcher import internal_port, process_record, terminate_owned
from assignmenthub.service import Service
from test_process_recovery import boot


@pytest.mark.integration
@pytest.mark.parametrize("point", ["roster_before_commit", "roster_after_commit"])
def test_roster_result_survives_real_process_exit_without_duplicate_accounts(tmp_path, point):
    config = Config(instance_id="crash_" + point, course_name="명단 강제 종료 검증", port=internal_port(),
                    storage_root=str(tmp_path / "store"), bind_host="127.0.0.1", public_host="127.0.0.1",
                    min_free_bytes=0)
    config_path = tmp_path / "config.json"
    config.save(config_path)
    service = Service(config)
    password = "Roster-crash-admin-password-73"
    service.bootstrap_admin("admin", password)
    token = service.login("admin", password, "local")["token"]
    headers = {"Authorization": "Bearer " + token}
    request = {"request_id": "same_roster_after_process_exit", "rows": [
        {"user_id": f"00{i}", "name": f"복구 수강생 {i}", "group": "A반"} for i in range(3)]}
    accepted_id = None
    process = boot(config_path, config.port, point)
    try:
        with httpx.Client(base_url=config.public_url, timeout=20, trust_env=False) as client:
            try:
                response = client.post("/api/admin/roster/jobs", headers=headers, json=request)
                assert response.status_code == 202, response.text
                accepted_id = response.json()["id"]
            except httpx.TransportError:
                # The worker may exit the entire process before even the short
                # acceptance response reaches the client. Its request ID survives.
                pass
        assert process.wait(20) == 73
        with service.store.connect() as db:
            durable = dict(db.execute("SELECT * FROM roster_jobs WHERE request_id=?", (request["request_id"],)).fetchone())
            before_hashes = {row["user_id"]: row["password_hash"] for row in db.execute("SELECT * FROM users WHERE role='student'")}
            committed = point == "roster_after_commit"
            assert len(before_hashes) == (3 if committed else 0)
            assert durable["status"] == ("completed" if committed else "running")
            assert (durable["result"] is not None) == committed
            assert (durable["payload"] is None) == committed
        job_id = durable["id"]
        if accepted_id:
            assert accepted_id == job_id
        key_path = config.root / "roster-keys" / (job_id + ".key")
        assert key_path.is_file()

        process = boot(config_path, config.port)
        with httpx.Client(base_url=config.public_url, timeout=20, trust_env=False) as client:
            fresh = client.post("/api/auth/login", json={"user_id": "admin", "password": password})
            assert fresh.status_code == 200, fresh.text
            headers = {"Authorization": "Bearer " + fresh.json()["token"]}
            visible = client.get("/api/admin/roster/jobs", headers=headers)
            assert visible.status_code == 200 and any(job["id"] == job_id for job in visible.json())
            repeated = client.post("/api/admin/roster/jobs", headers=headers, json=request)
            assert repeated.status_code == 202 and repeated.json()["id"] == job_id
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = client.get(f"/api/admin/roster/jobs/{job_id}", headers=headers)
                assert status.status_code == 200, status.text
                current = status.json()
                if current["status"] == "completed":
                    break
                assert current["status"] in ("queued", "running"), current
                time.sleep(0.05)
            else:
                pytest.fail("Restarted server did not recover the roster job")
            result = client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=headers)
            assert result.status_code == 200, result.text
            receipt = result.json()
            assert len(receipt["created"]) == 3 and receipt["updated"] == 0
            assert client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=headers).json() == receipt
            for row in receipt["created"]:
                assert len(row["temporary_password"]) == 8
                signed_in = client.post("/api/auth/login", json={"user_id": row["user_id"], "password": row["temporary_password"]})
                assert signed_in.status_code == 200 and signed_in.json()["must_change_password"]
                for path in config.root.glob("assignmenthub.db*"):
                    assert row["temporary_password"].encode() not in path.read_bytes()
            with service.store.connect() as db:
                recovered_hashes = {row["user_id"]: row["password_hash"] for row in db.execute("SELECT * FROM users WHERE role='student'")}
                assert len(recovered_hashes) == 3
                if committed:
                    assert recovered_hashes == before_hashes
                assert db.execute("SELECT count(*) FROM roster_jobs").fetchone()[0] == 1
                assert db.execute("SELECT count(*) FROM audit WHERE action='roster_apply'").fetchone()[0] == 1
            acknowledged = client.delete(f"/api/admin/roster/jobs/{job_id}/result", headers=headers)
            assert acknowledged.status_code == 200, acknowledged.text
            assert not key_path.exists()
            assert client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=headers).status_code == 410
            replay_after_delete = client.post("/api/admin/roster/jobs", headers=headers, json=request)
            assert replay_after_delete.status_code == 202
            assert replay_after_delete.json()["id"] == job_id
            assert replay_after_delete.json()["status"] == "acknowledged"
            with service.store.connect() as db:
                assert db.execute("SELECT result FROM roster_jobs WHERE id=?", (job_id,)).fetchone()[0] is None
                assert db.execute("SELECT count(*) FROM users WHERE role='student'").fetchone()[0] == 3
    finally:
        if process.poll() is None:
            terminate_owned([process_record(process)], timeout=2)
        process.wait(10)
