"""Slot 3 (AI 3) — Adaptation API connector.

AI 3's job is to take:
  * **AI 1's learner state** (levers, step-size limit, check-in cognitive load /
    frustration policy, subject ladder rung, language, tone), and
  * **AI 2's content pack** (verified facts, worked examples, misconceptions,
    and check questions),
and adapt **how the material is sequenced and phrased** for *this* learner.

Three ways to connect Slot 3 (all CLI / ``.env`` only, never browser-exposed):

1. **Custom Adaptation Site API** (``ADAPT_AI3_API_URL`` + ``ADAPT_AI3_API_KEY``):
   Connect any HTTPS REST endpoint that accepts ``{concept, title, limits,
   levers, content_pack, recent_evidence}`` and returns ``{"blocks": [...]}``.
2. **OpenAI-Compatible Chat Completions API**
   (``ADAPT_AI3_API_URL`` ending in ``/chat/completions`` + ``ADAPT_AI3_API_KEY``):
   Connect OpenAI, OpenRouter, Together, Groq, or any OpenAI-compatible HTTPS
   endpoint dedicated to AI 3.
3. **Dedicated Gemini Key / Model Pin**
   (``ADAPT_AI3_API_KEY`` and/or ``ADAPT_GEMINI_MODEL_AI3``):
   Runs AI 3 on its own Gemini API key and/or pinned model with ``role="ai3"``.

In every mode:
* Outbound payloads pass through ``redact_payload`` — zero learner PII is sent.
* Returned plans are **untrusted proposals**: they must pass AI 3's whole-plan
  code validator (``adapter.validate_model_plan`` / ``lesson_writer``), and any
  violation falls back cleanly to the deterministic ``LessonAdapter``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from ..ai.adapter import derive_limits, normalise_pack
from ..security.redact import redact_payload
from .http import Transport, auth_headers, guarded_post_json

log = logging.getLogger("adapt.connectors.ai3_adaptation")

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

ADAPTATION_SPEC: dict[str, Any] = {
    "blocks": ("array", {"type": "OBJECT"}),
}

_ADAPTATION_SYSTEM_PROMPT = (
    "You are the lesson adaptation engine. Given verified educational content and a learner's "
    "step-size/load limits, return ONLY a JSON object {\"blocks\": [...]}. "
    "Allowed block kinds: text, worked_example, check, predict, explain, reflect. "
    "Rules:\n"
    "1. Every 'text' block has content {\"text\": \"...\"} within max_words_per_block words "
    "and MUST be immediately followed by a 'check' block.\n"
    "2. Every 'check' block has content {\"question\": \"...?\", \"questions\": 1} asking "
    "exactly one question ending with '?'.\n"
    "3. End with a 'reflect' block {\"text\": \"...\"}.\n"
    "4. Plain text only — never HTML, Markdown, backticks, links, or placeholders.\n"
    "5. Use ONLY facts from the supplied content_pack; never contradict its facts or answers."
)


def slot_enabled(settings: Any | None, *, dedicated_llm: Any = None) -> bool:
    """Whether Slot 3 has a configured external/dedicated adaptation API."""
    if settings is None:
        return False
    if str(getattr(settings, "ai3_api_url", "") or "").strip():
        return True
    if str(getattr(settings, "ai3_api_key", "") or "").strip():
        return True
    return bool(dedicated_llm is not None and getattr(dedicated_llm, "configured", False))


def is_openai_compatible_url(url: str | None) -> bool:
    cleaned = str(url or "").strip().rstrip("/").lower()
    return cleaned.endswith("/chat/completions")


def _extract_json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, Mapping):
        if isinstance(raw.get("plan"), Mapping) and isinstance(raw["plan"].get("blocks"), list):
            return dict(raw["plan"])
        if isinstance(raw.get("data"), Mapping) and isinstance(raw["data"].get("blocks"), list):
            return dict(raw["data"])
        return dict(raw)
    if isinstance(raw, str):
        stripped = _JSON_FENCE.sub("", raw).strip()
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None
    return None


def _parse_openai_chat_response(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return _extract_json_object(payload)
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    message = first.get("message")
    if isinstance(message, Mapping):
        return _extract_json_object(message.get("content"))
    return _extract_json_object(first.get("text"))


def _run_coro_sync(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=35.0)
    return asyncio.run(coro)


def _sanitized_adaptation_request(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact, PII-scrubbed adaptation payload from AI 1 + AI 2 data."""
    pack = normalise_pack(
        inputs.get("content_pack"),
        concept=str(inputs.get("concept") or ""),
        title=str(inputs.get("title") or ""),
    )
    limits = derive_limits(
        levers=inputs.get("levers"),
        preferences=inputs.get("preferences"),
        constraints=inputs.get("constraints"),
    )
    raw = {
        "concept": pack.concept or pack.title,
        "title": pack.title,
        "limits": limits.as_dict(),
        "content_pack": {
            "explanation": list(pack.explanation[:8]),
            "facts": list(pack.facts[:8]),
            "worked_steps": list(pack.worked_steps[:5]),
            "questions": list(pack.questions[:4]),
            "hints": list(pack.hints[:3]),
        },
        "ladder": inputs.get("ladder") or {},
    }
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), default=str)[:8000]
    safe, _dropped, _kinds = redact_payload({"payload": encoded}, max_chars=8000)
    try:
        return json.loads(safe["payload"])
    except ValueError:
        return {"concept": pack.title, "limits": limits.as_dict()}


class AI3AdaptationClient:
    """Dedicated Slot 3 client bridging custom REST APIs, OpenAI-compatible APIs, and Gemini."""

    def __init__(
        self,
        settings: Any,
        *,
        llm: Any = None,
        transport: Transport | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.transport = transport

    @property
    def configured(self) -> bool:
        return slot_enabled(self.settings, dedicated_llm=self.llm)

    def propose_plan(self, **inputs: Any) -> dict[str, Any]:
        """Request an adapted ``{"blocks": [...]}`` proposal for ``LessonAdapter``."""
        request_body = _sanitized_adaptation_request(inputs)
        custom_url = str(getattr(self.settings, "ai3_api_url", "") or "").strip()
        api_key = getattr(self.settings, "ai3_api_key", None)
        api_header = str(
            getattr(self.settings, "ai3_api_header", "Authorization") or "Authorization"
        )
        timeout = float(getattr(self.settings, "connector_timeout_s", 15.0) or 15.0)

        if custom_url:
            parsed = urlparse(custom_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise RuntimeError("Slot ai3 URL must use https://")
            headers = auth_headers(api_key, api_header)
            if is_openai_compatible_url(custom_url):
                model_name = (
                    str(getattr(self.settings, "gemini_model_ai3", "") or "").strip()
                    or "gpt-4o-mini"
                )
                oa_body: dict[str, Any] = {
                    "model": model_name,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _ADAPTATION_SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(request_body, ensure_ascii=False)},
                    ],
                }
                payload, _src = guarded_post_json(
                    connector="ai3_openai",
                    allowed_host=parsed.hostname,
                    url=custom_url,
                    headers=headers,
                    body=oa_body,
                    timeout_s=max(timeout, 15.0),
                    transport=self.transport,
                )
                extracted = _parse_openai_chat_response(payload)
                if extracted is None:
                    raise RuntimeError("Slot ai3 OpenAI-compatible endpoint returned no valid JSON")
                return extracted

            payload, _src = guarded_post_json(
                connector="ai3_custom",
                allowed_host=parsed.hostname,
                url=custom_url,
                headers=headers,
                body=request_body,
                timeout_s=max(timeout, 12.0),
                transport=self.transport,
            )
            extracted = _extract_json_object(payload)
            if extracted is None:
                raise RuntimeError("Slot ai3 adaptation API returned no valid plan object")
            return extracted

        if self.llm is not None and getattr(self.llm, "configured", False):
            user_text = json.dumps(request_body, ensure_ascii=False, separators=(",", ":"))

            async def _ask_gemini() -> dict[str, Any]:
                res = await self.llm.complete(
                    system=_ADAPTATION_SYSTEM_PROMPT,
                    user=user_text,
                    purpose="ai3_lesson_plan",
                    role="ai3",
                    student_id=inputs.get("student_id"),
                    temperature=0.35,
                    max_output_tokens=1400,
                    response_spec=ADAPTATION_SPEC,
                )
                if not res.ok or not isinstance(res.data, dict):
                    raise RuntimeError(f"Slot ai3 model did not return a plan ({res.status})")
                return res.data

            return _run_coro_sync(_ask_gemini())

        raise RuntimeError("Slot ai3 is not configured")

    def call_frozen_writer(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        purpose: str = "ai3_lesson_plan",
        student_id: str | None = None,
        max_tokens: int = 1400,
    ) -> dict[str, Any]:
        """Execute a structured AI 3 call for ``lesson_writer`` (plan / explain_wrong / reteach)."""
        safe_map, _dropped, _kinds = redact_payload({"u": str(user or "")}, max_chars=12_000)
        safe_user = safe_map["u"]
        custom_url = str(getattr(self.settings, "ai3_api_url", "") or "").strip()
        api_key = getattr(self.settings, "ai3_api_key", None)
        api_header = str(
            getattr(self.settings, "ai3_api_header", "Authorization") or "Authorization"
        )
        timeout = float(getattr(self.settings, "connector_timeout_s", 15.0) or 15.0)

        if custom_url:
            parsed = urlparse(custom_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise RuntimeError("Slot ai3 URL must use https://")
            headers = auth_headers(api_key, api_header)
            if is_openai_compatible_url(custom_url):
                model_name = (
                    str(getattr(self.settings, "gemini_model_ai3", "") or "").strip()
                    or "gpt-4o-mini"
                )
                body = {
                    "model": model_name,
                    "temperature": 0.35,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": safe_user},
                    ],
                }
                payload, _src = guarded_post_json(
                    connector="ai3_openai",
                    allowed_host=parsed.hostname,
                    url=custom_url,
                    headers=headers,
                    body=body,
                    timeout_s=max(timeout, 20.0),
                    transport=self.transport,
                )
                extracted = _parse_openai_chat_response(payload)
                if extracted is None:
                    raise RuntimeError("Slot ai3 OpenAI-compatible endpoint returned invalid JSON")
                return extracted

            payload, _src = guarded_post_json(
                connector="ai3_custom",
                allowed_host=parsed.hostname,
                url=custom_url,
                headers=headers,
                body={"purpose": purpose, "system": system, "user": safe_user, "schema": schema},
                timeout_s=max(timeout, 20.0),
                transport=self.transport,
            )
            extracted = _extract_json_object(payload)
            if extracted is None:
                raise RuntimeError("Slot ai3 custom API returned invalid JSON")
            return extracted

        if self.llm is not None and getattr(self.llm, "configured", False):
            async def _invoke() -> dict[str, Any]:
                res = await self.llm.complete(
                    system=system,
                    user=safe_user,
                    purpose=purpose,
                    role="ai3",
                    student_id=student_id,
                    max_output_tokens=max_tokens,
                    response_spec=schema,
                )
                if not res.ok or not isinstance(res.data, dict):
                    raise RuntimeError(f"Slot ai3 model call did not return JSON ({res.status})")
                return res.data

            return _run_coro_sync(_invoke())

        raise RuntimeError("Slot ai3 is not configured")


def health(
    settings: Any,
    *,
    llm: Any = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """One minimal live probe of AI 3's configured Adaptation API."""
    custom_url = str(getattr(settings, "ai3_api_url", "") or "").strip()
    ai3_key = str(getattr(settings, "ai3_api_key", "") or "").strip()
    shared_key = str(getattr(settings, "gemini_api_key", "") or "").strip()

    if not custom_url and not ai3_key and not shared_key and llm is None:
        return {
            "ok": False,
            "detail": (
                "Slot ai3 is off (set ADAPT_AI3_API_URL with `adapt config set-url --slot ai3` "
                "or set a dedicated key with `adapt config set-key --service ai3`)"
            ),
        }

    if custom_url:
        client = AI3AdaptationClient(settings, llm=llm, transport=transport)
        sample_pack = {
            "concept": "probe",
            "title": "Probe",
            "explanation": "Addition combines two quantities into one total.",
            "questions": [{"question": "What does addition combine?", "format": "short_text"}],
        }
        try:
            proposal = client.propose_plan(concept="probe", title="Probe", content_pack=sample_pack)
        except Exception as exc:
            return {
                "ok": False,
                "detail": f"Slot ai3 endpoint did not answer ({type(exc).__name__})",
            }
        if isinstance(proposal, Mapping) and isinstance(proposal.get("blocks"), list):
            return {"ok": True, "detail": "Slot ai3 adaptation endpoint answered with blocks"}
        return {
            "ok": False,
            "detail": "Slot ai3 endpoint answered, but without a valid blocks array",
        }

    # Gemini-backed Slot 3 probe (dedicated ai3_api_key or shared gemini_api_key).
    active_llm = llm
    created_temp = False
    if active_llm is None:
        from ..ai.llm import GeminiLLM

        effective_key = ai3_key or shared_key
        probe_settings = settings.model_copy(
            update={
                "gemini_api_key": effective_key,
                "gemini_max_retries": 0,
                "gemini_min_interval_ms": 0,
            }
        )
        active_llm = GeminiLLM(probe_settings)
        created_temp = True

    async def _probe():
        try:
            return await active_llm.complete(
                system="Reply with the single word OK.",
                user="Connection check.",
                purpose="console_connection_test",
                role="ai3",
                temperature=0.0,
                max_output_tokens=8,
            )
        finally:
            if created_temp and hasattr(active_llm, "aclose"):
                await active_llm.aclose()

    try:
        res = _run_coro_sync(_probe())
    except Exception as exc:
        return {"ok": False, "detail": f"Slot ai3 model probe raised {type(exc).__name__}"}
    if res.ok:
        mode = "dedicated ai3 key" if ai3_key else "shared gemini key"
        return {
            "ok": True,
            "detail": f"Slot ai3 model probe passed ({res.model or 'ok'} via {mode})",
        }
    return {"ok": False, "detail": f"Slot ai3 model probe failed ({res.status})"}
