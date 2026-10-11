"""Direct audit-log writes for non-HTTP callers (CLI, services, background jobs).

The HTTP layer has its own request-scoped queue in ``security.middleware`` -- that
one must never block a learner's turn, so it defers. This module is for code that
already holds a session and is *changing something persistent*: an adaptation being
turned on, a history row being purged. Those need the record written in the same
commit, not queued.

Its own module so that ``services.adaptations`` can import it without pulling in
``security.middleware`` (which needs FastAPI). Every audit write goes through here
so there is one place that decides what shape an audit record has -- and one thing
for the tests to check.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import AuditLog


def record_audit(
    s: Session,
    *,
    actor_id: str | None,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    outcome: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    if not get_settings().audit_log_enabled:
        return
    s.add(
        AuditLog(
            actor_id=actor_id,
            action=action[:64],
            object_type=object_type,
            object_id=object_id,
            outcome=outcome[:16],
            detail=detail,
        )
    )
    s.commit()


def recent(s: Session, *, actor_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    from sqlalchemy import select

    conds = []
    if actor_id:
        conds.append(AuditLog.actor_id == actor_id)
    rows = s.execute(select(AuditLog).where(*conds).order_by(AuditLog.ts.desc()).limit(limit)).scalars().all()
    return [
        {
            "ts": r.ts.isoformat(),
            "actor_id": r.actor_id,
            "action": r.action,
            "object": f"{r.object_type}:{r.object_id}" if r.object_type else None,
            "outcome": r.outcome,
            "detail": r.detail or {},
        }
        for r in rows
    ]


__all__ = ["record_audit", "recent"]
