"""Credential boundaries and opening the real uploader without a connection code."""
import json
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from assignmenthub.api import create_app
from assignmenthub.cli import _admin_inputs
from assignmenthub.management import Catalog
from assignmenthub.passwords import temporary_password
from assignmenthub.service import Service
from test_api import hub, auth, student, login, complete_one, NEW_PASSWORD
from test_management import config_at


def bootstrap(response):
    assert response.status_code == 200
    return json.loads(re.search(r'<script id="session-data" type="application/json">(.*?)</script>', response.text).group(1))


def register(client, admin, rows, common=None, **extra):
    return client.post("/api/admin/roster/apply", headers=auth(admin), json={
        "rows": rows, "common_temporary_password": common, **extra})


def test_desktop_and_cli_accept_eight_character_admin_password(tmp_path, monkeypatch):
    password = "Adm1n!23"
    catalog = Catalog(tmp_path / "instances")
    path = catalog.create(config_at(tmp_path / "store"), "admin", password, password)
    assert Service(catalog.courses()[0].config).login("admin", password, "test")["user"]["role"] == "admin"
    assert path.is_file()
    monkeypatch.setattr("assignmenthub.cli.getpass.getpass", lambda _: password)
    assert _admin_inputs("admin") == ("admin", password)
    with pytest.raises(ValueError, match="8~128"):
        catalog.create(config_at(tmp_path / "too-short", "short"), "admin", "1234567", "1234567")


@pytest.mark.parametrize("password,expected", [("1234567", 422), ("Ab12!xyz", 200), ("가나다라마바사아", 200), ("x" * 128, 200), ("x" * 129, 422)])
def test_change_password_length_boundaries(hub, password, expected):
    client, _, admin = hub
    created = register(client, admin, [{"user_id": "001", "name": "학생"}]).json()["created"][0]
    initial = created["temporary_password"]
    assert len(initial.encode("utf-8")) == 8 and initial.isascii() and initial.isalnum()
    restricted = login(client, "001", initial)
    assert client.post("/upload", data={"token": restricted["token"]}).status_code == 403
    response = client.post("/api/auth/password", headers=auth(restricted), json={
        "current_password": initial, "new_password": password, "confirm_password": password})
    assert response.status_code == expected
    if expected == 200:
        session = login(client, "001", password)
        assert bootstrap(client.post("/upload", data={"token": session["token"]}))["user"]["user_id"] == "001"
        assert client.get("/api/quota", headers=auth(restricted)).status_code == 401


def test_common_roster_password_only_affects_new_accounts(hub):
    client, _, admin = hub
    existing = student(client, admin)
    completed = complete_one(client, existing)
    common = "Class123"
    response = register(client, admin, [{"user_id": "001", "name": "기존 학생", "group": "변경"},
                                       {"user_id": "002", "name": "신규 2"}, {"user_id": "003", "name": "신규 3"}],
                        common, update_existing=True)
    assert response.status_code == 200, response.text
    assert [row["temporary_password"] for row in response.json()["created"]] == [common, common]
    for uid in ("002", "003"):
        session = login(client, uid, common)
        assert session["must_change_password"]
        assert client.get("/api/assignments", headers=auth(session)).status_code == 403
    assert login(client, "001", NEW_PASSWORD)["user"]["group"] == "변경"
    assert client.get("/api/submissions", headers=auth(existing)).json()[0]["id"] == completed["id"]
    # Later registrations default to a fresh per-person value unless specified again.
    later = register(client, admin, [{"user_id": "004", "name": "추가 학생"}]).json()["created"][0]
    assert later["temporary_password"] != common and len(later["temporary_password"].encode()) == 8
    service = client.app.state.service
    with service.store.connect() as db:
        rows = list(db.execute("SELECT password_hash FROM users WHERE user_id IN ('002','003')"))
        assert rows[0][0] != rows[1][0], "Independently salted account hashes"
        assert common not in " ".join(str(tuple(row)) for row in db.execute("SELECT * FROM audit"))
    assert common not in client.get("/api/admin/users", headers=auth(admin)).text


@pytest.mark.parametrize("bad", ["", "1234567", "123456789", "가나다라마바사아", "abc defg", "Abcd!234"])
def test_invalid_common_password_rejects_the_whole_roster(hub, bad):
    client, _, admin = hub
    response = register(client, admin, [{"user_id": "001", "name": "학생"}], bad)
    assert response.status_code == 422
    assert [u["user_id"] for u in client.get("/api/admin/users", headers=auth(admin)).json()] == ["admin"]


def test_reset_uses_eight_bytes_and_revokes_the_open_uploader(hub):
    client, _, admin = hub
    session = student(client, admin)
    assert bootstrap(client.post("/upload", data={"token": session["token"]}))["token"] == session["token"]
    result = client.post(f"/api/admin/users/{session['user']['id']}/reset", headers=auth(admin)).json()
    assert re.fullmatch(r"[A-Za-z0-9]{8}", result["temporary_password"])
    assert client.get("/api/quota", headers=auth(session)).status_code == 401
    assert login(client, "001", result["temporary_password"])["must_change_password"]


def test_direct_upload_keeps_assignment_identity_and_session_boundaries(hub):
    client, config, admin = hub
    session = student(client, admin, name="</script><img src=x onerror=alert(1)>")
    created = client.post("/api/admin/assignments", headers=auth(admin), json={"title": "두 번째 과제"}).json()
    response = client.post("/upload", data={"token": session["token"], "assignment_id": created["id"]},
                           headers={"Origin": config.public_url})
    data = bootstrap(response)
    assert data["assignment_id"] == created["id"] and data["token"] == session["token"]
    assert "</script><img" not in response.text
    assert "no-store" in response.headers["cache-control"]
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
    assert "set-cookie" not in response.headers
    assert client.get("/upload").status_code == 200
    assert session["token"] not in client.get("/upload").text
    assert client.post("/upload", data={}).status_code == 401
    assert client.post("/upload", data={"token": "invalid"}).status_code == 401
    other = student(client, admin, "002")
    assert register(client, other, [{"user_id": "003", "name": "불가"}], "Class123").status_code == 403
    with TestClient(create_app(replace(config, instance_id="another", storage_root=str(config.root.parent / "other")))) as second:
        assert second.post("/upload", data={"token": session["token"]}).status_code == 401
    with client.app.state.service.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM grants").fetchone()[0] == 0


def test_temporary_values_are_displayed_as_exactly_eight_ascii_bytes():
    for _ in range(100):
        value = temporary_password()
        assert re.fullmatch(r"[A-Za-z0-9]{8}", value) and len(value.encode("utf-8")) == 8
