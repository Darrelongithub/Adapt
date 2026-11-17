"""Credits: grants, an append-only ledger, and reservations

Revision ID: 0005_credits
Revises: 0004_key_hardening
Create Date: 2026-11-17

Three tables, per ``METERING.md`` §4, plus one that the spec implies:
``credit_reservation_draws`` records *which* grants a reservation took credits
from. Without it a release cannot put the credits back where they came from,
and a drawdown order that nobody recorded is a drawdown order nobody tested.

The rules that live in the schema rather than in application code:

* ``remaining >= 0`` — a balance cannot go negative even if a handler is
  wrong. The concurrency test drives 100 reservations at a balance of 10; the
  CHECK is the last line, not the mechanism.
* ``remaining <= amount`` — drawing from an expired grant cannot resurrect
  more than was granted.
* ``credit_ledger`` is append-only: a trigger refuses UPDATE and DELETE for
  every role, because "append-only" that the owner can rewrite is a log, not a
  ledger.
* ``unique (tenant_id, idempotency_key)`` — replays are decided by the
  constraint, not by a lookup-then-insert race in Python.

Drawdown order is expiring-first, then purchased oldest-first, which is the
order that protects the customer's money: use the credits that are about to
evaporate before the ones that never expire. It is expressed once, in the
``ORDER BY`` of the lock query in ``credits/service.py``, and asserted by two
tests.
"""

from __future__ import annotations

from alembic import op

revision = "0005_credits"
down_revision = "0004_key_hardening"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"


def _tenant_scoped(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
          USING      ({TENANT_PREDICATE})
          WITH CHECK ({TENANT_PREDICATE})
        """
    )
    op.execute(f"CREATE INDEX {table}_tenant_id_idx ON {table} (tenant_id)")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE credit_grants (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            kind        text NOT NULL,
            amount      integer NOT NULL,
            remaining   integer NOT NULL,
            granted_at  timestamptz NOT NULL DEFAULT now(),
            expires_at  timestamptz,
            source_ref  text,
            CONSTRAINT credit_grants_kind_known
                CHECK (kind IN ('included', 'purchased', 'promo')),
            CONSTRAINT credit_grants_amount_positive CHECK (amount > 0),
            CONSTRAINT credit_grants_remaining_bounded
                CHECK (remaining >= 0 AND remaining <= amount),
            -- An allowance that never expires is a pack with a nicer name.
            -- No "expires_at > granted_at" CHECK: shortening an expiry is a
            -- legitimate correction, and a CHECK would block it (and block
            -- testing a grant that expires mid-flight).
            CONSTRAINT credit_grants_included_expires
                CHECK (kind <> 'included' OR expires_at IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX credit_grants_drawdown_idx"
        " ON credit_grants (tenant_id, expires_at NULLS LAST, granted_at)"
    )

    op.execute(
        """
        CREATE TABLE credit_reservations (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            amount         integer NOT NULL,
            state          text NOT NULL,
            operation_ref  text NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            closed_at      timestamptz,
            CONSTRAINT credit_reservations_amount_positive CHECK (amount > 0),
            CONSTRAINT credit_reservations_state_known
                CHECK (state IN ('held', 'settled', 'released')),
            CONSTRAINT credit_reservations_closed_has_timestamp
                CHECK ((state = 'held') = (closed_at IS NULL))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE credit_reservation_draws (
            id              bigserial PRIMARY KEY,
            tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            reservation_id  uuid NOT NULL REFERENCES credit_reservations (id) ON DELETE CASCADE,
            grant_id        uuid NOT NULL REFERENCES credit_grants (id) ON DELETE CASCADE,
            amount          integer NOT NULL,
            CONSTRAINT credit_reservation_draws_amount_positive CHECK (amount > 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX credit_reservation_draws_reservation_idx"
        " ON credit_reservation_draws (reservation_id)"
    )

    op.execute(
        """
        CREATE TABLE credit_ledger (
            id               bigserial PRIMARY KEY,
            tenant_id        uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            grant_id         uuid REFERENCES credit_grants (id) ON DELETE SET NULL,
            delta            integer NOT NULL,
            reason           text NOT NULL,
            ref              text,
            idempotency_key  text NOT NULL,
            period_start     date,
            created_at       timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT credit_ledger_reason_known
                CHECK (reason IN ('reserve', 'settle', 'release', 'grant', 'expire')),
            CONSTRAINT credit_ledger_consumption_never_positive
                CHECK (reason <> 'settle' OR delta <= 0),
            CONSTRAINT credit_ledger_tenant_idempotency_key
                UNIQUE (tenant_id, idempotency_key)
        )
        """
    )

    for table in (
        "credit_grants",
        "credit_reservations",
        "credit_reservation_draws",
        "credit_ledger",
    ):
        _tenant_scoped(table)

    # Append-only, for every role. A trigger is what makes that true for the
    # owner too; a REVOKE alone would only bind the app role.
    op.execute(
        """
        CREATE FUNCTION credit_ledger_append_only() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            RAISE EXCEPTION 'credit_ledger is append-only: % is not permitted', TG_OP
                USING ERRCODE = '42501';
        END
        $fn$
        """
    )
    op.execute(
        """
        CREATE TRIGGER credit_ledger_no_update
            BEFORE UPDATE OR DELETE ON credit_ledger
            FOR EACH ROW EXECUTE FUNCTION credit_ledger_append_only()
        """
    )
    op.execute("ALTER FUNCTION credit_ledger_append_only() OWNER TO adapt_owner")

    # The app role works inside tenant context; RLS is the boundary here, and
    # unlike api_keys there is no reason to withhold the table itself.
    op.execute("GRANT SELECT, INSERT, UPDATE ON credit_grants TO adapt_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON credit_reservations TO adapt_app")
    op.execute("GRANT SELECT, INSERT ON credit_reservation_draws TO adapt_app")
    op.execute("GRANT SELECT, INSERT ON credit_ledger TO adapt_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE credit_ledger_id_seq TO adapt_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE credit_reservation_draws_id_seq TO adapt_app")
    op.execute("REVOKE UPDATE, DELETE ON credit_ledger FROM adapt_app")
    op.execute("REVOKE UPDATE, DELETE ON credit_reservation_draws FROM adapt_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS credit_ledger_no_update ON credit_ledger")
    op.execute("DROP FUNCTION IF EXISTS credit_ledger_append_only()")
    for table in (
        "credit_ledger",
        "credit_reservation_draws",
        "credit_reservations",
        "credit_grants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
