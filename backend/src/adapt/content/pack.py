"""One concept's teaching variants, its matched question pair, its hints and its checklist (§7.2).

§7.2, per concept: "three variants: written explanation, visual (JSON spec for the generic templates in
Phase 5), worked example; plus a matched question pair (A/B) in one format, a graded 3-hint set, and a
3-point teach-back checklist."

**Every part of a pack is traceable to authored material.** The chain kits already contain, for each
concept, a rung-1 recognise task (whose near-misses *are* the misconceptions), a rung-2 recall task
(whose ``why`` explains the key fact), a rung-3 standard case, a rung-4 transfer case and a rung-5
explain-why checklist. That is the whole pack, already written, by a person, and already graded by the
repo's own scorers. This module assembles it:

* **written** — the concept's authored description, plus one learning objective per authored rung,
  quoting the task stem and the brief's own rung demand label from ``ladder.RUNGS``;
* **visual** — :func:`adapt.learning.visuals.build_visual` applied to that description, so the spec is
  the *same shape the Phase 5 templates render* and the same component draws it (this is what the
  brief means by "JSON spec for the generic templates in Phase 5");
* **worked example** — four steps, each from an authored rung: what is asked, the method (the rung-2
  ``why``), the answer (the rung-3 ``why``), and the trap to rule out (the rung-1 contrast). Built here
  rather than by ``worked.generate_example`` because that function's option-shaped path needs per-option
  ``why_wrong`` reasons the chain kits do not carry, and its fallback for a choice item with none is
  two steps. The example is then passed through ``worked.verify_example`` anyway — the existing gate
  for placeholders, key disagreement and early answer leaks — so a pack example is checked by the same
  code that checks the bank's;
* **matched pair (A/B)** — the rung-3 and rung-4 tasks of the *same concept*, which is the pair the
  ladder itself asks. They are matched on concept and on format and are **not** matched on difficulty,
  and the pack says exactly that rather than implying otherwise: rung 3 and rung 4 are different
  demands by design (§6.4), so a comparison between the legs has to use expected performance — which
  ``subjects.methodtest.expected_accuracy`` already computes from the learner's own ``p_know`` — and
  never the raw scores. Claiming they were equally hard would be the "visual learner" mistake §6.5
  exists to prevent;
* **hints** — three, graded nudge / method / near-answer, each quoting an authored rung. Written here
  rather than by ``worked.ensure_hints`` because that function's near-answer hint asserts "three of
  them were written to catch a specific mistake", which is only true of bank items that carry
  per-option ``why_wrong`` reasons; the chain kits do not, and a hint that makes a claim the pack
  cannot support is the generic-hint defect §4.1 item 4 in a new coat;
* **checklist** — the rung-5 kit's three authored points, in the shape ``teachback.checklist_from_rubric``
  reads, so the pack's teach-back is graded by the same rule grader as every other teach-back.

**Difficulty is not declared.** The chain kits carry no difficulty (the ladder places by rung, not by
a p-value), and inventing one here would put a number in the pack that no authored source supports.
``validate_item`` therefore has nothing to reject on the "far from its declared band" rule for these
items, and says so, rather than the pack guessing a band and the checker agreeing with its own guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..assessment import teachback as TB
from ..assessment import worked as W
from ..learning.visuals import build_visual
from ..subjects.methodtest import expected_accuracy
from ..subjects.ladder import rung_row

#: Bumped when the *shape* of a pack changes, so a cached pack can tell "the spec moved" from "the
#: content moved" (§7.6: regenerate only on spec change). A test asserts that bumping it invalidates.
PACK_SPEC = "pack/1"

#: The three graded hint levels, from ``assessment.worked.HINT_GRADES`` — quoted rather than
#: re-declared so a pack hint and a bank hint are graded the same way.
HINT_GRADES: tuple[str, ...] = W.HINT_GRADES

#: §6.5's rule, stated where it is used rather than trusted from memory.
MATCHED_NOTE = (
    "matched on concept and on question format; NOT matched on difficulty, because rung 3 and rung 4 "
    "are different demands by design. Compare the legs on expected performance "
    "(subjects.methodtest.expected_accuracy), never on raw scores."
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ConceptPack:
    """Everything needed to teach one concept, and where each part came from."""

    concept_id: str
    concept_title: str
    topic_key: str
    topic_title: str
    sub_area: str
    available: bool
    reason: str
    spec: str = PACK_SPEC
    variants: dict[str, Any] = field(default_factory=dict)
    pair: dict[str, Any] = field(default_factory=dict)
    hints: list[dict[str, Any]] = field(default_factory=list)
    checklist: list[dict[str, Any]] = field(default_factory=list)
    misconceptions: list[dict[str, Any]] = field(default_factory=list)
    objectives: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rejections: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "concept_title": self.concept_title,
            "topic_key": self.topic_key,
            "topic_title": self.topic_title,
            "sub_area": self.sub_area,
            "available": self.available,
            "reason": self.reason,
            "spec": self.spec,
            "variants": dict(self.variants),
            "pair": dict(self.pair),
            "hints": [dict(h) for h in self.hints],
            "checklist": [dict(c) for c in self.checklist],
            "misconceptions": [dict(m) for m in self.misconceptions],
            "objectives": [dict(o) for o in self.objectives],
            "notes": list(self.notes),
            "rejections": [dict(r) for r in self.rejections],
        }


# --------------------------------------------------------------------------- #
# shapes
# --------------------------------------------------------------------------- #


def bank_shaped(kit_item: Mapping[str, Any], *, concept: Any, rung: int) -> dict[str, Any] | None:
    """A chain kit task in the bank's item shape, so the bank's own tools can read it.

    The chain kit writes a choice task as ``{kind: "choice", stem, options, answer, why}``; the bank
    writes one as ``{kind: "mcq", stem, choices: [{text, correct, why_wrong}], answer_spec: {...}}``.
    Both are correct in their own module and neither can be dropped, so the conversion lives here and
    says what it cannot carry: the kit has no per-option ``why_wrong``, so those fields are left
    absent rather than filled with something plausible.
    """
    if not isinstance(kit_item, Mapping):
        return None
    kind = str(kit_item.get("kind") or "")
    stem = str(kit_item.get("stem") or "").strip()
    if not stem:
        return None
    if kind != "choice":
        return None
    options = [str(o) for o in (kit_item.get("options") or [])]
    answer = kit_item.get("answer")
    if not options or not isinstance(answer, int) or not 0 <= answer < len(options):
        return None
    return {
        "id": f"pk_{concept.id}_r{rung}",
        "kind": "mcq",
        "stem": stem,
        "experience_mode": "practice",
        "choices": [
            {"text": text, "correct": i == answer, **({} if i == answer else {"why_wrong": None})}
            for i, text in enumerate(options)
        ],
        "answer_spec": {"correct_index": int(answer)},
        # The authored explanation travels with the item as `why`, which is what the bank calls it
        # and what `games.public_task` strips before a browser sees it. Dropping it made every
        # generated pair item look like an item with no reasoning of its own.
        "why": str(kit_item.get("why") or ""),
        "hints": [],
        "solution_steps": [],
        "rung": rung,
        "demand": rung_row(rung)["demand"],
        "topic_id": concept.bank_code or "",
        "tags": ["generated_pack", f"rung{rung}"],
        "source": f"the authored rung-{rung} kit task for {concept.title}",
    }


def _written_variant(concept: Any) -> dict[str, Any]:
    description = " ".join(str(concept.description or "").split())
    sentences = [s for s in _SENTENCE.split(description) if s.strip()]
    return {
        "text": sentences,
        "word_count": len(description.split()),
        "source": "the authored chain description for this concept",
    }


def _visual_variant(concept: Any, *, seed: str) -> dict[str, Any]:
    """The Phase 5 template spec, built by the module the templates live in.

    ``build_visual`` never invents a fact: it reshapes the sentences it was given and reports
    ``unfilled`` for a slot it has nothing for. So a concept whose description has no dates gets a
    flow, and the spec says which template was chosen and why — which is the answer to "why this
    picture?" that a learner is entitled to.
    """
    visual = build_visual(
        title=str(concept.title),
        text=" ".join(str(concept.description or "").split()),
        seed=seed,
    )
    return visual.as_dict()


def _worked_variant(concept: Any, kit: Mapping[int, Mapping[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Four steps, one per authored rung, then the bank's own example gate.

    Returns ``(variant, notes)``. ``notes`` records anything the example could not carry — an absent
    rung-2 explanation, say — because a thinner example that admits it is better than a full-looking
    one that hides a gap.
    """
    notes: list[str] = []
    r3 = kit.get(3) or {}
    r2 = kit.get(2) or {}
    r1 = kit.get(1) or {}
    stem = str(r3.get("stem") or "").strip()
    if not stem:
        return ({"steps": [], "verified": False, "flags": ["no_rung3_task"]}, ["no authored rung-3 task, so there is no case to work through"])
    options = [str(o) for o in (r3.get("options") or [])]
    answer = r3.get("answer")
    correct = options[answer] if isinstance(answer, int) and 0 <= answer < len(options) else ""
    # Step 1 restates the question in the learner's own words -- it does *not* repeat the stem.
    # Repeating the stem put the question's own data (and, for "what is the median of 5, 7, 7, 9,
    # 12?", the answer itself) into the first step of the worked example, which the example gate
    # correctly reads as an answer leak. Restating is also the better lesson: the first move on any
    # question is saying what it asks, and the stem stays where it belongs, on the question.
    restated = str(r2.get("stem") or "").strip()
    steps: list[dict[str, Any]] = [
        {
            "index": 1,
            "text": (
                f"First say what is being asked, in your own words: {restated}"
                if restated
                else "First say what is being asked, in your own words, before looking at any option."
            ),
            "why": "A question you have restated is one a near-miss option cannot pull you off.",
        }
    ]
    if str(r2.get("why") or "").strip():
        steps.append(
            {
                "index": 2,
                "text": f"The fact that decides it: {' '.join(str(r2['why']).split())}",
                "why": "This is the key fact for the concept, in the words the material uses for it.",
            }
        )
    else:
        notes.append("no authored rung-2 explanation, so the worked example has no method step")
    if stem and stem not in " ".join(s["text"] for s in steps):
        pack_note = "the worked example restates the question rather than quoting the stem, so the stem stays on the question"
    if correct:
        steps.append(
            {
                "index": len(steps) + 1,
                "text": f"Applying it here gives: {correct}",
                "why": " ".join(str(r3.get("why") or "").split()) or "the answer the rung-3 task marks correct",
            }
        )
    near = [str(o) for i, o in enumerate(r1.get("options") or []) if i != r1.get("answer") and str(o).strip()]
    if near and str(r1.get("why") or "").strip():
        steps.append(
            {
                "index": len(steps) + 1,
                "text": f"What to rule out: {'; '.join(near[:3])}.",
                "why": " ".join(str(r1["why"]).split()),
            }
        )
    else:
        notes.append("no authored rung-1 near-miss, so the worked example has no trap step")
    notes.append(pack_note)
    normalised = W.normalize_steps(steps)
    verdict = W.verify_example(_example_probe(concept, kit, correct), normalised)
    return (
        {
            "steps": normalised,
            "verified": bool(verdict.ok),
            "flags": list(verdict.flags),
        },
        notes,
    )


def _example_probe(concept: Any, kit: Mapping[int, Mapping[str, Any]], correct: str) -> dict[str, Any]:
    """A minimal item for ``worked.verify_example`` to check the example against.

    The probe carries only what the gate reads: the stem and the key. It exists so the pack's example
    is checked by the same code the bank's examples are checked by, rather than by a second, weaker
    copy of the rules that happens to agree with this module.
    """
    r3 = kit.get(3) or {}
    return {
        "stem": str(r3.get("stem") or ""),
        "choices": [{"text": correct, "correct": True}],
        "answer_spec": {"correct_index": 0},
    }


def _hints(concept: Any, kit: Mapping[int, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Three graded hints, each quoting an authored rung.

    Grade order is ``assessment.worked.HINT_GRADES``: nudge (restate the task), method (the first
    move), near-answer (how to check without the answer). The rung-2 recall stem is what the nudge
    restates, the rung-2 ``why`` is the method, and the rung-1 contrast is the near-answer trap — all
    authored, so none of the three is the generic sentence §4.1 item 4 removed.
    """
    r1, r2, r3 = kit.get(1) or {}, kit.get(2) or {}, kit.get(3) or {}
    out: list[dict[str, Any]] = []
    nudge = str(r2.get("stem") or "").strip()
    out.append(
        {
            "grade": HINT_GRADES[0],
            "text": (
                f"In your own words: {nudge}" if nudge else "Say what the question is asking before you answer it."
            ),
            "source": "the authored rung-2 recall task",
        }
    )
    method = " ".join(str(r2.get("why") or "").split())
    out.append(
        {
            "grade": HINT_GRADES[1],
            "text": (
                f"The method, in one line: {method}" if method else "Start from the key fact for this concept."
            ),
            "source": "the authored rung-2 explanation",
        }
    )
    near = [str(o) for i, o in enumerate(r1.get("options") or []) if i != r1.get("answer") and str(o).strip()]
    contrast = " ".join(str(r1.get("why") or "").split())
    out.append(
        {
            "grade": HINT_GRADES[2],
            "text": (
                f"Before you commit, rule out the near-miss ({near[0]}): {contrast}"
                if near and contrast
                else "Check your answer against the key fact for this concept before you commit."
            ),
            "source": "the authored rung-1 near-miss and its contrast",
        }
    )
    if str(r3.get("why") or "").strip():
        out.append(
            {
                "grade": "answer_reason",
                "text": " ".join(str(r3["why"]).split()),
                "source": "the authored rung-3 explanation, shown only after an attempt",
            }
        )
    return out


def _checklist(concept: Any, kit: Mapping[int, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """The rung-5 kit's three authored points, in the shape the rule grader reads."""
    r5 = kit.get(5) or {}
    rubric = r5.get("rubric") or {}
    points = [dict(p) for p in (rubric.get("required_concepts") or []) if isinstance(p, Mapping)]
    criteria = TB.checklist_from_rubric(rubric)
    notes: list[str] = []
    if len(points) > TB.CHECKLIST_SIZE:
        notes.append(f"the rung-5 rubric carries {len(points)} points; the checklist grader uses the first {TB.CHECKLIST_SIZE}")
        points = points[: TB.CHECKLIST_SIZE]
    if not points:
        notes.append("no authored rung-5 checklist, so this concept has no teach-back checklist")
    out = []
    for point, criterion in zip(points, criteria):
        out.append(
            {
                "concept": str(point.get("concept") or ""),
                "statement": str(point.get("statement") or ""),
                "idea": str(point.get("idea") or ""),
                "synonyms": [str(s) for s in (point.get("synonyms") or [])],
                "criterion": {
                    "id": criterion.id,
                    "statement": criterion.statement,
                    "idea": criterion.idea,
                    "accepts": list(criterion.accepts),
                    "required": bool(criterion.required),
                    "wordings": [sorted(w) for w in criterion.wordings()],
                },
            }
        )
    return out, notes


# --------------------------------------------------------------------------- #
# the builder
# --------------------------------------------------------------------------- #


def build_concept_pack(concept: Any, *, topic_key: str, topic_title: str) -> ConceptPack:
    """Assemble one concept's pack from its authored kit."""
    kit = {int(r): dict(v) for r, v in (getattr(concept, "kit", {}) or {}).items() if isinstance(v, Mapping)}
    pack = ConceptPack(
        concept_id=str(getattr(concept, "id", "")),
        concept_title=str(getattr(concept, "title", "")),
        topic_key=topic_key,
        topic_title=topic_title,
        sub_area=str(getattr(concept, "sub_area", "")),
        available=bool(kit),
        reason=(
            f"built from the authored chain kit for {getattr(concept, 'title', '')} "
            f"({len(kit)} of 5 rungs authored)"
            if kit
            else "no authored kit for this concept"
        ),
    )
    if not kit:
        pack.notes.append("no authored kit, so no variants, pair, hints or checklist could be built")
        return pack

    pack.variants = {
        "written": _written_variant(concept),
        "visual": _visual_variant(concept, seed=f"{topic_key}:{concept.id}"),
    }
    worked, worked_notes = _worked_variant(concept, kit)
    pack.variants["worked_example"] = worked
    pack.notes.extend(worked_notes)
    if not worked.get("verified"):
        pack.rejections.append(
            {
                "code": "worked_example_unverified",
                "detail": f"the worked example for {pack.concept_id} failed its own gate: {', '.join(worked.get('flags') or [])}",
            }
        )

    a = bank_shaped(kit.get(3) or {}, concept=concept, rung=3)
    b = bank_shaped(kit.get(4) or {}, concept=concept, rung=4)
    pair: dict[str, Any] = {
        "format": "choice",
        "matched": MATCHED_NOTE,
        "expected_accuracy_note": (
            "compare the legs with subjects.methodtest.expected_accuracy(p_know, difficulty) — the same "
            "expected-performance rule the method test uses, so a strong learner's 2/2 and 2/2 is a gain "
            "of about zero rather than a preference"
        ),
    }
    if a is None:
        pair["A"] = None
        pack.rejections.append({"code": "no_rung3_task", "detail": f"{pack.concept_id} has no authored rung-3 task"})
    else:
        pair["A"] = a
    if b is None:
        pair["B"] = None
        pack.rejections.append({"code": "no_rung4_task", "detail": f"{pack.concept_id} has no authored rung-4 task"})
    else:
        pair["B"] = b
    pack.pair = pair

    pack.hints = _hints(concept, kit)
    pack.checklist, checklist_notes = _checklist(concept, kit)
    pack.notes.extend(checklist_notes)
    pack.misconceptions = [
        {
            "claim": str(o),
            "contrast": " ".join(str((kit.get(1) or {}).get("why") or "").split()),
            "source": "the authored rung-1 near-miss",
        }
        for i, o in enumerate((kit.get(1) or {}).get("options") or [])
        if i != (kit.get(1) or {}).get("answer") and str(o).strip()
    ]
    pack.objectives = [
        {
            "rung": rung,
            "key": rung_row(rung)["key"],
            "demand": rung_row(rung)["demand"],
            "task_stem": str((kit.get(rung) or {}).get("stem") or ""),
        }
        for rung in (1, 2, 3, 4, 5)
        if kit.get(rung)
    ]
    return pack


def build_pack(breakdown: Any) -> list[ConceptPack]:
    """Every concept's pack for a breakdown, in the breakdown's prerequisite order."""
    if not getattr(breakdown, "available", False):
        return []
    return [
        build_concept_pack(concept, topic_key=breakdown.topic_key, topic_title=breakdown.title)
        for concept in breakdown.concepts
    ]


def expected_accuracy_for(pack: ConceptPack, *, p_know: float) -> dict[str, Any]:
    """The expected performance for each leg of a pair, from the learner's own belief.

    Kept here (rather than in the caller) so the pair's honesty claim and the number that backs it are
    in one place. ``difficulty`` is the rung's position in the ladder's demand order, which is the
    only difficulty signal the authored kits carry — and it is labelled as such rather than presented
    as a calibrated p-value.
    """
    out: dict[str, Any] = {}
    for leg in ("A", "B"):
        item = pack.pair.get(leg)
        if not item:
            out[leg] = None
            continue
        rung = int(item.get("rung") or 3)
        # A rung's place in the ladder, 1..5, mapped to 0..1. Not a calibrated difficulty: the label
        # says where it came from and the pack's difficulty_note says it is not one.
        difficulty = (rung - 1) / 4.0
        out[leg] = {
            "rung": rung,
            "difficulty_from_rung": difficulty,
            "expected_accuracy": round(expected_accuracy(p_know, difficulty), 4),
        }
    out["note"] = (
        "difficulty is the rung's position in the ladder's demand order, not a calibrated item "
        "difficulty; the authored kits carry no p-value and none was invented here"
    )
    return out
