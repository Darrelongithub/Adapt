"""The adapted lesson over HTTP (round brief §8, Phase 5).

Three routes:

* ``POST /api/v1/lesson-plan`` — one lesson for the signed-in learner, built
  by the adaptation layer from their stored levers (or the levers in the
  body), their subject map, and a content pack — the body's, the topic's
  authored material, or an honest skeleton when neither exists. The plan is
  recorded through the Orchestrator, so what was planned, from which model
  version and which content, stays answerable afterwards.
* ``POST /api/v1/lesson-plan/contrast`` — the contrast demo: one concept,
  two profiles, two lessons and the computed differences between them.
* ``GET  /api/v1/lesson-plan/visual-kinds`` — the seven picture templates
  and their schemas, so a client draws only what code can validate.

Nothing here names a model, a provider or a key (brief §3): a lesson is
``deterministic`` (the template adapter) or ``model_validated`` (a suggestion
that passed every code check), and that is all the client is told.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import db_session, rate, student_from_request
from ...ai.adapter import AdaptationError, adapter
from ...db.models import Student
from ...lesson import templates as TPL
from ...services import current

log = logging.getLogger("adapt.lesson_plan")

router = APIRouter(prefix="/api/v1/lesson-plan", tags=["lesson-plan"])


class PlanBody(BaseModel):
    concept: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=200)
    topic_id: str | None = Field(default=None, max_length=120)
    content_pack: dict[str, Any] | None = None
    #: A model's proposed plan. Validated whole; rejected whole. The lesson
    #: that comes back says which happened, and why.
    model_proposal: Any = None
    levers: dict[str, Any] | None = None
    subject_map: Any = None
    ladder: dict[str, Any] | None = None
    recent_evidence: Any = None
    preferences: dict[str, Any] | None = None
    constraints: dict[str, Any] | None = None
    session_id: str | None = Field(default=None, max_length=120)


class ContrastBody(BaseModel):
    concept: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=200)
    topic_id: str | None = Field(default=None, max_length=120)
    content_pack: dict[str, Any] | None = None
    profiles: list[dict[str, Any]] = Field(default_factory=list)


def _pack_for(s: Session, *, body_pack: dict[str, Any] | None, topic_id: str | None, learner_id: str) -> dict[str, Any] | None:
    """The pack the lesson is built from: the body's, else the authored bank's.

    The bank path is ``concept_versions_for`` — the same source the practice
    queue teaches from — converted by the adapter's pack normalisation, so a
    concept with material gets a real lesson before any generated pack exists,
    and a concept without material gets the adapter's honest skeleton rather
    than invented prose.
    """
    if body_pack:
        return body_pack
    if topic_id:
        from ...services import lessons as lessons_service

        versions = lessons_service.concept_versions_for(s, topic_id, learner_id=learner_id)
        if versions is not None:
            return versions
    return None


def _inputs(s: Session, student_id: str, body: PlanBody) -> dict[str, Any]:
    svcs = current()
    levers = body.levers
    if levers is None:
        try:
            levers = svcs.baseline.snapshot(student_id).get("levers") or None
        except Exception:  # a learner with no baseline yet still gets a lesson
            log.debug("lesson-plan: no baseline levers for %s", student_id, exc_info=True)
            levers = None
    subject_map = body.subject_map
    if subject_map is None:
        try:
            snapshot = svcs.state.get_snapshot(student_id)
            subject_map = [m.as_dict() for m in snapshot.mastery]
        except Exception:
            log.debug("lesson-plan: no subject map for %s", student_id, exc_info=True)
            subject_map = None
    return {
        "levers": levers,
        "subject_map": subject_map,
        "ladder": body.ladder,
        "content_pack": _pack_for(s, body_pack=body.content_pack, topic_id=body.topic_id, learner_id=student_id),
        "recent_evidence": body.recent_evidence,
        "preferences": body.preferences,
        "constraints": body.constraints,
        "concept": body.concept,
        "title": body.title,
    }


def _record(student_id: str, plan: dict[str, Any], *, session_id: str | None) -> str | None:
    """Step 4–5 of the turn cycle: store the plan, remember what it teaches.

    Recording must never cost the learner their lesson, so a store failure is
    logged and the plan is still returned — with no plan id, which the caller
    can see, rather than a plan id that resolves to nothing.
    """
    svcs = current()
    try:
        model_version = int((plan.get("provenance") or {}).get("model_version") or svcs.state.latest_version(student_id))
        content_id = (plan.get("provenance") or {}).get("content_id")
        plan_id, _replanned = svcs.orchestrator.plan(
            student_id,
            session_id=session_id,
            blocks=plan["blocks"],
            model_version=model_version,
            content_ids=[content_id] if content_id else [],
        )
        concept = str(plan.get("concept") or "")
        if concept:
            svcs.state.remember(student_id, kind="explained", ref=concept, session_id=session_id, module="ai3")
        for block in plan["blocks"]:
            if block["kind"] == "text":
                svcs.state.remember(student_id, kind="explained", ref=block["chunk_id"], session_id=session_id, module="ai3")
            elif block["kind"] == "worked_example":
                svcs.state.remember(student_id, kind="examples_shown", ref=block["chunk_id"], session_id=session_id, module="ai3")
            elif block["kind"] in ("check", "predict", "explain"):
                question = str(block["content"].get("question") or "")
                if question:
                    svcs.state.remember(student_id, kind="asked", ref=question, session_id=session_id, module="ai3")
        return plan_id
    except Exception:
        log.exception("lesson-plan: plan for %s was built but not recorded", student_id)
        return None


@router.post("")
def build_lesson(
    body: PlanBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """One adapted lesson for this learner, with its reasons attached."""
    inputs = _inputs(s, student.id, body)
    # Version 0 is a real answer — the cold start — so it is recorded as 0,
    # not disguised as "unknown".
    inputs["model_version"] = current().state.latest_version(student.id)
    try:
        if body.model_proposal is not None:
            plan = adapter.plan_from_model(body.model_proposal, **inputs)
        else:
            plan = adapter.plan(**inputs)
    except AdaptationError as exc:
        raise HTTPException(status_code=422, detail={"error": "adaptation_refused", "message": str(exc)}) from exc
    plan_id = _record(student.id, plan, session_id=body.session_id)
    return {"plan": plan, "plan_id": plan_id, "recorded": plan_id is not None}


@router.post("/contrast")
def contrast(
    body: ContrastBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """The contrast demo: the same concept under two profiles, side by side."""
    if len(body.profiles) != 2:
        raise HTTPException(
            status_code=422,
            detail={"error": "contrast_needs_two_profiles", "message": "the contrast demo takes exactly two profiles"},
        )
    pack = _pack_for(s, body_pack=body.content_pack, topic_id=body.topic_id, learner_id=student.id)
    try:
        return adapter.contrast(content_pack=pack, concept=body.concept, title=body.title, profiles=body.profiles)
    except AdaptationError as exc:
        raise HTTPException(status_code=422, detail={"error": "adaptation_refused", "message": str(exc)}) from exc


@router.get("/visual-kinds")
def visual_kinds(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """The seven picture templates and the schema each one validates against."""
    return TPL.schema_summary()
