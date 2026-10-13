"""Segmented teaching: short steps, each with a check — never a truncation.

What this replaces: "split long explanations into short segments" was implemented
as ``" ".join(words[:max_words]) + "…"``. That is not segmenting, it is cutting:
the last step routinely lost the sentence that carried the point, and there was no
check between steps, so a learner could read nothing, learn nothing, and be
recorded as having read an explanation.

The rule here is the one the evidence is about (Tabbers et al. 2004; Mayer &
Anderson 1991): the *whole* text, in self-contained steps, with a question after
each step that is answerable only from that step. Nothing is dropped to fit a
budget. If a caller insists on a word budget, the budget decides how many steps
there are — not which sentences survive — and :attr:`Segmented.dropped` is empty
by construction (a test asserts it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .seeds import stable_seed

#: A step is short enough to hold in working memory while reading the next one. It was 45,
#: which is three sentences of a science paragraph: "short steps" that long is the paragraph
#: again with a line break in it, and the check question then covers three ideas at once. One
#: idea per step is the point, so the budget is the length of a long sentence.
DEFAULT_STEP_WORDS = 30
#: Refuse to build a wall of 30 micro-steps; the budget is a preference, the text is a fact.
MAX_STEPS = 12

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split((text or "").strip()) if s.strip()]


def _words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text or "") if w])


def chunk_sentences(sentences_: Sequence[str], *, max_words: int = DEFAULT_STEP_WORDS) -> list[list[str]]:
    """Group whole sentences into steps. A sentence is never split."""
    steps: list[list[str]] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences_:
        words = _words(sentence)
        if current and current_words + words > max_words:
            steps.append(current)
            current, current_words = [], 0
        current.append(sentence)
        current_words += words
    if current:
        steps.append(current)
    if len(steps) > MAX_STEPS:
        # Merge the tail: more steps is not the goal, and a step per sentence is
        # how a 12-sentence paragraph becomes a 12-screen click-through.
        head = steps[: MAX_STEPS - 1]
        tail = [s for group in steps[MAX_STEPS - 1 :] for s in group]
        head.append(tail)
        steps = head
    return steps


#: Words that carry no retrieval value in a check question.
_STOP = frozenset(
    """a an the and or but if while of to in on for with without at by from as is are was were be been being
    do does did not no so than then that this these those it its they them he she you your we our i me my
    what which how why when where who about into over under more most less least very just also can could
    should would will shall may might must have has had having get got make made use used using there their
    """.split()
)


def key_terms(text: str, *, limit: int = 3) -> list[str]:
    """The content words a check question should be about (longest first)."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}|\d+(?:\.\d+)?", text or "") if w.lower() not in _STOP]
    words.sort(key=lambda w: (-len(w), w.lower()))
    seen: list[str] = []
    for w in words:
        if w.lower() not in {x.lower() for x in seen}:
            seen.append(w)
        if len(seen) >= limit:
            break
    return seen


@dataclass
class Step:
    index: int
    text: str
    words: int
    check: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "text": self.text, "words": self.words, "check": self.check}


@dataclass
class Segmented:
    steps: list[Step] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    source_words: int = 0
    note: str = ""

    @property
    def covered_words(self) -> int:
        return sum(s.words for s in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.as_dict() for s in self.steps],
            "dropped": self.dropped,
            "source_words": self.source_words,
            "covered_words": self.covered_words,
            "complete": not self.dropped,
            "note": self.note,
        }

    def as_text(self) -> str:
        return " ".join(s.text for s in self.steps)


def _check_for(step_sentences: Sequence[str], *, seed: int) -> dict[str, Any]:
    """A quick check that can only be answered from this step.

    Deterministic and cheap on purpose: the point is that the learner has to
    produce the idea in their own words before the next step, which is what makes
    segmenting work. It is *not* scored into mastery — it is a check, not a test
    item — so a wrong answer here never moves the trace (see
    ``services.attempts``: only items do that).
    """
    text = " ".join(step_sentences)
    terms = key_terms(text, limit=3)
    focus = terms[seed % len(terms)] if terms else "this step"
    others = [t for t in terms if t != focus]
    _options = [focus, *others]
    return {
        "question": f"Without scrolling back: what does this step say about {focus}?",
        "kind": "recall",
        "expect_any": [focus] + others[:2],
        "hint": f"One sentence is enough. The step is about {focus}.",
    }


def segment(
    text: str,
    *,
    max_words: int = DEFAULT_STEP_WORDS,
    seed: str = "",
    checks: bool = True,
) -> Segmented:
    """Split into steps with a check after each. Every sentence is kept."""
    all_sentences = sentences(text)
    if not all_sentences:
        return Segmented(note="nothing to segment: the material has no sentences")
    groups = chunk_sentences(all_sentences, max_words=max_words)
    steps: list[Step] = []
    for i, group in enumerate(groups):
        body = " ".join(group)
        steps.append(
            Step(
                index=i + 1,
                text=body,
                words=_words(body),
                check=_check_for(group, seed=stable_seed(seed, str(i))) if checks else {},
            )
        )
    covered = set(" ".join(s.text for s in steps).split())
    dropped = [s for s in all_sentences if not set(s.split()) <= covered] if covered else []
    return Segmented(
        steps=steps,
        dropped=dropped,
        source_words=_words(text),
        note=(
            f"{len(steps)} short steps, each with a check; the full text is kept "
            f"({_words(text)} words in, {sum(s.words for s in steps)} words shown)"
        ),
    )


def segment_many(texts: Iterable[str], *, max_words: int = DEFAULT_STEP_WORDS, seed: str = "") -> Segmented:
    """Segment concatenated material as one lesson."""
    return segment(" ".join(t for t in texts if t), max_words=max_words, seed=seed)
