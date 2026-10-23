"""Regression: subject runs resume from account-owned server state, not browser storage."""


def _plan(client, learner):
    chosen = client.post("/api/v1/subjects/choose", json={"text": "Biology"}, headers=learner["headers"])
    assert chosen.status_code == 200, chosen.text
    subject_id = chosen.json()["subject_id"]
    response = client.post(
        "/api/v1/tests/plan",
        json={"subject": "Biology", "subject_id": subject_id, "level": 2, "style": "try", "goal": "exam"},
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text
    return subject_id, response.json()


def _row(item, answer="not the authored answer"):
    return {
        "item_id": item["id"],
        "grading_seal": item["grading_seal"],
        "item_type": item["item_type"],
        "skill": item["skill"],
        "difficulty": item["difficulty"],
        "answer": answer,
        "correct": True,
        "ms": 25,
        "hints_used": 0,
        "parameters": item["parameters"],
    }


def test_partial_run_and_results_resume_from_the_subject_record(client, learner):
    subject_id, plan = _plan(client, learner)
    saved = client.get(f"/api/v1/tests/run?subject_id={subject_id}", headers=learner["headers"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["chosen"]["id"] == subject_id
    assert saved.json()["evidence"] == [] and saved.json()["step"] == "run"

    first = plan["items"][0]
    graded = client.post(
        "/api/v1/tests/grade",
        json={"run_id": plan["run_id"], "item_id": first["id"], "grading_seal": first["grading_seal"], "answer": "not the authored answer", "ms": 25},
        headers=learner["headers"],
    )
    assert graded.status_code == 200 and graded.json()["correct"] is False
    partial = client.get(f"/api/v1/tests/run?subject_id={subject_id}", headers=learner["headers"]).json()
    assert partial["step"] == "run" and partial["index"] == 1
    assert len(partial["evidence"]) == 1 and partial["evidence"][0]["correct"] is False

    final = client.post(
        "/api/v1/tests/evidence",
        json={"run_id": plan["run_id"], "subject_id": subject_id, "rows": [_row(item) for item in plan["items"]]},
        headers=learner["headers"],
    )
    assert final.status_code == 200, final.text
    assert final.json()["stored"] == len(plan["items"]) - 1
    complete = client.get(f"/api/v1/tests/run?subject_id={subject_id}", headers=learner["headers"]).json()
    assert complete["step"] == "results"
    assert len(complete["evidence"]) == len(plan["items"])
    assert [row["correct"] for row in complete["evidence"]] == [False] * len(plan["items"])
    repeat = client.post(
        "/api/v1/tests/evidence",
        json={"run_id": plan["run_id"], "subject_id": subject_id, "rows": [_row(item) for item in plan["items"]]},
        headers=learner["headers"],
    )
    assert repeat.json()["stored"] == 0


def test_an_incomplete_batch_cannot_mark_results(client, learner):
    subject_id, plan = _plan(client, learner)
    response = client.post(
        "/api/v1/tests/evidence",
        json={"run_id": plan["run_id"], "subject_id": subject_id, "rows": [_row(plan["items"][0])]},
        headers=learner["headers"],
    )
    assert response.status_code == 200 and response.json()["stored"] == 1
    saved = client.get(f"/api/v1/tests/run?subject_id={subject_id}", headers=learner["headers"]).json()
    assert saved["step"] == "run"
    assert len(saved["evidence"]) == 1
    assert saved["index"] == 1
