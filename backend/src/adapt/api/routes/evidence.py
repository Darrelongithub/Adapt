"""Authenticated evidence report and resumable failure recovery.

Only the attempt id is accepted at start: grades, methods, prerequisites and
private matched pairs are loaded server-side, never supplied by the browser.
Registered in the production application alongside learning and baseline.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...db.models import Student
from ...learning.failure import FailureError, FailureLoop
from ...services import current
from ...services.evidence import start_for_attempt
from ..deps import db_session, rate, student_from_request

router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    attempt_id: str = Field(min_length=1, max_length=80)


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    question_id: str = Field(min_length=1, max_length=80)
    answer: str | int | float


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": "evidence_refused", "message": str(exc)})


@router.get("")
def report(student: Student = Depends(student_from_request)) -> dict[str, Any]:
    return FailureLoop(current().state).report(student.id)


@router.post("/failure")
def start(
    body: StartBody, s: Session = Depends(db_session), student: Student = Depends(rate("mutation"))
) -> dict[str, Any]:
    try:
        return start_for_attempt(current(), s, student.id, body.attempt_id)
    except KeyError as exc:
        raise HTTPException(
            404, detail={"error": "unknown_attempt", "message": "no such attempt"}
        ) from exc
    except (FailureError, ValueError) as exc:
        raise _error(exc) from exc


@router.get("/failure/{loop_id}")
def view(loop_id: str, student: Student = Depends(student_from_request)) -> dict[str, Any]:
    try:
        return FailureLoop(current().state).view(student.id, loop_id)
    except KeyError as exc:
        raise HTTPException(
            404, detail={"error": "unknown_loop", "message": "no such recovery"}
        ) from exc


@router.post("/failure/{loop_id}/answer")
def answer(
    loop_id: str, body: AnswerBody, student: Student = Depends(rate("mutation"))
) -> dict[str, Any]:
    try:
        svcs = current()
        result = FailureLoop(svcs.state).answer(
            student.id, loop_id, question_id=body.question_id, answer=body.answer
        )
        # Every recovery grade updates the derived knowledge/method model, then
        # replaces the plan without resetting the learner's current block.
        transitions = [
            r
            for r in svcs.state.evidence_rows(student.id)
            if r["kind"] == "failure_transition" and r["payload"].get("loop_id") == loop_id
        ]
        session_id = transitions[-1]["payload"]["session_id"]
        turn = svcs.orchestrator.replan(
            student.id,
            session_id,
            reason=f"failure recovery: {result['stage']}",
            blocks=[{"kind": "failure_recovery", "loop_id": loop_id, "stage": result["stage"]}],
        )
        svcs.orchestrator.deliver(
            student.id,
            session_id=session_id,
            plan_id=turn.plan_id,
            block=f"failure:{loop_id}",
            seen={"loop_id": loop_id, "stage": result["stage"]},
            why={"model_version": turn.model_version, "reason": result["note"]},
        )
        return result
    except KeyError as exc:
        raise HTTPException(
            404, detail={"error": "unknown_loop", "message": "no such recovery"}
        ) from exc
    except FailureError as exc:
        raise _error(exc) from exc
