"""Regression: learner plans hide answer keys and only the server decides verdicts."""

from __future__ import annotations

import pytest

from adapt.db.models import new_id
from adapt.subjects.grading import (
    GRADING_FIELDS,
    issue_grading_token,
    public_item,
    read_grading_token,
    score_answer,
)
from adapt.subjects.test_items import validate_item


CASES = (
    ("pattern_pick", "patterns.sequence", {"sequence": "2,4,8,16", "options": "20,24,32,30", "answer_index": 2}, "32", "20"),
    ("drag_order", "ordering", {"items": "one,two,three", "answer": "one,two,three"}, "one>two>three", "three>two>one"),
    ("drag_match", "matching", {"left": "Cat,Dog", "right": "Meow,Woof", "pairs": "Cat:Meow,Dog:Woof"}, "Cat=Meow,Dog=Woof", "Cat=Woof,Dog=Meow"),
    ("odd_one_out", "classification", {"options": "square,circle,triangle,line", "answer_index": 1, "why": "Only the circle is round."}, "circle", "square"),
    ("truth_table", "logic", {"expression": "A and B", "columns": 2, "rows": 4, "target_cells": "0,1,1,0"}, "0,1,1,0", "0,0,0,0"),
    ("venn_region", "sets", {"sets": "A,B", "question": "Where do they overlap?", "answer_region": "AB"}, "AB", "A"),
    ("geometry_angle", "geometry.spatial", {"angle": 90, "kind": "right", "options": "15°,45°,90°,135°", "answer_index": 2}, "90°", "15°"),
    ("geometry_congruent", "geometry.spatial", {"target_sides": "30,50,40", "options": "20,25,30|30,40,50|30,50,80|10,20,30", "answer_index": 1}, "30,40,50", "20,25,30"),
    ("number_line", "number.sense", {"value": 50, "max": 100, "tolerance": 8}, "52", "90"),
    ("fraction_bar", "number.fractions", {"numerator": 1, "denominator": 4, "options": "1/2,1/3,1/4,1/5", "answer_index": 2}, "1/4", "1/2"),
    ("cloze_word_bank", "vocabulary", {"sentence": "She ___ yesterday.", "bank": "go,goes,went,gone", "answer_index": 2}, "went", "go"),
    ("quick_recall", "memory", {"shown": "red,green,blue", "choices": "red,green,blue,yellow", "answer_indices": "0,1,2", "seconds": 2}, "blue,red,green", "red,green,yellow"),
)


def _item(case):
    kind, skill, parameters, _right, _wrong = case
    item = validate_item({"item_type": kind, "skill": skill, "difficulty": 3, "parameters": parameters})
    assert item is not None
    return item


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
def test_server_scorer_distinguishes_authored_right_and_wrong_answers(case):
    item = _item(case)
    assert score_answer(item, case[3]) is True
    assert score_answer(item, case[4]) is False


@pytest.mark.parametrize("case", CASES, ids=lambda case: case[0])
def test_grade_endpoint_uses_server_item_not_client_verdict(case, client, learner):
    item = _item(case)
    for expected, answer in ((True, case[3]), (False, case[4])):
        run_id = new_id("trun")
        item_id = f"item-{expected}"
        token = issue_grading_token(item, student_id=learner["student_id"], run_id=run_id, item_id=item_id)
        response = client.post(
            "/api/v1/tests/grade",
            json={
                "run_id": run_id,
                "item_id": item_id,
                "grading_seal": token,
                "answer": answer,
                "correct": not expected,  # ignored: this is not part of the grading contract
                "ms": 1234,
                "hints_used": 1,
            },
            headers=learner["headers"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["correct"] is expected
        assert response.json()["stored"] == 1
        rows = client.get(f"/api/v1/tests/evidence?run_id={run_id}").json()["rows"]
        assert len(rows) == 1
        assert rows[0]["correct"] is expected
        assert rows[0]["answer"] == answer


def test_every_api_plan_hides_grading_fields_and_authenticates_tokens(client, learner):
    seen = set()
    for subject in ("geometry", "discrete maths", "general", "language", "law"):
        response = client.post(
            "/api/v1/tests/plan", json={"subject": subject, "level": 2}, headers=learner["headers"]
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"].startswith("trun_")
        for item in body["items"]:
            seen.add(item["item_type"])
            assert not (GRADING_FIELDS & set(item["parameters"]))
            assert item["id"] and item["grading_seal"]
            unwrapped = read_grading_token(
                item["grading_seal"],
                student_id=learner["student_id"],
                run_id=body["run_id"],
                item_id=item["id"],
            )
            assert unwrapped["item"]["item_type"] == item["item_type"]
            with pytest.raises(ValueError, match="does not belong"):
                read_grading_token(
                    item["grading_seal"], student_id="stu_someone_else", run_id=body["run_id"], item_id=item["id"]
                )
    assert seen == {case[0] for case in CASES}


def test_public_item_does_not_even_expose_the_authored_explanation(learner):
    item = _item(CASES[3])
    visible = public_item(item, student_id=learner["student_id"], run_id="trun_visible", item_id="item-0")
    assert "why" not in visible["parameters"]
    assert "answer_index" not in visible["parameters"]


def test_tampered_and_cross_learner_tokens_cannot_grade(client, learner):
    item = _item(CASES[0])
    run_id = "trun_token_guards"
    token = issue_grading_token(item, student_id=learner["student_id"], run_id=run_id, item_id="item-0")
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    refused = client.post(
        "/api/v1/tests/grade",
        json={"run_id": run_id, "item_id": "item-0", "grading_seal": tampered, "answer": "32"},
        headers=learner["headers"],
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "invalid_grading_token"

    other = client.post(
        "/api/v1/students", json={"display_name": "Other Learner", "as_guest": True}
    ).json()
    cross = client.post(
        "/api/v1/tests/grade",
        json={"run_id": run_id, "item_id": "item-0", "grading_seal": token, "answer": "32"},
        headers={"x-csrf-token": other["csrf_token"]},
    )
    assert cross.status_code == 422
    assert client.get(f"/api/v1/tests/evidence?run_id={run_id}").json()["rows"] == []


def test_fabricated_evidence_type_is_refused_and_never_stored(client, learner, services):
    from sqlalchemy import select

    from adapt.subjects.models import SubjectTestEvidence

    response = client.post(
        "/api/v1/tests/evidence",
        json={
            "run_id": "run-fabricated",
            "rows": [{
                "item_id": "made-up",
                "item_type": "made_up_type",
                "skill": "fabricated",
                "difficulty": 5,
                "answer": "anything",
                "correct": True,
                "grading_seal": "not-a-real-seal",
            }],
        },
        headers=learner["headers"],
    )
    assert response.status_code == 422
    with services.scope() as session:
        assert session.execute(select(SubjectTestEvidence).where(SubjectTestEvidence.run_id == "run-fabricated")).scalars().all() == []


def test_batch_evidence_ignores_forged_verdict_metadata_and_parameters(client, learner, services):
    from sqlalchemy import select

    from adapt.subjects.models import SubjectTestEvidence

    plan = client.post(
        "/api/v1/tests/plan", json={"subject": "geometry"}, headers=learner["headers"]
    ).json()
    item = plan["items"][0]
    response = client.post(
        "/api/v1/tests/evidence",
        json={
            "run_id": plan["run_id"],
            "rows": [{
                "item_id": item["id"],
                "grading_seal": item["grading_seal"],
                "item_type": "made_up_type",
                "skill": "fabricated.skill",
                "difficulty": 1,
                "answer": "definitely-not-one-of-the-options",
                "correct": True,
                "ms": 987,
                "hints_used": 2,
                "parameters": {"answer_index": 0, "answer": "fabricated"},
            }],
        },
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["stored"] == 1
    row = client.get(f"/api/v1/tests/evidence?run_id={plan['run_id']}").json()["rows"][0]
    assert row["item_type"] == item["item_type"]
    assert row["skill"] == item["skill"]
    assert row["difficulty"] == item["difficulty"]
    assert row["correct"] is False
    with services.scope() as session:
        stored = session.execute(
            select(SubjectTestEvidence).where(SubjectTestEvidence.run_id == plan["run_id"])
        ).scalar_one()
        assert stored.facts == {"parameters": item["parameters"]}
        assert not (GRADING_FIELDS & set(stored.facts["parameters"]))


def test_a_seal_cannot_be_moved_to_another_run_or_item(client, learner):
    plan = client.post("/api/v1/tests/plan", json={"subject": "general"}, headers=learner["headers"]).json()
    item = plan["items"][0]
    for run_id, item_id in (("trun_other_run", item["id"]), (plan["run_id"], "item-other")):
        response = client.post(
            "/api/v1/tests/evidence",
            json={
                "run_id": run_id,
                "rows": [{
                    "item_id": item_id,
                    "grading_seal": item["grading_seal"],
                    "item_type": item["item_type"],
                    "skill": item["skill"],
                    "difficulty": item["difficulty"],
                    "answer": "0",
                }],
            },
            headers=learner["headers"],
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_grading_seal"
