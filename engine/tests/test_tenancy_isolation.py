"""Adversarial tenant-isolation tests.

Each of these must fail when the corresponding guard is removed; the
``ADAPT_SABOTAGE`` modes in conftest exist to prove that (see the increment 2
report for the red-then-green output). A test that cannot fail is not
evidence, and RLS fails silently: the wrong policy and the wrong test look
identical when everything is green.

Tests 1-5 run against both roles. The owner variant is what gives FORCE its
teeth: without FORCE the owner bypasses its own policy, and only the owner
variant notices.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import QueuePool

from adapt_engine.db.tenant import TENANT_SETTING, tenant_transaction
from tests.pg import SeededTenants, create_learner, synthetic_suffix


def _learner_ids(conn) -> list[str]:
    return [row[0] for row in conn.execute(text("SELECT learner_id FROM learners"))]


def test_a_cannot_read_b_row_by_primary_key(role_engine: Engine, seeded: SeededTenants) -> None:
    """1. Tenant A cannot reach B's row, even knowing its primary key."""
    with tenant_transaction(role_engine, seeded.a_id) as conn:
        rows = conn.execute(
            text("SELECT id, learner_id FROM learners WHERE id = :id"),
            {"id": seeded.b_learner},
        ).all()
        assert rows == [], "tenant A read tenant B's row by primary key"

        visible = conn.execute(text("SELECT id FROM learners")).scalars().all()
        assert seeded.b_learner not in visible
        assert seeded.a_learner in visible

        # A domain-style lookup by the id it *should* not find is also empty.
        missing = conn.execute(
            text("SELECT id FROM learners WHERE learner_id = :lid"),
            {"lid": seeded.b_learner_id},
        ).all()
        assert missing == []


def test_no_tenant_variable_sees_zero_rows(role_engine: Engine, seeded: SeededTenants) -> None:
    """2. With no tenant variable set, a table yields zero rows — not all."""
    with role_engine.connect() as conn:
        actual = conn.execute(
            text("SELECT current_setting('adapt.tenant_id', true)")
        ).scalar_one_or_none()
        assert actual in (None, ""), f"tenant context leaked into a fresh connection: {actual!r}"

        assert conn.execute(text("SELECT count(*) FROM learners")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 0


def test_insert_with_foreign_tenant_is_rejected(role_engine: Engine, seeded: SeededTenants) -> None:
    """3. WITH CHECK refuses a row written into another tenant."""
    with pytest.raises(DBAPIError) as excinfo:
        with tenant_transaction(role_engine, seeded.a_id) as conn:
            conn.execute(
                text(
                    "INSERT INTO learners (id, tenant_id, learner_id, profile) "
                    "VALUES (:id, :tenant_id, :learner_id, 'adult_general')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": seeded.b_id,
                    "learner_id": f"smuggled-{synthetic_suffix()}",
                },
            )
    assert "row-level security" in str(excinfo.value).lower()

    # And nothing was written into B.
    with tenant_transaction(role_engine, seeded.b_id) as conn:
        count = conn.execute(text("SELECT count(*) FROM learners")).scalar_one()
        assert count == 1, "the rejected insert still landed in tenant B"


def test_update_across_tenants_affects_zero_rows(
    role_engine: Engine, seeded: SeededTenants
) -> None:
    """4. UPDATE ... WHERE tenant_id = B is a no-op, not an error."""
    with tenant_transaction(role_engine, seeded.a_id) as conn:
        result = conn.execute(
            text("UPDATE learners SET deleted_at = now() WHERE tenant_id = :other"),
            {"other": seeded.b_id},
        )
        assert result.rowcount == 0

    with tenant_transaction(role_engine, seeded.b_id) as conn:
        still_there = conn.execute(
            text("SELECT deleted_at FROM learners WHERE id = :id"),
            {"id": seeded.b_learner},
        ).scalar_one()
        assert still_there is None, "tenant A updated tenant B's row"


def test_hostile_filter_values_stay_inside_the_tenant(
    role_engine: Engine, seeded: SeededTenants
) -> None:
    """5. SQL-ish filter values and B's own UUID do not cross the boundary."""
    hostile = ["' OR '1'='1", "%", "' OR tenant_id IS NOT NULL --", "'; DROP TABLE learners; --"]

    with tenant_transaction(role_engine, seeded.a_id) as conn:
        for value in hostile:
            rows = conn.execute(
                text("SELECT id FROM learners WHERE learner_id = :v"), {"v": value}
            ).all()
            assert rows == [], f"hostile value {value!r} returned rows"

            # A LIKE pattern is allowed to match A's own rows — what it must
            # never do is reach past them.
            rows = conn.execute(
                text("SELECT id FROM learners WHERE learner_id ILIKE :v"), {"v": f"%{value}%"}
            ).all()
            assert all(row[0] == seeded.a_learner for row in rows)

        # B's own primary key used as a filter value, not as a row id.
        rows = conn.execute(
            text("SELECT id FROM learners WHERE tenant_id = :v OR id = :v"),
            {"v": seeded.b_id},
        ).all()
        assert rows == []

        # Interpolated *only* to prove RLS holds even then; the table is intact
        # and every row still belongs to A or is invisible.
        rows = conn.execute(text("SELECT DISTINCT tenant_id FROM learners")).all()
        assert [r[0] for r in rows] == [seeded.a_id]

    # The hostile DDL string did not execute.
    with tenant_transaction(role_engine, seeded.b_id) as conn:
        assert _learner_ids(conn) == [seeded.b_learner_id]


def test_recycled_connection_has_no_residual_tenant_context(
    database, seeded: SeededTenants
) -> None:
    """6. After commit and pool return, the next checkout is context-free.

    The pool is pinned to a single connection on purpose: with a wider pool
    the next checkout might be a different connection, and only a session-wide
    SET would leak. One connection is the adversarial case.
    """
    engine = database.engine(
        database.app_url,
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    try:
        with tenant_transaction(engine, seeded.a_id) as conn:
            backend_pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
            assert conn.execute(text("SELECT count(*) FROM learners")).scalar_one() == 1

        # Same pooled connection, fresh transaction, no SET LOCAL this time.
        with engine.connect() as conn:
            same_backend = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
            assert same_backend == backend_pid, "the pool handed out a different connection"
            leftover = conn.execute(
                text("SELECT current_setting('adapt.tenant_id', true)")
            ).scalar_one_or_none()
            assert leftover in (None, ""), f"residual tenant context: {leftover!r}"
            assert conn.execute(text("SELECT count(*) FROM learners")).scalar_one() == 0

        # And the settings are not merely hidden from this query.
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM learners WHERE tenant_id = :t"),
                    {"t": seeded.a_id},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_tenant_context_helper_refuses_outside_a_transaction(app_engine: Engine) -> None:
    """SET LOCAL outside a transaction is a silent no-op; we refuse instead."""
    from adapt_engine.db.tenant import TenantContextError, set_tenant_local

    with app_engine.connect() as conn:
        with pytest.raises(TenantContextError):
            set_tenant_local(conn, uuid.uuid4())


def test_setting_name_is_the_documented_one() -> None:
    assert TENANT_SETTING == "adapt.tenant_id"


def test_learners_can_only_hold_their_own_tenant(database, seeded: SeededTenants) -> None:
    """A member of A inserted while context is A is invisible to B."""
    engine = database.engine(database.app_url)
    try:
        learner_id = f"learner-extra-{synthetic_suffix()}"
        create_learner(engine, seeded.a_id, learner_id)
        with tenant_transaction(engine, seeded.a_id) as conn:
            assert learner_id in _learner_ids(conn)
        with tenant_transaction(engine, seeded.b_id) as conn:
            assert learner_id not in _learner_ids(conn)
    finally:
        engine.dispose()
