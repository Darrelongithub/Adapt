"""Mastery: fold evidence into state, serve an estimate or abstain.

Two operations, deliberately separate:

* :func:`fold` runs **inside the ingest transaction**. The counters it writes are
  the same facts the events record, derived once, so a replay (which inserts no
  events) cannot move mastery and a rolled-back batch cannot leave a counter
  ahead of its evidence.
* :func:`estimate_for` is a **read**. It loads the state and the prior version it
  was folded under, and returns either a calibrated estimate or an abstention
  that says how many more trials would change the answer.

The abstention is the interesting one. Below :data:`MIN_TRIALS` observations the
endpoint does not return a number with a wide interval — it returns
``{"reason": "insufficient_evidence", "needs": {"trials": ...}}``, because a
consumer that gets a number will use the number, and "0.31 ± 0.4" reads as "31%"
to every dashboard that renders it. The count is what makes the abstention
*actionable*: it is the answer to "what would I have to do to get one".

Everything here is a pure function of (stored counters, stored prior, clock).
No RNG, no ambient time: the same history and the same clock produce the same
bytes, which is what lets the golden files in `tests/golden/` be exact.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from adapt_engine.levels import flat_level_axes
from adapt_engine.mastery import bkt
from adapt_engine.mastery.bkt import Params, State
from adapt_engine.tenants import tier_of

#: Trials below which the endpoint abstains instead of reporting a number.
#: Eight is the floor from AGENT_M1 §1.3 ("at least 8 observations before a
#: mastery value is available"), and it is also roughly where an 8-trial
#: interval stops being wider than the whole possible range.
MIN_TRIALS: int = bkt.EVIDENCE_FLOOR

#: The `source` reported with a cold-start estimate: the cohort prior, because
#: no cohort refit has produced a fitted version for this skill yet.
COLD_START_SOURCE: str = "cohort_prior"


@dataclass(frozen=True, slots=True)
class Prior:
    """A stored prior version, with the parameters the estimator needs."""

    skill: str
    version: int
    params: Params
    source: str


def prior_for(conn: Connection, *, tenant_id: uuid.UUID, skill: str) -> Prior:
    """The newest stored prior for this skill, creating the default if absent.

    Version 1 is the documented default and is written on first use rather than
    seeded by a migration, because the set of skills is open: a migration cannot
    write a row for a skill nobody has observed yet.
    """
    row = conn.execute(
        text(
            """
            SELECT version, p_init, p_learn, p_guess, p_slip, source
              FROM skill_priors
             WHERE tenant_id = :tenant_id AND skill = :skill
             ORDER BY version DESC
             LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "skill": skill},
    ).one_or_none()
    if row is None:
        default = bkt.DEFAULT_PARAMS
        conn.execute(
            text(
                """
                INSERT INTO skill_priors
                    (tenant_id, skill, version, p_init, p_learn, p_guess, p_slip, source)
                VALUES (:tenant_id, :skill, 1, :p_init, :p_learn, :p_guess, :p_slip,
                        'documented_default')
                ON CONFLICT (tenant_id, skill, version) DO NOTHING
                """
            ),
            {
                "tenant_id": tenant_id,
                "skill": skill,
                "p_init": default.p_init,
                "p_learn": default.p_learn,
                "p_guess": default.p_guess,
                "p_slip": default.p_slip,
            },
        )
        return Prior(skill=skill, version=1, params=default, source="documented_default")
    return Prior(
        skill=skill,
        version=int(row.version),
        params=Params(
            p_init=float(row.p_init),
            p_learn=float(row.p_learn),
            p_guess=float(row.p_guess),
            p_slip=float(row.p_slip),
        ),
        source=str(row.source),
    )


@dataclass(frozen=True, slots=True)
class Trial:
    """One observation, reduced to what the fold needs."""

    learner_id: str
    skill: str
    correct: bool
    ts: datetime
    seq: int | None
    item_id: str

    @property
    def order(self) -> tuple[datetime, int, str]:
        """A total order over a learner's trials.

        The filter belief is order-dependent, so an arbitrary order would make
        two replays of the same history disagree. Sorting by (time, seq, item)
        is a function of the evidence alone: no arrival order, no dict order.
        """
        return (self.ts, -1 if self.seq is None else self.seq, self.item_id)


def _state_for(
    conn: Connection, *, tenant_id: uuid.UUID, learner_id: str, skill: str
) -> tuple[State, int]:
    row = conn.execute(
        text(
            """
            SELECT prior_version, trials, correct, model_p
              FROM learner_mastery
             WHERE tenant_id = :tenant_id AND learner_id = :learner_id AND skill = :skill
            """
        ),
        {"tenant_id": tenant_id, "learner_id": learner_id, "skill": skill},
    ).one_or_none()
    if row is None:
        return State(trials=0, correct=0, p=0.5), 0
    return (
        State(trials=int(row.trials), correct=int(row.correct), p=float(row.model_p)),
        int(row.prior_version),
    )


def fold(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    trials: list[Trial],
    now: datetime,
) -> int:
    """Advance each learner's state by their trials. Returns rows written.

    Only the *fresh* trials of a batch are passed in: a duplicate is not
    evidence, and counting it would raise mastery with no answer behind it.
    """
    if not trials:
        return 0

    by_pair: dict[tuple[str, str], list[Trial]] = {}
    for trial in trials:
        by_pair.setdefault((trial.learner_id, trial.skill), []).append(trial)

    rows: list[dict[str, Any]] = []
    for (learner_id, skill), group in sorted(by_pair.items()):
        state, stored_version = _state_for(
            conn, tenant_id=tenant_id, learner_id=learner_id, skill=skill
        )
        prior = prior_for(conn, tenant_id=tenant_id, skill=skill)
        # A refit mid-history does not rewrite the past: the state keeps folding
        # under the version it started with until the next refit re-folds it.
        version = stored_version or prior.version
        params = prior.params
        for trial in sorted(group, key=lambda t: t.order):
            state = bkt.update(state, params, correct=trial.correct)
        rows.append(
            {
                "tenant_id": tenant_id,
                "learner_id": learner_id,
                "skill": skill,
                "prior_version": version,
                "trials": state.trials,
                "correct": state.correct,
                "model_p": state.p,
                "updated_at": now,
            }
        )

    conn.execute(
        text(
            """
            INSERT INTO learner_mastery
                (tenant_id, learner_id, skill, prior_version, trials, correct,
                 model_p, updated_at)
            VALUES (:tenant_id, :learner_id, :skill, :prior_version, :trials,
                    :correct, :model_p, :updated_at)
            ON CONFLICT (tenant_id, learner_id, skill) DO UPDATE
               SET prior_version = EXCLUDED.prior_version,
                   trials        = EXCLUDED.trials,
                   correct       = EXCLUDED.correct,
                   model_p       = EXCLUDED.model_p,
                   updated_at    = EXCLUDED.updated_at
            """
        ),
        rows,
    )
    return len(rows)


def needs_more_evidence(trials: int) -> dict[str, int] | None:
    """The abstention body for a learner below the floor, else ``None``.

    The floor lives in the estimator module, so the endpoint and the model can
    never disagree about when the model is allowed to speak.
    """
    return bkt.needs_more_evidence(trials)


def estimate_for(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    learner_id: str,
    skill: str,
) -> dict[str, Any]:
    """One learner's mastery on one skill, or an abstention.

    Raises :class:`LookupError` when the learner has no state for the skill, so
    the route can turn "we have never heard of them" into a 404 instead of a
    fabricated 0.5.
    """
    state, version = _state_for(conn, tenant_id=tenant_id, learner_id=learner_id, skill=skill)
    if version == 0:
        raise LookupError(learner_id)
    return _served(conn, tenant_id=tenant_id, learner_id=learner_id, skill=skill, state=state)


def snapshot(conn: Connection, *, tenant_id: uuid.UUID, learner_id: str) -> list[dict[str, Any]]:
    """Every skill this learner has evidence on, in skill order."""
    rows = conn.execute(
        text(
            """
            SELECT skill, prior_version, trials, correct, model_p
              FROM learner_mastery
             WHERE tenant_id = :tenant_id AND learner_id = :learner_id
             ORDER BY skill
            """
        ),
        {"tenant_id": tenant_id, "learner_id": learner_id},
    ).all()
    if not rows:
        raise LookupError(learner_id)
    out: list[dict[str, Any]] = []
    for row in rows:
        state = State(trials=int(row.trials), correct=int(row.correct), p=float(row.model_p))
        out.append(
            _served(
                conn,
                tenant_id=tenant_id,
                learner_id=learner_id,
                skill=str(row.skill),
                state=state,
            )
        )
    return out


def served_body(
    *,
    learner_id: str,
    skill: str,
    state: State,
    prior: Prior,
    tier: str,
) -> dict[str, Any]:
    """The INTEGRATION §6B body: an estimate, or an abstention with no `value`.

    Pure — no connection, no clock — because a golden file has to be able to
    pin it and because the abstention branch is then testable without a database.
    The one rule that matters most is the absence: an abstention carries no
    `value` key at all, so a host cannot render a number we declined to give.
    """
    axes = flat_level_axes(tier)
    needs = needs_more_evidence(state.trials)
    if needs is not None:
        return {
            "learner_id": learner_id,
            "skill": skill,
            "abstain": True,
            "reason": "insufficient_evidence",
            "needs": needs,
            "n": state.trials,
            **axes,
        }
    estimate = bkt.estimate(state, prior.params)
    return {
        "learner_id": learner_id,
        "skill": skill,
        "value": _round(estimate.value),
        "interval": {
            "low": _round(estimate.low),
            "high": _round(estimate.high),
            "mass": bkt.INTERVAL_MASS,
        },
        "estimator": bkt.ESTIMATOR,
        "prior": {
            "source": COLD_START_SOURCE if prior.source == "documented_default" else "fitted",
            "strength": int(bkt.PRIOR_STRENGTH),
            "version": prior.version,
        },
        "n": estimate.trials,
        **axes,
    }


def _served(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    learner_id: str,
    skill: str,
    state: State,
) -> dict[str, Any]:
    prior = prior_for(conn, tenant_id=tenant_id, skill=skill)
    return served_body(
        learner_id=learner_id,
        skill=skill,
        state=state,
        prior=prior,
        tier=tier_of(conn, tenant_id),
    )


def _round(value: float) -> float:
    """Four decimals: enough to pin a golden file, not enough to imply precision."""
    return round(value, 4)
