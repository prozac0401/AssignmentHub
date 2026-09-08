from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import parse_qs, quote
import uuid

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.concurrency import run_in_threadpool

from .config import Config
from .service import Service, fail


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Model):
    user_id: str = Field(max_length=128)
    password: str = Field(max_length=128)


class Password(Model):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)


class FileDeclaration(Model):
    name: str = Field(min_length=1, max_length=255)
    size: StrictInt
    sha256: str = Field(min_length=64, max_length=64)


class UploadStart(Model):
    assignment_id: str = Field(max_length=128)
    request_id: str = Field(max_length=128)
    files: list[FileDeclaration] = Field(min_length=1, max_length=100)


class RosterApply(Model):
    rows: list[dict] = Field(min_length=1, max_length=10000)
    update_existing: StrictBool = False
    common_temporary_password: str | None = Field(default=None, max_length=8)


class Active(Model):
    active: StrictBool


class Assignment(Model):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    is_open: StrictBool = True


class AssignmentPatch(Model):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    is_open: StrictBool | None = None


class BrowserAssets(StaticFiles):
    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        # FileResponse otherwise uses the host's MIME registry. On some Windows
        # PCs .js is text/plain, which browsers correctly reject with nosniff.
        media_type = {".js": "text/javascript", ".css": "text/css"}.get(Path(full_path).suffix.lower())
        if media_type:
            response.headers["content-type"] = media_type + "; charset=utf-8"
        return response


class RequestPolicy:
    """Pure ASGI guards: do not buffer request bodies, never disable XSRF/CORS."""
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode("latin1")
        if origin and origin.rstrip("/") != self.config.public_url:
            return await JSONResponse({"detail": "허용되지 않은 접속 origin입니다. 안내된 서버 주소를 사용하세요."}, 403)(scope, receive, send)
        path = scope.get("path", "")
        limit = 5 * 1024**2 if path == "/api/admin/roster/preview" else 2 * 1024**2
        chunk = scope["method"] == "PATCH" and "/files/" in path
        if chunk:
            limit = self.config.chunk_bytes
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Content-Length가 잘못되었습니다."}, 400)(scope, receive, send)
        if declared > limit:
            return await JSONResponse({"detail": "요청 본문 크기 제한을 초과했습니다."}, 413)(scope, receive, send)
        actual = 0

        async def bounded_receive():
            nonlocal actual
            message = await receive()
            if message["type"] == "http.request":
                actual += len(message.get("body", b""))
                if actual > limit:
                    fail(413, "실제 수신 바이트가 요청 제한을 초과했습니다.")
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                extra = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"), (b"cache-control", b"no-store")]
                if path == "/upload":
                    extra.append((b"content-security-policy", b"default-src 'self'; script-src 'self'; worker-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)
        await self.app(scope, bounded_receive, secure_send)


def bearer(request: Request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        fail(401, "로그인이 필요합니다.")
    return header[7:]


def create_app(config: Config):
    service = Service(config)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(service.recover)
        stop = threading.Event()

        def janitor():
            while not stop.wait(60):
                try:
                    service.cleanup()
                except (OSError, sqlite3.Error):
                    pass
        thread = threading.Thread(target=janitor, daemon=True, name="upload-cleanup")
        thread.start()
        yield
        stop.set()
        await run_in_threadpool(thread.join, 5)

    app = FastAPI(title="AssignmentHub", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service = service
    app.state.config = config
    app.add_middleware(RequestPolicy, config=config)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors otherwise echo password/token input values.
        return JSONResponse({"detail": "입력 형식/필수 항목/길이를 확인하세요.", "fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}, 422)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        return JSONResponse({"detail": "DB 쓰기 또는 조회에 실패했습니다. 관리자에게 공간과 권한 점검을 요청하세요."}, 503)

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return JSONResponse({"detail": "저장 장치 작업에 실패했습니다. 공간과 권한을 확인한 뒤 재시도하세요."}, 507)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "instance_id": config.instance_id}

    @app.get("/api/info")
    def info():
        return {"instance_id": config.instance_id, "course_name": config.course_name, "timezone": config.timezone, "public_url": config.public_url,
                "max_file_bytes": config.max_file_bytes, "user_quota_bytes": config.user_quota_bytes,
                "chunk_bytes": config.chunk_bytes, "max_files": config.max_files, "concurrent_uploads": config.concurrent_uploads,
                "ui_path": "/ui/" + config.instance_id + "/"}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request):
        return service.login(body.user_id, body.password, request.client.host if request.client else "local")

    @app.post("/api/auth/password")
    def password(body: Password, token=Depends(bearer)):
        service.change_password(token, **body.model_dump())
        return {"message": "비밀번호가 변경되었습니다. 새 비밀번호로 다시 로그인하세요."}

    @app.post("/api/auth/logout")
    def logout(token=Depends(bearer)):
        service.logout(token)
        return {"message": "로그아웃되었습니다."}

    @app.get("/api/auth/me")
    def me(token=Depends(bearer)):
        return service.public_user(service.authenticate(token, allow_restricted=True))

    @app.get("/api/assignments")
    def assignments(token=Depends(bearer)):
        service.authenticate(token)
        with service.store.connect() as db:
            return [{**dict(r), "is_open": bool(r["is_open"])} for r in db.execute("SELECT * FROM assignments ORDER BY rowid")]

    @app.get("/api/quota")
    def quota(token=Depends(bearer)):
        return service.quota(token)

    @app.get("/api/uploads")
    def pending(token=Depends(bearer)):
        user = service.authenticate(token)
        with service.store.connect() as db:
            return [service.upload_result(db, r) for r in db.execute("SELECT * FROM uploads WHERE user_pk=? AND status != 'completed' ORDER BY created_at DESC", (user["id"],))]

    @app.get("/api/submissions")
    def submissions(assignment_id: str | None = None, all_versions: bool = True, token=Depends(bearer)):
        user = service.authenticate(token)
        with service.store.connect() as db:
            rows = db.execute("SELECT * FROM uploads WHERE user_pk=? AND status='completed' AND (? IS NULL OR assignment_id=?) ORDER BY completed_at DESC", (user["id"], assignment_id, assignment_id))
            result = [service.upload_result(db, r) for r in rows]
        if all_versions:
            return result
        seen, latest = set(), []
        for s in result:
            if s["assignment_id"] not in seen:
                latest.append(s)
                seen.add(s["assignment_id"])
        return latest

    @app.post("/api/uploads")
    def start(body: UploadStart, token=Depends(bearer)):
        return service.start_upload(token, body.assignment_id, body.request_id, [f.model_dump() for f in body.files])

    @app.get("/api/uploads/{upload_id}")
    def status(upload_id: str, token=Depends(bearer)):
        return service.get_upload(token, upload_id)

    @app.patch("/api/uploads/{upload_id}/files/{file_id}")
    async def chunk(upload_id: str, file_id: str, request: Request, offset: int, token=Depends(bearer)):
        expected_hash = request.headers.get("X-Chunk-SHA256", "").lower()
        if not re.fullmatch("[0-9a-f]{64}", expected_hash):
            fail(422, "청크 SHA-256 헤더가 필요합니다.")
        await run_in_threadpool(service.authenticate, token)
        if not service.slots.acquire(blocking=False):
            fail(429, "동시 전송 슬롯이 사용 중입니다. 잠시 뒤 재시도하세요.")
        lock = service.lock(upload_id)
        if not lock.acquire(blocking=False):
            service.slots.release()
            fail(409, "이 제출의 다른 전송/검증이 진행 중입니다. 상태 조회 후 재시도하세요.")
        handle, committed, duplicate, original_offset, prepared = None, False, None, 0, False
        try:
            upload, file, duplicate = await run_in_threadpool(service.prepare_chunk, token, upload_id, file_id, offset)
            prepared = True
            original_offset = file["offset"]
            if not duplicate:
                path = service.file_path(upload, file)

                def open_part():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    f = path.open("r+b" if path.exists() else "w+b")
                    if f.seek(0, 2) < original_offset:
                        f.close()
                        fail(409, "확정된 임시 파일이 손상되었습니다. 관리자 점검이 필요합니다.")
                    f.truncate(original_offset)
                    f.seek(original_offset)
                    return f
                handle = await run_in_threadpool(open_part)
            digest, received = hashlib.sha256(), 0
            last_activity = time.monotonic()
            async for data in request.stream():
                if not data:
                    continue
                received += len(data)
                if received > config.chunk_bytes or offset + received > file["size"] or offset + received > config.max_file_bytes:
                    fail(413, "실제 수신량이 청크/파일 크기 제한을 초과했습니다.")
                if duplicate and received > duplicate["size"]:
                    fail(409, "이미 확정된 청크와 크기가 다릅니다.")
                # Thread-pool I/O, bounded Uvicorn receive blocks, no full request/file buffering.
                def write_block(block):
                    service.authenticate(token)
                    with service.store.connect() as db:
                        service.check_disk(db)
                    digest.update(block)
                    if handle:
                        handle.write(block)
                    service.fault_hook("during_chunk")
                await run_in_threadpool(write_block, data)
                if time.monotonic() - last_activity >= min(5, config.upload_ttl_hours * 900):
                    await run_in_threadpool(service.touch, token, upload_id)
                    last_activity = time.monotonic()
            if received <= 0:
                fail(422, "빈 청크는 허용되지 않습니다. 0바이트 파일은 바로 검증하세요.")
            if digest.hexdigest() != expected_hash:
                fail(422, "청크 해시가 일치하지 않습니다. 같은 offset에서 재시도하세요.")
            if handle:
                def durable():
                    handle.flush()
                    os.fsync(handle.fileno())
                await run_in_threadpool(durable)
            value = await run_in_threadpool(service.commit_chunk, token, upload, file, offset, received, expected_hash, duplicate)
            committed = True
            return {"offset": value}
        except BaseException:
            if handle and not committed:
                def rollback():
                    handle.truncate(original_offset)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    await run_in_threadpool(rollback)
                except OSError:
                    pass  # Startup truncates trailing bytes to durable offset.
            if prepared:
                try:
                    await run_in_threadpool(service.pause, upload_id, "전송이 중단되었습니다. 서버 offset을 조회한 뒤 재시도하세요.")
                except (OSError, sqlite3.Error):
                    pass
            raise
        finally:
            if handle:
                await run_in_threadpool(handle.close)
            lock.release()
            service.slots.release()

    @app.post("/api/uploads/{upload_id}/verify")
    def verify(upload_id: str, token=Depends(bearer)):
        return service.verify(token, upload_id)

    @app.post("/api/uploads/{upload_id}/complete")
    def complete(upload_id: str, token=Depends(bearer)):
        return service.complete(token, upload_id)

    @app.post("/api/uploads/{upload_id}/cancel")
    def cancel(upload_id: str, token=Depends(bearer)):
        return service.cancel(token, upload_id)

    def download(path, metadata, validate):
        def stream():
            with path.open("rb") as f:
                while True:
                    validate()
                    data = f.read(1024 * 1024)
                    if not data:
                        break
                    yield data
        return StreamingResponse(stream(), media_type="application/octet-stream", headers={
            "Content-Length": str(metadata["size"]),
            "Content-Disposition": "attachment; filename=assignment-file; filename*=UTF-8''" + quote(metadata["name"], safe=""),
        })

    @app.get("/api/files/{file_id}/download")
    def download_file(file_id: str, token=Depends(bearer)):
        user = service.authenticate(token)
        path, metadata = service.download_info(user, file_id)
        return download(path, metadata, lambda: service.authenticate(token))

    @app.post("/api/files/{file_id}/ticket")
    def ticket(file_id: str, token=Depends(bearer)):
        user = service.authenticate(token)
        service.download_info(user, file_id)
        return {"ticket": service.make_grant(token, "download", file_id), "expires_in": 120}

    @app.post("/api/downloads")
    async def form_download(request: Request):
        # Form body contains only an opaque one-use ticket, never a URL query parameter.
        raw = await request.body()
        if len(raw) > 1024:
            fail(413, "다운로드 요청이 너무 큽니다.")
        ticket = parse_qs(raw.decode("utf-8")).get("ticket", [""])[0]
        user, file_id, session = await run_in_threadpool(service.consume_grant, ticket, "download")
        path, metadata = await run_in_threadpool(service.download_info, user, file_id)

        def validate():
            with service.store.connect() as db:
                s = db.execute("SELECT * FROM sessions WHERE digest=?", (session["digest"],)).fetchone()
                u = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
                if not s or s["expires"] <= time.time() or not u["active"] or u["must_change_password"] or u["epoch"] != s["epoch"]:
                    fail(401, "다운로드 권한이 만료되었습니다.")
                if s["parent_digest"] and not db.execute("SELECT 1 FROM sessions WHERE digest=? AND expires>?", (s["parent_digest"], time.time())).fetchone():
                    fail(401, "상위 로그인이 만료되었습니다.")
        return download(path, metadata, validate)

    @app.get("/api/admin/users")
    def users(search: str = "", token=Depends(bearer)):
        service.authenticate(token, admin=True)
        with service.store.connect() as db:
            return [service.public_user(r) for r in db.execute("SELECT * FROM users ORDER BY user_id") if not search or search.casefold() in " ".join((r["user_id"], r["name"], r["group_name"])).casefold()]

    @app.post("/api/admin/roster/preview")
    async def preview(request: Request, token=Depends(bearer)):
        await run_in_threadpool(service.authenticate, token, False, True)
        return await run_in_threadpool(service.roster_preview, token, await request.body())

    @app.post("/api/admin/roster/apply")
    def apply_roster(body: RosterApply, token=Depends(bearer)):
        return service.roster_apply(token, body.rows, body.update_existing, body.common_temporary_password)

    @app.post("/api/admin/users/{user_pk}/reset")
    def reset(user_pk: str, token=Depends(bearer)):
        return service.reset_password(token, user_pk)

    @app.patch("/api/admin/users/{user_pk}")
    def active(user_pk: str, body: Active, token=Depends(bearer)):
        service.set_active(token, user_pk, body.active)
        return {"active": body.active}

    @app.post("/api/admin/assignments")
    def create_assignment(body: Assignment, token=Depends(bearer)):
        with service.store.connect(write=True) as db:
            user = service.authenticate(token, admin=True, db=db)
            if not body.title.strip():
                fail(422, "과제명을 입력하세요.")
            aid = uuid.uuid4().hex
            db.execute("INSERT INTO assignments VALUES (?,?,?,?)", (aid, body.title.strip(), body.description, int(body.is_open)))
            service.audit(db, user["user_id"], "assignment_create", aid)
            return {"id": aid, **body.model_dump()}

    @app.patch("/api/admin/assignments/{assignment_id}")
    def edit_assignment(assignment_id: str, body: AssignmentPatch, token=Depends(bearer)):
        with service.store.connect(write=True) as db:
            user = service.authenticate(token, admin=True, db=db)
            old = db.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
            if not old:
                fail(404, "과제를 찾을 수 없습니다.")
            values = {**dict(old), **body.model_dump(exclude_none=True)}
            if not values["title"].strip():
                fail(422, "과제명을 입력하세요.")
            changed = db.execute("UPDATE assignments SET title=?,description=?,is_open=? WHERE id=?", (values["title"].strip(), values["description"], int(values["is_open"]), assignment_id)).rowcount
            if not changed:
                fail(404, "과제를 찾을 수 없습니다.")
            service.audit(db, user["user_id"], "assignment_edit", assignment_id, "is_open=" + str(values["is_open"]))
            return values

    @app.get("/api/admin/dashboard")
    def dashboard(assignment_id: str | None = None, search: str = "", group: str = "", include_inactive: bool = False, token=Depends(bearer)):
        if assignment_id is None:
            with service.store.connect() as db:
                assignment_id = db.execute("SELECT id FROM assignments ORDER BY rowid LIMIT 1").fetchone()[0]
        return service.dashboard(token, assignment_id, search, group, include_inactive)

    @app.get("/api/admin/export")
    def export(assignment_id: str, token=Depends(bearer)):
        return Response(service.export_csv(token, assignment_id), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=submissions.csv"})

    @app.get("/api/admin/storage")
    def storage(token=Depends(bearer)):
        return service.storage(token)

    @app.get("/api/admin/audit")
    def audit(token=Depends(bearer)):
        service.authenticate(token, admin=True)
        with service.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 1000")]

    static = Path(__file__).parent / "static"
    static.mkdir(exist_ok=True)
    app.mount("/upload-static", BrowserAssets(directory=static), name="uploader-assets")

    @app.get("/upload")
    def upload_page():
        return FileResponse(static / "upload.html", media_type="text/html")

    @app.post("/upload")
    async def authenticated_upload_page(request: Request):
        # A normal form navigation carries the existing login in the POST body.
        # No connection code, credential URL, cookie, or persistent browser storage.
        raw = await request.body()
        if len(raw) > 2048:
            fail(413, "제출 화면 요청이 너무 큽니다.")
        try:
            values = parse_qs(raw.decode("utf-8"), max_num_fields=2, keep_blank_values=True)
        except (ValueError, UnicodeDecodeError):
            fail(422, "제출 화면 요청 형식을 확인하세요.")
        if set(values) - {"token", "assignment_id"} or any(len(v) != 1 for v in values.values()):
            fail(422, "제출 화면 요청 형식을 확인하세요.")
        token = values.get("token", [""])[0]
        user = await run_in_threadpool(service.authenticate, token)
        assignment_id = values.get("assignment_id", [""])[0]
        if len(assignment_id) > 128:
            fail(422, "과제 정보를 확인하세요.")
        payload = json.dumps({"token": token, "user": service.public_user(user),
                              "must_change_password": False, "assignment_id": assignment_id},
                             ensure_ascii=True).replace("<", "\\u003c")
        document = (static / "upload.html").read_text(encoding="utf-8")
        document = document.replace('<section id="auth" class="card">', '<section id="auth" class="card hidden" hidden>')
        document = document.replace("<!-- authenticated-session -->",
                                    '<script id="session-data" type="application/json">' + payload + '</script>')
        return HTMLResponse(document)

    return app


app = create_app(Config.load(os.environ["AH_CONFIG"])) if os.environ.get("AH_CONFIG") else FastAPI()
