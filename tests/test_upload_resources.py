"""Upload resource failures and disk reservation accounting at capacity."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest
from fastapi import HTTPException

from assignmenthub.service import Service
from test_api import auth, begin, chunk, finish, hub, student


def remaining(service):
    with service.store.connect() as db:
        value = db.execute("SELECT unwritten_bytes FROM disk_reservation").fetchone()[0]
        expected = db.execute("""SELECT COALESCE(SUM(f.size-f.offset),0) FROM files f
            JOIN uploads u ON u.id=f.upload_id
            WHERE u.status IN ('uploading','paused','verifying','finalizing')""").fetchone()[0]
    assert value == expected
    return value


@pytest.mark.parametrize("point", ["write", "flush", "fsync", "close"])
def test_chunk_io_failure_returns_slot_and_upload_lock(hub, monkeypatch, point):
    client, _, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one.txt", b"12345678")]).json()
    service = client.app.state.service
    service.slots = threading.BoundedSemaphore(1)
    original_open, original_fsync = Path.open, os.fsync
    injected = False

    class FailingFile:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            original = getattr(self.handle, name)
            if name != point:
                return original

            def call(*args, **kwargs):
                nonlocal injected
                if not injected:
                    injected = True
                    if point == "write":
                        self.handle.write(args[0][:3])
                    elif point == "close":
                        self.handle.close()
                    raise OSError("injected file " + point + " failure")
                return original(*args, **kwargs)
            return call

    def open_file(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return FailingFile(handle) if path.suffix == ".part" else handle

    def fsync(fd):
        nonlocal injected
        if point == "fsync" and not injected:
            injected = True
            raise OSError("injected fsync failure")
        return original_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_file)
        patch.setattr(os, "fsync", fsync)
        assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 507
    assert injected
    assert not service.inflight_bytes
    assert service.lock(upload["id"]).acquire(blocking=False)
    service.lock(upload["id"]).release()
    assert service.slots.acquire(blocking=False)
    service.slots.release()
    expected_offset = 8 if point == "close" else 0
    assert service.get_upload(user["token"], upload["id"])["files"][0]["offset"] == expected_offset
    assert remaining(service) == 8 - expected_offset
    # A lost response after close can retry the committed chunk idempotently.
    assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 200
    assert finish(client, user, upload).json()["status"] == "completed"
    other = begin(client, user, [("two.txt", b"abcdefgh")]).json()
    assert chunk(client, user, other, other["files"][0], 0, b"abcdefgh").status_code == 200


def streamed_chunk(client, user, upload, blocks):
    messages = iter({"type": "http.request", "body": block, "more_body": i + 1 < len(blocks)}
                    for i, block in enumerate(blocks))
    responses = []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "PATCH",
             "scheme": "http", "path": f"/api/uploads/{upload['id']}/files/{upload['files'][0]['id']}",
             "raw_path": b"/", "query_string": b"offset=0",
             "headers": [(b"authorization", ("Bearer " + user["token"]).encode()),
                         (b"x-chunk-sha256", hashlib.sha256(b"".join(blocks)).hexdigest().encode())],
             "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 18991)}

    async def receive():
        return next(messages)

    async def send(message):
        responses.append(message)

    asyncio.run(client.app(scope, receive, send))
    return responses[0]["status"]


def test_reserved_disk_boundary_credits_uncommitted_bytes_once(hub, monkeypatch):
    client, config, admin = hub
    user = student(client, admin)
    one = begin(client, user, [("one.txt", b"12345678")]).json()
    two = begin(client, user, [("two.txt", b"abcdefgh")]).json()
    service = client.app.state.service
    config.min_free_bytes = 100
    root = config.root

    def disk_usage(_):
        materialized = sum(p.stat().st_size for directory in ("tmp", "submissions")
                           for p in (root / directory).rglob("*") if p.is_file())
        return SimpleNamespace(free=116 - materialized)

    monkeypatch.setattr("assignmenthub.service.shutil.disk_usage", disk_usage)
    checks = []

    def at_block(point):
        if point != "during_chunk":
            return
        with service.store.connect() as db:
            service.check_disk(db)
            with pytest.raises(HTTPException) as rejected:
                service.check_disk(db, extra=1)
            assert rejected.value.status_code == 507
        checks.append(True)

    service.fault_hook = at_block
    commit = service.commit_chunk

    def checked_commit(*args):
        value = commit(*args)
        # Bytes remain in the in-flight map after the offset transaction commits.
        # Crediting them again would admit one more byte beyond available space.
        at_block("during_chunk")
        return value

    monkeypatch.setattr(service, "commit_chunk", checked_commit)
    assert streamed_chunk(client, user, one, [b"1234", b"5678"]) == 200
    assert checks and remaining(service) == 8
    assert begin(client, user, [("extra.txt", b"!")]).status_code == 507
    assert finish(client, user, one).json()["status"] == "completed"
    assert chunk(client, user, two, two["files"][0], 0, b"abcdefgh").status_code == 200
    assert remaining(service) == 0
    assert client.post(f"/api/uploads/{two['id']}/cancel", headers=auth(user)).status_code == 200
    assert begin(client, user, [("reclaimed.txt", b"abcdefgh")]).status_code == 200
    assert remaining(service) == 8


@pytest.mark.parametrize("transfers", [1, 4])
def test_many_pending_files_never_walked_during_chunk(hub, monkeypatch, transfers):
    client, config, admin = hub
    user = student(client, admin)
    service = client.app.state.service
    uploads = []
    while len(uploads) < transfers:
        upload = begin(client, user, [("active.txt", b"12345678")]).json()
        if any(service.lock(other["id"]) is service.lock(upload["id"]) for other in uploads):
            service.cancel(user["token"], upload["id"])
        else:
            uploads.append(upload)
    with service.store.connect(write=True) as db:
        for index in range(1000):
            uid = "pending-" + str(index)
            db.execute("""INSERT INTO uploads
                (id,user_pk,assignment_id,request_id,manifest,status,total_bytes,created_at,updated_at)
                VALUES (?,?,?,?,'[]','paused',1,?,?)""",
                       (uid, user["user"]["id"], upload["assignment_id"], uid, time.time(), time.time()))
            db.execute("INSERT INTO files(id,upload_id,ordinal,name,size,sha256) VALUES (?,?,0,'pending.txt',1,?)",
                       (uid, uid, hashlib.sha256(b"x").hexdigest()))
    assert remaining(service) == 1000 + transfers * 8
    config.min_free_bytes = 100
    temp_root = config.root / "tmp"

    def disk_usage(_):
        materialized = sum(path.stat().st_size for path in temp_root.rglob("*.part"))
        return SimpleNamespace(free=1100 + transfers * 8 - materialized)

    monkeypatch.setattr("assignmenthub.service.shutil.disk_usage", disk_usage)
    original_path = service.file_path
    visited = []

    def current_file_only(row, file, final=False):
        assert row["id"] in {upload["id"] for upload in uploads}, "disk check walked an unrelated pending file"
        visited.append(file["id"])
        return original_path(row, file, final)

    monkeypatch.setattr(service, "file_path", current_file_only)
    barrier = threading.Barrier(transfers)
    service.fault_hook = lambda point: barrier.wait(timeout=10) if point == "during_chunk" else None
    with ThreadPoolExecutor(max_workers=transfers) as pool:
        futures = [pool.submit(streamed_chunk, client, user, upload,
                               [b"1", b"2", b"3", b"4", b"5", b"6", b"7", b"8"]) for upload in uploads]
        assert [future.result(timeout=30) for future in futures] == [200] * transfers
    assert len(visited) == transfers
    assert remaining(service) == 1000


def test_reservation_counter_rebuild_and_chunk_commit_rollback(hub):
    client, config, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("one.txt", b"12345678abcdefgh")]).json()
    service = client.app.state.service

    def fail_commit(point):
        if point == "chunk_before_commit":
            raise OSError("injected chunk commit failure")

    service.fault_hook = fail_commit
    assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 507
    assert remaining(service) == 16
    service.fault_hook = lambda _: None
    assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 200
    assert remaining(service) == 8
    path = next(config.root.glob("tmp/*/*.part"))
    with path.open("ab") as handle:
        handle.write(b"crash")
    with service.store.connect(write=True) as db:
        db.execute("UPDATE disk_reservation SET unwritten_bytes=999")
    restarted = Service(config)
    restarted.recover()
    assert path.read_bytes() == b"12345678"
    assert remaining(restarted) == 8
    restarted.cancel(user["token"], upload["id"])
    assert remaining(restarted) == 0
