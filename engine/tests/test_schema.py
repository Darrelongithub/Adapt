"""Schema-level assertions: the guards exist, not just the behaviour.

Behavioural tests can pass for the wrong reason. These check the catalog
directly — role attributes, FORCE flags, both policy clauses, the tenant_id
indexes, and the audit_log grants — so the definition of done is verified
rather than assumed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from adapt_engine.db.roles import APP_ROLE, OWNER_ROLE
from tests.pg import Database

TENANT_SCOPED = ("learners", "audit_log")


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("rolsuper", False),
        ("rolbypassrls", False),
        ("rolinherit", False),
    ],
)
def test_app_role_is_restricted(owner_engine: Engine, attribute: str, expected: bool) -> None:
    with owner_engine.connect() as conn:
        value = conn.execute(
            text(f"SELECT {attribute} FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        ).scalar_one()
    assert value is expected, f"{APP_ROLE}.{attribute} is {value}, expected {expected}"


def test_app_role_owns_nothing(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        owned = (
            conn.execute(
                text(
                    """
                SELECT c.relname
                  FROM pg_class c
                  JOIN pg_roles r ON r.oid = c.relowner
                 WHERE r.rolname = :role
                   AND c.relkind IN ('r', 'p', 'S')
                """
                ),
                {"role": APP_ROLE},
            )
            .scalars()
            .all()
        )
        tables_owned_by_owner = (
            conn.execute(
                text(
                    """
                SELECT c.relname
                  FROM pg_class c
                  JOIN pg_roles r ON r.oid = c.relowner
                 WHERE r.rolname = :role
                   AND c.relname IN ('tenants', 'learners', 'audit_log')
                """
                ),
                {"role": OWNER_ROLE},
            )
            .scalars()
            .all()
        )
    assert owned == [], f"{APP_ROLE} owns {owned}; RLS is bypassed for an owner"
    assert sorted(tables_owned_by_owner) == ["audit_log", "learners", "tenants"]


def test_app_role_cannot_create_objects(app_engine: Engine) -> None:
    with pytest.raises(DBAPIError):
        with app_engine.begin() as conn:
            conn.execute(text("CREATE TABLE should_not_exist (id int)"))


@pytest.mark.parametrize("table", ["tenants", "learners", "audit_log"])
def test_rls_is_enabled_and_forced(owner_engine: Engine, table: str) -> None:
    with owner_engine.connect() as conn:
        enabled, forced = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
    assert enabled is True, f"{table} has RLS disabled"
    assert forced is True, f"{table} lacks FORCE ROW LEVEL SECURITY"


@pytest.mark.parametrize("table", ["tenants", "learners", "audit_log"])
def test_policy_has_using_and_with_check(owner_engine: Engine, table: str) -> None:
    with owner_engine.connect() as conn:
        qual, with_check = conn.execute(
            text(
                """
                SELECT qual, with_check
                  FROM pg_policies
                 WHERE tablename = :t AND policyname = 'tenant_isolation'
                """
            ),
            {"t": table},
        ).one()
    assert qual, f"{table} policy has no USING clause"
    assert with_check, f"{table} policy has no WITH CHECK clause — writes can cross tenants"
    assert "adapt.tenant_id" in qual


@pytest.mark.parametrize("table", TENANT_SCOPED)
def test_tenant_id_has_an_index(owner_engine: Engine, table: str) -> None:
    with owner_engine.connect() as conn:
        indexes = (
            conn.execute(
                text(
                    """
                SELECT indexdef
                  FROM pg_indexes
                 WHERE tablename = :t AND schemaname = 'public'
                """
                ),
                {"t": table},
            )
            .scalars()
            .all()
        )
    assert any("(tenant_id)" in definition for definition in indexes), (
        f"{table} has no index on tenant_id; every policy check would scan"
    )


@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE", "TRUNCATE"])
def test_audit_log_is_append_only(owner_engine: Engine, privilege: str) -> None:
    with owner_engine.connect() as conn:
        granted = conn.execute(
            text("SELECT has_table_privilege(:role, 'audit_log', :priv)"),
            {"role": APP_ROLE, "priv": privilege},
        ).scalar_one()
    assert granted is False, f"{APP_ROLE} holds {privilege} on audit_log"


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT"])
def test_audit_log_allows_append_and_read(owner_engine: Engine, privilege: str) -> None:
    with owner_engine.connect() as conn:
        granted = conn.execute(
            text("SELECT has_table_privilege(:role, 'audit_log', :priv)"),
            {"role": APP_ROLE, "priv": privilege},
        ).scalar_one()
    assert granted is True


def test_audit_log_update_fails_at_runtime(app_engine: Engine, database: Database) -> None:
    """The grant, not a code convention, is what stops an update."""
    from adapt_engine.db.tenant import tenant_transaction
    from tests.pg import create_tenant

    tenant_id = create_tenant(app_engine, slug=f"audit-{database.name}", name="Audit Tenant")
    with tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(
            text(
                "INSERT INTO audit_log (tenant_id, actor, action, target, metadata) "
                "VALUES (:t, 'system', 'tenant.created', 'self', '{}'::jsonb)"
            ),
            {"t": tenant_id},
        )
    for statement in (
        "UPDATE audit_log SET actor = 'tampered'",
        "DELETE FROM audit_log",
    ):
        with pytest.raises(DBAPIError) as excinfo:
            with tenant_transaction(app_engine, tenant_id) as conn:
                conn.execute(text(statement))
        assert "permission denied" in str(excinfo.value).lower()


def test_app_role_has_plan_cache_mode_set(owner_engine: Engine) -> None:
    """Transaction-mode poolers would otherwise share one plan across tenants."""
    with owner_engine.connect() as conn:
        setting = conn.execute(
            text(
                "SELECT setting FROM pg_roles r, LATERAL ("
                "  SELECT coalesce((SELECT option FROM unnest(r.rolconfig) AS option "
                "  WHERE option LIKE 'plan_cache_mode=%'), '') AS setting"
                ") s WHERE r.rolname = :role"
            ),
            {"role": APP_ROLE},
        ).scalar_one()
    assert setting == "plan_cache_mode=force_custom_plan"
