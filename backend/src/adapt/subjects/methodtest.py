"""The pop-up lesson: two matched concepts, two formats, one seeded counterbalance (§6.5).

The design question this module answers is not "which format do you like?" — §5 measures preferences
and Tier 2 measures skills. It is **"which format actually taught you something, in a comparison that
is fair by construction?"** Four properties make it fair, and each one is a rule the code enforces
rather than a hope:

1. **Matched concepts.** Two concepts of equal difficulty inside the same topic, so the only
   difference between the two legs is the format. If concept B were easier, a learner who did better
   on it would look like a learner who learned better from its format.
2. **One question format across both legs.** The three questions belong to the *concept*, not to the
   method, so a leg taught with a diagram and a leg taught with prose are answered in exactly the same
   way. Otherwise the comparison would be between "answered three MCQs" and "wrote three sentences".
3. **The lesson is written for the format, not the same lesson in three costumes.** ``text`` is prose;
   ``visual`` is a spec the app's existing template renderers draw (the same shape
   :mod:`adapt.learning.visuals` produces, so the same component draws it); ``worked_example`` is a
   step list where every step says why it is there.
4. **Order and method are counterbalanced by seeded code.** Twelve cells — six ordered pairs of
   distinct methods times two leg orders — are assigned by
   ``stable_seed(learner_id, topic, round) % 12``, so no learner chooses their own format, no format
   always comes first, and the same learner gets the same assignment every time. Randomised *per
   learner* and *balanced across learners* is the same thing here, which is the point of doing it in
   code.

**Gain is measured against expectation, not against the other leg's raw score.** §6.5's rule is the
one that makes this honest: the expected number of correct answers comes from the learner's own
``p_know`` for the topic and each question's authored difficulty (the same calculation the placement
selector uses), so a strong learner scoring 3/3 on both legs shows a gain of about zero on both and is
not recorded as "visual learner". With three questions per leg, every gain is labelled an early
signal, and the interval beside it is the app's own.

Nothing here writes to the learner model. The comparison lands in the evidence log through the store,
where AI 1 does with it what the evidence supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..learning import bkt as B
from ..learning.seeds import stable_seed
from .games import _c, public_task, score_task

METHODS: tuple[str, ...] = ("text", "visual", "worked_example")

#: What a learner is told each format is. Never "visual learner": the label describes the lesson, not
# the person, because §13 forbids learning-style labels.
METHOD_LABELS: dict[str, str] = {
    "text": "Read it",
    "visual": "See it",
    "worked_example": "Follow it step by step",
}

METHOD_NOTES: dict[str, str] = {
    "text": "a short piece of writing, about ninety seconds to read",
    "visual": "a diagram the app draws from a template, about ninety seconds to read",
    "worked_example": "an example worked through one step at a time, with the reason for each step",
}

#: How long a lesson is allowed to take (§6.5: a 90-second lesson).
LESSON_SECONDS = 90
#: Three matched questions per concept.
QUESTIONS_PER_CONCEPT = 3
#: The twelve counterbalance cells: six ordered pairs of distinct methods, each with two leg orders.
#: Built by :func:`_build_cells` rather than written out by hand, because a hand-written table of
#: twelve is a table that somebody eventually edits to eleven.
def _build_cells() -> tuple[dict[str, Any], ...]:
    pairs = [(a, b) for a in METHODS for b in METHODS if a != b]
    out: list[dict[str, Any]] = []
    for i, (a, b) in enumerate(pairs):
        for first in (0, 1):
            out.append({"cell": len(out), "pair": i, "methods": (a, b), "first_concept": first})
    return tuple(out)


CELLS = _build_cells()
N_CELLS = len(CELLS)


@dataclass(frozen=True)
class Concept:
    """One of the two matched concepts in a method test.

    ``questions`` belongs to the concept and is used whichever method taught it, which is what makes
    the two legs comparable (§6.5).
    """

    key: str
    title: str
    #: The key of the concept this one is matched with. Checked by :func:`validate_pairs`.
    matched_with: str
    #: An author's difficulty prior on the same 0-1 scale the bank uses, so the expected score comes
    #: from the same calculation the placement selector uses. It is a prior, not a calibrated
    #: parameter, and every result says so.
    difficulty: float
    why_matched: str
    text: str
    visual: Mapping[str, Any]
    steps: Sequence[Mapping[str, Any]]
    questions: tuple[Mapping[str, Any], ...]

    def lesson(self, method: str) -> dict[str, Any]:
        """The 90-second lesson for one method, written for that method."""
        if method == "text":
            return {
                "method": "text",
                "label": METHOD_LABELS["text"],
                "seconds": LESSON_SECONDS,
                "body": self.text,
                "why": "prose, because this concept is a rule with two cases and reading it is the shortest way to hold both",
            }
        if method == "visual":
            return {
                "method": "visual",
                "label": METHOD_LABELS["visual"],
                "seconds": LESSON_SECONDS,
                "visual": dict(self.visual),
                "why": str(self.visual.get("why") or "a template diagram, because this concept is a shape or a sequence"),
            }
        return {
            "method": "worked_example",
            "label": METHOD_LABELS["worked_example"],
            "seconds": LESSON_SECONDS,
            "steps": [dict(s) for s in self.steps],
            "why": "a worked example, because this concept is a procedure and seeing it done once is what makes it doable",
        }

    def as_dict(self, *, with_lesson: str | None = None, with_questions: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "title": self.title,
            "difficulty": self.difficulty,
            "matched_with": self.matched_with,
            "why_matched": self.why_matched,
            "questions": len(self.questions),
        }
        if with_lesson:
            out["lesson"] = self.lesson(with_lesson)
        if with_questions:
            out["items"] = [dict(q) for q in self.questions]
        return out


@dataclass(frozen=True)
class Pair:
    """Two matched concepts and the questions that make them comparable."""

    topic_key: str
    concepts: tuple[Concept, Concept]
    #: One question format for both concepts, so the legs are answering the same kind of thing.
    question_format: str
    why_matched: str

    def concept(self, index: int) -> Concept:
        return self.concepts[max(0, min(1, int(index)))]


def _concept(
    key: str,
    title: str,
    matched_with: str,
    difficulty: float,
    why_matched: str,
    *,
    text: str,
    visual: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]],
) -> Concept:
    assert len(questions) == QUESTIONS_PER_CONCEPT, f"{key}: a method test asks exactly three questions"
    kinds = {str(q.get("kind")) for q in questions}
    assert kinds == {"choice"}, f"{key}: the three questions must share one format, got {sorted(kinds)}"
    return Concept(
        key=key,
        title=title,
        matched_with=matched_with,
        difficulty=float(difficulty),
        why_matched=why_matched,
        text=text,
        visual=dict(visual),
        steps=tuple(dict(s) for s in steps),
        questions=tuple(dict(q) for q in questions),
    )


def _step(index: int, text: str, why: str) -> dict[str, Any]:
    return {"index": index, "text": text, "why": why}


def _flow(title: str, alt: str, why: str, nodes: Sequence[str]) -> dict[str, Any]:
    """A visual spec in the shape ``learning.visuals.Visual.as_dict`` produces, so the app's existing
    template renderer draws it instead of this module shipping its own pictures."""
    return {
        "kind": "flow",
        "template": True,
        "title": title,
        "alt": alt,
        "why": why,
        "nodes": [{"index": i + 1, "label": f"Step {i + 1}", "text": t, "decision": False} for i, t in enumerate(nodes)],
        "columns": [],
        "rows": [],
        "labels": [],
        "unfilled": [],
    }


def _table(title: str, alt: str, why: str, rows: Sequence[tuple[str, str, str]]) -> dict[str, Any]:
    return {
        "kind": "comparison_table",
        "template": True,
        "title": title,
        "alt": alt,
        "why": why,
        "nodes": [],
        "columns": ["aspect", "one side", "the other side"],
        "rows": [{"aspect": a, "left": left, "right": right} for a, left, right in rows],
        "labels": [],
        "unfilled": [],
    }


# --------------------------------------------------------------------------- #
# the authored pairs
# --------------------------------------------------------------------------- #

CALCULUS = Pair(
    topic_key="calculus",
    question_format="choice",
    why_matched=(
        "Both concepts are about a gradient and both are answered with three choice questions of the "
        "same shape. One is the straight case and one the curved case, which is the difference the "
        "topic turns on; neither is easier in a way that would decide the comparison for us."
    ),
    concepts=(
        _concept(
            "calc_gradient_line",
            "The gradient of a straight line",
            "calc_gradient_curve",
            0.45,
            "the same idea (how steeply y changes with x) in its simplest setting",
            text=(
                "The gradient of a straight line says how much y changes for each step across in x. Take "
                "any two points on the line and measure two things: the change in y between them, called "
                "the rise, and the change in x, called the run. The gradient is the rise divided by the "
                "run. A line that goes up 4 as x goes across 2 has a gradient of 2. A line that goes down "
                "2 as x goes across 1 has a gradient of -2, and the minus sign is the only thing that says "
                "it is falling. Because the line is straight, every pair of points you choose gives the "
                "same answer — that is what being straight means here."
            ),
            visual=_flow(
                "Finding a gradient from two points",
                "Four steps: pick two points, measure the rise, measure the run, divide",
                "a procedure in a fixed order, which is what a flow template is for",
                (
                    "Pick any two points on the line",
                    "Measure the rise: the change in y between them",
                    "Measure the run: the change in x between them",
                    "Divide rise by run, and keep the sign",
                ),
            ),
            steps=(
                _step(1, "Take the points (1, 3) and (3, 7) on the line.", "Two points are all a straight line needs."),
                _step(2, "Rise = 7 - 3 = 4.", "The rise is the change in y, taken in the same direction as the run."),
                _step(3, "Run = 3 - 1 = 2.", "The run is the change in x; using the same two points keeps the signs consistent."),
                _step(4, "Gradient = 4 / 2 = 2.", "Rise over run. A positive answer means the line goes up as you move right."),
            ),
            questions=(
                _c("A line rises 4 as x goes from 1 to 3. What is its gradient?", ("2", "4", "1/2", "8"), 0,
                   "Rise over run: 4 / (3 - 1) = 4 / 2 = 2.", difficulty=0.4),
                _c("Which line has a negative gradient?",
                   ("one that falls as you move to the right", "one that rises as you move to the right", "a horizontal line", "a vertical line"),
                   0, "Falling as x increases is a negative rise over a positive run. A horizontal line has gradient 0 and a vertical line has no defined gradient.", difficulty=0.4),
                _c("A line passes through (0, 3) and (2, 7). What is its gradient?", ("2", "3", "4", "7"), 0,
                   "(7 - 3) / (2 - 0) = 4 / 2 = 2. The 3 is where the line crosses the y-axis, which is a different number.", difficulty=0.45),
            ),
        ),
        _concept(
            "calc_gradient_curve",
            "The gradient of a curve at one point",
            "calc_gradient_line",
            0.5,
            "the same idea where it stops being constant, which is the step calculus exists for",
            text=(
                "A curve does not have one gradient. It has a different gradient at every point on it, so "
                "the question is always 'the gradient at which point?'. To find it at one point, put a "
                "second point on the curve very close by and work out the gradient of the straight line "
                "joining the two — that line is called a chord. Then move the second point closer and "
                "closer and watch the chord gradients. They settle towards one value, and that value is "
                "the gradient of the curve at the first point. The line with exactly that gradient "
                "touching the curve at the point is the tangent."
            ),
            visual=_flow(
                "From a chord to a tangent",
                "Five steps: pick the point, pick a second point close by, find the chord gradient, move the second point closer, read the value it settles on",
                "the idea is a process of getting closer, and a flow shows the getting closer",
                (
                    "Pick the point on the curve you want the gradient at",
                    "Pick a second point on the curve close to it",
                    "Work out the gradient of the chord joining them",
                    "Move the second point closer and repeat",
                    "The value the gradients settle on is the gradient at the point",
                ),
            ),
            steps=(
                _step(1, "We want the gradient of y = x squared at x = 2, where y = 4.", "Naming the point first is what makes 'the gradient' mean something."),
                _step(2, "Take a second point at x = 2.1, where y = 4.41.", "Any second point will do; a close one gives a chord near the tangent."),
                _step(3, "Chord gradient = (4.41 - 4) / (2.1 - 2) = 0.41 / 0.1 = 4.1.", "Rise over run, exactly as for a straight line."),
                _step(4, "Take x = 2.01 instead: (4.0401 - 4) / 0.01 = 4.01.", "The closer the second point, the closer the chord gradient gets to 4."),
                _step(5, "So the gradient at x = 2 is 4.", "The value the chords approach is the gradient of the curve at the point."),
            ),
            questions=(
                _c("For y = x squared, the chord from x = 2 to x = 2.1 has gradient 4.1 and the chord from x = 2 to x = 2.01 has gradient 4.01. What happens as the second point moves closer to x = 2?",
                   ("The chord gradients get closer and closer to 4", "The chord gradients get closer and closer to 0", "The chord gradients stay at 4.1", "The chord gradients grow without limit"),
                   0, "The sequence 4.1, 4.01, ... approaches 4, which is the gradient at the point.", difficulty=0.45),
                _c("What is the gradient of y = x squared at x = 3?", ("6", "9", "3", "12"), 0,
                   "The gradient of x squared at a point is twice the x value, so at x = 3 it is 6. Nine is the height of the curve there, not its gradient.", difficulty=0.5),
                _c("At the very top of a hill on a smooth curve, what is the gradient?", ("0", "at its largest", "negative", "undefined"), 0,
                   "At the top the curve is momentarily level: it has stopped rising and has not started falling.", difficulty=0.5),
            ),
        ),
    ),
)

ALGEBRA = Pair(
    topic_key="algebra",
    question_format="choice",
    why_matched=(
        "Both are manipulation of an expression in one or two brackets, both are answered with three "
        "choice questions, and both are decided by the same underlying rule (multiply every term). "
        "Neither needs the other to be understood first."
    ),
    concepts=(
        _concept(
            "alg_collecting",
            "Collecting like terms",
            "alg_two_brackets",
            0.4,
            "one bracket, one rule: terms can only be combined when they count the same thing",
            text=(
                "An expression is a sum of terms, and a term is a number, a letter, or a number "
                "multiplying a letter. Two terms are like terms when their letters and their powers match "
                "exactly, so 3a and -5a are like terms and 3a and 3b are not, however similar they look. "
                "Only like terms can be added, because adding them is counting more of the same thing. The "
                "number in front of a letter is its coefficient and it carries its sign with it: in 3a - 5b "
                "the second term is -5b, not 5b. To simplify, gather the like terms and add their "
                "coefficients, and leave anything with no partner exactly as it is."
            ),
            visual=_table(
                "Like terms and terms that only look alike",
                "A table of four pairs, saying whether they can be combined and why",
                "the concept is a distinction between two kinds of pair, which a table holds better than prose",
                (
                    ("3a and -5a", "can be combined: -2a", "same letter, same power"),
                    ("3a and 3b", "cannot", "same coefficient, different letters"),
                    ("x and x squared", "cannot", "same letter, different powers"),
                    ("7 and 2y", "cannot", "one has no letter at all"),
                ),
            ),
            steps=(
                _step(1, "Simplify 4x + 3y - x + 5y.", "Write the expression down exactly as given; nothing has been done to it yet."),
                _step(2, "Find the like terms: 4x and -x are alike, 3y and 5y are alike.", "Like means the same letter to the same power."),
                _step(3, "Combine them: 4x - x = 3x, and 3y + 5y = 8y.", "Add the coefficients only; the letter never changes."),
                _step(4, "So the answer is 3x + 8y.", "Nothing else has a partner, so nothing else changes."),
            ),
            questions=(
                _c("Simplify 3a + 7b - a + 2b.", ("2a + 9b", "4a + 9b", "2a + 5b", "11ab"), 0,
                   "3a - a = 2a and 7b + 2b = 9b; a terms and b terms cannot merge with each other.", difficulty=0.35),
                _c("Which pair are like terms?", ("5m and -2m", "5m and 5n", "5m and m squared", "5m and 5"), 0,
                   "Like terms have the same letter to the same power.", difficulty=0.35),
                _c("Simplify 4x squared + 3x - x squared + 5 - 2x.", ("3x squared + x + 5", "3x squared + 5x + 5", "4x squared + x + 5", "3x cubed + 5"), 0,
                   "4x squared - x squared = 3x squared, 3x - 2x = x, and 5 stands alone. x squared and x are not like terms.", difficulty=0.45),
            ),
        ),
        _concept(
            "alg_two_brackets",
            "Expanding two brackets",
            "alg_collecting",
            0.5,
            "the same rule applied twice over: every term in the first bracket multiplies every term in the second",
            text=(
                "To expand two brackets multiplied together, every term in the first bracket is multiplied "
                "by every term in the second. With (x + 4)(x - 3) that is four multiplications: x times x, "
                "x times -3, 4 times x and 4 times -3. Write them all down before simplifying anything, "
                "because the signs are easiest to lose in the middle of the process. Then collect the like "
                "terms that appear: -3x and +4x become +x. The constant terms multiply too, and a negative "
                "times a positive stays negative, so the last term here is -12."
            ),
            visual=_flow(
                "Expanding (x + 4)(x - 3)",
                "Five steps: multiply each term in turn, write all four products, then collect like terms",
                "a fixed sequence where the mistake is skipping a multiplication, so the flow shows every one",
                (
                    "First term x first term: x times x = x squared",
                    "First term x second term: x times -3 = -3x",
                    "Second term x first term: 4 times x = 4x",
                    "Second term x second term: 4 times -3 = -12",
                    "Collect the like terms: x squared + x - 12",
                ),
            ),
            steps=(
                _step(1, "We want (x + 4)(x - 3).", "Two brackets multiplied means every term meets every term."),
                _step(2, "x times x = x squared, and x times -3 = -3x.", "The first bracket's first term against both terms of the second."),
                _step(3, "4 times x = 4x, and 4 times -3 = -12.", "The first bracket's second term against both terms of the second."),
                _step(4, "-3x and 4x are like terms, so they give +x.", "Only now is anything simplified; doing it earlier is where signs get lost."),
                _step(5, "So (x + 4)(x - 3) = x squared + x - 12.", "Four products became three terms."),
            ),
            questions=(
                _c("Expand (x + 4)(x - 3).", ("x squared + x - 12", "x squared - 12", "x squared + 7x - 12", "x squared - x + 12"), 0,
                   "Four products: x squared, -3x, 4x and -12. The two middle terms collect to +x.", difficulty=0.45),
                _c("Expand (x + 5)(x + 2).", ("x squared + 7x + 10", "x squared + 10x + 7", "x squared + 7", "x squared + 5x + 2"), 0,
                   "x squared + 2x + 5x + 10, and 2x + 5x = 7x.", difficulty=0.4),
                _c("Expand (x - 6)(x + 1).", ("x squared - 5x - 6", "x squared + 5x - 6", "x squared - 6x + 1", "x squared - 5x + 6"), 0,
                   "x squared + x - 6x - 6, and x - 6x = -5x. The constant is -6 because -6 times +1 is negative.", difficulty=0.5),
            ),
        ),
    ),
)

STATISTICS = Pair(
    topic_key="statistics",
    question_format="choice",
    why_matched=(
        "Two averages, both worked from the same kind of list of numbers, both answered with three "
        "choice questions. The mean and the median are the pair learners confuse most, and neither is "
        "prior knowledge for the other."
    ),
    concepts=(
        _concept(
            "stat_median",
            "The median of a list",
            "stat_mean",
            0.4,
            "one of the two averages a learner meets first, and the one an extreme value cannot move",
            text=(
                "The median is the middle value of a list once the list has been put in order. Ordering "
                "first is not a formality: with the numbers scattered, the one in the middle of the page is "
                "not the median. With an odd number of values the median is the one exactly in the centre. "
                "With an even number there is no single central value, so the median is halfway between the "
                "two central ones. Because it only cares about position, one enormous or tiny value moves "
                "the median very little, which is why it is used for things like incomes."
            ),
            visual=_flow(
                "Finding the median",
                "Four steps: order the list, count the values, take the middle one or the halfway point between the two middle ones",
                "an order of operations with a branch in the middle, which a flow shows and a paragraph hides",
                (
                    "Put the values in order, smallest to largest",
                    "Count how many values there are",
                    "If the count is odd, take the value exactly in the middle",
                    "If the count is even, take the halfway point between the two middle values",
                ),
            ),
            steps=(
                _step(1, "Find the median of 12, 5, 9, 7, 5.", "Nothing can be said about the middle until the list is ordered."),
                _step(2, "In order: 5, 5, 7, 9, 12.", "Ordering is the step most often skipped, and skipping it changes the answer."),
                _step(3, "There are five values, which is odd, so take the third.", "With an odd count there is exactly one central value."),
                _step(4, "The median is 7.", "Note that 7 appears once; the median is a position, not a frequency."),
            ),
            questions=(
                _c("What is the median of 12, 5, 9, 7, 5?", ("7", "5", "9", "7.6"), 0,
                   "Ordered: 5, 5, 7, 9, 12. The middle of five values is the third, which is 7. 7.6 is the mean.", difficulty=0.35),
                _c("What is the median of 4, 8, 6, 10?", ("7", "8", "6", "7.5"), 0,
                   "Ordered: 4, 6, 8, 10. With four values the median is halfway between 6 and 8, which is 7. 7.5 would be the mean.", difficulty=0.45),
                _c("Five households earn 20, 22, 25, 28 and 30 thousand. A sixth earning 500 thousand moves in. What happens to the median?",
                   ("It moves a little, from 25 to about 26.5", "It jumps to about 104", "It stays exactly 25", "It becomes 500"),
                   0, "Ordered, the middle of six values is halfway between 25 and 28. An extreme value shifts the median only slightly.", difficulty=0.5),
            ),
        ),
        _concept(
            "stat_mean",
            "The mean of a list",
            "stat_median",
            0.42,
            "the other of the two averages, computed from every value and therefore moved by all of them",
            text=(
                "The mean shares the total out equally. Add every value in the list together, then divide "
                "by how many values there are — not by how many different values appear, and not by the "
                "largest one. Because every value goes into the total, an unusually large or small value "
                "pulls the mean towards it, which is the mean's strength when the list is even and its "
                "weakness when it is not. The mean does not have to be one of the values in the list, and "
                "usually is not."
            ),
            visual=_table(
                "What the mean uses and what it ignores",
                "A table of four questions about a list, with the mean's answer beside the median's",
                "the distinction between the two averages is a set of contrasts, which is what a table is for",
                (
                    ("Uses every value?", "yes", "no: only the middle position"),
                    ("Moved by an extreme value?", "a lot", "barely"),
                    ("Needs the list ordered?", "no", "yes"),
                    ("Must be one of the values?", "no", "yes when the count is odd"),
                ),
            ),
            steps=(
                _step(1, "Find the mean of 12, 5, 9, 7, 5.", "The mean is a total shared out, so the first move is the total."),
                _step(2, "Total = 12 + 5 + 9 + 7 + 5 = 38.", "Every value counts, including the repeated 5."),
                _step(3, "There are five values, so divide by 5.", "Divide by how many values there are, not how many distinct ones."),
                _step(4, "The mean is 38 / 5 = 7.6.", "7.6 is not one of the values in the list, and that is normal."),
            ),
            questions=(
                _c("What is the mean of 12, 5, 9, 7, 5?", ("7.6", "7", "38", "5"), 0,
                   "38 / 5 = 7.6. Seven is the median and 38 is the total.", difficulty=0.35),
                _c("What is the mean of 4, 8, 6 and 10?", ("7", "7.5", "8", "28"), 0,
                   "28 / 4 = 7. 7.5 is halfway between the two middle values, which is the median.", difficulty=0.4),
                _c("Five households earn 20, 22, 25, 28 and 30 thousand. A sixth earning 500 thousand moves in. What happens to the mean?",
                   ("It jumps a long way, to about 104", "It moves a little, to about 26.5", "It stays at 25", "It becomes 500"),
                   0, "The total becomes 625 shared between six, which is about 104 — a number no household in the list earns.", difficulty=0.5),
            ),
        ),
    ),
)

GOVERNMENT = Pair(
    topic_key="government",
    question_format="choice",
    why_matched=(
        "Two of the three arms of government, each with one job and one limit, each answered with three "
        "choice questions. Neither arm is simpler than the other, and confusing them is the standard "
        "error the topic exists to correct."
    ),
    concepts=(
        _concept(
            "gov_legislature",
            "What the legislature does",
            "gov_judiciary",
            0.45,
            "one arm and its limit: it makes the law but does not apply it to a case",
            text=(
                "The legislature is the arm that makes the law. Its members debate proposals, amend them, "
                "and vote; a proposal that passes both houses and receives assent becomes law. Its job "
                "stops there. It does not decide what a law means in a particular dispute, and it does not "
                "carry the law out day to day — those belong to the courts and to the executive. A "
                "legislature can change a law for the future, and in many systems it can also remove a "
                "government from office, which is one of the ways the arms check each other."
            ),
            visual=_table(
                "The legislature's job and its limits",
                "Four rows: what the legislature does, what it does not do, who does that instead, and how it can check the others",
                "an institution is best understood by its boundaries, and a boundary is a two-column fact",
                (
                    ("Makes and amends the law", "debates, amends, votes", "nobody else can"),
                    ("Deciding what a law means in a case", "not its job", "the judiciary"),
                    ("Carrying the law out day to day", "not its job", "the executive"),
                    ("Checking the executive", "questions, committees, votes of no confidence", "shared with the courts and the press"),
                ),
            ),
            steps=(
                _step(1, "A member introduces a bill to make it illegal to sell a product to under-18s.", "A bill starts as a proposal from a member or the government."),
                _step(2, "A committee amends it to include online sales as well as shops.", "Amendment is where the text actually changes."),
                _step(3, "Both houses vote and the bill passes.", "A vote is acceptance of a text, not a judgement about a case."),
                _step(4, "A shopkeeper is later prosecuted under it. That decision is not the legislature's.", "Applying the law to a person belongs to the courts."),
            ),
            questions=(
                _c("Which is the legislature's own job?", ("Debating, amending and voting on a bill", "Deciding whether a minister broke the law", "Running the health service day to day", "Arresting somebody who breaks a law"), 0,
                   "Making the law is the legislature's; the other three belong to the courts and the executive.", difficulty=0.4),
                _c("A bill passes the lower house and the upper house amends it. What must happen next?",
                   ("The two houses must agree on one text", "The upper house's text becomes law at once", "The lower house's text becomes law at once", "The bill is lost automatically"),
                   0, "An amendment has to be accepted by the other house; neither text wins outright.", difficulty=0.5),
                _c("Which of these is a way the legislature can check the executive?",
                   ("Questioning a minister in public and publishing the answers", "Overturning a court's decision in one case", "Appointing judges to hear a case it is involved in", "Issuing a regulation without a vote"),
                   0, "Questions, committees and votes are the legislature's tools; overturning a judgement in a case is not available to it.", difficulty=0.5),
            ),
        ),
        _concept(
            "gov_judiciary",
            "What the judiciary does",
            "gov_legislature",
            0.48,
            "the other arm and its limit: it decides what the law means in a case but does not make the law",
            text=(
                "The judiciary decides what the law means when it is applied to a real case. A court hears "
                "evidence, finds the facts, and applies the law to them; if two parties disagree about what "
                "a law or a contract requires, the court's reading settles it for that case. Courts can "
                "also decide that an action by the executive went beyond the powers the law gave it. What "
                "a court does not do is make statute law or run a ministry: it interprets and it checks, "
                "and it does both by deciding cases rather than by setting policy."
            ),
            visual=_table(
                "The judiciary's job and its limits",
                "Four rows: what a court decides, what it cannot do, who does that instead, and how it checks the others",
                "the same boundary shape as the legislature's table, so the two lessons can be compared directly",
                (
                    ("Decides what the law means in a case", "hears evidence, finds facts, applies the law", "nobody else can"),
                    ("Makes new statute law", "not its job", "the legislature"),
                    ("Runs a ministry day to day", "not its job", "the executive"),
                    ("Checking the executive", "deciding that an action went beyond its powers", "shared with the legislature and the press"),
                ),
            ),
            steps=(
                _step(1, "A minister issues a regulation requiring a permit for street trading.", "The executive acts under powers a law gave it."),
                _step(2, "A trader says the law only allowed permits inside markets, and asks a court to decide.", "A real dispute about what the law means is what brings a court in."),
                _step(3, "The court reads the law and finds the regulation goes beyond it.", "Interpreting the law for this case is the judiciary's own job."),
                _step(4, "The regulation is set aside, but the court does not write a new one.", "A court can stop an action; writing the replacement belongs to the legislature and the executive."),
            ),
            questions=(
                _c("Which is the judiciary's own job?", ("Deciding what a law means when it is applied to a case", "Debating and voting on a bill", "Collecting taxes", "Appointing members of parliament"), 0,
                   "Interpreting and applying the law in a case is the courts'; the others belong to the legislature and the executive.", difficulty=0.4),
                _c("A court finds that a minister acted beyond the powers given by law. What can the court do?",
                   ("Set the action aside", "Write a replacement regulation itself", "Remove the minister from office", "Change the law for the future"),
                   0, "A court can declare an action unlawful and set it aside; making rules and removing ministers belong elsewhere.", difficulty=0.5),
                _c("Why is it a problem if the same body both makes a rule and decides whether the rule was broken?",
                   ("Because there would be no independent check on how the rule is applied", "Because it would take longer", "Because courts are not trained to make rules", "Because it is not a problem"),
                   0, "Separation exists so that the body with an interest in the outcome is not the body judging it.", difficulty=0.5),
            ),
        ),
    ),
)

BIOLOGY = Pair(
    topic_key="biology",
    question_format="choice",
    why_matched=(
        "Two ways substances cross a membrane, both passive, both answered with three choice "
        "questions. They are the pair most often confused, and neither depends on knowing the other."
    ),
    concepts=(
        _concept(
            "bio_diffusion",
            "Diffusion",
            "bio_osmosis",
            0.42,
            "the general case: any substance moving down its own concentration gradient",
            text=(
                "Diffusion is the net movement of particles from a place where they are more concentrated "
                "to a place where they are less concentrated. It happens because particles are moving all "
                "the time and bumping into each other, so a crowded region spreads out on its own. Nothing "
                "has to push it and the cell spends no energy on it, which is why it is called a passive "
                "process. Diffusion is fastest over short distances, in warm conditions, and when the "
                "difference in concentration is large. It works for gases and for substances dissolved in "
                "a liquid."
            ),
            visual=_flow(
                "How diffusion happens",
                "Four steps: particles move and collide, a crowded region spreads out, movement continues until even, no energy is spent",
                "a process with a direction and an end state, which a flow shows as a sequence",
                (
                    "Particles are always moving and colliding with each other",
                    "Where they are crowded, more move out than move in",
                    "The net movement is from more concentrated to less concentrated",
                    "It continues until the particles are evenly spread, with no energy spent",
                ),
            ),
            steps=(
                _step(1, "A drop of dye is put into still water.", "Start with a large concentration difference in one place."),
                _step(2, "The dye particles move and collide, spreading outwards.", "Movement is random; the net result is not."),
                _step(3, "More particles leave the crowded region than enter it.", "That imbalance is what 'net movement' means."),
                _step(4, "Eventually the water is evenly coloured, and no energy was used.", "Even spread is the end state, and the process was passive throughout."),
            ),
            questions=(
                _c("In which direction does diffusion move a substance?",
                   ("From where it is more concentrated to where it is less concentrated", "From where it is less concentrated to where it is more concentrated", "In whichever direction the cell needs it", "Only downwards"),
                   0, "Down the concentration gradient. Going the other way needs energy and is active transport.", difficulty=0.4),
                _c("Which change would make diffusion faster?", ("A bigger difference in concentration", "A smaller difference in concentration", "A longer distance to travel", "A lower temperature"), 0,
                   "A steeper gradient, a shorter distance and a higher temperature all speed it up.", difficulty=0.45),
                _c("Why is diffusion called a passive process?", ("The cell spends no energy on it", "It only happens when the cell is resting", "It needs no particles", "It happens only in dead cells"), 0,
                   "Passive means no energy from the cell; the movement comes from the particles' own motion.", difficulty=0.4),
            ),
        ),
        _concept(
            "bio_osmosis",
            "Osmosis",
            "bio_diffusion",
            0.45,
            "the specific case: water only, across a partially permeable membrane",
            text=(
                "Osmosis is the movement of water across a partially permeable membrane from a dilute "
                "solution to a more concentrated one. The membrane lets water through but not the dissolved "
                "substance, so the water moves instead of the solute, and it moves towards wherever the "
                "solution is stronger. Like diffusion it is passive and needs no energy from the cell. "
                "Saying 'from more water to less water' and 'from dilute to concentrated' describe the same "
                "movement; saying 'from concentrated to dilute solution' describes the opposite, and is the "
                "most common mistake made about it."
            ),
            visual=_table(
                "Diffusion and osmosis side by side",
                "Four rows comparing what moves, whether a membrane is needed, the direction, and the energy cost",
                "osmosis is a special case of diffusion, and a comparison table is what shows which parts are shared",
                (
                    ("What moves", "any particles", "water only"),
                    ("Membrane needed?", "no", "yes, partially permeable"),
                    ("Direction", "more concentrated to less concentrated", "dilute solution to concentrated solution"),
                    ("Energy from the cell", "none", "none"),
                ),
            ),
            steps=(
                _step(1, "A cell with a fairly concentrated interior is put in pure water.", "Set up a difference in concentration across the membrane."),
                _step(2, "The membrane lets water through but not the dissolved substances inside.", "That selectivity is what makes the movement one-sided."),
                _step(3, "Water moves into the cell, from the dilute outside to the concentrated inside.", "Water goes towards the stronger solution."),
                _step(4, "The cell swells; a plant cell is held by its wall, an animal cell may burst.", "The consequence depends on what is around the membrane."),
            ),
            questions=(
                _c("Which way does water move in osmosis?",
                   ("From a dilute solution to a more concentrated one, across a partially permeable membrane", "From a concentrated solution to a dilute one", "In whichever direction the cell chooses", "Equally in both directions at all times"),
                   0, "Water moves towards the stronger solution. 'Concentrated to dilute' is the common reversal.", difficulty=0.45),
                _c("What does 'partially permeable' mean?", ("Water can pass but the dissolved substance cannot", "Nothing can pass", "Everything can pass", "Only gases can pass"), 0,
                   "The membrane selects, which is what makes the movement one-sided.", difficulty=0.4),
                _c("A plant cell is placed in a very concentrated sugar solution. What do you expect?",
                   ("Water leaves the cell and it shrinks away from its wall", "Water enters and the cell bursts", "Nothing changes", "The cell makes more sugar"),
                   0, "Water moves towards the stronger solution, which here is outside the cell.", difficulty=0.5),
            ),
        ),
    ),
)

GRAMMAR = Pair(
    topic_key="grammar",
    question_format="choice",
    why_matched=(
        "Two perfect aspects of English, both answered with three choice questions and both decided by "
        "the same idea (a state or action viewed from a later moment). Confusing them is the standard "
        "error, and neither is prior knowledge for the other."
    ),
    concepts=(
        _concept(
            "gr_present_perfect",
            "The present perfect",
            "gr_past_perfect",
            0.45,
            "one of the two perfect forms, and the one that connects the past to now",
            text=(
                "The present perfect is 'has' or 'have' with a past participle: she has written, they have "
                "arrived. It is used when something that happened in the past still matters now — an "
                "experience in a life that is still going, a recent event with a present consequence, or a "
                "state that began in the past and continues. It does not go with a finished time: 'yesterday' "
                "and 'in 2019' need the past simple, because those times are closed. 'Since' and 'for' with "
                "something continuing take the present perfect, which is why 'she has lived here since 2019' "
                "is right and 'she lives here since 2019' is not."
            ),
            visual=_table(
                "When the present perfect is right and when it is not",
                "Four rows pairing a time expression with the form it takes and the reason",
                "the rule is a set of pairings between a time phrase and a verb form, which is a table's shape",
                (
                    ("'since 2019' with a continuing state", "has lived", "the state reaches the present"),
                    ("'yesterday'", "lived (past simple)", "the time is finished and closed"),
                    ("'ever' in a life so far", "have you ever seen", "the period is still open"),
                    ("'just' with a present consequence", "has just left", "the result matters now"),
                ),
            ),
            steps=(
                _step(1, "We want to say that she moved here in 2019 and still lives here.", "First decide whether the situation reaches the present."),
                _step(2, "It does, so the present perfect is available.", "A continuing state is exactly what this form is for."),
                _step(3, "'Since' marks the starting point of that continuing state.", "'Since' with a point in time, 'for' with a length of time."),
                _step(4, "So: 'She has lived here since 2019.'", "The past simple would put the living in a finished period, which is not what we mean."),
            ),
            questions=(
                _c("Choose the correct sentence.", ("She has lived here since 2019.", "She lives here since 2019.", "She is living here since 2019.", "She did live here since 2019."), 0,
                   "'Since' with a state continuing to the present takes the present perfect.", difficulty=0.4),
                _c("Choose the correct sentence.", ("I have never been to Mombasa.", "I never was to Mombasa in my life so far.", "I am never being to Mombasa.", "I did never go to Mombasa until now."), 0,
                   "An experience in a life that is still open takes the present perfect with 'never'.", difficulty=0.4),
                _c("Which sentence needs the past simple rather than the present perfect?",
                   ("She arrived yesterday.", "She has arrived.", "She has just arrived.", "She has arrived three times this month."),
                   0, "'Yesterday' is a finished time, so the present perfect cannot be used with it.", difficulty=0.5),
            ),
        ),
        _concept(
            "gr_past_perfect",
            "The past perfect",
            "gr_present_perfect",
            0.48,
            "the other perfect form, and the one that orders two past events",
            text=(
                "The past perfect is 'had' with a past participle: the film had started, they had eaten. It "
                "places one past event before another past event, so a sentence with two past happenings "
                "uses it for the earlier of the two. Without it, the order can be unclear: 'when we "
                "arrived, the film started' could mean the film began at that moment. 'Had started' says it "
                "was already under way. The past perfect is not a more formal version of the past simple, "
                "and using it for everything in a story makes the sequence harder to follow, not easier."
            ),
            visual=_flow(
                "Choosing between two past forms",
                "Four steps: find the two events, decide which is earlier, put the earlier one in the past perfect, keep the later one in the past simple",
                "the decision is a sequence with one branch, which is what a flow shows",
                (
                    "Find the two events in the sentence",
                    "Decide which one happened first",
                    "Put the earlier one in the past perfect: had + past participle",
                    "Keep the later one in the past simple",
                ),
            ),
            steps=(
                _step(1, "Two things happened: we arrived, and the film started.", "The past perfect only earns its place when there are two past events to order."),
                _step(2, "The film started first.", "Deciding the order is the whole job."),
                _step(3, "So the earlier one takes 'had': the film had started.", "'Had' plus the past participle places it before the other event."),
                _step(4, "'When we arrived, the film had already started.'", "The later event stays in the past simple, and 'already' makes the sequence unmistakable."),
            ),
            questions=(
                _c("Choose the sentence that says the film began before we got there.",
                   ("When we arrived, the film had already started.", "When we arrived, the film already started.", "When we arrive, the film had started.", "When we arrived, the film was already start."),
                   0, "Two past events, the earlier one in the past perfect.", difficulty=0.45),
                _c("Choose the correct sentence.", ("She had finished the report before the deadline.", "She has finished the report before the deadline yesterday.", "She had finish the report before the deadline.", "She finishing the report before the deadline."),
                   0, "'Had' plus the past participle, ordering one past event before another.", difficulty=0.45),
                _c("Which sentence does NOT need the past perfect?",
                   ("He walked to school and sat down.", "When he got there, the lesson had begun.", "She had never seen snow before that trip.", "By the time the bus came, they had waited an hour."),
                   0, "One sequence of events in order needs no past perfect; the form exists to reorder two past events.", difficulty=0.5),
            ),
        ),
    ),
)

PAIRS: dict[str, Pair] = {p.topic_key: p for p in (CALCULUS, ALGEBRA, STATISTICS, GOVERNMENT, BIOLOGY, GRAMMAR)}


def pair_for(topic_key: str) -> Pair | None:
    """The authored pair for a topic, or ``None`` — which the service reports honestly."""
    return PAIRS.get(topic_key)


def served_items(assignment: "Assignment", concept: Concept) -> list[dict[str, Any]]:
    """The three questions for one concept, as this learner is served them — keys included.

    The options are shuffled per (learner, topic, question), so two learners do not share an answer
    position and a habit of picking the first option cannot score. The shuffle is a pure function of
    the seed, which is what lets the scorer re-derive exactly what was shown without trusting anything
    the browser sent back.
    """
    import random

    out: list[dict[str, Any]] = []
    for n, question in enumerate(concept.questions):
        task: dict[str, Any] = {
            **{k: v for k, v in dict(question).items() if k != "difficulty"},
            "id": f"{concept.key}_q{n + 1}",
            "kind": "choice",
            "skill": "method_test",
            "stage": "method_test",
            "phase": "measure",
            "seconds": 40,
            "instructions": "Three questions, the same shape whichever way the lesson was taught.",
            "wagerable": True,
            "item_of": n + 1,
            "items_total": QUESTIONS_PER_CONCEPT,
        }
        options = list(task.get("options") or [])
        rng = random.Random(stable_seed("method-test", assignment.learner_id, assignment.topic_key, task["id"]))
        positions = list(range(len(options)))
        rng.shuffle(positions)
        task["options"] = [options[p] for p in positions]
        task["answer"] = positions.index(int(question["answer"]))
        out.append(task)
    return out


# --------------------------------------------------------------------------- #
# assignment: randomised and counterbalanced by seeded code
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Assignment:
    """Which concept is taught which way, and in which order, for one learner and one topic."""

    learner_id: str
    topic_key: str
    cell: int
    cells: int
    methods: tuple[str, str]
    first_concept: int
    seed: int
    design: str = "counterbalanced by seeded code"
    why: str = ""

    def legs(self, pair: Pair) -> tuple[dict[str, Any], dict[str, Any]]:
        """The two legs in the order this learner gets them, with the answer keys left out."""
        order = [self.first_concept, 1 - self.first_concept]
        out = []
        for i, concept_index in enumerate(order):
            concept = pair.concept(concept_index)
            method = self.methods[i]
            out.append(
                {
                    "leg": "a" if i == 0 else "b",
                    "concept_index": concept_index,
                    "concept": concept.key,
                    "concept_title": concept.title,
                    "method": method,
                    "method_label": METHOD_LABELS[method],
                    "method_note": METHOD_NOTES[method],
                    "lesson": concept.lesson(method),
                    "items": [public_task(t) for t in served_items(self, concept)],
                    "seconds": LESSON_SECONDS,
                }
            )
        return out[0], out[1]

    def as_dict(self, *, pair: Pair | None = None) -> dict[str, Any]:
        out = {
            "learner_id": self.learner_id,
            "topic_key": self.topic_key,
            "cell": self.cell,
            "of_cells": self.cells,
            "methods": list(self.methods),
            "method_labels": [METHOD_LABELS[m] for m in self.methods],
            "first_concept": self.first_concept,
            "seed": self.seed,
            "design": self.design,
            "why": self.why,
            "question_format": pair.question_format if pair else "choice",
            "matched": pair.why_matched if pair else "",
        }
        return out


def assign(learner_id: str, topic_key: str, *, round_no: int = 1) -> Assignment:
    """The counterbalanced assignment for one learner, from a seed and nothing else.

    Twelve cells, each an ordered pair of distinct methods plus a leg order. The cell is chosen by a
    stable hash of ``(learner, topic, round)``, so:

    * a learner never chooses their own format (which would measure preference and call it learning);
    * the same learner gets the same assignment on every device and after every reload;
    * across learners the twelve cells are hit in rotation, so no format is always first and no pair of
      formats is over-represented. That is what makes a later comparison of the cells a comparison
      rather than a story about whoever volunteered.
    """
    seed = stable_seed("method-test", learner_id, topic_key, round_no)
    cell = CELLS[seed % N_CELLS]
    methods = (str(cell["methods"][0]), str(cell["methods"][1]))
    first = int(cell["first_concept"])
    return Assignment(
        learner_id=learner_id,
        topic_key=topic_key,
        cell=int(cell["cell"]),
        cells=N_CELLS,
        methods=methods,
        first_concept=first,
        seed=seed & ((1 << 62) - 1),
        why=(
            f"cell {int(cell['cell']) + 1} of {N_CELLS}: the method pair and the order were dealt by a "
            "seeded step in code, not chosen by you, so the twelve combinations are spread "
            "evenly across learners"
        ),
    )


def balance_report(learner_ids: Sequence[str], topic_key: str) -> dict[str, Any]:
    """How a set of learners is spread across the twelve cells. Used by the tests and the console."""
    counts: dict[int, int] = {i: 0 for i in range(N_CELLS)}
    first_method_counts: dict[str, int] = {m: 0 for m in METHODS}
    for lid in learner_ids:
        a = assign(lid, topic_key)
        counts[a.cell] += 1
        first_method_counts[a.methods[a.first_concept]] += 1
    return {
        "n": len(learner_ids),
        "cells": N_CELLS,
        "per_cell": counts,
        "first_leg_by_method": first_method_counts,
        "empty_cells": [c for c, n in counts.items() if n == 0],
    }


# --------------------------------------------------------------------------- #
# gain against expectation
# --------------------------------------------------------------------------- #


def expected_accuracy(p_know: float, difficulty: float, *, choices: int = 4) -> float:
    """The chance this learner gets one of these questions right, before the lesson is taught.

    The same calculation the placement selector uses (BKT emission with a guess rate for the item's
    format, nudged by the author's difficulty prior), so "expected" means the same thing here as it
    does everywhere else in the app.
    """
    params = B.params_for_kind("mcq", choices=choices)
    from ..assessment.selector import expected_success_rate

    return expected_success_rate(max(0.001, min(0.999, float(p_know))), float(difficulty), params)


@dataclass(frozen=True)
class LegResult:
    """One leg's measurement: what happened, what was expected, and how much evidence that is."""

    leg: str
    concept: str
    concept_title: str
    method: str
    method_label: str
    correct: int
    n: int
    accuracy: float
    ci: tuple[float, float]
    expected: float
    residual: float
    evidence: str
    seconds: int | None = None
    latencies_ms: tuple[int, ...] = ()
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "leg": self.leg,
            "concept": self.concept,
            "concept_title": self.concept_title,
            "method": self.method,
            "method_label": self.method_label,
            "correct": self.correct,
            "n": self.n,
            "observations": self.n,
            "accuracy": round(self.accuracy, 3),
            "range": [round(self.ci[0], 3), round(self.ci[1], 3)],
            "expected": round(self.expected, 3),
            "residual": round(self.residual, 3),
            "evidence": self.evidence,
            "seconds": self.seconds,
            "latencies_ms": list(self.latencies_ms),
            "note": self.note,
            "score": None,
        }


def score_leg(
    *,
    leg: str,
    concept: Concept,
    method: str,
    answers: Sequence[Mapping[str, Any]],
    p_know: float,
    lesson_seconds: int | None = None,
    scored: Sequence[Mapping[str, Any]] | None = None,
    assignment: "Assignment | None" = None,
) -> LegResult:
    """Score one leg: three answers against three authored questions, and both against expectation.

    ``scored`` is used when the questions were already judged as they were answered (the round path,
    where each answer was scored against the task the learner was actually shown). Without it, the
    tasks are re-derived from ``assignment`` — never from the authored order, because the served order
    is shuffled and scoring against the wrong order would mark right answers wrong.
    """
    latencies: list[int] = []
    for answer in answers:
        if isinstance(answer, Mapping) and answer.get("latency_ms") is not None:
            try:
                latencies.append(int(answer["latency_ms"]))
            except (TypeError, ValueError):
                pass
    if scored is None:
        if assignment is None:
            raise ValueError("score_leg needs either the scores already made or the assignment they were made from")
        tasks = served_items(assignment, concept)
        scored = [
            score_task(task, dict(answers[n]) if n < len(answers) and isinstance(answers[n], Mapping) else {})
            for n, task in enumerate(tasks)
        ]
    scored = [dict(r) for r in scored]
    correct = sum(1 for r in scored if r.get("correct") is True)
    answered = sum(1 for r in scored if r.get("correct") is not None)
    accuracy = correct / max(1, answered) if answered else 0.0
    lo, hi = B.credible_interval(accuracy, answered, correct)
    if answered:
        # An interval that excludes the estimate it describes (0 of 3 giving [0.001, …])
        # cannot be shown next to that estimate; the clamp only ever widens it to include it.
        lo, hi = min(lo, accuracy), max(hi, accuracy)
    # With no answers there is nothing to have expected: reporting the model's expectation
    # against an accuracy of 0.0 would read as a large negative gain from a lesson the
    # learner never answered, which is a measurement of an absence.
    expected = (
        sum(
            expected_accuracy(p_know, float(q.get("difficulty") or concept.difficulty))
            for q in concept.questions
        )
        / max(1, len(concept.questions))
        if answered
        else 0.0
    )
    residual = accuracy - expected
    note = ""
    if not answered:
        note = "none of the three questions were answered, so this leg is not measured rather than failed"
    elif answered < len(concept.questions):
        note = (
            f"{len(concept.questions) - answered} of the three questions were left blank and are counted "
            "as not measured, not as wrong"
        )
    if answered and answered < B.EARLY_SIGNAL_N:
        note = (note + ". " if note else "") + (
            f"early signal: {answered} answer(s) is under the {B.EARLY_SIGNAL_N} the app asks for before it "
            "calls an estimate measured"
        )
    return LegResult(
        leg=leg,
        concept=concept.key,
        concept_title=concept.title,
        method=method,
        method_label=METHOD_LABELS[method],
        correct=correct,
        n=answered,
        accuracy=accuracy,
        ci=(lo, hi),
        expected=expected,
        residual=residual,
        evidence="not measured" if not answered else ("early signal" if answered < B.EARLY_SIGNAL_N else "measured"),
        seconds=lesson_seconds,
        latencies_ms=tuple(latencies),
        note=note,
    )


def compare(legs: Sequence[LegResult], *, p_know: float) -> dict[str, Any]:
    """The two legs side by side, with the sentence §6.5 requires: a small sample is a small sample.

    The comparison is between *residuals* (actual minus expected), not between raw scores, so a strong
    learner who gets 3/3 on both legs is not recorded as having learned more from either format.
    """
    if len(legs) < 2:
        return {"available": False, "note": "both legs have to be answered before anything can be compared"}
    a, b = legs[0], legs[1]
    if a.n == 0 and b.n == 0:
        return {
            "available": False,
            "legs": [x.as_dict() for x in legs],
            "note": (
                "neither lesson was answered, so there is no comparison to make. That is not a tie and "
                "not a preference: nothing was measured."
            ),
            "n": 0,
            "evidence": "not measured",
            "learner_style": None,
            "claim": None,
        }
    if a.n == 0 or b.n == 0:
        return {
            "available": False,
            "legs": [x.as_dict() for x in legs],
            "note": (
                f"only the {'first' if a.n else 'second'} lesson was answered, so the two cannot be "
                "compared. What was answered is recorded; the other leg is not measured, not zero."
            ),
            "n": a.n + b.n,
            "evidence": "not comparable",
            "learner_style": None,
            "claim": None,
        }
    difference = a.residual - b.residual
    n = a.n + b.n
    better = a.method if difference > 0 else b.method if difference < 0 else None
    note = (
        f"{n} answers in total, {a.n} and {b.n}. That is far too few to say which format suits you: the "
        "difference is shown because it is a measurement, and it is labelled an early signal because it "
        "is one. The app keeps measuring and any lasting change needs your approval."
    )
    if abs(difference) < 0.17:  # under half a question of three
        note = (
            f"{n} answers in total and the two legs came out within half a question of each other, which "
            "is what 'no difference found' looks like at this sample size. Nothing here favours a format."
        )
    return {
        "available": True,
        "legs": [leg.as_dict() for leg in legs],
        "residual_difference": round(difference, 3),
        "direction": ("the first lesson looked better" if difference > 0 else "the second lesson looked better" if difference < 0 else "no difference at all"),
        "better_method": better,
        "n": n,
        "evidence": "early signal" if n < B.EARLY_SIGNAL_N else "measured",
        "expected_basis": f"your own p_know for this topic ({round(p_know, 3)}) and each question's authored difficulty",
        "note": note,
        "claim": None,
        "learner_style": None,
    }


def recheck_for(legs: Sequence[LegResult], pair: Pair, *, seed: int) -> list[dict[str, Any]]:
    """The next-day recheck: two of the same questions per concept, asked again.

    A gain that disappears by the next day was not a gain. The recheck uses the *same* questions (the
    concept's own three, seeded down to two) so that what is measured is retention and not a new item's
    difficulty, and it is served only when the original test was taken the day before.
    """
    out: list[dict[str, Any]] = []
    import random

    for result in legs:
        concept = next((c for c in pair.concepts if c.key == result.concept), None)
        if concept is None:
            continue
        rng = random.Random(stable_seed("method-recheck", seed, concept.key))
        picks = rng.sample(range(len(concept.questions)), min(2, len(concept.questions)))
        out.append(
            {
                "concept": concept.key,
                "concept_title": concept.title,
                "method_first_taught": result.method,
                "items": [
                    public_task(
                        {
                            **dict(concept.questions[i]),
                            "id": f"{concept.key}_recheck_{i}",
                            "skill": "method_test",
                            "stage": "method_recheck",
                            "seconds": 30,
                            "instructions": "Two of yesterday's questions, asked again.",
                        }
                    )
                    for i in picks
                ],
                "first_round": result.as_dict(),
            }
        )
    return out


def score_recheck(row: Mapping[str, Any], pair: Pair, answers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Score the next-day recheck against the first round's own numbers.

    The recheck items carry the index of the question they came from in their id, so the answer key is
    the same authored question and not a look-alike: retention is only comparable if the item is the
    same item.
    """
    concept = next((c for c in pair.concepts if c.key == row.get("concept")), None)
    if concept is None:
        return {"correct": 0, "n": 0, "note": "this recheck does not belong to this topic's pair"}
    first = dict(row.get("first_round") or {})
    scored: list[dict[str, Any]] = []
    for task, answer in zip(list(row.get("items") or []), list(answers)):
        task_id = str(task.get("id") or "")
        suffix = task_id.rsplit("_", 1)[-1]
        index = int(suffix) if suffix.isdigit() and int(suffix) < len(concept.questions) else None
        if index is None:
            scored.append({"correct": None, "status": "unknown_item"})
            continue
        full = {**dict(concept.questions[index]), "id": task_id, "kind": "choice", "stage": "method_recheck"}
        scored.append(score_task(full, dict(answer or {})))
    correct = sum(1 for r in scored if r.get("correct") is True)
    n = sum(1 for r in scored if r.get("correct") is not None)
    kept = correct / n if n else 0.0
    before = float(first.get("accuracy") or 0.0)
    return {
        "concept": row.get("concept"),
        "method_first_taught": row.get("method_first_taught"),
        "correct": correct,
        "n": n,
        "accuracy": round(kept, 3),
        "first_round_accuracy": round(before, 3),
        "retained": round(kept - before, 3),
        "evidence": "early signal" if n < B.EARLY_SIGNAL_N else "measured",
        "items": scored,
        "note": (
            f"{correct} of {n} the next day, against {round(before * 100)}% yesterday. Two answers is a "
            "signal about whether the lesson stuck, not a measurement of it."
            if n
            else "nothing was answered, so this recheck is not measured rather than failed"
        ),
    }


def validate_pairs() -> list[str]:
    """Checks on the authored content. Returns problems (empty = valid)."""
    problems: list[str] = []
    for key, pair in PAIRS.items():
        if len(pair.concepts) != 2:
            problems.append(f"{key}: a method test needs exactly two concepts, has {len(pair.concepts)}")
            continue
        a, b = pair.concepts
        if a.matched_with != b.key or b.matched_with != a.key:
            problems.append(f"{key}: the two concepts do not name each other as their match")
        if abs(a.difficulty - b.difficulty) > 0.15:
            problems.append(
                f"{key}: difficulties {a.difficulty} and {b.difficulty} are not matched, so the comparison "
                "would be decided by the content rather than the format"
            )
        for concept in (a, b):
            if len(concept.questions) != QUESTIONS_PER_CONCEPT:
                problems.append(f"{concept.key}: {len(concept.questions)} questions, and the rule is three")
            kinds = {str(q.get("kind")) for q in concept.questions}
            if kinds != {"choice"}:
                problems.append(f"{concept.key}: question formats {sorted(kinds)} are not one shared format")
            for field_name, value in (("text", concept.text), ("visual", concept.visual), ("steps", concept.steps)):
                if not value:
                    problems.append(f"{concept.key}: no {field_name} lesson, so one method would show nothing")
            if concept.visual.get("kind") not in ("flow", "comparison_table", "timeline", "labelled_diagram"):
                problems.append(f"{concept.key}: visual template {concept.visual.get('kind')!r} is not one the app can draw")
            words = len(concept.text.split())
            if not 60 <= words <= 160:
                problems.append(f"{concept.key}: the text lesson is {words} words, and ninety seconds is about 60-160")
            for n, step in enumerate(concept.steps):
                if not str(step.get("why") or "").strip():
                    problems.append(f"{concept.key}: step {n + 1} has no reason, so it is not a worked example")
    if len(CELLS) != 12:
        problems.append(f"the counterbalance has {len(CELLS)} cells and the design needs 12")
    return problems


def methods_payload() -> list[dict[str, Any]]:
    return [{"id": m, "value": m, "label": METHOD_LABELS[m], "note": METHOD_NOTES[m]} for m in METHODS]


__all__ = [
    "CELLS",
    "LESSON_SECONDS",
    "METHODS",
    "METHOD_LABELS",
    "METHOD_NOTES",
    "N_CELLS",
    "PAIRS",
    "QUESTIONS_PER_CONCEPT",
    "Assignment",
    "Concept",
    "LegResult",
    "Pair",
    "assign",
    "balance_report",
    "compare",
    "expected_accuracy",
    "methods_payload",
    "pair_for",
    "recheck_for",
    "score_leg",
    "served_items",
    "score_recheck",
    "validate_pairs",
]
