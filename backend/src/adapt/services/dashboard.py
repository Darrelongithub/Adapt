"""Dashboard aggregates: one read path for the student's own overview screen.

The layout follows what the blueprint says the learner should be able to see, not
what is easiest to compute: where you are on each topic (with the uncertainty), what
we think might be going on and how sure we are, what we changed at your request and
whether it helped, what is due for review, and -- deliberately last -- the raw
numbers underneath. No "learning style" card exists here, and no panel claims more
than the evidence supports.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..learning import strategies as ST

from ..db.models import (
    ActiveAdaptation,
    AdaptationProposal,
    Attempt,
    BktState,
    EvidenceRecord,
    Hypothesis,
    LearnerEvent,
    LearningSession,
    ProfileSnapshot,
    ProposalStatus,
    RetentionCheck,
    RetentionSchedule,
    Topic,
    utcnow,
)


def overview(services: Any, student_id: str) -> dict[str, Any]:
    s: Session = services.session()
    try:
        titles = {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}
        states = s.execute(select(BktState).where(BktState.student_id == student_id)).scalars().all()
        mastery = [
            {
                "topic_id": st.topic_id,
                "title": titles.get(st.topic_id, st.topic_id),
                "p_know": round(st.p_know, 3),
                "band": st.mastery_band,
                "observations": st.n_obs,
                "correct": st.correct_count,
                "ci": _ci(st),
                "half_life_hours": round(st.half_life_hours, 1) if st.half_life_hours else None,
                "last_practice": st.last_practice_at.isoformat() if st.last_practice_at else None,
                "stale": bool(st.last_practice_at and st.last_practice_at < utcnow() - timedelta(days=14)),
            }
            for st in sorted(states, key=lambda x: x.p_know)
        ]
        prof_snap = s.execute(
            select(ProfileSnapshot).where(ProfileSnapshot.student_id == student_id).order_by(ProfileSnapshot.taken_at.desc()).limit(1)
        ).scalar_one_or_none()
        profile = (prof_snap.profile if prof_snap else None) or {}
        gaps = profile.get("knowledge_gaps") or []
        hyps = s.execute(
            select(Hypothesis).where(Hypothesis.student_id == student_id).order_by(Hypothesis.posterior.desc())
        ).scalars().all()
        pending = s.execute(
            select(AdaptationProposal).where(
                AdaptationProposal.student_id == student_id, AdaptationProposal.status == ProposalStatus.PENDING.value
            )
        ).scalars().all()
        from ..ai.ai1 import proposal_dict

        acts = s.execute(
            select(ActiveAdaptation).where(ActiveAdaptation.student_id == student_id, ActiveAdaptation.active.is_(True))
        ).scalars().all()
        due = s.execute(
            select(RetentionSchedule)
            .where(RetentionSchedule.student_id == student_id, RetentionSchedule.due_at.is_not(None))
            .order_by(RetentionSchedule.due_at)
            .limit(12)
        ).scalars().all()
        now = utcnow()
        checks_stmt = (
            select(RetentionCheck.student_id, func.count(), func.avg(RetentionCheck.score))
            .where(RetentionCheck.student_id == student_id)
            .group_by(RetentionCheck.student_id)
        )
        checks = s.execute(checks_stmt).one_or_none() or (student_id, 0, None)
        recent = s.execute(
            select(
                func.count(Attempt.id),
                func.avg(Attempt.score),
                func.sum(func.coalesce(Attempt.hints_used, 0)),
                func.avg(Attempt.time_on_item_ms),
            ).where(Attempt.student_id == student_id, Attempt.submitted_at >= now - timedelta(days=7))
        ).one()
        return {
            "student_id": student_id,
            "generated_at": now.isoformat(),
            "profile_taken_at": prof_snap.taken_at.isoformat() if prof_snap else None,
            "mastery": mastery,
            "band_summary": profile.get("band_summary") or _bands(mastery),
            "weakest": [m for m in mastery if m["band"] in ("new", "developing")][:5],
            "knowledge_gaps": gaps,
            "hypotheses": [
                {
                    "id": h.id,
                    "detector": h.detector,
                    "statement": h.statement,
                    "status": h.status,
                    "posterior": h.posterior,
                    "supports": h.supports,
                    "refutes": h.refutes,
                    "falsification": h.falsification,
                    "proposed_strategy": h.proposed_strategy,
                    "citations": h.citations or [],
                    "status_note": _STATUS_NOTE.get(h.status, ""),
                }
                for h in hyps
            ],
            # the catalog entry is what carries the label and the citation, so a dashboard that
            # leaves it out reports a proposal with no source next to one that has one
            "pending_proposals": [proposal_dict(p, strategy=ST.CATALOG.get(p.strategy)) for p in pending],
            "active_adaptations": [
                {
                    "id": a.id,
                    "strategy": a.strategy,
                    "action": a.action,
                    "payload": a.payload or {},
                    "source": a.source,
                    "since": a.started_at.isoformat() if a.started_at else None,
                    "ends_at": a.ends_at.isoformat() if a.ends_at else None,
                    "effect_summary": a.effect_summary or {},
                }
                for a in acts
            ],
            "decided_adaptations": [
                proposal_dict(p, strategy=ST.CATALOG.get(p.strategy))
                for p in s.execute(
                    select(AdaptationProposal)
                    .where(
                        AdaptationProposal.student_id == student_id,
                        AdaptationProposal.status.in_([s2 for s2 in (ProposalStatus.APPROVED.value, ProposalStatus.REJECTED.value, ProposalStatus.MODIFIED.value, ProposalStatus.EVALUATED.value)]),
                    )
                    .order_by(AdaptationProposal.decided_at.desc())
                    .limit(12)
                ).scalars().all()
            ],
            "reviews": {
                "due_now": [
                    {
                        "topic_id": r.topic_id,
                        "title": titles.get(r.topic_id, r.topic_id),
                        "due_at": r.due_at.isoformat(),
                        "overdue": r.due_at <= now,
                        "hours_overdue": round((now - r.due_at).total_seconds() / 3600, 1) if r.due_at <= now else 0,
                        "half_life_hours": round(r.half_life_hours, 1),
                        "expected_recall": round(r.expected_recall_at_due, 3),
                        "strength": round(r.strength, 3),
                    }
                    for r in due
                    if r.due_at is not None and r.due_at <= now + timedelta(hours=24)
                ],
                "checks_total": int(checks[1] or 0),
                "checks_mean_score": round(float(checks[2]), 3) if checks[2] is not None else None,
            },
            "last_7_days": {
                "attempts": int(recent[0] or 0),
                "mean_score": round(float(recent[1]), 3) if recent[1] is not None else None,
                "hints_used": int(recent[2] or 0),
                "mean_seconds_on_question": round(float(recent[3]) / 1000, 1) if recent[3] else None,
            },
            "data_volume": profile.get("data_volume") or _volume(s, student_id),
            "open_questions": profile.get("open_questions") or [],
            "measured_outcomes": profile.get("measured_outcomes") or {},
            "strategy_evidence": profile.get("strategy_evidence") or [],
            "student_preferences": profile.get("student_preferences") or {},
            "guardrails": profile.get("guardrails") or {},
            "notes": [
                "Bands are estimates with uncertainty; a 'developing' label with 3 observations "
                "is not the same claim as one with 30.",
                "Hypotheses are fallible explanations, not diagnoses. Each row shows what would change our mind.",
                "Nothing here was applied without your answer; the proposals list keeps both yes and no.",
            ],
        }
    finally:
        s.close()


_STATUS_NOTE = {
    "candidate": "we have not seen enough either way",
    "supported": "the recent evidence leans this way; it could still be wrong",
    "refuted": "the follow-up evidence argues against it",
    "inconclusive": "the follow-up was too small or too mixed to say",
    "retired": "kept for the record; no longer driving anything",
}


def _ci(state: BktState) -> list[float]:
    """Rough 90% interval on the mastery estimate from observation count."""
    from math import sqrt

    def cl(x: float) -> float:
        return min(0.999, max(0.001, x))

    p = cl(state.p_know)
    n = max(1, state.n_obs)
    # Binomial-style spread on the empirical rate, widened by the number of
    # observations: 4/5 and 40/50 are not the same claim, and the UI must not
    # let them look identical.
    se = sqrt(max(1e-6, p * (1 - p) / n))
    return [round(cl(p - 1.645 * se), 3), round(cl(p + 1.645 * se), 3)]


def _bands(mastery: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in mastery:
        out[m["band"]] = out.get(m["band"], 0) + 1
    return out


def _volume(s: Session, student_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for model, key in (
        (Attempt, "attempts"),
        (LearnerEvent, "events"),
        (EvidenceRecord, "evidence"),
        (Hypothesis, "hypotheses"),
        (AdaptationProposal, "proposals"),
        (RetentionCheck, "retention_checks"),
        (LearningSession, "sessions"),
    ):
        counts[key] = int(s.execute(select(func.count()).select_from(model).where(model.student_id == student_id)).scalar_one())
    return counts
