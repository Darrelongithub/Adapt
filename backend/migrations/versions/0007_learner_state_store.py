"""learner state store: the seven tables round brief section 2A needs

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13 10:40:00.000000

New tables, no changes to existing ones. That is deliberate on two counts: this store is *additive*
(``create_all`` cannot alter a table, so a store that needed new columns on ``items`` or ``attempts``
would only exist on a fresh clone), and the ownership rule in the brief is easier to keep when a
module's tables are its own — the evidence log is not an extra column on somebody else's record.

``state_evidence`` carries the unique ``(student_id, event_id)`` that makes ingestion idempotent;
``model_versions`` is keyed by ``(student_id, version)`` so a version is immutable by construction;
``content_packs`` is keyed by the content hash, so "generate once, reuse by id" is a primary-key
lookup; ``teaching_memory`` has the unique key that makes remembering something twice a no-op.

Existence-aware, like 0003–0006: a database built by ``create_all`` from this build already has the
tables, and ``stamp 0006`` then ``upgrade head`` replays this onto it.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from adapt.crypto.fields import EncryptedJSON, EncryptedText

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()

    if "state_evidence" not in existing:
        op.create_table(
            "state_evidence",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("event_id", sa.String(length=96), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("source_type", sa.String(length=16), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("topic_id", sa.String(), nullable=True),
            sa.Column("item_id", sa.String(), nullable=True),
            sa.Column("correct", sa.Boolean(), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("payload", EncryptedJSON(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("student_id", "event_id", name="uq_state_evidence_event"),
        )
        for name, cols in (
            ("ix_state_evidence_student_id", ["student_id"]),
            ("ix_state_evidence_session_id", ["session_id"]),
            ("ix_state_evidence_event_id", ["event_id"]),
            ("ix_state_evidence_kind", ["kind"]),
            ("ix_state_evidence_source_type", ["source_type"]),
            ("ix_state_evidence_topic_id", ["topic_id"]),
            ("ix_state_evidence_item_id", ["item_id"]),
            ("ix_state_evidence_occurred_at", ["occurred_at"]),
            ("ix_state_evidence_student_time", ["student_id", "occurred_at"]),
        ):
            op.create_index(name, "state_evidence", cols)

    if "model_versions" not in existing:
        op.create_table(
            "model_versions",
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("version", sa.Integer(), primary_key=True),
            sa.Column("evidence_upto", sa.String(), nullable=True),
            sa.Column("evidence_count", sa.Integer(), nullable=False),
            sa.Column("model", EncryptedJSON(), nullable=False),
            sa.Column("reason", sa.String(length=24), nullable=False),
            sa.Column("parent_version", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_model_versions_created_at", "model_versions", ["created_at"])
        op.create_index("ix_model_versions_student_created", "model_versions", ["student_id", "created_at"])

    if "content_packs" not in existing:
        op.create_table(
            "content_packs",
            sa.Column("id", sa.String(length=72), primary_key=True),
            sa.Column("topic_id", sa.String(), nullable=True),
            sa.Column("concept", sa.String(length=96), nullable=False),
            sa.Column("kind", sa.String(length=24), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("payload", EncryptedJSON(), nullable=False),
            sa.Column("validation", sa.String(length=16), nullable=False),
            sa.Column("validation_notes", sa.JSON(), nullable=True),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_content_packs_topic_id", "content_packs", ["topic_id"])
        op.create_index("ix_content_packs_concept", "content_packs", ["concept"])
        op.create_index("ix_content_packs_kind", "content_packs", ["kind"])
        op.create_index("ix_content_packs_validation", "content_packs", ["validation"])
        op.create_index("ix_content_packs_created_at", "content_packs", ["created_at"])
        op.create_index("ix_content_packs_concept_kind", "content_packs", ["concept", "kind"])

    if "teaching_memory" not in existing:
        op.create_table(
            "teaching_memory",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("kind", sa.String(length=24), nullable=False),
            sa.Column("ref", sa.String(length=96), nullable=False),
            sa.Column("text", EncryptedText(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("student_id", "session_id", "kind", "ref", name="uq_teaching_memory_entry"),
        )
        for name, cols in (
            ("ix_teaching_memory_student_id", ["student_id"]),
            ("ix_teaching_memory_session_id", ["session_id"]),
            ("ix_teaching_memory_kind", ["kind"]),
            ("ix_teaching_memory_created_at", ["created_at"]),
        ):
            op.create_index(name, "teaching_memory", cols)

    if "lesson_plans" not in existing:
        op.create_table(
            "lesson_plans",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("model_version", sa.Integer(), nullable=False),
            sa.Column("content_ids", sa.JSON(), nullable=True),
            sa.Column("plan", EncryptedJSON(), nullable=False),
            sa.Column("superseded_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        for name, cols in (
            ("ix_lesson_plans_student_id", ["student_id"]),
            ("ix_lesson_plans_session_id", ["session_id"]),
            ("ix_lesson_plans_created_at", ["created_at"]),
            ("ix_lesson_plans_student_created", ["student_id", "created_at"]),
        ):
            op.create_index(name, "lesson_plans", cols)

    if "deliveries" not in existing:
        op.create_table(
            "deliveries",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("plan_id", sa.String(), nullable=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("block", sa.String(length=48), nullable=False),
            sa.Column("content_id", sa.String(), nullable=True),
            sa.Column("seen", EncryptedJSON(), nullable=True),
            sa.Column("why", EncryptedJSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        for name, cols in (
            ("ix_deliveries_plan_id", ["plan_id"]),
            ("ix_deliveries_student_id", ["student_id"]),
            ("ix_deliveries_session_id", ["session_id"]),
            ("ix_deliveries_block", ["block"]),
            ("ix_deliveries_created_at", ["created_at"]),
        ):
            op.create_index(name, "deliveries", cols)

    if "session_states" not in existing:
        op.create_table(
            "session_states",
            sa.Column("session_id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("topic_id", sa.String(), nullable=True),
            sa.Column("block", sa.String(length=48), nullable=False),
            sa.Column("plan_id", sa.String(), nullable=True),
            sa.Column("pace", sa.Float(), nullable=False),
            sa.Column("fatigue", sa.Float(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_session_states_student_id", "session_states", ["student_id"])


def downgrade() -> None:
    for table in ("session_states", "deliveries", "lesson_plans", "teaching_memory", "content_packs", "model_versions", "state_evidence"):
        if table in _tables():
            op.drop_table(table)
