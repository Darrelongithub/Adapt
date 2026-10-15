"""First-use routing, saved consent, private dashboard resumes and reviewed subject plans."""
from sqlalchemy import select

from adapt.db.models import BaselineRun, LessonRun, LessonScene
from adapt.subjects.models import SubjectRound


def register(client, *, username=None):
    body = {"consent_analytics": True, "consent_share_with_llm": False}
    if username:
        body.update(username=username, password="long test password")
    else:
        body["as_guest"] = True
    result = client.post("/api/v1/students", json=body)
    assert result.status_code == 200, result.text
    profile = result.json()
    return profile, {"x-csrf-token": profile["csrf_token"]}


def test_new_guest_has_profile_and_explicit_consent_then_baseline_history(client):
    profile, headers = register(client)
    assert profile["baseline_started"] is False
    assert profile["display_name"]
    assert profile["consent_analytics"] is True
    assert profile["consent_share_with_ai"] is False
    run = client.post("/api/v1/baseline", json={}, headers=headers)
    assert run.status_code == 200, run.text
    assert client.get("/api/v1/me").json()["baseline_started"] is True


def test_returning_login_contains_history_even_when_baseline_is_unfinished(client):
    import uuid
    username = "entry." + uuid.uuid4().hex[:12]
    profile, headers = register(client, username=username)
    run = client.post("/api/v1/baseline", json={}, headers=headers).json()
    client.post("/api/v1/logout", json={}, headers=headers)
    back = client.post(
        "/api/v1/login", json={"username": username, "password": "long test password"}
    )
    assert back.status_code == 200, back.text
    assert back.json()["student_id"] == profile["student_id"]
    assert back.json()["baseline_started"] is True
    dash = client.get("/api/v1/dashboard").json()
    assert run["run_id"] in {row["id"] for row in dash["learning_activity"]["tests"]}


def test_dashboard_only_lists_own_unfinished_activity_and_does_not_create_runs(client, services):
    owner, _ = register(client)
    outsider, _ = register(client)
    with services.scope() as s:
        scene = s.scalars(select(LessonScene)).first()
        s.add_all([
            LessonRun(student_id=owner["student_id"], scene_id=scene.id, status="active"),
            LessonRun(student_id=outsider["student_id"], scene_id=scene.id, status="finished"),
            BaselineRun(student_id=owner["student_id"], status="in_progress"),
            BaselineRun(student_id=outsider["student_id"], status="complete"),
        ])
    for _ in range(2):
        response = client.get("/api/v1/dashboard")
        assert response.status_code == 200
        assert response.json()["learning_activity"] == {"lessons": [], "tests": []}
    assert client.get("/api/v1/me").json()["baseline_started"] is True


def test_reviewed_outline_is_used_and_comments_survive_resume(client, services):
    profile, headers = register(client)
    proposed = client.get("/api/v1/subjects/plan", params={"topic": "calculus"}).json()
    selected = [proposed["tested"][0]["id"]]
    payload = {"topic": "calculus", "selected_skills": selected, "comments": "Please start slowly."}
    response = client.post("/api/v1/subjects/round", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    run = response.json()
    assert [row["id"] for row in run["plan"]["tested"]] == selected
    assert run["plan"]["learner_review"]["comments"] == payload["comments"]
    removed = {row["id"] for row in proposed["tested"]} - set(selected)
    skipped = {row["id"]: row for row in run["plan"]["not_measured"]}
    assert all(skipped[key]["score"] is None for key in removed)
    resumed = client.get(f"/api/v1/subjects/round/{run['run_id']}").json()
    assert resumed["plan"]["learner_review"] == run["plan"]["learner_review"]
    assert resumed["task"]["skill"] in selected
    with services.scope() as s:
        stored = s.get(SubjectRound, run["run_id"])
        assert stored.student_id == profile["student_id"]
        assert stored.plan["learner_review"]["comments"] == payload["comments"]
    # Changed drafts never silently overwrite an in-progress round.
    changed = client.post(
        "/api/v1/subjects/round", json={**payload, "comments": "changed"}, headers=headers
    )
    assert changed.status_code == 422


def test_invalid_or_empty_outline_cannot_start_a_round(client):
    _, headers = register(client)
    for selection in ([], ["not-a-skill"]):
        response = client.post(
            "/api/v1/subjects/round",
            json={"topic": "calculus", "selected_skills": selection}, headers=headers,
        )
        assert response.status_code == 422, response.text
