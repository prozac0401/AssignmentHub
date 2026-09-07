"""Actual Windows BAT -> pythonw launch, scoped to this test's unique arguments."""
import ctypes
from ctypes import wintypes
import os
import subprocess
import time

import psutil
import pytest

from assignmenthub.launcher import PROJECT_ROOT, process_record, terminate_owned


@pytest.mark.skipif(os.name != "nt", reason="Windows BAT entry point")
@pytest.mark.integration
def test_start_bat_opens_owned_pythonw_manager(tmp_path):
    catalog = str(tmp_path / "instances")
    launched = []
    since = time.time()
    def discover():
        records = []
        for process in psutil.process_iter(["create_time", "cmdline"]):
            try:
                command = process.info["cmdline"] or []
                if process.info["create_time"] >= since - 1 and "assignmenthub.server_manager" in command and catalog in command:
                    records.append(process_record(process))
            except psutil.Error:
                pass
        return records
    try:
        log = tmp_path / "entrypoint.log"
        with log.open("wb") as output:
            process = subprocess.Popen([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "start.bat",
                                        "--instances-dir", catalog, "--data-dir", str(tmp_path / "data")],
                                       cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output)
            try:
                code = process.wait(timeout=30)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        assert code == 0, log.read_text(encoding="utf-8", errors="replace")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            launched = discover()
            if launched:
                break
            time.sleep(0.1)
        assert launched, "BAT did not launch the manager"
        assert any("pythonw" in record["command"][0].lower() for record in launched)
        titles = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        @callback_type
        def inspect(hwnd, _):
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                text = ctypes.create_unicode_buffer(300)
                ctypes.windll.user32.GetWindowTextW(hwnd, text, len(text))
                if text.value:
                    titles.append(text.value)
            return True
        while time.monotonic() < deadline:
            launched = discover()
            pids = {record["pid"] for record in launched}
            titles.clear()
            ctypes.windll.user32.EnumWindows(inspect, 0)
            if "AssignmentHub · 서버 관리" in titles:
                break
            time.sleep(0.1)
        assert "AssignmentHub · 서버 관리" in titles
    finally:
        terminate_owned(discover(), timeout=2)
