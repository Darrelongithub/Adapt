"""One prompt, several answers: merging the LLM calls a request was making in sequence.

The shape this replaces is common and expensive: prompt A classifies the input, prompt B
then acts on the classification. Two round trips, two chances to hit a rate limit, two
places for the prompt to be *almost* the same -- and the second call re-reads content the
first call already had in front of it. Where the two questions need the same reading of the
same words, they belong in one prompt that returns one JSON object, and the merge is not a
clever trick: it is the observation that a model asked two questions about one input is
still one model call.

What a :class:`Chain` is:

* **one** :meth:`~adapt.ai.llm.GeminiLLM.complete` call, with a merged response schema;
* a *step per answer* -- each with its own question, its own validation, and its own local
  fallback. A step whose answer is missing or invalid does not poison the others: it falls
  back to the rules, and the fallback is recorded. That property is the whole reason this is
  safe to do, because it means the merged call can be refused in part;
* a named row in ``ai/routing.py``, so the class of work (and the fact that this used to be
  two calls) is visible in the table rather than in a comment.

What it is not: a framework. There is no retry-per-step, no planner, no tool loop. A merged
call that fails falls back everywhere, exactly as the sequential calls would have done one
after the other -- except cheaper, because there is one round trip to lose instead of two.

Validation is per step and it is deliberate that the validators are the *existing*
functions. ``subjects.placement`` reuses the resolve validator and the ratings validator, so
merging the calls cannot loosen what is accepted: the model still proposes, code still
decides, and a step that fails validation produces the same fallback the old code produced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from . import routing

log = logging.getLogger("adapt.ai.chain")


@dataclass(frozen=True)
class Step:
    """One answer inside a merged call.

    ``parse`` receives the raw payload and the chain's context (what the caller already
    knows) and returns ``(value, notes)``. Returning ``(None, notes)`` means "the model's
    answer is not usable", which is a normal outcome: the step's ``fallback`` answers
    instead, and ``notes`` explains what was rejected.
    """

    key: str
    ask: str
    spec: Mapping[str, Any]
    parse: Callable[[Mapping[str, Any], dict[str, Any]], tuple[Any, list[str]]]
    fallback: Callable[[dict[str, Any]], Any]
    note: str = ""


@dataclass(frozen=True)
class Chain:
    """A named group of answers that one call produces."""

    name: str
    purpose: str
    build_user: Callable[[dict[str, Any]], str]
    steps: tuple[Step, ...]
    #: The static head of the prompt (system instructions + the contract). Left empty for
    #: chains whose system text is entirely per-request.
    system: str = ""
    #: The contract text: what to reply with, in one paragraph. This is *part of the static
    #: head* when a cache prefix is supplied, but a chain is also usable without one.
    note: str = ""


@dataclass
class ChainResult:
    """What came back: one value per step, and what each of them was."""

    name: str
    values: dict[str, Any] = field(default_factory=dict)
    #: step key -> "model" | "fallback"
    sources: dict[str, str] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)
    #: Model-call accounting for the whole chain: 1 when the call reached a model.
    hops: int = 0
    status: str = "disabled"
    model: str | None = None
    tier: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    #: The purposes this chain replaced, for the audit row and the cost report.
    replaced: tuple[str, ...] = ()

    @property
    def used_model(self) -> bool:
        return any(source == "model" for source in self.sources.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "chain": self.name,
            "purposes_replaced": list(self.replaced),
            "hops": self.hops,
            "status": self.status,
            "sources": dict(self.sources),
            "notes": {k: list(v) for k, v in self.notes.items()},
            "cached_tokens": self.cached_tokens,
            "latency_ms": self.latency_ms,
        }


def merge_specs(steps: Sequence[Step]) -> dict[str, Any]:
    """One response schema for the whole chain. Collisions are a bug, not a merge policy."""
    out: dict[str, Any] = {}
    for step in steps:
        for key, spec in step.spec.items():
            if key in out:
                raise ValueError(
                    f"chain step {step.key!r} and an earlier step both produce {key!r}: a merged "
                    "schema cannot have two owners for one field"
                )
            out[key] = spec
    return out


def contract(steps: Sequence[Step], *, extra: str = "") -> str:
    """The one-paragraph output contract a merged call has to follow."""
    lines = ["Reply with ONE JSON object containing all of these keys:"]
    for step in steps:
        lines.append(f"- {step.ask}")
    lines.append("Omit nothing and add nothing: an invented key is dropped, a missing one falls back.")
    if extra:
        lines.append(extra)
    return "\n".join(lines)


async def run(
    llm: Any,
    chain: Chain,
    context: dict[str, Any],
    *,
    student_id: str | None = None,
    role: str = "ai2",
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    cache_prefix: str | None = None,
    extra_system: str = "",
) -> ChainResult:
    """Make the one call and hand each step its answer.

    A chain whose every step falls back has still done its job: the caller gets the same
    local answers it would have got with no model at all, and ``sources`` says so.
    """
    # Which purposes this one call stands in for comes from the routing table, not from the
    # chain: the table is where "this used to be two calls" is recorded, and reading it back
    # is what stops the two from disagreeing.
    result = ChainResult(name=chain.name, replaced=tuple(routing.policy(chain.purpose).joins))
    system = "\n\n".join(
        part for part in ((chain.system or "").strip(), contract(chain.steps, extra=extra_system)) if part
    )
    user = chain.build_user(context)
    spec = merge_specs(chain.steps)
    try:
        res = await llm.complete(
            system=system,
            user=user,
            purpose=chain.purpose,
            role=role,
            student_id=student_id,
            cache_prefix=cache_prefix,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_spec=spec,
        )
    except Exception as exc:  # a chain is an optimisation; it may never be the failure
        log.info("chain %s could not be run (%s)", chain.name, type(exc).__name__)
        result.status = "error"
        result.hops = 1  # the call was attempted; the failure is the provider's, not the merge's
        result.sources = {step.key: "fallback" for step in chain.steps}
        result.values = {step.key: step.fallback(context) for step in chain.steps}
        result.notes = {step.key: [f"chain error: {type(exc).__name__}"] for step in chain.steps}
        return result

    # A client with no key resolves the plan and returns "disabled" without reaching anyone:
    # that is not a round trip, and counting it as one would make the cache and the budget
    # reports disagree with the wire.
    result.hops = 0 if str(getattr(res, "status", "")) == "disabled" else 1
    result.status = res.status
    result.model = getattr(res, "model", None)
    result.tier = getattr(res, "tier", None)
    result.latency_ms = int(getattr(res, "latency_ms", 0) or 0)
    result.tokens_in = int(getattr(res, "tokens_in", 0) or 0)
    result.tokens_out = int(getattr(res, "tokens_out", 0) or 0)
    result.cached_tokens = int(getattr(res, "cached_tokens", 0) or 0)
    data: Mapping[str, Any] = (res.data or {}) if getattr(res, "ok", False) else {}
    for step in chain.steps:
        # A later step may depend on an earlier answer (rate the skills *of the family you
        # chose*), so each step's parse sees the accepted values so far. They are handed
        # over under a private key rather than merged into the caller's context, which is
        # shared and meant to hold only what the caller knew before the call.
        context["_accepted"] = dict(result.values)
        value, notes = (None, ["no model answer"]) if not data else step.parse(data, context)
        if value is None:
            result.values[step.key] = step.fallback(context)
            result.sources[step.key] = "fallback"
        else:
            result.values[step.key] = value
            result.sources[step.key] = "model"
        result.notes[step.key] = list(notes)
    return result


__all__ = ["Chain", "ChainResult", "Step", "contract", "merge_specs", "run"]
