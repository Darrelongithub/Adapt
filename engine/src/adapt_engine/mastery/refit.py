"""The nightly cohort refit: fit the priors, or refuse and say why.

AGENT_M1 §1.3 asks for "priors per skill, stored and versioned" and a nightly
cohort refit. Both halves matter, and the refusal matters more:

* **Fitted on a cohort, never per learner.** Fitting four parameters to forty
  trials of one learner reproduces the trials, not the learner. The refit takes
  sequences from many learners and fits the parameters *shared* by them.
* **A cohort has a floor.** Below :data:`MIN_COHORT_LEARNERS` distinct learners,
  or below :data:`MIN_NEW_OBSERVATIONS` observations since the last fit, the
  refit refuses and leaves the previous version serving. A nightly job that
  always writes a new version is a job that turns noise into priors — and, since
  served values carry the version they were computed under, a version written
  from three learners is a version that can never be defended later.
* **The fit is deterministic.** A fixed grid, evaluated in a fixed order, with
  ties broken by grid order. No optimiser that depends on where it started: the
  same cohort must produce the same version, or a golden file cannot pin it.

The grid is small on purpose (300 points). The likelihood is flat over a wide
region for realistic cohort sizes — that flatness is the finding, not a reason
to search harder: more precision would imply a confidence the data does not
have.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection

from adapt_engine.mastery.bkt import Params
from adapt_engine.mastery.service import prior_for

#: Distinct learners a cohort needs before its parameters are worth fitting.
MIN_COHORT_LEARNERS: Final = 20

#: New observations a cohort needs since the last fit. Below this the previous
#: version keeps serving — 200 is the number AGENT_M1 §1.3 states.
MIN_NEW_OBSERVATIONS: Final = 200

P_INIT_GRID: Final = (0.10, 0.20, 0.30, 0.40, 0.50)
P_LEARN_GRID: Final = (0.02, 0.05, 0.10, 0.20, 0.30)
P_GUESS_GRID: Final = (0.05, 0.10, 0.20, 0.30)
P_SLIP_GRID: Final = (0.05, 0.10, 0.20)


@dataclass(frozen=True, slots=True)
class Fit:
    """A fitted parameter set and the likelihood it achieved."""

    params: Params
    log_likelihood: float
    learners: int
    observations: int


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why the cohort was not fitted. Served as a log line, never as silence."""

    reason: str
    learners: int
    observations: int


def log_likelihood(sequences: list[list[bool]], params: Params) -> float:
    """Total log-likelihood of the sequences under the BKT response model.

    The filter is the generative model here: predict with the current belief,
    score the observation, update, then apply the learning transition. Scoring
    after the update would leak the observation into its own prediction.
    """
    total = 0.0
    for sequence in sequences:
        p = params.p_init
        for correct in sequence:
            p_correct = p * (1.0 - params.p_slip) + (1.0 - p) * params.p_guess
            probability = p_correct if correct else 1.0 - p_correct
            total += math.log(max(probability, 1e-12))
            if correct:
                posterior = p * (1.0 - params.p_slip) / p_correct
            else:
                posterior = p * params.p_slip / max(1.0 - p_correct, 1e-12)
            p = posterior + (1.0 - posterior) * params.p_learn
    return total


def fit(
    sequences: list[list[bool]], *, grid: list[tuple[float, float, float, float]] | None = None
) -> Fit | Refusal:
    """Grid-search the parameters on a cohort, or refuse it."""
    learners = len(sequences)
    observations = sum(len(sequence) for sequence in sequences)
    if learners < MIN_COHORT_LEARNERS:
        return Refusal(reason="cohort_too_small", learners=learners, observations=observations)
    if observations < MIN_NEW_OBSERVATIONS:
        return Refusal(reason="too_few_observations", learners=learners, observations=observations)

    points = (
        grid
        if grid is not None
        else [
            (p_init, p_learn, p_guess, p_slip)
            for p_init, p_learn, p_guess, p_slip in product(
                P_INIT_GRID, P_LEARN_GRID, P_GUESS_GRID, P_SLIP_GRID
            )
            if p_guess + p_slip < 1.0
        ]
    )
    best: Fit | None = None
    for p_init, p_learn, p_guess, p_slip in points:
        params = Params(p_init=p_init, p_learn=p_learn, p_guess=p_guess, p_slip=p_slip)
        score = log_likelihood(sequences, params)
        # Strictly greater: ties keep the earlier grid point, so the fit is a
        # function of the cohort and the grid order, never of float luck.
        if best is None or score > best.log_likelihood:
            best = Fit(
                params=params,
                log_likelihood=score,
                learners=learners,
                observations=observations,
            )
    assert best is not None
    return best


def refit_cohort(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    skill: str,
    band: str,
    sequences: list[list[bool]],
    now: datetime,
) -> Fit | Refusal:
    """Fit and store a new prior version, or refuse without writing."""
    result = fit(sequences)
    if isinstance(result, Refusal):
        return result

    prior = prior_for(conn, tenant_id=tenant_id, skill=skill)
    version = prior.version + 1
    conn.execute(
        text(
            """
            INSERT INTO skill_priors
                (tenant_id, skill, version, p_init, p_learn, p_guess, p_slip,
                 source, observations, fitted_at)
            VALUES (:tenant_id, :skill, :version, :p_init, :p_learn, :p_guess,
                    :p_slip, 'cohort_fit', :observations, :now)
            """
        ),
        {
            "tenant_id": tenant_id,
            "skill": skill,
            "version": version,
            "p_init": result.params.p_init,
            "p_learn": result.params.p_learn,
            "p_guess": result.params.p_guess,
            "p_slip": result.params.p_slip,
            "observations": result.observations,
            "now": now,
        },
    )
    conn.execute(
        text(
            """
            INSERT INTO cohort_fits
                (tenant_id, skill, band, prior_version, learners, observations,
                 log_likelihood, fitted_at)
            VALUES (:tenant_id, :skill, :band, :version, :learners, :observations,
                    :log_likelihood, :now)
            """
        ),
        {
            "tenant_id": tenant_id,
            "skill": skill,
            "band": band,
            "version": version,
            "learners": result.learners,
            "observations": result.observations,
            "log_likelihood": result.log_likelihood,
            "now": now,
        },
    )
    return result
