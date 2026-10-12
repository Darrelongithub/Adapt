"""The dynamic learner profile: the one object AI 1 writes and AI 2 reads.

Deliberately *derived*, never authored: the profile is a snapshot recomputed from
BKT traces, evidence, hypotheses and approved adaptations, and every field keeps
its provenance (which evidence, how many observations, when). Two guardrails are
enforced in code here:

* **No fixed-trait labels.** Style/personality labels are rejected outright (see
  :func:`assert_evidence_only`), because the blueprint's evidence base says
  learning-style matching is unsupported and because a label invites the system to
  stop looking for new evidence.
* **Data minimization.** :func:`to_llm_payload` is the only representation allowed
  to leave the process for a Gemini call, and it carries aggregated numbers --
  never names, emails, transcripts or raw answers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

BANNED_LABEL_PATTERNS = (
    r"\b(visual|auditory|kinesthetic|tactile|reading|verbal)\s+learner\b",
    r"\b(left|right)\s+brained\b",
    r"\b(genius|gifted|slow learner|low[- ]ability|unmotivated|lazy|bad at)\b",
    r"\bstrengths?[- ]based? profile\b",
    r"\b(MBTI|DISC|Enneagram|Myers[- ]Briggs)\b",
)


class StyleLabelError(ValueError):
    """Raised when something tries to write a fixed-trait label into a profile."""


def assert_evidence_only(payload: dict[str, Any]) -> None:
    """Scan a would-be profile for trait language and refuse it.

    Cheap insurance against a future prompt change or an LLM output that sneaks a
    label in -- the guardrail has to be enforced where the data is written, not
    only where it is reviewed.
    """
    text = re.sub(r"\s+", " ", str(payload))
    for pat in BANNED_LABEL_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raise StyleLabelError(
                f"rejected fixed-trait label {m.group(0)!r} in learner profile; "
                "ADAPT records topic mastery and measured responses only"
            )


@dataclass
class TopicTrace:
    topic_id: str
    title: str
    p_know: float
    band: str
    n_obs: int
    half_life_hours: float | None = None
    due_at: datetime | None = None
    expected_recall: float | None = None
    trend: str | None = None
    recent_lift: float | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "p_know": round(self.p_know, 3),
            "band": self.band,
            "observations": self.n_obs,
            "half_life_hours": round(self.half_life_hours, 2) if self.half_life_hours else None,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "expected_recall": round(self.expected_recall, 3) if self.expected_recall is not None else None,
            "trend": self.trend,
            "note": self.note,
        }


@dataclass
class Directive:
    """A teaching instruction AI 2 must follow, with its reason and its origin."""

    strategy: str
    action: str
    payload: dict[str, Any]
    why: str
    source: str  # student_approved | measured | student_stated
    proposal_id: str | None = None
    student_note: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "action": self.action,
            "payload": self.payload,
            "why": self.why,
            "source": self.source,
            "proposal_id": self.proposal_id,
            "student_note": self.student_note,
        }


@dataclass
class LearnerProfile:
    student_id: str
    taken_at: datetime
    traces: list[TopicTrace] = field(default_factory=list)
    directives: list[Directive] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    pending_proposals: list[dict[str, Any]] = field(default_factory=list)
    strategy_stats: list[dict[str, Any]] = field(default_factory=list)
    session_behaviour: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)
    outcomes: dict[str, Any] = field(default_factory=dict)
    data_volume: dict[str, int] = field(default_factory=dict)
    open_questions: list[str] = field(default_factory=list)
    # What the learner asked for, verbatim. Kept apart from ``directives`` so the
    # UI can show "you said this" next to "we measured this".
    preferences: dict[str, Any] = field(default_factory=dict)

    # -- derived views -------------------------------------------------- #

    @property
    def weakest(self) -> list[TopicTrace]:
        return sorted(self.traces, key=lambda t: t.p_know)[:5]

    @property
    def due_reviews(self) -> list[TopicTrace]:
        return [t for t in self.traces if t.due_at and t.due_at <= self.taken_at]

    def band_summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.traces:
            out[t.band] = out.get(t.band, 0) + 1
        return out

    def directive_for(self, strategy: str) -> Directive | None:
        return next((d for d in self.directives if d.strategy == strategy), None)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "student_id": self.student_id,
            "taken_at": self.taken_at.isoformat(),
            "profile_kind": "dynamic_evidence_based",
            "mastery": [t.as_dict() for t in sorted(self.traces, key=lambda x: x.p_know)],
            "band_summary": self.band_summary(),
            "knowledge_gaps": self.gaps,
            "hypotheses": self.hypotheses,
            "pending_adaptation_proposals": self.pending_proposals,
            "active_adaptations": [d.as_dict() for d in self.directives],
            "strategy_evidence": self.strategy_stats,
            "session_behaviour": self.session_behaviour,
            "retention": self.retention,
            "measured_outcomes": self.outcomes,
            "data_volume": self.data_volume,
            "student_preferences": self.preferences,
            "open_questions": self.open_questions,
            "guardrails": {
                "no_fixed_trait_labels": True,
                "hypotheses_are_not_facts": True,
                "persistent_change_requires_student_approval": True,
            },
        }
        assert_evidence_only(payload)
        return payload


def build_profile(
    *,
    student_id: str,
    traces: Iterable[TopicTrace],
    directives: Sequence[Directive] = (),
    gaps: Sequence[dict[str, Any]] = (),
    hypotheses: Sequence[dict[str, Any]] = (),
    pending_proposals: Sequence[dict[str, Any]] = (),
    strategy_stats: Sequence[dict[str, Any]] = (),
    session_behaviour: dict[str, Any] | None = None,
    retention: dict[str, Any] | None = None,
    outcomes: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
    data_volume: dict[str, int] | None = None,
    taken_at: datetime | None = None,
) -> LearnerProfile:
    p = LearnerProfile(
        student_id=student_id,
        taken_at=taken_at or datetime.utcnow(),
        traces=list(traces),
        directives=list(directives),
        gaps=list(gaps),
        hypotheses=list(hypotheses),
        pending_proposals=list(pending_proposals),
        strategy_stats=list(strategy_stats),
        session_behaviour=session_behaviour or {},
        retention=retention or {},
        outcomes=outcomes or {},
        data_volume=data_volume or {},
        preferences=dict(preferences or {}),
    )
    p.open_questions = _open_questions(p)
    return p


def _open_questions(p: LearnerProfile) -> list[str]:
    """What we still cannot tell from the evidence. Surfaced, not hidden."""
    out: list[str] = []
    thin = [t for t in p.traces if t.n_obs < 5]
    if thin:
        out.append(
            f"{len(thin)} topic(s) have fewer than 5 scored attempts, so their mastery "
            "estimates could still move a lot"
        )
    if not p.retention.get("checks"):
        out.append("no delayed retests yet, so retention is modelled but not measured")
    if not any(d.source == "student_approved" for d in p.directives):
        out.append("no adaptation has been approved yet, so we have not tested any change")
    if not p.session_behaviour.get("interruptions"):
        out.append("no interruptions recorded this session, so focus effects are unmeasured")
    return out


# --------------------------------------------------------------------------- #
# data minimization for external AI calls
# --------------------------------------------------------------------------- #

_MAX_TITLE_LEN = 40


def to_llm_payload(p: LearnerProfile, *, max_topics: int = 12) -> dict[str, Any]:
    """The *only* profile view allowed to reach Gemini.

    Kept small on purpose (tokens = quota = money we are not spending) and free of
    identifiers, transcripts and verbatim answers.
    """
    traces = sorted(p.traces, key=lambda t: t.p_know)[:max_topics]
    return {
        "learner_ref": "local",  # pseudonymous; server maps it back if needed
        "topic_traces": [
            {
                "topic": t.title[:_MAX_TITLE_LEN],
                "p_know": round(t.p_know, 2),
                "band": t.band,
                "n": t.n_obs,
                "recall_half_life_h": round(t.half_life_hours, 1) if t.half_life_hours else None,
            }
            for t in traces
        ],
        "gaps": [
            {
                "topic": str(g.get("topic_title", ""))[:_MAX_TITLE_LEN],
                "prerequisite": str(g.get("blocking_prerequisite_title", ""))[:_MAX_TITLE_LEN],
                "prereq_mastery": g.get("prerequisite_mastery"),
            }
            for g in p.gaps[:4]
        ],
        "measured_effects": [
            {
                "detector": str(h.get("detector", ""))[:32],
                "status": str(h.get("status", ""))[:16],
                "posterior": round(float(h.get("posterior", 0) or 0), 2),
            }
            for h in p.hypotheses[:6]
        ],
        "active_adaptations": [
            {"strategy": d.strategy, "params": d.payload} for d in p.directives if d.source != "student_stated"
        ],
        "student_requests": [
            d.student_note for d in p.directives if d.source == "student_stated" and d.student_note
        ][:3],
        "session_hints": {
            k: v
            for k, v in (p.session_behaviour or {}).items()
            if isinstance(v, (int, float)) and k in {"interruptions", "away_seconds", "voice_turns"}
        },
    }
