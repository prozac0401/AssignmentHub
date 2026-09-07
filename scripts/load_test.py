"""Actual bounded-memory HTTP uploads via the public gateway (not a browser test).

Default: 4 distinct students each upload an actual 2 GiB file concurrently.
Credentials are read interactively or from AH_TEST_ADMIN_ID/AH_TEST_ADMIN_PASSWORD.
Run only on a dedicated test instance. Completed files are deliberately retained;
the script deletes only source files in its uniquely created source directory.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import importlib.metadata
import json
import os
import platform
import secrets
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psutil

GIB = 1024 ** 3
MIB = 1024 ** 2


def call(client, method, path, token=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = "Bearer " + token
    response = client.request(method, path, headers=headers, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{method} {path.split('?')[0]}: HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def login(client, user_id, password):
    return call(client, "POST", "/api/auth/login", json={"user_id": user_id, "password": password})


def create_source(path, size, job):
    digest = hashlib.sha256()
    # Distinct content per worker detects cross-user/file corruption; block remains 1 MiB.
    seed = hashlib.sha256(f"AssignmentHub actual load test worker {job}".encode()).digest()
    block = seed * (MIB // len(seed))
    with path.open("xb") as stream:
        remaining = size
        while remaining:
            part = block[:min(len(block), remaining)]
            stream.write(part)
            digest.update(part)
            remaining -= len(part)
        stream.flush()
        os.fsync(stream.fileno())
    assert path.stat().st_size == size
    return digest.hexdigest()


def tree_bytes(root):
    total = 0
    if root.exists():
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                pass
    return total


class Sampler:
    def __init__(self, server_pid, storage_root):
        self.server_pid = server_pid
        self.root = storage_root
        self.stop_event = threading.Event()
        self.samples = []
        self.identities = {}
        self.errors = set()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def sample(self):
        rss = 0
        try:
            parent = psutil.Process(self.server_pid)
            processes = [parent, *parent.children(recursive=True)]
            for process in processes:
                try:
                    rss += process.memory_info().rss
                    self.identities[str(process.pid)] = {"name": process.name(), "created_at": process.create_time()}
                except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                    self.errors.add(type(exc).__name__)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            self.errors.add(type(exc).__name__)
        # API uses tmp; temp is also checked for a changed configuration/layout.
        temp_bytes = tree_bytes(self.root / "tmp") + tree_bytes(self.root / "temp")
        self.samples.append({"elapsed_seconds": round(time.monotonic() - self.started, 3),
                             "rss_bytes": rss, "temp_bytes": temp_bytes})

    def run(self):
        while not self.stop_event.is_set():
            self.sample()
            self.stop_event.wait(0.25)

    def start(self):
        self.started = time.monotonic()
        self.sample()
        self.baseline = self.samples[-1]["rss_bytes"]
        self.thread.start()

    def finish(self):
        self.stop_event.set()
        self.thread.join()
        self.sample()
        peak = max(sample["rss_bytes"] for sample in self.samples)
        return {"server_parent_pid": self.server_pid, "baseline_rss_bytes": self.baseline,
                "peak_tree_rss_bytes": peak, "rss_growth_bytes": max(0, peak - self.baseline),
                "peak_temp_bytes": max(sample["temp_bytes"] for sample in self.samples),
                "sample_interval_seconds": 0.25, "processes": self.identities,
                "measurement_errors": sorted(self.errors), "samples": self.samples}


def download_hash(client, token, file_id):
    digest = hashlib.sha256()
    size = 0
    with client.stream("GET", f"/api/files/{file_id}/download", headers={"Authorization": "Bearer " + token}) as response:
        response.raise_for_status()
        for block in response.iter_bytes(MIB):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def upload_worker(args, token, source, expected_hash, barrier, index, chunk_bytes):
    start = time.monotonic()
    record = {"worker": index, "source_name": source.name, "source_bytes": source.stat().st_size,
              "source_sha256": expected_hash, "success": False}
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=httpx.Timeout(300, connect=30), trust_env=False) as client:
        upload = None
        try:
            barrier.wait(timeout=120)
            record["request_start_monotonic"] = time.monotonic()
            upload = call(client, "POST", "/api/uploads", token, json={
                "assignment_id": args.assignment_id, "request_id": str(uuid.uuid4()),
                "files": [{"name": source.name, "size": record["source_bytes"], "sha256": expected_hash}]})
            record["upload_id"] = upload["id"]
            file_id = upload["files"][0]["id"]
            record["file_id"] = file_id
            record["transfer_start_monotonic"] = time.monotonic()
            offset = 0
            retries = 0
            next_progress = 128 * MIB
            with source.open("rb") as stream:
                while offset < record["source_bytes"]:
                    stream.seek(offset)
                    block = stream.read(chunk_bytes)
                    response = None
                    try:
                        response = client.patch(f"/api/uploads/{upload['id']}/files/{file_id}?offset={offset}",
                                                headers={"Authorization": "Bearer " + token,
                                                         "X-Chunk-SHA256": hashlib.sha256(block).hexdigest()}, content=block)
                        if response.is_success:
                            offset = response.json()["offset"]
                            retries = 0
                            if offset >= next_progress or offset == record["source_bytes"]:
                                print(json.dumps({"worker": index, "server_received_bytes": offset,
                                                  "file_bytes": record["source_bytes"],
                                                  "state": "verifying" if offset == record["source_bytes"] else "uploading"}), flush=True)
                                next_progress = offset + 128 * MIB
                            continue
                        if response.status_code not in (409, 429, 502, 503, 504):
                            raise RuntimeError(f"chunk: HTTP {response.status_code}: {response.text[:300]}")
                    except (httpx.TimeoutException, httpx.NetworkError):
                        pass
                    retries += 1
                    if retries > args.retries:
                        raise RuntimeError("bounded chunk retry limit exceeded")
                    time.sleep(min(0.2 * 2 ** retries, 5))
                    state = call(client, "GET", f"/api/uploads/{upload['id']}", token)
                    offset = next(file["offset"] for file in state["files"] if file["id"] == file_id)
            record["transfer_end_monotonic"] = time.monotonic()
            completed = call(client, "POST", f"/api/uploads/{upload['id']}/complete", token)
            # Repeated completion must return the same committed submission.
            repeated = call(client, "POST", f"/api/uploads/{upload['id']}/complete", token)
            stored_file = completed["files"][0]
            size, digest = download_hash(client, token, file_id)
            record.update({"status": completed["status"], "submission_number": completed["submission_number"],
                           "stored_bytes": stored_file["size"], "stored_sha256": stored_file["stored_sha256"],
                           "download_bytes": size, "download_sha256": digest,
                           "completed_at": completed["completed_at"],
                           "completion_idempotent": repeated["submission_number"] == completed["submission_number"],
                           "success": completed["status"] == "completed" and size == record["source_bytes"]
                           and digest == expected_hash == stored_file["stored_sha256"]
                           and repeated["submission_number"] == completed["submission_number"]})
        except Exception as exc:
            record["error"] = str(exc)
            if upload:
                try:
                    call(client, "POST", f"/api/uploads/{upload['id']}/cancel", token)
                except Exception:
                    pass  # A completed upload must never be deleted by cleanup.
    record["elapsed_seconds"] = round(time.monotonic() - start, 3)
    print(json.dumps({k: v for k, v in record.items() if k not in ("source_sha256", "stored_sha256", "download_sha256")}, ensure_ascii=False), flush=True)
    return record


def verify_report(args, client, admin_token):
    report = json.loads(args.verify_report.read_text(encoding="utf-8"))
    results = []
    for record in report["files"]:
        if not record.get("success"):
            continue
        result = {"file_id": record["file_id"], "success": False}
        try:
            size, digest = download_hash(client, admin_token, record["file_id"])
            result.update({"bytes": size, "sha256": digest,
                           "success": size == record["source_bytes"] and digest == record["source_sha256"]})
        except Exception as exc:
            result["error"] = str(exc)
        results.append(result)
    report["post_restart_verification"] = {"utc": datetime.now(timezone.utc).isoformat(),
        "operator_claimed_restart": bool(args.confirm_restarted), "files": results,
        "all_match": bool(results) and all(item["success"] for item in results)}
    args.verify_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["post_restart_verification"], ensure_ascii=False, indent=2))
    return 0 if report["post_restart_verification"]["all_match"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Public single-port gateway URL")
    parser.add_argument("--server-pid", type=int, help="Launcher supervisor PID; all descendants are measured")
    parser.add_argument("--storage-root", type=Path, help="Dedicated test instance root on this host")
    parser.add_argument("--users", type=int, default=4, help="Concurrent file jobs; default 4")
    parser.add_argument("--file-bytes", type=int, default=2 * GIB)
    parser.add_argument("--same-user", action="store_true", help="All jobs use one account (API equivalent of concurrent tabs)")
    parser.add_argument("--assignment-id")
    parser.add_argument("--output", type=Path, default=Path("test-runs"))
    parser.add_argument("--retries", type=int, default=12)
    parser.add_argument("--keep-sources", action="store_true")
    parser.add_argument("--verify-report", type=Path, help="Re-download and hash records in an existing report after restart")
    parser.add_argument("--confirm-restarted", action="store_true", help="Record operator confirmation that server was restarted")
    args = parser.parse_args()
    if args.users < 1 or args.file_bytes < 1:
        parser.error("users and file-bytes must be positive")
    if not args.verify_report and (not args.server_pid or not args.storage_root):
        parser.error("actual upload run requires --server-pid and --storage-root for memory/disk measurements")
    admin_id = os.environ.get("AH_TEST_ADMIN_ID") or input("관리자 ID: ")
    admin_password = os.environ.get("AH_TEST_ADMIN_PASSWORD") or getpass.getpass("관리자 비밀번호: ")
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=300, trust_env=False) as client:
        admin = login(client, admin_id, admin_password)
        if admin.get("must_change_password"):
            raise RuntimeError("Change the admin password before running load validation")
        if args.verify_report:
            return verify_report(args, client, admin["token"])
        psutil.Process(args.server_pid)  # Reject invalid measurement PID before creating test users/files.
        info = call(client, "GET", "/api/info")
        limits = call(client, "GET", "/api/quota", admin["token"])
        chunk_bytes = limits["chunk_bytes"]
        if args.file_bytes > limits["max_file_bytes"]:
            raise RuntimeError("Requested file size exceeds instance max_file_bytes")
        quota_need = args.file_bytes * (args.users if args.same_user else 1)
        if quota_need > limits["quota_bytes"]:
            raise RuntimeError("Requested per-user total exceeds instance quota")
        assignments = call(client, "GET", "/api/assignments", admin["token"])
        args.assignment_id = args.assignment_id or next(item["id"] for item in assignments if item["is_open"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        output_dir = args.output.resolve() / stamp
        source_dir = output_dir / "sources"
        source_dir.mkdir(parents=True, exist_ok=False)
        source_need = args.users * args.file_bytes
        if shutil.disk_usage(source_dir).free < source_need + GIB:
            raise RuntimeError("Insufficient disk space for streamed source files plus 1 GiB margin")
        run_id = "load_" + uuid.uuid4().hex[:10]
        count = 1 if args.same_user else args.users
        rows = [{"user_id": f"{run_id}_{i+1}", "name": f"부하검증 {i+1}", "group": run_id} for i in range(count)]
        created = call(client, "POST", "/api/admin/roster/apply", admin["token"],
                       json={"rows": rows, "update_existing": False})["created"]
        tokens = []
        for user in created:
            restricted = login(client, user["user_id"], user["temporary_password"])
            password = secrets.token_urlsafe(24)
            call(client, "POST", "/api/auth/password", restricted["token"], json={
                "current_password": user["temporary_password"], "new_password": password, "confirm_password": password})
            tokens.append(login(client, user["user_id"], password)["token"])
        print(f"Generating {args.users} actual files of {args.file_bytes:,} bytes in {source_dir}", flush=True)
        sources = []
        for index in range(args.users):
            source = source_dir / f"worker-{index + 1}.bin"
            sources.append((source, create_source(source, args.file_bytes, index)))
        sampler = Sampler(args.server_pid, args.storage_root.resolve())
        sampler.start()
        started = time.monotonic()
        barrier = threading.Barrier(args.users)
        results = []
        try:
            with ThreadPoolExecutor(max_workers=args.users) as pool:
                futures = [pool.submit(upload_worker, args, tokens[0 if args.same_user else i],
                                       source, digest, barrier, i + 1, chunk_bytes)
                           for i, (source, digest) in enumerate(sources)]
                results = [future.result() for future in as_completed(futures)]
        finally:
            measurements = sampler.finish()
        elapsed = time.monotonic() - started
        dashboard = call(client, "GET", f"/api/admin/dashboard?assignment_id={args.assignment_id}&group={run_id}", admin["token"])
        quota_results = [call(client, "GET", "/api/quota", token) for token in tokens]
        overlap_events = []
        for item in results:
            if "transfer_start_monotonic" in item and "transfer_end_monotonic" in item:
                overlap_events.extend([(item["transfer_start_monotonic"], 1), (item["transfer_end_monotonic"], -1)])
        active = peak_active = 0
        for _, delta in sorted(overlap_events):
            active += delta
            peak_active = max(peak_active, active)
        total_bytes = args.users * args.file_bytes
        scoped_rows = dashboard["rows"]
        expected_per_user = args.file_bytes * (args.users if args.same_user else 1)
        expected_versions = args.users if args.same_user else 1
        acceptance_checks = {
            "all_file_hashes_and_sizes_match": len(results) == args.users and all(item["success"] for item in results),
            "unique_submissions": len({item.get("submission_number") for item in results if item.get("success")}) == args.users,
            "unique_files": len({item.get("file_id") for item in results if item.get("success")}) == args.users,
            "usage_and_reservations_match": all(q["used_bytes"] == expected_per_user and q["reserved_bytes"] == 0 for q in quota_results),
            "run_submitter_and_version_counts_match": len(scoped_rows) == count and all(row["submitted"] and row["submission_count"] == expected_versions for row in scoped_rows),
            "all_requested_transfer_intervals_overlap": peak_active == args.users,
            "whole_process_tree_measurement_succeeded": not measurements["measurement_errors"] and measurements["baseline_rss_bytes"] > 0,
        }
        report = {"utc": stamp, "path_kind": "real HTTP through public gateway; browser not exercised",
                  "browser_verified": False, "server_url": args.url, "instance_info": info,
                  "environment": {"os": platform.platform(), "python": sys.version,
                                  "httpx": httpx.__version__, "psutil": psutil.__version__,
                                  "installed_versions": {name: importlib.metadata.version(name) for name in ("fastapi", "starlette", "uvicorn", "streamlit", "argon2-cffi", "openpyxl")},
                                  "logical_cpus": psutil.cpu_count(), "physical_memory_bytes": psutil.virtual_memory().total},
                  "requested_concurrency": args.users, "observed_transfer_interval_overlap": peak_active,
                  "same_user": args.same_user, "test_user_ids": [row["user_id"] for row in rows],
                  "file_bytes": args.file_bytes, "total_source_bytes": total_bytes,
                  "elapsed_seconds": round(elapsed, 3), "success_count": sum(item["success"] for item in results),
                  "failure_count": sum(not item["success"] for item in results), "files": results,
                  "measurements": measurements, "rss_growth_divided_by_total_source_bytes": measurements["rss_growth_bytes"] / total_bytes,
                  "memory_interpretation": "Sampled RSS is environment-specific; compare baseline/peak and repeated different file sizes. It is not a maximum capacity guarantee.",
                  "quotas_after": quota_results, "target_count": dashboard["target_count"],
                  "submitted_count": dashboard["submitted_count"],
                  "run_submitter_count": sum(row["submitted"] for row in scoped_rows),
                  "acceptance_checks": acceptance_checks,
                  "post_restart_verification": "미검증: restart then run --verify-report with --confirm-restarted"}
        report_path = output_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.keep_sources:
            # Delete only exact source files made in this unique run; no recursive deletion.
            for source, _ in sources:
                source.unlink()
            source_dir.rmdir()
        print(f"Report: {report_path}\nSuccess: {report['success_count']}; failure: {report['failure_count']}")
        print("Completed server files and test accounts are retained for restart verification. Remove a dedicated test instance only after stopping it.")
        return 0 if all(acceptance_checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
