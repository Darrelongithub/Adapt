"""api_keys, and the one function allowed to read across tenants

Revision ID: 0003_api_keys
Revises: 0002_tenant_provisioning
Create Date: 2026-11-17

Two things here need reading, not skimming.

1. ``api_keys`` has RLS **enabled but not forced**, unlike every other
   tenant-scoped table. Reason: the resolver function is SECURITY DEFINER and
   owned by ``adapt_owner``; with FORCE, the owner is subject to the policy and
   the function returns zero rows — measured, not assumed:

       ENABLE only   -> definer sees 1 row
       ENABLE+FORCE  -> definer sees 0 rows

   The protection that matters is unchanged: ``adapt_app`` is not the owner,
   so RLS applies to every request-serving connection. The tests assert both
   halves: the app role cannot cross tenants, and the app role cannot read
   another tenant's key. ``DECISIONS.md`` records the trade-off.

2. ``adapt_resolve_api_key`` is the only place in the system that reads across
   tenants. Key resolution happens before the tenant is known, so it cannot
   run under tenant context. Its grants are therefore narrow: EXECUTE to
   ``adapt_app`` and nobody else, no table reads for anybody else, search_path
   pinned in the definition so a hostile session cannot shadow the table.
"""

from __future__ import annotations

from alembic import op

revision = "0003_api_keys"
down_revision = "0002_tenant_provisioning"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE api_keys (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            prefix       text NOT NULL UNIQUE,
            hash         bytea NOT NULL,
            scopes       text[] NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz,
            revoked_at   timestamptz,
            CONSTRAINT api_keys_prefix_format CHECK (prefix ~ '^ak_(live|test)_[a-z2-7]{4}$'),
            CONSTRAINT api_keys_scopes_not_empty CHECK (cardinality(scopes) > 0)
        )
        """
    )
    op.execute("CREATE INDEX api_keys_tenant_id_idx ON api_keys (tenant_id)")

    # ENABLE, deliberately not FORCE: see the module docstring and DECISIONS.md.
    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON api_keys
          USING      ({TENANT_PREDICATE})
          WITH CHECK ({TENANT_PREDICATE})
        """
    )

    # INSERT to issue, UPDATE for revocation and the last_used_at touch.
    # SELECT so a tenant can list its own keys; RLS scopes it.
    op.execute("GRANT SELECT, INSERT, UPDATE ON api_keys TO adapt_app")
    op.execute("REVOKE DELETE, TRUNCATE ON api_keys FROM adapt_app")

    # The resolver. SECURITY DEFINER, owned by the table owner, search_path
    # pinned, no writes, exact match on the unique prefix (no LIKE, no range)
    # so it cannot be walked to enumerate keys or tenants.
    op.execute(
        """
        CREATE FUNCTION adapt_resolve_api_key(p_prefix text)
        RETURNS TABLE (
            key_id       uuid,
            tenant_id    uuid,
            key_hash     bytea,
            scopes       text[],
            revoked_at   timestamptz,
            last_used_at timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            SELECT k.id, k.tenant_id, k.hash, k.scopes, k.revoked_at, k.last_used_at
              FROM public.api_keys AS k
             WHERE k.prefix = p_prefix
        $fn$
        """
    )
    op.execute("ALTER FUNCTION adapt_resolve_api_key(text) OWNER TO adapt_owner")
    op.execute("REVOKE ALL ON FUNCTION adapt_resolve_api_key(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION adapt_resolve_api_key(text) TO adapt_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS adapt_resolve_api_key(text)")
    op.execute("DROP TABLE IF EXISTS api_keys")
