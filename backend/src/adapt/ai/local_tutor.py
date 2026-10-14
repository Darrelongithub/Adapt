"""The local tutor: AI 2's offline fallback and its grounded retrieval layer.

Design goal from the blueprint: *"Transcription accuracy and teaching accuracy are
separate requirements"* and *"provide sensible fallbacks when a free service
reaches its limits."* A chatbot that stops when the API is down is not a demo.

So ADAPT ships a second teaching path that needs no network at all:

* BM25 retrieval over the learner's own course materials, item solutions and
  cached resource snippets -- an honest extractive RAG. It cannot invent a
  theorem, which is also why it never contradicts the syllabus.
* Template composition driven by the *measured* level (BKT trace) and the approved
  adaptations, so the fallback still behaves adaptively.
* Rubric-based teach-back scoring using required concepts + synonym sets.
* Practice generation by sampling the item bank and re-seeding numeric variants.

When Gemini answers, :mod:`adapt.ai.ai2` uses this output as grounding context and
lets the model rewrite/extend it. When Gemini is unavailable, this output *is* the
answer, flagged ``provider="local"`` in the UI so nobody mistakes it for LLM text.
"""

from __future__ import annotations

import logging
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

log = logging.getLogger("adapt.tutor")

STOPWORDS = frozenset(
    """a an the and or but if while of to in on for with without at by from as is are was were be been being
    do does did not no so than then that this these those it its they them he she you your we our i me my
    what which how why when where who whom whose about into over under more most less least very just also
    can could should would will shall may might must have has had having get got make made use used using""".split()
)

WORD = re.compile(r"[A-Za-z][A-Za-z'\u00c0-\u024f\-]{1,}|[0-9][0-9.,]*")


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text or "") if w.lower() not in STOPWORDS]


def stem(word: str) -> str:
    """A deliberately small stemmer, for matching a question to a sentence about it.

    "adding fractions" and "how do I add fractions" are the same question, and a plain token
    overlap says they share nothing. This is not linguistics: four suffixes, no dictionary, no
    dependency — enough for ``-ing``/``-ed``/``-es``/``-s`` and nothing that could merge two
    different ideas by accident (words of five letters or fewer are left alone, so "same" stays
    "same" and "less" stays "less")."""
    w = (word or "").lower()
    for suffix in ("ing", "ed", "es"):
        if len(w) > 5 and w.endswith(suffix):
            w = w[: -len(suffix)]
            break
    else:
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
    # A trailing "e" goes too, so "divide"/"dividing"/"divided" all land on "divid" and
    # "value"/"values" both land on "valu". Words of five letters or fewer are left alone,
    # which is what keeps "same", "less" and "note" from merging into something else.
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


def sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 20]


@dataclass
class Doc:
    doc_id: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tokens = tokenize(self.text)


class BM25:
    """Okapi BM25 (k1=1.4, b=0.75) over an in-memory index."""

    def __init__(self, docs: Sequence[Doc], *, k1: float = 1.4, b: float = 0.75) -> None:
        self.docs = list(docs)
        self.k1, self.b = k1, b
        self.df: Counter[str] = Counter()
        self.avg_len = 0.0
        for d in self.docs:
            self.avg_len += len(d.tokens)
            for t in set(d.tokens):
                self.df[t] += 1
        self.avg_len = self.avg_len / max(1, len(self.docs))
        self.n = len(self.docs)

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def score(self, query: str) -> list[tuple[float, Doc]]:
        q = tokenize(query)
        if not q:
            return []
        out: list[tuple[float, Doc]] = []
        for d in self.docs:
            tf = Counter(d.tokens)
            s = 0.0
            dl = max(1, len(d.tokens))
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                s += self._idf(term) * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / max(1e-6, self.avg_len)))
            if s > 0:
                out.append((s, d))
        return sorted(out, key=lambda x: x[0], reverse=True)

    def search(self, query: str, *, k: int = 4) -> list[Doc]:
        return [d for _, d in self.score(query)[:k]]


# --------------------------------------------------------------------------- #
# corpus building
# --------------------------------------------------------------------------- #


def build_index(
    *,
    materials: Iterable[tuple[str, str, str, dict[str, Any]]] = (),  # (material_id, topic_id, body, meta)
    items: Iterable[dict[str, Any]] = (),
    resources: Iterable[dict[str, Any]] = (),
) -> BM25:
    docs: list[Doc] = []
    for mid, topic_id, body, meta in materials:
        for i, sent in enumerate(sentences(body)):
            docs.append(
                Doc(
                    f"mat:{mid}:{i}",
                    sent,
                    {
                        "kind": "material",
                        "topic_id": topic_id,
                        "topic_title": meta.get("topic_title") or "",
                        "title": meta.get("title", ""),
                    },
                )
            )
    for item in items:
        topic_id = item.get("topic_id", "")
        topic_title = item.get("topic_title") or ""
        for i, step in enumerate(item.get("solution_steps") or []):
            text = step.get("text") if isinstance(step, dict) else str(step)
            if text:
                docs.append(Doc(f"sol:{item.get('id')}:{i}", text, {"kind": "solution", "topic_id": topic_id, "topic_title": topic_title}))
        for i, hint in enumerate(item.get("hints") or []):
            docs.append(Doc(f"hint:{item.get('id')}:{i}", hint, {"kind": "hint", "topic_id": topic_id, "topic_title": topic_title}))
        expl = item.get("explanation") or ""
        for i, sent in enumerate(sentences(expl)):
            docs.append(Doc(f"expl:{item.get('id')}:{i}", sent, {"kind": "explanation", "topic_id": topic_id, "topic_title": topic_title}))
    for r in resources:
        text = r.get("snippet") or r.get("abstract") or r.get("title") or ""
        for i, sent in enumerate(sentences(text)[:8]):
            docs.append(
                Doc(
                    f"res:{r.get('provider','ext')}:{r.get('external_id','x')}:{i}",
                    sent,
                    {"kind": "resource", "provider": r.get("provider"), "url": r.get("url"), "title": r.get("title")},
                )
            )
    return BM25(docs)


# --------------------------------------------------------------------------- #
# level-aware text assembly
# --------------------------------------------------------------------------- #

LEVELS = ("new", "developing", "proficient", "mastered")


@dataclass
class Grounded:
    """An assembled answer plus its provenance (so the UI can show 'why this').

    ``grounded`` is a claim about where the words came from, and it used to be hard-coded to
    ``True`` in :meth:`as_dict` — so a reply stitched together from *another topic's* notes
    still arrived at the screen wearing the badge. It is now a property of the assembly:
    true only when the body text came from a source tagged with this item's own topic.
    """

    text: str
    sections: list[dict[str, str]] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    directives_applied: list[str] = field(default_factory=list)
    level: str = "developing"
    confident: bool = True
    #: Did the shown body actually come from the item's topic?
    grounded: bool = False
    #: Which topic the shown body came from (None when there is no body).
    source_topic: str | None = None
    #: Short steps with a one-question check after each (see ``learning.segments``).
    steps: list[dict[str, Any]] = field(default_factory=list)
    #: Plain-language reason when nothing relevant was found.
    note: str | None = None
    #: Did the shown body come from the item's own topic (rather than from elsewhere in the
    #: course, where it may still be the right answer to the question that was asked)?
    topic_match: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.text,
            "sections": self.sections,
            "sources": self.sources,
            "steps": self.steps,
            "adaptations_applied": self.directives_applied,
            "level": self.level,
            "provider": "local",
            "grounded": bool(self.grounded),
            "topic_match": bool(self.topic_match),
            "topic_id": self.source_topic,
            "note": self.note
            or (None if self.confident else "I could not find this in your course materials."),
        }


def _level_from(p_know: float | None) -> str:
    if p_know is None:
        return "new"
    if p_know < 0.35:
        return "new"
    if p_know < 0.65:
        return "developing"
    if p_know < 0.85:
        return "proficient"
    return "mastered"


#: A retrieved sentence has to share this share of the query's content words before it may
#: be shown as the explanation. Tuned against the failure that prompted it: "Work out
#: 7 - 1 - 9" was answered with text about factorising, which shared the word "work" and
#: nothing else, and "which calculation first in 7 + 3 x 2 - 1" with text about powers of
#: zero. A single shared stopword is not a topic.
RELEVANCE_MIN_TERMS = 0.4
#: And it has to score a decent share of the best hit in the same topic, so a weak match
#: inside the right topic is not promoted just for being the only one there.
RELEVANCE_MIN_SHARE = 0.45


def _relevance(query: str, doc: Doc, score: float, best: float) -> bool:
    """Is this sentence about the question that was asked?

    Three gates, in the order that the observed defects asked for them:

    1. **Numbers are decisive.** A question carrying two or more numbers is about *those*
    numbers: if the sentence shares none of them it is about a different sum, however well its
    words match. ("Work out 7 - 1 - 9" was answered with a paragraph on factorising, which
    shared the words "work" and "out" and no numbers at all.)
    2. **Words have to actually overlap**, with a light stem so "adding" matches "add": a
    question with three or more content words must share half of them; a question with one or
    two must share all of them, because otherwise a single common word ("same") is enough to
    attach an unrelated sentence to any question containing it.
    3. **Score competitiveness**, so the best sentence is not swamped by merely-eligible ones.
    """
    q_terms = list(dict.fromkeys(tokenize(query)))
    if not q_terms:
        return False
    doc_terms = {stem(w) for w in doc.tokens}
    numbers = [w for w in q_terms if any(ch.isdigit() for ch in w)]
    words = [w for w in q_terms if w not in numbers]
    uniq_numbers = set(numbers)
    if len(uniq_numbers) >= 2 and len(uniq_numbers & doc_terms) < math.ceil(0.5 * len(uniq_numbers)):
        # Half the numbers, not one: a passage that shares a single digit with the question is
        # usually about a different sum that happens to contain it.
        return False
    shared_words = {stem(w) for w in words} & doc_terms
    if len(words) <= 2:
        # A one- or two-word question *is* those words: "denominators, same" has to be met by
        # both, or a sentence containing only "same" would attach itself to any question that
        # happens to use it.
        needed = len(words)
    else:
        needed = max(2, math.ceil(RELEVANCE_MIN_TERMS * len(words)))
    if len(shared_words) < needed:
        return False
    if best > 0 and score < RELEVANCE_MIN_SHARE * best:
        return False
    return True


def _shorten(text: str, max_words: int) -> str:
    """Kept for the few callers that want a one-line string, and it no longer truncates.

    It used to be ``" ".join(words[:max_words]) + "…"``, which cut mid-sentence: an
    explanation could end "…because the denominator" and be stored as a complete answer.
    Now it delegates to the sentence-boundary chunker in ``learning.segments`` and returns
    whole sentences that fit the budget; if even the first sentence is longer than the
    budget, the whole sentence is returned rather than half of it.
    """
    from ..learning import segments as SEG

    sents = SEG.sentences(text)
    if not sents:
        return (text or "").strip()
    out: list[str] = []
    used = 0
    for s in sents:
        w = len(s.split())
        if out and used + w > max_words:
            break
        out.append(s)
        used += w
        if used >= max_words:
            break
    return " ".join(out)


#: Openers, by what is actually happening. The old build had exactly one ("Let's start from
#: one concrete case.") for every explanation, so a learner who asked twice read the same
#: sentence as if it were the answer. Selected by a seed so the same question in the same
#: context reads the same on a reload, but a repeat error or a post-hint question reads
#: differently from a first attempt.
OPENERS: dict[str, tuple[str, ...]] = {
    "new": (
        "Let's take one concrete case.",
        "Start with the smallest example of this.",
        "Here it is with real numbers, once.",
    ),
    "developing": (
        "You have most of {topic}; here is the part that is missing.",
        "The idea is nearly there — this is the piece that slips.",
        "Close. One move is missing from what you did.",
    ),
    "proficient": (
        "Short version, then the edge case.",
        "You can do this; here is where it bites.",
        "Straight to the part that catches people.",
    ),
    "mastered": (
        "You know this — so here is the harder question instead.",
        "Then take this one a step further.",
        "Skip the method; here is the case that breaks it.",
    ),
}
#: and by why the learner is asking
CONTEXT_OPENERS: dict[str, tuple[str, ...]] = {
    "repeat_error": (
        "Same idea, different angle: the last attempt shows where it went wrong.",
        "This is the second time this one has bitten, so let us do it the long way.",
    ),
    "after_hint": (
        "The hint was the start; here is the whole move.",
        "Building on that hint: the rest of the step.",
    ),
    "after_reveal": (
        "Now that the answer is on screen, here is what it was doing.",
        "The answer is not the point; this is what produced it.",
    ),
    "refused": (
        "I cannot find this in your material, and I will not guess at it.",
    ),
}


def _opener(level: str, context: str, *, topic_title: str | None, seed: str) -> str:
    pool = CONTEXT_OPENERS.get(context) or OPENERS.get(level) or OPENERS["developing"]
    rng = random.Random(seed or level)
    return rng.choice(pool).replace("{topic}", topic_title or "this")


#: Authored simple-mode "why" for every bank concept (Round 2, Phase A,
#: task 6). One sentence for the key idea and one for why it works, written
#: for the deterministic path so a learner in simple mode meets the idea
#: before the worked steps rather than only a recipe. ``title`` is the
#: bank topic's title: the live path knows a topic by that title (and by
#: an opaque row id), while the quality walk knows it by bank code, so
#: :func:`concept_idea_for` accepts either.
CONCEPT_IDEAS: dict[str, dict[str, str]] = {
    "integer_ops": {
        "title": "Integer operations",
        "key_idea": "In a chain of adding and subtracting, work from left to right and let each minus sign belong to the number after it.",
        "why_it_works": "This works because operations of the same rank are done in reading order, so no sign gets moved or lost.",
    },
    "fractions_intro": {
        "title": "What a fraction means",
        "key_idea": "A fraction says how many equal parts of one whole you have, with the denominator naming the size of each part and the numerator counting them.",
        "why_it_works": "This works because equal parts are the unit, so a fraction only means something when every part is the same size.",
    },
    "equivalent_fractions": {
        "title": "Equivalent fractions",
        "key_idea": "Equivalent fractions name the same amount even though the numerator and denominator look different.",
        "why_it_works": "This works because multiplying the top and bottom by the same non-zero number multiplies the fraction by one, so its value does not change.",
    },
    "fraction_add": {
        "title": "Adding and subtracting fractions",
        "key_idea": "Fractions can only be added or subtracted once they share a common denominator.",
        "why_it_works": "This works because a common denominator makes both fractions count parts of the same size, so the numerators can be added.",
    },
    "fraction_mult": {
        "title": "Multiplying and dividing fractions",
        "key_idea": "To multiply fractions, multiply the numerators together and the denominators together, with no common denominator needed.",
        "why_it_works": "This works because multiplying by a fraction takes that share of a share, and dividing by a fraction is the same as multiplying by its reciprocal.",
    },
    "decimals": {
        "title": "Decimals and place value",
        "key_idea": "Each decimal place is worth one tenth of the place to its left.",
        "why_it_works": "This works because our number system is built in tens, so moving the point multiplies or divides by ten without changing the digits.",
    },
    "percentages": {
        "title": "Percentages of quantities",
        "key_idea": "A percentage is an amount out of one hundred, so a percentage of a quantity is that share of the quantity.",
        "why_it_works": "This works because turning the percentage into a decimal or fraction and multiplying finds the stated share of the whole.",
    },
    "ratio": {
        "title": "Ratio and proportion",
        "key_idea": "A ratio compares quantities part to part, and sharing in a ratio means first finding the value of one equal part.",
        "why_it_works": "This works because every term of the ratio counts the same-sized part, so scaling one part rebuilds the whole comparison.",
    },
    "negative_numbers": {
        "title": "Negative numbers",
        "key_idea": "Negative numbers are positions below zero on the number line, and their signs also decide the direction of a calculation.",
        "why_it_works": "This works because subtracting always moves left on the line and multiplying two negatives reverses direction twice, ending positive.",
    },
    "order_of_operations": {
        "title": "Order of operations",
        "key_idea": "Calculations have an agreed order: brackets first, then powers, then multiplication and division, then addition and subtraction.",
        "why_it_works": "This works because everyone reading the same expression in that order gets the same value, with equal-rank steps read left to right.",
    },
    "collecting_terms": {
        "title": "Collecting like terms",
        "key_idea": "Only like terms can be collected, meaning terms with exactly the same letters to exactly the same powers.",
        "why_it_works": "This works because like terms count the same kind of quantity, so their coefficients can be added without changing what is being counted.",
    },
    "linear_equations": {
        "title": "Solving linear equations",
        "key_idea": "Solving an equation means keeping both sides equal while you undo operations until the unknown stands alone.",
        "why_it_works": "This works because doing the same operation to both sides preserves equality, and undoing in reverse order peels the calculation away from the unknown.",
    },
    "rearranging": {
        "title": "Rearranging formulae",
        "key_idea": "Rearranging a formula is solving it for a chosen letter instead of for a number.",
        "why_it_works": "This works because the same equality-preserving moves apply, as long as a letter trapped inside a sum is freed by factorising before you divide.",
    },
    "graphs_lines": {
        "title": "Graphs of straight lines",
        "key_idea": "In y = mx + c, m tells you the gradient of the straight line and c tells you where it crosses the y-axis.",
        "why_it_works": "This works because m measures how much y changes for each step in x, so lines with the same m rise in exactly the same way.",
    },
    "indices": {
        "title": "Indices and powers",
        "key_idea": "An index counts how many times its base is multiplied by itself.",
        "why_it_works": "This works because counting repeated factors turns multiplying powers into adding indices, dividing powers into subtracting indices, and a negative index into a reciprocal.",
    },
    "standard_form": {
        "title": "Standard form",
        "key_idea": "Standard form writes a number as a value between one and ten multiplied by a power of ten.",
        "why_it_works": "This works because the power of ten carries the size of the number, so very large and very small numbers become easy to compare.",
    },
    "statistical_mean": {
        "title": "Mean, median and range",
        "key_idea": "The mean shares a total equally, the median is the middle value in order, and the range measures spread from smallest to largest.",
        "why_it_works": "This works because each summary answers a different question, with the mean using every value, the median resisting extremes, and the range describing how far apart the data are.",
    },
    "area_perimeter": {
        "title": "Area and perimeter",
        "key_idea": "Perimeter is the distance around a shape in single lengths, while area counts the square units that cover its surface.",
        "why_it_works": "This works because lengths add around the edge while area multiplies two lengths together, which is why their units are different.",
    },
    "unit_conversion": {
        "title": "Converting units",
        "key_idea": "Converting units means multiplying by a fraction equal to one, such as one hundred centimetres over one metre.",
        "why_it_works": "This works because multiplying by one cannot change the amount, only the unit it is written in, and area units need that factor squared.",
    },
    "probability": {
        "title": "Probability as a fraction",
        "key_idea": "For equally likely outcomes, probability is the number of favourable outcomes divided by the number of possible outcomes.",
        "why_it_works": "This works because equally likely outcomes share the chance evenly, and independent events multiply because each stage keeps its own share of possibilities.",
    },
    "history_ww1": {
        "title": "Why the First World War began",
        "key_idea": "The First World War had a trigger, the assassination in Sarajevo in June 1914, and deeper causes that turned it into a European war.",
        "why_it_works": "This works as an explanation because separating the trigger from the alliance system, militarism and imperial rivalry lets you weigh how a local quarrel became a general war.",
    },
    "bio_photosynthesis": {
        "title": "How plants make their food",
        "key_idea": "Photosynthesis uses light energy trapped by chlorophyll to join carbon dioxide and water into glucose, releasing oxygen.",
        "why_it_works": "This works because the plant takes carbon dioxide in through its leaves and water up from its roots, then stores the light energy in the glucose it makes.",
    },
}


def _idea_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def concept_idea_for(
    topic_id: object = None,
    topic_title: object = None,
    item: Mapping[str, Any] | None = None,
) -> dict[str, str] | None:
    """The authored key idea for a concept, by bank code, by row id suffix, or by title.

    Returns ``None`` for a topic with no authored entry rather than
    inventing one: a concept this table does not cover gets the
    explanation without this section, exactly as before.
    """
    item = item or {}
    candidates = [
        topic_id,
        item.get("topic_id"),
        item.get("topic_code"),
        item.get("code"),
        topic_title,
        item.get("topic_title"),
    ]
    for candidate in candidates:
        key = _idea_key(candidate)
        if not key:
            continue
        if key.replace(" ", "_") in CONCEPT_IDEAS:
            return CONCEPT_IDEAS[key.replace(" ", "_")]
        # A database row id ends in the bank code for seeded items
        # (``itm_fraction_add_02`` / a topic id built from the code).
        for code, entry in CONCEPT_IDEAS.items():
            if key.replace(" ", "_").endswith(code) or key == _idea_key(entry["title"]):
                return entry
    return None


#: Item kinds whose closing prompt asks the learner to recall a fact.
_FACTUAL_KINDS = frozenset({"factual", "text", "short_text", "recall", "factual_recall"})
#: Item kinds whose closing prompt is about choosing between options.
_CHOICE_KINDS = frozenset({"mcq", "choice", "multiple_choice", "multiple-choice"})
#: Item kinds whose closing prompt can safely say "change the numbers".
_NUMERIC_KINDS = frozenset({"numeric", "number", "typed_number", "typed-number"})


def closing_prompt(item: Mapping[str, Any] | None, *, has_example: bool = True) -> str | None:
    """The closing "try one" for an explanation, chosen by the item's kind.

    Round 2, Phase A, task 4: the old closing told every learner to
    "copy the first line of the example and change only the numbers",
    which is nonsense under a factual or a choice question. Now the
    kind decides: numeric items change the numbers, choice items cover
    the options and commit to a pick with a reason, factual items
    recall without looking — and a kind none of those fit (a teach-back,
    a worked example itself, no item at all) gets no closing prompt
    rather than one pasted on anyway.
    """
    if not item:
        return None
    kind = str(item.get("kind") or "").lower()
    if kind in _NUMERIC_KINDS:
        if has_example:
            return "Try one: copy the first line of the example and change only the numbers."
        return "Try one with the smallest numbers you can think of, and say each step out loud."
    if kind in _CHOICE_KINDS or item.get("choices") or item.get("options"):
        return "Try one: cover the options, say which you would pick and why, then uncover them and check."
    if kind in _FACTUAL_KINDS:
        return "Try one: recall the answer without looking, say it out loud, then check what you said."
    return None


def explain(
    query: str,
    *,
    index: BM25,
    topic_title: str | None = None,
    topic_id: str | None = None,
    p_know: float | None = None,
    directives: Sequence[dict[str, Any]] = (),
    item: dict[str, Any] | None = None,
    prereq_note: str | None = None,
    max_words: int = 180,
    context: str = "first_attempt",
    seed: str = "",
    segment: bool = True,
) -> Grounded:
    """Compose a level-appropriate, source-cited explanation.

    Retrieval is restricted to the item's own topic *before* ranking, and every candidate has
    to pass :func:`_relevance` against the actual question. When nothing relevant exists this
    says so, out loud, instead of answering with whatever ranked highest across the whole
    course — the defect that answered a subtraction question with a paragraph about
    factorising. ``grounded`` is true only when the shown body came from this topic.
    """
    level = _level_from(p_know)
    dmap = {d.get("strategy") or d.get("action", ""): d for d in directives}
    payload_words = int((dmap.get("segmented_text") or {}).get("payload", {}).get("max_words", 0) or 0)
    budget = min(max_words, payload_words * 2) if payload_words else max_words
    want_example = bool(dmap.get("worked_examples")) or level in ("new", "developing")

    # --- retrieval, filtered by relevance ---------------------------------------
    # The defect this replaces: BM25 ran across every topic in the course and the top hit was
    # shown, so "Work out 7 - 1 - 9" was answered with a paragraph about factorising (the two
    # shared the word "work" and nothing else). Now:
    #
    # 1) a sentence has to share content with the *question that was asked* — numbers count
    #    double, because a different sum shares none of them (:func:`_relevance`);
    # 2) the item's own topic is ranked first, and a passage from another topic is shown only
    #    with its source topic named on it, never as if it were this topic's material;
    # 3) if nothing relevant exists, this says so rather than showing something adjacent.
    wanted_topic = topic_id or (item or {}).get("topic_id")
    # When the learner is looking at an item ("walk me through this one"), the item *is* the
    # context: matching only the words they typed would find nothing in the bank, because the
    # material is written about the numbers in the question. The item's own stem joins the
    # retrieval query, and relevance is then measured against the two together — which is
    # stricter, not looser, since the query now carries more terms.
    item_query = f"{query} {(item.get('stem') or '')}".strip() if item else query
    # Two queries, and the one that is *about the question* wins unless only the item-anchored
    # one finds anything. Concatenating the item's stem onto every question fixed the bare
    # pronoun ("walk me through this one") and broke the opposite case: the digits of the item
    # in front of the learner are decisive terms, so "why do I have to make the denominators the
    # same?" asked over "16 - 7 - 6" scored the fractions notes as *irrelevant* and the reply
    # fell back to the item's own integer working — which is exactly the defect this filter was
    # written to stop, arriving by a different door.
    queries = [query] if item_query == query else [query, item_query]
    pool: dict[str, tuple[float, Doc, int]] = {}
    scored_total = 0
    for q in queries:
        hits_q = list(index.score(q)[:24]) or (list(index.score(topic_title)[:12]) if topic_title else [])
        scored_total = max(scored_total, len(hits_q))
        best_q = max((s for s, _ in hits_q), default=0.0)
        for s, d in hits_q:
            if not _relevance(q, d, s, best_q):
                continue
            prev = pool.get(d.doc_id)
            pool[d.doc_id] = (max(s, prev[0]) if prev else s, d, (prev[2] if prev else 0) + 1)
    # Same topic first, then whatever *both* queries found, then the raw score. A passage that
    # answers the question and belongs to the item's own topic outranks one that only does half.
    q_numbers = {w for w in tokenize(query) if any(ch.isdigit() for ch in w)}

    def _coverage(doc: Doc) -> int:
        """How many of the question's own numbers this passage actually contains."""
        return len(q_numbers & set(doc.tokens))

    relevant = sorted(
        pool.values(),
        key=lambda e: (
            0 if (not wanted_topic or e[1].meta.get("topic_id") == wanted_topic) else 1,
            -_coverage(e[1]),
            -e[2],
            -e[0],
        ),
    )
    rejected = max(0, scored_total - len(relevant))
    hits = relevant[:10]
    answered_from_item = False
    by_kind: dict[str, list[tuple[float, Doc]]] = defaultdict(list)
    for s, d, _n in hits:
        by_kind[d.meta.get("kind", "other")].append((s, d))

    sections: list[dict[str, str]] = []
    sources: list[dict[str, str]] = []
    parts: list[str] = []
    # What the item's own variables say, for the last-ditch substitution below.
    item_vars: dict[str, Any] = dict(((item or {}).get("answer_spec") or {}).get("variables") or {})

    def add(text: str, label: str, src: Doc | None = None) -> None:
        if not text:
            return
        # Nothing a learner reads may contain the item's template. Handing ``explain`` a
        # stored row instead of the rendered variant used to put "{a} - {b} = {ab}" in the
        # middle of an answer -- and in the model's prompt in llm mode. AI 2 now renders
        # before it gets here; this is the guard that keeps the same mistake from coming
        # back through another door: substitute what the item declares, and if any
        # placeholder is still unresolved, leave the section out rather than show braces.
        if TEMPLATE_KEY.search(text):
            filled = _substitute(text, item_vars)
            if TEMPLATE_KEY.search(filled):
                log.warning("tutor: dropped the %r section - it still held unrendered placeholders", label)
                return
            text = filled
        parts.append(text)
        sections.append({"label": label, "text": text})
        if src is not None:
            entry: dict[str, Any] = {
                "kind": str(src.meta.get("kind", "")),
                "ref": src.doc_id,
                "title": str(src.meta.get("title", "")),
            }
            # A citation the learner cannot open is a decoration. External resources
            # carry their URL; the learner's own material carries which note it came from.
            if src.meta.get("url"):
                entry["url"] = str(src.meta["url"])
            if entry["kind"] == "material" and str(src.doc_id).startswith("mat:"):
                entry["material_id"] = str(src.doc_id).split(":")[1]
            sources.append(entry)

    idea_added = False

    def add_key_idea() -> None:
        """The authored key idea and why it works, before any worked steps.

        Simple mode has to say *why* before it shows a recipe (task 6):
        the two authored sentences go in as their own sections, once,
        ahead of whichever worked example this explanation is about to
        show. A topic with no authored entry gets no section rather
        than an invented one.
        """
        nonlocal idea_added
        if idea_added:
            return
        entry = concept_idea_for(wanted_topic, topic_title, item)
        if not entry:
            return
        add(entry["key_idea"], "key idea")
        add(entry["why_it_works"], "why it works")
        idea_added = True

    if prereq_note:
        add(prereq_note, "first, the prerequisite")

    opener = _opener(level, context, topic_title=topic_title, seed=seed or str((item or {}).get("id") or query))

    # Content: pick the most relevant material sentences, de-duplicated.
    seen: set[str] = set()
    chosen: list[Doc] = []
    for score, doc, _n in hits:
        # Only declarative material becomes the body of an explanation. Hints are
        # opt-in pedagogy (hint_for serves them) and solution steps are the answer;
        # letting BM25 pull either into "the explanation" would spoil an unanswered
        # item and teach the recipe instead of the idea.
        if doc.meta.get("kind") not in ("material", "explanation"):
            continue
        key = re.sub(r"\W+", "", doc.text.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        chosen.append(doc)
        if len(chosen) >= (3 if level != "new" else 2):
            break
    # One topic's material, not a grab-bag. The ranked pool is relevant-to-the-question, and
    # relevance alone let a denominators question pull in a sentence about like terms because
    # both contain "same". The first doc decides which topic is being explained; anything from
    # a *third* topic is dropped. (Same topic and cross-topic are both fine; a third voice is
    # not — it reads as a tutor changing the subject mid-answer.)
    if chosen:
        lead_topic = chosen[0].meta.get("topic_id")
        chosen = [d for d in chosen if d.meta.get("topic_id") in (None, lead_topic)] or chosen
    topic_match = True
    if chosen:
        source_topic = str(chosen[0].meta.get("topic_id") or wanted_topic or "")
        topic_match = (not wanted_topic) or (not source_topic) or source_topic == str(wanted_topic)
        add(opener, "framing")
        if not topic_match and topic_title:
            # Relevant, but from somewhere else in the course — a learner asking about
            # denominators while the screen says "Integer operations" is better served by the
            # fractions paragraph *with a label* than by a refusal. What is never allowed is
            # presenting it as this topic's material.
            add(
                f"This comes from your notes on {chosen[0].meta.get('topic_title') or 'another topic'}, "
                f"not on {topic_title} — that is where this is actually covered:",
                "where this came from",
            )
        cap = 2 if not topic_match else 3  # a cross-topic answer stays short and to the point
        body = " ".join(_shorten(d.text, budget // max(1, len(chosen[:cap])) + 8) for d in chosen[:cap])
        add(body, "explanation", chosen[0])
        add_key_idea()
    elif item and (item.get("solution_steps") or []):
        # The material has nothing relevant, but the question in front of the learner does —
        # its own worked example, written for it. Answering from that is grounded in the
        # course's own content and is exactly what "walk me through this one" asked for.
        add(opener, "framing")
        add(
            "There is no note in your material about this exact question, so here is the "
            "question's own working, checked against its answer key:",
            "where this came from",
        )
        add_key_idea()
        lines = []
        for i, st in enumerate((item.get("solution_steps") or [])[:5]):
            txt = st.get("text") if isinstance(st, dict) else str(st)
            if txt:
                lines.append(f"{i + 1}. {txt}")
        # Shown as a labelled section, not as a citation: ``sources`` means "somewhere you can
        # go and read this", and the item has no URL to open (the client renders a URL-less
        # source as plain text, and the contract is that a non-material source is clickable).
        add("\n".join(lines), "worked example", None)
        # ``grounded`` is a claim about the *material*, and this text is the item's own
        # working, not a passage the learner can go and read. It is useful (it is the answer to
        # "walk me through this one"), so it is shown — with the one honest flag it deserves.
        source_topic = None
        topic_match = False
        answered_from_item = True
    else:
        # Nothing relevant. Saying so is the honest answer, and it is the *only* answer this
        # path is allowed to give: it is what stops a subtraction question being answered with
        # a paragraph about factorising.
        add(_opener(level, "refused", topic_title=topic_title, seed=seed), "framing")
        add(
            (
                f"I do not have anything in your material that answers this about "
                f"{topic_title or 'this topic'}"
                + (f" ({rejected} nearby passage(s) were about something else, so I left them out)." if rejected else ".")
                + " I will not guess at it. Ask me something else, or add the notes and I will read them."
            ),
            "explanation",
        )
        return Grounded(
            "\n\n".join(p for p in parts if p),
            sections,
            sources,
            list(dmap),
            level,
            confident=False,
            grounded=False,
            source_topic=None,
            note=(
                f"nothing relevant to this question in the material for "
                f"{topic_title or 'this topic'}"
            ),
        )

    # Worked example: prefer the item's own solution steps -- the ones for *this* item, not
    # the first row of whatever topic the caller happened to pass.
    already_shown = any(sec.get("label") == "worked example" for sec in sections)
    if want_example and item and not already_shown:
        steps = item.get("solution_steps") or []
        lines = []
        for i, st in enumerate(steps[:5]):
            txt = st.get("text") if isinstance(st, dict) else str(st)
            if txt:
                lines.append(f"{i + 1}. {txt}")
        if lines:
            add("\n".join(lines), "worked example")
    elif by_kind.get("solution") and want_example and not already_shown:
        top = by_kind["solution"][0][1]
        add(top.text, "worked example", top)

    if by_kind.get("resource") and level in ("new", "developing"):
        r = by_kind["resource"][0][1]
        add(
            f"For a second explanation, see {r.meta.get('title') or 'this source'} "
            f"({r.meta.get('kind', 'resource')}).",
            "extra source",
            r,
        )

    # The closing prompt follows the *item kind*, not the learner's level
    # (task 4): "change only the numbers" only makes sense under a numeric
    # item, and a kind with no fitting prompt gets none.
    has_example = any(sec.get("label") == "worked example" for sec in sections) or bool(
        item and (item.get("solution_steps") or [])
    )
    next_step = closing_prompt(item, has_example=has_example)
    if next_step:
        add(next_step, "your turn")

    applied = [k for k in dmap if k]
    text = "\n\n".join(p for p in parts if p)
    steps: list[dict[str, Any]] = []
    if segment:
        # Short steps at sentence boundaries, each with a check question: never a word count
        # applied to prose. ``learning.segments`` keeps every sentence (see its docstring).
        from ..learning import segments as SEG

        steps = SEG.segment(text, max_words=max(24, budget // 3), seed=seed or query).as_dict()["steps"]
    if answered_from_item:
        note = "answered from this question's own working — there is no note in your material about it"
    elif topic_match:
        note = None
    else:
        note = "the text shown is from another topic in your course; the source is named above"
    return Grounded(
        text,
        sections,
        sources,
        applied,
        level,
        # A claim about *which topic* the words came from, and the brief's rule is exact:
        # grounded is true only when the shown body came from this item's topic (4.1.2). The
        # cross-topic fallback above is honest -- it names the source topic on the passage --
        # but it is not this topic's material, so it must not claim to be. The topic is
        # measured separately (`topic_match`) so callers can tell "your own material" from
        # "somewhere else in the course, and here is where".
        grounded=bool(sources) and not answered_from_item and topic_match,
        source_topic=source_topic or wanted_topic,
        steps=steps,
        topic_match=topic_match,
        note=note,
    )


def hint_for(item: dict[str, Any], *, level: str, hints_used: int = 0, max_hints: int | None = None) -> str:
    """Next hint, respecting an approved hint-fading adaptation."""
    hints: Sequence[str] = list(item.get("hints") or [])
    if max_hints is not None and hints_used >= max_hints:
        return (
            "You have your hint budget for this one, and that is deliberate: try the "
            "first line, then I will tell you exactly where it goes wrong."
        )
    if not hints:
        # Every seeded item has a three-grade ladder now (``assessment/worked.py`` fills the
        # gaps at seed time and on demand), so this is the last-resort path for an item that
        # arrived from somewhere else — an import, a model-authored row. It says something
        # specific, or it says nothing: the old fallback here was one sentence that fitted
        # every question in the bank ("Write down the single fact the question is asking
        # for"), which is the defect this branch was rewritten for.
        from ..assessment import worked as W

        generated = W.generate_hints(item)
        if not generated:
            return (
                "This question has no hint ladder yet, so I will not invent one. "
                "Ask for the worked example, or type the line you are unsure of and I will say where it breaks."
            )
        hints = generated
    if hints_used >= len(hints):
        return (
            "That is every hint this question has. The rest would give the answer away, so: try the "
            "line you are unsure of and I will tell you where it breaks, or ask for the worked example."
        )
    idx = min(len(hints) - 1, hints_used)
    if level == "mastered":
        return hints[-1]
    return hints[idx]


def feedback(item: dict[str, Any], answer: str | None, *, correct: bool, score: float | None = None) -> str:
    """Rule-based corrective feedback: name the misconception, don't just judge."""
    spec = item.get("answer_spec") or {}
    misc = spec.get("misconceptions") or {}
    a = (answer or "").strip()
    bits: list[str] = []
    if correct:
        bits.append("Correct.")
        alt = spec.get("why_right")
        if alt:
            bits.append(str(alt))
        steps = item.get("solution_steps") or []
        if steps and len(steps) > 1:
            bits.append("What you did is step general: the same move works on the next two.")
    else:
        why = None
        for key, text in misc.items():
            if key and key.lower() in a.lower():
                why = str(text)
                break
        if why:
            bits.append(f"Here is the specific trap: {why}")
        elif a:
            bits.append(f"Not yet. '{_shorten(a, 12)}' does not satisfy what the question asks for.")
        else:
            bits.append("Nothing was answered, so this is a free diagnostic: what was the first step you considered?")
        steps = item.get("solution_steps") or []
        if steps:
            first = steps[0].get("text") if isinstance(steps[0], dict) else str(steps[0])
            bits.append(f"Try starting here: {first}")
    if score is not None and 0 < score < 1:
        bits.append(f"Partial credit {score:.0%}: the working counted, the final value did not.")
    return " ".join(bits)


# --------------------------------------------------------------------------- #
# teach-back scoring (rubric coverage, deliberately not writing quality)
# --------------------------------------------------------------------------- #


def teachback_score(text: str, rubric: dict[str, Any] | None) -> dict[str, Any]:
    """Score an explanation against required concepts with synonym sets."""
    rubric = rubric or {}
    concepts: list[dict[str, Any]] = rubric.get("required_concepts") or []
    if not concepts:
        # No rubric: fall back to a coarse length/structure judgement, clearly
        # labelled as such instead of pretending to be a rubric score.
        n_words = len(tokenize(text))
        coverage = min(1.0, n_words / 60)
        return {
            "score": round(0.3 * coverage, 3),
            "coverage": round(coverage, 3),
            "method": "word_count_proxy",
            "missing": [],
            "note": "no rubric on this item, so this is a length proxy and is not used to judge understanding",
            "words": n_words,
        }
    words = set(tokenize(text))
    lower = (text or "").lower()
    matched: list[str] = []
    missing: list[str] = []
    for c in concepts:
        name = str(c.get("concept") or c.get("name") or "")
        needles = [str(x).lower() for x in (c.get("synonyms") or [name])]
        hit = any(n in words or n in lower for n in needles if n)
        (matched if hit else missing).append(name or needles[0])
    score = len(matched) / max(1, len(concepts))
    followup = None
    if missing:
        followup = f"Which step gives you {missing[0]}? Say it out loud and I will check it."
    return {
        "score": round(score, 3),
        "coverage": round(score, 3),
        "method": "rubric_concept_coverage",
        "matched": matched,
        "missing": missing,
        "followup": followup,
        "words": len([w for w in re.split(r"\s+", text or "") if w]),
        "note": "scored on ideas covered, not fluency or spelling",
    }


# --------------------------------------------------------------------------- #
# practice generation
# --------------------------------------------------------------------------- #

TEMPLATE_KEY = re.compile(r"\{(\w+)\}")


def _substitute(template: str, variables: dict[str, Any]) -> str:
    """Format ``{name}`` placeholders from the item's own variable table."""
    if not template:
        return ""
    out = template
    for name in TEMPLATE_KEY.findall(out):
        if name in variables:
            val = variables[name]
            text = f"{val:g}" if isinstance(val, (int, float)) and not isinstance(val, bool) else str(val)
            out = out.replace("{" + name + "}", text)
    return out


def render_variant(item: dict[str, Any], *, rng: random.Random | None = None) -> dict[str, Any]:
    """Generate a fresh version of a parametric item and recompute its answer.

    Items declare ``stem_template`` + ``answer_spec.variables`` (fixed defaults)
    and optionally ``answer_spec.ranges`` (integer ranges). This is the same
    variable/expression model Numbas uses, so an exported item keeps its
    randomisation there and a rendered one stays answerable here.

    Non-parametric items (no formula) are returned with a ``variant_of`` marker
    and otherwise untouched -- inventing an answer for a fixed item would be worse
    than reusing it.
    """
    rng = rng or random.Random()
    spec = dict(item.get("answer_spec") or {})
    formula = spec.get("formula")
    template = item.get("stem_template")
    variables = dict(spec.get("variables") or {})
    ranges = spec.get("ranges") or {}
    if not formula or (not template and not ranges):
        return {**item, "variant_of": item.get("id") or item.get("numbas_ref")}

    sampled: list[str] = []
    for name, bound in ranges.items():
        try:
            lo, hi = float(bound[0]), float(bound[1])
        except (TypeError, ValueError, IndexError):
            continue
        for _ in range(8):
            val = (
                round(rng.uniform(lo, hi), 2)
                if (lo != int(lo) or hi != int(hi))
                else int(rng.randint(int(lo), int(hi)))
            )
            # Avoid degenerate variants (2/2, x+x, ...) by resampling collisions.
            if val in [variables.get(o) for o in sampled]:
                continue
            variables[name] = val
            sampled.append(name)
            break
        else:
            variables[name] = int(rng.randint(int(lo), int(hi)))
            sampled.append(name)
    # A denominator of 0 makes the item meaningless; clamp it away.
    for name, val in list(variables.items()):
        if isinstance(val, (int, float)) and val == 0 and name in ("b", "d", "h"):
            variables[name] = 2

    # Derived variables (e.g. bd = b*d) are recomputed from the sampled ones so a
    # stem can reference them without the author keeping them in sync.
    for name, expr in (spec.get("derived") or {}).items():
        try:
            variables[name] = round(eval_formula(str(expr), dict(variables)), 10)
        except Exception:
            continue
    try:
        answer = eval_formula(str(formula), {k: v for k, v in variables.items()})
    except Exception:
        return {**item, "variant_of": item.get("id") or item.get("numbas_ref")}

    text_vars = dict(variables)
    text_vars["ans"] = round(answer, 6)
    text_vars["answer"] = round(answer, 6)
    stem = _substitute(template or item.get("stem", ""), text_vars)
    out = {
        **item,
        "stem": stem,
        "variant_of": item.get("id") or item.get("numbas_ref"),
        "answer_spec": {**spec, "variables": variables, "value": answer},
        "solution_steps": [
            (
                dict(st, text=_substitute(st.get("text", ""), text_vars))
                if isinstance(st, dict)
                else {"text": _substitute(str(st), text_vars)}
            )
            for st in (item.get("solution_steps") or [])
        ],
        "hints": [_substitute(str(h), text_vars) for h in (item.get("hints") or [])],
    }
    # Distractor text may reference variables or the computed answer too.
    if out.get("choices"):
        out["choices"] = [
            dict(c, text=_substitute(str(c.get("text", "")), text_vars)) for c in out["choices"]
        ]
    return out


SAFE_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "int": int, "float": float}


def eval_formula(formula: str, mapping: dict[str, str]) -> float:
    """Evaluate a restricted arithmetic formula with only the item's variables."""
    allowed = re.compile(r"^[0-9a-zA-Z_+\-*/().,%\s]+$")
    if not allowed.match(formula):
        raise ValueError(f"formula contains disallowed characters: {formula!r}")
    ns: dict[str, Any] = {k: float(v) for k, v in mapping.items()}
    ns.update(SAFE_FUNCS)
    value = eval(formula, {"__builtins__": {}}, ns)  # noqa: S307
    if isinstance(value, bool):
        raise ValueError("formula must not evaluate to a bool")
    return float(value)


def practice_plan(
    *,
    bank: Sequence[dict[str, Any]],
    mastery: dict[str, float],
    due_topics: Sequence[str] = (),
    gap_topics: Sequence[str] = (),
    target_success: float = 0.78,
    count: int = 5,
    directives: Sequence[dict[str, Any]] = (),
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Pick the next ``count`` items: gaps and due reviews first, then the topic
    whose expected success rate is furthest from the target band.
    """
    rng = rng or random.Random()
    dmap = {d.get("strategy") or d.get("action", ""): d for d in directives}
    interleaving = float((dmap.get("interleaving") or {}).get("payload", {}).get("ratio", 0.0) or 0.0)
    need_examples = "worked_examples" in dmap

    def priority(item: dict[str, Any]) -> tuple[float, float]:
        topic = item.get("topic_id", "")
        m = mastery.get(topic, 0.2)
        base = 0.0
        if topic in gap_topics:
            base += 3.0
        if topic in due_topics:
            base += 2.5
        # prefer difficulty nearest target success
        fit = 1.0 - abs((m * (1 - float(item.get("difficulty", 0.5)))) - target_success)
        return (base + fit, rng.random())

    picked: list[dict[str, Any]] = []
    topics_used: list[str] = []
    pool = sorted([i for i in bank if i.get("active", True)], key=priority, reverse=True)
    for item in pool:
        if len(picked) >= count:
            break
        topic = item.get("topic_id", "")
        # Blocking guard: with interleaving enabled, avoid 3 in a row of the same topic.
        if interleaving <= 0 or len(topics_used) < 2 or topics_used[-1] != topics_used[-2] or topics_used[-1] != topic:
            chosen = item
        else:
            alt = next((p for p in pool if p.get("topic_id") != topic and p not in picked), None)
            if alt is None:
                continue
            chosen = alt
            picked.append(render_variant(chosen) | ({"attach_worked_example": need_examples} if need_examples else {}))
            topics_used.append(alt.get("topic_id", ""))
            continue
        v = render_variant(chosen)
        if need_examples:
            v["attach_worked_example"] = True
        picked.append(v)
        topics_used.append(topic)
    return picked


def simplify(text: str, *, max_words: int = 60) -> str:
    """Rewrite-free simplification: keep the highest-information sentences."""
    sents = sentences(text)
    if not sents:
        return _shorten(text, max_words)
    tokens = Counter(tokenize(text))
    scored = sorted(
        ((sum(min(3, tokens.get(t, 0)) for t in tokenize(s)) / max(6, len(tokenize(s))), s) for s in sents),
        key=lambda x: x[0],
        reverse=True,
    )
    out: list[str] = []
    n = 0
    for _, s in scored:
        w = len(s.split())
        if n + w > max_words and out:
            break
        out.append(s)
        n += w
    order = {s: i for i, s in enumerate(sents)}
    return " ".join(sorted(out, key=lambda s: order.get(s, 0)))


def unexpected_request(text: str) -> str | None:
    """Classify the common in-the-moment asks so they are never gated.

    Returns an intent id, or None (which means: treat it as a teaching question).
    """
    t = (text or "").lower()
    rules = (
        ("simpler", ("more simply", "simpler", "explain simply", "eli5", "i don't get it", "confusing")),
        ("shorter", ("shorter", "too long", "brief", "tl;dr")),
        ("another_example", ("another example", "one more example", "different example", "another one")),
        ("check_answer", ("is this right", "check my", "did i get", "correct or not", "mark this")),
        ("next_question", ("next question", "give me one", "practice", "quiz me", "harder", "easier")),
        ("why_me", ("why are you", "why did you", "what did you notice", "why this", "what makes you say")),
        ("show_work", ("show working", "worked example", "steps", "walk me through")),
        ("skip", ("skip", "move on", "next topic", "done with this")),
        ("voice", ("read it", "say it", "speak", "out loud", "listen")),
        ("review", ("revise", "review", "spaced", "come back to", "forgetting")),
    )
    for intent, needles in rules:
        if any(n in t for n in needles):
            return intent
    return None
