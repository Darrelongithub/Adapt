"""Which model answers which question, and why that question gets that model.

One table, in code, so "the cheap model does the cheap work" is a property of the
build rather than a habit. Every call to :meth:`adapt.ai.llm.GeminiLLM.complete` names a
``purpose`` (it always did -- the audit row needs it), and this module turns that name
into a **class of work**:

``light``
    Answers that are checked against a fixed list or a rule, where being wrong is
    cheap because code rejects the answer and falls back: placing free text into one
    of five subject families, rating a topic against a fixed skill list, two sentences
    of feedback over a grade that was already computed, three sentences about frames a
    simulation already produced, one retention question about a topic.

``standard``
    The middle of the product: the teaching reply, the second-opinion teach-back
    judgement, the repair pass on a rejected scene. The wording *is* the product here,
    so the model has to be good at prose -- but the prompt is short and the answer is
    checked.

``deep``
    Open-ended authoring where a weak answer costs a rewrite and a wrong one can
    mislead: writing new practice items, authoring a scene specification from a
    request, phrasing a proposal to a learner about their own learning.

Two consequences worth stating plainly, because both are easy to get wrong:

* **A tier is a model name from configuration, never a hard-coded string.** The table
  says *class*; ``ADAPT_GEMINI_MODEL_LIGHT`` / ``_STANDARD`` / ``_DEEP`` say which
  model that class is on this deployment, and ``ADAPT_GEMINI_TASK_MODELS`` can pin one
  task (``{"ai2_explain": "..."}``) without touching code.
* **The free tier is the constraint, not an afterthought.** A deep task that falls back
  to the local reasoner is a worse answer; a light task that does is barely noticeable.
  That asymmetry is why the light class exists at all: it is the class whose failure the
  learner does not see, so it is the class that can run first and most.

:func:`report` is the operator's view (tier, model, call counts) and is printed by
``adapt status``. It is deliberately *not* on any HTTP surface: §3 of the brief keeps
provider and model names off every public response, and ``adapt.security.secrecy``
strips them from bodies even if a route forgets.

``purpose`` values that are not in :data:`TASKS` still work -- they get
:data:`DEFAULT_POLICY` (``standard``) -- but they are *recorded* by the client and
reported by :func:`report`, and a test walks the source for every ``purpose="..."``
literal and asserts it is registered. A new call site cannot quietly skip the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: The three classes of work. Ordered cheapest-first, which is also the order the
#: fallback story runs in: a light task that cannot reach a model degrades to a rule.
Tier = Literal["light", "standard", "deep"]

TIERS: tuple[Tier, ...] = ("light", "standard", "deep")

#: One sentence per class, for the CLI report and for anyone reading a log line.
TIER_MEANING: dict[str, str] = {
    "light": "checked against a fixed list or a rule; cheap to redo, safe to lose",
    "standard": "teaching and judgement, where the wording is the product",
    "deep": "open-ended authoring, where a weak answer costs a rewrite",
}


@dataclass(frozen=True)
class TaskPolicy:
    """What class of work one ``purpose`` is, and what the call site asks for.

    ``temperature`` and ``max_output_tokens`` are the values the call site passes. They
    are recorded here so the table can be read as the answer to "what is this app
    spending on this task?", and so a call site that forgets to pass them still gets a
    sane default from :func:`plan`. An explicitly passed keyword always wins: the
    spoken variant of a teaching reply is genuinely a different size from the written
    one, and a table that pretended otherwise would be a table people work around.
    """

    tier: Tier
    temperature: float
    max_output_tokens: int
    #: True when the *static head* of this prompt (system instructions, rubric, subject
    #: catalogue) is the same for every learner and worth a context cache. See
    #: :mod:`adapt.ai.context_cache`. False only for the operator's connection probe,
    #: which is one request that shares nothing.
    cacheable: bool = True
    #: Model calls this request needs, as designed. 1 is the floor; anything above 1 is
    #: either a documented retry or a chain that has not been merged yet, and the comment
    #: has to say which. ``tests/test_ai_efficiency.py`` compares this with what the call
    #: sites actually do.
    calls: int = 1
    #: Purposes this one used to be a separate call to, now answered in one prompt
    #: (:mod:`adapt.ai.chain`). Empty means "this was always one call".
    joins: tuple[str, ...] = ()
    note: str = ""


#: Fallback for an unregistered purpose: the middle class, so an unknown call site is
#: not silently cheap. ``GeminiLLM`` records the names it saw.
DEFAULT_POLICY = TaskPolicy(
    tier="standard",
    temperature=0.35,
    max_output_tokens=800,
    note="unregistered purpose: treated as standard work until it is named in the table",
)


def _p(
    tier: Tier,
    temperature: float,
    max_output_tokens: int,
    note: str,
    *,
    cacheable: bool = True,
    calls: int = 1,
    joins: tuple[str, ...] = (),
) -> TaskPolicy:
    return TaskPolicy(
        tier=tier,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        cacheable=cacheable,
        calls=calls,
        joins=joins,
        note=note,
    )


#: Every model call the app makes, by audit purpose. The note is the justification for
#: the class -- a tier nobody can argue with is a tier nobody can maintain.
TASKS: dict[str, TaskPolicy] = {
    # -- learner intelligence (AI 1) --------------------------------------- #
    "ai1_propose": _p(
        "deep",
        0.25,
        700,
        "writes a proposal about a learner's own learning, in five parts, with a hedged "
        "hypothesis and a rival explanation: the wording is the product and a bad one is "
        "read as a diagnosis",
    ),
    "ai1_retention_check": _p(
        "light",
        0.3,
        300,
        "one question plus a rationale about a topic the learner already passed; the "
        "question is shown for approval and the schedule is decided by code",
    ),
    # -- educational intelligence (AI 2) ----------------------------------- #
    "ai2_explain": _p(
        "standard",
        0.45,
        900,
        "the teaching reply itself; every sentence is checked against the grounded local "
        "answer before it is used, so the model extends rather than replaces it",
    ),
    "ai2_teachback": _p(
        "standard",
        0.0,
        420,
        "second-opinion checklist judgement over a learner's explanation; quotes must be "
        "verbatim or the rule grader stands",
    ),
    "ai2_feedback": _p(
        "light",
        0.3,
        320,
        "two or three sentences over a grade that rules already computed",
    ),
    "ai2_generate_practice": _p(
        "deep",
        0.7,
        1200,
        "writes new questions; each one is held to the item contract and calibrated or "
        "retired from real answers afterwards",
    ),
    "ai2_curate": _p(
        "light",
        0.2,
        520,
        "picks from search results it was handed; a pick whose URL was not in the input "
        "is refused",
    ),
    "ai2_scene_spec": _p(
        "deep",
        0.2,
        700,
        "authors a scene specification for a simulation the server already implements; "
        "the validator decides whether it may be shown",
    ),
    "ai2_scene_fix": _p(
        "standard",
        0.0,
        700,
        "the one retry, with the validator's reasons in the prompt; a repair pass, not a "
        "rewrite from nothing",
    ),
    # -- narration (AI 3) -------------------------------------------------- #
    "ai3_narrate": _p(
        "light",
        0.2,
        320,
        "three sentences about frames a simulation already produced, using its numbers; "
        "the deterministic path is checked first and this only fills the gap",
    ),
    "ai3_lesson_plan": _p(
        "standard",
        0.4,
        1400,
        "authors a whole lesson plan against the frozen Pack; the proposal is only "
        "shown after strict whole-plan validation (block contract, cited facts, "
        "length budgets, no answer leakage) and any rejection falls back to the "
        "deterministic lesson, so the wording is the product but never unchecked",
    ),
    "ai3_explain_wrong": _p(
        "standard",
        0.35,
        700,
        "a misconception-specific reply to an answer the learner already gave; it is "
        "checked against that item's key and matched misconception before it is "
        "shown, and the deterministic misconception reply stands when it is refused",
    ),
    "ai3_reteach": _p(
        "standard",
        0.4,
        900,
        "re-teaches one concept by a method code chose (simpler words, smaller step, "
        "example or visual) with the attempts so far as context; validated like the "
        "lesson plan, with the deterministic re-teach as the fallback",
    ),
    # -- subject placement (the round) ------------------------------------- #
    "subject_placement": _p(
        "light",
        0.0,
        700,
        "ONE call that both places free text in a subject family and rates that family's "
        "skills: two decisions that need the same reading of the same words",
        joins=("topic_resolve", "subject_leanings"),
    ),
    "topic_resolve": _p(
        "light",
        0.0,
        160,
        "places free text into one of five families; code holds the decision, so a wrong "
        "family is a fallback rather than a wrong answer",
    ),
    "subject_leanings": _p(
        "light",
        0.1,
        520,
        "numbers about a topic against a fixed skill list, clamped and rejected when "
        "degenerate",
    ),
    # -- console ----------------------------------------------------------- #
    "console_connection_test": _p(
        "light",
        0.0,
        8,
        "the operator's one-word connectivity probe; shares no context with anything",
        cacheable=False,
    ),
}


def policy(task: str) -> TaskPolicy:
    """The policy for a purpose, or :data:`DEFAULT_POLICY` if it is not registered."""
    return TASKS.get(str(task or ""), DEFAULT_POLICY)


def is_registered(task: str) -> bool:
    return str(task or "") in TASKS


def model_for_tier(tier: str, settings: Any) -> str:
    """The configured model name for a class, defaulting to the middle class.

    The three settings are the *only* place a model name is written down
    (``config.Settings.gemini_model_light`` etc.), which is what makes swapping a
    deployment's models an environment change rather than a code change.
    """
    field = {
        "light": "gemini_model_light",
        "standard": "gemini_model_standard",
        "deep": "gemini_model_deep",
    }.get(str(tier), "gemini_model_standard")
    name = str(getattr(settings, field, "") or "").strip()
    return name or str(getattr(settings, "gemini_model_standard", "") or "").strip()


def pinned_models(settings: Any) -> dict[str, str]:
    """Operator overrides, most specific last: per-task, then per-role.

    ``ADAPT_GEMINI_TASK_MODELS='{"ai2_explain": "..."}'`` pins one task.
    ``ADAPT_GEMINI_MODEL_AI1``/``_AI2`` pin every task a role performs, which is how a
    deployment that wants one model for everything gets one without editing the table.
    """
    out: dict[str, str] = {}
    for role, field in (("ai1", "gemini_model_ai1"), ("ai2", "gemini_model_ai2")):
        value = str(getattr(settings, field, "") or "").strip()
        if value:
            out[role] = value
    raw = getattr(settings, "gemini_task_models", None)
    if isinstance(raw, dict):
        for task, name in raw.items():
            text = str(name or "").strip()
            if text:
                out[f"task:{task}"] = text
    return out


def model_for_purpose(task: str, settings: Any, *, role: str = "ai2", model: str | None = None) -> str:
    """Resolve one call's model: explicit argument, then per-task pin, then role pin, then tier."""
    explicit = str(model or "").strip()
    if explicit:
        return explicit
    pins = pinned_models(settings)
    if f"task:{task}" in pins:
        return pins[f"task:{task}"]
    if role in pins:
        return pins[role]
    return model_for_tier(policy(task).tier, settings)


@dataclass(frozen=True)
class CallPlan:
    """What one call will actually do: the answer :func:`plan` returns to the client."""

    task: str
    tier: str
    model: str
    temperature: float
    max_output_tokens: int
    cacheable: bool
    calls: int
    joins: tuple[str, ...]
    pinned: bool
    registered: bool
    note: str


def plan(
    task: str,
    settings: Any,
    *,
    role: str = "ai2",
    model: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
) -> CallPlan:
    """The full decision for one call, with every override applied.

    Passing ``model``/``temperature``/``max_output_tokens`` overrides the table (call
    sites do this where the size genuinely depends on the answer's shape, like the
    spoken variant of a teaching reply). Those overrides are still reported: what the
    table is for is making the *default* visible, not in pretending overrides do not
    exist.
    """
    task = str(task or "")
    pol = policy(task)
    pins = pinned_models(settings)
    chosen = model_for_purpose(task, settings, role=role, model=model)
    pinned = bool(model) or f"task:{task}" in pins or role in pins
    return CallPlan(
        task=task,
        tier=pol.tier,
        model=chosen,
        temperature=pol.temperature if temperature is None else float(temperature),
        max_output_tokens=(
            pol.max_output_tokens if max_output_tokens is None else int(max_output_tokens)
        ),
        cacheable=pol.cacheable,
        calls=pol.calls,
        joins=pol.joins,
        pinned=pinned,
        registered=is_registered(task),
        note=pol.note,
    )


def public_table() -> list[dict[str, Any]]:
    """The task table with no model names in it, for any surface that must stay neutral.

    Keys here are chosen to survive :func:`adapt.security.secrecy.scrub_public_payload`
    *and* to carry no provider identity if it does not run: a class of work, whether the
    prompt has a static head, how many calls the request needs, and which decisions used
    to be separate calls. ``calls`` above 1 is the interesting number -- it is the
    outstanding work.
    """
    rows = []
    for task, pol in sorted(TASKS.items()):
        rows.append(
            {
                "task": task,
                "class": pol.tier,
                "one_call": True,
                "calls": pol.calls,
                "joins": list(pol.joins),
                "shared_context": pol.cacheable,
                "why": pol.note,
            }
        )
    return rows


def report(settings: Any, *, unknown: tuple[str, ...] = ()) -> dict[str, Any]:
    """The operator's view: which model each class uses, and how many tasks are in it.

    Printed by ``adapt status``. Names appear here because this is a console report on
    the operator's own machine; the same data on an HTTP surface would be stripped by
    the secrecy boundary, so it is not put on one.
    """
    by_tier: dict[str, list[str]] = {tier: [] for tier in TIERS}
    for task, pol in sorted(TASKS.items()):
        by_tier[pol.tier].append(task)
    pins = pinned_models(settings)
    return {
        "classes": {
            tier: {
                "model": model_for_tier(tier, settings),
                "meaning": TIER_MEANING[tier],
                "tasks": by_tier[tier],
            }
            for tier in TIERS
        },
        "pins": pins,
        "unknown_purposes": sorted({str(u) for u in unknown if u}),
        "note": (
            "every task is one model call unless a retry is documented; the count of calls per "
            "learner request is what the latency budget is made of"
        ),
    }


__all__ = [
    "CallPlan",
    "DEFAULT_POLICY",
    "TASKS",
    "TIERS",
    "TIER_MEANING",
    "TaskPolicy",
    "Tier",
    "is_registered",
    "model_for_purpose",
    "model_for_tier",
    "pinned_models",
    "plan",
    "policy",
    "public_table",
    "report",
]
