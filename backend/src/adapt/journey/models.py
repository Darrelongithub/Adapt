"""The one table this package owns: a learner's explicit answer about a stage.

Declared here rather than in ``db/models.py`` for the same reason the subject round's
four tables live in ``adapt/subjects/models.py``: that file is a shared surface, and a
round-2 workstream adding columns to it is how two workstreams collide. ``db/models.py``
imports this module at the bottom, which is what puts ``journey_stages`` on
``Base.metadata`` so ``create_all`` builds it and ``alembic check`` can see it.

Why a table at all, when every stage is derived? Because two things cannot be derived:
a **skip** (nothing was recorded because the learner waved it through) and an explicit
"I have done this" for work that happened outside the app. Both are the learner's own
statements. Everything else stays derived, and :mod:`adapt.journey.state` prefers the
evidence when the two disagree -- a stage that says ``skipped`` and then has a sealed
baseline behind it is ``done``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.models import Base, new_id, utcnow


class JourneyStage(Base):
    """One learner's answer about one stage of the path.

    ``(student_id, stage)`` is unique: a learner who skips a stage twice wastes a day's
    typing, not a second row, and the first-answer-wins rule that runs through the rest
    of this app (evidence, trials, attempts) holds here too. The row keeps the *current*
    answer, with ``updated_at`` saying when it last changed -- because "I have answered
    this question now" is a correction, not a duplicate.
    """

    __tablename__ = "journey_stages"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: new_id("jrn")
    )
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    #: One of ``stages.STAGES``. Checked in code rather than by a DB constraint, because
    #: SQLite cannot alter a check once a database exists (see migration 0009).
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    #: ``done`` or ``skipped``. Never ``todo``: absence of a row *is* "todo".
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Set when a stage is skipped, and left empty otherwise. A short phrase from the
    #: client about *why* -- kept for the learner, never shown to anyone else.
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("student_id", "stage", name="uq_journey_stages_student_stage"),
        Index("ix_journey_stages_student", "student_id", "updated_at"),
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "updated_at": (
                (self.updated_at or self.created_at).isoformat()
                if (self.updated_at or self.created_at)
                else None
            ),
        }


__all__ = ["JourneyStage"]
