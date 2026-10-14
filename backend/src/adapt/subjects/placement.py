"""Subject placement in one model call: which family, and what that family's topic asks of you.

This is the app's worked example of the merge described in :mod:`adapt.ai.chain`, and it is a
real one, not a demonstration: ``POST /topics/resolve``, ``GET /subjects/plan`` and
``POST /subjects/round`` each used to make **two** model calls back to back --

1. ``topic_resolve``: put the learner's free text into one of five subject families;
2. ``subject_leanings``: rate that family's skills by how hard the topic leans on them.

-- and both of them needed the same thing: a reading of the same few words. The second call
was even told the first call's answer. That is one question split across two round trips,
which costs a second latency, a second rate-limit slot, and a second chance to fail into a
fallback.

The merged call answers both at once (:data:`PLACEMENT_CHAIN`), and the two validators are the
*same functions* the sequential path uses:

* :func:`adapt.subjects.resolve.accept_placement` for the family/title/level/confidence;
* :func:`adapt.subjects.leanings._validate_model_ratings` for the ratings -- against the skill
  list of the family the model chose in the same reply, which is exactly the coupling that
  made the two calls feel like one question.

Two properties are worth stating because they are what makes this safe to ship:

* **The merged call cannot accept more than the sequential one did.** The validators are
  shared, so a merged reply that would have been rejected by call two is still rejected.
  "Merging" here means fewer round trips, never looser rules.
* **Every part falls back on its own.** If the model answers the placement and then produces
  ratings that fail the skill check, the placement is used and the ratings fall back to the
  labelled generic profile -- the same outcome the sequential path produced, minus one call.

``ADAPT_AI_SINGLE_CALL=0`` restores the two-call path (:func:`place_and_rate` falls back to
it), which exists so the change can be measured against what it replaced rather than believed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from ..ai import chain as CH
from ..ai import static_context as SC
from .leanings import (
    Leanings,
    _hint_from,
    _validate_model_ratings,
    generic_fallback,
    model_leanings,
    rate_leanings,
)
from .leanings import (
    _as_data as _topic_as_data,
)
from .resolve import (
    Resolved,
    accept_placement,
    refine_with_model,
    resolve_topic,
)
from .taxonomy import FAMILY_IDS, known_skill_ids
from .topics import topic_spec

log = logging.getLogger("adapt.subjects.placement")


def _placement_parse(data: Mapping[str, Any], context: dict[str, Any]) -> tuple[Any, list[str]]:
    """Step 1: the family. Returns the ``Resolved`` the model's answer implies, or ``None``."""
    accepted = accept_placement(data, level_hint=context["hint"].get("level_hint"))
    if accepted is None:
        return None, ["the placement did not pass the family/level check"]
    deterministic: Resolved = context["deterministic"]
    hint = context["hint"]
    return (
        Resolved(
            raw=deterministic.raw,
            normalised=deterministic.normalised,
            topic_key=deterministic.topic_key,
            title=accepted["title"],
            subject=str(hint.get("subject") or accepted["family"]),
            family=accepted["family"],
            level_hint=accepted.get("level_hint") or deterministic.level_hint,
            source="model",
            bank_code=deterministic.bank_code,
            bank_topic_id=deterministic.bank_topic_id,
            alternatives=deterministic.alternatives,
            note=(
                f"a model placed this in {accepted['family']} with confidence "
                f"{accepted['confidence']:.2f}, and rated that family's skills in the same call; "
                "both answers were validated against the taxonomy before anything was chosen"
            ),
            topic_text=deterministic.topic_text,
        ),
        [],
    )


def _ratings_parse(data: Mapping[str, Any], context: dict[str, Any]) -> tuple[Any, list[str]]:
    """Step 2: the ratings, validated against the family the *same reply* chose."""
    placed = (context.get("_accepted") or {}).get("placement")
    family = placed.family if isinstance(placed, Resolved) else str(context["hint"].get("family") or "generic")
    allowed = list(known_skill_ids(family))
    ratings, notes = _validate_model_ratings(data, allowed=allowed, family=family)
    if not ratings:
        return None, notes or ["the ratings were not usable"]
    reason = str(data.get("reason") or "").strip()[:220]
    if reason:
        notes.append(f"model's own reason: {reason}")
    hint = {**context["hint"], "family": family}
    return (
        model_leanings(hint=hint, family=family, ratings=ratings, notes=notes),
        notes,
    )


def _placement_fallback(context: dict[str, Any]) -> Resolved:
    """No usable answer: the deterministic reading stands, unchanged and labelled."""
    return context["deterministic"]


def _ratings_fallback(context: dict[str, Any]) -> Leanings:
    """No usable ratings: the generic profile for whichever family we ended up in.

    Which family that is depends on whether step 1 was accepted, so this reads the accepted
    values too -- a fallback that guessed a family would be the wrong kind of honest.
    """
    placed = (context.get("_accepted") or {}).get("placement")
    hint = context["hint"]
    family = placed.family if isinstance(placed, Resolved) else str(hint.get("family") or "generic")
    if family not in FAMILY_IDS:
        family = "generic"
    return generic_fallback(
        topic_key=str(hint.get("topic_key") or hint.get("title") or "this topic"),
        title=str(hint.get("title") or "this topic"),
        subject=str(hint.get("subject") or ""),
        family=family,
        model_note=(
            "the model's placement or ratings did not pass the taxonomy check, so this is the "
            "generic profile for that family rather than a claim about your topic"
        ),
    )


#: The merged call. Its system head is ``static_context.block("placement")``: the instructions,
    # the five families, every skill with what it measures, and the rating scale -- identical for
#: every learner, which is what makes it worth caching (see :mod:`adapt.ai.context_cache`).
PLACEMENT_STEP = CH.Step(
    key="placement",
    ask="family (one id from the list), topic_title (short), level_hint (from the list or empty), confidence (0..1)",
    spec={"family": "str", "topic_title": "str", "level_hint": "str", "confidence": "num"},
    parse=_placement_parse,
    fallback=_placement_fallback,
    note="the deterministic resolver reads the same words; a model only improves the weak outcomes",
)

RATINGS_STEP = CH.Step(
    key="ratings",
    ask="ratings (array of {skill, rating} for the family you chose) and reason (one short sentence)",
    spec={
        "ratings": (
            "array",
            {
                "type": "OBJECT",
                "properties": {"skill": {"type": "STRING"}, "rating": {"type": "INTEGER"}},
                "required": ["skill", "rating"],
            },
        ),
        "reason": "str",
    },
    parse=_ratings_parse,
    fallback=_ratings_fallback,
    note="ids are checked against the family's own skill list; a flat answer is refused",
)

PLACEMENT_CHAIN = CH.Chain(
    name="subject_placement",
    purpose="subject_placement",
    build_user=lambda ctx: _build_user(ctx),
    steps=(PLACEMENT_STEP, RATINGS_STEP),
    note=(
        "two decisions that need one reading of one piece of text: which family, and how much "
        "the topic leans on that family's skills"
    ),
)


def _build_user(context: dict[str, Any]) -> str:
    """The dynamic half: the learner's words as data, plus what code already worked out.

    The learner's topic name is the *only* thing here that came from outside, and it is
    wrapped by ``leanings._as_data`` -- the same tagging the sequential prompts used -- so the
    merged prompt inherits the injection handling rather than re-inventing it.
    """
    hint = context["hint"]
    deterministic: Resolved = context["deterministic"]
    return "\n".join(
        [
            _topic_as_data(hint.get("title") or hint.get("raw") or ""),
            "",
            f"our own reading so far: topic_key={deterministic.topic_key!r}, "
            f"family={deterministic.family!r}, level={deterministic.level_hint or '(none)'} "
            f"({deterministic.source})",
            "if your reading differs from ours, say so with your answer and give your confidence",
        ]
    )


def _run(coro: Any) -> Any:
    """Run the one call from sync code. Inside a live loop there is no honest way to block."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return None


def place_and_rate(
    text: str,
    *,
    llm: Any = None,
    student_id: str | None = None,
    bank: Mapping[str, Any] | None = None,
    topic_ids: Mapping[str, str] | None = None,
    allow_model: bool = True,
    need_ratings: bool = True,
) -> tuple[Resolved, Leanings, dict[str, Any]]:
    """Free text in; a resolved topic, its skill leanings, and how they were produced.

    Returns ``(resolved, leanings, meta)`` where ``meta`` is the call's own account of itself:
    how many model calls it took, which questions a model answered and which code did, and the
    validation notes. Callers pass that through to the API response, because "we asked once"
    and "we asked twice" are different facts about a placement and the screen may say so.

    **How many calls, and why.** The two questions are not equally necessary, and a merge that
    asked both when only one was needed would be a regression dressed as an optimisation:

    * a topic the *authored* tables already cover needs **no** model call -- the answer exists
      in code, and asking about it would replace a written-down rating with a sample
      (``the authored answer is used and nothing is asked``);
    * a topic that needs **one** answer asks exactly that one (``refine_with_model`` for the
      placement, ``rate_leanings`` for the ratings) -- including the case where the caller only
      wants the topic: ``need_ratings=False`` says so, and then the ratings are answered locally
      instead of being bought and thrown away;
    * a topic that needs **both** -- free text nothing matched, so the family and the ratings
      are each unknown -- asks one prompt that answers both, which is the only case where
      merging saves a round trip, and the case this module exists for.
    """
    raw = str(text or "")
    deterministic = resolve_topic(raw, bank=bank, topic_ids=topic_ids)
    hint = _hint_from(deterministic.as_dict())
    configured = llm is not None and getattr(llm, "configured", False)
    single = bool(getattr(getattr(llm, "settings", None), "ai_single_call", True))

    spec = topic_spec(str(deterministic.topic_key or ""))
    authored_leanings = spec is not None and bool(spec.leanings)
    # A deterministic match into a known topic is better than anything a model can say about
    # it, and re-asking would turn a written-down placement into a sample. Same rule the
    # sequential path always had, kept here so the merge cannot quietly drop it.
    refine = allow_model and configured and deterministic.source not in ("authored", "curriculum")
    rate = allow_model and configured and not authored_leanings and need_ratings
    calls_needed = int(refine) + int(rate)

    if calls_needed == 0:
        return deterministic, rate_leanings(hint, llm=None, allow_model=False), {
            "chain": None,
            "calls": 0,
            "replaces": [],
            "sources": {
                # "authored" means a table in this repository answered it; "code" means the
                # deterministic resolver did; neither is a model, and the meta says which.
                "placement": deterministic.source
                if deterministic.source in ("authored", "curriculum")
                else "code",
                "ratings": "authored" if authored_leanings else "code",
            },
            "note": (
                "the authored tables answer this topic, so no model was asked: "
                f"placement is {deterministic.source} and the ratings are the topic's own"
                if authored_leanings
                else "no model is configured on this machine, so nothing was asked"
            ),
        }

    if calls_needed == 1 or not single:
        # One call is what one answer costs; two when there is nothing to merge (the merge is
        # switched off, or only one of the two questions is open). Either way this is the
        # path that existed before the merge existed, so it is also the control case.
        resolved = refine_with_model(deterministic, llm=llm, student_id=student_id) if refine else deterministic
        leanings = (
            rate_leanings({**resolved.as_dict(), "raw": raw}, llm=llm, student_id=student_id, allow_model=True)
            if rate
            else rate_leanings(hint, llm=None, allow_model=False)
        )
        return resolved, leanings, {
            "chain": None,
            "calls": calls_needed,
            "replaces": [],
            "sources": {"placement": "model" if refine else "code", "ratings": "model" if rate else "authored"},
            "note": (
                "one model call was needed, so one was made"
                if calls_needed == 1
                else "the merge is switched off on this deployment, so the two questions were asked separately"
            ),
        }

    context = {"hint": hint, "deterministic": deterministic, "raw": raw}
    result = _run(
        CH.run(
            llm,
            PLACEMENT_CHAIN,
            context,
            student_id=student_id,
            role="ai2",
            temperature=0.0,
            max_output_tokens=700,
            cache_prefix=SC.block("placement"),
        )
    )
    if result is None:
        # A running event loop: the model cannot be called from here. The same condition
        # `leanings._run` reports, with the same outcome -- the deterministic answer plus the
        # labelled generic profile.
        leanings = rate_leanings(hint, llm=None, allow_model=False)
        return deterministic, leanings, {
            "chain": "subject_placement",
            "calls": 0,
            "replaces": [],
            "note": (
                "the model could not be asked from inside a running event loop, so the topic was "
                "placed by code and the generic skill profile was used; both are labelled"
            ),
        }

    resolved: Resolved = result.values.get("placement") or deterministic
    leanings: Leanings = result.values.get("ratings") or _ratings_fallback(context)
    meta = {
        "chain": result.name,
        "calls": result.hops,
        "replaces": list(result.replaced),
        "sources": dict(result.sources),
        "notes": {k: list(v) for k, v in result.notes.items()},
        "status": result.status,
        "latency_ms": result.latency_ms,
        "cached_tokens": result.cached_tokens,
        "hops_saved": max(0, calls_needed - result.hops),
        "note": (
            "one call answered both questions: which family this belongs to and how much the "
            "topic leans on that family's skills"
            if result.used_model
            else "the model's answer was not usable, so code placed the topic and the generic skill profile was used"
        ),
    }
    return resolved, leanings, meta


def chain_summary() -> dict[str, Any]:
    """The chain's shape, for the docs and the tests: steps, validator, fallback."""
    return {
        "name": PLACEMENT_CHAIN.name,
        "purpose": PLACEMENT_CHAIN.purpose,
        "steps": [
            {"key": step.key, "ask": step.ask, "note": step.note, "keys": sorted(step.spec)}
            for step in PLACEMENT_CHAIN.steps
        ],
        "replaces": ["topic_resolve", "subject_leanings"],
        "static_head": "placement",
    }


__all__ = ["PLACEMENT_CHAIN", "RATINGS_STEP", "PLACEMENT_STEP", "chain_summary", "place_and_rate"]
