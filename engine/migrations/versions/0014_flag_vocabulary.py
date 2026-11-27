"""the flag vocabulary gains `undeterminable`

Revision ID: 0014_flag_vocabulary
Revises: 0013_decide
Create Date: 2026-11-18

`straight_line` is a property of the learner's *response*, not of whether they
were right: eight identical correctness outcomes is what a learner on a roll
produces, and flagging it turned good performance into an accusation of
inattention (MEASUREMENT §4, METRICS §1.3). The rule now reads
`response_digest`, and when the host sent no response the honest flag is
`undeterminable` rather than a guess derived from correctness.

A CHECK constraint is the vocabulary, so widening it is a migration: `flags` is
`<@` a closed array, and a row carrying a value outside it cannot be written. The
downgrade narrows the array again; rows that already hold `undeterminable` would
make it fail, which is the correct behaviour for a downgrade that would erase a
fact — the operator sees it and decides.
"""

from __future__ import annotations

from alembic import op

revision = "0014_flag_vocabulary"
down_revision = "0013_decide"
branch_labels = None
depends_on = None

BEFORE = (
    "'rapid_guess', 'tab_hidden', 'straight_line', 'device_change', 'assisted', 'repeated_item'"
)
AFTER = BEFORE + ", 'undeterminable'"


def _replace(allowed: str) -> None:
    op.execute("ALTER TABLE learning_events DROP CONSTRAINT learning_events_flags_known")
    op.execute(
        f"""
        ALTER TABLE learning_events ADD CONSTRAINT learning_events_flags_known
            CHECK (validity_flags <@ ARRAY[{allowed}]::text[])
        """
    )


def upgrade() -> None:
    _replace(AFTER)


def downgrade() -> None:
    _replace(BEFORE)
