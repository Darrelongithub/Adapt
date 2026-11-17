"""Single error shape for every HTTP error.

Every error response is exactly::

    {"error": {"code": ..., "message": ..., "request_id": ..., "retryable": ...}}

Handlers never echo request bodies, keys, or exception tracebacks.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ConfigError(Exception):
    """Startup configuration is missing or unsafe. Names the variable."""

    def __init__(self, variable: str, detail: str) -> None:
        self.variable = variable
        self.detail = detail
        super().__init__(f"{variable}: {detail}")


class AdaptError(Exception):
    """Base for HTTP errors that use the single envelope."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        retryable: bool,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


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


def _code_for_status(status_code: int) -> str:
    mapping: dict[int, str] = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
        502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
    }
    return mapping.get(status_code, "ERROR")


def _retryable_for_status(status_code: int) -> bool:
    return status_code in (429, 500, 502, 503, 504)


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
        code=_code_for_status(exc.status_code),
        message=message,
        request_id=_request_id(request),
        retryable=_retryable_for_status(exc.status_code),
    )
    return JSONResponse(status_code=exc.status_code, content=body)


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
        code="VALIDATION_ERROR",
        message=message,
        request_id=_request_id(request),
        retryable=False,
    )
    return JSONResponse(status_code=422, content=body)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render unexpected failures as a generic 500 without leaking detail."""
    body = error_body(
        code="INTERNAL_ERROR",
        message="Internal server error",
        request_id=_request_id(request),
        retryable=True,
    )
    return JSONResponse(status_code=500, content=body)
