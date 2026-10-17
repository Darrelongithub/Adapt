"""Gemini client + quota guard + local fallback + the two things that make it affordable.

Transport is the Google AI Studio REST endpoint (``x-goog-api-key`` header, so the
key never lands in a URL or an access log). No SDK dependency: the free tier is a
plain JSON API and keeping it to ``httpx`` means one less thing to break on the
demo machine.

Six properties the blueprint asks for, implemented here:

* **Free-tier safety.** :class:`QuotaGuard` counts calls/tokens per UTC day against
  configured budgets and throttles with a minimum inter-call spacing. A call reserves its
  budget *before* it goes out and settles against the provider's real usage afterwards, so
  a hundred concurrent learners cannot all read the same untouched counter and decide there
  is room for all of them. When the budget is gone, callers get ``status="quota"`` and fall
  back to the local reasoner instead of hammering the API into a longer lockout.
* **An empty answer is a failed answer.** A 200 whose candidate list is empty -- filtered by
  safety, blocked by the prompt, or simply nothing -- is ``ok=False`` with
  ``status="blocked"`` or ``"empty"``, never a success with an empty string in it. The
  alternative is a tutor that "answered" and taught nothing, and a redaction count and a
  latency figure that make the incident look healthy in every report.
* **Graceful degradation.** Every failure mode (no key, DNS failure, 429, 5xx, bad JSON, a
  model id the provider no longer recognises) returns a typed result, never raises, so the
  app still answers.
* **Minimized egress.** Prompts pass through :mod:`adapt.security.redact` and the
  count of withheld fields is recorded in ``llm_calls`` for the privacy panel.
* **The right model for the job.** Every call names a ``purpose``, and
  :mod:`adapt.ai.routing` turns that into a class of work -- ``light``, ``standard``
  or ``deep`` -- whose model comes from configuration. A light task that fails is a
  fallback nobody sees; a deep task that fails is a rewrite, and the table says which
  is which (:meth:`GeminiLLM.plan` for one call, ``adapt status`` for all of them).
* **Static heads cached.** A caller may pass ``cache_prefix``: the part of the prompt
  that is identical for every learner (instructions, rubric, subject catalogue). It is
  registered once with a TTL and referenced afterwards instead of resent
  (:mod:`adapt.ai.context_cache`). A cache that cannot be created is a warning and one
  full prompt, never a failed answer, and learner text is refused inside a prefix
  because the cache is shared between learners.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

import httpx

from ..config import Settings, get_settings
from ..security.redact import redact_text
from . import routing
from .context_cache import ContextCache

log = logging.getLogger("adapt.llm")

JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

#: What each safety mode asks the provider to filter.
#:
#: ``provider`` sends no ``safetySettings`` at all, which means the answer depends on a
#: default this codebase does not control -- for the Gemini 2.5 generation that default is
#: documented as *off*, so the same prompt can produce a reply here and an empty candidate
#: list after a provider-side change, with nothing in the log to explain the difference.
#: ``balanced`` pins the four harm categories at ``BLOCK_ONLY_HIGH``: teaching text about
#: war, biology or bullying survives, and what *is* filtered is filtered predictably
#: enough to be *reported* -- a typed ``status="blocked"`` result, whose caller falls back
#: to the local tutor rather than printing nothing. ``strict`` blocks at
#: medium-and-above, which is what a deployment serving under-18 learners should run.
#:
#: Only the four categories the JSON API accepts appear here. Anything else is a
#: Vertex-only name, and one of those in a body makes every request a 400 -- which is the
#: opposite of the point.
SAFETY_THRESHOLDS: dict[str, dict[str, str]] = {
    "provider": {},
    "balanced": {
        "HARM_CATEGORY_HARASSMENT": "BLOCK_ONLY_HIGH",
        "HARM_CATEGORY_HATE_SPEECH": "BLOCK_ONLY_HIGH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_ONLY_HIGH",
        "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_ONLY_HIGH",
    },
    "strict": {
        "HARM_CATEGORY_HARASSMENT": "BLOCK_MEDIUM_AND_ABOVE",
        "HARM_CATEGORY_HATE_SPEECH": "BLOCK_MEDIUM_AND_ABOVE",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_MEDIUM_AND_ABOVE",
        "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_MEDIUM_AND_ABOVE",
    },
}

#: Finish reasons that mean "the model was asked and refused", rather than "it ran out of
#: room". The first is a blocked answer and gets its own status; the second is a truncated
#: answer, which is kept, because a half-sentence the caller can still use beats no
#: sentence at all.
_BLOCKED_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "PROHIBITED_CONTENT",
        "BLOCKLIST",
        "SPII",
        "RECITATION",
        "IMAGE_SAFETY",
    }
)


def safety_settings_for(mode: str | None) -> list[dict[str, str]]:
    """The ``safetySettings`` array for a configured mode, in stable order.

    Stable order matters more than it looks: this array is part of the request body, and a
    body whose key order wanders between two identical calls defeats any provider-side
    request caching and makes a diff of two logged requests unreadable.
    """
    table = SAFETY_THRESHOLDS.get(
        str(mode or "balanced").strip().lower(), SAFETY_THRESHOLDS["balanced"]
    )
    return [{"category": category, "threshold": table[category]} for category in sorted(table)]


#: A provider answer that means "that model is not something I can call" rather than "that
#: prompt was bad". A rename and a gated preview both land here, and the two deserve
#: different reactions: one is a configuration problem worth routing elsewhere, the other is
#: a refusal to route anywhere at all.
#: The words have to appear *together* with a mention of a model, because "unsupported" is
#: also what a rejected `responseSchema` says, and treating that as a retired model id would
#: take a working model out of service for the rest of the process.
_MODEL_UNAVAILABLE_MARKERS = (
    "is not found",
    "not found for api",
    "does not exist",
    "no such",
    "is not a valid model",
    "disabled for this project",
)


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    data: dict[str, Any] | None = None
    status: str = "ok"
    # ok | quota | disabled | consent_required | error | parse_error | throttled | blocked | empty
    # `blocked` and `empty` are the two that used to be `ok`: a response with no text in it is a
    # failure the caller must fall back on, not a success it has to notice by accident.

    provider: str = "gemini"
    model: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    redacted_fields: int = 0
    error: str | None = None
    attempts: int = 0
    fallback: bool = False
    #: Which class of work this call was (see ``ai/routing.py``), and which model answered.
    tier: str | None = None
    #: Input tokens the provider served from a registered static prefix on this call. The
    #: number the cache is judged by: it is the part of the prompt that was not paid for.
    cached_tokens: int = 0
    #: True when the request referenced a cache entry rather than resending the static head.
    context_cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "tier": self.tier,
            "latency_ms": self.latency_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cached_tokens": self.cached_tokens,
            "context_cached": self.context_cached,
            "redacted_fields": self.redacted_fields,
            "error": self.error,
            "attempts": self.attempts,
            "fallback": self.fallback,
        }


#: Ceiling on how long one caller will sit waiting for its paced slot. Pacing protects the
#: RPM ceiling, but a lesson must not stall behind a queue: a caller that cannot get a slot
#: inside this window takes it late rather than holding the whole loop open.
MAX_PACE_WAIT_S = 3.0


@dataclass
class Reservation:
    """One call's slice of the daily budget, taken *before* the request goes out.

    The estimate is carried on the reservation so the guard can swap it for the provider's
    real usage when the answer arrives (:meth:`QuotaGuard.settle`) or hand it all back when
    nothing was billed (:meth:`QuotaGuard.refund`). Without that handle the day's count is
    whatever the last response happened to report, which is how a guard can be both
    over-subscribed and under-counted in the same hour.
    """

    tokens: int
    #: Monotonic instant this caller is paced to. Waiting until it is what spaces calls out.
    slot: float
    settled: bool = False


@dataclass
class _Wallet:
    """The reservation a call is currently paying with.

    A cache the provider refuses means the request is re-sent and re-charged, so the
    reservation is not fixed for the life of the call. Carrying it in a mutable holder rather
    than a local variable is what lets the caller's ``finally`` refund the one that is
    actually outstanding at the moment the request path exits, rather than the one it started
    with -- a leak here is a learner quietly billed for a call nobody made.
    """

    reservation: Reservation | None = None


class QuotaGuard:
    """Daily budget + spacing enforcement for the free tier.

    Three rules, all of them about concurrency rather than arithmetic:

    * **A budget is spent when it is committed, not when it is billed.** :meth:`reserve`
      adds the call and its estimated tokens under the lock, so twenty learners arriving at
      once cannot each read the same untouched counter and each conclude there is room. The
      estimate is corrected by :meth:`settle` once the provider reports real usage, and
      reversed by :meth:`refund` for the attempts that never reached a model (DNS failure,
      timeout, 5xx) -- a request that was never served should not eat a learner's day.
    * **Spacing is a queue, not a glance.** Each reservation is handed the next free slot
      and the schedule moves forward, so concurrent callers take turns instead of all
      stamping the same "last call" instant and firing together. The wait happens after the
      lock, and is capped at :data:`MAX_PACE_WAIT_S` because a stalled lesson is worse than
      a slightly fast one.
    * **The lock is never held across an await.** Everything here is synchronous and fast;
      the sleeping is done by the caller. A :class:`threading.Lock` held over
      ``await asyncio.sleep`` is how an async app turns a rate limiter into a deadlock.
    """

    def __init__(
        self,
        *,
        daily_calls: int,
        daily_tokens: int,
        min_interval_ms: int,
        today_used: int = 0,
        today_tokens: int = 0,
    ) -> None:
        self.daily_calls = daily_calls
        self.daily_tokens = daily_tokens
        self.min_interval_s = min_interval_ms / 1000.0
        self.calls = today_used
        self.tokens = today_tokens
        #: Tokens counted against the budget by reservations that have not settled yet.
        #: Reported so ``adapt status`` can tell "the day is spent" from "a burst is in flight".
        self.pending = 0
        self._next_slot = 0.0
        self._lock = threading.Lock()
        self.day = self._today()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _rollover(self) -> None:
        today = self._today()
        if today != self.day:
            self.day = today
            self.calls = 0
            self.tokens = 0
            self.pending = 0

    def headroom(self) -> dict[str, object]:
        with self._lock:
            self._rollover()
            pending = self.pending
            calls = self.calls
            tokens = self.tokens
        return {
            "day": self.day,
            "calls_used": calls,
            "calls_left": max(0, self.daily_calls - calls),
            "tokens_used": tokens,
            "tokens_left": max(0, self.daily_tokens - tokens),
            "budget_calls": self.daily_calls,
            "budget_tokens": self.daily_tokens,
            "tokens_reserved_in_flight": pending,
        }

    def reserve(self, estimated_tokens: int) -> tuple[Reservation | None, str]:
        """Take a call and its estimated tokens out of the day. ``(None, why)`` means fall back."""
        with self._lock:
            self._rollover()
            if self.calls >= self.daily_calls:
                return None, f"daily call budget ({self.daily_calls}) reached"
            if self.tokens + max(0, int(estimated_tokens)) > self.daily_tokens:
                return None, "daily token budget reached"
            now = time.monotonic()
            # A slot behind the last caller that asked -- but never a slot so far in the
            # future that the wait is longer than the app is willing to make a learner pay.
            slot = min(max(now, self._next_slot), now + MAX_PACE_WAIT_S)
            self._next_slot = slot + self.min_interval_s
            self.calls += 1
            self.tokens += max(0, int(estimated_tokens))
            self.pending += max(0, int(estimated_tokens))
            return Reservation(tokens=int(estimated_tokens), slot=slot), ""

    async def pace(self, reservation: Reservation | None) -> None:
        """Wait out this caller's turn. Called after the lock is released, never inside it."""
        if reservation is None:
            return
        wait = reservation.slot - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    def settle(self, reservation: Reservation | None, *, tokens: int, billed: bool = True) -> None:
        """Swap a reservation's estimate for the real usage of the request.

        ``billed=False`` is for a request the provider refused before generating anything
        (a refused cache handle, for instance): the day keeps no charge for it. A 429 is
        settled with ``tokens=0`` instead: the call happened and the rate limiter saw it, so
        counting it is what stops the next caller from hammering the same locked door.
        """
        if reservation is None or reservation.settled:
            return
        reservation.settled = True
        with self._lock:
            self._rollover()
            self.pending = max(0, self.pending - max(0, reservation.tokens))
            self.tokens = max(0, self.tokens - max(0, reservation.tokens) + (max(0, int(tokens)) if billed else 0))
            if not billed:
                self.calls = max(0, self.calls - 1)

    def refund(self, reservation: Reservation | None) -> None:
        """Give the whole reservation back: the request never reached a model."""
        self.settle(reservation, tokens=0, billed=False)

    def record(self, calls: int, tokens: int) -> None:
        """Accounting for usage this guard did not reserve for.

        Not part of the request path any more -- :meth:`reserve` / :meth:`settle` own it --
        but kept because a caller that writes its own audit row (a replay of a day's usage,
        or a script that spent budget outside the client) needs the same counter rather
        than a second one that disagrees with the first.
        """
        with self._lock:
            self._rollover()
            self.calls += calls
            self.tokens += tokens


def effective_models(settings: Settings) -> dict[str, Any]:
    """Which model each class of work uses on this deployment, plus any pins.

    Operator-facing (``adapt status``, the CLI doctor): names appear here, which is why it
    is not wired to an HTTP surface -- §3 keeps model names off every learner-visible
    response and this one would be stripped anyway.
    """
    return {
        "classes": {
            tier: routing.model_for_tier(tier, settings) for tier in routing.TIERS
        },
        "pins": routing.pinned_models(settings),
    }


def estimate_tokens(messages: Sequence[str]) -> int:
    """Cheap proxy (~3.6 chars/token for English) -- good enough to budget against."""
    return max(1, int(sum(len(m) for m in messages) / 3.6))


#: Provider types that mean "this fragment is already a schema", as opposed to a compact
#: field spec that still has to be translated. The distinction is what lets one call site
#: write ``("array", {"type": "STRING", "maxLength": 64})`` and another ``["id": "str"]``.
_RAW_SCHEMA_TYPES = frozenset({"OBJECT", "ARRAY", "STRING", "INTEGER", "NUMBER", "BOOLEAN"})


def _is_raw_schema(fragment: Any) -> bool:
    return isinstance(fragment, dict) and str(fragment.get("type") or "").upper() in _RAW_SCHEMA_TYPES


def _items_for(fragment: Any) -> dict[str, Any]:
    """The ``items`` schema for an array, from either spelling of "what's in the array".

    Two shapes arrive here and both are in use across the codebase: a provider-shaped
    fragment (``{"type": "OBJECT", "properties": ...}``, as the content pipeline writes) or
    a compact field spec for one element (``[{"id": "str", "met": "bool"}]``, as the
    teach-back rubric writes). Passing the second through untranslated is what made the
    teach-back judge unanswerable: ``points`` reached the model as ``{"type": "STRING"}``,
    so an answer could only ever be rejected for not being a list of points.
    """
    if fragment is None:
        return {"type": "STRING"}
    if _is_raw_schema(fragment):
        return dict(fragment)
    if isinstance(fragment, (list, tuple)):
        element = fragment[0] if fragment else None
        return {"type": "ARRAY", "items": _items_for(element)}
    if isinstance(fragment, dict):
        inner = _schema_for(fragment)
        if inner is None:
            return {"type": "STRING"}
        return inner
    return {"type": "STRING"}


def _schema_for(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Translate a compact field spec into Gemini's OpenAPI-subset responseSchema.

    A field whose type ends in ``?`` is *optional*: it appears in ``properties`` so the
    model may answer it, and it stays out of ``required`` so the model may not. Marking
    everything required was a false promise to the parser -- a field the prompt only asks
    for "if you can" came back invented to satisfy the schema, or the honest answer came
    back without it and the whole call was thrown away. Now a call site says which of its
    fields it can live without, in the same dict it already passes.

    The trailing ``?`` is stripped before the schema is built, so it is a marker on the
    spec rather than a name the model has to match.
    """
    if not spec:
        return None
    props: dict[str, Any] = {}
    required: list[str] = []
    for key, typ in spec.items():
        # The `?` may sit on the type name of a plain field ("str?") or on the first half of
        # a tuple ("array?", {...}), so it is read off whichever spelling was used before
        # either one is unpacked. Only the marker is removed here: the property keeps the
        # name the validator reads back.
        head = typ if isinstance(typ, str) else (typ[0] if isinstance(typ, tuple) and typ else "")
        optional = isinstance(head, str) and head.strip().endswith("?")
        extra: Any = None
        if isinstance(typ, tuple):
            typ, extra = typ  # ("array", {...})
        if isinstance(typ, list):
            # `[{"id": "str"}]` shorthand: an array whose elements are objects, spelled
            # inline. `_items_for` translates the element spec instead of stringifying it.
            props[key] = _items_for(typ)
            if not optional:
                required.append(key)
            continue
        name = typ.strip().rstrip("?").strip() if isinstance(typ, str) else typ
        if name in ("array", "list"):
            props[key] = {"type": "ARRAY", "items": _items_for(extra)}
        elif _is_raw_schema(typ):
            # Already a schema fragment (a nested object the caller wrote in the provider's
            # own shape): pass it through rather than describing it as a string, which is
            # what the `else` below would do.
            props[key] = dict(typ)
        elif isinstance(typ, dict):
            nested = _schema_for(typ)
            props[key] = nested or {"type": "OBJECT"}
        elif name == "str":
            props[key] = {"type": "STRING"}
        elif name == "int":
            props[key] = {"type": "INTEGER"}
        elif name == "num":
            props[key] = {"type": "NUMBER"}
        elif name == "bool":
            props[key] = {"type": "BOOLEAN"}
        elif name == "object":
            props[key] = {"type": "OBJECT"}
        else:
            props[key] = {"type": "STRING"}
        if not optional:
            required.append(key)
    schema: dict[str, Any] = {"type": "OBJECT", "properties": props}
    if required:
        # An empty `required` list is legal but meaningless; some gateways reject it, and a
        # schema where every field is optional does not need a key to say so.
        schema["required"] = required
    return schema


class GeminiLLM:
    """Async wrapper over ``models/{model}:generateContent``."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        limiter: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
        consent_checker: Callable[[str], bool] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.guard = QuotaGuard(
            daily_calls=self.settings.gemini_daily_call_budget,
            daily_tokens=self.settings.gemini_daily_token_budget,
            min_interval_ms=self.settings.gemini_min_interval_ms,
        )
        self.limiter = limiter
        # The service container injects the learner-consent lookup. Keeping this at the
        # provider boundary means a newly added call site cannot accidentally bypass the
        # learner's sharing choice by forgetting an API-layer dependency.
        self.consent_checker = consent_checker
        #: Injectable so a test can exercise the real request/response plumbing -- including
        #: the cache round trip -- without a key and without the network.
        self.transport = transport
        self.cache = ContextCache(
            enabled=bool(getattr(self.settings, "ai_context_cache", True)),
            ttl_s=int(getattr(self.settings, "gemini_context_cache_ttl_s", 1800)),
            min_tokens=int(getattr(self.settings, "gemini_context_cache_min_tokens", 1024)),
            max_tokens=int(getattr(self.settings, "gemini_context_cache_max_tokens", 32_000)),
            max_entries=int(getattr(self.settings, "gemini_context_cache_max_entries", 8)),
        )
        self._client: httpx.AsyncClient | None = None
        self.last_error: str | None = None
        #: Purposes that reached the wire without a row in ``routing.TASKS``. Non-empty means
        #: a call site was added without saying what class of work it is, which is the one way
        #: the table can silently stop being the truth.
        self.unregistered_purposes: set[str] = set()
        #: Cacheable tasks called without a static prefix -- a call site that left half the
        #: optimisation on the table. Recorded rather than raised: a slow prompt is not a bug.
        self.prefixless_calls: set[str] = set()
        #: Model ids the provider said it cannot call, with the answer that said so. Kept for
        #: the life of the process: a renamed model is a configuration fact, and re-learning
        #: it on every one of a hundred calls is a hundred failed round trips.
        self.unavailable_models: dict[str, str] = {}
        self.stats: dict[str, int] = {
            "ok": 0,
            "quota": 0,
            "error": 0,
            "disabled": 0,
            "throttled": 0,
            #: A 200 that answered nothing: filtered by safety, or no candidates at all.
            #: Counted apart from "error" because the fix is different -- one is a prompt
            #: problem, the other is a network problem.
            "blocked": 0,
            "empty": 0,
            "parse_error": 0,
            #: Calls that had to move to a configured alternate because the id in the routing
            #: table is no longer callable. Non-zero means the deployment is one env var away
            #: from running on its intended model.
            "model_fallback": 0,
            "cache_hits": 0,
            "cache_created": 0,
            "cache_missed": 0,
        }

    # -- lifecycle ------------------------------------------------------ #

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.gemini_timeout_s, connect=8.0),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                follow_redirects=False,
                transport=self.transport,
            )
        return self._client

    async def aclose(self) -> None:
        # A registered cache outlives the socket that made it; releasing it on the way out
        # stops a deploy-and-restart loop from accumulating storage the operator pays for.
        await self._release_caches()
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self.settings.gemini_api_key)

    def plan(self, purpose: str, *, role: str = "ai2", **kw: Any) -> routing.CallPlan:
        """What this call would do, without making it. Used by tests, docs and the CLI."""
        return routing.plan(purpose, self.settings, role=role, **kw)

    def available(self) -> dict[str, object]:
        return {
            "provider": "gemini",
            "configured": self.configured,
            "models": effective_models(self.settings),
            #: Model ids this provider refused, and what it said. Surfaced because the failure
            #: it describes is invisible from the learner's side: every call degrades to the
            #: local tutor and nothing 500s.
            "models_refused": dict(sorted(self.unavailable_models.items())),
            "safety_mode": str(getattr(self.settings, "gemini_safety_mode", "balanced") or "balanced"),
            "safety_categories": len(safety_settings_for(getattr(self.settings, "gemini_safety_mode", "balanced"))),
            "headroom": self.guard.headroom(),
            "last_error": self.last_error,
            "stats": dict(self.stats),
            "routing": routing.report(self.settings, unknown=tuple(sorted(self.unregistered_purposes))),
            "context_reuse": self.cache.report(),
            "prefixless_tasks": sorted(self.prefixless_calls),
        }

    # -- main entry ----------------------------------------------------- #

    async def complete(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        role: str = "ai2",
        model: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_spec: dict[str, Any] | None = None,
        history: Sequence[tuple[str, str]] = (),
        student_id: str | None = None,
        cache_prefix: str | None = None,
    ) -> LLMResult:
        """One model call: chosen model, cached static head, redacted egress, fallback result.

        ``temperature``/``max_output_tokens`` default to the task's policy in
        :mod:`adapt.ai.routing` instead of being hard-coded here, so "how big is this
        answer allowed to be" is answerable from the table rather than from fourteen call
        sites. ``cache_prefix`` is the static head of the prompt (see
        :mod:`adapt.ai.static_context`): when it is given and the provider accepts it, the
        head is registered once and referenced afterwards, and ``system`` carries only the
        part that differs per call.
        """
        started = time.monotonic()
        plan = self.plan(purpose, role=role, model=model, temperature=temperature, max_output_tokens=max_output_tokens)
        plan = self._avoid_dead_models(plan)
        if not plan.registered:
            self.unregistered_purposes.add(str(purpose))
        if plan.cacheable and not cache_prefix and purpose != "console_connection_test":
            self.prefixless_calls.add(str(purpose))
        if not self.configured:
            return LLMResult(
                ok=False,
                status="disabled",
                error="no ADAPT_GEMINI_API_KEY",
                provider="none",
                tier=plan.tier,
            )

        # Consent is checked immediately before any prompt is prepared or budget is
        # reserved. ``student_id`` is absent for operator/content calls; learner-bound
        # calls must have an explicit consent row *and* an injected lookup. A missing
        # checker or a lookup failure fails closed so a wiring problem cannot turn into
        # an unintended external disclosure.
        if student_id:
            allowed = False
            if self.consent_checker is None:
                log.warning("learner model call has no consent checker; refusing external call")
            else:
                try:
                    allowed = bool(self.consent_checker(student_id))
                except Exception as exc:  # pragma: no cover - exercised through the service boundary
                    log.warning("model consent lookup failed (%s); refusing learner call", type(exc).__name__)
            if not allowed:
                return LLMResult(
                    ok=False,
                    status="consent_required",
                    error="learner has not consented to sharing a summary with the model",
                    provider="none",
                    tier=plan.tier,
                )

        # 1) minimize egress -- every string that leaves is scrubbed, including
        # conversation history, which is where a learner is most likely to name
        # their school.
        (safe_system, sys_drop), _ = _red(system)
        (safe_user, user_drop), _ = _red(user)
        safe_history: list[dict[str, Any]] = []
        hist_drop = 0
        for role_name, text in history:
            (t, n), _k = _red(text)
            hist_drop += n
            safe_history.append(
                {"role": "user" if role_name == "user" else "model", "parts": [{"text": t}]}
            )
        redacted = sys_drop + user_drop + hist_drop

        # 2) the static head: a registered prefix is referenced, not resent. The prefix is
        #    checked *after* redaction for the same reason the rest of the prompt is: a
        #    prefix that failed the scrubber must not become a shared cache entry.
        safe_prefix = ""
        if cache_prefix:
            (safe_prefix, _prefix_drop), _ = _red(cache_prefix)
            redacted += _prefix_drop
        cache_key, cache_entry = "", None
        if safe_prefix and plan.cacheable:
            cache_key = self.cache.key_for(plan.model, safe_prefix)
            if self.cache.refused(cache_key):
                cache_key = ""  # this deployment already said no; do not ask again
            else:
                cache_entry = self.cache.lookup(cache_key)
                if cache_entry is not None:
                    self.stats["cache_hits"] += 1
                else:
                    self.stats["cache_missed"] += 1

        if safe_prefix and plan.cacheable and cache_key and cache_entry is None:
            cache_entry = await self.register_static(
                model=plan.model, prefix=safe_prefix, purpose=purpose
            )

        # What actually leaves the process decides what the day is charged for: a call that
        # references a registered prefix does not pay for the prefix again.
        out_budget = max_output_tokens or plan.max_output_tokens
        tail = [safe_system, safe_user, *(h["parts"][0]["text"] for h in safe_history)]
        est_tail = estimate_tokens(tail) + out_budget
        est_full = (estimate_tokens([safe_prefix, *tail]) if safe_prefix else est_tail) + out_budget
        est = est_tail if cache_entry is not None else est_full
        # The reservation is taken here, before the first byte leaves: the whole point of the
        # guard is that a hundred concurrent callers cannot each be told "there is room".
        reservation, why = self.guard.reserve(est)
        if reservation is None:
            self.stats["quota"] += 1
            return LLMResult(
                ok=False,
                status="quota",
                error=why,
                redacted_fields=redacted,
                model=plan.model,
                tier=plan.tier,
            )
        wallet = _Wallet(reservation)
        url_base = self.settings.gemini_base_url.rstrip("/")
        headers = {"x-goog-api-key": self.settings.gemini_api_key or "", "content-type": "application/json"}
        try:
            # Waiting happens after the reservation, never inside the lock: the wait is the
            # queue, and holding a lock across it is how a rate limiter becomes a deadlock.
            await self.guard.pace(wallet.reservation)
            if self.limiter is not None:
                ok, retry = self.limiter.allow("gemini", "shared")
                if not ok:
                    # Nothing left this process, so nothing is charged to the learner's day.
                    self.stats["throttled"] += 1
                    return LLMResult(
                        ok=False,
                        status="throttled",
                        error=f"local pacing limit ({retry:.1f}s)",
                        redacted_fields=redacted,
                        tier=plan.tier,
                    )
            return await self._send(
                wallet=wallet, plan=plan, url_base=url_base, headers=headers,
                safe_prefix=safe_prefix, safe_system=safe_system, safe_user=safe_user,
                safe_history=safe_history, response_spec=response_spec,
                cache_key=cache_key, cache_entry=cache_entry, est_full=est_full,
                redacted=redacted, started=started,
            )
        finally:
            # A guard that keeps an estimate for a request no longer in flight charges a
            # learner for a call they never made, so the reservation is resolved here whatever
            # happened below: settled by the response, refunded by an early return, or
            # refunded because something raised. `settle` marks a reservation spent, so a
            # call that already paid is never handed anything back.
            self.guard.refund(wallet.reservation)

    async def _send(
        self,
        *,
        wallet: _Wallet,
        plan: routing.CallPlan,
        url_base: str,
        headers: dict[str, str],
        safe_prefix: str,
        safe_system: str,
        safe_user: str,
        safe_history: Sequence[dict[str, Any]],
        response_spec: dict[str, Any] | None,
        cache_key: str,
        cache_entry: Any,
        est_full: int,
        redacted: int,
        started: float,
    ) -> LLMResult:
        """The request itself: two passes at most, and one settled reservation.

        The second pass exists only for a cache the provider turns out not to support; it
        re-sends the full prompt and pays for that request out of the same daily budget
        rather than treating the retry as free. The wallet is the caller's, so the
        reservation this method is holding at the moment it returns -- or raises, or swaps --
        is the one that gets refunded by the caller's ``finally``.
        """
        attempts = 0
        last: LLMResult | None = None
        for pass_no in range(2):
            if pass_no == 1:
                # The cached head was refused: forget the entry we thought we had, remember
                # the refusal, and send the prompt in full. This is the difference between
                # "caching is unavailable on this deployment" and "every call pays for a
                # failing round trip".
                self.cache.invalidate(cache_key)
                self.cache.refuse(cache_key, why=str(last.error if last else "cached call refused")[:120])
                cache_entry = None
                # The refused request was never served, so the day gives it back -- and the
                # retry that *is* served reserves for itself. A cache must never be the
                # reason a learner gets no answer, and it must never be the reason a day of
                # budget is spent on requests that produced nothing either.
                self.guard.settle(wallet.reservation, tokens=0, billed=False)
                wallet.reservation, why = self.guard.reserve(est_full)
                if wallet.reservation is None:
                    self.stats["quota"] += 1
                    return LLMResult(
                        ok=False,
                        status="quota",
                        error=why,
                        redacted_fields=redacted,
                        model=plan.model,
                        tier=plan.tier,
                        attempts=attempts,
                    )
                await self.guard.pace(wallet.reservation)
            body = self._body(
                plan=plan, prefix=safe_prefix, system=safe_system, user=safe_user,
                history=safe_history,
                temperature=plan.temperature, max_output_tokens=plan.max_output_tokens,
                response_spec=response_spec, cache_entry=cache_entry,
            )
            url = f"{url_base}/models/{plan.model}:generateContent"

            for attempt in range(self.settings.gemini_max_retries + 1):
                attempts = attempt + 1
                try:
                    client = await self._http()
                    resp = await client.post(url, headers=headers, json=body)
                except httpx.TimeoutException:
                    last = LLMResult(ok=False, status="error", error="timeout", attempts=attempts, tier=plan.tier)
                    if attempt < self.settings.gemini_max_retries:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    break
                except httpx.HTTPError as exc:
                    # DNS/TLS/connection refused -- typical for a plane, a cafe, or a
                    # blocked egress. Treated exactly like a quota miss: fall back.
                    self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                    return LLMResult(
                        ok=False,
                        status="error",
                        error=self.last_error,
                        attempts=attempts,
                        redacted_fields=redacted,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        tier=plan.tier,
                    )

                if resp.status_code == 429:
                    # The call happened and the rate limiter saw it, so the day keeps the call
                    # and gives back the tokens that were never generated.
                    self.guard.settle(wallet.reservation, tokens=0)
                    self.stats["quota"] += 1
                    detail = _err_text(resp)
                    self.last_error = f"429 {detail}"[:200]
                    return LLMResult(
                        ok=False,
                        status="quota",
                        error=self.last_error,
                        attempts=attempts,
                        redacted_fields=redacted,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        tier=plan.tier,
                    )
                if resp.status_code in (401, 403):
                    self.last_error = f"auth {resp.status_code}: {_err_text(resp)}"[:200]
                    return LLMResult(
                        ok=False, status="error", error=self.last_error, attempts=attempts, tier=plan.tier
                    )
                if resp.status_code == 404 or (resp.status_code == 400 and cache_entry is None and self._unknown_model(resp)):
                    # "No such model" is not a bad prompt, and it will not get better on a
                    # retry: it is a name this deployment is carrying that the provider no
                    # longer answers to. Remember it (so `adapt status` says so instead of
                    # every call quietly degrading), hand the day back the estimate, and
                    # answer from the local tutor. The next call in this process starts on the
                    # configured alternate instead of rediscovering the rename.
                    detail = _err_text(resp)
                    self.unavailable_models[plan.model] = f"{resp.status_code} {detail}"[:200]
                    self.last_error = f"model unavailable: {detail}"[:200]
                    log.warning(
                        "the model this deployment named for `%s` is not callable (%s); "
                        "alternates: %s",
                        plan.task,
                        detail[:120],
                        "configured" if self._alternate_for(plan.model) else "none configured",
                    )
                    return LLMResult(
                        ok=False,
                        status="model_unavailable",
                        error=self.last_error,
                        attempts=attempts,
                        redacted_fields=redacted,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        tier=plan.tier,
                        model=plan.model,
                    )
                if resp.status_code == 400 and cache_entry is not None:
                    # The provider refused to use this cache with this call (an unsupported
                    # model, a deleted entry, a shifted payload). Take the cache out of the
                    # picture and send the prompt in full: a cache must never be the reason
                    # a learner gets no answer.
                    last = LLMResult(
                        ok=False,
                        status="error",
                        error=f"400 with cached context: {_err_text(resp)}"[:200],
                        attempts=attempts,
                        tier=plan.tier,
                    )
                    log.info("cached call refused (%s); resending the full prompt", _err_text(resp)[:120])
                    break
                if resp.status_code >= 500:
                    last = LLMResult(
                        ok=False, status="error", error=f"upstream {resp.status_code}", attempts=attempts, tier=plan.tier
                    )
                    if attempt < self.settings.gemini_max_retries:
                        await asyncio.sleep(1.2 * (attempt + 1))
                        continue
                    break
                if resp.status_code != 200:
                    self.last_error = f"{resp.status_code}: {_err_text(resp)}"[:240]
                    return LLMResult(
                        ok=False, status="error", error=self.last_error, attempts=attempts, tier=plan.tier
                    )

                try:
                    data = resp.json()
                    if not isinstance(data, dict):
                        raise ValueError("the answer was not a JSON object")
                except Exception as exc:
                    # A 200 whose body is not JSON is a proxy, a captive portal or a
                    # half-written response -- all of them "the lesson must not 500", none of
                    # them a reason to hand the caller `text=""` and call it an answer.
                    self.last_error = f"undecodable response: {type(exc).__name__}"[:200]
                    return LLMResult(
                        ok=False,
                        status="error",
                        error=self.last_error,
                        attempts=attempts,
                        redacted_fields=redacted,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        tier=plan.tier,
                    )
                text, blocked, finish = _extract_completion(data)
                usage = data.get("usageMetadata") or {}
                tin = int(usage.get("promptTokenCount") or estimate_tokens([safe_system, safe_user]))
                tout = int(usage.get("candidatesTokenCount") or 0)
                cached_in = int(usage.get("cachedContentTokenCount") or 0)
                if cached_in and cache_key:
                    self.cache.note_reuse(cache_key, cached_in)
                # The prompt was read and the day pays for it even when the answer is empty --
                # which is exactly why an empty answer has to be *named* rather than billed
                # silently as a success.
                self.guard.settle(wallet.reservation, tokens=tin + tout)
                if not text:
                    status = "blocked" if blocked else "empty"
                    self.stats[status] += 1
                    why = blocked or finish or "the response carried no text"
                    log.warning(
                        "`%s` returned a 200 with no answer (%s); falling back to the local tutor",
                        plan.task,
                        str(why)[:120],
                    )
                    return LLMResult(
                        ok=False,
                        status=status,
                        error=f"the model returned no text ({str(why)[:120]})",
                        model=plan.model,
                        tier=plan.tier,
                        cached_tokens=cached_in,
                        context_cached=cache_entry is not None,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        tokens_in=tin,
                        tokens_out=tout,
                        redacted_fields=redacted,
                        attempts=attempts,
                    )
                self.stats["ok"] += 1
                parsed: dict[str, Any] | None = None
                status = "ok"
                if response_spec:
                    parsed, status = _parse_json(text)
                    if parsed is None:
                        # A reply that does not answer the contract it was given is not a
                        # reply the app can use. It used to be reported as ok=True with
                        # data=None, which left every caller to invent the same check.
                        self.stats["parse_error"] += 1
                        log.info("`%s` answered outside its schema; using the local answer", plan.task)
                        return LLMResult(
                            ok=False,
                            status="parse_error",
                            text=text,
                            model=plan.model,
                            tier=plan.tier,
                            cached_tokens=cached_in,
                            context_cached=cache_entry is not None,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            tokens_in=tin,
                            tokens_out=tout,
                            redacted_fields=redacted,
                            attempts=attempts,
                        )
                return LLMResult(
                    ok=True,
                    text=text,
                    data=parsed,
                    status=status,
                    model=plan.model,
                    tier=plan.tier,
                    cached_tokens=cached_in,
                    context_cached=cache_entry is not None,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    tokens_in=tin,
                    tokens_out=tout,
                    redacted_fields=redacted,
                    attempts=attempts,
                )
            if last is not None and last.error and last.error.startswith("400 with cached context") and pass_no == 0:
                continue  # retry the whole request without the cache
            break

        self.stats["error"] += 1
        res = last or LLMResult(ok=False, status="error", error="unknown", attempts=attempts, tier=plan.tier)
        res.tier = res.tier or plan.tier
        res.latency_ms = int((time.monotonic() - started) * 1000)
        res.redacted_fields = redacted
        return res

    # -- request construction ------------------------------------------- #

    def _body(
        self,
        *,
        plan: routing.CallPlan,
        prefix: str,
        system: str,
        user: str,
        history: Sequence[dict[str, Any]],
        temperature: float,
        max_output_tokens: int,
        response_spec: dict[str, Any] | None,
        cache_entry: Any = None,
    ) -> dict[str, Any]:
        """The generateContent body, with or without a registered static head.

        ``prefix`` is the static head and ``system`` the part that varies. With a cache
        entry the head is already registered, so it leaves the request; without one -- caching
        off, below the floor, refused by the provider -- the two are simply concatenated back
        into the system instruction they came from. A call site therefore never has to know
        which of the two paths it is on, which is what makes turning caching off a
        configuration change rather than a code change.

        Three things worth knowing about the shape:

        * **the field is camelCase.** ``systemInstruction`` is what the REST API reads, and it
          is what ``register_static`` already sends. The snake_case spelling here was a
          second name for the same field, which the provider ignores rather than rejects --
          so the shape was valid, the call succeeded, and every instruction in the static
          head simply was not sent. A silently dropped system prompt is worse than a
          rejected request, because nothing anywhere says it happened; the casing is now
          asserted by a test rather than by a comment.
        * a request that references cached content must not *also* send the same text, so
          the static head leaves the request entirely -- and the provider rejects a
          ``systemInstruction`` alongside a cache that already contains one, which is why
          the dynamic remainder of the system prompt is folded into the user turn;
        * the remaining system text is still instructions, so it goes first inside that
          turn, ahead of the learner's material, in the order it would have been read.
        """
        if cache_entry is not None and getattr(cache_entry, "name", None):
            lead = "\n\n".join(part for part in (system.strip(), user) if part)
            body: dict[str, Any] = {
                "cachedContent": cache_entry.name,
                "contents": [*history, {"role": "user", "parts": [{"text": lead}]}],
            }
        else:
            full_system = "\n\n".join(part for part in (prefix.strip(), system.strip()) if part)
            body = {
                "systemInstruction": {"parts": [{"text": full_system}]},
                "contents": [*history, {"role": "user", "parts": [{"text": user}]}],
            }
        body["generationConfig"] = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "topP": 0.95,
        }
        safety = safety_settings_for(getattr(self.settings, "gemini_safety_mode", "balanced"))
        if safety:
            body["safetySettings"] = safety
        schema = _schema_for(response_spec)
        if schema:
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = schema
        return body

    async def register_static(
        self, *, model: str, prefix: str, purpose: str = ""
    ) -> Any | None:
        """Register a static head with the provider, or return None if it cannot be cached.

        The promise is "a cache that cannot be created is a warning and one full prompt,
        never a failed answer", and it is kept by catching *everything* here rather than the
        handful of provider failures the happy path expects: a bug inside the optimisation
        must cost a resent prefix, not a lesson. (An earlier version of this method raised on
        its own success -- the provider answered with a cache handle and the client crashed
        reading it, which every call site's broad ``except`` then reported as "no model".)
        """
        try:
            return await self._register_static(model=model, prefix=prefix, purpose=purpose)
        except Exception as exc:  # noqa: BLE001 - the whole point of this wrapper
            log.warning(
                "static context could not be registered (%s: %s); sending full prompts",
                type(exc).__name__,
                str(exc)[:120],
            )
            return None

    async def _register_static(
        self, *, model: str, prefix: str, purpose: str = ""
    ) -> Any | None:
        """The registration itself. Every failure below is a ``None``, never a raised answer.

        Called on the first use of a prefix. Everything about it is optional: below the
        documented minimum, switched off, a provider that answers 4xx, a network that is
        down -- all of them return ``None`` and the caller sends the full prompt. What is
        *not* optional is refusing learner text: the prefix is shared between learners, so
        :func:`adapt.ai.context_cache.assert_static` gets the final word.
        """
        if not prefix or not self.configured:
            return None
        ok, why = self.cache.eligible(prefix)
        if not ok:
            log.debug("static context skipped: %s", why)
            return None
        key = self.cache.key_for(model, prefix)
        if self.cache.refused(key):
            return None
        url = f"{self.settings.gemini_base_url.rstrip('/')}/cachedContents"
        body = {
            "model": f"models/{model}",
            "systemInstruction": {"parts": [{"text": prefix}]},
            "displayName": f"adapt-{purpose or 'static'}"[:128],
            "ttl": f"{self.cache.ttl_s}s",
        }
        headers = {"x-goog-api-key": self.settings.gemini_api_key or "", "content-type": "application/json"}
        try:
            client = await self._http()
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.info("static context could not be registered (%s); sending full prompts", type(exc).__name__)
            self.cache.refuse(key, why=type(exc).__name__)
            return None
        if resp.status_code != 200:
            self.cache.refuse(key, why=f"{resp.status_code} {_err_text(resp)}")
            return None
        try:
            name = str((resp.json() or {}).get("name") or "")
        except Exception:
            name = ""
        if not name:
            self.cache.refuse(key, why="the provider returned no cache handle")
            return None
        entry = self.cache.remember(key, model=model, name=name, tokens=self.cache.estimate(prefix))
        self.stats["cache_created"] += 1
        return entry

    async def _release_caches(self) -> None:
        """Delete the provider-side entries this process created. Best effort, on shutdown."""
        entries = list(self.cache.entries.values())
        self.cache.invalidate()
        if not entries or self._client is None or self._client.is_closed:
            return
        headers = {"x-goog-api-key": self.settings.gemini_api_key or ""}
        base = self.settings.gemini_base_url.rstrip("/")
        for entry in entries:
            if not entry.name:
                continue
            try:
                await self._client.delete(f"{base}/{entry.name}", headers=headers)
            except Exception:  # a cache expiring on its own is an acceptable outcome
                log.debug("cache release skipped", exc_info=True)

    def _alternate_for(self, model: str) -> str:
        """The substitute an operator named for a model id, or ``""`` for "none".

        Deliberately no default. Guessing at a model name the provider may or may not serve
        turns a visible configuration problem into an invisible one, and a silent swap of the
        model that writes to a learner is the kind of change this codebase only makes in a
        table the operator reads.
        """
        table = getattr(self.settings, "gemini_model_alternates", None) or {}
        try:
            return str(table.get(model) or "").strip()
        except AttributeError:  # a mapping-shaped config value that is not a mapping
            return ""

    def _unknown_model(self, resp: httpx.Response) -> bool:
        """Is this error "that model is not callable here" rather than "that prompt was bad"?

        A 404 on a ``models/{name}`` path needs no parsing. A 400 does, because that is also
        the answer to a malformed request, and treating every 400 as a rename would let one
        bad prompt retire a model for the whole process. So a 400 has to name a model *and*
        say it cannot have it -- the two halves of "models/x is not found for API version".
        """
        if resp.status_code == 404:
            return True
        if resp.status_code not in (400, 403):
            return False
        detail = _err_text(resp).casefold()
        if "model" not in detail:
            return False
        return any(marker in detail for marker in _MODEL_UNAVAILABLE_MARKERS)

    def _avoid_dead_models(self, plan: routing.CallPlan) -> routing.CallPlan:
        """Route around a model id this provider has already refused, if a substitute exists.

        Learning the id is dead costs one call per process; paying for that lesson on every
        call afterwards is how a rename turns into a permanent, silent, app-wide fallback.
        With no substitute configured nothing changes -- the answer stays the typed
        ``model_unavailable`` failure, which is honest in a way a guess is not.
        """
        current = plan.model
        seen: set[str] = set()
        while current in self.unavailable_models and current not in seen:
            seen.add(current)
            nxt = self._alternate_for(current)
            if not nxt or nxt == current:
                break
            current = nxt
        if current == plan.model:
            return plan
        self.stats["model_fallback"] += 1
        log.info(
            "`%s` answered on a configured alternate because %s is not callable here",
            plan.task,
            plan.model,
        )
        return replace(plan, model=current)

    async def list_models(self) -> dict[str, Any]:
        """Which of the models this deployment names the provider will actually answer to.

        The point is a *named* failure. A pinned id that has been renamed or gated makes
        every call fall back to the local tutor, which the learner sees as "plain answers"
        and nobody sees as a bug until someone reads a log; this says, in one request, which
        of the configured ids the provider recognises. Operator-facing only -- the result
        carries model names, and §3 keeps those off every learner-visible surface.
        """
        if not self.configured:
            return {"configured": False, "checked": False, "reason": "no credential configured"}
        url = f"{self.settings.gemini_base_url.rstrip('/')}/models"
        headers = {"x-goog-api-key": self.settings.gemini_api_key or ""}
        try:
            client = await self._http()
            resp = await client.get(url, headers=headers, params={"pageSize": 200})
        except httpx.HTTPError as exc:
            return {"configured": True, "checked": False, "reason": f"unreachable ({type(exc).__name__})"}
        if resp.status_code != 200:
            return {"configured": True, "checked": False, "reason": f"{resp.status_code} {_err_text(resp)}"[:160]}
        names = {
            str(m.get("name") or "").removeprefix("models/")
            for m in (resp.json() or {}).get("models", [])
            if isinstance(m, dict)
        }
        wanted = {
            tier: routing.model_for_tier(tier, self.settings) for tier in routing.TIERS
        }
        return {
            "configured": True,
            "checked": True,
            "recognised": {tier: name in names for tier, name in sorted(wanted.items())},
            "count": len(names),
        }

    def _model_for(self, role: str) -> str:
        """The model for a role with no task context: the middle class.

        Every real call names a purpose and goes through :meth:`plan`; this exists for the
        paths that ask a question about a role rather than about a task.
        """
        return routing.model_for_tier("standard", self.settings)

    # -- accounting ----------------------------------------------------- #

    def audit_row(
        self,
        *,
        purpose: str,
        role: str,
        result: LLMResult,
        student_id: str | None,
        prompt_chars: int,
    ) -> dict[str, Any]:
        from ..db.models import utcnow

        return {
            "student_id": student_id,
            "purpose": purpose,
            "role": role,
            "model": result.model or "n/a",
            "status": result.status if result.ok else (result.status or "error"),
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "latency_ms": result.latency_ms,
            "redacted_fields": result.redacted_fields,
            "prompt_chars": prompt_chars,
            "tier": result.tier,
            "cached_tokens": result.cached_tokens,
            "day": utcnow().date().isoformat(),
        }


def _red(text: str) -> tuple[tuple[str, int], list[str]]:
    r = redact_text(text)
    return (r.text, r.removed), r.kinds


def _err_text(resp: httpx.Response) -> str:
    try:
        j = resp.json()
        err = j.get("error") or {}
        return str(err.get("message") or j)[:200]
    except Exception:
        return resp.text[:200]


def _extract_completion(data: dict[str, Any]) -> tuple[str, str | None, str]:
    """Read a ``generateContent`` answer: ``(text, blocked_because, finish_reason)``.

    The three-way return is the point. ``("...", None, "STOP")`` is an answer;
    ``("", "safety", ...)`` is a refusal the learner should hear about through the local
    tutor; ``("", None, "MAX_TOKENS")`` is an answer that ran out of room, which is a budget
    problem rather than a content one. Collapsing all three into ``""`` is how a filtered
    reply, a truncated reply and a successful empty reply used to look identical to the
    caller -- and identical to a success.
    """
    cands = data.get("candidates") or []
    parts: list[str] = []
    finish = ""
    for cand in cands:
        if not isinstance(cand, dict):
            continue
        for p in ((cand.get("content") or {}).get("parts") or []):
            if isinstance(p, dict) and p.get("text"):
                parts.append(str(p["text"]))
        finish = str(cand.get("finishReason") or finish)
    text = "\n".join(parts).strip()
    if text:
        return text, None, finish
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if not blocked and finish in _BLOCKED_FINISH_REASONS:
        blocked = f"finishReason={finish}"
    return "", (str(blocked) if blocked else None), finish


def _parse_json(text: str) -> tuple[dict[str, Any] | None, str]:
    cleaned = JSON_FENCE.sub("", text or "").strip()
    if not cleaned:
        return None, "parse_error"
    try:
        obj = json.loads(cleaned)
        return (obj if isinstance(obj, dict) else {"value": obj}), "ok"
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return (obj if isinstance(obj, dict) else {"value": obj}), "ok"
        except json.JSONDecodeError:
            return None, "parse_error"
    return None, "parse_error"


def usage_totals(rows: Sequence[Any]) -> dict[str, object]:
    """Aggregate ``llm_calls`` rows for the privacy/cost panel."""
    calls = len(rows)
    tin = sum(int(r.tokens_in or 0) for r in rows)
    tout = sum(int(r.tokens_out or 0) for r in rows)
    red = sum(int(r.redacted_fields or 0) for r in rows)
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    cached = 0
    for r in rows:
        by_status[str(r.status)] = by_status.get(str(r.status), 0) + 1
        tier = str(getattr(r, "tier", None) or "unclassified")
        by_tier[tier] = by_tier.get(tier, 0) + 1
        cached += int(getattr(r, "cached_tokens", 0) or 0)
    return {
        "calls": calls,
        "tokens_in": tin,
        "tokens_out": tout,
        "tokens_in_served_from_static_context": cached,
        "by_class": by_tier,
        "redacted_fields_total": red,
        "cost_usd": 0.0,  # free tier by policy; shown so the demo can prove it
        "note": "counts cover calls attempted through ADAPT only; the provider keeps its own usage page",
    }
