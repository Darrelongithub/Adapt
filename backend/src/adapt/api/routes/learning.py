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

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, rate, require_csrf, student_from_request
from ...assessment import bank
from ...config import get_settings
from ...db.models import BktState, LearningSession, Student, Topic
from ...learning import strategies as ST
from ...learning.telemetry import ALLOWED_EVENTS as TELEMETRY_EVENTS
from ...services import attempts as A
from ...services import dashboard as D
from ...services import journey as J
from ...services import telemetry as TEL
from ...services import sessions as S
from ...services import current

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
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    svcs = current()
    items = svcs.ai2.diagnostic_items(student.id, count=12)
    return {
        "purpose": "place you on each topic with a confidence band, not to grade you",
        "items": items,
        "how_it_is_used": (
            "Answers, timings, hint use and answer changes are recorded as events. AI 1 turns them into "
            "mastery estimates and possible explanations; it proposes a change only if the pattern is "
            "consistent, and nothing is applied until you answer."
        ),
        "note_on_time": "If you need longer, say so - time spent is evidence about the interaction, not a score.",
    }


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
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.as_dict()


@router.get("/practice")
def practice(
    topic_id: str | None = None,
    count: int = 5,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    svcs = current()
    picks = svcs.ai2.next_items(student.id, topic_id=topic_id, count=max(1, min(12, count)))
    return {"items": picks, "why": "selected for expected information gain, review due-date and gap utility"}


@router.post("/review/complete")
def review_result(
    body: RetentionBody,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    """A delayed retest that was not tied to a specific bank item (self-report)."""
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
    d = res.as_dict()
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
        return current().ai1.snapshot(inner, student.id)


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
            "In-the-moment help (a clearer explanation, one more example) is not here because it needs no "
            "approval: you asked for it. Only changes that keep applying after this screen are proposed, "
            "and only with your answer."
        ),
    }


@router.get("/plan")
def plan(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    _graph, mastery, out = current().ai1.plan(student.id)
    return out
