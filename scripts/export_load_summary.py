"""Export load evidence without credentials, user IDs, instance secrets, or paths."""
import json
from pathlib import Path
import sys


def main():
    root = Path(sys.argv[1])
    output = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/evidence/load-results.json")
    records = []
    for path in sorted(root.rglob("report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if "measurements" not in report:
            continue
        restart = report.get("post_restart_verification")
        after = {row["file_id"]: row for row in restart.get("files", [])} if isinstance(restart, dict) else {}
        memory = report["measurements"]
        files = []
        for row in sorted(report["files"], key=lambda item: item["worker"]):
            files.append({key: row[key] for key in (
                "worker", "source_bytes", "source_sha256", "stored_bytes", "stored_sha256",
                "download_bytes", "download_sha256", "success", "completion_idempotent") if key in row})
            files[-1]["post_restart"] = {key: value for key, value in after.get(row.get("file_id"), {}).items() if key != "file_id"}
        records.append({
            "mode": "same-user" if report["same_user"] else "separate-users",
            "method": report["path_kind"], "environment": report["environment"],
            "file_bytes": report["file_bytes"], "concurrency": report["requested_concurrency"],
            "observed_transfer_overlap": report["observed_transfer_interval_overlap"],
            "elapsed_seconds": report["elapsed_seconds"], "success_count": report["success_count"],
            "failure_count": report["failure_count"], "files": files,
            "measurement": {key: memory[key] for key in ("baseline_rss_bytes", "peak_tree_rss_bytes", "rss_growth_bytes", "peak_temp_bytes", "sample_interval_seconds", "measurement_errors")},
            "observed_process_names": sorted(set(process["name"] for process in memory["processes"].values())),
            "observed_process_count": len(memory["processes"]),
            "accounts_measured": len(report["quotas_after"]),
            "used_bytes_per_account": [q["used_bytes"] for q in report["quotas_after"]],
            "reserved_bytes_per_account": [q["reserved_bytes"] for q in report["quotas_after"]],
            "unique_submission_count": len({row["submission_number"] for row in report["files"] if row["success"]}),
            "post_restart_all_match": bool(isinstance(restart, dict) and restart.get("all_match")),
        })
    if not records:
        raise SystemExit("No completed load reports found")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"runs": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(records)} nonsecret load evidence records to {output}")


if __name__ == "__main__":
    main()
