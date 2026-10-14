"""The loop itself: test -> observe -> understand -> personalize -> learn.

Every endpoint here is one visible beat of that cycle, and the UI is allowed to be
boring about it: the diagnostic returns items, the client posts the events it
observed, submitting an answer updates the trace, ``/analyze`` produces evidence +
hypotheses + (maybe) a proposal, and ``/journey`` shows the whole thing in order.

Nothing in this file writes ``approved_adaptations``; that only happens in
``routes/adaptations.py`` after the learner answers, which calls the one function in
the codebase allowed to create those rows.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("adapt.routes.learning")

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import db_session, rate, require_csrf, student_from_request
from ...assessment import bank
from ...config import get_settings
from ...db.models import Attempt, BktState, LearningSession, Student, Topic
from ...learning import strategies as ST
from ...learning.telemetry import ALLOWED_EVENTS as TELEMETRY_EVENTS
from ...services import attempts as A
from ...services import dashboard as D
from ...services import journey as J
from ...services import telemetry as TEL
from ...services import sessions as S
from ...services import current
from ...services.evidence import remember_offers
from ...security import secrecy

router = APIRouter(prefix="/api/v1", tags=["learning"])


class SessionBody(BaseModel):
    mode: str = "practice"
    course_id: str | None = None
    device_hint: str | None = Field(default=None, max_length=32)


class EventsBody(BaseModel):
    session_id: str
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class AttemptBody(BaseModel):
    session_id: str
    item_id: str
    answer: Any = None
    teachback_text: str | None = Field(default=None, max_length=4000)
    attempt_no: int | None = None
    client_ts: str | None = None
    # Backwards-compatible echoes only. The server delivery log owns assignment
    # and prediction; client metadata cannot manufacture controlled evidence.
    method: str | None = Field(default=None, max_length=32)
    method_design: str | None = Field(default=None, max_length=24)
    method_arm: str | None = Field(default=None, max_length=24)
    expected_success: float | None = None
    reteach_of: str | None = Field(default=None, max_length=40)


class RetentionBody(BaseModel):
    topic_id: str
    item_id: str | None = None
    correct: bool
    score: float | None = None
    latency_ms: int | None = None
    kind: str = "probe"


@router.post("/sessions")
def start_session(
    body: SessionBody,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    sess = S.start_session(
        s,
        student.id,
        mode=body.mode,
        course_id=body.course_id,
        device_hint=body.device_hint,
    )
    # The lesson now has a plan with a model version on it (brief 2A, step 4). It is recorded here,
    # at the moment the lesson starts, so every later "why does this lesson look like this?" panel
    # has a version to point at — including the mid-lesson re-plan that replaces it.
    try:
        current().orchestrator.cycle(
            student.id,
            session_id=sess.id,
            blocks=[{"kind": body.mode, "topic_id": None, "note": "opening block chosen by the plan"}],
        )
    except Exception:  # pragma: no cover - a store hiccup must not stop the learner starting
        log.exception("sessions: could not record the opening plan (the session still started)")
    return {
        "session_id": sess.id,
        "mode": sess.mode,
        "started_at": sess.started_at.isoformat(),
        "event_types_accepted": sorted(TELEMETRY_EVENTS),
    }


@router.post("/sessions/{session_id}/finish")
def finish_session(
    session_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    out = S.finish_session(s, session_id, student_id=student.id)
    if not out.get("ok") and out.get("reason") == "unknown session":
        # 200-with-ok:false is how a client ends up believing a save worked.
        raise HTTPException(status_code=404, detail=out)
    return out


@router.get("/sessions/{session_id}/events")
def get_events(
    session_id: str,
    after_seq: int = 0,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    sess = s.get(LearningSession, session_id)
    if sess is None or sess.student_id != student.id:
        raise HTTPException(status_code=404, detail="unknown session")
    return {"events": TEL.event_stream(s, session_id, after_seq=after_seq)}


@router.post("/events")
@router.post("/sessions/{sid}/events")
def post_events(
    body: EventsBody,
    request: Request,
    sid: str | None = None,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Append the learner-side event batch. Server derives all metrics from it."""
    session_id = sid or body.session_id
    result = TEL.ingest(s, session_id=session_id, student_id=student.id, events=body.events)
    return result.as_dict()


@router.get("/assessment/diagnostic")
def diagnostic(
    session_id: str | None = None,
    count: int = 8,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """**Placement only.** Adaptive, and nothing else.

    It used to be one 12-item run doing two jobs (placing the learner *and* comparing
    teaching methods), which is why it was long and why neither half was clean. The
    method comparison now lives at ``GET /assessment/method-probe`` and runs for six to
    eight concepts.

    Pass ``session_id`` and the plan adapts to the answers already recorded in that
    session: each request re-plans from the evidence, so a client that asks again after
    every answer gets a real adaptive test rather than a fixed 12-item form.
    """
    svcs = current()
    if session_id:
        owned = s.get(LearningSession, session_id)
        if owned is None or owned.student_id != student.id:
            raise HTTPException(status_code=404, detail="unknown session")
    items = svcs.ai2.diagnostic_items(student.id, count=max(4, min(12, count)), session_id=session_id)
    if session_id:
        remember_offers(svcs, student.id, items, session_id=session_id, placement=True)
    # Say what the plan was built *from*. The client may ask again after each answer, and
    # "n answers went into this" is the difference between an adaptive test and a fixed
    # form that happens to be re-requested.
    answered = 0
    if session_id:
        answered = int(
            s.execute(
                select(func.count()).select_from(Attempt).where(Attempt.session_id == session_id, Attempt.correct.is_not(None))
            ).scalar_one()
            or 0
        )
    return {
        "phase": "placement",
        "adaptive": True,
        "plans_from": {"session_id": session_id, "answered": answered},
        # A full sentence, capital and all: the client renders this as the opening line of the
        # placement screen and used to print the bare clause, so the page began mid-sentence.
        "purpose": "This placement puts you on each topic with a confidence band — not to grade you.",
        "items": items,
        # "AI 1" used to be printed here. The learner is not told about the app's internal
        # component names: what they need to know is what is recorded, what is done with it, and
        # who decides. Brief section 2A: the names are internal only.
        "how_it_is_used": (
            "Answers, timings, hint use and answer changes are recorded as events. They are turned "
            "into mastery estimates and possible explanations; a change is only proposed when the "
            "pattern is consistent, and nothing is applied until you agree to it."
        ),
        "note_on_time": "If you need longer, say so - time spent is evidence about the interaction, not a score.",
    }


@router.get("/assessment/method-probe")
def method_probe(
    count: int = 8,
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """Six to eight concepts, one teaching method each, format held fixed.

    This is the half of the old diagnostic that was about *teaching* rather than
    placement. Every concept is measured with one question; the method rotates, so the
    difference between two of these is what was taught, not which topic it was.
    """
    items = current().ai2.method_probe_items(student.id, count=max(6, min(8, count)))
    return {
        "phase": "method_probe",
        "concepts": len(items),
        "purpose": (
            "find out which way of teaching works better for you on these concepts — "
            "the same kind of question each time, taught a different way"
        ),
        "how_it_is_judged": (
            "each answer is compared with what the model expected from you on that question, "
            "so a method is not punished for meeting harder material; under 8 observations on "
            "either side is reported as an early signal, not a finding"
        ),
        "items": items,
    }


@router.get("/methods/report")
def methods_report(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """Actual vs expected per teaching method, accumulated over every session.

    Raw accuracy is deliberately absent: the number per method is actual minus expected,
    where "expected" is what the model predicted for that exact question before it was
    served, so a method that met harder material is not punished for it. Every entry
    carries ``n`` and an ``evidence`` label, and anything under eight observations says
    ``early_signal`` rather than drawing a conclusion.
    """
    return A.method_report_for(s, student.id, state=current().state)


@router.post("/attempts")
async def submit_attempt(
    body: AttemptBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    svcs = current()
    try:
        result = await A.submit(
            svcs,
            student_id=student.id,
            session_id=body.session_id,
            item_id=body.item_id,
            answer=body.answer,
            teachback_text=body.teachback_text,
            attempt_no=body.attempt_no,
            client_ts=body.client_ts,
            method=body.method,
            method_design=body.method_design,
            method_arm=body.method_arm,
            expected_success_client=body.expected_success,
            reteach_of=body.reteach_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.as_dict()


@router.get("/me/attempts")
def my_attempts(
    limit: int = 200,
    topic_id: str | None = None,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    rows = A.attempts_for(s, student.id, limit=max(1, min(500, limit)), topic_id=topic_id)
    return {"attempts": rows, "count": len(rows)}


@router.get("/concepts/{topic_id}/versions")
def concept_versions(
    topic_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """The three teaching versions of a concept, plus its matched question pair.

    Subject-agnostic by construction: the text is segmented, the visual is a generic
    template filled from the same material, the worked example is the material's own
    sentences labelled by what they do, and the two questions are keyed to words that
    are *in the source*. Nothing here is specific to mathematics, which is the point —
    an arithmetic expression was the only answer key the old path could check.
    """
    from ...services import lessons as L

    versions = L.concept_versions_for(s, topic_id, learner_id=student.id)
    if versions is None:
        raise HTTPException(status_code=404, detail="no such topic")
    return versions


@router.get("/practice")
def practice(
    topic_id: str | None = None,
    count: int = 5,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    svcs = current()
    picks = svcs.ai2.next_items(student.id, topic_id=topic_id, count=max(1, min(12, count)))
    remember_offers(svcs, student.id, picks)
    why = "selected for expected information gain, review due date and gap utility"
    # "What the learner actually saw, and why" (brief 2A, step 5). The queue is the delivery; the
    # store keeps the item ids and the reason, with the model version that produced it.
    try:
        svcs.orchestrator.deliver(
            student.id,
            block="practice_queue",
            seen={"items": [p["item"]["id"] for p in picks], "topic_id": topic_id},
            why={"reason": why},
        )
    except Exception:  # pragma: no cover - recording a delivery must never withhold the queue
        log.exception("practice: could not record the delivery (the queue is still served)")
    return {"items": picks, "why": why}


@router.post("/review/complete")
def review_result(
    body: RetentionBody,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    """A delayed retest that was not tied to a specific bank item (self report)."""
    out = current().ai1.record_retention_check(
        student_id=student.id,
        topic_id=body.topic_id,
        item_id=body.item_id,
        correct=body.correct,
        score=body.score,
        latency_ms=body.latency_ms,
        kind=body.kind if body.kind in ("scheduled", "probe", "post_adaptation") else "probe",
        # This route is the self-reported review: no attempt exists, so nothing else
        # will move the schedule. Without this the topic stays due forever.
        reschedule=True,
    )
    return out


@router.get("/review/due")
def due(
    within_hours: float = 24.0,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    rows = S.due_reviews(s, student.id, within_hours=within_hours)
    return {"due": rows, "count": len(rows), "rule": "scheduled at the point your own recall is projected to reach ~85%"}


@router.get("/review/check")
async def retention_check(
    topic_id: str,
    use_llm: bool = False,
    with_notes: bool = False,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """Master prompt 5's check for one due topic: the question, and why now.

    ``with_notes`` is the "I have answered — what should it have covered?" step:
    the notes name what a good answer covers, so they are withheld until asked for
    rather than shown beside the question. Without a model the check is still
    fully formed (deterministic, grounded in the learner's own last item); a 404
    means the topic has no schedule, i.e. nothing has been practised to check.
    """
    check = await current().ai1.design_retention_check(
        student.id, topic_id, use_llm=use_llm and bool(get_settings().gemini_api_key)
    )
    if check is None:
        raise HTTPException(status_code=404, detail="no scheduled review for this topic")
    if not with_notes:
        check.pop("expected_answer_notes", None)
        check["notes"] = "what a good answer covers arrives with with_notes=1, after you have answered"
    return check


@router.post("/analyze")
async def analyze(
    session_id: str | None = None,
    use_llm: bool = True,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("llm")),
) -> dict[str, Any]:
    svcs = current()
    # A session id that is not this learner's is not "a session with no evidence yet":
    # answering 200 labelled an analysis of the whole record with a session that does not
    # exist, and a caller cannot tell that from a real analysis that found nothing. The
    # events route next door already answers 404 for the same id. An *empty* id is a
    # different case and stays accepted: the loop screens send ``session_id=`` when the
    # session is already gone, and analysing the record is what they want there.
    if session_id:
        sess = s.get(LearningSession, session_id)
        if sess is None or sess.student_id != student.id:
            raise HTTPException(status_code=404, detail="unknown session")
    res = await svcs.ai1.analyze(student.id, session_id=session_id, use_llm=use_llm and bool(get_settings().gemini_api_key))
    # The analysis says where its prose came from, in the learner's terms: the service's identity
    # and its spend stay behind the console (brief §3). ``public_payload`` drops the runtime
    # fields and adds ``served_by``.
    d = secrecy.public_payload(res.as_dict(), served_locally=getattr(res, "provider", "local") != "gemini")
    d["loop"] = _loop_shape(d)
    return d


def _loop_shape(d: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"step": "test", "done": "what the learner just answered", "evidence": f"{len(d.get('traces') or [])} topic traces"},
        {"step": "observe", "done": "metrics derived from the event log", "evidence": f"{len(d.get('evidence') or [])} evidence rows"},
        {"step": "understand", "done": "hypotheses with status and disproof conditions", "evidence": f"{len(d.get('hypotheses') or [])} hypotheses"},
        {"step": "personalize", "done": "proposals awaiting a decision - not applied", "evidence": f"{len([p for p in (d.get('proposals') or []) if isinstance(p, dict) and not p.get('skipped')])} proposals"},
        {"step": "learn", "done": "measured effect of what was approved before", "evidence": f"{len(d.get('evaluated') or [])} evaluated"},
    ]


@router.get("/profile")
def profile(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    with current().db.session() as inner:
        out = current().ai1.snapshot(inner, student.id)
    # ``shared_state`` is the Learner State Store's own view (brief 2A): the versioned model plus
    # teaching memory, built once from the log. It is additive — ``mastery`` above is the profile
    # AI 1 has always reported — and it is what the screens use to say *which* version of a learner
    # they are looking at. One call, one immutable snapshot.
    shared = current().state.get_snapshot(student.id).as_dict()
    # The store calls this "model" (it *is* the learner model). On the public boundary that key is
    # reserved for a runtime model identifier and is stripped, so the snapshot travels under a
    # name that says whose model it is. Same object, same version, nothing lost.
    if "model" in shared:
        shared["learner_model"] = shared.pop("model")
    out["shared_state"] = shared
    return out


@router.get("/dashboard")
def dashboard(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    return D.overview(current(), student.id)


@router.get("/journey")
def journey(
    days: float = 90,
    limit: int = 400,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    data = J.build(s, student.id, days=days, limit=limit)
    data["milestones"] = J.milestones(s, student.id)
    data["stats"] = J.stats(s, student.id)
    return data


@router.get("/topics")
def topics(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> list[dict[str, Any]]:
    rows = s.execute(select(Topic).order_by(Topic.difficulty)).scalars().all()
    mastery = {st.topic_id: st.p_know for st in s.execute(select(BktState).where(BktState.student_id == student.id)).scalars().all()}
    return [
        {"id": t.id, "code": t.code, "title": t.title, "difficulty": t.difficulty, "items": len(bank.ITEMS.get(t.code, [])), "p_know": round(mastery.get(t.id, 0.2), 3)}
        for t in rows
    ]


@router.get("/strategies")
def strategies() -> dict[str, Any]:
    """The catalog AI 1 may propose from, with what each one claims and cites."""
    return {
        "strategies": [ST.get(k).as_dict() for k in ST.CATALOG],
        "persistent_require_approval": sorted(ST.PERSISTENT_STRATEGIES),
        "note": (
            "Help in the moment (a clearer explanation, one more example) is not here because it needs no "
            "approval: you asked for it. Only changes that keep applying after this screen are proposed, "
            "and only with your answer."
        ),
    }


@router.get("/plan")
def plan(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    _graph, _mastery, out = current().ai1.plan(student.id)
    return out
