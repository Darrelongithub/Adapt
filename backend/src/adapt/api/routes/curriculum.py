"""Curriculum scan + the syllabus approval gate over HTTP (Round 9).

* ``POST /api/v1/curriculum/scan``             — find what the curriculum requires.
* ``POST /api/v1/curriculum/approve``          — "Use this plan" (the gate's key turn).
* ``POST /api/v1/curriculum/reject-proposal``  — decline a rescanned list.
* ``GET  /api/v1/curriculum/guideline``        — the approved list (+ any proposal).

A scan never changes the guideline by itself: when one already exists
and the fresh list differs, the fresh list is stored as a proposal the
learner answers here. Origins stay the server's to state — a client
cannot label its own objectives "from syllabus" (see
``curriculum.service._with_origins``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...curriculum import scanner as SCAN
from ...curriculum import service as GATE
from ...db.models import Student
from ...services import current
from ...subjects.models import LearnerSubject
from ..deps import db_session, rate, student_from_request

router = APIRouter(prefix="/api/v1/curriculum", tags=["curriculum"])


class ScanBody(BaseModel):
    subject_id: str = Field(min_length=1, max_length=120)
    curriculum: str | None = Field(default=None, max_length=24)
    grade: str | None = Field(default=None, max_length=24)
    goal: str | None = Field(default=None, max_length=24)
    #: "Skip": no source is consulted; objectives are proposed instead and
    #: stay labelled as not checked. The learner still approves them.
    skip_sources: bool = False


class ObjectiveBody(BaseModel):
    id: str | None = Field(default=None, max_length=96)
    title: str = Field(min_length=1, max_length=160)
    strand: str = Field(default="", max_length=80)


class ApproveBody(BaseModel):
    subject_id: str = Field(min_length=1, max_length=120)
    objectives: list[ObjectiveBody] = Field(min_length=1, max_length=30)
    curriculum: str | None = Field(default=None, max_length=24)
    grade: str | None = Field(default=None, max_length=24)


class RejectBody(BaseModel):
    subject_id: str = Field(min_length=1, max_length=120)


def _subject(s: Session, student_id: str, subject_id: str) -> LearnerSubject:
    row = s.execute(
        select(LearnerSubject).where(LearnerSubject.id == subject_id, LearnerSubject.student_id == student_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_subject", "message": "no such subject"})
    return row


def _spec(row: LearnerSubject) -> tuple[str | None, str | None]:
    spec = (row.plan or {}).get("curriculum_spec") or {}
    if isinstance(spec, dict):
        return spec.get("curriculum"), spec.get("grade")
    return None, None


def _llm() -> Any:
    svcs = current()
    return getattr(getattr(svcs, "ai2", None), "llm", None)


@router.post("/scan")
def scan(
    body: ScanBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    svcs = current()
    row = _subject(s, student.id, body.subject_id)
    spec_curr, spec_grade = _spec(row)
    curriculum = SCAN.normalise_curriculum(body.curriculum or spec_curr)
    grade = (body.grade or spec_grade or "").strip()[:24] or None
    settings = svcs.settings
    fingerprint = "oak" if str(getattr(settings, "oak_api_key", "") or "") else "nooak"
    fingerprint += "+wiki" if getattr(settings, "connector_wikimedia_enabled", False) else ""
    fingerprint += "+skip" if body.skip_sources else ""
    key = GATE.scan_cache_key(subject=row.text, curriculum=curriculum, grade=grade, sources_fingerprint=fingerprint)
    cached = None if body.skip_sources else GATE.cached_scan(s, key)
    if cached and cached.get("objectives"):
        result = SCAN.ScanResult(
            subject=str(cached.get("subject") or row.text),
            curriculum=cached.get("curriculum"),
            grade=cached.get("grade"),
            objectives=list(cached.get("objectives") or []),
            source=str(cached.get("source") or "simple"),
            source_label=str(cached.get("source_label") or ""),
            attribution=str(cached.get("attribution") or ""),
            licence=str(cached.get("licence") or ""),
            checked=bool(cached.get("checked")),
            from_cache=True,
            notes=list(cached.get("notes") or []),
        )
    else:
        result = SCAN.scan(
            subject=row.text,
            curriculum=curriculum,
            grade=grade,
            settings=settings,
            cache_session=s,
            llm=_llm(),
            student_id=student.id,
            goal=body.goal,
            skip_sources=body.skip_sources,
        )
        if not body.skip_sources:
            GATE.store_scan(s, key, result)
    GATE.remember_candidate(s, student_id=student.id, subject_id=row.id, result=result)
    proposal = GATE.note_rescan(s, student_id=student.id, subject_id=row.id, result=result)
    guideline = GATE.approved(s, student_id=student.id, subject_id=row.id)
    s.commit()
    out = result.as_dict()
    out["subject_id"] = row.id
    out["approved_version"] = int(guideline.version) if guideline else None
    out["proposal_pending"] = proposal is not None
    return out


@router.post("/approve")
def approve(
    body: ApproveBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    row = _subject(s, student.id, body.subject_id)
    try:
        guideline = GATE.approve(
            s,
            student_id=student.id,
            subject_id=row.id,
            objectives_raw=[o.model_dump() for o in body.objectives],
            curriculum=body.curriculum,
            grade=body.grade,
        )
    except GATE.GuidelineError as exc:
        raise HTTPException(status_code=422, detail={"error": "guideline_refused", "message": str(exc)}) from exc
    s.commit()
    return {"guideline": guideline.as_dict()}


@router.post("/reject-proposal")
def reject(
    body: RejectBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    row = _subject(s, student.id, body.subject_id)
    gone = GATE.reject_proposal(s, student_id=student.id, subject_id=row.id)
    s.commit()
    return {"rejected": gone}


@router.get("/guideline")
def guideline(
    subject_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    row = _subject(s, student.id, subject_id)
    current_row = GATE.approved(s, student_id=student.id, subject_id=row.id)
    pending = GATE.proposed(s, student_id=student.id, subject_id=row.id)
    return {
        "subject_id": row.id,
        "approved": current_row.as_dict() if current_row else None,
        "proposed": pending.as_dict() if pending else None,
    }


__all__ = ["router"]
