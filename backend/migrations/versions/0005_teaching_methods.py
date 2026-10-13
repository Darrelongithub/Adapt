"""teaching method recorded per attempt + how an item's key was verified

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12 11:05:00.000000

Adds what the fail→reteach→compare loop needs to be readable *across sessions* rather
than only inside the session in front of the learner:

``attempts``: ``method`` (the teaching method assigned for this attempt — a decision,
not an item field), ``question_kind`` (the format it was measured with, kept separate
because a method comparison confounded with the answer format is a format
comparison), ``expected_success`` (the model's own prediction when the question was
served, which is what makes "actual vs expected" possible at all), ``mapping_design``
(``randomised`` when the teaching arm was assigned, ``observational`` when the learner
chose it), ``method_arm``, ``reteach_of``, and ``pilot`` (these rows came from the
pre-demo pilot and are labelled as such wherever they are reported).

``items``: ``verification`` — ``authored`` / ``arithmetic`` / ``quotable`` /
``unverified``. Maths keys can be computed; a history key cannot, so it is checked by
finding the answer in the source material, and an item whose key could not be checked
says so instead of being shown as if it had been.

Both directions are existence-aware, like ``0003`` and ``0004``: a database built by
``create_all`` from this build already has every column, and the documented recovery
path (``stamp 0002`` then ``upgrade head``) replays revisions onto tables that match
them. Without the check, that path ends in ``duplicate column name`` and the operator
deletes the database — the outcome this chain exists to prevent.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ATTEMPT_COLUMNS: list[tuple[str, sa.Column]] = [
    ("method", sa.Column("method", sa.String(length=32), nullable=True)),
    ("question_kind", sa.Column("question_kind", sa.String(length=24), nullable=True)),
    ("expected_success", sa.Column("expected_success", sa.Float(), nullable=True)),
    ("mapping_design", sa.Column("mapping_design", sa.String(length=24), nullable=True)),
    ("method_arm", sa.Column("method_arm", sa.String(length=24), nullable=True)),
    ("reteach_of", sa.Column("reteach_of", sa.String(), nullable=True)),
    ("pilot", sa.Column("pilot", sa.Boolean(), nullable=False, server_default=sa.false())),
]

ITEM_COLUMNS: list[tuple[str, sa.Column]] = [
    ("verification", sa.Column("verification", sa.String(length=16), nullable=False, server_default="authored")),
]

INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_attempts_method", "attempts", ["method"]),
    ("ix_attempts_reteach_of", "attempts", ["reteach_of"]),
]


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    for table, columns in (("attempts", ATTEMPT_COLUMNS), ("items", ITEM_COLUMNS)):
        have = _columns(table)
        missing = [(name, col) for name, col in columns if name not in have]
        if missing:
            with op.batch_alter_table(table, schema=None) as batch_op:
                for _, col in missing:
                    batch_op.add_column(col)
        present = _indexes(table)
        for name, index_table, cols in INDEXES:
            if index_table == table and name not in present:
                with op.batch_alter_table(table, schema=None) as batch_op:
                    batch_op.create_index(name, cols, unique=False)


def downgrade() -> None:
    for name, index_table, _cols in reversed(INDEXES):
        if index_table not in ("attempts", "items"):
            continue
        if name in _indexes(index_table):
            with op.batch_alter_table(index_table, schema=None) as batch_op:
                batch_op.drop_index(name)
    for table, columns in (("attempts", ATTEMPT_COLUMNS), ("items", ITEM_COLUMNS)):
        have = _columns(table)
        drop = [name for name, _ in reversed(columns) if name in have]
        if drop:
            with op.batch_alter_table(table, schema=None) as batch_op:
                for name in drop:
                    batch_op.drop_column(name)
