"""Offline diagnostics for the self-contained Windows distribution."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_files(root: Path) -> int:
    manifest = json.loads((root / "distribution.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    if not files:
        raise ValueError("배포 파일 목록이 비어 있습니다.")
    canonical = root.resolve()
    def verify(item):
        name, expected = item
        path = (root / name).resolve()
        if not path.is_relative_to(canonical) or not path.is_file() or sha256(path) != expected:
            raise ValueError(f"배포 파일이 없거나 변경되었습니다: {name}")
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(verify, files.items()))
    return len(files)


def check(gui: bool = False, verify: bool = False, quick: bool = False) -> dict:
    runtime = ROOT / "runtime" / "python"
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise ValueError("Windows x64 / 동봉된 Python 3.12가 필요합니다.")
    if Path(sys.executable).resolve().parent != runtime.resolve() or not sys.flags.isolated or not sys.flags.no_site:
        raise ValueError("동봉된 Python으로 start.bat 또는 setup.bat를 실행하세요.")
    if any(not Path(p).resolve().is_relative_to(ROOT) for p in sys.path):
        raise ValueError("배포 폴더 외부의 Python 검색 경로가 있습니다.")
    modules = ("tkinter", "fastapi", "psutil", "assignmenthub.server_manager") if quick else (
        "ssl", "sqlite3", "tkinter", "fastapi", "uvicorn", "streamlit", "argon2",
        "openpyxl", "httpx", "portalocker", "psutil", "multipart", "tzdata",
        "numpy", "pandas", "pyarrow", "assignmenthub.server_manager")
    for module in modules:
        importlib.import_module(module)
    if not quick:
        import sqlite3
        from argon2 import PasswordHasher
        from zoneinfo import ZoneInfo
        with sqlite3.connect(":memory:") as database:
            assert database.execute("select 1").fetchone() == (1,)
        hasher = PasswordHasher()
        assert hasher.verify(hasher.hash("portable-self-test"), "portable-self-test")
        ZoneInfo("Asia/Seoul")
    if gui:
        import tkinter
        os.environ["TCL_LIBRARY"] = str(runtime / "tcl" / "tcl8.6")
        os.environ["TK_LIBRARY"] = str(runtime / "tcl" / "tk8.6")
        window = tkinter.Tk()
        window.withdraw()
        window.update_idletasks()
        window.destroy()
    from assignmenthub.launcher import proxy_binary
    binary = proxy_binary()
    proxy = json.loads((ROOT / "tools" / "caddy-install.json").read_text(encoding="utf-8"))
    if sha256(binary) != proxy["binary_sha256"]:
        raise ValueError("Caddy 파일이 손상되었습니다.")
    result = subprocess.run([str(binary), "version"], capture_output=True, text=True,
                            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not result.stdout.startswith("v" + proxy["version"] + " "):
        raise ValueError("동봉된 Caddy 실행에 실패했습니다.")
    count = verify_files(ROOT) if verify else None
    return {"ok": True, "python": sys.version.split()[0], "executable": str(Path(sys.executable).resolve()),
            "isolated": bool(sys.flags.isolated), "gui": gui, "caddy": proxy["version"], "verified_files": count}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check"])
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--quick", action="store_true", help="Only check manager startup dependencies")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.gui, args.verify, args.quick), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"배포 환경 점검 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
