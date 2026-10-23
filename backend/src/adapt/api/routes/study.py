"""Learner-authored notes and flashcards. These are tools, not assessed evidence."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Student, StudyItem
from ..deps import db_session, rate, student_from_request

router = APIRouter(prefix="/api/v1/study", tags=["study"])


class StudyItemBody(BaseModel):
    kind: Literal["note", "card"]
    text: str = Field(min_length=1, max_length=4000)
    answer: str | None = Field(default=None, max_length=4000)


def _owned(s: Session, item_id: str, student: Student) -> StudyItem:
    row = s.get(StudyItem, item_id)
    if row is None or row.student_id != student.id:
        raise HTTPException(status_code=404, detail="unknown study item")
    return row


def _payload(row: StudyItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "text": row.text,
        "answer": row.answer,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/items")
def list_items(
    s: Session = Depends(db_session), student: Student = Depends(student_from_request)
) -> dict[str, Any]:
    rows = s.scalars(
        select(StudyItem).where(StudyItem.student_id == student.id).order_by(StudyItem.created_at.desc(), StudyItem.id)
    ).all()
    return {
        "notes": [_payload(row) for row in rows if row.kind == "note"],
        "cards": [_payload(row) for row in rows if row.kind == "card"],
    }


@router.post("/items")
def create_item(
    body: StudyItemBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    text = body.text.strip()
    answer = body.answer.strip() if body.answer is not None else None
    if not text or (body.kind == "card" and not answer):
        raise HTTPException(status_code=422, detail="a card needs both a front and an answer" if body.kind == "card" else "write a note first")
    if body.kind == "note" and answer is not None:
        raise HTTPException(status_code=422, detail="a note does not have a separate answer")
    row = StudyItem(student_id=student.id, kind=body.kind, text=text, answer=answer if body.kind == "card" else None)
    s.add(row)
    s.commit()
    s.refresh(row)
    return {"item": _payload(row)}


@router.delete("/items/{item_id}")
def delete_item(
    item_id: str,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    row = _owned(s, item_id, student)
    s.delete(row)
    s.commit()
    return {"deleted": True, "id": item_id}
