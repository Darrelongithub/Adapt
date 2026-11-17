"""Single error shape and the documented error-code vocabulary.

Every HTTP error response is exactly::

    {"error": {"code": ..., "message": ..., "request_id": ..., "retryable": ...}}

Codes are lowercase snake_case. The authoritative list is the table in
``docs/v2/INTEGRATION.md`` §6. A status that has no code there is a gap to
report, not a licence to invent a name: such responses carry ``unmapped_status``
and a warning is logged.

Handlers never echo request bodies, keys, or exception tracebacks.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

#: Every code documented in INTEGRATION.md §6. Two statuses have two codes
#: each (403 not_entitled / connection_paused, 404 route_unknown /
#: learner_unknown); the second of each pair is only reachable by raising
#: AdaptError, so it cannot be derived from a bare status and is listed here.
DOCUMENTED_CODES: frozenset[str] = frozenset(
    {
        "payload_invalid",
        "unauthorized",
        "not_entitled",
        "connection_paused",
        "learner_unknown",
        "route_unknown",
        "instrument_not_renderable",
        "not_capable",
        "conflict",
        "payload_too_large",
        "rate_limited",
        "quota_exhausted",
        "internal",
        "gone",
    }
)

#: Default code for a bare status. A framework route-miss is 404
#: route_unknown; a domain miss raises learner_unknown explicitly. A bare 403
#: is not_entitled; connection_paused is raised explicitly. 401 is always
#: unauthorized.
STATUS_DEFAULT_CODES: dict[int, str] = {
    400: "payload_invalid",
    401: "unauthorized",
    402: "quota_exhausted",
    403: "not_entitled",
    404: "route_unknown",
    406: "instrument_not_renderable",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    422: "payload_invalid",
    429: "rate_limited",
    500: "internal",
    501: "not_capable",
    502: "internal",
    503: "internal",
}

#: The sentinel emitted when a status has no documented code. §6 says such a
#: status is "a gap to report, not a licence to invent one"; this sentinel is
#: that report, paired with a warning. It is deliberately *not* exempted from
#: the checker: an emitted code missing from §6 fails the build.
UNMAPPED_CODE = "unmapped_status"

#: Every code this module can emit.
EMITTED_CODES: frozenset[str] = DOCUMENTED_CODES | {UNMAPPED_CODE}

#: Statuses the doc marks as retryable (429, and 500/502/503).
RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503})

#: Statuses whose semantics are "the caller may retry once the condition
#: clears" (403 connection_paused is raised explicitly, not by status alone,
#: and carries retryable=True at the raise site).
CONDITIONAL_RETRY_STATUSES: frozenset[int] = frozenset({403})


class ConfigError(Exception):
    """Startup configuration is missing or unsafe. Names the variable."""

    def __init__(self, variable: str, detail: str) -> None:
        self.variable = variable
        self.detail = detail
        super().__init__(f"{variable}: {detail}")


class AdaptError(Exception):
    """Base for HTTP errors that use the single envelope.

    ``code`` must be a documented code, so a handler cannot introduce a name
    that is absent from INTEGRATION.md §6.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        retryable: bool,
    ) -> None:
        if code not in EMITTED_CODES:
            raise ValueError(
                f"error code {code!r} is not in INTEGRATION.md §6; "
                f"use a documented code or {UNMAPPED_CODE!r}"
            )
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class ErrorDetail(BaseModel):
    """The inner error object."""

    code: str
    message: str
    request_id: str
    retryable: bool


class ErrorEnvelope(BaseModel):
    """The one error shape, documented for every route in OpenAPI."""

    error: ErrorDetail


def _logger() -> Any:
    return structlog.get_logger("adapt_engine")


def code_for_status(status_code: int, *, warn: bool = True) -> str:
    """Return the documented code for ``status_code``.

    A bare 404 is a framework route-miss and maps to ``route_unknown``; a
    domain lookup that found no learner raises ``AdaptError`` with
    ``learner_unknown`` instead, so neither defaults to the other. A bare 403
    maps to ``not_entitled``; ``connection_paused`` is only ever raised
    explicitly. 401 is always ``unauthorized`` — there is no key-level
    variant.

    Emits ``unmapped_status`` and logs a warning when the doc has no code for
    the status, rather than inventing a name. The middleware passes
    ``warn=False`` so a single response produces a single warning.
    """
    code: str | None = STATUS_DEFAULT_CODES.get(status_code)
    if code is not None:
        return code
    if warn:
        _logger().warning(
            "error_code_unmapped",
            status=status_code,
            error_code=UNMAPPED_CODE,
        )
    return UNMAPPED_CODE


def retryable_for_status(status_code: int) -> bool:
    """Return whether the doc marks this status as retryable."""
    return status_code in RETRYABLE_STATUSES


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if isinstance(rid, str) and rid:
        return rid
    return str(uuid.uuid4())


def error_body(
    *,
    code: str,
    message: str,
    request_id: str,
    retryable: bool,
) -> dict[str, object]:
    """Build the single envelope body."""
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "retryable": retryable,
        }
    }


def responses_with_envelope(statuses: list[int]) -> dict[int | str, dict[str, Any]]:
    """Build a FastAPI ``responses`` mapping that documents the envelope."""
    described: str = "Single error envelope: code, message, request_id, retryable"
    return {status: {"model": ErrorEnvelope, "description": described} for status in statuses}


#: Declared on every /v1 route so the envelope is part of the contract.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = responses_with_envelope(
    sorted({400, 401, 402, 403, 404, 406, 409, 410, 413, 422, 429, 500, 501, 502, 503})
)


async def adapt_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render an AdaptError in the single shape."""
    if not isinstance(exc, AdaptError):  # pragma: no cover - registration guard
        return await unhandled_exception_handler(request, exc)
    body = error_body(
        code=exc.code,
        message=exc.message,
        request_id=_request_id(request),
        retryable=exc.retryable,
    )
    return JSONResponse(status_code=exc.status_code, content=body)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render any Starlette/FastAPI HTTPException in the single shape."""
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - guard
        return await unhandled_exception_handler(request, exc)
    detail = exc.detail
    message = detail if isinstance(detail, str) and detail else "Request failed"
    body = error_body(
        code=code_for_status(exc.status_code),
        message=message,
        request_id=_request_id(request),
        retryable=retryable_for_status(exc.status_code),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render request validation failures without echoing submitted values."""
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - guard
        return await unhandled_exception_handler(request, exc)
    fields: list[str] = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        parts = [str(p) for p in loc if str(p) not in ("body", "query", "path")]
        if parts:
            fields.append(".".join(parts))
    if fields:
        message = "Request validation failed: " + ", ".join(sorted(set(fields))[:12])
    else:
        message = "Request validation failed"
    body = error_body(
        code=code_for_status(422),
        message=message,
        request_id=_request_id(request),
        retryable=retryable_for_status(422),
    )
    return JSONResponse(status_code=422, content=body)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render unexpected failures as a generic 500 without leaking detail."""
    body = error_body(
        code=code_for_status(500),
        message="Internal server error",
        request_id=_request_id(request),
        retryable=retryable_for_status(500),
    )
    return JSONResponse(status_code=500, content=body)
