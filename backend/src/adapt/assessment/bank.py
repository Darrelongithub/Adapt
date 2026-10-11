"""ADAPT's seed curriculum: a real prerequisite graph + parametric items.

Subject choice is deliberate: key-stage-3/4 maths is where BKT, worked examples and
spaced retrieval have the densest evidence base, and where "the answer is a number"
means grading can be objective rather than vibes-based.

Every item is either:

* **parametric** -- ``stem_template`` + ``answer_spec.ranges`` + ``answer_spec.formula``
  (the same variable/expression model Numbas uses), so :func:`adapt.ai.local_tutor.render_variant`
  can generate a fresh version with a recomputed answer. This is what makes
  "adaptive practice" real rather than a shuffled list; and
* **fixed** -- a specific stem (visual/teach-back/worked-example items), where a
  number would not be the point.

Teach-back items carry a ``rubric.required_concepts`` list with synonym sets, so
"explain it in your own words" is scored on ideas covered and never on eloquence.
"""

from __future__ import annotations

import random
from typing import Any

from ..ai.local_tutor import render_variant  # re-exported for the API layer


TOPICS: list[dict[str, Any]] = [
    # ---- code, title, difficulty (0=easy .. 1=hard), est seconds, prereq codes
    dict(code="integer_ops", title="Integer operations", difficulty=0.2, est=60, prereqs=[]),
    dict(code="fractions_intro", title="What a fraction means", difficulty=0.25, est=75, prereqs=["integer_ops"]),
    dict(code="equivalent_fractions", title="Equivalent fractions", difficulty=0.35, est=90, prereqs=["fractions_intro"]),
    dict(code="fraction_add", title="Adding and subtracting fractions", difficulty=0.5, est=110, prereqs=["equivalent_fractions"]),
    dict(code="fraction_mult", title="Multiplying and dividing fractions", difficulty=0.5, est=110, prereqs=["fractions_intro"]),
    dict(code="decimals", title="Decimals and place value", difficulty=0.35, est=90, prereqs=["fractions_intro"]),
    dict(code="percentages", title="Percentages of quantities", difficulty=0.45, est=100, prereqs=["decimals", "equivalent_fractions"]),
    dict(code="ratio", title="Ratio and proportion", difficulty=0.55, est=120, prereqs=["fraction_mult"]),
    dict(code="negative_numbers", title="Negative numbers", difficulty=0.4, est=80, prereqs=["integer_ops"]),
    dict(code="order_of_operations", title="Order of operations", difficulty=0.3, est=70, prereqs=["integer_ops"]),
    dict(code="collecting_terms", title="Collecting like terms", difficulty=0.45, est=95, prereqs=["order_of_operations"]),
    dict(code="linear_equations", title="Solving linear equations", difficulty=0.6, est=130, prereqs=["collecting_terms", "negative_numbers"]),
    dict(code="rearranging", title="Rearranging formulae", difficulty=0.7, est=150, prereqs=["linear_equations"]),
    dict(code="graphs_lines", title="Graphs of straight lines", difficulty=0.65, est=140, prereqs=["linear_equations"]),
    dict(code="indices", title="Indices and powers", difficulty=0.55, est=110, prereqs=["integer_ops", "fraction_mult"]),
    dict(code="standard_form", title="Standard form", difficulty=0.65, est=125, prereqs=["indices", "decimals"]),
    dict(code="statistical_mean", title="Mean, median and range", difficulty=0.4, est=90, prereqs=["decimals"]),
    dict(code="area_perimeter", title="Area and perimeter", difficulty=0.35, est=85, prereqs=["order_of_operations"]),
    dict(code="unit_conversion", title="Converting units", difficulty=0.45, est=95, prereqs=["ratio", "decimals"]),
    dict(code="probability", title="Probability as a fraction", difficulty=0.5, est=110, prereqs=["fractions_intro", "percentages"]),
]

TOPIC_NOTES: dict[str, str] = {
    "fractions_intro": (
        "A fraction names a part of a whole. The denominator says how many equal parts the whole is cut "
        "into; the numerator says how many of those parts you are talking about. The parts must be equal or "
        "the fraction does not mean anything. Writing a fraction is division: 3/4 means 3 divided by 4."
    ),
    "equivalent_fractions": (
        "Two fractions are equivalent when they name the same point on the number line, even though the "
        "digits differ. Multiplying the numerator and the denominator by the same non-zero number cannot "
        "change the value, because you have multiplied by 1. Cancelling down divides both by a common "
        "factor, for the same reason. To compare fractions, rewrite them over a common denominator."
    ),
    "fraction_add": (
        "You can only add or subtract like units, so fractions need a common denominator first. Choose the "
        "lowest common multiple of the denominators, scale each fraction up to it, then add or subtract the "
        "numerators and keep that denominator. Never add denominators: that would change the size of the part."
    ),
    "fraction_mult": (
        "Multiplying fractions needs no common denominator: multiply the numerators, multiply the "
        "denominators, then cancel. 'Of' means multiply, so 2/5 of 15 is 2/5 x 15. Dividing by a fraction "
        "is multiplying by its reciprocal, because dividing by a/b asks how many a/b fit into the amount."
    ),
    "decimals": (
        "Each place value is ten times the one on its right, and that keeps going past the decimal point: "
        "tenths, hundredths, thousandths. A decimal is a fraction whose denominator is a power of ten, so "
        "0.37 is 37/100. Shifting the point multiplies or divides by ten; it does not change the digits."
    ),
    "percentages": (
        "Percent means per hundred, so a percentage is a fraction over 100 and a decimal divided by 100. "
        "To find a percentage of a quantity, convert the percentage to a decimal and multiply. A decrease of "
        "p percent is multiplying by (1 - p/100); repeated percentage change multiplies each time, which is "
        "why a 10% rise then a 10% fall does not return you to the start."
    ),
    "ratio": (
        "A ratio compares quantities in the parts they are made of, and it simplifies like a fraction. To "
        "share an amount in the ratio a:b, add the parts, divide the total by that number to find one part, "
        "then scale up. Equivalent ratios come from multiplying every term by the same number."
    ),
    "negative_numbers": (
        "On a number line, subtracting moves left and adding moves right, and that stays true below zero. "
        "The error to avoid is treating the minus sign as a separator: -3 - 5 is two moves left, giving -8. "
        "Multiplying or dividing two negatives gives a positive because the direction reverses twice."
    ),
    "order_of_operations": (
        "Brackets first, then powers, then multiplication and division left to right, then addition and "
        "subtraction left to right. Multiplication and division have equal rank, so you work strictly left to "
        "right; writing a fraction bar is an implicit bracket around the numerator and denominator."
    ),
    "collecting_terms": (
        "Like terms have the same variables to the same powers, so they count the same thing and can be "
        "added. x and x squared are not like terms. The coefficient is the number in front, including its "
        "sign: in 3a - 5b the second term is -5b, not 5b."
    ),
    "linear_equations": (
        "Solving means finding the value that makes both sides equal, and you keep equality by doing the same "
        "operation to both sides. Undo operations in reverse order: brackets first, then the constant, then "
        "the coefficient. Check by substituting back into the original equation."
    ),
    "rearranging": (
        "Rearranging is solving with the subject as the unknown, so the same legal moves apply. Collect the "
        "terms containing the subject on one side, factorise it out, then divide by the remaining bracket. You "
        "cannot cancel a term that is part of a sum; factorise first."
    ),
    "graphs_lines": (
        "In y = mx + c, m is the gradient (change in y over change in x) and c is the y-intercept. Parallel "
        "lines share a gradient; perpendicular lines have gradients whose product is -1. A point lies on the "
        "line exactly when its coordinates satisfy the equation."
    ),
    "indices": (
        "A power counts repeated multiplication of a base. Multiplying same-base powers adds indices, "
        "dividing subtracts them, and a power of a power multiplies them. Anything to the power zero is 1, "
        "and a negative index means reciprocal: x^-2 = 1/x^2."
    ),
    "standard_form": (
        "Standard form writes a number as A x 10^n where 1 <= A < 10, which makes size comparisons easy. A "
        "large number gets a positive n and a small one a negative n. Multiply the A values and add the "
        "indices, then re-adjust so A is back in range."
    ),
    "statistical_mean": (
        "The mean is the total divided by the count, so it depends on every value and is pulled by outliers. "
        "The median is the middle value in order and resists outliers. The range is largest minus smallest and "
        "describes spread only, never the average."
    ),
    "area_perimeter": (
        "Perimeter is a length, so it is added in units; area counts square units inside, so it multiplies "
        "two lengths. For a triangle, halve the enclosing rectangle. Rearrange the area formula to recover a "
        "missing side rather than memorising a variant."
    ),
    "unit_conversion": (
        "Converting units is multiplying by 1 written as an equivalent pair, such as 100 cm over 1 m, so the "
        "unit you do not want cancels. Squared units need the factor squared; cubic units cubed. Estimate the "
        "answer first to catch a multiply-versus-divide slip."
    ),
    "probability": (
        "For equally likely outcomes, probability is favourable outcomes over total outcomes, which is a "
        "fraction between 0 and 1. Multiply by 100 for a percentage. Independent events multiply; 'at least "
        "one' is usually faster as 1 minus the probability of none."
    ),
}


def mcq(stem, choices, correct, *, hints=(), steps=(), difficulty=0.4, mode="read", est=70, misc=None, prompt=None, **kw):
    """choices: list of (text, why_wrong_or_None)."""
    return {
        "kind": "mcq",
        "stem": stem,
        "experience_mode": mode,
        "prompt_context": prompt,
        "choices": [{"text": t, "correct": i == correct, "why_wrong": w} for i, (t, w) in enumerate(choices)],
        "answer_spec": {"correct_index": correct},
        "hints": list(hints),
        "solution_steps": [{"text": s} for s in steps],
        "difficulty": difficulty,
        "est_seconds": est,
        "tags": kw.pop("tags", []),
        **({"tags": kw.pop("tags")} if kw.get("tags") else {}),
    }


def num(
    stem_template,
    formula,
    ranges=None,
    variables=None,
    *,
    hints=(),
    steps=(),
    difficulty=0.4,
    mode="practice",
    est=80,
    unit=None,
    tolerance=1e-9,
    tags=None,
    prompt=None,
    derived=None,
):
    spec: dict[str, Any] = {"formula": formula, "variables": dict(variables or {}), "tolerance": tolerance}
    if ranges:
        spec["ranges"] = {k: list(v) for k, v in ranges.items()}
    if derived:
        spec["derived"] = dict(derived)
    if unit:
        spec["unit"] = unit
    return {
        "kind": "numeric",
        "stem": _render(stem_template, spec["variables"]),
        "stem_template": stem_template,
        "experience_mode": mode,
        "prompt_context": prompt,
        "answer_spec": spec,
        # Steps and hints stay as *templates*: the numbers in them must match the
        # sampled variant, so they are rendered per variant, not once at authoring.
        "solution_steps": [{"text": t} for t in steps],
        "hints": list(hints),
        "difficulty": difficulty,
        "est_seconds": est,
        "tags": tags or [],
    }


def text_q(stem, accepted, *, keywords=None, hints=(), steps=(), difficulty=0.4, mode="read", est=90, tags=None, prompt=None):
    return {
        "kind": "text",
        "stem": stem,
        "experience_mode": mode,
        "prompt_context": prompt,
        "answer_spec": {"values": list(accepted), "keywords": list(keywords or [])},
        "solution_steps": [{"text": s} for s in steps],
        "hints": list(hints),
        "difficulty": difficulty,
        "est_seconds": est,
        "tags": tags or [],
    }


def teachback(topic_title, concepts, *, est=150, difficulty=0.5, prompt=None):
    """concepts: (canonical phrase, [synonyms])."""
    return {
        "kind": "teachback",
        "stem": f"Explain {topic_title} out loud in your own words, as if to someone meeting it for the first time.",
        "experience_mode": "teachback",
        "prompt_context": prompt,
        "answer_spec": {"min_words": 12},
        "rubric": {
            "required_concepts": [{"concept": c, "synonyms": [c] + list(s)} for c, s in concepts],
            "pass_fraction": 0.6,
        },
        "hints": [
            "Name the key quantity first.",
            "Say which operation connects the quantities.",
            "Finish with one example, even a tiny one.",
        ],
        "solution_steps": [],
        "difficulty": difficulty,
        "est_seconds": est,
        "tags": ["meta-cognition"],
    }


def example(stem, steps, *, est=90, difficulty=0.3, prompt=None):
    return {
        "kind": "worked_example",
        "stem": stem,
        "experience_mode": "example",
        "prompt_context": prompt,
        "answer_spec": {},
        "solution_steps": [{"text": s} for s in steps],
        "hints": [],
        "difficulty": difficulty,
        "est_seconds": est,
        "tags": ["worked-example"],
    }


def visual(stem, alt, diagram, *, choices=None, correct=0, est=110, difficulty=0.45, hints=(), tags=None):
    return {
        "kind": "mcq" if choices else "text",
        "stem": stem,
        "experience_mode": "visual",
        "prompt_context": alt,
        "visual_spec": diagram,
        "choices": [{"text": t, "correct": i == correct, "why_wrong": w} for i, (t, w) in enumerate(choices or [])],
        "answer_spec": ({"correct_index": correct} if choices else {"values": []}),
        "hints": list(hints),
        "solution_steps": [],
        "difficulty": difficulty,
        "est_seconds": est,
        "tags": tags or ["visual"],
    }


def variant_seed(student_id: str | None, item_id: str | None) -> int:
    """The seed every learner-facing render of one item must use.

    Derived from ``zlib.crc32`` rather than ``hash()`` on purpose: ``hash()`` of a string
    is salted per process, so a hint asked for after a server restart would substitute
    *different numbers* into the same question the learner is looking at. One item, one
    variant per learner, whatever endpoint is asked.
    """
    import zlib

    return zlib.crc32(f"{student_id or 'anon'}:{item_id or ''}".encode()) & 0xFFFFFFFF


def _render(template: str, variables: dict[str, Any]) -> str:
    if not template:
        return ""
    try:
        return template.format(**variables) if variables else template
    except (KeyError, IndexError):
        return template


# --------------------------------------------------------------------------- #
# the bank
# --------------------------------------------------------------------------- #

ITEMS: dict[str, list[dict[str, Any]]] = {
    "integer_ops": [
        num("Work out {a} - {b} - {c}.", "a-b-c", {"a": [5, 20], "b": [1, 9], "c": [1, 9]}, {"a": 12, "b": 5, "c": 4},
            hints=["Work left to right; each minus sign belongs to the number after it."],
            steps=["{a} - {b} = {ab}", "{ab} - {c} = {ans}"],
            difficulty=0.2, tags=["arithmetic"], derived={"ab": "a-b"}),
        mcq("Which calculation must be done first in 7 + 3 x 2 - 1?",
            [("Add 7 + 3", "Addition only comes first inside brackets; here it does not."),
             ("Multiply 3 x 2", "Multiplication outranks addition and subtraction."),
             ("Subtract 2 - 1", "Work left to right only within the same rank."),
             ("Go strictly left to right", "That rule applies within a rank, not across ranks.")], 1,
            difficulty=0.25, mode="read"),
        example("Working through 12 - 5 - 4 without sign errors",
                ["Read it as two moves, left to right: start at 12, subtract 5, then subtract 4.",
                 "12 - 5 = 7.",
                 "7 - 4 = 3. The answer is a position on the number line, not a calculation left half-done."],
                difficulty=0.2, est=70),
    ],
    "fractions_intro": [
        mcq("A pizza is cut into 8 equal slices and 3 are eaten. What fraction is left?",
            [("3/8", "That is the fraction eaten, not left."),
             ("5/8", "5 slices left out of 8 equal slices."),
             ("8/5", "The whole goes underneath: 8 equal parts in total."),
             ("3/5", "That mixes the eaten count with the remaining count.")], 1,
            hints=["The bottom number is the total number of equal parts."],
            steps=["8 - 3 = 5 slices left", "5 of 8 equal parts = 5/8"], difficulty=0.25, mode="read"),
        visual("Which shaded bar matches 1/4? Each bar is split into the number of equal parts shown.",
               "Four bars: A has 4 equal parts with 1 shaded; B has 2 parts with 1 shaded; C has 4 parts with 2 shaded; D has 3 parts with 1 shaded.",
               {"type": "bars", "parts": [[1, 4], [1, 2], [2, 4], [1, 3]], "target": "1/4"},
               choices=[("Bar A: 1 shaded of 4 equal parts", None),
                        ("Bar B: 1 shaded of 2 parts", "Half is not a quarter."),
                        ("Bar C: 2 shaded of 4 parts", "That is 2/4, which equals 1/2."),
                        ("Bar D: 1 shaded of 3 parts", "Three equal parts give thirds, not quarters.")],
               correct=0, hints=["Count the total number of equal parts first, then the shaded ones."]),
        teachback("what a fraction means", [("denominator", ["bottom", "parts total", "how many parts"]),
                                          ("numerator", ["top", "parts taken", "how many of"]),
                                          ("equal parts", ["same size", "equal size", "equal pieces"])],
                  difficulty=0.3),
    ],
    "equivalent_fractions": [
        num("Write {a}/{b} as an equivalent fraction with denominator {d}. Give the numerator only.",
            "a*k", {"a": [1, 5], "b": [2, 6], "k": [2, 4]}, {"a": 3, "b": 4, "k": 2},
            hints=["By what number must {b} be multiplied to reach {d}?"],
            steps=["{b} x {k} = {d}, so the scale factor is {k}",
                   "multiply the numerator by the same number: {a} x {k} = {ans}",
                   "check: {a}/{b} and {ans}/{d} name the same point"],
            difficulty=0.35, derived={"d": "b*k"}),
        mcq("Which pair are equivalent?",
            [("2/3 and 8/12", "2/3 x 4/4 = 8/12, so these are the same number."),
             ("2/3 and 3/2", "One is the reciprocal of the other."),
             ("1/3 and 2/6", "1/3 x 2/2 = 2/6, so this pair is equivalent too."),
             ("3/4 and 4/5", "Adding 1 to each part is not a scale factor.")], 0,
            difficulty=0.4, mode="read",
            prompt="More than one pair here is equivalent: the point is to test each pair by scale factor, not by shape."),
        example("Cancelling 12/18 to lowest terms",
                ["Look for a number dividing both 12 and 18. 6 works.",
                 "12 / 6 = 2 and 18 / 6 = 3.",
                 "So 12/18 = 2/3, and 2 and 3 share no factor above 1, so it is fully cancelled."],
                difficulty=0.3),
        teachback("why cancelling does not change the value",
                  [("dividing top and bottom by the same number", ["same number", "both", "equal"]),
                   ("dividing by one", ["by 1", "one", "identity"]),
                   ("same point on the number line", ["same value", "same size", "equivalent"])]),
    ],
    "fraction_add": [
        num("Work out {a}/{b} + {c}/{d}. Give the numerator of the answer over {bd}.",
            "a*d+c*b", {"a": [1, 5], "b": [3, 6], "c": [1, 5], "d": [7, 11]}, {"a": 3, "b": 4, "c": 1, "d": 8},
            hints=["Make the denominators the same before adding anything."],
            steps=["{b} x {d} = {bd}, a common denominator",
                   "{a}/{b} = {an}/{bd} and {c}/{d} = {cn}/{bd}",
                   "{an} + {cn} = {ans}, so the answer is {ans}/{bd}",
                   "the denominator is NOT added: that would change the size of each part"],
            difficulty=0.5, est=110, derived={"bd": "b*d", "an": "a*d", "cn": "c*b"}),
        mcq("Why can you not do 1/2 + 1/3 = 2/5?",
            [("Because the denominators count different-sized parts", "Halves and thirds are not the same unit, so the counts cannot be added."),
             ("Because you must add denominators too", "Adding denominators changes the size of each part."),
             ("Because the fractions are both less than 1", "That is not the obstacle here."),
             ("Because 2/5 is not in lowest terms", "Cancelling is a separate step.")], 0,
            difficulty=0.45, mode="read"),
        visual("Two strips are split as shown. Which total is shown by the shaded parts?",
               "Strip 1: 2 of 4 parts shaded. Strip 2: 1 of 4 parts shaded. Both strips have the same 4 equal parts.",
               {"type": "strips", "shaded": [2, 1], "parts": [4, 4]},
               choices=[("3/4", "Two quarters plus one quarter makes three quarters."),
                        ("3/8", "That adds the denominators as well."),
                        ("2/4", "That ignores the second strip."),
                        ("1/2", "That is only the first strip.")],
               correct=0, hints=["Both strips are in quarters, so the units already match."],
               difficulty=0.45, est=100),
        example("Adding 3/4 + 1/8 the slow way, then the quick way",
                ["Slow but safe: 4 x 8 = 32, so 3/4 = 24/32 and 1/8 = 4/32.",
                 "24 + 4 = 28, giving 28/32, which cancels to 7/8.",
                 "Quicker: 8 is a multiple of 4, so use 8: 3/4 = 6/8, and 6 + 1 = 7, giving 7/8 directly."],
                difficulty=0.5, est=100),
        teachback("adding two fractions with different denominators",
                  [("common denominator", ["same denominator", "like units", "lcd"]),
                   ("scale both fractions", ["multiply top and bottom", "equivalent", "scale"]),
                   ("add only the numerators", ["add tops", "numerators only", "keep the denominator"])],
                  difficulty=0.5, est=170),
    ],
    "fraction_mult": [
        num("Work out {a}/{b} of {c}.", "a*c/b", {"a": [1, 4], "b": [3, 6], "c": [12, 40]}, {"a": 2, "b": 5, "c": 15},
            hints=["'Of' means multiply.", "Find one {b}th of {c} first."],
            steps=["{c} / {b} = {one} is one {b}th", "{one} x {a} = {ans} is {a} {b}ths"],
            difficulty=0.5, derived={"one": "c/b"}),
        num("Divide {a}/{b} by {c}/{d}. Give the result as a decimal.",
            "(a*d)/(b*c)", {"a": [1, 6], "b": [3, 7], "c": [1, 5], "d": [4, 9]}, {"a": 3, "b": 4, "c": 2, "d": 5},
            hints=["Dividing by a fraction means multiplying by its reciprocal."],
            steps=["Reciprocal of {c}/{d} is {d}/{c}", "{a}/{b} x {d}/{c} = {ad}/{bc}", "as a decimal: {ans}"],
            difficulty=0.55, tolerance=1e-6, derived={"ad": "a*d", "bc": "b*c"}),
        example("Why dividing by 1/8 multiplies by 8",
                ["The question '3/4 divided by 1/8' asks: how many eighths fit into three quarters?",
                 "Each quarter holds 8 eighths, so 3 quarters hold 24 eighths.",
                 "That is exactly 3/4 x 8 = 6, because 24/4 = 6."],
                difficulty=0.55, est=95),
    ],
    "decimals": [
        num("What is the value of the digit {d} in 4.{d}1? Give the place value as a decimal.",
            "d/10", None, {"d": 7},
            hints=["Which column is the digit in, counting from the point?"],
            steps=["The first column after the point is tenths", "so {d} there means {d} x 0.1 = {ans}"],
            difficulty=0.35, tolerance=1e-9),
        mcq("Which is largest?",
            [("0.4", "Four tenths."), ("0.36", "Thirty-six hundredths, i.e. 0.36."),
             ("0.401", "Four tenths and a thousandth more, so this beats 0.4."),
             ("0.04", "Four hundredths, the smallest.")], 2,
            difficulty=0.4, mode="read",
            prompt="More digits does not mean a bigger decimal. Compare column by column from the left."),
    ],
    "percentages": [
        num("Work out {p}% of {n}.", "p*n/100", {"p": [5, 40], "n": [20, 400]}, {"p": 15, "n": 80},
            hints=["Convert the percentage to a decimal first: {p}% = {dec}."],
            steps=["{p}% = {dec}", "{dec} x {n} = {ans}"], difficulty=0.45, derived={"dec": "p/100"}, tolerance=1e-6),
        mcq("A price goes up 10% then down 10%. It ends up:",
            [("the same as before", "The two 10% are taken of different amounts."),
             ("lower than before", "0.9 x 1.1 = 0.99, so a 1% net fall."),
             ("higher than before", "The decrease is taken from the larger, raised amount."),
             ("impossible to say", "It is determined exactly by the multipliers.")], 1,
            difficulty=0.6, mode="read",
            prompt="Think in multipliers rather than adding and subtracting 10."),
        num("A jacket costs {p} after {d}% off. What was the original price?",
            "p/(1-d/100)", {"p": [20, 200], "d": [10, 40]}, {"p": 45, "d": 25},
            hints=["Dividing by the multiplier reverses the decrease."],
            steps=["After {d}% off you pay {m} of the original", "{p} / {m} = {ans}"],
            difficulty=0.6, tolerance=1e-6, est=120, derived={"m": "1-d/100"}),
        teachback("how a percentage decrease is applied",
                  [("convert the percent to a decimal", ["divide by 100", "decimal"]),
                   ("use one multiplier", ["0.8", "multiply by 1 minus", "multiplier"]),
                   ("the base changes each time", ["different amount", "new total", "compounding"])],
                  difficulty=0.5, est=160),
    ],
    "ratio": [
        num("Share {t} in the ratio {a}:{b}. What is the larger share?", "t*b/(a+b)",
            {"a": [1, 5], "b": [2, 7], "t": [20, 200]}, {"a": 2, "b": 3, "t": 50},
            hints=["How many equal parts is the total split into?"],
            steps=["{a} + {b} = {parts} parts", "{t} / {parts} = {one} per part",
                   "the larger share is {b} x {one} = {ans}"],
            difficulty=0.55, tolerance=1e-6, derived={"parts": "a+b", "one": "t/(a+b)"}),
        visual("A bar model splits a total into 5 equal parts, with 2 shaded and 3 unshaded. Which ratio does it show?",
               "One bar, 5 equal parts: 2 shaded, 3 unshaded.",
               {"type": "bar_model", "shaded": 2, "unshaded": 3},
               choices=[("2:3", "Shaded parts to unshaded parts."), ("2:5", "That compares one part to the whole."),
                        ("3:2", "That is the reverse order."), ("5:2", "That is whole to shaded.")],
               correct=0, hints=["Say which quantities the ratio is comparing, in that order."],
               difficulty=0.5),
    ],
    "negative_numbers": [
        num("Work out {a} - {b}.", "a-b", {"a": [-9, -1], "b": [1, 9]}, {"a": -3, "b": 5},
            hints=["Two moves on the number line: start at {a}, then go {b} left."],
            steps=["start at {a}", "subtracting {b} moves {b} to the left", "you land on {ans}"],
            difficulty=0.4),
        mcq("What is -3 x -4?",
            [("-12", "The sign is wrong; two negatives multiply to a positive."),
             ("12", "Both signs reverse, so the product is positive."),
             ("-7", "That adds the numbers instead of multiplying."),
             ("7", "Adds the magnitudes and ignores the sign rule.")], 1, difficulty=0.45, mode="read"),
        visual("On the number line, which point is -3 - 5?",
               "Number line from -10 to 5 with points labelled P at -8, Q at 2, R at 2.5, S at -2.",
               {"type": "number_line", "from": -10, "to": 5, "points": {"P": -8, "Q": 2, "R": 2.5, "S": -2}},
               choices=[("P (-8)", "Start at -3 and move 5 left: -8."), ("Q (2)", "That moves right instead of left."),
                        ("S (-2)", "That only moves 1 left."), ("R (2.5)", "That is the sum, not the difference.")],
               correct=0, hints=["Subtracting always moves left, even when you are already below zero."],
               difficulty=0.4),
    ],
    "order_of_operations": [
        num("Work out {a} + {b} x {c}.", "a+b*c", {"a": [1, 9], "b": [2, 9], "c": [2, 9]}, {"a": 7, "b": 3, "c": 2},
            hints=["Multiplication outranks addition, whatever the left-to-right order looks like."],
            steps=["{b} x {c} = {bc}", "{a} + {bc} = {ans}"], difficulty=0.3, derived={"bc": "b*c"}),
        mcq("What is 12 - 3 x 2 + 4?",
            [("10", None),
             ("22", "That works strictly left to right and does 12 - 3 before the multiplication."),
             ("4", "That treats 3 x 2 + 4 as one group."),
             ("14", "That does 12 - 3 first, then multiplies the 2 in.")],
            0, hints=["Multiplication and division come before addition and subtraction; work left to right only within a rank."],
            steps=["3 x 2 = 6", "12 - 6 + 4 = 10"], difficulty=0.35),
        num("Work out {a} x {b} + {c} div {d}.", "a*b + c//d", {"a": [2, 6], "b": [2, 6], "c": [2, 9], "d": [2, 3]},
            {"a": 3, "b": 4, "c": 9, "d": 3},
            hints=["Do the division before the addition, and use exact (integer) division."],
            steps=["{a} x {b} = {ab}", "{c} div {d} = {cd}", "{ab} + {cd} = {ans}"],
            derived={"ab": "a*b", "cd": "c//d"}, difficulty=0.4, est=75),
    ],
    "collecting_terms": [
        num("Simplify 3a + {b}a - 2a. Give the coefficient of a.", "3+b-2", {"b": [1, 9]}, {"b": 4},
            hints=["Like terms have the same variable to the same power."],
            steps=["3a + {b}a = {s}a", "{s}a - 2a = {ans}a"], difficulty=0.45, derived={"s": "3+b"}),
        mcq("Which are like terms?",
            [("3x and 3x^2", "Different powers, so not like terms."),
             ("-5y and 2y", "Same variable to the same power: these can be collected."),
             ("4 and 4x", "A constant is not a like term of a variable term."),
             ("2ab and 2a", "Different variable sets.")], 1, difficulty=0.5),
    ],
    "linear_equations": [
        num("Solve {a}x + {b} = {c}. Give x as a decimal.", "(c-b)/a", {"a": [2, 7], "b": [1, 15], "c": [20, 60]},
            {"a": 3, "b": 4, "c": 25},
            hints=["Undo the +{b} before undoing the x{a}."],
            steps=["{c} - {b} = {t}", "{t} / {a} = {ans}", "check: {a} x {ans} + {b} = {c}"],
            difficulty=0.6, tolerance=1e-6, derived={"t": "c-b"}),
        num("Solve {a}x + 3 = {b}x + {c}. Give x.", "(c-3)/(a-b)", {"a": [1, 4], "b": [5, 9], "c": [10, 40]},
            {"a": 2, "b": 5, "c": 18},
            hints=["Get all x terms on one side; keep the coefficient positive if you can."],
            steps=["{a}x + 3 = {b}x + {c}", "subtract {a}x: 3 = {d}x + {c}",
                   "subtract {c}: {neg} = {d}x", "divide by {d}: x = {ans}"],
            difficulty=0.7, tolerance=1e-6, est=140, derived={"d": "b-a", "neg": "3-c"}),
        teachback("how you solve a two-step equation",
                  [("same operation on both sides", ["both sides", "balance", "equal"]),
                   ("undo operations in reverse order", ["reverse", "inverse", "undo"]),
                   ("check by substituting", ["substitute", "check", "plug"])],
                  difficulty=0.6, est=180),
    ],
    "rearranging": [
        num("Rearrange v = u + a*t for t. Given u={u}, v={v}, a={a}, what is t?", "(v-u)/a",
            {"u": [1, 9], "v": [10, 40], "a": [2, 9]}, {"u": 4, "v": 22, "a": 3},
            hints=["Isolate the a*t term first, then divide by a."],
            steps=["v - u = a t", "t = (v - u) / a", "t = ({v} - {u}) / {a} = {ans}"],
            difficulty=0.7, tolerance=1e-6),
        mcq("Which is the correct rearrangement of y = 3(x + 2) for x?",
            [("x = y/3 - 2", None),
             ("x = y/3 + 2", "The +2 moves to the other side, so it becomes -2."),
             ("x = (y - 2)/3", "That subtracts 2 before undoing the multiplication by 3."),
             ("x = 3y - 2", "That multiplies by 3 instead of dividing.")],
            0, hints=["Undo the operations in reverse order: divide by 3 first, then deal with the +2."],
            steps=["y/3 = x + 2", "x = y/3 - 2"], difficulty=0.7, est=110),
        num("Rearrange y = {k}(x + {b}) for x. Given y={y}, k={k} and b={b}, what is x?", "y/k-b",
            {"k": [2, 6], "b": [1, 9], "y": [10, 40]}, {"k": 3, "b": 2, "y": 21},
            hints=["Divide by {k} before you subtract {b}."],
            steps=["y / {k} = x + {b}", "x = y / {k} - {b}", "x = {y} / {k} - {b} = {ans}"],
            difficulty=0.72, tolerance=1e-6, est=140),
    ],
    "graphs_lines": [
        num("What is the gradient of the line through ({x1},{y1}) and ({x2},{y2})?",
            "(y2-y1)/(x2-x1)", {"x1": [0, 3], "y1": [1, 6], "x2": [4, 9], "y2": [2, 12]},
            {"x1": 1, "y1": 2, "x2": 5, "y2": 10},
            hints=["Gradient is change in y divided by change in x."],
            steps=["up: {y2} - {y1} = {dy}", "across: {x2} - {x1} = {dx}", "{dy}/{dx} = {ans}"],
            difficulty=0.65, tolerance=1e-6, derived={"dy": "y2-y1", "dx": "x2-x1"}),
        mcq("Which line is parallel to y = 2x + 1?",
            [("y = 2x - 4", "Same gradient 2, different intercept: parallel."),
             ("y = x + 1", "Gradient 1, not 2."),
             ("y = -2x + 1", "Same number, opposite sign: that is a reflection, not a parallel."),
             ("y = 0.5x + 1", "Gradient is the reciprocal, not equal.")], 0, difficulty=0.6),
    ],
    "indices": [
        num("Simplify x^{a} * x^{b}. Give the new index.", "a+b", {"a": [2, 9], "b": [2, 9]}, {"a": 5, "b": 3},
            hints=["Same base multiplied: what happens to the indices?"],
            steps=["x^{a} means {a} factors of x, and x^{b} means {b} more",
                   "counting them together gives {a} + {b} = {ans}"],
            difficulty=0.55),
        mcq("What is 2^-3?",
            [("-6", "The sign of the index does not make the value negative."),
             ("1/8", "A negative index means the reciprocal: 1 / 2^3."),
             ("8", "That is 2^3, without the reciprocal."),
             ("-1/8", "No sign change is involved here.")], 1, difficulty=0.6, mode="read"),
    ],
    "standard_form": [
        num("Write {n} in standard form as A x 10^3. Give A to 2 decimal places.",
            "n/1000", {"n": [1200, 9800]}, {"n": 4650},
            hints=["A must be at least 1 and less than 10."],
            steps=["{n} / 1000 = {ans}", "so {n} = {ans} x 10^3"],
            difficulty=0.65, tolerance=0.01),
        mcq("Which ordinary number is 4.65 x 10^3?",
            [("4650", None),
             ("465", "That is 4.65 x 10^2."),
             ("46500", "That is 4.65 x 10^4."),
             ("0.00465", "That is 4.65 x 10^-3: the exponent is positive here.")],
            0, hints=["Multiply by 1000: move the decimal point three places right."],
            steps=["10^3 = 1000", "4.65 x 1000 = 4650"], difficulty=0.55, est=60),
        num("Write {n} in standard form as A x 10^-3. Give A to 2 decimal places.",
            "n*1000", {"n": [0.0012, 0.0098]}, {"n": 0.0042},
            hints=["A must be at least 1 and less than 10, so move the point three places right."],
            steps=["{n} x 1000 = {ans}", "so {n} = {ans} x 10^-3"],
            difficulty=0.68, tolerance=0.01, est=110),
    ],
    "statistical_mean": [
        num("The mean of {a}, {b} and {c} is what?", "(a+b+c)/3", {"a": [2, 20], "b": [3, 20], "c": [4, 20]},
            {"a": 4, "b": 9, "c": 8},
            hints=["Total first, then divide by how many numbers there are."],
            steps=["{a} + {b} + {c} = {tot}", "{tot} / 3 = {ans}"],
            difficulty=0.4, tolerance=1e-6, derived={"tot": "a+b+c"}),
        mcq("Adding one very large value to a data set affects:",
            [("the mean more than the median", "The mean uses every value, so the outlier pulls it; the median may barely move."),
             ("the median more than the mean", "The median is the resistant one."),
             ("neither", "The mean always changes when the total changes."),
             ("the range only", "The mean changes too.")], 0, difficulty=0.5, mode="read"),
    ],
    "area_perimeter": [
        num("A rectangle is {w} cm by {h} cm. Give its perimeter in cm.", "2*(w+h)",
            {"w": [3, 20], "h": [2, 15]}, {"w": 8, "h": 5},
            hints=["Perimeter adds all four sides: two widths and two heights."],
            steps=["{w} + {h} = {s}", "{s} x 2 = {ans}"], difficulty=0.35, unit="cm", derived={"s": "w+h"}),
        num("A triangle has base {b} cm and height {h} cm. Give its area in cm^2.", "b*h/2",
            {"b": [4, 20], "h": [3, 12]}, {"b": 10, "h": 6},
            hints=["A triangle is half the rectangle that encloses it."],
            steps=["{b} x {h} = {dbl}", "half of that = {ans}"], difficulty=0.4, unit="cm^2", derived={"dbl": "b*h"}),
    ],
    "unit_conversion": [
        num("{n} km is how many metres?", "n*1000", {"n": [1, 9]}, {"n": 4.5},
            hints=["1 km = 1000 m, so multiply."],
            steps=["{n} x 1000 = {ans}"], difficulty=0.45, unit="m", derived={"m": "1"}),
        num("How many square centimetres are in {n} square metres?", "n*10000", {"n": [1, 5]}, {"n": 3},
            hints=["The linear factor is 100, and area is length x length."],
            steps=["1 m = 100 cm", "1 m^2 = 100 x 100 = 10000 cm^2", "{n} x 10000 = {ans}"],
            difficulty=0.6, est=110),
    ],
    "probability": [
        num("A bag has {r} red and {b} blue counters. Give the probability of drawing red as a decimal.",
            "r/(r+b)", {"r": [2, 9], "b": [1, 9]}, {"r": 3, "b": 5},
            hints=["Favourable outcomes over total outcomes."],
            steps=["{r} + {b} = {tot} counters", "{r}/{tot} = {ans}"],
            tolerance=1e-6, difficulty=0.5, derived={"tot": "r+b"}),
        mcq("A fair coin is tossed twice. What is the probability of two heads?",
            [("1/2", "That is one toss."), ("1/4", "Independent events: 1/2 x 1/2."),
             ("1/3", "There are 4 equally likely outcomes, not 3."), ("1", "That would make it certain.")], 1,
            difficulty=0.55, mode="read"),
    ],
}


def as_rows() -> list[tuple[str, dict[str, Any]]]:
    """Flatten to ``(topic_code, item_dict)`` with defaults filled in."""
    rows: list[tuple[str, dict[str, Any]]] = []
    for code, items in ITEMS.items():
        for item in items:
            item = dict(item[0] if isinstance(item, tuple) else item)
            item.setdefault("tags", [])
            item["tags"] = list(item["tags"] or []) + [code]
            item.setdefault("active", True)
            item.setdefault("source", "bank")
            rows.append((code, item))
    return rows


def topic_catalog() -> list[dict[str, Any]]:
    return [
        {
            "code": t["code"],
            "title": t["title"],
            "difficulty": t["difficulty"],
            "est_seconds": t["est"],
            "prerequisites": t["prereqs"],
            "note": TOPIC_NOTES.get(t["code"], ""),
            "item_count": len(ITEMS.get(t["code"], [])),
        }
        for t in TOPICS
    ]


def materials_for(code: str) -> list[tuple[str, str]]:
    """(title, body) learning material rows for a topic -- AI 2's teaching corpus."""
    note = TOPIC_NOTES.get(code)
    if not note:
        return []
    title = next((t["title"] for t in TOPICS if t["code"] == code), code)
    return [(f"{title} - course notes", note)]


def render_item_for(item: dict[str, Any], *, seed: int | None = None, variant: bool = True) -> dict[str, Any]:
    """Fully render a bank item for display/grading (variables substituted)."""
    if not variant:
        return {**item}
    rng = random.Random(seed)
    out = render_variant(item, rng=rng)
    if "answer_spec" in out:
        out["answer_spec"] = dict(out["answer_spec"])
    return out
