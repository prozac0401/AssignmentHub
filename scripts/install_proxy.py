"""Install the pinned official Caddy binary; no runtime network dependency.

Only the named executable is extracted, and release checksum verification occurs
before extraction. HTTPS protects the official checksum fetch. No arbitrary
archive paths are extracted and no executable is launched before verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile

VERSION = "2.10.2"
RELEASE = f"https://github.com/caddyserver/caddy/releases/download/v{VERSION}"
ROOT = Path(__file__).resolve().parents[1]


def download(url: str, target: Path):
    request = urllib.request.Request(url, headers={"User-Agent": "AssignmentHub-setup/1"})
    with urllib.request.urlopen(request, timeout=120) as source, target.open("wb") as output:
        shutil.copyfileobj(source, output, 1024 * 1024)


def digest(path: Path, algorithm="sha256"):
    value = hashlib.new(algorithm)
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def install(force: bool = False):
    operating_system = {"Windows": "windows", "Linux": "linux", "Darwin": "mac"}.get(platform.system())
    architecture = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    if not operating_system or not architecture:
        raise RuntimeError(f"Unsupported Caddy platform: {platform.system()}/{platform.machine()}")
    executable = "caddy.exe" if operating_system == "windows" else "caddy"
    extension = "zip" if operating_system == "windows" else "tar.gz"
    archive_name = f"caddy_{VERSION}_{operating_system}_{architecture}.{extension}"
    target_dir = ROOT / "tools"
    target = target_dir / executable
    manifest = target_dir / "caddy-install.json"
    if not force and target.exists() and manifest.exists():
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if saved.get("version") == VERSION and saved.get("binary_sha256") == digest(target):
            print(f"Verified installed Caddy {VERSION}: {target}")
            return
    target_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="assignmenthub-caddy-") as folder:
        temporary = Path(folder)
        checksum_file = temporary / "checksums.txt"
        download(f"{RELEASE}/caddy_{VERSION}_checksums.txt", checksum_file)
        expected = None
        for line in checksum_file.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].lstrip("*") == archive_name:
                expected = fields[0].lower()
                break
        if not expected or len(expected) not in (64, 128):
            raise RuntimeError("The pinned official checksum manifest has no supported archive checksum.")
        archive = temporary / archive_name
        print(f"Downloading official Caddy {VERSION} ({operating_system}/{architecture})...")
        download(f"{RELEASE}/{archive_name}", archive)
        algorithm = "sha512" if len(expected) == 128 else "sha256"
        if digest(archive, algorithm) != expected:
            raise RuntimeError("Caddy checksum mismatch; refusing installation.")
        staged = target_dir / (executable + ".installing")
        try:
            if extension == "zip":
                with zipfile.ZipFile(archive) as bundle, bundle.open(executable) as source, staged.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            else:
                with tarfile.open(archive, "r:gz") as bundle:
                    member = bundle.getmember(executable)
                    if not member.isfile():
                        raise RuntimeError("Expected regular Caddy binary was absent.")
                    with bundle.extractfile(member) as source, staged.open("wb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            binary_sha = digest(staged)
            os.replace(staged, target)
            manifest.write_text(json.dumps({"version": VERSION, "source": f"{RELEASE}/{archive_name}",
                                            "archive_checksum_algorithm": algorithm, "archive_checksum": expected,
                                            "binary_sha256": binary_sha}, indent=2) + "\n", encoding="utf-8")
        finally:
            staged.unlink(missing_ok=True)
    print(f"Installed and checksum-verified Caddy {VERSION}: {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    install(force=args.force)
