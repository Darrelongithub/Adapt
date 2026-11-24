"""Increment-4 corrections: the four-term identity, no hash out, audited expiry

Revision ID: 0006_credits_identity
Revises: 0005_credits
Create Date: 2026-11-17

Four changes, each one closing a hole that review found rather than a bug that
testing found.

1. ``credit_ledger.reason`` becomes ``entry_type`` (``grant``, ``reserve``,
   ``release``, ``settle``, ``expire``). ``METERING.md`` §4's table still calls
   the column ``reason``; the review calls it ``entry_type`` and requires that
   billing reads ``entry_type = 'settle'`` only. The later, more specific
   instruction wins, and the view below is where that rule lives.

2. ``credit_consumption`` is the only sanctioned consumption figure:
   ``sum(-delta) WHERE entry_type = 'settle'``. ``sum(delta)`` over the whole
   ledger is *not* consumption — reserve and release rows are zero now, but a
   future entry type would silently change the answer. The view is
   ``security_invoker`` so the caller's RLS applies to the underlying table.

3. ``CHECK (kind <> 'purchased' OR expires_at IS NULL)``: paid credits never
   expire, and a column that permits the opposite eventually gets used to do
   it. Its mirror, ``included`` must expire, already exists.

4. ``adapt_resolve_api_key(text, bytea)`` replaces the prefix-only resolver.
   The old one returned ``key_hash``, which meant the runtime held every hash
   *and* the pepper — and a pepper only helps if the hash is not sitting next
   to it in memory. Now the client sends the digest it computed and the
   function returns a row only on an exact `(prefix, hash)` match. No hash
   leaves the database.

5. ``adapt_set_key_expiry`` makes shortening a key's life an audited function
   instead of a raw ``UPDATE`` (``adapt_app`` has no UPDATE on ``api_keys`` at
   all, so a raw update is not possible from a request connection — this makes
   the owner-only path auditable too, and keeps the intent of the removed
   ``expires_at > created_at`` CHECK: an expiry is validated at the moment it
   is set, not frozen at creation).
"""

from __future__ import annotations

from alembic import op

revision = "0006_credits_identity"
down_revision = "0005_credits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _entry_type()
    _purchased_never_expires()
    _consumption_view()
    _resolver_without_hash()
    _audited_expiry()
    _declared_tenant_owner_read()


def _entry_type() -> None:
    op.execute("ALTER TABLE credit_ledger RENAME COLUMN reason TO entry_type")
    op.execute(
        "ALTER TABLE credit_ledger RENAME CONSTRAINT credit_ledger_reason_known"
        " TO credit_ledger_entry_type_known"
    )
    op.execute(
        "ALTER TABLE credit_ledger "
        "DROP CONSTRAINT IF EXISTS credit_ledger_consumption_never_positive"
    )
    op.execute(
        "ALTER TABLE credit_ledger ADD CONSTRAINT credit_ledger_settle_is_never_positive"
        " CHECK (entry_type <> 'settle' OR delta <= 0)"
    )


def _purchased_never_expires() -> None:
    op.execute(
        "ALTER TABLE credit_grants ADD CONSTRAINT credit_grants_purchased_never_expires"
        " CHECK (kind <> 'purchased' OR expires_at IS NULL)"
    )


def _consumption_view() -> None:
    op.execute(
        """
        CREATE VIEW credit_consumption
        WITH (security_invoker = true)
        AS
        SELECT tenant_id,
               date_trunc('month', created_at)::date AS period_start,
               coalesce(sum(-delta), 0)::bigint     AS consumed
          FROM credit_ledger
         WHERE entry_type = 'settle'
         GROUP BY tenant_id, date_trunc('month', created_at)::date
        """
    )
    op.execute("GRANT SELECT ON credit_consumption TO adapt_app")


def _declared_tenant_owner_read() -> None:
    """Let the owner read one tenant's audit rows — but only a declared one.

    `audit_system_read` (0004) gives the owner the tenant-less rows and nothing
    else, so `scripts/audit_query.py` could report failures but not key
    issuance. This policy is the narrowest thing that fixes it: the owner sees
    a tenant's audit rows only when it has named that tenant in
    `adapt.tenant_id`, and the setting is NULL otherwise, which matches no row.
    """
    op.execute(
        """
        CREATE POLICY audit_declared_tenant_read ON audit_log
            AS PERMISSIVE
            FOR SELECT
            TO adapt_owner
            USING (tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid)
        """
    )


def _resolver_without_hash() -> None:
    op.execute("DROP FUNCTION IF EXISTS adapt_resolve_api_key(text)")
    op.execute(
        """
        CREATE FUNCTION adapt_resolve_api_key(p_prefix text, p_hash bytea)
        RETURNS TABLE (
            key_id     uuid,
            tenant_id  uuid,
            scopes     text[],
            revoked_at timestamptz,
            expires_at timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            SELECT k.id, k.tenant_id, k.scopes, k.revoked_at, k.expires_at
              FROM public.api_keys AS k
             WHERE k.prefix = p_prefix
               AND k.hash = p_hash
        $fn$
        """
    )
    op.execute("ALTER FUNCTION adapt_resolve_api_key(text, bytea) OWNER TO adapt_owner")
    op.execute("REVOKE ALL ON FUNCTION adapt_resolve_api_key(text, bytea) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION adapt_resolve_api_key(text, bytea) TO adapt_app")


def _audited_expiry() -> None:
    op.execute(
        """
        CREATE FUNCTION adapt_set_key_expiry(
            p_tenant_id  uuid,
            p_key_id     uuid,
            p_expires_at timestamptz
        )
        RETURNS boolean
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
        BEGIN
            IF p_expires_at IS NULL OR p_expires_at <= now() THEN
                RAISE EXCEPTION 'expiry_not_in_the_past' USING ERRCODE = '22023';
            END IF;

            UPDATE public.api_keys
               SET expires_at = p_expires_at
             WHERE id = p_key_id
               AND tenant_id = p_tenant_id
               AND revoked_at IS NULL;

            RETURN FOUND;
        END
        $fn$
        """
    )
    op.execute("ALTER FUNCTION adapt_set_key_expiry(uuid, uuid, timestamptz) OWNER TO adapt_owner")
    op.execute(
        "REVOKE ALL ON FUNCTION adapt_set_key_expiry(uuid, uuid, timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION adapt_set_key_expiry(uuid, uuid, timestamptz) TO adapt_app"
    )


def _drop_declared_tenant_owner_read() -> None:
    op.execute("DROP POLICY IF EXISTS audit_declared_tenant_read ON audit_log")


def downgrade() -> None:
    _drop_declared_tenant_owner_read()
    op.execute("DROP FUNCTION IF EXISTS adapt_set_key_expiry(uuid, uuid, timestamptz)")
    op.execute("DROP FUNCTION IF EXISTS adapt_resolve_api_key(text, bytea)")
    op.execute("DROP VIEW IF EXISTS credit_consumption")
    op.execute(
        "ALTER TABLE credit_grants DROP CONSTRAINT IF EXISTS credit_grants_purchased_never_expires"
    )
    op.execute(
        "ALTER TABLE credit_ledger DROP CONSTRAINT IF EXISTS credit_ledger_settle_is_never_positive"
    )
    op.execute(
        "ALTER TABLE credit_ledger ADD CONSTRAINT credit_ledger_consumption_never_positive"
        " CHECK (entry_type <> 'settle' OR delta <= 0)"
    )
    op.execute(
        "ALTER TABLE credit_ledger RENAME CONSTRAINT credit_ledger_entry_type_known"
        " TO credit_ledger_reason_known"
    )
    op.execute("ALTER TABLE credit_ledger RENAME COLUMN entry_type TO reason")

    # Resolver back to the increment-4 shape (prefix only, returns the hash).
    op.execute(
        """
        CREATE FUNCTION adapt_resolve_api_key(p_prefix text)
        RETURNS TABLE (
            key_id     uuid,
            tenant_id  uuid,
            key_hash   bytea,
            scopes     text[],
            revoked_at timestamptz,
            expires_at timestamptz
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            SELECT k.id, k.tenant_id, k.hash, k.scopes, k.revoked_at, k.expires_at
              FROM public.api_keys AS k
             WHERE k.prefix = p_prefix
        $fn$
        """
    )
    op.execute("ALTER FUNCTION adapt_resolve_api_key(text) OWNER TO adapt_owner")
    op.execute("REVOKE ALL ON FUNCTION adapt_resolve_api_key(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION adapt_resolve_api_key(text) TO adapt_app")
