from copy import deepcopy

from streamlit.testing.v1 import AppTest

from assignmenthub import ui


def render_assignments():
    from assignmenthub.ui import admin_assignments
    admin_assignments()


def test_assignment_form_can_save_enable_then_disable(monkeypatch):
    assignment = {"id": "test", "title": "문서 과제", "description": "", "is_open": True,
                  "allowed_file_categories": ["documents"], "video_audio_required": True}
    writes = []

    def call(path, method="GET", data=None):
        if method == "GET":
            return [deepcopy(assignment)]
        assert method == "PATCH"
        writes.append(deepcopy(data))
        assignment.update(data)
        return deepcopy(assignment)

    monkeypatch.setattr(ui, "call", call)
    app = AppTest.from_function(render_assignments).run()
    assert not app.exception
    app.checkbox(key="edit_test_archives").check()
    app.button[1].click().run()
    assert not app.exception
    assert assignment["allowed_file_categories"] == ["documents", "archives"]
    app.checkbox(key="edit_test_archives").uncheck()
    app.button[1].click().run()
    assert not app.exception
    assert assignment["allowed_file_categories"] == ["documents"], writes
    app.checkbox(key="edit_test_video_audio_required").uncheck()
    app.button[1].click().run()
    assert assignment["video_audio_required"] is False
    app.checkbox(key="edit_test_video_audio_required").check()
    app.button[1].click().run()
    assert assignment["video_audio_required"] is True
    app.checkbox(key="edit_test_documents").uncheck()
    app.button[1].click().run()
    assert assignment["allowed_file_categories"] == []
    app.checkbox(key="edit_test_documents").check()
    app.button[1].click().run()
    assert assignment["allowed_file_categories"] == ["documents"]
