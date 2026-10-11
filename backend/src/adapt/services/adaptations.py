"""The approval gate -- the only place a persistent adaptation may be written.

Blueprint §3: *AI 1 never applies a persistent change on its own.* That rule is
worth less than a comment unless it is enforceable, so it is enforced here in two
ways:

* :func:`approve` requires ``student_decision`` -- an explicit record of the
  learner's answer to a specific proposal, including *when*. It then writes the
  ``ActiveAdaptation`` row that AI 2 reads. No other module imports
  ``ActiveAdaptation`` for writing, and ``tests/test_adaptation_gate.py`` asserts
  that by scanning AI 1's source for construction of that model.
* ``modify`` cannot smuggle in a new strategy: the student may change the knobs of
  the proposed action, and anything else they typed is stored as a *stated
  preference* (which is theirs to ask for) rather than as an approved adaptation.

Rejecting is not discarded either: it is recorded, because "this learner said no to
hint fading" is evidence about them and it stops AI 1 proposing the same thing
tomorrow.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    ActiveAdaptation,
    AdaptationProposal,
    ProposalStatus,
    StudentPreference,
    utcnow,
)
from ..learning import strategies as ST
from ..security.audit import record_audit

log = logging.getLogger("adapt.adaptations")

DECISIONS = ("accept", "reject", "modify", "later")


class GateError(RuntimeError):
    """Raised when something tries to pass a persistent change without consent."""


def _assert_student_approved(student_decision: Any, proposal: AdaptationProposal) -> str:
    """The gate. Returns the normalised decision or raises."""
    if not isinstance(student_decision, dict):
        raise GateError(
            "a persistent adaptation requires the student's own answer "
            "({'decision': 'accept'|'reject'|'modify', 'answered_at': ...})"
        )
    decision = str(student_decision.get("decision") or "").strip().lower()
    if decision not in DECISIONS:
        raise GateError(f"decision must be one of {DECISIONS}")
    if student_decision.get("answered_by") != "student":
        # Not a formality. The whole design rests on "a persistent change needs the
        # learner", and the way that claim rots is a caller that omits the field and
        # gets a free pass. Every entry point stamps it: the web route, the CLI (which
        # has to say the learner answered), the demo (which says it is synthetic).
        raise GateError(
            "only the student can answer an adaptation proposal: pass "
            '\"answered_by\": \"student\" (and a \"channel\" saying where it came from)'
        )
    if not student_decision.get("answered_at"):
        raise GateError("the decision needs a timestamp; a decision we cannot date cannot be reviewed")
    proposal_id = student_decision.get("proposal_id")
    if proposal_id and proposal_id != proposal.id:
        raise GateError("the answer does not belong to this proposal")
    return decision


def pending(s: Session, student_id: str) -> list[dict[str, Any]]:
    rows = s.execute(
        select(AdaptationProposal)
        .where(AdaptationProposal.student_id == student_id, AdaptationProposal.status == ProposalStatus.PENDING.value)
        .order_by(AdaptationProposal.created_at.desc())
    ).scalars().all()
    from ..ai.ai1 import proposal_dict

    return [proposal_dict(r) for r in rows]


def history(s: Session, student_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = s.execute(
        select(AdaptationProposal)
        .where(AdaptationProposal.student_id == student_id, AdaptationProposal.status != ProposalStatus.PENDING.value)
        .order_by(AdaptationProposal.decided_at.desc())
        .limit(limit)
    ).scalars().all()
    from ..ai.ai1 import proposal_dict

    return [proposal_dict(r) for r in rows]


def active(s: Session, student_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    conds = [ActiveAdaptation.student_id == student_id]
    if not include_inactive:
        conds.append(ActiveAdaptation.active.is_(True))
    rows = s.execute(select(ActiveAdaptation).where(*conds).order_by(ActiveAdaptation.started_at.desc())).scalars().all()
    out = []
    for r in rows:
        strat = ST.CATALOG.get(r.strategy)
        out.append(
            {
                "id": r.id,
                "strategy": r.strategy,
                "label": strat.label if strat else r.strategy,
                "action": r.action,
                "payload": r.payload or {},
                "source": r.source,
                "active": bool(r.active),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ends_at": r.ends_at.isoformat() if r.ends_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "end_reason": r.end_reason,
                "proposal_id": r.proposal_id,
                "effect_summary": r.effect_summary,
                "watch_metric": strat.watch_metric if strat else None,
                "mechanism": strat.mechanism if strat else None,
                "citations": [c["ref"] for c in (strat.as_dict()["citations"] if strat else [])],
            }
        )
    return out


def decide(
    s: Session,
    *,
    student_id: str,
    proposal_id: str,
    student_decision: dict[str, Any],
    note: str | None = None,
    modifications: dict[str, Any] | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Record the student's answer and, if it is yes, turn it on.

    ``modifications`` may only change the tunable *knobs* of the proposed strategy
    (and only keys the strategy declares) -- it cannot redirect the change to a
    different strategy, which is what keeps "modify" from being a way to write
    anything we like.
    """
    proposal = s.get(AdaptationProposal, proposal_id)
    if proposal is None or proposal.student_id != student_id:
        raise GateError("unknown proposal for this learner")
    if proposal.status != ProposalStatus.PENDING.value:
        return {"ok": False, "status": proposal.status, "reason": "already answered", "proposal_id": proposal.id}
    decision = _assert_student_approved(student_decision, proposal)
    now = utcnow()
    proposal.decided_at = now
    proposal.student_note = (note or student_decision.get("note") or None) and str(note or student_decision.get("note"))[:2000]
    strategy = ST.CATALOG.get(proposal.strategy)
    outcome: dict[str, Any] = {
        "proposal_id": proposal.id,
        "strategy": proposal.strategy,
        "decision": decision,
        # Every answer has the same shape, so a client can render any outcome without
        # branching on which keys happen to exist: what is on, with what payload,
        # what will be measured, and the sentence that says why.
        "active_adaptation_id": None,
        "payload": None,
        "review": None,
        "note": None,
    }

    if decision in ("accept", "modify"):
        payload = dict(proposal.action_payload or {})
        if decision == "modify":
            allowed = set((strategy.payload_defaults or {}).keys()) if strategy else set()
            clean = {k: v for k, v in (modifications or {}).items() if k in allowed}
            refused = sorted(set((modifications or {}).keys()) - allowed)
            payload.update(clean)
            proposal.modifications = {"applied": clean, "refused_keys": refused}
            if refused:
                outcome["refused_keys"] = refused
        proposal.status = ProposalStatus.APPROVED.value if decision == "accept" else ProposalStatus.MODIFIED.value
        proposal.applied_at = now
        # baseline snapshot for the effect measurement, taken *before* the change
        proposal.baseline = {**(proposal.baseline or {}), "taken_at": now.isoformat(), "review_metric": proposal.review_metric}
        row = ActiveAdaptation(
            student_id=student_id,
            proposal_id=proposal.id,
            strategy=proposal.strategy,
            action=proposal.action,
            payload=payload,
            source="approved" if decision == "accept" else "modified",
            active=True,
            started_at=now,
            ends_at=now + timedelta(days=days) if days else None,
        )
        s.add(row)
        s.flush()
        outcome["active_adaptation_id"] = row.id
        outcome["payload"] = payload
        outcome["review"] = {
            "metric": proposal.review_metric or (strategy.watch_metric if strategy else None),
            "window": proposal.review_window or {"attempts": 6},
            "note": "AI 1 compares this against the baseline and reports it to you either way.",
        }
    elif decision == "reject":
        proposal.status = ProposalStatus.REJECTED.value
        outcome["note"] = "not applied; kept in your history so the same idea is not proposed again tomorrow"
        outcome["review"] = {"metric": proposal.review_metric, "window": None, "note": "nothing is being measured: nothing was applied"}
    else:  # "not now" -- still pending, but parked: not applied, not re-asked today
        proposal.expires_at = now + timedelta(days=7)
        proposal.student_note = proposal.student_note or "asked me later"
        outcome["note"] = (
            "kept as a pending idea until "
            + proposal.expires_at.date().isoformat()
            + "; nothing was applied in the meantime"
        )

    record_audit(
        s,
        actor_id=student_id,
        action=f"adaptation_{proposal.status}",
        object_type="adaptation_proposal",
        object_id=proposal.id,
        outcome="ok",
        detail={
            "strategy": proposal.strategy,
            "decision": decision,
            "answered_at": student_decision.get("answered_at"),
            # Who answered, and through what. The proposal row itself stores status and
            # timestamps but no consent provenance, so without this the database could
            # not tell a learner's own answer from one a script typed for them -- and
            # the audit detail is encrypted at rest like the rest of the private data.
            "answered_by": student_decision.get("answered_by"),
            "channel": student_decision.get("channel") or "unspecified",
        },
    )
    outcome["ok"] = True
    outcome["status"] = proposal.status
    return outcome


def approve(
    s: Session,
    *,
    student_id: str,
    proposal_id: str,
    student_decision: dict[str, Any],
    note: str | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Explicitly-named wrapper: the only path to a new ActiveAdaptation row."""
    return decide(s, student_id=student_id, proposal_id=proposal_id, student_decision=student_decision, note=note, days=days)


def modify(
    s: Session,
    *,
    student_id: str,
    proposal_id: str,
    student_decision: dict[str, Any],
    modifications: dict[str, Any],
    note: str | None = None,
) -> dict[str, Any]:
    """Accept the idea with the learner's own numbers."""
    merged = {**student_decision, "decision": "modify"}
    return decide(
        s,
        student_id=student_id,
        proposal_id=proposal_id,
        student_decision=merged,
        note=note,
        modifications=modifications,
    )


def revoke(s: Session, *, student_id: str, active_id: str | None = None, strategy: str | None = None, reason: str = "student_revoked") -> dict[str, Any]:
    """Turn a change off. The learner can always take it back."""
    conds = [ActiveAdaptation.student_id == student_id, ActiveAdaptation.active.is_(True)]
    if active_id:
        conds.append(ActiveAdaptation.id == active_id)
    elif strategy:
        conds.append(ActiveAdaptation.strategy == strategy)
    else:
        raise GateError("revoke needs an adaptation id or a strategy name")
    rows = s.execute(select(ActiveAdaptation).where(*conds)).scalars().all()
    for r in rows:
        r.active = False
        r.ended_at = utcnow()
        r.ends_at = None
        r.end_reason = reason[:32]
    record_audit(
        s,
        actor_id=student_id,
        action="adaptation_revoked",
        object_type="active_adaptation",
        object_id=(rows[0].id if rows else None),
        outcome="ok",
        detail={"count": len(rows), "reason": reason[:200], "strategies": [r.strategy for r in rows]},
    )
    return {"ok": True, "revoked": len(rows), "strategies": [r.strategy for r in rows]}


def state_preference(s: Session, *, student_id: str, key: str, value: Any, note: str | None = None) -> dict[str, Any]:
    """Store a learner's own setting (voice on/off, gender, 'stop suggesting videos').

    Kept separate from adaptations on purpose: this is them instructing us, not us
    proposing something they agreed to. It takes effect immediately and needs no
    approval, exactly like "explain that more simply".
    """
    row = s.get(StudentPreference, (student_id, key))
    if row is None:
        row = StudentPreference(student_id=student_id, key=key, value=value, source="settings", note=note)
        s.add(row)
    else:
        row.value = value
        row.source = "settings"
        row.note = note
    s.commit()
    return {"ok": True, "key": key, "value": value}
