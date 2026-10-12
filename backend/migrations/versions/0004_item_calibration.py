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

Both directions are existence-aware, for the same reason revision ``0003`` is: a fresh clone
gets its schema from ``Base.metadata.create_all``, which already builds these five columns
from ``Item.__table__``. Such a database has no ``alembic_version`` row, and the documented
way into the chain (``stamp 0002``, then ``upgrade head``) replays every revision after it --
so without the check below the recovery step ends in ``duplicate column name: generated_by``
and the operator's next move is to delete the database, which is exactly what that path
exists to avoid.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "items"

# name -> column definition. Added in this order, so a database that has some of them (a
# half-applied revision, or a create_all database from a build between the two) gets the rest.
COLUMNS: list[tuple[str, sa.Column]] = [
    ("generated_by", sa.Column("generated_by", sa.String(length=64), nullable=True)),
    ("prior_difficulty", sa.Column("prior_difficulty", sa.Float(), nullable=True)),
    ("n_attempts", sa.Column("n_attempts", sa.Integer(), nullable=False, server_default="0")),
    ("n_correct", sa.Column("n_correct", sa.Integer(), nullable=False, server_default="0")),
    ("calibrated_at", sa.Column("calibrated_at", sa.DateTime(), nullable=True)),
]


def _present() -> set[str]:
    """The columns ``items`` has right now, whoever put them there (this chain or create_all)."""
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade() -> None:
    have = _present()
    missing = [(name, col) for name, col in COLUMNS if name not in have]
    if not missing:
        return
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for _, col in missing:
            batch_op.add_column(col)


def downgrade() -> None:
    have = _present()
    drop = [name for name, _ in reversed(COLUMNS) if name in have]
    if not drop:
        return
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for name in drop:
            batch_op.drop_column(name)
