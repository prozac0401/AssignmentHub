"""Actual partial TCP request bodies through the public Caddy gateway."""
import hashlib
import socket
import time

import httpx
import pytest

from assignmenthub.config import Config
from assignmenthub.launcher import LaunchError, internal_port, proxy_binary, start, stop
from assignmenthub.service import Service


@pytest.mark.integration
def test_real_proxy_disconnect_mid_chunk_resumes_at_durable_offset(tmp_path):
    try:
        proxy_binary()
    except LaunchError:
        pytest.skip("Pinned Caddy not installed")
    config = Config(instance_id="network_disconnect", course_name="연결 종료 검증", port=internal_port(),
                    storage_root=str(tmp_path / "store"), bind_host="127.0.0.1", public_host="127.0.0.1",
                    min_free_bytes=0, chunk_bytes=8, max_file_bytes=64, user_quota_bytes=128)
    config_path = tmp_path / "config.json"
    config.save(config_path)
    service = Service(config)
    service.bootstrap_admin("admin", "Network-test-admin-32")
    admin = service.login("admin", "Network-test-admin-32", "local")["token"]
    temporary = service.roster_apply(admin, [{"user_id": "0001", "name": "연결 검증", "group": ""}])["created"][0]["temporary_password"]
    restricted = service.login("0001", temporary, "local")["token"]
    service.change_password(restricted, temporary, "Network-test-student-32", "Network-test-student-32")
    token = service.login("0001", "Network-test-student-32", "local")["token"]
    headers = {"Authorization": "Bearer " + token}
    data = b"abcdefghijklmnop"
    started = False
    try:
        start(config_path)
        started = True
        with httpx.Client(base_url=config.public_url, timeout=30, trust_env=False) as client:
            assignment_id = client.get("/api/assignments", headers=headers).json()[0]["id"]
            response = client.post("/api/uploads", headers=headers, json={"assignment_id": assignment_id,
                                   "request_id": "network_disconnect_01", "files": [{"name": "청크 중단.bin", "size": len(data),
                                   "sha256": hashlib.sha256(data).hexdigest()}]})
            assert response.status_code == 200, response.text
            upload = response.json()
            upload_id, file_id = upload["id"], upload["files"][0]["id"]
            route = f"/api/uploads/{upload_id}/files/{file_id}"

            def interrupted_chunk(offset):
                chunk = data[offset:offset+8]
                request = (f"PATCH {route}?offset={offset} HTTP/1.1\r\n"
                           f"Host: 127.0.0.1:{config.port}\r\n"
                           f"Authorization: Bearer {token}\r\n"
                           f"X-Chunk-SHA256: {hashlib.sha256(chunk).hexdigest()}\r\n"
                           "Content-Type: application/octet-stream\r\nContent-Length: 8\r\nConnection: close\r\n\r\n").encode("ascii")
                with socket.create_connection((config.bind_host, config.port), timeout=10) as wire:
                    wire.sendall(request + chunk[:3])
                    # The declared body remains incomplete while the gateway can
                    # forward the first bytes. Closing generates actual TCP EOF.
                    time.sleep(0.4)
                    wire.shutdown(socket.SHUT_RDWR)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    current = client.get(f"/api/uploads/{upload_id}", headers=headers).json()
                    assert current["files"][0]["offset"] == offset
                    if current["status"] == "paused":
                        return
                    time.sleep(0.1)
                raise AssertionError("The actual disconnected request did not settle to paused")

            for offset in (0, 8):
                interrupted_chunk(offset)
                temporary_path = config.root / "tmp" / upload_id / (file_id + ".part")
                assert temporary_path.stat().st_size == offset
                chunk = data[offset:offset+8]
                response = client.patch(route + f"?offset={offset}", content=chunk,
                                        headers={**headers, "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()})
                assert response.status_code == 200, response.text
                assert response.json()["offset"] == offset + 8
            complete = client.post(f"/api/uploads/{upload_id}/complete", headers=headers)
            assert complete.status_code == 200, complete.text
            assert complete.json()["status"] == "completed"
            downloaded = client.get(f"/api/files/{file_id}/download", headers=headers)
            assert downloaded.content == data
            assert hashlib.sha256(downloaded.content).hexdigest() == hashlib.sha256(data).hexdigest()
            quota = client.get("/api/quota", headers=headers).json()
            assert quota["used_bytes"] == len(data) and quota["reserved_bytes"] == 0
    finally:
        stop(config)
