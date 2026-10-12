"""Teaching-strategy catalog + per-learner outcome model.

AI 1's job is to decide *which approach might help this learner next*. Two parts:

1. ``CATALOG`` -- the interventions ADAPT is allowed to try, each with the
   mechanism it exploits, the citation behind it, and the metric it is judged on.
   Strategies are representations/behaviours (worked examples, retrieval,
   spacing, hint fading...), never "visual learner" style labels; see
   :mod:`adapt.learning.profile`, which refuses style labels.
2. ``StrategyModel`` -- a Beta posterior per (learner, strategy) updated with the
   *measured* outcome of past exposure, sampled with Thompson sampling to choose
   the next intervention. Exploration is therefore explicit and bounded rather
   than random, and it stays honest: the posterior records the sample size next to
   the estimate so the UI can say "based on 4 attempts".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np


@dataclass(frozen=True)
class Citation:
    ref: str
    claim: str

    def as_dict(self) -> dict[str, str]:
        return {"ref": self.ref, "claim": self.claim}

    def __str__(self) -> str:  # so a citation can be joined into prose safely
        return self.ref


@dataclass(frozen=True)
class Strategy:
    id: str
    label: str
    family: str  # load_reduction | memory | feedback | motivation | diagnosis
    what: str  # concrete change the app makes
    mechanism: str
    citations: tuple[Citation, ...]
    watch_metric: str  # pre-registered metric used to judge it
    action: str  # machine action id handled by AI 2 / the quiz engine
    payload_defaults: dict[str, object] = field(default_factory=dict)
    caveat: str = ""
    # Whether the change is persistent (needs approval) or in-the-moment.
    persistent: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "what": self.what,
            "mechanism": self.mechanism,
            "watch_metric": self.watch_metric,
            "action": self.action,
            "caveat": self.caveat,
            "persistent": self.persistent,
            "citations": [{"ref": c.ref, "claim": c.claim} for c in self.citations],
        }


_S = Strategy
CATALOG: dict[str, Strategy] = {
    s.id: s
    for s in [
        _S(
            id="worked_examples",
            label="Worked example before the next attempt",
            family="load_reduction",
            what="Show a fully solved, step-labelled example of the same problem type immediately before the question.",
            mechanism="Reduces intrinsic load on first exposure so working memory can be spent on the schema, not on retrieving a procedure that is not there yet.",
            citations=(
                Citation("Sweller & Cooper 1985", "Worked examples beat equivalent practice for novice schema acquisition."),
                Citation("Renkl 2014", "Worked examples help most early; fading them prevents the expertise reversal effect."),
            ),
            watch_metric="accuracy",
            action="insert_worked_example",
            payload_defaults={"steps_before_answer": 1, "fade_after": 3},
            caveat="Kept visible only while accuracy is low; for known-good topics it would slow you down.",
        ),
        _S(
            id="retrieval_practice",
            label="More practice questions, fewer re-reads",
            family="memory",
            what="Replace 'read the explanation again' with a short attempt followed by feedback.",
            mechanism="Retrieval attempts modify memory; testing is a learning event, not just a measurement.",
            citations=(
                Citation("Roediger & Karpicke 2006", "Testing effect: repeated retrieval beats repeated study for later recall."),
                Citation("Karpicke & Blunt 2011", "Retrieval practice outperformed concept mapping on transfer after 1 week."),
            ),
            watch_metric="retention_7d",
            action="increase_retrieval",
            payload_defaults={"questions_per_session": 4, "allow_re_read": False},
            caveat="Feels harder than re-reading while producing better memory; expected.",
        ),
        _S(
            id="spaced_practice",
            label="Space the reviews out",
            family="memory",
            what="Lengthen the interval until the next review of this topic.",
            mechanism="Widening gaps while recall is still successful raises retrieval strength and halves relearning cost.",
            citations=(
                Citation("Cepeda et al. 2006", "Longest gaps that still support recall maximise retention for a given study time."),
                Citation("Settles & Meeder 2016", "Modelled half-lives beat fixed ladders (Duolingo/Anki lineage)."),
            ),
            watch_metric="retention_7d",
            action="adjust_spacing",
            payload_defaults={"target_recall": 0.85, "multiplier": 1.5},
            caveat="If expected recall drops below ~0.6 the interval is too long and we shorten it.",
        ),
        _S(
            id="hint_fading",
            label="Fade the hints",
            family="feedback",
            what="Provide one fewer hint and delay it by a short attempt window.",
            mechanism="Hints that arrive before an attempt become the procedure the learner memorises; fading transfers the retrieval to the learner.",
            citations=(
                Citation("McLaren et al. 2016", "Fading hints outperform always-available help in tutoring systems."),
                Citation("Depper et al. 2023", "Premature hints correlate with near-term, not long-term, gains."),
            ),
            watch_metric="unaided_accuracy",
            action="limit_hints",
            payload_defaults={"max_hints": 1, "delay_s": 25},
            caveat="Only applied when unaided accuracy is far below hinted accuracy.",
        ),
        _S(
            id="segmented_text",
            label="Shorter explanations",
            family="load_reduction",
            what="Split long explanations into short segments with a check after each.",
            mechanism="Segmenting reduces the need to hold earlier sentences in working memory while integrating new ones.",
            citations=(
                Citation("Tabbers et al. 2004", "Segmented instructions lower extraneous load versus continuous text."),
                Citation("Mayer & Anderson 1991", "Segmenting multimedia lessons improves transfer."),
            ),
            watch_metric="accuracy_after_read",
            action="max_stem_words",
            payload_defaults={"max_words": 90, "check_after_segment": True},
            caveat="Some derivations genuinely need length; we keep the full version one click away.",
        ),
        _S(
            id="visual_representation",
            label="Show the structure visually",
            family="load_reduction",
            what="Add a diagram/number-line/quantity bar alongside the text for this topic.",
            mechanism="Dual coding plus an external representation of relations reduces the need to hold them mentally. Chosen because visual items measurably help *this* learner -- not because they were declared a 'visual learner'.",
            citations=(
                Citation("Mayer 2009", "Multimedia principle: words + relevant pictures beat words alone."),
                Citation("Pashler et al. 2008", "No evidence for learning-style matching, so we only use representations that carry information the text lacks."),
            ),
            watch_metric="accuracy_visual_vs_read",
            action="attach_visual",
            payload_defaults={"visual_kind": "auto"},
            caveat="Only offered where a picture is informative (relations, spatial structure); decorative graphics are not evidence-based.",
        ),
        _S(
            id="interleaving",
            label="Mix problem types",
            family="memory",
            what="Interleave neighbouring problem types instead of blocking one type at a time.",
            mechanism="Interleaving forces discrimination between procedures, which hurts practice scores and helps later selection of the right method.",
            citations=(
                Citation("Rohrer & Taylor 2007", "Interleaved practice produced roughly double the test performance after a week."),
                Citation("Carroll 1966", "The blocked-practice / mixed-test effect that motivated the finding."),
            ),
            watch_metric="discrimination_accuracy",
            action="interleave_ratio",
            payload_defaults={"ratio": 0.4},
            caveat="Practice accuracy usually dips first; that is expected and is why we measure the delayed test.",
        ),
        _S(
            id="immediate_feedback",
            label="Feedback right after each answer",
            family="feedback",
            what="Give corrective feedback per item instead of at the end of the quiz.",
            mechanism="Short retention interval between attempt and correction; prevents consolidating an error.",
            citations=(
                Citation("Hattie & Timperley 2007", "Feedback is among the largest documented effects when it addresses the task and process."),
                Citation("Pashler et al. 2005", "Immediate feedback wins for motor/factual recall; delayed can help reasoning -- hence the measurement."),
            ),
            watch_metric="accuracy",
            action="feedback_delay",
            payload_defaults={"delay_s": 0},
            caveat="For multi-step reasoning, a short delay sometimes helps; ADAPT tests both.",
        ),
        _S(
            id="self_explanation",
            label="Teach-back before moving on",
            family="memory",
            what="Ask the learner to explain the idea in their own words after the explanation.",
            mechanism="Self-explanation forces integration with prior knowledge and exposes missing steps.",
            citations=(
                Citation("Chi et al. 1989", "Self-explanation prompts improve gesture/physics learning without extra content."),
                Citation("Dunlosky et al. 2013", "Self-explanation is 'high utility' among common study techniques."),
            ),
            watch_metric="retention_7d",
            action="require_teachback",
            payload_defaults={"after_explanation": True, "min_words": 25},
            caveat="We score the explanation for coverage of key steps, never for eloquence.",
        ),
        _S(
            id="prerequisite_remediation",
            label="Back up to the missing prerequisite",
            family="diagnosis",
            what="Insert a mini-lesson and 3 items on the prerequisite topic before continuing.",
            mechanism="Errors on a dependent skill frequently originate one level down; practicing the dependent skill cannot fix a missing ingredient.",
            citations=(
                Citation("Anderson et al. 2004", "Prerequisite structure in cognitive tutors: lower-level knowledge gates higher-level acquisition."),
                Citation("Biswas et al. 2005", "Metacognitive tutoring on knowledge gaps improves learning in open-ended environments."),
            ),
            watch_metric="dependent_topic_accuracy",
            action="remediate_prerequisite",
            payload_defaults={"max_topics": 1, "items": 3},
            caveat="Proposed only when the prerequisite trace is low AND the errors are on items that need it.",
        ),
        _S(
            id="difficulty_recalibration",
            label="Meet the current level",
            family="motivation",
            what="Shift item difficulty toward the point where expected success is ~70-85%.",
            mechanism="Items near the zone of proximal development maximise information per attempt and avoid both boredom and overload.",
            citations=(
                Citation("Vygotsky 1978", "Zone of proximal development (qualitative, but the target band matches success-rate data)."),
                Citation("Kaplan 1974", "Optimal discrepancy/incongruity produces maximal learning per exposure."),
            ),
            watch_metric="accuracy",
            action="target_success_rate",
            payload_defaults={"target": 0.78},
            caveat="Not a reward schedule: diagnostic and retention probes stay at their own difficulty.",
        ),
        _S(
            id="confidence_calibration",
            label="Calibrate before submitting",
            family="feedback",
            what="Ask for a confidence judgement, then compare it with the result.",
            mechanism="Judgement-of-learning errors drive over/under-study allocation; feedback on the judgement improves calibration.",
            citations=(
                Citation("Bjork, Dunlosky & Kornell 2013", "Self-regulated study depends on calibration; errors here sustain ineffective habits."),
                Citation("Metcalfe & Shimamura 1994", "JOLs guide allocation of study time."),
            ),
            watch_metric="calibration_gap",
            action="ask_confidence",
            payload_defaults={"scale": 4},
            caveat="Optional and skippable; we never treat self-reported confidence as a trait.",
        ),
        _S(
            id="voice_explanation",
            label="Talk it through out loud",
            family="memory",
            persistent=False,
            what="Offer a spoken explanation + spoken Q&A instead of text for this topic.",
            mechanism="Modality change is cheap to try and can help when reading is the bottleneck; the decision is made from measured reading latency, not from a preference label.",
            citations=(
                Citation("Dunlosky et al. 2013", "Highlighting/rereading are low utility; self-testing via talking is higher."),
            ),
            watch_metric="accuracy_after_read",
            action="enable_voice_mode",
            payload_defaults={"auto_speak": True},
            caveat="Available on demand at any time; only *proposed* when reading latency is the measured bottleneck.",
        ),
    ]
}

PERSISTENT_STRATEGIES = tuple(s.id for s in CATALOG.values() if s.persistent)


# Onboarding/settings answers that name a strategy in the catalog. A stated preference
# needs no approval gate -- the learner is the one asking -- but it does need to reach
# the code that honours it, which means arriving as a directive with a payload.
PREFERENCE_STRATEGIES: dict[str, str | None] = {
    "max_explanation_words": "segmented_text",
    "push_me": "retrieval_practice",
}


def directive_for_preference(key: str, value: Any) -> tuple[Strategy, dict[str, Any]] | None:
    """The catalog strategy a stated preference maps to, with the value folded in.

    ``None`` means the preference names no strategy: those ride along as plain
    preferences (AI 2 can honour them, the settings page shows them) instead of being
    dressed up as a teaching change that would not actually happen.
    """
    strategy = CATALOG.get(PREFERENCE_STRATEGIES.get(key, key) or "")
    if strategy is None or value in (False, 0, "off", None, ""):
        return None
    payload = dict(strategy.payload_defaults or {})
    if isinstance(value, dict):
        payload.update(value)
    elif key == "max_explanation_words":
        try:
            payload["max_words"] = max(20, int(value))
        except (TypeError, ValueError):
            return None
    elif key == "push_me":
        payload["allow_re_read"] = False
    return strategy, payload


def get(strategy_id: str) -> Strategy:
    try:
        return CATALOG[strategy_id]
    except KeyError as exc:
        raise KeyError(f"unknown strategy '{strategy_id}'") from exc


def catalog_dicts() -> list[dict[str, object]]:
    return [s.as_dict() for s in CATALOG.values()]


# --------------------------------------------------------------------------- #
# per-learner posteriors
# --------------------------------------------------------------------------- #


@dataclass
class StrategyStat:
    strategy: str
    alpha: float = 1.0
    beta: float = 1.0
    mean_lift: float = 0.0
    lift_var: float = 0.0
    n: int = 0

    @property
    def p_helps(self) -> float:
        """Posterior mean of 'this exposure was net-positive'."""
        return (self.alpha - 1.0) / max(1e-6, (self.alpha - 1.0) + (self.beta - 1.0)) if self.n else 0.5

    @property
    def variance(self) -> float | None:
        """Sample variance of the measured lift (M2 / (n-1))."""
        return None if self.n < 2 else self.lift_var / (self.n - 1)

    @property
    def ci(self) -> tuple[float, float]:
        a, b = self.alpha, self.beta
        m = a / (a + b)
        sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
        return (max(0.0, m - 1.96 * sd), min(1.0, m + 1.96 * sd))

    def as_dict(self) -> dict[str, object]:
        lo, hi = self.ci
        return {
            "strategy": self.strategy,
            "n": self.n,
            "p_helps": round(self.p_helps, 3),
            "ci": [round(lo, 3), round(hi, 3)],
            "mean_lift": round(self.mean_lift, 4),
            "band": band_for(self.mean_lift, self.n),
        }


def band_for(lift: float, n: int) -> str:
    if n == 0:
        return "untested"
    if n < 4:
        return "provisional"
    if lift > 0.03:
        return "helping"
    if lift < -0.03:
        return "not_helping"
    return "neutral"


class StrategyModel:
    """Beta-binomial posterior over 'exposure helped', per learner and strategy."""

    def __init__(self, stats: Iterable[StrategyStat] | None = None) -> None:
        self.stats: dict[str, StrategyStat] = {s.strategy: s for s in (stats or [])}

    def record(self, strategy: str, *, lift: float, helpful: bool | None = None, weight: float = 1.0) -> StrategyStat:
        """Fold one measured exposure into the posterior.

        A continuous ``lift`` becomes a *soft* success: the fractional pseudo-count
        is ``sigmoid(8 * lift)``, so +0.25 accuracy is near-certain evidence of
        help while +0.02 barely moves the posterior. Deterministic on purpose --
        no sampling, no hidden randomness in the learner's model.
        """
        st = self.stats.setdefault(strategy, StrategyStat(strategy=strategy))
        if helpful is None:
            p_help = _sigmoid(lift * 8.0)
            st.alpha += weight * p_help
            st.beta += weight * (1.0 - p_help)
        else:
            st.alpha += weight * (1.0 if helpful else 0.0)
            st.beta += weight * (0.0 if helpful else 1.0)
        # Welford: mean + M2 (sum of squared deviations); variance() below.
        st.n += 1
        delta = lift - st.mean_lift
        st.mean_lift += delta / st.n
        st.lift_var = max(0.0, st.lift_var + delta * (lift - st.mean_lift))
        return st

    def sample(self, *, rng: np.random.Generator | None = None, exclude: Sequence[str] = ()) -> tuple[str, float]:
        """Thompson sampling over allowed strategies, with pessimism for n=0."""
        rng = rng or np.random.default_rng()
        best, best_v = None, -math.inf
        for sid, st in self.stats.items():
            if sid in exclude:
                continue
            v = float(rng.beta(st.alpha, st.beta))
            if v > best_v:
                best, best_v = sid, v
        if best is None:
            options = [s for s in CATALOG if s not in exclude]
            best = options[0] if options else "retrieval_practice"
            best_v = 0.5
        return best, best_v

    def ranked(self, *, exclude: Sequence[str] = ()) -> list[StrategyStat]:
        return sorted(
            (st for sid, st in self.stats.items() if sid not in exclude),
            key=lambda s: (s.p_helps, s.n),
            reverse=True,
        )

    @classmethod
    def from_rows(cls, rows: Iterable[object]) -> StrategyModel:
        return cls(
            [
                StrategyStat(
                    strategy=str(r.strategy),  # type: ignore[attr-defined]
                    alpha=float(r.alpha),  # type: ignore[attr-defined]
                    beta=float(r.beta),  # type: ignore[attr-defined]
                    mean_lift=float(r.mean_lift),  # type: ignore[attr-defined]
                    n=int(r.n),  # type: ignore[attr-defined]
                )
                for r in rows
            ]
        )


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def strategy_for_gap(gap_severity: float, *, has_visual_item: bool = False) -> str:
    """Deterministic fallback when there is not enough data to bandit yet."""
    if gap_severity > 0.6:
        return "prerequisite_remediation"
    if gap_severity > 0.35:
        return "worked_examples"
    if has_visual_item:
        return "visual_representation"
    return "retrieval_practice"
