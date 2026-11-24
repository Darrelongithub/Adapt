"""tenants.tier: core | plus | deep, with a default and a CHECK

Revision ID: 0009_tenants_tier
Revises: 0008_key_lifecycle
Create Date: 2026-11-17

The contract column `ADR-002` depends on. It is *entitlement* only: what the
customer bought. Capability (what the code can do) and validation (what the
gates have earned) live in code and in the coverage matrix, and the three are
never collapsed into this column — `resolve_level()` takes all three and says
which one bound the answer.

Default `core`: a tenant created without a tier is the smallest entitlement,
not a missing one. A nullable tier would let a NULL mean both "nobody decided"
and "no access", and the two need different answers at the API boundary.
"""

from __future__ import annotations

from alembic import op

revision = "0009_tenants_tier"
down_revision = "0008_key_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenants ADD COLUMN tier text NOT NULL DEFAULT 'core'")
    op.execute(
        """
        ALTER TABLE tenants
            ADD CONSTRAINT tenants_tier_known CHECK (tier IN ('core', 'plus', 'deep'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_tier_known")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS tier")
