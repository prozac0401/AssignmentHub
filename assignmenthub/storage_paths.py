"""Readable, persistent storage names. Authentication IDs remain independent."""
from pathlib import Path
import re
import unicodedata


def storage_name(value, limit=120):
    # NFC plus case-insensitive de-duplication matches common Windows filesystems.
    value = unicodedata.normalize("NFC", str(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).strip().rstrip(". ")
    value = value or "파일"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:[. ]|$)", value, re.I):
        value = "_" + value
    def trim(text, count):
        return text.encode("utf-16-le")[:count * 2].decode("utf-16-le", errors="ignore")
    suffix = trim(Path(value).suffix, min(20, limit // 2))
    if len(value.encode("utf-16-le")) // 2 > limit:
        full_suffix = Path(value).suffix
        value = trim(value[:-len(full_suffix)] if full_suffix else value,
                     limit - len(suffix.encode("utf-16-le")) // 2) + suffix
    return value.rstrip(". ") or "파일"


def assign_storage_names(db, upload_id):
    row = db.execute("""SELECT u.rowid AS sequence,u.storage_dir,u.temp_dir,
        a.rowid AS assignment_number,a.title,p.rowid AS student_number,p.name,p.user_id
        FROM uploads u JOIN assignments a ON a.id=u.assignment_id
        JOIN users p ON p.id=u.user_pk WHERE u.id=?""", (upload_id,)).fetchone()
    if not row["storage_dir"]:
        course = f"과제-{row['assignment_number']}_{storage_name(row['title'], 40)}"
        student = f"{storage_name(row['name'], 32)}_{storage_name(row['user_id'], 24)}_수강생-{row['student_number']}"
        receipt = f"접수-{row['sequence']:06d}"
        db.execute("UPDATE uploads SET storage_dir=?,temp_dir=? WHERE id=?",
                   (f"{course}/{student}/{receipt}", f"{student}_{receipt}", upload_id))
    files = list(db.execute("SELECT id,name,storage_name FROM files WHERE upload_id=? ORDER BY ordinal", (upload_id,)))
    used = {f["storage_name"].casefold() for f in files if f["storage_name"]}
    for file in files:
        if file["storage_name"]:
            continue
        name = storage_name(file["name"])
        candidate, number = name, 1
        while candidate.casefold() in used:
            number += 1
            suffix = Path(name).suffix
            stem = name[:-len(suffix)] if suffix else name
            candidate = f"{stem} ({number}){suffix}"
        used.add(candidate.casefold())
        db.execute("UPDATE files SET storage_name=? WHERE id=?", (candidate, file["id"]))


def contained_path(root, relative):
    root = Path(root).resolve()
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise OSError("저장 경로가 지정된 폴더를 벗어났습니다.")
    return target
