"""Last-mile redaction for public HTTP/WebSocket frames and application logs.

This module is intentionally a boundary filter, not a request-data scrubber: it
removes provider/model/quota metadata while preserving ordinary lesson content. The
ASGI middleware is deliberately dependency-free and can wrap either a Starlette app
or an individual test app. The main application installs it through the existing
security-headers middleware so both HTTP responses and WebSocket frames pass it.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

# Generic configured services used by this build.  The model-specific terms supplied
# by Settings are added dynamically, including custom names and credentials.
_PROVIDER_RE = re.compile(
    r"(?i)\b(?:generativelanguage\.googleapis\.com|google(?:\s+generative\s+language)?|"
    r"faster[-_ ]whisper(?:[-/][a-z0-9_.-]+)*|open[ _]*library|openalex|arxiv|youtube|"
    r"kokoro(?:[-/][a-z0-9_.-]+)*|gemini(?:[-/][a-z0-9_.-]+)*)\b"
)
_KEY_NAME_RE = re.compile(r"(?i)\b(?:ADAPT_)?(?:GEMINI|YOUTUBE)_API_KEY\b")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[-_ ]?key|x-goog-api-key|authorization|access[-_ ]?token)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s&,'\"<>]+"
)
_SECRET_QUERY_RE = re.compile(r"(?i)([?&](?:key|api[-_]?key|access[-_]?token)=)[^&\s'\"<>]+")
_BUDGET_PHRASE_RE = re.compile(
    r"(?i)\b(?:daily\s+)?(?:call|token)\s+budget(?:\s*(?:\([^)]*\)|[:=]\s*\d+))?"
    r"(?:\s+(?:reached|exceeded|exhausted))?"
)
_BUDGET_SCALAR_RE = re.compile(
    r'(?i)("(?:daily_call_budget|daily_token_budget|budget_calls|budget_tokens|calls_left|tokens_left)"\s*:\s*)'
    r'(?:"(?:\\.|[^"\\])*"|true|false|null|-?\d+(?:\.\d+)?)'
)
_BUDGET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:(?:daily[ _-]+)?(?:(?:call|token)[ _-]+)?budget|"
    r"budget[ _-]?(?:calls|tokens)|(?:calls|tokens)[ _-](?:left|remaining)|headroom|quota)"
    r"\s*[:=]\s*\d+(?:\.\d+)?"
)
_UNSAFE_ERROR_DETAIL_RE = re.compile(r"(?i)(?:model|provider|budget|quota|gemini|youtube)")
_SECRET_HEADER_RE = re.compile(r"(?i)(?:api[-_ ]?key|authorization|x-goog-api-key)")
_REDACTED = "[withheld]"
_SAFE_LIMIT = "optional service limit reached"
_WITHHELD_COMPRESSED_BODY = b'{"detail":"Compressed response withheld by the privacy boundary."}'
_MAX_BUFFERED_TEXT = 16 * 1024 * 1024


def _normal_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


#: Keys that merely contain the word "model" without naming an AI model or its configuration.
#: `model` is the learner model that §2A requires on `/profile`'s shared snapshot;
#: `model_version` is its version; `sent_to_model` is the provider-neutral honesty flag §3 asks
#: for; `model_calls_saved` counts lessons answered without a model call. `model_name`,
#: `model_id`, `ai2_model` and the rest stay banned: those name a deployed model.
_MODEL_WORD_ALLOWED = frozenset({"senttomodel", "modelversion", "modelcallssaved"})


# The public protocol has no need for operational AI/quota metadata.  Dropping the
# whole metadata object is safer than trying to preserve every future nested shape.
_DROP_KEYS = frozenset(
    {
        "provider",
        "providers",
        "providerdetail",
        "providernote",
        "providername",
        "providerid",
        "apikey",
        "xapikey",
        "xgoogapikey",
        "geminiapikey",
        "youtubeapikey",
        "authorization",
        "ai1provider",
        "ai2provider",
        "models",
        "modelid",
        "modelname",
        "modelms",
        "modelsize",
        "modelconfig",
        "modelconfiguration",
        "modelpath",
        "modelrevision",
        "modelai1",
        "modelai2",
        "ai1model",
        "ai2model",
        "whispermodel",
        "whispermodelsize",
        "sttmodel",
        "ttsmodel",
        "ttsengine",
        "llm",
        "llmmeta",
        "llmstatus",
        "quotas",
        "quota",
        "headroom",
        "budget",
        "budgets",
        "budgetcalls",
        "budgettokens",
        "dailycallbudget",
        "dailytokenbudget",
        "callbudget",
        "tokenbudget",
        "dailybudget",
        "dailycalllimit",
        "dailytokenlimit",
        "callsleft",
        "tokensleft",
        "callsremaining",
        "tokensremaining",
        "freetiers",
        "freemodels",
        "ratelimiter",
        "ratelimit",
        "ratelimits",
        "ratelimitmultiplier",
        "limitssnapshot",
        "limiter",
        "buckets",
        "senttogemini",
    }
)


def _configured_terms(settings: Any | None) -> tuple[str, ...]:
    if settings is None:
        return ()
    terms: set[str] = set()
    for attr in (
        "gemini_api_key",
        "youtube_api_key",
        "gemini_model_ai1",
        "gemini_model_ai2",
        "gemini_base_url",
        "openalex_mailto",
    ):
        value = getattr(settings, attr, None)
        if isinstance(value, str) and value:
            terms.add(value)
    return tuple(sorted(terms, key=len, reverse=True))


def scrub_text(value: object, settings: Any | None = None) -> str:
    """Remove configured key/model literals, provider names and budget disclosures."""
    text = str(value)
    for term in _configured_terms(settings):
        # Configured values are treated as literals, not regular expressions.
        text = re.sub(re.escape(term), _REDACTED, text, flags=re.IGNORECASE)
    text = _KEY_NAME_RE.sub(_REDACTED, text)
    text = _SECRET_ASSIGNMENT_RE.sub(_REDACTED, text)
    text = _SECRET_QUERY_RE.sub(r"\1[withheld]", text)
    text = _PROVIDER_RE.sub(_REDACTED, text)
    text = _BUDGET_PHRASE_RE.sub(_SAFE_LIMIT, text)
    text = _BUDGET_ASSIGNMENT_RE.sub(_SAFE_LIMIT, text)
    text = _BUDGET_SCALAR_RE.sub(r'\1"[withheld]"', text)
    return text


def _is_private_public_key(key: object, settings: Any | None) -> bool:
    """Identify runtime configuration keys, including future naming variants.

    Exact names in ``_DROP_KEYS`` preserve compatibility with known payloads. The
    marker checks stop a route from bypassing the boundary simply by prefixing a
    budget or provider field with a service name (for example,
    ``gemini_daily_call_budget``).
    """
    raw = str(key)
    normalized = _normal_key(raw)
    if normalized in _DROP_KEYS:
        return True
    if any(marker in normalized for marker in ("provider", "budget", "quota", "headroom")):
        return True
    if any(marker in normalized for marker in ("apikey", "authorization", "secret")):
        return True
    if "token" in normalized and "csrf" not in normalized:
        return True
    if "llm" in normalized:
        return True
    if (
        "model" in normalized
        and normalized not in _MODEL_WORD_ALLOWED
        and not any(
            context in normalized
            for context in ("learner", "learning", "mathematical", "conceptual")
        )
    ):
        return True
    if _PROVIDER_RE.search(raw):
        return True
    lowered = raw.casefold()
    return any(term.casefold() in lowered for term in _configured_terms(settings))


def _scrub_value(value: Any, settings: Any | None) -> Any:
    if isinstance(value, str):
        return scrub_text(value, settings)
    if isinstance(value, Mapping):
        error = next((child for key, child in value.items() if _normal_key(key) == "error"), None)
        if isinstance(error, str) and _UNSAFE_ERROR_DETAIL_RE.search(error):
            return {
                "error": "service_unavailable",
                "note": "This feature is temporarily unavailable.",
            }
        out: dict[str, Any] = {}
        for key, child in value.items():
            if _is_private_public_key(key, settings):
                continue
            out[str(key)] = _scrub_value(child, settings)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub_value(child, settings) for child in value]
    return value


#: Keys that name the runtime service behind an answer, or account for what it spent. A learner
#: gets the *effect* (was this written for me, or taken from my own material) and never the
#: service's identity or its spend -- brief 3, "applies to everything".
_PRIVATE_PAYLOAD_KEYS = frozenset(
    {
        "provider",
        "providernote",
        "providername",
        "providers",
        "llm",
        "llmmeta",
        "llmstatus",
        "model",
        "modelname",
        "modelms",
        "modelsize",
        "tokensin",
        "tokensout",
    }
)


def public_payload(data: Mapping[str, Any], *, served_locally: bool) -> dict[str, Any]:
    """A reply body with the service identity replaced by the one fact the learner needs.

    ``served_by`` is the honest, provider-neutral marker: ``"your own material"`` when the answer
    was assembled locally, ``"written for you"`` when a model wrote it (which is also how the
    product already talks about generated content: "written for you, not from your notes").
    """
    out = {k: v for k, v in data.items() if _normal_key(k) not in _PRIVATE_PAYLOAD_KEYS}
    out["served_by"] = "your own material" if served_locally else "written for you"
    return out


def scrub_public_payload(value: Any, settings: Any | None = None) -> Any:
    """Return public JSON data with provider, model and quota configuration removed."""
    return _scrub_value(value, settings)


def scrub_public_body(body: bytes, content_type: str, settings: Any | None = None) -> bytes:
    """Sanitize a response body, preserving JSON structure where possible."""
    content_type_lower = (content_type or "").lower()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        # Binary payloads are not rewritten as text.  The API has no reason to place
        # a runtime configuration value in an audio or archive body.
        return body

    if "ndjson" in content_type_lower or "jsonl" in content_type_lower:
        sanitized_lines = []
        for line in text.splitlines():
            if not line.strip():
                sanitized_lines.append(line)
                continue
            try:
                parsed_line = json.loads(line)
            except json.JSONDecodeError:
                sanitized_lines.append(scrub_text(line, settings))
            else:
                safe_line = scrub_public_payload(parsed_line, settings)
                sanitized_lines.append(
                    json.dumps(safe_line, ensure_ascii=False, separators=(",", ":"))
                )
        return "\n".join(sanitized_lines).encode("utf-8")

    looks_json = text.lstrip().startswith(("{", "["))
    if "json" in content_type_lower or looks_json:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            safe = scrub_public_payload(parsed, settings)
            return json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return scrub_text(text, settings).encode("utf-8")


def _safe_headers(
    headers: list[tuple[bytes, bytes]], settings: Any | None, *, rewrite_body: bool
) -> list[tuple[bytes, bytes]]:
    out: list[tuple[bytes, bytes]] = []
    for name, value in headers:
        original_name = name.decode("latin-1")
        scrubbed_name = scrub_text(original_name, settings)
        if _SECRET_HEADER_RE.search(original_name) or scrubbed_name != original_name:
            continue
        header_name = scrubbed_name.encode("latin-1")
        header_value = scrub_text(value.decode("latin-1"), settings).encode("latin-1")
        if rewrite_body and header_name.lower() in {b"content-length", b"etag", b"content-md5"}:
            continue
        out.append((header_name, header_value))
    return out


def _is_textual(content_type: str, headers: list[tuple[bytes, bytes]]) -> bool:
    lower = content_type.lower()
    if not (
        "json" in lower or lower.startswith("text/") or "xml" in lower or "javascript" in lower
    ):
        return False
    if any(name.lower() == b"content-encoding" for name, _ in headers):
        # The current API does not compress responses in-process.  Avoid silently
        # passing an encoded body through if that changes; fail closed in middleware.
        return True
    return True


class ConfigurationSecrecyMiddleware:
    """ASGI middleware that sanitizes all public textual frames and response headers."""

    def __init__(self, app: Callable[..., Any], settings: Any | None = None) -> None:
        self.app = app
        self.settings = settings
        self._max_body = _MAX_BUFFERED_TEXT

    async def __call__(
        self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]
    ) -> None:
        if scope.get("type") == "websocket":
            await self._websocket(scope, receive, send)
            return
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        await self._http(scope, receive, send)

    async def _http(
        self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]
    ) -> None:
        start: dict[str, Any] | None = None
        chunks: list[bytes] = []
        size = 0
        textual = False
        opaque_text = False
        sent_start = False
        overflow = False

        async def emit_start(message: dict[str, Any], *, rewrite_body: bool) -> None:
            headers = _safe_headers(
                list(message.get("headers", [])), self.settings, rewrite_body=rewrite_body
            )
            if rewrite_body:
                # A boundary rewrite invalidates an existing length and validator.
                headers = [
                    (n, v)
                    for n, v in headers
                    if n.lower() not in {b"content-length", b"etag", b"content-md5"}
                ]
            await send({**message, "headers": headers})

        async def capture(message: dict[str, Any]) -> None:
            nonlocal start, size, textual, opaque_text, sent_start, overflow, chunks
            kind = message.get("type")
            if kind == "http.response.start":
                start = message
                headers = list(message.get("headers", []))
                content_type = next(
                    (v.decode("latin-1") for n, v in headers if n.lower() == b"content-type"),
                    "",
                )
                textual = _is_textual(content_type, headers)
                opaque_text = textual and any(n.lower() == b"content-encoding" for n, _ in headers)
                if not textual:
                    await emit_start(message, rewrite_body=False)
                    sent_start = True
                return
            if kind == "http.response.body" and start is not None and textual:
                part = bytes(message.get("body", b""))
                size += len(part)
                if size > self._max_body:
                    overflow = True
                    chunks.clear()
                elif not overflow:
                    chunks.append(part)
                if message.get("more_body", False):
                    return
                if opaque_text:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 500,
                            "headers": [
                                (b"content-type", b"application/json; charset=utf-8"),
                                (b"cache-control", b"no-store"),
                            ],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": _WITHHELD_COMPRESSED_BODY,
                            "more_body": False,
                        }
                    )
                    sent_start = True
                    return
                if overflow:
                    # Never pass a huge unsanitized error/export through.  This is
                    # exceptional (normal JSON exports are far below the cap), and a
                    # safe error is better than a configuration leak.
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 500,
                            "headers": [
                                (b"content-type", b"application/json; charset=utf-8"),
                                (b"cache-control", b"no-store"),
                            ],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b'{"detail":"Response withheld by the privacy boundary."}',
                            "more_body": False,
                        }
                    )
                    sent_start = True
                    return
                raw = b"".join(chunks)
                headers = list(start.get("headers", []))
                content_type = next(
                    (v.decode("latin-1") for n, v in headers if n.lower() == b"content-type"),
                    "",
                )
                safe_body = scrub_public_body(raw, content_type, self.settings)
                safe_start = {
                    **start,
                    "headers": _safe_headers(headers, self.settings, rewrite_body=True),
                }
                safe_headers = list(safe_start["headers"])
                # If a caller set a content length, it no longer describes the body.
                safe_headers = [(n, v) for n, v in safe_headers if n.lower() != b"content-length"]
                safe_headers.append((b"content-length", str(len(safe_body)).encode("ascii")))
                await send({**safe_start, "headers": safe_headers})
                await send({"type": "http.response.body", "body": safe_body, "more_body": False})
                sent_start = True
                chunks.clear()
                return
            if kind == "http.response.body" and start is not None and not textual:
                part = bytes(message.get("body", b""))
                if not sent_start:
                    await emit_start(start, rewrite_body=False)
                    sent_start = True
                # Binary response bodies are preserved; headers still pass through the
                # configuration scrubber above.
                await send({**message, "body": part})
                return
            if kind == "http.response.start":
                return
            if start is not None and not sent_start and kind == "http.response.trailers":
                await emit_start(start, rewrite_body=textual)
                sent_start = True
            await send(message)

        await self.app(scope, receive, capture)
        # ASGI apps normally emit a final body, but keep empty/early-close responses valid.
        if start is not None and not sent_start:
            if textual:
                headers = _safe_headers(
                    list(start.get("headers", [])), self.settings, rewrite_body=True
                )
                headers = [(n, v) for n, v in headers if n.lower() != b"content-length"]
                safe_start = {**start, "headers": [*headers, (b"content-length", b"0")]}
                await send(safe_start)
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            else:
                await emit_start(start, rewrite_body=False)

    async def _websocket(
        self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]
    ) -> None:
        async def safe_send(message: dict[str, Any]) -> None:
            kind = message.get("type")
            if kind == "websocket.accept" and message.get("headers"):
                await send(
                    {
                        **message,
                        "headers": _safe_headers(
                            list(message.get("headers", [])), self.settings, rewrite_body=False
                        ),
                    }
                )
                return
            if kind == "websocket.send":
                text = message.get("text")
                if isinstance(text, str):
                    # JSON frames need structural filtering too: replacing just the
                    # value would still expose metadata keys and quota figures.
                    if text.lstrip().startswith(("{", "[")):
                        safe = scrub_public_body(
                            text.encode("utf-8"), "application/json", self.settings
                        ).decode("utf-8")
                    else:
                        safe = scrub_text(text, self.settings)
                    await send({**message, "text": safe})
                    return
                data = message.get("bytes")
                if isinstance(data, bytes) and data[:1] in (b"{", b"["):
                    safe = scrub_public_body(data, "application/json", self.settings)
                    await send({**message, "bytes": safe})
                    return
            if kind == "websocket.close" and message.get("reason") is not None:
                await send({**message, "reason": scrub_text(message["reason"], self.settings)})
                return
            await send(message)

        await self.app(scope, receive, safe_send)


_LOG_STATE: dict[str, Any] = {
    "factory": None,
    "base": None,
    "make_record": None,
    "base_make_record": None,
    "settings": None,
}


def _safe_log_text(value: str, settings: Any) -> str:
    if value.lstrip().startswith(("{", "[")):
        return scrub_public_body(value.encode("utf-8"), "application/json", settings).decode(
            "utf-8"
        )
    return scrub_text(value, settings)


def _safe_log_argument(value: Any, settings: Any) -> Any:
    if isinstance(value, str):
        return _safe_log_text(value, settings)
    if isinstance(value, tuple):
        return tuple(_safe_log_argument(child, settings) for child in value)
    if isinstance(value, list):
        return [_safe_log_argument(child, settings) for child in value]
    if isinstance(value, Mapping):
        return _scrub_value(value, settings)
    return value


def _redact_log_record(record: logging.LogRecord, settings: Any) -> logging.LogRecord:
    try:
        if isinstance(record.msg, (Mapping, list, tuple)):
            safe_message = _safe_log_argument(record.msg, settings)
            record.msg = _safe_log_text(str(safe_message), settings)
            record.args = ()
        else:
            # Redact each argument in place *by position* and leave the tuple
            # (or mapping) the caller supplied intact in shape and length.
            # The previous version collapsed the record to its rendered
            # message with ``args = ()``: that is safe for a plain formatter,
            # but uvicorn's access formatter unpacks ``record.args`` as
            # ``(client, method, path, version, status)``, so every request
            # then raised inside the formatter and Python printed a
            # ``Logging error`` traceback instead of the access line.
            # Scrubbing the format string plus each argument separately
            # gives the same rendered redaction without breaking any
            # formatter that reads ``args`` for itself.
            if isinstance(record.args, Mapping):
                record.args = _safe_log_argument(record.args, settings)
                record.msg = scrub_text(str(record.msg), settings)
            elif isinstance(record.args, tuple) and record.args:
                record.args = tuple(_safe_log_argument(arg, settings) for arg in record.args)
                record.msg = scrub_text(str(record.msg), settings)
            elif record.args:
                # ``logging`` normally stores a tuple or a mapping; keep an
                # unusual single argument's shape rather than dropping it.
                record.args = _safe_log_argument(record.args, settings)  # type: ignore[arg-type]
                record.msg = scrub_text(str(record.msg), settings)
            else:
                record.msg = _safe_log_text(str(record.msg), settings)
                record.args = record.args
    except Exception:
        record.msg = "[log detail withheld]"
        record.args = ()
    # Avoid formatting the original exception or traceback after the message has
    # been sanitized. The event itself remains in the application response path.
    if record.exc_info:
        record.exc_info = None
        record.exc_text = None
    if record.stack_info:
        record.stack_info = scrub_text(record.stack_info, settings)
    for name, value in tuple(record.__dict__.items()):
        if name in {"msg", "args", "exc_info", "exc_text", "stack_info"}:
            continue
        normalized_name = _normal_key(name)
        if normalized_name in _DROP_KEYS or any(
            token in normalized_name
            for token in ("provider", "model", "budget", "quota", "headroom")
        ):
            record.__dict__[name] = _REDACTED
        elif isinstance(value, str):
            record.__dict__[name] = scrub_text(value, settings)
        elif isinstance(value, (Mapping, list, tuple)):
            record.__dict__[name] = _scrub_value(value, settings)
    return record


def install_log_redaction(settings: Any | None = None) -> None:
    """Install idempotent logging hooks that suppress runtime configuration details.

    Stack traces are omitted from records at this boundary: exception messages and
    source paths may contain URLs, provider names, or request arguments. Application
    code should log a safe error code instead when diagnostic detail is needed.
    """
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    _LOG_STATE["settings"] = settings
    if _LOG_STATE["factory"] is not None:
        return
    base_factory = logging.getLogRecordFactory()
    base_make_record = logging.Logger.makeRecord
    _LOG_STATE["base"] = base_factory
    _LOG_STATE["base_make_record"] = base_make_record

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base_factory(*args, **kwargs)
        current = _LOG_STATE.get("settings")
        return _redact_log_record(record, current) if current is not None else record

    def make_record(self: logging.Logger, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base_make_record(self, *args, **kwargs)
        current = _LOG_STATE.get("settings")
        return _redact_log_record(record, current) if current is not None else record

    _LOG_STATE["factory"] = factory
    _LOG_STATE["make_record"] = make_record
    logging.setLogRecordFactory(factory)
    logging.Logger.makeRecord = make_record


def reset_log_redaction_for_tests() -> None:
    """Restore the previous logging hooks; intentionally used only by isolated tests."""
    if _LOG_STATE.get("factory") is not None:
        logging.setLogRecordFactory(_LOG_STATE["base"])
        logging.Logger.makeRecord = _LOG_STATE["base_make_record"]
    _LOG_STATE.update(
        factory=None,
        base=None,
        make_record=None,
        base_make_record=None,
        settings=None,
    )
