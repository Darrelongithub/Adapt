"""The approval screen, and only that.

The student sees: the observed facts, the pattern, the possible explanation, the
concrete change, and the question. They answer accept / reject / modify / not now.
This router is the *only* HTTP surface that can create a row in
``approved_adaptations`` -- via :func:`adapt.services.adaptations.approve`, which
refuses to run without a recorded student decision. That is what makes "AI 1 cannot
change your education by itself" a property of the code rather than a promise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import db_session, rate, require_csrf, student_from_request
from ...db.models import Student
from ...learning import strategies as ST
from ...services import adaptations as AD
from ...services import current

router = APIRouter(prefix="/api/v1/adaptations", tags=["adaptations"])


class DecisionBody(BaseModel):
    decision: str = Field(min_length=2, max_length=16)
    note: str | None = Field(default=None, max_length=2000)
    modifications: dict[str, Any] = Field(default_factory=dict)
    days: int | None = Field(default=None, ge=1, le=365)

    def as_student_decision(self, proposal_id: str) -> dict[str, Any]:
        """The decision object the gate expects.

        ``answered_at`` is stamped here, at the moment the learner's answer arrives,
        rather than accepted from the client: a timestamp the browser supplies could
        be back-dated, and the whole point of the effect measurement is that the
        baseline is taken at the real decision time.
        """
        return {
            "decision": self.decision,
            "proposal_id": proposal_id,
            "answered_by": "student",
            "answered_at": datetime.now(timezone.utc).isoformat(),
            "channel": "web",
            "note": self.note,
        }


@router.get("/pending")
def pending(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    rows = AD.pending(s, student.id)
    return {
        "proposals": rows,
        "explain": (
            "Each row separates what was observed from what we think it means. Nothing here has been "
            "applied. Saying no is recorded too, and stops us asking again tomorrow."
        ),
    }


@router.get("/history")
def history(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    return {"decisions": AD.history(s, student.id)}


@router.get("/active")
def active(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    live = AD.active(s, student.id)
    # "not yet measured" means no verdict yet, which includes a change whose window is still
    # filling up: it has a stored sentence and a count, and still nothing that ranks it.
    return {"active": live, "waiting_for_effect": [a for a in live if not a["effect_measured"]]}


@router.post("/{proposal_id}/decision")
def decide(
    proposal_id: str,
    body: DecisionBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    if body.decision not in AD.DECISIONS:
        raise HTTPException(status_code=422, detail={"error": "unknown decision", "allowed": list(AD.DECISIONS)})
    try:
        out = AD.decide(
            s,
            student_id=student.id,
            proposal_id=proposal_id,
            student_decision=body.as_student_decision(proposal_id),
            note=body.note,
            modifications=body.modifications,
            days=body.days,
        )
    except AD.GateError as exc:
        raise HTTPException(status_code=403, detail={"error": "approval_gate", "detail": str(exc)}) from exc
    if not out.get("ok"):
        raise HTTPException(status_code=409, detail=out)
    return out


@router.post("/{active_id}/revoke")
def revoke(
    active_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return AD.revoke(s, student_id=student.id, active_id=active_id, reason="student_revoked")
    except AD.GateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/strategy/{name}/revoke")
def revoke_strategy(
    name: str,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    if name not in ST.CATALOG:
        raise HTTPException(status_code=404, detail="unknown strategy")
    try:
        return AD.revoke(s, student_id=student.id, strategy=name, reason="student_revoked")
    except AD.GateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/evaluate")
async def evaluate(s: Session = Depends(db_session), student: Student = Depends(require_csrf)) -> dict[str, Any]:
    """Measure whether the approved changes are helping -- including to the learner
    who would rather we stopped. AI 1 reports this; it does not act on it alone."""
    rows = current().ai1.evaluate_pending_adaptations(student.id)
    return {"evaluated": rows, "note": "small samples are labelled insufficient rather than smoothed over"}
