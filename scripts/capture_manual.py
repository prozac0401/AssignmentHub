"""Capture a real, isolated manual walkthrough. Never uses production accounts.

Run with the project venv after setup. Requires Pillow and Playwright/Edge for
capture only. Credentials stay in a private temporary fixture removed on exit.
"""
import ctypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assignmenthub.launcher import internal_port, status, stop
from assignmenthub.server_manager import CourseDialog, ServerManager
from assignmenthub import server_manager


def pump(root, condition, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(0.02)
    raise TimeoutError("Manual walkthrough operation timed out")


def capture(root, window, target):
    from PIL import ImageGrab
    window.lift()
    root.update()
    time.sleep(0.3)
    root.update()
    hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
    ImageGrab.grab(window=hwnd).save(target)


def main():
    if os.name != "nt":
        raise RuntimeError("Native manager captures require Windows")
    work = ROOT / ".local-tests" / ("manual-" + secrets.token_hex(4))
    work.mkdir(parents=True)
    output = ROOT / "docs" / "manual" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    manager = ServerManager(root, work / "instances", work / "data")
    # The walkthrough owns this isolated course and confirms its own stop.
    server_manager.messagebox.askyesno = lambda *args, **kwargs: True
    errors = []
    manager.show_error = lambda error: errors.append(str(error))
    config = None
    fixture = work / "private-browser.json"
    try:
        pump(root, lambda: not manager.jobs)
        capture(root, root, output / "01-manager-empty.png")
        manager.new_button.invoke()
        dialog = next(w for w in root.winfo_children() if isinstance(w, CourseDialog))
        values = dict(course_name="파이썬 기초 실습", instance_id="manual_" + secrets.token_hex(3),
                      public_host="127.0.0.1", port=str(internal_port()),
                      storage_root=str(work / "data" / "python_basic"), admin_id="admin")
        password = secrets.token_urlsafe(24)
        for key, value in values.items():
            dialog.variables[key].set(value)
        dialog.variables["password"].set(password)
        dialog.variables["confirm"].set(password)
        capture(root, dialog, output / "02-create-course.png")
        notebook = next(w for w in dialog.winfo_children() if isinstance(w, ttk.Notebook))
        notebook.select(1)
        capture(root, dialog, output / "03-course-limits.png")
        dialog.save_button.invoke()
        pump(root, lambda: not dialog.winfo_exists() and bool(manager.courses) and not manager.jobs)
        config = manager.selected().config
        manager.buttons["start"].invoke()
        pump(root, lambda: not manager.busy_paths and not manager.jobs, timeout=660)
        assert not errors, errors
        assert status(config)["running"]
        manager.copy_button.invoke()
        assert root.clipboard_get() == config.public_url
        capture(root, root, output / "04-server-running.png")
        root.geometry("640x520")
        root.update()
        manager.viewport.canvas.yview_moveto(1)
        root.update()
        for button in manager.buttons.values():
            assert 0 <= button.winfo_rootx() - root.winfo_rootx()
            assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
            assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        capture(root, root, output / "22-manager-compact.png")
        root.geometry("1100x760")
        root.update()
        manager.viewport.canvas.yview_moveto(0)
        manager.buttons["diagnose"].invoke()
        pump(root, lambda: not manager.jobs and any(isinstance(w, tk.Toplevel) for w in root.winfo_children()))
        diagnostic = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        capture(root, diagnostic, output / "05-diagnostics.png")
        diagnostic.destroy()
        fixture.write_text(json.dumps({"url": config.public_url, "user_id": "admin", "password": password,
                                       "work": str(work), "output": str(output)}), encoding="utf-8")
        with (work / "browser-output.txt").open("w", encoding="utf-8") as log:
            child = subprocess.Popen(["node", str(ROOT / "scripts" / "capture_manual.cjs"), str(fixture)],
                                     cwd=ROOT, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            pump(root, lambda: child.poll() is not None, timeout=600)
        if child.returncode:
            print((work / "browser-output.txt").read_text(encoding="utf-8"))
            raise RuntimeError("Browser walkthrough failed")
        manager.buttons["stop"].invoke()
        pump(root, lambda: not manager.busy_paths and not manager.jobs, timeout=120)
        assert not status(config)["running"]
        capture(root, root, output / "21-server-stopped.png")
        report_path = output.parent / "walkthrough.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report.update(screenshots=len(list(output.glob('*.png'))), server_stopped=True, native_compact_viewport=[640, 520],
                      native_manager="Real Tk window and real widget callbacks; confirmation supplied within isolated test process")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "screenshots": len(list(output.glob('*.png'))),
                          "manager_created_started_copied_diagnosed_stopped": True}, ensure_ascii=False))
    finally:
        fixture.unlink(missing_ok=True)
        if config:
            stop(config)
        manager.executor.shutdown(wait=True, cancel_futures=True)
        root.destroy()


if __name__ == "__main__":
    main()
