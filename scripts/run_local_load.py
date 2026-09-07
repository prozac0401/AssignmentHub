"""Provision a dedicated local instance and run real gateway load validation.

This writes up to 16 GiB of retained server submissions by default (4 x 2 GiB
distinct users plus 4 x 2 GiB for one user), and transient streamed source files.
Only the created instance is stopped; source cleanup is owned by load_test.py.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assignmenthub.config import Config
from assignmenthub.launcher import internal_port, start, stop
from assignmenthub.service import Service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=4)
    parser.add_argument("--file-bytes", type=int, default=2 * 1024 ** 3)
    parser.add_argument("--skip-same-user", action="store_true")
    args = parser.parse_args()
    run_id = "load_" + uuid.uuid4().hex[:10]
    directory = Path("test-runs").resolve() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    config_path = directory / "config.json"
    config = Config(instance_id=run_id, course_name="실제 대용량 검증", port=internal_port(),
                    bind_host="127.0.0.1", public_host="127.0.0.1", storage_root=str(directory / "server"))
    password = secrets.token_urlsafe(24)
    Service(config).bootstrap_admin("load_admin", password)
    config.save(config_path)
    environment = os.environ.copy()
    environment.update(AH_TEST_ADMIN_ID="load_admin", AH_TEST_ADMIN_PASSWORD=password, PYTHONUTF8="1")
    started = time.monotonic()
    results = []
    state = start(config_path)
    print(f"Public gateway: {config.public_url}; supervisor PID: {state['supervisor']['pid']}", flush=True)
    try:
        for mode in ([False] if args.skip_same_user else [False, True]):
            command = [sys.executable, str(Path(__file__).with_name("load_test.py")), "--url", config.public_url,
                       "--server-pid", str(state["supervisor"]["pid"]), "--storage-root", str(config.root),
                       "--users", str(args.users), "--file-bytes", str(args.file_bytes),
                       "--output", str(directory / ("same-user" if mode else "separate-users"))]
            if mode:
                command.append("--same-user")
            result = subprocess.run(command, env=environment, check=False)
            results.append({"mode": "same-user" if mode else "separate-users", "exit_code": result.returncode})
            if result.returncode:
                break
        reports = list(directory.glob("*/**/report.json"))
        stop(config)
        state = start(config_path)
        for report in reports:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("load_test.py")), "--url", config.public_url,
                                     "--verify-report", str(report), "--confirm-restarted"], env=environment, check=False)
            results.append({"mode": "post-restart", "report": str(report), "exit_code": result.returncode})
    finally:
        stop(config)
    result = {"config": str(config_path), "stopped": True, "elapsed_seconds": time.monotonic() - started,
              "results": results, "credentials_saved": False}
    (directory / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if results and all(record["exit_code"] == 0 for record in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
