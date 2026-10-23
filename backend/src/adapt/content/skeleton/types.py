"""Frozen public value objects for the generated content Pack contract.

The model output is never represented by these types directly. It first passes the strict parsers in
:mod:`adapt.content.skeleton.validation`; only then do we construct a Pack that callers may cache or
consume. The learner-facing contract deliberately carries no generated difficulty estimate: that
value is checked during authoring, and observed difficulty belongs to the existing item-calibration
path once an item has actual attempts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Verification = Literal["computed", "cross_checked", "unverified"]
PackSource = Literal["authored", "model", "simple"]
ItemKind = Literal["numeric", "mcq", "factual"]


@dataclass(frozen=True)
class KeyFact:
    id: str
    statement: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "statement": self.statement}


@dataclass(frozen=True)
class Misconception:
    id: str
    wrong_belief: str
    why_tempting: str
    correction: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "wrong_belief": self.wrong_belief,
            "why_tempting": self.why_tempting,
            "correction": self.correction,
        }


@dataclass(frozen=True)
class Concept:
    id: str
    name: str
    key_idea: str
    why_it_works: str
    prerequisites: tuple[str, ...]
    rung: int
    key_facts: tuple[KeyFact, ...]
    misconceptions: tuple[Misconception, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "key_idea": self.key_idea,
            "why_it_works": self.why_it_works,
            "prerequisites": list(self.prerequisites),
            "rung": self.rung,
            "key_facts": [fact.as_dict() for fact in self.key_facts],
            "misconceptions": [misconception.as_dict() for misconception in self.misconceptions],
        }


@dataclass(frozen=True)
class Option:
    id: str
    text: str
    misconception_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "misconception_id": self.misconception_id,
        }


@dataclass(frozen=True)
class ItemCalibration:
    """Private runtime calibration metadata; deliberately omitted from Pack JSON."""

    declared_difficulty: float
    observed_difficulty: float
    n_attempts: int = 0
    n_correct: int = 0
    active: bool = True
    retirement_reason: str | None = None


@dataclass(frozen=True)
class Item:
    id: str
    concept_id: str
    kind: ItemKind
    stem: str
    options: tuple[Option, ...]
    key: str
    pair_id: str
    hints: tuple[str, str, str]
    teachback: tuple[str, str, str]
    fact_ids: tuple[str, ...]
    verification: Verification
    calibration: ItemCalibration | None = field(default=None, repr=False, compare=False)

    @property
    def scorable(self) -> bool:
        """Only items with an explicit successful check may enter scoring or placement."""
        return self.verification in ("computed", "cross_checked") and (
            self.calibration is None or self.calibration.active
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "concept_id": self.concept_id,
            "kind": self.kind,
            "stem": self.stem,
            "options": [option.as_dict() for option in self.options],
            "key": self.key,
            "pair_id": self.pair_id,
            "hints": list(self.hints),
            "teachback": list(self.teachback),
            "fact_ids": list(self.fact_ids),
            "verification": self.verification,
        }


@dataclass(frozen=True)
class ObjectiveRef:
    """One approved guideline objective this skeleton was scoped to (Round 9).

    Ids come from the learner's approved guideline (``adapt/curriculum``),
    never from the model that authored the skeleton. A lesson built from
    this pack may only cite these ids.
    """

    id: str
    title: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title}


@dataclass(frozen=True)
class Pack:
    topic_key: str
    topic_title: str
    subject_family: str
    source: PackSource
    concepts: tuple[Concept, ...]
    prereq_edges: tuple[tuple[str, str], ...]
    items: tuple[Item, ...]
    #: Round 9 scope, recorded only when the pack was built for one. Absent
    #: (not null) from ``as_dict`` when unscoped, so the frozen contract --
    #: and every stored pack's content hash -- is unchanged for old packs.
    curriculum: str | None = None
    grade: str | None = None
    objectives: tuple[ObjectiveRef, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """A JSON-ready copy with exactly the fields in the frozen Pack contract."""
        out: dict[str, Any] = {
            "topic_key": self.topic_key,
            "topic_title": self.topic_title,
            "subject_family": self.subject_family,
            "source": self.source,
            "concepts": [concept.as_dict() for concept in self.concepts],
            "prereq_edges": [[source, target] for source, target in self.prereq_edges],
            "items": [item.as_dict() for item in self.items],
        }
        if self.curriculum:
            out["curriculum"] = self.curriculum
        if self.grade:
            out["grade"] = self.grade
        if self.objectives:
            out["objectives"] = [objective.as_dict() for objective in self.objectives]
        return out

    def scorable_items(self) -> tuple[Item, ...]:
        """The only items downstream scoring/placement code is permitted to consume."""
        return tuple(item for item in self.items if item.scorable)


class PackError(ValueError):
    """A candidate pack failed a deterministic validation gate."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def pack_from_dict(payload: dict[str, Any]) -> Pack:
    """Rebuild a Pack from its encrypted cache representation.

    The stored payload was validated before insertion. We still parse every field explicitly here so
    cache corruption or a future incompatible payload fails closed rather than yielding partially
    typed learner content.
    """
    concepts = tuple(
        Concept(
            id=str(row["id"]),
            name=str(row["name"]),
            key_idea=str(row["key_idea"]),
            why_it_works=str(row["why_it_works"]),
            prerequisites=tuple(str(value) for value in row["prerequisites"]),
            rung=int(row["rung"]),
            key_facts=tuple(
                KeyFact(id=str(fact["id"]), statement=str(fact["statement"]))
                for fact in row["key_facts"]
            ),
            misconceptions=tuple(
                Misconception(
                    id=str(misconception["id"]),
                    wrong_belief=str(misconception["wrong_belief"]),
                    why_tempting=str(misconception["why_tempting"]),
                    correction=str(misconception["correction"]),
                )
                for misconception in row["misconceptions"]
            ),
        )
        for row in payload["concepts"]
    )
    items = tuple(
        Item(
            id=str(row["id"]),
            concept_id=str(row["concept_id"]),
            kind=str(row["kind"]),  # type: ignore[arg-type]
            stem=str(row["stem"]),
            options=tuple(
                Option(
                    id=str(option["id"]),
                    text=str(option["text"]),
                    misconception_id=(
                        str(option["misconception_id"]) if option.get("misconception_id") else None
                    ),
                )
                for option in row["options"]
            ),
            key=str(row["key"]),
            pair_id=str(row["pair_id"]),
            hints=tuple(str(value) for value in row["hints"]),  # type: ignore[arg-type]
            teachback=tuple(str(value) for value in row["teachback"]),  # type: ignore[arg-type]
            fact_ids=tuple(str(value) for value in row["fact_ids"]),
            verification=str(row["verification"]),  # type: ignore[arg-type]
        )
        for row in payload["items"]
    )
    return Pack(
        topic_key=str(payload["topic_key"]),
        topic_title=str(payload["topic_title"]),
        subject_family=str(payload["subject_family"]),
        source=str(payload["source"]),  # type: ignore[arg-type]
        concepts=concepts,
        prereq_edges=tuple((str(edge[0]), str(edge[1])) for edge in payload["prereq_edges"]),
        items=items,
        curriculum=(str(payload["curriculum"]) if payload.get("curriculum") else None),
        grade=(str(payload["grade"]) if payload.get("grade") else None),
        objectives=tuple(
            ObjectiveRef(id=str(row["id"]), title=str(row["title"]))
            for row in (payload.get("objectives") or [])
            if isinstance(row, dict) and row.get("id") and row.get("title")
        ),
    )
