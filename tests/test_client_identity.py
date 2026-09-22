"""Client isolation and authenticated identity across gateway/UI/API hops."""
from __future__ import annotations

import io
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from types import SimpleNamespace
from urllib.error import HTTPError

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.runtime.context import StreamlitHeaders

from assignmenthub import ui
from assignmenthub.api import create_app
from assignmenthub.client_identity import (CLIENT_IP_HEADER, GATEWAY_KEY_HEADER, SECRET_ENV,
                                           UI_SIGNATURE_HEADER, login_client_ip, ui_login_headers)
from assignmenthub.config import Config
from assignmenthub.launcher import (_environment, caddy_config, internal_port,
                                    owned_process, proxy_binary, start, stop)
from assignmenthub.service import Service


PASSWORD = "Client-isolation-password!"


def gateway_headers(secret, address):
    return {GATEWAY_KEY_HEADER: secret, CLIENT_IP_HEADER: address}


def test_login_proof_is_bound_to_source_credentials_time_and_local_transport():
    secret = secrets.token_urlsafe(32)
    headers = ui_login_headers(gateway_headers(secret, "192.0.2.1"), secret,
                               "admin", PASSWORD, now=1000)
    assert login_client_ip(headers, "127.0.0.1", secret, "admin", PASSWORD, now=1020) == "192.0.2.1"
    for peer, user_id, password, now in [
        ("127.0.0.1", "admin", PASSWORD, 1061),
        ("127.0.0.1", "admin", PASSWORD, 994),
        ("127.0.0.1", "another", PASSWORD, 1000),
        ("127.0.0.1", "admin", "changed", 1000),
        ("198.51.100.1", "admin", PASSWORD, 1000),
    ]:
        assert login_client_ip(headers, peer, secret, user_id, password, now=now) == peer
    for changes in [{CLIENT_IP_HEADER: "192.0.2.2"}, {UI_SIGNATURE_HEADER: "forged"}]:
        assert login_client_ip({**headers, **changes}, "127.0.0.1", secret,
                               "admin", PASSWORD, now=1000) == "127.0.0.1"
    assert ui_login_headers(gateway_headers("forged", "192.0.2.1"), secret, "admin", PASSWORD) == {}
    assert ui_login_headers({"X-Forwarded-For": "192.0.2.1"}, secret, "admin", PASSWORD) == {}
    assert ui_login_headers(gateway_headers("", "192.0.2.1"), "", "admin", PASSWORD) == {}


@pytest.fixture
def identity_hub(tmp_path, monkeypatch):
    secret = secrets.token_urlsafe(32)
    monkeypatch.setenv(SECRET_ENV, secret)
    config = Config(instance_id="identity_test", course_name="로그인 격리", port=internal_port(),
                    public_host="127.0.0.1", storage_root=str(tmp_path / "store"), min_free_bytes=0)
    Service(config).bootstrap_admin("admin", PASSWORD)
    app = create_app(config)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client, app.state.service, secret


def test_ui_call_isolates_clients_and_keeps_account_rate_limit(identity_hub, monkeypatch):
    client, service, secret = identity_hub
    context = SimpleNamespace(headers=StreamlitHeaders(gateway_headers(secret, "192.0.2.1").items()))
    monkeypatch.setattr(ui, "st", SimpleNamespace(context=context, session_state={}))

    def forward_to_api(request, timeout):
        response = client.request(request.method, "/api/auth/login", content=request.data,
                                  headers=dict(request.header_items()))
        if response.status_code >= 400:
            raise HTTPError(request.full_url, response.status_code, "", {}, io.BytesIO(response.content))
        return io.BytesIO(response.content)

    monkeypatch.setattr(ui, "urlopen", forward_to_api)
    for index in range(100):
        with pytest.raises(ui.APIError) as exc:
            ui.call("/auth/login", "POST", {"user_id": f"missing-{index}", "password": "wrong"})
        assert exc.value.status == 401
    with pytest.raises(ui.APIError) as exc:
        ui.call("/auth/login", "POST", {"user_id": "admin", "password": PASSWORD})
    assert exc.value.status == 429
    context.headers = StreamlitHeaders(gateway_headers(secret, "192.0.2.2").items())
    assert ui.call("/auth/login", "POST", {"user_id": "admin", "password": PASSWORD})["user"]["user_id"] == "admin"
    for index in range(8):
        context.headers = StreamlitHeaders(gateway_headers(secret, f"198.51.100.{index + 1}").items())
        with pytest.raises(ui.APIError) as exc:
            ui.call("/auth/login", "POST", {"user_id": "admin", "password": "wrong"})
        assert exc.value.status == 401
    context.headers = StreamlitHeaders(gateway_headers(secret, "198.51.100.99").items())
    with pytest.raises(ui.APIError) as exc:
        ui.call("/auth/login", "POST", {"user_id": "admin", "password": PASSWORD})
    assert exc.value.status == 429


def test_forged_forwarded_headers_do_not_change_api_peer(identity_hub):
    client, service, secret = identity_hub
    proof = ui_login_headers(gateway_headers(secret, "192.0.2.10"), secret,
                            "admin", PASSWORD, now=time.time() - 120)
    variants = [
        {"X-Forwarded-For": "192.0.2.1", "X-Real-IP": "192.0.2.2", "Forwarded": "for=192.0.2.3"},
        gateway_headers("fake", "192.0.2.4"),
        {**gateway_headers("fake", "192.0.2.5"), UI_SIGNATURE_HEADER: "fake"},
        proof,
    ]
    with service.store.connect(write=True) as db:
        db.execute("INSERT INTO login_attempts VALUES (?,100,?)",
                   (service.digest("login-peer:127.0.0.1"), time.time()))
    for headers in variants:
        response = client.post("/api/auth/login", headers=headers,
                               json={"user_id": "admin", "password": PASSWORD})
        assert response.status_code == 429
    response = client.post("/api/auth/login", headers=gateway_headers(secret, "192.0.2.6"),
                           json={"user_id": "admin", "password": PASSWORD})
    assert response.status_code == 200


def test_launcher_secret_rotates_and_is_not_persisted(tmp_path):
    first = _environment(tmp_path / "config.json", 12345, "first", tmp_path)
    second = _environment(tmp_path / "config.json", 12345, "second", tmp_path)
    assert first[SECRET_ENV] != second[SECRET_ENV]
    config = Config(instance_id="identity_test", course_name="로그인 격리", port=12346,
                    public_host="127.0.0.1", storage_root=str(tmp_path))
    rendered = caddy_config(config, 12345, 12347)
    assert first[SECRET_ENV] not in rendered
    assert "{$AH_INTERNAL_CLIENT_SECRET}" in rendered
    assert "persist_config off" in rendered


def test_https_gateway_path_mismatch_never_redirects_login_over_http(identity_hub):
    client, service, secret = identity_hub
    headers = {**gateway_headers(secret, "192.0.2.70"),
               "X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.80"}
    body = {"user_id": "admin", "password": PASSWORD}
    response = client.post("http://course.example/api/auth/login/", headers=headers,
                           json=body, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers
    response = client.post("http://course.example/api/auth/login", headers=headers, json=body)
    assert response.status_code == 200
    with service.store.connect() as db:
        assert db.execute("SELECT failures FROM login_attempts WHERE key=?",
                          (service.digest("login-peer:192.0.2.70"),)).fetchone()["failures"] == 0
        assert db.execute("SELECT 1 FROM login_attempts WHERE key=?",
                          (service.digest("login-peer:192.0.2.80"),)).fetchone() is None


@pytest.mark.integration
def test_real_browser_gateway_ui_login_identity(tmp_path):
    """Real browser WebSocket -> Caddy -> Streamlit -> loopback API login."""
    proxy_binary()
    config = Config(instance_id="login_browser", course_name="로그인 격리", port=internal_port(),
                    public_host="127.0.0.1", bind_host="127.0.0.1", storage_root=str(tmp_path / "store"),
                    min_free_bytes=0)
    config_path = tmp_path / "config.json"
    config.save(config_path)
    service = Service(config)
    service.bootstrap_admin("admin", PASSWORD)
    with service.store.connect(write=True) as db:
        db.execute("INSERT INTO login_attempts VALUES (?,100,?)",
                   (service.digest("login-peer:127.0.0.2"), time.time()))
    fixture = tmp_path / "browser.json"
    fixture.write_text(json.dumps({"url": config.public_url, "password": PASSWORD}), encoding="utf-8")
    try:
        state = start(config_path)
        with httpx.Client(timeout=10, trust_env=False,
                          transport=httpx.HTTPTransport(local_address="127.0.0.2")) as client:
            for base_url in [config.public_url, f"http://127.0.0.1:{state['api_port']}"]:
                # The public gateway overrides forged identities; the private
                # API ignores untrusted XFF and uses this socket's 127.0.0.2.
                response = client.post(base_url + "/api/auth/login",
                                       headers={**gateway_headers("fake", "192.0.2.1"),
                                                "X-Forwarded-For": "192.0.2.2"},
                                       json={"user_id": "admin", "password": PASSWORD})
                assert response.status_code == 429, response.text
        result = subprocess.run([shutil.which("node"), str(Path(__file__).with_suffix(".cjs")), str(fixture)],
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        with service.store.connect() as db:
            assert db.execute("SELECT failures FROM login_attempts WHERE key=?",
                              (service.digest("login-peer:127.0.0.2"),)).fetchone()["failures"] == 100
            assert db.execute("SELECT failures FROM login_attempts WHERE key=?",
                              (service.digest("login-peer:127.0.0.3"),)).fetchone()["failures"] == 0
        assert not list((config.root / "proxy-config").rglob("autosave.json"))
        secret = owned_process(state["children"][0]).environ()[SECRET_ENV].encode()
        for path in [config.root / "Caddyfile", config.root / "runtime.json", *(config.root / "logs").glob("*.log")]:
            # Do not let pytest's assertion introspection print the secret.
            if secret in path.read_bytes():
                raise AssertionError(f"Internal client secret persisted in {path.name}")
    finally:
        stop(config)
        fixture.unlink(missing_ok=True)
