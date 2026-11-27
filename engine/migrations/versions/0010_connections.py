"""connections and their webhook delivery log

Revision ID: 0010_connections
Revises: 0009_tenants_tier
Create Date: 2026-11-17

A connection is the unit a customer understands: **host app x environment x key
x webhook** (AGENT_M1 §1). It is also the only place an environment is recorded
per credential, which is why the key id lives on the connection rather than the
other way round.

Three properties are in the schema rather than in a handler:

* **Exactly one of each (host, environment) per tenant.** A second "iOS live"
  connection is a configuration mistake, and a unique constraint says so at
  write time instead of letting two keys quietly disagree.
* **Paused and revoked are timestamps, not just a word.** ``status`` is the
  state the API reads; ``paused_at`` / ``revoked_at`` are when it happened, and
  the CHECK constraints keep the two in step. ``revoked`` is terminal. Pause
  stops ingest and keeps data; revoke stops authentication, because the same
  transaction revokes the key.
* **The delivery log never holds a payload.** ``webhook_deliveries`` records the
  event type, an opaque subject id, the attempt count and the last status code;
  ``webhook_attempts`` records one row per attempt. There is no column for the
  body, so "we only kept the audit trail" is enforced by the schema rather than
  by review. A webhook is a notification that something happened, not a second
  version of a learner's data.

The webhook signing secret is stored as an ordinary value envelope under the
tenant data key, authenticated against ``(tenant, this row, 'webhook_secret')``
— the same AAD rule as every other encrypted column (DECISIONS §12). No new
exception, no separate key hierarchy.

``webhook_deliveries`` is unique on ``(tenant_id, webhook_id)``: Standard
Webhooks puts the message id in a header precisely so a duplicate id is
recognisably the same message. A retry keeps the id (the receiver dedupes); a
second enqueue of the same id is a no-op, not a second delivery.
"""

from __future__ import annotations

from alembic import op

revision = "0010_connections"
down_revision = "0009_tenants_tier"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"

#: The value envelope every encrypted column must hold (0008).
VALUE_ENVELOPE = r"^v1:dk[0-9]{1,6}:[A-Za-z0-9_-]{16}:[A-Za-z0-9_-]{16,}$"

#: Event types the engine emits at M1. A closed set in the schema: the console
#: renders a delivery log per type, and "some other event" is not a thing a
#: receiver can subscribe to.
EVENT_TYPES = "'decision.created', 'consent.changed', 'quota.warning'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE connections (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            host_name           text NOT NULL,
            environment         text NOT NULL,
            key_id              uuid NOT NULL UNIQUE REFERENCES api_keys (id),
            webhook_url         text,
            webhook_secret      text,
            status              text NOT NULL DEFAULT 'active',
            event_quota_monthly bigint NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            created_by          uuid,
            paused_at           timestamptz,
            revoked_at          timestamptz,
            CONSTRAINT connections_host_name_shape
                CHECK (host_name ~ '^[a-z0-9][a-z0-9._-]{{1,62}}[a-z0-9]$'),
            CONSTRAINT connections_environment_known
                CHECK (environment IN ('live', 'test')),
            CONSTRAINT connections_status_known
                CHECK (status IN ('active', 'paused', 'revoked')),
            CONSTRAINT connections_quota_positive CHECK (event_quota_monthly > 0),
            -- A webhook URL is https or it does not exist. Nothing here
            -- resolves the host or fetches it; that happens in the dispatcher,
            -- behind one seam, with a timeout.
            CONSTRAINT connections_webhook_url_shape
                CHECK (webhook_url IS NULL OR webhook_url ~ '^https://[^[:space:]]+$'),
            CONSTRAINT connections_webhook_secret_is_envelope
                CHECK (webhook_secret IS NULL OR webhook_secret ~ '{VALUE_ENVELOPE}'),
            -- A URL without a secret is unsigned, and an unsigned webhook is a
            -- webhook anyone can forge. Both or neither.
            CONSTRAINT connections_webhook_url_and_secret_agree
                CHECK ((webhook_url IS NULL) = (webhook_secret IS NULL)),
            CONSTRAINT connections_paused_at_matches_status
                CHECK ((status = 'paused') = (paused_at IS NOT NULL)),
            CONSTRAINT connections_revoked_at_matches_status
                CHECK ((status = 'revoked') = (revoked_at IS NOT NULL)),
            -- Revoking a paused connection keeps its paused_at: the history is
            -- what happened, and the service refuses to un-revoke either way.
            CONSTRAINT connections_tenant_host_environment_unique
                UNIQUE (tenant_id, host_name, environment)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE webhook_deliveries (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            connection_id    uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
            event_type       text NOT NULL,
            webhook_id       text NOT NULL,
            subject_id       text,
            status           text NOT NULL DEFAULT 'pending',
            attempts         integer NOT NULL DEFAULT 0,
            last_status_code integer,
            last_error       text,
            next_attempt_at  timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            delivered_at     timestamptz,
            CONSTRAINT webhook_deliveries_event_type_known
                CHECK (event_type IN ({EVENT_TYPES})),
            CONSTRAINT webhook_deliveries_webhook_id_shape
                CHECK (webhook_id ~ '^msg_[0-9a-f]{{32}}$'),
            CONSTRAINT webhook_deliveries_status_known
                CHECK (status IN ('pending', 'delivering', 'delivered', 'failed')),
            CONSTRAINT webhook_deliveries_attempts_bounded
                CHECK (attempts >= 0 AND attempts <= 5),
            CONSTRAINT webhook_deliveries_last_error_short
                CHECK (last_error IS NULL OR length(last_error) <= 200),
            CONSTRAINT webhook_deliveries_subject_is_opaque
                CHECK (subject_id IS NULL OR length(subject_id) <= 64),
            CONSTRAINT webhook_deliveries_delivered_at_matches_status
                CHECK ((status = 'delivered') = (delivered_at IS NOT NULL)),
            CONSTRAINT webhook_deliveries_duplicate_id_is_the_same_message
                UNIQUE (tenant_id, webhook_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE webhook_attempts (
            id           bigserial PRIMARY KEY,
            tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            delivery_id  uuid NOT NULL REFERENCES webhook_deliveries (id) ON DELETE CASCADE,
            attempt      integer NOT NULL,
            status_code  integer,
            error_class  text,
            duration_ms  integer NOT NULL,
            attempted_at timestamptz NOT NULL DEFAULT now(),
            -- Exactly one of "the receiver answered" and "we never got one".
            CONSTRAINT webhook_attempts_outcome_known
                CHECK ((status_code IS NULL) <> (error_class IS NULL)),
            CONSTRAINT webhook_attempts_number_positive CHECK (attempt >= 1),
            CONSTRAINT webhook_attempts_error_class_short
                CHECK (error_class IS NULL OR error_class ~ '^[a-z_]+$'),
            CONSTRAINT webhook_attempts_duration_sane
                CHECK (duration_ms >= 0 AND duration_ms <= 120000),
            CONSTRAINT webhook_attempts_once_per_number UNIQUE (delivery_id, attempt)
        )
        """
    )

    for table in ("connections", "webhook_deliveries", "webhook_attempts"):
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

    op.execute(
        "CREATE INDEX webhook_deliveries_connection_status_idx"
        " ON webhook_deliveries (connection_id, status)"
    )
    op.execute(
        "CREATE INDEX webhook_deliveries_due_idx"
        " ON webhook_deliveries (next_attempt_at) WHERE status IN ('pending', 'failed')"
    )

    op.execute("GRANT SELECT, INSERT, UPDATE ON connections TO adapt_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON webhook_deliveries TO adapt_app")
    op.execute("GRANT SELECT, INSERT ON webhook_attempts TO adapt_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE webhook_attempts_id_seq TO adapt_app")


def downgrade() -> None:
    op.execute("REVOKE ALL ON SEQUENCE webhook_attempts_id_seq FROM adapt_app")
    for table in ("webhook_attempts", "webhook_deliveries", "connections"):
        op.execute(f"REVOKE ALL ON {table} FROM adapt_app")
    op.execute("DROP TABLE webhook_attempts")
    op.execute("DROP TABLE webhook_deliveries")
    op.execute("DROP TABLE connections")
