"""The mind baseline over HTTP (round brief §5).

Five endpoints, each one screen of the flow:

* ``POST /baseline`` — open (or resume) a run.
* ``GET  /baseline/{run_id}`` — what to show next.
* ``POST /baseline/{run_id}/format`` — the pre-game format choice.
* ``POST /baseline/{run_id}/questions`` — the stated questions, in one submit.
* ``POST /baseline/{run_id}/trial`` — answer the task on offer; the reply carries the next one.
* ``POST /baseline/{run_id}/finish`` — seal the run into the mind snapshot (skippable at any point).
* ``GET  /baseline/snapshot`` — the eight factors and the levers, latest or in progress.
* ``GET  /baseline/next-day`` — the codeword re-check for a learner who came back.

The learner owns the run: every route acts on the signed-in student and a run id must belong to
them. That check is one helper (:func:`_owned`) rather than a copy per route, because a
per-route check is how one route ends up missing it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ..deps import rate, require_csrf, student_from_request
from ...db.models import BaselineRun, Student
from ...services import current
from ...baseline import BaselineError

router = APIRouter(prefix="/api/v1/baseline", tags=["baseline"])


class FormatBody(BaseModel):
    value: str = Field(min_length=1, max_length=24)


class QuestionsBody(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)


class TrialBody(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    response: dict[str, Any] = Field(default_factory=dict)
    #: Measured by the client, because only the client knows when the stimulus appeared. Clamped
    #: server-side: a clock that says four hours is a bug in the page, not a slow learner.
    latency_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    #: Confidence in coins (0–3). Absent means the task was not wagered on.
    wager: int | None = Field(default=None, ge=0, le=3)


def _owned(run_id: str, student_id: str) -> dict[str, Any]:
    """Check the run belongs to this learner, on a connection that is closed before anything else.

    Deliberately *not* a FastAPI dependency session: the service opens its own sessions, and one
    SQLite file allows one writer, so a request that holds a session open while the service writes
    waits on its own lock and eventually fails with "database is locked". The check is cheap and the
    connection is released immediately.
    """
    with current().db.session() as s:
        run = s.get(BaselineRun, run_id)
        if run is None or run.student_id != student_id:
            # One message for both cases: whether a run id exists is not information a stranger needs.
            raise HTTPException(status_code=404, detail={"error": "unknown_run", "message": "no such baseline run"})
        return {"status": run.status}


def _wrap(exc: BaselineError) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": "baseline_refused", "message": str(exc)})


@router.post("")
def start(student: Student = Depends(rate("mutation"))) -> dict[str, Any]:
    """Open the baseline, or hand back the one that is already open."""
    return current().baseline.start(student.id)


@router.get("/snapshot")
def snapshot(run_id: str | None = None, student: Student = Depends(student_from_request)) -> dict[str, Any]:
    """The mind map: eight factors with ranges and n, the levers, and the stated preferences."""
    svcs = current()
    if run_id:
        _owned(run_id, student.id)
    return svcs.baseline.snapshot(student.id, run_id=run_id)


@router.get("/next-day")
def next_day(
    run_id: str | None = None,
    finished: bool = False,
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """The delayed codeword check, for a learner who has come back since.

    ``finished=true`` asks for the check of the last sitting that was finished (the return visit).
    Without it, the check belongs to the run named, or to the newest run.
    """
    svcs = current()
    if run_id:
        _owned(run_id, student.id)
    return svcs.baseline.next_day(student.id, run_id=run_id, finished=finished)


@router.get("/{run_id}")
def view(run_id: str, student: Student = Depends(student_from_request)) -> dict[str, Any]:
    _owned(run_id, student.id)
    return current().baseline.view(student.id, run_id)


@router.post("/{run_id}/format")
def set_format(run_id: str, body: FormatBody, student: Student = Depends(require_csrf)) -> dict[str, Any]:
    run = _owned(run_id, student.id)
    if run["status"] != "in_progress":
        raise HTTPException(status_code=409, detail={"error": "run_finished", "message": "this baseline is finished"})
    try:
        return current().baseline.set_format(student.id, run_id, body.value)
    except BaselineError as exc:
        raise _wrap(exc) from exc


@router.post("/{run_id}/questions")
def answer_questions(run_id: str, body: QuestionsBody, student: Student = Depends(require_csrf)) -> dict[str, Any]:
    run = _owned(run_id, student.id)
    if run["status"] != "in_progress":
        raise HTTPException(status_code=409, detail={"error": "run_finished", "message": "this baseline is finished"})
    try:
        return current().baseline.answer_questions(student.id, run_id, body.answers)
    except BaselineError as exc:
        raise _wrap(exc) from exc


@router.post("/{run_id}/trial")
def submit_trial(run_id: str, body: TrialBody, student: Student = Depends(rate("mutation"))) -> dict[str, Any]:
    """Answer the task on offer. The reply contains the score and the next task."""
    _owned(run_id, student.id)
    try:
        return current().baseline.submit(
            student.id,
            run_id,
            task_id=body.task_id,
            response=body.response,
            latency_ms=body.latency_ms,
            wager=body.wager,
        )
    except BaselineError as exc:
        raise _wrap(exc) from exc


@router.post("/{run_id}/finish")
def finish(run_id: str, student: Student = Depends(require_csrf)) -> dict[str, Any]:
    """Seal the run. A partial baseline is a finished baseline: it stores what was measured."""
    _owned(run_id, student.id)
    return current().baseline.finish(student.id, run_id)
