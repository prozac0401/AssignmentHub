"""Recoverable roster imports with short-lived, encrypted credential results.

Only encrypted input/results enter SQLite. A separate per-job key is destroyed
on acknowledgement, expiration or revocation, including after a process restart.
Account inserts and their encrypted result share one transaction.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
import portalocker

from .service import fail, sync_directory

ROSTER_PREVIEW_BYTES = 5 * 1024**2
# 10,000 rows * (128 + 200 + 200) code points * 12 bytes for escaped
# surrogate pairs, plus JSON keys/punctuation; covers every valid normalized row.
ROSTER_JSON_BYTES = 64 * 1024**2
RESULT_TTL = 3600
MAX_PENDING_JOBS = 4
MAX_LIVE_JOBS = 32
MAX_STORED_BYTES = 128 * 1024**2
ACTIVE_JOBS = ("queued", "running")
LIVE_JOBS = (*ACTIVE_JOBS, "completed")


class JobStopped(Exception):
    pass


class RosterJobs:
    def __init__(self, service):
        self.service = service
        self.directory = service.config.root / "roster-keys"
        self.directory.mkdir(exist_ok=True)
        self.directory.chmod(0o700)
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.worker_lock = None

    def key_path(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            fail(404, "명단 작업을 찾을 수 없습니다.")
        return self.directory / (job_id + ".key")

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def public(row):
        return {key: row[key] for key in (
            "id", "request_id", "status", "created_at", "completed_at", "expires_at",
            "total_rows", "processed_rows", "created_count", "updated_count", "error")}

    def start(self):
        # Refuse a second worker for this store; restart recovery must never reset
        # a job whose original worker is still hashing or committing accounts.
        self.worker_lock = portalocker.Lock(self.service.config.root / "roster-worker.lock", timeout=0)
        self.worker_lock.acquire()
        try:
            self.prune()
            with self.service.store.connect(write=True) as db:
                db.execute("UPDATE roster_jobs SET status='queued',processed_rows=0 WHERE status='running'")
                keep = {row[0] for row in db.execute("SELECT id FROM roster_jobs WHERE status IN ('queued','running','completed')")}
                for path in self.directory.glob("*.key"):
                    if path.stem not in keep:
                        path.unlink(missing_ok=True)
            self.thread = threading.Thread(target=self.run, daemon=True, name="roster-jobs")
            self.thread.start()
        except BaseException:
            self.worker_lock.release()
            raise

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(5)
        # The worker releases its lock in finally; a still-stopping worker must
        # retain ownership until it can no longer commit a job.

    def erase(self, db, row, status, error=None):
        self.key_path(row["id"]).unlink(missing_ok=True)
        sync_directory(self.directory)
        db.execute("UPDATE roster_jobs SET status=?,payload=NULL,result=NULL,error=? WHERE id=?",
                   (status, error, row["id"]))

    def prune(self):
        with self.service.store.connect(write=True) as db:
            rows = list(db.execute("SELECT j.*,u.epoch AS current_epoch,u.active,u.role FROM roster_jobs j "
                                   "JOIN users u ON j.owner_pk=u.id WHERE j.status IN ('queued','running','completed')"))
            for row in rows:
                if row["owner_epoch"] != row["current_epoch"] or not row["active"] or row["role"] != "admin":
                    self.erase(db, row, "revoked", "관리자 계정이 변경되어 작업 권한이 취소되었습니다.")
                elif row["expires_at"] <= time.time():
                    self.erase(db, row, "expired", "명단 작업의 보관 시간이 만료되었습니다.")
                elif not self.key_path(row["id"]).is_file():
                    self.erase(db, row, "unavailable" if row["status"] == "completed" else "failed",
                               "복구 키를 읽을 수 없습니다. 완료된 계정의 비밀번호는 필요 시 초기화하세요.")

    def create(self, token, request_id, rows, update_existing=False, common_temporary_password=None):
        self.service.authenticate(token, admin=True)
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            fail(422, "명단 요청 ID 형식을 확인하세요.")
        clean = self.service.roster_rows(rows, common_temporary_password)
        payload = self.encode({"rows": clean, "update_existing": update_existing,
                               "common_temporary_password": common_temporary_password})
        manifest = self.service.digest("roster-manifest:" + payload.decode("utf-8"))
        self.prune()
        job_id = uuid.uuid4().hex
        key_path = self.key_path(job_id)
        try:
            with self.service.store.connect(write=True) as db:
                admin = self.service.authenticate(token, admin=True, db=db)
                old = db.execute("SELECT * FROM roster_jobs WHERE owner_pk=? AND request_id=?", (admin["id"], request_id)).fetchone()
                if old:
                    if old["owner_epoch"] != admin["epoch"]:
                        fail(409, "관리자 계정 변경 전에 사용한 요청 ID입니다. 새 명단 요청을 시작하세요.")
                    if old["manifest_digest"] != manifest:
                        fail(409, "이 요청 ID는 다른 명단에 사용되었습니다. 새 요청 ID를 사용하세요.")
                    return self.public(old)
                pending = db.execute("SELECT count(*) FROM roster_jobs WHERE status IN ('queued','running')").fetchone()[0]
                live = db.execute("SELECT count(*) FROM roster_jobs WHERE status IN ('queued','running','completed')").fetchone()[0]
                stored = db.execute("SELECT COALESCE(SUM(COALESCE(length(payload),0)+COALESCE(length(result),0)),0) FROM roster_jobs").fetchone()[0]
                if pending >= MAX_PENDING_JOBS or live >= MAX_LIVE_JOBS or stored + len(payload) * 2 > MAX_STORED_BYTES:
                    fail(429, "보관 중인 명단 작업이 많습니다. 기존 결과를 저장하고 닫은 뒤 재시도하세요.")
                key = Fernet.generate_key()
                with key_path.open("xb") as output:
                    key_path.chmod(0o600)
                    output.write(key)
                    output.flush()
                    os.fsync(output.fileno())
                sync_directory(self.directory)
                created = time.time()
                db.execute("INSERT INTO roster_jobs(id,owner_pk,owner_epoch,request_id,manifest_digest,status,payload,created_at,expires_at,total_rows) "
                           "VALUES (?,?,?,?,?,'queued',?,?,?,?)",
                           (job_id, admin["id"], admin["epoch"], request_id, manifest, Fernet(key).encrypt(payload), created, created + RESULT_TTL, len(clean)))
                result = self.public(db.execute("SELECT * FROM roster_jobs WHERE id=?", (job_id,)).fetchone())
        except BaseException:
            # Do not remove a key if SQLite committed but response construction
            # failed: the persistent result must remain recoverable.
            with self.service.store.connect() as db:
                saved = db.execute("SELECT 1 FROM roster_jobs WHERE id=?", (job_id,)).fetchone()
            if not saved:
                key_path.unlink(missing_ok=True)
            raise
        self.wake.set()
        return result

    def owned(self, db, token, job_id):
        self.key_path(job_id)
        admin = self.service.authenticate(token, admin=True, db=db)
        row = db.execute("SELECT * FROM roster_jobs WHERE id=? AND owner_pk=? AND owner_epoch=?",
                         (job_id, admin["id"], admin["epoch"])).fetchone()
        if not row:
            fail(404, "명단 작업을 찾을 수 없습니다.")
        return row

    def list(self, token):
        self.service.authenticate(token, admin=True)
        self.prune()
        with self.service.store.connect() as db:
            admin = self.service.authenticate(token, admin=True, db=db)
            return [self.public(row) for row in db.execute(
                "SELECT * FROM roster_jobs WHERE owner_pk=? AND owner_epoch=? AND status!='acknowledged' "
                "ORDER BY CASE WHEN status IN ('queued','running','completed') THEN 0 ELSE 1 END,created_at DESC LIMIT 50",
                (admin["id"], admin["epoch"]))]

    def get(self, token, job_id, result=False):
        self.service.authenticate(token, admin=True)
        self.prune()
        # Serialize reads with acknowledgement/key deletion.
        unavailable = None
        with self.service.store.connect(write=True) as db:
            row = self.owned(db, token, job_id)
            if not result:
                return self.public(row)
            if row["status"] != "completed":
                fail(409 if row["status"] in ACTIVE_JOBS else 410, row["error"] or "결과가 삭제되었거나 아직 준비되지 않았습니다.")
            try:
                value = json.loads(Fernet(self.key_path(job_id).read_bytes()).decrypt(row["result"]))
            except (OSError, ValueError, InvalidToken):
                unavailable = "명단 결과를 복구할 수 없습니다. 완료된 계정의 비밀번호는 필요 시 초기화하세요."
                self.erase(db, row, "unavailable", unavailable)
        if unavailable:
            fail(410, unavailable)
        return value

    def acknowledge(self, token, job_id):
        self.prune()
        with self.service.store.connect(write=True) as db:
            row = self.owned(db, token, job_id)
            if row["status"] in ACTIVE_JOBS:
                fail(409, "진행 중인 작업입니다. 완료 후 결과를 저장하고 닫으세요.")
            self.erase(db, row, "acknowledged")
            return {"message": "명단 작업 결과와 복구 키를 삭제했습니다."}

    def current_owner(self, db, job):
        owner = db.execute("SELECT * FROM users WHERE id=?", (job["owner_pk"],)).fetchone()
        current = db.execute("SELECT * FROM roster_jobs WHERE id=?", (job["id"],)).fetchone()
        if (not owner or not owner["active"] or owner["role"] != "admin" or owner["epoch"] != job["owner_epoch"]
                or not current or current["status"] not in ACTIVE_JOBS or current["expires_at"] <= time.time()):
            raise JobStopped()
        return owner

    def process_one(self):
        with self.service.store.connect(write=True) as db:
            job = db.execute("SELECT * FROM roster_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not job:
                return False
            job = dict(job)
            db.execute("UPDATE roster_jobs SET status='running',processed_rows=0 WHERE id=?", (job["id"],))
        last_progress = 0

        def progress(count):
            nonlocal last_progress
            if self.stop.is_set():
                raise JobStopped()
            if count == 0 or count == job["total_rows"] or time.monotonic() - last_progress >= 1:
                with self.service.store.connect(write=True) as db:
                    self.current_owner(db, job)
                    db.execute("UPDATE roster_jobs SET processed_rows=? WHERE id=?", (count, job["id"]))
                last_progress = time.monotonic()

        try:
            crypt = Fernet(self.key_path(job["id"]).read_bytes())
            payload = json.loads(crypt.decrypt(job["payload"]))
            prepared = self.service.prepare_roster(payload["rows"], payload["common_temporary_password"], progress)
            if self.stop.is_set():
                raise JobStopped()
            with self.service.store.connect(write=True) as db:
                owner = self.current_owner(db, job)
                result = self.service.commit_roster(db, owner, payload["rows"], prepared,
                                                    payload["update_existing"], payload["common_temporary_password"] is not None)
                completed = time.time()
                db.execute("UPDATE roster_jobs SET status='completed',payload=NULL,result=?,completed_at=?,expires_at=?,created_count=?,updated_count=? WHERE id=?",
                           (crypt.encrypt(self.encode(result)), completed, completed + RESULT_TTL, len(result["created"]), result["updated"], job["id"]))
                self.service.fault_hook("roster_before_commit")
            self.service.fault_hook("roster_after_commit")
        except JobStopped:
            if not self.stop.is_set():
                self.prune()
        except Exception as exc:
            # Never log request bodies, passwords, ciphertext or exception repr.
            message = str(exc.detail) if isinstance(exc, HTTPException) else "명단 등록을 완료하지 못했습니다. 명단을 확인하고 새 작업으로 재시도하세요."
            with self.service.store.connect(write=True) as db:
                current = db.execute("SELECT * FROM roster_jobs WHERE id=?", (job["id"],)).fetchone()
                if current and current["status"] in ACTIVE_JOBS:
                    self.erase(db, current, "failed", message)
        return True

    def run(self):
        recover_running = False
        try:
            while not self.stop.is_set():
                try:
                    if recover_running:
                        with self.service.store.connect(write=True) as db:
                            db.execute("UPDATE roster_jobs SET status='queued',processed_rows=0 WHERE status='running'")
                        recover_running = False
                    self.prune()
                    if self.process_one():
                        continue
                except (OSError, sqlite3.Error):
                    # Temporary storage failure leaves the durable queue intact.
                    # Keep retrying recovery even when its first DB write fails.
                    recover_running = True
                self.wake.wait(1)
                self.wake.clear()
        finally:
            self.worker_lock.release()
