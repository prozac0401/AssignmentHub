"""Exercise the operator filters and rendered detail with Streamlit."""
from copy import deepcopy

from streamlit.testing.v1 import AppTest
from assignmenthub import ui


def render_dashboard():
    from assignmenthub.ui import admin_dashboard
    admin_dashboard()


def test_dashboard_latest_search_missing_and_previous_versions(monkeypatch):
    def submission(id, version, filename, user="001", name="홍길동"):
        return {"id": id, "user_id": user, "name": name, "group": "A", "assignment_id": "a", "assignment_title": "실습",
                "submission_number": version, "version": version, "completed_at": f"2026-09-10T00:00:0{version}+00:00",
                "total_bytes": 3, "files": [{"id": id + "_file", "name": filename, "size": 3,
                                           "storage_path": "D:/제출/홍길동/" + id + "/" + filename}]}
    old = submission("old", 1, "초안.txt")
    latest = submission("latest", 2, "최종.txt")
    rows = [{"id": "u1", "user_id": "001", "name": "홍길동", "group": "A", "active": True,
             "submitted": True, "submission_count": 2, "latest": latest},
            {"id": "u2", "user_id": "002", "name": "김미제출", "group": "A", "active": True,
             "submitted": False, "submission_count": 0, "latest": None}]
    data = {"target_count": 2, "submitted_count": 1, "missing_count": 1, "in_progress_count": 0,
            "failed_count": 0, "rows": rows, "submissions": [latest, old]}
    exports = []
    def call(path, *args, **kwargs):
        if path == "/assignments":
            return [{"id": "a", "title": "실습"}]
        return deepcopy(data)
    monkeypatch.setattr(ui, "call", call)
    monkeypatch.setattr(ui, "private_csv_download", lambda label, data, name: exports.append(data.decode("utf-8-sig")))
    app = AppTest.from_function(render_dashboard, default_timeout=15).run()
    assert not app.exception
    assert len(app.dataframe[1].value) == 1
    assert app.dataframe[1].value.iloc[0]["원본 파일명"] == "최종.txt"
    assert "홍길동" in app.selectbox[-1].options[0] and "최종.txt" in app.selectbox[-1].options[0]
    app.checkbox(key="admin_previous_versions").check().run()
    assert not app.exception
    assert len(app.dataframe[1].value) == 2
    app.text_input(key="admin_file_search").set_value("초안").run()
    assert not app.exception
    assert app.dataframe[1].value.iloc[0]["원본 파일명"] == "초안.txt"
    assert "초안.txt" in exports[-1] and "최종.txt" not in exports[-1]
    app.radio[0].set_value("미제출자").run()
    assert not app.exception
    assert len(app.dataframe) == 1
    assert app.dataframe[0].value.iloc[0]["이름"] == "김미제출"
    assert any("완료 제출물이 없습니다" in item.value for item in app.info)
