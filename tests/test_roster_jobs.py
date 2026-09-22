"""Recoverable credential delivery and authenticated bounded roster input."""
import json
from contextlib import contextmanager
import sqlite3
import threading
import time
import uuid

import pytest
from fastapi import HTTPException

from assignmenthub.config import Config
from assignmenthub.roster_jobs import MAX_PENDING_JOBS, ROSTER_JSON_BYTES, RosterJobs
from assignmenthub.service import Service
from test_api import ADMIN_PASSWORD, auth, hub, login, tsv_bytes


@pytest.fixture
def queue(tmp_path):
    config = Config(instance_id="roster_test", course_name="명단 테스트", port=18995,
                    public_host="127.0.0.1", storage_root=str(tmp_path / "store"), min_free_bytes=0)
    service = Service(config)
    service.bootstrap_admin("admin", ADMIN_PASSWORD)
    admin = service.login("admin", ADMIN_PASSWORD, "local")
    yield RosterJobs(service), admin["token"]


def create(queue, request_id="request_1", name="홍길동", common=None):
    jobs, token = queue
    return jobs.create(token, request_id, [{"user_id": "001", "name": name, "group": "A"}],
                       common_temporary_password=common)


def error(code, function, *args, **kwargs):
    with pytest.raises(HTTPException) as result:
        function(*args, **kwargs)
    assert result.value.status_code == code


def wait_complete(jobs, token, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = jobs.get(token, job_id)
        if job["status"] == "completed":
            return job
        assert job["status"] in ("queued", "running"), job
        time.sleep(0.02)
    pytest.fail("roster worker did not finish")


@pytest.mark.parametrize("common", [None, "Abcd2345"])
def test_lost_response_same_request_replays_identical_credentials(queue, common):
    jobs, token = queue
    first = create(queue, common=common)
    assert jobs.process_one()
    original = jobs.get(token, first["id"], result=True)
    replay = create(queue, common=common)
    assert replay["id"] == first["id"] and replay["created_count"] == 1
    assert jobs.get(token, replay["id"], result=True) == original
    assert not jobs.process_one()
    credential = original["created"][0]["temporary_password"]
    assert len(credential) == 8
    if common:
        assert credential == common
    assert jobs.service.login("001", credential, "student")["must_change_password"]
    for path in jobs.service.config.root.glob("assignmenthub.db*"):
        assert credential.encode() not in path.read_bytes()
    with jobs.service.store.connect() as db:
        assert db.execute("SELECT count(*) FROM users WHERE role='student'").fetchone()[0] == 1
        assert db.execute("SELECT payload FROM roster_jobs").fetchone()[0] is None
        assert db.execute("SELECT count(*) FROM audit WHERE action='roster_apply'").fetchone()[0] == 1


def test_completed_result_survives_new_service_and_logout(queue):
    jobs, token = queue
    job = create(queue)
    jobs.process_one()
    original = jobs.get(token, job["id"], result=True)
    jobs.service.logout(token)
    restarted = RosterJobs(Service(jobs.service.config))
    fresh = restarted.service.login("admin", ADMIN_PASSWORD, "local")["token"]
    assert restarted.list(fresh)[0]["id"] == job["id"]
    assert restarted.get(fresh, job["id"], result=True) == original
    error(401, restarted.get, token, job["id"], result=True)


def test_restart_resumes_interrupted_hashing_without_partial_accounts(queue, monkeypatch):
    jobs, token = queue
    job = create(queue, common="Abcd2345")
    original_prepare = jobs.service.prepare_roster

    def terminate(*args):
        raise SystemExit("simulated process stop during hashing")

    monkeypatch.setattr(jobs.service, "prepare_roster", terminate)
    with pytest.raises(SystemExit):
        jobs.process_one()
    assert jobs.get(token, job["id"])["status"] == "running"
    with jobs.service.store.connect() as db:
        assert db.execute("SELECT count(*) FROM users WHERE role='student'").fetchone()[0] == 0
    monkeypatch.setattr(jobs.service, "prepare_roster", original_prepare)
    restarted = RosterJobs(Service(jobs.service.config))
    restarted.start()
    try:
        wait_complete(restarted, token, job["id"])
        assert restarted.get(token, job["id"], result=True)["created"][0]["temporary_password"] == "Abcd2345"
    finally:
        restarted.close()


@pytest.mark.parametrize("point,committed", [("roster_before_commit", False), ("roster_after_commit", True)])
def test_accounts_and_recoverable_result_commit_atomically(queue, point, committed):
    jobs, token = queue
    job = create(queue)

    def fault(name):
        if name == point:
            raise RuntimeError("simulated response/commit failure")

    jobs.service.fault_hook = fault
    jobs.process_one()
    with jobs.service.store.connect() as db:
        assert db.execute("SELECT count(*) FROM users WHERE role='student'").fetchone()[0] == int(committed)
    if committed:
        assert jobs.get(token, job["id"], result=True)["created"][0]["user_id"] == "001"
    else:
        assert jobs.get(token, job["id"])["status"] == "failed"
        assert not jobs.key_path(job["id"]).exists()


def test_manifest_conflict_acknowledgement_expiry_are_not_new_imports(queue):
    jobs, token = queue
    job = create(queue)
    error(409, create, queue, name="다른 이름")
    jobs.process_one()
    jobs.acknowledge(token, job["id"])
    assert create(queue)["status"] == "acknowledged"
    error(410, jobs.get, token, job["id"], result=True)
    assert not jobs.key_path(job["id"]).exists()
    jobs.acknowledge(token, job["id"])
    expired = create(queue, request_id="later")
    jobs.process_one()
    with jobs.service.store.connect(write=True) as db:
        db.execute("UPDATE roster_jobs SET expires_at=0 WHERE id=?", (expired["id"],))
    error(410, jobs.get, token, expired["id"], result=True)
    assert create(queue, request_id="later")["status"] == "expired"
    with jobs.service.store.connect() as db:
        assert db.execute("SELECT result FROM roster_jobs WHERE id=?", (expired["id"],)).fetchone()[0] is None
    assert not jobs.key_path(expired["id"]).exists()


def test_owner_epoch_checked_before_commit_and_for_result_access(queue, monkeypatch):
    jobs, token = queue
    job = create(queue)
    prepare = jobs.service.prepare_roster

    def revoke(*args):
        value = prepare(*args)
        with jobs.service.store.connect(write=True) as db:
            db.execute("UPDATE users SET epoch=epoch+1 WHERE user_id='admin'")
        return value

    monkeypatch.setattr(jobs.service, "prepare_roster", revoke)
    jobs.process_one()
    new = jobs.service.login("admin", ADMIN_PASSWORD, "local")["token"]
    error(404, jobs.get, new, job["id"])
    error(401, jobs.get, token, job["id"])
    error(409, jobs.create, new, "request_1", [{"user_id": "001", "name": "홍길동", "group": "A"}])
    assert jobs.list(new) == []
    with jobs.service.store.connect() as db:
        assert db.execute("SELECT count(*) FROM users WHERE role='student'").fetchone()[0] == 0
        assert db.execute("SELECT status FROM roster_jobs").fetchone()[0] == "revoked"


def test_other_admin_and_student_cannot_fetch_job(queue):
    jobs, token = queue
    job = create(queue)
    jobs.process_one()
    password = jobs.get(token, job["id"], result=True)["created"][0]["temporary_password"]
    student = jobs.service.login("001", password, "student")["token"]
    error(403, jobs.get, student, job["id"], result=True)
    with jobs.service.store.connect(write=True) as db:
        db.execute("INSERT INTO users(id,user_id,name,role,password_hash,must_change_password) "
                   "SELECT ?,'other','다른 관리자','admin',password_hash,0 FROM users WHERE user_id='admin'", (uuid.uuid4().hex,))
    other = jobs.service.login("other", ADMIN_PASSWORD, "other")["token"]
    error(404, jobs.get, other, job["id"], result=True)
    error(404, jobs.acknowledge, other, job["id"])


@pytest.mark.parametrize("damage", ["missing_key", "invalid_ciphertext"])
def test_damaged_result_is_reported_without_reapplying_accounts(queue, damage):
    jobs, token = queue
    job = create(queue)
    jobs.process_one()
    if damage == "missing_key":
        jobs.key_path(job["id"]).unlink()
    else:
        with jobs.service.store.connect(write=True) as db:
            db.execute("UPDATE roster_jobs SET result=? WHERE id=?", (b"invalid", job["id"]))
    error(410, jobs.get, token, job["id"], result=True)
    assert jobs.get(token, job["id"])["status"] == "unavailable"
    assert create(queue)["status"] == "unavailable"
    assert not jobs.key_path(job["id"]).exists()
    jobs.acknowledge(token, job["id"])


def test_live_result_is_discoverable_among_many_newer_failure_notices(queue):
    jobs, token = queue
    job = create(queue)
    jobs.process_one()
    with jobs.service.store.connect(write=True) as db:
        row = db.execute("SELECT * FROM roster_jobs WHERE id=?", (job["id"],)).fetchone()
        for i in range(60):
            db.execute("INSERT INTO roster_jobs(id,owner_pk,owner_epoch,request_id,manifest_digest,status,created_at,expires_at,total_rows) "
                       "VALUES (?,?,?,?,?,'failed',?,?,1)",
                       (uuid.uuid4().hex, row["owner_pk"], row["owner_epoch"], f"failed_{i}", "digest", time.time() + i, time.time() + 100))
    visible = jobs.list(token)
    assert len(visible) == 50 and visible[0]["id"] == job["id"]


def test_queue_is_bounded_and_worker_owner_is_exclusive(queue):
    jobs, token = queue
    for index in range(MAX_PENDING_JOBS):
        create(queue, request_id=f"job_{index}")
    error(429, create, queue, request_id="too_many")
    assert len(list(jobs.directory.glob("*.key"))) == MAX_PENDING_JOBS
    # Block hashing to keep the first worker alive while testing ownership.
    gate = threading.Event()
    original = jobs.service.prepare_roster
    jobs.service.prepare_roster = lambda *args: (gate.wait(5), original(*args))[1]
    jobs.start()
    try:
        import portalocker
        with pytest.raises(portalocker.exceptions.LockException):
            RosterJobs(Service(jobs.service.config)).start()
    finally:
        gate.set()
        jobs.close()


def test_api_accepts_without_waiting_for_hash_and_reconnects(hub, monkeypatch):
    client, config, admin = hub
    started, release = threading.Event(), threading.Event()
    prepare = client.app.state.service.prepare_roster

    def blocked(*args):
        started.set()
        assert release.wait(5)
        return prepare(*args)

    monkeypatch.setattr(client.app.state.service, "prepare_roster", blocked)
    body = {"request_id": "lost_response", "rows": [{"user_id": "async_001", "name": "명단"}]}
    try:
        response = client.post("/api/admin/roster/jobs", headers=auth(admin), json=body)
        assert response.status_code == 202, response.text
        assert started.wait(2)
        job_id = response.json()["id"]
        assert client.post("/api/admin/roster/jobs", headers=auth(admin), json=body).json()["id"] == job_id
        assert client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=auth(admin)).status_code == 409
    finally:
        release.set()
    wait_complete(client.app.state.roster_jobs, admin["token"], job_id)
    result = client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=auth(admin))
    assert result.status_code == 200 and len(result.json()["created"]) == 1
    assert client.delete(f"/api/admin/roster/jobs/{job_id}/result", headers=auth(admin)).status_code == 200
    assert client.get(f"/api/admin/roster/jobs/{job_id}/result", headers=auth(admin)).status_code == 410


def test_worker_recovers_when_failure_record_and_first_requeue_both_fail(queue, monkeypatch):
    jobs, token = queue
    job = create(queue)
    original_prepare = jobs.service.prepare_roster
    original_connect = jobs.service.store.connect
    broken_writes = 0
    preparations = 0

    @contextmanager
    def connection(write=False):
        nonlocal broken_writes
        if write and broken_writes:
            broken_writes -= 1
            raise sqlite3.OperationalError("temporary storage failure")
        with original_connect(write=write) as db:
            yield db

    def prepare(*args):
        nonlocal broken_writes, preparations
        preparations += 1
        if preparations == 1:
            broken_writes = 2  # failure recording, then initial requeue attempt
            raise sqlite3.OperationalError("temporary storage failure")
        return original_prepare(*args)

    monkeypatch.setattr(jobs.service.store, "connect", connection)
    monkeypatch.setattr(jobs.service, "prepare_roster", prepare)
    jobs.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with original_connect() as db:
                if db.execute("SELECT status FROM roster_jobs WHERE id=?", (job["id"],)).fetchone()[0] == "completed":
                    break
            time.sleep(0.02)
        assert preparations == 2
        assert jobs.get(token, job["id"], result=True)["created"][0]["user_id"] == "001"
    finally:
        jobs.close()


def test_valid_preview_over_old_json_limit_and_maximum_escaped_rows(hub, monkeypatch):
    client, config, admin = hub
    rows = [(f"{index:05d}", "x" * 180, "g" * 180) for index in range(5500)]
    preview = client.post("/api/admin/roster/preview", headers=auth(admin), content=tsv_bytes(rows))
    assert preview.status_code == 200 and preview.json()["valid"]
    calls = []

    def accepted(token, **body):
        clean = client.app.state.service.roster_rows(body["rows"], body["common_temporary_password"])
        calls.append(len(clean))
        return {"id": "accepted", "status": "queued"}

    monkeypatch.setattr(client.app.state.roster_jobs, "create", accepted)
    body = {"request_id": "large", "rows": [{key: row[key] for key in ("user_id", "name", "group")} for row in preview.json()["rows"]]}
    assert len(json.dumps(body).encode()) > 2 * 1024**2
    assert client.post("/api/admin/roster/jobs", headers=auth(admin), json=body).status_code == 202
    # Astral characters are the worst JSON escape expansion (12 bytes/codepoint).
    body["rows"] = [{"user_id": "😀" * 123 + f"{i:05d}", "name": "😀" * 200, "group": "😀" * 200} for i in range(10000)]
    raw = json.dumps(body, ensure_ascii=True).encode()
    assert len(raw) < ROSTER_JSON_BYTES
    response = client.post("/api/admin/roster/jobs", headers={**auth(admin), "Content-Type": "application/json"}, content=raw)
    assert response.status_code == 202, response.text
    assert calls == [5500, 10000]


def test_large_roster_authentication_precedes_json_parsing(hub):
    client, config, admin = hub
    malformed = b"!" * (3 * 1024**2)
    response = client.post("/api/admin/roster/jobs", content=malformed, headers={"Content-Type": "application/json"})
    assert response.status_code == 401
    oversized = client.post("/api/admin/roster/jobs", content=b"", headers={**auth(admin), "Content-Length": str(ROSTER_JSON_BYTES + 1)})
    assert oversized.status_code == 413
