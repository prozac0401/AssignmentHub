from __future__ import annotations

from dataclasses import asdict, dataclass
import ipaddress
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass
class Config:
    instance_id: str
    course_name: str
    port: int
    storage_root: str
    public_host: str = "127.0.0.1"
    bind_host: str = "0.0.0.0"
    max_file_bytes: int = 2 * 1024**3
    user_quota_bytes: int = 10 * 1024**3
    min_free_bytes: int = 5 * 1024**3
    chunk_bytes: int = 8 * 1024**2
    max_files: int = 10
    concurrent_uploads: int = 4
    upload_ttl_hours: float = 24
    session_hours: float = 8
    timezone: str = "Asia/Seoul"
    secure_cookies: bool = False
    tls_cert_file: str | None = None
    tls_key_file: str | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.instance_id):
            raise ValueError("instance_id는 영문, 숫자, 밑줄, 하이픈 1~64자입니다.")
        if not isinstance(self.port, int) or not 1024 <= self.port <= 65535:
            raise ValueError("접속 포트는 1024~65535입니다.")
        if not self.course_name.strip() or len(self.course_name) > 200:
            raise ValueError("과정명은 1~200자입니다.")
        for host in (self.public_host, self.bind_host):
            if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host):
                raise ValueError("호스트명에는 주소만 입력하세요. URL이나 경로는 허용하지 않습니다.")
        try:
            ipaddress.ip_address(self.bind_host)
        except ValueError:
            if self.bind_host != "localhost":
                raise ValueError("바인딩 주소는 IP 주소 또는 localhost여야 합니다.") from None
        for key in ("max_file_bytes", "user_quota_bytes", "chunk_bytes", "max_files", "concurrent_uploads"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError(f"{key}는 양의 정수여야 합니다.")
        if self.max_file_bytes > 2**53 - 1 or self.user_quota_bytes > 2**53 - 1:
            raise ValueError("용량은 브라우저의 정확한 정수 범위 이내여야 합니다.")
        if self.chunk_bytes > 32 * 1024**2 or self.max_files > 100 or self.concurrent_uploads > 64:
            raise ValueError("청크는 최대 32MiB, 파일은 100개, 동시 전송은 64개까지입니다.")
        if type(self.min_free_bytes) is not int or self.min_free_bytes < 0:
            raise ValueError("최소 디스크 여유 공간은 0 이상의 바이트 수입니다.")
        if not 0 < self.upload_ttl_hours <= 8760 or not 0 < self.session_hours <= 168:
            raise ValueError("업로드 보관 시간/세션 시간이 유효하지 않습니다.")
        if not self.storage_root:
            raise ValueError("저장 루트가 필요합니다.")
        ZoneInfo(self.timezone)
        if self.secure_cookies:
            if not self.tls_cert_file or not self.tls_key_file:
                raise ValueError("HTTPS에는 tls_cert_file과 tls_key_file이 모두 필요합니다.")
            if not Path(self.tls_cert_file).is_file() or not Path(self.tls_key_file).is_file():
                raise ValueError("TLS 인증서 또는 키 파일을 찾을 수 없습니다.")

    @property
    def root(self) -> Path:
        return Path(self.storage_root).expanduser().resolve()

    @property
    def public_url(self) -> str:
        host = f"[{self.public_host}]" if ":" in self.public_host else self.public_host
        return f"{'https' if self.secure_cookies else 'http'}://{host}:{self.port}"

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path).resolve()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        root = Path(data["storage_root"]).expanduser()
        if not root.is_absolute():
            data["storage_root"] = str((path.parent / root).resolve())
        return cls(**data)

    def save(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["storage_root"] = str(self.root)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
