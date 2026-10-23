"""The approval gate's tables: guidelines and the scan cache.

``approved_guidelines`` is the learner's own "this is my syllabus"
decision — versioned, append-only in spirit (a new approval is a new
version, the old one stays readable), with at most one ``proposed`` row
per subject for a rescan the learner has not answered yet. Nothing
downstream may use objectives that are not in the latest approved
version for that subject.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto.fields import EncryptedJSON
from ..db.models import Base, new_id, utcnow


class ApprovedGuideline(Base):
    __tablename__ = "approved_guidelines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("gdl"))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("learner_subjects.id", ondelete="CASCADE"), index=True)
    curriculum: Mapped[str | None] = mapped_column(String(24))
    grade: Mapped[str | None] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer, default=1)
    #: approved (in force) | proposed (a rescan awaiting the learner's answer)
    status: Mapped[str] = mapped_column(String(12), default="approved", index=True)
    objectives: Mapped[list[dict[str, Any]]] = mapped_column(EncryptedJSON(context="guideline.objectives"), default=list)
    source_label: Mapped[str] = mapped_column(String(120), default="")
    attribution: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("student_id", "subject_id", "version", "status", name="uq_guideline_version"),
        Index("ix_guidelines_student_subject", "student_id", "subject_id", "status"),
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "curriculum": self.curriculum,
            "grade": self.grade,
            "version": self.version,
            "status": self.status,
            "objectives": [dict(o) for o in (self.objectives or [])],
            "source_label": self.source_label,
            "attribution": self.attribution,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ScanCacheEntry(Base):
    """One cached scan (or a learner's last candidate), encrypted.

    ``key`` is either a hash of (subject, curriculum, grade, sources) for
    shared reuse, or ``last:<student>:<subject>`` for the candidate the
    approval step must compare origins against.
    """

    __tablename__ = "scan_cache"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON(context="scan_cache.payload"), default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


__all__ = ["ApprovedGuideline", "ScanCacheEntry"]
