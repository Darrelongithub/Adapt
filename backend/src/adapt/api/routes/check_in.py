"""The 10-second check-in over HTTP (Round 9, Track M).

* ``POST   /api/v1/check-in``           — tiles in, one validated estimate out.
* ``GET    /api/v1/check-in``           — "what we recorded about you": the rows.
* ``GET    /api/v1/check-in/latest``    — the newest estimate (lesson shaping reads it server-side).
* ``GET    /api/v1/check-in/prompt``    — should this sitting be *asked*? (at most once per sitting)
* ``DELETE /api/v1/check-in/{id}``      — remove one.
* ``DELETE /api/v1/check-in``           — remove them all.

The response carries the estimate, what it will do to the next lesson
(the deterministic policy preview, in the learner's words), and — only
after a high-frustration check-in and only when the operator enabled it —
up to two general wellbeing reading links with attribution. Nothing here
names a model, a provider, or a score for the person: it is an estimate
about this sitting, with its evidence attached.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...ai.adaptation_policy import adaptation_policy
from ...ai.monitor.checkin import CheckInError
from ...ai.monitor import service as monitor
from ...db.models import Student
from ...services import current
from ..deps import db_session, rate, student_from_request

router = APIRouter(prefix="/api/v1/check-in", tags=["check-in"])


class CheckInBody(BaseModel):
    effort: int = Field(ge=1, le=9)
    emotion: str | None = Field(default=None, max_length=24)
    help: str | None = Field(default=None, max_length=24)
    note: str = Field(default="", max_length=280)
    context: str = Field(default="other", max_length=16)
    session_id: str | None = Field(default=None, max_length=120)
    subject_id: str | None = Field(default=None, max_length=120)


def _llm() -> Any:
    svcs = current()
    return getattr(getattr(svcs, "ai1", None), "llm", None) or getattr(getattr(svcs, "ai2", None), "llm", None)


def _view(row: Any, *, settings: Any = None, cache_session: Session | None = None) -> dict[str, Any]:
    out = row.as_dict()
    policy = adaptation_policy(out.get("estimate") or {})
    out["next_lesson"] = {
        "changed": policy.changed,
        "reason": policy.reason,
        "break_suggested": policy.break_suggested,
        "length": policy.length,
        "challenge_delta": policy.challenge_delta,
    }
    if settings is not None:
        links = monitor.wellbeing_reading(settings, out.get("estimate") or {}, cache_session=cache_session)
        if links:
            out["read_more"] = links
    return out


@router.post("")
def submit(
    body: CheckInBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    svcs = current()
    try:
        row = monitor.record(
            s,
            student_id=student.id,
            raw_tiles=body.model_dump(),
            llm=_llm(),
            state_store=getattr(svcs, "state", None),
        )
    except CheckInError as exc:
        raise HTTPException(status_code=422, detail={"error": "check_in_refused", "message": str(exc)}) from exc
    s.commit()
    return _view(row, settings=svcs.settings, cache_session=s)


@router.get("")
def mine(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    rows = monitor.list_mine(s, student.id)
    return {"check_ins": [r.as_dict() for r in rows], "count": len(rows)}


@router.get("/latest")
def latest(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    row = monitor.latest(s, student.id)
    return {"check_in": row.as_dict(include_note=False) if row else None}


@router.get("/prompt")
def prompt(
    session_id: str | None = None,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    return {"show": monitor.should_prompt(s, student.id, session_id=session_id)}


@router.delete("/{check_in_id}")
def remove_one(
    check_in_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    if not monitor.delete_one(s, student.id, check_in_id):
        raise HTTPException(status_code=404, detail={"error": "unknown_check_in", "message": "no such check-in"})
    s.commit()
    return {"deleted": 1}


@router.delete("")
def remove_all(
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    n = monitor.delete_all(s, student.id)
    s.commit()
    return {"deleted": n}


__all__ = ["router"]
