"""TSV decoding, identity preservation, validation and authorization boundaries."""
import pytest

from test_api import auth, hub, student, tsv_bytes


def preview(client, admin, content):
    return client.post("/api/admin/roster/preview", content=content,
                       headers={**auth(admin), "Content-Type": "text/tab-separated-values"})


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-be-bom"])
def test_tsv_encodings_leading_zeroes_case_and_optional_group(hub, encoding):
    client, _, admin = hub
    text = "user_id\tname\r\n001\t홍길동\r\n1\t숫자 모양 ID\r\nCase\t대문자\r\ncase\t소문자\r\n"
    body = b"\xfe\xff" + text.encode("utf-16-be") if encoding == "utf-16-be-bom" else text.encode(encoding)
    response = preview(client, admin, body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["valid"]
    assert [row["user_id"] for row in result["rows"]] == ["001", "1", "Case", "case"]
    assert all(row["group"] == "" for row in result["rows"])
    assert len(client.get("/api/admin/users", headers=auth(admin)).json()) == 1
    applied = client.post("/api/admin/roster/apply", headers=auth(admin), json={"rows": result["rows"]})
    assert applied.status_code == 200, applied.text
    assert {row["user_id"] for row in applied.json()["created"]} == {"001", "1", "Case", "case"}


def test_tsv_quoted_fields_blank_lines_and_reordered_headers(hub):
    client, _, admin = hub
    body = '\n\t\t\n name\tgroup\tuser_id \n"홍\t길동"\t"A ""반"""\t001\n\n"김\n민수"\tB\t002\n'.encode()
    response = preview(client, admin, body)
    assert response.status_code == 200 and response.json()["valid"], response.text
    first, second = response.json()["rows"]
    assert (first["name"], first["group"], first["user_id"], first["row"]) == ("홍\t길동", 'A "반"', "001", 4)
    assert second["name"] == "김\n민수" and second["row"] == 6


@pytest.mark.parametrize("body", [
    b"", b"user_id,name,group\n001,Name,A\n", b"user_id\tuser_id\tname\n001\t001\tName\n",
    b"user_id\tname\tgroup\tgroup\n001\tName\tA\tB\n", b"user_id\tname\n\n\t\n",
    b"user_id\tname\n001\tName\textra\n", b"user_id\tname\n001\tName\n 001 \tOther\n",
    b"user_id\tname\n001\n", b"user_id\tname\nadmin\tOverwrite admin\n",
    tsv_bytes([("x" * 129, "Name", "")]), tsv_bytes([("a\tb", "Name", "")]),
    tsv_bytes([("001", "x" * 201, "")]), tsv_bytes([("001", "Name", "x" * 201)]),
], ids=["empty", "csv", "duplicate-id-header", "duplicate-group-header", "blank-rows", "extra-column",
        "duplicate-id", "missing-name", "admin-id", "long-id", "control-id", "long-name", "long-group"])
def test_tsv_invalid_rows_and_headers_cannot_be_applied_from_preview(hub, body):
    client, _, admin = hub
    response = preview(client, admin, body)
    assert response.status_code == 200, response.text
    assert not response.json()["valid"]
    assert response.json()["errors"] or any(row["errors"] for row in response.json()["rows"])
    assert len(client.get("/api/admin/users", headers=auth(admin)).json()) == 1


@pytest.mark.parametrize("body", [
    b"PK\x03\x04old-xlsx", b"\xffbad encoding", b"user_id\tname\n001\tbad\x00name",
    b'user_id\tname\n001\t"unclosed', b'user_id\tname\n001\t"name"broken\n',
    b"user_id\tname\n001\t" + b"x" * 140000,
    tsv_bytes([(str(n), "Name") for n in range(10001)], ["user_id", "name"]),
    tsv_bytes([], ["user_id", "name", *[f"extra{n}" for n in range(29)]]),
], ids=["xlsx", "encoding", "nul", "unclosed-quote", "broken-quote", "huge-field", "too-many-rows", "too-many-columns"])
def test_tsv_malformed_encoding_quoting_and_resource_limits(hub, body):
    client, _, admin = hub
    response = preview(client, admin, body)
    assert response.status_code == 422, response.text


def test_tsv_size_limit_and_admin_permission(hub):
    client, _, admin = hub
    body = tsv_bytes([("001", "Name", "")])
    assert client.post("/api/admin/roster/preview", content=body).status_code == 401
    session = student(client, admin)
    assert preview(client, session, body).status_code == 403
    assert preview(client, admin, b"x" * (5 * 2**20 + 1)).status_code == 413


def test_tsv_omitted_trailing_group_is_empty(hub):
    client, _, admin = hub
    response = preview(client, admin, b"user_id\tname\tgroup\n001\tName\n")
    assert response.status_code == 200 and response.json()["valid"], response.text
    assert response.json()["rows"][0]["group"] == ""
