"""Allowlist request logging.

Every request log carries only: request_id, tenant_id, route, method,
status, duration_ms, error_code (plus the structlog event field). Bodies,
keys, learner identifiers, emails and provider names are never logged.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from adapt_engine.clock import Clock

_ALLOWED_FIELDS = frozenset(
    {"request_id", "tenant_id", "route", "method", "status", "duration_ms", "error_code"}
)


def configure_logging() -> None:
    """Configure structlog for JSON output.

    No timestamper processor is installed: the log allowlist has no
    timestamp field, and reading the wall clock directly is not allowed.
    """
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger() -> Any:
    """Return the shared structlog logger."""
    return structlog.get_logger("adapt_engine")


class LoggingMiddleware(BaseHTTPMiddleware):
    """Attach request_id and emit one allowlist log line per request."""

    def __init__(self, app: Any, clock: Clock) -> None:
        super().__init__(app)
        self._clock = clock

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = self._clock.now()
        status = 500
        error_code: str | None = None
        try:
            response = await call_next(request)
            status = response.status_code
            if status >= 400:
                error_code = _code_for_status(status)
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception:
            error_code = "INTERNAL_ERROR"
            raise
        finally:
            end = self._clock.now()
            duration_ms = int((end - start).total_seconds() * 1000)
            route = _route_of(request)
            tenant_id = getattr(request.state, "tenant_id", None)
            get_logger().info(
                "request",
                request_id=request_id,
                tenant_id=tenant_id,
                route=route,
                method=request.method,
                status=status,
                duration_ms=duration_ms,
                error_code=error_code,
            )


def _route_of(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return request.url.path


def _code_for_status(status_code: int) -> str:
    mapping: dict[int, str] = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }
    return mapping.get(status_code, "ERROR")
