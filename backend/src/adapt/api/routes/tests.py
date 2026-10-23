"""Real subject tests over HTTP (Round 6, Track B).

Three endpoints:

* ``POST /api/v1/tests/plan``     — subject + level + style + goal -> a validated
  interactive test plan (Gemini proposes, code decides; deterministic without a key).
* ``POST /api/v1/tests/evidence`` — the scored rows of a test run (item, answer,
  correct, ms, hints). Idempotent per (run_id, item_id).
* ``GET  /api/v1/tests/evidence`` — read the rows back (dashboard, AI 1, Progress).

The plan's item types are the registry in ``adapt.subjects.test_items``; the
browser's registry mirrors it so anything here renders and scores over there.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Student
from ...services import current
from ...subjects.models import LearnerSubject, SubjectTestEvidence
from ...subjects.test_items import FAMILIES, build_plan
from ..deps import db_session, rate, student_from_request

router = APIRouter(prefix="/api/v1/tests", tags=["tests"])


class PlanBody(BaseModel):
    subject: str = Field(min_length=1, max_length=240)
    subject_id: str | None = None
    level: int = Field(default=2, ge=1, le=3)  # 1 beginner · 2 some knowledge · 3 confident
    style: str = Field(default="see", max_length=16)  # see|try|hear|read
    goal: str = Field(default="skills", max_length=16)  # exam|skills|curiosity
    #: Round 9 scope; the approved guideline's own values win when one exists.
    curriculum: str | None = Field(default=None, max_length=32)
    grade: str | None = Field(default=None, max_length=64)


class EvidenceRow(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    item_type: str = Field(min_length=1, max_length=48)
    skill: str = Field(min_length=1, max_length=64)
    difficulty: int = Field(default=3, ge=1, le=5)
    answer: str | None = Field(default=None, max_length=240)
    correct: bool = False
    ms: int = Field(default=0, ge=0, le=600_000)
    hints_used: int = Field(default=0, ge=0, le=20)
    parameters: dict[str, Any] = Field(default_factory=dict)


class EvidenceBody(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    subject_id: str | None = None
    rows: Sequence[EvidenceRow] = Field(default_factory=list)


def _llm() -> Any:
    svcs = current()
    return getattr(getattr(svcs, "ai2", None), "llm", None)


@router.post("/plan")
def plan(
    body: PlanBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """A validated interactive test plan. ``source`` is model or simple — never a provider name.

    Round 9 approval gate: for a subject this learner has *chosen* (a
    ``LearnerSubject`` row — found by id, or by the same words, so
    dropping the id is not a way around the gate), the plan may only run
    once the learner approved the syllabus checklist, and it then uses
    exactly the approved objective ids. A subject the learner has not
    chosen yet keeps the older stateless behaviour.
    """
    from ...curriculum import service as gate
    from ...curriculum.scanner import normalise_curriculum
    from ...subjects.resolve import normalise

    family = None
    subject_text = body.subject.strip()
    row = None
    if body.subject_id:
        row = s.execute(
            select(LearnerSubject).where(LearnerSubject.id == body.subject_id, LearnerSubject.student_id == student.id)
        ).scalar_one_or_none()
    if row is None:
        key = normalise(subject_text)
        if key:
            row = s.execute(
                select(LearnerSubject).where(LearnerSubject.student_id == student.id, LearnerSubject.normalised_text == key)
            ).scalar_one_or_none()
    objectives = None
    curriculum = normalise_curriculum(body.curriculum)
    grade = body.grade
    guideline = None
    if row is not None:
        family = row.family if row.family in FAMILIES else None
        subject_text = row.text
        guideline = gate.approved(s, student_id=student.id, subject_id=row.id)
        if guideline is None:
            raise HTTPException(
                status_code=422,
                detail={"error": "syllabus_not_approved", "message": gate.gate_message(row.title)},
            )
        objectives = [dict(o) for o in (guideline.objectives or []) if isinstance(o, dict)]
        curriculum = curriculum or guideline.curriculum
        grade = grade or guideline.grade
    plan_payload = build_plan(
        subject=subject_text,
        family=family,
        level=body.level,
        style=body.style,
        goal=body.goal,
        llm=_llm(),
        student_id=student.id,
        objectives=objectives,
        curriculum=curriculum,
        grade=grade,
    )
    if guideline is not None:
        plan_payload["guideline"] = {
            "version": int(guideline.version),
            "source_label": guideline.source_label,
            "attribution": guideline.attribution,
        }
    return plan_payload


@router.post("/evidence")
def write_evidence(
    body: EvidenceBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Store the scored rows of one test run. Idempotent per (run_id, item_id)."""
    if not body.rows:
        raise HTTPException(status_code=422, detail={"error": "no_rows", "message": "a test run must produce evidence rows"})
    subject_id = body.subject_id or None
    if subject_id:
        owned = s.execute(
            select(LearnerSubject.id).where(LearnerSubject.id == subject_id, LearnerSubject.student_id == student.id)
        ).first()
        if owned is None:
            raise HTTPException(status_code=404, detail={"error": "unknown_subject", "message": "no such subject"})
    stored = 0
    for row in body.rows[:64]:
        exists = s.execute(
            select(SubjectTestEvidence.id).where(
                SubjectTestEvidence.run_id == body.run_id,
                SubjectTestEvidence.item_id == row.item_id,
            )
        ).first()
        if exists is not None:
            continue
        s.add(SubjectTestEvidence(
            student_id=student.id,
            subject_id=subject_id,
            run_id=body.run_id,
            item_id=row.item_id,
            item_type=row.item_type,
            skill=row.skill,
            difficulty=max(1, min(5, int(row.difficulty))),
            answer=(row.answer or "")[:240] or None,
            correct=bool(row.correct),
            ms=max(0, int(row.ms)),
            hints_used=max(0, int(row.hints_used)),
            facts={"parameters": dict(row.parameters)},
        ))
        stored += 1
    s.commit()
    return {"stored": stored, "run_id": body.run_id}


@router.get("/evidence")
def read_evidence(
    subject_id: str | None = None,
    run_id: str | None = None,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    """The evidence rows this learner's test runs produced, newest first."""
    stmt = select(SubjectTestEvidence).where(SubjectTestEvidence.student_id == student.id)
    if subject_id:
        stmt = stmt.where(SubjectTestEvidence.subject_id == subject_id)
    if run_id:
        stmt = stmt.where(SubjectTestEvidence.run_id == run_id)
    rows = s.execute(stmt.order_by(SubjectTestEvidence.observed_at.desc()).limit(200)).scalars().all()
    return {
        "rows": [
            {
                "id": r.id,
                "run_id": r.run_id,
                "subject_id": r.subject_id,
                "item_id": r.item_id,
                "item_type": r.item_type,
                "skill": r.skill,
                "difficulty": r.difficulty,
                "answer": r.answer,
                "correct": bool(r.correct),
                "ms": r.ms,
                "hints_used": r.hints_used,
                "observed_at": r.observed_at.isoformat() if r.observed_at else None,
            }
            for r in rows
        ]
    }


__all__ = ["router"]
