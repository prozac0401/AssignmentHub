"""Real API, filesystem and migration checks for readable submission storage."""
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote

import pytest

from assignmenthub.service import Service
from test_api import hub, auth, student, begin, send_file, finish, complete_one, assignment, chunk


def report(client, admin, aid):
    response = client.get("/api/admin/dashboard", params={"assignment_id": aid}, headers=auth(admin))
    assert response.status_code == 200, response.text
    return response.json()


def test_names_versions_and_role_specific_downloads(hub):
    client, config, admin = hub
    user = student(client, admin)
    first = complete_one(client, user, b"first", "실습 결과.txt")
    second = complete_one(client, user, b"second", "실습 결과.txt")
    data = report(client, admin, assignment(client, user))
    assert data["rows"][0]["latest"]["version"] == 2
    for item, expected in zip(reversed(data["submissions"]), (b"first", b"second")):
        file = item["files"][0]
        location = Path(file["storage_path"])
        assert "기본 과제" in str(location) and "홍길동" in str(location) and "001" in str(location)
        assert location.name == "실습 결과.txt"
        assert not re.search(r"[0-9a-f]{32}", str(location.relative_to(config.root)))
        assert location.read_bytes() == expected
        for session in (admin, user):
            download = client.get(f"/api/files/{file['id']}/download", headers=auth(session))
            assert download.content == expected
            name = unquote(download.headers["Content-Disposition"])
            assert "실습 결과.txt" in name
            if session is admin:
                assert "홍길동_001_제출-" in name and f"_v{item['version']}_" in name
            else:
                assert "홍길동" not in name
    own = client.get("/api/submissions", headers=auth(user)).json()
    assert all("storage_path" not in f for s in own for f in s["files"])
    other = student(client, admin, "002", "홍길동")
    assert client.get(f"/api/files/{first['files'][0]['id']}/download", headers=auth(other)).status_code == 404
    exported = client.get("/api/admin/export", params={"assignment_id": first["assignment_id"]}, headers=auth(admin))
    assert "storage_path" in exported.text and "실습 결과.txt" in exported.text
    # Names are captured at upload creation; roster and assignment edits cannot break paths.
    client.post("/api/admin/roster/apply", headers=auth(admin), json={
        "rows": [{"user_id": "001", "name": "수정 이름", "group": "B"}], "update_existing": True})
    client.patch(f"/api/admin/assignments/{first['assignment_id']}", headers=auth(admin), json={"title": "수정 과제"})
    Service(config).recover()
    assert client.get(f"/api/files/{second['files'][0]['id']}/download", headers=auth(user)).content == b"second"


def test_same_names_case_unicode_and_unsafe_names_do_not_overwrite(hub):
    client, config, admin = hub
    user = student(client, admin, name="동명/수강생")
    names = ["결과.txt", "결과.txt", unicodedata.normalize("NFD", "결과.txt"),
             "../escape.txt", "CON", "a.txt:stream", "Report.txt", "report.txt",
             "긴" * 220 + ".txt", "결과 (2).txt"]
    upload = begin(client, user, [(name, str(i).encode()) for i, name in enumerate(names)]).json()
    for i in range(len(names)):
        send_file(client, user, upload, i, str(i).encode())
    done = finish(client, user, upload)
    assert done.status_code == 200, done.text
    files = report(client, admin, upload["assignment_id"])["submissions"][0]["files"]
    paths = [Path(f["storage_path"]) for f in files]
    assert len({str(p).casefold() for p in paths}) == len(names)
    for i, p in enumerate(paths):
        assert p.resolve().is_relative_to((config.root / "submissions").resolve())
        assert p.read_bytes() == str(i).encode()
        assert not any(c in p.name for c in '<>:"/\\|?*')
    assert [f["name"] for f in files] == names


def move_to_legacy(service, upload_id):
    with service.store.connect() as db:
        upload = dict(service._upload(db, upload_id))
        files = [dict(r) for r in db.execute("SELECT * FROM files WHERE upload_id=?", (upload_id,))]
    for file in files:
        for final in (False, True):
            source = service.file_path(upload, file, final)
            if source.exists():
                target = service.legacy_file_path(upload, file, final)
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
    with service.store.connect(write=True) as db:
        db.execute("UPDATE uploads SET storage_dir=NULL,temp_dir=NULL WHERE id=?", (upload_id,))
        db.execute("UPDATE files SET storage_name=NULL WHERE upload_id=?", (upload_id,))


def test_existing_files_migrate_with_restart_after_partial_move(hub):
    client, config, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("문서.txt", b"one"), ("문서.txt", b"two")]).json()
    for i, content in enumerate((b"one", b"two")):
        send_file(client, user, upload, i, content)
    assert finish(client, user, upload).status_code == 200
    service = client.app.state.service
    move_to_legacy(service, upload["id"])

    def fault(point):
        if point == "storage_migration_after_move":
            raise OSError("interrupted migration")
    service.fault_hook = fault
    with pytest.raises(OSError, match="interrupted migration"):
        service.recover()
    # Both the already moved and the not-yet-moved files remain downloadable.
    for file, expected in zip(upload["files"], (b"one", b"two")):
        assert client.get(f"/api/files/{file['id']}/download", headers=auth(user)).content == expected
    restarted = Service(config)
    restarted.recover()
    restarted.recover()
    data = report(client, admin, upload["assignment_id"])
    for file, expected in zip(data["submissions"][0]["files"], (b"one", b"two")):
        assert "홍길동" in file["storage_path"]
        assert Path(file["storage_path"]).read_bytes() == expected
    quota = client.get("/api/quota", headers=auth(user)).json()
    assert quota["used_bytes"] == 6 and quota["reserved_bytes"] == 0


def test_legacy_partial_upload_resumes_in_named_temporary_folder(hub):
    client, config, admin = hub
    user = student(client, admin)
    upload = begin(client, user, [("실습.txt", b"12345678abcdef")]).json()
    assert chunk(client, user, upload, upload["files"][0], 0, b"12345678").status_code == 200
    service = client.app.state.service
    move_to_legacy(service, upload["id"])
    Service(config).recover()
    temporary = list((config.root / "tmp").rglob("*.part"))
    assert len(temporary) == 1 and "홍길동" in str(temporary[0]) and temporary[0].name == "실습.txt.part"
    assert chunk(client, user, upload, upload["files"][0], 8, b"abcdef").status_code == 200
    assert finish(client, user, upload).status_code == 200
    assert client.get(f"/api/files/{upload['files'][0]['id']}/download", headers=auth(user)).content == b"12345678abcdef"


def test_different_students_and_assignments_with_same_labels(hub):
    client, _, admin = hub
    one = student(client, admin, "Case", "동명이인")
    two = student(client, admin, "case", "동명이인")
    first = complete_one(client, one)
    second = complete_one(client, two)
    response = client.post("/api/admin/assignments", headers=auth(admin), json={"title": "기본 과제"})
    aid = response.json()["id"]
    third = begin(client, one, [("과제.txt", b"third")], assignment_id=aid).json()
    send_file(client, one, third, 0, b"third")
    assert finish(client, one, third).status_code == 200
    all_items = report(client, admin, first["assignment_id"])["submissions"] + report(client, admin, aid)["submissions"]
    paths = [s["files"][0]["storage_path"] for s in all_items]
    assert len({p.casefold() for p in paths}) == 3


@pytest.mark.parametrize("limit", [24, 32, 40, 120])
def test_long_unicode_names_fit_windows_component_budget(limit):
    from assignmenthub.storage_paths import storage_name
    for name in ("😀" * 128, "이름." + "😀" * 60, "긴" * 240 + ".txt"):
        result = storage_name(name, limit)
        assert 0 < len(result.encode("utf-16-le")) // 2 <= limit
