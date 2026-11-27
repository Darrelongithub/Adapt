"""observe: learning events, batches, and the monthly event counter

Revision ID: 0011_observe
Revises: 0010_connections
Create Date: 2026-11-18

Three tables and one partition scheme, each answering a question the increment
would otherwise leave to a handler:

* ``observe_batches`` — the idempotency record. ``(tenant_id, idempotency_key)``
  is unique and the **response body is stored with it**, because "a duplicate
  batch returns the original response" is only true if the original response is
  still somewhere. ``request_hash`` is what lets a replay with a *different*
  body be a `409 conflict` instead of silently accepting the second body as a
  retry of the first.
* ``learning_events`` — append-only, ``PARTITION BY RANGE (ts)`` by month. The
  partition key has to be in the primary key, so the key is ``(id, ts)``.
  Partitions are created ahead by ``scripts/ensure_event_partitions.py`` (the
  runtime role cannot run DDL, and a table that can be dropped by a request is a
  table an attacker can drop); a DEFAULT partition exists so an event can never
  be refused by a missing partition — and a test asserts it stays empty, which
  is the alert that the window ran out.
* ``connection_event_usage`` — the monthly counter ``event_quota_monthly`` is
  enforced against. One row per (connection, month), incremented in the same
  statement that checks the cap, so two concurrent batches cannot both see
  "one space left".

Validity flags are stored on the row, never imputed and never dropped
(MEASUREMENT §4). ``tab_hidden`` has no input channel in the allowlist at M1 —
see KNOWN_GAPS — so it exists in the vocabulary and cannot be set.

The webhook event set grows here too: INTEGRATION §8 lists five event types and
0010 closed the CHECK at three. Widening a CHECK is cheap; a delivery row that
cannot be written because the type was added to the doc and not to the schema is
a 500 discovered in production.
"""

from __future__ import annotations

from datetime import UTC, datetime

from alembic import op

revision = "0011_observe"
down_revision = "0010_connections"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"


def _months_ahead(start: datetime, count: int) -> list[str]:
    out: list[str] = []
    year, month = start.year, start.month
    for _ in range(count):
        out.append(f"{year:04d}_{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _month_start_utc(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE observe_batches (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            connection_id   uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
            idempotency_key text NOT NULL,
            request_hash    bytea NOT NULL,
            event_count     integer NOT NULL,
            accepted_at     timestamptz NOT NULL,
            response        jsonb NOT NULL,
            CONSTRAINT observe_batches_key_shape CHECK (length(idempotency_key) BETWEEN 8 AND 200),
            CONSTRAINT observe_batches_count_in_range
                CHECK (event_count BETWEEN 0 AND 500),
            CONSTRAINT observe_batches_once_per_key UNIQUE (tenant_id, idempotency_key)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE learning_events (
            id              uuid NOT NULL DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            connection_id   uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
            batch_id        uuid NOT NULL REFERENCES observe_batches (id) ON DELETE CASCADE,
            learner_id      text NOT NULL,
            skill           text NOT NULL,
            item_id         text NOT NULL,
            correct         boolean NOT NULL,
            ts              timestamptz NOT NULL,
            seq             integer,
            session_id      text,
            device          text,
            input_mode      text,
            latency_ms      integer,
            hint_used       boolean NOT NULL DEFAULT false,
            assisted        boolean NOT NULL DEFAULT false,
            difficulty_band text,
            decision_id     text,
            -- The learner's free-text answer is never stored. A digest keeps
            -- "was this the same response" answerable without keeping text that
            -- could contain anything the learner typed.
            response_digest bytea,
            response_length integer,
            validity_flags  text[] NOT NULL DEFAULT '{}',
            created_at      timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, ts),
            CONSTRAINT learning_events_learner_id_length
                CHECK (length(learner_id) BETWEEN 8 AND 64),
            CONSTRAINT learning_events_item_id_length
                CHECK (length(item_id) BETWEEN 1 AND 128),
            CONSTRAINT learning_events_skill_shape
                CHECK (skill ~ '^[a-z][a-z0-9_.-]{0,63}$'),
            CONSTRAINT learning_events_latency_sane
                CHECK (latency_ms IS NULL OR latency_ms BETWEEN 0 AND 86_400_000),
            CONSTRAINT learning_events_flags_known
                CHECK (validity_flags <@ ARRAY[
                    'rapid_guess', 'tab_hidden', 'straight_line', 'device_change',
                    'assisted', 'repeated_item'
                ]::text[])
        ) PARTITION BY RANGE (ts)
        """
    )

    # A window of real partitions, plus a default that keeps an insert from
    # failing outside it. The test that the default is empty is the alert.
    for suffix in _months_ahead(_month_start_utc(datetime.now(UTC)), 24):
        year, month = suffix.split("_")
        start = f"'{year}-{month}-01 00:00:00+00'"
        end = f"('{year}-{month}-01 00:00:00+00'::timestamptz + interval '1 month')"
        op.execute(
            f"CREATE TABLE learning_events_{suffix} PARTITION OF learning_events"
            f" FOR VALUES FROM ({start}) TO {end}"
        )
    op.execute("CREATE TABLE learning_events_default PARTITION OF learning_events DEFAULT")

    op.execute(
        """
        CREATE TABLE connection_event_usage (
            tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            connection_id uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
            period        date NOT NULL,
            events        bigint NOT NULL DEFAULT 0,
            PRIMARY KEY (connection_id, period),
            CONSTRAINT connection_event_usage_non_negative CHECK (events >= 0),
            CONSTRAINT connection_event_usage_period_is_month_start
                CHECK (period = date_trunc('month', period)::date)
        )
        """
    )

    for table in ("learning_events", "observe_batches", "connection_event_usage"):
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

    # Partitions are tables of their own; a policy on the parent does not apply
    # to a partition inserted into directly, so each one carries the same
    # policy. (RLS on the parent *is* enforced for queries through the parent,
    # which is how the API reads them — this is defence in depth, not the only
    # layer.)
    partitions = [*_months_ahead(_month_start_utc(datetime.now(UTC)), 24), "default"]
    for suffix in partitions:
        table = f"learning_events_{suffix}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING      ({TENANT_PREDICATE})
              WITH CHECK ({TENANT_PREDICATE})
            """
        )

    op.execute(
        "CREATE INDEX learning_events_learner_ts_idx"
        " ON learning_events (tenant_id, learner_id, ts DESC)"
    )
    # Three indexes, each for a query that exists today: the flag lookback,
    # the repeat check, the duplicate check. `batch_id`, `decision_id` and
    # `connection_id` are each indexed when the read that needs them lands
    # (increment 4 onward) — an index nothing queries is a tax every insert
    # pays, and the load test measured that tax at roughly half the write cost.
    # The duplicate check joins on (learner, session); without this it is a
    # sequential scan per batch once a learner has history.
    # `repeated_item` asks "has this learner seen this item", which must be an
    # index probe, not a scan of the learner's month.
    op.execute(
        "CREATE INDEX learning_events_repeat_idx ON learning_events (learner_id, item_id, ts)"
    )
    op.execute(
        "CREATE INDEX learning_events_dedupe_idx"
        " ON learning_events (learner_id, session_id, seq)"
        " WHERE seq IS NOT NULL AND session_id IS NOT NULL"
    )

    op.execute("GRANT SELECT, INSERT ON observe_batches TO adapt_app")
    op.execute("GRANT SELECT, INSERT ON learning_events TO adapt_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON connection_event_usage TO adapt_app")

    # INTEGRATION §8: five event types. 0010 closed the set at three.
    op.execute("ALTER TABLE webhook_deliveries DROP CONSTRAINT webhook_deliveries_event_type_known")
    op.execute(
        """
        ALTER TABLE webhook_deliveries ADD CONSTRAINT webhook_deliveries_event_type_known
            CHECK (event_type IN (
                'decision.created', 'consent.changed', 'quota.warning',
                'learner.erased', 'instrument.not_renderable'
            ))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE webhook_deliveries DROP CONSTRAINT webhook_deliveries_event_type_known")
    op.execute(
        """
        ALTER TABLE webhook_deliveries ADD CONSTRAINT webhook_deliveries_event_type_known
            CHECK (event_type IN ('decision.created', 'consent.changed', 'quota.warning'))
        """
    )
    op.execute("REVOKE ALL ON connection_event_usage FROM adapt_app")
    op.execute("REVOKE ALL ON learning_events FROM adapt_app")
    op.execute("REVOKE ALL ON observe_batches FROM adapt_app")
    op.execute("DROP TABLE connection_event_usage")
    op.execute("DROP TABLE learning_events")
    op.execute("DROP TABLE observe_batches")
