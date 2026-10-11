"""Hypothesis tracking: turning measurements into *fallible* explanations.

Blueprint section 3 requires a strict separation:

    observed facts  ->  pattern  ->  hypothesis (an explanation, explicitly not a
                     diagnosis)  ->  concrete proposed change  ->  student approval

This module owns the middle two steps. Each hypothesis carries:

* ``statement``     -- phrased as a candidate explanation, in second person, with
                       the numbers inline so nothing has to be taken on trust.
* ``mechanism``     -- the learning-science story behind it.
* ``citations``     -- which evidence the story rests on.
* ``falsification`` -- the observation that would make us drop it. This is what
                       makes it a hypothesis; :mod:`adapt.services.adaptations`
                       checks it after the adaptation runs and can mark the
                       hypothesis ``refuted``.
* ``status`` + Beta counts -- replications move the posterior; refutation is as
                       informative as confirmation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from ..db.models import HypothesisStatus
from .patterns import Observation

# Words ADAPT will not use about a learner. Enforced when rendering, not just in
# review: a proposal that drifts into trait language is refused at construction.
BANNED_PHRASES = (
    "lazy",
    "unmotivated",
    "low ability",
    "not smart",
    "ADHD",
    "dyslexic",
    "learning disabled",
    "attention deficit",
    "gifted",
    "fixed mindset",
    "can't learn",
    "bad at",
    "visual learner",
    "auditory learner",
    "kinesthetic learner",
    "left-brained",
    "right-brained",
    "IQ",
)

STATUS_ORDER = (
    HypothesisStatus.CANDIDATE,
    HypothesisStatus.ACTIVE,
    HypothesisStatus.TESTING,
    HypothesisStatus.SUPPORTED,
    HypothesisStatus.INCONCLUSIVE,
    HypothesisStatus.REFUTED,
    HypothesisStatus.RETIRED,
)


@dataclass(frozen=True)
class HypothesisTemplate:
    detector: str
    strategy: str
    statement: str  # {value} {baseline} {n} {topics} placeholders
    mechanism: str
    citations: tuple[str, ...]
    falsification: str
    alternatives: tuple[str, ...] = ()  # rival explanations we must show the student

    def render(self, obs: Observation) -> str:
        return self.statement.format_map(_SafeMap(obs))


TEMPLATES: dict[str, HypothesisTemplate] = {
    t.detector: t
    for t in [
        HypothesisTemplate(
            detector="worked_example_need",
            strategy="worked_examples",
            statement=(
                "You average {baseline} right after reading a full explanation, but {value} right after "
                "seeing a worked example of the same problem type ({n} questions each). One explanation that "
                "fits: the procedure is easier to copy from a solved example than to reconstruct from prose."
            ),
            mechanism="A worked example removes the need to invent the procedure while also learning the content.",
            citations=("Sweller & Cooper 1985 (worked-example effect)", "Renkl 2014 (example fading)"),
            falsification="If accuracy after examples is not maintained on the next unassisted quiz, the example was doing the thinking, not supporting it.",
            alternatives=("items after examples may have been easier", "reading time, not reading, was the real difference"),
        ),
        HypothesisTemplate(
            detector="hint_dependency",
            strategy="hint_fading",
            statement=(
                "Unaided you are at {baseline}; with a hint you reach {value} ({n} questions). One explanation "
                "that fits: the hint is arriving before you have committed to an attempt, so it is answering "
                "for you more often than it is nudging you."
            ),
            mechanism="Help offered before retrieval turns into a crutch; help offered after an attempt improves retention.",
            citations=("Depper et al. 2023 (hint timing)", "McLaren et al. 2016 (fading hints)"),
            falsification="If unaided accuracy does not rise within ~10 questions, the gap is a knowledge gap, not a hint-timing effect.",
            alternatives=("you may simply prefer checking rather than guessing", "the unaided items were harder"),
        ),
        HypothesisTemplate(
            detector="long_text_overload",
            strategy="segmented_text",
            statement=(
                "Questions with long setup text land at {baseline}; short ones at {value} ({n} questions, median "
                "{words}-word setups). One explanation that fits: the set-up is where the errors start, before any maths."
            ),
            mechanism="Long single-pass text overruns working memory before the task itself begins.",
            citations=("Tabbers et al. 2004 (segmented instructions)", "Mayer & Anderson 1991"),
            falsification="If segmented accuracy is no better on the next 6 items, the text length was not the cause.",
            alternatives=("long-stem items also happen to be the harder topics", "reading effort vs reading skill are not separated by this data"),
        ),
        HypothesisTemplate(
            detector="answer_oscillation",
            strategy="confidence_calibration",
            statement=(
                "When you change an answer twice or more you land at {baseline}; when you commit once you reach "
                "{value} ({n} questions). One explanation that fits: your first judgement is carrying more signal "
                "than your revision does, and there is no external check in between."
            ),
            mechanism="Making an explicit confidence judgement before submitting gives a hook for calibration feedback.",
            citations=("Metcalfe & Shimamura 1994 (JOLs)", "Bjork et al. 2013"),
            falsification="If the change rate is unchanged after 8 items, this was noise in a small sample.",
            alternatives=("changed answers may correlate with genuinely ambiguous items", "time pressure could explain both"),
        ),
        HypothesisTemplate(
            detector="prerequisite_gap",
            strategy="prerequisite_remediation",
            statement=(
                "On {topics} you are at {value} across {n} questions, while the prerequisite we trace underneath it "
                "sits at {baseline}. One explanation that fits: the gap is one level down, so practising the upper "
                "skill is not reaching the cause."
            ),
            mechanism="Dependent skills cannot be consolidated while the ingredient skill is missing.",
            citations=("Anderson et al. 2004 (ACCEL/prerequisite structure)", "Biswas et al. 2005"),
            falsification="If remediating the prerequisite leaves {topics} unchanged, this was not the binding constraint.",
            alternatives=("both topics may be under-taught", "the upper topic may just need more reps"),
        ),
        HypothesisTemplate(
            detector="retention_decay",
            strategy="spaced_practice",
            statement=(
                "Delayed checks came in at {baseline} where our spacing model predicted {value} ({n} reviews). "
                "One explanation that fits: the review gaps are slightly too long for these items."
            ),
            mechanism="Shortening intervals until recall is reliable again restores a workable half-life estimate.",
            citations=("Cepeda et al. 2006 (spacing)", "Settles & Meeder 2016 (half-life regression)"),
            falsification="If recall stays flat at shorter intervals, forgetting is not the binding factor.",
            alternatives=("lapses may cluster on one subtopic", "sleep/study-session timing is not measured here"),
        ),
        HypothesisTemplate(
            detector="slow_start",
            strategy="worked_examples",
            statement=(
                "You spend a median of {seconds}s before the first input on the items you miss, versus near-immediate "
                "starts on the ones you get right ({n} questions). One explanation that fits: the blank step is the "
                "hard part, not the arithmetic that follows it."
            ),
            mechanism="The opening move of a solution is the highest-cost retrieval for a novice.",
            citations=("Renkl 2014 (first-step scaffolding)", "Sweller 1988"),
            falsification="If time-to-first-input returns to baseline after 6 examples, this was not a starting-step problem.",
            alternatives=("deliberate reading time is not distinguishable from stalling in this data",),
        ),
        HypothesisTemplate(
            detector="visual_representation_benefit",
            strategy="visual_representation",
            statement=(
                "On {topics} the version with a diagram ran at {value} against {baseline} on the text-only version "
                "({n} items, variant assigned randomly). One explanation that fits: the relation is easier to see "
                "than to hold in words."
            ),
            mechanism="An external representation of a relation reduces the need to maintain it verbally.",
            citations=("Mayer 2009 (multimedia principle)", "Pashler et al. 2008 (against style matching)"),
            falsification="This is deliberately topic-local: if the diagram stops helping on new items, we stop attaching it.",
            alternatives=("the diagrammed items may be more concrete/harder to misread", "we tested one comparison, so we hold this loosely"),
        ),
        HypothesisTemplate(
            detector="retrieval_over_reread",
            strategy="retrieval_practice",
            statement=(
                "Items you were re-quizzeed on land at {value}; items you re-read land at {baseline} ({n} cases). "
                "One explanation that fits: re-reading feels safer and works less."
            ),
            mechanism="Retrieval strengthens memory; re-reading mostly strengthens familiarity.",
            citations=("Roediger & Karpicke 2006 (testing effect)", "Karpicke & Blunt 2011"),
            falsification="If delayed retention does not improve, practice effects confounded this comparison.",
            alternatives=("students who re-read may differ from those who self-test",),
        ),
        HypothesisTemplate(
            detector="type_confusion",
            strategy="interleaving",
            statement=(
                "{share:.0%} of your errors pick a method belonging to {confusion} rather than the one asked for "
                "({n} errors). One explanation that fits: the individual methods work; telling them apart is the "
                "missing step."
            ),
            mechanism="Interleaving trains discrimination between procedures that blocking does not.",
            citations=("Rohrer & Taylor 2007 (interleaving)", "Carroll 1966"),
            falsification="If mixed practice does not reduce cross-method errors in the next 8 items, this was content confusion.",
            alternatives=("both methods may be incompletely learned",),
        ),
        HypothesisTemplate(
            detector="difficulty_misalignment",
            strategy="difficulty_recalibration",
            statement=(
                "Your recent success rate is {value}; the range where corrections actually stick is around 0.78. "
                "One explanation that fits: the current difficulty is {extra}."
            ),
            mechanism="Items near the edge of current ability carry the most information per attempt.",
            citations=("Vygotsky 1978 (ZPD)", "Kaplan 1974 (optimal discrepancy)"),
            falsification="If accuracy does not move toward the band after recalibration, the difficulty was not the constraint.",
            alternatives=("a low rate can also mean careless speed rather than overload",),
        ),
        HypothesisTemplate(
            detector="teachback_gain",
            strategy="self_explanation",
            statement=(
                "After teach-backs that covered the rubric you hit {value} later; after thin ones {baseline} "
                "({n} pairs). One explanation that fits: explaining is where the gap becomes visible to you."
            ),
            mechanism="Self-explanation surfaces missing steps before they are consolidated as errors.",
            citations=("Chi et al. 1989 (self-explanation)", "Dunlosky et al. 2013"),
            falsification="If coverage improves but later accuracy does not, teaching-to-the-rubric is the mechanism, not learning.",
            alternatives=("stronger prior knowledge predicts both good teach-backs and good scores",),
        ),
    ]
}


@dataclass
class Draft:
    detector: str
    statement: str
    mechanism: str
    citations: list[str]
    falsification: str
    alternatives: list[str]
    strategy: str
    confidence: float
    evidence_ids: list[str] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "statement": self.statement,
            "mechanism": self.mechanism,
            "citations": self.citations,
            "falsification": self.falsification,
            "alternative_explanations": self.alternatives,
            "proposed_strategy": self.strategy,
            "confidence": round(self.confidence, 3),
            "evidence_ids": self.evidence_ids,
            "topic_ids": self.topic_ids,
        }


class _SafeMap(dict):
    """``str.format_map`` helper: unknown placeholders degrade to 'this topic'."""

    def __init__(self, obs: Observation) -> None:
        super().__init__(
            value=f"{obs.value:.0%}" if obs.value <= 1.5 else f"{obs.value:.1f}",
            baseline=f"{obs.baseline:.0%}" if obs.baseline <= 1.5 else f"{obs.baseline:.1f}",
            n=obs.n,
            # The caller resolves topic ids to human titles (AI 1 owns the topic
            # table); cosmetically replacing underscores here is how ids end up
            # looking like prose and stop being replaceable.
            topics=str(obs.facts.get("primary_topic") or ", ".join(obs.topic_ids[:2]) or "this topic"),
            share=obs.facts.get("share_of_errors", 0),
            confusion=obs.facts.get("confused_with_topic", "a neighbouring topic"),
            words=obs.facts.get("long_stem_words_median", 0),
            seconds=obs.facts.get("median_seconds_before_first_input", 0),
            extra="too hard" if obs.facts.get("direction") == "too_hard" else "too easy",
            weak=", ".join(obs.facts.get("weak_prerequisites", [])[:2]) or "the prerequisite",
        )

    def __missing__(self, key: str) -> str:
        return "this topic"


def check_language(text: str) -> list[str]:
    """Return banned phrases found in ``text`` (case-insensitive, word-ish match)."""
    low = text.lower()
    return [p for p in BANNED_PHRASES if p.lower() in low]


def draft(obs: Observation, *, evidence_id: str | None = None) -> Draft | None:
    tpl = TEMPLATES.get(obs.detector)
    if tpl is None:
        return None
    statement = tpl.render(obs)
    # Falsification text may reference the same placeholders -> render them too.
    falsification = tpl.falsification.format_map(_SafeMap(obs))
    banned = check_language(statement) + check_language(falsification)
    if banned:
        # Never emit a trait/diagnosis-flavoured sentence, even from a template bug.
        return None
    prior = 0.3
    confidence = round(min(0.85, prior + 0.55 * obs.strength), 3)
    return Draft(
        detector=obs.detector,
        statement=statement,
        mechanism=tpl.mechanism,
        citations=list(tpl.citations),
        falsification=falsification,
        alternatives=[tpl.alternatives] if isinstance(tpl.alternatives, str) else list(tpl.alternatives),
        strategy=tpl.strategy,
        confidence=confidence,
        evidence_ids=[evidence_id] if evidence_id else [],
        topic_ids=list(obs.topic_ids),
    )


def posterior(supports: int, refutes: int, *, prior_alpha: float = 1.5, prior_beta: float = 3.5) -> float:
    """Beta-binomial posterior mean; the prior is sceptical (0.3) by design."""
    return (prior_alpha + supports) / (prior_alpha + prior_beta + supports + refutes)


def decide(
    *,
    supports: int,
    refutes: int,
    status: str = HypothesisStatus.ACTIVE.value,
    min_replications: int = 3,
) -> HypothesisStatus:
    """Status machine.

    candidate -> active -> testing -> supported | refuted | inconclusive -> retired
    """
    total = supports + refutes
    if total < min_replications:
        return (
            HypothesisStatus.TESTING
            if status in (HypothesisStatus.TESTING.value, "testing")
            else HypothesisStatus.ACTIVE
        )
    a, b = 1.5 + supports, 3.5 + refutes
    p = a / (a + b)
    sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
    lo, hi = p - 1.96 * sd, p + 1.96 * sd
    if lo > 0.6:
        return HypothesisStatus.SUPPORTED
    if hi < 0.4:
        return HypothesisStatus.REFUTED
    return HypothesisStatus.INCONCLUSIVE


def summarize(hypotheses: Sequence[dict[str, object]], *, now: datetime | None = None) -> dict[str, object]:
    """Counts by status, for the dashboard's 'what we think we know' panel."""
    by: dict[str, int] = {}
    for h in hypotheses:
        by[str(h.get("status", "candidate"))] = by.get(str(h.get("status", "candidate")), 0) + 1
    return {
        "total": len(hypotheses),
        "by_status": by,
        "strongest": sorted(hypotheses, key=lambda h: float(h.get("posterior", 0) or 0), reverse=True)[:3],
        "note": "hypotheses are tracked explanations, not conclusions; refuted and inconclusive stay visible on purpose",
    }
