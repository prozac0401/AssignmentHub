"""Check uploader initialization and failures on one disposable local course."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assignmenthub.config import Config
from assignmenthub.launcher import internal_port, start, stop
from assignmenthub.passwords import temporary_password
from assignmenthub.service import Service


def main():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("The browser check needs Node.js and Playwright/Edge.")
    folder = ROOT / ".local-tests" / ("upload-boot-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    config = Config(instance_id="boot_" + uuid.uuid4().hex[:8], course_name="파일 제출 화면 확인",
                    port=internal_port(), public_host="127.0.0.1", bind_host="127.0.0.1",
                    storage_root=str(folder / "data"), min_free_bytes=0)
    config_path, fixture = folder / "course.json", folder / "private.json"
    try:
        config.save(config_path)
        service = Service(config)
        admin_password = temporary_password()
        service.bootstrap_admin("admin", admin_password)
        admin = service.login("admin", admin_password, "local-test")["token"]
        initial = service.roster_apply(admin, [{"user_id": "001", "name": "화면 확인 학생"}])["created"][0]["temporary_password"]
        student = service.login("001", initial, "local-test")["token"]
        password = temporary_password()
        service.change_password(student, initial, password, password)
        start(config_path)
        fixture.write_text(json.dumps({"url": config.public_url, "user_id": "001", "password": password,
                                       "output": str(folder)}), encoding="utf-8")
        subprocess.run([node, str(ROOT / "tests/test_upload_boot_browser.cjs"), str(fixture)],
                       cwd=ROOT, check=True, timeout=240)
        print(json.dumps({"artifacts": str(folder)}), flush=True)
    finally:
        stop(config)
        fixture.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
