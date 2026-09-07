from __future__ import annotations

from contextlib import contextmanager
import json
import os
import secrets
import sqlite3
import uuid

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, user_id TEXT UNIQUE COLLATE BINARY NOT NULL,
 name TEXT NOT NULL, group_name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL CHECK(role IN ('admin','student')),
 password_hash TEXT NOT NULL, must_change_password INTEGER NOT NULL DEFAULT 1,
 active INTEGER NOT NULL DEFAULT 1, epoch INTEGER NOT NULL DEFAULT 0,
 used_bytes INTEGER NOT NULL DEFAULT 0 CHECK(used_bytes >= 0),
 reserved_bytes INTEGER NOT NULL DEFAULT 0 CHECK(reserved_bytes >= 0)
);
CREATE TABLE IF NOT EXISTS sessions (
 digest TEXT PRIMARY KEY, user_pk TEXT NOT NULL REFERENCES users(id), epoch INTEGER NOT NULL,
 expires REAL NOT NULL, restricted INTEGER NOT NULL, parent_digest TEXT
);
CREATE TABLE IF NOT EXISTS grants (
 digest TEXT PRIMARY KEY, session_digest TEXT NOT NULL, kind TEXT NOT NULL, object_id TEXT, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assignments (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', is_open INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS uploads (
 id TEXT PRIMARY KEY, user_pk TEXT NOT NULL REFERENCES users(id), assignment_id TEXT NOT NULL REFERENCES assignments(id),
 request_id TEXT NOT NULL, manifest TEXT NOT NULL, status TEXT NOT NULL,
 total_bytes INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 submission_number INTEGER UNIQUE, version INTEGER, completed_at REAL, error TEXT,
 UNIQUE(user_pk,request_id)
);
CREATE TABLE IF NOT EXISTS files (
 id TEXT PRIMARY KEY, upload_id TEXT NOT NULL REFERENCES uploads(id), ordinal INTEGER NOT NULL,
 name TEXT NOT NULL, size INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
 sha256 TEXT NOT NULL, stored_sha256 TEXT, UNIQUE(upload_id,ordinal)
);
CREATE TABLE IF NOT EXISTS chunks (
 file_id TEXT NOT NULL REFERENCES files(id), offset INTEGER NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(file_id,offset)
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT, at REAL NOT NULL, detail TEXT
);
CREATE INDEX IF NOT EXISTS uploads_user ON uploads(user_pk,status);
CREATE INDEX IF NOT EXISTS uploads_assignment ON uploads(assignment_id,status);
CREATE INDEX IF NOT EXISTS files_upload ON files(upload_id);
"""


class Store:
    def __init__(self, config: Config):
        self.config = config
        self.path = config.root / "assignmenthub.db"

    def initialize(self):
        root = self.config.root
        root.mkdir(parents=True, exist_ok=True)
        marker = root / "instance.json"
        try:
            with marker.open("x", encoding="utf-8") as f:
                json.dump({"instance_id": self.config.instance_id, "schema_version": 1}, f)
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            if json.loads(marker.read_text(encoding="utf-8"))["instance_id"] != self.config.instance_id:
                raise ValueError("이 저장 루트는 다른 인스턴스에 속합니다.")
        for name in ("tmp", "submissions", "logs"):
            (root / name).mkdir(exist_ok=True)
        secret = root / "auth.secret"
        try:
            with secret.open("x", encoding="ascii") as f:
                f.write(secrets.token_hex(32))
                f.flush()
                os.fsync(f.fileno())
            secret.chmod(0o600)
        except FileExistsError:
            pass
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            if not db.execute("SELECT 1 FROM assignments").fetchone():
                db.execute("INSERT INTO assignments(id,title) VALUES (?,?)", (uuid.uuid4().hex, "기본 과제"))

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()
