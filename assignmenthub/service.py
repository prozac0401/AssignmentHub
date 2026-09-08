from __future__ import annotations

import csv
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException

from .config import Config
from .db import Store
from .passwords import temporary_password, valid_password_length, valid_temporary_password

ACTIVE = ("uploading", "paused", "verifying", "finalizing")
TERMINAL = ("completed", "failed", "cancelled", "expired")
PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(20))


def fail(code, detail):
    raise HTTPException(code, detail)


def now_iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as f:
        while chunk := f.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def sync_directory(path):
    # Windows fsyncs files; directory fsync is supported on POSIX only.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def safe_cell(value):
    value = str(value if value is not None else "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value


class Service:
    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config)
        self.store.initialize()
        self.secret = (config.root / "auth.secret").read_bytes()
        self.locks_guard = threading.Lock()
        # Fixed stripes bound memory and serialize each upload across chunks/cleanup/finalization.
        self.locks = [threading.Lock() for _ in range(257)]
        self.slots = threading.BoundedSemaphore(config.concurrent_uploads)
        self.fault_hook = lambda point: None  # Tests replace this in process; never exposed over HTTP.

    def lock(self, key):
        return self.locks[int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(self.locks)]

    @contextmanager
    def operation(self, key):
        lock = self.lock(key)
        if not lock.acquire(blocking=False):
            fail(409, "이 제출의 전송/검증이 진행 중입니다. 잠시 뒤 상태를 조회하고 재시도하세요.")
        try:
            yield
        finally:
            lock.release()

    def digest(self, token):
        return hmac.new(self.secret, (self.config.instance_id + ":" + token).encode(), hashlib.sha256).hexdigest()

    def audit(self, db, actor, action, object_id=None, detail=None):
        db.execute("INSERT INTO audit(actor,action,object_id,at,detail) VALUES (?,?,?,?,?)",
                   (actor, action, object_id, time.time(), detail))

    def public_user(self, row):
        return {"id": row["id"], "user_id": row["user_id"], "name": row["name"], "group": row["group_name"],
                "role": row["role"], "active": bool(row["active"]), "must_change_password": bool(row["must_change_password"])}

    def bootstrap_admin(self, user_id, password):
        user_id = self.valid_user_id(user_id)
        self.password_policy(password)
        hashed = PASSWORDS.hash(password)
        with self.store.connect(write=True) as db:
            if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                raise ValueError("초기 관리자는 이미 등록되어 있습니다.")
            db.execute("INSERT INTO users(id,user_id,name,role,password_hash,must_change_password) VALUES (?,?,?,?,?,0)",
                       (uuid.uuid4().hex, user_id, "관리자", "admin", hashed))
            self.audit(db, user_id, "bootstrap_admin")

    @staticmethod
    def valid_user_id(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128 or any(ord(c) < 32 for c in value):
            fail(422, "ID는 앞뒤 공백을 제거한 1~128자의 텍스트여야 합니다.")
        return value.strip()

    @staticmethod
    def password_policy(password):
        if not valid_password_length(password):
            fail(422, "비밀번호는 8~128자여야 합니다.")

    def _new_session(self, db, user, restricted=False, parent=None):
        token = secrets.token_urlsafe(32)
        expires = time.time() + (600 if restricted else self.config.session_hours * 3600)
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (self.digest(token), user["id"], user["epoch"], expires, int(restricted), parent))
        return {"token": token, "must_change_password": bool(restricted), "user": self.public_user(user)}

    def login(self, user_id, password, peer):
        user_id = str(user_id).strip()[:129]
        # Rate limits persist across restarts and are independent of proxy-controlled headers.
        keys = [self.digest("login-id:" + user_id), self.digest("login-peer:" + peer)]
        current = time.time()
        with self.store.connect(write=True) as db:
            db.execute("DELETE FROM login_attempts WHERE window_start < ?", (current - 900,))
            for key, limit in zip(keys, (8, 100)):
                row = db.execute("SELECT * FROM login_attempts WHERE key=?", (key,)).fetchone()
                if row and row["failures"] >= limit:
                    fail(429, "로그인 시도가 너무 많습니다. 15분 뒤 다시 시도하세요.")
                db.execute("INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET failures=failures+1", (key, current))
            user = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        try:
            valid = isinstance(password, str) and len(password) <= 128 and PASSWORDS.verify(user["password_hash"] if user else DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid or not user or not user["active"]:
            fail(401, "ID 또는 비밀번호를 확인하세요.")
        with self.store.connect(write=True) as db:
            fresh = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if fresh["epoch"] != user["epoch"] or not fresh["active"]:
                fail(401, "계정이 변경되었습니다. 다시 로그인하세요.")
            db.execute("DELETE FROM login_attempts WHERE key=?", (keys[0],))
            db.execute("UPDATE login_attempts SET failures=MAX(0,failures-1) WHERE key=?", (keys[1],))
            return self._new_session(db, fresh, bool(fresh["must_change_password"]))

    def authenticate(self, token, allow_restricted=False, admin=False, db=None):
        if not token or len(token) > 256:
            fail(401, "로그인이 필요합니다.")
        if db is None:
            with self.store.connect() as con:
                return self.authenticate(token, allow_restricted, admin, con)
        digest = self.digest(token)
        session = db.execute("SELECT * FROM sessions WHERE digest=?", (digest,)).fetchone()
        user = db.execute("SELECT * FROM users WHERE id=?", (session["user_pk"],)).fetchone() if session else None
        if not session or session["expires"] <= time.time() or not user or not user["active"] or session["epoch"] != user["epoch"]:
            fail(401, "로그인이 만료되었거나 취소되었습니다. 다시 로그인하세요.")
        if session["parent_digest"]:
            parent = db.execute("SELECT * FROM sessions WHERE digest=?", (session["parent_digest"],)).fetchone()
            if not parent or parent["expires"] <= time.time():
                fail(401, "연결한 화면에서 로그아웃되었습니다. 다시 로그인하세요.")
        if not allow_restricted and (session["restricted"] or user["must_change_password"]):
            fail(403, "임시비밀번호를 변경한 뒤 새 비밀번호로 다시 로그인하세요.")
        if admin and user["role"] != "admin":
            fail(403, "관리자 권한이 필요합니다.")
        return dict(user)

    def logout(self, token):
        with self.store.connect(write=True) as db:
            digest = self.digest(token)
            db.execute("DELETE FROM grants WHERE session_digest=?", (digest,))
            db.execute("DELETE FROM sessions WHERE digest=? OR parent_digest=?", (digest, digest))

    def change_password(self, token, current_password, new_password, confirm_password):
        user = self.authenticate(token, allow_restricted=True)
        self.password_policy(new_password)
        if new_password != confirm_password:
            fail(422, "새 비밀번호와 확인 입력이 일치하지 않습니다.")
        try:
            PASSWORDS.verify(user["password_hash"], current_password)
        except (VerificationError, InvalidHashError):
            fail(403, "현재 비밀번호가 맞지 않습니다.")
        if current_password == new_password:
            fail(422, "기존 또는 임시비밀번호와 다른 값을 입력하세요.")
        hashed = PASSWORDS.hash(new_password)
        with self.store.connect(write=True) as db:
            self.authenticate(token, allow_restricted=True, db=db)
            db.execute("UPDATE users SET password_hash=?,must_change_password=0,epoch=epoch+1 WHERE id=?", (hashed, user["id"]))
            db.execute("DELETE FROM sessions WHERE user_pk=?", (user["id"],))
            self.audit(db, user["user_id"], "password_changed", user["id"])

    def make_grant(self, token, kind, object_id=None):
        if kind != "download":
            fail(404, "지원하지 않는 권한 요청입니다.")
        with self.store.connect(write=True) as db:
            self.authenticate(token, db=db)
            code = secrets.token_urlsafe(24)
            db.execute("DELETE FROM grants WHERE expires < ?", (time.time(),))
            db.execute("INSERT INTO grants VALUES (?,?,?,?,?)", (self.digest(code), self.digest(token), kind, object_id, time.time() + 120))
            return code

    def consume_grant(self, code, kind):
        if kind != "download":
            fail(404, "지원하지 않는 권한 요청입니다.")
        with self.store.connect(write=True) as db:
            row = db.execute("SELECT * FROM grants WHERE digest=? AND kind=?", (self.digest(code.strip()), kind)).fetchone()
            if not row or row["expires"] <= time.time():
                fail(401, "일회성 코드가 만료되었거나 이미 사용되었습니다.")
            session = db.execute("SELECT * FROM sessions WHERE digest=?", (row["session_digest"],)).fetchone()
            user = db.execute("SELECT * FROM users WHERE id=?", (session["user_pk"],)).fetchone() if session else None
            if not session or session["expires"] <= time.time() or not user or user["epoch"] != session["epoch"] or not user["active"] or user["must_change_password"]:
                fail(401, "로그인이 취소되었습니다.")
            if session["parent_digest"] and not db.execute("SELECT 1 FROM sessions WHERE digest=? AND expires>?", (session["parent_digest"], time.time())).fetchone():
                fail(401, "상위 로그인이 만료되었습니다.")
            db.execute("DELETE FROM grants WHERE digest=?", (self.digest(code.strip()),))
            return dict(user), row["object_id"], dict(session)

    def roster_preview(self, token, content):
        self.authenticate(token, admin=True)
        if len(content) > 5 * 1024**2:
            fail(413, "명단은 5MiB 이하만 허용됩니다.")
        if content.startswith(b"PK\x03\x04"):
            fail(422, "엑셀 파일 대신 TSV 명단을 올리거나 셀을 복사해 붙여넣어 주세요.")
        try:
            # Excel's Unicode Text export uses UTF-16 with a BOM. Never guess
            # legacy encodings or coerce identifiers to numbers.
            encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = content.decode(encoding)
        except UnicodeError:
            fail(422, "TSV 명단은 UTF-8 또는 BOM이 있는 UTF-16으로 저장하거나 직접 붙여넣어 주세요.")
        if "\x00" in text:
            fail(422, "TSV 명단에 읽을 수 없는 문자가 있습니다. UTF-8로 저장하거나 직접 붙여넣어 주세요.")
        reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t", strict=True)
        try:
            headers = next((row for row in reader if any(cell.strip() for cell in row)), [])
            headers = [cell.strip() for cell in headers]
            if len(headers) > 30:
                fail(422, "명단은 10,000명, 30열 이내로 작성하세요.")
            errors = []
            if any(headers.count(key) != 1 for key in ("user_id", "name")) or headers.count("group") > 1:
                return {"rows": [], "errors": ["첫 행을 탭으로 구분하고 user_id와 name을 각각 한 번 넣어 주세요. group은 선택입니다."], "valid": False}
            columns = {key: headers.index(key) for key in ("user_id", "name", "group") if key in headers}
            seen, rows = set(), []
            with self.store.connect() as db:
                while True:
                    number = reader.line_num + 1
                    cells = next(reader, None)
                    if cells is None:
                        break
                    if not any(cell.strip() for cell in cells):
                        continue
                    if len(rows) >= 10000 or len(cells) > 30:
                        fail(422, "명단은 10,000명, 30열 이내로 작성하세요.")
                    values = {key: cells[idx] if idx < len(cells) else "" for key, idx in columns.items()}
                    problems = []
                    if len(cells) > len(headers):
                        problems.append("첫 행보다 열이 많습니다. 탭 구분과 따옴표를 확인하세요.")
                    for key in ("user_id", "name"):
                        if not values.get(key, "").strip():
                            problems.append(f"{key}: 필수 값이 비어 있습니다.")
                    uid = values.get("user_id", "").strip()
                    name = values.get("name", "").strip()
                    group = values.get("group", "").strip()
                    if len(uid) > 128 or len(name) > 200 or len(group) > 200 or any(ord(c) < 32 for c in uid):
                        problems.append("ID/이름/그룹 길이 또는 제어문자를 확인하세요.")
                    if uid in seen:
                        problems.append("명단 내 중복 ID입니다.")
                    seen.add(uid)
                    existing = db.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
                    if existing and existing["role"] == "admin":
                        problems.append("관리자 ID는 수강생 명단으로 수정할 수 없습니다.")
                    action = "new" if not existing else ("update" if (existing["name"], existing["group_name"]) != (name, group) else "unchanged")
                    rows.append({"row": number, "user_id": uid, "name": name, "group": group, "action": action,
                                 "errors": problems, "existing": self.public_user(existing) if existing else None})
            if not rows:
                errors.append("등록할 행이 없습니다.")
            return {"rows": rows, "errors": errors, "valid": not errors and all(not r["errors"] for r in rows)}
        except csv.Error:
            fail(422, f"TSV {reader.line_num}행 부근을 읽을 수 없습니다. 따옴표와 셀 길이를 확인하세요.")

    def roster_apply(self, token, rows, update_existing=False, common_temporary_password=None):
        admin = self.authenticate(token, admin=True)
        if common_temporary_password is not None and not valid_temporary_password(common_temporary_password):
            fail(422, "공통 임시비밀번호는 영문·숫자 8자리(8바이트)로 입력하세요.")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
            fail(422, "1~10,000행의 명단이 필요합니다.")
        clean, seen = [], set()
        for row in rows:
            uid = self.valid_user_id(row.get("user_id"))
            if uid in seen:
                fail(409, "명단 내 중복 ID입니다. 명단 전체가 반영되지 않았습니다.")
            seen.add(uid)
            name, group = row.get("name"), row.get("group", "")
            if not isinstance(name, str) or not name.strip() or len(name) > 200 or not isinstance(group, str) or len(group) > 200:
                fail(422, "이름은 필수 텍스트이며 이름/그룹은 200자 이내입니다.")
            clean.append((uid, name.strip(), group.strip()))
        # Argon2 runs outside SQLite write transactions.
        with self.store.connect() as db:
            existing = {r["user_id"]: dict(r) for r in db.execute("SELECT * FROM users")}
        prepared = {}
        for uid, name, group in clean:
            if uid not in existing:
                password = common_temporary_password if common_temporary_password is not None else temporary_password()
                prepared[uid] = (password, PASSWORDS.hash(password))
        created, updated = [], 0
        with self.store.connect(write=True) as db:
            self.authenticate(token, admin=True, db=db)
            for uid, name, group in clean:
                old = db.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
                if old:
                    if old["role"] != "student":
                        fail(409, "관리자 계정은 명단으로 수정할 수 없습니다.")
                    if update_existing and (name, group) != (old["name"], old["group_name"]):
                        db.execute("UPDATE users SET name=?,group_name=? WHERE id=?", (name, group, old["id"]))
                        updated += 1
                else:
                    if uid not in prepared:
                        fail(409, "명단 상태가 변경되었습니다. 미리보기를 다시 확인하세요.")
                    password, hashed = prepared[uid]
                    db.execute("INSERT INTO users(id,user_id,name,group_name,role,password_hash) VALUES (?,?,?,?,'student',?)", (uuid.uuid4().hex, uid, name, group, hashed))
                    created.append({"user_id": uid, "name": name, "temporary_password": password})
            mode = "common" if common_temporary_password is not None else "individual"
            self.audit(db, admin["user_id"], "roster_apply", detail=f"created={len(created)},updated={updated},temporary_password_mode={mode}")
        return {"created": created, "updated": updated}

    def reset_password(self, token, user_pk):
        admin = self.authenticate(token, admin=True)
        password = temporary_password()
        hashed = PASSWORDS.hash(password)
        with self.store.connect(write=True) as db:
            self.authenticate(token, admin=True, db=db)
            target = db.execute("SELECT * FROM users WHERE id=?", (user_pk,)).fetchone()
            if not target or target["role"] != "student":
                fail(404, "수강생 계정을 찾을 수 없습니다.")
            db.execute("UPDATE users SET password_hash=?,must_change_password=1,epoch=epoch+1 WHERE id=?", (hashed, user_pk))
            db.execute("DELETE FROM sessions WHERE user_pk=?", (user_pk,))
            self.audit(db, admin["user_id"], "password_reset", user_pk)
        return {"temporary_password": password}

    def set_active(self, token, user_pk, active):
        with self.store.connect(write=True) as db:
            admin = self.authenticate(token, admin=True, db=db)
            target = db.execute("SELECT * FROM users WHERE id=? AND role='student'", (user_pk,)).fetchone()
            if not target:
                fail(404, "수강생 계정을 찾을 수 없습니다.")
            db.execute("UPDATE users SET active=?,epoch=epoch+1 WHERE id=?", (int(active), user_pk))
            db.execute("DELETE FROM sessions WHERE user_pk=?", (user_pk,))
            self.audit(db, admin["user_id"], "account_activate" if active else "account_deactivate", user_pk)

    def quota(self, token):
        user = self.authenticate(token)
        return {"used_bytes": user["used_bytes"], "reserved_bytes": user["reserved_bytes"], "quota_bytes": self.config.user_quota_bytes,
                "max_file_bytes": self.config.max_file_bytes, "chunk_bytes": self.config.chunk_bytes,
                "max_files": self.config.max_files, "min_free_bytes": self.config.min_free_bytes}

    def file_path(self, upload, file, final=False):
        if final:
            return self.config.root / "submissions" / upload["assignment_id"] / upload["user_pk"] / upload["id"] / file["id"]
        return self.config.root / "tmp" / upload["id"] / (file["id"] + ".part")

    def _upload(self, db, upload_id, user=None):
        row = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if not row or user and row["user_pk"] != user["id"] and user["role"] != "admin":
            fail(404, "업로드를 찾을 수 없습니다.")
        return row

    def upload_result(self, db, row, admin=False):
        user = db.execute("SELECT user_id FROM users WHERE id=?", (row["user_pk"],)).fetchone()
        assignment = db.execute("SELECT title FROM assignments WHERE id=?", (row["assignment_id"],)).fetchone()
        files = []
        for f in db.execute("SELECT * FROM files WHERE upload_id=? ORDER BY ordinal", (row["id"],)):
            data = {k: f[k] for k in ("id", "name", "size", "offset", "sha256", "stored_sha256")}
            if admin and row["status"] == "completed":
                data["storage_path"] = str(self.file_path(row, f, True))
            files.append(data)
        return {"id": row["id"], "assignment_id": row["assignment_id"], "assignment_title": assignment["title"],
                "user_id": user["user_id"], "status": row["status"], "total_bytes": row["total_bytes"],
                "submission_number": row["submission_number"], "version": row["version"], "completed_at": now_iso(row["completed_at"]),
                "updated_at": now_iso(row["updated_at"]), "error": row["error"], "files": files}

    def get_upload(self, token, upload_id):
        user = self.authenticate(token)
        with self.store.connect() as db:
            return self.upload_result(db, self._upload(db, upload_id, user), user["role"] == "admin")

    def check_disk(self, db, extra=0):
        # disk_usage already accounts for materialized bytes. Subtract only unreceived reservations.
        rows = db.execute("SELECT f.id,f.size,f.upload_id,u.assignment_id,u.user_pk FROM files f JOIN uploads u ON f.upload_id=u.id WHERE u.status IN ('uploading','paused','verifying','finalizing')").fetchall()
        remaining = 0
        for row in rows:
            materialized = 0
            temporary = self.config.root / "tmp" / row["upload_id"] / (row["id"] + ".part")
            final = self.config.root / "submissions" / row["assignment_id"] / row["user_pk"] / row["upload_id"] / row["id"]
            for path in (temporary, final):
                try:
                    materialized += path.stat().st_size
                except FileNotFoundError:
                    pass
            remaining += max(0, row["size"] - materialized)
        free = shutil.disk_usage(self.config.root).free
        if free - remaining - extra < self.config.min_free_bytes:
            fail(507, "디스크 여유 공간이 부족합니다. 관리자가 공간을 확보한 뒤 재시도하세요.")

    def start_upload(self, token, assignment_id, request_id, files):
        user = self.authenticate(token)
        if user["role"] != "student":
            fail(403, "관리자는 제출 대상이 아닙니다.")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id):
            fail(422, "유효한 제출 요청 식별자가 필요합니다.")
        if not 1 <= len(files) <= self.config.max_files:
            fail(422, f"파일은 1~{self.config.max_files}개까지 선택하세요.")
        clean = []
        for f in files:
            name, size, sha = f.get("name"), f.get("size"), f.get("sha256")
            if not isinstance(name, str) or not name or len(name) > 255 or any(ord(c) < 32 for c in name):
                fail(422, "파일명은 제어문자를 제외한 1~255자여야 합니다.")
            if type(size) is not int or size < 0 or size > self.config.max_file_bytes:
                fail(413, "파일당 크기 제한을 초과했거나 크기가 잘못되었습니다.")
            if not isinstance(sha, str) or not re.fullmatch("[0-9a-fA-F]{64}", sha):
                fail(422, "파일 전체 SHA-256이 필요합니다.")
            clean.append({"name": name, "size": size, "sha256": sha.lower()})
        manifest = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        total = sum(f["size"] for f in clean)
        upload_id = uuid.uuid4().hex
        with self.store.connect(write=True) as db:
            user = self.authenticate(token, db=db)
            existing = db.execute("SELECT * FROM uploads WHERE user_pk=? AND request_id=?", (user["id"], request_id)).fetchone()
            if existing:
                if existing["manifest"] != manifest or existing["assignment_id"] != assignment_id:
                    fail(409, "같은 요청 식별자에 다른 파일 목록을 사용할 수 없습니다.")
                return self.upload_result(db, existing)
            assignment = db.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
            if not assignment or not assignment["is_open"]:
                fail(409, "접수 중인 과제가 아닙니다.")
            if user["used_bytes"] + user["reserved_bytes"] + total > self.config.user_quota_bytes:
                fail(413, "사용자 누적 한도를 초과합니다. 진행 중인 제출을 취소하거나 관리자에게 문의하세요.")
            self.check_disk(db, total)
            ts = time.time()
            db.execute("INSERT INTO uploads(id,user_pk,assignment_id,request_id,manifest,status,total_bytes,created_at,updated_at) VALUES (?,?,?,?,?,'uploading',?,?,?)",
                       (upload_id, user["id"], assignment_id, request_id, manifest, total, ts, ts))
            for i, f in enumerate(clean):
                db.execute("INSERT INTO files(id,upload_id,ordinal,name,size,sha256) VALUES (?,?,?,?,?,?)",
                           (uuid.uuid4().hex, upload_id, i, f["name"], f["size"], f["sha256"]))
            db.execute("UPDATE users SET reserved_bytes=reserved_bytes+? WHERE id=?", (total, user["id"]))
            return self.upload_result(db, self._upload(db, upload_id))

    def assert_live(self, upload):
        if upload["status"] not in ACTIVE:
            fail(409, f"이 제출은 {upload['status']} 상태입니다.")
        if upload["updated_at"] < time.time() - self.config.upload_ttl_hours * 3600:
            fail(410, "미완료 제출의 보관 시간이 지났습니다.")

    def prepare_chunk(self, token, upload_id, file_id, offset):
        with self.store.connect() as db:
            user = self.authenticate(token, db=db)
            upload = self._upload(db, upload_id, user)
            self.assert_live(upload)
            if upload["status"] not in ("uploading", "paused"):
                fail(409, "검증/저장이 진행 중입니다. 상태를 조회하세요.")
            f = db.execute("SELECT * FROM files WHERE id=? AND upload_id=?", (file_id, upload_id)).fetchone()
            if not f:
                fail(404, "파일을 찾을 수 없습니다.")
            if offset < 0 or offset > f["offset"]:
                fail(409, "서버 확정 offset을 조회한 뒤 재시도하세요.")
            duplicate = None
            if offset < f["offset"]:
                duplicate = db.execute("SELECT * FROM chunks WHERE file_id=? AND offset=?", (file_id, offset)).fetchone()
                if not duplicate:
                    fail(409, "확정된 청크 경계와 일치하지 않습니다.")
            self.check_disk(db)
            return dict(upload), dict(f), dict(duplicate) if duplicate else None

    def commit_chunk(self, token, upload, file, offset, size, digest, duplicate):
        with self.store.connect(write=True) as db:
            user = self.authenticate(token, db=db)
            fresh = self._upload(db, upload["id"], user)
            self.assert_live(fresh)
            if duplicate:
                if (size, digest) != (duplicate["size"], duplicate["sha256"]):
                    fail(409, "이미 받은 청크와 내용이 다릅니다.")
                return file["offset"]
            current = db.execute("SELECT offset FROM files WHERE id=?", (file["id"],)).fetchone()[0]
            if current != offset:
                fail(409, "청크 offset이 변경되었습니다.")
            db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (file["id"], offset, size, digest))
            db.execute("UPDATE files SET offset=? WHERE id=?", (offset + size, file["id"]))
            db.execute("UPDATE uploads SET status='uploading',updated_at=?,error=NULL WHERE id=?", (time.time(), upload["id"]))
            self.fault_hook("chunk_before_commit")
            return offset + size

    def pause(self, upload_id, error):
        with self.store.connect(write=True) as db:
            db.execute("UPDATE uploads SET status='paused',error=?,updated_at=? WHERE id=? AND status IN ('uploading','paused')", (error, time.time(), upload_id))

    def touch(self, token, upload_id):
        with self.store.connect(write=True) as db:
            user = self.authenticate(token, db=db)
            upload = self._upload(db, upload_id, user)
            if upload["status"] not in ("uploading", "paused"):
                fail(409, "전송 상태가 변경되었습니다.")
            db.execute("UPDATE uploads SET updated_at=? WHERE id=?", (time.time(), upload_id))

    def _discard_locked(self, upload_id, status):
        with self.store.connect() as db:
            upload = self._upload(db, upload_id)
            if upload["status"] == "completed":
                fail(409, "완료 제출은 삭제할 수 없습니다.")
            files = list(db.execute("SELECT * FROM files WHERE upload_id=?", (upload_id,)))
        # Clean physical files first. Failed unlink keeps reservation and is retried, never hides disk use.
        for f in files:
            for final in (False, True):
                self.file_path(upload, f, final).unlink(missing_ok=True)
        with self.store.connect(write=True) as db:
            current = self._upload(db, upload_id)
            if current["status"] in ACTIVE:
                db.execute("UPDATE users SET reserved_bytes=reserved_bytes-? WHERE id=?", (current["total_bytes"], current["user_pk"]))
            db.execute("UPDATE uploads SET status=?,updated_at=? WHERE id=?", (status, time.time(), upload_id))

    def cancel(self, token, upload_id):
        with self.operation(upload_id):
            with self.store.connect() as db:
                self._upload(db, upload_id, self.authenticate(token, db=db))
            self._discard_locked(upload_id, "cancelled")
        return self.get_upload(token, upload_id)

    def verify(self, token, upload_id):
        with self.operation(upload_id):
            return self._verify_locked(token, upload_id)

    def _verify_locked(self, token, upload_id, recovery=False):
        with self.store.connect(write=True) as db:
            user = self.authenticate(token, db=db) if not recovery else None
            upload = self._upload(db, upload_id, user)
            if upload["status"] == "completed":
                return self.upload_result(db, upload, bool(user and user["role"] == "admin"))
            self.assert_live(upload)
            files = [dict(f) for f in db.execute("SELECT * FROM files WHERE upload_id=? ORDER BY ordinal", (upload_id,))]
            if any(f["offset"] != f["size"] for f in files):
                fail(409, "선택한 모든 파일을 수신해야 제출을 확정할 수 있습니다.")
            self.check_disk(db)
            db.execute("UPDATE uploads SET status='verifying',updated_at=?,error=NULL WHERE id=?", (time.time(), upload_id))
        try:
            for f in files:
                tmp, final = self.file_path(upload, f), self.file_path(upload, f, True)
                path = final if final.exists() else tmp
                if f["size"] == 0 and not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
                size, sha = hash_file(path)
                if size != f["size"] or sha != f["sha256"]:
                    self._discard_locked(upload_id, "failed")
                    fail(422, "파일 크기/해시 검증에 실패했습니다. 같은 파일을 확인하고 새 제출을 시작하세요.")
                with path.open("r+b") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                with self.store.connect(write=True) as db:
                    if not recovery:
                        self.authenticate(token, db=db)
                    db.execute("UPDATE files SET stored_sha256=? WHERE id=?", (sha, f["id"]))
                    db.execute("UPDATE uploads SET updated_at=? WHERE id=?", (time.time(), upload_id))
            with self.store.connect(write=True) as db:
                db.execute("UPDATE uploads SET status='finalizing',updated_at=? WHERE id=?", (time.time(), upload_id))
                return self.upload_result(db, self._upload(db, upload_id))
        except (OSError, sqlite3.Error):
            # Durable verifying state is retryable; never report success on a write/commit error.
            fail(507, "검증 파일 또는 DB를 읽거나 동기화하지 못했습니다. 공간/권한 확인 후 재시도하세요.")

    def complete(self, token, upload_id, recovery=False):
        with self.operation(upload_id):
            with self.store.connect() as db:
                user = self.authenticate(token, db=db) if not recovery else None
                row = self._upload(db, upload_id, user)
                if row["status"] == "completed":
                    return self.upload_result(db, row, bool(user and user["role"] == "admin"))
                self.assert_live(row)
                status = row["status"]
            if status != "finalizing":
                self._verify_locked(token, upload_id, recovery)
            with self.store.connect() as db:
                upload = dict(self._upload(db, upload_id))
                files = [dict(f) for f in db.execute("SELECT * FROM files WHERE upload_id=? ORDER BY ordinal", (upload_id,))]
                self.check_disk(db)
            try:
                for f in files:
                    src, dst = self.file_path(upload, f), self.file_path(upload, f, True)
                    if not recovery:
                        self.authenticate(token)
                    if not dst.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(src, dst)
                        sync_directory(dst.parent)
                    else:
                        size, sha = hash_file(dst)
                        if size != f["size"] or sha != f["sha256"]:
                            fail(409, "최종 저장 파일이 변경되었습니다. 관리자 점검이 필요합니다.")
                    if not f["stored_sha256"] or dst.stat().st_size != f["size"]:
                        fail(409, "검증된 최종 파일이 없습니다.")
                self.fault_hook("after_move")
                with self.store.connect(write=True) as db:
                    if not recovery:
                        self.authenticate(token, db=db)
                    fresh = self._upload(db, upload_id)
                    if fresh["status"] == "completed":
                        return self.upload_result(db, fresh)
                    self.check_disk(db)
                    number = db.execute("SELECT COALESCE(MAX(submission_number),0)+1 FROM uploads").fetchone()[0]
                    version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM uploads WHERE user_pk=? AND assignment_id=?", (upload["user_pk"], upload["assignment_id"])).fetchone()[0]
                    ts = time.time()
                    db.execute("UPDATE uploads SET status='completed',submission_number=?,version=?,completed_at=?,updated_at=?,error=NULL WHERE id=?", (number, version, ts, ts, upload_id))
                    db.execute("UPDATE users SET reserved_bytes=reserved_bytes-?,used_bytes=used_bytes+? WHERE id=?", (upload["total_bytes"], upload["total_bytes"], upload["user_pk"]))
                    self.fault_hook("final_before_commit")
                self.fault_hook("after_commit")
                with self.store.connect() as db:
                    return self.upload_result(db, self._upload(db, upload_id), bool(user and user["role"] == "admin"))
            except (OSError, sqlite3.Error):
                fail(507, "최종 파일 저장 또는 DB 확정에 실패했습니다. 완료되지 않았으며 상태 조회 후 재시도할 수 있습니다.")

    def recover(self):
        # Called under the exclusive instance supervisor lock, before API accepts requests.
        with self.store.connect() as db:
            pending = [dict(r) for r in db.execute("SELECT * FROM uploads WHERE status IN ('uploading','paused','verifying','finalizing')")]
        for upload in pending:
            if True:  # Startup is exclusive; complete() acquires its own upload lock.
                if upload["updated_at"] < time.time() - self.config.upload_ttl_hours * 3600:
                    self._discard_locked(upload["id"], "expired")
                    continue
                if upload["status"] in ("verifying", "finalizing"):
                    # Recovery checks current account too; revoked accounts wait for renewed authentication.
                    with self.store.connect() as db:
                        user = db.execute("SELECT * FROM users WHERE id=?", (upload["user_pk"],)).fetchone()
                    if user["active"] and not user["must_change_password"]:
                        try:
                            self._verify_locked(None, upload["id"], recovery=True)
                            self.complete(None, upload["id"], recovery=True)
                        except (HTTPException, OSError, sqlite3.Error):
                            pass
                    continue
                with self.store.connect() as db:
                    files = [dict(r) for r in db.execute("SELECT * FROM files WHERE upload_id=?", (upload["id"],))]
                damaged = False
                for f in files:
                    path = self.file_path(upload, f)
                    actual = path.stat().st_size if path.exists() else 0
                    if actual < f["offset"]:
                        damaged = True
                        break
                    if actual > f["offset"]:
                        with path.open("r+b") as handle:
                            handle.truncate(f["offset"])
                            handle.flush()
                            os.fsync(handle.fileno())
                if damaged:
                    self._discard_locked(upload["id"], "failed")
                else:
                    with self.store.connect(write=True) as db:
                        db.execute("UPDATE uploads SET status='paused' WHERE id=?", (upload["id"],))
        return len(pending)

    def cleanup(self):
        with self.store.connect(write=True) as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            db.execute("DELETE FROM grants WHERE expires < ?", (time.time(),))
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM uploads WHERE status IN ('uploading','paused','verifying','finalizing') AND updated_at < ?", (time.time() - self.config.upload_ttl_hours * 3600,))]
        for uid in ids:
            lock = self.lock(uid)
            if not lock.acquire(blocking=False):
                continue
            try:
                with self.store.connect() as db:
                    current = self._upload(db, uid)
                    if current["status"] not in ACTIVE or current["updated_at"] >= time.time() - self.config.upload_ttl_hours * 3600:
                        continue
                self._discard_locked(uid, "expired")
            except (OSError, sqlite3.Error):
                pass
            finally:
                lock.release()

    def download_info(self, user, file_id):
        with self.store.connect() as db:
            f = db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            if not f:
                fail(404, "파일을 찾을 수 없습니다.")
            upload = self._upload(db, f["upload_id"], user)
            if upload["status"] != "completed":
                fail(404, "완료된 제출 파일만 다운로드할 수 있습니다.")
            path = self.file_path(upload, f, True)
            if not path.is_file() or path.stat().st_size != f["size"]:
                fail(409, "완료 파일이 없거나 외부에서 변경되었습니다. 관리자에게 복원을 요청하세요.")
            return path, dict(f)

    def storage(self, token):
        self.authenticate(token, admin=True)
        with self.store.connect() as db:
            used, reserved = db.execute("SELECT COALESCE(SUM(used_bytes),0),COALESCE(SUM(reserved_bytes),0) FROM users").fetchone()
            pending = db.execute("SELECT COALESCE(SUM(f.size-f.offset),0) FROM files f JOIN uploads u ON f.upload_id=u.id WHERE u.status IN ('uploading','paused','verifying','finalizing')").fetchone()[0]
        tmp = sum(p.stat().st_size for p in (self.config.root / "tmp").rglob("*.part") if p.is_file())
        return {"used_bytes": used, "reserved_bytes": reserved, "temp_bytes": tmp, "unwritten_reserved_bytes": pending,
                "free_bytes": shutil.disk_usage(self.config.root).free, "min_free_bytes": self.config.min_free_bytes,
                "user_quota_bytes": self.config.user_quota_bytes, "max_file_bytes": self.config.max_file_bytes,
                "storage_root": str(self.config.root), "chunk_bytes": self.config.chunk_bytes, "concurrent_uploads": self.config.concurrent_uploads}

    def dashboard(self, token, assignment_id, search="", group="", include_inactive=False):
        self.authenticate(token, admin=True)
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM assignments WHERE id=?", (assignment_id,)).fetchone():
                fail(404, "과제를 찾을 수 없습니다.")
            users = [dict(r) for r in db.execute("SELECT * FROM users WHERE role='student' ORDER BY user_id")]
            completed = [self.upload_result(db, r, True) for r in db.execute("SELECT * FROM uploads WHERE assignment_id=? AND status='completed' ORDER BY completed_at DESC", (assignment_id,))]
            pending = db.execute("SELECT COUNT(*) FROM uploads WHERE assignment_id=? AND status IN ('uploading','paused','verifying','finalizing')", (assignment_id,)).fetchone()[0]
            failed = db.execute("SELECT COUNT(*) FROM uploads WHERE assignment_id=? AND status='failed'", (assignment_id,)).fetchone()[0]
        active = [u for u in users if u["active"]]
        submitted = {s["user_id"] for s in completed}
        rows = []
        for u in users:
            if not include_inactive and not u["active"]:
                continue
            if search and search.casefold() not in " ".join((u["user_id"], u["name"], u["group_name"])).casefold():
                continue
            if group and u["group_name"] != group:
                continue
            history = [s for s in completed if s["user_id"] == u["user_id"]]
            rows.append({**self.public_user(u), "submitted": bool(history), "submission_count": len(history), "latest": history[0] if history else None})
        count = sum(u["user_id"] in submitted for u in active)
        visible = {u["user_id"] for u in rows}
        return {"target_count": len(active), "submitted_count": count, "missing_count": len(active)-count,
                "in_progress_count": pending, "failed_count": failed, "rows": rows,
                "submissions": [s for s in completed if s["user_id"] in visible]}

    def export_csv(self, token, assignment_id):
        report = self.dashboard(token, assignment_id, include_inactive=True)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["user_id", "name", "group", "active", "submitted", "submission_count", "submission_number", "version", "completed_at (" + self.config.timezone + ")", "files", "total_bytes"])
        for row in report["rows"]:
            submissions = [s for s in report["submissions"] if s["user_id"] == row["user_id"]] or [None]
            for s in submissions:
                when = datetime.fromisoformat(s["completed_at"]).astimezone(ZoneInfo(self.config.timezone)).isoformat() if s else ""
                writer.writerow([safe_cell(row["user_id"]), safe_cell(row["name"]), safe_cell(row["group"]), row["active"], row["submitted"], row["submission_count"], s["submission_number"] if s else "", s["version"] if s else "", when,
                                 safe_cell(json.dumps([{ "name": f["name"], "size_bytes": f["size"]} for f in s["files"]], ensure_ascii=False)) if s else "", s["total_bytes"] if s else 0])
        return "\ufeff" + output.getvalue()
