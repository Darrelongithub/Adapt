"""The authored prerequisite chains, one per topic that has content (§6.4).

A chain is a small DAG of nodes, and every node has to be **described and individually testable**:
a description a learner can read, and a task at each of rungs 1 to 5. That is not bureaucracy — a
node that cannot be tested is a node the staircase cannot stop at, and a staircase that cannot stop
somewhere either runs to its item limit or guesses.

The kits are built from one :func:`_node` call per node so that the five rungs always mean the same
thing everywhere:

* rung 1 (recognise) — pick an example of the idea from near-misses. The near-misses are authored,
  and each one is a thing a learner actually confuses the idea with, not an absurdity: an absurd
  distractor makes rung 1 free.
* rung 2 (recall) — produce the key fact in your own words, graded by a fixed rule.
* rung 3 (apply a standard case) — the case the idea is always taught with.
* rung 4 (apply in a new situation) — the same idea with the surface features changed.
* rung 5 (explain why) — three fixed checklist points, graded by the repo's own rule grader.

**What is here and what is not.** Eleven topics have chains, because those are the eleven §6.3 asks
for authored ratings for and a ladder with no chain is a ladder that cannot run. Everything else gets
``chain_for`` returning ``None`` and an honest answer from the service: the skill round still runs
(it needs only ratings), the method test runs if it has content, and the ladder says it has no
prerequisite chain for that topic rather than inventing one. Inventing a chain would be the worst
available option, because the learner would be placed in a subject the app made up on the spot.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .games import _c, _concept, _k, _t
from .ladder import MAX_DEPTH, Chain, Node

# --------------------------------------------------------------------------- #
# node authoring
# --------------------------------------------------------------------------- #


def _node(
    node_id: str,
    title: str,
    sub_area: str,
    description: str,
    *,
    prerequisites: Sequence[str] = (),
    bank_code: str | None = None,
    example: str = "",
    not_examples: Sequence[str] = (),
    example_why: str = "",
    recall_stem: str = "",
    fact_accepts: Sequence[str] = (),
    fact_why: str = "",
    standard: Mapping[str, Any] | None = None,
    transfer: Mapping[str, Any] | None = None,
    explain_stem: str = "",
    why_points: Sequence[Mapping[str, Any]] = (),
    explain_why: str = "",
) -> Node:
    """One chain node, with a full kit. Every argument except the four positional ones is content."""
    kit: dict[int, Mapping[str, Any]] = {
        1: _c(
            f"Which of these is an example of {title.lower()}?",
            [example, *not_examples],
            0,
            example_why,
        ),
        2: _t(
            recall_stem or f"Without looking it up, what is {title.lower()}? Write the key fact in your own words.",
            [tuple(fact_accepts)],
            fact_why,
            min_words=4,
        ),
        3: dict(standard or {}),
        4: dict(transfer or {}),
        5: _k(
            explain_stem or f"Explain why {title.lower()} works the way it does. Three points are marked.",
            tuple(why_points),
            explain_why,
            min_words=12,
        ),
    }
    return Node(
        id=node_id,
        title=title,
        sub_area=sub_area,
        description=description,
        prerequisites=tuple(prerequisites),
        bank_code=bank_code,
        kit=kit,
    )


# --------------------------------------------------------------------------- #
# calculus
# --------------------------------------------------------------------------- #

CALCULUS_NODES: tuple[Node, ...] = (
    _node(
        "calc_rate",
        "Rate of change",
        "rates",
        "How much one quantity changes for each unit change in another: speed is the rate at which distance changes as time passes.",
        example="how fast a car's distance from home grows as the minutes pass",
        not_examples=(
            "how far the car is from home at one moment",
            "how long the whole journey took",
            "how much fuel was in the tank before the journey",
        ),
        example_why="A rate compares two changing quantities. Each of the others is a single amount, not a comparison of changes.",
        fact_accepts=("changes for each", "per unit", "how fast one quantity changes", "change in distance over change in time", "ratio of the two changes"),
        fact_why="The key fact is that a rate is one change measured against another change.",
        standard=_c(
            "A tank holds 40 litres and loses 3 litres every minute. How much is in it after 6 minutes?",
            ("22 litres", "18 litres", "58 litres", "4 litres"),
            0,
            "Six minutes at 3 litres a minute is 18 litres lost, and 40 - 18 = 22.",
        ),
        transfer=_c(
            "A queue grows by 4 people a minute, then a counter opens and it shrinks by 6 people a minute. Which is true of the rate of change of the queue?",
            (
                "It is positive at first and negative afterwards",
                "It is positive the whole time",
                "It is negative the whole time",
                "It is zero the whole time",
            ),
            0,
            "Growing is a positive rate and shrinking a negative one, so the sign changes when the counter opens.",
        ),
        why_points=(
            _concept("two_quantities", "A rate compares a change in one quantity with a change in another", "per unit of", "for each", "change in one over change in the other", "ratio of changes"),
            _concept("the_sign", "The sign of the rate says which direction the change is going", "positive means growing", "negative means shrinking", "direction", "increasing or decreasing"),
            _concept("an_instance", "An example or a consequence of that", "for example", "such as", "so if", "which means"),
        ),
        explain_why="A rate without a second quantity is just an amount, and a rate without a sign says nothing about direction.",
    ),
    _node(
        "calc_limit",
        "Limits: what a quantity approaches",
        "limits",
        "The value a quantity gets closer and closer to as something else approaches a value — which may not be the value you get by substituting.",
        prerequisites=("calc_rate",),
        example="as x gets closer and closer to 2, x squared gets closer and closer to 4",
        not_examples=(
            "x squared is 4 when x is 2",
            "the square root of 4",
            "the largest value x squared can reach",
        ),
        example_why="A limit is about approaching, not about arriving: 'x squared is 4 when x is 2' is a value, not a limit.",
        fact_accepts=("gets closer to", "approaches", "tends to", "the value it nears", "as x approaches"),
        fact_why="The key fact is the idea of approaching a value, which may or may not be reached.",
        standard=_c(
            "As n gets larger and larger, what does 1/n get closer to?",
            ("0", "1", "n", "it grows without limit"),
            0,
            "Dividing 1 by bigger and bigger numbers gives smaller and smaller results, approaching 0 without reaching it.",
        ),
        transfer=_c(
            "A ball bounces back to half the height it fell from, every time. What does the bounce height approach?",
            (
                "0, getting smaller each bounce without ever quite arriving",
                "half the height of the first bounce, and stays there",
                "the height of the first bounce",
                "nothing: it does not approach any value",
            ),
            0,
            "Halving repeatedly approaches 0. It never lands on half the first height and stays there.",
        ),
        why_points=(
            _concept("approaching", "A limit is about getting closer, not about reaching", "closer and closer", "approaches", "without reaching", "as near as you like"),
            _concept("not_substitution", "Why substituting is not always the same as taking the limit", "0 over 0", "undefined at that point", "substituting fails", "a hole"),
            _concept("an_instance", "An example of a limit, or of a case where substitution breaks", "for example", "such as", "1 over x", "if you try"),
        ),
        explain_why="Limits exist because substitution sometimes gives an expression with no value, while the approach still has one.",
    ),
    _node(
        "calc_derivative",
        "The derivative as a gradient at a point",
        "differentiation",
        "The instantaneous rate of change of a curve at one point: the limit of the average gradient as the gap between two points shrinks to nothing.",
        prerequisites=("calc_limit",),
        example="the gradient of the curve y = x squared at one particular point on it",
        not_examples=(
            "the area between the curve and the x-axis",
            "the average gradient between two widely separated points",
            "the highest point the curve reaches",
        ),
        example_why="A derivative is at a point. An average between two far-apart points is a secant gradient, and an area is an integral.",
        fact_accepts=("gradient at a point", "instantaneous rate of change", "slope of the tangent", "rate of change at a point"),
        fact_why="The key fact is 'at a point', which is what separates a derivative from an average.",
        standard=_c(
            "What is the derivative of y = x squared with respect to x?",
            ("2x", "x squared", "2", "x / 2"),
            0,
            "The power rule brings the power down and reduces it by one: x squared differentiates to 2x.",
        ),
        transfer=_c(
            "The side of a square is growing at 2 cm per second. How fast is the area growing when the side is 5 cm?",
            (
                "20 square cm per second",
                "10 square cm per second",
                "25 square cm per second",
                "4 square cm per second",
            ),
            0,
            "Area = side squared, so dA/dt = 2 x side x d(side)/dt = 2 x 5 x 2 = 20.",
        ),
        why_points=(
            _concept("the_limit", "A derivative is a limit of average gradients as the gap closes", "limit of the average", "gap goes to zero", "as h approaches 0", "secant becomes tangent"),
            _concept("at_a_point", "Why 'at a point' matters and what goes wrong over an interval", "at one point", "not an average", "over an interval you get", "the secant instead"),
            _concept("an_instance", "An example, or what breaks if you skip the limit", "for example", "if you skip", "such as", "that is why"),
        ),
        explain_why="The limit is the whole idea: without it you have an average, and an average hides every change inside the interval.",
    ),
    _node(
        "calc_rules",
        "Rules for differentiating: chain, product and quotient",
        "rules",
        "What to do when the thing you are differentiating is a composition or a product of two functions, rather than a single term.",
        prerequisites=("calc_derivative",),
        example="differentiating (2x + 1) cubed, where one function sits inside another",
        not_examples=(
            "differentiating 3x + 5, a sum of two simple terms",
            "differentiating x squared + 4x one term at a time",
            "differentiating the constant 7",
        ),
        example_why="Sums of simple terms are done term by term. A function inside a function is what the chain rule is for.",
        fact_accepts=("chain rule", "product rule", "quotient rule", "differentiate the outside then the inside", "multiply by the derivative of the inside"),
        fact_why="The key fact is that a composition or a product has its own rule, and term-by-term does not cover it.",
        standard=_c(
            "What is the derivative of sin(3x)?",
            ("3 cos(3x)", "cos(3x)", "-3 sin(3x)", "3 sin(3x)"),
            0,
            "Chain rule: differentiate the outside (sin becomes cos) and multiply by the derivative of the inside (3).",
        ),
        transfer=_c(
            "What is the derivative of x squared multiplied by sin(x)?",
            (
                "2x sin(x) + x squared cos(x)",
                "2x cos(x)",
                "x squared cos(x)",
                "2x sin(x) - x squared cos(x)",
            ),
            0,
            "Product rule: first times the derivative of the second, plus second times the derivative of the first.",
        ),
        why_points=(
            _concept("why_a_rule", "Why the structure of the function needs its own rule", "function of a function", "two functions multiplied", "composition", "inside and outside"),
            _concept("what_breaks", "What goes wrong if you differentiate only one part of it", "you lose", "only the outside", "forgets the inside", "misses the other factor"),
            _concept("an_instance", "An example of one of the rules being used", "for example", "such as", "the chain rule on", "product rule on"),
        ),
        explain_why="Each rule exists because a structure hides a rate inside another rate, and differentiating only what you can see loses it.",
    ),
)

# --------------------------------------------------------------------------- #
# algebra
# --------------------------------------------------------------------------- #

ALGEBRA_NODES: tuple[Node, ...] = (
    _node(
        "alg_terms",
        "Terms, coefficients and like terms",
        "expressions",
        "An expression is a sum of terms; a coefficient is the number multiplying a letter; two terms are 'like' only when their letters and powers match exactly.",
        example="3a and -5a, which are like terms because the letter and its power are the same",
        not_examples=(
            "3a and 3b, which have the same coefficient but different letters",
            "x and x squared, which have the same letter but different powers",
            "7 and 2y, one of which has no letter at all",
        ),
        example_why="Like terms count the same thing, so they can be added. Same coefficient or same letter is not enough.",
        fact_accepts=("same letters to the same powers", "same variable and power", "count the same thing", "can be added together"),
        fact_why="The key fact is what makes two terms like: identical letters and identical powers.",
        standard=_c(
            "Simplify 3a + 7b - a + 2b.",
            ("2a + 9b", "4a + 9b", "2a + 5b", "11ab"),
            0,
            "3a - a = 2a and 7b + 2b = 9b. The a terms and the b terms cannot be merged with each other.",
        ),
        transfer=_c(
            "Simplify 4x squared + 3x - x squared + 5 - 2x.",
            (
                "3x squared + x + 5",
                "3x squared + 5x + 5",
                "4x squared + x + 5",
                "3x cubed + 5",
            ),
            0,
            "4x squared - x squared = 3x squared, 3x - 2x = x, and the 5 stands alone. x squared and x are not like terms.",
        ),
        why_points=(
            _concept("why_like", "Why only like terms can be combined", "count the same thing", "same unit", "you cannot add", "different letters mean different quantities"),
            _concept("the_coefficient", "What the coefficient does, including its sign", "the number in front", "including the sign", "multiplies the letter", "-5a means negative"),
            _concept("an_instance", "An example, or the error this prevents", "for example", "such as", "the mistake is", "x and x squared"),
        ),
        explain_why="Combining unlike terms is the most common algebra error, and it is prevented by knowing what a term counts.",
    ),
    _node(
        "alg_expand",
        "Expanding brackets",
        "manipulation",
        "Multiplying everything inside a bracket by whatever is outside it, so that a product becomes a sum.",
        prerequisites=("alg_terms",),
        example="2(x + 3) becoming 2x + 6, because the 2 multiplies both terms inside",
        not_examples=(
            "2x + 3, where only the first term was multiplied",
            "x + 6, where the 2 was added instead of multiplied",
            "(x + 3) squared becoming x squared + 9",
        ),
        example_why="Every term inside the bracket is multiplied by the term outside; missing one is the classic error.",
        fact_accepts=("multiply every term inside", "multiply each term", "distributive", "the outside multiplies both"),
        fact_why="The key fact is 'every term inside', not 'the first term inside'.",
        standard=_c(
            "Expand 3(2x - 5).",
            ("6x - 15", "6x - 5", "5x - 15", "6x + 15"),
            0,
            "3 x 2x = 6x and 3 x -5 = -15. The minus sign belongs to the 5 and travels with it.",
        ),
        transfer=_c(
            "Expand (x + 4)(x - 3).",
            (
                "x squared + x - 12",
                "x squared - 12",
                "x squared + 7x - 12",
                "x squared - x + 12",
            ),
            0,
            "Every term in the first bracket multiplies every term in the second: x squared - 3x + 4x - 12 = x squared + x - 12.",
        ),
        why_points=(
            _concept("distributive", "Why the outside multiplies every term inside", "each term", "distributive law", "area of a rectangle", "both parts"),
            _concept("signs", "How the signs are handled", "the minus travels", "negative times negative", "signs", "-3x"),
            _concept("an_instance", "An example, or the error this prevents", "for example", "such as", "if you only multiply", "the mistake"),
        ),
        explain_why="Expanding is the distributive law, and the sign handling is where it usually goes wrong.",
    ),
    _node(
        "alg_solve",
        "Solving a linear equation by keeping both sides balanced",
        "equations",
        "Finding the value of the unknown by doing the same thing to both sides, so the balance is never broken.",
        prerequisites=("alg_expand",),
        bank_code="linear_equations",
        example="solving 2x + 5 = 13 by taking 5 from both sides and then halving both sides",
        not_examples=(
            "moving the 5 to the other side and changing its sign without a reason",
            "guessing values of x until one works",
            "dividing only the left-hand side by 2",
        ),
        example_why="Whatever is done to one side is done to the other. 'Change the sign when you move it' is a shortcut for exactly that.",
        fact_accepts=("same thing to both sides", "keep the balance", "both sides", "undo the operations in reverse"),
        fact_why="The key fact is the balance: an equation stays true when both sides change identically.",
        standard=_c(
            "Solve 3x - 7 = 14.",
            ("x = 7", "x = 3", "x = 21", "x = -7"),
            0,
            "Add 7 to both sides to get 3x = 21, then divide both sides by 3 to get x = 7.",
        ),
        transfer=_c(
            "Solve 5(x - 2) = 3x + 4.",
            ("x = 7", "x = 3", "x = -7", "x = 14"),
            0,
            "Expand to 5x - 10 = 3x + 4, take 3x from both sides (2x - 10 = 4), add 10 (2x = 14), halve (x = 7).",
        ),
        why_points=(
            _concept("balance", "Why doing the same to both sides keeps the equation true", "both sides", "balance", "stays true", "equal stays equal"),
            _concept("reverse_order", "Why the operations are undone in reverse order", "reverse order", "undo", "last operation first", "inverse operations"),
            _concept("a_check", "How the answer can be checked", "substitute back", "put the answer back", "check by", "both sides give"),
        ),
        explain_why="Balancing explains the shortcut everybody is taught, and it is what makes checking the answer possible.",
    ),
    _node(
        "alg_rearrange",
        "Rearranging a formula to make a letter the subject",
        "formulae",
        "The same balancing, used on a formula with several letters, to isolate the one you want.",
        prerequisites=("alg_solve",),
        bank_code="rearranging",
        example="making r the subject of C = 2 pi r, by dividing both sides by 2 pi",
        not_examples=(
            "substituting numbers into C = 2 pi r to find one value of C",
            "expanding 2(pi + r), which is a different expression",
            "rounding pi to 3.14 before solving",
        ),
        example_why="Rearranging changes which letter is alone on one side; substituting only produces a number.",
        fact_accepts=("isolate the letter", "make it the subject", "get the letter on its own", "same operations to both sides"),
        fact_why="The key fact is isolating one letter using the same balance as an equation.",
        standard=_c(
            "Make b the subject of A = (a + b) h / 2.",
            (
                "b = 2A / h - a",
                "b = 2A / h + a",
                "b = A h / 2 - a",
                "b = 2A h - a",
            ),
            0,
            "Multiply both sides by 2, divide by h, then take a: b = 2A/h - a.",
        ),
        transfer=_c(
            "Make t the subject of v = u + at.",
            (
                "t = (v - u) / a",
                "t = (v + u) / a",
                "t = v / a - u",
                "t = a(v - u)",
            ),
            0,
            "Take u from both sides, then divide by a. The u must be subtracted before the division, because it is inside the same expression as at.",
        ),
        why_points=(
            _concept("same_balance", "Why the same balancing steps apply to a formula as to an equation", "both sides", "balance", "same as solving", "equal stays equal"),
            _concept("order_matters", "Why the order of undoing matters with several letters", "reverse order", "before dividing", "inside the bracket", "last operation first"),
            _concept("a_check", "How the rearrangement can be checked", "substitute", "put numbers in", "check with", "both forms give the same"),
        ),
        explain_why="A formula is an equation with letters instead of one unknown, so the reasoning is identical and the order is what trips people up.",
    ),
)

# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #

STATISTICS_NODES: tuple[Node, ...] = (
    _node(
        "stat_collect",
        "Collecting and organising data",
        "data",
        "Getting numbers that answer a question, and arranging them so a pattern can be seen: what was counted, how, and in what groups.",
        example="a tally of how many learners walked, took a bus or cycled to school, grouped by mode",
        not_examples=(
            "an opinion about which way is best to travel",
            "a single learner's story about one morning",
            "a guess at how many learners there are in the school",
        ),
        example_why="Data is counted and grouped; an opinion, a single anecdote and a guess are none of those.",
        fact_accepts=("counted", "measured", "grouped", "recorded", "how it was collected"),
        fact_why="The key fact is that data is collected by a stated method and organised into groups.",
        standard=_c(
            "A class of 40 was asked how they travel to school: 18 walk, 12 take a bus, 6 cycle and 4 come by car. Which is the modal method?",
            ("walking", "taking a bus", "cycling", "coming by car"),
            0,
            "The mode is the most frequent category, and 18 is the largest count.",
        ),
        transfer=_c(
            "The same question is asked next year, but the school has grown to 80 learners and 30 walk. Which comparison is fair?",
            (
                "Compare the proportions, not the counts, because the school is a different size",
                "Compare the counts, because 30 is bigger than 18",
                "Compare nothing: the two years cannot be compared",
                "Compare the totals, because 80 is bigger than 40",
            ),
            0,
            "30 out of 80 is 37.5% and 18 out of 40 is 45%, so the proportion fell even though the count rose.",
        ),
        why_points=(
            _concept("the_method", "Why how data is collected decides what it can show", "how it was counted", "the method", "who was asked", "biased if"),
            _concept("grouping", "Why organising into groups matters", "grouped", "categories", "so a pattern", "raw numbers"),
            _concept("size_effect", "Why counts and proportions differ when the group size changes", "proportion", "different size", "percentage", "per hundred"),
        ),
        explain_why="Every later statistic inherits the quality of the collection, so this node is where the honesty of a result is decided.",
    ),
    _node(
        "stat_average",
        "Mean, median and mode",
        "averages",
        "Three different ways of naming a typical value, each of which can be the honest one and each of which can mislead.",
        prerequisites=("stat_collect",),
        bank_code="statistical_mean",
        example="the mean of 4, 6 and 8, which is 6, because the total is shared equally between the three",
        not_examples=(
            "the largest of the three numbers",
            "the range, which is 4",
            "the total, which is 18",
        ),
        example_why="A mean shares the total equally; the largest value, the range and the total are different quantities.",
        fact_accepts=("total divided by how many", "the middle value", "the most common", "shared equally", "average"),
        fact_why="Each average has its own definition, and mixing them up is the error this rung catches.",
        standard=_c(
            "The marks in a test were 5, 7, 7, 9 and 12. What is the median?",
            ("7", "8", "7.5", "9"),
            0,
            "Ordered, the middle value of five numbers is the third one, which is 7.",
        ),
        transfer=_c(
            "Five households earn 20, 22, 25, 28 and 30 thousand shillings. A sixth household earning 500 thousand moves in. Which statement is true?",
            (
                "The mean jumps a long way and stops describing a typical household; the median barely moves",
                "Both the mean and the median jump a long way",
                "Neither changes, because one household cannot matter",
                "The median becomes larger than the mean and describes nobody",
            ),
            0,
            "The mean rises from 25 to about 104 while the median moves from 25 to 26.5, which is why the median is used for skewed data.",
        ),
        why_points=(
            _concept("definitions", "What each of the three averages actually is", "total divided by the count", "the middle value", "the most frequent"),
            _concept("outliers", "Why an extreme value affects them differently", "outlier", "skewed", "pulls the mean", "the median is resistant"),
            _concept("an_instance", "An example of an average misleading, or of choosing the right one", "for example", "such as", "income", "when the data is"),
        ),
        explain_why="'The average' is not one number, and picking the wrong one is how a true statistic misleads.",
    ),
    _node(
        "stat_spread",
        "Range and spread",
        "spread",
        "How far the values are scattered around the typical one — the part of a description an average leaves out entirely.",
        prerequisites=("stat_average",),
        example="two classes with the same mean of 60, one scoring between 55 and 65 and the other between 20 and 100",
        not_examples=(
            "the mean of the two classes, which is the same for both",
            "the number of learners in each class",
            "the highest mark in one class only",
        ),
        example_why="Spread is about the scatter of all the values, not about a central one or a single extreme.",
        fact_accepts=("how spread out", "scatter", "range is the highest minus the lowest", "how far from the average", "variability"),
        fact_why="The key fact is that spread describes scatter, and the range is the simplest measure of it.",
        standard=_c(
            "The marks were 12, 15, 18, 22 and 30. What is the range?",
            ("18", "30", "12", "19.4"),
            0,
            "Range = highest - lowest = 30 - 12 = 18. 19.4 is the mean, which is a different quantity.",
        ),
        transfer=_c(
            "Two buses take the same average time over a month: 22 minutes. Bus A's times run from 20 to 24 minutes and bus B's from 8 to 40. Which should you rely on to arrive by a fixed time?",
            (
                "Bus A, because its spread is small, so its average is something you can plan around",
                "Bus B, because its fastest trip is only 8 minutes",
                "Either, because their averages are the same",
                "Neither, because both averages are 22 minutes",
            ),
            0,
            "The average is identical; the spread is what makes one predictable and the other a gamble.",
        ),
        why_points=(
            _concept("what_spread_adds", "What spread tells you that an average does not", "an average hides", "the same mean", "scatter", "variability"),
            _concept("a_measure", "A measure of spread and what it is computed from", "highest minus lowest", "range", "difference from the average", "quartiles"),
            _concept("a_consequence", "A consequence of ignoring spread", "plan around", "misleading", "risk", "unreliable"),
        ),
        explain_why="Two data sets with the same average can describe completely different situations, and spread is the difference.",
    ),
)

# --------------------------------------------------------------------------- #
# government and civics
# --------------------------------------------------------------------------- #

GOVERNMENT_NODES: tuple[Node, ...] = (
    _node(
        "gov_power",
        "The three arms of government",
        "institutions",
        "The legislature makes the law, the executive carries it out, and the judiciary decides what it means and whether it was followed.",
        example="a court deciding that a rule made by a minister goes beyond what the law allows",
        not_examples=(
            "a newspaper reporting on a court case",
            "a citizen writing to complain about a road",
            "a political party choosing its candidate",
        ),
        example_why="A court interpreting and checking a rule is the judiciary doing its own job; the others are not arms of government.",
        fact_accepts=("legislature makes", "executive implements", "judiciary interprets", "three arms", "parliament, government and courts"),
        fact_why="The key fact is which arm does which of the three jobs.",
        standard=_c(
            "Which arm of government is responsible for debating and passing a bill?",
            ("the legislature", "the executive", "the judiciary", "the civil service"),
            0,
            "Passing laws is the legislature's job; the executive administers them and the judiciary interprets them.",
        ),
        transfer=_c(
            "A minister issues a regulation that a citizen says goes beyond the law passed by parliament. Which arm decides?",
            (
                "the judiciary, because deciding what the law means is its job",
                "the executive, because the minister issued it",
                "the legislature, because it passed the original law",
                "the political party in power",
            ),
            0,
            "The body that made the rule cannot be the one that judges whether it was lawful; that is why the arms are separate.",
        ),
        why_points=(
            _concept("the_three_jobs", "Which arm does which job", "makes the law", "carries it out", "interprets", "decides disputes"),
            _concept("why_separate", "Why the jobs are kept in different hands", "checks", "no single body", "would be able to", "accountable"),
            _concept("an_instance", "An example of one arm checking another", "for example", "a court can", "parliament can", "such as"),
        ),
        explain_why="Separation is not tidiness: it is the mechanism by which one arm can stop another.",
    ),
    _node(
        "gov_law",
        "How a bill becomes law",
        "law-making",
        "The stages a proposal goes through — introduction, debate and amendment, a vote in each house, and assent — and what each stage can change.",
        prerequisites=("gov_power",),
        example="a bill being amended in committee after members argue that one clause is unworkable",
        not_examples=(
            "a government policy announced at a press conference",
            "a court ruling that interprets an existing law",
            "a by-law passed by a single county office with no debate",
        ),
        example_why="A bill is changed by debate and amendment in a legislature; announcements, rulings and unilateral by-laws are not law-making of this kind.",
        fact_accepts=("introduced", "debated", "amended", "voted", "assent", "stages in the legislature"),
        fact_why="The key fact is that there are stages, and that each one can change the text.",
        standard=_c(
            "In most parliamentary systems, what happens to a bill between its introduction and the final vote?",
            (
                "It is debated and may be amended, usually in committee",
                "It is put to a national referendum",
                "It is signed by the head of state",
                "It is tested in the courts",
            ),
            0,
            "Debate and amendment are the middle of the process; assent comes after the votes and courts come later, if at all.",
        ),
        transfer=_c(
            "A bill passes the lower house but the upper house amends it. What must happen before it can become law?",
            (
                "The two houses must agree on one text before it goes for assent",
                "The upper house's version becomes law immediately",
                "The lower house's version becomes law immediately",
                "The bill is dropped and must start again from the beginning",
            ),
            0,
            "An amendment by one house has to be accepted by the other; neither text wins outright, and the bill is not automatically lost.",
        ),
        why_points=(
            _concept("the_stages", "What the stages are and in what order", "introduced", "debated", "voted", "assent"),
            _concept("what_changes", "Which stage can change the text and which cannot", "amended in committee", "cannot be changed after", "the vote accepts", "assent does not rewrite"),
            _concept("why_it_matters", "Why the process matters rather than the outcome alone", "scrutiny", "so that", "public and members can", "prevents"),
        ),
        explain_why="The stages are what turn a proposal into something that has been examined, and knowing which stage does what is the whole skill.",
    ),
    _node(
        "gov_rights",
        "Rights and their limits in a constitution",
        "rights",
        "The rights a constitution guarantees, and the fact that most of them can be limited by law for a stated reason rather than being absolute.",
        prerequisites=("gov_power",),
        example="freedom of movement being limited by a curfew imposed by law during an emergency",
        not_examples=(
            "a right that no law may ever limit in any circumstance",
            "a favour granted by a minister to one person",
            "a policy promise in a party manifesto",
        ),
        example_why="Most constitutional rights can be limited by law for a reason; a minister's favour and a manifesto promise are not rights.",
        fact_accepts=("guaranteed by the constitution", "can be limited by law", "not absolute", "protected", "for a stated reason"),
        fact_why="The key fact is that a right is guaranteed and usually limitable, which is not the same as being optional.",
        standard=_c(
            "Which of these is a right typically guaranteed by a constitution?",
            (
                "freedom of expression",
                "the right to a government job",
                "the right to a car",
                "the right to be exempt from tax",
            ),
            0,
            "Freedom of expression is a standard constitutional right; jobs, goods and tax exemption are not.",
        ),
        transfer=_c(
            "A law forbids publishing details of an ongoing court case. A journalist says this breaches freedom of expression. Which is the strongest constitutional argument about the law?",
            (
                "Whether the limit is set by law, has a legitimate aim, and is no more than necessary",
                "Whether the journalist agrees with the aim of the law",
                "Whether other countries have the same law",
                "Whether the law was popular when it was passed",
            ),
            0,
            "Most constitutions test a limit on those three things: lawful basis, legitimate aim and proportionality.",
        ),
        why_points=(
            _concept("guarantee", "What makes something a right rather than a privilege", "guaranteed by the constitution", "not a favour", "everyone has it", "protected by law"),
            _concept("the_limit", "Why most rights can be limited and what makes a limit acceptable", "limited by law", "legitimate aim", "proportionate", "necessary"),
            _concept("an_instance", "An example of a right and a limit on it", "for example", "such as", "curfew", "hate speech"),
        ),
        explain_why="'Rights are absolute' and 'rights are whatever the government allows' are both wrong, and the limit test is the middle.",
    ),
    _node(
        "gov_accountability",
        "How a government is held to account",
        "accountability",
        "The routes by which the people who carry out the law answer for it: elections, questions and committees in the legislature, the courts, auditors, and a free press.",
        prerequisites=("gov_law", "gov_rights"),
        example="a parliamentary committee summoning a minister to explain why a project ran over budget",
        not_examples=(
            "a minister announcing a new project on television",
            "a citizen posting an opinion about the government",
            "a ruling party meeting to choose its next leader",
        ),
        example_why="Accountability is being made to answer; an announcement, an opinion and an internal party meeting do not require anybody to answer.",
        fact_accepts=("answer for", "made to explain", "elections", "courts", "audit", "parliament questions", "press"),
        fact_why="The key fact is that accountability means there is a route by which an official must answer.",
        standard=_c(
            "Which of these is a route by which an executive is held to account?",
            (
                "questions in parliament and a report by the auditor-general",
                "a press release from the ministry",
                "a party conference speech",
                "a poster campaign",
            ),
            0,
            "Questions and audit require an answer and a record; the other three are the executive speaking for itself.",
        ),
        transfer=_c(
            "A court finds that a minister acted beyond the powers given by the law. What does this show about accountability?",
            (
                "That the judiciary can require the executive to act within the law, even though it does not run the country",
                "That the judiciary has taken over the minister's duties",
                "That the legislature must now pass the minister's decision into law",
                "That the minister is exempt from future decisions of the court",
            ),
            0,
            "Judicial review is a check, not a transfer of power: the court says what the law allows and the executive still acts.",
        ),
        why_points=(
            _concept("the_routes", "The different routes and who uses them", "elections", "parliament", "courts", "auditor", "the press"),
            _concept("why_several", "Why more than one route is needed", "no single route", "if one fails", "different times", "between elections"),
            _concept("an_instance", "An example of one route being used", "for example", "such as", "when a committee", "a court found"),
        ),
        explain_why="Elections happen every few years; accountability has to work in between them, which is why there are several routes.",
    ),
)

# --------------------------------------------------------------------------- #
# biology
# --------------------------------------------------------------------------- #

BIOLOGY_NODES: tuple[Node, ...] = (
    _node(
        "bio_cell",
        "The cell as the unit of life",
        "cells",
        "Every living thing is made of cells; a cell is the smallest unit that is alive, and its parts have distinct jobs.",
        example="a cheek cell seen under a microscope, with a nucleus and a membrane around it",
        not_examples=(
            "a grain of sand, which is not alive",
            "a strand of hair, which is dead material pushed out by living cells",
            "a drop of water",
        ),
        example_why="A cell is alive and has the parts of a living unit; sand, hair and water are not cells.",
        fact_accepts=("smallest unit of life", "all living things are made of", "basic unit", "building block of living things"),
        fact_why="The key fact is that the cell is the smallest thing that counts as alive.",
        standard=_c(
            "Which structure is found in a plant cell but not in an animal cell?",
            ("a cell wall", "a nucleus", "a cell membrane", "cytoplasm"),
            0,
            "Plant cells have a rigid cell wall outside the membrane; animal cells have a nucleus, a membrane and cytoplasm but no wall.",
        ),
        transfer=_c(
            "A single-celled organism lives in a pond. Which statement follows from it being one cell?",
            (
                "All the processes of life have to happen inside that one cell",
                "It cannot respond to its surroundings",
                "It must be a plant, because plants are simpler",
                "It has no nucleus",
            ),
            0,
            "There are no other cells to share the work, so feeding, respiration, excretion and reproduction all happen in one cell.",
        ),
        why_points=(
            _concept("unit_of_life", "Why the cell is called the unit of life", "smallest thing that is alive", "all living things", "made of cells", "basic unit"),
            _concept("parts_and_jobs", "That the parts have different jobs", "nucleus controls", "membrane controls what enters", "cytoplasm", "cell wall supports"),
            _concept("a_consequence", "A consequence of that, or an example", "for example", "which means", "so a single-celled", "that is why"),
        ),
        explain_why="Everything else in biology is built on this: tissues, organs and systems are cells doing specialised jobs.",
    ),
    _node(
        "bio_transport",
        "How materials move in and out of a cell",
        "transport",
        "Substances cross the cell membrane by diffusion, osmosis and active transport, and which one happens depends on the direction and the energy available.",
        prerequisites=("bio_cell",),
        example="oxygen moving into a cell from a place where it is more concentrated to a place where it is less concentrated",
        not_examples=(
            "a whole cell being carried along in a stream of blood",
            "food being chewed in the mouth",
            "water evaporating from a leaf surface",
        ),
        example_why="Diffusion is movement of a substance across a membrane down its concentration gradient; the others are bulk movement or processes outside a cell.",
        fact_accepts=("from high to low concentration", "down the concentration gradient", "diffusion", "osmosis", "across the membrane"),
        fact_why="The key fact is movement down a gradient across the membrane, and that going the other way needs energy.",
        standard=_c(
            "Water moves into a cell from a dilute solution outside it. What is this called?",
            ("osmosis", "digestion", "respiration", "transpiration"),
            0,
            "Osmosis is the movement of water across a partially permeable membrane from a dilute to a more concentrated solution.",
        ),
        transfer=_c(
            "A cell needs to take up a mineral ion that is already more concentrated inside the cell than outside. Which must be true?",
            (
                "It uses active transport, which needs energy, because the ion is moving against its gradient",
                "It happens by diffusion, because ions always diffuse",
                "It happens by osmosis, because water is involved",
                "It cannot happen at all",
            ),
            0,
            "Movement against a gradient cannot be passive; the cell spends energy to do it.",
        ),
        why_points=(
            _concept("the_gradient", "What decides which way a substance moves on its own", "high to low", "down the gradient", "concentration difference", "diffusion"),
            _concept("energy", "When energy is needed and why", "against the gradient", "active transport", "needs energy", "cannot happen on its own"),
            _concept("an_instance", "An example of one of the three processes", "for example", "osmosis in", "oxygen into", "root hair"),
        ),
        explain_why="The gradient explains the passive cases and explains why the others must cost the cell energy.",
    ),
    _node(
        "bio_energy",
        "How a green plant makes its own food",
        "energy",
        "Photosynthesis: light energy is captured by chlorophyll and used to make sugar from carbon dioxide and water, releasing oxygen.",
        prerequisites=("bio_cell",),
        bank_code="bio_photosynthesis",
        example="a leaf making sugar from carbon dioxide and water, using light captured by chlorophyll",
        not_examples=(
            "a root taking up water from the soil",
            "a plant releasing energy from sugar by respiration",
            "an animal digesting the plant it ate",
        ),
        example_why="Photosynthesis builds sugar using light; uptake, respiration and digestion are different processes.",
        fact_accepts=("light energy", "carbon dioxide and water", "makes sugar", "chlorophyll", "releases oxygen"),
        fact_why="The key fact is the conversion: light energy into chemical energy stored in sugar, from carbon dioxide and water.",
        standard=_c(
            "Which two substances does a plant use to make sugar in photosynthesis?",
            (
                "carbon dioxide and water",
                "oxygen and water",
                "carbon dioxide and oxygen",
                "sugar and oxygen",
            ),
            0,
            "Carbon dioxide from the air and water from the roots are the raw materials; oxygen is released, not used up.",
        ),
        transfer=_c(
            "A plant is kept in bright light with plenty of water but in air from which the carbon dioxide has been removed. What do you expect?",
            (
                "Photosynthesis stops or nearly stops, even though the light and water are there",
                "Photosynthesis continues normally, because light is the important part",
                "The plant makes sugar from water alone",
                "The plant releases more oxygen than usual",
            ),
            0,
            "Carbon dioxide supplies the carbon the sugar is built from; without it the process cannot run, however bright the light.",
        ),
        why_points=(
            _concept("the_inputs", "What goes in and what comes out", "carbon dioxide", "water", "oxygen is released", "sugar is made"),
            _concept("the_energy", "Where the energy comes from and what captures it", "light", "chlorophyll", "captured", "converted into chemical"),
            _concept("why_it_matters", "Why the process matters beyond the plant", "food for", "oxygen for", "start of the food chain", "animals depend"),
        ),
        explain_why="Photosynthesis is the entry point for energy into almost every food chain, so the inputs and the capture matter.",
    ),
    _node(
        "bio_systems",
        "How organs work together in a system",
        "systems",
        "Organs with different jobs are arranged so the output of one is the input of the next, and a failure anywhere shows up somewhere else.",
        prerequisites=("bio_transport", "bio_energy"),
        example="the digestive system: the mouth breaks food down, the stomach continues it, and the small intestine absorbs what is useful",
        not_examples=(
            "one organ doing all of the work on its own",
            "two organs that happen to be next to each other but do unrelated jobs",
            "a single cell carrying out all life processes",
        ),
        example_why="A system is a sequence of specialised organs whose jobs connect; neighbours with unrelated jobs are not a system.",
        fact_accepts=("organs work together", "each has a different job", "the output of one", "a sequence of organs", "system"),
        fact_why="The key fact is specialisation plus connection: different jobs, arranged in order.",
        standard=_c(
            "In which order does food pass through the human digestive system?",
            (
                "mouth, oesophagus, stomach, small intestine, large intestine",
                "mouth, stomach, oesophagus, small intestine, large intestine",
                "mouth, small intestine, stomach, large intestine, oesophagus",
                "oesophagus, mouth, stomach, large intestine, small intestine",
            ),
            0,
            "The oesophagus carries food from the mouth to the stomach, and absorption happens in the small intestine before water is reclaimed in the large one.",
        ),
        transfer=_c(
            "A person's pancreas stops producing enough insulin. Which statement explains why this affects the whole body rather than one organ?",
            (
                "Insulin controls how cells everywhere take up sugar from the blood, so a failure in one gland changes every tissue",
                "The pancreas is physically attached to every other organ",
                "Insulin is only used by the liver, so the effect is limited",
                "The pancreas makes the blood itself",
            ),
            0,
            "Systems are connected by what they carry: a hormone acts on cells throughout the body, so one gland's failure is a body-wide change.",
        ),
        why_points=(
            _concept("specialisation", "That each organ has a different job in the sequence", "different jobs", "specialised", "one breaks down", "another absorbs"),
            _concept("connection", "How the organs are connected to each other", "output of one", "carried by the blood", "in sequence", "depends on"),
            _concept("a_failure", "What a failure somewhere shows up as, somewhere else", "if one fails", "affects the whole", "shows up", "for example"),
        ),
        explain_why="A system is more than a list of organs: the point is that the jobs are connected, which is why one failure spreads.",
    ),
)

# --------------------------------------------------------------------------- #
# chemistry
# --------------------------------------------------------------------------- #

CHEMISTRY_NODES: tuple[Node, ...] = (
    _node(
        "chem_particle",
        "Atoms, molecules and ions",
        "particles",
        "The particles matter is made of: an atom is the smallest piece of an element, a molecule is atoms bonded together, and an ion is a particle with a charge.",
        example="a water molecule, made of two hydrogen atoms bonded to one oxygen atom",
        not_examples=(
            "a drop of water, which contains an enormous number of molecules",
            "a grain of salt you can see, which is a crystal of many ions",
            "an electron on its own, which is a part of an atom rather than an atom",
        ),
        example_why="A molecule is a specific bonded group of atoms; a visible sample is a huge collection of particles.",
        fact_accepts=("smallest particle of an element", "atoms bonded together", "a charged particle", "particles", "made of atoms"),
        fact_why="Each of the three words names a different kind of particle, and mixing them up makes every equation harder.",
        standard=_c(
            "What is the chemical formula for one molecule of water?",
            ("H2O", "CO2", "O2", "NaCl"),
            0,
            "Two hydrogen atoms bonded to one oxygen atom: H2O. The others are carbon dioxide, oxygen gas and salt.",
        ),
        transfer=_c(
            "A sodium atom loses one electron. What is left?",
            (
                "A positively charged sodium ion",
                "A negatively charged sodium ion",
                "A neutral sodium atom with a smaller mass",
                "A different element",
            ),
            0,
            "Losing a negative electron leaves a net positive charge. The number of protons is unchanged, so it is still sodium.",
        ),
        why_points=(
            _concept("the_three", "What distinguishes an atom, a molecule and an ion", "smallest piece of an element", "bonded together", "has a charge"),
            _concept("charge", "Where a charge comes from", "loses an electron", "gains an electron", "protons and electrons", "not equal"),
            _concept("an_instance", "An example of each", "for example", "such as", "water is", "sodium ion"),
        ),
        explain_why="Every reaction is particles rearranging, so the vocabulary for particles is the floor of the subject.",
    ),
    _node(
        "chem_change",
        "Physical change and chemical change",
        "changes",
        "A physical change alters the form of a substance without making a new one; a chemical change makes new substances with different properties.",
        prerequisites=("chem_particle",),
        example="iron rusting, which produces a new substance that is not iron",
        not_examples=(
            "ice melting into water, which is the same substance in a different state",
            "salt dissolving in water, which can be recovered by evaporating the water",
            "a glass breaking into pieces, which are still glass",
        ),
        example_why="Rusting makes a new substance; melting, dissolving and breaking are all reversible changes of form.",
        fact_accepts=("new substance", "chemical makes a new", "physical is reversible", "same substance different state", "cannot be undone easily"),
        fact_why="The test is whether a new substance with different properties has been made.",
        standard=_c(
            "Which of these is a chemical change?",
            (
                "wood burning to ash and smoke",
                "water boiling into steam",
                "sugar dissolving in tea",
                "a metal rod being bent",
            ),
            0,
            "Burning makes new substances and cannot be undone; the other three change form only.",
        ),
        transfer=_c(
            "A blue solution turns colourless when a second solution is added, and a solid settles at the bottom. Which conclusion is best supported?",
            (
                "A chemical change has probably happened, because a new substance has appeared",
                "It must be a physical change, because nothing was heated",
                "Nothing has changed, because the total amount of liquid is the same",
                "The solution has simply been diluted",
            ),
            0,
            "A colour change plus a new solid forming is evidence of new substances; dilution would not produce a solid.",
        ),
        why_points=(
            _concept("the_test", "How you tell the two apart", "new substance", "reversible", "different properties", "cannot be undone"),
            _concept("the_evidence", "What counts as evidence that a chemical change happened", "colour change", "gas given off", "a precipitate", "temperature change"),
            _concept("an_instance", "An example of each kind of change", "for example", "melting is", "burning is", "rusting"),
        ),
        explain_why="The distinction decides whether an equation is needed at all, so it comes before equations.",
    ),
    _node(
        "chem_equation",
        "Writing and balancing a chemical equation",
        "equations",
        "Showing a reaction with formulae, and making the number of atoms of each element the same on both sides, because atoms are neither made nor destroyed.",
        prerequisites=("chem_change",),
        example="2H2 + O2 giving 2H2O, balanced so that four hydrogen atoms and two oxygen atoms appear on each side",
        not_examples=(
            "writing the words 'hydrogen plus oxygen gives water' with no formulae",
            "a mathematical equation solving for x",
            "a list of the elements present in a sample",
        ),
        example_why="A chemical equation uses formulae and balances atoms; words alone and element lists are not equations.",
        fact_accepts=("same number of atoms", "conservation of mass", "atoms are not created or destroyed", "balance", "coefficients"),
        fact_why="The key fact is that balancing follows from atoms being conserved.",
        standard=_c(
            "Balance: __ H2 + __ O2 gives __ H2O. What are the coefficients, in order?",
            ("2, 1, 2", "1, 1, 1", "2, 2, 2", "1, 2, 1"),
            0,
            "2H2 + O2 gives 2H2O: four hydrogens and two oxygens on each side.",
        ),
        transfer=_c(
            "An equation is written as H2 + O2 giving H2O and is called balanced. What is wrong with it?",
            (
                "The right-hand side has one oxygen atom and the left has two, so atoms are not conserved",
                "The formula for water should be HO2",
                "Nothing: hydrogen and oxygen are both diatomic",
                "The equation needs a coefficient of 3 in front of the water",
            ),
            0,
            "One oxygen atom cannot appear from two. The fix is 2H2O, which then needs 2H2 on the left.",
        ),
        why_points=(
            _concept("conservation", "Why the equation has to balance", "atoms are not created", "conservation of mass", "same number on both sides", "nothing disappears"),
            _concept("coefficients", "What may be changed to balance it and what may not", "coefficients", "cannot change the formula", "subscripts stay", "numbers in front"),
            _concept("a_check", "How the balance can be checked", "count the atoms", "each element", "check both sides", "for example"),
        ),
        explain_why="Balancing is bookkeeping for a conservation law, and changing a subscript instead of a coefficient is the classic error.",
    ),
)

# --------------------------------------------------------------------------- #
# world history
# --------------------------------------------------------------------------- #

WORLD_HISTORY_NODES: tuple[Node, ...] = (
    _node(
        "wh_evidence",
        "What counts as evidence about the past",
        "evidence",
        "The material historians work from — documents, objects, buildings, records, images — and the fact that evidence survives unevenly, which shapes what can be known.",
        example="a tax record listing what a village owed in a given year",
        not_examples=(
            "a novel written last year about that village",
            "a film reconstruction with actors",
            "what a person today believes probably happened",
        ),
        example_why="A tax record is a trace left by the period itself; a novel, a reconstruction and a modern belief are not.",
        fact_accepts=("left from the time", "documents", "objects", "survives", "traces of the past", "records"),
        fact_why="The key fact is that evidence is what survived from the period, and survival is uneven.",
        standard=_c(
            "Which of these would a historian treat as primary evidence about a war in 1916?",
            (
                "a letter written by a soldier during the war",
                "a history textbook published in 2010",
                "a documentary made for television",
                "a novel about the war written in the 1960s",
            ),
            0,
            "A letter from the period is a primary source; the others were all produced later by people who were not there.",
        ),
        transfer=_c(
            "Very few written records survive from a community, but its pottery and buildings do. What follows for the historian?",
            (
                "The community can still be studied, but the questions that can be answered are the ones objects can answer",
                "Nothing can be said about the community at all",
                "The pottery can be read as if it were a document",
                "The historian should reconstruct the missing records from later accounts",
            ),
            0,
            "Survival decides what is knowable: objects answer questions about daily life and trade, not about what people wrote.",
        ),
        why_points=(
            _concept("what_counts", "What makes something evidence about a period", "from the time", "left behind", "produced then", "not written later"),
            _concept("uneven_survival", "Why the evidence is uneven and what that does", "survives unevenly", "some things decay", "who could write", "shapes what we know"),
            _concept("a_limit", "A limit this puts on a claim about the past", "cannot answer", "we do not know", "only tells us", "for example"),
        ),
        explain_why="Everything else in history rests on this: a source that is not evidence for the period cannot support a claim about it.",
    ),
    _node(
        "wh_sources",
        "Reading a source: who made it, for whom, and why",
        "sources",
        "Asking of any source who produced it, what they wanted, who they expected to read or see it, and what they had no reason to mention.",
        prerequisites=("wh_evidence",),
        bank_code="history_ww1",
        example="a government poster from 1915, made to persuade men to enlist, which says nothing about the casualties",
        not_examples=(
            "reading the poster only for the facts it states",
            "assuming that because it is old it is neutral",
            "using the poster to find out what soldiers actually experienced",
        ),
        example_why="A source is made by somebody for a purpose; treating it as a neutral record is the error this node exists to prevent.",
        fact_accepts=("who made it", "purpose", "audience", "for whom", "why it was made", "point of view"),
        fact_why="The key facts are authorship, purpose and audience — the three things that decide how far a source can be trusted.",
        standard=_c(
            "A newspaper editorial argues that school fees should be abolished two weeks before an election. What should a historian ask first?",
            (
                "Who owns the paper, and what does it gain from that outcome?",
                "Whether the fees are actually high",
                "How long the editorial is",
                "Whether the paper is printed on good quality paper",
            ),
            0,
            "The argument's interest is what determines how to read it; the other questions are beside the point.",
        ),
        transfer=_c(
            "A colonial officer's report says the local population was 'content under administration'. What is the strongest reason to treat the claim carefully?",
            (
                "The author had an interest in reporting success to the people who appointed him",
                "Reports are always false",
                "The report is too short to be believed",
                "Nobody was content anywhere in that period",
            ),
            0,
            "Interest is the reason for caution; 'always false' and 'too short' are not reasons, and the last option is a claim with no evidence.",
        ),
        why_points=(
            _concept("the_questions", "The questions to ask of any source", "who made it", "for whom", "what did they want", "when"),
            _concept("interest", "Why the author's interest changes how the source is read", "had an interest", "would want to", "not neutral", "persuade"),
            _concept("silence", "What a source does not say, and why that matters", "does not mention", "silent about", "no reason to record", "missing"),
        ),
        explain_why="A source is not a window; it is somebody's account, and reading it means reading them too.",
    ),
    _node(
        "wh_causes",
        "Causes and consequences, and how they are ranked",
        "causation",
        "Distinguishing a trigger from an underlying cause, and ranking causes rather than listing them, because an explanation is an argument about which mattered most.",
        prerequisites=("wh_evidence",),
        example="treating an assassination as the trigger of a war and the alliance system as the reason it spread across a continent",
        not_examples=(
            "listing every event that happened before the war",
            "saying the war happened because people are violent",
            "describing what happened after the war ended",
        ),
        example_why="A cause explains; a list of prior events and a general statement about human nature do not, and consequences come after.",
        fact_accepts=("trigger", "underlying cause", "which mattered most", "ranked", "long term and short term", "not just a list"),
        fact_why="The key fact is that causes are ranked and split into triggers and underlying conditions.",
        standard=_c(
            "Which is an underlying cause rather than a trigger?",
            (
                "years of competition between empires over colonies and trade",
                "the assassination of a visiting archduke",
                "the date on which a declaration was signed",
                "the weather on the day mobilisation began",
            ),
            0,
            "Competition between empires was in place before any single event; the others are moments that set it off or recorded it.",
        ),
        transfer=_c(
            "Two historians agree on every event of a revolution and disagree about what caused it. What does this show?",
            (
                "That an explanation is an argument about which cause mattered most, not a list of events",
                "That one of them must have the facts wrong",
                "That the revolution had no causes",
                "That causes cannot be discussed at all",
            ),
            0,
            "The same events support different rankings; the disagreement is about weight, and that is what an interpretation is.",
        ),
        why_points=(
            _concept("trigger_vs_underlying", "The difference between a trigger and an underlying cause", "trigger sets it off", "underlying was already there", "long term", "short term"),
            _concept("ranking", "Why causes have to be ranked rather than listed", "which mattered most", "an argument", "weight", "not just a list"),
            _concept("an_instance", "An example of a ranked explanation", "for example", "such as", "the alliance system", "the main cause"),
        ),
        explain_why="Mark schemes reward ranked explanations, and more importantly a list of causes is not yet an explanation.",
    ),
)

# --------------------------------------------------------------------------- #
# grammar
# --------------------------------------------------------------------------- #

GRAMMAR_NODES: tuple[Node, ...] = (
    _node(
        "gr_sentence",
        "What makes a complete sentence",
        "sentences",
        "A complete sentence has a subject and a finite verb and expresses a whole thought; a fragment is missing one of them.",
        example="'The bus left early' — a subject and a finite verb making a complete statement",
        not_examples=(
            "'Because the bus left early' — a dependent clause left on its own",
            "'The bus, which was full' — a subject with a description but no main verb",
            "'Left early' — a verb with no subject",
        ),
        example_why="Each of the others is missing something a sentence needs; only the first stands alone as a complete thought.",
        fact_accepts=("subject and a verb", "a complete thought", "finite verb", "stands on its own"),
        fact_why="The key fact is a subject plus a finite verb plus a complete thought.",
        standard=_c(
            "Which of these is a complete sentence?",
            (
                "The market opens at six.",
                "Because the market opens at six.",
                "The market, which opens at six.",
                "Opens at six.",
            ),
            0,
            "Only the first has a subject and a finite verb and stands alone; the others are a dependent clause, a fragment and a bare verb.",
        ),
        transfer=_c(
            "'Walking home in the rain, the shops were all closed.' What is wrong with this sentence?",
            (
                "The opening phrase has nothing to attach to: it was not the shops that were walking home",
                "Nothing; the sentence is correct",
                "It should not contain a comma",
                "It is too short to be a sentence",
            ),
            0,
            "A modifier has to sit next to the thing it describes, so 'walking home' is left describing the shops.",
        ),
        why_points=(
            _concept("the_parts", "What a complete sentence must contain", "subject", "finite verb", "complete thought", "stands alone"),
            _concept("fragments", "What makes something a fragment", "missing a verb", "dependent clause", "because", "no main clause"),
            _concept("an_instance", "An example of a fragment or a dangling modifier", "for example", "such as", "walking home", "because it"),
        ),
        explain_why="Everything above sentence level is built out of sentences, so this is where the chain starts.",
    ),
    _node(
        "gr_agreement",
        "Agreement between subject and verb",
        "agreement",
        "The verb takes the number and person of its subject, and the subject is not always the nearest noun — a phrase between them does not change it.",
        prerequisites=("gr_sentence",),
        example="'The box of old letters was left in the attic', where the verb agrees with 'box' and not with 'letters'",
        not_examples=(
            "'The box of old letters were left in the attic'",
            "'The letters was left in the attic'",
            "'The box and the letters was left in the attic'",
        ),
        example_why="The subject is 'the box'; the prepositional phrase in between does not become the subject, and a compound subject takes a plural verb.",
        fact_accepts=("verb agrees with the subject", "singular subject takes", "number and person", "not the nearest noun"),
        fact_why="The key fact is agreement with the subject, which a phrase in between can hide.",
        standard=_c(
            "Choose the correct sentence.",
            (
                "Each of the students has submitted an essay.",
                "Each of the students have submitted an essay.",
                "Each of the student has submitted an essay.",
                "Each of the students are submitting an essay.",
            ),
            0,
            "'Each' is singular, so the verb is 'has', and 'each of' takes a plural noun.",
        ),
        transfer=_c(
            "Choose the correct sentence.",
            (
                "Neither the teacher nor the students were told about the change.",
                "Neither the teacher nor the students was told about the change.",
                "Neither the teacher nor the students has been told about the change.",
                "Neither the teacher or the students were told about the change.",
            ),
            0,
            "With 'neither… nor', the verb agrees with the nearer subject, which is the plural 'students'; and the pairing is 'neither… nor', not 'or'.",
        ),
        why_points=(
            _concept("what_agrees", "What has to agree with what", "verb with the subject", "number", "person", "singular takes"),
            _concept("the_trap", "What hides the real subject", "phrase in between", "of the students", "nearest noun", "each of"),
            _concept("an_instance", "An example, including a tricky one", "for example", "neither nor", "each of", "the box of"),
        ),
        explain_why="Agreement errors survive proof-reading precisely because the intervening phrase makes the wrong verb sound right.",
    ),
    _node(
        "gr_tense",
        "Tense and aspect: when, and whether it was completed",
        "tense",
        "Tense places an event in time and aspect says whether it was ongoing or completed, which is why two past events need different forms.",
        prerequisites=("gr_sentence",),
        example="'By the time we arrived, the film had already started' — one past event completed before another",
        not_examples=(
            "'By the time we arrived, the film already started'",
            "'By the time we arrive, the film had already started'",
            "'By the time we arrived, the film was already start'",
        ),
        example_why="The earlier of two past events takes the past perfect; the others mix tenses or leave a form unfinished.",
        fact_accepts=("when it happened", "completed before", "ongoing", "past perfect", "time and whether it finished"),
        fact_why="The key fact is that tense is about time and aspect about completion, and the two together pick the form.",
        standard=_c(
            "Choose the correct sentence.",
            (
                "She has lived here since 2019.",
                "She lives here since 2019.",
                "She is living here since 2019.",
                "She did live here since 2019.",
            ),
            0,
            "'Since' with a state continuing to the present takes the present perfect.",
        ),
        transfer=_c(
            "Choose the sentence that says the writing was finished before the deadline.",
            (
                "I had written the report before the deadline.",
                "I was writing the report before the deadline.",
                "I have written the report before the deadline.",
                "I write the report before the deadline.",
            ),
            0,
            "The past perfect places the completion before another past moment; the continuous says nothing about finishing.",
        ),
        why_points=(
            _concept("tense_vs_aspect", "What tense does and what aspect does", "places it in time", "whether it was completed", "ongoing", "perfect"),
            _concept("two_events", "Why two past events need two different forms", "before another", "earlier one", "past perfect", "sequence"),
            _concept("an_instance", "An example where the wrong form changes the meaning", "for example", "had left versus left", "was writing", "changes the meaning"),
        ),
        explain_why="The form carries the order of events, so a wrong tense does not just sound wrong — it says something different.",
    ),
)

# --------------------------------------------------------------------------- #
# essay writing
# --------------------------------------------------------------------------- #

ESSAY_NODES: tuple[Node, ...] = (
    _node(
        "ew_idea",
        "A claim worth arguing",
        "claims",
        "A claim is a statement somebody could disagree with and that evidence could support — not a topic, not a fact nobody disputes, not a preference.",
        example="'A town should spend on a clinic before a stadium', which somebody could oppose and evidence could settle",
        not_examples=(
            "'Hospitals' — a topic, not a claim",
            "'Water boils at 100 C at sea level' — a fact nobody in the essay would dispute",
            "'I like stadiums' — a preference, which no evidence can settle",
        ),
        example_why="A claim has to be arguable and testable; a topic, a settled fact and a taste are none of those.",
        fact_accepts=("somebody could disagree", "arguable", "evidence could support", "a position", "not just a topic"),
        fact_why="The key fact is that a claim is something a reader could reasonably oppose.",
        standard=_c(
            "Which of these is a claim an essay could argue?",
            (
                "Schools should teach practical skills alongside academic ones",
                "Schools",
                "Most schools have classrooms",
                "I enjoy practical lessons",
            ),
            0,
            "Only the first is a position somebody could disagree with and evidence could support.",
        ),
        transfer=_c(
            "A learner's essay question is 'Discuss the effects of plastic waste.' Which claim would make the strongest essay?",
            (
                "'Banning single-use bags reduces litter more than any campaign has, and the cost falls unfairly on small shops'",
                "'Plastic waste is a problem'",
                "'There are many effects of plastic waste'",
                "'Some people like plastic and some do not'",
            ),
            0,
            "The first takes a position, can be supported with evidence and can be opposed; the others restate the question.",
        ),
        why_points=(
            _concept("arguable", "What makes a statement arguable", "somebody could disagree", "a position", "not a fact everybody accepts", "not a topic"),
            _concept("testable", "Why it has to be something evidence could settle", "evidence could", "could be supported", "could be tested", "not a taste"),
            _concept("an_instance", "An example of a claim and of a non-claim", "for example", "such as", "is only a topic", "is a preference"),
        ),
        explain_why="An essay without a claim is a description, and no amount of structure fixes that.",
    ),
    _node(
        "ew_evidence",
        "Evidence that supports a claim",
        "evidence",
        "Something specific that a reader could check — a figure, a case, a quotation, an observation — and the difference between evidence and restating the claim.",
        prerequisites=("ew_idea",),
        example="'In the year after the ban, the town counted 40 per cent fewer bags in its drains' — a specific, checkable figure",
        not_examples=(
            "'Everyone knows plastic is bad' — an appeal to agreement, not evidence",
            "'This proves the claim' — a restatement of what the claim already says",
            "'Some people disagree' — a fact about opinion, not about the question",
        ),
        example_why="Evidence is specific and checkable; agreement, restatement and reports of opinion are none of those.",
        fact_accepts=("specific", "could be checked", "a figure or a case", "supports the claim", "not just repeating it"),
        fact_why="The key fact is that evidence is specific enough that a reader could go and check it.",
        standard=_c(
            "Which of these is evidence for the claim that a clinic should come before a stadium?",
            (
                "'The nearest clinic is 30 km away and the town recorded 400 untreated cases last year'",
                "'A clinic is obviously more important'",
                "'Everybody agrees health comes first'",
                "'Stadiums are a waste of money'",
            ),
            0,
            "Only the first gives specific, checkable facts; the others are assertions or appeals to agreement.",
        ),
        transfer=_c(
            "A learner writes: 'Plastic bans work, because they are effective.' What is the problem?",
            (
                "The reason restates the claim, so no evidence has been offered",
                "The sentence is too short",
                "The claim should have been about paper bags",
                "Nothing: the sentence supports itself",
            ),
            0,
            "'Because they are effective' says the same thing as 'they work'; support has to add something the reader could check.",
        ),
        why_points=(
            _concept("what_counts", "What counts as evidence", "specific", "could be checked", "a figure", "a case", "a quotation"),
            _concept("not_evidence", "What is often offered instead and why it does not work", "restating the claim", "everybody knows", "obviously", "adds nothing"),
            _concept("the_link", "How evidence has to be connected to the claim", "shows that", "which supports", "this matters because", "link"),
        ),
        explain_why="Evidence without a link is a fact on its own, and a link without evidence is an opinion; an essay needs both.",
    ),
    _node(
        "ew_structure",
        "Paragraph structure: point, evidence, explanation",
        "structure",
        "One paragraph makes one point, backs it with something specific, and explains how that support bears on the claim.",
        prerequisites=("ew_evidence",),
        example="a paragraph that opens with its point, gives one figure, and then says what the figure shows about the claim",
        not_examples=(
            "a paragraph that lists five facts with no point and no explanation",
            "a paragraph that repeats the essay's claim three times in different words",
            "a paragraph that tells a story and never returns to the claim",
        ),
        example_why="Structure is point, then evidence, then explanation; a list, a repetition and an unreturned story are all missing a part.",
        fact_accepts=("one point per paragraph", "then evidence", "then explanation", "point evidence explanation", "how it supports"),
        fact_why="The key fact is the three moves, in that order, inside one paragraph.",
        standard=_c(
            "Which paragraph shape follows point, evidence, explanation?",
            (
                "'Small shops suffer most. In one town, 30 of 45 shops reported higher costs in the first month. That matters because the ban was justified as costless to traders.'",
                "'Plastic is bad. Plastic is harmful. Plastic should be banned.'",
                "'Here are five facts about plastic: it lasts, it floats, it breaks down slowly, animals eat it, and it clogs drains.'",
                "'I once saw a bag in a drain, and it made me think about many things.'",
            ),
            0,
            "Only the first makes a point, gives one specific piece of evidence and then explains what it shows.",
        ),
        transfer=_c(
            "A learner has a strong point and three good facts but the paragraph reads as a list. What is missing?",
            (
                "The sentence that says what the facts show about the point",
                "More facts",
                "A longer point",
                "A quotation from a book",
            ),
            0,
            "The explanation is the third move; without it the reader has to make the connection, which is the essay's job.",
        ),
        why_points=(
            _concept("the_three_moves", "What the three moves are", "point first", "then evidence", "then explanation", "one point per paragraph"),
            _concept("why_explanation", "Why the explanation cannot be left to the reader", "connects", "shows what it means", "the reader should not have to", "without it"),
            _concept("an_instance", "An example of a paragraph doing it, or failing to", "for example", "such as", "a list of facts", "reads as"),
        ),
        explain_why="Structure is what turns evidence into an argument; unstructured facts leave the reader doing the work.",
    ),
)

# --------------------------------------------------------------------------- #
# discrete maths
# --------------------------------------------------------------------------- #

DISCRETE_NODES: tuple[Node, ...] = (
    _node(
        "dm_sets",
        "Sets, membership and the operations on them",
        "sets",
        "A set is a collection of distinct objects; union takes everything in either, intersection only what is in both, and complement is everything outside.",
        example="the set of learners who take both physics and chemistry, which is the intersection of two sets",
        not_examples=(
            "the list of learners in a class written twice, which is not a set of distinct objects",
            "the number 7 on its own, which is an element rather than a collection",
            "'tall people', which has no stated criterion for membership",
        ),
        example_why="A set has distinct members and a criterion for belonging; 'both physics and chemistry' is exactly an intersection.",
        fact_accepts=("collection of distinct objects", "union is everything in either", "intersection is what is in both", "membership", "in both"),
        fact_why="The key facts are what union and intersection do, and that members are distinct.",
        standard=_c(
            "A = {1, 2, 3} and B = {3, 4}. What is the intersection of A and B?",
            ("{3}", "{1, 2, 3, 4}", "{1, 2, 4}", "{}"),
            0,
            "The intersection holds only what is in both, and 3 is the only element they share. {1,2,3,4} is the union.",
        ),
        transfer=_c(
            "In a class of 40, 25 take maths and 18 take physics, and 3 take neither. How many take both?",
            ("6", "3", "10", "43"),
            0,
            "37 take at least one, so both = 25 + 18 - 37 = 6.",
        ),
        why_points=(
            _concept("the_operations", "What union, intersection and complement each give", "union is either", "intersection is both", "complement is outside", "distinct members"),
            _concept("counting", "Why counting both sets double-counts the overlap", "counted twice", "subtract the overlap", "inclusion exclusion", "at least one"),
            _concept("an_instance", "An example using the operations", "for example", "such as", "both subjects", "the overlap is"),
        ),
        explain_why="Most set errors are counting errors, and the overlap is the thing that gets counted twice.",
    ),
    _node(
        "dm_logic",
        "Statements, negation and implication",
        "logic",
        "A statement is either true or false; its negation flips it; 'if P then Q' is false only when P is true and Q is false, and it says nothing when P is false.",
        prerequisites=("dm_sets",),
        example="'If a shape is a square then it has four equal sides', which is false only for a shape that is a square without four equal sides",
        not_examples=(
            "'Is this a square?', which is a question and not a statement",
            "'This shape is probably a square', which has no fixed truth value",
            "'Square', which names a thing rather than asserting anything",
        ),
        example_why="A statement has a truth value; a question, a hedge and a bare label do not.",
        fact_accepts=("true or false", "negation flips it", "false only when the first is true and the second false", "if then", "truth value"),
        fact_why="The key fact is when an implication is false, which is narrower than most people expect.",
        standard=_c(
            "What is the negation of 'All the learners passed'?",
            (
                "At least one learner did not pass",
                "No learner passed",
                "All the learners failed",
                "Some learners passed",
            ),
            0,
            "The negation only has to break the original claim, and one counterexample does that. 'None passed' is a stronger claim than the negation needs.",
        ),
        transfer=_c(
            "'If it rains, the pitch is closed.' The pitch is closed. What follows?",
            (
                "Nothing certain: something else could have closed it",
                "It rained",
                "It did not rain",
                "The pitch will be open tomorrow",
            ),
            0,
            "Rain is enough to close the pitch but is not the only possible reason, so a closed pitch does not prove rain.",
        ),
        why_points=(
            _concept("implication", "When 'if P then Q' is false and when it says nothing", "false only when", "P true and Q false", "says nothing when P is false", "converse is not"),
            _concept("negation", "What a negation has to do", "breaks the claim", "one counterexample", "not a stronger claim", "at least one"),
            _concept("an_instance", "An example, especially of the converse error", "for example", "such as", "the converse", "closed pitch"),
        ),
        explain_why="Implication is not symmetric, and assuming it is produces confident nonsense in proofs and in ordinary argument.",
    ),
    _node(
        "dm_counting",
        "Counting arrangements: permutations and combinations",
        "counting",
        "How many ways things can be arranged, and the distinction that decides everything: whether the order of the chosen items matters.",
        prerequisites=("dm_sets",),
        example="how many different three-letter arrangements can be made from the letters A, B and C, where ABC and CBA count as different",
        not_examples=(
            "how many letters are in the alphabet",
            "how many ways to choose a committee of three where the order does not matter",
            "how many times the letter A appears in a word",
        ),
        example_why="Permutations count ordered arrangements; a committee has no order, so it is a combination and the two answers differ.",
        fact_accepts=("order matters", "order does not matter", "permutations are ordered", "combinations are unordered", "how many ways"),
        fact_why="The key fact is the order question, which decides whether the answer is a permutation or a combination.",
        standard=_c(
            "How many ways can three runners finish first, second and third in a race of eight?",
            ("336", "56", "512", "24"),
            0,
            "8 x 7 x 6 = 336. Order matters here, so it is a permutation; 56 would be the number of unordered groups of three.",
        ),
        transfer=_c(
            "How many different committees of three can be chosen from eight people?",
            ("56", "336", "24", "512"),
            0,
            "Order does not matter in a committee, so 8 x 7 x 6 is divided by 3 x 2 x 1, giving 56.",
        ),
        why_points=(
            _concept("the_question", "The question that decides which count to use", "does order matter", "arrangement or selection", "permutation or combination", "committees"),
            _concept("the_division", "Why one of them divides by the number of orders", "each group counted", "divide by", "over-counts", "3 factorial"),
            _concept("an_instance", "An example of both on the same numbers", "for example", "race gives", "committee gives", "the same eight"),
        ),
        explain_why="The two counts use the same numbers and differ by a factor, so the order question is the whole skill.",
    ),
)

# --------------------------------------------------------------------------- #
# Kiswahili
# --------------------------------------------------------------------------- #

KISWAHILI_NODES: tuple[Node, ...] = (
    _node(
        "sw_neno",
        "Maneno na maana yake (words and what they mean)",
        "maneno",
        "Kila neno katika Kiswahili lina kazi yake katika sentensi: nomino, kitenzi, kivumishi au kielezi. Knowing which class of word you are looking at is what makes agreement possible.",
        example="'mti' — a nomino (a noun) naming a thing",
        not_examples=(
            "'kimbia' — a kitenzi (a verb), an action rather than a thing",
            "'kubwa' — a kivumishi (an adjective), a description",
            "'haraka' — a kielezi (an adverb), how something is done",
        ),
        example_why="'Mti' names a thing, so it is a nomino; the others name an action, a quality and a manner.",
        fact_accepts=("nomino", "noun", "jina la", "kitenzi", "kivumishi", "name of a person thing or place"),
        fact_why="Mtihani huu ni wa kutambua aina za maneno: knowing the classes is the floor under agreement.",
        standard=_c(
            "Ni ipi kati ya hizi ni nomino?",
            ("shule", "soma", "nzuri", "haraka"),
            0,
            "'Shule' ni jina la mahali, hivyo ni nomino. 'Soma' ni kitenzi, 'nzuri' ni kivumishi, na 'haraka' ni kielezi.",
        ),
        transfer=_c(
            "Katika sentensi 'Wanafunzi wazuri wanasoma kwa bidii', ni neno lipi ni kivumishi?",
            ("wazuri", "wanafunzi", "wanasoma", "bidii"),
            0,
            "'Wazuri' inaelezea 'wanafunzi', hivyo ni kivumishi; pia imekubaliana na ngeli ya nomino hiyo.",
        ),
        why_points=(
            _concept("aina", "The classes of word and what each does", "nomino names", "kitenzi is the action", "kivumishi describes", "kielezi tells how"),
            _concept("kubadilika", "Why the class of a word decides its form", "agreement", "prefix changes", "in the same class", "kubaliana"),
            _concept("mfano", "An example from a sentence", "for example", "kwa mfano", "katika sentensi", "ndiyo maana"),
        ),
        explain_why="Aina za maneno ndizo msingi wa ngeli na kubadilika kwa vitenzi, so this node comes first.",
    ),
    _node(
        "sw_ngeli",
        "Ngeli: noun classes and the agreement they force",
        "ngeli",
        "Kiswahili groups nouns into classes, and every word that describes a noun — its adjective, its possessive, its verb prefix — has to agree with that class.",
        prerequisites=("sw_neno",),
        example="'kitabu hiki ni kipya' — 'kitabu' (ki-/vi- class) makes 'hiki' and 'kipya' take ki- and vi- forms",
        not_examples=(
            "'kitabu hii ni mpya', where the agreement follows a different class",
            "'vitabu hiki ni kipya', where the noun is plural and the rest singular",
            "'kitabu ni new', which is not Kiswahili agreement at all",
        ),
        example_why="Agreement is forced by the noun's class: 'kitabu' takes 'hiki' and 'kipya', and 'vitabu' takes 'hivi' and 'vipya'.",
        fact_accepts=("agreement", "concord", "the class decides", "prefix", "viambishi", "words agree with the noun"),
        fact_why="The key fact is that the noun's class controls the forms around it.",
        standard=_c(
            "Chagua sentensi sahihi.",
            ("Vitabu hivi ni vipya.", "Vitabu hii ni mpya.", "Vitabu ule ni mpya.", "Vitabu hilo ni kipya."),
            0,
            "'Vitabu' iko katika ngeli ya vi-, hivyo kivumishi na kiashiria lazima vichukue 'hivi' na 'vipya' — hapa 'hivi' na 'vipya'.",
        ),
        transfer=_c(
            "Chagua sentensi sahihi ya kumiliki: 'the teachers' books'.",
            ("Vitabu vya walimu", "Vitabu wa walimu", "Vitabu cha walimu", "Vitabu ya walimu"),
            0,
            "Kiambishi cha kumiliki kinakubaliana na ngeli ya 'vitabu' (vi-), hivyo ni 'vya'.",
        ),
        why_points=(
            _concept("ngeli", "What a noun class is and what it decides", "class of the noun", "decides the prefixes", "agreement", "ngeli huamua"),
            _concept("nini_hubadilika", "Which words have to change to agree", "adjective", "possessive", "verb prefix", "demonstrative"),
            _concept("kwa_nini", "Why the system is useful rather than arbitrary", "tells you which noun", "removes doubt", "so the listener knows", "kwa mfano"),
        ),
        explain_why="Ngeli ndiyo inayoamua umbo la maneno mengine yote yanayoelezea nomino, so agreement is the skill and not a detail.",
    ),
    _node(
        "sw_vitenzi",
        "Vitenzi: the markers inside a verb",
        "vitenzi",
        "A Kiswahili verb is built in a fixed order: subject marker, then tense marker, then the object marker if there is one, then the verb root, then any extensions and the final vowel.",
        prerequisites=("sw_neno",),
        example="'anasoma' — 'a-' is the subject (he/she), '-na-' is the present continuous, and '-soma' is the root",
        not_examples=(
            "'soma' on its own, which is the root with no subject or tense",
            "'kitabu', which is a noun and not a verb at all",
            "'haraka', which is an adverb telling how, not who or when",
        ),
        example_why="A full verb carries its subject and its tense inside itself, which is what the markers are for.",
        fact_accepts=("subject marker", "tense marker", "verb root", "order of the markers", "who and when"),
        fact_why="The key fact is the order: subject, tense, (object), root.",
        standard=_c(
            "Katika 'watasoma', ni kipi kiambishi cha wakati ujao?",
            ("-ta-", "wa-", "-som-", "-a"),
            0,
            "'wa-' ni kiambishi cha mada (wao), '-ta-' ni cha wakati ujao, na '-soma' ni mzizi.",
        ),
        transfer=_c(
            "Tafsiri: 'they will read it' (kitabu).",
            ("Watakisoma", "Watasoma", "Anakisoma", "Watakisome"),
            0,
            "Mpangilio ni mada (wa-), wakati (-ta-), kinyume (ki- kwa kitabu), mzizi (-som-), na kishia (-a).",
        ),
        why_points=(
            _concept("mpangilio", "The order of the markers", "subject first", "then tense", "then object", "then the root"),
            _concept("kazi_yake", "What each marker contributes", "tells who", "tells when", "tells what", "the root carries the meaning"),
            _concept("kosa", "What goes wrong if the order is broken", "changes the meaning", "wrong subject", "unreadable", "kwa mfano"),
        ),
        explain_why="Mpangilio wa viambishi hubeba maana yote ya nani, lini na kitu gani, so the order is the grammar.",
    ),
)

# --------------------------------------------------------------------------- #
# the chains
# --------------------------------------------------------------------------- #

CHAINS: dict[str, Chain] = {}


def _chain(chain_id: str, title: str, nodes: Sequence[Node], *, note: str = "") -> Chain:
    chain = Chain(chain_id, title, nodes, topic_key=chain_id, source="authored", note=note)
    CHAINS[chain_id] = chain
    return chain


_chain("calculus", "Calculus: from rates to rules", CALCULUS_NODES)
_chain("algebra", "Algebra: from terms to formulae", ALGEBRA_NODES)
_chain("statistics", "Statistics: from data to spread", STATISTICS_NODES)
_chain("government", "Government: from institutions to accountability", GOVERNMENT_NODES)
_chain("biology", "Biology: from cells to systems", BIOLOGY_NODES)
_chain("chemistry", "Chemistry: from particles to equations", CHEMISTRY_NODES)
_chain("world_history", "World history: from evidence to causes", WORLD_HISTORY_NODES)
_chain("grammar", "Grammar: from sentences to tense", GRAMMAR_NODES)
_chain("essay_writing", "Essay writing: from a claim to a paragraph", ESSAY_NODES)
_chain("discrete_maths", "Discrete maths: from sets to counting", DISCRETE_NODES)
_chain("kiswahili", "Kiswahili: from words to verb markers", KISWAHILI_NODES)


def chain_for_key(key: str) -> Chain | None:
    """The authored chain for a topic key, or ``None`` when there is not one."""
    return CHAINS.get(key)


def chain_for(resolved: Any) -> Chain | None:
    """The chain a resolved topic gets: the authored one for its key, else ``None``.

    ``None`` is an answer, not an error. The service reports it as "we have no prerequisite chain for
    this topic yet", which is honest in a way that a chain invented on the spot would not be.
    """
    key = str(getattr(resolved, "topic_key", "") or (resolved.get("topic_key") if isinstance(resolved, Mapping) else "") or "")
    return CHAINS.get(key)


def chains_payload() -> list[dict[str, Any]]:
    return [c.as_dict() for c in CHAINS.values()]


def validate_chains() -> list[str]:
    """Every chain checked against §6.4's rules. Called by the test suite at import time."""
    problems: list[str] = []
    for chain in CHAINS.values():
        for problem in chain.validate():
            problems.append(f"{chain.chain_id}: {problem}")
        if not chain.depth_ok():
            problems.append(f"{chain.chain_id}: depth {chain.depth} exceeds the maximum of {MAX_DEPTH}")
        if len(chain.sub_areas()) < 3:
            problems.append(
                f"{chain.chain_id}: {len(chain.sub_areas())} sub-area(s); the ladder rotates across 3-4, "
                "so a chain with fewer cannot rotate"
            )
    return problems


__all__ = [
    "CHAINS",
    "Node",
    "chain_for",
    "chain_for_key",
    "chains_payload",
    "validate_chains",
]
