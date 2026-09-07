"""Real Tk widgets and real processes; widget invoke is not an OS mouse-input test."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import tkinter as tk

import httpx
import pytest

from assignmenthub.config import Config
from assignmenthub.launcher import PROJECT_ROOT, internal_port, status, stop
from assignmenthub.server_manager import CourseDialog, ServerManager


def pump(root, condition, timeout=20):
    deadline = time.monotonic() + timeout
    ticks = 0
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return ticks
        ticks += 1
        time.sleep(0.02)
    raise AssertionError("Tk operation timed out")


@pytest.mark.integration
def test_real_manager_create_start_browser_settings_and_stop(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    data_root = PROJECT_ROOT / ".local-tests" / "manager-data" / secrets.token_hex(5)
    manager = ServerManager(root, tmp_path / "instances", data_root)
    errors = []
    manager.show_error = lambda error: errors.append(str(error))
    monkeypatch.setattr("assignmenthub.server_manager.messagebox.askyesno", lambda *args, **kwargs: True)
    password = secrets.token_urlsafe(24)
    config = None
    try:
        pump(root, lambda: not manager.jobs)
        manager.new_button.invoke()
        dialog = next(child for child in root.winfo_children() if isinstance(child, CourseDialog))
        dialog.variables["course_name"].set("사용성 검증 과정")
        dialog.variables["public_host"].set("127.0.0.1")
        dialog.variables["port"].set(str(internal_port()))
        # Avoid cross-test and normal course ID lock collisions.
        dialog.variables["instance_id"].set("manager_" + secrets.token_hex(5))
        dialog.variables["storage_root"].set(str(data_root / "course"))
        dialog.variables["password"].set(password)
        dialog.variables["confirm"].set("different")
        dialog.save_button.invoke()
        assert "확인" in dialog.error.get()
        assert not list((tmp_path / "instances").glob("*.json"))
        dialog.variables["confirm"].set(password)
        dialog.save_button.invoke()
        pump(root, lambda: not dialog.winfo_exists() and bool(manager.courses) and not manager.jobs)
        course = manager.selected()
        config = course.config
        assert config.course_name == "사용성 검증 과정"
        assert manager.buttons["start"].instate(["!disabled"])
        manager.copy_button.invoke()
        assert root.clipboard_get() == config.public_url
        manager.buttons["start"].invoke()
        assert manager.buttons["start"].instate(["disabled"])
        ticks = pump(root, lambda: not manager.busy_paths and not manager.jobs, timeout=660)
        assert ticks >= 2, "Tk must keep servicing events while server starts"
        assert not errors, errors
        assert status(config)["running"]
        assert manager.buttons["settings"].instate(["disabled"])
        with httpx.Client(trust_env=False, timeout=10) as client:
            assert client.get(config.public_url + "/api/health").json()["instance_id"] == config.instance_id
        from assignmenthub.management import diagnose
        findings = diagnose(config)
        assert any(name == "업로드 API" and result == "정상" for name, result, _ in findings)
        assert any(name == "관리 화면" and result == "정상" for name, result, _ in findings)
        evidence = PROJECT_ROOT / ".local-tests" / "manager-usability"
        evidence.mkdir(parents=True, exist_ok=True)
        fixture = tmp_path / "browser.json"
        fixture.write_text(json.dumps({"url": config.public_url, "user_id": "admin", "password": password,
                                       "screenshot": str(evidence / "admin-home.png")}), encoding="utf-8")
        browser = subprocess.Popen(["node", "tests/test_admin_home.cjs", str(fixture)], cwd=PROJECT_ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        pump(root, lambda: browser.poll() is not None, timeout=180)
        output, error = browser.communicate(timeout=5)
        assert browser.returncode == 0, error
        report = json.loads(output.strip().splitlines()[-1])
        # Capture only this newly created test window, never the whole desktop.
        from PIL import ImageGrab
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) if os.name == "nt" else None
        if hwnd:
            ImageGrab.grab(window=hwnd).save(evidence / "server-manager.png")
        pump(root, lambda: not manager.jobs)
        manager.close()
        assert status(config)["running"], "Closing the manager must not stop its server"
        root = tk.Tk()
        manager = ServerManager(root, tmp_path / "instances", data_root)
        manager.show_error = lambda error: errors.append(str(error))
        pump(root, lambda: not manager.jobs and bool(manager.courses))
        assert manager.selected().config.instance_id == config.instance_id
        assert manager.buttons["stop"].instate(["!disabled"])
        manager.buttons["stop"].invoke()
        pump(root, lambda: not manager.busy_paths and not manager.jobs, timeout=70)
        assert not status(config)["running"] and not errors
        manager.buttons["settings"].invoke()
        edit = next(child for child in root.winfo_children() if isinstance(child, CourseDialog))
        edit.variables["course_name"].set("사용성 검증 다음 수업")
        edit.variables["max_file_bytes"].set("0.5")
        edit.save_button.invoke()
        pump(root, lambda: not edit.winfo_exists() and not manager.jobs)
        changed = Config.load(course.path)
        assert changed.max_file_bytes == 536870912
        assert changed.root == config.root and changed.instance_id == config.instance_id
        report.update(real_tk_widgets=True,create_validation=True,clipboard_copy=True,real_start_stop=True,
                      event_loop_ticks_during_start=ticks,settings_after_stop=True,
                      close_and_reopen_keeps_server=True,os_mouse_input_verified=False)
        (evidence / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        if config:
            stop(config)
        manager.executor.shutdown(wait=True, cancel_futures=True)
        root.destroy()
