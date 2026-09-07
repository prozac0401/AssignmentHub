"""Check that resizing preserves access to real controls, including keyboard focus."""
import tkinter as tk
import time

import pytest

from assignmenthub.server_manager import CourseDialog, ServerManager


def settle(root):
    for _ in range(10):
        root.update()
        time.sleep(.02)


def test_small_manager_and_dialog_keep_controls_accessible(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    manager = ServerManager(root, tmp_path / "instances", tmp_path / "data")
    try:
        settle(root)
        for width, height in ((640, 520), (1100, 760), (800, 600), (1100, 760)):
            root.geometry(f"{width}x{height}")
            settle(root)
            view = manager.viewport
            assert view.content.winfo_height() == max(view.content.winfo_reqheight(), view.canvas.winfo_height())
            view.canvas.yview_moveto(1)
            settle(root)
            for button in manager.buttons.values():
                assert button.winfo_rootx() >= root.winfo_rootx()
                assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + width
                assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + height
        dialog = CourseDialog(manager)
        dialog.geometry("540x480")
        settle(root)
        # Tab focus must reveal the last input even when the form needs scrolling.
        dialog.confirm.focus_force()
        settle(root)
        canvas = dialog.views[0].canvas
        assert dialog.confirm.winfo_rooty() >= canvas.winfo_rooty()
        assert dialog.confirm.winfo_rooty() + dialog.confirm.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()
        assert dialog.save_button.winfo_rooty() + dialog.save_button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
        dialog.destroy()
        manager.text_window("접속 점검", "진단 결과\n" * 100)
        diagnostic = next(child for child in root.winfo_children() if isinstance(child, tk.Toplevel))
        diagnostic.geometry("440x320")
        settle(root)
        close_button = next(child for child in diagnostic.winfo_children() if child.winfo_class() == "TButton")
        assert close_button.winfo_ismapped()
        assert close_button.winfo_rooty() + close_button.winfo_height() <= diagnostic.winfo_rooty() + diagnostic.winfo_height()
        close_button.invoke()
    finally:
        manager.executor.shutdown(wait=True, cancel_futures=True)
        root.destroy()
