"""The authored topic table: which subject family a topic belongs to, and how much it leans on
each skill (round brief §6.3).

§6.3 is explicit that pre-written ratings exist in code for the common topics and that **the model
only fills gaps**. That order is the whole point: an authored rating is a claim a person made and
can be argued with, a model rating is a sample, and a placement round that changes shape between
two learners who both typed "calculus" is not a measurement instrument.

So this file is the authored half. Ratings are 0–3 (:data:`adapt.subjects.leanings.LEANING_SCALE`),
over the skill ids in :mod:`adapt.subjects.taxonomy`, and they are stored with the taxonomy version
they were written against.

Two authoring rules worth knowing before editing a number:

* **Leave something unselected.** The round tests the top three or four leanings plus one
  cross-check under a time cap, and §6.3 requires every skipped skill to be recorded as
  *not measured* rather than zero. A topic whose ratings are all 3 would test everything, which
  sounds generous and actually means the round measured nothing selectively — and the learner spent
  twice as long. Each table below leaves at least one skill at 1 or 2.
* **A cross-family rating is allowed and is a claim.** ``essay_writing`` really does lean on
  ``hist.argument``: supporting a claim with evidence is the same skill in a language class and a
  civics class. Ratings are validated against the whole skill list, not just the family's, so this
  is expressible — and the plan shows it with the family named, so it does not look like a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .taxonomy import TAXONOMY_VERSION


@dataclass(frozen=True)
class TopicSpec:
    """One authored topic: what it is, which family it sits in, and what it leans on."""

    key: str
    title: str
    #: The subject the learner named it as, in the words a learner would use ("Maths", "Sciences").
    subject: str
    family: str
    #: Free-text spellings that resolve here. Lowercase, no punctuation: :mod:`adapt.subjects.resolve`
    #: normalises before matching, so an alias with a capital or a comma would never be hit.
    aliases: tuple[str, ...] = ()
    #: skill id → 0..3. May name a skill outside ``family`` when the topic genuinely uses it.
    leanings: Mapping[str, int] = field(default_factory=dict)
    #: A code in the shipped curriculum bank (``assessment.bank.TOPICS``) when this topic *is* one,
    #: so the ladder can use the bank's real prerequisite graph and the bank's real items instead of
    #: a parallel invented set.
    bank_code: str | None = None
    #: Which authored prerequisite chain the ladder uses (``curriculum.CHAINS``).
    chain: str | None = None
    #: Which authored method-test pair the pop-up lesson uses (``methodtest.PAIRS``).
    method_pair: str | None = None
    #: A level hint that applies unless the learner's own words say otherwise.
    default_level: str | None = None

    @property
    def considered_skills(self) -> tuple[str, ...]:
        """Every skill a rating may name for this topic, in the order the plan ranks them."""
        return tuple(self.leanings.keys())

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "subject": self.subject,
            "family": self.family,
            "aliases": list(self.aliases),
            "leanings": dict(self.leanings),
            "bank_code": self.bank_code,
            "chain": self.chain,
            "method_pair": self.method_pair,
            "default_level": self.default_level,
            "taxonomy_version": TAXONOMY_VERSION,
        }


#: The eleven topics §6.3 names, plus the aliases a learner actually types. Aliases are how the
#: resolver earns its keep: "differentiation" and "dy/dx" are the same topic as "calculus", and a
#: learner who has to type the app's own word for their subject has already been told the app does
#: not know their subject.
TOPIC_SPECS: tuple[TopicSpec, ...] = (
    TopicSpec(
        key="calculus",
        title="Calculus",
        subject="Maths",
        family="maths",
        aliases=("calculus", "differentiation", "integration", "derivatives", "dy dx", "rates of change", "limits"),
        leanings={
            "maths.number_sense": 2,
            "maths.symbol_fluency": 3,
            "maths.translate_words": 2,
            "maths.logic_chains": 2,
            "maths.pattern_structure": 2,
            "maths.graph_sense": 3,
            "maths.checking": 3,
        },
        chain="calculus",
        method_pair="calculus",
    ),
    TopicSpec(
        key="discrete_maths",
        title="Discrete maths",
        subject="Maths",
        family="maths",
        aliases=(
            "discrete maths",
            "discrete mathematics",
            "combinatorics",
            "graph theory",
            "number theory",
            "sets and logic",
            "boolean algebra",
        ),
        leanings={
            "maths.number_sense": 1,
            "maths.symbol_fluency": 3,
            "maths.translate_words": 2,
            "maths.logic_chains": 3,
            "maths.pattern_structure": 3,
            # A plotted graph and a graph in the graph-theory sense are not the same object, and
            # rating this 2 because the word matches would send the round to measure the wrong thing.
            "maths.graph_sense": 1,
            "maths.checking": 2,
        },
        chain="discrete_maths",
        method_pair="discrete_maths",
    ),
    TopicSpec(
        key="algebra",
        title="Algebra",
        subject="Maths",
        family="maths",
        aliases=("algebra", "equations", "simultaneous equations", "quadratics", "expanding brackets", "factorising", "indices"),
        leanings={
            "maths.number_sense": 2,
            "maths.symbol_fluency": 3,
            "maths.translate_words": 3,
            "maths.logic_chains": 1,
            "maths.pattern_structure": 2,
            "maths.graph_sense": 2,
            "maths.checking": 3,
        },
        # The shipped bank *is* key-stage 3/4 algebra and number work, so the ladder for this topic
        # runs on the bank's own prerequisite graph and its own graded items.
        bank_code="linear_equations",
        chain="algebra",
        method_pair="algebra",
    ),
    TopicSpec(
        key="statistics",
        title="Statistics",
        subject="Maths",
        family="maths",
        aliases=("statistics", "stats", "mean median and mode", "data handling", "averages", "cumulative frequency"),
        leanings={
            "maths.number_sense": 3,
            "maths.symbol_fluency": 1,
            "maths.translate_words": 2,
            "maths.logic_chains": 1,
            "maths.pattern_structure": 1,
            "maths.graph_sense": 3,
            "maths.checking": 2,
        },
        bank_code="statistical_mean",
        chain="statistics",
        method_pair="statistics",
    ),
    TopicSpec(
        key="government",
        title="Government and civics",
        subject="History, humanities and government",
        family="history",
        aliases=(
            "government",
            "civics",
            "civic education",
            "political science",
            "the constitution",
            "how a bill becomes law",
            "separation of powers",
        ),
        leanings={
            "hist.chronology": 1,
            "hist.cause_consequence": 2,
            "hist.source_analysis": 3,
            "hist.argument": 3,
            "hist.perspective": 3,
        },
        chain="government",
        method_pair="government",
    ),
    TopicSpec(
        key="grammar",
        title="Grammar",
        subject="Languages",
        family="languages",
        aliases=("grammar", "english grammar", "parts of speech", "punctuation", "sentence structure", "tenses", "sarufi"),
        leanings={
            "lang.vocabulary_context": 2,
            "lang.grammar_sense": 3,
            "lang.comprehension": 1,
            "lang.flexibility": 2,
            "lang.production": 1,
        },
        chain="grammar",
        method_pair="grammar",
    ),
    TopicSpec(
        key="essay_writing",
        title="Essay writing",
        subject="Languages",
        family="languages",
        aliases=("essay writing", "essays", "composition", "creative writing", "argumentative essay", "insha", "how to write an essay"),
        leanings={
            "lang.vocabulary_context": 1,
            "lang.grammar_sense": 2,
            "lang.comprehension": 2,
            "lang.flexibility": 2,
            "lang.production": 3,
            # Cross-family on purpose: an essay is an argument with evidence, whichever subject it is
            # filed under. The plan names the family when it shows this skill, so it reads as a claim
            # and not as a lookup error.
            "hist.argument": 3,
        },
        chain="essay_writing",
        method_pair="essay_writing",
    ),
    TopicSpec(
        key="biology",
        title="Biology",
        subject="Sciences",
        family="sciences",
        aliases=("biology", "life science", "cells", "ecology", "the human body", "photosynthesis", "genetics"),
        leanings={
            "sci.prediction_models": 2,
            "sci.cause_chains": 3,
            "sci.data_sense": 2,
            "sci.experimental_thinking": 2,
            "sci.scale_sense": 1,
        },
        bank_code="bio_photosynthesis",
        chain="biology",
        method_pair="biology",
    ),
    TopicSpec(
        key="chemistry",
        title="Chemistry",
        subject="Sciences",
        family="sciences",
        aliases=("chemistry", "chemical reactions", "the periodic table", "acids and bases", "moles", "balancing equations"),
        leanings={
            "sci.prediction_models": 2,
            "sci.cause_chains": 3,
            "sci.data_sense": 2,
            "sci.experimental_thinking": 2,
            "sci.scale_sense": 3,
            # Balancing an equation is symbol fluency wearing a lab coat, so this topic leans on a
            # maths skill. It is rated, and the round shows it as coming from the maths set.
            "maths.symbol_fluency": 2,
        },
        chain="chemistry",
        method_pair="chemistry",
    ),
    TopicSpec(
        key="world_history",
        title="World history",
        subject="History, humanities and government",
        family="history",
        aliases=(
            "world history",
            "history",
            "the first world war",
            "ww1",
            "ww2",
            "the second world war",
            "the cold war",
            "colonialism",
            "the industrial revolution",
        ),
        leanings={
            "hist.chronology": 3,
            "hist.cause_consequence": 3,
            "hist.source_analysis": 3,
            "hist.argument": 2,
            "hist.perspective": 2,
        },
        bank_code="history_ww1",
        chain="world_history",
        method_pair="world_history",
    ),
    TopicSpec(
        key="kiswahili",
        title="Kiswahili",
        subject="Languages",
        family="languages",
        aliases=("kiswahili", "swahili", "kiswahili grammar", "ufahamu", "isimu jamii", "sarufi ya kiswahili"),
        leanings={
            "lang.vocabulary_context": 3,
            "lang.grammar_sense": 3,
            "lang.comprehension": 2,
            "lang.flexibility": 2,
            "lang.production": 1,
        },
        chain="kiswahili",
        method_pair="kiswahili",
        default_level="secondary",
    ),
)

_BY_KEY: dict[str, TopicSpec] = {t.key: t for t in TOPIC_SPECS}

#: alias → topic key. Built once; an alias that belongs to two topics would make resolution depend
#: on dictionary order, which is the sort of thing that changes when somebody adds a topic.
_ALIAS_INDEX: dict[str, str] = {}
for _spec in TOPIC_SPECS:
    _ALIAS_INDEX[_spec.key] = _spec.key
    for _alias in _spec.aliases:
        _ALIAS_INDEX[_alias] = _spec.key

AUTHORED_TOPICS: tuple[str, ...] = tuple(t.key for t in TOPIC_SPECS)


def topic_spec(key: str) -> TopicSpec | None:
    return _BY_KEY.get(key)


def spec_for_alias(alias: str) -> TopicSpec | None:
    """The topic an already-normalised alias names, if one does."""
    key = _ALIAS_INDEX.get(alias)
    return _BY_KEY.get(key) if key else None


def aliases() -> dict[str, str]:
    """The whole alias index, for the console and for the resolver's suggestions."""
    return dict(_ALIAS_INDEX)


def validate_topics() -> list[str]:
    """Structural checks on the authored table. Returns problems (empty = valid)."""
    from .taxonomy import SKILL_IDS

    problems: list[str] = []
    for spec in TOPIC_SPECS:
        if not spec.leanings:
            problems.append(f"{spec.key}: has no ratings, so the round would fall back to generic")
        for skill_id, rating in spec.leanings.items():
            if skill_id not in SKILL_IDS:
                problems.append(f"{spec.key}: rates {skill_id!r}, which is not in taxonomy {TAXONOMY_VERSION}")
            if not isinstance(rating, int) or not 0 <= rating <= 3:
                problems.append(f"{spec.key}: rating {rating!r} for {skill_id} is not 0-3")
        if not any(r <= 2 for r in spec.leanings.values()):
            problems.append(
                f"{spec.key}: every skill is rated 3, so the round would test the whole family and skip nothing"
            )
        if spec.chain and spec.chain != spec.key:
            problems.append(f"{spec.key}: chain {spec.chain!r} is not the topic key (one chain per topic)")
    for alias, key in _ALIAS_INDEX.items():
        if key not in _BY_KEY:
            problems.append(f"alias {alias!r} points at {key!r}, which is not a topic")
    return problems


__all__ = [
    "AUTHORED_TOPICS",
    "TOPIC_SPECS",
    "TopicSpec",
    "aliases",
    "spec_for_alias",
    "topic_spec",
    "validate_topics",
]
