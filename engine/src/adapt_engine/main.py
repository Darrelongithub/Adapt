"""Application factory and entrypoint.

Startup fails closed: missing or unsafe configuration exits non-zero
naming the variable. No network egress and no model calls in M0.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable

import redis
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from adapt_engine.api.health import router as health_router
from adapt_engine.clock import Clock, SystemClock
from adapt_engine.config import Settings
from adapt_engine.db import check_database
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
) -> FastAPI:
    """Build the FastAPI app with injected clock and dependency checks."""
    active_clock: Clock = clock if clock is not None else SystemClock()
    configure_logging()
    app = FastAPI(title="Adapt Engine", version=settings.version)
    app.state.settings = settings
    app.state.clock = active_clock
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
    return app


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
