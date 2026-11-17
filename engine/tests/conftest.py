"""Shared fixtures. All identifiers are obviously synthetic."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

import tests.pg as pg
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


# --------------------------------------------------------------------------
# PostgreSQL fixtures (increment 2).
#
# The suite reads ADAPT_TEST_DATABASE_URL; when it is unset the dev-only
# bundled server is started as a fallback. See tests/pg.py.
# --------------------------------------------------------------------------

SABOTAGE = os.environ.get("ADAPT_SABOTAGE", "").strip()

SABOTAGES = {
    "no_force": "FORCE ROW LEVEL SECURITY removed from every tenant-scoped table",
    # Dropping WITH CHECK entirely is NOT a hole: PostgreSQL falls back to
    # USING for writes when the clause is omitted, so this mode is kept as
    # evidence that the prompt's stated trap does not behave as described.
    "drop_with_check": "policy recreated with USING only (Postgres then uses USING for writes)",
    # The hole that actually exists: a WITH CHECK clause that does not
    # constrain the row, which is what a careless policy rewrite produces.
    "with_check_true": "policy recreated with WITH CHECK (true)",
    "session_set": "tenant context set session-wide instead of SET LOCAL",
}


@pytest.fixture(scope="session")
def database() -> Iterator[pg.Database]:
    db = pg.provision()
    yield db
    pg.drop_database(db)
    pg.stop_bundled_server()


def _apply_sabotage(db: pg.Database) -> None:
    """Break one guard on purpose, in the test database only.

    This lives in test support, never in ``src``: production code keeps one
    correct implementation. Each mode reproduces a specific real mistake, so a
    test that passes under it is a test with no teeth.
    """
    from sqlalchemy import text

    if SABOTAGE not in SABOTAGES:
        raise SystemExit(f"ADAPT_SABOTAGE={SABOTAGE!r} is not one of {sorted(SABOTAGES)}")

    owner = pg.engine_for(db, "owner")
    try:
        with owner.begin() as conn:
            if SABOTAGE == "no_force":
                for table in ("tenants", "learners", "audit_log"):
                    conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            elif SABOTAGE in ("drop_with_check", "with_check_true"):
                for table, predicate in (
                    (
                        "learners",
                        "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid",
                    ),
                    (
                        "audit_log",
                        "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid",
                    ),
                    ("tenants", "id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"),
                ):
                    conn.execute(text(f"DROP POLICY tenant_isolation ON {table}"))
                    check = "WITH CHECK (true)" if SABOTAGE == "with_check_true" else ""
                    conn.execute(
                        text(
                            f"CREATE POLICY tenant_isolation ON {table} USING ({predicate}) {check}"
                        )
                    )
            elif SABOTAGE == "session_set":
                # Monkeypatching the single place tenant context is set.
                import adapt_engine.db.tenant as tenant_module

                def session_wide_set(conn, tenant_id):
                    conn.execute(
                        text("SELECT set_config(:n, :v, false)"),
                        {"n": tenant_module.TENANT_SETTING, "v": str(tenant_id)},
                    )

                tenant_module.set_tenant_local = session_wide_set
    finally:
        owner.dispose()


@pytest.fixture(scope="session", autouse=True)
def _sabotaged(database: pg.Database) -> Iterator[None]:
    if not SABOTAGE:
        yield
        return
    print(f"\n*** SABOTAGE ACTIVE: {SABOTAGE} — {SABOTAGES[SABOTAGE]} ***\n")
    _apply_sabotage(database)
    yield


@pytest.fixture
def seeded(database: pg.Database) -> pg.SeededTenants:
    """Fresh tenants per test: no test can be satisfied by another's rows."""
    return pg.seed_two_tenants(database)


@pytest.fixture
def app_engine(database: pg.Database) -> Iterator[Engine]:
    engine = pg.engine_for(database, "app")
    yield engine
    engine.dispose()


@pytest.fixture
def owner_engine(database: pg.Database) -> Iterator[Engine]:
    engine = pg.engine_for(database, "owner")
    yield engine
    engine.dispose()


@pytest.fixture(params=["app", "owner"])
def role_engine(request: pytest.FixtureRequest, database: pg.Database) -> Iterator[Engine]:
    """Both roles, because FORCE RLS is exactly what makes the owner safe."""
    engine = pg.engine_for(database, request.param)
    yield engine
    engine.dispose()
