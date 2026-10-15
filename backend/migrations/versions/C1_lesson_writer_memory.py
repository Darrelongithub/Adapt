"""Store AI 3's chosen teaching method and analogy for each explanation.

Revision ID: C1
Revises: P2
Create Date: 2026-10-14

The columns are nullable so existing teaching-memory rows remain valid.  A stable
explanation id continues to live in ``ref`` under the existing unique constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "C1"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "teaching_memory" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("teaching_memory")}
    for name, length in (("method", 48), ("analogy_id", 96), ("moment", 24)):
        if name not in columns:
            op.add_column(
                "teaching_memory", sa.Column(name, sa.String(length=length), nullable=True)
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "teaching_memory" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("teaching_memory")}
    for name in ("moment", "analogy_id", "method"):
        if name in columns:
            op.drop_column("teaching_memory", name)
