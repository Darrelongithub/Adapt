"""Key material is protected by grants: no app-role privilege on api_keys

Revision ID: 0004_key_hardening
Revises: 0003_api_keys
Create Date: 2026-11-17

Increment 3 gave ``adapt_app`` ``SELECT, INSERT, UPDATE`` on ``api_keys`` and
relied on RLS as the boundary. That was the wrong boundary for this table: RLS
keeps *tenants* apart, and a key table needs to keep *key material* from
everyone. So:

1. ``REVOKE ALL ON api_keys FROM adapt_app``. The runtime role holds no
   privilege on the table at all — not SELECT, not INSERT, not UPDATE. Every
   access goes through one of four narrow ``SECURITY DEFINER`` functions owned
   by ``adapt_owner``, each granted to ``adapt_app`` and to nobody else:

       adapt_resolve_api_key(text)                       read one row by prefix
       adapt_touch_api_key(uuid)                         debounced last_used_at
       adapt_issue_api_key(...)                          insert, with the mint rules
       adapt_revoke_api_key(uuid, uuid)                  set revoked_at, tenant-addressed

   ``ENABLE ROW LEVEL SECURITY`` and the tenant policy stay, without ``FORCE``,
   as defence in depth for a future grant: if someone re-grants SELECT, the
   policy is still there. It is belt-and-braces, not the boundary — and the
   schema test asserts the grants, not the policy.

2. ``expires_at`` on every key. NOT NULL, defaulting to 90 days from creation.
   An expired key fails as ``unauthorized``, identically to every other
   failure, so expiry is not an oracle either.

3. The mint rules live in the database, not only in the handler: an issuing key
   must be an unexpired, unrevoked ``admin`` key of the same tenant, may only
   request scopes it already holds, and may never mint ``admin``. Provisioning
   (``session_user = adapt_owner``) is the only way to create the first admin
   key, which is why escalation over the API is one rule away from impossible
   rather than one call away.

4. Failed authentication is audited, and it has no tenant — the key was not
   recognised. ``audit_log`` is FORCEd, so a ``NULL``-tenant row is invisible
   and unwritable by every role, owner included, under the tenant policy. Two
   role-targeted permissive policies fix exactly that and nothing else:

       audit_system_insert  TO adapt_owner
                            INSERT  WITH CHECK (tenant_id IS NULL AND action = 'auth_failed')
       audit_system_read    TO adapt_owner
                            SELECT  USING      (tenant_id IS NULL AND action = 'auth_failed')

   ``adapt_app`` cannot insert a tenant-less row even directly, because the
   policy is granted to the owner role, not to PUBLIC. ``audit_log.tenant_id``
   loses its NOT NULL: a failed authentication has no tenant to name. The IP is
   stored as an HMAC, never as an address: ``metadata`` has no PII.
"""

from __future__ import annotations

from alembic import op

revision = "0004_key_hardening"
down_revision = "0003_api_keys"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"

#: ``admin`` keys are required to expire; the column-level default covers the
#: rest, so this is one rule rather than two.
DEFAULT_EXPIRY = "interval '90 days'"


def upgrade() -> None:
    _revoke_table_privileges()
    _add_expiry()
    _replace_resolver()
    _create_touch_function()
    _create_issue_function()
    _create_revoke_function()
    _add_audit_policies()
    _create_auth_failure_function()


def _revoke_table_privileges() -> None:
    op.execute("REVOKE ALL ON api_keys FROM adapt_app")


def _add_expiry() -> None:
    # One statement: existing rows take the default (Postgres 11+ fills without
    # a rewrite), so there is no backfill — and a migration carries no data
    # statements, which the migration test enforces.
    op.execute(
        "ALTER TABLE api_keys ADD COLUMN expires_at timestamptz NOT NULL "
        f"DEFAULT now() + {DEFAULT_EXPIRY}"
    )
    op.execute("ALTER TABLE api_keys ALTER COLUMN expires_at SET NOT NULL")
    # No "expires_at > created_at" CHECK: shortening a key's life is a
    # legitimate administrative act, and a CHECK would make *testing* expiry
    # impossible without a clock at the SQL boundary. NOT NULL is the rule.


def _replace_resolver() -> None:
    """The return type changes, so the function is replaced, not altered."""
    op.execute("DROP FUNCTION IF EXISTS adapt_resolve_api_key(text)")
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


def _create_touch_function() -> None:
    op.execute(
        """
        CREATE FUNCTION adapt_touch_api_key(p_key_id uuid, p_interval_seconds integer DEFAULT 60)
        RETURNS boolean
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            WITH touched AS (
                UPDATE public.api_keys
                   SET last_used_at = now()
                 WHERE id = p_key_id
                   AND (
                        last_used_at IS NULL
                        OR last_used_at < now() - make_interval(secs => p_interval_seconds)
                   )
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM touched)
        $fn$
        """
    )
    op.execute("ALTER FUNCTION adapt_touch_api_key(uuid, integer) OWNER TO adapt_owner")
    op.execute("REVOKE ALL ON FUNCTION adapt_touch_api_key(uuid, integer) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION adapt_touch_api_key(uuid, integer) TO adapt_app")


def _create_issue_function() -> None:
    op.execute(
        """
        CREATE FUNCTION adapt_issue_api_key(
            p_tenant_id      uuid,
            p_prefix         text,
            p_hash           bytea,
            p_scopes         text[],
            p_expires_at     timestamptz,
            p_issuer_key_id  uuid DEFAULT NULL
        )
        RETURNS uuid
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
        DECLARE
            v_issuer_scopes text[];
            v_key_id        uuid;
        BEGIN
            IF p_scopes IS NULL OR cardinality(p_scopes) = 0 THEN
                RAISE EXCEPTION 'scopes_required' USING ERRCODE = '22023';
            END IF;
            IF p_expires_at IS NULL OR p_expires_at <= now() THEN
                RAISE EXCEPTION 'expires_at_required' USING ERRCODE = '22023';
            END IF;

            IF p_issuer_key_id IS NULL THEN
                -- Provisioning path. session_user is the connecting role, not
                -- the definer, so a runtime connection cannot reach this.
                IF session_user <> 'adapt_owner' THEN
                    RAISE EXCEPTION 'issuer_required' USING ERRCODE = '42501';
                END IF;
            ELSE
                SELECT k.scopes
                  INTO v_issuer_scopes
                  FROM public.api_keys AS k
                 WHERE k.id = p_issuer_key_id
                   AND k.tenant_id = p_tenant_id
                   AND k.revoked_at IS NULL
                   AND k.expires_at > now();

                IF NOT FOUND THEN
                    RAISE EXCEPTION 'issuer_unknown' USING ERRCODE = '42501';
                END IF;
                IF NOT ('admin' = ANY (v_issuer_scopes)) THEN
                    RAISE EXCEPTION 'issuer_not_admin' USING ERRCODE = '42501';
                END IF;
                IF 'admin' = ANY (p_scopes) THEN
                    RAISE EXCEPTION 'admin_not_mintable' USING ERRCODE = '42501';
                END IF;
                IF NOT (p_scopes <@ v_issuer_scopes) THEN
                    RAISE EXCEPTION 'scope_escalation' USING ERRCODE = '42501';
                END IF;
            END IF;

            INSERT INTO public.api_keys (tenant_id, prefix, hash, scopes, expires_at)
            VALUES (p_tenant_id, p_prefix, p_hash, p_scopes, p_expires_at)
            RETURNING id INTO v_key_id;

            RETURN v_key_id;
        END
        $fn$
        """
    )
    op.execute(
        "ALTER FUNCTION adapt_issue_api_key(uuid, text, bytea, text[], timestamptz, uuid) "
        "OWNER TO adapt_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION adapt_issue_api_key(uuid, text, bytea, text[], timestamptz, uuid) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "adapt_issue_api_key(uuid, text, bytea, text[], timestamptz, uuid) TO adapt_app"
    )


def _create_revoke_function() -> None:
    op.execute(
        """
        CREATE FUNCTION adapt_revoke_api_key(p_tenant_id uuid, p_key_id uuid)
        RETURNS boolean
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            WITH revoked AS (
                UPDATE public.api_keys
                   SET revoked_at = now()
                 WHERE id = p_key_id
                   AND tenant_id = p_tenant_id
                   AND revoked_at IS NULL
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM revoked)
        $fn$
        """
    )
    op.execute("ALTER FUNCTION adapt_revoke_api_key(uuid, uuid) OWNER TO adapt_owner")
    op.execute("REVOKE ALL ON FUNCTION adapt_revoke_api_key(uuid, uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION adapt_revoke_api_key(uuid, uuid) TO adapt_app")


def _add_audit_policies() -> None:
    # A system row has no tenant to name — the key was not recognised.
    op.execute("ALTER TABLE audit_log ALTER COLUMN tenant_id DROP NOT NULL")
    op.execute(
        """
        CREATE POLICY audit_system_insert ON audit_log
            AS PERMISSIVE
            FOR INSERT
            TO adapt_owner
            WITH CHECK (tenant_id IS NULL AND action = 'auth_failed')
        """
    )
    op.execute(
        """
        CREATE POLICY audit_system_read ON audit_log
            AS PERMISSIVE
            FOR SELECT
            TO adapt_owner
            USING (tenant_id IS NULL AND action = 'auth_failed')
        """
    )


def _create_auth_failure_function() -> None:
    op.execute(
        """
        CREATE FUNCTION adapt_note_auth_failure(
            p_ip_hash       text,
            p_window_start  timestamptz,
            p_count         integer,
            p_aggregated    boolean
        )
        RETURNS void
        LANGUAGE sql
        VOLATILE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            INSERT INTO public.audit_log (tenant_id, actor, action, target, metadata)
            VALUES (
                NULL,
                'system',
                'auth_failed',
                NULL,
                jsonb_build_object(
                    'ip_hash', p_ip_hash,
                    'window_start', p_window_start,
                    'count', p_count,
                    'aggregated', p_aggregated
                )
            )
        $fn$
        """
    )
    op.execute(
        "ALTER FUNCTION adapt_note_auth_failure(text, timestamptz, integer, boolean) "
        "OWNER TO adapt_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION adapt_note_auth_failure(text, timestamptz, integer, boolean)"
        " FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION adapt_note_auth_failure(text, timestamptz, integer, boolean)"
        " TO adapt_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS adapt_note_auth_failure(text, timestamptz, integer, boolean)"
    )
    op.execute("DROP POLICY IF EXISTS audit_system_read ON audit_log")
    op.execute("DROP POLICY IF EXISTS audit_system_insert ON audit_log")
    op.execute("ALTER TABLE audit_log ALTER COLUMN tenant_id SET NOT NULL")
    op.execute("DROP FUNCTION IF EXISTS adapt_revoke_api_key(uuid, uuid)")
    op.execute(
        "DROP FUNCTION IF EXISTS adapt_issue_api_key(uuid, text, bytea, text[], timestamptz, uuid)"
    )
    op.execute("DROP FUNCTION IF EXISTS adapt_touch_api_key(uuid, integer)")
    op.execute("DROP FUNCTION IF EXISTS adapt_resolve_api_key(text)")
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_expiry_after_creation")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS expires_at")
    op.execute("GRANT SELECT, INSERT, UPDATE ON api_keys TO adapt_app")

    # Resolver back to its increment-3 shape (no expires_at).
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
