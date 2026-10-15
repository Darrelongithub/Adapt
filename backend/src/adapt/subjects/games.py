"""The subject skill games: authored content and deterministic scoring (round brief §6.2).

Every skill in the taxonomy has one fast game. This module holds both halves of it — the authored
material and the code that judges an answer — and they are kept together on purpose, because a scorer
that does not know what its own item is asking is how a bank ends up with items whose "wrong"
answers are also right (this repo has a test named after exactly that failure).

**Tier 2 measures the subject, not the topic.** §6.1 is careful about this: the skill round is "the
core skills of the whole subject, valid for any branch". So a calculus learner and an algebra learner
play the *same* symbol-fluency game; what differs is which skills the round chose (§6.3) and what the
ladder does afterwards. That is why the pools below are written per skill and not per topic, and it
is why the answer to "why did these two learners get different tests" is a reason about leanings and
never a reason about content.

Three rules the code enforces:

1. **Answers never reach the browser.** :func:`public_task` strips the key before a task is sent; the
   server keeps the full task on the trial row and re-derives it from the run seed when a learner
   reloads. (The mind baseline sends its answers to the client. That is a defect there, and it is not
   repeated here.)
2. **Code marks everything.** No scorer calls a model. The checklist-graded skills use the rule grader
   in :mod:`adapt.assessment.teachback` — three fixed points, evidence must be a quote of the
   learner's own words, one span pays for one point — which is the same grader the teach-backs use, so
   "explain it" means the same thing in both places.
3. **A skipped item is not a wrong item.** Leaving an answer blank returns ``correct=None`` with
   ``status="skipped"``, which the service records as *not measured*. Counting a blank as a miss is
   how a learner who ran out of time ends up with a lower estimate than a learner who never started.

The generic skills are the exception worth explaining. They are used when a topic could not be placed
in a subject, so their content cannot be about that topic without inventing it; they measure the
universal mechanics with neutral material instead, and every task says so. ``gen.recall`` even opens
with an unscored "read this" card, because a recall measurement that never showed anything is
measuring prior knowledge and calling it recall.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..assessment import teachback as TB
from ..learning.seeds import stable_seed
from .taxonomy import ITEMS_PER_SKILL, require_skill

# --------------------------------------------------------------------------- #
# authored item constructors
# --------------------------------------------------------------------------- #


def _c(stem: str, options: Sequence[str], answer: int, why: str, **extra: Any) -> dict[str, Any]:
    """A single-answer choice item. ``answer`` indexes ``options`` as authored; it is shuffled at
    generation time, so the authored position is not the served position."""
    assert 0 <= answer < len(options), f"a choice item points outside its own options: {stem[:40]}"
    return {"kind": "choice", "stem": stem, "options": list(options), "answer": int(answer), "why": why, **extra}


def _o(stem: str, steps: Sequence[str], why: str, **extra: Any) -> dict[str, Any]:
    """An ordering item. ``steps`` is authored in the correct order."""
    return {"kind": "order", "stem": stem, "steps": list(steps), "why": why, **extra}


def _n(stem: str, answer: float, why: str, *, tolerance: float = 0.001, unit: str = "", **extra: Any) -> dict[str, Any]:
    return {"kind": "numeric", "stem": stem, "answer": float(answer), "tolerance": float(tolerance), "unit": unit, "why": why, **extra}


def _t(stem: str, required: Sequence[Sequence[str]], why: str, *, banned: Sequence[str] = (), min_words: int = 5, **extra: Any) -> dict[str, Any]:
    """A short-answer item graded by rule: every group must be answered by one of its phrasings."""
    return {
        "kind": "text",
        "stem": stem,
        "required_groups": [list(g) for g in required],
        "banned": list(banned),
        "min_words": int(min_words),
        "why": why,
        **extra,
    }


def _k(stem: str, points: Sequence[Mapping[str, Any]], why: str, *, min_words: int = 10, **extra: Any) -> dict[str, Any]:
    """A checklist item: three fixed points, graded by ``assessment.teachback``."""
    assert len(points) <= TB.CHECKLIST_SIZE, "a checklist is three points, never more"
    return {"kind": "text", "grader": "checklist", "stem": stem, "rubric": {"required_concepts": [dict(p) for p in points]}, "min_words": int(min_words), "why": why, **extra}


def _m(stem: str, left: Sequence[str], right: Sequence[str], pairs: Sequence[tuple[int, int]], why: str, **extra: Any) -> dict[str, Any]:
    """A matching item. ``pairs`` maps an index in ``left`` to an index in ``right``."""
    return {"kind": "match", "stem": stem, "left": list(left), "right": list(right), "pairs": [tuple(p) for p in pairs], "why": why, **extra}


def _concept(name: str, statement: str, idea: str, *synonyms: str) -> dict[str, Any]:
    """One checklist point, in the shape ``teachback.checklist_from_rubric`` reads."""
    return {"concept": name, "statement": statement, "idea": idea, "synonyms": list(synonyms)}


# --------------------------------------------------------------------------- #
# maths
# --------------------------------------------------------------------------- #

MATHS_POOLS: dict[str, tuple[dict[str, Any], ...]] = {
    "maths.number_sense": (
        _c(
            "A shop sells 48 pens at 19 shillings each. About how much is that, without working it out exactly?",
            ("about 900 shillings", "about 600 shillings", "about 1,900 shillings", "about 4,800 shillings"),
            0,
            "48 x 19 = 912, so about 900. Estimating 50 x 20 = 1,000 and adjusting down lands in the same place.",
        ),
        _c(
            "Which is closest to 62 x 38?",
            ("2,400", "1,800", "3,000", "620"),
            0,
            "62 x 38 = 2,356. 60 x 40 = 2,400 is the estimate that gets you there.",
        ),
        _c(
            "A tank holds 2,000 litres. About how many 3-litre jugs does it fill?",
            ("about 670", "about 150", "about 6,000", "about 200"),
            0,
            "2,000 / 3 = 666.6..., so about 670 jugs.",
        ),
        _c(
            "Which is closest to 1/3 + 1/4?",
            ("0.58", "0.25", "0.75", "0.07"),
            0,
            "1/3 + 1/4 = 7/12 = 0.583. Adding the tops and the bottoms (1+1 / 3+4 = 2/7 = 0.29) is the common error.",
        ),
        _c(
            "A bus travels 297 km in 4 hours. About what is its average speed?",
            ("about 75 km/h", "about 40 km/h", "about 150 km/h", "about 300 km/h"),
            0,
            "297 / 4 = 74.25, so about 75 km/h. 300 / 4 = 75 is the quick estimate.",
        ),
    ),
    "maths.symbol_fluency": (
        _c(
            "Are 2(x + 3) and 2x + 6 the same for every value of x?",
            (
                "Yes: multiplying both terms inside the bracket gives 2x + 6",
                "No: the second is larger whenever x is negative",
                "They are equal only when x = 3",
                "They are equal only when x is a whole number",
            ),
            0,
            "2(x + 3) = 2x + 6 by the distributive law, for every x including negatives and fractions.",
        ),
        _c(
            "Are x squared and 2x the same for every value of x?",
            (
                "No: they are equal only when x is 0 or 2",
                "Yes: both mean x multiplied by itself",
                "No: x squared is always the larger of the two",
                "No: they are never equal for any value of x",
            ),
            0,
            "x squared = 2x gives x(x - 2) = 0, so x = 0 or x = 2. At x = 1 the first is 1 and the second is 2; at x = 3 the first is 9 and the second is 6.",
        ),
        _c(
            "Are -(a - b) and b - a the same for every a and b?",
            (
                "Yes: taking the minus through the bracket reverses both signs",
                "No: the first is always negative and the second positive",
                "They are equal only when a is bigger than b",
                "They are equal only when b is zero",
            ),
            0,
            "-(a - b) = -a + b = b - a, whatever the signs of a and b.",
        ),
        _c(
            "Are (x + 3) squared and x squared + 9 the same for every value of x?",
            (
                "No: the second is missing the 6x that comes from multiplying the two brackets",
                "Yes: squaring each term inside the bracket is enough",
                "Yes, but only when x is positive",
                "No: the first is always smaller",
            ),
            0,
            "(x + 3)(x + 3) = x squared + 6x + 9. At x = 1 the first is 16 and the second is 10.",
        ),
    ),
    "maths.translate_words": (
        _m(
            "Match each sentence to the expression that says the same thing.",
            (
                "Three more than twice a number n",
                "Twice the sum of n and three",
                "Three times n, then take away two",
                "Two divided by n, then add three",
            ),
            ("2n + 3", "2(n + 3)", "3n - 2", "(2 / n) + 3"),
            ((0, 0), (1, 1), (2, 2), (3, 3)),
            "'Twice the sum' needs a bracket: 2(n + 3) is not 2n + 3.",
        ),
        _m(
            "Match each sentence to the expression that says the same thing.",
            (
                "Half of a number n",
                "A number n halved, then five added",
                "Five times n, all of it halved",
                "The square of n, plus one",
            ),
            ("n / 2", "(n / 2) + 5", "5n / 2", "n squared + 1"),
            ((0, 0), (1, 1), (2, 2), (3, 3)),
            "'Five times n, all halved' brackets the whole product: 5n / 2, not 5 + n / 2.",
        ),
        _m(
            "Mangoes cost 30 shillings each and oranges cost 20 shillings each. Match the words to the expression.",
            (
                "The cost of m mangoes",
                "The change from 200 shillings after buying m mangoes",
                "The cost of m mangoes and three oranges",
                "The price of one mango if m mangoes cost 300 shillings",
            ),
            ("30m", "200 - 30m", "30m + 60", "300 / m"),
            ((0, 0), (1, 1), (2, 2), (3, 3)),
            "Three oranges at 20 is 60, so the third expression is 30m + 60.",
        ),
        _m(
            "Match each sentence about a quantity x to its expression.",
            (
                "x increased by ten per cent",
                "ten per cent of x",
                "x after a ten per cent decrease",
                "the ratio of x to ten",
            ),
            ("1.1x", "0.1x", "0.9x", "x / 10"),
            ((0, 0), (1, 1), (2, 2), (3, 3)),
            "A ten per cent rise multiplies by 1.1; a ten per cent fall multiplies by 0.9.",
        ),
    ),
    "maths.logic_chains": (
        _c(
            "Every square is a rectangle. Every rectangle has four right angles. Which must be true?",
            (
                "Every square has four right angles",
                "Every shape with four right angles is a square",
                "Some squares have no right angles",
                "Only some rectangles have four right angles",
            ),
            0,
            "The chain runs one way: squares are inside rectangles, and rectangles all have four right angles. The reverse claim is not supported.",
        ),
        _c(
            "If it rains, the pitch is closed. Today the pitch is open. What follows?",
            (
                "It did not rain",
                "It rained, but the pitch was left open",
                "Nothing can be concluded about the weather",
                "If it rains tomorrow, the pitch will be open",
            ),
            0,
            "Rain is enough to close the pitch, so an open pitch means it did not rain. This is the one direction that is safe.",
        ),
        _c(
            "All the players in squad A are also in squad B. Some players in squad B are captains. Which must be true?",
            (
                "Nothing here guarantees that a player in squad A is a captain",
                "Some players in squad A are captains",
                "Every captain is in squad A",
                "No player in squad A is a captain",
            ),
            0,
            "The captains could all be the squad-B players who are not in squad A. 'Some B are captains' says nothing about the A part of B.",
        ),
        _c(
            "We know that if x is greater than 3 then x squared is greater than 9. We are told x squared is greater than 9. What follows?",
            (
                "Either x is greater than 3 or x is less than -3",
                "x is greater than 3",
                "x is less than -3",
                "x is exactly 3",
            ),
            0,
            "x squared > 9 has two branches. Assuming the first one (x > 3) is the classic error of reversing an implication.",
        ),
    ),
    "maths.pattern_structure": (
        _n("A machine does this: 2 gives 7, 4 gives 13, 7 gives 22. What does 10 give?", 31, "The rule is 3n + 1: 3(10) + 1 = 31.", tolerance=0.5),
        _n("The sequence is 3, 7, 15, 31, ... What comes next?", 63, "Each term is one less than a power of two, or double the previous term plus one: 2(31) + 1 = 63.", tolerance=0.5),
        _n("A machine does this: 0 gives 5, 1 gives 5, 2 gives 5, 4 gives 5. What does 6 give?", 5, "The rule is constant. Looking for a slope where there is none is the error this item is built to catch.", tolerance=0.5),
        _n("The terms are 1, 4, 9, 16, 25, ... What is the eighth term?", 64, "The nth term is n squared, so the eighth is 64.", tolerance=0.5),
    ),
    "maths.graph_sense": (
        _c(
            "A bus leaves town at a steady speed, waits ten minutes at a stage, then continues faster than before. Which describes its distance-from-town against time?",
            (
                "A straight line, then a flat section, then a straight line that is steeper",
                "A single straight line the whole way",
                "A flat section, then a straight line, then a flat section",
                "A curve that gets steeper all the way, with no flat part",
            ),
            0,
            "Steady speed is a straight line; waiting is flat (distance does not change); a faster steady speed is a steeper straight line.",
        ),
        _c(
            "A cup of hot tea is left in a room. Which describes its temperature against time?",
            (
                "It falls quickly at first, then more and more slowly, settling near the room temperature",
                "It falls at exactly the same rate until it reaches zero",
                "It stays flat for a while and then drops all at once",
                "It falls slowly at first and then faster and faster until it reaches zero",
            ),
            0,
            "Cooling is fastest when the gap to the room is biggest, and it never goes below the room temperature on its own.",
        ),
        _c(
            "A car accelerates steadily from rest, holds one speed, then brakes steadily to a stop. Which describes its speed against time?",
            (
                "A rising straight line, a flat line, then a falling straight line back to zero",
                "A flat line, then a rising straight line, then a flat line",
                "A rising curve that never becomes flat",
                "A straight line rising from zero to a maximum and stopping there",
            ),
            0,
            "Steady acceleration is a straight rise; a held speed is flat; steady braking is a straight fall to zero.",
        ),
        _c(
            "Water is poured at a constant rate into a container that is narrow at the bottom and wide at the top. Which describes the depth of the water against time?",
            (
                "It rises quickly at first and then more slowly as the container widens",
                "It rises at a constant rate the whole way",
                "It rises slowly at first and then faster and faster",
                "It stays flat and then jumps when the container is full",
            ),
            0,
            "The same volume per second spreads over a wider cross-section higher up, so the depth climbs more slowly.",
        ),
    ),
    "maths.checking": (
        _c(
            "Solve 2(x + 4) = 18.  Step 1: 2x + 4 = 18.  Step 2: 2x = 14.  Step 3: x = 7.  Which step is wrong?",
            ("Step 1", "Step 2", "Step 3", "None: the working is correct"),
            0,
            "The bracket must be fully multiplied: 2(x + 4) = 2x + 8. Steps 2 and 3 follow correctly from the wrong line, which is why the wrong answer looks tidy.",
        ),
        _c(
            "Estimate 49 x 31.  Step 1: 50 x 30 = 1,500.  Step 2: so the answer is about 150.  Step 3: the exact answer will be close to 1,500.  Which step is wrong?",
            ("Step 2", "Step 1", "Step 3", "None: the working is correct"),
            0,
            "Step 2 drops a factor of ten. 49 x 31 = 1,519, so step 3 is right and step 2 is not.",
        ),
        _c(
            "Simplify (x squared - 9) / (x - 3).  Step 1: x squared - 9 = (x - 3)(x + 3).  Step 2: cancel (x - 3).  Step 3: so the answer is x + 3 for every value of x.  Which step is wrong?",
            ("Step 3", "Step 1", "Step 2", "None: the working is correct"),
            0,
            "The cancellation is fine, but at x = 3 the original expression divides by zero, so 'for every value of x' is the wrong claim.",
        ),
        _c(
            "Find the mean of 4, 8 and 6.  Step 1: the sum is 18.  Step 2: there are three numbers.  Step 3: the mean is 18 x 3 = 54.  Which step is wrong?",
            ("Step 3", "Step 1", "Step 2", "None: the working is correct"),
            0,
            "A mean divides the sum by the count: 18 / 3 = 6, not 18 x 3.",
        ),
    ),
}

# --------------------------------------------------------------------------- #
# languages
# --------------------------------------------------------------------------- #

LANGUAGES_POOLS: dict[str, tuple[dict[str, Any], ...]] = {
    "lang.vocabulary_context": (
        _c(
            "The farmer put the harvested maize in the *kisera* so that the rain could not reach it and the rats could not chew it. What does *kisera* mean?",
            ("a covered store", "a heavy rain", "a digging tool", "a kind of rat"),
            0,
            "The sentence rules out rain, a tool and a rat: the thing keeps both away from the maize, so it is a covered store.",
        ),
        _c(
            "She answered in a *melova* voice, so everybody in the back row heard her clearly even though she was not shouting. What does *melova* mean?",
            ("carrying without force", "very quiet", "angry", "cracking and uneven"),
            0,
            "Heard clearly at the back, without shouting: the voice carries. 'Very quiet' contradicts the sentence.",
        ),
        _c(
            "The path was *tirrin*, so after the rain nobody could cross it without sinking to the ankle. What does *tirrin* mean?",
            ("soft and wet", "steep", "narrow", "newly paved"),
            0,
            "Sinking to the ankle after rain means soft and wet. Steep or narrow paths do not swallow ankles.",
        ),
        _c(
            "He gave a *nurobo* answer: exactly what was asked, in as few words as possible, and nothing else. What does *nurobo* mean?",
            ("short and to the point", "confusing", "rude", "long and detailed"),
            0,
            "'As few words as possible' is the definition the sentence gives; 'long and detailed' is its opposite.",
        ),
    ),
    "lang.grammar_sense": (
        _c(
            "Which sentence is grammatically correct?",
            (
                "Each of the students has submitted an essay.",
                "Each of the students have submitted an essay.",
                "Each of the student has submitted an essay.",
                "Each of the students are submitting an essay.",
            ),
            0,
            "'Each' is singular, so the verb is 'has'; and 'each of' is followed by a plural noun, 'the students'.",
        ),
        _c(
            "By the time we arrived, the film ___ .",
            ("had already started", "already started", "has already started", "was already start"),
            0,
            "One past event completed before another past event takes the past perfect.",
        ),
        _c(
            "Which sentence is grammatically correct?",
            (
                "Walking to school, I felt the rain begin to fall.",
                "Walking to school, the rain began to fall.",
                "The rain began to fall walking to school.",
                "Walking to school the rain it began to fall.",
            ),
            0,
            "In the second sentence the rain is doing the walking: a modifier has to sit next to the thing it describes.",
        ),
        _c(
            "The box of old letters ___ in the attic for years.",
            ("was left", "were left", "have been left", "are left"),
            0,
            "The subject is 'the box', not 'letters', so the verb is singular.",
        ),
    ),
    # Each passage carries one question that is stated and one that has to be inferred; the generator
    # serves one of each, because a comprehension score built from two literal questions measures
    # reading speed and calls it comprehension.
    "lang.comprehension": (
        {
            "kind": "passage",
            "passage": (
                "Amina leaves home at six. The first matatu is full, so she waits for the next one and "
                "reaches school after the bell. The teacher says nothing. Amina sits at the back, copies "
                "the notes from the board, and does not ask about the question she got wrong yesterday."
            ),
            "literal": _c(
                "Why did Amina reach school after the bell?",
                ("The first matatu was full, so she waited for another", "She left home later than usual", "The teacher kept her outside", "She missed every matatu"),
                0,
                "The passage states the reason: the first matatu was full and she waited for the next one.",
            ),
            "inference": _c(
                "What does the passage suggest about Amina?",
                (
                    "She is unwilling to draw attention to herself",
                    "She does not care about her schoolwork",
                    "Her teacher is angry with her",
                    "She enjoys sitting at the back of the class",
                ),
                0,
                "Copying the notes and not asking about yesterday's mistake both point the same way; nothing in the passage says she does not care.",
            ),
            "why": "One answer is stated and one has to be inferred from what she does and does not do.",
        },
        {
            "kind": "passage",
            "passage": (
                "The committee met for three hours and agreed on nothing. Afterwards the chair wrote a "
                "one-page summary that listed four decisions. Three of the four had been proposed by the "
                "chair and voted down."
            ),
            "literal": _c(
                "How many of the four decisions in the summary had been voted down?",
                ("Three", "Four", "One", "None"),
                0,
                "The passage says three of the four had been proposed by the chair and voted down.",
            ),
            "inference": _c(
                "What does the passage suggest about the summary?",
                (
                    "It does not faithfully report what the committee agreed",
                    "It is a fair record of a long meeting",
                    "The committee changed its mind after the meeting",
                    "The chair was not present at the meeting",
                ),
                0,
                "Decisions that were voted down cannot be decisions of the committee, so the summary misreports the meeting.",
            ),
            "why": "The inference is about the gap between the meeting and the record of it.",
        },
        {
            "kind": "passage",
            "passage": (
                "Juma practised the same ten sums every evening for a month. In the first week he needed "
                "about nine minutes. By the fourth week he needed four, and he made fewer careless slips, "
                "though he still stopped at the two questions involving fractions."
            ),
            "literal": _c(
                "How long did the ten sums take Juma in the fourth week?",
                ("About four minutes", "About nine minutes", "About a month", "About ten minutes"),
                0,
                "The passage gives both times and says which week each belongs to.",
            ),
            "inference": _c(
                "What does the passage suggest about fractions?",
                (
                    "They are the part Juma has not yet practised to confidence",
                    "Juma dislikes practising in the evening",
                    "Juma is slower at every kind of sum",
                    "The ten sums contained no fractions",
                ),
                0,
                "He still stops at the two fraction questions while everything else has got faster, which points at that one part.",
            ),
            "why": "Speed improved overall, so the remaining hesitation is specific.",
        },
    ),
    "lang.flexibility": (
        _t(
            "Say this in your own words, without using the phrase 'because of the heat': The metal rail expanded because of the heat.",
            (("got longer", "grew longer", "expanded", "became bigger", "got bigger"), ("hot", "warm", "temperature", "heated")),
            "The restatement has to carry both halves — the change and its cause — without borrowing the sentence's own wording.",
            banned=("because of the heat",),
            min_words=6,
        ),
        _t(
            "Say this in your own words, without using the word 'increase': Demand for maize went up after the price of rice rose.",
            (("more people wanted", "more buyers", "wanted more", "demand rose", "demand went up", "bought more"), ("rice", "price")),
            "A restatement that drops the cause is a summary of half the sentence.",
            banned=("increase",),
            min_words=6,
        ),
        _t(
            "Say this in your own words, without using the word 'so': The road was flooded, so the bus turned back.",
            (("went back", "turned back", "returned", "did not continue"), ("flood", "flooded", "water")),
            "The link has to survive without the word that carried it.",
            banned=(" so ", "so the"),
            min_words=6,
        ),
        _t(
            "Say this in your own words, without using the word 'therefore': The sample was contaminated; therefore the results were thrown away.",
            (("thrown away", "discarded", "not used", "rejected", "ignored"), ("contaminated", "not pure", "spoiled", "dirty", "impure")),
            "Both the reason and the consequence have to be there in the learner's own phrasing.",
            banned=("therefore",),
            min_words=6,
        ),
    ),
    "lang.production": (
        _k(
            "Write two sentences on why a market day matters to a town. The three points are: one specific effect on people, one specific effect on trade or money, and something that links the two.",
            (
                _concept("effect_on_people", "One specific effect on the people of the town", "people come together", "food is available", "families", "buyers", "sellers meet"),
                _concept("effect_on_trade", "One specific effect on trade or money", "money changes hands", "trade", "income", "goods are sold", "prices"),
                _concept("the_link", "Something that connects the two effects", "because", "which means", "so the", "this is why"),
            ),
            "Marked on the three points only: not on spelling, not on style, and never on length beyond two sentences.",
            min_words=12,
        ),
        _k(
            "Write two sentences about a choice a character in a story you know made badly. The three points are: what the character chose, the reason they had for it, and what it cost them.",
            (
                _concept("the_choice", "What the character actually decided", "chose", "decided", "he took", "she took", "refused"),
                _concept("their_reason", "The reason the character had, as they would have seen it", "because they thought", "wanted", "believed", "hoped"),
                _concept("the_cost", "What the choice cost them", "lost", "as a result", "ended up", "cost"),
            ),
            "A summary of the plot scores nothing: the three points are a decision, a motive and a consequence.",
            min_words=12,
        ),
        _k(
            "Write two sentences on why clean water at home matters. The three points are: a health reason, a practical reason, and something that links them.",
            (
                _concept("health", "A health reason", "disease", "illness", "sick", "cholera", "healthy"),
                _concept("practical", "A practical reason", "cooking", "washing", "carrying", "time", "queues"),
                _concept("the_link", "Something that links the two", "because", "which means", "so", "this is why"),
            ),
            "Both reasons must be specific; 'water is important' on its own meets no point.",
            min_words=12,
        ),
    ),
}

# --------------------------------------------------------------------------- #
# sciences
# --------------------------------------------------------------------------- #

SCIENCES_POOLS: dict[str, tuple[dict[str, Any], ...]] = {
    "sci.prediction_models": (
        _c(
            "A stone and a feather are dropped at the same moment inside a long tube that has had all the air taken out. What happens?",
            (
                "They reach the bottom at the same time",
                "The stone lands first because it is heavier",
                "The feather lands first because it is lighter",
                "Neither of them falls",
            ),
            0,
            "With no air to push against, both fall at the same rate. In ordinary air the feather is slowed by drag, which is why the tube matters.",
        ),
        _c(
            "Ice floats on liquid water. Which must be true?",
            (
                "Ice is less dense than liquid water",
                "Ice is colder, and cold things always float",
                "Ice weighs less than the water it sits in",
                "Ice always contains trapped air bubbles",
            ),
            0,
            "Floating is about density, not weight or temperature: a heavy ship floats for the same reason.",
        ),
        _c(
            "A green plant is kept in a dark cupboard with water for a week. What do you expect?",
            (
                "It stays alive but goes pale, because it cannot make food without light",
                "It dies within a day",
                "It grows faster because the cupboard is warm",
                "Nothing about it changes",
            ),
            0,
            "Light is the energy source for making food; a week without it stops that but does not kill a plant that has water and stored food.",
        ),
        _c(
            "You add a little acid to blue litmus solution. Predict what you will see.",
            ("It turns red", "It turns colourless", "It stays blue", "It turns green"),
            0,
            "Blue litmus turns red in acid; it stays blue in a base.",
        ),
    ),
    "sci.cause_chains": (
        _o(
            "Put the stages of the water cycle in order, first to last.",
            (
                "The sun heats the surface of the sea",
                "Water evaporates into the air",
                "The water vapour rises and cools",
                "Clouds form as the vapour condenses",
                "Rain falls and runs back to the sea",
            ),
            "Evaporation needs the heat first, and condensation has to happen before anything can fall.",
        ),
        _o(
            "Put the journey of a meal through the body in order, first to last.",
            (
                "Food is chewed in the mouth",
                "It is swallowed down the oesophagus",
                "Acid and enzymes break it down in the stomach",
                "Nutrients are absorbed in the small intestine",
                "Water is reabsorbed in the large intestine",
            ),
            "Absorption happens after breakdown, and water is taken back last, from what is left.",
        ),
        _o(
            "Put the stages of blood moving through the heart in order, first to last.",
            (
                "Blood returns from the body to the right side of the heart",
                "It is pumped to the lungs",
                "It picks up oxygen in the lungs",
                "It returns to the left side of the heart",
                "It is pumped out to the rest of the body",
            ),
            "The right side sends blood to the lungs; only oxygenated blood leaves the left side for the body.",
        ),
        _o(
            "Put the stages of a new law in order, first to last.",
            (
                "A member introduces a bill",
                "The bill is debated and amended",
                "The lower house votes on it",
                "The upper house considers it",
                "The president signs it into law",
            ),
            "A bill cannot be voted on before it has been debated, and it is not law until it is signed.",
        ),
    ),
    "sci.data_sense": (
        {
            "kind": "dataset",
            "data": "A seedling was measured every week: week 1 = 3 cm, week 2 = 5 cm, week 3 = 8 cm, week 4 = 12 cm, week 5 = 17 cm.",
            "value": _c(
                "How much did the seedling grow between week 2 and week 3?",
                ("3 cm", "5 cm", "8 cm", "2 cm"),
                0,
                "8 - 5 = 3 cm. The weekly increases are 2, 3, 4 and 5 cm.",
            ),
            "trend": _c(
                "What is the trend across the five weeks?",
                (
                    "It grows by a larger amount each week",
                    "It grows by the same amount each week",
                    "It is growing more slowly each week",
                    "It stops growing after week 4",
                ),
                0,
                "The increases are 2, 3, 4, 5 cm — getting bigger, so the growth is speeding up.",
            ),
            "why": "One question reads a value and one reads the trend, because either alone is half a measurement.",
        },
        {
            "kind": "dataset",
            "data": "A cup of water was cooled and measured: 0 min = 90 C, 5 min = 70 C, 10 min = 55 C, 15 min = 45 C, 20 min = 38 C.",
            "value": _c(
                "How much did the water cool in the first five minutes?",
                ("20 C", "10 C", "35 C", "70 C"),
                0,
                "90 - 70 = 20 C.",
            ),
            "trend": _c(
                "What is the cooling doing?",
                (
                    "It is fastest at the start and slows down",
                    "It happens at a constant rate",
                    "It is slowest at the start and speeds up",
                    "It stops after ten minutes",
                ),
                0,
                "The drops are 20, 15, 10 and 7 C: each five minutes loses less than the one before.",
            ),
            "why": "The value question is a subtraction; the trend question is about the size of the drops.",
        },
        {
            "kind": "dataset",
            "data": "Customers in a shop each day: Monday 40, Tuesday 45, Wednesday 60, Thursday 55, Friday 90.",
            "value": _c(
                "How many more customers came on Friday than on Monday?",
                ("50", "45", "90", "40"),
                0,
                "90 - 40 = 50.",
            ),
            "trend": _c(
                "Which describes the week?",
                (
                    "Rising overall, with one dip on Thursday",
                    "Rising steadily every single day",
                    "Falling overall",
                    "Flat, with one busy day",
                ),
                0,
                "40, 45, 60, 55, 90 rises overall but falls from Wednesday to Thursday, and a trend claim that ignores the dip is not a reading of the data.",
            ),
            "why": "A trend that is 'rising' but not monotone has to be described as it is.",
        },
    ),
    "sci.experimental_thinking": (
        _c(
            "A learner tests whether fertiliser makes bean plants taller. She puts fertiliser on ten plants on the sunny windowsill and none on ten plants on a shelf in the corner, and measures them all after three weeks. What is wrong with the test?",
            (
                "The two groups also differ in light, so light could be the real cause",
                "Ten plants is too many to compare",
                "She should have measured them every day instead",
                "Bean plants do not respond to fertiliser at all",
            ),
            0,
            "Two things changed at once (fertiliser and light), so a difference in height cannot be attributed to either.",
        ),
        _c(
            "To test whether hot water freezes faster than cold, a learner puts 200 ml of boiling water in a glass beaker and 200 ml of cold water in a plastic cup, and leaves both in the same freezer. What is wrong?",
            (
                "The containers differ, so the material could explain any difference",
                "Both samples should have been hot",
                "200 ml is too much water to freeze",
                "A freezer is not cold enough for the test",
            ),
            0,
            "Glass and plastic conduct heat differently, so the test compares a container as well as a temperature.",
        ),
        _c(
            "A survey asks learners: 'Don't you agree that the new school uniform is better than the old one?' What is wrong with the question?",
            (
                "It pushes the answer, so it cannot measure what people actually think",
                "It should have been asked in writing only",
                "It is too short to be a proper question",
                "Nothing: it is a fair question",
            ),
            0,
            "A question that supplies the answer measures agreement with the questioner, not opinion.",
        ),
        _c(
            "A learner wants to know whether music helps her concentrate. She does one homework with music on and compares it with how she remembers doing homework last week without music. What is wrong?",
            (
                "There is no measurement without music under the same conditions",
                "Music cannot affect concentration",
                "One homework is too long a task",
                "She should have used louder music",
            ),
            0,
            "A remembered comparison is not a measurement: the two attempts differ in the work, the day and the memory as well as the music.",
        ),
    ),
    "sci.scale_sense": (
        _c(
            "About how big is a human red blood cell?",
            (
                "About 8 thousandths of a millimetre",
                "About 8 millimetres",
                "About 8 centimetres",
                "About 8 metres",
            ),
            0,
            "Roughly 8 micrometres across — far too small to see without a microscope.",
        ),
        _c(
            "About how far away is the Moon?",
            (
                "About 400,000 km",
                "About 4,000 km",
                "About 4 million km",
                "About 150 million km",
            ),
            0,
            "The Moon is about 384,000 km away. 150 million km is the distance to the Sun.",
        ),
        _c(
            "About how many cells are there in an adult human body?",
            (
                "About 30 trillion",
                "About 30 million",
                "About 30 billion",
                "About 30 thousand",
            ),
            0,
            "Estimates put it near 3 x 10 to the 13. Thirty billion is a thousand times too small.",
        ),
        _c(
            "About how thick is one sheet of ordinary printing paper?",
            (
                "About a tenth of a millimetre",
                "About one millimetre",
                "About one hundredth of a millimetre",
                "About one centimetre",
            ),
            0,
            "A ream of 500 sheets is about 5 cm thick, so one sheet is about 0.1 mm.",
        ),
    ),
}

# --------------------------------------------------------------------------- #
# history, humanities and government
# --------------------------------------------------------------------------- #

HISTORY_POOLS: dict[str, tuple[dict[str, Any], ...]] = {
    "hist.chronology": (
        _o(
            "Put these events of 1914 in order, earliest first.",
            (
                "The Archduke is assassinated in Sarajevo",
                "Austria-Hungary declares war on Serbia",
                "Germany invades Belgium",
                "Britain declares war on Germany",
                "The fighting ends in an armistice",
            ),
            "The assassination is the trigger, the declarations follow it, and the armistice is four years later.",
        ),
        _o(
            "Put these stages of industrial change in order, earliest first.",
            (
                "New machines are built for spinning and weaving",
                "Factories are built beside rivers and coalfields",
                "Workers move from villages into factory towns",
                "Trade unions are formed by workers",
                "Laws are passed limiting child labour",
            ),
            "Machines come first, then the buildings that house them, then the people, then their organisation, then the response to it.",
        ),
        _o(
            "Put these stages in a country's move to self-rule in order, earliest first.",
            (
                "Colonial rule is established",
                "A nationalist movement is formed",
                "Negotiations with the colonial power begin",
                "Independence is declared",
                "A new constitution is written",
            ),
            "Rule precedes the movement against it, and a constitution follows independence.",
        ),
        _o(
            "Put these stages of a public health reform in order, earliest first.",
            (
                "Officials record that a disease is spreading in one district",
                "A committee is set up to investigate the cause",
                "The committee reports that the water supply is contaminated",
                "A law is passed requiring clean water supplies",
                "Deaths from the disease fall in the following years",
            ),
            "The record of the problem comes before the investigation, and the fall in deaths is the last thing to happen.",
        ),
    ),
    "hist.cause_consequence": (
        _c(
            "Which best explains why a quarrel between Austria-Hungary and Serbia in 1914 became a war across Europe?",
            (
                "Alliances pulled in countries that had no direct quarrel with Serbia",
                "The assassination in Sarajevo",
                "The small size of the Serbian army",
                "The weather that summer delayed mobilisation",
            ),
            0,
            "The assassination explains why the quarrel started; the alliance system explains why it spread to countries with no stake in it.",
        ),
        _c(
            "Which best explains why a constitution keeps the courts separate from the government that runs the country?",
            (
                "So that the people who apply the law are not the same people who make and enforce it",
                "Because judges are better educated than ministers",
                "To make the business of government slower",
                "So that the head of state can appoint supporters as judges",
            ),
            0,
            "Separation of powers exists so that no single body both makes the rules and decides whether they were broken.",
        ),
        _c(
            "Which best explains why coastal towns in East Africa grew wealthy earlier than inland ones?",
            (
                "They could trade by sea with places far away",
                "They received more rain than inland towns",
                "Their soil was better for farming",
                "They were never attacked",
            ),
            0,
            "Sea trade reached markets that overland routes could not, and wealth followed the trade rather than the rainfall.",
        ),
        _c(
            "Which best explains why the earliest factories were built beside rivers?",
            (
                "Moving water provided power before steam engines were common",
                "Rivers supplied drinking water for the workers",
                "Land beside rivers was the cheapest to buy",
                "Factories had to be built away from towns",
            ),
            0,
            "Water wheels were the available source of power; drinking water and land price were not what decided the site.",
        ),
    ),
    "hist.source_analysis": (
        {
            "kind": "source",
            "source": (
                "A poster from 1915: a soldier in uniform points straight at the person looking at it, "
                "and the words underneath read 'Your country needs you'."
            ),
            "question": _c(
                "What was this source made to do?",
                (
                    "Persuade men to join the army",
                    "Report how many soldiers had enlisted",
                    "Explain to historians how the war began",
                    "Warn the public about enemy spies",
                ),
                0,
                "A pointed finger and 'your country needs you' is an appeal to the reader, not a report: it is trying to make them do something.",
            ),
            "why": "Purpose is read off the form of the source as much as its words.",
        },
        {
            "kind": "source",
            "source": (
                "A government press release announcing that a new road was opened on schedule and within "
                "budget, issued by the ministry that paid for it."
            ),
            "question": _c(
                "What was this source made to do?",
                (
                    "Present the project as a success to the public",
                    "Give a neutral account of what the road cost",
                    "Criticise the company that built it",
                    "Explain how roads are engineered",
                ),
                0,
                "The author is the body with an interest in the outcome, and the claims are the two that make it look good.",
            ),
            "why": "Who issued it decides how far its claims can be taken at face value.",
        },
        {
            "kind": "source",
            "source": (
                "A trader's private diary entry describing the day the railway reached his town, including "
                "what he felt about the crowds."
            ),
            "question": _c(
                "What was this source made to do?",
                (
                    "Record the writer's own experience for himself",
                    "Persuade investors to buy railway shares",
                    "Instruct railway workers on their duties",
                    "Report the official passenger numbers",
                ),
                0,
                "A private diary is written for its author; it is strong evidence of experience and weak evidence of statistics.",
            ),
            "why": "A source's audience is what tells you which parts of it to trust.",
        },
        {
            "kind": "source",
            "source": (
                "A newspaper editorial arguing that school fees should be abolished, published two weeks "
                "before a national election."
            ),
            "question": _c(
                "What was this source made to do?",
                (
                    "Convince readers to support a policy",
                    "Report what the government had already decided",
                    "Describe an ordinary day at a school",
                    "Explain how school fees are calculated",
                ),
                0,
                "An editorial argues; its timing before an election is part of what it is for.",
            ),
            "why": "Genre and timing together give the purpose.",
        },
    ),
    "hist.argument": (
        _k(
            "Make this claim convincing to somebody who disagrees with it: 'A town should spend its money on a clinic before a stadium.' Three points are marked: the claim in your own words, one specific piece of evidence or example, and the link between them.",
            (
                _concept("claim_restated", "The claim, stated in your own words", "clinic first", "before the stadium", "health comes first", "priority"),
                _concept("specific_evidence", "One specific piece of evidence or example", "because in", "for example", "data shows", "when the town", "patients"),
                _concept("the_link", "The link between the evidence and the claim", "which means", "so the town", "that is why", "therefore"),
            ),
            "Agreeing with the claim scores nothing; the three points are a claim, a specific support and the connection between them.",
            min_words=15,
        ),
        _k(
            "Make this claim convincing to somebody who disagrees with it: 'Schools should teach practical skills alongside academic ones.' Three points are marked: the claim in your own words, one specific piece of evidence or example, and the link between them.",
            (
                _concept("claim_restated", "The claim, stated in your own words", "practical skills", "alongside", "as well as academic", "both"),
                _concept("specific_evidence", "One specific piece of evidence or example", "for example", "learners who", "graduates", "workshops", "in one school"),
                _concept("the_link", "The link between the evidence and the claim", "which means", "so schools", "that is why", "therefore"),
            ),
            "The evidence point has to be specific: 'skills are useful' is a restatement, not support.",
            min_words=15,
        ),
        _k(
            "Make this claim convincing to somebody who disagrees with it: 'Voting matters even when one vote cannot change the result.' Three points are marked: the claim in your own words, one specific piece of evidence or example, and the link between them.",
            (
                _concept("claim_restated", "The claim, stated in your own words", "voting matters", "even though one vote", "turnout", "still worth voting"),
                _concept("specific_evidence", "One specific piece of evidence or example", "for example", "an election decided by", "when turnout", "margins of"),
                _concept("the_link", "The link between the evidence and the claim", "which means", "so voting", "that is why", "therefore"),
            ),
            "The strongest answers use the fact that margins are sometimes tiny, or that turnout itself changes what politicians do.",
            min_words=15,
        ),
    ),
    "hist.perspective": (
        _k(
            "A town council wants to build a market where a football field is. Write the reason the young people who use the field would give, in their own words, as they would say it. Three points are marked: their side is named, their own reason is given, and the council's aim or the cost of the change is acknowledged.",
            (
                _concept("their_side", "The people whose view is being written", "we use the field", "young people", "our team", "us who play"),
                _concept("their_reason", "A reason they would actually give", "nowhere else to play", "we have trained there", "it is the only", "our meetings"),
                _concept("acknowledgement", "The council's aim, or what the change would cost", "we know the town needs", "the market would help", "but if", "even though"),
            ),
            "A caricature ('they just want to play') meets no point: the three points are a position, a reason and an acknowledgement.",
            min_words=15,
        ),
        _k(
            "In 1850 a factory owner and a worker disagree about how long the working day should be. Write the owner's reason as he would give it. Three points are marked: his position, a reason he would genuinely hold, and something he would say about the workers or the trade.",
            (
                _concept("his_position", "The owner's own position", "longer hours", "we must keep the machines running", "the day should stay"),
                _concept("his_reason", "A reason he would genuinely hold", "the coal and rent", "competitors", "if we stop", "the orders"),
                _concept("his_account_of_others", "What he would say about the workers or the trade", "they are paid", "we give them work", "their families", "wages are fair"),
            ),
            "The point of the exercise is a reason the owner could believe, not one a modern reader would accept.",
            min_words=15,
        ),
        _k(
            "A government bans the sale of plastic bags. Write the reason a small shopkeeper gives for objecting. Three points are marked: her position, a specific cost to her business, and something she would accept about the reason for the ban.",
            (
                _concept("her_position", "The shopkeeper's position", "i object", "this ban hurts", "we cannot", "against the ban"),
                _concept("specific_cost", "A specific cost to her business", "paper bags cost", "my customers", "i would have to raise", "no money to buy"),
                _concept("what_she_accepts", "Something she accepts about the ban's aim", "i understand litter", "the environment", "rubbish is a problem", "i agree that"),
            ),
            "An objection with no acknowledgement is a slogan; the third point is what makes it a perspective.",
            min_words=15,
        ),
    ),
}

# --------------------------------------------------------------------------- #
# the generic fallback (§6.2, fifth list)
# --------------------------------------------------------------------------- #
#: The universal mechanics, with neutral material. These tasks are *not* about the learner's topic —
#: they cannot be, because the topic is one the app has no authored content for — and every task says
#: so. Inventing topic-specific questions here would be the exact fabrication §13 forbids.
GENERIC_NOTE = (
    "These tasks use the mechanics every subject shares, not your topic's own material: the app has no "
    "written content for this topic yet and will not invent any."
)

GENERIC_POOLS: dict[str, tuple[dict[str, Any], ...]] = {
    "gen.recognise": (
        _c(
            "Here are four statements about a school water tank. Which one is an observation rather than an opinion?",
            (
                "The tank holds 2,000 litres and was half full on Monday morning",
                "The tank is far too small for the school",
                "Somebody ought to repair the tank",
                "The tank is the ugliest thing in the yard",
            ),
            0,
            "An observation is something that could be checked and agreed on by anybody who looked; the other three are judgements.",
            generic=True,
        ),
        _c(
            "Which of these is an example of a claim rather than of evidence?",
            (
                "Fertiliser makes maize grow taller",
                "Plot A grew 40 cm and plot B grew 25 cm",
                "Both plots were measured with the same tape on the same day",
                "Plot A received fertiliser and plot B did not",
            ),
            0,
            "The first is what somebody asserts; the other three are things that could be measured and checked.",
            generic=True,
        ),
        _c(
            "Which of these is a method rather than a result?",
            (
                "Count the number of learners who arrive after the bell for two weeks",
                "Twelve learners arrived late on Monday",
                "Lateness fell by half in one term",
                "Most learners arrive on time",
            ),
            0,
            "A method says how a number would be obtained; the others are numbers or summaries of them.",
            generic=True,
        ),
    ),
    "gen.recall": (
        {
            "kind": "study_recall",
            "study": (
                "Read this, then it will be taken away. A fair test needs three things: one variable "
                "changed, everything else kept the same, and the measurement taken the same way each time."
            ),
            "study_seconds": 20,
            "question": _t(
                "Write the three things a fair test needs, from memory.",
                (
                    ("one variable", "one thing", "one factor", "change one"),
                    ("kept the same", "everything else", "the same conditions", "unchanged", "controlled"),
                    ("measured the same way", "same way each time", "same method", "consistently"),
                ),
                "All three have to be recalled; two out of three is recorded as two out of three, not as a pass.",
                min_words=6,
            ),
            "why": "Recall of material the round itself just showed, so the score is a memory measurement and not a prior-knowledge one.",
            "generic": True,
        },
        {
            "kind": "study_recall",
            "study": (
                "Read this, then it will be taken away. An estimate is reported with two things next to "
                "it: how many observations it came from, and the range it could reasonably be within."
            ),
            "study_seconds": 20,
            "question": _t(
                "Write the two things that must be reported next to an estimate.",
                (
                    ("how many", "the number of observations", "sample size", "count", "n"),
                    ("range", "interval", "margin", "spread", "confidence"),
                ),
                "A number with no count and no range is a claim dressed as a measurement.",
                min_words=5,
            ),
            "why": "The same rule the app's own snapshot screens follow.",
            "generic": True,
        },
    ),
    "gen.apply": (
        _c(
            "A school wants to know whether a new timetable reduces lateness. Which of these applies the idea of a fair test?",
            (
                "Count lateness the same way before and after the change, and keep everything else as it was",
                "Ask the teachers whether they think lateness has fallen",
                "Compare this school with a school in another town",
                "Count only the learners who arrive on time",
            ),
            0,
            "One thing changes (the timetable), the counting stays the same, and the rest is left alone.",
            generic=True,
        ),
        _c(
            "A shopkeeper believes a new sign brings more customers. Which is the best way to apply a fair test?",
            (
                "Count customers for two weeks with the old sign and two weeks with the new one, counting the same way",
                "Ask five regular customers whether they like the sign",
                "Put the sign up and assume any rise is caused by it",
                "Compare the shop with a shop that has no sign at all",
            ),
            0,
            "The comparison has to be with the same shop under the same conditions, with only the sign changed.",
            generic=True,
        ),
        _c(
            "A learner says a fertiliser worked because the plants that got it grew taller. Applying the idea of a fair test, what should she check first?",
            (
                "Whether the two groups differed in anything else, such as light or water",
                "Whether the fertiliser was expensive",
                "How tall the plants were at the end",
                "Whether the plants were the same species",
            ),
            0,
            "The first question is always whether something else changed with it; the others do not address the cause.",
            generic=True,
        ),
    ),
    "gen.explain": (
        _k(
            "Explain why a measurement has to be taken the same way each time when the thing being measured is changing. Three points are marked: the mechanism, why a changed method would spoil the comparison, and an example of what would go wrong.",
            (
                _concept("mechanism", "Why the same method makes numbers comparable", "comparing", "same scale", "the difference is real", "like for like"),
                _concept("the_spoiling", "Why changing the method would spoil it", "you cannot tell", "the method changed", "confused with", "attributed to"),
                _concept("example", "An example of what would go wrong", "for example", "if you measured", "such as", "imagine"),
            ),
            "The three points are a mechanism, a consequence and an instance; restating the question meets none of them.",
            min_words=15,
            generic=True,
        ),
        _k(
            "Explain why an estimate based on three observations should be treated differently from one based on three hundred. Three points are marked: what a small number of observations does to the estimate, what it means for the range, and what should be done about it.",
            (
                _concept("small_n", "What few observations do to an estimate", "one result moves it", "not stable", "could easily be chance", "swings"),
                _concept("the_range", "What it means for the range", "wider range", "less certain", "bigger interval", "uncertainty"),
                _concept("the_response", "What should be done about it", "label it early", "measure more", "wait for more", "say it is not enough"),
            ),
            "This is the rule every screen in the app follows: under about eight observations an estimate is called an early signal.",
            min_words=15,
            generic=True,
        ),
    ),
    "gen.spot_error": (
        _c(
            "Which of these uses evidence wrongly?",
            (
                "Concluding a fertiliser works because the plants that got it were also the ones nearest the window",
                "Repeating a measurement three times and taking the middle value",
                "Comparing two groups that differ in only one respect",
                "Saying a result is uncertain because only six people were measured",
            ),
            0,
            "The first attributes an effect to a treatment that was confounded with light; the other three are ordinary good practice.",
            generic=True,
        ),
        _c(
            "Which of these is a wrong use of an average?",
            (
                "Reporting the mean income of a village where one household earns a hundred times what the others do",
                "Reporting the median income of the same village",
                "Reporting the mean together with the spread of the incomes",
                "Reporting the mean of a set of measurements that are all close together",
            ),
            0,
            "One extreme value drags a mean, so it stops describing anybody in the village; the other three are honest uses.",
            generic=True,
        ),
        _c(
            "Which of these reasons is the weak one?",
            (
                "It must have rained, because the ground is wet and I saw no other cause",
                "The rain gauge recorded 12 mm last night",
                "Three neighbours all said it rained",
                "The forecast said rain and the gauge agrees",
            ),
            0,
            "Wet ground has other causes (a hose, a burst pipe), so 'no other cause occurred to me' is not evidence.",
            generic=True,
        ),
    ),
    "gen.predict": (
        _c(
            "A ball is rolled with the same push across a smooth floor and then across a rough carpet. What do you expect on the carpet?",
            (
                "It slows down sooner and travels a shorter distance",
                "It travels faster",
                "It travels exactly the same distance",
                "It stops the instant it touches the carpet",
            ),
            0,
            "More friction means more of the push is used up per metre, but it does not stop instantly.",
            generic=True,
        ),
        _c(
            "A learner revises with past papers and her score rises from 4 out of 10 to 7 out of 10. What is the best-supported prediction for the next test, if nothing else changes?",
            (
                "Somewhere near 7, because one improvement is not yet a settled pattern",
                "Exactly 10 out of 10",
                "Back down to 4 out of 10",
                "Nothing at all can be said about the next test",
            ),
            0,
            "One observation moves an expectation but does not fix it; the honest prediction is near the new score with room either side.",
            generic=True,
        ),
        _c(
            "A shop sells 40, 45 and 50 loaves on three successive Mondays. What is the most reasonable prediction for the next Monday?",
            (
                "About 55, with a range either side",
                "Exactly 55",
                "About 40, returning to the first week",
                "About 500, since it keeps rising",
            ),
            0,
            "The pattern is a rise of five a week, so about 55 — but three points support a range, not a certainty.",
            generic=True,
        ),
    ),
}

#: Every pool, by skill id.
POOLS: dict[str, tuple[dict[str, Any], ...]] = {
    **MATHS_POOLS,
    **LANGUAGES_POOLS,
    **SCIENCES_POOLS,
    **HISTORY_POOLS,
    **GENERIC_POOLS,
}


def pool_for(skill_id: str) -> tuple[dict[str, Any], ...]:
    return POOLS.get(skill_id, ())


# --------------------------------------------------------------------------- #
# generation: pure in (skill, seed, index), so a reload reproduces the same task
# --------------------------------------------------------------------------- #


def _rng(*parts: Any) -> random.Random:
    return random.Random(stable_seed("subjects", *parts))


@dataclass(frozen=True)
class GeneratedTasks:
    """The tasks for one skill game: the full (server-side) list and what the learner may see."""

    skill_id: str
    tasks: tuple[dict[str, Any], ...]

    def public(self) -> list[dict[str, Any]]:
        return [public_task(t) for t in self.tasks]

    def by_id(self, task_id: str) -> dict[str, Any] | None:
        return next((t for t in self.tasks if t.get("id") == task_id), None)


def tasks_for_skill(
    skill_id: str,
    *,
    seed: int,
    topic_title: str | None = None,
    level_hint: str | None = None,
    count: int | None = None,
) -> GeneratedTasks:
    """The tasks for one skill, generated from the run seed.

    Nothing here is random per request: the same ``(skill, seed, learner)`` always produces the same
    tasks in the same order, so a learner who reloads mid-round is answered on the item they were
    shown, and a resume on another device is served the same game.
    """
    sk = require_skill(skill_id)
    pool = pool_for(skill_id)
    if not pool:
        raise KeyError(f"skill {skill_id!r} has no authored items, so it cannot be measured")
    want = int(count if count is not None else (sk.game.get("items") or ITEMS_PER_SKILL))
    want = max(1, min(want, len(pool)))
    out: list[dict[str, Any]] = []

    if sk.id == "lang.comprehension":
        # One literal and one inference, from two different passages: two literal questions would
        # measure how fast they read.
        order = _rng(skill_id, seed, "passages", topic_title or "").sample(range(len(pool)), min(2, len(pool)))
        picked = [(pool[i], part) for i, part in zip(order, ("literal", "inference"))]
        for n, (entry, part) in enumerate(picked):
            item = dict(entry[part])
            out.append(
                _finish(
                    sk,
                    item,
                    index=n,
                    seed=seed,
                    task_id=f"{skill_id}_{part}_{n}",
                    passage=str(entry.get("passage") or ""),
                    part=part,
                    topic_title=topic_title,
                    level_hint=level_hint,
                    total=len(picked),
                )
            )
        return GeneratedTasks(skill_id=skill_id, tasks=tuple(out))

    if sk.id == "sci.data_sense":
        order = _rng(skill_id, seed, "datasets", topic_title or "").sample(range(len(pool)), min(2, len(pool)))
        entry = dict(pool[order[0]])
        for n, part in enumerate(("value", "trend")):
            item = dict(entry[part])
            out.append(
                _finish(
                    sk,
                    item,
                    index=n,
                    seed=seed,
                    task_id=f"{skill_id}_{part}_{n}",
                    data=str(entry.get("data") or ""),
                    part=part,
                    topic_title=topic_title,
                    level_hint=level_hint,
                    total=2,
                )
            )
        return GeneratedTasks(skill_id=skill_id, tasks=tuple(out))

    if sk.id == "hist.source_analysis":
        order = _rng(skill_id, seed, "sources", topic_title or "").sample(range(len(pool)), min(want, len(pool)))
        for n, i in enumerate(order):
            entry = dict(pool[i])
            item = dict(entry["question"])
            out.append(
                _finish(
                    sk,
                    item,
                    index=n,
                    seed=seed,
                    task_id=f"{skill_id}_source_{n}",
                    source=str(entry.get("source") or ""),
                    topic_title=topic_title,
                    level_hint=level_hint,
                    total=len(order),
                )
            )
        return GeneratedTasks(skill_id=skill_id, tasks=tuple(out))

    if sk.id == "gen.recall":
        # Two tasks: an unscored study card and the recall that follows it. A recall item with no
        # study phase measures what the learner already knew and calls it memory.
        order = _rng(skill_id, seed, "recall", topic_title or "").sample(range(len(pool)), min(1, len(pool)))
        entry = dict(pool[order[0]])
        out.append(
            {
                "id": f"{skill_id}_study_0",
                "skill": skill_id,
                "skill_title": sk.title,
                "family": sk.family,
                "kind": "study",
                "phase": "show",
                "stage": "skills",
                "stem": str(entry.get("study") or ""),
                "seconds": int(entry.get("study_seconds") or 20),
                "instructions": "Read this. It will be taken away and you will be asked for it.",
                "wagerable": False,
                "generic": bool(entry.get("generic")),
                "note": GENERIC_NOTE if entry.get("generic") else "",
                "item_of": 1,
                "items_total": 2,
            }
        )
        item = dict(entry["question"])
        out.append(
            _finish(
                sk,
                item,
                index=1,
                seed=seed,
                task_id=f"{skill_id}_recall_0",
                topic_title=topic_title,
                level_hint=level_hint,
                total=2,
            )
        )
        return GeneratedTasks(skill_id=skill_id, tasks=tuple(out))

    order = _rng(skill_id, seed, "order", topic_title or "", level_hint or "").sample(range(len(pool)), min(want, len(pool)))
    for n, i in enumerate(order):
        out.append(
            _finish(
                sk,
                dict(pool[i]),
                index=n,
                seed=seed,
                task_id=f"{skill_id}_{n}",
                topic_title=topic_title,
                level_hint=level_hint,
                total=len(order),
            )
        )
    return GeneratedTasks(skill_id=skill_id, tasks=tuple(out))


def _finish(
    sk: Any,
    item: dict[str, Any],
    *,
    index: int,
    seed: int,
    task_id: str,
    total: int,
    topic_title: str | None = None,
    level_hint: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One served task: the authored item plus the run's identity, with choices shuffled by seed."""
    task: dict[str, Any] = {
        "id": task_id,
        "skill": sk.id,
        "skill_title": sk.title,
        "family": sk.family,
        "kind": sk.kind,
        "grader": item.get("grader") or sk.grader,
        "phase": "measure",
        "stage": "skills",
        "seconds": max(10, int(round(sk.seconds / max(1, total)))),
        "instructions": str(sk.game.get("instructions") or ""),
        "wagerable": bool(sk.game.get("wagerable", True)),
        "item_of": index + 1,
        "items_total": total,
        "topic_title": topic_title or "",
        "level_hint": level_hint or "",
        "generic": bool(item.get("generic")),
        **{k: v for k, v in extra.items() if v},
    }
    if item.get("generic"):
        task["note"] = GENERIC_NOTE
    task.update({k: v for k, v in item.items() if k not in ("grader", "generic")})

    # Choice order is shuffled per (learner, task) so that two learners do not share an answer key by
    # position, and so a "always pick the second one" habit cannot score.
    if task.get("kind") == "choice" and task.get("options"):
        r = _rng(task_id, seed, "shuffle")
        positions = list(range(len(task["options"])))
        r.shuffle(positions)
        task["options"] = [task["options"][p] for p in positions]
        task["answer"] = positions.index(int(item["answer"]))
        task["shuffled"] = True
    if task.get("kind") == "order" and task.get("steps"):
        r = _rng(task_id, seed, "order")
        positions = list(range(len(task["steps"])))
        r.shuffle(positions)
        task["items"] = [task["steps"][p] for p in positions]
        # ``answer_order`` lists, for each correct position, the index in ``items`` that belongs there.
        task["answer_order"] = [positions.index(i) for i in range(len(positions))]
    if task.get("kind") == "match" and task.get("right"):
        r = _rng(task_id, seed, "match")
        positions = list(range(len(task["right"])))
        r.shuffle(positions)
        task["right_shuffled"] = [task["right"][p] for p in positions]
        task["answer_pairs"] = [[left_i, positions.index(right_i)] for left_i, right_i in task["pairs"]]
    return task


#: Fields that would tell the learner the answer. Stripped before anything is sent to a browser.
PRIVATE_FIELDS = frozenset(
    {
        "answer",
        "answer_order",
        "answer_pairs",
        "pairs",
        "steps",
        "required_groups",
        "banned",
        "rubric",
        "tolerance",
        "why",
    }
)


def public_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """The learner's copy of a task: everything they need to answer, and no key.

    A checklist-graded task *does* show its points, because §6.2 grades against a fixed checklist the
    learner is told about — hiding the points would grade them against something they could not see.
    """
    out = {k: v for k, v in dict(task).items() if k not in PRIVATE_FIELDS}
    if str(task.get("grader")) == "checklist":
        rubric = task.get("rubric") or {}
        out["checklist"] = [str(p.get("statement") or p.get("concept")) for p in rubric.get("required_concepts") or []]
    if task.get("kind") == "match":
        out["right"] = list(task.get("right_shuffled") or task.get("right") or [])
    if task.get("kind") == "text" and task.get("banned"):
        out["avoid"] = [str(b).strip() for b in task["banned"] if str(b).strip()]
    out.pop("right_shuffled", None)
    return out


# --------------------------------------------------------------------------- #
# scoring (all of it in code)
# --------------------------------------------------------------------------- #

_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset(
    "a an the and or but of to in on for with at by from as is are was were be been it its this that these those "
    "i you we they he she his her their our your".split()
)


def _tokens(text: Any) -> set[str]:
    return {m.group(0) for m in _WORD.finditer(str(text or "").lower())} - _STOP


def _content_words(text: Any) -> list[str]:
    """The words that carry meaning, in order — ``teachback.content_words`` without importing it twice."""
    return [w for w in _WORD.findall(str(text or "").lower()) if w not in _STOP]


def _phrase_hit(answer_tokens: set[str], phrase: str) -> bool:
    """True when every content word of ``phrase`` appears in the answer (stems allowed).

    Stemming is a prefix test on words of five letters or more, which is enough for "change/changed"
    and "measure/measured" without letting "car" match "careful".
    """
    wanted = _tokens(phrase)
    if not wanted:
        return False
    for word in wanted:
        if word in answer_tokens:
            continue
        if len(word) >= 5 and any(t.startswith(word[:5]) for t in answer_tokens):
            continue
        return False
    return True


def score_task(task: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    """Judge one response. Returns a dict with ``correct`` (``None`` when nothing was answered or the
    task is a study card), ``points``, ``max_points``, ``status`` and a ``why`` the learner can read.
    """
    task = dict(task or {})
    response = dict(response or {})
    kind = str(task.get("kind") or "")
    grader = str(task.get("grader") or "deterministic")

    if task.get("phase") == "show":
        return {
            "correct": None,
            "status": "studied",
            "points": 0,
            "max_points": 0,
            "why": "a study card is not a question: nothing here is scored",
        }

    if kind == "choice":
        given = response.get("choice", response.get("value", response.get("index")))
        if given is None or given == "":
            return _blank(task)
        try:
            chosen = int(given)
        except (TypeError, ValueError):
            return {"correct": False, "status": "unparseable", "points": 0, "max_points": 1, "why": "the answer was not one of the choices"}
        options = list(task.get("options") or [])
        if not 0 <= chosen < len(options):
            return {"correct": False, "status": "out_of_range", "points": 0, "max_points": 1, "why": f"choice {chosen} is not one of the {len(options)} options"}
        correct = chosen == int(task.get("answer", -1))
        return {
            "correct": correct,
            "status": "scored",
            "points": 1 if correct else 0,
            "max_points": 1,
            "chosen": chosen,
            "chosen_text": str(options[chosen]),
            "why": str(task.get("why") or "") if correct else _wrong_choice_why(task, chosen),
        }

    if kind == "order":
        given = response.get("order")
        if not isinstance(given, Sequence) or isinstance(given, (str, bytes)) or not given:
            return _blank(task)
        try:
            order = [int(i) for i in given]
        except (TypeError, ValueError):
            return {"correct": False, "status": "unparseable", "points": 0, "max_points": 1, "why": "the order was not a list of positions"}
        expected = [int(i) for i in (task.get("answer_order") or [])]
        items = list(task.get("items") or [])
        if sorted(order) != list(range(len(items))):
            return {
                "correct": False,
                "status": "incomplete",
                "points": 0,
                "max_points": 1,
                "why": f"an order needs each of the {len(items)} items exactly once",
            }
        hits = sum(1 for a, b in zip(order, expected) if a == b)
        return {
            "correct": order == expected,
            "status": "scored",
            "points": 1 if order == expected else 0,
            "max_points": 1,
            "positions_correct": hits,
            "positions": len(expected),
            "why": (
                str(task.get("why") or "")
                if order == expected
                else f"{hits} of {len(expected)} items were in the right place. {task.get('why', '')}".strip()
            ),
        }

    if kind == "numeric":
        raw = response.get("value", response.get("answer"))
        if raw is None or str(raw).strip() == "":
            return _blank(task)
        try:
            value = float(str(raw).replace(",", "").strip())
        except (TypeError, ValueError):
            return {"correct": False, "status": "unparseable", "points": 0, "max_points": 1, "why": "this one needs a number"}
        expected = float(task.get("answer") or 0.0)
        tol = float(task.get("tolerance") or 0.0)
        correct = abs(value - expected) <= max(tol, 1e-9)
        return {
            "correct": correct,
            "status": "scored",
            "points": 1 if correct else 0,
            "max_points": 1,
            "given": value,
            "expected": expected,
            "why": str(task.get("why") or "") if correct else f"the answer is {_show(expected)}. {task.get('why', '')}".strip(),
        }

    if kind == "match":
        given = response.get("pairs")
        if not isinstance(given, Sequence) or isinstance(given, (str, bytes)) or not given:
            return _blank(task)
        try:
            pairs = sorted((int(a), int(b)) for a, b in (tuple(p) for p in given))
        except (TypeError, ValueError):
            return {"correct": False, "status": "unparseable", "points": 0, "max_points": 1, "why": "each match needs two positions"}
        expected = sorted(tuple(p) for p in (task.get("answer_pairs") or []))
        hits = sum(1 for p in pairs if tuple(p) in expected)
        return {
            "correct": pairs == expected,
            "status": "scored",
            "points": 1 if pairs == expected else 0,
            "max_points": 1,
            "matched": hits,
            "of": len(expected),
            "why": (
                str(task.get("why") or "")
                if pairs == expected
                else f"{hits} of {len(expected)} pairs were right. {task.get('why', '')}".strip()
            ),
        }

    if kind == "text":
        text = str(response.get("text") or response.get("value") or "").strip()
        if not text:
            return _blank(task)
        if grader == "checklist":
            return _score_checklist(task, text)
        return _score_rule_text(task, text)

    return {"correct": None, "status": "unsupported", "points": 0, "max_points": 0, "why": f"no scorer for task kind {kind!r}"}


def _blank(task: Mapping[str, Any]) -> dict[str, Any]:
    """No answer given. Not a miss: §6.3's rule that a skipped skill is never zero applies to items too."""
    return {
        "correct": None,
        "status": "skipped",
        "points": 0,
        "max_points": 0,
        "why": "nothing was answered, so this item is recorded as not measured rather than as a miss",
        "skill": str(task.get("skill") or ""),
    }


def _wrong_choice_why(task: Mapping[str, Any], chosen: int) -> str:
    """The explanation for a miss, which is the authored one plus what they picked."""
    options = list(task.get("options") or [])
    picked = str(options[chosen]) if 0 <= chosen < len(options) else "?"
    why = str(task.get("why") or "")
    return f"You picked {picked[:90]!r}. {why}".strip()


def _show(value: float) -> str:
    return f"{value:g}"


def _score_rule_text(task: Mapping[str, Any], text: str) -> dict[str, Any]:
    """A short answer graded by rule: every required group met, no banned phrase, enough words."""
    tokens = _tokens(text)
    words = _content_words(text)
    groups = [list(g) for g in (task.get("required_groups") or [])]
    met = [next((p for p in group if _phrase_hit(tokens, p)), None) for group in groups]
    missed = [group[0] for group, hit in zip(groups, met) if not hit]
    banned = [str(b).strip().lower() for b in (task.get("banned") or []) if str(b).strip()]
    used_banned = [b for b in banned if b in f" {text.lower()} "]
    min_words = int(task.get("min_words") or 0)
    too_short = len(words) < min_words
    correct = not missed and not used_banned and not too_short
    notes = []
    if missed:
        notes.append(f"missing: {', '.join(missed[:3])}")
    if used_banned:
        notes.append(f"reused the wording it asked you to avoid: {', '.join(used_banned)}")
    if too_short:
        notes.append(f"too short: {len(words)} content word(s), {min_words} asked for")
    return {
        "correct": correct,
        "status": "scored",
        "points": sum(1 for m in met if m) if groups else (1 if correct else 0),
        "max_points": max(1, len(groups)),
        "graded_by": "rule",
        "words": len(words),
        "why": (
            str(task.get("why") or "")
            if correct
            else ("; ".join(notes) + f". {task.get('why', '')}").strip(". ") + "."
        ),
        "note": "graded by a fixed rule in code; nothing else read your answer",
    }


def _score_checklist(task: Mapping[str, Any], text: str) -> dict[str, Any]:
    """Three fixed points, graded by the repo's own rule grader (§6.2's checklist rule)."""
    criteria = TB.checklist_from_rubric(task.get("rubric") or {})
    if not criteria:
        return {"correct": None, "status": "no_checklist", "points": 0, "max_points": 0, "why": "this task has no checklist, so it cannot be graded"}
    result = TB.grade_checklist(text, criteria)
    payload = result.as_dict()
    met = int(payload.get("met") or 0)
    size = int(payload.get("checklist_size") or len(criteria))
    return {
        "correct": met >= max(2, math.ceil(size * 2 / 3)),
        "status": "scored",
        "points": met,
        "max_points": size,
        "graded_by": payload.get("graded_by") or "rules",
        "verdict": payload.get("verdict"),
        "words": payload.get("words"),
        "listing_suspected": bool(payload.get("listing_suspected")),
        "checklist": payload.get("points"),
        "why": (
            str(task.get("why") or "")
            if met == size
            else f"{met} of {size} points were made. {payload.get('note') or ''} {task.get('why', '')}".strip()
        ),
        "note": "graded against three fixed points by a rule in code; your text was treated as data, never as instructions",
    }


# --------------------------------------------------------------------------- #
# validation of the authored content itself
# --------------------------------------------------------------------------- #


def validate_pools() -> list[str]:
    """Structural checks on the authored pools. Returns problems (empty = valid).

    The one that matters most is the duplicate-option check: two identical options in a single-answer
    item is an item that cannot be answered correctly, and it is invisible to a reader skimming a
    bank of a hundred items.
    """
    from .taxonomy import SKILL_IDS

    problems: list[str] = []
    for skill_id in SKILL_IDS:
        pool = pool_for(skill_id)
        if not pool:
            problems.append(f"{skill_id}: no authored items, so the skill cannot be measured")
            continue
        for i, item in enumerate(pool):
            label = f"{skill_id}[{i}]"
            if item.get("kind") in ("passage", "dataset", "source", "study_recall"):
                for key in ("literal", "inference", "value", "trend", "question"):
                    if key in item and not isinstance(item[key], Mapping):
                        problems.append(f"{label}: {key} is not an item")
                    elif key in item:
                        problems.extend(_check_item(f"{label}.{key}", item[key]))
                continue
            problems.extend(_check_item(label, item))
    return problems


def _check_item(label: str, item: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    kind = str(item.get("kind") or "")
    if kind not in ("choice", "order", "numeric", "text", "match"):
        problems.append(f"{label}: kind {kind!r} has no scorer")
        return problems
    if not str(item.get("why") or "").strip():
        problems.append(f"{label}: no explanation for the answer, so a miss cannot be taught from")
    if kind == "choice":
        options = [str(o).strip() for o in (item.get("options") or [])]
        if len(options) < 3:
            problems.append(f"{label}: {len(options)} options; a two-option item is a coin flip")
        if len(set(o.lower() for o in options)) != len(options):
            problems.append(f"{label}: two options are the same, so the item cannot be answered correctly")
        answer = item.get("answer")
        if not isinstance(answer, int) or not 0 <= answer < len(options):
            problems.append(f"{label}: answer {answer!r} is not an index into its own options")
    if kind == "order":
        steps = [str(s).strip() for s in (item.get("steps") or [])]
        if len(steps) < 3:
            problems.append(f"{label}: an order of {len(steps)} steps is not a chain")
        if len(set(steps)) != len(steps):
            problems.append(f"{label}: two steps are identical")
    if kind == "numeric":
        if not isinstance(item.get("answer"), (int, float)):
            problems.append(f"{label}: numeric answer is not a number")
    if kind == "text":
        if item.get("grader") == "checklist":
            points = (item.get("rubric") or {}).get("required_concepts") or []
            if len(points) != TB.CHECKLIST_SIZE:
                problems.append(f"{label}: a checklist has {len(points)} points, and the rule is three")
            for p in points:
                if not str(p.get("idea") or "").strip() and not (p.get("synonyms") or []):
                    problems.append(f"{label}: a checklist point has no wording to match against")
        else:
            groups = item.get("required_groups") or []
            if not groups:
                problems.append(f"{label}: a rule-graded text item requires nothing, so anything passes")
    if kind == "match":
        left, right = list(item.get("left") or []), list(item.get("right") or [])
        pairs = [tuple(p) for p in (item.get("pairs") or [])]
        if len(left) != len(right) or len(pairs) != len(left):
            problems.append(f"{label}: a match needs the same number on both sides and one pair each")
        if len({p[1] for p in pairs}) != len(pairs):
            problems.append(f"{label}: two left-hand items point at the same right-hand one")
    return problems


def task_stats() -> dict[str, Any]:
    """How much authored content there is, for the console and for the report."""
    kinds: dict[str, int] = {}
    for skill_id, pool in POOLS.items():
        for item in pool:
            kind = str(item.get("kind") or "item")
            kinds[kind] = kinds.get(kind, 0) + 1
    return {"skills_with_items": len(POOLS), "items": sum(len(p) for p in POOLS.values()), "by_kind": kinds}


__all__ = [
    "GENERIC_NOTE",
    "GeneratedTasks",
    "POOLS",
    "PRIVATE_FIELDS",
    "public_task",
    "pool_for",
    "score_task",
    "task_stats",
    "tasks_for_skill",
    "validate_pools",
]
