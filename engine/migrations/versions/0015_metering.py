"""metering: test traffic is a property of the decision row

Revision ID: 0015_metering
Revises: 0014_flag_vocabulary
Create Date: 2026-11-18

`X-Adapt-Test: 1` (or `ADAPT_ENV=staging`) marks a call as test traffic: it bills
**zero** and is excluded from metrics (METERING §3 rule 3). Two things follow, and
both are schema:

* ``decisions.test_traffic`` — the fact has to be stored on the row. A dashboard
  that counted test decisions would inflate every number a customer looks at, and
  "excluded from metrics" is not implementable without a column to exclude on.
* ``decisions_served_is_charged`` — the constraint that a served decision has a
  reservation now says "or the call was test traffic". A test decision is served
  and unbilled; without this, the free path would fail as a constraint violation.

**The `credit_consumption` view already exists** (migration 0006) and already
reads `entry_type = 'settle'` only, with `security_invoker = true`. It is not
recreated here: an increment that reimplements a working mechanism to look busy is
how two definitions of "consumption" end up in one database. What this increment
adds is the *test* that a served decision produces exactly one row in it per
tenant-month and that nothing else does.
"""

from __future__ import annotations

from alembic import op

revision = "0015_metering"
down_revision = "0014_flag_vocabulary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE decisions ADD COLUMN test_traffic boolean NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE decisions DROP CONSTRAINT decisions_served_is_charged")
    op.execute(
        """
        ALTER TABLE decisions ADD CONSTRAINT decisions_served_is_charged
            CHECK (abstain OR test_traffic OR reservation_id IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE decisions DROP CONSTRAINT decisions_served_is_charged")
    op.execute(
        """
        ALTER TABLE decisions ADD CONSTRAINT decisions_served_is_charged
            CHECK (abstain OR reservation_id IS NOT NULL)
        """
    )
    op.execute("ALTER TABLE decisions DROP COLUMN test_traffic")
