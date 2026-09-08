"""Exercise common roster passwords and direct uploads on two disposable courses."""
import json
from pathlib import Path
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
    folder = ROOT / ".local-tests" / ("submission-browser-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    configs, fixtures, private_files = [], [], []
    try:
        for index in range(2):
            config = Config(instance_id="submit_" + uuid.uuid4().hex[:8], course_name=f"직접 제출 검증 {index + 1}차",
                            port=internal_port(), public_host="127.0.0.1", bind_host="127.0.0.1",
                            storage_root=str(folder / f"store-{index}"), min_free_bytes=0, chunk_bytes=1024 * 1024)
            configs.append(config)
            config_path = folder / f"course-{index}.json"
            config.save(config_path)
            password, common = temporary_password(), temporary_password()
            service = Service(config)
            service.bootstrap_admin("admin", password)
            admin = service.login("admin", password, "local-test")["token"]
            with service.store.connect(write=True) as db:
                db.execute("INSERT INTO assignments(id,title) VALUES (?,?)", (uuid.uuid4().hex, "두 번째 실습 과제"))
            start(config_path)
            if index == 0:
                roster = folder / "private-roster.json"
                private_files.append(roster)
                roster.write_text(json.dumps({"url": config.public_url, "user_id": "admin", "password": password,
                                              "common_password": common, "screenshot": str(folder / "common-password-option.png")}), encoding="utf-8")
                subprocess.run([node, str(ROOT / "tests/test_roster_tsv_browser.cjs"), str(roster)], cwd=ROOT, check=True, timeout=240)
            else:
                service.roster_apply(admin, [{"user_id": "001", "name": "두 번째 차수 학생"}], common_temporary_password=common)
            fixtures.append({"url": config.public_url, "instance_id": config.instance_id, "user_id": "001",
                             "password": common, "new_password": temporary_password(), "resume_test": index == 0,
                             "streamlit_download": True, "assignment_title": "두 번째 실습 과제"})
        fixture = folder / "private-upload.json"
        private_files.append(fixture)
        fixture.write_text(json.dumps(fixtures), encoding="utf-8")
        subprocess.run([node, str(ROOT / "tests/test_browser.cjs"), str(fixture)], cwd=ROOT, check=True, timeout=480)
        print(json.dumps({"passed": True, "courses": 2, "common_password_ui": True, "eight_character_password_change": True,
                          "direct_submission": True, "resume": True, "native_download": True,
                          "selected_assignment": True, "logout_revokes_submission": True, "artifacts": str(folder)}), flush=True)
    finally:
        for config in configs:
            stop(config)
        for file in private_files:
            file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
