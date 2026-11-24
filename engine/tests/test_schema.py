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
RLS_FORCED = (
    "tenants",
    "learners",
    "audit_log",
    "credit_grants",
    "credit_ledger",
    "credit_reservations",
    "credit_reservation_draws",
)
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
            text("SELECT proconfig FROM pg_proc WHERE proname = 'adapt_resolve_api_key'")
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
            text(
                "SELECT has_function_privilege("
                "'public', 'adapt_resolve_api_key(text,bytea)', 'EXECUTE')"
            )
        ).scalar_one()
    assert has_public is False


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


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"])
def test_app_role_holds_no_privilege_on_api_keys(owner_engine: Engine, privilege: str) -> None:
    """Key material is protected by grants, not by RLS.

    RLS keeps tenants apart; grants keep key material from everyone. The app
    role holds **nothing** on `api_keys` — it cannot read a hash, cannot write
    one, cannot delete one. Every path is a SECURITY DEFINER function.
    """
    with owner_engine.connect() as conn:
        held = conn.execute(
            text("SELECT has_table_privilege('adapt_app', 'api_keys', :privilege)"),
            {"privilege": privilege},
        ).scalar_one()
    assert held is False, f"adapt_app holds {privilege} on api_keys"


def test_no_definer_function_returns_a_hash(owner_engine: Engine) -> None:
    """No hash leaves the database, asserted against the catalogue.

    The resolver used to return ``key_hash``, which meant the runtime held
    every stored hash next to the pepper that makes it useful. Now the digest
    is an *input* and the return type names no hash at all. This test reads
    every function the owner owns and refuses any whose result mentions one.
    """
    with owner_engine.connect() as conn:
        functions = conn.execute(
            text(
                """
                SELECT p.proname, pg_get_function_result(p.oid) AS result
                  FROM pg_proc p
                  JOIN pg_roles r ON r.oid = p.proowner
                  JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE r.rolname = 'adapt_owner' AND n.nspname = 'public'
                 ORDER BY p.proname
                """
            )
        ).all()
    assert functions, "no functions found owned by adapt_owner"
    leaking = [row.proname for row in functions if "hash" in (row.result or "").lower()]
    assert leaking == [], f"these functions return a hash: {leaking}"


def test_the_resolver_takes_the_digest_as_an_argument(owner_engine: Engine) -> None:
    """The comparison happens in the database, on an argument it is given."""
    with owner_engine.connect() as conn:
        args = conn.execute(
            text(
                "SELECT pg_get_function_arguments(p.oid) FROM pg_proc p "
                "WHERE p.proname = 'adapt_resolve_api_key'"
            )
        ).scalar_one()
        result = conn.execute(
            text(
                "SELECT pg_get_function_result(p.oid) FROM pg_proc p "
                "WHERE p.proname = 'adapt_resolve_api_key'"
            )
        ).scalar_one()
    assert args == "p_prefix text, p_hash bytea", args
    assert "hash" not in result.lower()
    for column in ("key_id", "tenant_id", "scopes", "revoked_at", "expires_at"):
        assert column in result


def test_the_resolver_matches_prefix_and_hash_exactly(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        source = " ".join(
            conn.execute(text("SELECT prosrc FROM pg_proc WHERE proname = 'adapt_resolve_api_key'"))
            .scalar_one()
            .lower()
            .split()
        )
    assert "k.prefix = p_prefix" in source
    assert "k.hash = p_hash" in source
    assert "like" not in source


def test_shortening_an_expiry_is_a_function_not_a_raw_update(owner_engine: Engine) -> None:
    """`adapt_app` cannot UPDATE api_keys, so the audited path is the only path."""
    with owner_engine.connect() as conn:
        granted = conn.execute(
            text(
                "SELECT has_function_privilege('adapt_app', 'adapt_set_key_expiry(uuid,uuid,timestamptz)', 'EXECUTE')"
            )
        ).scalar_one()
        update = conn.execute(
            text("SELECT has_table_privilege('adapt_app', 'api_keys', 'UPDATE')")
        ).scalar_one()
    assert granted is True
    assert update is False


def test_app_role_keeps_execute_on_the_key_functions(owner_engine: Engine) -> None:
    """No table privilege, but the four functions are the sanctioned path."""
    expected = (
        "adapt_resolve_api_key(text,bytea)",
        "adapt_touch_api_key(uuid,integer)",
        "adapt_set_key_expiry(uuid,uuid,timestamptz)",
        "adapt_issue_api_key(uuid,text,bytea,text[],timestamptz,uuid)",
        "adapt_revoke_api_key(uuid,uuid)",
        "adapt_note_auth_failure(text,timestamptz,integer,boolean)",
    )
    with owner_engine.connect() as conn:
        for signature in expected:
            granted = conn.execute(
                text("SELECT has_function_privilege('adapt_app', :signature, 'EXECUTE')"),
                {"signature": signature},
            ).scalar_one()
            public = conn.execute(
                text("SELECT has_function_privilege('public', :signature, 'EXECUTE')"),
                {"signature": signature},
            ).scalar_one()
            assert granted is True, signature
            assert public is False, f"{signature} is executable by PUBLIC"


def test_nothing_but_the_key_functions_touches_api_keys_as_adapt_app(app_engine: Engine) -> None:
    """A direct read is a permission error, reported as such."""
    with pytest.raises(DBAPIError):
        with app_engine.connect() as conn:
            conn.execute(text("SELECT count(*) FROM api_keys"))


def test_expiry_is_not_null_and_defaults_to_ninety_days(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        nullable, default = conn.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'api_keys' AND column_name = 'expires_at'"
            )
        ).one()
    assert nullable == "NO"
    assert "90 days" in default, default


def test_audit_log_allows_tenant_less_auth_failure_rows_only(owner_engine: Engine) -> None:
    """The system policies are narrow: NULL tenant *and* action = auth_failed."""
    with owner_engine.connect() as conn:
        policies = dict(
            conn.execute(
                text(
                    "SELECT policyname, coalesce(qual, with_check) FROM pg_policies "
                    "WHERE tablename = 'audit_log'"
                )
            ).all()
        )
    system = policies.get("audit_system_insert")
    assert system is not None, sorted(policies)
    assert "tenant_id IS NULL" in system
    assert "auth_failed" in system


def test_app_role_cannot_write_a_tenant_less_audit_row(app_engine: Engine) -> None:
    """The system policy is granted to the owner role, so the app role is out."""
    with pytest.raises(DBAPIError):
        with app_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO audit_log (tenant_id, actor, action) "
                    "VALUES (NULL, 'attacker', 'auth_failed')"
                )
            )


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


def test_the_kid_catalogue_is_read_only_for_the_app_role(owner_engine: Engine) -> None:
    """Provisioning and re-wrap write it (as the owner); the app may only read.

    A role that serves requests must not be able to register a master key id
    that the startup check then trusts.
    """
    with owner_engine.connect() as conn:
        privileges = {
            verb: conn.execute(
                text("SELECT has_table_privilege('adapt_app', 'data_key_ids', :verb)"),
                {"verb": verb},
            ).scalar_one()
            for verb in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
    assert privileges == {"SELECT": True, "INSERT": False, "UPDATE": False, "DELETE": False}


def test_tenant_data_keys_is_scoped_and_forced(owner_engine: Engine) -> None:
    """Key material gets the same isolation as the rows it protects.

    Two owner policies are added in 0008 — a tenant-scoped write policy and the
    one cross-tenant read in the schema, which reaches wrapped keys and nothing
    else — so the assertion names the full set rather than a single policy.
    """
    with owner_engine.connect() as conn:
        flags = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'tenant_data_keys'"
            )
        ).one()
        policies = sorted(
            conn.execute(
                text("SELECT polname FROM pg_policy WHERE polrelid = 'tenant_data_keys'::regclass")
            ).scalars()
        )
    assert flags.relrowsecurity is True
    assert flags.relforcerowsecurity is True
    assert policies == ["data_key_owner_read", "data_key_owner_write", "tenant_isolation"]


def test_the_learner_columns_only_accept_envelopes(owner_engine: Engine) -> None:
    """The constraint that replaced the profile allowlist checks shape, not value."""
    with owner_engine.connect() as conn:
        checks = {
            row.conname: row.consrc
            for row in conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) AS consrc FROM pg_constraint "
                    "WHERE conrelid = 'learners'::regclass AND contype = 'c'"
                )
            )
        }
    assert "learners_profile_is_envelope" in checks
    assert "learners_profile_check" not in checks
    assert "learners_date_of_birth_is_envelope" in checks
    assert "^v1:" in checks["learners_profile_is_envelope"]


def test_tenants_have_a_tier_with_a_default_and_a_check(owner_engine: Engine, database) -> None:
    """Entitlement is contract state: a value, a default, and a refusal of junk."""
    from sqlalchemy.exc import IntegrityError

    with owner_engine.connect() as conn:
        column = conn.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'tenants' AND column_name = 'tier'"
            )
        ).one()
    assert column.is_nullable == "NO"
    assert "'core'" in column.column_default

    from tests.pg import create_tenant

    tenant_id = create_tenant(owner_engine, slug=f"tier-{uuid.uuid4().hex[:8]}", name="Tier")
    with tenant_transaction(owner_engine, tenant_id) as conn:
        assert conn.execute(text("SELECT tier FROM tenants")).scalar_one() == "core"
        with pytest.raises(IntegrityError) as refused:
            conn.execute(text("UPDATE tenants SET tier = 'platinum'"))
    assert "tenants_tier_known" in str(refused.value)
