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

from copy import deepcopy
from typing import Any, Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Student, new_id
from ...services import current
from ...subjects.catalog import subject_payload
from ...subjects.grading import GRADING_FIELDS, public_item, read_grading_token, score_answer
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
    grading_seal: str = Field(min_length=1, max_length=16_384)
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


class GradeBody(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1, max_length=64)
    grading_seal: str = Field(min_length=1, max_length=16_384)
    answer: str | None = Field(default=None, max_length=240)
    ms: int = Field(default=0, ge=0, le=600_000)
    hints_used: int = Field(default=0, ge=0, le=20)


def _llm() -> Any:
    svcs = current()
    return getattr(getattr(svcs, "ai2", None), "llm", None)


def _owned_subject(s: Session, student_id: str, subject_id: str) -> LearnerSubject:
    row = s.execute(
        select(LearnerSubject).where(LearnerSubject.id == subject_id, LearnerSubject.student_id == student_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_subject", "message": "no such subject"})
    return row


def _update_active_run(
    row: LearnerSubject | None,
    run_id: str,
    *,
    entry: dict[str, Any] | None = None,
    finish: bool = False,
) -> None:
    """Persist the server-graded resume snapshot inside the encrypted subject plan."""
    if row is None:
        return
    plan = deepcopy(row.plan or {})
    saved = plan.get("active_run")
    if not isinstance(saved, dict) or saved.get("run_id") != run_id:
        return
    items = [item for item in saved.get("items", []) if isinstance(item, dict)]
    evidence = [old for old in saved.get("evidence", []) if isinstance(old, dict)]
    if entry is not None and not any(old.get("item_id") == entry.get("item_id") for old in evidence):
        evidence.append(entry)
    order = {str(item.get("id")): index for index, item in enumerate(items)}
    evidence.sort(key=lambda old: order.get(str(old.get("item_id")), len(order)))
    answered = {str(old.get("item_id")) for old in evidence}
    next_index = next((index for index, item in enumerate(items) if str(item.get("id")) not in answered), max(0, len(items) - 1))
    saved["evidence"] = evidence
    saved["index"] = next_index
    if finish and items and len(evidence) == len(items):
        saved["step"] = "results"
    plan["active_run"] = saved
    row.plan = plan


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
    subject_row = None
    subject_text = body.subject.strip()
    row = None
    if body.subject_id:
        # An explicit id is an ownership claim: the newer main-branch
        # behaviour 404s an unknown or foreign subject instead of
        # silently planning a stateless test for it.
        row = _owned_subject(s, student.id, body.subject_id)
    if row is None:
        # No id supplied: still find a subject this learner has chosen
        # under the same words, so dropping the id is not a way around
        # the Round 9 approval gate.
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
        subject_row = row
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
    run_id = new_id("trun")
    plan_payload["run_id"] = run_id
    effective_subject_id = subject_row.id if subject_row is not None else body.subject_id
    plan_payload["items"] = [
        public_item(
            item,
            student_id=student.id,
            run_id=run_id,
            item_id=f"item-{index}",
            subject_id=effective_subject_id,
        )
        for index, item in enumerate(plan_payload["items"])
    ]
    if subject_row is not None:
        subject_row.plan = {
            **deepcopy(subject_row.plan or {}),
            "active_run": {
                "version": 1,
                "run_id": run_id,
                "step": "run",
                "level": body.level,
                "style": body.style,
                "goal": body.goal,
                "items": deepcopy(plan_payload["items"]),
                "index": 0,
                "evidence": [],
            },
        }
        s.commit()
    return plan_payload


@router.get("/run")
def read_active_run(
    subject_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(student_from_request),
) -> dict[str, Any]:
    row = _owned_subject(s, student.id, subject_id)
    saved = (row.plan or {}).get("active_run")
    if not isinstance(saved, dict) or not saved.get("run_id"):
        raise HTTPException(status_code=404, detail={"error": "no_active_run", "message": "no saved test run"})
    return {**deepcopy(saved), "chosen": subject_payload(row)}


@router.post("/grade")
def grade(
    body: GradeBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """Grade from the authenticated server-held item, never a browser verdict."""
    try:
        payload = read_grading_token(
            body.grading_seal, student_id=student.id, run_id=body.run_id, item_id=body.item_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "invalid_grading_token", "message": str(exc)}
        ) from None
    item = payload["item"]
    subject_id = payload.get("subject_id")
    subject_row = _owned_subject(s, student.id, subject_id) if subject_id else None
    existing = s.execute(
        select(SubjectTestEvidence).where(
            SubjectTestEvidence.run_id == body.run_id,
            SubjectTestEvidence.item_id == body.item_id,
            SubjectTestEvidence.student_id == student.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"correct": bool(existing.correct), "stored": 0, "run_id": body.run_id, "item_id": body.item_id}
    correct = score_answer(item, body.answer)
    parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
    visible_parameters = {key: value for key, value in parameters.items() if key not in GRADING_FIELDS}
    s.add(SubjectTestEvidence(
        student_id=student.id,
        subject_id=subject_id,
        run_id=body.run_id,
        item_id=body.item_id,
        item_type=str(item["item_type"]),
        skill=str(item["skill"]),
        difficulty=max(1, min(5, int(item["difficulty"]))),
        answer=(body.answer or "")[:240] or None,
        correct=correct,
        ms=body.ms,
        hints_used=body.hints_used,
        facts={"parameters": visible_parameters},
    ))
    _update_active_run(subject_row, body.run_id, entry={
        "item_id": body.item_id,
        "item_type": str(item["item_type"]),
        "skill": str(item["skill"]),
        "difficulty": max(1, min(5, int(item["difficulty"]))),
        "answer": (body.answer or "")[:240] or None,
        "correct": correct,
        "ms": body.ms,
        "hints_used": body.hints_used,
        "parameters": visible_parameters,
        "grading_seal": body.grading_seal,
    })
    s.commit()
    return {"correct": correct, "stored": 1, "run_id": body.run_id, "item_id": body.item_id}


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
    subject_row = _owned_subject(s, student.id, subject_id) if subject_id else None
    stored = 0
    for row in body.rows[:64]:
        try:
            payload = read_grading_token(
                row.grading_seal, student_id=student.id, run_id=body.run_id, item_id=row.item_id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "invalid_grading_seal", "message": str(exc)}
            ) from None
        if payload.get("subject_id") != subject_id:
            raise HTTPException(
                status_code=422,
                detail={"error": "subject_mismatch", "message": "the grading seal belongs to a different subject"},
            )
        item = payload["item"]
        exists = s.execute(
            select(SubjectTestEvidence.id).where(
                SubjectTestEvidence.run_id == body.run_id,
                SubjectTestEvidence.item_id == row.item_id,
                SubjectTestEvidence.student_id == student.id,
            )
        ).first()
        if exists is not None:
            continue
        parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        visible_parameters = {key: value for key, value in parameters.items() if key not in GRADING_FIELDS}
        correct = score_answer(item, row.answer)
        s.add(SubjectTestEvidence(
            student_id=student.id,
            subject_id=subject_id,
            run_id=body.run_id,
            item_id=row.item_id,
            item_type=str(item["item_type"]),
            skill=str(item["skill"]),
            difficulty=max(1, min(5, int(item["difficulty"]))),
            answer=(row.answer or "")[:240] or None,
            correct=correct,
            ms=row.ms,
            hints_used=row.hints_used,
            facts={"parameters": visible_parameters},
        ))
        _update_active_run(subject_row, body.run_id, entry={
            "item_id": row.item_id,
            "item_type": str(item["item_type"]),
            "skill": str(item["skill"]),
            "difficulty": max(1, min(5, int(item["difficulty"]))),
            "answer": (row.answer or "")[:240] or None,
            "correct": correct,
            "ms": row.ms,
            "hints_used": row.hints_used,
            "parameters": visible_parameters,
            "grading_seal": row.grading_seal,
        })
        stored += 1
    _update_active_run(subject_row, body.run_id, finish=True)
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
