"""Importing a Numbas package that did not come from ADAPT's own export.

The round-trip test in ``test_api_learner_loop.py`` covers "we export, they edit, we
import" -- and only that, because our export carries the originating item id and the
topic id, so identity and placement are both handed back to us. A package from the
Numbas editor carries neither. That is the case two defects lived in:

* the tag convention ``adapt-topic:<...>`` was read as a topic *id*, so a teacher tagging
  a question with the human-readable code (or tagging it not at all) hit a foreign-key
  error -- a 500 for a bad tag, with the SQL statement in the local-mode response;
* a part type this build has no grader for was imported as free text and marked every
  answer wrong, including the teacher's own, while the report called it runnable.

Both are the same question: what does the app do with a question it cannot faithfully
place or mark? The answer now is: place it only on a topic that exists, never serve what
nothing can grade, and say so.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from adapt.assessment import numbas as NB
from adapt.assessment.grading import grade
from adapt.db.models import Item, Topic

# A Numbas editor export shape: exam -> questions -> metadata.question -> parts.
FACTORISE_STEM = "<p>Simplify $x^2+3x$</p>"


def _exam(*, tag: str | None, part_type: str = "custom", answer: str = "x*(x+3)", uuid: str) -> dict:
    tags = [tag] if tag else []
    return {
        "name": "Teacher exam",
        "questions": [
            {
                "name": "Factorise",
                "uuid": uuid,
                "tags": tags,
                "metadata": {
                    "question": {
                        "question": {"html": FACTORISE_STEM},
                        "parts": [{"type": part_type, "answer": answer}],
                        "advice": {"html": "<p>Factorise.</p>"},
                    }
                },
            }
        ],
    }


def _post(client, learner, payload: dict):
    return client.post(
        "/api/v1/numbas/import",
        content=json.dumps(payload).encode(),
        headers={**learner["headers"], "content-type": "application/json"},
    )


def _topic(services) -> tuple[str, str]:
    with services.scope() as s:
        topic = s.execute(select(Topic).order_by(Topic.code)).scalars().first()
        return topic.code, topic.id


def _imported(services) -> list[Item]:
    with services.scope() as s:
        return list(s.execute(select(Item).where(Item.source == "numbas")).scalars().all())


def test_a_question_tagged_with_a_topic_code_imports_onto_that_topic(client, learner, services):
    code, topic_id = _topic(services)
    response = _post(client, learner, _exam(tag=f"adapt-topic:{code}", uuid="q-tagged-code-1"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] + body["updated"] == 1, body
    # The tag names the topic the way a human writes it; the row needs the id.
    row = next(r for r in _imported(services) if r.numbas_ref == "q-tagged-code-1")
    assert row.topic_id == topic_id, f"stored onto {row.topic_id!r} instead of {topic_id!r}"


def test_an_untagged_question_is_skipped_with_the_valid_codes_not_a_500(client, learner, services):
    code, _ = _topic(services)
    response = _post(client, learner, _exam(tag=None, uuid="q-untagged-1"))
    assert response.status_code == 200, f"an untagged question answered {response.status_code}: {response.text[:200]}"
    body = response.json()
    assert body["imported"] == 0 and body["updated"] == 0
    warnings = " ".join(body["report"]["warnings"])
    assert "not a topic here" in warnings and code in warnings, warnings
    assert not [r for r in _imported(services) if r.numbas_ref == "q-untagged-1"], (
        "an unplaced item must not be attached to whichever topic came first"
    )


def test_a_part_type_this_build_cannot_grade_is_imported_inactive_and_never_served(client, learner, services):
    code, _ = _topic(services)
    response = _post(client, learner, _exam(tag=f"adapt-topic:{code}", uuid="q-ungradeable-1"))
    assert response.status_code == 200, response.text
    warnings = " ".join(response.json()["report"]["warnings"])
    assert "needs the hosted Numbas runtime" in warnings, warnings

    row = next(r for r in _imported(services) if r.numbas_ref == "q-ungradeable-1")
    assert row.active is False, "a question nothing can grade must not reach a learner"

    # ...and prove it: the practice queue must not contain it.
    opened = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()
    assert opened["session_id"].startswith("lrs_")
    served: set[str] = set()
    for i in range(6):
        plan = client.get(f"/api/v1/practice?count=6&i={i}", headers=learner["headers"]).json()
        served |= {entry["item"]["id"] for entry in (plan.get("items") or [])}
    assert row.id not in served, "an ungradeable item was served to the learner"


def test_an_imported_question_can_be_answered_scored_and_returned(client, learner, services):
    """The half of the Numbas story that is not the file format: submit, score, return.

    An imported question is only useful if the loop that follows accepts it like any
    other item -- practice serves it, an answer is marked, the score comes back with
    feedback, and the attempt lands in the learner's own record.
    """
    code, _ = _topic(services)
    response = _post(
        client, learner, _exam(tag=f"adapt-topic:{code}", part_type="n", answer="7", uuid="q-numbas-numeric-1")
    )
    assert response.status_code == 200, response.text
    row = next(r for r in _imported(services) if r.numbas_ref == "q-numbas-numeric-1")
    assert row.kind == "numeric" and row.active is True, f"kind={row.kind} active={row.active}"
    # A Numbas numeric part carries its answer as a JME expression, which the importer
    # keeps as a formula; either shape has to end up gradeable, which the attempts below
    # prove rather than a key-by-key assertion here.
    assert row.answer_spec, "the imported question arrived with nothing to grade against"

    session = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()
    sid = session["session_id"]
    right = client.post(
        "/api/v1/attempts",
        json={"session_id": sid, "item_id": row.id, "answer": 7, "attempt_no": 1},
        headers=learner["headers"],
    )
    assert right.status_code == 200, right.text
    marked = right.json()
    assert marked["correct"] is True and marked["score"] == 1.0, marked
    assert marked["grading_method"] == "numeric", marked["grading_method"]
    assert marked["feedback"], "a marked answer has to come back with something to read"

    wrong = client.post(
        "/api/v1/attempts",
        json={"session_id": sid, "item_id": row.id, "answer": 8, "attempt_no": 2},
        headers=learner["headers"],
    )
    assert wrong.status_code == 200, wrong.text
    assert wrong.json()["correct"] is False and wrong.json()["score"] == 0.0

    # ...and it returns: the two attempts are in the session's own log, in order.
    events = client.get(f"/api/v1/sessions/{sid}/events", headers=learner["headers"]).json()["events"]
    kinds = [e["type"] for e in events]
    assert kinds.count("answer_submitted") >= 2, kinds
    submitted = [e for e in events if e["type"] == "answer_submitted"]
    assert submitted[0].get("item_id") == row.id, (
        f"the returned event has to name the question that was answered: {submitted[0]}"
    )
    assert submitted[0]["payload"].get("correct") is True, submitted[0]


def test_the_export_report_counts_an_inactive_item_as_needing_the_runtime(client, learner, services, items):
    exported = client.get("/api/v1/numbas/export", headers=learner["headers"])
    assert exported.status_code == 200, exported.text
    runnable = exported.json()["runnable"]
    assert runnable["runnable_in_app"], "the bank itself has to stay runnable in app"
    with services.scope() as s:
        from adapt.db.models import Item as _Item

        inactive = [r.numbas_ref for r in s.execute(select(_Item).where(_Item.active.is_(False))).scalars().all()]
    for ref in inactive:
        if ref:
            assert ref not in runnable["runnable_in_app"], f"{ref} is inactive but reported runnable"


def test_nothing_to_grade_against_says_so_instead_of_marking_the_right_answer_wrong():
    """The grader is the last line of defence: no spec means no verdict, not "wrong"."""
    graded = grade({"kind": "text", "answer_spec": {"formula": "x*(x+3)"}}, "x*(x+3)")
    assert graded.correct is False, "still refuses to pass it"
    assert graded.method == "none", graded.method
    assert "Numbas runtime" in graded.detail, graded.detail
    assert "expected one of: " not in graded.detail, "the old detail was an empty sentence"


def test_supported_subset_does_not_call_an_ungradeable_item_runnable():
    subset = NB.supported_subset(
        [
            {"kind": "text", "numbas_ref": "ok-1", "needs_numbas_runtime": False, "active": True},
            {"kind": "text", "numbas_ref": "needs-1", "needs_numbas_runtime": True, "active": False},
            {"kind": "text", "numbas_ref": "inactive-1", "active": False},
            {"kind": "mcq", "numbas_ref": "ok-2", "active": True},
        ]
    )
    assert subset["runnable_in_app"] == ["ok-1", "ok-2"], subset
    assert subset["needs_numbas_runtime"] == ["needs-1", "inactive-1"], subset
