"""the journey: one table for a learner's own answer about a stage

Revision ID: 0009
Revises: P2
Create Date: 2026-10-14 09:10:00.000000

Round 2, Part D makes the journey one path, and the path's state has to survive a
closed tab. Almost all of that state is *derived* -- the questions answered, the games
sealed, the round finished, the lesson opened -- and derived state needs no table. Two
answers cannot be derived, and they are what ``journey_stages`` holds:

* a **skip**: nothing was recorded because the learner waved the step through, which is
  the point of a skip, so there is no other row that could say so;
* an explicit **done** for work that happened elsewhere.

``(student_id, stage)`` is unique: the row holds the learner's current answer, and
``updated_at`` says when it last changed. A second skip is an update, not a second row --
the same first-answer-wins shape ``baseline_trials``, ``subject_trials`` and the evidence
log use, so a flaky network cannot turn one tap into two states.

Declared in ``adapt/journey/models.py`` rather than ``db/models.py`` (that file is a
shared surface for the round); the import below registers it on ``Base.metadata``, which
is what makes ``alembic check`` compare against a metadata that knows about it.

Existence-aware, like 0003-0008: a database built by ``create_all`` from this build
already has the table, and ``stamp P2`` then ``upgrade head`` replays this onto it.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Registers journey_stages on Base.metadata for ``alembic check`` -- see the module docstring.
import adapt.journey.models  # noqa: F401

revision: str = '0009'
down_revision: str | None = 'P2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()

    if "journey_stages" not in existing:
        op.create_table(
            "journey_stages",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("stage", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("note", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("student_id", "stage", name="uq_journey_stages_student_stage"),
        )
        op.create_index("ix_journey_stages_student", "journey_stages", ["student_id", "updated_at"])


def downgrade() -> None:
    if "journey_stages" in _tables():
        op.drop_index("ix_journey_stages_student", table_name="journey_stages")
        op.drop_table("journey_stages")
