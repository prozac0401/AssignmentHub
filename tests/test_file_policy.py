import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from assignmenthub.api import create_app
from assignmenthub.config import Config
from assignmenthub.db import SCHEMA
from assignmenthub.file_policy import ALL_CATEGORIES, DEFAULT_CATEGORIES, file_category
from assignmenthub.service import Service
from assignmenthub.video_validation import VideoValidatorUnavailable
from media_samples import make_video
from test_api import ADMIN_PASSWORD, auth, begin, chunk, finish, login, student


@pytest.fixture
def policy_hub(tmp_path):
    config = Config(instance_id="policy_test", course_name="파일 정책 검사", port=18995,
                    storage_root=str(tmp_path / "store"), min_free_bytes=0,
                    max_file_bytes=1024**2, user_quota_bytes=10 * 1024**2, chunk_bytes=65536)
    Service(config).bootstrap_admin("admin", ADMIN_PASSWORD)
    with TestClient(create_app(config)) as client:
        admin = login(client, "admin", ADMIN_PASSWORD)
        session = student(client, admin)
        yield client, admin, session


def new_assignment(client, admin, allowed, **extra):
    response = client.post("/api/admin/assignments", headers=auth(admin), json={
        "title": "제출 규칙", "allowed_file_categories": allowed, **extra})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def upload_bytes(client, session, aid, name, data):
    response = begin(client, session, [(name, data)], assignment_id=aid)
    assert response.status_code == 200, response.text
    upload = response.json()
    for offset in range(0, len(data), 65536):
        response = chunk(client, session, upload, upload["files"][0], offset, data[offset:offset + 65536])
        assert response.status_code == 200, response.text
    return upload


def assert_rejected(client, session, upload):
    status = client.get("/api/uploads/" + upload["id"], headers=auth(session)).json()
    assert status["status"] == "failed" and status["error"]
    assert status["submission_number"] is None
    assert client.get("/api/submissions", headers=auth(session)).json() == []
    assert client.get("/api/quota", headers=auth(session)).json()["reserved_bytes"] == 0
    assert client.get("/api/quota", headers=auth(session)).json()["used_bytes"] == 0
    assert client.get("/api/files/" + upload["files"][0]["id"] + "/download", headers=auth(session)).status_code != 200
    assert not list(client.app.state.config.root.glob("tmp/**/*.part"))


@pytest.mark.parametrize("name,expected", [
    ("보고서.HWPX", "documents"), ("report.PDF", "documents"), ("data.CSV", "spreadsheets"),
    ("slides.pptx", "presentations"), ("photo.HEIC", "images"), ("recording.MP4", "video"),
    ("voice.m4a", "audio"), ("files.tar.gz", "archives"), ("source.ts", "code"),
    ("unknown.bin", "other"), ("README", "other"), ("report.pdf.exe", "other"),
    ("fake.MP4 .", "video"), ("dir.mp4/file.zip", "archives"), ("dir\\file.MOV", "video"),
])
def test_common_categories_and_filename_edges(name, expected):
    assert file_category(name) == expected


def test_old_database_migration_is_idempotent_and_preserves_policy(tmp_path):
    config = Config(instance_id="old_policy", course_name="기존 과정", port=18996, storage_root=str(tmp_path), min_free_bytes=0)
    with sqlite3.connect(tmp_path / "assignmenthub.db") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO assignments VALUES ('old','기존 과제','설명',0)")
    service = Service(config)
    service.store.initialize()
    with service.store.connect() as db:
        row = db.execute("SELECT * FROM assignments").fetchone()
        assert row["id"] == "old" and row["description"] == "설명" and row["is_open"] == 0
        assert json.loads(row["allowed_file_categories"]) == ALL_CATEGORIES
        assert row["video_audio_required"] == 1
        assert [r["name"] for r in db.execute("PRAGMA table_info(files)")].count("video_validation") == 1


def test_create_edit_empty_selection_and_authentication(policy_hub):
    client, admin, session = policy_hub
    created = client.post("/api/admin/assignments", headers=auth(admin), json={"title": " 새 과제 "}).json()
    assert created["title"] == "새 과제" and created["allowed_file_categories"] == DEFAULT_CATEGORIES
    assert created["video_audio_required"] is True
    endpoint = "/api/admin/assignments/" + created["id"]
    assert client.post("/api/admin/assignments", headers=auth(session), json={"title": "x"}).status_code == 403
    assert client.patch(endpoint, headers=auth(session), json={"allowed_file_categories": []}).status_code == 403
    for value in [["unknown"], ["video", "video"], "documents", [True]]:
        assert client.patch(endpoint, headers=auth(admin), json={"allowed_file_categories": value}).status_code == 422
    response = client.patch(endpoint, headers=auth(admin), json={"allowed_file_categories": [], "video_audio_required": False})
    assert response.status_code == 200 and response.json()["allowed_file_categories"] == []
    assert response.json()["video_audio_required"] is False
    assert begin(client, session, [("file.txt", b"test")], assignment_id=created["id"]).status_code == 415
    # A partial title update must keep both policy fields (including empty/false).
    patched = client.patch(endpoint, headers=auth(admin), json={"title": "바꾼 이름"}).json()
    assert patched["allowed_file_categories"] == [] and patched["video_audio_required"] is False


def test_policy_is_per_assignment_and_other_does_not_override_disabled_categories(policy_hub):
    client, admin, session = policy_hub
    docs = new_assignment(client, admin, ["documents", "other"])
    archives = new_assignment(client, admin, ["archives"])
    assert begin(client, session, [("files.ZIP", b"zip")], assignment_id=docs).status_code == 415
    assert begin(client, session, [("report.docx", b"doc")], assignment_id=archives).status_code == 415
    assert begin(client, session, [("good.pdf", b"doc"), ("bad.7z", b"zip")], assignment_id=docs).status_code == 415
    assert client.get("/api/quota", headers=auth(session)).json()["reserved_bytes"] == 0
    assert begin(client, session, [("report.HWPX", b"doc")], assignment_id=docs).status_code == 200
    assert begin(client, session, [("custom.bin", b"other")], assignment_id=docs).status_code == 200
    assert begin(client, session, [("files.ZIP", b"zip")], assignment_id=archives).status_code == 200


@pytest.mark.parametrize("stage", ["uploading", "verified", "after_move"])
def test_changed_policy_is_checked_before_completion_and_releases_quota(policy_hub, stage):
    client, admin, session = policy_hub
    aid = new_assignment(client, admin, ["archives"])
    upload = upload_bytes(client, session, aid, "files.zip", b"archive content")
    def change():
        assert client.patch("/api/admin/assignments/" + aid, headers=auth(admin), json={"allowed_file_categories": ["documents"]}).status_code == 200
    if stage == "verified":
        assert client.post("/api/uploads/" + upload["id"] + "/verify", headers=auth(session)).status_code == 200
    if stage == "after_move":
        client.app.state.service.fault_hook = lambda point: change() if point == "after_move" else None
    else:
        change()
    assert finish(client, session, upload).status_code == 415
    assert_rejected(client, session, upload)
    assert not [p for p in (client.app.state.config.root / "submissions").rglob("*") if p.is_file()]


def test_video_receipt_contains_persisted_result_and_completed_files_survive_policy_changes(policy_hub, tmp_path):
    client, admin, session = policy_hub
    aid = new_assignment(client, admin, ["video"])
    data = make_video(tmp_path / "source.mp4", seconds=6).read_bytes()
    upload = upload_bytes(client, session, aid, "발표.MP4", data)
    response = finish(client, session, upload)
    assert response.status_code == 200, response.text
    report = response.json()["files"][0]["video_validation"]
    assert report["status"] == "passed" and report["sample_seconds"] == 5
    assert report["audio_detected"] and report["sha256"] == hashlib.sha256(data).hexdigest()
    assert client.get("/api/submissions", headers=auth(session)).json()[0]["files"][0]["video_validation"] == report
    assert client.patch("/api/admin/assignments/" + aid, headers=auth(admin), json={"allowed_file_categories": []}).status_code == 200
    assert finish(client, session, upload).status_code == 200
    download = client.get("/api/files/" + upload["files"][0]["id"] + "/download", headers=auth(session))
    assert download.content == data


@pytest.mark.parametrize("kind", ["fake", "silent", "no_audio", "audio_only"])
def test_bad_video_never_registers_and_failed_batch_is_cleaned(policy_hub, tmp_path, kind):
    client, admin, session = policy_hub
    aid = new_assignment(client, admin, ["video"])
    data = b"not a video" if kind == "fake" else make_video(tmp_path / "source.mp4",
        audio={"silent": "silent", "no_audio": "none", "audio_only": "tone"}[kind], video=kind != "audio_only").read_bytes()
    upload = upload_bytes(client, session, aid, "video.mp4", data)
    assert finish(client, session, upload).status_code == 422
    assert_rejected(client, session, upload)
    # A retry of a terminal failure must not release its quota twice.
    assert finish(client, session, upload).status_code == 409
    assert client.get("/api/quota", headers=auth(session)).json()["reserved_bytes"] == 0


@pytest.mark.parametrize("change_audio", [False, True])
def test_optional_audio_and_changed_requirement_after_verification(policy_hub, tmp_path, change_audio):
    client, admin, session = policy_hub
    aid = new_assignment(client, admin, ["video"], video_audio_required=False)
    upload = upload_bytes(client, session, aid, "mute.mp4", make_video(tmp_path / "source.mp4", audio="none").read_bytes())
    verified = client.post("/api/uploads/" + upload["id"] + "/verify", headers=auth(session))
    assert verified.status_code == 200, verified.text
    if change_audio:
        assert client.patch("/api/admin/assignments/" + aid, headers=auth(admin), json={"video_audio_required": True}).status_code == 200
        assert finish(client, session, upload).status_code == 415
        assert_rejected(client, session, upload)
    else:
        response = finish(client, session, upload)
        assert response.status_code == 200
        assert not response.json()["files"][0]["video_validation"]["audio_detected"]


def test_recovery_and_legacy_finalizing_state_cannot_skip_video_validation(policy_hub):
    client, admin, session = policy_hub
    aid = new_assignment(client, admin, ["video"])
    upload = upload_bytes(client, session, aid, "fake.mp4", b"not a video")
    service = client.app.state.service
    with service.store.connect(write=True) as db:
        db.execute("UPDATE uploads SET status='finalizing' WHERE id=?", (upload["id"],))
        db.execute("UPDATE files SET stored_sha256=sha256 WHERE upload_id=?", (upload["id"],))
    assert finish(client, session, upload).status_code == 422
    assert_rejected(client, session, upload)
    upload = upload_bytes(client, session, aid, "fake.mp4", b"not a video")
    with service.store.connect(write=True) as db:
        db.execute("UPDATE uploads SET status='verifying' WHERE id=?", (upload["id"],))
    service.recover()
    assert_rejected(client, session, upload)


def test_unavailable_validator_keeps_upload_retryable(policy_hub, tmp_path, monkeypatch):
    client, admin, session = policy_hub
    aid = new_assignment(client, admin, ["video"])
    upload = upload_bytes(client, session, aid, "video.mp4", make_video(tmp_path / "source.mp4").read_bytes())
    def unavailable(*args):
        raise VideoValidatorUnavailable("검사기 점검 필요")
    with monkeypatch.context() as patch:
        patch.setattr("assignmenthub.service.validate_video", unavailable)
        assert finish(client, session, upload).status_code == 503
        assert client.get("/api/submissions", headers=auth(session)).json() == []
        assert client.get("/api/quota", headers=auth(session)).json()["reserved_bytes"] == upload["total_bytes"]
    assert finish(client, session, upload).status_code == 200
