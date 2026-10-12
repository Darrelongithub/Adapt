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

Both directions are existence-aware, and that is not decoration. A fresh clone gets its schema
from ``Base.metadata.create_all`` at startup, which already builds the indexes below from
``LessonScene.__table__``; such a database has no ``alembic_version`` row, and the documented way
to bring it into the chain (``stamp 0002``, then ``upgrade head``) would otherwise replay this
revision against the tables that already match it and die with ``index uq_scene_spec_shared already
exists`` -- on the one path the README tells every new operator to take.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "lesson_scenes"
SHARED_WHERE = "author_student_id IS NULL"


def _indexes() -> dict[str, dict]:
    """What is actually on the table right now, whoever put it there (this chain or create_all).

    ``column_names`` and ``unique`` are the two properties that can disagree with the target: a
    ``create_all`` database already has all three indexes with the right shape, while a
    pre-revision database has one unique ``ix_lesson_scenes_spec_id`` and none of the others. A
    partial index's ``WHERE`` clause is not reported by SQLite's inspector, so the pair below is
    matched by name and shape -- which is enough to tell those two situations apart, the only two
    that occur.
    """
    return {i["name"]: i for i in sa.inspect(op.get_bind()).get_indexes(TABLE)}


def _ensure(name: str, columns: list[str], *, unique: bool, partial: bool = False) -> None:
    have = _indexes().get(name)
    right = (
        have is not None
        and list(have.get("column_names") or []) == columns
        and bool(have.get("unique")) == unique
    )
    if right:
        return
    if have is not None:
        op.drop_index(name, table_name=TABLE)
    if partial:
        op.create_index(
            name,
            TABLE,
            columns,
            unique=unique,
            sqlite_where=sa.text(SHARED_WHERE),
            postgresql_where=sa.text(SHARED_WHERE),
        )
    else:
        op.create_index(name, TABLE, columns, unique=unique)


def upgrade() -> None:
    # the old unique index by name; SQLite lets an index be dropped without rebuilding the table
    _ensure("ix_lesson_scenes_spec_id", ["spec_id"], unique=False)
    _ensure("uq_scene_spec_shared", ["spec_id"], unique=True, partial=True)
    _ensure("uq_scene_spec_author", ["spec_id", "author_student_id"], unique=True)


def downgrade() -> None:
    for name in ("uq_scene_spec_author", "uq_scene_spec_shared"):
        if name in _indexes():
            op.drop_index(name, table_name=TABLE)
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
    # back to one row per content id: the plain index becomes the unique one again
    _ensure("ix_lesson_scenes_spec_id", ["spec_id"], unique=True)
