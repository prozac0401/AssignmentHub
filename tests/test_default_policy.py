"""New assignments use safe defaults while legacy assignments keep their policy."""
import json

from assignmenthub.file_policy import ALL_CATEGORIES, DEFAULT_CATEGORIES
from assignmenthub.service import Service
from test_api import auth, begin
from test_file_policy import policy_hub


def test_first_assignment_matches_new_api_assignment_and_rejects_other(policy_hub):
    client, admin, student = policy_hub
    first = client.get("/api/assignments", headers=auth(admin)).json()[0]
    added = client.post("/api/admin/assignments", headers=auth(admin),
                        json={"title": "추가 과제"}).json()
    for assignment in (first, added):
        assert assignment["allowed_file_categories"] == DEFAULT_CATEGORIES
        assert assignment["video_audio_required"] is True
        assert begin(client, student, [("unknown.bin", b"content")],
                     assignment_id=assignment["id"]).status_code == 415
        assert begin(client, student, [("report.txt", b"content")],
                     assignment_id=assignment["id"]).status_code == 200


def test_restart_preserves_explicit_other_permission(policy_hub):
    client, admin, student = policy_hub
    assignment = client.get("/api/assignments", headers=auth(admin)).json()[0]
    response = client.patch("/api/admin/assignments/" + assignment["id"], headers=auth(admin),
                            json={"allowed_file_categories": ALL_CATEGORIES})
    assert response.status_code == 200
    restarted = Service(client.app.state.config)
    with restarted.store.connect() as db:
        row = db.execute("SELECT * FROM assignments WHERE id=?", (assignment["id"],)).fetchone()
        assert json.loads(row["allowed_file_categories"]) == ALL_CATEGORIES
    assert begin(client, student, [("unknown.bin", b"content")],
                 assignment_id=assignment["id"]).status_code == 200
