"""Regression: a learner can download populated own data without credentials."""


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def test_export_contains_work_and_strips_credentials(client, learner, session, items, make_answer):
    item = items[0]["item"]
    answer = make_answer(item["id"])
    submitted = client.post(
        "/api/v1/attempts",
        json={"session_id": session, "item_id": item["id"], "answer": answer},
        headers=learner["headers"],
    )
    assert submitted.status_code == 200, submitted.text
    event = client.post(
        "/api/v1/events",
        json={"session_id": session, "events": [{"type": "pause", "payload": {"reason": "export regression"}}]},
        headers=learner["headers"],
    )
    assert event.json()["accepted"] == 1
    reflected = client.post(
        "/api/v1/journey/advance",
        json={"stage": "first_lesson", "status": "done", "note": "Export this reflection."},
        headers=learner["headers"],
    )
    assert reflected.status_code == 200

    response = client.get("/api/v1/me/export")
    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert body["format"] == "adapt-learner-export"
    assert body["student"]["id"] == learner["student_id"]
    assert body["records"]["learning_sessions"][0]["id"] == session
    assert body["records"]["attempts"][0]["final_answer"] == {"value": answer}
    assert any(row["type"] == "pause" and row["session_id"] == session for row in body["records"]["learner_events"])
    assert body["records"]["journey_stages"][0]["note"] == "Export this reflection."
    assert learner["csrf"] not in response.text
    assert "correct horse" not in response.text
    assert not (set(_keys(body)) & {"password_hash", "csrf_token", "token_hash", "grading_seal", "email_index"})
    assert body["record_counts"]["attempts"] == 1


def test_registered_export_has_identity_not_password_material(client, registered_learner):
    response = client.get("/api/v1/me/export")
    assert response.status_code == 200
    assert response.json()["student"]["email"] == registered_learner["email"]
    assert registered_learner["password"] not in response.text
    assert "password_hash" not in set(_keys(response.json()))


def test_export_requires_a_learner_session(client):
    client.cookies.clear()
    response = client.get("/api/v1/me/export")
    assert response.status_code == 401
