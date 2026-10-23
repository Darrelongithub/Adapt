"""A complete, credential-free export of one learner's own records.

Rows are discovered from the ORM rather than maintained as a second, easily-stale
list.  The database models decrypt protected fields on read.  Authentication
secrets and grading credentials are not learner data downloads: they are removed
recursively, including from JSON snapshots.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session

from ..db.models import AuditLog, Base, Student, utcnow

_OMIT = object()
_SECRET_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "pass_phrase",
        "pass_phrase_hash",
        "csrf",
        "csrf_token",
        "token",
        "token_hash",
        "grading_seal",
        "email_index",
        "username_index",
    }
)


def _clean(value: Any, key: str = "") -> Any:
    if key in _SECRET_KEYS or key.endswith("_hash") or key.endswith("_blind_index"):
        return _OMIT
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _clean(value.value)
    if isinstance(value, dict):
        result = {}
        for child_key, child in value.items():
            cleaned = _clean(child, str(child_key))
            if cleaned is not _OMIT:
                result[str(child_key)] = cleaned
        return result
    if isinstance(value, (list, tuple)):
        return [cleaned for child in value if (cleaned := _clean(child)) is not _OMIT]
    if isinstance(value, bytes):
        return _OMIT
    return str(value)


def _row(row: Any) -> dict[str, Any]:
    result = {}
    for attribute in inspect(row).mapper.column_attrs:
        cleaned = _clean(getattr(row, attribute.key), attribute.key)
        if cleaned is not _OMIT:
            result[attribute.key] = cleaned
    return result


def export_learner(s: Session, student: Student) -> dict[str, Any]:
    """Return every ORM row directly owned by ``student`` and their audit actions."""

    records: dict[str, list[dict[str, Any]]] = {}
    models = sorted(
        {
            mapper.class_
            for mapper in Base.registry.mappers
            if hasattr(mapper.class_, "__table__") and "student_id" in mapper.class_.__table__.c
        },
        key=lambda model: model.__tablename__,
    )
    for model in models:
        rows = s.scalars(select(model).where(model.student_id == student.id)).all()
        records[model.__tablename__] = [_row(row) for row in rows]

    records["audit_log"] = [
        _row(row) for row in s.scalars(select(AuditLog).where(AuditLog.actor_id == student.id).order_by(AuditLog.id)).all()
    ]
    return {
        "format": "adapt-learner-export",
        "format_version": 1,
        "exported_at": utcnow().isoformat(),
        "student": _row(student),
        "records": dict(sorted(records.items())),
        "record_counts": {table: len(rows) for table, rows in sorted(records.items())},
        "excluded": "Authentication credentials, lookup indexes, and grading seals are not included.",
    }
