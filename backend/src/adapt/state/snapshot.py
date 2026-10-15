"""The immutable view of one learner's state.

A snapshot is built once and never changes. That is not a style preference: it is what makes
"the lesson was planned from model version N" checkable, and what stops one module's write from
changing the facts another module is in the middle of reasoning about.

Immutability is real, not documented: nested mappings are ``MappingProxyType`` and nested
sequences are tuples, so ``snapshot.model["mastery"].append(...)`` raises rather than quietly
succeeding. :func:`thaw` gives a plain dict back at the edge (the API, the database), which is
where it is safe to hand out a mutable copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping, Sequence

#: Where a value came from. ``stated`` is the learner's own words (a preference), ``measured`` is
#: something the app scored, ``derived`` is a version of the model, ``noticed`` is observational
#: evidence. The distinction is shown in the UI and must not be blurred by a later write.
PROVENANCE = ("stated", "measured", "derived", "noticed")


def freeze(value: Any) -> Any:
    """Deep-freeze a JSON-shaped value (dict → mapping proxy, list → tuple)."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, set | frozenset):  # type: ignore[operator]
        return frozenset(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    """The inverse of :func:`freeze`. Safe to mutate the result."""
    if isinstance(value, Mapping):
        return {str(k): thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    if isinstance(value, (frozenset, set)):
        return sorted(thaw(v) for v in value)
    return value


@dataclass(frozen=True)
class TopicMastery:
    """One topic's estimate, with the evidence behind it.

    ``n`` is the number of *controlled* observations that went into the estimate, and ``early`` is
    the label the UI shows (under eight observations). The interval is the range the evidence
    supports — see ``learning.bkt.credible_interval``.
    """

    topic_id: str
    title: str
    p_know: float
    band: str
    n: int
    correct: int
    ci: tuple[float, float]
    early: bool
    #: ISO-8601 string: the model payload is JSON, so this is what a fold writes and what a
    #: rebuild reads back. Typed as a string rather than smuggled through as a datetime.
    last_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "p_know": round(self.p_know, 3),
            "band": self.band,
            "n": self.n,
            "observations": self.n,
            "correct": self.correct,
            "ci": [round(self.ci[0], 3), round(self.ci[1], 3)],
            "evidence": "early signal" if self.early else "measured",
            "last_at": self.last_at,
        }


@dataclass(frozen=True)
class LearnerSnapshot:
    """Everything a module may read about one learner, frozen at one model version."""

    learner_id: str
    model_version: int
    built_at: datetime | None
    evidence_count: int
    evidence_upto: str | None
    model: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    preferences: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    #: Teaching memory, learner-wide: what has been explained, shown, asked and got wrong, so
    #: nothing repeats by accident. Learner-wide rather than per-session because "don't explain
    #: fractions the same way again" is a promise that has to survive the lesson ending.
    explained: frozenset[str] = frozenset()
    examples_shown: frozenset[str] = frozenset()
    asked: tuple[str, ...] = ()
    misconceptions: tuple[str, ...] = ()
    #: The same memory narrowed to the session the snapshot was built for, when one was given.
    #: A module that wants "what happened in *this* lesson" reads this instead of guessing from
    #: timestamps; a module that wants continuity reads the fields above.
    this_session: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    session: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    #: Method, analogy and encrypted text for previous AI 3 explanations, in display order.
    teaching_memory_records: tuple[Mapping[str, Any], ...] = ()

    # -- convenience readers ------------------------------------------------- #
    @property
    def mastery(self) -> tuple[TopicMastery, ...]:
        rows = self.model.get("topics") or ()
        return tuple(
            TopicMastery(
                topic_id=str(r.get("topic_id") or ""),
                title=str(r.get("title") or ""),
                p_know=float(r.get("p_know") or 0.0),
                band=str(r.get("band") or "new"),
                n=int(r.get("n") or 0),
                correct=int(r.get("correct") or 0),
                ci=(float((r.get("ci") or (0.0, 1.0))[0]), float((r.get("ci") or (0.0, 1.0))[1])),
                early=bool(r.get("early", True)),
                last_at=r.get("last_at"),
            )
            for r in rows
            if isinstance(r, Mapping)
        )

    def topic(self, topic_id: str | None) -> TopicMastery | None:
        if not topic_id:
            return None
        return next((m for m in self.mastery if m.topic_id == topic_id), None)

    def p_know(self, topic_id: str | None, *, default: float = 0.2) -> float:
        row = self.topic(topic_id)
        return float(row.p_know) if row else default

    def already_explained(self, ref: str) -> bool:
        return ref in self.explained

    def already_asked(self, question: str) -> bool:
        norm = " ".join(str(question).lower().split())
        return any(norm == " ".join(str(q).lower().split()) for q in self.asked)

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "model_version": self.model_version,
            "built_at": self.built_at.isoformat() if self.built_at else None,
            "evidence_count": self.evidence_count,
            "evidence_upto": self.evidence_upto,
            "model": thaw(self.model),
            "preferences": thaw(self.preferences),
            "teaching_memory": {
                "explained": sorted(self.explained),
                "examples_shown": sorted(self.examples_shown),
                "asked": list(self.asked),
                "misconceptions": list(self.misconceptions),
            },
            "teaching_memory_this_session": {k: list(v) for k, v in sorted(self.this_session.items())},
            "teaching_memory_records": [thaw(record) for record in self.teaching_memory_records],
            "session": thaw(self.session),
        }


def snapshot_from_row(
    *,
    learner_id: str,
    version_row: Any,
    preferences: Mapping[str, Any],
    memory: Mapping[str, Sequence[str]],
    session: Mapping[str, Any],
    session_memory: Mapping[str, Sequence[str]] | None = None,
    teaching_memory_records: Sequence[Mapping[str, Any]] = (),
) -> LearnerSnapshot:
    """Build a snapshot from the store's rows. Used by ``LearnerState.get_snapshot``."""
    return LearnerSnapshot(
        learner_id=learner_id,
        model_version=int(getattr(version_row, "version", 0) or 0),
        built_at=getattr(version_row, "created_at", None),
        evidence_count=int(getattr(version_row, "evidence_count", 0) or 0),
        evidence_upto=getattr(version_row, "evidence_upto", None),
        model=freeze(getattr(version_row, "model", None) or {}),
        preferences=freeze(dict(preferences or {})),
        explained=frozenset(memory.get("explained") or ()),
        examples_shown=frozenset(memory.get("examples_shown") or ()),
        asked=tuple(memory.get("asked") or ()),
        misconceptions=tuple(memory.get("misconceptions") or ()),
        session=freeze(dict(session or {})),
        this_session=freeze({k: list(v) for k, v in (session_memory or {}).items()}),
        teaching_memory_records=tuple(freeze(dict(row)) for row in teaching_memory_records),
    )
