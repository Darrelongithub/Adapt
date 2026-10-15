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
import threading
import time
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


def _legacy_frozen_pack(content_pack: dict[str, Any] | None, *, concept_id: str, title: str) -> dict[str, Any]:
    """Convert the old display-pack envelope at the contrast boundary only.

    The AI 3 writer still consumes the frozen Pack contract. Legacy questions are
    deliberately not promoted to verified assessment items here; only their checked
    explanatory material becomes facts.
    """
    import re

    raw = content_pack or {}
    versions = raw.get("versions") or raw.get("variants") or {}
    explanation: Any = raw.get("explanation") or raw.get("source_text") or raw.get("text")
    if not explanation and isinstance(versions, dict):
        candidate = versions.get("written") or versions.get("text") or {}
        explanation = candidate.get("text") if isinstance(candidate, dict) else candidate
    if isinstance(explanation, list):
        explanation = " ".join(str(value) for value in explanation)
    if isinstance(explanation, dict):
        explanation = explanation.get("text") or " ".join(
            str(step.get("text") or "") for step in explanation.get("steps") or [] if isinstance(step, dict)
        )
    explanation_text = " ".join(str(explanation or "").split())
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", explanation_text) if part.strip()]
    facts: list[dict[str, str]] = []
    for row in raw.get("facts") or raw.get("key_facts") or []:
        if isinstance(row, dict):
            statement = str(row.get("statement") or "").strip()
            fact_id = str(row.get("id") or "")
        else:
            statement = str(row).strip()
            fact_id = ""
        if statement and not any(f["statement"] == statement for f in facts):
            facts.append({"id": fact_id or f"{concept_id}:fact:{len(facts) + 1}", "statement": statement})
    for statement in sentences:
        if not any(f["statement"] == statement for f in facts):
            facts.append({"id": f"{concept_id}:fact:{len(facts) + 1}", "statement": statement})
    idea = str(raw.get("key_idea") or (facts[0]["statement"] if facts else ""))
    reason = str(raw.get("why_it_works") or (facts[1]["statement"] if len(facts) > 1 else ""))
    if not facts:
        for label, statement in (("key-idea", idea), ("why", reason)):
            if statement.strip():
                facts.append({"id": f"{concept_id}:{label}", "statement": statement.strip()})
    return {
        "topic_key": str(raw.get("topic_id") or raw.get("concept") or concept_id),
        "topic_title": str(raw.get("title") or title or concept_id),
        "subject_family": "",
        "source": "authored",
        "concepts": [{
            "id": concept_id,
            "name": str(raw.get("title") or title or raw.get("concept") or concept_id),
            "key_idea": idea,
            "why_it_works": reason,
            "prerequisites": [],
            "rung": 2,
            "key_facts": facts,
            "misconceptions": [
                dict(row) for row in raw.get("misconceptions") or [] if isinstance(row, dict)
            ],
        }],
        "prereq_edges": [],
        "items": [],
    }


def _contrast_ai3(
    *,
    pack: dict[str, Any],
    concept_id: str,
    profiles: list[dict[str, Any]],
    student_id: str,
) -> dict[str, Any]:
    """Real checked AI 3 proposals when configured; deterministic templates otherwise.

    Both profile calls share one bounded deadline. The contrast request therefore cannot
    take two sequential model timeouts; any late or failed profile is filled by the same
    deterministic writer used when no key is configured.
    """
    from .lesson import _configured_model, _model_callable
    from ...ai.lesson_writer.core import MODEL_TIMEOUT_SECONDS

    model = _model_callable(student_id) if _configured_model() else None
    snapshots = [
        {
            "levers": profile.get("levers") or {},
            "preferences": profile.get("preferences") or {},
            "learner_rung": (profile.get("ladder") or {}).get("rung", 2),
            "recent_evidence": profile.get("recent_evidence") or {"rows": [], "evidence_ids": [], "n": 0},
            "lesson_explanations": profile.get("teaching_memory") or [],
        }
        for profile in profiles
    ]
    values: list[Any | None] = [None] * len(profiles)

    def write_one(index: int) -> None:
        try:
            values[index] = adapter.write_frozen_lesson(pack, concept_id, snapshots[index], model=model)
        except Exception as exc:
            log.warning("contrast AI3 profile fell back (%s)", type(exc).__name__)
            values[index] = adapter.write_frozen_lesson(pack, concept_id, snapshots[index], model=None)

    if model is None:
        for index in range(len(profiles)):
            write_one(index)
    else:
        workers = [
            threading.Thread(target=write_one, args=(index,), name=f"adapt-contrast-ai3-{index}", daemon=True)
            for index in range(len(profiles))
        ]
        for worker in workers:
            worker.start()
        deadline = time.monotonic() + MODEL_TIMEOUT_SECONDS + 0.25
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        for index, value in enumerate(values):
            if value is None:
                log.warning("contrast AI3 profile exceeded the shared deadline; using template")
                values[index] = adapter.write_frozen_lesson(pack, concept_id, snapshots[index], model=None)

    lessons = []
    for profile, value in zip(profiles, values):
        assert value is not None
        lessons.append({
            "profile": str(profile.get("name") or "Profile"),
            "label": "written for this profile, checked" if value.source == "written_for_you" else "template lesson",
            "plan": value.as_dict(),
        })
    return {
        "lessons": lessons,
        "source": "written_for_you" if any(row["plan"]["source"] == "written_for_you" for row in lessons) else "simple",
    }


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
        contrast_result = adapter.contrast(content_pack=pack, concept=body.concept, title=body.title, profiles=body.profiles)
        frozen_pack = _legacy_frozen_pack(pack, concept_id=body.concept or str((pack or {}).get("concept") or "concept"), title=body.title)
        contrast_result["ai3_checked_output"] = _contrast_ai3(
            pack=frozen_pack,
            concept_id=str(frozen_pack["concepts"][0]["id"]),
            profiles=body.profiles,
            student_id=student.id,
        )
        return contrast_result
    except AdaptationError as exc:
        raise HTTPException(status_code=422, detail={"error": "adaptation_refused", "message": str(exc)}) from exc


@router.get("/visual-kinds")
def visual_kinds(
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """The seven picture templates and the schema each one validates against."""
    return TPL.schema_summary()
