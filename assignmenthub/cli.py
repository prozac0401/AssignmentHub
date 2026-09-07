"""Windows-friendly local administration. Passwords are only read with getpass."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import json
from pathlib import Path
import secrets
import sys

from fastapi import HTTPException

from assignmenthub.config import Config
from assignmenthub.launcher import LaunchError, StorageLock, preflight, start, status, stop, supervise


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="AssignmentHub 인스턴스 관리 (용량 옵션은 바이트)")
    actions = root.add_subparsers(dest="action", required=True)
    for action in ("create", "configure", "start", "run", "status", "stop", "validate", "admin"):
        command = actions.add_parser(action)
        command.add_argument("--config", type=Path, required=True, help="인스턴스 JSON 설정 경로")
        if action in ("create", "configure"):
            for name in ("course-name", "public-host", "bind-host", "timezone", "tls-cert-file", "tls-key-file"):
                command.add_argument("--" + name)
            for name in ("port", "max-file-bytes", "user-quota-bytes", "min-free-bytes", "chunk-bytes", "max-files", "concurrent-uploads"):
                command.add_argument("--" + name, type=int)
            for name in ("upload-ttl-hours", "session-hours"):
                command.add_argument("--" + name, type=float)
            command.add_argument("--secure-cookies", action=argparse.BooleanOptionalAction, default=None)
        if action == "create":
            command.add_argument("--instance-id", required=True)
            command.add_argument("--storage-root", type=Path, required=True)
            command.add_argument("--admin-id", default=None, help="생략 시 로컬 콘솔에서 입력")
        if action == "admin":
            command.add_argument("--admin-id", default=None)
        if action == "run":
            command.add_argument("--launch-id", default=None, help=argparse.SUPPRESS)
        if action in ("status", "start", "stop"):
            command.add_argument("--json", action="store_true")
    return root


def _admin_inputs(admin_id: str | None) -> tuple[str, str]:
    if admin_id is None:
        admin_id = input("최초 관리자 ID: ").strip()
    password = getpass.getpass("최초 관리자 비밀번호 (12~128자): ")
    repeated = getpass.getpass("비밀번호 확인: ")
    if password != repeated:
        raise LaunchError("비밀번호 확인이 일치하지 않습니다.")
    if not 12 <= len(password) <= 128:
        raise LaunchError("비밀번호는 12~128자여야 합니다.")
    return admin_id.strip(), password


def create(args):
    from assignmenthub.service import Service
    if args.config.exists():
        raise LaunchError("설정 파일이 이미 있습니다. configure 명령을 사용하세요.")
    required = {"course_name": args.course_name, "port": args.port, "public_host": args.public_host}
    if any(value is None for value in required.values()):
        raise LaunchError("create에는 --course-name, --port, --public-host가 필요합니다. 수강생에게 안내할 서버 IP/호스트명을 지정하세요.")
    values = {key: value for key, value in vars(args).items() if key in Config.__dataclass_fields__ and value is not None}
    values["storage_root"] = str(args.storage_root.resolve())
    config = Config(**values)
    admin_id, password = _admin_inputs(args.admin_id)
    with StorageLock(config.root):
        preflight(config)
        service = Service(config)
        service.bootstrap_admin(admin_id, password)
        config.save(args.config)
    print(f"생성했습니다: {config.instance_id}\n설정: {args.config.resolve()}\n저장: {config.root}\n접속 주소: {config.public_url}")


def configure(args):
    config = Config.load(args.config)
    with StorageLock(config.root):
        values = asdict(config)
        for key, value in vars(args).items():
            if key in values and value is not None:
                values[key] = value
        updated = Config(**values)
        preflight(updated)
        updated.save(args.config)
    print(f"설정을 저장했습니다. 접속 주소: {updated.public_url}")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "create":
            create(args)
        elif args.action == "configure":
            configure(args)
        elif args.action == "run":
            return supervise(args.config, args.launch_id or secrets.token_hex(16))
        elif args.action == "admin":
            from assignmenthub.service import Service
            config = Config.load(args.config)
            with StorageLock(config.root):
                admin_id, password = _admin_inputs(args.admin_id)
                Service(config).bootstrap_admin(admin_id, password)
            print("로컬 관리자 계정을 생성했습니다.")
        elif args.action == "validate":
            config = Config.load(args.config)
            with StorageLock(config.root):
                preflight(config)
            print(f"실행 전 검사를 통과했습니다: {config.public_url}")
        else:
            config = Config.load(args.config)
            result = start(args.config) if args.action == "start" else stop(config) if args.action == "stop" else status(config)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"{config.instance_id}: {'실행 중' if result.get('running') else '중지됨'}\n접속 주소: {config.public_url}\n저장: {config.root}")
        return 0
    except (LaunchError, ValueError, OSError, EOFError, HTTPException) as exc:
        print(f"오류: {getattr(exc, 'detail', str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
