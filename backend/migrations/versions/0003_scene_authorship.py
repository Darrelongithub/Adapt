"""scene copies belong to the learner who made them

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 09:44:07.114280

Written by hand, and reviewed against the model it has to match.

What changes and why: ``lesson_scenes.spec_id`` was UNIQUE in 0002 because a scene is
content-addressed, and content-addressing looked like the right way to make startup seeding
idempotent. It was right about seeding and wrong about people. Two learners who both narrow a
binary search to the same eight numbers produce the *same content id*, so ``store_spec`` handed
the second learner the first learner's row: their "your copy" appeared in nobody's list, their
note about why they changed it was written onto a stranger's scene, and their run pointed at a
scene they could not see. The only way to see this was to run the player twice, which is what
the UI probe does.

So the rule becomes: one canonical row per content id for the shared scenes (builtin and
AI-authored, which have no author), and one row per learner for a derived copy. That is two
partial indexes rather than a column constraint.

Nothing is backfilled and no column changes, so an existing deployment upgrades in one DDL
statement pair. ``downgrade`` is the lossy direction and says so: the previous schema cannot
hold two rows for one content id, so the duplicate learner copies are deleted (their runs
cascade with them, exactly as deleting a scene always has).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # the old unique index by name; SQLite lets an index be dropped without rebuilding the table
    op.drop_index("ix_lesson_scenes_spec_id", table_name="lesson_scenes")
    op.create_index("ix_lesson_scenes_spec_id", "lesson_scenes", ["spec_id"], unique=False)
    op.create_index(
        "uq_scene_spec_shared",
        "lesson_scenes",
        ["spec_id"],
        unique=True,
        sqlite_where=sa.text("author_student_id IS NULL"),
        postgresql_where=sa.text("author_student_id IS NULL"),
    )
    op.create_index(
        "uq_scene_spec_author", "lesson_scenes", ["spec_id", "author_student_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_scene_spec_author", table_name="lesson_scenes")
    op.drop_index("uq_scene_spec_shared", table_name="lesson_scenes")
    # one row per content id again: keep the oldest copy of each scene and let its runs cascade
    op.execute(
        """
        DELETE FROM lesson_scenes
        WHERE id NOT IN (
            SELECT keep.id FROM lesson_scenes AS keep
            JOIN (
                SELECT spec_id, MIN(created_at) AS first_seen
                FROM lesson_scenes GROUP BY spec_id
            ) AS first_row
              ON first_row.spec_id = keep.spec_id AND first_row.first_seen = keep.created_at
        )
        """
    )
    op.drop_index("ix_lesson_scenes_spec_id", table_name="lesson_scenes")
    op.create_index("ix_lesson_scenes_spec_id", "lesson_scenes", ["spec_id"], unique=True)
