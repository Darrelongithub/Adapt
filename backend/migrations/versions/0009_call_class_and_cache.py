"""which class of work each call was, and how much of it the cache served

Revision ID: 0009
Revises: P2_subject_round
Create Date: 2026-10-14 10:20:00.000000

Adds the two columns the cost round needs to be *checkable* rather than believed:

* ``llm_calls.tier`` -- the class of work (``ai/routing.py``) a call was routed as. Written
  per call, because the routing table is meant to change: "we moved the retention question to
  the quick class last week and the daily call count halved" is a claim that needs the old
  rows to still say what they were.
* ``llm_calls.cached_tokens`` -- input tokens the provider served from a registered static
  prefix. This is a saving, not a cost, and it is the only honest answer to "did caching earn
  its place on this deployment" (a cache that is created and never hit shows as created=1,
  reused=0).

Both are nullable/defaulted so existing rows stay valid without a data migration: they
predate the routing table and were, by definition, not served from a cache.

Existence-aware in both directions, like ``0003`` and ``0004``: a fresh clone gets its schema
from ``Base.metadata.create_all``, which already builds these columns, and the documented
``stamp 0002 && upgrade head`` recovery path replays every revision after it -- so without the
check the recovery step would end in ``duplicate column name``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: str | None = 'P2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "llm_calls"

COLUMNS: list[tuple[str, sa.Column]] = [
    ("tier", sa.Column("tier", sa.String(length=12), nullable=True)),
    ("cached_tokens", sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0")),
]


def _present() -> set[str]:
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
