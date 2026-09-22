"""Verify operator review in Edge against a disposable local course."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient
from assignmenthub.api import create_app
from assignmenthub.config import Config
from assignmenthub.launcher import internal_port, start, stop
from assignmenthub.service import Service
from test_api import login, student, complete_one


def main():
    folder = ROOT / ".local-tests" / ("operator-browser-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    config = Config(instance_id="review_" + uuid.uuid4().hex[:8], course_name="과제 제출 확인",
                    port=internal_port(), public_host="127.0.0.1", bind_host="127.0.0.1",
                    storage_root=str(folder / "store"), min_free_bytes=0)
    config_path = folder / "course.json"
    config.save(config_path)
    fixture = folder / "fixture.json"
    try:
        password = uuid.uuid4().hex
        service = Service(config)
        service.bootstrap_admin("admin", password)
        with TestClient(create_app(config)) as client:
            admin = login(client, "admin", password)
            one = student(client, admin)
            student(client, admin, "002", "김미제출")
            complete_one(client, one, b"draft", "초안.txt")
            complete_one(client, one, b"final", "실습 결과.txt")
        fixture.write_text(json.dumps({"url": config.public_url, "admin_password": password,
                                       "output": str(folder)}), encoding="utf-8")
        start(config_path)
        subprocess.run([shutil.which("node"), str(ROOT / "tests/test_operator_browser.cjs"), str(fixture)],
                       cwd=ROOT, check=True, timeout=240)
        print(json.dumps({"passed": True, "artifacts": str(folder)}), flush=True)
    finally:
        stop(config)
        fixture.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
