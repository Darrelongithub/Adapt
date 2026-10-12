"""item provenance + calibration for model-written questions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12 09:40:00.000000

Adds the five columns ``assessment/generated.py`` needs to store a generated item and
calibrate it from responses instead of trusting the writing model's difficulty guess:
``generated_by``, ``prior_difficulty``, ``n_attempts``, ``n_correct``, ``calibrated_at``.

The integer counters are added with a server default so existing rows are valid without
a data migration; every row that predates this revision is authored, so 0 attempts is
the truth rather than a placeholder. ``prior_difficulty`` is left NULL on existing rows:
they have no declared-vs-observed split to preserve.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('generated_by', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('prior_difficulty', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('n_attempts', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('n_correct', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('calibrated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('items', schema=None) as batch_op:
        batch_op.drop_column('calibrated_at')
        batch_op.drop_column('n_correct')
        batch_op.drop_column('n_attempts')
        batch_op.drop_column('prior_difficulty')
        batch_op.drop_column('generated_by')
