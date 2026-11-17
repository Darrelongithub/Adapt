"""Shared FastAPI dependencies.

Health checks are injected as callables so tests can substitute fakes
without a live Postgres or Redis.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

from adapt_engine.clock import Clock
from adapt_engine.config import Settings


def get_settings(request: Request) -> Settings:
    """Return the app settings."""
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    """Return the app clock."""
    clock: Clock = request.app.state.clock
    return clock


def get_db_check(request: Request) -> Callable[[], bool]:
    """Return the database reachability callable."""
    check: Callable[[], bool] = request.app.state.db_check
    return check


def get_redis_check(request: Request) -> Callable[[], bool]:
    """Return the Redis reachability callable."""
    check: Callable[[], bool] = request.app.state.redis_check
    return check
