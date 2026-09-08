"""Create the UTF-8 BOM / CRLF TSV roster template without Excel libraries."""
import csv
from pathlib import Path


def generate(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\r\n")
        writer.writerows([("user_id", "name", "group"), ("001", "홍길동", "A반"), ("002", "김민수", "B반")])


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "templates" / "users_template.tsv")
