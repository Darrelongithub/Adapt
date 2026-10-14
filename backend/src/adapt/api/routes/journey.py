"""The journey over HTTP: where the learner is, and their answer about a stage.

Two routes, both under the path's own prefix:

* ``GET  /api/v1/journey/state`` — the frozen ``{stage, stages: [{id, status}]}`` shape,
  plus the counts a progress bar needs. This is the *light* read: no timeline, no
  profile, one request that a phone on a slow connection can afford on every screen.
* ``POST /api/v1/journey/advance`` — ``{stage, status}``, ``status`` being ``done`` or
  ``skipped``. Returns the same payload, so a client never has to guess what its own
  write did.

The full timeline that already lived at ``GET /api/v1/journey`` keeps its nodes and
gains the same ``stage``/``stages`` keys, so the frozen contract holds on the path the
header names *and* the progress screen keeps the record it renders. That route is
registered in ``routes/learning.py``; the state itself is built here.

Auth: both routes act on the signed-in learner and nobody else. There is no "student id"
parameter to pass, which is the point -- a path that could be read for somebody else is a
path that could be advanced for somebody else.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db.models import Student
from ...journey import state as J
from ...journey.stages import SETTABLE_STATUSES, STAGE_IDS
from ..deps import db_session, rate, student_from_request

router = APIRouter(prefix="/api/v1/journey", tags=["journey"])


class AdvanceBody(BaseModel):
    stage: str = Field(min_length=1, max_length=32)
    #: ``done`` or ``skipped``. Validated against the frozen list in the service as well,
    #: so the rule lives in one place even if a second caller ever appears.
    status: str = Field(min_length=1, max_length=16)
    #: Optional, learner-written, and only ever shown back to the learner.
    note: str | None = Field(default=None, max_length=200)


@router.get("/state")
def journey_state(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """Where this learner is on the path, derived from their own rows."""
    return J.payload(s, student.id)


@router.post("/advance")
def journey_advance(
    body: AdvanceBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Record the learner's own answer about a stage: done, or skipped.

    A skipped stage is stored as skipped and reported as *not measured*; nothing here
    gives it a value, and nothing downstream will turn it into a zero.
    """
    if body.stage not in STAGE_IDS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unknown_stage",
                "note": f"the path has {len(STAGE_IDS)} stages and this is not one of them",
                "stages": list(STAGE_IDS),
            },
        )
    if body.status not in SETTABLE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unknown_status",
                # Plain wording: this text can reach a learner through the API error surface.
                "note": f"a stage can be marked {SETTABLE_STATUSES[0]} or {SETTABLE_STATUSES[1]}",
            },
        )
    try:
        return J.advance(
            s, student.id, stage=body.stage, status=body.status, note=body.note
        )
    except J.JourneyError as exc:  # pragma: no cover - the checks above already refuse these
        raise HTTPException(
            status_code=422, detail={"error": "journey_refused", "note": str(exc)}
        ) from exc
