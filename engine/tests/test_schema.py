"""Schema-level assertions: the guards exist, not just the behaviour.

Behavioural tests can pass for the wrong reason. These check the catalog
directly — role attributes, FORCE flags, both policy clauses, the tenant_id
indexes, and the audit_log grants — so the definition of done is verified
rather than assumed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from adapt_engine.db.roles import APP_ROLE, OWNER_ROLE
from adapt_engine.db.schema import RLS_NOT_FORCED
from adapt_engine.db.schema import TENANT_SCOPED_TABLES as SCHEMA_TENANT_TABLES
from adapt_engine.db.tenant import tenant_transaction
from tests.pg import Database

#: Tables carrying tenant_id (from the schema module, so this cannot drift).
TENANT_SCOPED = tuple(SCHEMA_TENANT_TABLES)
RLS_FORCED = ("tenants", "learners", "audit_log")
#: Derived from the schema module: a table added there cannot quietly skip the
#: FORCE assertion below without being named as an exception in code.
RLS_ENABLED_ONLY = tuple(RLS_NOT_FORCED)
assert set(TENANT_SCOPED) <= set(RLS_FORCED) | set(RLS_ENABLED_ONLY), (
    "every tenant-scoped table is either FORCEd or explicitly excused"
)


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
                   AND c.relname IN ('tenants', 'learners', 'audit_log', 'api_keys')
                """
                ),
                {"role": OWNER_ROLE},
            )
            .scalars()
            .all()
        )
    assert owned == [], f"{APP_ROLE} owns {owned}; RLS is bypassed for an owner"
    assert sorted(tables_owned_by_owner) == ["api_keys", "audit_log", "learners", "tenants"]


def test_app_role_cannot_create_objects(app_engine: Engine) -> None:
    with pytest.raises(DBAPIError):
        with app_engine.begin() as conn:
            conn.execute(text("CREATE TABLE should_not_exist (id int)"))


@pytest.mark.parametrize("table", RLS_FORCED)
def test_rls_is_enabled_and_forced(owner_engine: Engine, table: str) -> None:
    with owner_engine.connect() as conn:
        enabled, forced = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
    assert enabled is True, f"{table} has RLS disabled"
    assert forced is True, f"{table} lacks FORCE ROW LEVEL SECURITY"


@pytest.mark.parametrize("table", RLS_FORCED + RLS_ENABLED_ONLY)
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


@pytest.mark.parametrize("table", RLS_ENABLED_ONLY)
def test_api_keys_rls_is_enabled_but_not_forced(owner_engine: Engine, table: str) -> None:
    """The one deliberate asymmetry, asserted rather than described.

    ``adapt_resolve_api_key`` is SECURITY DEFINER and owned by ``adapt_owner``,
    i.e. by the table owner. With FORCE, the owner is subject to the policy
    too, no ``adapt.tenant_id`` is set yet (that is the whole problem — the key
    is what tells us the tenant), and the resolver returns zero rows. Measured:
    ENABLE alone → 1 row; ENABLE + FORCE → 0 rows. So api_keys is ENABLE-only,
    and every other tenant table stays FORCE.
    """
    with owner_engine.connect() as conn:
        enabled, forced = conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
    assert enabled is True, f"{table} has RLS disabled"
    assert forced is False, f"{table} is FORCEd; the SECURITY DEFINER resolver goes blind"


def test_resolver_is_security_definer_owned_by_adapt_owner(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        secdef, owner, language, volatility = conn.execute(
            text(
                """
                SELECT p.prosecdef, r.rolname, l.lanname, p.provolatile
                  FROM pg_proc p
                  JOIN pg_roles r ON r.oid = p.proowner
                  JOIN pg_language l ON l.oid = p.prolang
                 WHERE p.proname = 'adapt_resolve_api_key'
                """
            )
        ).one()
    assert secdef is True, "the resolver must run as its owner, not as the caller"
    assert owner == OWNER_ROLE
    assert language == "sql"
    assert volatility == "s"


def test_resolver_pins_search_path(owner_engine: Engine) -> None:
    """Without a pinned search_path, a caller's path can shadow the objects."""
    with owner_engine.connect() as conn:
        config = conn.execute(
            text(
                "SELECT proconfig FROM pg_proc WHERE proname = 'adapt_resolve_api_key'"
            )
        ).scalar_one()
    assert config is not None, "proconfig is null: search_path is not pinned"
    assert "search_path=pg_catalog, public" in config, config


def test_resolver_execute_is_granted_only_to_the_app_role(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        granted = (
            conn.execute(
                text(
                    """
                    SELECT r.rolname
                      FROM pg_proc p
                      CROSS JOIN LATERAL aclexplode(p.proacl) AS a
                      JOIN pg_roles r ON r.oid = a.grantee
                     WHERE p.proname = 'adapt_resolve_api_key'
                       AND a.privilege_type = 'EXECUTE'
                    """
                )
            )
            .scalars()
            .all()
        )
    # aclexplode omits PUBLIC; a PUBLIC grant would show as grantee 0 and is
    # caught by the explicit check below.
    # The owner keeps EXECUTE by virtue of owning the function; that is the
    # only other grantee, and it is the role no request ever connects as.
    assert sorted(granted) == sorted([APP_ROLE, OWNER_ROLE]), f"EXECUTE granted to {granted}"


def test_resolver_is_not_executable_by_public(app_engine: Engine) -> None:
    """`PUBLIC` must not be able to call the one cross-tenant read."""
    with app_engine.connect() as conn:
        has_public = conn.execute(
            text("SELECT has_function_privilege('public', 'adapt_resolve_api_key(text)', 'EXECUTE')")
        ).scalar_one()
    assert has_public is False


def test_resolver_takes_the_prefix_only(owner_engine: Engine) -> None:
    """The signature is the guarantee that a full key never reaches SQL."""
    with owner_engine.connect() as conn:
        args = conn.execute(
            text("SELECT pg_get_function_arguments(p.oid) FROM pg_proc p WHERE p.proname = 'adapt_resolve_api_key'")
        ).scalar_one()
    assert args == "p_prefix text", args


def test_resolver_matches_the_prefix_exactly(owner_engine: Engine) -> None:
    """Equality on the indexed column, not LIKE, not a range scan.

    A ``LIKE`` would match every key of the same environment (they share the
    first eight characters) and could return more than one row; a range scan
    would read keys it was not asked for. The source is asserted here because
    the difference is invisible from the outside until it is a leak.
    """
    with owner_engine.connect() as conn:
        source = conn.execute(
            text("SELECT prosrc FROM pg_proc WHERE proname = 'adapt_resolve_api_key'")
        ).scalar_one()
    lowered = " ".join(source.lower().split())
    assert "k.prefix = p_prefix" in lowered
    assert "like" not in lowered
    assert ">=" not in lowered


def test_app_role_cannot_see_api_keys_without_tenant_context(app_engine: Engine) -> None:
    """No tenant set: the policy yields nothing, so a route cannot leak keys."""
    with app_engine.connect() as conn:
        conn.execute(text("BEGIN"))
        count = conn.execute(text("SELECT count(*) FROM api_keys")).scalar_one()
        conn.execute(text("ROLLBACK"))
    assert count == 0


def test_app_role_cannot_delete_api_keys(app_engine: Engine) -> None:
    """Only SELECT/INSERT/UPDATE are granted; DELETE is withheld."""
    with pytest.raises(DBAPIError):
        with app_engine.begin() as conn:
            conn.execute(text("DELETE FROM api_keys"))


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
    from tests.pg import provision_tenant

    tenant_id = provision_tenant(
        database, slug=f"audit-{uuid.uuid4().hex[:10]}", name="Audit Tenant"
    )
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


def test_tenants_are_read_only_for_the_app_role(owner_engine: Engine) -> None:
    """Follow-up 4: tenant creation is provisioning, not a runtime action."""
    with owner_engine.connect() as conn:
        granted = {
            privilege: conn.execute(
                text("SELECT has_table_privilege(:r, 'tenants', :p)"),
                {"r": APP_ROLE, "p": privilege},
            ).scalar_one()
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        }
    assert granted == {
        "SELECT": True,
        "INSERT": False,
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
    }, f"tenants grants drifted: {granted}"


def test_app_role_cannot_create_a_tenant(app_engine: Engine) -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(DBAPIError) as excinfo:
        with tenant_transaction(app_engine, tenant_id) as conn:
            conn.execute(
                text(
                    "INSERT INTO tenants (id, slug, name, profile, status) "
                    "VALUES (:id, 'sneaky', 'Sneaky', 'adult_general', 'active')"
                ),
                {"id": tenant_id},
            )
    assert "permission denied" in str(excinfo.value).lower()
