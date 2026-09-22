"""One file category catalogue shared by the API, console and uploader."""
from __future__ import annotations

import json
from typing import Literal

FileCategory = Literal["documents", "spreadsheets", "presentations", "images", "video", "audio", "archives", "code", "other"]

FILE_CATEGORIES = [
    {"id": "documents", "label": "문서", "extensions": [".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages", ".epub"]},
    {"id": "spreadsheets", "label": "스프레드시트", "extensions": [".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".csv", ".tsv", ".numbers"]},
    {"id": "presentations", "label": "발표자료", "extensions": [".ppt", ".pptx", ".pps", ".ppsx", ".odp", ".key"]},
    {"id": "images", "label": "이미지", "extensions": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff", ".heic", ".heif", ".avif", ".psd", ".ai"]},
    {"id": "video", "label": "영상", "extensions": [".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg", ".mts", ".m2ts", ".flv", ".ogv", ".3gp", ".3g2", ".vob"]},
    {"id": "audio", "label": "음성·음악", "extensions": [".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aif", ".aiff", ".amr"]},
    {"id": "archives", "label": "압축파일", "extensions": [".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".alz", ".egg"]},
    {"id": "code", "label": "코드·데이터", "extensions": [".py", ".ipynb", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".json", ".xml", ".yaml", ".yml", ".sql", ".r", ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".kt", ".go", ".rs", ".swift", ".php", ".rb", ".sh", ".bat", ".ps1"]},
    {"id": "other", "label": "기타", "extensions": []},
]
ALL_CATEGORIES = [category["id"] for category in FILE_CATEGORIES]
DEFAULT_CATEGORIES = [category for category in ALL_CATEGORIES if category != "other"]
EXTENSION_CATEGORIES = {ext: category["id"] for category in FILE_CATEGORIES for ext in category["extensions"]}


class FilePolicyError(ValueError):
    pass


def normalize_categories(values):
    if not isinstance(values, list) or any(value not in ALL_CATEGORIES for value in values) or len(set(values)) != len(values):
        raise ValueError("허용 파일 분류를 중복 없이 선택하세요.")
    return [category for category in ALL_CATEGORIES if category in values]


def file_category(name: str) -> str:
    # Treat both path separators consistently on every server OS. Names are
    # display metadata only; the storage path always uses server-generated IDs.
    basename = name.replace("\\", "/").rsplit("/", 1)[-1].rstrip(" .").lower()
    suffix = "." + basename.rsplit(".", 1)[-1] if "." in basename else ""
    return EXTENSION_CATEGORIES.get(suffix, "other")


def public_assignment(row):
    return {**dict(row), "is_open": bool(row["is_open"]),
            "allowed_file_categories": json.loads(row["allowed_file_categories"]),
            "video_audio_required": bool(row["video_audio_required"])}


def validate_files(assignment, files):
    allowed = json.loads(assignment["allowed_file_categories"])
    for file in files:
        category = file_category(file["name"])
        if category not in allowed:
            label = next(item["label"] for item in FILE_CATEGORIES if item["id"] == category)
            raise FilePolicyError(f"{file['name']}: 이 과제는 {label} 파일을 허용하지 않습니다. 허용 분류를 확인하고 새 제출을 시작하세요.")


def policy_summary(assignment):
    allowed = assignment["allowed_file_categories"]
    return ", ".join(c["label"] for c in FILE_CATEGORIES if c["id"] in allowed) or "허용된 파일 분류 없음"
