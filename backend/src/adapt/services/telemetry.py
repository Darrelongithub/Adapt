"""Telemetry ingestion: the event log AI 1 reads, and nothing else.

Two rules shape this module (blueprint §6):

1. **The client reports events; the server derives metrics.** Time-to-first-
   interaction, answer-change counts, first-vs-final answer and time-on-question
   are *computed* from the event sequence in :func:`adapt.learning.telemetry.
   metrics_for_events`, never taken on trust from a browser-side number. That is
   what makes the same data available to a judge as a CSV replay, and it means a
   tampered client cannot fabricate mastery.
2. **The allowlist is the privacy boundary.** Every event goes through
   :func:`sanitize_event` before it touches the database: unknown types are
   dropped, forbidden keys (keystroke lists, identifiers, URLs, DOM selectors) are
   stripped, and the payload schema is per-event-type. We log *that* a pause
   happened and for how long, never *what* the student was looking at.

Events are append-only, ordered by a per-session ``seq`` and deduplicated by the
client's ``client_id`` so a flaky mobile connection can retry a batch safely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import LearnerEvent, LearningSession, utcnow
from ..learning import telemetry as T

log = logging.getLogger("adapt.telemetry")

BATCH_MAX = 500


@dataclass
class IngestResult:
    accepted: int = 0
    rejected: int = 0
    duplicate: int = 0
    dropped_fields: int = 0
    reasons: list[str] = field(default_factory=list)
    session_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "rejected": self.rejected,
            "duplicate": self.duplicate,
            "dropped_fields": self.dropped_fields,
            "reasons": self.reasons[:12],
            "session_id": self.session_id,
            "event_types": sorted(T.ALLOWED_EVENTS),
        }


def next_seq(s: Session, session_id: str) -> int:
    cur = s.execute(select(func.max(LearnerEvent.seq)).where(LearnerEvent.session_id == session_id)).scalar_one_or_none()
    return int(cur or 0) + 1


def append_event(
    s: Session,
    *,
    session_id: str,
    student_id: str,
    type: str,  # noqa: A002
    payload: dict[str, Any] | None = None,
    item_id: str | None = None,
    topic_id: str | None = None,
    attempt_id: str | None = None,
    ms_since_question: int | None = None,
    client_id: str | None = None,
    seq: int | None = None,
    ts: datetime | None = None,
) -> tuple[LearnerEvent | None, list[str]]:
    """Validate and append one event. Returns ``(row, complaints)``.

    The complaint list is what the API shows the student/developer: a dropped
    field is a design signal, not something to swallow.
    """
    clean = T.sanitize_event(type, payload or {})
    if not clean.ok:
        return None, [f"rejected: {r}" for r in (clean.rejected or [f"unknown type {type}"])]
    row = LearnerEvent(
        session_id=session_id,
        student_id=student_id,
        seq=seq if seq is not None else next_seq(s, session_id),
        type=clean.type,
        item_id=item_id,
        topic_id=topic_id,
        attempt_id=attempt_id,
        ms_since_question=ms_since_question,
        payload=clean.payload or None,
        client_id=client_id,
    )
    if ts is not None:
        row.ts = ts
    s.add(row)
    # A forbidden key is dropped *and* reported: the client's counter for "fields I must
    # stop sending" should see it, not only the free-text reason.
    forbidden = [r for r in clean.rejected if r.startswith("forbidden telemetry field")]
    other = [r for r in clean.rejected if not r.startswith("forbidden telemetry field")]
    notes = (
        [f"dropped field '{k}'" for k in clean.dropped]
        + [f"dropped {f}" for f in forbidden]
        + [f"rejected '{r}'" for r in other]
    )
    return row, notes


def ingest(
    s: Session,
    *,
    session_id: str,
    student_id: str,
    events: Sequence[dict[str, Any]],
) -> IngestResult:
    """Record a client batch, then refresh the session-level rollups."""
    out = IngestResult(session_id=session_id)
    sess = s.get(LearningSession, session_id)
    if sess is None or sess.student_id != student_id:
        out.rejected = len(events)
        out.reasons.append("unknown session for this student")
        return out
    events = list(events)[:BATCH_MAX]
    seq = next_seq(s, session_id)
    seen_clients = {
        c
        for (c,) in s.execute(
            select(LearnerEvent.client_id).where(LearnerEvent.session_id == session_id, LearnerEvent.client_id.is_not(None))
        ).all()
        if c
    }
    for raw in events:
        etype = str(raw.get("type") or "")
        payload = raw.get("payload") or {}
        if not isinstance(payload, dict):
            out.rejected += 1
            out.reasons.append(f"{etype}: payload must be an object")
            continue
        cid = raw.get("client_id") or raw.get("idempotency_key")
        if cid and str(cid) in seen_clients:
            out.duplicate += 1
            continue
        item_id = raw.get("item_id") or payload.get("item_id")
        row, notes = append_event(
            s,
            session_id=session_id,
            student_id=student_id,
            type=etype,
            payload=payload,
            item_id=item_id if isinstance(item_id, str) else None,
            topic_id=raw.get("topic_id") if isinstance(raw.get("topic_id"), str) else None,
            attempt_id=raw.get("attempt_id") if isinstance(raw.get("attempt_id"), str) else None,
            ms_since_question=_as_int(raw.get("ms_since_question") or payload.get("ms_since_question")),
            client_id=str(cid) if cid else None,
            seq=seq,
        )
        if row is None:
            out.rejected += 1
            out.reasons.extend(notes)
            continue
        if cid:
            seen_clients.add(str(cid))
        seq += 1
        out.accepted += 1
        out.dropped_fields += len([n for n in notes if n.startswith("dropped")])
        out.reasons.extend(notes)

    s.flush()
    refresh_session_stats(s, session_id=session_id)
    s.commit()
    return out


def _as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def refresh_session_stats(s: Session, *, session_id: str) -> dict[str, int]:
    """Recompute session rollups from the event log (idempotent, replayable)."""
    rows = s.execute(
        select(LearnerEvent.ts, LearnerEvent.type, LearnerEvent.payload)
        .where(LearnerEvent.session_id == session_id)
        .order_by(LearnerEvent.seq)
    ).all()
    events = [{"type": t, "payload": p or {}} for _ts, t, p in rows]
    agg = T.session_aggregates(events)
    sess = s.get(LearningSession, session_id)
    if sess is None:
        return agg
    for k, v in agg.items():
        setattr(sess, k, v)
    end = sess.ended_at or utcnow()
    total_ms = int((end - sess.started_at).total_seconds() * 1000)
    sess.active_ms = T.active_ms(total_ms, int(agg.get("away_seconds", 0)) * 1000)
    return agg


def metrics_for_attempt(s: Session, *, session_id: str, item_id: str, submitted_at: datetime | None = None) -> T.AttemptMetrics | None:
    """Replay one question's event slice into derived metrics."""
    rows = s.execute(
        select(LearnerEvent.type, LearnerEvent.payload, LearnerEvent.ts, LearnerEvent.ms_since_question)
        .where(LearnerEvent.session_id == session_id, LearnerEvent.item_id == item_id)
        .order_by(LearnerEvent.seq)
    ).all()
    if not rows:
        return None
    raw = [{"type": t, "payload": dict(p or {})} for t, p, _ts, _ms in rows]
    for e in raw:  # server-side timing is the truth when the client sends none
        e["payload"].setdefault("_ts", None)
    metrics = T.metrics_for_events(raw)
    if submitted_at is not None and metrics.submission_ms is None:
        first_ts = rows[0][2]
        if first_ts is not None:
            metrics.submission_ms = int((submitted_at - first_ts).total_seconds() * 1000)
    return metrics


def event_stream(s: Session, session_id: str, *, after_seq: int = 0, limit: int = 2000) -> list[dict[str, Any]]:
    """The ordered log, for the journey view and for offline replay."""
    rows = s.execute(
        select(LearnerEvent)
        .where(LearnerEvent.session_id == session_id, LearnerEvent.seq > after_seq)
        .order_by(LearnerEvent.seq)
        .limit(limit)
    ).scalars().all()
    return [
        {
            "seq": r.seq,
            "ts": r.ts.isoformat(),
            "type": r.type,
            "item_id": r.item_id,
            "topic_id": r.topic_id,
            "ms_since_question": r.ms_since_question,
            "payload": r.payload or {},
        }
        for r in rows
    ]


def student_event_counts(s: Session, student_id: str, *, since: datetime | None = None) -> dict[str, int]:
    conds = [LearnerEvent.student_id == student_id]
    if since is not None:
        conds.append(LearnerEvent.ts >= since)
    rows = s.execute(select(LearnerEvent.type, func.count()).where(*conds).group_by(LearnerEvent.type)).all()
    return {t: int(n) for t, n in rows}


def privacy_summary() -> dict[str, Any]:
    """What we do and do not record -- rendered verbatim in the UI."""
    # The check-in's stored fields and their consent sentences live with the
    # check-in code (single source); a test diffs them against this list so
    # the consent copy cannot drift from what is really stored.
    from ..ai.monitor.checkin import RECORDED_FIELDS

    return {
        "recorded": [
            "the order of events (question shown, first interaction, answer changed, hint opened, submitted)",
            "timings: time to first interaction, time on question, submission time",
            "first vs final answer and how many times it changed",
            "pauses / focus changes / interruptions, as counts and durations",
            "which later items the same topic appeared in, and the result",
            *[phrase for _field, phrase in RECORDED_FIELDS],
            "the syllabus objectives you approved for each subject, and the syllabus list they came from",
        ],
        "never_recorded": [
            "keystrokes, key timing, or per character edit content",
            "screenshots, screen content, other tabs, or any other app",
            "webcam, microphone audio (only transcribed text when voice is switched on, and only if the learner records a turn)",
            "clipboard contents, names, emails, or any identifier we can map to a real person",
            "GPS, IP address in plaintext (we store a salted hash for abuse limits only)",
        ],
        "why": (
            "Timing and edit behaviour are evidence about the interaction, not proof of a mental state. "
            "ADAPT uses them to propose something to try, and the learner decides."
        ),
        "allowed_event_types": sorted(T.ALLOWED_EVENTS),
        "forbidden_keys": sorted(T.FORBIDDEN_PAYLOAD_KEYS),
    }
