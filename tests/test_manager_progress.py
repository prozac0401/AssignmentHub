"""Exercise real Tk animation in separate processes, as the desktop app runs."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import threading
import time
import tkinter as tk

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assignmenthub.server_manager import ServerManager


def pump(root, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("Tk operation timed out")


@contextmanager
def manager_ui(tmp_path, monkeypatch):
    root = tk.Tk()
    monkeypatch.setattr("assignmenthub.server_manager.network_addresses", lambda: [])
    manager = ServerManager(root, tmp_path / "instances", tmp_path / "data")
    gates = []

    def blocked_task(result=None):
        gate = threading.Event()
        gates.append(gate)

        def task():
            if not gate.wait(10):
                raise TimeoutError("Test worker was not released")
            return result

        return gate, task

    try:
        pump(root, lambda: not manager.jobs)
        yield manager, blocked_task
    finally:
        for gate in gates:
            gate.set()
        manager.executor.shutdown(wait=True, cancel_futures=True)
        manager.progress.stop()
        for timer in root.tk.splitlist(root.tk.call("after", "info")):
            # Some timers belong to Tcl itself, not Python callbacks.
            root.tk.call("after", "cancel", timer)
        root.destroy()


def check_automatic_refresh(manager_ui, monkeypatch):
    manager, blocked_task = manager_ui
    manager.submit("서버 시작", lambda: None)
    pump(manager.root, lambda: not manager.jobs)
    completed = manager.notice.get()
    assert "완료했습니다" in completed
    assert not manager.progress.winfo_manager()

    release, read = blocked_task([])
    monkeypatch.setattr(manager.catalog, "courses", read)
    manager.auto_refresh()
    later = time.monotonic() + 0.25
    pump(manager.root, lambda: time.monotonic() >= later)
    assert manager.refreshing
    assert not manager.progress.winfo_manager()
    assert manager.progress["value"] == 0
    assert manager.notice.get() == completed
    release.set()
    pump(manager.root, lambda: not manager.jobs)
    assert manager.notice.get() == completed

    # An explicit refresh still provides immediate feedback to the operator.
    release, read = blocked_task([])
    monkeypatch.setattr(manager.catalog, "courses", read)
    manager.toolbar_buttons[2].invoke()
    pump(manager.root, lambda: manager.progress["value"] > 2)
    assert manager.progress.winfo_ismapped()
    assert manager.notice.get().startswith("상태 확인…")
    release.set()
    pump(manager.root, lambda: not manager.jobs)
    assert not manager.progress.winfo_manager()
    assert manager.progress["value"] == 0
    assert "초 경과" not in manager.notice.get()


def check_overlapping_operations(manager_ui, monkeypatch, first_finished):
    manager, blocked_task = manager_ui
    release_refresh, read = blocked_task([])
    monkeypatch.setattr(manager.catalog, "courses", read)
    manager.auto_refresh()
    operations = [blocked_task(), blocked_task()]
    labels = ["1차수 서버 시작", "2차수 서버 중지"]
    manager.submit(labels[0], operations[0][1])
    pump(manager.root, lambda: manager.progress["value"] >= 3)
    before = manager.progress["value"]
    manager.submit(labels[1], operations[1][1])
    assert manager.progress["value"] == before, "Adding work must not restart the animation"
    assert labels[0] in manager.notice.get()
    assert "총 2개 작업" in manager.notice.get()
    operations[first_finished][0].set()
    pump(manager.root, lambda: len(manager.jobs) == 2)
    assert manager.progress.winfo_ismapped()
    assert manager.progress["value"] >= before
    assert labels[1 - first_finished] in manager.notice.get()
    assert "총 2개 작업" not in manager.notice.get()

    operations[1 - first_finished][0].set()
    pump(manager.root, lambda: len(manager.jobs) == 1)
    assert manager.refreshing, "The background refresh is deliberately still pending"
    assert not manager.progress.winfo_manager()
    assert manager.progress["value"] == 0
    completed = manager.notice.get()
    assert completed == f"{labels[1 - first_finished]}: 완료했습니다."
    release_refresh.set()
    pump(manager.root, lambda: not manager.jobs)
    assert manager.notice.get() == completed


def check_failed_operation(manager_ui, monkeypatch):
    manager, blocked_task = manager_ui
    seen = []

    def error_dialog(title, text, **kwargs):
        # A message box runs a nested Tk loop. Completed work must already be idle.
        later = time.monotonic() + 0.15
        pump(manager.root, lambda: time.monotonic() >= later)
        seen.append((text, manager.progress.winfo_manager(), manager.progress["value"]))

    monkeypatch.setattr("assignmenthub.server_manager.messagebox.showerror", error_dialog)
    release, wait = blocked_task()

    def failed_start():
        wait()
        raise RuntimeError("검증용 서버 시작 실패")

    manager.submit("서버 시작", failed_start)
    pump(manager.root, lambda: manager.progress["value"] >= 3)
    release.set()
    pump(manager.root, lambda: bool(seen))
    assert seen == [("검증용 서버 시작 실패", "", 0)]
    assert manager.notice.get() == "검증용 서버 시작 실패"

    release, retry = blocked_task()
    manager.submit("서버 다시 시작", retry)
    assert manager.progress["value"] <= 1
    pump(manager.root, lambda: manager.progress["value"] >= 3)
    release.set()
    pump(manager.root, lambda: not manager.jobs)
    assert manager.progress["value"] == 0
    assert not manager.progress.winfo_manager()
    assert manager.notice.get() == "서버 다시 시작: 완료했습니다."


@pytest.mark.parametrize("scenario", ["refresh", "finish_first", "finish_second", "failure"])
def test_manager_progress(tmp_path, scenario):
    # Destroying several Tcl interpreters alongside worker threads can collect Tk
    # objects on a worker. Each scenario owns one app process and one interpreter.
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), scenario, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "progress scenario passed" in result.stdout


if __name__ == "__main__":
    with pytest.MonkeyPatch.context() as patch:
        try:
            with manager_ui(Path(sys.argv[2]), patch) as context:
                scenario = sys.argv[1]
                if scenario == "refresh":
                    check_automatic_refresh(context, patch)
                elif scenario in ("finish_first", "finish_second"):
                    check_overlapping_operations(context, patch, 0 if scenario == "finish_first" else 1)
                elif scenario == "failure":
                    check_failed_operation(context, patch)
                else:
                    raise ValueError(scenario)
        except tk.TclError as exc:
            if sys.platform != "win32" and "display" in str(exc).lower():
                print(f"Tk display unavailable: {exc}")
                raise SystemExit(77)
            raise
    print("progress scenario passed")
