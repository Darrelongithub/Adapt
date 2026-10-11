"""Bayesian Knowledge Tracing.

Standard BKT (Corbett & Anderson 1995) with the four canonical parameters plus a
forgetting transition, implemented exactly twice and tested from both ends:

1. ``BKTParams.update`` -- the online Bayesian update used during a session.
2. ``fit_em`` -- forward-backward EM over many learners' answer sequences, so the
   parameters are *estimated from this deployment's data* instead of being
   copy-pasted from a paper (blueprint §13: research informs the system, measured
   results evaluate it).

Explicitly out of scope: treating ``p_know`` as a fixed ability score. It is a
per-topic, per-time belief that decays (see :mod:`adapt.learning.spacing`) and is
revised whenever new evidence arrives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

EPS = 1e-9
BANDS = ("new", "developing", "proficient", "mastered")


def clamp(x: float, lo: float = 0.001, hi: float = 0.999) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class BKTParams:
    """p_init, p_learn, p_guess, p_slip, p_forget (per attempt opportunity)."""

    p_init: float = 0.20
    p_learn: float = 0.20
    p_guess: float = 0.20
    p_slip: float = 0.10
    p_forget: float = 0.02
    # Beta pseudo-counts used when EM learning from few sequences.
    prior_alpha: float = 2.0
    prior_beta: float = 8.0
    n_observations: int = 0

    def bounds_ok(self) -> bool:
        """Cognitive sanity: guessing must not beat knowing, slipping must be rare."""
        return (
            self.p_guess < 0.7
            and self.p_slip < 0.6
            and (1 - self.p_slip) > self.p_guess
            and 0.01 <= self.p_learn <= 0.95
        )

    def emission(self, know: bool, correct: bool) -> float:
        if know:
            return (1 - self.p_slip) if correct else self.p_slip
        return self.p_guess if correct else (1 - self.p_guess)

    def p_correct(self, p_know: float) -> float:
        return p_know * (1 - self.p_slip) + (1 - p_know) * self.p_guess

    def band(self, p_know: float, *, proficient: float = 0.8, developing: float = 0.55) -> str:
        if p_know >= 0.95:
            return "mastered"
        if p_know >= proficient:
            return "proficient"
        if p_know >= developing:
            return "developing"
        if self.n_observations == 0:
            return "new"
        return "gapped"

    def as_dict(self) -> dict[str, float | int]:
        return {
            "p_init": round(self.p_init, 4),
            "p_learn": round(self.p_learn, 4),
            "p_guess": round(self.p_guess, 4),
            "p_slip": round(self.p_slip, 4),
            "p_forget": round(self.p_forget, 4),
            "n_observations": self.n_observations,
        }


def posterior(p_know: float, correct: bool, params: BKTParams) -> float:
    """P(Knowing | observation) -- the Bayes step, without the learning transition."""
    num = p_know * params.emission(True, correct)
    den = num + (1 - p_know) * params.emission(False, correct)
    return clamp(num / (den + EPS))


def update(p_know: float, correct: bool, params: BKTParams, *, opportunity: bool = True) -> float:
    """Posterior + learning/forgetting transition.

    ``opportunity=False`` means the learner saw the item but did not practice
    (e.g. read an explanation) -- knowledge is assumed unchanged, so we only
    propagate forgetting, not learning.
    """
    post = posterior(p_know, correct, params)
    if not opportunity or not correct:
        # Learning is only credited by an attempt that demonstrated it. The classic
        # formulation applies the flat learning transition after *every* problem,
        # which with default parameters makes p_know rise on a wrong answer
        # (0.200 -> 0.224): repeated failure would slowly look like progress, and
        # the gaps this model exists to find would never open up. Updated-BKT
        # restricts the transition to successes; a miss only decays knowledge.
        return clamp(post * (1 - params.p_forget))
    return clamp(post + (1 - post) * params.p_learn)


def expected_observation_logprob(p_know: float, correct: bool, params: BKTParams) -> float:
    return math.log(p_params_prob(p_know, correct, params) + EPS)


def p_params_prob(p_know: float, correct: bool, params: BKTParams) -> float:
    return params.p_correct(p_know) if correct else 1 - params.p_correct(p_know)


def entropy(p: float) -> float:
    p = clamp(p)
    return -(p * math.log(p) + (1 - p) * math.log(1 - p))


def information_gain(p_know: float, params: BKTParams) -> float:
    """Expected reduction in mastery uncertainty from a single attempt.

    Used for item selection: ask the question whose answer teaches the system the
    most about what the learner knows (adaptive testing rather than fixed order).
    """
    pc = params.p_correct(p_know)
    post_c = update(p_know, True, params)
    post_w = update(p_know, False, params)
    return entropy(p_know) - (pc * entropy(post_c) + (1 - pc) * entropy(post_w))


def sequence_logprob(observations: Sequence[int], params: BKTParams) -> float:
    """log P(answer sequence) via the forward algorithm -- model fit metric."""
    a = params.p_init
    total = 0.0
    for o in observations:
        pc = params.p_correct(clamp(a))
        p_o = pc if o else 1 - pc
        total += math.log(p_o + EPS)
        a = update(a, bool(o), params)
    return total


# --------------------------------------------------------------------------- #
# EM parameter estimation (exact forward-backward)
# --------------------------------------------------------------------------- #


def _transition(params: BKTParams) -> list[list[float]]:
    """A[k][k\'] with state convention 0 = not knowing, 1 = knowing."""
    return [
        [1 - params.p_learn, params.p_learn],  # from not knowing
        [params.p_forget, 1 - params.p_forget],  # from knowing
    ]


def _forward(obs: Sequence[int], params: BKTParams) -> tuple[list[list[float]], list[float]]:
    """Scaled forward table: alpha[t][k] = P(K_t=k | o_1..t), plus normalizers."""
    A = _transition(params)
    T = len(obs)
    alpha = [[0.0, 0.0] for _ in range(T)]
    norms: list[float] = []
    for k in (0, 1):
        # convention: k == 1 means "knowing"
        alpha[0][k] = (params.p_init if k == 1 else 1 - params.p_init) * params.emission(
            k == 1, bool(obs[0])
        )
    s = alpha[0][0] + alpha[0][1] + EPS
    norms.append(s)
    alpha[0][0] /= s
    alpha[0][1] /= s
    for t in range(1, T):
        o = bool(obs[t])
        for k in (0, 1):
            alpha[t][k] = (
                alpha[t - 1][0] * A[0][k] + alpha[t - 1][1] * A[1][k]
            ) * params.emission(k == 1, o)
        s = alpha[t][0] + alpha[t][1] + EPS
        norms.append(s)
        alpha[t][0] /= s
        alpha[t][1] /= s
    return alpha, norms


def _backward(obs: Sequence[int], params: BKTParams) -> list[list[float]]:
    """Scaled backward table: beta[t][k] = P(o_{t+1}..o_T | K_t=k)."""
    A = _transition(params)
    T = len(obs)
    beta = [[1.0, 1.0] for _ in range(T)]
    for t in range(T - 2, -1, -1):
        o = bool(obs[t + 1])
        for k in (0, 1):
            beta[t][k] = sum(
                A[k][j] * params.emission(j == 1, o) * beta[t + 1][j] for j in (0, 1)
            )
        s = beta[t][0] + beta[t][1]
        if s > EPS:
            beta[t][0] /= s
            beta[t][1] /= s
    return beta


@dataclass
class EMState:
    """Sufficient statistics accumulated across learners."""

    learn_num: float = 0.0
    learn_den: float = 0.0
    forget_num: float = 0.0
    forget_den: float = 0.0
    guess_num: float = 0.0
    guess_den: float = 0.0
    slip_num: float = 0.0
    slip_den: float = 0.0
    init_sum: float = 0.0
    init_n: int = 0
    n_seq: int = 0
    obs_n: int = 0


def fit_em(
    sequences: Sequence[Sequence[int]],
    *,
    init: BKTParams | None = None,
    iters: int = 60,
    tol: float = 1e-5,
) -> tuple[BKTParams, dict[str, float | int | str]]:
    """Exact forward-backward EM for the BKT parameters.

    Hidden state per time step: 1 = knowing, 0 = not knowing.
    Transitions: not-knowing -> knowing with ``p_learn``; knowing -> not-knowing
    with ``p_forget``. Emissions: ``p_slip`` (know but wrong) / ``p_guess``
    (not know but right).

    Parameters are shrunk toward a Beta prior so small classes (n = 1 demo
    learner) cannot collapse the model onto 0/1 -- the failure mode that makes
    textbook BKT look broken on tiny data.

    Returns ``(params, diagnostics)``. With fewer than 3 usable sequences the
    input priors are returned untouched and ``diagnostics["skipped"]`` explains
    why, so a fresh deployment still has defensible traces.
    """
    usable = [list(s) for s in sequences if len(s) >= 2]
    params = init or BKTParams()
    diag: dict[str, float | int | str] = {"sequences_used": len(usable), "iterations": 0}
    if len(usable) < 3:
        diag["skipped"] = "insufficient sequences (need >= 3 of length >= 2)"
        return params, diag

    pa, pb = params.prior_alpha, params.prior_beta

    def _est(num: float, den: float, *, a: float, b: float, lo: float, hi: float) -> float:
        return clamp((num + a) / (den + a + b + EPS), lo, hi)

    prev_ll = -math.inf
    ll = 0.0
    converged = False
    for it in range(iters):
        st = EMState()
        ll = 0.0
        for obs in usable:
            T = len(obs)
            alpha, norms = _forward(obs, params)
            beta = _backward(obs, params)
            ll += sum(math.log(n) for n in norms)
            A = _transition(params)

            for t in range(T):
                g_unknown = alpha[t][0] * beta[t][0]
                g_know = alpha[t][1] * beta[t][1]
                sg = g_unknown + g_know + EPS
                g_unknown, g_know = g_unknown / sg, g_know / sg
                if t == 0:
                    st.init_sum += g_know
                    st.init_n += 1
                if obs[t]:
                    st.guess_num += g_unknown
                st.guess_den += g_unknown
                if not obs[t]:
                    st.slip_num += g_know
                st.slip_den += g_know
                st.obs_n += 1

            for t in range(T - 1):
                o = bool(obs[t + 1])
                den = 0.0
                xis = {}
                for k in (0, 1):
                    for j in (0, 1):
                        v = alpha[t][k] * A[k][j] * params.emission(j == 1, o) * beta[t + 1][j]
                        xis[(k, j)] = v
                        den += v
                den += EPS
                st.learn_num += xis[(0, 1)] / den
                st.learn_den += xis[(0, 1)] / den + xis[(0, 0)] / den
                st.forget_num += xis[(1, 0)] / den
                st.forget_den += xis[(1, 1)] / den + xis[(1, 0)] / den

        new = BKTParams(
            p_init=_est(st.init_sum, st.init_n, a=2.0, b=8.0, lo=0.02, hi=0.9),
            p_learn=_est(st.learn_num, st.learn_den, a=pa, b=pb, lo=0.01, hi=0.95),
            p_forget=_est(st.forget_num, st.forget_den, a=1.0, b=20.0, lo=0.0, hi=0.5),
            p_guess=_est(st.guess_num, st.guess_den, a=1.0, b=4.0, lo=0.02, hi=0.7),
            p_slip=_est(st.slip_num, st.slip_den, a=1.0, b=9.0, lo=0.01, hi=0.6),
            prior_alpha=pa,
            prior_beta=pb,
            n_observations=st.obs_n,
        )
        params = new
        delta = ll - prev_ll
        converged = abs(delta) < tol
        diag["log_likelihood"] = round(ll, 5)
        diag["delta"] = round(delta, 7)
        diag["iterations"] = it + 1
        prev_ll = ll
        if converged:
            break

    denom = max(1, sum(len(s) for s in usable))
    diag["mean_logprob"] = round(ll / denom, 5)
    diag["converged"] = bool(converged)
    return params, diag


def naive_mle_params(sequences: Sequence[Sequence[int]]) -> BKTParams:
    """Transparent baseline used to sanity-check EM in the tests."""
    obs = [o for s in sequences for o in s]
    p1 = sum(obs) / max(1, len(obs))
    first = [s[0] for s in sequences if s]
    return BKTParams(
        p_init=sum(first) / max(1, len(first)),
        p_learn=min(0.9, max(0.05, _learn_rate_from_transitions(sequences))),
        p_guess=min(0.65, max(0.05, p1 * 0.5)),
        p_slip=min(0.5, max(0.02, (1 - p1) * 0.5)),
        p_forget=0.02,
    )


def _learn_rate_from_transitions(sequences: Sequence[Sequence[int]]) -> float:
    num = den = 0
    for s in sequences:
        for i in range(1, len(s)):
            if s[i - 1] == 0:
                den += 1
                if s[i] == 1:
                    num += 1
    return num / den if den else 0.2


def mastery_report(
    p_know: float,
    params: BKTParams,
    *,
    half_life_hours: float | None = None,
    hours_since: float | None = None,
) -> dict[str, object]:
    """Human-readable numbers for the dashboard (with uncertainty, not a grade)."""
    p_now = p_know
    decay = 1.0
    if half_life_hours and hours_since and hours_since > 0:
        decay = 0.5 ** (hours_since / max(0.25, half_life_hours))
        p_now = clamp(p_now * decay + (1 - decay) * 0.02)
    return {
        "p_know_observed": round(p_know, 4),
        "p_know_after_decay": round(p_now, 4),
        "expected_recall_now": round(params.p_correct(p_now), 4),
        "band": params.band(p_now),
        "uncertainty": round(entropy(p_now) / math.log(2), 4),  # bits
        "half_life_hours": round(half_life_hours, 2) if half_life_hours else None,
        "decay_factor": round(decay, 4),
    }
