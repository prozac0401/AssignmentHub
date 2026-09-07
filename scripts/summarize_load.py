"""Print nonsecret summary fields from actual load reports."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else "test-runs")
for path in sorted(root.rglob("report.json")):
    report = json.loads(path.read_text(encoding="utf-8"))
    if "measurements" not in report:
        continue
    memory = report["measurements"]
    restart = report["post_restart_verification"]
    print(json.dumps({
        "path": str(path), "utc": report["utc"], "environment": report["environment"],
        "file_bytes": report["file_bytes"], "requested_concurrency": report["requested_concurrency"],
        "overlap": report["observed_transfer_interval_overlap"], "same_user": report["same_user"],
        "elapsed_seconds": report["elapsed_seconds"], "success_count": report["success_count"],
        "failure_count": report["failure_count"], "baseline_rss_bytes": memory["baseline_rss_bytes"],
        "peak_tree_rss_bytes": memory["peak_tree_rss_bytes"], "rss_growth_bytes": memory["rss_growth_bytes"],
        "peak_temp_bytes": memory["peak_temp_bytes"], "measurement_errors": memory["measurement_errors"],
        "acceptance_checks": report.get("acceptance_checks"),
        "target_count": report["target_count"], "submitted_count": report["submitted_count"],
        "quota_used_bytes": [q["used_bytes"] for q in report["quotas_after"]],
        "quota_reserved_bytes": [q["reserved_bytes"] for q in report["quotas_after"]],
        "independent_unique_submission_count": len({f["submission_number"] for f in report["files"] if f["success"]}),
        "independent_hash_match_count": sum(f.get("source_sha256") == f.get("stored_sha256") == f.get("download_sha256") for f in report["files"]),
        "post_restart_all_match": restart.get("all_match") if isinstance(restart, dict) else False,
    }, ensure_ascii=False))
