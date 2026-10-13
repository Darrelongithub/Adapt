"""Evidence surfaces (Phase 6, brief §9).

Three reads and one loop, all honest in the same four ways:

* **Actual vs expected, per method, accumulated across topics.** Raw accuracy
  is deliberately absent — a method that met harder material is not punished
  for it. ``?topic_id=`` narrows the same computation to one topic; with no
  topic the accumulation is topic-agnostic, which is what makes "all topics"
  a real value instead of a promise.
* **Weights next to the numbers.** Assigned (controlled) evidence weighs 1.0;
  what the learner selected into weighs 0.4. Every method row says how much
  of its count is which, and shows the raw ``n`` beside the effective one.
* **Self-selection detectors stay, labelled.** They are returned as
  observational, weight 0.4, ``is_proof: false`` — they can inform and cannot
  decide, and the payload refuses to let a screen forget it.
* **Strategy posteriors, not draws.** Thompson sampling picks behind the
  scenes (seeded, so a demo can reproduce the draw); what is shown is the
  posterior mean with its interval and ``n``.

``POST /failure-loop`` runs :func:`adapt.learning.failure.run_failure_loop`
for the signed-in learner: prerequisite check by question, the brief's
fatigue rule, a next-best-method reteach with a matched same-format
question, and the weighted update rule — recorded on the evidence log as a
controlled event carrying the attribution weight.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("adapt.routes.evidence")

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, rate, student_from_request
from ...baseline import factors as FACTORS
from ...db.models import Attempt, Item, Student, TopicPrerequisite
from ...learning import failure as F
from ...learning import methods as M
from ...learning.seeds import stable_seed
from ...services import attempts as A
from ...services import current

router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])

#: Method -> the baseline's stated-format vocabulary (§5.1 step 2). Retrieval
#: has no stated-format counterpart and is honestly absent from the comparison.
_METHOD_TO_FORMAT = {"visual": "diagram", "explanation": "text", "worked_example": "worked_example"}


def _attempt_rows(s: Session, student_id: str, topic_id: str | None = None, limit: int = 400) -> list[dict[str, Any]]:
    conds = [Attempt.student_id == student_id]
    if topic_id:
        conds.append(Attempt.topic_id == topic_id)
    rows = s.execute(select(Attempt).where(*conds).order_by(Attempt.submitted_at.desc()).limit(limit)).scalars().all()
    return M.rows_from_attempts(rows)


def _preference_payload(s: Session, student_id: str) -> dict[str, Any]:
    prefs = current().state.preferences(student_id, session=s)
    rows = _attempt_rows(s, student_id)
    by_format: dict[str, dict[str, int]] = {}
    for row in rows:
        fmt = _METHOD_TO_FORMAT.get(str(row.get("method") or ""))
        if not fmt:
            continue
        bucket = by_format.setdefault(fmt, {"n": 0, "correct": 0})
        bucket["n"] += 1
        bucket["correct"] += 1 if row.get("correct") else 0
    row = FACTORS.preference_vs_performance({}, prefs, outcomes={"by_format": by_format})
    if row is None:
        return {
            "available": False,
            "stated": prefs.get("preferred_format"),
            "status": "insufficient evidence",
            "note": "not enough measured answers in the formats you have been shown to compare with what you picked — no verdict is invented",
        }
    return {"available": True, **row}


@router.get("/methods")
def evidence_methods(
    topic_id: str | None = None,
    seed: int = 20260101,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """Actual vs expected per method, with weights, posteriors and detectors.

    Topic-agnostic unless ``topic_id`` is given: the same rows, accumulated
    across every topic, are what "all topics" means here.
    """
    rows = _attempt_rows(s, student.id, topic_id)
    report = M.method_report(rows)
    weights = {w["method"]: w for w in F.weighted_method_summary(rows)}
    methods = []
    for entry in report["methods"]:
        merged = {**entry, **{k: v for k, v in weights.get(str(entry["method"]), {}).items() if k != "method"}}
        methods.append(merged)
    attempt_dicts = A.attempts_for(s, student.id)
    return {
        "topic_id": topic_id,
        "scope": "one topic" if topic_id else "all topics",
        "methods": methods,
        "strategies": F.strategy_posteriors(rows, seed=seed),
        "detectors": F.self_selection_detectors(attempt_dicts),
        "preference_vs_performance": _preference_payload(s, student.id),
        "note": (
            "actual minus expected per method: expected is what the model predicted for that exact question "
            "before it was served, so harder material does not punish a method. Assigned evidence weighs 1.0; "
            "self-selected observations weigh 0.4 and are labelled observational wherever they appear."
        ),
    }


@router.get("/preference-vs-performance")
def preference_vs_performance(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """What the learner said (baseline), next to what was measured (§9.7)."""
    return _preference_payload(s, student.id)


class FailureLoopBody(BaseModel):
    topic_id: str = Field(min_length=1, max_length=128)
    item_id: str = Field(min_length=1, max_length=128)
    method: str | None = Field(default=None, max_length=32)
    expected_success: float | None = Field(default=None, ge=0, le=1)
    prereq_correct: bool | None = None
    matched_correct: bool | None = None
    matched_expected: float | None = Field(default=None, ge=0, le=1)
    session_latencies_ms: list[float | None] = Field(default_factory=list, max_length=60)
    session_correct: list[bool | None] = Field(default_factory=list, max_length=60)


@router.post("/failure-loop")
def failure_loop(
    body: FailureLoopBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Run the "why did I fail" loop for one failed attempt (§9.5)."""
    item = s.execute(select(Item).where(Item.id == body.item_id)).scalar_one_or_none()
    if item is None or item.topic_id != body.topic_id:
        raise HTTPException(status_code=404, detail={"error": "item_not_found", "message": "that question is not in this topic"})
    candidates = [
        {"id": it.id, "topic_id": it.topic_id, "kind": it.kind, "difficulty": it.difficulty, "active": bool(it.active)}
        for it in s.execute(select(Item).where(Item.topic_id == body.topic_id, Item.active.is_(True))).scalars().all()
    ]
    prereq_ids = [
        row.prerequisite_id
        for row in s.execute(select(TopicPrerequisite).where(TopicPrerequisite.topic_id == body.topic_id)).scalars().all()
    ]
    prereq_candidates: dict[str, list[dict[str, Any]]] = {}
    for pid in prereq_ids:
        prereq_candidates[pid] = [
            {"id": it.id, "topic_id": it.topic_id, "kind": it.kind, "difficulty": it.difficulty, "active": bool(it.active)}
            for it in s.execute(select(Item).where(Item.topic_id == pid, Item.active.is_(True))).scalars().all()
        ]
    rows = _attempt_rows(s, student.id)
    latencies: list[float | None] = list(body.session_latencies_ms)
    correctness: list[bool | None] = list(body.session_correct)
    if not latencies:
        recent = s.execute(
            select(Attempt).where(Attempt.student_id == student.id).order_by(Attempt.submitted_at.desc()).limit(12)
        ).scalars().all()
        latencies = [float(a.time_to_first_ms or a.time_on_item_ms) if (a.time_to_first_ms or a.time_on_item_ms) else None for a in reversed(recent)]
        correctness = [a.correct for a in reversed(recent)]

    result = F.run_failure_loop(
        learner_id=student.id,
        topic_id=body.topic_id,
        first_item={"id": item.id, "topic_id": item.topic_id, "kind": item.kind, "difficulty": item.difficulty},
        first_method=body.method,
        first_expected=body.expected_success,
        candidates=candidates,
        prereqs={body.topic_id: prereq_ids},
        prereq_candidates=prereq_candidates,
        prereq_correct=body.prereq_correct,
        session_latencies_ms=latencies,
        session_correct=correctness,
        method_rows=rows,
        matched_correct=body.matched_correct,
        matched_expected=body.matched_expected,
    )
    event_id = f"evf_{stable_seed(student.id, body.item_id, str(body.prereq_correct), str(body.matched_correct), 'failure-loop')}"
    recorded = True
    try:
        F.record_failure_outcome(current().state, student.id, result, event_id=event_id)
    except Exception:
        recorded = False
        log.exception("evidence: failure-loop outcome for %s was computed but not recorded", student.id)
    return {**result, "recorded": recorded, "event_id": event_id}
