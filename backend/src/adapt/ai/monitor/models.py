"""The check-in's one table.

Declared beside its feature (like ``journey/models.py`` and
``subjects/models.py``) and imported at the bottom of ``db/models.py`` so
``Base.metadata`` knows it. The note and the estimate are encrypted
columns in the same store as everything else — there is no second store,
and deleting a learner cascades here like everywhere else.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ...crypto.fields import EncryptedJSON, EncryptedText
from ...db.models import Base, new_id, utcnow


class LearnerCheckIn(Base):
    """One check-in: the tiles the learner tapped and the estimate code made.

    ``estimate`` and ``metrics`` are stored together — an estimate without
    the evidence behind it would be a label, and this app does not label.
    ``note`` is the learner's own one line, kept only so they can read it
    back in "what we recorded about you".
    """

    __tablename__ = "learner_check_ins"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("chk"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(120), index=True)
    subject_id: Mapped[str | None] = mapped_column(String(120), index=True)
    context: Mapped[str] = mapped_column(String(16), default="other")
    tiles: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="check_in.tiles"), default=dict)
    note: Mapped[str | None] = mapped_column(EncryptedText(context="check_in.note"))
    estimate: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="check_in.estimate"), default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="check_in.metrics"), default=dict)
    #: "simple" (deterministic) or "model" (phrased by the model, validated by code).
    source: Mapped[str] = mapped_column(String(8), default="simple")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (Index("ix_check_ins_student_created", "student_id", "created_at"),)

    def as_dict(self, *, include_note: bool = True) -> dict[str, Any]:
        out = {
            "id": self.id,
            "context": self.context,
            "tiles": {k: v for k, v in dict(self.tiles or {}).items() if k in ("effort", "emotion", "help")},
            "estimate": dict(self.estimate or {}),
            "metrics": dict(self.metrics or {}),
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_note:
            out["note"] = self.note or ""
        return out


__all__ = ["LearnerCheckIn"]
