"""tenancy, learners and audit_log with forced row-level security

Revision ID: 0001_tenancy
Revises:
Create Date: 2026-11-17

Hand-written, reviewed line by line. Autogenerate cannot express FORCE, the
two policy clauses, or the grants, and those are the parts that decide whether
a tenant can see another tenant's rows.

No data in this migration: tenants and learners are created by the runtime,
and a migration that seeds rows would have to invent them.

Prerequisites, created by bootstrap (``scripts/bootstrap_db.py``) and not
here: the ``adapt_owner`` and ``adapt_app`` roles. A migration cannot create
the role that owns its own tables.
"""

from __future__ import annotations

from alembic import op

revision = "0001_tenancy"
down_revision = None
branch_labels = None
depends_on = None

#: The predicate both policy clauses use. ``nullif(..., '')`` matters: an
#: unset GUC reads as '' here, and '' would otherwise be cast to uuid and
#: raise instead of returning zero rows.
TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"

#: ``tenants`` has no tenant_id column: a tenant is its own scope.
TENANTS_PREDICATE = "id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"

LEARNER_ID_CHECK = "learner_id ~ '^[a-z0-9][a-z0-9_-]{7,63}$' AND learner_id !~ '[0-9]{7,}'"

PROFILE_CHECK = "profile IN ('adult_general', 'cognitive_support')"


def _tenant_scoped_rls(table: str) -> None:
    """Enable and FORCE RLS, then add the two-clause isolation policy."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    # FORCE is the whole point: an owner bypasses its own policy without it,
    # and every test passes while the hole stays open.
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
          USING      ({TENANT_PREDICATE})
          WITH CHECK ({TENANT_PREDICATE})
        """
    )


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE tenants (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug        text NOT NULL UNIQUE,
            name        text NOT NULL,
            profile     text NOT NULL,
            status      text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT tenants_profile_check CHECK ({PROFILE_CHECK})
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE learners (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            learner_id   text NOT NULL,
            profile      text NOT NULL,
            date_of_birth date,
            created_at   timestamptz NOT NULL DEFAULT now(),
            deleted_at   timestamptz,
            CONSTRAINT learners_profile_check CHECK ({PROFILE_CHECK}),
            CONSTRAINT learners_learner_id_format CHECK ({LEARNER_ID_CHECK}),
            CONSTRAINT learners_tenant_learner_id_key UNIQUE (tenant_id, learner_id)
        )
        """
    )
    # The policy predicate is a filter on tenant_id; without the index every
    # policy check scans.
    op.execute("CREATE INDEX learners_tenant_id_idx ON learners (tenant_id)")

    op.execute(
        """
        CREATE TABLE audit_log (
            id          bigserial PRIMARY KEY,
            tenant_id   uuid NOT NULL REFERENCES tenants (id),
            occurred_at timestamptz NOT NULL DEFAULT now(),
            actor       text NOT NULL,
            action      text NOT NULL,
            target      text,
            metadata    jsonb NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX audit_log_tenant_id_idx ON audit_log (tenant_id)")

    _tenant_scoped_rls("learners")
    _tenant_scoped_rls("audit_log")

    # tenants is not tenant-scoped in the column sense, but a tenant may still
    # only see itself: without this the app role could enumerate every tenant.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON tenants
          USING      ({TENANTS_PREDICATE})
          WITH CHECK ({TENANTS_PREDICATE})
        """
    )

    # Grants. DML only for the app role, and audit_log is append-only: INSERT
    # and SELECT, never UPDATE, DELETE or TRUNCATE (TRUNCATE ignores RLS).
    op.execute("GRANT SELECT, INSERT, UPDATE ON tenants TO adapt_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON learners TO adapt_app")
    op.execute("GRANT SELECT, INSERT ON audit_log TO adapt_app")
    op.execute("REVOKE ALL ON audit_log FROM adapt_app")
    op.execute("GRANT SELECT, INSERT ON audit_log TO adapt_app")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {_audit_log_sequence()} TO adapt_app")
    for table in ("tenants", "learners", "audit_log"):
        op.execute(f"REVOKE DELETE, TRUNCATE ON {table} FROM adapt_app")


def _audit_log_sequence() -> str:
    """The sequence backing audit_log.id, as bigserial names it."""
    return "audit_log_id_seq"


def downgrade() -> None:
    # Policies go with the tables, but drop them explicitly so the intent of
    # the down migration is readable rather than implied.
    for table in ("tenants", "learners", "audit_log"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS learners")
    op.execute("DROP TABLE IF EXISTS tenants")
