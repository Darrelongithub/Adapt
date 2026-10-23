"""Durable subject choices for the learner-facing product flow.

The original subject-round service is deliberately kept as the measurement engine.  This module
adds the small course spine around it: choose a subject, persist its checked plan, activate one
subject, and report several subjects without leaking implementation details to the learner.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import AdaptationProposal
from ..services.sessions import due_reviews
from .models import LearnerSubject
from .resolve import normalise
from .taxonomy import SKILL_IDS


def _level(rating: Any, hint: str | None = None) -> int:
    """Turn a bounded leaning into a stable, learner-safe difficulty level (1..4)."""
    try:
        value = int(rating)
    except (TypeError, ValueError):
        value = 1
    base = max(1, min(4, value + 1))
    if hint in {"primary", "beginner"}:
        return max(1, base - 1)
    if hint in {"tertiary", "advanced", "advanced_secondary"}:
        return min(4, base + 1)
    return base


def _simple_plan(svc: Any, text: str, student_id: str) -> dict[str, Any]:
    """Ask the existing constrained placement/ladder code for a plan, then flatten its public bits."""
    raw = svc.plan_tests(text, student_id=student_id)
    topic = raw.get("topic") or {}
    hint = topic.get("level_hint")
    rows = list(raw.get("tested") or [])[:6]
    tests = [
        {
            "id": str(row.get("id") or row.get("skill") or "").strip(),
            "title": str(row.get("title") or row.get("id") or "Check").strip(),
            "why": str(row.get("reason") or row.get("measures") or "This is useful for this subject.").strip()[:220],
            "level": _level(row.get("rating"), hint),
        }
        for row in rows
        if str(row.get("id") or row.get("skill") or "").strip()
        and str(row.get("id") or row.get("skill") or "").strip() in SKILL_IDS
    ]
    # A malformed internal plan must never become a learner-facing empty or duplicated plan.
    seen: set[str] = set()
    tests = [row for row in tests if not (row["id"] in seen or seen.add(row["id"]))]
    if not tests:
        tests = [{"id": "general.recall", "title": "Recall", "why": "A short starting check.", "level": 1}]
    placement = raw.get("placement") if isinstance(raw.get("placement"), dict) else {}
    sources = placement.get("sources") if isinstance(placement.get("sources"), dict) else {}
    source = "model" if any(str(value) == "model" for value in (raw.get("leanings_source"), sources.get("placement"), sources.get("ratings"))) else "simple"
    return {
        "tests": tests[:6],
        "source": source,
        "detail": raw,
        "family": str(topic.get("family") or "generic"),
        "title": str(topic.get("title") or text.strip().title())[:160],
    }


def subject_payload(row: LearnerSubject) -> dict[str, Any]:
    progress = dict(row.progress or {})
    plan = dict(row.plan or {})
    tests = list(plan.get("tests") or [])
    detail = plan.get("detail") if isinstance(plan.get("detail"), dict) else {}
    topic = detail.get("topic") if isinstance(detail.get("topic"), dict) else {}
    # ``topic_id`` is an authored bank id, never learner text. The practice queue can therefore
    # stay subject-specific without asking the browser to resolve or invent a curriculum id.
    topic_id = topic.get("bank_topic_id") or topic.get("topic_id")
    next_step = None if row.stage == "done" else progress.get("next") or {
        "label": "Start the subject checks",
        "href": "/journey?step=subject",
    }
    return {
        "id": row.id,
        "text": row.text,
        "title": row.title,
        "family": row.family,
        "topic_id": str(topic_id) if topic_id else None,
        "source": row.source,
        "stage": row.stage,
        "active": bool(row.active),
        "plan": {"tests": tests, "source": row.source},
        "curriculum_spec": (plan.get("curriculum_spec") if isinstance(plan.get("curriculum_spec"), dict) else None),
        "progress": {
            "done": int(progress.get("done", 0)),
            "total": int(progress.get("total", len(tests))),
        },
        "next": next_step,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def choose(
    s: Session,
    svc: Any,
    *,
    student_id: str,
    text: str,
    curriculum: str | None = None,
    grade: str | None = None,
) -> dict[str, Any]:
    clean = " ".join(str(text or "").split())[:240]
    key = normalise(clean)
    if not key:
        raise ValueError("type a subject or topic first")
    # Round 9 wizard spec: which syllabus the learner means, in their own
    # tap. Stored with the (encrypted) plan; the scanner and the approval
    # gate read it from here so the choice is made once.
    spec = {
        "curriculum": str(curriculum)[:24] if curriculum else None,
        "grade": " ".join(str(grade or "").split())[:24] or None,
    }
    existing = s.execute(
        select(LearnerSubject).where(LearnerSubject.student_id == student_id, LearnerSubject.normalised_text == key)
    ).scalar_one_or_none()
    if existing is not None:
        if curriculum is not None or grade is not None:
            plan = dict(existing.plan or {})
            plan["curriculum_spec"] = spec
            existing.plan = plan
            s.commit()
        return subject_payload(existing) | {"cached": True}

    built = _simple_plan(svc, clean, student_id)
    # Choosing is the act that puts the subject in the dashboard. Activation remains a separate
    # endpoint so a switch never happens by accident, but a new subject is active when it is the
    # learner's first one.
    has_active = s.execute(
        select(LearnerSubject.id).where(LearnerSubject.student_id == student_id, LearnerSubject.active.is_(True)).limit(1)
    ).first() is not None
    row = LearnerSubject(
        student_id=student_id,
        text=clean,
        normalised_text=key,
        title=built["title"],
        family=built["family"],
        source=built["source"],
        active=not has_active,
        stage="tests",
        plan={"tests": built["tests"], "source": built["source"], "detail": built.get("detail"), "curriculum_spec": spec},
        progress={"done": 0, "total": len(built["tests"]), "next": {"label": "Start the subject checks", "href": "/journey?step=subject"}},
    )
    s.add(row)
    s.flush()
    payload = subject_payload(row)
    # the row id is only known after flush; replace the temporary href in the persisted JSON too
    payload["next"] = {"label": "Start the subject checks", "href": "/journey?step=subject"}
    row.progress = {"done": 0, "total": len(built["tests"]), "next": payload["next"]}
    s.commit()
    return payload


def overview(s: Session, student_id: str) -> dict[str, Any]:
    rows = s.execute(
        select(LearnerSubject).where(LearnerSubject.student_id == student_id).order_by(LearnerSubject.updated_at.desc())
    ).scalars().all()
    subjects = [subject_payload(row) for row in rows]
    active = next((row["id"] for row in subjects if row["active"]), None)
    # These counters are deliberately derived from the same rows as the cards. They do not claim
    # a review is due merely because a subject exists.
    due_count = len(due_reviews(s, student_id))
    proposal_count = int(s.execute(
        select(func.count()).select_from(AdaptationProposal).where(
            AdaptationProposal.student_id == student_id,
            AdaptationProposal.status == "pending",
        )
    ).scalar_one())
    return {
        "active_subject_id": active,
        "subjects": [
            {
                "id": row["id"], "title": row["title"], "family": row["family"], "stage": row["stage"],
                "progress": row["progress"], "next": row["next"],
            }
            for row in subjects
        ],
        "due": {"count": due_count},
        "proposals": {"count": proposal_count},
    }


def complete_tests(s: Session, student_id: str, subject_id: str, completed_ids: list[str]) -> dict[str, Any]:
    row = s.execute(
        select(LearnerSubject).where(LearnerSubject.id == subject_id, LearnerSubject.student_id == student_id)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("no such subject")
    tests = list((row.plan or {}).get("tests") or [])
    allowed = {str(test.get("id")) for test in tests if isinstance(test, dict)}
    done_ids = [item for item in completed_ids if item in allowed]
    done = len(set(done_ids))
    total = len(tests)
    row.progress = {"done": done, "total": total, "next": {"label": "Open the first lesson", "href": f"/first-lesson?subject={row.id}"}}
    row.stage = "done" if total and done >= total else "learning"
    s.commit()
    return subject_payload(row)


def activate(s: Session, student_id: str, subject_id: str) -> dict[str, Any]:
    row = s.execute(
        select(LearnerSubject).where(LearnerSubject.id == subject_id, LearnerSubject.student_id == student_id)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("no such subject")
    # Keep the identity map in sync with the bulk deactivation. With ``False`` SQLAlchemy
    # still believes the chosen row was already active, so assigning ``True`` would look like
    # no change and would not be flushed on the next request.
    s.query(LearnerSubject).filter(LearnerSubject.student_id == student_id).update({"active": False}, synchronize_session="fetch")
    row.active = True
    s.commit()
    return subject_payload(row)
