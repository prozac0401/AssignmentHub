"""Check the TSV UI against a disposable local server (Node, Playwright and Edge required)."""
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assignmenthub.config import Config
from assignmenthub.launcher import internal_port, start, stop
from assignmenthub.service import Service


def main():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for the browser check.")
    folder = ROOT / ".local-tests" / ("tsv-browser-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    config = Config(instance_id="tsv_" + uuid.uuid4().hex[:10], course_name="TSV 명단 검증",
                    port=internal_port(), public_host="127.0.0.1", bind_host="127.0.0.1",
                    storage_root=str(folder / "store"), min_free_bytes=0)
    config_path = folder / "instance.json"
    config.save(config_path)
    password = secrets.token_urlsafe(24)
    Service(config).bootstrap_admin("admin", password)
    fixture = folder / "fixture.json"
    fixture.write_text(json.dumps({"url": config.public_url, "user_id": "admin", "password": password,
                                   "screenshot": str(folder / "tsv-preview.png")}, ensure_ascii=False), encoding="utf-8")
    try:
        start(config_path)
        result = subprocess.run([node, str(ROOT / "tests/test_roster_tsv_browser.cjs"), str(fixture)],
                                cwd=ROOT, timeout=180)
        print("Browser evidence:", folder, flush=True)
        return result.returncode
    finally:
        stop(config)


if __name__ == "__main__":
    raise SystemExit(main())
