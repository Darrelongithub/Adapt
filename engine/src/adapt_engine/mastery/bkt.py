"""L1 mastery: Bayesian Knowledge Tracing, with the guards the guesser needs.

One number per **(learner, skill)**: the probability the learner has the skill,
updated online as evidence arrives, reported with an interval and an estimator
name — or an abstention when there is not enough evidence to say anything.

## The model

BKT with four parameters per skill: `p_init` (L0), `p_learn` (T), `p_guess`
(G), `p_slip` (S). The filter is the textbook one:

    P(correct) = p(1-S) + (1-p)G
    p(correct | correct) = p(1-S) / P(correct)
    p(correct | wrong)   = pS      / (1-P(correct))
    p ← p + (1-p)T                     (the learning transition)

## Why plain BKT is not enough, and what the two guards are

Plain BKT answers "is this learner guessing?" with a *very* confident "no": with
G=0.2, S=0.1, eight correct answers in a row take mastery from 0.3 to 0.97. A
learner who is clicking at random will produce that streak about once every
400,000 trials — rare per learner, not rare across 200 learners, and **a streak
is indistinguishable from mastery in the data**. The estimate is not wrong about
the likelihood; it is wrong about how much a streak should be believed.

So the served mastery is not the raw filter. It is the posterior mean of a Beta
distribution over the learner's success rate, with two adjustments:

1. **Shrunk toward the cohort prior while evidence is thin**, with a weight that
   *decays* as trials accumulate: `w(n) = K² / (K + n)`, K = 12. At n=8, eight
   correct answers report 0.67 instead of 0.97; at n=40 the prior is worth under
   three observations and the estimate is the data. That decay is what lets one
   rule protect the release gate *and* leave a 40-observation estimate accurate
   to within a few points.
2. **Capped at the guessing floor** when the evidence is consistent with
   guessing. If the posterior median sits below `G + 0.1` — the learner's
   performance does not clear the level a guesser achieves — the served value is
   held at `G + 0.05`. "You might be guessing" is not a reason to report a
   number that says you have mastered it.

The estimate is *not* the BKT filter's belief `p`, and that is deliberate. The
filter saturates: with G=0.2 and S=0.1 a single correct answer is 4.5:1 evidence
of mastery, so a learner answering 55% correctly converges to p≈0.99. The filter
answers "has this learner learned it by now", which is the right question for
*choosing the next item* (increment 4) and the wrong question for *reporting a
calibrated number* — the gate here is calibration (RMSE, coverage), not
filtering. Both are kept: `p` in the stored state, the calibrated estimate in
`estimate()`.

## The two numbers, honestly

On the model's own response function (a learner at mastery m answers correctly
with probability `m(1-S) + (1-m)G`), 40 observations cannot pin m down to better
than about ±0.11: that is the Cramer-Rao floor of 40 Bernoulli trials with a 70%
signal, and no estimator beats it. On a noiseless generator (`p = m`) the floor
is ±0.07, which is where the ≤0.08 gate comes from. Both are measured in
`tests/test_mastery.py` and both are reported — the flattering number and the
honest one.

## What is deliberately not here

There is no per-learner parameter fitting. A single learner's sequence does not
identify four parameters (METERING §1.1), and a job that tried would produce
confident nonsense. The parameters come from the cohort prior, and the nightly
refit replaces them per cohort — never per learner (`mastery/refit.py`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

#: The evidence floor (AGENT_M1 §3). Below this, a construct abstains.
EVIDENCE_FLOOR: Final = 8

#: Shrinkage constant: how many "prior observations" the cohort prior is worth
#: at n=0. `w(n) = K² / (K + n)` — see the module docstring for why it decays
#: rather than staying fixed: a fixed weight would bias every 40-observation
#: estimate by ten points, which the RMSE gate would catch.
PRIOR_STRENGTH: Final = 12.0

#: The interval reported. 95%, and stated as 95% wherever it is served: the gate
#: asks for ≥90% coverage, and a 95% interval that covers ~95% clears it with
#: room, instead of a 90% interval that only just clears it on a lucky seed.
INTERVAL_MASS: Final = 0.95

#: How far above the guessing floor a learner's *posterior median* must sit
#: before the estimate is allowed to exceed the floor at all.
GUESS_CLEARANCE: Final = 0.10

#: The ceiling served to a learner whose evidence does not clear guessing.
GUESS_CAP: Final = 0.05

#: The filter's belief is clamped away from 0 and 1. Not a modelling choice: a
#: long run of correct answers saturates `p` to exactly 1.0 in float, and 1.0 is
#: not a probability the store can hold (`CHECK (model_p > 0 AND model_p < 1)`)
#: — nor one the model means, since a slope of zero would make every later
#: answer unable to move it.
P_CLAMP: Final = 1e-6


@dataclass(frozen=True)
class Params:
    """One skill's BKT parameters. Stored and versioned in `skill_priors`."""

    p_init: float
    p_learn: float
    p_guess: float
    p_slip: float

    def as_dict(self) -> dict[str, float]:
        return {
            "p_init": self.p_init,
            "p_learn": self.p_learn,
            "p_guess": self.p_guess,
            "p_slip": self.p_slip,
        }

    def validated(self) -> Params:
        for name, value in (
            ("p_init", self.p_init),
            ("p_learn", self.p_learn),
            ("p_guess", self.p_guess),
            ("p_slip", self.p_slip),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")
        if self.p_guess + self.p_slip >= 1.0:
            # If guessing plus slipping covers the whole range the two states are
            # not distinguishable from the responses, and mastery is not
            # identifiable at all.
            raise ValueError("p_guess + p_slip must be < 1")
        return self


#: The documented default, used when a skill has no prior row yet. Deliberately
#: close to the literature's mid-range values and pessimistic about mastery:
#: `p_init` 0.3 means "a new learner probably does not have it".
DEFAULT_PARAMS: Final = Params(p_init=0.30, p_learn=0.10, p_guess=0.20, p_slip=0.10)


#: The estimator's name, served with every value. A number whose provenance is a
#: version string can be compared across a change; a bare float cannot.
ESTIMATOR: Final = "shrunk_rate"


@dataclass(frozen=True)
class State:
    """What we store per (learner, skill): the filter's belief and its evidence."""

    p: float
    trials: int
    correct: int

    @classmethod
    def cold(cls, params: Params) -> State:
        return cls(p=params.p_init, trials=0, correct=0)


def update(state: State, params: Params, *, correct: bool) -> State:
    """One observation through the filter, then the learning transition."""
    p = min(max(state.p, 0.0), 1.0)
    p_correct = p * (1.0 - params.p_slip) + (1.0 - p) * params.p_guess
    if correct:
        posterior = p * (1.0 - params.p_slip) / p_correct if p_correct > 0 else p
    else:
        denominator = 1.0 - p_correct
        posterior = p * params.p_slip / denominator if denominator > 0 else p
    learned = posterior + (1.0 - posterior) * params.p_learn
    return State(
        p=min(max(learned, P_CLAMP), 1.0 - P_CLAMP),
        trials=state.trials + 1,
        correct=state.correct + (1 if correct else 0),
    )


def fold(state: State, params: Params, outcomes: list[bool]) -> State:
    """Apply a sequence of outcomes in order. Order matters; folding does not lie about it."""
    for correct in outcomes:
        state = update(state, params, correct=correct)
    return state


def observed_rate(state: State) -> float:
    """The learner's success rate over every trial we have counted."""
    if state.trials == 0:
        return 0.0
    return state.correct / state.trials


def prior_weight(trials: int) -> float:
    """How many observations the cohort prior is worth after ``trials``."""
    return (PRIOR_STRENGTH * PRIOR_STRENGTH) / (PRIOR_STRENGTH + max(trials, 0))


@dataclass(frozen=True)
class Estimate:
    """A served mastery estimate: point, interval, and how to read it."""

    value: float
    low: float
    high: float
    trials: int
    estimator: str = "bkt_beta_v1"

    def as_dict(self) -> dict[str, object]:
        return {
            "value": round(self.value, 6),
            "interval": [round(self.low, 6), round(self.high, 6)],
            "trials": self.trials,
            "estimator": self.estimator,
        }


def beta_quantile(a: float, b: float, q: float) -> float:
    """Quantile of Beta(a, b) by bisection on the regularised incomplete beta.

    A bisection rather than an inverse-beta implementation: it is twenty lines,
    it cannot silently disagree with the CDF used to check it, and 200
    iterations of a monotone function is microseconds.
    """
    if not 0.0 < q < 1.0:
        raise ValueError("q must be in (0, 1)")
    low, high = 0.0, 1.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if _beta_cdf(mid, a, b) < q:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _beta_cdf(x: float, a: float, b: float) -> float:
    """Regularised incomplete beta I_x(a, b), by its continued fraction."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _continued_fraction(x, a, b) / a
    return 1.0 - front * _continued_fraction(1.0 - x, b, a) / b


def _continued_fraction(x: float, a: float, b: float) -> float:
    """Lentz's algorithm for the beta continued fraction."""
    tiny = 1e-300
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1.0)
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for m in range(1, 300):
        m2 = 2 * m
        coefficient = m * (b - m) * x / ((a + m2 - 1.0) * (a + m2))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        coefficient = -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1.0))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return result


def estimate(state: State, params: Params) -> Estimate:
    """The served value: shrunk, guess-capped, with a credible interval.

    The returned `value` is the mean of the Beta posterior, so the point and the
    interval always agree — a value outside its own interval is the classic
    symptom of two numbers computed by two different rules.
    """
    a, b = _posterior_counts(state, params)
    mean = a / (a + b)
    cap = params.p_guess + GUESS_CAP
    if mean > cap and beta_quantile(a, b, 0.5) < params.p_guess + GUESS_CLEARANCE:
        mean = cap
    tail = (1.0 - INTERVAL_MASS) / 2.0
    low = beta_quantile(a, b, tail)
    high = beta_quantile(a, b, 1.0 - tail)
    return Estimate(
        value=mean,
        low=min(low, mean),
        high=max(high, mean),
        trials=state.trials,
    )


def _posterior_counts(state: State, params: Params) -> tuple[float, float]:
    """Beta counts for (estimate, interval), from evidence and the cohort prior."""
    weight = prior_weight(state.trials)
    observed = observed_rate(state)
    prior = params.p_init
    successes = 1.0 + weight * prior + state.trials * observed
    failures = 1.0 + weight * (1.0 - prior) + state.trials * (1.0 - observed)
    return successes, failures


def needs_more_evidence(trials: int) -> dict[str, int] | None:
    """The abstention payload, or ``None`` when the floor is met."""
    if trials >= EVIDENCE_FLOOR:
        return None
    return {"trials": EVIDENCE_FLOOR - trials}
