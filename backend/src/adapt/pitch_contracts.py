"""Shared cross-agent wire contracts and helpers for the pitch demo & engine API.

Owned by Agent 4 (Handler) as a zero-side-effect schema and deterministic-mapping
module so Agent 1 (backend/capacity), Agent 2 (demo UI), and Agent 3 (HTTP
integration endpoint) share exact field names without editing one another's files.

Every response schema in this module is tested against
``ConfigurationSecrecyMiddleware`` (``adapt.security.secrecy.scrub_public_payload``)
so no field is silently stripped on the wire (never use response keys containing
``provider``, ``llm``, ``quota``, ``budget``, ``headroom``, ``secret``, ``buckets``,
``limiter``, ``ratelimit``, ``token`` without ``csrf``, or bare ``model`` without
``learner_``/``learning_``/``model_version``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from adapt.ai.adapter import AdaptationError, adapter

# --------------------------------------------------------------------------- #
# Seam 1 & 2: Demo Mode discovery + no-password guest session (Agent 1 <-> 2)
# --------------------------------------------------------------------------- #

DEMO_MODE_ENV = "ADAPT_DEMO_MODE"
DEMO_UI_ENV = "NEXT_PUBLIC_ADAPT_DEMO"

#: Canonical guest-creation endpoint already present in backend + frontend mock.ts.
GUEST_SESSION_ENDPOINT = "POST /api/v1/students"


class DemoModeFlag(BaseModel):
    """Additive flag on both ``GET /api/v1/health`` (signed-out) and ``GET /api/v1/status``."""

    model_config = ConfigDict(extra="allow")

    demo_mode: bool = False


class DemoGuestRequest(BaseModel):
    """Consent-only, passwordless guest request body for ``POST /api/v1/students``."""

    model_config = ConfigDict(extra="forbid")

    as_guest: bool = True
    consent_analytics: bool = False
    consent_share_with_llm: bool = False
    display_name: str | None = Field(default=None, max_length=120)


class DemoGuestResponse(BaseModel):
    """Secrecy-safe guest session payload returned alongside session + CSRF cookies."""

    model_config = ConfigDict(extra="allow")

    student_id: str
    csrf_token: str
    display_name: str
    created: bool = True
    demo_mode: bool = False
    onboarding_complete: bool = False
    baseline_started: bool = False
    consent_analytics: bool = False
    consent_share_with_ai: bool = False
    mode: str = "local"


# --------------------------------------------------------------------------- #
# Seam 3: Three-game phone baseline -> immediate analysis (Agent 1 <-> Agent 2)
# --------------------------------------------------------------------------- #

#: Exact sequential stage keys in ``adapt.baseline.flow`` for the <2-minute 3-game demo.
DEMO_THREE_GAME_STAGES: tuple[str, ...] = (
    "codewords_study",
    "codewords_cued",
    "quick_a",
    "vault",
)

#: When ``view["stage"] == DEMO_STOP_STAGE``, all 3 demo games are complete and
#: ``POST /api/v1/baseline/{run_id}/finish`` seals the snapshot immediately.
DEMO_STOP_STAGE = "shopkeeper"

#: Factors guaranteed to reach ``state == "measured"`` after completing the 3 demo games
#: (plus ``metacognition`` when confidence wagers are answered on Games 1-2).
DEMO_MEASURED_FACTORS: tuple[str, ...] = (
    "long_term_memory",
    "attention_self_control",
    "working_memory",
)


# --------------------------------------------------------------------------- #
# Seam 4: External HTTP adaptation contract for GenLink (Agent 3)
# --------------------------------------------------------------------------- #

#: Landed self-contained HTTP engine route path (Agent 3, PR #43).
INTEGRATION_ADAPT_ENDPOINT = "POST /api/v1/integration/adapt"

TeachingMethodName = Literal["explanation", "worked_example", "visual", "retrieval"]
LeadFormatChoice = Literal["diagram", "visual", "written", "explanation", "worked_example"]
StepSizeChoice = Literal["small", "medium", "large", "short", "long"]
LanguageChoice = Literal["en", "sw", "mixed"]
ToneChoice = Literal["plain", "gentle", "direct"]
ReadingLevelChoice = Literal["plain", "standard", "advanced"]
_STEP_SIZE_ALIASES: dict[str, str] = {
    "short": "small",
    "small": "small",
    "medium": "medium",
    "long": "large",
    "large": "large",
}


class EngineActivityItem(BaseModel):
    """One observed learner interaction sent by an external app (e.g. GenLink)."""

    model_config = ConfigDict(extra="ignore")

    item_id: str = Field(default="item-1", max_length=120)
    topic_id: str | None = Field(default=None, max_length=120)
    correct: bool
    latency_ms: int | None = Field(default=None, ge=0, le=600_000)
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Confidence in [0.0, 1.0] (or 1..4 wager / 0..100 percent, normalised).",
    )
    hint_used: bool = False
    method: TeachingMethodName | None = None
    difficulty: float | None = Field(default=None, ge=0.0, le=1.0)


class EngineQuestionInput(BaseModel):
    """Optional check question supplied with external course content."""

    model_config = ConfigDict(extra="ignore")

    question: str = Field(..., min_length=1, max_length=1000)
    answer: str | None = Field(default=None, max_length=500)
    format: str | None = Field(default=None, max_length=64)


class EngineContentInput(BaseModel):
    """Course content sent by an external app to be adapted for the learner."""

    model_config = ConfigDict(extra="ignore")

    concept: str = Field(..., min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    topic_id: str | None = Field(default=None, max_length=120)
    explanation: str | list[str] = Field(default="")
    worked_steps: list[str] = Field(default_factory=list)
    questions: list[EngineQuestionInput] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    visual_spec: dict[str, Any] | None = None
    language: LanguageChoice = "en"


class EngineLearnerInput(BaseModel):
    """Optional explicit learner preferences or profile overrides from caller."""

    model_config = ConfigDict(extra="ignore")

    lead_format: LeadFormatChoice | None = None
    step_size: StepSizeChoice | None = None
    language: LanguageChoice = "en"
    tone: ToneChoice = "plain"
    reading_level: ReadingLevelChoice = "standard"
    ladder_rung: int | None = Field(default=None, ge=0, le=5)
    timers_visible: bool = True


class EngineAdaptRequest(BaseModel):
    """Canonical HTTP request body for stateless/stateful external adaptation."""

    model_config = ConfigDict(extra="ignore")

    learner_id: str | None = Field(default=None, max_length=120)
    activity: list[EngineActivityItem] = Field(default_factory=list, max_length=500)
    learner: EngineLearnerInput = Field(default_factory=EngineLearnerInput)
    content: EngineContentInput


class EngineLearnerSummary(BaseModel):
    """Secrecy-safe summary of what Adapt inferred from the submitted activity."""

    observations: int
    accuracy: float | None = None
    mean_latency_ms: int | None = None
    estimated_mastery: float | None = None
    ladder_rung: int
    lead_format: str
    step_size: str
    max_words_per_block: int
    signals: list[str] = Field(default_factory=list)


class EngineAdaptedBlock(BaseModel):
    """One adapted teaching/check block returned to the calling application."""

    block: int
    chunk_id: str
    kind: str
    content: dict[str, Any]
    why: dict[str, Any]


class EngineAdaptResponse(BaseModel):
    """Secrecy-safe HTTP response body returned to external apps (e.g. GenLink)."""

    ok: bool = True
    learner_id: str | None = None
    concept: str
    title: str
    topic_id: str
    mode: str
    lead_format: str
    ladder_rung: int
    limits: dict[str, Any]
    learner_model: EngineLearnerSummary
    blocks: list[EngineAdaptedBlock]
    why_panel: dict[str, Any]
    fallbacks: list[str] = Field(default_factory=list)
    provenance: dict[str, Any]


def _normalise_confidence(raw: float | None) -> float | None:
    if raw is None:
        return None
    if raw <= 1.0:
        return max(0.0, min(1.0, float(raw)))
    if raw <= 4.0:
        return max(0.0, min(1.0, (float(raw) - 1.0) / 3.0))
    return max(0.0, min(1.0, float(raw) / 100.0))


def derive_adaptation_inputs(
    req: EngineAdaptRequest,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    """Turn external ``activity`` + ``learner`` into deterministic ``LessonAdapter`` inputs.

    Returns ``(levers, recent_evidence, subject_map, preferences, ladder, summary_seed)``.
    Pure and deterministic: never touches the network or an external AI service.
    """
    topic_id = (req.content.topic_id or req.content.concept).strip().lower().replace(" ", "_")
    items = list(req.activity)
    n = len(items)
    signals: list[str] = []

    correct_count = sum(1 for a in items if a.correct)
    accuracy = (correct_count / n) if n else None
    latencies = [a.latency_ms for a in items if a.latency_ms is not None]
    mean_latency = round(sum(latencies) / len(latencies)) if latencies else None

    # Laplace-smoothed mastery estimate in [0.05, 0.95] so n=1 never claims 0% or 100%.
    p_know = round((correct_count + 1.0) / (n + 2.0), 3) if n else None

    if req.learner.ladder_rung is not None:
        rung = req.learner.ladder_rung
    elif p_know is None:
        rung = 2
    elif p_know < 0.35:
        rung = 1
    elif p_know < 0.55:
        rung = 2
    elif p_know < 0.75:
        rung = 3
    elif p_know < 0.88:
        rung = 4
    else:
        rung = 5

    # Detect concrete behavioural signals from the activity stream.
    fast_wrong = sum(
        1 for a in items if (not a.correct) and a.latency_ms is not None and a.latency_ms < 2500
    )
    early_guessing = n >= 2 and (fast_wrong / n) >= 0.4
    if early_guessing:
        signals.append("early_guessing")

    overconfident_wrong = 0
    underconfident_right = 0
    for a in items:
        conf = _normalise_confidence(a.confidence)
        if conf is None:
            continue
        if (not a.correct) and conf >= 0.75:
            overconfident_wrong += 1
        elif a.correct and conf <= 0.35:
            underconfident_right += 1

    tail = items[-3:] if len(items) >= 3 else []
    tail_wrong = sum(1 for a in tail if not a.correct)
    tail_slow = sum(1 for a in tail if a.latency_ms is not None and a.latency_ms >= 25_000)
    fatigued = len(tail) == 3 and tail_wrong >= 2 and tail_slow >= 2
    if fatigued:
        signals.append("fatigued")

    hint_heavy = n >= 2 and (sum(1 for a in items if a.hint_used) / n) >= 0.5
    struggling = (accuracy is not None and accuracy < 0.5) or hint_heavy
    if struggling:
        signals.append("needs_smaller_steps")

    # Step size: explicit override > struggling/fatigue > strong mastery > medium default.
    # LessonAdapter.STEP_WORD_LIMITS uses ("small"=30, "medium"=50, "large"=75); accept
    # caller aliases "short"/"long" and normalise to those canonical lever values.
    if req.learner.step_size is not None:
        step_size = _STEP_SIZE_ALIASES.get(req.learner.step_size, "medium")
        step_source = "stated"
        step_why = f"caller requested {step_size} steps"
    elif struggling or fatigued:
        step_size = "small"
        step_source = "measured"
        step_why = "recent errors or hint use point to smaller steps before each check"
    elif n >= 4 and accuracy is not None and accuracy >= 0.85 and not hint_heavy:
        step_size = "large"
        step_source = "measured"
        step_why = f"{correct_count}/{n} recent answers were right without heavy hinting"
    else:
        step_size = "medium"
        step_source = "default"
        step_why = "default middle step size until enough activity points smaller or larger"

    # Lead format prior: explicit override > visual/worked_example for struggling > worked_example.
    if req.learner.lead_format is not None:
        lead_prior = req.learner.lead_format
        lead_source = "stated"
        lead_why = f"caller selected {lead_prior} as the opening format"
    elif struggling and req.content.visual_spec is not None:
        lead_prior = "visual"
        lead_source = "derived"
        lead_why = "recent difficulty + a supplied diagram spec favour showing structure visually"
    else:
        lead_prior = "worked_example"
        lead_source = "default"
        lead_why = "opens with a worked example unless method evidence or caller choice overrides"

    hint_policy = (
        ("offer early", "measured", "frequent errors or hints in activity unlock early hints")
        if struggling
        else ("on request", "default", "hints remain available on request")
    )

    if overconfident_wrong >= 2:
        signals.append("overconfident_errors")
        conf_lever = (
            "before_each_answer",
            "measured",
            "high confidence on wrong answers triggers a brief confidence check before answering",
        )
    elif underconfident_right >= 2:
        signals.append("underconfident_success")
        conf_lever = (
            "evidence_of_success",
            "measured",
            "correct answers under low confidence surface concrete receipts of success",
        )
    else:
        conf_lever = ("none", "default", "no confidence miscalibration signal in activity")

    halflife_hours = 12 if struggling else (48 if (accuracy or 0.0) >= 0.8 and n >= 3 else 24)
    pace = "short_blocks" if fatigued else "steady"

    levers: dict[str, Any] = {
        "step_size": {"value": step_size, "source": step_source, "why": step_why},
        "lead_format_prior": {"value": lead_prior, "source": lead_source, "why": lead_why},
        "hint_policy": {"value": hint_policy[0], "source": hint_policy[1], "why": hint_policy[2]},
        "confidence_prompting": {
            "value": conf_lever[0],
            "source": conf_lever[1],
            "why": conf_lever[2],
        },
        "review_halflife_prior": {
            "value": halflife_hours,
            "source": "measured" if n else "default",
            "why": f"review scheduled in {halflife_hours}h from recent accuracy",
        },
        "pace": {
            "value": pace,
            "source": "measured" if fatigued else "default",
            "why": "short pause inserted after slowing and slipping" if fatigued else "steady pace",
        },
    }

    rows: list[dict[str, Any]] = []
    for idx, a in enumerate(items):
        expected = 1.0 - float(a.difficulty) if a.difficulty is not None else 0.5
        row: dict[str, Any] = {
            "id": a.item_id or f"act-{idx + 1}",
            "topic_id": a.topic_id or topic_id,
            "correct": bool(a.correct),
            "score": 1.0 if a.correct else 0.0,
            "expected": max(0.05, min(0.95, expected)),
        }
        if a.method is not None:
            row["method"] = a.method
        if a.latency_ms is not None:
            row["latency_ms"] = a.latency_ms
        rows.append(row)

    recent_evidence: dict[str, Any] = {
        "rows": rows,
        "evidence_ids": [r["id"] for r in rows],
        "flags": {
            "early_guessing": early_guessing,
            "predict_first": early_guessing,
            "fatigued": fatigued,
        },
    }

    subject_map: dict[str, Any] = {}
    if p_know is not None:
        band = "building" if p_know < 0.5 else ("steady" if p_know < 0.8 else "strong")
        half_width = round(min(0.35, 0.5 / ((n + 2) ** 0.5)), 2)
        ci_low = round(max(0.0, p_know - half_width), 2)
        ci_high = round(min(1.0, p_know + half_width), 2)
        subject_map[topic_id] = {
            "topic_id": topic_id,
            "p_know": p_know,
            "n": n,
            "observations": n,
            "band": band,
            "ci": [ci_low, ci_high],
            "rung": rung,
        }

    preferences: dict[str, Any] = {
        "language": req.learner.language,
        "tone": req.learner.tone,
        "reading_level": req.learner.reading_level,
        "timers_visible": req.learner.timers_visible,
    }
    ladder = {"rung": rung}
    summary_seed = {
        "observations": n,
        "accuracy": round(accuracy, 3) if accuracy is not None else None,
        "mean_latency_ms": mean_latency,
        "estimated_mastery": p_know,
        "signals": signals,
    }
    return levers, recent_evidence, subject_map, preferences, ladder, summary_seed


def adapt_external_content(req: EngineAdaptRequest) -> EngineAdaptResponse:
    """Adapt ``req.content`` to ``req.activity`` + ``req.learner`` deterministically.

    Wraps :data:`adapt.ai.adapter.adapter` so external callers (such as GenLink)
    get validated, chunked, learner-specific blocks in milliseconds with zero
    dependency on external model availability or quota.
    """
    concept = (req.content.concept or "").strip()
    title = (req.content.title or concept).strip()
    if not concept and not title:
        raise AdaptationError("a lesson needs a concept: pass content.concept or content.title")
    topic_id = (req.content.topic_id or concept or title).strip().lower().replace(" ", "_")
    levers, recent_evidence, subject_map, preferences, ladder, summary_seed = (
        derive_adaptation_inputs(req)
    )

    content_pack: dict[str, Any] = {
        "concept": concept,
        "title": title,
        "topic_id": topic_id,
        "explanation": req.content.explanation,
        "worked_steps": list(req.content.worked_steps),
        "questions": [q.model_dump(exclude_none=True) for q in req.content.questions],
        "hints": list(req.content.hints),
        "facts": list(req.content.facts),
        "visual_spec": req.content.visual_spec,
        "language": req.content.language,
        "source": "external_caller",
    }

    plan = adapter.plan(
        levers=levers,
        subject_map=subject_map,
        ladder=ladder,
        content_pack=content_pack,
        recent_evidence=recent_evidence,
        preferences=preferences,
        concept=concept,
        title=title,
    )

    learner_summary = EngineLearnerSummary(
        observations=summary_seed["observations"],
        accuracy=summary_seed["accuracy"],
        mean_latency_ms=summary_seed["mean_latency_ms"],
        estimated_mastery=summary_seed["estimated_mastery"],
        ladder_rung=int(plan["ladder_rung"]),
        lead_format=str(plan["lead_format"]),
        step_size=str(plan["limits"]["step_size"]),
        max_words_per_block=int(plan["limits"]["max_words_per_block"]),
        signals=list(summary_seed["signals"]),
    )

    return EngineAdaptResponse(
        ok=True,
        learner_id=req.learner_id,
        concept=str(plan["concept"]),
        title=str(plan["title"]),
        topic_id=str(plan["topic_id"]),
        mode=str(plan["mode"]),
        lead_format=str(plan["lead_format"]),
        ladder_rung=int(plan["ladder_rung"]),
        limits=dict(plan["limits"]),
        learner_model=learner_summary,
        blocks=[EngineAdaptedBlock(**b) for b in plan["blocks"]],
        why_panel=dict(plan["why_panel"]),
        fallbacks=list(plan.get("fallbacks") or []),
        provenance=dict(plan["provenance"]),
    )


__all__ = [
    "DEMO_MEASURED_FACTORS",
    "DEMO_MODE_ENV",
    "DEMO_STOP_STAGE",
    "DEMO_THREE_GAME_STAGES",
    "DEMO_UI_ENV",
    "GUEST_SESSION_ENDPOINT",
    "INTEGRATION_ADAPT_ENDPOINT",
    "AdaptationError",
    "DemoGuestRequest",
    "DemoGuestResponse",
    "DemoModeFlag",
    "EngineActivityItem",
    "EngineAdaptRequest",
    "EngineAdaptResponse",
    "EngineAdaptedBlock",
    "EngineContentInput",
    "EngineLearnerInput",
    "EngineLearnerSummary",
    "EngineQuestionInput",
    "adapt_external_content",
    "derive_adaptation_inputs",
]
