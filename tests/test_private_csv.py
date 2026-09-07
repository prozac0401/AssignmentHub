"""Security boundaries for private CSV generation; no public Streamlit media URLs."""
from assignmenthub import ui


def test_private_csv_escapes_script_terminators_and_uses_client_blob(monkeypatch):
    captured = []
    monkeypatch.setattr(ui.components, "html", lambda content, **kwargs: captured.append(content))
    data = ui.csv_bytes([{"user_id": "0001", "name": "</script><img src=x onerror=alert(1)>", "temporary_password": "=DANGEROUS()"}], ["user_id", "name", "temporary_password"])
    ui.private_csv_download("다운로드 <확인>", data, "temporary_passwords.csv")
    assert data.startswith(b"\xef\xbb\xbf")
    assert "'=DANGEROUS()" in data.decode("utf-8-sig")
    markup = captured[0]
    assert "</script><img" not in markup
    assert "\\u003c/script>" in markup
    assert "다운로드 &lt;확인&gt;" in markup
    assert "new Blob(" in markup
    assert "URL.revokeObjectURL(url)" in markup
    assert "/media/" not in markup
    assert "fetch(" not in markup
