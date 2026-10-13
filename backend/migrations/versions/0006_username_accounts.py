"""username accounts: a learner identifier that is not an email address

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13 09:20:00.000000

The front door now asks for a username and a password rather than a name, an email and a
password. That is a schema change and not a relabelling: the identifier has to be findable
by equality at sign-in time, which means its own encrypted column and its own blind index.

Two columns, not one reused field. ``student.email`` and ``student.username`` are keyed
digests — the digest is bound to the column label, so storing a username under the email
label produces rows whose lookup never matches, and the failure mode is "sign-in works for
everyone except the people who registered". A separate column also keeps the answer to
"which identifier does this row carry" readable from the row itself, which matters when both
kinds of account exist at once (they do: email registration is still accepted, so accounts
made before this revision and the tunnel demo keep signing in).

Existence-aware in both directions, like ``0003``, ``0004`` and ``0005``: a database built by
``create_all`` from this build already has the columns, and ``stamp 0005`` then ``upgrade
head`` replays this revision onto a table that matches it. Without the check that path ends
in ``duplicate column name``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from adapt.crypto.fields import BlindIndexedUsername

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STUDENT_COLUMNS: list[tuple[str, sa.Column]] = [
    ("username", sa.Column("username", BlindIndexedUsername(), nullable=True)),
    ("username_index", sa.Column("username_index", sa.String(length=32), nullable=True)),
]

INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_students_username_index", "students", ["username_index"]),
]


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    have = _columns("students")
    missing = [(name, col) for name, col in STUDENT_COLUMNS if name not in have]
    if missing:
        with op.batch_alter_table("students", schema=None) as batch_op:
            for _, col in missing:
                batch_op.add_column(col)
    present = _indexes("students")
    for name, table, cols in INDEXES:
        if name not in present:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.create_index(name, cols, unique=True)


def downgrade() -> None:
    for name, table, _cols in reversed(INDEXES):
        if name in _indexes(table):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_index(name)
    have = _columns("students")
    drop = [name for name, _ in reversed(STUDENT_COLUMNS) if name in have]
    if drop:
        with op.batch_alter_table("students", schema=None) as batch_op:
            for name in drop:
                batch_op.drop_column(name)
