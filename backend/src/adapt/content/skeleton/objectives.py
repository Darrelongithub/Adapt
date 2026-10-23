"""Skeleton objectives: scoping, the subset validator, and lesson extras.

Round 9's fidelity rule in one place: a skeleton records the curriculum
and the approved objectives it was built for, and a lesson may only cite
objective ids its skeleton lists. The lesson-level
``common_misconceptions`` and ``suggested_practice`` are derived here from
the checked pack — validated, capped, and (for practice) scored by code
against the item's key and its accepted variants. Answer keys never
leave through :func:`suggested_practice`; :func:`score_practice` is the
only way an answer is judged, and it reveals the expected answer only
after an attempt.
"""

from __future__ import annotations

import re
from dataclasses import replace
from fractions import Fraction
from typing import Any, Mapping, Sequence

from .types import ObjectiveRef, Pack

MAX_MISCONCEPTIONS = 5
MAX_PRACTICE = 4
_MAX_TEXT = 300


def with_curriculum(
    pack: Pack,
    *,
    curriculum: str | None = None,
    grade: str | None = None,
    objectives: Sequence[Mapping[str, Any]] | None = None,
) -> Pack:
    """A copy of ``pack`` recording the scope it was built for."""
    refs = tuple(
        ObjectiveRef(id=str(o.get("id"))[:96], title=str(o.get("title"))[:160])
        for o in (objectives or [])
        if isinstance(o, Mapping) and o.get("id") and o.get("title")
    )[:30]
    return replace(
        pack,
        curriculum=(str(curriculum)[:24] if curriculum else pack.curriculum),
        grade=(str(grade)[:24] if grade else pack.grade),
        objectives=refs or pack.objectives,
    )


def skeleton_objective_ids(pack: Pack | Mapping[str, Any] | None) -> list[str]:
    if pack is None:
        return []
    if isinstance(pack, Pack):
        return [o.id for o in pack.objectives]
    return [str(o.get("id")) for o in (pack.get("objectives") or []) if isinstance(o, Mapping) and o.get("id")]


def validate_lesson_objectives(lesson_ids: Sequence[str], skeleton_ids: Sequence[str]) -> list[str]:
    """Violations of "lesson objectives ⊆ skeleton objectives"."""
    outside = sorted({str(v) for v in lesson_ids} - {str(v) for v in skeleton_ids})
    if not outside:
        return []
    return [f"lesson cites objective(s) outside its skeleton: {', '.join(outside)}"]


def _concepts(pack: Pack | Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if isinstance(pack, Pack):
        return [c.as_dict() for c in pack.concepts]
    return [c for c in (pack.get("concepts") or []) if isinstance(c, Mapping)]


def _items(pack: Pack | Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if isinstance(pack, Pack):
        return [i.as_dict() for i in pack.items]
    return [i for i in (pack.get("items") or []) if isinstance(i, Mapping)]


def _objectives_for_concept(pack: Pack | Mapping[str, Any], concept_id: str, concept_name: str) -> list[str]:
    """Objective ids the pack itself links to a concept, when it links any.

    A scoped pack's objectives may carry ``concept_id``; failing that, an
    objective whose title matches the concept name counts. No link is ever
    invented: an unlinked extra simply carries no objective ids.
    """
    raw = pack.objectives if isinstance(pack, Pack) else (pack.get("objectives") or [])
    out: list[str] = []
    for row in raw:
        data = row.as_dict() if isinstance(row, ObjectiveRef) else (row if isinstance(row, Mapping) else {})
        if not isinstance(data, Mapping) or not data.get("id"):
            continue
        if str(data.get("concept_id") or "") == concept_id and concept_id:
            out.append(str(data["id"]))
        elif concept_name and str(data.get("title") or "").casefold() == concept_name.casefold():
            out.append(str(data["id"]))
    return out


def common_misconceptions(pack: Pack | Mapping[str, Any], *, cap: int = MAX_MISCONCEPTIONS) -> list[dict[str, Any]]:
    """The pack's checked misconceptions, flattened for a lesson. Capped."""
    out: list[dict[str, Any]] = []
    for concept in _concepts(pack):
        concept_id = str(concept.get("id") or "")
        concept_name = str(concept.get("name") or "")
        for row in concept.get("misconceptions") or []:
            if not isinstance(row, Mapping):
                continue
            wrong = " ".join(str(row.get("wrong_belief") or "").split())[:_MAX_TEXT]
            correction = " ".join(str(row.get("correction") or "").split())[:_MAX_TEXT]
            if not wrong or not correction:
                continue
            out.append(
                {
                    "wrong_belief": wrong,
                    "correction": correction,
                    "concept": concept_name[:120],
                    "objective_ids": _objectives_for_concept(pack, concept_id, concept_name),
                }
            )
            if len(out) >= cap:
                return out
    return out


def suggested_practice(pack: Pack | Mapping[str, Any], *, cap: int = MAX_PRACTICE) -> list[dict[str, Any]]:
    """Scorable pack items as practice — stem and options, never the key."""
    concepts = {str(c.get("id")): str(c.get("name") or "") for c in _concepts(pack)}
    out: list[dict[str, Any]] = []
    for item in _items(pack):
        if str(item.get("verification") or "") not in ("computed", "cross_checked"):
            continue
        concept_id = str(item.get("concept_id") or "")
        out.append(
            {
                "item_id": str(item.get("id") or ""),
                "stem": " ".join(str(item.get("stem") or "").split())[:_MAX_TEXT],
                "kind": str(item.get("kind") or ""),
                "options": [
                    {"id": str(o.get("id") or ""), "text": str(o.get("text") or "")[:160]}
                    for o in (item.get("options") or [])
                    if isinstance(o, Mapping) and o.get("text")
                ],
                "objective_ids": _objectives_for_concept(pack, concept_id, concepts.get(concept_id, "")),
            }
        )
        if len(out) >= cap:
            break
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s./-]", " ", str(text or "").casefold()).split())


def _matches(answer: str, accepted: Sequence[str]) -> bool:
    given = _norm(answer)
    if not given:
        return False
    for candidate in accepted:
        want = _norm(candidate)
        if given == want:
            return True
        try:  # numeric equivalence: "0.5" answers "1/2"
            if Fraction(given.replace(" ", "")) == Fraction(want.replace(" ", "")):
                return True
        except (ValueError, ZeroDivisionError):
            continue
    return False


def score_practice(pack: Pack | Mapping[str, Any], item_id: str, answer: str) -> dict[str, Any] | None:
    """Judge one practice answer by code. ``None`` when the item is unknown.

    Accepted answers: the item's key, the keyed option's text, and their
    normalised/numeric equivalents. A wrong option tied to a misconception
    returns that misconception's correction — the teachable moment, not
    just a cross.
    """
    for item in _items(pack):
        if str(item.get("id") or "") != str(item_id):
            continue
        key = str(item.get("key") or "")
        options = [o for o in (item.get("options") or []) if isinstance(o, Mapping)]
        keyed = next((o for o in options if str(o.get("id") or "") == key), None)
        accepted = [key] + ([str(keyed.get("text"))] if keyed else [])
        correct = _matches(answer, accepted)
        feedback = ""
        if not correct:
            chosen = next((o for o in options if _matches(answer, [str(o.get("id") or ""), str(o.get("text") or "")])), None)
            misconception_id = str((chosen or {}).get("misconception_id") or "")
            if misconception_id:
                for concept in _concepts(pack):
                    for row in concept.get("misconceptions") or []:
                        if isinstance(row, Mapping) and str(row.get("id") or "") == misconception_id:
                            feedback = str(row.get("correction") or "")[:_MAX_TEXT]
        return {
            "correct": correct,
            "expected": (str(keyed.get("text")) if keyed else key)[:160],
            "feedback": feedback,
        }
    return None


__all__ = [
    "MAX_MISCONCEPTIONS",
    "MAX_PRACTICE",
    "common_misconceptions",
    "score_practice",
    "skeleton_objective_ids",
    "suggested_practice",
    "validate_lesson_objectives",
    "with_curriculum",
]
