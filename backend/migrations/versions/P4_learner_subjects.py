"""Add the learner-facing subject/course spine.

Revision ID: P4
Revises: P3

The older subject-round tables remain the evidence ledger.  This additive table stores the
learner's chosen subjects, their validated test plan, active subject, and progress so multiple
subjects can resume without re-asking a model.  The operation is existence-aware for installations
that received the ORM table through create_all before this revision was shipped.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from adapt.crypto.fields import EncryptedJSON
import adapt.subjects.models  # noqa: F401  register the table for metadata checks

revision: str = "P4"
down_revision: str | None = "P3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "learner_subjects" in inspector.get_table_names():
        return
    op.create_table(
        "learner_subjects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("text", sa.String(length=240), nullable=False),
        sa.Column("normalised_text", sa.String(length=240), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("plan", EncryptedJSON(), nullable=False),
        sa.Column("progress", EncryptedJSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", "normalised_text", name="uq_learner_subject_text"),
    )
    op.create_index("ix_learner_subjects_active", "learner_subjects", ["active"], unique=False)
    op.create_index("ix_learner_subjects_student", "learner_subjects", ["student_id", "updated_at"], unique=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "learner_subjects" not in inspector.get_table_names():
        return
    indexes = {str(row["name"]) for row in inspector.get_indexes("learner_subjects")}
    for name in ("ix_learner_subjects_student", "ix_learner_subjects_active"):
        if name in indexes:
            op.drop_index(name, table_name="learner_subjects")
    op.drop_table("learner_subjects")
