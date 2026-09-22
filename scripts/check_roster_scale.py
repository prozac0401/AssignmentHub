"""Validate a large roster through Caddy, reconnect, restart and result acknowledgement.

Creates an isolated local instance; only timing/counts are written to its report.
Uses real password hashing. The default 2,500 rows may take several minutes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assignmenthub.config import Config
from assignmenthub.launcher import internal_port, start, stop
from assignmenthub.service import Service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=2500)
    args = parser.parse_args()
    if not 1 <= args.rows <= 10000:
        parser.error("--rows must be between 1 and 10000")
    run_id = "roster_scale_" + uuid.uuid4().hex[:10]
    folder = ROOT / ".local-tests" / run_id
    folder.mkdir(parents=True)
    config = Config(instance_id=run_id, course_name="대량 명단 회귀 검증", port=internal_port(),
                    public_host="127.0.0.1", bind_host="127.0.0.1",
                    storage_root=str(folder / "store"), min_free_bytes=0)
    config_path = folder / "instance.json"
    config.save(config_path)
    password = secrets.token_urlsafe(24)
    Service(config).bootstrap_admin("admin", password)
    payload = {"request_id": "scale_request_1", "rows": [
        {"user_id": f"student_{i:05}", "name": f"검증 수강생 {i}", "group": "대량 등록"}
        for i in range(args.rows)]}
    report = {"rows": args.rows, "credentials_saved": False}
    base = config.public_url + "/api"
    try:
        start(config_path)
        with httpx.Client(timeout=60, trust_env=False) as client:
            def request(method, path, expected=200, **kwargs):
                response = client.request(method, base + path, **kwargs)
                assert response.status_code == expected, f"{method} {path}: HTTP {response.status_code}"
                return response.json()

            token = request("POST", "/auth/login", json={"user_id": "admin", "password": password})["token"]
            client.headers["Authorization"] = "Bearer " + token
            started = time.monotonic()
            job = request("POST", "/admin/roster/jobs", expected=202, json=payload)
            report["accept_seconds"] = round(time.monotonic() - started, 3)
            assert report["accept_seconds"] < 60
            job_path = "/admin/roster/jobs/" + job["id"]
            replay = request("POST", "/admin/roster/jobs", expected=202, json=payload)
            assert replay["id"] == job["id"]
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                job = request("GET", job_path)
                if job["status"] == "completed":
                    break
                assert job["status"] in ("queued", "running"), job["status"]
                time.sleep(2)
            else:
                raise AssertionError("large roster did not finish within 30 minutes")
            report["completion_seconds"] = round(time.monotonic() - started, 3)
            result = request("GET", job_path + "/result")
            assert len(result["created"]) == args.rows and result["updated"] == 0
            fingerprint = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
            request("POST", "/auth/logout")
            token = request("POST", "/auth/login", json={"user_id": "admin", "password": password})["token"]
            client.headers["Authorization"] = "Bearer " + token
            assert any(item["id"] == job["id"] for item in request("GET", "/admin/roster/jobs"))
            assert request("GET", job_path + "/result") == result
            print(json.dumps({**report, "reconnected": True}), flush=True)
            stop(config)
            start(config_path)
            recovered = request("GET", job_path + "/result")
            assert hashlib.sha256(json.dumps(recovered, sort_keys=True).encode()).hexdigest() == fingerprint
            assert request("POST", "/admin/roster/jobs", expected=202, json=payload)["created_count"] == args.rows
            for index in sorted({0, args.rows // 2, args.rows - 1}):
                row = recovered["created"][index]
                assert request("POST", "/auth/login", json={"user_id": row["user_id"],
                               "password": row["temporary_password"]})["must_change_password"]
            with Service(config).store.connect() as db:
                assert db.execute("SELECT count(*) FROM users WHERE role='student'").fetchone()[0] == args.rows
                assert db.execute("SELECT count(*) FROM audit WHERE action='roster_apply'").fetchone()[0] == 1
            request("DELETE", job_path + "/result")
            assert client.get(base + job_path + "/result").status_code == 410
            assert not (config.root / "roster-keys" / (job["id"] + ".key")).exists()
            report.update(reconnect_result=True, restart_result=True, idempotent=True,
                          sampled_passwords_valid=True, acknowledged_key_deleted=True)
    finally:
        stop(config)
    report["stopped"] = True
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    print("Report:", folder / "report.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
