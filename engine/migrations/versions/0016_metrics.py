"""metrics: a replay counter, and the indexes a window query needs

Revision ID: 0016_metrics
Revises: 0015_metering
Create Date: 2026-11-19

`METRICS.md` §1.2 puts "idempotent replays (same Idempotency-Key already counted)"
outside the abstention denominator, and §1.5's `abstention_polling` alert needs
to see repeats of the same `(learner, construct)`. Neither is computable from the
rows that exist today:

* A replay returns the stored decision and writes no new row, so `decisions`
  cannot count replays. `replay_count` is incremented on the row a replay
  re-serves — **no money moves, and no second decision row exists**; the counter
  is the difference between "we excluded replays" and "we assumed we did".
* The repeat ratio is a group-by over `(connection, learner, skill)` inside a
  time window. The existing `decisions_learner_idx` is ordered for a single
  learner's history; a window scan over one tenant wants `(tenant_id,
  issued_at)`, and the polling check wants the grouping key with `issued_at`
  last so it can still be range-limited.

`replay_count` is a plain column with a plain UPDATE rather than a counter table:
`decisions` already carries a tenant-isolation policy and an `UPDATE` grant
(migration 0013), and a second table would be a second place for a decision's
facts to live.
"""

from __future__ import annotations

from alembic import op

revision = "0016_metrics"
down_revision = "0015_metering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE decisions ADD COLUMN replay_count integer NOT NULL DEFAULT 0")
    op.execute(
        "ALTER TABLE decisions ADD CONSTRAINT decisions_replay_count_sane CHECK (replay_count >= 0)"
    )
    op.execute("CREATE INDEX decisions_tenant_issued_idx ON decisions (tenant_id, issued_at)")
    op.execute(
        "CREATE INDEX decisions_repeat_idx"
        " ON decisions (tenant_id, connection_id, learner_id, skill, issued_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS decisions_repeat_idx")
    op.execute("DROP INDEX IF EXISTS decisions_tenant_issued_idx")
    op.execute("ALTER TABLE decisions DROP CONSTRAINT decisions_replay_count_sane")
    op.execute("ALTER TABLE decisions DROP COLUMN replay_count")
