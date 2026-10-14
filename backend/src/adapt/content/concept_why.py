"""One-sentence key idea and why for each authored bank concept.

Shown before worked steps in the deterministic tutor. The sentences are written
from the bank notes in ``assessment.bank.TOPIC_NOTES``, not generated, and they
are looked up by bank code, topic title, or a tag that names either. A database
topic id, or the placeholder title "the topic", matches nothing.
"""

from __future__ import annotations

from typing import Any

# title is the bank title, matched case-insensitively and only as a whole title.
CONCEPT_WHY: dict[str, dict[str, str]] = {
    "integer_ops": {
        "title": "Integer operations",
        "key_idea": "In a chain of additions and subtractions, equal-rank operations are done strictly left to right, and each minus sign belongs to the number after it.",
        "why_it_works": "Working left to right keeps the running total honest, and checking the chain backwards catches a sign that was added back by accident.",
    },
    "fractions_intro": {
        "title": "What a fraction means",
        "key_idea": "A fraction names equal parts of a whole: the denominator is how many equal parts, and the numerator is how many of those parts you mean.",
        "why_it_works": "The parts have to be equal, because unequal pieces do not name a single share, and writing the fraction is the same as dividing the numerator by the denominator.",
    },
    "equivalent_fractions": {
        "title": "Equivalent fractions",
        "key_idea": "Two fractions are equivalent when they name the same point on the number line, even if the digits differ.",
        "why_it_works": "Multiplying or dividing the numerator and the denominator by the same non-zero number multiplies by 1, so the value cannot change.",
    },
    "fraction_add": {
        "title": "Adding and subtracting fractions",
        "key_idea": "Fractions can be added or subtracted only after they share a common denominator.",
        "why_it_works": "You can only add like units, and adding the denominators would change the size of each part instead of counting parts of the same size.",
    },
    "fraction_mult": {
        "title": "Multiplying and dividing fractions",
        "key_idea": "To multiply fractions, multiply the numerators and multiply the denominators; to divide by a fraction, multiply by its reciprocal.",
        "why_it_works": "Multiplication does not need a common denominator, and dividing by a fraction asks how many copies of that fraction fit into the amount.",
    },
    "decimals": {
        "title": "Decimals and place value",
        "key_idea": "Each place is ten times the place on its right, and that pattern continues past the decimal point into tenths, hundredths and thousandths.",
        "why_it_works": "A decimal is a fraction whose denominator is a power of ten, so shifting the point multiplies or divides by ten without changing the digits.",
    },
    "percentages": {
        "title": "Percentages of quantities",
        "key_idea": "A percentage is a fraction over 100, so finding a percentage of a quantity means converting it to a decimal and multiplying.",
        "why_it_works": "A decrease of p percent multiplies by one minus p over 100, and repeating that multiplication is why a rise then a fall does not return you to the start.",
    },
    "ratio": {
        "title": "Ratio and proportion",
        "key_idea": "A ratio compares quantities as parts, and it simplifies the same way a fraction does.",
        "why_it_works": "Sharing an amount in a ratio starts by adding the parts and finding the value of one part, then scaling each part up.",
    },
    "negative_numbers": {
        "title": "Negative numbers",
        "key_idea": "On a number line, adding moves right and subtracting moves left, and that stays true below zero.",
        "why_it_works": "The minus sign is a move, not a separator, so two subtractions are two moves left, and two negatives multiply to a positive because the direction reverses twice.",
    },
    "order_of_operations": {
        "title": "Order of operations",
        "key_idea": "Brackets come first, then powers, then multiplication and division left to right, then addition and subtraction left to right.",
        "why_it_works": "Multiplication and division have equal rank, so they are not done before each other by type, and a fraction bar acts as a bracket around the top and the bottom.",
    },
    "collecting_terms": {
        "title": "Collecting like terms",
        "key_idea": "Like terms have the same variables to the same powers, so they count the same thing and can be added.",
        "why_it_works": "The coefficient includes its sign, and a variable is not like the same variable squared because the powers differ.",
    },
    "linear_equations": {
        "title": "Solving linear equations",
        "key_idea": "Solving finds the value that makes both sides equal, and equality is kept by doing the same operation to both sides.",
        "why_it_works": "Undoing operations in reverse order, then substituting back into the original equation, checks that the value really balances both sides.",
    },
    "rearranging": {
        "title": "Rearranging formulae",
        "key_idea": "Rearranging is solving with the subject as the unknown, using the same legal moves as an equation.",
        "why_it_works": "Terms that contain the subject are collected and factorised before dividing, because a term that is part of a sum cannot be cancelled.",
    },
    "graphs_lines": {
        "title": "Graphs of straight lines",
        "key_idea": "In y = mx + c, m is the gradient, change in y over change in x, and c is where the line crosses the y-axis.",
        "why_it_works": "Parallel lines share a gradient, perpendicular gradients multiply to -1, and a point lies on the line only when its coordinates satisfy the equation.",
    },
    "indices": {
        "title": "Indices and powers",
        "key_idea": "A power counts repeated multiplication of a base: same-base products add indices, quotients subtract them, and a power of a power multiplies them.",
        "why_it_works": "Anything to the power zero is 1, and a negative index means the reciprocal, so a power of -2 is 1 over the base squared.",
    },
    "standard_form": {
        "title": "Standard form",
        "key_idea": "Standard form writes a number as A times 10 to a power, with A at least 1 and less than 10.",
        "why_it_works": "A large number gets a positive power and a small number a negative power, and after multiplying the A values and adding the powers, A is readjusted back into range.",
    },
    "statistical_mean": {
        "title": "Mean, median and range",
        "key_idea": "The mean is the total divided by how many values there are, the median is the middle value in order, and the range is largest minus smallest.",
        "why_it_works": "The mean uses every value and is pulled by outliers, while the median resists them, and the range describes spread only, never the average.",
    },
    "area_perimeter": {
        "title": "Area and perimeter",
        "key_idea": "Perimeter is a length found by adding sides, while area counts the square units inside and so multiplies two lengths.",
        "why_it_works": "A triangle's area is half the enclosing rectangle, and a missing side is recovered by rearranging the area formula rather than memorising a variant.",
    },
    "unit_conversion": {
        "title": "Converting units",
        "key_idea": "Converting units is multiplying by 1 written as an equivalent pair, so the unit you do not want cancels.",
        "why_it_works": "Squared units need the factor squared and cubic units need it cubed, and estimating first catches a multiply-versus-divide slip.",
    },
    "probability": {
        "title": "Probability as a fraction",
        "key_idea": "For equally likely outcomes, probability is favourable outcomes over total outcomes, a fraction between 0 and 1.",
        "why_it_works": "Independent events multiply, and the chance of at least one is usually faster as 1 minus the probability of none.",
    },
    "history_ww1": {
        "title": "Why the First World War began",
        "key_idea": "The First World War began in 1914 after the assassination of Archduke Franz Ferdinand, and no single cause explains it.",
        "why_it_works": "Alliances turned a local quarrel into a European war, with militarism and imperial rivalry already in place before 1914.",
    },
    "bio_photosynthesis": {
        "title": "How plants make their food",
        "key_idea": "Photosynthesis is how a plant makes glucose in its chloroplasts, using light trapped by chlorophyll, carbon dioxide and water, and releasing oxygen.",
        "why_it_works": "Carbon dioxide and water are joined in the presence of light and chlorophyll to give glucose and oxygen, while respiration releases the stored energy and happens all the time.",
    },
}


def _norm(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _code_for(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip()
    if key in CONCEPT_WHY:
        return key
    return None


def _title_for(value: object) -> str | None:
    norm = _norm(value)
    if not norm or norm == "the topic":
        return None
    for code, row in CONCEPT_WHY.items():
        if norm == _norm(row["title"]):
            return code
    return None


def sentences_for(
    *,
    topic_id: object = None,
    topic_title: object = None,
    tags: Any = None,
) -> tuple[str, str] | None:
    """``(key_idea, why_it_works)`` or nothing when the concept is not in the bank."""
    code = _code_for(topic_id) or _title_for(topic_title)
    if code is None:
        for tag in list(tags or []):
            code = _code_for(tag) or _title_for(tag)
            if code:
                break
    if code is None:
        return None
    row = CONCEPT_WHY[code]
    return row["key_idea"], row["why_it_works"]
