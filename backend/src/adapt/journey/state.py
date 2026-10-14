"""Reading and writing the path's state, server-side.

The API surface is two calls (frozen in the shared header):

* :func:`payload` — ``GET /api/v1/journey`` (and ``GET /api/v1/journey/state``)
  answers ``{stage, stages: [{id, status}]}``.
* :func:`advance` — ``POST /api/v1/journey/advance`` records the learner's own
  answer about one stage, ``done`` or ``skipped``.

Everything else is derivation, and the derivation is deliberately boring: one query
per stage, answered from the row that proves the work happened. Where a stored answer
and the evidence disagree, **the evidence wins** — a learner who skipped the games and
then played them has played them, and a status that kept saying "skipped" would be the
app arguing with its own database.

Two rules the rest of the app depends on:

1. **Skipped means not measured.** A skipped stage contributes no value anywhere; the
   snapshot, the subject map and the profile already phrase an absent measurement as
   "not measured" and this module does not add a zero to any of them.
2. **Resumable from any device.** The state lives on the learner's row, so the same
   browser, a second laptop and the phone all read the same answer. Nothing in this
   module reads a cookie, a session or a client-supplied cursor.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Attempt,
    BaselineRun,
    LearningSession,
    LessonRun,
    MindSnapshot,
    Student,
    utcnow,
)
from ..subjects.models import SubjectRound
from .models import JourneyStage
from .stages import SETTABLE_STATUSES, STAGE_IDS, first_todo


class JourneyError(ValueError):
    """A caller asked for a stage or a status the path does not have."""


#: Session modes that are measurement rather than practice. An attempt inside one of
#: these is placement evidence and must not count as "has started practising" --
#: otherwise the path would tick its last stage on the strength of the test that
#: stands in front of it.
_PLACEMENT_MODES = ("placement", "diagnostic", "assessment")


def _derived(s: Session, student_id: str) -> dict[str, bool]:
    """Which stages the evidence says are done. One query per stage, none of them clever."""
    has_student = s.get(Student, student_id) is not None

    # Whole rows rather than a column projection: ``completion`` is an encrypted
    # column, and reading it through the model is the path that decrypts it.
    runs = (
        s.execute(select(BaselineRun).where(BaselineRun.student_id == student_id)).scalars().all()
    )
    questions_done = any(bool((run.completion or {}).get("questions")) for run in runs)
    games_done = any(str(run.status) == "complete" for run in runs)

    rounds = s.execute(
        select(SubjectRound.status).where(SubjectRound.student_id == student_id)
    ).scalars().all()
    subject_chosen = bool(rounds)
    round_done = any(str(status) == "complete" for status in rounds)

    snapshot_done = (
        s.execute(
            select(MindSnapshot.id).where(MindSnapshot.student_id == student_id).limit(1)
        ).first()
        is not None
    )

    lesson_run = s.execute(
        select(LessonRun.id).where(LessonRun.student_id == student_id).limit(1)
    ).first()
    lesson_session = s.execute(
        select(LearningSession.id)
        .where(LearningSession.student_id == student_id, LearningSession.mode == "lesson")
        .limit(1)
    ).first()
    first_lesson = lesson_run is not None or lesson_session is not None

    # A scored answer outside placement. A blank (``correct IS NULL``) is not a practice
    # answer: the subject round stores skipped items that way, and so does the baseline.
    practice = (
        s.execute(
            select(Attempt.id)
            .join(LearningSession, LearningSession.id == Attempt.session_id)
            .where(
                Attempt.student_id == student_id,
                Attempt.correct.is_not(None),
                LearningSession.mode.not_in(_PLACEMENT_MODES),
            )
            .limit(1)
        ).first()
        is not None
    )

    return {
        "account": has_student,
        "preferences": questions_done,
        "games": games_done,
        "subject": subject_chosen,
        "subject_round": round_done,
        "snapshot": snapshot_done,
        "first_lesson": first_lesson,
        "practice": practice,
    }


def _stored(s: Session, student_id: str) -> dict[str, JourneyStage]:
    rows = (
        s.execute(select(JourneyStage).where(JourneyStage.student_id == student_id))
        .scalars()
        .all()
    )
    return {row.stage: row for row in rows}


def statuses(s: Session, student_id: str) -> list[dict[str, str]]:
    """Every stage with its status, in path order.

    ``status`` is one of ``todo|done|skipped`` (frozen). ``evidence`` is an extra key
    that says *why* a stage is marked done, so a screen (or an operator reading the API)
    can tell "you answered the questions" from "you told us you had": the frozen
    statuses alone cannot express that, and guessing would be exactly the kind of
    invented claim this app refuses.
    """
    derived = _derived(s, student_id)
    stored = _stored(s, student_id)
    out: list[dict[str, str]] = []
    for stage in STAGE_IDS:
        row = stored.get(stage)
        declared = row.status if row is not None else ""
        stored_status = declared if declared in SETTABLE_STATUSES else "todo"
        if derived.get(stage):
            status = "done"
            evidence = "recorded"
        elif stored_status != "todo":
            status = stored_status
            # `stated` for a stage the learner marked themselves; the run, the round
            # or the lesson that would have proved it is simply absent.
            evidence = "stated"
        else:
            status = "todo"
            evidence = "none"
        out.append({"id": stage, "status": status, "evidence": evidence})
    return out


def payload(s: Session, student_id: str) -> dict[str, Any]:
    """The frozen shape, plus the counts a progress bar needs.

    ``stage`` is where the learner is: the first stage still to do. When everything is
    done or skipped it is the last stage, so the answer is never empty.
    """
    rows = statuses(s, student_id)
    by_id = {row["id"]: row["status"] for row in rows}
    done = sum(1 for row in rows if row["status"] == "done")
    skipped = sum(1 for row in rows if row["status"] == "skipped")
    stored_at = None
    for row in _stored(s, student_id).values():
        stamp = row.updated_at or row.created_at
        if stamp is not None and (stored_at is None or stamp > stored_at):
            stored_at = stamp
    return {
        "student_id": student_id,
        "stage": first_todo(by_id),
        "stages": rows,
        "progress": {
            "done": done,
            "skipped": skipped,
            "todo": len(rows) - done - skipped,
            "total": len(rows),
            "percent": round((done + skipped) / max(1, len(rows)) * 100),
        },
        # Which stages were waved through, in the words the rest of the app uses for an
        # absent measurement. A skipped stage is not a zero anywhere downstream.
        "not_measured": [row["id"] for row in rows if row["status"] == "skipped"],
        "updated_at": stored_at.isoformat() if stored_at else None,
        "generated_at": utcnow().isoformat(),
    }


def advance(
    s: Session,
    student_id: str,
    *,
    stage: str,
    status: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Record the learner's own answer about one stage, and return the new state.

    Only ``done`` and ``skipped`` can be set. Unknown stages and unknown statuses are
    refused with :class:`JourneyError` rather than quietly ignored: a client that
    misspells ``first_lesson`` is a client whose learner thinks they moved on when they
    did not, and a silent 200 would hide that until somebody asks why the path never
    advanced.
    """
    if stage not in STAGE_IDS:
        raise JourneyError(f"unknown stage: {stage!r}")
    if status not in SETTABLE_STATUSES:
        allowed = " or ".join(SETTABLE_STATUSES)
        raise JourneyError(f"a stage can be marked {allowed}, not {status!r}")
    cleaned = " ".join(str(note or "").split())[:200] or None
    row = s.execute(
        select(JourneyStage).where(
            JourneyStage.student_id == student_id, JourneyStage.stage == stage
        )
    ).scalar_one_or_none()
    if row is None:
        row = JourneyStage(student_id=student_id, stage=stage, status=status, note=cleaned)
        s.add(row)
    else:
        row.status = status
        row.note = cleaned
    s.commit()
    return payload(s, student_id)


__all__ = ["JourneyError", "advance", "payload", "statuses"]
