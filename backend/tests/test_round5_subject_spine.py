"""Round five subject/course spine contracts.

These tests stay at the HTTP boundary for the learner flow: the plan shown to the browser is
bounded, the active subject is learner-owned, and progress never accepts an unknown test id.
"""
from __future__ import annotations


def test_choose_returns_a_closed_plan_and_overview_supports_multiple_subjects(client, learner):
    headers = learner["headers"]
    first = client.post("/api/v1/subjects/choose", json={"text": "discrete structures"}, headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) >= {"subject_id", "plan"}
    assert body["plan"]["source"] in {"model", "simple"}
    tests = body["plan"]["tests"]
    assert 1 <= len(tests) <= 6
    assert all(set(item) >= {"id", "title", "why", "level"} for item in tests)
    assert all(1 <= item["level"] <= 4 for item in tests)

    second = client.post("/api/v1/subjects/choose", json={"text": "Kiswahili"}, headers=headers)
    assert second.status_code == 200, second.text
    overview = client.get("/api/v1/learning/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert set(data) == {"active_subject_id", "subjects", "due", "proposals"}
    assert len(data["subjects"]) == 2
    assert {row["family"] for row in data["subjects"]} >= {"maths", "languages"}
    assert all(set(row) == {"id", "title", "family", "stage", "progress", "next"} for row in data["subjects"])


def test_subject_progress_only_accepts_plan_ids_and_activation_is_scoped(client, learner):
    headers = learner["headers"]
    chosen = client.post("/api/v1/subjects/choose", json={"text": "geometry"}, headers=headers).json()
    subject_id = chosen["subject_id"]
    ids = [row["id"] for row in chosen["plan"]["tests"]]

    bad = client.post(
        f"/api/v1/subjects/{subject_id}/tests/complete",
        json={"completed_ids": ids + ["not-a-real-test"]},
        headers=headers,
    )
    assert bad.status_code == 200, bad.text
    assert bad.json()["progress"]["done"] == len(ids)
    assert bad.json()["stage"] == "done"

    activated = client.post(f"/api/v1/subjects/{subject_id}/activate", headers=headers)
    assert activated.status_code == 200, activated.text
    assert activated.json()["active"] is True
    overview = client.get("/api/v1/learning/overview", headers=headers).json()
    assert overview["active_subject_id"] == subject_id


def test_subject_id_and_csrf_cannot_cross_learner_boundaries(client, learner):
    first = client.post("/api/v1/subjects/choose", json={"text": "biology"}, headers=learner["headers"])
    assert first.status_code == 200, first.text
    subject_id = first.json()["subject_id"]
    other = client.post(
        "/api/v1/students", json={"display_name": "Other", "as_guest": True, "age_band": "25+"}
    )
    assert other.status_code == 200, other.text
    other_headers = {"x-csrf-token": other.json()["csrf_token"]}
    hidden = client.get(f"/api/v1/subjects/{subject_id}", headers=other_headers)
    assert hidden.status_code == 404, hidden.text
    csrf = client.post("/api/v1/subjects/choose", json={"text": "biology"})
    assert csrf.status_code in {403, 422}, csrf.text


def test_template_library_rejects_injection_and_malformed_pairs_without_raising():
    from adapt.learning.template_library import validate_template

    bad_text, errors = validate_template({
        "template": "timeline",
        "params": {"events": [["2020", "<script>alert(1)</script>"]]},
        "captions": ["safe"],
        "source": "model",
    })
    assert bad_text is None
    assert errors

    malformed, errors = validate_template({
        "template": "fraction-bars",
        "params": {"fractions": [["not", "numbers"], [1]]},
        "captions": [],
        "source": "simple",
    })
    assert malformed is None
    assert errors

    unknown, errors = validate_template({"template": "flowchart", "params": {}, "run": "no"})
    assert unknown is None
    assert errors
