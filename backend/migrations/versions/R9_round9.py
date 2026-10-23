"""Round 9: the check-in, the syllabus approval gate, and the scan cache.

Revision ID: R9
Revises: P5

Three additive tables, existence-aware like every revision since 0003 (a
database built by ``create_all`` from this build already has them):

* ``learner_check_ins`` — one 10-second check-in: tiles, the learner's own
  one-line note (encrypted), the validated estimate and the code-computed
  metrics behind it.
* ``approved_guidelines`` — the learner's approved objective list per
  subject, versioned, plus at most one proposed (unanswered rescan) row.
* ``scan_cache`` — cached curriculum scans, encrypted, so a syllabus
  fetched once keeps working offline.

Declared in ``adapt/ai/monitor/models.py`` and ``adapt/curriculum/models.py``;
importing them below registers the tables on ``Base.metadata`` for
``alembic check``. Encrypted columns store TEXT either way (ciphertext when
field encryption is on, JSON text inside a SQLCipher file otherwise), so a
plain ``Text`` column matches the ORM on every deployment.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import adapt.ai.monitor.models  # noqa: F401
from adapt.crypto.fields import EncryptedJSON, EncryptedText
import adapt.curriculum.models  # noqa: F401

revision: str = "R9"
down_revision: str | None = "P5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()

    if "learner_check_ins" not in existing:
        op.create_table(
            "learner_check_ins",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("session_id", sa.String(length=120), nullable=True),
            sa.Column("subject_id", sa.String(length=120), nullable=True),
            sa.Column("context", sa.String(length=16), nullable=False),
            sa.Column("tiles", EncryptedJSON(context="check_in.tiles"), nullable=False),
            sa.Column("note", EncryptedText(context="check_in.note"), nullable=True),
            sa.Column("estimate", EncryptedJSON(context="check_in.estimate"), nullable=False),
            sa.Column("metrics", EncryptedJSON(context="check_in.metrics"), nullable=False),
            sa.Column("source", sa.String(length=8), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_learner_check_ins_student_id", "learner_check_ins", ["student_id"])
        op.create_index("ix_learner_check_ins_created_at", "learner_check_ins", ["created_at"])
        op.create_index("ix_check_ins_student_created", "learner_check_ins", ["student_id", "created_at"])
        op.create_index("ix_learner_check_ins_session_id", "learner_check_ins", ["session_id"])
        op.create_index("ix_learner_check_ins_subject_id", "learner_check_ins", ["subject_id"])

    if "approved_guidelines" not in existing:
        op.create_table(
            "approved_guidelines",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_id", sa.String(), sa.ForeignKey("learner_subjects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("curriculum", sa.String(length=24), nullable=True),
            sa.Column("grade", sa.String(length=24), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False),
            sa.Column("objectives", EncryptedJSON(context="guideline.objectives"), nullable=False),
            sa.Column("source_label", sa.String(length=120), nullable=False),
            sa.Column("attribution", sa.String(length=240), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("student_id", "subject_id", "version", "status", name="uq_guideline_version"),
        )
        op.create_index("ix_approved_guidelines_student_id", "approved_guidelines", ["student_id"])
        op.create_index("ix_approved_guidelines_subject_id", "approved_guidelines", ["subject_id"])
        op.create_index("ix_approved_guidelines_status", "approved_guidelines", ["status"])
        op.create_index("ix_approved_guidelines_created_at", "approved_guidelines", ["created_at"])
        op.create_index("ix_guidelines_student_subject", "approved_guidelines", ["student_id", "subject_id", "status"])

    if "scan_cache" not in existing:
        op.create_table(
            "scan_cache",
            sa.Column("key", sa.String(length=160), primary_key=True),
            sa.Column("payload", EncryptedJSON(context="scan_cache.payload"), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_scan_cache_expires_at", "scan_cache", ["expires_at"])


def downgrade() -> None:
    existing = _tables()
    if "scan_cache" in existing:
        op.drop_table("scan_cache")
    if "approved_guidelines" in existing:
        op.drop_table("approved_guidelines")
    if "learner_check_ins" in existing:
        op.drop_table("learner_check_ins")
