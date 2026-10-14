"""the subject round: rounds, trials, the pop-up method test and the subject map

Revision ID: P2
Revises: 0008
Create Date: 2026-10-13 12:40:00.000000

Four new tables for the subject round (round brief section 6). They are declared in
``adapt/subjects/models.py`` rather than ``db/models.py`` because that file is frozen for this
round; this revision is the one place that has to know about both.

``subject_trials`` carries the unique ``(run_id, task_id, index)`` that makes a re-sent trial one
row -- the same idempotency rule ``baseline_trials`` and the evidence log use, because a flaky
network must not turn one tap into two measurements. ``method_test_runs`` is its own table rather
than a stage on ``subject_rounds`` because the next-day recheck has to be findable by the learner
without replaying the round that produced it, and because a pop-up lesson can be offered outside a
round at all (``run_id`` is nullable for exactly that case).

The import of ``adapt.subjects.models`` below is not decoration. Alembic's ``target_metadata`` in
``env.py`` is ``adapt.db.models.Base.metadata``, which is a *live* object: importing the module that
declares these tables registers them on it, and that has to have happened before autogenerate
compares the database to the models. Without it ``alembic check`` sees four tables the models never
declared and proposes dropping them -- which is the mirror image of the defect ``check`` exists to
catch. ``env.py`` importing the module directly would be the cleaner home for this line (noted in
``agent-notes/2-subject-round.md``); putting it here makes the chain correct on its own.

Existence-aware, like 0003-0008: a database built by ``create_all`` from this build already has the
tables, and ``stamp 0008`` then ``upgrade head`` replays this onto it.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from adapt.crypto.fields import EncryptedJSON

# Registers subject_rounds/subject_trials/method_test_runs/subject_snapshots on Base.metadata for
# ``alembic check`` -- see the module docstring.
import adapt.subjects.models  # noqa: F401

revision: str = 'P2'
down_revision: str | None = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()

    if "subject_rounds" not in existing:
        op.create_table(
            "subject_rounds",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("topic_key", sa.String(length=96), nullable=False),
            sa.Column("topic_title", sa.String(length=160), nullable=False),
            sa.Column("family", sa.String(length=24), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=False),
            sa.Column("stage", sa.String(length=24), nullable=True),
            sa.Column("resolved", EncryptedJSON(), nullable=False),
            sa.Column("leanings", EncryptedJSON(), nullable=False),
            sa.Column("plan", EncryptedJSON(), nullable=False),
            sa.Column("completion", EncryptedJSON(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_subject_rounds_student", "subject_rounds", ["student_id", "started_at"])

    if "subject_trials" not in existing:
        op.create_table(
            "subject_trials",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("run_id", sa.String(), sa.ForeignKey("subject_rounds.id", ondelete="CASCADE"), nullable=False),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("stage", sa.String(length=24), nullable=False),
            sa.Column("skill", sa.String(length=48), nullable=True),
            sa.Column("node_id", sa.String(length=64), nullable=True),
            sa.Column("sub_area", sa.String(length=64), nullable=True),
            sa.Column("rung", sa.Integer(), nullable=True),
            sa.Column("method", sa.String(length=24), nullable=True),
            sa.Column("concept", sa.String(length=64), nullable=True),
            sa.Column("task_id", sa.String(length=96), nullable=False),
            sa.Column("index", sa.Integer(), nullable=False),
            sa.Column("phase", sa.String(length=12), nullable=False),
            sa.Column("correct", sa.Boolean(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("wager", sa.Integer(), nullable=True),
            sa.Column("task", EncryptedJSON(), nullable=False),
            sa.Column("response", EncryptedJSON(), nullable=False),
            sa.Column("score", EncryptedJSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", "task_id", "index", name="uq_subject_trial_task"),
        )
        op.create_index("ix_subject_trials_run", "subject_trials", ["run_id", "index"])
        op.create_index("ix_subject_trials_student_stage", "subject_trials", ["student_id", "stage"])

    if "method_test_runs" not in existing:
        op.create_table(
            "method_test_runs",
            sa.Column("id", sa.String(), primary_key=True),
            # Nullable: a pop-up lesson can be offered outside a subject round, and the recheck is
            # found by (student_id, topic_key) rather than by replaying a round that may be gone.
            sa.Column("run_id", sa.String(), sa.ForeignKey("subject_rounds.id", ondelete="SET NULL"), nullable=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("topic_key", sa.String(length=96), nullable=False),
            sa.Column("pair", sa.String(length=64), nullable=False),
            sa.Column("cell", sa.Integer(), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("assignment", EncryptedJSON(), nullable=False),
            sa.Column("legs", EncryptedJSON(), nullable=False),
            sa.Column("results", EncryptedJSON(), nullable=False),
            sa.Column("recheck", EncryptedJSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("recheck_at", sa.DateTime(), nullable=True),
            sa.Column("recheck_done_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_method_tests_student", "method_test_runs", ["student_id", "created_at"])

    if "subject_snapshots" not in existing:
        op.create_table(
            "subject_snapshots",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("run_id", sa.String(), sa.ForeignKey("subject_rounds.id", ondelete="SET NULL"), nullable=True),
            sa.Column("topic_key", sa.String(length=96), nullable=False),
            sa.Column("topic_title", sa.String(length=160), nullable=False),
            sa.Column("family", sa.String(length=24), nullable=False),
            # Without the taxonomy version a rating of 3 is a number with no scale behind it.
            sa.Column("taxonomy_version", sa.String(length=48), nullable=False),
            sa.Column("skills", EncryptedJSON(), nullable=False),
            sa.Column("leanings", EncryptedJSON(), nullable=False),
            sa.Column("ladder", EncryptedJSON(), nullable=False),
            sa.Column("method_test", EncryptedJSON(), nullable=False),
            sa.Column("completion", EncryptedJSON(), nullable=False),
            sa.Column("taken_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", "topic_key", name="uq_subject_snapshot_run"),
        )
        op.create_index(
            "ix_subject_snapshots_student", "subject_snapshots", ["student_id", "topic_key", "taken_at"]
        )


def downgrade() -> None:
    # Reverse order: the child tables first, so nothing is left pointing at a dropped parent.
    for table in ("subject_snapshots", "method_test_runs", "subject_trials", "subject_rounds"):
        if table in _tables():
            op.drop_table(table)
