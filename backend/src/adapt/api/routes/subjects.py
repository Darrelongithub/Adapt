"""The subject round over HTTP (round brief §6).

The four endpoints the brief names, plus the ones a screen needs to walk a round:

* ``POST /topics/resolve``        — free text to subject, topic and level hint (§6.3 step 1).
* ``GET  /subjects/plan``         — which skills the round tests, which it does not, and why (§6.3).
* ``POST /subjects/ladder/{step}``— one answer on the staircase; the reply carries the next rung (§6.4).
* ``POST /subjects/method-test``  — the pop-up lesson: open it, or score the answers to it (§6.5).
* ``POST /subjects/round``        — open (or resume) a round for a topic.
* ``GET  /subjects/round/{id}``   — what to show next.
* ``POST /subjects/round/{id}/trial`` — answer the skill task on offer.
* ``POST /subjects/round/{id}/finish``— seal the round into a subject map, skippable at any point.
* ``GET  /subjects/snapshot``     — the subject map screen's payload.
* ``GET  /subjects/taxonomy``     — the skill sets themselves, with their version.
* ``GET  /subjects/recheck`` and ``POST /subjects/recheck/{id}`` — the next-day lesson recheck.

**One router, one prefix.** ``/topics/resolve`` is not under ``/subjects``, and ``api/app.py`` is
frozen for this round, so both live on a single router mounted at ``/api/v1`` — one line for the
integrator to add instead of two (see ``docs/AUDIT.md``).

**Errors are the house shape.** ``{"detail": {"error": "<slug>", "message": …}}``, 422 for refused
input, 404 for a run that is not yours *and* for one that does not exist (one message for both, so
probing somebody else's run id learns nothing), 409 for a mutation on a sealed round.

**The learner owns everything here.** The student comes from the session, never from a query
parameter, and every run id is checked against it inside the service.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Student
from ...services import current
from ...subjects import TAXONOMY_VERSION
from ...subjects.service import Subjects, SubjectsError
from ...subjects.catalog import activate as activate_subject, choose as choose_subject, complete_tests, overview as subject_overview, subject_payload
from ...subjects.models import LearnerSubject
from ...subjects.taxonomy import taxonomy_payload
from ..deps import db_session, rate, student_from_request

router = APIRouter(prefix="/api/v1", tags=["subjects"])


# --------------------------------------------------------------------------- #
# bodies
# --------------------------------------------------------------------------- #


class ResolveBody(BaseModel):
    #: What the learner typed. Long enough for "gcse higher tier calculus", short enough that nobody
    #: pastes an essay into a topic box (the service refuses past 240 characters).
    text: str = Field(min_length=1, max_length=240)


class ChooseBody(BaseModel):
    """The learner's subject words are data, never instructions to a model."""

    text: str = Field(min_length=1, max_length=240)


class CompleteTestsBody(BaseModel):
    completed_ids: list[str] = Field(default_factory=list, max_length=6)


class RoundBody(BaseModel):
    topic: str = Field(min_length=1, max_length=240)
    selected_skills: list[str] | None = Field(default=None, max_length=20)
    comments: str = Field(default="", max_length=2000)


class TrialBody(BaseModel):
    task_id: str = Field(min_length=1, max_length=96)
    response: dict[str, Any] = Field(default_factory=dict)
    #: Measured by the client, because only the client knows when the task appeared. Clamped here: a
    #: clock that says four hours is a bug in the page, not a slow learner.
    latency_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    #: Confidence in coins (0–3), the same economy the mind baseline uses, so the two rounds are
    #: comparable. Absent means the answer was not wagered on.
    wager: int | None = Field(default=None, ge=0, le=3)


class LadderBody(BaseModel):
    #: Omitted to ask what is on offer without recording anything (a page reload does exactly this).
    response: dict[str, Any] | None = None
    latency_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    wager: int | None = Field(default=None, ge=0, le=3)


class MethodTestBody(BaseModel):
    #: Either a topic (a standalone pop-up lesson) or a run (the lesson inside a round).
    topic: str | None = Field(default=None, max_length=240)
    run_id: str | None = Field(default=None, max_length=64)
    #: Six answers, three per leg, in the order the legs were served. Omitted to open or resume.
    answers: Sequence[dict[str, Any]] | None = None


class RecheckBody(BaseModel):
    #: One list of answers per concept in the recheck, in the order served.
    answers: Sequence[Sequence[dict[str, Any]]] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# the service
# --------------------------------------------------------------------------- #


def _subjects() -> Subjects:
    """The round's service, from the shared wiring if it has one and built on the spot if it does not.

    ``services.AppServices`` is frozen for this round, so it has no ``subjects`` field yet; the
    integrator adds one (see ``docs/AUDIT.md``). Until then the router builds the
    service from the same session factory, state store and model client the rest of the app uses and
    caches it on the services object, so the endpoints are live rather than a 500 waiting for a merge.
    """
    svcs = current()
    svc = getattr(svcs, "subjects", None)
    if isinstance(svc, Subjects):
        return svc
    built = Subjects(
        svcs.db.sessionmaker,
        state=svcs.state,
        settings=svcs.settings,
        llm=getattr(svcs.ai2, "llm", None),
    )
    try:
        setattr(svcs, "subjects", built)
    except Exception:  # pragma: no cover - a frozen dataclass would refuse; the round still works
        pass
    return built


def _refused(exc: SubjectsError) -> HTTPException:
    """The house error shape, with the status the message asks for."""
    message = str(exc)
    if message.startswith("unknown_round:") or message.startswith("unknown_method_test:"):
        # One message for "does not exist" and "is not yours": whether a run id exists is not
        # information a stranger needs.
        return HTTPException(status_code=404, detail={"error": "unknown_run", "message": "no such subject round"})
    if message.startswith("round_finished:"):
        return HTTPException(
            status_code=409,
            detail={
                "error": "round_finished",
                "message": "that round is sealed; its subject map is at GET /api/v1/subjects/snapshot",
            },
        )
    return HTTPException(status_code=422, detail={"error": "subject_refused", "message": message})


# --------------------------------------------------------------------------- #
# §6.3 step 1: resolve
# --------------------------------------------------------------------------- #


@router.post("/topics/resolve")
def resolve_topic(
    body: ResolveBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Free text to subject, topic and level hint, with what it matched and what else it could be.

    A mutation scope for the rate limit even though it changes nothing, because this is the one
    endpoint that may call a model, and the free-tier budget is the thing the limiter protects.
    """
    try:
        return _subjects().resolve(student.id, body.text)
    except SubjectsError as exc:
        raise _refused(exc) from None


# --------------------------------------------------------------------------- #
# §6.3 step 2: the plan
# --------------------------------------------------------------------------- #


@router.get("/subjects/plan")
def plan(
    topic: str,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """Which skills the round tests, which it does not, and the reason for both.

    Every response that shows a skill set carries ``reason`` (§6.3 step 3), and the skills the round
    skipped come back in ``not_measured`` with ``status="not_measured"`` and no score, because a
    skill nobody tested is not a skill somebody failed.
    """
    if not topic.strip():
        raise HTTPException(status_code=422, detail={"error": "topic_required", "message": "?topic= cannot be empty"})
    try:
        return _subjects().plan_tests(topic, student_id=student.id)
    except SubjectsError as exc:
        raise _refused(exc) from None


@router.get("/subjects/taxonomy")
def taxonomy(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """The skill sets themselves, with the version every rating in the app is read against."""
    payload = taxonomy_payload(with_games=False)
    payload["note"] = (
        "These are the skills a subject asks of somebody, whichever topic inside it they pick. The "
        "version travels with every rating so an old measurement is never read against a new list."
    )
    return payload


# --------------------------------------------------------------------------- #
# the round
# --------------------------------------------------------------------------- #


@router.post("/subjects/round")
def start_round(
    body: RoundBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Open a round for a topic, or hand back the unfinished one for the same topic."""
    try:
        return _subjects().start_round(
            student.id, topic=body.topic, selected_skills=body.selected_skills, comments=body.comments,
        )
    except SubjectsError as exc:
        raise _refused(exc) from None


@router.get("/subjects/round/{run_id}")
def round_view(
    run_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """What to show next: the task on offer, how far along, and the plan with its reasons."""
    try:
        return _subjects().view(student.id, run_id)
    except SubjectsError as exc:
        raise _refused(exc) from None


@router.post("/subjects/round/{run_id}/trial")
def submit_trial(
    run_id: str,
    body: TrialBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Answer the skill task on offer. The reply carries the score, the reason, and the next task."""
    try:
        return _subjects().submit_trial(
            student.id,
            run_id,
            task_id=body.task_id,
            response=body.response,
            latency_ms=body.latency_ms,
            wager=body.wager,
        )
    except SubjectsError as exc:
        raise _refused(exc) from None


@router.post("/subjects/round/{run_id}/finish")
def finish_round(
    run_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Seal the round into a subject map. Allowed at any point: stopping early is a real outcome."""
    try:
        return _subjects().finish_round(student.id, run_id)
    except SubjectsError as exc:
        raise _refused(exc) from None


@router.get("/subjects/snapshot")
def snapshot(
    topic: str | None = None,
    run_id: str | None = None,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """The subject map: measured skills, not-measured skills greyed with their reason, ladder positions."""
    try:
        return _subjects().snapshot(student.id, topic_key=topic, run_id=run_id)
    except SubjectsError as exc:
        raise _refused(exc) from None


# --------------------------------------------------------------------------- #
# §6.4: the ladder
# --------------------------------------------------------------------------- #


@router.post("/subjects/ladder/{step}")
def ladder_step(
    step: str,
    body: LadderBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """One answer on the staircase.

    ``step`` is the token the previous response issued (``<run_id>.<item number>``). Send no
    ``response`` to ask what is on offer without recording anything, which is what a reload does.
    """
    if not step or step.count(".") != 1:
        raise HTTPException(
            status_code=422,
            detail={"error": "step_required", "message": "a ladder step is the token the last response gave you"},
        )
    try:
        return _subjects().ladder_step(
            student.id,
            step,
            response=body.response,
            latency_ms=body.latency_ms,
            wager=body.wager,
        )
    except SubjectsError as exc:
        raise _refused(exc) from None


# --------------------------------------------------------------------------- #
# §6.5: the pop-up lesson
# --------------------------------------------------------------------------- #


@router.post("/subjects/method-test")
def method_test(
    body: MethodTestBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Open the pop-up lesson, or score the answers to it.

    Without ``answers`` it returns both legs: two matched concepts, each taught the way a seeded step
    in code dealt it, with three questions in the same format. With ``answers`` (six, three per leg)
    it scores each leg against what was *expected* of this learner and returns the comparison —
    labelled an early signal, because six answers is six answers.

    When a topic has no authored lesson pair the response says ``available: false`` and names the
    topics that do. Inventing a lesson on the spot would produce a pair that is not matched to
    anything, and an unmatched pair measures nothing.
    """
    try:
        return _subjects().method_test(
            student.id,
            topic=body.topic,
            run_id=body.run_id,
            answers=[dict(a) for a in body.answers] if body.answers is not None else None,
        )
    except SubjectsError as exc:
        raise _refused(exc) from None


@router.get("/subjects/recheck")
def due_rechecks(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """Yesterday's lessons, due for their next-day recheck (§6.5)."""
    try:
        return _subjects().due_rechecks(student.id)
    except SubjectsError as exc:
        raise _refused(exc) from None


@router.post("/subjects/recheck/{method_test_id}")
def submit_recheck(
    method_test_id: str,
    body: RecheckBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Score the recheck: the same questions, the next day, against yesterday's own numbers."""
    try:
        return _subjects().submit_recheck(
            student.id,
            method_test_id,
            answers=[[dict(a) for a in chunk] for chunk in body.answers],
        )
    except SubjectsError as exc:
        raise _refused(exc) from None


# --------------------------------------------------------------------------- #
# The product subject spine (kept next to, not mixed into, the older round API)
# --------------------------------------------------------------------------- #


@router.get("/learning/overview")
def learning_overview(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """The dashboard contract: several subjects, one active subject, and honest counts."""
    return subject_overview(s, student.id)


@router.post("/subjects/choose")
def choose(
    body: ChooseBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Choose a subject and persist its code-checked plan.

    The existing subject resolver/leaning/ladder code does the proposal and code validates the
    resulting closed taxonomy. The browser receives only ``model`` or ``simple``; internal provider
    names and configuration never cross this boundary.
    """
    try:
        result = choose_subject(s, _subjects(), student_id=student.id, text=body.text)
    except (ValueError, SubjectsError) as exc:
        raise HTTPException(status_code=422, detail={"error": "subject_refused", "message": str(exc)}) from None
    return {
        "subject_id": result["id"],
        "plan": result["plan"],
        "subject": result,
    }


@router.get("/subjects/{subject_id}")
def get_chosen_subject(
    subject_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    row = s.execute(
        select(LearnerSubject).where(LearnerSubject.id == subject_id, LearnerSubject.student_id == student.id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_subject", "message": "no such subject"})
    return subject_payload(row)


@router.post("/subjects/{subject_id}/activate")
def activate_chosen_subject(
    subject_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    try:
        return activate_subject(s, student.id, subject_id)
    except LookupError:
        raise HTTPException(status_code=404, detail={"error": "unknown_subject", "message": "no such subject"}) from None


@router.post("/subjects/{subject_id}/tests/complete")
def complete_subject_tests(
    subject_id: str,
    body: CompleteTestsBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    try:
        return complete_tests(s, student.id, subject_id, body.completed_ids)
    except LookupError:
        raise HTTPException(status_code=404, detail={"error": "unknown_subject", "message": "no such subject"}) from None


__all__ = ["router", "TAXONOMY_VERSION"]
