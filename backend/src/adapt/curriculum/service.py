"""The syllabus approval gate: storage, approval, and the refusal.

Flow: scan -> the learner reviews the checklist (untick, reorder, add
their own) -> "Use this plan" -> only then may the test plan, the lesson
skeleton and lessons run for that subject, and only with the approved
objective ids. A later rescan that disagrees is stored as a *proposed*
version the learner answers; it never replaces the guideline by itself.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import utcnow
from .models import ApprovedGuideline, ScanCacheEntry
from .scanner import NOT_CHECKED_LABEL, ScanResult, normalise_curriculum, validate_objectives

SCAN_CACHE_DAYS = 30


class GuidelineError(ValueError):
    """An approval the gate cannot record. The message is learner-safe."""


def scan_cache_key(*, subject: str, curriculum: str | None, grade: str | None, sources_fingerprint: str) -> str:
    raw = json.dumps({"s": subject.casefold(), "c": curriculum, "g": grade, "f": sources_fingerprint}, sort_keys=True)
    return "h:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cached_scan(s: Session, key: str) -> dict[str, Any] | None:
    row = s.get(ScanCacheEntry, key)
    if row is None or row.expires_at is None or row.expires_at <= utcnow():
        return None
    return dict(row.payload or {})


def store_scan(s: Session, key: str, result: ScanResult, *, days: int = SCAN_CACHE_DAYS) -> None:
    payload = result.as_dict()
    payload["fetched_at"] = utcnow().isoformat()
    row = s.get(ScanCacheEntry, key)
    if row is None:
        s.add(ScanCacheEntry(key=key, payload=payload, fetched_at=utcnow(), expires_at=utcnow() + timedelta(days=days)))
    else:
        row.payload = payload
        row.fetched_at = utcnow()
        row.expires_at = utcnow() + timedelta(days=days)


def remember_candidate(s: Session, *, student_id: str, subject_id: str, result: ScanResult) -> None:
    """Keep the candidate the learner is reviewing, for origin checks at approval."""
    store_scan(s, f"last:{student_id}:{subject_id}", result)


def last_candidate(s: Session, *, student_id: str, subject_id: str) -> dict[str, Any] | None:
    return cached_scan(s, f"last:{student_id}:{subject_id}")


def approved(s: Session, *, student_id: str, subject_id: str) -> ApprovedGuideline | None:
    return s.execute(
        select(ApprovedGuideline)
        .where(
            ApprovedGuideline.student_id == student_id,
            ApprovedGuideline.subject_id == subject_id,
            ApprovedGuideline.status == "approved",
        )
        .order_by(ApprovedGuideline.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def proposed(s: Session, *, student_id: str, subject_id: str) -> ApprovedGuideline | None:
    return s.execute(
        select(ApprovedGuideline)
        .where(
            ApprovedGuideline.student_id == student_id,
            ApprovedGuideline.subject_id == subject_id,
            ApprovedGuideline.status == "proposed",
        )
        .order_by(ApprovedGuideline.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def approved_objective_ids(s: Session, *, student_id: str, subject_id: str) -> list[str] | None:
    row = approved(s, student_id=student_id, subject_id=subject_id)
    if row is None:
        return None
    return [str(o.get("id")) for o in (row.objectives or []) if isinstance(o, dict) and o.get("id")]


def _with_origins(s: Session, *, student_id: str, subject_id: str, objectives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Origins are the server's to state, not the client's.

    An objective whose id matches the stored scan candidate (or the
    current guideline) keeps that row's origin/source/attribution. One
    the server has never seen is the learner's own — "yours" — however
    the client labelled it. A client cannot mint "from syllabus".
    """
    known: dict[str, dict[str, Any]] = {}
    candidate = last_candidate(s, student_id=student_id, subject_id=subject_id)
    for row in (candidate or {}).get("objectives") or []:
        if isinstance(row, dict) and row.get("id"):
            known[str(row["id"])] = row
    current = approved(s, student_id=student_id, subject_id=subject_id)
    for row in (current.objectives if current else []) or []:
        if isinstance(row, dict) and row.get("id"):
            known.setdefault(str(row["id"]), row)
    pending = proposed(s, student_id=student_id, subject_id=subject_id)
    for row in (pending.objectives if pending else []) or []:
        if isinstance(row, dict) and row.get("id"):
            known.setdefault(str(row["id"]), row)
    out: list[dict[str, Any]] = []
    for obj in objectives:
        hit = known.get(str(obj.get("id")))
        if hit is not None:
            out.append({**obj, "origin": hit.get("origin", "suggested"), "source": hit.get("source", obj.get("source", "")), "source_ref": hit.get("source_ref", ""), "attribution": hit.get("attribution", ""), "licence": hit.get("licence", "")})
        else:
            out.append({**obj, "origin": "yours", "source": "learner", "attribution": "", "licence": ""})
    return out


def approve(
    s: Session,
    *,
    student_id: str,
    subject_id: str,
    objectives_raw: Sequence[Any],
    curriculum: str | None,
    grade: str | None,
    source_label: str | None = None,
    attribution: str | None = None,
) -> ApprovedGuideline:
    """Record the learner's "Use this plan". Creates the next version."""
    objectives = validate_objectives(list(objectives_raw or []), default_origin="yours")
    if not objectives:
        raise GuidelineError("keep at least one objective ticked, or add your own")
    objectives = _with_origins(s, student_id=student_id, subject_id=subject_id, objectives=objectives)
    current = approved(s, student_id=student_id, subject_id=subject_id)
    candidate = last_candidate(s, student_id=student_id, subject_id=subject_id) or {}
    pending = proposed(s, student_id=student_id, subject_id=subject_id)
    if pending is not None:
        s.delete(pending)
        s.flush()
    checked = any(o.get("origin") in ("syllabus", "outline") for o in objectives)
    row = ApprovedGuideline(
        student_id=student_id,
        subject_id=subject_id,
        curriculum=normalise_curriculum(curriculum) or (candidate.get("curriculum") if isinstance(candidate, dict) else None),
        grade=(" ".join(str(grade or "").split())[:24] or None),
        version=(int(current.version) + 1 if current else 1),
        status="approved",
        objectives=objectives,
        source_label=(str(source_label or candidate.get("source_label") or (NOT_CHECKED_LABEL if not checked else "From the syllabus list saved in the app")))[:120],
        attribution=(str(attribution or candidate.get("attribution") or next((o.get("attribution") for o in objectives if o.get("attribution")), "")))[:240],
    )
    s.add(row)
    s.flush()
    return row


def note_rescan(s: Session, *, student_id: str, subject_id: str, result: ScanResult) -> ApprovedGuideline | None:
    """A rescan that disagrees with the guideline becomes a proposal.

    Returns the proposed row when one was created (the learner has
    something to answer), else ``None`` — including when nothing changed,
    which must not nag.
    """
    current = approved(s, student_id=student_id, subject_id=subject_id)
    if current is None:
        return None
    have = {str(o.get("title", "")).casefold() for o in (current.objectives or []) if isinstance(o, dict)}
    fresh = {str(o.get("title", "")).casefold() for o in result.objectives}
    if have == fresh:
        return None
    pending = proposed(s, student_id=student_id, subject_id=subject_id)
    if pending is not None:
        s.delete(pending)
        s.flush()
    row = ApprovedGuideline(
        student_id=student_id,
        subject_id=subject_id,
        curriculum=result.curriculum,
        grade=result.grade,
        version=int(current.version) + 1,
        status="proposed",
        objectives=result.objectives,
        source_label=result.source_label,
        attribution=result.attribution,
    )
    s.add(row)
    s.flush()
    return row


def reject_proposal(s: Session, *, student_id: str, subject_id: str) -> bool:
    pending = proposed(s, student_id=student_id, subject_id=subject_id)
    if pending is None:
        return False
    s.delete(pending)
    return True


def gate_message(subject_title: str) -> str:
    return (
        f"Choose what to cover in {subject_title} first: review the syllabus checklist and tap "
        "\"Use this plan\" — then the checks and lessons can start."
    )
