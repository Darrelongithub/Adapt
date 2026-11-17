"""Application factory and entrypoint.

Startup fails closed: missing or unsafe configuration exits non-zero
naming the variable. No network egress and no model calls in M0.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from typing import Any

import redis
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from sqlalchemy.engine import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from adapt_engine.api.health import router as health_router
from adapt_engine.api.keys import router as keys_router
from adapt_engine.api.learners import router as learners_router
from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import Clock, SystemClock
from adapt_engine.config import Settings
from adapt_engine.db import check_database, create_db_engine
from adapt_engine.errors import (
    AdaptError,
    ConfigError,
    adapt_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from adapt_engine.logging import LoggingMiddleware, configure_logging


def check_redis(redis_url: str) -> bool:
    """Return True when Redis answers PING, False otherwise."""
    client: redis.Redis | None = None
    try:
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        return bool(client.ping())
    except Exception:
        return False
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()


def create_app(
    settings: Settings,
    clock: Clock | None = None,
    db_check: Callable[[], bool] | None = None,
    redis_check: Callable[[], bool] | None = None,
    engine: Engine | None = None,
    auth_limiter: FailureLimiter | None = None,
) -> FastAPI:
    """Build the FastAPI app with injected clock, engine and dependency checks."""
    active_clock: Clock = clock if clock is not None else SystemClock()
    configure_logging()
    app = FastAPI(title="Adapt Engine", version=settings.version)
    app.state.settings = settings
    app.state.clock = active_clock
    app.state.engine = engine if engine is not None else create_db_engine(settings.database_url)
    app.state.auth_limiter = (
        auth_limiter if auth_limiter is not None else FailureLimiter(clock=active_clock)
    )
    app.state.db_check = db_check if db_check is not None else _default_db_check(settings)
    app.state.redis_check = (
        redis_check if redis_check is not None else _default_redis_check(settings)
    )
    app.add_middleware(LoggingMiddleware, clock=active_clock)
    app.add_exception_handler(AdaptError, adapt_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(health_router)
    app.include_router(keys_router)
    app.include_router(learners_router)
    _install_openapi(app)
    return app


def _install_openapi(app: FastAPI) -> None:
    """Register the bearer scheme so the contract names its auth.

    ``check_openapi.py`` fails any /v1 route without a security requirement,
    so the scheme has to be in the document, not just in the code.
    """

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
            description=app.description,
        )
        components = schema.setdefault("components", {})
        components.setdefault("securitySchemes", {})
        for path, item in schema.get("paths", {}).items():
            if not path.startswith("/v1") or path in ("/v1/health", "/v1/version"):
                continue
            for operation in item.values():
                if not isinstance(operation, dict):
                    continue
                # Scopes are attached by require_scope; fall back to the
                # scheme so a route can never document "no auth" by accident.
                operation.setdefault("security", [{"APIKeyAuth": []}])
                # FastAPI emits one requirement per Security() dependency, so
                # the same scheme can appear with and without scopes. A list of
                # requirement objects means OR, so an entry with no scopes must
                # not survive next to one that names scopes — it would document
                # "authenticate with no capability required" as an alternative.
                requirements: list[dict[str, list[str]]] = operation["security"]
                schemes_with_scopes = {
                    scheme
                    for requirement in requirements
                    for scheme, scopes in requirement.items()
                    if scopes
                }
                deduped: list[dict[str, list[str]]] = []
                for requirement in requirements:
                    if any(
                        scheme in schemes_with_scopes and not scopes
                        for scheme, scopes in requirement.items()
                    ):
                        continue
                    if requirement not in deduped:
                        deduped.append(requirement)
                operation["security"] = deduped
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def create_app_from_env() -> FastAPI:
    """Build the app from environment configuration.

    Raises ConfigError naming the variable when configuration is missing
    or unsafe. Used by the uvicorn factory and by ``main``.
    """
    return create_app(Settings.from_env())


def _default_db_check(settings: Settings) -> Callable[[], bool]:
    def _check() -> bool:
        return check_database(settings.database_url)

    return _check


def _default_redis_check(settings: Settings) -> Callable[[], bool]:
    def _check() -> bool:
        return check_redis(settings.redis_url)

    return _check


def main() -> None:
    """Entrypoint: check config, then serve. Exits non-zero on bad config."""
    try:
        app = create_app_from_env()
    except ConfigError as exc:
        print(f"error: {exc.variable}: {exc.detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    import uvicorn

    # access_log=False keeps only the allowlist request log line; uvicorn's
    # own access log would emit client addresses and query strings.
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)


if __name__ == "__main__":
    main()
