"""Add real subject-test evidence rows (Round 6, Track B).

Revision ID: P5
Revises: P4

Each row is one scored interaction from a real test run: item, answer, correct,
ms, hints. The table is existence-aware: installations that received the ORM
table through create_all before this revision are left alone.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from adapt.crypto.fields import EncryptedJSON
import adapt.subjects.models  # noqa: F401  register the table for metadata checks

revision: str = "P5"
down_revision: str | None = "P4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "subject_test_evidence" in inspector.get_table_names():
        return
    op.create_table(
        "subject_test_evidence",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("item_type", sa.String(length=48), nullable=False),
        sa.Column("skill", sa.String(length=64), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("answer", sa.String(length=240), nullable=True),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("hints_used", sa.Integer(), nullable=False),
        sa.Column("facts", EncryptedJSON(context="subject_test_evidence.facts"), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "item_id", name="uq_subject_test_evidence_item"),
    )
    op.create_index("ix_subject_test_evidence_student_id", "subject_test_evidence", ["student_id"], unique=False)
    op.create_index("ix_subject_test_evidence_subject_id", "subject_test_evidence", ["subject_id"], unique=False)
    op.create_index("ix_subject_test_evidence_run_id", "subject_test_evidence", ["run_id"], unique=False)
    op.create_index("ix_subject_test_evidence_observed_at", "subject_test_evidence", ["observed_at"], unique=False)
    op.create_index("ix_subject_test_evidence_run", "subject_test_evidence", ["student_id", "run_id", "observed_at"], unique=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "subject_test_evidence" not in inspector.get_table_names():
        return
    for name in ("ix_subject_test_evidence_student_id", "ix_subject_test_evidence_subject_id", "ix_subject_test_evidence_run_id", "ix_subject_test_evidence_observed_at", "ix_subject_test_evidence_run"):
        indexes = {str(row["name"]) for row in inspector.get_indexes("subject_test_evidence")}
        if name in indexes:
            op.drop_index(name, table_name="subject_test_evidence")
    op.drop_table("subject_test_evidence")
