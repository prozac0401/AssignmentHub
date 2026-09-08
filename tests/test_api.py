"""Small real API tests; these do not establish 2 GiB/browser performance."""
from __future__ import annotations

import hashlib
import csv
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assignmenthub.api import create_app
from assignmenthub.config import Config
from assignmenthub.service import Service

ADMIN_PASSWORD = "Admin-test-password!"
NEW_PASSWORD = "Student-new-password!"


@pytest.fixture
def hub(tmp_path):
    config = Config(instance_id="test_hub", course_name="테스트 과정", port=18991,
                    public_host="127.0.0.1", storage_root=str(tmp_path / "store"),
                    max_file_bytes=32, user_quota_bytes=80, min_free_bytes=0,
                    chunk_bytes=8)
    service = Service(config)
    service.bootstrap_admin("admin", ADMIN_PASSWORD)
    with TestClient(create_app(config)) as client:
        admin = login(client, "admin", ADMIN_PASSWORD)
        yield client, config, admin


def login(client, user_id, password):
    response = client.post("/api/auth/login", json={"user_id": user_id, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def auth(login_result):
    return {"Authorization": "Bearer " + login_result["token"]}


def student(client, admin, user_id="001", name="홍길동", group="A반"):
    response = client.post("/api/admin/roster/apply", headers=auth(admin),
                           json={"rows": [{"user_id": user_id, "name": name, "group": group}],
                                 "update_existing": False})
    assert response.status_code == 200, response.text
    temporary = response.json()["created"][0]["temporary_password"]
    restricted = login(client, user_id, temporary)
    response = client.post("/api/auth/password", headers=auth(restricted),
                           json={"current_password": temporary, "new_password": NEW_PASSWORD,
                                 "confirm_password": NEW_PASSWORD})
    assert response.status_code == 200, response.text
    return login(client, user_id, NEW_PASSWORD)


def assignment(client, session):
    response = client.get("/api/assignments", headers=auth(session))
    assert response.status_code == 200, response.text
    return response.json()[0]["id"]


def begin(client, session, files, request_id=None, assignment_id=None):
    return client.post("/api/uploads", headers=auth(session), json={
        "assignment_id": assignment_id or assignment(client, session),
        "request_id": request_id or str(uuid.uuid4()),
        "files": [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in files],
    })


def chunk(client, session, upload, file, offset, data, digest=None):
    return client.patch(f"/api/uploads/{upload['id']}/files/{file['id']}?offset={offset}",
                        headers={**auth(session), "X-Chunk-SHA256": digest or hashlib.sha256(data).hexdigest()},
                        content=data)


def send_file(client, session, upload, index, data):
    file = upload["files"][index]
    for offset in range(0, len(data), 8):
        response = chunk(client, session, upload, file, offset, data[offset:offset + 8])
        assert response.status_code == 200, response.text


def finish(client, session, upload):
    return client.post(f"/api/uploads/{upload['id']}/complete", headers=auth(session))


def complete_one(client, session, data=b"hello", name="과제.txt"):
    response = begin(client, session, [(name, data)])
    assert response.status_code == 200, response.text
    upload = response.json()
    send_file(client, session, upload, 0, data)
    response = finish(client, session, upload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    return response.json()


def tsv_bytes(rows, headers=None):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\r\n")
    writer.writerow(headers if headers is not None else ["user_id", "name", "group"])
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def test_a01_template_and_roster_validation(hub):
    client, _, admin = hub
    shipped = (Path(__file__).parents[1] / "templates" / "users_template.tsv").read_bytes()
    preview = client.post("/api/admin/roster/preview", content=shipped, headers=auth(admin))
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid"]
    assert [row["user_id"] for row in preview.json()["rows"]] == ["001", "002"]
    good = client.post("/api/admin/roster/preview", content=tsv_bytes([("001", "가", "A")]), headers=auth(admin))
    assert good.status_code == 200, good.text
    assert good.json()["valid"] and good.json()["rows"][0]["user_id"] == "001"
    for rows in [[("001", "가", "A"), ("001", "나", "B")], [(None, "가", "")], [("001", "", "")]]:
        response = client.post("/api/admin/roster/preview", content=tsv_bytes(rows), headers=auth(admin))
        assert response.status_code == 200, response.text
        assert not response.json()["valid"]


def test_a01_reregistration_keeps_password_history_and_active(hub):
    client, _, admin = hub
    session = student(client, admin)
    done = complete_one(client, session)
    response = client.post("/api/admin/roster/apply", headers=auth(admin), json={
        "rows": [{"user_id": "001", "name": "변경 이름", "group": "B반"}], "update_existing": True})
    assert response.status_code == 200, response.text
    assert response.json()["created"] == []
    again = login(client, "001", NEW_PASSWORD)
    assert again["user"]["name"] == "변경 이름"
    assert client.get("/api/submissions?all_versions=true", headers=auth(again)).json()[0]["id"] == done["id"]


def test_a01_inactive_reregistration_and_case_sensitive_ids(hub):
    client, _, admin = hub
    session = student(client, admin)
    completed = complete_one(client, session)
    assert client.patch(f"/api/admin/users/{session['user']['id']}", headers=auth(admin), json={"active": False}).status_code == 200
    response = client.post("/api/admin/roster/apply", headers=auth(admin), json={
        "rows": [{"user_id": " 001 ", "name": "바뀐 이름", "group": "B"},
                 {"user_id": "Case", "name": "대문자", "group": ""},
                 {"user_id": "case", "name": "소문자", "group": ""}], "update_existing": True})
    assert response.status_code == 200, response.text
    new = response.json()["created"]
    assert {row["user_id"] for row in new} == {"Case", "case"}
    assert len({row["temporary_password"] for row in new}) == 2
    accounts = client.get("/api/admin/users", headers=auth(admin)).json()
    changed = next(row for row in accounts if row["user_id"] == "001")
    assert changed["name"] == "바뀐 이름" and not changed["active"]
    assert client.get("/api/quota", headers=auth(session)).status_code in (401, 403)
    assert client.patch(f"/api/admin/users/{session['user']['id']}", headers=auth(admin), json={"active": True}).status_code == 200
    again = login(client, "001", NEW_PASSWORD)
    history = client.get("/api/submissions?all_versions=true", headers=auth(again)).json()
    assert history[0]["id"] == completed["id"]


def test_a01_missing_headers_and_existing_preview(hub):
    client, _, admin = hub
    student(client, admin)
    missing = client.post("/api/admin/roster/preview", headers=auth(admin),
                          content=tsv_bytes([("001", "가", "A")], ["wrong_id", "name", "group"]))
    assert missing.status_code == 200 and not missing.json()["valid"] and missing.json()["errors"]
    existing = client.post("/api/admin/roster/preview", headers=auth(admin),
                           content=tsv_bytes([("001", "수정 이름", "A")]))
    assert existing.status_code == 200
    assert existing.json()["rows"][0]["existing"]["user_id"] == "001"
    assert existing.json()["rows"][0]["action"] == "update"


def test_a02_restricted_session_and_password_rules(hub):
    client, _, admin = hub
    response = client.post("/api/admin/roster/apply", headers=auth(admin), json={
        "rows": [{"user_id": "001", "name": "가", "group": ""}], "update_existing": False})
    temporary = response.json()["created"][0]["temporary_password"]
    session = login(client, "001", temporary)
    assert session["must_change_password"]
    for route in ["/api/assignments", "/api/submissions", "/api/quota", "/api/uploads"]:
        assert client.get(route, headers=auth(session)).status_code in (401, 403)
    for new, confirm in [(temporary, temporary), ("short", "short"), ("x" * 129, "x" * 129), (NEW_PASSWORD, "different")]:
        response = client.post("/api/auth/password", headers=auth(session), json={
            "current_password": temporary, "new_password": new, "confirm_password": confirm})
        assert 400 <= response.status_code < 500


def test_a03_reset_invalidates_existing_chunk_and_complete(hub):
    client, _, admin = hub
    session = student(client, admin)
    upload = begin(client, session, [("f", b"12345678")]).json()
    response = client.post(f"/api/admin/users/{session['user']['id']}/reset", headers=auth(admin))
    assert response.status_code == 200, response.text
    assert chunk(client, session, upload, upload["files"][0], 0, b"12345678").status_code in (401, 403)
    assert finish(client, session, upload).status_code in (401, 403)
    restricted = login(client, "001", response.json()["temporary_password"])
    assert finish(client, restricted, upload).status_code in (401, 403)


def test_a04_ownership_and_admin_only(hub):
    client, _, admin = hub
    one, two = student(client, admin), student(client, admin, "002")
    upload = begin(client, one, [("f", b"12345678")]).json()
    assert client.get(f"/api/uploads/{upload['id']}", headers=auth(two)).status_code in (403, 404)
    assert chunk(client, two, upload, upload["files"][0], 0, b"12345678").status_code in (403, 404)
    assert finish(client, two, upload).status_code in (403, 404)
    assert client.get("/api/admin/users", headers=auth(one)).status_code == 403
    send_file(client, one, upload, 0, b"12345678")
    completed = finish(client, one, upload).json()
    file_id = completed["files"][0]["id"]
    assert client.get(f"/api/files/{file_id}/download", headers=auth(two)).status_code in (403, 404)
    assert client.get(f"/api/files/{file_id}/download", headers=auth(admin)).content == b"12345678"


def test_u02_scaled_file_boundary_actual_bytes_and_hash(hub):
    client, _, admin = hub
    session = student(client, admin)
    assert begin(client, session, [("too-big", b"x" * 33)]).status_code in (400, 413, 422)
    upload = begin(client, session, [("at-limit", b"x" * 32)]).json()
    assert chunk(client, session, upload, upload["files"][0], 0, b"x" * 9).status_code in (400, 413, 422)
    assert chunk(client, session, upload, upload["files"][0], 0, b"x" * 8, "0" * 64).status_code in (400, 409, 422)
    send_file(client, session, upload, 0, b"x" * 32)
    assert chunk(client, session, upload, upload["files"][0], 32, b"x").status_code in (400, 409, 413)
    assert finish(client, session, upload).json()["status"] == "completed"


def test_u05_start_chunk_and_completion_idempotency(hub):
    client, _, admin = hub
    session = student(client, admin)
    request_id = str(uuid.uuid4())
    upload = begin(client, session, [("f", b"12345678")], request_id).json()
    assert begin(client, session, [("f", b"12345678")], request_id).json()["id"] == upload["id"]
    for _ in range(2):
        assert chunk(client, session, upload, upload["files"][0], 0, b"12345678").status_code == 200
    done = finish(client, session, upload).json()
    assert finish(client, session, upload).json()["submission_number"] == done["submission_number"]
    quota = client.get("/api/quota", headers=auth(session)).json()
    assert quota["used_bytes"] == 8 and quota["reserved_bytes"] == 0
    assert len(client.get("/api/submissions?all_versions=true", headers=auth(session)).json()) == 1


def test_u07_bundle_is_atomic_and_closed_assignment_allows_existing(hub):
    client, _, admin = hub
    session = student(client, admin)
    aid = assignment(client, session)
    upload = begin(client, session, [("first", b"123"), ("second", b"456")]).json()
    send_file(client, session, upload, 0, b"123")
    assert finish(client, session, upload).status_code in (400, 409)
    dashboard = client.get(f"/api/admin/dashboard?assignment_id={aid}", headers=auth(admin)).json()
    assert dashboard["submitted_count"] == 0
    assert client.patch(f"/api/admin/assignments/{aid}", headers=auth(admin), json={"is_open": False}).status_code == 200
    assert begin(client, session, [("new", b"123")], assignment_id=aid).status_code in (400, 403, 409)
    send_file(client, session, upload, 1, b"456")
    assert finish(client, session, upload).json()["status"] == "completed"


def test_q01_same_user_parallel_reservations_and_q03_cancel(hub):
    client, _, admin = hub
    session = student(client, admin)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: begin(client, session, [("f", b"x" * 32)]), range(4)))
    accepted = [r.json() for r in responses if r.status_code == 200]
    assert len(accepted) == 2, [r.text for r in responses]
    quota = client.get("/api/quota", headers=auth(session)).json()
    assert quota["reserved_bytes"] == 64
    for upload in accepted:
        response = client.post(f"/api/uploads/{upload['id']}/cancel", headers=auth(session))
        assert response.status_code == 200, response.text
    assert client.get("/api/quota", headers=auth(session)).json()["reserved_bytes"] == 0


def test_i01_i02_instance_token_separation(hub, tmp_path):
    client, _, admin = hub
    session = student(client, admin)
    config = Config(instance_id="other_hub", course_name="다른 과정", port=18992,
                    public_host="127.0.0.1", storage_root=str(tmp_path / "other"), min_free_bytes=0)
    Service(config).bootstrap_admin("admin", ADMIN_PASSWORD)
    with TestClient(create_app(config)) as other:
        other_admin = login(other, "admin", ADMIN_PASSWORD)
        other_session = student(other, other_admin)
        assert other.get("/api/quota", headers=auth(session)).status_code in (401, 403)
        assert client.get("/api/quota", headers=auth(other_session)).status_code in (401, 403)
        complete_one(client, session)
        assert other.get("/api/quota", headers=auth(other_session)).json()["used_bytes"] == 0


def test_m01_versions_targets_inactive_and_csv_safety(hub):
    client, _, admin = hub
    session = student(client, admin, name="=1+1", group="+A")
    other = student(client, admin, "002")
    one, two = complete_one(client, session), complete_one(client, session)
    assert one["submission_number"] != two["submission_number"]
    aid = assignment(client, session)
    dashboard = client.get(f"/api/admin/dashboard?assignment_id={aid}", headers=auth(admin)).json()
    assert (dashboard["target_count"], dashboard["submitted_count"], dashboard["missing_count"]) == (2, 1, 1)
    assert client.patch(f"/api/admin/users/{other['user']['id']}", headers=auth(admin), json={"active": False}).status_code == 200
    dashboard = client.get(f"/api/admin/dashboard?assignment_id={aid}", headers=auth(admin)).json()
    assert dashboard["target_count"] == 1
    exported = client.get(f"/api/admin/export?assignment_id={aid}", headers=auth(admin))
    assert exported.status_code == 200 and "'=1+1" in exported.text


@pytest.mark.parametrize("name", ["../escape.txt", "C:\\absolute.txt", "CON", "file.txt:ads", "한글 공백.txt"])
def test_s01_untrusted_names_never_control_storage_path(hub, name):
    client, config, admin = hub
    session = student(client, admin)
    response = begin(client, session, [(name, b"safe")])
    if response.status_code != 200:
        assert 400 <= response.status_code < 500
        return
    upload = response.json()
    send_file(client, session, upload, 0, b"safe")
    done = finish(client, session, upload)
    assert done.status_code == 200, done.text
    assert done.json()["files"][0]["name"] == name
    dashboard = client.get("/api/admin/dashboard", headers=auth(admin)).json()
    for submission in dashboard["submissions"]:
        for file in submission["files"]:
            storage_path = Path(file["storage_path"]).resolve()
            assert storage_path.is_relative_to(config.root.resolve())
            assert storage_path.name != name


def test_existing_login_opens_upload_and_download_ticket_stays_one_time(hub):
    client, _, admin = hub
    session = student(client, admin)
    opened = client.post("/upload", data={"token": session["token"]})
    assert opened.status_code == 200
    assert 'id="session-data"' in opened.text
    assert client.post("/api/auth/bridge", headers=auth(session)).status_code == 404
    assert client.post("/api/auth/exchange", json={"code": "old-code"}).status_code == 404
    completed = complete_one(client, session)
    file_id = completed["files"][0]["id"]
    ticket = client.post(f"/api/files/{file_id}/ticket", headers=auth(session)).json()["ticket"]
    downloaded = client.post("/api/downloads", data={"ticket": ticket})
    assert downloaded.status_code == 200 and downloaded.content == b"hello"
    assert client.post("/api/downloads", data={"ticket": ticket}).status_code in (400, 401, 403, 404)
    assert client.post("/api/auth/logout", headers=auth(session)).status_code == 200
    assert client.get("/api/quota", headers=auth(session)).status_code in (401, 403)


def test_successful_login_does_not_consume_peer_failure_budget(hub):
    client, _, admin = hub
    student(client, admin)
    service = client.app.state.service
    peer_key = service.digest("login-peer:testclient")
    with service.store.connect(write=True) as db:
        db.execute("UPDATE login_attempts SET failures=99 WHERE key=?", (peer_key,))
    # A valid login must not increase the existing failure budget. Repeating it
    # models many students behind the same single-port gateway peer address.
    for _ in range(3):
        response = client.post("/api/auth/login", json={"user_id": "001", "password": NEW_PASSWORD})
        assert response.status_code == 200, response.text
