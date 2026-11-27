"""replays become an append-only log; the decision row becomes immutable again

Revision ID: 0017_replays_append_only
Revises: 0016_metrics
Create Date: 2026-11-19

Increment 6 counted replays with `decisions.replay_count`, incremented on the row
that a replay re-serves. That made a served decision mutable, which is exactly the
property G1 rests on: the stored body *is* the evidence, and evidence that changes
after the fact is not evidence (`AGENT_M1` §6, "Replay accounting: log it, do not
mutate the decision").

So the counter goes and `decision_replays` arrives: one row per replay, holding
when it happened and whether it was test traffic, with `adapt_app` holding
`INSERT` and `SELECT` only. No `UPDATE`, no `DELETE` — an audit trail a bug can
rewrite is not an audit trail.

`UPDATE` is also revoked on `decisions` itself, which nothing has needed since the
counter left: the only writer of that table is the request that created the row.
`replay_count` is dropped rather than kept as a vestigial column, because a
column that no code writes is a column somebody will eventually read.
"""

from __future__ import annotations

from alembic import op

revision = "0017_replays_append_only"
down_revision = "0016_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE decision_replays (
            id           uuid NOT NULL DEFAULT gen_random_uuid(),
            tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            decision_id  uuid NOT NULL,
            at           timestamptz NOT NULL,
            test_traffic boolean NOT NULL DEFAULT false,
            PRIMARY KEY (id),
            CONSTRAINT decision_replays_decision
                FOREIGN KEY (decision_id) REFERENCES decisions (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX decision_replays_decision_idx ON decision_replays (decision_id)")
    op.execute("CREATE INDEX decision_replays_tenant_idx ON decision_replays (tenant_id, at)")

    # Same row-level security as every other tenant table: forced, with the
    # tenant predicate on both USING and WITH CHECK.
    op.execute("ALTER TABLE decision_replays ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE decision_replays FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON decision_replays
          USING      (tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT ON decision_replays TO adapt_app")

    op.execute("REVOKE UPDATE ON decisions FROM adapt_app")
    op.execute("ALTER TABLE decisions DROP CONSTRAINT decisions_replay_count_sane")
    op.execute("ALTER TABLE decisions DROP COLUMN replay_count")


def downgrade() -> None:
    op.execute("ALTER TABLE decisions ADD COLUMN replay_count integer NOT NULL DEFAULT 0")
    op.execute(
        "ALTER TABLE decisions ADD CONSTRAINT decisions_replay_count_sane CHECK (replay_count >= 0)"
    )
    # No backfill: this is a schema migration, and a downgrade that silently
    # reconstructs a counter from the log would be DML in a migration. The
    # counter comes back at zero and `decision_replays` is dropped with it.
    op.execute("GRANT UPDATE ON decisions TO adapt_app")
    op.execute("DROP TABLE decision_replays")
