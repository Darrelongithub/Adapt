"""Gemini client + quota guard + local fallback.

Transport is the Google AI Studio REST endpoint (``x-goog-api-key`` header, so the
key never lands in a URL or an access log). No SDK dependency: the free tier is a
plain JSON API and keeping it to ``httpx`` means one less thing to break on the
demo machine.

Three properties the blueprint asks for, implemented here:

* **Free-tier safety.** :class:`QuotaGuard` counts calls/tokens per UTC day against
  configured budgets and throttles with a minimum inter-call spacing. When the
  budget is gone, callers get ``status="quota"`` and fall back to the local
  reasoner instead of hammering the API into a longer lockout.
* **Graceful degradation.** Every failure mode (no key, DNS failure, 429, 5xx,
  bad JSON) returns a typed result, never raises, so the app still answers.
* **Minimized egress.** Prompts pass through :mod:`adapt.security.redact` and the
  count of withheld fields is recorded in ``llm_calls`` for the privacy panel.
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
from typing import Any, Sequence

import httpx

from ..config import Settings, get_settings
from ..security.redact import redact_text

log = logging.getLogger("adapt.llm")

JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    data: dict[str, Any] | None = None
    status: str = "ok"  # ok | quota | disabled | error | parse_error | throttled
    provider: str = "gemini"
    model: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    redacted_fields: int = 0
    error: str | None = None
    attempts: int = 0
    fallback: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
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

    def __init__(self, settings: Settings | None = None, *, limiter: Any = None) -> None:
        self.settings = settings or get_settings()
        self.guard = QuotaGuard(
            daily_calls=self.settings.gemini_daily_call_budget,
            daily_tokens=self.settings.gemini_daily_token_budget,
            min_interval_ms=self.settings.gemini_min_interval_ms,
        )
        self.limiter = limiter
        self._client: httpx.AsyncClient | None = None
        self.last_error: str | None = None
        self.stats: dict[str, int] = {"ok": 0, "quota": 0, "error": 0, "disabled": 0, "throttled": 0}

    # -- lifecycle ------------------------------------------------------ #

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.gemini_timeout_s, connect=8.0),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self.settings.gemini_api_key)

    def available(self) -> dict[str, object]:
        return {
            "provider": "gemini",
            "configured": self.configured,
            "models": [self.settings.gemini_model_ai1, self.settings.gemini_model_ai2],
            "headroom": self.guard.headroom(),
            "last_error": self.last_error,
            "stats": dict(self.stats),
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
        temperature: float = 0.35,
        max_output_tokens: int = 1100,
        response_spec: dict[str, Any] | None = None,
        history: Sequence[tuple[str, str]] = (),
        student_id: str | None = None,
    ) -> LLMResult:
        started = time.monotonic()
        if not self.configured:
            return LLMResult(ok=False, status="disabled", error="no ADAPT_GEMINI_API_KEY", provider="none")

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

        payload_messages = [safe_system, safe_user, *(h["parts"][0]["text"] for h in safe_history)]
        est = estimate_tokens(payload_messages) + max_output_tokens
        allowed, why = await self.guard.acquire(est)
        if not allowed:
            self.stats["quota"] += 1
            return LLMResult(
                ok=False,
                status="quota",
                error=why,
                redacted_fields=redacted,
                model=model or self._model_for(role),
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
                )

        body: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": safe_system}]},
            "contents": [
                *safe_history,
                {"role": "user", "parts": [{"text": safe_user}]},
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "topP": 0.95,
            },
        }
        schema = _schema_for(response_spec)
        if schema:
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = schema

        url = f"{self.settings.gemini_base_url.rstrip('/')}/models/{model or self._model_for(role)}:generateContent"
        headers = {"x-goog-api-key": self.settings.gemini_api_key or "", "content-type": "application/json"}

        attempts = 0
        last: LLMResult | None = None
        for attempt in range(self.settings.gemini_max_retries + 1):
            attempts = attempt + 1
            try:
                client = await self._http()
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException:
                last = LLMResult(ok=False, status="error", error="timeout", attempts=attempts)
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
                )
            if resp.status_code in (401, 403):
                self.last_error = f"auth {resp.status_code}: {_err_text(resp)}"[:200]
                return LLMResult(ok=False, status="error", error=self.last_error, attempts=attempts)
            if resp.status_code >= 500:
                last = LLMResult(ok=False, status="error", error=f"upstream {resp.status_code}", attempts=attempts)
                if attempt < self.settings.gemini_max_retries:
                    await asyncio.sleep(1.2 * (attempt + 1))
                    continue
                break
            if resp.status_code != 200:
                self.last_error = f"{resp.status_code}: {_err_text(resp)}"[:240]
                return LLMResult(ok=False, status="error", error=self.last_error, attempts=attempts)

            data = resp.json()
            text = _extract_text(data)
            usage = data.get("usageMetadata") or {}
            tin = int(usage.get("promptTokenCount") or estimate_tokens([safe_system, safe_user]))
            tout = int(usage.get("candidatesTokenCount") or 0)
            self.guard.record(1, tin + tout)
            self.stats["ok"] += 1
            parsed: dict[str, Any] | None = None
            status = "ok"
            if schema:
                parsed, status = _parse_json(text)
            return LLMResult(
                ok=True,
                text=text,
                data=parsed,
                status=status,
                model=model or self._model_for(role),
                latency_ms=int((time.monotonic() - started) * 1000),
                tokens_in=tin,
                tokens_out=tout,
                redacted_fields=redacted,
                attempts=attempts,
            )

        self.stats["error"] += 1
        res = last or LLMResult(ok=False, status="error", error="unknown", attempts=attempts)
        res.latency_ms = int((time.monotonic() - started) * 1000)
        res.redacted_fields = redacted
        return res

    def _model_for(self, role: str) -> str:
        return self.settings.gemini_model_ai1 if role == "ai1" else self.settings.gemini_model_ai2

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
    for r in rows:
        by_status[str(r.status)] = by_status.get(str(r.status), 0) + 1
    return {
        "calls": calls,
        "tokens_in": tin,
        "tokens_out": tout,
        "redacted_fields_total": red,
        "by_status": by_status,
        "cost_usd": 0.0,  # free tier by policy; shown so the demo can prove it
        "note": "counts cover calls attempted through ADAPT only; the provider keeps its own usage page",
    }
