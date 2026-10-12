"""The visible learning journey: one chronological story, assembled on demand.

This is the blueprint's demo requirement -- assessment -> recorded interactions ->
AI 1 analysis -> proposed adaptation -> student decision -> AI 2 changes teaching ->
follow-up assessment -> persistent history and mastery updates.

Nothing here is stored. Every node is derived by joining the tables that already
exist (sessions, events, evidence, hypotheses, proposals, active adaptations,
attempts, retention checks, profile snapshots) onto one timeline, which is the
point: a journey that could be edited independently of the evidence behind it would
be a narrative, not a record.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    ActiveAdaptation,
    AdaptationProposal,
    Attempt,
    EvidenceRecord,
    Hypothesis,
    LearnerEvent,
    LearningSession,
    ProfileSnapshot,
    RetentionCheck,
    Topic,
    utcnow,
)

KIND_ORDER = {
    "session_start": 0,
    "attempt": 1,
    "observation": 1,
    "pattern": 2,
    "hypothesis": 2,
    "proposal": 3,
    "decision": 3,
    "adaptation": 4,
    "retention": 5,
    "snapshot": 6,
}


# Snapshot reasons in words a learner can read. The raw token stays in ``meta`` so the
# detail is still traceable to whatever wrote the row.
_SNAPSHOT_TITLES = {
    "ai1_analyze": "Reviewed what this session showed",
    "diagnostic": "Placement recorded",
    "init": "Starting profile",
    "demo": "Sample learner profile",
}


def _title_map(s: Session) -> dict[str, str]:
    return {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}


def build(s: Session, student_id: str, *, days: float | None = 90, limit: int = 400) -> dict[str, Any]:
    """Return the timeline plus a one-paragraph 'what happened' summary."""
    since = utcnow() - timedelta(days=days) if days else None
    titles = _title_map(s)

    def older(ts: datetime | None) -> bool:
        return bool(ts and since and ts < since)

    nodes: list[dict[str, Any]] = []

    sessions = s.execute(
        select(LearningSession).where(LearningSession.student_id == student_id).order_by(LearningSession.started_at)
    ).scalars().all()
    attempts = s.execute(
        select(Attempt).where(Attempt.student_id == student_id).order_by(Attempt.submitted_at)
    ).scalars().all()
    evidence = s.execute(
        select(EvidenceRecord).where(EvidenceRecord.student_id == student_id).order_by(EvidenceRecord.observed_at)
    ).scalars().all()
    hyps = s.execute(
        select(Hypothesis).where(Hypothesis.student_id == student_id).order_by(Hypothesis.first_seen_at)
    ).scalars().all()
    props = s.execute(
        select(AdaptationProposal).where(AdaptationProposal.student_id == student_id).order_by(AdaptationProposal.created_at)
    ).scalars().all()
    acts = s.execute(
        select(ActiveAdaptation).where(ActiveAdaptation.student_id == student_id).order_by(ActiveAdaptation.started_at)
    ).scalars().all()
    checks = s.execute(
        select(RetentionCheck).where(RetentionCheck.student_id == student_id).order_by(RetentionCheck.created_at)
    ).scalars().all()
    snaps = s.execute(
        select(ProfileSnapshot).where(ProfileSnapshot.student_id == student_id).order_by(ProfileSnapshot.taken_at)
    ).scalars().all()

    for sess in sessions:
        if older(sess.started_at):
            continue
        n_att = sum(1 for a in attempts if a.session_id == sess.id)
        nodes.append(
            {
                "ts": sess.started_at.isoformat(),
                "kind": "session",
                "tone": "neutral",
                "title": f"{sess.mode.capitalize()} session started",
                "detail": (
                    f"{n_att} scored question{'s' if n_att != 1 else ''}, "
                    f"{sess.pause_count} pause{'s' if sess.pause_count != 1 else ''}, "
                    f"{sess.focus_change_count} focus change{'s' if sess.focus_change_count != 1 else ''}"
                ),
                "meta": {"session_id": sess.id, "status": sess.status, "active_ms": sess.active_ms, "analyzed": sess.analyzed_at is not None},
            }
        )
    for a in attempts:
        if older(a.submitted_at):
            continue
        bits = []
        if a.time_on_item_ms:
            bits.append(f"{a.time_on_item_ms / 1000:.0f}s on the question")
        if a.hints_used:
            bits.append(f"{a.hints_used} hint{'s' if a.hints_used != 1 else ''}")
        if a.change_count:
            bits.append(f"changed answer {a.change_count}x")
        if a.intervention_ids:
            bits.append("with adaptations active")
        nodes.append(
            {
                "ts": (a.submitted_at or utcnow()).isoformat(),
                "kind": "attempt",
                "tone": "good" if a.correct else ("warn" if a.correct is None else "bad"),
                "title": ("Answered correctly: " if a.correct else "Missed: " if a.correct is False else "Attempt recorded: ") + titles.get(a.topic_id, a.topic_id),
                "detail": "; ".join(bits) or "no interaction detail recorded for this attempt",
                "meta": {
                    "attempt_id": a.id,
                    "topic_id": a.topic_id,
                    "item_id": a.item_id,
                    "score": a.score,
                    "mode": a.experience_mode,
                    "attempt_no": a.attempt_no,
                    "latency_z": a.latency_z,
                },
            }
        )
    for e in evidence:
        if older(e.observed_at):
            continue
        nodes.append(
            {
                "ts": e.observed_at.isoformat(),
                "kind": "observation",
                "tone": "neutral",
                "title": f"Observed: {e.detector.replace('_', ' ')}",
                "detail": f"{e.metric} = {round(e.value, 3) if e.value is not None else 'n/a'} "
                f"against a baseline of {round(e.baseline, 3) if e.baseline is not None else 'n/a'} over {e.n} observation(s)",
                "meta": {"evidence_id": e.id, "topics": e.topic_ids or [], "strength": round(e.strength, 3), "attempt_ids": (e.attempt_ids or [])[:8]},
            }
        )
    for h in hyps:
        if older(h.last_updated_at):
            continue
        nodes.append(
            {
                "ts": h.last_updated_at.isoformat(),
                "kind": "hypothesis",
                "tone": "think",
                "title": f"Hypothesis ({h.status}): {h.detector.replace('_', ' ')}",
                "detail": h.statement,
                "meta": {
                    "hypothesis_id": h.id,
                    "posterior": h.posterior,
                    "supports": h.supports,
                    "refutes": h.refutes,
                    "falsification": h.falsification,
                    "citations": h.citations or [],
                    "not_a_fact": True,
                },
            }
        )
    for pr in props:
        if older(pr.created_at):
            continue
        nodes.append(
            {
                "ts": pr.created_at.isoformat(),
                "kind": "proposal",
                "tone": "ask",
                "title": f"Proposed change: {pr.strategy.replace('_', ' ')} ({pr.status})",
                "detail": pr.change_text,
                "meta": {
                    "proposal_id": pr.id,
                    "status": pr.status,
                    "facts": pr.facts_text,
                    "pattern": pr.pattern_text,
                    "hypothesis": pr.hypothesis_text,
                    "ask": pr.ask_text,
                    "expected_effect": pr.expected_effect,
                    "caveat": pr.caveat_text,
                    "decided_at": pr.decided_at.isoformat() if pr.decided_at else None,
                    "student_note": pr.student_note,
                    "evaluation_status": pr.evaluation_status,
                },
            }
        )
    for a in acts:
        if older(a.started_at):
            continue
        nodes.append(
            {
                "ts": a.started_at.isoformat(),
                "kind": "adaptation",
                "tone": "good" if a.active else "muted",
                "title": ("Now applying: " if a.active else "Stopped: ") + a.strategy.replace("_", " "),
                "detail": (a.payload or {}).get("note") or f"action={a.action}; source={a.source}",
                "meta": {
                    "active_adaptation_id": a.id,
                    "proposal_id": a.proposal_id,
                    "student_approved": a.source in ("approved", "modified"),
                    "effect": a.effect_summary or {},
                    "end_reason": a.end_reason,
                },
            }
        )
    for c in checks:
        if older(c.created_at):
            continue
        nodes.append(
            {
                "ts": c.created_at.isoformat(),
                "kind": "retention",
                "tone": "good" if c.correct else "warn",
                "title": f"Later check after {c.lag_hours:.0f}h: " + ("remembered" if c.correct else "forgot"),
                "detail": f"expected recall was {c.expected_recall:.0%}" if c.expected_recall is not None else "no prediction on file for this topic",
                "meta": {"topic_id": c.topic_id, "kind_of_check": c.kind, "score": c.score, "proposal_id": c.proposal_id, "topic_title": titles.get(c.topic_id, c.topic_id)},
            }
        )
    for sn in snaps:
        if older(sn.taken_at):
            continue
        prof = sn.profile or {}
        bands = prof.get("band_summary") or {}
        nodes.append(
            {
                "ts": sn.taken_at.isoformat(),
                "kind": "snapshot",
                "tone": "muted",
                # A learner-facing timeline must not read like a log line: the internal
                # reason token goes in meta, the sentence says what happened.
                "title": _SNAPSHOT_TITLES.get(sn.reason or "", "Profile updated"),
                "detail": ", ".join(f"{v} {k}" for k, v in sorted(bands.items())) or "no topics traced yet",
                "meta": {
                    "snapshot_id": sn.id,
                    "reason": sn.reason,
                    "topics": sn.topic_count,
                    "hypotheses": sn.hypothesis_count,
                    "adaptations": sn.adaptation_count,
                },
            }
        )

    nodes.sort(key=lambda n: (str(n["ts"]), KIND_ORDER.get(n["kind"], 9)))
    nodes = nodes[-limit:]
    return {
        "student_id": student_id,
        "generated_at": utcnow().isoformat(),
        "window_days": days,
        "nodes": nodes,
        "counts": _counts(nodes),
        "phases": _phases(nodes),
        "summary": _summary(nodes, titles),
    }


def _counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in nodes:
        out[n["kind"]] = out.get(n["kind"], 0) + 1
    return out


def _phases(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the timeline into the loop's own steps, so the UI can show the cycle.

    The phases are *labelled from the evidence that produced them*: a Test node is
    an attempt, Observe is an observation, Understand is a hypothesis, Personalize
    is a proposal+decision, Learn is the adaptation and what happened after it.
    """
    labels = {
        "attempt": ("Test", "what the learner did"),
        "observation": ("Observe", "what the events showed"),
        "hypothesis": ("Understand", "a possible explanation, not a fact"),
        "proposal": ("Personalize", "a change to try, asked not imposed"),
        "decision": ("Personalize", "the learner's answer"),
        "adaptation": ("Learn", "what we changed and whether it helped"),
        "retention": ("Learn", "the later check"),
        "session": ("Test", "a working session"),
        "snapshot": ("Learn", "profile history"),
    }
    out: list[dict[str, Any]] = []
    for n in nodes:
        label, why = labels.get(n["kind"], ("Other", ""))
        out.append({"ts": n["ts"], "phase": label, "why": why, "kind": n["kind"], "title": n["title"]})
    return out


def _summary(nodes: list[dict[str, Any]], titles: dict[str, str]) -> str:
    if not nodes:
        return "Nothing recorded yet. The journey starts with the diagnostic."
    attempts = [n for n in nodes if n["kind"] == "attempt"]
    correct = sum(1 for n in attempts if n["tone"] == "good")
    props = [n for n in nodes if n["kind"] == "proposal"]
    applied = [n for n in nodes if n["kind"] == "adaptation" and n["tone"] == "good"]
    checks = [n for n in nodes if n["kind"] == "retention"]
    held = sum(1 for n in checks if n["tone"] == "good")
    parts = [
        f"{len([n for n in nodes if n['kind'] == 'session'])} session(s), "
        f"{len(attempts)} scored attempt(s) ({correct} correct).",
    ]
    if props:
        parts.append(
            f"AI 1 raised {len(props)} adaptation proposal(s); {len(applied)} were approved and are shaping teaching now."
        )
    if checks:
        parts.append(f"On the {len(checks)} delayed retest(s), recall held for {held}.")
    return " ".join(parts)


def milestones(s: Session, student_id: str) -> list[dict[str, Any]]:
    """The few moments worth calling 'progress', chosen by evidence, not praise.

    A topic crosses a mastery band; an approved adaptation measurably helped; a
    delayed retest was held. Anything else is noise, and inflating it would teach
    the learner that our words are cheap.
    """
    out: list[dict[str, Any]] = []
    titles = _title_map(s)
    snaps = s.execute(
        select(ProfileSnapshot).where(ProfileSnapshot.student_id == student_id).order_by(ProfileSnapshot.taken_at)
    ).scalars().all()
    prev_bands: dict[str, str] = {}
    order = {"new": 0, "developing": 1, "proficient": 2, "mastered": 3}
    for sn in snaps:
        for m in (sn.profile or {}).get("mastery", []) or []:
            tid, band = m.get("topic_id"), m.get("band", "new")
            before = prev_bands.get(tid)
            if before and order.get(band, 0) > order.get(before, 0):
                out.append(
                    {
                        "ts": sn.taken_at.isoformat(),
                        "type": "mastery_band",
                        "topic_id": tid,
                        "topic_title": titles.get(tid, tid),
                        "from": before,
                        "to": band,
                        "p_know": m.get("p_know"),
                    }
                )
            prev_bands[tid] = band
    for pr in s.execute(
        select(AdaptationProposal).where(
            AdaptationProposal.student_id == student_id,
            AdaptationProposal.evaluation_status.is_not(None),
        )
    ).scalars().all():
        eff = pr.outcome or {}
        out.append(
            {
                "ts": (pr.evaluated_at or pr.decided_at or utcnow()).isoformat(),
                "type": "adaptation_effect",
                "strategy": pr.strategy,
                "status": pr.evaluation_status,
                "effect": eff.get("effect") or {},
                "verdict_words": eff.get("verdict_words") or eff.get("note") or "",
            }
        )
    for c in s.execute(
        select(RetentionCheck).where(RetentionCheck.student_id == student_id, RetentionCheck.correct.is_(True))
    ).scalars().all():
        if c.lag_hours and c.lag_hours >= 24:
            out.append(
                {
                    "ts": c.created_at.isoformat(),
                    "type": "retention_held",
                    "topic_id": c.topic_id,
                    "topic_title": titles.get(c.topic_id, c.topic_id),
                    "lag_hours": round(c.lag_hours, 1),
                }
            )
    out.sort(key=lambda x: str(x["ts"]))
    return out


def stats(s: Session, student_id: str) -> dict[str, Any]:
    """Small aggregates used by the journey header."""
    n_att = s.execute(
        select(func.count(), func.avg(Attempt.score)).where(Attempt.student_id == student_id)
    ).one()
    n_sess = s.execute(select(func.count()).select_from(LearningSession).where(LearningSession.student_id == student_id)).scalar_one()
    n_events = s.execute(select(func.count()).select_from(LearnerEvent).where(LearnerEvent.student_id == student_id)).scalar_one()
    return {
        "sessions": int(n_sess),
        "attempts": int(n_att[0]),
        "mean_score": round(float(n_att[1]), 3) if n_att[1] is not None else None,
        "events": int(n_events),
    }
