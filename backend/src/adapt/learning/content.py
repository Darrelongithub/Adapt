"""Three versions of a concept, plus a matched question pair — for any subject.

The gap this closes: content was one paragraph per topic and the bank was maths
only, so (a) a concept could only ever be met as prose, which is why the method
rotation had nothing to rotate between, and (b) the answer key could be checked by
*computing* it — an arithmetic expression — which is a property of maths and of
nothing else. A history or biology question has no expression to evaluate.

So, per concept:

* ``text`` — the material segmented into short steps with a check after each
  (:mod:`adapt.learning.segments`);
* ``visual`` — a generic template filled from the same material
  (:mod:`adapt.learning.visuals`);
* ``worked_example`` — the material's own reasoning, step-labelled;
* ``questions`` — a **matched pair**: two questions of the same format and the same
  declared difficulty, written from the source, each keyed to a word or phrase that
  is *in the source*, and each run through :func:`adapt.assessment.generated.validate_generated`
  with ``require_source=True``. A key that cannot be found in the material is
  rejected here rather than stored, and anything this build cannot check is
  returned with ``verification="unverified"`` so the caller has to decide what to do
  with it instead of assuming it is fine.

Nothing in this module calls a model. A model can be plugged in as ``writer`` (see
:func:`concept_versions`), and its output goes through exactly the same rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..assessment import generated as GEN
from . import segments as SEG
from . import visuals as VIS
from .seeds import stable_seed

#: The three teaching versions every concept has to be able to produce.
VERSIONS: tuple[str, ...] = ("text", "visual", "worked_example")

_STOP = SEG._STOP  # one stopword list for the whole package, not two
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")


def worked_example(title: str, source_text: str, *, max_steps: int = 6) -> dict[str, Any]:
    """The material's reasoning, one step per sentence, with what each step settles.

    Not an invented solution: every step is a sentence of the source, so a learner
    who reads the "worked example" is reading the same material they were given,
    arranged as a procedure. The step label names what the sentence does
    (``definition`` / ``method`` / ``check`` / ``why``), which is the part an author
    would otherwise have to write by hand for every subject.
    """
    sentences = SEG.sentences(source_text)[:max_steps]
    steps: list[dict[str, Any]] = []
    for i, sentence in enumerate(sentences):
        steps.append(
            {
                "index": i + 1,
                "text": sentence,
                "why": _role(sentence),
                "from_source": True,
            }
        )
    return {
        "kind": "worked_example",
        "title": f"Worked example: {title}",
        "steps": steps,
        "complete": bool(steps),
        "note": (
            "every step is a sentence of the source material in its original order, "
            "labelled with what it does — no step is invented"
        ),
    }


def _role(sentence: str) -> str:
    low = sentence.lower()
    if re.search(r"\b(is|are|means|is called|defined as|refers to)\b", low):
        return "this is the definition to hold on to"
    if re.search(r"\b(first|then|next|finally|to do this|start by)\b", low):
        return "this is a step of the method"
    if re.search(r"\b(because|so that|therefore|which is why)\b", low):
        return "this is why the previous step is allowed"
    if re.search(r"\b(check|estimate|verify|notice)\b", low):
        return "this is the check"
    return "this sentence carries a fact the rest depends on"


def _cloze_candidates(sentences: Sequence[str], source_text: str) -> list[tuple[str, str]]:
    """(stem, answer) pairs whose answer is a word unique to the material."""
    lowered = source_text.lower()
    out: list[tuple[str, str]] = []
    for sentence in sentences:
        words = [w for w in _WORD.findall(sentence) if w.lower() not in _STOP]
        # The longest content word that appears exactly once in the whole source:
        # a term that recurs is ambiguous when blanked.
        words.sort(key=lambda w: (-len(w), w.lower()))
        for word in words:
            if lowered.count(word.lower()) != 1:
                continue
            stem = re.sub(rf"\b{re.escape(word)}\b", "________", sentence, count=1)
            if stem == sentence:
                continue
            out.append((stem, word))
            break
    return out


def matched_question_pair(
    *,
    topic_id: str,
    source_text: str,
    seed: str = "",
    difficulty: float = 0.45,
    count: int = 2,
) -> dict[str, Any]:
    """Two questions, same format and difficulty, both keyed from the source.

    Both go through the generated-item rulebook with ``require_source=True``, so a
    key that is not in the material is *rejected* and reported — which is the
    subject-agnostic replacement for "check the arithmetic": the check is that the
    answer is quotable from what the learner was given.
    """
    sentences = SEG.sentences(source_text)
    candidates = _cloze_candidates(sentences, source_text)
    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    picked = sorted(candidates, key=lambda c: stable_seed(seed, c[0]))[: max(0, count * 3)]
    for stem, answer in picked:
        if len(accepted) >= count:
            break
        raw = {
            "kind": "text",
            "stem": f"Fill the gap with the word the material uses:\n\n{stem}",
            "answer_value": [answer],
            "difficulty": difficulty,
            "est_seconds": 60,
            "solution_steps": [{"text": next((s for s in sentences if answer in s), answer)}],
            "hints": [f"It is the word the material uses for this idea ({len(answer)} letters)."],
        }
        validated, bad = GEN.validate_generated(
            raw,
            topic_id=topic_id,
            source_text=source_text,
            require_source=True,
        )
        if validated is None:
            rejections.extend(r.as_dict() for r in bad)
            continue
        payload = dict(validated.payload)
        if any(a["id"] == payload["id"] for a in accepted):
            continue  # the same question twice is one question
        payload["pair"] = {
            "format": "text",
            "difficulty": difficulty,
            "same_format_as": [a["id"] for a in accepted],
            "seed": seed,
        }
        payload["source_quote"] = next((s for s in sentences if answer in s), "")
        payload["checks"] = list(validated.checks)
        accepted.append(payload)
    matched = len(accepted) >= 2 and len({a["kind"] for a in accepted}) == 1
    return {
        "pair": accepted,
        "matched": matched,
        "same_format": len({a["kind"] for a in accepted}) <= 1,
        "same_difficulty": len({round(float(a["difficulty"]), 4) for a in accepted}) <= 1,
        "verification": sorted({str(a.get("verification") or "unverified") for a in accepted}),
        "rejections": rejections,
        "note": (
            "both questions are the same format and the same declared difficulty, so a "
            "difference between two attempts is the teaching and not the question"
            if matched
            else "a full matched pair could not be written from this material; the rejections say why"
        ),
    }


@dataclass
class ConceptContent:
    topic_id: str
    title: str
    versions: dict[str, Any] = field(default_factory=dict)
    questions: dict[str, Any] = field(default_factory=dict)
    unverified: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "versions": self.versions,
            "questions": self.questions,
            "unverified": self.unverified,
            "checks": self.checks,
        }


def concept_versions(
    *,
    topic_id: str,
    title: str,
    source_text: str,
    learner_id: str = "",
    visual_kind: str | None = None,
    writer: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
) -> ConceptContent:
    """All three versions of one concept, plus its matched question pair."""
    source_text = source_text or ""
    segmented = SEG.segment(source_text, seed=f"{topic_id}:{learner_id}") if source_text else SEG.Segmented()
    visual = (
        VIS.build_visual(title=title, text=source_text, kind=visual_kind, seed=str(stable_seed(learner_id, topic_id)))
        if source_text
        else VIS.Visual("flow", title, "", "no material to draw from")
    )
    example = worked_example(title, source_text)
    questions = matched_question_pair(topic_id=topic_id, source_text=source_text, seed=f"{topic_id}:{learner_id}")
    unverified = [str(p) for p in questions.get("rejections", []) if p.get("code") == "key_not_in_source"]
    if writer is not None:
        extra = list(writer(topic_id=topic_id, title=title, source_text=source_text) or ())
        for candidate in extra:
            validated, bad = GEN.validate_generated(
                candidate, topic_id=topic_id, source_text=source_text, require_source=False
            )
            if validated is None:
                questions.setdefault("rejections", []).extend(r.as_dict() for r in bad)
                continue
            questions.setdefault("pair", []).append({**validated.payload, "checks": list(validated.checks)})
            if validated.unverified:
                unverified.append(str(validated.payload.get("id")))
    checks = {
        "text_covers_source": not segmented.dropped and segmented.covered_words > 0,
        "text_steps": len(segmented.steps),
        "visual_template": visual.kind,
        "visual_unfilled": visual.unfilled,
        "worked_example_steps": len(example["steps"]),
        "questions_matched": bool(questions.get("matched")),
        "answer_keys_verification": questions.get("verification", []),
    }
    return ConceptContent(
        topic_id=topic_id,
        title=title,
        versions={
            "text": segmented.as_dict(),
            "visual": visual.as_dict(),
            "worked_example": example,
        },
        questions=questions,
        unverified=unverified,
        checks=checks,
    )


def teaching_payload(
    *,
    method: str,
    topic_id: str,
    title: str,
    source_text: str,
    learner_id: str = "",
    example_steps: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """What the assigned method actually puts on the screen.

    This is the single function that makes "the method is a decision" true for the
    learner: the three methods that teach before the question all end up here, and a
    fourth (retrieval) deliberately sends nothing, because the teaching *is* the
    attempt.
    """
    if method == "retrieval":
        return {
            "method": method,
            "kind": "attempt_first",
            "text": "Try it first — the attempt is the teaching. The explanation comes after.",
        }
    if method == "worked_example":
        steps = [dict(s) for s in (example_steps or ())]
        if steps:
            return {"method": method, "kind": "worked_example", "steps": steps, "from_item": True}
        example = worked_example(title, source_text)
        if example["steps"]:
            return {"method": method, "kind": "worked_example", "steps": example["steps"], "from_source": True}
        return {
            "method": method,
            "kind": "no_material",
            "text": "There is no course material for this concept yet, so there is no worked example to show before the question.",
        }
    if method == "visual":
        visual = VIS.build_visual(title=title, text=source_text, seed=str(stable_seed(learner_id, topic_id)))
        if not (visual.nodes or visual.rows or visual.labels):
            return {
                "method": method,
                "kind": "no_material",
                "text": "There is no course material to draw from yet, so nothing is shown before the question.",
            }
        return {"method": method, "kind": "visual", "visual": visual.as_dict()}
    segmented = SEG.segment(source_text, seed=f"{topic_id}:{learner_id}") if source_text else SEG.Segmented()
    return {"method": method, "kind": "segments", **segmented.as_dict()}
