"""Shared fixtures. All identifiers are obviously synthetic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adapt_engine.clock import FixedClock
from adapt_engine.config import Settings
from adapt_engine.main import create_app

TEST_MASTER_KEY = "test-master-key-32-bytes-long-xxxx"
TEST_PEPPER = "test-pepper-synthetic-only"


def make_settings(**overrides: object) -> Settings:
    """Build test settings with synthetic values."""
    base: dict[str, object] = {
        "env": "test",
        "database_url": "postgresql://test:test@localhost:5432/adapt_test",
        "master_key": TEST_MASTER_KEY,
        "key_pepper": TEST_PEPPER,
        "redis_url": "redis://localhost:6379/0",
        "commit": "test-commit-synthetic",
        "build_time": "2026-11-17T00:00:00Z",
        "version": "0.1.0",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def test_settings() -> Settings:
    return make_settings()


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 17, 0, 0, 0, tzinfo=UTC))


@pytest.fixture
def app(test_settings: Settings, fixed_clock: FixedClock) -> FastAPI:
    return create_app(
        test_settings,
        clock=fixed_clock,
        db_check=lambda: True,
        redis_check=lambda: True,
    )


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def make_client(
    *,
    db_ok: bool = True,
    redis_ok: bool = True,
    settings: Settings | None = None,
    clock: FixedClock | None = None,
) -> TestClient:
    """Build a client with controllable dependency checks."""
    active_settings = settings if settings is not None else make_settings()
    active_clock = (
        clock if clock is not None else FixedClock(datetime(2026, 11, 17, 0, 0, 0, tzinfo=UTC))
    )

    def db_check() -> bool:
        return db_ok

    def redis_check() -> bool:
        return redis_ok

    test_app = create_app(
        active_settings,
        clock=active_clock,
        db_check=db_check,
        redis_check=redis_check,
    )
    return TestClient(test_app, raise_server_exceptions=False)
