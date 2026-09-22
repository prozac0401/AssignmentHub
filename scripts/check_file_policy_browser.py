"""Run the file policy / video acceptance browser flow on a disposable course."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from assignmenthub.config import Config
from assignmenthub.launcher import internal_port, start, stop
from assignmenthub.passwords import temporary_password
from assignmenthub.service import Service
from media_samples import make_video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args()
    if not args.node:
        parser.error("Use --node with the Node.js executable path.")
    folder = ROOT / ".local-tests" / ("file-policy-browser-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    config = Config(instance_id="policy_" + uuid.uuid4().hex[:8], course_name="파일 종류·영상 검사",
                    port=internal_port(), public_host="127.0.0.1", bind_host="127.0.0.1",
                    storage_root=str(folder / "store"), min_free_bytes=0, chunk_bytes=1024 * 1024)
    config_path = folder / "course.json"
    config.save(config_path)
    fixture = folder / "private-fixture.json"
    try:
        admin_password, student_password = temporary_password(), temporary_password()
        service = Service(config)
        service.bootstrap_admin("admin", admin_password)
        admin = service.login("admin", admin_password, "local-test")["token"]
        created = service.roster_apply(admin, [{"user_id": "001", "name": "영상 제출 학생"}])["created"][0]
        temporary = created["temporary_password"]
        token = service.login("001", temporary, "local-test")["token"]
        service.change_password(token, temporary, student_password, student_password)
        make_video(folder / "normal.mp4", seconds=6)
        make_video(folder / "silent.mp4", audio="none")
        fixture.write_text(json.dumps({"url": config.public_url, "admin_password": admin_password,
                                      "student_password": student_password, "output": str(folder)}), encoding="utf-8")
        start(config_path)
        subprocess.run([args.node, str(ROOT / "tests/test_file_policy_browser.cjs"), str(fixture)],
                       cwd=ROOT, check=True, timeout=300)
        print(json.dumps({"passed": True, "artifacts": str(folder)}), flush=True)
    finally:
        stop(config)
        fixture.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
