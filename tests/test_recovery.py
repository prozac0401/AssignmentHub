"""Fault injection with small files. Distinct from real socket/process crash tests."""
import asyncio
import hashlib
import sqlite3
import time
from collections import namedtuple

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from assignmenthub.api import create_app
from assignmenthub.service import Service
from test_api import hub, auth, student, begin, send_file, chunk, finish, complete_one, login, NEW_PASSWORD


@pytest.mark.parametrize("point", ["after_move", "final_before_commit", "after_commit"])
def test_u08_commit_failure_and_restart_recovery(hub, point):
    client, config, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one", b"12345678"), ("two", b"87654321")]).json()
    send_file(client, user, upload, 0, b"12345678")
    send_file(client, user, upload, 1, b"87654321")

    def fault(at):
        if at == point:
            raise sqlite3.OperationalError("simulated disk full")
    client.app.state.service.fault_hook = fault
    response = finish(client, user, upload)
    assert response.status_code == 507
    client.app.state.service.fault_hook = lambda _: None
    with TestClient(create_app(config)) as restarted:
        again = login(restarted, "001", NEW_PASSWORD)
        done = finish(restarted, again, upload)
        assert done.status_code == 200 and done.json()["status"] == "completed"
        assert restarted.get("/api/quota", headers=auth(again)).json()["used_bytes"] == 16
        assert restarted.get("/api/quota", headers=auth(again)).json()["reserved_bytes"] == 0
        for item, expected in zip(done.json()["files"], (b"12345678", b"87654321")):
            assert restarted.get(f"/api/files/{item['id']}/download", headers=auth(again)).content == expected


@pytest.mark.parametrize("point", ["during_chunk", "chunk_before_commit"])
def test_q02_chunk_write_or_db_error_rolls_back_offset(hub, point):
    client, _, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one", b"12345678")]).json()
    service = client.app.state.service

    def fault(at):
        if at == point:
            raise OSError(28, "simulated disk full")
    service.fault_hook = fault
    assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 507
    status = client.get(f"/api/uploads/{upload['id']}", headers=auth(user)).json()
    assert status["files"][0]["offset"] == 0
    assert next(service.config.root.glob("tmp/*/*.part")).stat().st_size == 0
    service.fault_hook = lambda _: None
    send_file(client, user, upload, 0, b"12345678")
    assert finish(client, user, upload).json()["status"] == "completed"


def test_u08_uncommitted_tail_truncated_on_restart(hub):
    client, config, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one", b"12345678abcdefgh")]).json()
    assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 200
    path = next(config.root.glob("tmp/*/*.part"))
    with path.open("ab") as f:
        f.write(b"crash")
    Service(config).recover()
    assert path.read_bytes() == b"12345678"
    assert chunk(client, user, upload, upload["files"][0], 8, b"abcdefgh").status_code == 200
    assert finish(client, user, upload).json()["status"] == "completed"


def test_q02_external_space_loss_at_start_and_finalize(hub, monkeypatch):
    client, config, admin = hub
    user = student(client, admin)
    service = client.app.state.service
    usage = namedtuple("usage", "total used free")
    import assignmenthub.service as module
    real = module.shutil.disk_usage
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: usage(100, 100, 0))
    assert begin(client, user, [("one", b"12345678")]).status_code == 507
    assert client.get("/api/quota", headers=auth(user)).json()["reserved_bytes"] == 0
    monkeypatch.setattr(module.shutil, "disk_usage", real)
    upload = begin(client, user, [("one", b"12345678")]).json()
    send_file(client, user, upload, 0, b"12345678")
    config.min_free_bytes = 1
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: usage(100, 100, 0))
    assert finish(client, user, upload).status_code == 507
    assert not client.get("/api/submissions", headers=auth(user)).json()
    monkeypatch.setattr(module.shutil, "disk_usage", real)
    assert finish(client, user, upload).json()["status"] == "completed"


def test_q03_cleanup_skips_locked_upload_preserves_completed(hub):
    client, _, admin = hub
    user = student(client, admin)
    completed = complete_one(client, user)
    one = begin(client, user, [("one", b"12345678")]).json()
    two = begin(client, user, [("two", b"12345678")]).json()
    service = client.app.state.service
    # Allocate a different stripe to avoid a probabilistic test collision.
    while service.lock(one["id"]) is service.lock(two["id"]):
        client.post(f"/api/uploads/{two['id']}/cancel", headers=auth(user))
        two = begin(client, user, [("two", b"12345678")]).json()
    send_file(client, user, one, 0, b"12345678")
    send_file(client, user, two, 0, b"12345678")
    with service.store.connect(write=True) as db:
        db.execute("UPDATE uploads SET updated_at=0 WHERE id IN (?,?)", (one["id"], two["id"]))
    with service.lock(one["id"]):
        service.cleanup()
    assert client.get(f"/api/uploads/{one['id']}", headers=auth(user)).json()["status"] == "uploading"
    assert client.get(f"/api/uploads/{two['id']}", headers=auth(user)).json()["status"] == "expired"
    quota = client.get("/api/quota", headers=auth(user)).json()
    assert (quota["used_bytes"], quota["reserved_bytes"]) == (5, 8)
    assert client.get(f"/api/files/{completed['files'][0]['id']}/download", headers=auth(user)).content == b"hello"


def test_a03_reset_during_receive_prevents_commit(hub):
    client, _, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one", b"12345678")]).json()
    service = client.app.state.service

    def reset(at):
        if at == "during_chunk":
            service.reset_password(admin["token"], user["user"]["id"])
    service.fault_hook = reset
    assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 401
    with service.store.connect() as db:
        assert db.execute("SELECT offset FROM files WHERE id=?", (upload["files"][0]["id"],)).fetchone()[0] == 0
    assert next(service.config.root.glob("tmp/*/*.part")).stat().st_size == 0


def test_full_file_hash_rejects_different_reselected_content(hub):
    client, _, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("same-name", b"12345678")]).json()
    send_file(client, user, upload, 0, b"abcdefgh")
    response = finish(client, user, upload)
    assert response.status_code == 422
    assert client.get(f"/api/uploads/{upload['id']}", headers=auth(user)).json()["status"] == "failed"
    quota = client.get("/api/quota", headers=auth(user)).json()
    assert quota["used_bytes"] == quota["reserved_bytes"] == 0


def test_s01_origin_expiry_and_direct_upload_logout(hub):
    client, _, admin = hub
    user = student(client, admin)
    assert client.get("/api/quota", headers={**auth(user), "Origin": "http://evil.example"}).status_code == 403
    assert client.post("/upload", data={"token": user["token"]}).status_code == 200
    assert client.post("/upload", data={"token": user["token"]}, headers={"Origin": "http://evil.example"}).status_code == 403
    client.post("/api/auth/logout", headers=auth(user))
    assert client.post("/upload", data={"token": user["token"]}).status_code == 401
    assert client.get("/api/quota", headers=auth(user)).status_code == 401
    fresh = login(client, "001", NEW_PASSWORD)
    with client.app.state.service.store.connect(write=True) as db:
        db.execute("UPDATE sessions SET expires=0 WHERE digest=?", (client.app.state.service.digest(fresh["token"]),))
    assert client.get("/api/quota", headers=auth(fresh)).status_code == 401


def test_existing_moved_file_corruption_is_not_completed(hub):
    client, config, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one", b"12345678")]).json()
    send_file(client, user, upload, 0, b"12345678")
    service = client.app.state.service
    def fault(point):
        if point == "after_move":
            raise OSError("simulated crash after move")
    service.fault_hook = fault
    assert finish(client, user, upload).status_code == 507
    service.fault_hook = lambda _: None
    path = next(p for p in config.root.glob("submissions/*/*/*/*") if p.is_file())
    path.write_bytes(b"corrupt!")
    assert finish(client, user, upload).status_code == 409
    assert client.get("/api/submissions", headers=auth(user)).json() == []


def test_unauthorized_chunk_does_not_modify_victim_upload(hub):
    client, _, admin = hub
    one, two = student(client, admin), student(client, admin, "002")
    upload = begin(client, one, [("one", b"12345678")]).json()
    before = client.get(f"/api/uploads/{upload['id']}", headers=auth(one)).json()
    assert chunk(client, two, upload, upload["files"][0], 0, b"12345678").status_code == 404
    after = client.get(f"/api/uploads/{upload['id']}", headers=auth(one)).json()
    assert before == after


def test_busy_finalization_does_not_exhaust_upload_worker_pool(hub):
    from concurrent.futures import ThreadPoolExecutor
    client, _, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one", b"12345678")]).json()
    with client.app.state.service.lock(upload["id"]):
        with ThreadPoolExecutor(max_workers=48) as pool:
            futures = [pool.submit(finish, client, user, upload) for _ in range(48)]
            assert all(f.result(timeout=10).status_code == 409 for f in futures)
    send_file(client, user, upload, 0, b"12345678")
    assert finish(client, user, upload).json()["status"] == "completed"


def test_u04_asgi_disconnect_mid_chunk_rolls_back(hub):
    client, _, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one", b"12345678")]).json()
    fid = upload["files"][0]["id"]
    messages = iter([{"type": "http.request", "body": b"123", "more_body": True}, {"type": "http.disconnect"}])
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "PATCH", "scheme": "http",
             "path": f"/api/uploads/{upload['id']}/files/{fid}", "raw_path": b"/", "query_string": b"offset=0",
             "headers": [(b"authorization", ("Bearer " + user["token"]).encode()), (b"x-chunk-sha256", hashlib.sha256(b"12345678").hexdigest().encode())],
             "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 18991)}
    async def receive():
        return next(messages)
    async def send(message):
        pass
    from starlette.requests import ClientDisconnect
    with pytest.raises(ClientDisconnect):
        asyncio.run(client.app(scope, receive, send))
    assert client.get(f"/api/uploads/{upload['id']}", headers=auth(user)).json()["files"][0]["offset"] == 0
    send_file(client, user, upload, 0, b"12345678")
    assert finish(client, user, upload).json()["status"] == "completed"


def test_u02_actual_body_without_content_length_is_bounded(hub):
    client, _, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one", b"12345678")]).json()
    messages = iter([{"type": "http.request", "body": b"1234", "more_body": True},
                     {"type": "http.request", "body": b"56789", "more_body": False}])
    responses = []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "PATCH", "scheme": "http",
             "path": f"/api/uploads/{upload['id']}/files/{upload['files'][0]['id']}", "raw_path": b"/", "query_string": b"offset=0",
             "headers": [(b"authorization", ("Bearer " + user["token"]).encode()), (b"x-chunk-sha256", hashlib.sha256(b"123456789").hexdigest().encode())],
             "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 18991)}
    async def receive():
        return next(messages)
    async def send(message):
        responses.append(message)
    asyncio.run(client.app(scope, receive, send))
    assert responses[0]["status"] == 413
    assert client.get(f"/api/uploads/{upload['id']}", headers=auth(user)).json()["files"][0]["offset"] == 0
    send_file(client, user, upload, 0, b"12345678")
    assert finish(client, user, upload).json()["status"] == "completed"


def test_expired_and_full_slots_reject_without_damaging_completed(hub):
    client, _, admin = hub
    user = student(client, admin)
    done = complete_one(client, user)
    upload = begin(client, user, [("one", b"12345678")]).json()
    service = client.app.state.service
    for _ in range(service.config.concurrent_uploads):
        service.slots.acquire()
    try:
        assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 429
    finally:
        for _ in range(service.config.concurrent_uploads):
            service.slots.release()
    with service.store.connect(write=True) as db:
        db.execute("UPDATE uploads SET updated_at=0 WHERE id=?", (upload["id"],))
    assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 410
    assert finish(client, user, upload).status_code == 410
    service.cleanup()
    assert client.get("/api/quota", headers=auth(user)).json()["reserved_bytes"] == 0
    assert client.get(f"/api/files/{done['files'][0]['id']}/download", headers=auth(user)).content == b"hello"


def test_i04_two_instances_survive_external_disk_space_loss(hub, tmp_path, monkeypatch):
    from assignmenthub.config import Config
    from test_api import ADMIN_PASSWORD
    from assignmenthub import service as module
    client, _, admin = hub
    one = student(client, admin)
    saved_one = complete_one(client, one)
    config = Config(instance_id="same_volume", course_name="동일 볼륨", port=18993,
                    storage_root=str(tmp_path / "other"), min_free_bytes=0, chunk_bytes=8)
    Service(config).bootstrap_admin("admin", ADMIN_PASSWORD)
    with TestClient(create_app(config)) as other:
        other_admin = login(other, "admin", ADMIN_PASSWORD)
        two = student(other, other_admin)
        saved_two = complete_one(other, two)
        pending_one = begin(client, one, [("one", b"12345678")]).json()
        pending_two = begin(other, two, [("two", b"12345678")]).json()
        usage = namedtuple("usage", "total used free")
        with monkeypatch.context() as context:
            context.setattr(module.shutil, "disk_usage", lambda _: usage(100, 100, 0))
            for app, token, pending in ((client, one, pending_one), (other, two, pending_two)):
                assert chunk(app, token, pending, pending["files"][0], 0, b"12345678").status_code == 507
        for app, token, saved, pending in ((client, one, saved_one, pending_one), (other, two, saved_two, pending_two)):
            assert app.get(f"/api/files/{saved['files'][0]['id']}/download", headers=auth(token)).content == b"hello"
            assert app.get("/api/quota", headers=auth(token)).json()["used_bytes"] == 5
            send_file(app, token, pending, 0, b"12345678")
            assert finish(app, token, pending).json()["status"] == "completed"
