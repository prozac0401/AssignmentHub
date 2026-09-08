"""Build a Windows x64 ZIP containing CPython, Tcl/Tk, locked wheels and Caddy.

Only the build PC needs Python/pip and (without a wheelhouse) internet access.
The base interpreter is copied without its installed third-party packages.
No installer, registry change, venv redirector or absolute Python path is shipped.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tomllib
import uuid
import zipfile

import install_proxy

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_python(home: Path, target: Path) -> None:
    required = ("python.exe", "pythonw.exe", "python3.dll", "python312.dll",
                "vcruntime140.dll", "vcruntime140_1.dll", "Lib/encodings", "Lib/tkinter",
                "DLLs/_tkinter.pyd", "tcl/tcl8.6/init.tcl", "tcl/tk8.6/tk.tcl")
    for name in required:
        if not (home / name).exists():
            raise RuntimeError(f"Build Python must include x64 CPython 3.12 and Tcl/Tk: missing {home / name}")
    licenses = list(home.glob("LICENSE*"))
    if not licenses:
        raise RuntimeError("Python license file is missing from the build interpreter.")
    target.mkdir(parents=True)
    for name in ("python.exe", "pythonw.exe"):
        shutil.copy2(home / name, target / name)
    for path in [*home.glob("*.dll"), *licenses]:
        shutil.copy2(path, target / path.name)
    for name in ("DLLs", "tcl"):
        shutil.copytree(home / name, target / name, ignore=IGNORE)
    # Never inherit development dependencies or machine-specific .pth files.
    shutil.copytree(home / "Lib", target / "Lib",
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pyc", "*.pyo"))
    (target / "python312._pth").write_text(
        "# All paths are relative to this runtime; ignore registry and PYTHONPATH.\n"
        ".\nDLLs\nLib\nLib\\site-packages\n..\\..\n"
        "Lib\\site-packages\\win32\nLib\\site-packages\\win32\\lib\n", encoding="utf-8")


def copy_app(target: Path) -> None:
    for name in ("assignmenthub", "docs", "examples", "templates"):
        shutil.copytree(ROOT / name, target / name, ignore=IGNORE)
    for name in ("start.bat", "setup.bat", "manage.bat", "README.md", "requirements.in",
                 "requirements.lock", "pyproject.toml"):
        shutil.copy2(ROOT / name, target / name)
    (target / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "portable_env.bat", target / "scripts" / "portable_env.bat")
    # BAT files must work with cmd.exe regardless of the checkout's line endings.
    for path in target.rglob("*.bat"):
        content = path.read_text(encoding="utf-8")
        path.write_bytes(content.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def build(args) -> Path:
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise RuntimeError("Run the builder with 64-bit Python 3.12 on Windows.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    name = f"AssignmentHub-{version}-windows-x64"
    output = args.output_dir.resolve()
    destination, archive = output / name, output / (name + ".zip")
    if destination.exists() or archive.exists():
        raise RuntimeError(f"Output already exists; use --output-dir with a new folder: {output}")
    workspace = ROOT / "build" / ("portable-" + uuid.uuid4().hex)
    target = workspace / name
    target.mkdir(parents=True)
    try:
        runtime = target / "runtime" / "python"
        print("Copying standalone Python and Tcl/Tk...", flush=True)
        copy_python(args.python_home.resolve(), runtime)
        copy_app(target)
        print("Installing hash-locked wheels into the distribution...", flush=True)
        command = [sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
                   "--quiet", "--require-hashes", "--only-binary=:all:", "--no-compile", "--ignore-installed",
                   "--target", str(runtime / "Lib" / "site-packages"), "-r", str(ROOT / "requirements.lock")]
        if args.wheelhouse:
            command.extend(["--no-index", "--find-links", str(args.wheelhouse.resolve())])
        subprocess.run(command, check=True, cwd=ROOT)
        # pip's generated console wrappers contain the build interpreter's
        # absolute path. Entry points use python -m, so do not ship these.
        generated_scripts = runtime / "Lib" / "site-packages" / "bin"
        if generated_scripts.exists():
            if generated_scripts.resolve().parent != (runtime / "Lib" / "site-packages").resolve():
                raise RuntimeError("Unexpected generated script directory")
            shutil.rmtree(generated_scripts)
        # pywin32 normally loads these via an executable .pth file. Keep site
        # disabled (including user .pth files) and put its DLLs next to Python.
        for dll in (runtime / "Lib" / "site-packages" / "pywin32_system32").glob("*.dll"):
            shutil.copy2(dll, runtime / dll.name)
        install_proxy.install()
        (target / "tools").mkdir()
        for filename in ("caddy.exe", "caddy-install.json"):
            shutil.copy2(ROOT / "tools" / filename, target / "tools" / filename)
        license_path = ROOT / "build" / f"Caddy-{install_proxy.VERSION}-LICENSE.txt"
        if not license_path.exists():
            install_proxy.download(f"https://raw.githubusercontent.com/caddyserver/caddy/v{install_proxy.VERSION}/LICENSE", license_path)
        shutil.copy2(license_path, target / "tools" / "Caddy-LICENSE.txt")
        # Test native extension loading with no system Python or network discovery.
        environment = os.environ.copy()
        environment.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                           PYTHONHOME=str(workspace / "nonexistent-python"),
                           PYTHONPATH=str(workspace / "nonexistent-packages"),
                           PYTHONUSERBASE=str(workspace / "nonexistent-user-site"))
        result = subprocess.run([str(runtime / "python.exe"), "-X", "utf8", "-B", "-m",
                                 "assignmenthub.portable", "check", "--gui"],
                                cwd=workspace, env=environment, capture_output=True, text=True,
                                encoding="utf-8", timeout=90)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        check = json.loads(result.stdout)
        packages = {}
        for metadata in (runtime / "Lib" / "site-packages").glob("*.dist-info/METADATA"):
            from email.parser import Parser
            info = Parser().parsestr(metadata.read_text(encoding="utf-8"))
            packages[info["Name"]] = info["Version"]
        print("Recording distribution hashes...", flush=True)
        paths = [p for p in sorted(target.rglob("*"))
                 if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
        with ThreadPoolExecutor(max_workers=8) as executor:
            files = dict(zip((p.relative_to(target).as_posix() for p in paths), executor.map(digest, paths)))
        manifest = {"application": "AssignmentHub", "version": version, "platform": "windows-x64",
                    "built_at": datetime.now(timezone.utc).isoformat(), "python": check["python"],
                    "caddy": check["caddy"], "requirements_sha256": digest(ROOT / "requirements.lock"),
                    "packages": packages, "files": files}
        (target / "distribution.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        output.mkdir(parents=True, exist_ok=True)
        # Publish only after successful native/Tk validation; never overwrite an existing release.
        temporary_zip = workspace / (name + ".zip")
        print("Creating distribution ZIP...", flush=True)
        with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(target.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    bundle.write(path, path.relative_to(workspace).as_posix())
        shutil.move(str(target), str(destination))
        shutil.move(str(temporary_zip), str(archive))
        archive.with_suffix(".zip.sha256").write_text(f"{digest(archive)}  {archive.name}\n", encoding="ascii")
        print(f"Ready: {archive}\nExtract the complete ZIP and double-click start.bat.", flush=True)
        return archive
    finally:
        # This UUID directory was created above, and is never an operator's data directory.
        resolved = workspace.resolve()
        if resolved.parent == (ROOT / "build").resolve() and resolved.name.startswith("portable-"):
            shutil.rmtree(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-home", type=Path, default=Path(sys.base_prefix),
                        help="Complete CPython 3.12 x64 base directory with Tcl/Tk (not a venv)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--wheelhouse", type=Path, help="Install only from these hash-verified local wheels")
    args = parser.parse_args()
    try:
        build(args)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Portable build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
