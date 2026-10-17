"""Gemini client + quota guard + local fallback + the two things that make it affordable.

Transport is the Google AI Studio REST endpoint (``x-goog-api-key`` header, so the
key never lands in a URL or an access log). No SDK dependency: the free tier is a
plain JSON API and keeping it to ``httpx`` means one less thing to break on the
demo machine.

Five properties the blueprint asks for, implemented here:

* **Free-tier safety.** :class:`QuotaGuard` counts calls/tokens per UTC day against
  configured budgets and throttles with a minimum inter-call spacing. When the
  budget is gone, callers get ``status="quota"`` and fall back to the local
  reasoner instead of hammering the API into a longer lockout.
* **Graceful degradation.** Every failure mode (no key, DNS failure, 429, 5xx,
  bad JSON) returns a typed result, never raises, so the app still answers.
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
* **A retired model ID is not the end of the call.** Providers retire versioned model
  IDs on their own schedule -- and keep them on ``/models`` afterwards, so the list is
  not a statement of what a key can call. When a configured ID answers 404 the client
  tries a replacement: the operator's ``ADAPT_GEMINI_MODEL_FALLBACK``, another
  configured class, and then -- once per process, behind a lock -- a model discovered
  from the key's own ``/models`` list, ranked for the tier that failed. Bounded at two
  replacements and one discovery request; every dead ID and the model that replaced it
  are remembered, so the next call goes straight to what works. Discovery that fails
  is a local fallback, exactly like any other provider failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import httpx

from ..config import (
    MODEL_ALIAS_SUFFIX,
    MODEL_PREVIEW_SUFFIX,
    NON_TEXT_MODEL_MARKERS,
    TIER_MODEL_FAMILIES,
    Settings,
    get_settings,
)
from ..security.redact import redact_text
from . import routing
from .context_cache import ContextCache

log = logging.getLogger("adapt.llm")

JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

#: Categories the Generative Language API accepts a threshold for. The threshold itself is
#: one deployment setting (``ADAPT_GEMINI_SAFETY_THRESHOLD``) so the policy is stated in the
#: request instead of inferred from a provider default that differs by category and model.
SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)

#: Candidate finish reasons that mean "the provider refused to produce text", as opposed to
#: "the provider produced no text" (no candidates, an empty parts list, a length cut).
_BLOCKED_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
)

#: How many alternative models one call may try after its configured ID answers 404.
#: Two is the whole budget and the whole point: one *configured* replacement (the
#: operator's fallback, or another class) and one *discovered* from the key's own model
#: list. A third would be a loop, and a provider that 404s three IDs in a row is
#: telling us to stop and use the local tutor.
_MAX_MODEL_SWITCHES = 2


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    data: dict[str, Any] | None = None
    #: ok | quota | disabled | consent_required | error | parse_error | throttled | blocked | empty.
    #: ``blocked``/``empty`` are *failures*: a 200 with no usable candidate text is not an
    #: answer, and call sites must fall back exactly as they do for a transport error.
    status: str = "ok"
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


class QuotaGuard:
    """Daily budget + spacing enforcement for the free tier.

    ``acquire`` **reserves** one call and its estimated tokens under the lock, before any
    request is sent. ``settle`` replaces that estimate with what the provider reported;
    ``release`` gives it back when no billable request happened. Checking and reserving in
    one critical section is what makes the daily budget a ceiling instead of a race: two
    concurrent callers can no longer both see the last free slot.

    Spacing is reserved the same way. ``_last`` moves to the moment this caller may send,
    so a second caller arriving during the first one's sleep queues *behind* it instead of
    sleeping beside it. A queue whose next slot is further away than
    :data:`MAX_PACING_WAIT_S` is refused: the local fallback is better than a request that
    has already timed out by the time it is sent.
    """

    #: Refuse rather than make a caller wait longer than this for its turn.
    MAX_PACING_WAIT_S = 3.0

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
        self._last = 0.0
        self._lock = threading.Lock()
        self.day = self._today()

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _rollover(self) -> None:
        today = self._today()
        if today != self.day:
            self.day = today
            self.calls = 0
            self.tokens = 0

    def headroom(self) -> dict[str, object]:
        with self._lock:
            self._rollover()
            return {
                "day": self.day,
                "calls_used": self.calls,
                "calls_left": max(0, self.daily_calls - self.calls),
                "tokens_used": self.tokens,
                "tokens_left": max(0, self.daily_tokens - self.tokens),
                "budget_calls": self.daily_calls,
                "budget_tokens": self.daily_tokens,
            }

    async def acquire(self, estimated_tokens: int) -> tuple[bool, str]:
        """Reserve one call and ``estimated_tokens``. Returns ``(allowed, reason)``.

        A caller that receives ``True`` owns the reservation and must call
        :meth:`settle` (billable response) or :meth:`release` (no billable request) with
        the same token estimate before the process is done with it.
        """
        estimated = max(1, int(estimated_tokens))
        with self._lock:
            self._rollover()
            if self.calls >= self.daily_calls:
                return False, f"daily call budget ({self.daily_calls}) reached"
            if self.tokens + estimated > self.daily_tokens:
                return False, "daily token budget reached"
            now = time.monotonic()
            wait = max(0.0, self.min_interval_s - (now - self._last))
            # A configured interval larger than the cap is still one legal wait; the cap
            # exists to refuse a *backlog*, not to make the first caller impossible.
            if wait > max(self.MAX_PACING_WAIT_S, self.min_interval_s):
                return False, (
                    f"local pacing backlog ({wait:.1f}s); the next slot is too far away"
                )
            # Move the schedule forward, not to "now": the next caller has to wait behind
            # the slot this one just took.
            self._last = now + wait
            self.calls += 1
            self.tokens += estimated
        if wait > 0:
            await asyncio.sleep(wait)
        return True, ""

    def settle(self, reserved_tokens: int, actual_tokens: int) -> None:
        """Reconcile a reservation with the provider's reported usage."""
        reserve = max(0, int(reserved_tokens))
        actual = max(0, int(actual_tokens))
        with self._lock:
            self._rollover()
            self.tokens = max(0, self.tokens - reserve + actual)

    def release(self, *, calls: int = 1, tokens: int = 0) -> None:
        """Give a reservation back: the request never reached the billable path."""
        with self._lock:
            self._rollover()
            self.calls = max(0, self.calls - max(0, int(calls)))
            self.tokens = max(0, self.tokens - max(0, int(tokens)))

    def record(self, calls: int, tokens: int) -> None:
        """Add usage that was not reserved through :meth:`acquire` (kept for callers)."""
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


def _items_schema(extra: Any) -> dict[str, Any]:
    """The ``items`` half of an array, from a Gemini fragment or a compact object spec."""
    if not extra:
        return {"type": "STRING"}
    if isinstance(extra, dict):
        if "type" in extra:
            return dict(extra)
        return _schema_for(extra) or {"type": "STRING"}
    if isinstance(extra, list):
        inner = _schema_for(extra[0]) if extra and isinstance(extra[0], dict) else None
        return inner or {"type": "STRING"}
    return {"type": "STRING"}


def _type_schema(raw: Any) -> tuple[dict[str, Any], bool]:
    """One property's schema, and whether the compact spec marked it optional."""
    optional = False
    if isinstance(raw, str) and raw.startswith("?"):
        raw, optional = raw[1:], True
    if isinstance(raw, tuple):
        typ, extra = raw
        if isinstance(typ, str) and typ.startswith("?"):
            typ, optional = typ[1:], True
        if typ == "array":
            return {"type": "ARRAY", "items": _items_schema(extra)}, optional
        if typ == "object" and isinstance(extra, dict):
            nested = _schema_for(extra)
            if nested:
                return nested, optional
        raw = typ
    if isinstance(raw, list):
        # ``[{"id": "str", ...}]`` is a compact array-of-object: the teach-back judge
        # uses it for the quoted points, and treating it as a string (the old
        # behaviour) told the provider the wrong shape.
        inner = _schema_for(raw[0]) if raw and isinstance(raw[0], dict) else None
        return {"type": "ARRAY", "items": inner or {"type": "STRING"}}, optional
    if isinstance(raw, dict):
        return dict(raw), optional
    primitive = {
        "str": "STRING",
        "int": "INTEGER",
        "num": "NUMBER",
        "bool": "BOOLEAN",
        "object": "OBJECT",
        "array": "ARRAY",
    }.get(str(raw), "STRING")
    if primitive == "ARRAY":
        return {"type": "ARRAY", "items": {"type": "STRING"}}, optional
    return {"type": primitive}, optional


def _schema_for(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Translate a compact field spec into Gemini's OpenAPI-subset responseSchema.

    The compact form is ``{"field": "str"}``. A leading ``?`` on either the key or the
    type marks a field the model may omit::

        {"answer": "str", "?why_not_more": "str"}

    Only non-optional fields go into ``required``. A schema that demands every field turns
    a valid short answer into a parse failure, and a parse failure costs a local fallback
    that was avoidable.
    """
    if not spec:
        return None
    props: dict[str, Any] = {}
    required: list[str] = []
    for raw_key, raw_type in spec.items():
        key = str(raw_key)
        optional = key.startswith("?")
        if optional:
            key = key[1:]
        value, type_optional = _type_schema(raw_type)
        if not (optional or type_optional):
            required.append(key)
        props[key] = value
    out: dict[str, Any] = {"type": "OBJECT", "properties": props}
    if required:
        out["required"] = required
    return out


# --------------------------------------------------------------------------- #
# reading the provider's own model list
#
# The point of this block is a fact measured on a real key: `/models` lists IDs the
# key cannot call. `gemini-2.5-flash` was in the list while answering
# `404 ... no longer available to new users`. So the list is used as *candidates to
# try*, ranked, never as an answer about what works -- only a 200 proves that.
# --------------------------------------------------------------------------- #

#: ``(\d+(\.\d+)?)`` inside a model ID: ``gemini-3.8-flash`` -> 3.8, ``gemini-3-flash``
#: -> 3.0, ``gemini-flash-latest`` -> 0.0 (an alias is ranked by being an alias).
_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?")


def model_family(name: str) -> str:
    """``flash-lite``, ``flash``, ``pro``, or ``""`` for a family this build does not know."""
    lowered = str(name or "").lower()
    if "flash-lite" in lowered:
        return "flash-lite"
    if "flash" in lowered:
        return "flash"
    if "pro" in lowered:
        return "pro"
    return ""


def is_text_model(name: str) -> bool:
    """Can this client ask this ID for teaching text?

    ``/models`` mixes products: there are speech models (``*-tts``), music
    (``lyria``), image models (``*-image``, ``nano-banana``), embedding models, and
    separate surfaces (``deep-research``, ``antigravity``). Asking any of them for a
    paragraph of teaching is a request that cannot succeed, so they are never offered
    as a replacement for a retired ID.
    """
    lowered = str(name or "").lower()
    return not any(marker in lowered for marker in NON_TEXT_MODEL_MARKERS)


def _model_version(name: str) -> float:
    match = _VERSION_RE.search(str(name or ""))
    if not match:
        return 0.0
    return float(f"{match.group(1)}.{match.group(2) or 0}")


def rank_models(names: Sequence[str], tier: str = "standard") -> list[str]:
    """Text-capable models, best for ``tier`` first.

    Four keys, in descending weight:

    1. the family this class of work wants (:data:`adapt.config.TIER_MODEL_FAMILIES`);
    2. a rolling alias over a versioned ID -- an alias tracks what the provider serves
       *now*, which is the property that made it the default in the first place;
    3. a stable release over a ``-preview``;
    4. the newest version, then the name, so the order is a fact about the list rather
       than about whatever order the provider happened to return it in.
    """
    order = TIER_MODEL_FAMILIES.get(str(tier), TIER_MODEL_FAMILIES["standard"])

    def key(name: str) -> tuple[int, int, int, float, str]:
        lowered = str(name).lower()
        family = model_family(lowered)
        return (
            order.index(family) if family in order else len(order),
            0 if lowered.endswith(MODEL_ALIAS_SUFFIX) else 1,
            1 if MODEL_PREVIEW_SUFFIX in lowered else 0,
            -_model_version(lowered),
            lowered,
        )

    return sorted((str(n) for n in names if str(n).strip() and is_text_model(n)), key=key)


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
        #: Model IDs that returned 404 in this process. A deployment whose configured
        #: model was retired should not pay a failed round trip on every call.
        self._unavailable_models: set[str] = set()
        #: Dead ID -> the model that answered for it. Stronger than the configured chain:
        #: it is a model this key has already been answered by today.
        self._replacements: dict[str, str] = {}
        #: The filtered model list from one discovery, or None until discovery runs.
        self._discovered: list[str] | None = None
        #: The raw ``/models`` rows behind it, so `list_models` and discovery share one
        #: request instead of racing each other.
        self._model_rows_cache: list[dict[str, Any]] | None = None
        #: Discovery is attempted at most once: a key whose list cannot be read must not
        #: pay a second failed request on every call that follows.
        self._discovery_failed = False
        #: Why the last discovery attempt failed, for the operator's console. Kept apart
        #: from ``last_error`` so a discovery failure cannot overwrite the 404 it was
        #: trying to repair -- the 404 is the message the operator has to act on.
        self.discovery_error: str | None = None
        self._discovery_lock: asyncio.Lock | None = None
        self._discovery_loop: Any = None
        self.stats: dict[str, int] = {
            "ok": 0,
            "quota": 0,
            "error": 0,
            "disabled": 0,
            "throttled": 0,
            # A 200 that carried no usable answer is tracked apart from a transport
            # failure: the first says the prompt or the filter needs attention, the
            # second says the network does.
            "blocked": 0,
            "empty": 0,
            "parse_error": 0,
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

    def _model_chain(
        self, model: str, fallback_model: str = "", *, extra: Sequence[str] = ()
    ) -> list[str]:
        """Models to try, in order, when ``model`` answers 404.

        Three sources, in this order:

        1. the operator's explicit ``ADAPT_GEMINI_MODEL_FALLBACK`` -- deliberate
           configuration outranks anything this build inferred;
        2. a model this process has already been answered by, remembered per dead ID;
        3. any IDs discovered from the key's own ``/models`` list (``extra``) --
           ranked ahead of the other classes, because by the time discovery runs the
           configured classes have already failed once or had nothing to offer;
        4. the other configured classes (standard before light before deep).

        Without 2, 3 and 4, one retired tier ID -- a provider rename, a model gated for
        a new key -- would take down every call that routes through that class, and a
        deployment that pins all three classes (or pins a role) would have nothing left
        to try at all. IDs that already failed in this process are never retried.
        """
        class_models = [
            routing.model_for_tier(tier, self.settings) for tier in ("standard", "light", "deep")
        ]
        candidates: list[str] = [
            fallback_model,
            self._replacements.get(model, ""),
            *extra,
            *class_models,
        ]
        seen: set[str] = set()
        out: list[str] = []
        for raw in candidates:
            name = str(raw or "").strip()
            if not name or name == model or name in seen or name in self._unavailable_models:
                continue
            seen.add(name)
            out.append(name)
        return out

    # -- model discovery -------------------------------------------------- #

    def _discovery_lock_for(self, loop: Any) -> asyncio.Lock:
        """The single-flight lock, rebuilt if the running loop changed.

        Lazily created and loop-keyed so one client can serve several event loops (a
        test suite, a reload) instead of raising "is bound to a different event loop"
        on the first 404 after a restart.
        """
        if self._discovery_lock is None or self._discovery_loop is not loop:
            self._discovery_lock = asyncio.Lock()
            self._discovery_loop = loop
        return self._discovery_lock

    async def _fetch_models(self) -> list[dict[str, Any]] | None:
        """Raw rows from ``/models``, or ``None`` when the list could not be read.

        ``None`` is not an empty list and the difference matters: an empty list says
        "the list was read and has nothing in it", ``None`` says "ask again and you
        will fail again". Discovery remembers the second so it is only paid once.
        """
        if not self.configured:
            self.discovery_error = "no ADAPT_GEMINI_API_KEY"
            return None
        url = f"{self.settings.gemini_base_url.rstrip('/')}/models?pageSize=200"
        headers = {"x-goog-api-key": self.settings.gemini_api_key or ""}
        try:
            client = await self._http()
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            self.discovery_error = f"{type(exc).__name__}: {exc}"[:200]
            log.info("model discovery failed (%s); no replacement to offer", type(exc).__name__)
            return None
        if resp.status_code != 200:
            self.discovery_error = f"{resp.status_code}: {_err_text(resp)}"[:240]
            log.info("model discovery answered %s; no replacement to offer", resp.status_code)
            return None
        try:
            data = resp.json()
        except ValueError:
            self.discovery_error = "the model list was not JSON"
            return None
        rows = data.get("models") if isinstance(data, dict) else None
        return [row for row in rows or [] if isinstance(row, dict)]

    async def _model_rows(self) -> list[dict[str, Any]] | None:
        """The raw ``/models`` rows, read at most once per process.

        Lazy (only when something needs it), single-flight (one coroutine asks; the
        rest wait for its answer) and remembered -- including the failure, so a key
        whose list cannot be read is not asked again on every later call.
        """
        if self._model_rows_cache is not None:
            return self._model_rows_cache
        if self._discovery_failed:
            return None
        async with self._discovery_lock_for(asyncio.get_running_loop()):
            if self._model_rows_cache is not None:
                return self._model_rows_cache
            if self._discovery_failed:
                return None
            try:
                rows = await self._fetch_models()
            except Exception as exc:
                # Discovery is an optimisation on top of a failure. Nothing it can raise
                # -- not a transport quirk, not a shape the parser did not expect -- is
                # allowed to reach a learner: the call takes its local fallback.
                log.warning("model discovery raised %s; no replacement", type(exc).__name__)
                self.discovery_error = f"{type(exc).__name__}: {exc}"[:200]
                rows = None
            if rows is None:
                self._discovery_failed = True
                return None
            self._model_rows_cache = rows
            return rows

    async def _text_models(self) -> list[str]:
        """The provider's generateContent-capable IDs, ranked nowhere yet.

        Uses the same client, headers and settings as every other request: no SDK, so
        there is no second way to reach the provider that a test cannot fake.
        """
        if self._discovered is not None:
            return self._discovered
        rows = await self._model_rows()
        if not rows:
            return []
        names: list[str] = []
        for row in rows:
            # A key can see embedding/TTS/image models it cannot use here. When the
            # provider states the supported methods, keep only IDs that can answer a
            # generateContent call; when it does not, keep the row rather than hide
            # a usable model.
            methods = row.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            name = str(row.get("name") or "").strip().removeprefix("models/")
            if name:
                names.append(name)
        self._discovered = names
        log.info("model discovery offered %s generateContent-capable IDs", len(names))
        return names

    async def candidate_models(self, tier: str = "standard") -> list[str]:
        """Models this key could call for ``tier``, best first, or ``[]`` if unknown.

        Public because the operator's console prints it: after a retired ID, ``adapt
        config test`` names what this key *can* use. It costs nothing once discovery
        has run (the answer is cached) and nothing at all when discovery has not -- it
        never turns a successful probe into two requests.
        """
        names = await self._text_models()
        if not names:
            return []
        return rank_models(names, tier)

    async def known_candidates(self, tier: str = "standard") -> list[str]:
        """Models this key could call for ``tier``, **if that is already known**.

        Returns ``[]`` rather than asking the provider. That is the whole point: this
        exists so a console command that is already reporting a failure cannot add a
        request of its own -- and it costs nothing when it is useful, because a 404
        that reached the console has already run discovery and cached its answer.
        """
        if self._discovered is None:
            return []
        return rank_models(self._discovered, tier)

    def available(self) -> dict[str, object]:
        return {
            "provider": "gemini",
            "configured": self.configured,
            "models": effective_models(self.settings),
            "fallback_model": (
                str(getattr(self.settings, "gemini_model_fallback", "") or "") or None
            ),
            "unavailable_models": sorted(self._unavailable_models),
            "replacements": dict(sorted(self._replacements.items())),
            "discovered_models": (0 if self._discovered is None else len(self._discovered)),
            "discovery_error": self.discovery_error,
            "headroom": self.guard.headroom(),
            "last_error": self.last_error,
            "stats": dict(self.stats),
            "routing": routing.report(
                self.settings, unknown=tuple(sorted(self.unregistered_purposes))
            ),
            "context_reuse": self.cache.report(),
            "prefixless_tasks": sorted(self.prefixless_calls),
        }

    async def list_models(self) -> list[str]:
        """The model IDs this key can **see**, for the operator's console.

        Note the verb: this is what ``/models`` lists, and a listed ID is not a promise
        that it answers -- retired IDs stay on the list while returning 404, which is
        why :meth:`candidate_models` exists and why ``adapt config models`` says so.

        A diagnostic, not part of the learner path: it makes no learner data part of the
        request and returns only the provider's own names. An unavailable list is an empty
        list -- the CLI prints the failure rather than a traceback.
        """
        if not self.configured:
            return []
        rows = await self._model_rows()
        if not rows:
            self.last_error = self.discovery_error
            return []
        out: list[str] = []
        for row in rows:
            # A key can see embedding/TTS/image models it cannot use here. When the
            # provider states the supported methods, keep only IDs that can answer a
            # generateContent call; when it does not, keep the row rather than hide a
            # usable model.
            methods = row.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            name = str(row.get("name") or "").strip().removeprefix("models/")
            if name:
                out.append(name)
        return out

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

        A 200 response carrying no usable text is a failure, not an answer: safety blocks
        and empty candidate lists return ``ok=False`` with ``status="blocked"`` or
        ``"empty"`` so the caller takes its local fallback. A structured call whose text
        is not the requested JSON is ``status="parse_error"`` for the same reason.

        A 404 -- the configured model ID retired or not enabled for this key -- is not
        the end of the call either: up to :data:`_MAX_MODEL_SWITCHES` other models are
        tried, the second of them discovered from the key's own ``/models`` list, and
        ``result.model`` names whichever one answered.
        """
        started = time.monotonic()
        plan = self.plan(
            purpose,
            role=role,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        fallback_model = str(getattr(self.settings, "gemini_model_fallback", "") or "").strip()
        if plan.model in self._unavailable_models:
            replacements = self._model_chain(plan.model, fallback_model)
            if replacements:
                plan = replace(plan, model=replacements[0])
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
                except Exception as exc:  # pragma: no cover - exercised at the service boundary
                    log.warning(
                        "model consent lookup failed (%s); refusing learner call",
                        type(exc).__name__,
                    )
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

        base_url = self.settings.gemini_base_url.rstrip("/")
        headers = {
            "x-goog-api-key": self.settings.gemini_api_key or "",
            "content-type": "application/json",
        }
        switches = 0
        # IDs that answered 404 during this one call. Whatever ends up answering is
        # remembered against each of them, so the next call starts from a model this key
        # was already answered by.
        dead_here: list[str] = []
        attempts = 0
        last: LLMResult | None = None
        pass_no = 0

        def failed(result: LLMResult) -> LLMResult:
            """Stamp the shared failure fields on a result and count it once."""
            self.stats["error"] += 1
            result.tier = result.tier or plan.tier
            result.latency_ms = int((time.monotonic() - started) * 1000)
            result.redacted_fields = redacted
            return result

        # Two passes at most. The second exists only for a cache the provider turns out
        # not to support, and it re-sends the full prompt. A 404 on a configured model
        # retries on up to two other models -- a configured replacement, then one
        # discovered from the key's own model list -- without consuming a cache pass.
        while pass_no < 2:
            if pass_no == 1:
                # The cached head was refused: forget the entry we thought we had, remember
                # the refusal, and send the prompt in full. This is the difference between
                # "caching is unavailable on this deployment" and "every call pays for a
                # failing round trip".
                self.cache.invalidate(cache_key)
                self.cache.refuse(
                    cache_key,
                    why=str(last.error if last else "cached call refused")[:120],
                )
                cache_entry = None

            url = f"{base_url}/models/{plan.model}:generateContent"
            history_text = [h["parts"][0]["text"] for h in safe_history]
            payload_messages = [safe_system, safe_user, *history_text]
            if cache_entry is None and safe_prefix:
                # The uncached path sends the head in full, so the day's budget has to count it.
                payload_messages = [safe_prefix, *payload_messages]
            estimated = estimate_tokens(payload_messages) + plan.max_output_tokens
            allowed, why = await self.guard.acquire(estimated)
            if not allowed:
                pacing = why.startswith("local pacing backlog")
                self.stats["throttled" if pacing else "quota"] += 1
                return LLMResult(
                    ok=False,
                    status="throttled" if pacing else "quota",
                    error=why,
                    redacted_fields=redacted,
                    model=plan.model,
                    tier=plan.tier,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            if self.limiter is not None:
                ok, retry = self.limiter.allow("gemini", "shared")
                if not ok:
                    self.guard.release(tokens=estimated)
                    self.stats["throttled"] += 1
                    return LLMResult(
                        ok=False,
                        status="throttled",
                        error=f"local pacing limit ({retry:.1f}s)",
                        redacted_fields=redacted,
                        tier=plan.tier,
                    )

            body = self._body(
                plan=plan, prefix=safe_prefix, system=safe_system, user=safe_user,
                history=safe_history,
                temperature=plan.temperature, max_output_tokens=plan.max_output_tokens,
                response_spec=response_spec, cache_entry=cache_entry,
            )
            restart_with_fallback = False
            retry_without_cache = False
            for attempt in range(self.settings.gemini_max_retries + 1):
                attempts = attempt + 1
                try:
                    client = await self._http()
                    resp = await client.post(url, headers=headers, json=body)
                except httpx.TimeoutException:
                    last = LLMResult(
                        ok=False,
                        status="error",
                        error="timeout",
                        attempts=attempts,
                        tier=plan.tier,
                    )
                    if attempt < self.settings.gemini_max_retries:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    self.guard.release(tokens=estimated)
                    break
                except httpx.HTTPError as exc:
                    # DNS/TLS/connection refused -- typical for a plane, a cafe, or a
                    # blocked egress. Treated exactly like a quota miss: fall back.
                    self.guard.release(tokens=estimated)
                    self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                    return failed(LLMResult(
                        ok=False,
                        status="error",
                        error=self.last_error,
                        attempts=attempts,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        tier=plan.tier,
                    ))

                if resp.status_code == 429:
                    # The provider rejected the call for rate reasons. The reservation
                    # already counted the call; settle it with no tokens rather than
                    # adding a second unit.
                    self.guard.settle(estimated, 0)
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
                    self.guard.release(tokens=estimated)
                    self.last_error = f"auth {resp.status_code}: {_err_text(resp)}"[:200]
                    return failed(
                        LLMResult(
                            ok=False,
                            status="error",
                            error=self.last_error,
                            attempts=attempts,
                            tier=plan.tier,
                        )
                    )
                if resp.status_code == 404 and switches < _MAX_MODEL_SWITCHES:
                    # The configured model no longer exists (or is not enabled for this
                    # key). Retry on the next viable model, up to two of them, and leave
                    # everything else about the call unchanged. The cache entry is
                    # model-specific, so it cannot travel with the new model.
                    #
                    # Where the next model comes from is the whole of the recovery:
                    # first the operator's explicit fallback and the other configured
                    # classes, and -- once configuration has nothing left -- the key's
                    # own ``/models`` list, ranked for this call's tier. That last part
                    # is what makes a *fresh* install work: the provider retires IDs on
                    # its own schedule (and keeps them on the list afterwards), so a
                    # deployment that only knows its three pinned IDs has nowhere to go
                    # when they are gone.
                    dead_here.append(plan.model)
                    self._unavailable_models.add(plan.model)
                    extra: list[str] = []
                    configured = self._model_chain(plan.model, fallback_model)
                    if switches or not configured:
                        # Configuration has been tried (or has nothing to offer): ask
                        # the provider what this key can see. One request, once.
                        extra = await self.candidate_models(str(plan.tier or "standard"))
                    replacements = self._model_chain(plan.model, fallback_model, extra=extra)
                    if replacements:
                        self.guard.release(tokens=estimated)
                        self.last_error = f"404 {_err_text(resp)}"[:600]
                        log.info(
                            "model %s was not available; retrying on %s",
                            plan.model,
                            replacements[0],
                        )
                        switches += 1
                        plan = replace(plan, model=replacements[0])
                        cache_entry = None
                        cache_key = ""  # the entry belonged to the model that was not found
                        restart_with_fallback = True
                        break
                if resp.status_code == 400 and cache_entry is not None:
                    # The provider refused to use this cache with this call (an unsupported
                    # model, a deleted entry, a shifted payload). Take the cache out of the
                    # picture and send the prompt in full: a cache must never be the reason
                    # a learner gets no answer.
                    self.guard.release(tokens=estimated)
                    last = LLMResult(
                        ok=False,
                        status="error",
                        error=f"400 with cached context: {_err_text(resp)}"[:200],
                        attempts=attempts,
                        tier=plan.tier,
                    )
                    log.info(
                        "cached call refused (%s); resending the full prompt",
                        _err_text(resp)[:120],
                    )
                    retry_without_cache = True
                    break
                if resp.status_code >= 500:
                    last = LLMResult(
                        ok=False,
                        status="error",
                        error=f"upstream {resp.status_code}",
                        attempts=attempts,
                        tier=plan.tier,
                    )
                    if attempt < self.settings.gemini_max_retries:
                        await asyncio.sleep(1.2 * (attempt + 1))
                        continue
                    self.guard.release(tokens=estimated)
                    break
                if resp.status_code != 200:
                    self.guard.release(tokens=estimated)
                    self.last_error = f"{resp.status_code}: {_err_text(resp)}"[:600]
                    return failed(
                        LLMResult(
                            ok=False,
                            status="error",
                            error=self.last_error,
                            attempts=attempts,
                            tier=plan.tier,
                        )
                    )

                try:
                    data = resp.json()
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    self.guard.settle(estimated, 0)
                    return failed(LLMResult(
                        ok=False,
                        status="error",
                        error="the provider returned a body that was not a JSON object",
                        attempts=attempts,
                        tier=plan.tier,
                    ))
                text, outcome = _extract_text(data)
                usage = data.get("usageMetadata")
                usage = usage if isinstance(usage, dict) else {}
                tin = int(usage.get("promptTokenCount") or estimate_tokens(payload_messages))
                tout = int(usage.get("candidatesTokenCount") or 0)
                cached_in = int(usage.get("cachedContentTokenCount") or 0)
                if cached_in and cache_key:
                    self.cache.note_reuse(cache_key, cached_in)
                self.guard.settle(estimated, tin + tout)
                if not text:
                    # A 200 is not an answer. ``blocked`` is a safety/policy refusal;
                    # ``empty`` covers every other no-text shape (no candidates, a
                    # MAX_TOKENS cut before any content, an empty parts list). Both force
                    # the caller's local fallback instead of silently teaching nothing.
                    status = "blocked" if outcome.startswith("blocked") else "empty"
                    self.stats[status] += 1
                    return LLMResult(
                        ok=False,
                        text="",
                        data=None,
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
                        error=f"the model returned no usable text ({outcome})"[:240],
                    )
                parsed: dict[str, Any] | None = None
                status = "ok"
                if response_spec:
                    parsed, status = _parse_json(text)
                    if parsed is None:
                        self.stats["parse_error"] += 1
                        return LLMResult(
                            ok=False,
                            text=text,
                            data=None,
                            status="parse_error",
                            model=plan.model,
                            tier=plan.tier,
                            cached_tokens=cached_in,
                            context_cached=cache_entry is not None,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            tokens_in=tin,
                            tokens_out=tout,
                            redacted_fields=redacted,
                            attempts=attempts,
                            error="the model's reply was not the JSON object the schema required",
                        )
                if switches and dead_here:
                    # Remember what answered, per dead ID. The next call on this process
                    # starts from a model this key was already answered by, instead of
                    # paying the same 404 again.
                    for dead in dead_here:
                        self._replacements[dead] = plan.model
                self.stats["ok"] += 1
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

            if restart_with_fallback:
                continue
            if retry_without_cache and pass_no == 0:
                pass_no = 1
                continue
            break

        res = last or LLMResult(
            ok=False,
            status="error",
            error="unknown",
            attempts=attempts,
            tier=plan.tier,
        )
        res.tier = res.tier or plan.tier
        res.latency_ms = int((time.monotonic() - started) * 1000)
        res.redacted_fields = redacted
        self.stats["error"] += 1
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

        Two things worth knowing about the cached shape:

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
                # camelCase is the documented JSON name for the Generative Language API.
                # ``system_instruction`` is the proto name; sending it on the common
                # (non-cached) path is what silently dropped every system prompt.
                "systemInstruction": {"parts": [{"text": full_system}]},
                "contents": [*history, {"role": "user", "parts": [{"text": user}]}],
            }
        body["generationConfig"] = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "topP": 0.95,
        }
        schema = _schema_for(response_spec)
        if schema:
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = schema
        safety = _safety_settings(self.settings)
        if safety:
            body["safetySettings"] = safety
        return body

    async def register_static(
        self, *, model: str, prefix: str, purpose: str = ""
    ) -> Any | None:
        """Register a static head with the provider, or return None if it cannot be cached.

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
        headers = {
            "x-goog-api-key": self.settings.gemini_api_key or "",
            "content-type": "application/json",
        }
        try:
            client = await self._http()
            resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.info(
                "static context could not be registered (%s); sending full prompts",
                type(exc).__name__,
            )
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
    """The provider's own words, whole.

    The limit is generous because the sentence is the actionable part: a retired model
    ID is reported as one long message that ends with the ID the provider wants you to
    use instead, and truncating it at 200 characters cut that recommendation in half
    mid-word. The console clips it again for display; nothing else reads it.
    """
    try:
        j = resp.json()
        err = j.get("error") or {}
        return str(err.get("message") or j)[:600]
    except Exception:
        return resp.text[:600]


def _safety_settings(settings: Any) -> list[dict[str, str]]:
    """The explicit safety policy sent with every request, or ``[]`` when switched off."""
    threshold = str(getattr(settings, "gemini_safety_threshold", "") or "").strip().upper()
    if not threshold:
        return []
    return [{"category": category, "threshold": threshold} for category in SAFETY_CATEGORIES]


def _extract_text(data: dict[str, Any]) -> tuple[str, str]:
    """Return ``(text, outcome)``.

    ``outcome`` is ``""`` when text was found, ``"blocked:<reason>"`` when a safety or
    policy filter emptied the response, and ``"empty:<reason>"`` for every other no-text
    shape. The caller turns either non-empty outcome into ``ok=False`` -- the distinction
    is for the log and the audit row, not for whether a fallback is needed.
    """
    cands = data.get("candidates") or []
    parts: list[str] = []
    finish = ""
    for cand in cands:
        if not isinstance(cand, dict):
            continue
        reason = str(cand.get("finishReason") or "")
        if reason:
            finish = reason
        content = cand.get("content")
        content = content if isinstance(content, dict) else {}
        for part in content.get("parts") or []:
            # Thought summaries are not the answer. Models that emit them mark the part;
            # joining them into the learner's text would surface deliberation as teaching.
            if isinstance(part, dict) and part.get("text") and not part.get("thought"):
                parts.append(str(part["text"]))
    feedback = data.get("promptFeedback")
    feedback = feedback if isinstance(feedback, dict) else {}
    blocked = str(feedback.get("blockReason") or "").strip()
    if blocked:
        log.info("gemini blocked the prompt (reason=%s); using local answer", blocked)
        return "", f"blocked:{blocked}"
    # A filter that cut the candidate short is a refusal even if a fragment of text
    # survived; teaching from the fragment would present a truncated answer as complete.
    if finish.upper() in _BLOCKED_FINISH_REASONS:
        log.info("gemini blocked the candidate (finish=%s); using local answer", finish)
        return "", f"blocked:{finish}"
    if parts:
        text = "\n".join(parts).strip()
        if text:
            return text, ""
    return "", f"empty:{finish or 'no candidates'}"


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
        "note": (
            "counts cover calls attempted through ADAPT only; "
            "the provider keeps its own usage page"
        ),
    }
