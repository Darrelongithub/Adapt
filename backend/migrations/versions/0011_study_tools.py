"""Learner-authored notes and flashcards.

Revision ID: 0011
Revises: P5
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from adapt.crypto.fields import EncryptedText

import adapt.db.models  # noqa: F401

revision: str = "0011"
down_revision: str | None = "P5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "study_items" not in _tables():
        op.create_table(
            "study_items",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("text", EncryptedText(context="study.text"), nullable=False),
            sa.Column("answer", EncryptedText(context="study.answer"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint("kind IN ('note', 'card')", name="ck_study_items_kind"),
        )
        op.create_index("ix_study_items_student_id", "study_items", ["student_id"])
        op.create_index("ix_study_items_student_created", "study_items", ["student_id", "created_at"])


def downgrade() -> None:
    if "study_items" in _tables():
        op.drop_index("ix_study_items_student_created", table_name="study_items")
        op.drop_index("ix_study_items_student_id", table_name="study_items")
        op.drop_table("study_items")
