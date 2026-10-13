"""Seed the pre-demo pilot, and every number that comes out of it says so.

Why this exists, in the order the problem appeared:

1. ``evaluate_change`` needs five scored attempts on each side of a change before it
   will conclude anything — which is *correct*, and which made a live demo announce
   "insufficient evidence" at the moment it was supposed to demonstrate the loop.
2. The temptation is to lower the bar. The bar is the point: below it, a difference is
   noise dressed up as a result.
3. So the data has to exist before the demo. This module writes real ``Attempt`` rows —
   through the same columns the app reads, so nothing downstream is a special case —
   and flags every one of them ``pilot``. The label travels with the numbers: the
   effect report carries ``pilot: true`` and ``label: "pilot data — not evidence of
   efficacy"``, and the UI shows it next to the number it describes.

Run it, in order of preference:

* ``adapt pilot --student-id <id>`` after actually rehearsing with a few people (the
  rows then come from real answers, and this command is not needed at all); or
* ``adapt pilot --student-id <id> --seed`` before the demo, which writes a deterministic
  set of rows so the loop has something to show — labelled, in the payload and on the
  screen, because that is the difference between a demonstration and a false claim.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Attempt, Item, LearningSession, Student, Topic, utcnow
from ..learning import pilot as P


def seed(
    s: Session,
    student_id: str,
    *,
    specs: Sequence[P.PilotSpec] | None = None,
    start: datetime | None = None,
) -> dict[str, Any]:
    """Write the pilot rows (idempotent: re-running replaces its own rows).

    The rows land in one finished session with ``mode="pilot"`` so they are visible as
    what they are in ``/journey`` and in the session list, and each ``Attempt`` carries
    ``pilot=True``. An item and a topic are used if they exist — the *method* is the
    variable, so the item does not need to match it — and a missing curriculum is a
    sentence, not a crash.
    """
    specs = list(specs or P.DEFAULT_PILOT)
    student = s.get(Student, student_id)
    if student is None:
        return {"seeded": 0, "reason": "unknown student"}
    topic = s.execute(select(Topic).order_by(Topic.difficulty)).scalars().first()
    if topic is None:
        return {"seeded": 0, "reason": "no curriculum: run the app once, or `adapt seed`"}
    item = s.execute(select(Item).where(Item.topic_id == topic.id).limit(1)).scalars().first()
    item_id = item.id if item is not None else None

    # Re-running must not double the pilot: the previous rows and pilot sessions are
    # removed first, which is safe because they are flagged as what they are.
    previous = s.execute(select(Attempt).where(Attempt.student_id == student_id, Attempt.pilot.is_(True))).scalars().all()
    for prev_attempt in previous:
        s.delete(prev_attempt)
    previous_sessions = (
        s.execute(
            select(LearningSession).where(
                LearningSession.student_id == student_id,
                LearningSession.mode == "pilot",
            )
        )
        .scalars()
        .all()
    )
    for prev_session in previous_sessions:
        s.delete(prev_session)
    s.flush()

    started = (start or (utcnow() - timedelta(days=2)))
    session = LearningSession(
        student_id=student_id,
        mode="pilot",
        status="finished",
        started_at=started,
        ended_at=started + timedelta(hours=1),
    )
    s.add(session)
    s.flush()

    rows = P.seed_pilot_rows(
        student_id=student_id,
        specs=[P.PilotSpec(**{**spec.__dict__, "topic_id": topic.id, "item_id": item_id or "itm_missing"}) for spec in specs],
        start=started,
    )
    for entry in rows:
        s.add(
            Attempt(
                session_id=session.id,
                student_id=student_id,
                item_id=entry["item_id"],
                topic_id=entry["topic_id"],
                attempt_no=1,
                correct=bool(entry["correct"]),
                score=float(entry["score"]),
                graded_by="rules",
                submitted_at=entry["submitted_at"],
                method=entry["method"],
                question_kind=entry["question_kind"],
                expected_success=float(entry["expected_success"]),
                mapping_design=entry["mapping_design"],
                pilot=True,
            )
        )
    s.commit()
    return {
        "seeded": len(rows),
        "session_id": session.id,
        "topic_id": topic.id,
        "item_id": item_id,
        "methods": sorted({spec.method for spec in specs}),
        "label": P.PILOT_LABEL,
        "note": P.PILOT_NOTE,
    }


def remove(s: Session, student_id: str) -> dict[str, Any]:
    """Take the pilot rows back out (the demo is over; the claim stops here)."""
    rows = s.execute(select(Attempt).where(Attempt.student_id == student_id, Attempt.pilot.is_(True))).scalars().all()
    n = len(rows)
    for row in rows:
        s.delete(row)
    pilot_sessions = (
        s.execute(
            select(LearningSession).where(
                LearningSession.student_id == student_id,
                LearningSession.mode == "pilot",
            )
        )
        .scalars()
        .all()
    )
    for sess in pilot_sessions:
        s.delete(sess)
    s.commit()
    return {"removed": n}
