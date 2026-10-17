"""Frozen Part C lesson API: plan, misconception response, re-teach and upgrades."""

from __future__ import annotations

import asyncio
import importlib
import logging
import threading
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...ai.adapter import adapter
from ...ai.lesson_writer import Explanation, LessonPlan
from ...db.models import Attempt, Student
from ...security.redact import redact_payload
from ...services import current
from ..deps import db_session, rate, student_from_request

log = logging.getLogger("adapt.lesson")
router = APIRouter(prefix="/api/v1/lesson", tags=["lesson"])


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PlanBody(_StrictBody):
    topic: str = Field(min_length=2, max_length=96)
    concept_id: str = Field(min_length=1, max_length=96)


class WrongBody(_StrictBody):
    topic: str = Field(min_length=2, max_length=96)
    item_id: str = Field(min_length=1, max_length=96)
    answer: str | int | float = Field()


class GradeBody(_StrictBody):
    """Round 3 (Track S): the click of a check option is sent here for
    server-side grading.  The answer key never comes to the browser: the
    server compares the option id (or text) to the Pack's key and returns a
    `correct` flag plus, when wrong, the misconception the distractor
    matched.  The frontend then decides whether to show a confirmation or
    call /explain-wrong to get the misconception-specific follow-up.
    """

    topic: str = Field(min_length=2, max_length=96)
    item_id: str = Field(min_length=1, max_length=96)
    selected: str | int = Field()
    concept_id: str | None = Field(default=None, max_length=96)


class ReteachBody(_StrictBody):
    topic: str = Field(min_length=2, max_length=96)
    concept_id: str = Field(min_length=1, max_length=96)


@dataclass
class _UpgradeJob:
    owner_id: str
    state: Literal["running", "ready", "fallback"]
    result_key: Literal["plan", "explanation"]
    result: dict[str, Any]
    explanation_id: str
    created_at: float


_JOB_LOCK = threading.Lock()
_JOBS: dict[str, _UpgradeJob] = {}
_MAX_JOBS = 256


def _job_store(job_id: str, job: _UpgradeJob) -> None:
    with _JOB_LOCK:
        _JOBS[job_id] = job
        if len(_JOBS) > _MAX_JOBS:
            oldest = sorted(_JOBS.items(), key=lambda pair: pair[1].created_at)[
                : len(_JOBS) - _MAX_JOBS
            ]
            for key, _value in oldest:
                _JOBS.pop(key, None)


def _job_update(
    job_id: str, *, state: Literal["ready", "fallback"], result: dict[str, Any]
) -> None:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.state = state
            job.result = result


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _plain(value.as_dict())
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _plain(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _plain(getattr(value, item.name))
            for item in fields(value)
            if not item.name.startswith("_")
        }
    if hasattr(value, "__dict__"):
        return {
            str(key): _plain(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return value


def _as_mapping(value: Any) -> dict[str, Any]:
    plain = _plain(value)
    return dict(plain) if isinstance(plain, Mapping) else {}


def _topic_from_frozen(topic: str, level: int, language: str) -> Any:
    """Use B's frozen API when present; this branch's authored engine is the fallback.

    Phase G seam fix: a Pack that ``/content/prepare`` validated and cached is the Pack the
    lesson is written from. Before this, the route always rebuilt with ``force_simple=True``,
    which never sees the cache — so a typed topic that had just been prepared could not be
    taught from its prepared Pack. The cache lookup is best-effort: anything unexpected falls
    through to the deterministic pack, which is the honest fallback anyway.

    Second Phase G seam fix (found by the browser journey, not by any unit test): prepare
    caches at the level its caller asks for — the request default is 0, and a learner screen
    has no honest way to know the per-topic subject rung — while this route resolves at the
    learner's rung. Keyed strictly by level, the prepared Pack is unreachable in the exact
    case prepare exists for. The lookup therefore tries the learner's rung first and then
    the nearest cached levels for the same topic and language: a Pack B validated at a
    neighbouring rung is better source material than the deterministic fallback, and every
    downstream validator still guards the lesson written from it.
    """
    try:
        module = importlib.import_module("adapt.content.skeleton")
    except ImportError:
        return None
    try:
        from ...services import current as current_services

        services = current_services()
        topic_key = module.canonical_topic_key(topic)
        for probe in sorted(range(6), key=lambda rung: (abs(rung - level), rung)):
            identity = module.cache_identity(topic_key, level=probe, language=language)
            with services.scope() as session:
                cached = module.get_cached(session, identity)
            if cached is not None:
                pack, _digest = cached
                return pack
    except Exception:  # cache unavailable or unreadable: fall through to the built-in paths
        log.debug("lesson pack cache miss for topic", exc_info=True)
    try:
        return module.get_pack(topic, level=level, language=language, model=None, force_simple=True)
    except (AttributeError, TypeError):
        return None


def _sentences(text: str) -> list[str]:
    import re

    return [
        part.strip() for part in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if part.strip()
    ]


def _current_authored_pack(topic: str, concept_id: str) -> dict[str, Any]:
    """Adapt this checkout's authored chain pack to the frozen Part C contract.

    This is a read-only boundary adapter. It does not change the current content
    schema or create a parallel cache; the writer itself only sees the frozen Pack.
    """
    from ...assessment.bank import TOPIC_TEACHING
    from ...content.breakdown import breakdown_for, topic_spec
    from ...content.pack import build_concept_pack

    breakdown = breakdown_for(topic)
    if not breakdown.available:
        raise HTTPException(
            status_code=404, detail={"error": "topic_unavailable", "message": breakdown.reason}
        )
    concept = breakdown.concept(concept_id)
    if concept is None:
        names = ", ".join(row.id for row in breakdown.concepts)
        raise HTTPException(
            status_code=404,
            detail={
                "error": "concept_unavailable",
                "message": f"No concept {concept_id!r}; available: {names}",
            },
        )
    source_pack = build_concept_pack(
        concept, topic_key=breakdown.topic_key, topic_title=breakdown.title
    )
    topic_teaching = TOPIC_TEACHING.get(str(concept.bank_code or ""), {})
    description = _sentences(str(concept.description or ""))
    key_idea = str(
        topic_teaching.get("key_idea") or (description[0] if description else "")
    ).strip()
    why_it_works = str(
        topic_teaching.get("why_it_works") or (description[1] if len(description) > 1 else "")
    ).strip()

    statements: list[str] = []
    for text in (key_idea, why_it_works, *description):
        clean = " ".join(str(text).split())
        if clean and clean not in statements:
            statements.append(clean)
    fact_rows = [
        {"id": f"{concept.id}:fact:{index + 1}", "statement": text}
        for index, text in enumerate(statements)
    ]
    fact_ids = [row["id"] for row in fact_rows]
    resolved_topic = topic_spec(breakdown.topic_key)
    subject_family = resolved_topic.family if resolved_topic else ""

    from ...content.distractors import authored_distractors

    misconceptions = [
        {key: value for key, value in row.items() if key != "rung"}
        for row in authored_distractors(concept)
        if row["rung"] in (3, 4)
    ]

    items: list[dict[str, Any]] = []
    raw_pair = source_pack.pair or {}
    for leg in ("A", "B"):
        raw_item = raw_pair.get(leg)
        if not isinstance(raw_item, Mapping):
            continue
        item_id = str(raw_item.get("id") or f"{concept.id}:{leg}")
        choices = [
            choice for choice in (raw_item.get("choices") or []) if isinstance(choice, Mapping)
        ]
        correct_index = (raw_item.get("answer_spec") or {}).get("correct_index")
        options: list[dict[str, Any]] = []
        for index, choice in enumerate(choices):
            choice_text = str(choice.get("text") or "")
            matched = next(
                (
                    mis
                    for mis in misconceptions
                    if " ".join(mis["wrong_belief"].casefold().split())
                    == " ".join(choice_text.casefold().split())
                ),
                None,
            )
            option = {"id": f"{item_id}:option:{index}", "text": choice_text}
            if matched and index != correct_index:
                option["misconception_id"] = matched["id"]
            options.append(option)
        if (
            not choices
            or not isinstance(correct_index, int)
            or not 0 <= correct_index < len(choices)
        ):
            continue
        items.append(
            {
                "id": item_id,
                "concept_id": concept.id,
                "kind": "mcq",
                "stem": str(raw_item.get("stem") or ""),
                "options": options,
                "key": str(options[correct_index]["id"]),
                "pair_id": f"{concept.id}:matched-pair",
                "hints": [dict(row) for row in source_pack.hints[:3]],
                "teachback": [dict(row) for row in source_pack.checklist[:3]],
                "fact_ids": fact_ids,
                "verification": "cross_checked",
                "why": str(raw_item.get("why") or ""),
            }
        )

    return {
        "topic_key": breakdown.topic_key,
        "topic_title": breakdown.title,
        "subject_family": subject_family,
        "source": "authored",
        "concepts": [
            {
                "id": concept.id,
                "name": concept.title,
                "key_idea": key_idea,
                "why_it_works": why_it_works,
                "prerequisites": list(concept.prerequisites),
                "rung": 2,
                "key_facts": fact_rows,
                "misconceptions": misconceptions,
            }
        ],
        "prereq_edges": [(concept.id, prerequisite) for prerequisite in concept.prerequisites],
        "items": items,
    }


def _get_pack(topic: str, concept_id: str, *, level: int, language: str) -> Any:
    frozen = _topic_from_frozen(topic, level, language)
    if frozen is not None:
        raw = _as_mapping(frozen)
        concepts = raw.get("concepts") or []
        wanted = any(
            isinstance(row, Mapping) and str(row.get("id") or "") == concept_id for row in concepts
        )
        if wanted:
            return frozen
    return _current_authored_pack(topic, concept_id)


def _pack_for_item(topic: str, item_id: str, *, level: int, language: str) -> Any | None:
    """Find a Pack by item id, including B topics absent from authored A data."""
    frozen = _topic_from_frozen(topic, level, language)
    if frozen is not None:
        raw = _as_mapping(frozen)
        if any(
            isinstance(item, Mapping) and str(item.get("id") or "") == item_id
            for item in raw.get("items") or []
        ):
            return frozen
    from ...content.breakdown import breakdown_for

    breakdown = breakdown_for(topic)
    if not breakdown.available:
        return None
    for concept in breakdown.concepts:
        candidate = _get_pack(topic, concept.id, level=level, language=language)
        raw = _as_mapping(candidate)
        if any(
            isinstance(item, Mapping) and str(item.get("id") or "") == item_id
            for item in raw.get("items") or []
        ):
            return candidate
    return None


def _answer_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        for key in ("value", "text", "choice", "answer", "selected"):
            if key in value:
                return value[key]
    return value


def _attempt_evidence(session: Session, student_id: str, pack: Any) -> dict[str, Any]:
    raw = _as_mapping(pack)
    topic_keys = {str(raw.get("topic_key") or "")}
    topic_keys.discard("")
    item_ids = {
        str(row.get("id") or "") for row in raw.get("items") or [] if isinstance(row, Mapping)
    }
    stmt = select(Attempt).where(Attempt.student_id == student_id)
    if item_ids and topic_keys:
        stmt = stmt.where(or_(Attempt.item_id.in_(item_ids), Attempt.topic_id.in_(topic_keys)))
    elif item_ids:
        stmt = stmt.where(Attempt.item_id.in_(item_ids))
    elif topic_keys:
        stmt = stmt.where(Attempt.topic_id.in_(topic_keys))
    else:
        return {"rows": [], "evidence_ids": [], "n": 0}
    attempts = list(
        session.execute(stmt.order_by(Attempt.submitted_at.desc(), Attempt.id.desc()).limit(12))
        .scalars()
        .all()
    )
    attempts.reverse()
    pack_items = {
        str(row.get("id") or ""): row for row in raw.get("items") or [] if isinstance(row, Mapping)
    }
    rows: list[dict[str, Any]] = []
    for attempt in attempts:
        answer = _answer_value(attempt.final_answer or attempt.first_answer or {})
        item = pack_items.get(str(attempt.item_id))
        if item and isinstance(answer, int):
            options = item.get("options") or []
            if 0 <= answer < len(options) and isinstance(options[answer], Mapping):
                option = options[answer]
                answer = str(option.get("text") or answer)
                misconception_id = option.get("misconception_id")
            else:
                misconception_id = None
        else:
            misconception_id = None
        row = {
            "id": f"att_{attempt.id}",
            "event_id": f"att_{attempt.id}",
            "item_id": str(attempt.item_id),
            "correct": bool(attempt.correct) if attempt.correct is not None else None,
            "score": attempt.score,
        }
        if attempt.correct is False:
            row["answer"] = answer
        if misconception_id:
            row["misconception_id"] = str(misconception_id)
        rows.append(row)
    return {"rows": rows, "evidence_ids": [str(row["id"]) for row in rows], "n": len(rows)}


def _subject_rung(
    services: Any,
    student_id: str,
    topic_key: str | None,
    concept_id: str | None,
    fallback: int,
) -> int:
    if not topic_key:
        return fallback
    try:
        subject_map = services.subjects.snapshot(student_id, topic_key=topic_key)
    except Exception:
        return fallback
    ladder = subject_map.get("ladder") or {}
    positions = [row for row in ladder.get("positions") or [] if isinstance(row, Mapping)]
    if concept_id:
        for row in positions:
            nodes = [node for node in row.get("nodes") or [] if isinstance(node, Mapping)]
            matches = str(row.get("sub_area") or "") == concept_id or any(
                str(node.get("id") or "") == concept_id for node in nodes
            )
            if not matches:
                continue
            if row.get("placed") and row.get("steady_rung") is not None:
                return max(0, min(5, int(row["steady_rung"])))
            if row.get("below_rung") is not None:
                return max(0, min(5, int(row["below_rung"]) - 1))
    measured = [
        row
        for row in positions
        if row.get("placed") and row.get("steady_rung") is not None and int(row.get("n") or 0) > 0
    ]
    if len(measured) == 1:
        return max(0, min(5, int(measured[0]["steady_rung"])))
    return fallback


def _snapshot(
    session: Session,
    student_id: str,
    pack: Any,
    session_id: str | None,
    *,
    topic_key: str | None = None,
    concept_id: str | None = None,
) -> dict[str, Any]:
    services = current()
    try:
        snapshot = services.state.get_snapshot(
            student_id, session_id=session_id, session=session
        ).as_dict()
    except Exception:
        snapshot = {"preferences": {}, "teaching_memory": {}, "learner_rung": 2}
    try:
        baseline = services.baseline.snapshot(student_id)
        snapshot["levers"] = baseline.get("levers") or {}
    except Exception:
        snapshot["levers"] = {}
    raw = _as_mapping(pack)
    topic = topic_key or str(raw.get("topic_key") or raw.get("topic_id") or "") or None
    topic_metadata = raw.get("topic")
    if topic is None and isinstance(topic_metadata, Mapping):
        topic = str(topic_metadata.get("key") or topic_metadata.get("id") or "") or None
    concepts = [row for row in raw.get("concepts") or [] if isinstance(row, Mapping)]
    target_concept = next(
        (row for row in concepts if str(row.get("id") or "") == str(concept_id or "")),
        None,
    )
    declared_rung = target_concept.get("rung") if target_concept else None
    try:
        if declared_rung is not None:
            fallback_rung = max(0, min(5, int(declared_rung)))
        else:
            stored_rung = snapshot.get("learner_rung")
            fallback_rung = max(0, min(5, int(stored_rung))) if stored_rung is not None else 2
    except (TypeError, ValueError):
        fallback_rung = 2
    snapshot["learner_rung"] = _subject_rung(services, student_id, topic, concept_id, fallback_rung)
    snapshot["recent_evidence"] = _attempt_evidence(session, student_id, pack)
    snapshot["lesson_explanations"] = services.state.memory_details(student_id, session=session)
    return snapshot


def _learner_level(snapshot: Mapping[str, Any]) -> int:
    try:
        value = snapshot.get("learner_rung")
        return max(0, min(5, int(value))) if value is not None else 2
    except (TypeError, ValueError):
        return 2


def _configured_model() -> bool:
    services = current()
    llm = getattr(services.ai2, "llm", None)
    return bool(
        llm is not None and getattr(llm, "configured", bool(services.settings.gemini_api_key))
    )


def _model_callable(student_id: str):
    """Synchronous frozen callable bridged to the existing async Gemini entry point."""

    def call(
        purpose: str, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        services = current()
        base_llm = getattr(services.ai2, "llm", None)
        if base_llm is None or not getattr(
            base_llm, "configured", bool(services.settings.gemini_api_key)
        ):
            raise RuntimeError("model is not configured")
        from ...ai.llm import GeminiLLM

        client = (
            GeminiLLM(services.settings, limiter=services.limiter)
            if isinstance(base_llm, GeminiLLM)
            else base_llm
        )
        owned = client is not base_llm
        payload: Any
        try:
            payload, dropped, kinds = redact_payload({"prompt": user}, max_chars=9000)
            safe_user = str(payload.get("prompt") or "")
            if "[…truncated]" in safe_user or "[...truncated]" in safe_user:
                raise ValueError("redaction would truncate the bounded lesson prompt")
            # The provider entry point supports a compact OpenAPI subset. Nested block
            # validation remains in the writer, which rejects any schema drift as a whole.
            response_spec = {"blocks": ("array", {"type": "OBJECT"})}

            async def complete():
                try:
                    result = await client.complete(
                        system=system,
                        user=safe_user,
                        purpose=purpose,
                        role="ai3",
                        student_id=student_id,
                        temperature=0.3,
                        max_output_tokens=max_tokens,
                        response_spec=response_spec,
                    )
                finally:
                    if owned:
                        await client.aclose()
                auditor = getattr(services.ai2, "_log_llm", None)
                if auditor:
                    await auditor(
                        purpose, "ai3", result, student_id, len(safe_user), dropped, kinds=kinds
                    )
                return result

            result = asyncio.run(complete())
            if not result.ok or not isinstance(result.data, dict):
                raise RuntimeError(f"model response was {result.status or 'invalid'}")
            return result.data
        except Exception:
            if owned:
                with suppress(Exception):
                    asyncio.run(client.aclose())
            raise

    return call


def _rendered_text(value: Any) -> str:
    def strings(item: Any) -> list[str]:
        if isinstance(item, str):
            return [item]
        if isinstance(item, Mapping):
            return [text for child in item.values() for text in strings(child)]
        if isinstance(item, (list, tuple)):
            return [text for child in item for text in strings(child)]
        return []

    blocks = value.blocks if hasattr(value, "blocks") else []
    return "\n".join(text for block in blocks for text in strings(block.body))


def _record(
    student_id: str,
    value: LessonPlan | Explanation,
    *,
    session_id: str | None,
    explanation_id: str,
    supersedes: str | None = None,
) -> str | None:
    services = current()
    payload = value.as_dict()
    method = getattr(value, "method", None) or "initial_lesson"
    analogy_id = getattr(value, "analogy_id", None)
    moment = getattr(value, "moment", "initial")
    try:
        plan_id = services.state.put_plan(
            student_id,
            plan=payload,
            model_version=services.state.latest_version(student_id),
            content_ids=[str(payload.get("concept_id") or "")],
            session_id=session_id,
            supersedes=supersedes,
            module="ai3",
        )
    except Exception:
        log.exception("AI3 lesson plan was built but could not be persisted")
        plan_id = None
    try:
        services.state.remember_explanation(
            student_id,
            explanation_id=explanation_id,
            method=str(method),
            analogy_id=str(analogy_id) if analogy_id else None,
            moment=str(moment),
            text=_rendered_text(value),
            session_id=session_id,
            module="ai3",
        )
    except Exception:
        log.exception("AI3 teaching method was shown but its memory could not be persisted")
    return plan_id


def _new_explanation_id(value: Any) -> str:
    concept = str(getattr(value, "concept_id", "") or "concept")
    return f"exp_{concept[:40]}_{uuid.uuid4().hex[:32]}"


def _schedule_upgrade(
    background: BackgroundTasks,
    *,
    student_id: str,
    pack: Any,
    snapshot: dict[str, Any],
    result_key: Literal["plan", "explanation"],
    concept_id: str,
    item_id: str | None,
    learner_answer: Any,
    memory: Any,
    fallback: LessonPlan | Explanation,
    fallback_plan_id: str | None,
    session_id: str | None,
    explanation_id: str,
) -> str | None:
    if not _configured_model():
        return None
    job_id = f"lj_{uuid.uuid4().hex}"
    initial = fallback.as_dict()
    _job_store(
        job_id,
        _UpgradeJob(
            student_id, "running", result_key, initial, explanation_id, __import__("time").time()
        ),
    )
    background.add_task(
        _run_upgrade,
        job_id,
        student_id,
        pack,
        snapshot,
        result_key,
        concept_id,
        item_id,
        learner_answer,
        memory,
        fallback_plan_id,
        session_id,
        explanation_id,
    )
    return job_id


def _run_upgrade(
    job_id: str,
    student_id: str,
    pack: Any,
    snapshot: dict[str, Any],
    result_key: Literal["plan", "explanation"],
    concept_id: str,
    item_id: str | None,
    learner_answer: Any,
    memory: Any,
    fallback_plan_id: str | None,
    session_id: str | None,
    explanation_id: str,
) -> None:
    try:
        model = _model_callable(student_id)
        if result_key == "plan":
            value = adapter.write_frozen_lesson(pack, concept_id, snapshot, model=model)
        elif item_id is not None:
            value = adapter.explain_frozen_after_wrong(
                pack, item_id, learner_answer, snapshot, memory, model=model
            )
        else:
            value = adapter.reteach_frozen(pack, concept_id, snapshot, memory, model=model)
        result = value.as_dict()
        if value.source == "written_for_you":
            _record(
                student_id,
                value,
                session_id=session_id,
                explanation_id=explanation_id,
                supersedes=fallback_plan_id,
            )
            _job_update(job_id, state="ready", result=result)
        else:
            _job_update(job_id, state="fallback", result=result)
    except Exception as exc:
        log.warning("AI3 lesson upgrade failed (%s)", type(exc).__name__)
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job.state = "fallback"


def _authored_concepts(topic: str) -> list[dict[str, str]]:
    """The authored chain's concepts for a topic, or ``[]`` when it has none.

    Resolves through ``content.breakdown.breakdown_for`` — the same resolver
    ``GET /api/v1/content/breakdown`` uses — so the two routes cannot disagree about whether
    a topic has authored material. Any failure to resolve is treated as "no authored
    concepts", which leaves the caller on the generic plan it already had: this widens what
    ``/lesson/concepts`` can report and cannot narrow it or raise on the caller's behalf.
    """
    from ...content.breakdown import breakdown_for

    try:
        breakdown = breakdown_for(topic)
    except Exception as exc:  # an unresolvable topic is a "not authored" answer, not a 500
        log.debug("authored concept lookup failed for %r (%s)", topic, type(exc).__name__)
        return []
    if not getattr(breakdown, "available", False):
        return []
    return [
        {"id": str(row.id), "name": str(getattr(row, "title", "") or row.id)}
        for row in (getattr(breakdown, "concepts", ()) or ())
        if getattr(row, "id", "")
    ]


@router.get("/concepts")
def list_frozen_concepts(
    topic: str,
    session: Session = Depends(db_session),
    student: Student = Depends(rate("content")),
) -> dict[str, Any]:
    """Name the concepts a topic can teach, so a lesson can be asked for one.

    Phase G seam addition. The frozen lesson routes take a ``concept_id``, but nothing
    exposed one: pack payloads (with their keys) never leave the server, so a learner
    screen had no honest way to choose a concept. This answers with ids and names only —
    enough to ask for a lesson, never enough to leak a key. It resolves through the same
    path the plan route uses, so a Pack that ``/content/prepare`` cached is the Pack whose
    concepts are listed here.

    Round 3 (Track S, point 6): the response now reports ``source`` and ``topic_match`` so
    the learner screen can say plainly what happened.  ``source`` is ``authored`` (the
    typed topic has its own checked material), ``simple`` (the topic has no detailed
    lesson offline, so the screen is the generic "how to study any topic" plan), or
    ``model`` (the topic was prepared and the skeleton built a pack for it).  ``topic_match``
    is ``true`` when the concepts came from the typed topic's own chain, ``false`` when
    they are a generic fallback.
    """
    snapshot = _snapshot(session, student.id, {}, None, topic_key=topic, concept_id="")
    level = _learner_level(snapshot)
    language = str((snapshot.get("preferences") or {}).get("language") or "en")
    source = "authored"
    topic_match = True
    try:
        pack = _topic_from_frozen(topic, level, language)
        if pack is None:
            pack = _current_authored_pack(topic, "")
            source = "simple"
            topic_match = False
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail={"error": "pack_unavailable", "message": type(exc).__name__}
        ) from exc
    raw = _as_mapping(pack)
    raw_source = str(raw.get("source") or "").strip().lower()
    concepts = [
        {"id": str(row.get("id") or ""), "name": str(row.get("name") or "")}
        for row in raw.get("concepts") or []
        if isinstance(row, Mapping) and str(row.get("id") or "")
    ]
    if raw_source == "model":
        source = "model"
    elif raw_source == "simple":
        # The frozen skeleton had nothing better than the generic study plan for this topic.
        # That is not the same as the topic having no authored material: this checkout's own
        # chain may well cover it, in which case listing the generic concepts here contradicted
        # both this route's documented contract ("``source`` is ``authored`` when the typed
        # topic has its own checked material") and GET /content/breakdown on the same topic.
        #
        # Measured before the fix, for topic=calculus:
        #   GET /content/breakdown?topic=calculus -> available=true, "4 concepts from the
        #                                            authored chain" (calc_rate, calc_limit,
        #                                            calc_derivative, calc_rules)
        #   GET /lesson/concepts?topic=calculus   -> source=simple, topic_match=false,
        #                                            concepts = task/information/method/check
        # So a learner who typed "calculus" on /first-lesson was offered "Read the task", and
        # the lesson that came back was "Say what the task asks you to find or explain."
        #
        # The authored ids are safe to offer: POST /lesson/plan resolves them
        # (concept_id=calc_rate returns the authored lesson on rate of change, 200), so this
        # widens what the screen can reach rather than handing it an id that dead-ends.
        authored = _authored_concepts(topic)
        if authored:
            concepts = authored
        else:
            source = "simple"
            topic_match = False
    if not concepts:
        raise HTTPException(
            status_code=404,
            detail={"error": "no_concepts", "message": "nothing teachable for this topic yet"},
        )
    return {"topic": topic, "source": source, "topic_match": topic_match, "concepts": concepts}


@router.post("/plan")
def build_frozen_lesson(
    body: PlanBody,
    background: BackgroundTasks,
    session: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Return a deterministic lesson immediately; only a fully checked model plan upgrades it."""
    seed_snapshot = _snapshot(
        session, student.id, {}, None, topic_key=body.topic, concept_id=body.concept_id
    )
    try:
        pack = _get_pack(
            body.topic,
            body.concept_id,
            level=_learner_level(seed_snapshot),
            language=str((seed_snapshot.get("preferences") or {}).get("language") or "en"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail={"error": "pack_unavailable", "message": type(exc).__name__}
        ) from exc
    snapshot = _snapshot(
        session, student.id, pack, None, topic_key=body.topic, concept_id=body.concept_id
    )
    simple = adapter.write_frozen_lesson(pack, body.concept_id, snapshot, model=None)
    explanation_id = _new_explanation_id(simple)
    plan_id = _record(student.id, simple, session_id=None, explanation_id=explanation_id)
    job_id = _schedule_upgrade(
        background,
        student_id=student.id,
        pack=pack,
        snapshot=snapshot,
        result_key="plan",
        concept_id=body.concept_id,
        item_id=None,
        learner_answer=None,
        memory=snapshot.get("lesson_explanations") or [],
        fallback=simple,
        fallback_plan_id=plan_id,
        session_id=None,
        explanation_id=explanation_id,
    )
    return {
        "plan": simple.as_dict(),
        "plan_id": plan_id,
        "job_id": job_id,
        "upgrade_state": "running" if job_id else "not_available",
    }


@router.post("/explain-wrong")
def explain_wrong(
    body: WrongBody,
    background: BackgroundTasks,
    session: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Explain the actual answer against its matched, authored misconception."""
    seed_snapshot = _snapshot(session, student.id, {}, None, topic_key=body.topic)
    level = _learner_level(seed_snapshot)
    language = str((seed_snapshot.get("preferences") or {}).get("language") or "en")
    try:
        pack = _pack_for_item(body.topic, body.item_id, level=level, language=language)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail={"error": "item_unavailable", "message": type(exc).__name__}
        ) from exc
    if pack is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "item_unavailable",
                "message": "No verified item with that id exists in this topic.",
            },
        )
    pack_map = _as_mapping(pack)
    item = next(
        (
            row
            for row in pack_map.get("items") or []
            if isinstance(row, Mapping) and str(row.get("id") or "") == body.item_id
        ),
        None,
    )
    concept_id = str(item.get("concept_id") or "") if item else ""
    if not item or not concept_id:
        raise HTTPException(
            status_code=404,
            detail={"error": "item_unavailable", "message": "Item is not part of this Pack."},
        )
    snapshot = _snapshot(
        session, student.id, pack, None, topic_key=body.topic, concept_id=concept_id
    )
    memory = snapshot.get("lesson_explanations") or []
    simple = adapter.explain_frozen_after_wrong(
        pack, body.item_id, body.answer, snapshot, memory, model=None
    )
    if "answer:not_wrong" in simple.rejection_reasons:
        raise HTTPException(
            status_code=422,
            detail={"error": "answer_not_wrong", "message": "This answer matches the checked key."},
        )
    explanation_id = _new_explanation_id(simple)
    plan_id = _record(student.id, simple, session_id=None, explanation_id=explanation_id)
    job_id = _schedule_upgrade(
        background,
        student_id=student.id,
        pack=pack,
        snapshot=snapshot,
        result_key="explanation",
        concept_id=concept_id,
        item_id=body.item_id,
        learner_answer=body.answer,
        memory=memory,
        fallback=simple,
        fallback_plan_id=plan_id,
        session_id=None,
        explanation_id=explanation_id,
    )
    return {
        "explanation": simple.as_dict(),
        "plan_id": plan_id,
        "job_id": job_id,
        "upgrade_state": "running" if job_id else "not_available",
    }


@router.post("/reteach")
def reteach_lesson(
    body: ReteachBody,
    background: BackgroundTasks,
    session: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Code selects the next re-teaching method from profile and stored teaching memory."""
    snapshot = _snapshot(
        session, student.id, {}, None, topic_key=body.topic, concept_id=body.concept_id
    )
    pack = _get_pack(
        body.topic,
        body.concept_id,
        level=_learner_level(snapshot),
        language=str((snapshot.get("preferences") or {}).get("language") or "en"),
    )
    snapshot = _snapshot(
        session, student.id, pack, None, topic_key=body.topic, concept_id=body.concept_id
    )
    memory = snapshot.get("lesson_explanations") or []
    simple = adapter.reteach_frozen(pack, body.concept_id, snapshot, memory, model=None)
    explanation_id = _new_explanation_id(simple)
    plan_id = _record(student.id, simple, session_id=None, explanation_id=explanation_id)
    job_id = _schedule_upgrade(
        background,
        student_id=student.id,
        pack=pack,
        snapshot=snapshot,
        result_key="explanation",
        concept_id=body.concept_id,
        item_id=None,
        learner_answer=None,
        memory=memory,
        fallback=simple,
        fallback_plan_id=plan_id,
        session_id=None,
        explanation_id=explanation_id,
    )
    return {
        "explanation": simple.as_dict(),
        "plan_id": plan_id,
        "job_id": job_id,
        "upgrade_state": "running" if job_id else "not_available",
    }


@router.get("/upgrade/{job_id}")
def get_upgrade(job_id: str, student: Student = Depends(student_from_request)) -> dict[str, Any]:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job is None or job.owner_id != student.id:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "upgrade_unavailable",
                    "message": "No lesson upgrade is available.",
                },
            )
        result = dict(job.result)
        state = job.state
        result_key = job.result_key
    if state == "running":
        return {"job_id": job_id, "state": state}
    return {"job_id": job_id, "state": state, result_key: result}


def _grade_payload(
    *, item: Mapping[str, Any], selected: Any
) -> tuple[bool, str | None, str | None, str | None]:
    """Resolve a learner's pick against the Pack's key.  Returns
    ``(correct, misconception_id, selected_option_id, selected_option_text)``.
    The key is *never* sent back to the caller — the misconception id is the
    contract the follow-up /explain-wrong route needs.
    """
    key = str(item.get("key") or "").strip()
    options = item.get("options") or []
    selected_id = str(selected).strip()
    selected_text: str | None = None
    matched: Mapping[str, Any] | None = None
    if isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
        for option in options:
            if not isinstance(option, Mapping):
                continue
            if str(option.get("id") or "").strip() == selected_id:
                matched = option
                selected_text = str(option.get("text") or "").strip() or None
                break
        if matched is None:
            for option in options:
                if not isinstance(option, Mapping):
                    continue
                if str(option.get("text") or "").strip().casefold() == selected_id.casefold():
                    matched = option
                    selected_text = str(option.get("text") or "").strip() or None
                    break
    correct = bool(key and matched is not None and str(matched.get("id") or "").strip() == key)
    misconception_id: str | None = None
    if matched is not None and not correct:
        misconception_id = str(matched.get("misconception_id") or "").strip() or None
    return (
        correct,
        misconception_id,
        (str(matched.get("id") or "").strip() if matched is not None else None),
        selected_text,
    )


@router.post("/grade")
def grade(
    body: GradeBody,
    session: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Round 3 (Track S): server-side grading of a check option.

    The answer key never leaves the server.  The response carries the
    `correct` flag and, when the learner was wrong, the matched misconception
    id.  The client uses `correct` to show a brief confirmation, then calls
    /explain-wrong with the option text to get the misconception-specific
    follow-up.
    """
    level = _learner_level(_snapshot(session, student.id, {}, None, topic_key=body.topic))
    language = str(
        (
            _snapshot(session, student.id, {}, None, topic_key=body.topic).get("preferences") or {}
        ).get("language")
        or "en"
    )
    try:
        pack = _pack_for_item(body.topic, body.item_id, level=level, language=language)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail={"error": "item_unavailable", "message": type(exc).__name__}
        ) from exc
    if pack is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "item_unavailable",
                "message": "No verified item with that id exists in this topic.",
            },
        )
    pack_map = _as_mapping(pack)
    item = next(
        (
            row
            for row in pack_map.get("items") or []
            if isinstance(row, Mapping) and str(row.get("id") or "") == body.item_id
        ),
        None,
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "item_unavailable", "message": "Item is not part of this Pack."},
        )
    correct, misconception_id, selected_id, selected_text = _grade_payload(
        item=item, selected=body.selected
    )
    return {
        "item_id": body.item_id,
        "selected": selected_id,
        "selected_text": selected_text,
        "correct": correct,
        "misconception_id": misconception_id,
    }


__all__ = ["router"]
