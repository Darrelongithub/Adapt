"""the baseline: runs, trials and the mind snapshot

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-13 11:05:00.000000

Three new tables for the mind baseline (round brief section 5). ``baseline_trials`` carries the
unique ``(run_id, task_id, index)`` that makes a re-sent trial one row — the same idempotency rule
the evidence log uses, because a flaky network must not turn one tap into two measurements.

Existence-aware, like 0003–0007: a database built by ``create_all`` from this build already has the
tables, and ``stamp 0007`` then ``upgrade head`` replays this onto it.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from adapt.crypto.fields import EncryptedJSON

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()

    if "baseline_runs" not in existing:
        op.create_table(
            "baseline_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=False),
            sa.Column("format_pick", sa.String(length=24), nullable=True),
            sa.Column("stage", sa.String(length=32), nullable=True),
            sa.Column("completion", EncryptedJSON(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_baseline_runs_student", "baseline_runs", ["student_id", "started_at"])

    if "baseline_trials" not in existing:
        op.create_table(
            "baseline_trials",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("run_id", sa.String(), sa.ForeignKey("baseline_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("game", sa.String(length=32), nullable=False),
            sa.Column("task_id", sa.String(length=64), nullable=False),
            sa.Column("index", sa.Integer(), nullable=False),
            sa.Column("phase", sa.String(length=12), nullable=False),
            sa.Column("correct", sa.Boolean(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("wager", sa.Integer(), nullable=True),
            sa.Column("task", EncryptedJSON(), nullable=False),
            sa.Column("response", EncryptedJSON(), nullable=False),
            sa.Column("score", EncryptedJSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", "task_id", "index", name="uq_baseline_trial_task"),
        )
        op.create_index("ix_baseline_trials_run", "baseline_trials", ["run_id", "index"])
        op.create_index("ix_baseline_trials_student_game", "baseline_trials", ["student_id", "game"])

    if "mind_snapshots" not in existing:
        op.create_table(
            "mind_snapshots",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("student_id", sa.String(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("run_id", sa.String(), sa.ForeignKey("baseline_runs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("factors", EncryptedJSON(), nullable=False),
            sa.Column("levers", EncryptedJSON(), nullable=False),
            sa.Column("completion", EncryptedJSON(), nullable=False),
            sa.Column("taken_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_mind_snapshots_student", "mind_snapshots", ["student_id", "taken_at"])


def downgrade() -> None:
    for table in ("mind_snapshots", "baseline_trials", "baseline_runs"):
        if table in _tables():
            op.drop_table(table)
