"""The pre-demo pilot: real rows, and every number derived from them says so.

The defect this covers is not a crash — it is a live demonstration saying
"insufficient evidence" at the moment it is supposed to show the loop, and the
pressure that puts on whoever is holding the demo. The pilot is the way out:
rows written before the demo, flagged ``pilot``, and a label that travels with
every figure derived from them.

These tests pin the two halves: the rows are ordinary ``Attempt`` rows (so nothing
downstream is a special case), and they are *labelled* (so nothing downstream can
present them as a study).
"""

from __future__ import annotations

from sqlalchemy import select

from adapt.db.models import Attempt
from adapt.learning import effect, pilot as P
from adapt.services import pilot as PS


def _attempts(services, student_id: str) -> list[Attempt]:
    with services.scope() as s:
        return list(s.execute(select(Attempt).where(Attempt.student_id == student_id)).scalars().all())


def test_pilot_rows_are_real_attempt_rows_and_flagged(services, learner):
    student_id = learner["student_id"]

    with services.scope() as s:
        report = PS.seed(s, student_id)

    assert report["seeded"] == sum(spec.n for spec in P.DEFAULT_PILOT) == 14
    rows = _attempts(services, student_id)
    assert len(rows) == 14
    assert all(r.pilot for r in rows)
    # Ordinary columns, readable by every existing consumer: the report is not a special case.
    assert {r.method for r in rows} == {"worked_example", "explanation"}
    assert all(r.expected_success is not None for r in rows)
    assert all(r.graded_by == "rules" for r in rows)
    assert {r.question_kind for r in rows} == {"mcq"}

    with services.scope() as s:
        session = s.get(__import__("adapt.db.models", fromlist=["LearningSession"]).LearningSession, report["session_id"])
        assert session.mode == "pilot"
        assert session.status == "finished"


def test_pilot_rows_appear_in_the_method_report_with_their_n(services, learner, client):
    student_id = learner["student_id"]
    with services.scope() as s:
        PS.seed(s, student_id)

    body = client.get("/api/v1/methods/report", headers=learner["headers"]).json()
    by_method = {entry["method"]: entry for entry in body["methods"]}
    assert set(by_method) == {"worked_example", "explanation"}, by_method
    for entry in by_method.values():
        assert entry["n"] == 7  # never rounded away: the sample size is part of the number
        assert entry["evidence"] == "early_signal"
        assert "early signal" in entry["note"]
    assert body["min_n_for_a_finding"] == 8


def test_reseeding_replaces_its_own_rows_instead_of_doubling_them(services, learner):
    student_id = learner["student_id"]
    with services.scope() as s:
        first = PS.seed(s, student_id)
        second = PS.seed(s, student_id)
    assert first["session_id"] != second["session_id"]
    assert len(_attempts(services, student_id)) == 14

    with services.scope() as s:
        out = PS.remove(s, student_id)
    assert out["removed"] == 14
    assert _attempts(services, student_id) == []


def test_every_pilot_figure_carries_the_label(services, learner):
    student_id = learner["student_id"]
    with services.scope() as s:
        PS.seed(s, student_id)
        rows = [
            {
                "method": a.method,
                "correct": a.correct,
                "expected_success": a.expected_success,
                "pilot": a.pilot,
            }
            for a in s.execute(select(Attempt).where(Attempt.student_id == student_id)).scalars().all()
        ]

    assert all(row["pilot"] for row in rows)
    # The label is applied by the same function the payloads use, so this is the string a
    # learner would actually read next to the number.
    assert P.label(pilot=True) == P.PILOT_LABEL
    assert "not evidence of efficacy" in P.PILOT_LABEL
    assert P.label(pilot=False, base="measured over 12 answers") == "measured over 12 answers"

    # And the effect check itself refuses to be fooled by the flag: it still needs five per
    # group, so a short pilot leaves it saying so rather than concluding.
    tiny = effect.evaluate_change([1.0, 1.0], [0.0, 0.0], metric="accuracy", pilot=True)
    assert tiny.verdict == "insufficient_evidence"
    assert tiny.pilot is True
    assert P.PILOT_LABEL in tiny.label or "pilot" in tiny.label.lower()

    # With five a side it concludes — and the conclusion is still labelled as pilot data.
    enough = effect.evaluate_change([0.0] * 5, [1.0] * 5, metric="accuracy", pilot=True)
    assert enough.verdict == "supported"
    assert enough.pilot is True
