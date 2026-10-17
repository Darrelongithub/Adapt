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
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

import httpx

from ..config import Settings, get_settings
from ..security.redact import redact_text
from . import routing
from .context_cache import ContextCache

log = logging.getLogger("adapt.llm")

JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    data: dict[str, Any] | None = None
    status: str = "ok"  # ok | quota | disabled | consent_required | error | parse_error | throttled
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
    """Daily budget + spacing enforcement for the free tier."""

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
        return datetime.now(timezone.utc).date().isoformat()

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
        """Reserve budget. Returns ``(allowed, reason)``; callers must fall back."""
        with self._lock:
            self._rollover()
            if self.calls >= self.daily_calls:
                return False, f"daily call budget ({self.daily_calls}) reached"
            if self.tokens + estimated_tokens > self.daily_tokens:
                return False, "daily token budget reached"
            wait = self.min_interval_s - (time.monotonic() - self._last)
            self._last = time.monotonic()
        if wait > 0:
            # Pacing protects the RPM ceiling, but a demo must not stall: cap it.
            await asyncio.sleep(min(wait, 3.0))
        return True, ""

    def record(self, calls: int, tokens: int) -> None:
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


def _schema_for(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Translate a compact field spec into Gemini's OpenAPI-subset responseSchema."""
    if not spec:
        return None
    props: dict[str, Any] = {}
    for key, typ in spec.items():
        if isinstance(typ, tuple):
            typ, extra = typ  # ("array", {...})
        else:
            extra = None
        if typ == "str":
            props[key] = {"type": "STRING"}
        elif typ == "int":
            props[key] = {"type": "INTEGER"}
        elif typ == "num":
            props[key] = {"type": "NUMBER"}
        elif typ == "bool":
            props[key] = {"type": "BOOLEAN"}
        elif typ == "array":
            props[key] = {"type": "ARRAY", "items": (extra or {"type": "STRING"})}
        elif typ == "object":
            props[key] = {"type": "OBJECT"}
        else:
            props[key] = {"type": "STRING"}
    return {"type": "OBJECT", "properties": props, "required": list(props.keys())}


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
        self.stats: dict[str, int] = {
            "ok": 0,
            "quota": 0,
            "error": 0,
            "disabled": 0,
            "throttled": 0,
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

        payload_messages = [safe_system, safe_user, *(h["parts"][0]["text"] for h in safe_history)]
        if cache_entry is None and safe_prefix:
            # The uncached path sends the head in full, so the day's budget has to count it.
            payload_messages = [safe_prefix, *payload_messages]
        est = estimate_tokens(payload_messages) + (max_output_tokens or plan.max_output_tokens)
        allowed, why = await self.guard.acquire(est)
        if not allowed:
            self.stats["quota"] += 1
            return LLMResult(
                ok=False,
                status="quota",
                error=why,
                redacted_fields=redacted,
                model=plan.model,
                tier=plan.tier,
            )
        if self.limiter is not None:
            ok, retry = self.limiter.allow("gemini", "shared")
            if not ok:
                self.stats["throttled"] += 1
                return LLMResult(
                    ok=False,
                    status="throttled",
                    error=f"local pacing limit ({retry:.1f}s)",
                    redacted_fields=redacted,
                    tier=plan.tier,
                )

        url = f"{self.settings.gemini_base_url.rstrip('/')}/models/{plan.model}:generateContent"
        headers = {"x-goog-api-key": self.settings.gemini_api_key or "", "content-type": "application/json"}

        attempts = 0
        last: LLMResult | None = None
        # Two passes at most: the second exists only for a cache the provider turns out not
        # to support, and it re-sends the full prompt. Everything else keeps the original
        # retry budget.
        for pass_no in range(2):
            if pass_no == 1:
                # The cached head was refused: forget the entry we thought we had, remember
                # the refusal, and send the prompt in full. This is the difference between
                # "caching is unavailable on this deployment" and "every call pays for a
                # failing round trip".
                self.cache.invalidate(cache_key)
                self.cache.refuse(cache_key, why=str(last.error if last else "cached call refused")[:120])
                cache_entry = None
            body = self._body(
                plan=plan, prefix=safe_prefix, system=safe_system, user=safe_user,
                history=safe_history,
                temperature=plan.temperature, max_output_tokens=plan.max_output_tokens,
                response_spec=response_spec, cache_entry=cache_entry,
            )
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
                    self.guard.record(1, 0)
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

                data = resp.json()
                text = _extract_text(data)
                usage = data.get("usageMetadata") or {}
                tin = int(usage.get("promptTokenCount") or estimate_tokens([safe_system, safe_user]))
                tout = int(usage.get("candidatesTokenCount") or 0)
                cached_in = int(usage.get("cachedContentTokenCount") or 0)
                if cached_in and cache_key:
                    self.cache.note_reuse(cache_key, cached_in)
                self.guard.record(1, tin + tout)
                self.stats["ok"] += 1
                parsed: dict[str, Any] | None = None
                status = "ok"
                if response_spec:
                    parsed, status = _parse_json(text)
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
                "system_instruction": {"parts": [{"text": full_system}]},
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


def _extract_text(data: dict[str, Any]) -> str:
    cands = data.get("candidates") or []
    parts: list[str] = []
    for cand in cands:
        for p in ((cand.get("content") or {}).get("parts") or []):
            if isinstance(p, dict) and p.get("text"):
                parts.append(str(p["text"]))
    if not parts:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        if reason:
            log.info("gemini blocked the prompt (safety=%s); using local answer", reason)
        return ""
    return "\n".join(parts).strip()


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
