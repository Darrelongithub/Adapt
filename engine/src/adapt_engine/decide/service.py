"""`decide` v1: gather the inputs, then evaluate them as a pure function.

The split is the design. :func:`snapshot` reads (mastery state, the learner's
last few answers, the share of their recent trials that tripped a validity flag,
the tenant's tier, the claim ids) and returns a frozen :class:`Inputs`.
:func:`evaluate` takes those inputs plus a clock reading and a decision id and
returns the body — no database, no I/O, no randomness. Everything the product
promises about determinism follows from that signature:

* **Same history + same clock ⇒ byte-identical body.** `evaluate` is a function.
* **Replay means something.** A decision stores its `Inputs`; replay folds the
  snapshot, not today's evidence. Recomputing against live state would give a
  different answer for the honest reason that the learner has answered more
  items since, and a replay test that compared those two would either fail
  constantly or prove nothing.
* **Goldens are exact.** `tests/golden/decisions/` pins `Inputs + clock + id` to
  a body, which is a comparison of functions, not of environments.

The rules themselves are the closed action set of `AGENT_M1.md` §4, and each one
is a statement about the estimate the mastery layer already produced — mastery is
not recomputed here, so the two layers cannot disagree about a learner's number.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from adapt_engine.decide import claims as claim_service
from adapt_engine.decide.constructs import (
    ACTIONS,
    DECIDE_WEIGHT,
    DECISION_TTL_SECONDS,
    DEFAULT_REVIEW_DAYS,
    MAX_FLAGGED_SHARE,
    MAX_INTERVAL_WIDTH,
    TARGET_SUCCESS,
)
from adapt_engine.errors import AdaptError
from adapt_engine.levels import level_name, level_object
from adapt_engine.mastery import bkt
from adapt_engine.mastery.bkt import Params, State
from adapt_engine.mastery.service import MIN_TRIALS, Prior, prior_for, served_body
from adapt_engine.observe import flags as flag_rules
from adapt_engine.tenants import tier_of

#: Validity flags that say "this trial may not be the learner's", rather than
#: merely "this trial happened in an unusual way". Read from the flag module's
#: severity table so the gate and the vocabulary cannot drift: a device change is
#: a context note, `assisted` is recorded as data, and `undeterminable` is an
#: admission that we cannot tell — none of them is a reason to abstain.
QUALITY_FLAGS = flag_rules.UNINFORMATIVE_FLAGS

#: How many of the learner's most recent trials the quality and streak rules look
#: at. Three is the documented streak ("≥3 consecutive errors"), and twenty is
#: enough for a share to mean something without reaching back into old behaviour.
RECENT_TRIALS = 3
QUALITY_SAMPLE = 20


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything `evaluate` needs, and nothing it does not.

    Serialised into `decisions.inputs`, which is what makes a stored decision
    replayable a year later without the learner's current state.
    """

    learner_id: str
    skill: str
    tier: str
    trials: int
    correct: int
    model_p: float
    prior_version: int
    prior_source: str
    params: Params
    recent: tuple[bool, ...]
    flagged_share: float
    claims: dict[str, str | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "skill": self.skill,
            "tier": self.tier,
            "trials": self.trials,
            "correct": self.correct,
            "model_p": self.model_p,
            "prior_version": self.prior_version,
            "prior_source": self.prior_source,
            "params": self.params.as_dict(),
            "recent": list(self.recent),
            "flagged_share": self.flagged_share,
            "claims": dict(sorted(self.claims.items())),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Inputs:
        return cls(
            learner_id=str(raw["learner_id"]),
            skill=str(raw["skill"]),
            tier=str(raw["tier"]),
            trials=int(raw["trials"]),
            correct=int(raw["correct"]),
            model_p=float(raw["model_p"]),
            prior_version=int(raw["prior_version"]),
            prior_source=str(raw["prior_source"]),
            params=Params(**raw["params"]),
            recent=tuple(bool(value) for value in raw["recent"]),
            flagged_share=float(raw["flagged_share"]),
            claims={str(k): (None if v is None else str(v)) for k, v in raw["claims"].items()},
        )


@dataclass(frozen=True, slots=True)
class Outcome:
    """A body, and the facts the row needs that the body does not carry."""

    body: dict[str, Any]
    action: str
    abstain: bool
    reason: str | None
    extra: dict[str, Any] = field(default_factory=dict)


def snapshot(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    learner_id: str,
    skill: str,
) -> Inputs:
    """Read the evidence a decision may rest on. Raises ``LookupError`` if none."""
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
        raise LookupError(learner_id)

    prior: Prior = prior_for(conn, tenant_id=tenant_id, skill=skill)
    # First use on a tenant seeds the claim set. A migration cannot INSERT (the
    # repo rule is schema-only), and a tenant that has never run a job must not
    # be served an uncited recommendation — so seeding is part of the read path,
    # idempotent, and happens inside this transaction.
    claim_service.ensure_seeded(conn, tenant_id=tenant_id)
    recent = _recent_answers(conn, tenant_id=tenant_id, learner_id=learner_id, skill=skill)
    flagged = _flagged_share(conn, tenant_id=tenant_id, learner_id=learner_id, skill=skill)
    cite = {}
    for construct in ("worked_examples", "retrieval_practice", "desirable_difficulty", "spacing"):
        cite[construct] = claim_service.cite(conn, tenant_id=tenant_id, construct=construct)

    return Inputs(
        learner_id=learner_id,
        skill=skill,
        tier=tier_of(conn, tenant_id),
        trials=int(row.trials),
        correct=int(row.correct),
        model_p=float(row.model_p),
        prior_version=int(row.prior_version) or prior.version,
        prior_source=prior.source,
        params=prior.params,
        recent=tuple(recent),
        flagged_share=flagged,
        claims=cite,
    )


def _recent_answers(
    conn: Connection, *, tenant_id: uuid.UUID, learner_id: str, skill: str
) -> list[bool]:
    # The tie-break has to be total. `ts` ties whenever a client sends one
    # timestamp for a batch (our fixtures do) and `id` is a random uuid, so
    # `ORDER BY ts DESC, id DESC` picked a different "last three answers" per
    # tenant from identical input — the metering corpus caught it by abstaining
    # for `conflicting_signals` 0 to 3 times on the same 200 learners.
    rows = conn.execute(
        text(
            """
            SELECT correct
              FROM learning_events
             WHERE tenant_id = :tenant_id AND learner_id = :learner_id AND skill = :skill
             ORDER BY ts DESC, created_at DESC, seq DESC NULLS LAST, id DESC
             LIMIT :limit
            """
        ),
        {
            "tenant_id": tenant_id,
            "learner_id": learner_id,
            "skill": skill,
            "limit": RECENT_TRIALS,
        },
    ).all()
    return [bool(row.correct) for row in reversed(rows)]


def _flagged_share(conn: Connection, *, tenant_id: uuid.UUID, learner_id: str, skill: str) -> float:
    row = conn.execute(
        text(
            """
            WITH recent AS (
                SELECT validity_flags
                  FROM learning_events
                 WHERE tenant_id = :tenant_id AND learner_id = :learner_id AND skill = :skill
                 ORDER BY ts DESC, created_at DESC, seq DESC NULLS LAST, id DESC
                 LIMIT :limit
            )
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE validity_flags && CAST(:flags AS text[])) AS flagged
              FROM recent
            """
        ),
        {
            "tenant_id": tenant_id,
            "learner_id": learner_id,
            "skill": skill,
            "limit": QUALITY_SAMPLE,
            "flags": list(QUALITY_FLAGS),
        },
    ).one()
    total = int(row.n)
    if total == 0:
        return 0.0
    return round(int(row.flagged) / total, 4)


def evaluate(
    inputs: Inputs,
    *,
    now: datetime,
    decision_id: uuid.UUID,
) -> Outcome:
    """The pure core: inputs in, body out.

    Abstractions are checked in the order a reader would want them explained:
    can we serve at this level at all, is there enough evidence, is the evidence
    of good enough quality, is it precise enough to choose between actions, does
    it contradict itself, and only then which of the three actions to take.
    """
    level = level_object(inputs.tier, requested=1)
    series = served_series(inputs)

    # 1. A construct this build cannot serve at L1 is not an abstention about the
    #    learner; it is the construct not existing yet. `unsupported_request`.
    if int(str(level["served"])[1:]) < 1:
        return _abstain(
            inputs,
            now=now,
            decision_id=decision_id,
            level=level,
            series=series,
            reason="unsupported_request",
            needs={"level_required": "L1"},
        )

    # 2. The evidence floor — the same one mastery serves, read from the same
    #    module, so the two endpoints cannot disagree about when a number exists.
    if inputs.trials < MIN_TRIALS:
        return _abstain(
            inputs,
            now=now,
            decision_id=decision_id,
            level=level,
            series=series,
            reason="insufficient_evidence",
            needs={"trials": MIN_TRIALS - inputs.trials},
        )

    # 3. Validity: if enough of the recent trials look like the instrument's
    #    fault, the honest answer is to fix the instrument.
    if inputs.flagged_share > MAX_FLAGGED_SHARE:
        return _abstain(
            inputs,
            now=now,
            decision_id=decision_id,
            level=level,
            series=series,
            reason="data_quality",
            needs={"clean_trials": MIN_TRIALS},
            flag_evidence={
                "uninformative_share": inputs.flagged_share,
                "counted": list(QUALITY_FLAGS),
                "ignored": [name for name in flag_rules.SEVERITY if name not in QUALITY_FLAGS],
            },
        )

    estimate = bkt.estimate(
        bkt.State(trials=inputs.trials, correct=inputs.correct, p=inputs.model_p), inputs.params
    )
    width = estimate.high - estimate.low
    if width > MAX_INTERVAL_WIDTH:
        return _abstain(
            inputs,
            now=now,
            decision_id=decision_id,
            level=level,
            series=series,
            reason="low_confidence",
            needs={"trials": _trials_to_width(inputs, threshold=MAX_INTERVAL_WIDTH)},
        )

    # 4. Contradiction: a confident estimate and a run of answers pointing the
    #    other way. Neither is wrong; they disagree, and acting on either would
    #    be acting on half the evidence.
    if _contradicts(estimate.value, inputs.recent):
        return _abstain(
            inputs,
            now=now,
            decision_id=decision_id,
            level=level,
            series=series,
            reason="conflicting_signals",
            needs={"trials": MIN_TRIALS // 2},
            contributing=["low_confidence"] if width > MAX_INTERVAL_WIDTH / 2 else [],
        )

    # 5. Choose an action, and cite it or abstain trying.
    action, reason, cites = _choose(estimate.value, inputs)
    if cites is None:
        return _abstain(
            inputs,
            now=now,
            decision_id=decision_id,
            level=level,
            series=series,
            reason="unsupported_request",
            needs={"claims": "seeded"},
        )

    return _served(
        inputs,
        now=now,
        decision_id=decision_id,
        level=level,
        series=series,
        action=action,
        reason=reason,
        cites=cites,
        value=estimate.value,
        interval=(estimate.low, estimate.high),
    )


def served_series(inputs: Inputs) -> dict[str, object]:
    """The estimate a body carries, from the same function mastery serves.

    `decide` does not compute its own number: it calls `mastery.service.served_body`
    and reads the value out. Two endpoints that disagreed about a learner's
    mastery would be a bug with a customer-visible face.
    """
    state = State(trials=inputs.trials, correct=inputs.correct, p=inputs.model_p)
    prior = Prior(
        skill=inputs.skill,
        version=inputs.prior_version,
        params=inputs.params,
        source=inputs.prior_source,
    )
    return served_body(
        learner_id=inputs.learner_id,
        skill=inputs.skill,
        state=state,
        prior=prior,
        tier=inputs.tier,
    )


def _contradicts(value: float, recent: tuple[bool, ...]) -> bool:
    """A confident estimate and a run of answers pointing the other way."""
    if len(recent) < RECENT_TRIALS:
        return False
    all_wrong = not any(recent)
    all_right = all(recent)
    return (value >= 0.60 and all_wrong) or (value <= 0.40 and all_right)


def _trials_to_width(inputs: Inputs, *, threshold: float) -> int:
    """How many more trials would tighten the interval to `threshold`.

    Evaluated by continuing the learner's *observed* rate: an honest forecast of
    width, not a promise. Capped at 40 so an abstention never asks for a number
    of trials nobody would reach.
    """
    rate = inputs.correct / inputs.trials if inputs.trials else 0.5
    for extra in range(1, 41):
        trials = inputs.trials + extra
        correct = round(inputs.correct + extra * rate)
        state = State(trials=trials, correct=min(max(correct, 0), trials), p=inputs.model_p)
        estimate = bkt.estimate(state, inputs.params)
        if estimate.high - estimate.low <= threshold:
            return extra
    return 40


def _choose(value: float, inputs: Inputs) -> tuple[str, str, list[str] | None]:
    """Pick the action, its served reason code, and the claims it cites."""
    three_wrong = len(inputs.recent) == RECENT_TRIALS and not any(inputs.recent)

    if three_wrong:
        # AGENT_M1 §4: worked_example when there are ≥3 consecutive errors. The
        # claim's own boundary case is expertise reversal, and `_contradicts`
        # has already caught the high-mastery version of it.
        cite = inputs.claims.get("worked_examples")
        return "worked_example", "consecutive_errors", [cite] if cite else None

    if value >= 0.80:
        cite = inputs.claims.get("spacing")
        return "spaced_review", "high_mastery", [cite] if cite else None

    retrieval = inputs.claims.get("retrieval_practice")
    difficulty = inputs.claims.get("desirable_difficulty")
    cites = [cid for cid in (retrieval, difficulty) if cid]
    if not cites:
        return "next_item_difficulty", "mastery_band_mid", None
    reason = "mastery_band_mid" if value >= 0.40 else "low_mastery"
    return "next_item_difficulty", reason, cites


def _base(
    inputs: Inputs, *, now: datetime, decision_id: uuid.UUID, level: dict[str, object]
) -> dict[str, Any]:
    return {
        "decision_id": str(decision_id),
        "learner_id": inputs.learner_id,
        "skill": inputs.skill,
        "level": level,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=DECISION_TTL_SECONDS)).isoformat(),
    }


def _served(
    inputs: Inputs,
    *,
    now: datetime,
    decision_id: uuid.UUID,
    level: dict[str, object],
    series: dict[str, object],
    action: str,
    reason: str,
    cites: list[str],
    value: float,
    interval: tuple[float, float],
) -> Outcome:
    assert action in ACTIONS and action != "abstain"
    expires = now + timedelta(seconds=DECISION_TTL_SECONDS)
    recommendation: dict[str, Any] = {
        "action": action,
        "confidence": {
            "point": _round(value),
            "interval": {
                "low": _round(interval[0]),
                "high": _round(interval[1]),
                "mass": bkt.INTERVAL_MASS,
            },
        },
        "why": _why(inputs, action=action, value=value, interval=interval),
        "cites": cites,
        "expires_at": expires.isoformat(),
    }
    if action == "next_item_difficulty":
        recommendation["target"] = {
            "target_success": TARGET_SUCCESS,
            "current": _round(value),
            "direction": "harder" if value > TARGET_SUCCESS else "easier",
        }
    if action == "spaced_review":
        # No retention model at L1 (that is L2). The interval is the documented
        # default, cited to the spacing claim, and labelled as a default rather
        # than a prediction.
        recommendation["review_after_days"] = DEFAULT_REVIEW_DAYS
        recommendation["basis"] = "documented_default"

    body = {
        **_base(inputs, now=now, decision_id=decision_id, level=level),
        "action": action,
        "abstain": False,
        "reason_codes": [reason],
        "claim_ids": cites,
        "recommendations": [recommendation],
        # The served estimate travels with the advice, in the mastery shape, so a
        # host never has to make two calls to render one explanation.
        "estimate": {
            "value": series.get("value"),
            "interval": series.get("interval"),
            "n": series.get("n"),
            "estimator": series.get("estimator"),
        },
    }
    return Outcome(body=body, action=action, abstain=False, reason=None)


def _abstain(
    inputs: Inputs,
    *,
    now: datetime,
    decision_id: uuid.UUID,
    level: dict[str, object],
    series: dict[str, object],
    reason: str,
    needs: dict[str, object],
    contributing: list[str] | None = None,
    flag_evidence: dict[str, object] | None = None,
) -> Outcome:
    body = {
        **_base(inputs, now=now, decision_id=decision_id, level=level),
        "action": "abstain",
        "abstain": True,
        "reason": reason,
        "reason_codes": [reason, *(contributing or [])],
        "needs": needs,
        "contributing": contributing or [],
        "estimates": [] if inputs.trials < MIN_TRIALS else [series],
    }
    if flag_evidence is not None:
        body["flag_evidence"] = flag_evidence
    return Outcome(body=body, action="abstain", abstain=True, reason=reason)


def _why(inputs: Inputs, *, action: str, value: float, interval: tuple[float, float]) -> str:
    """A sentence a person can read, built from the estimate and the action.

    Deliberately not generated: the same inputs produce the same sentence, and
    the claim it cites carries the evidence. The number appears with its
    interval, because a point estimate quoted alone is the flattering number.
    """
    band = f"{value:.2f} (95% interval {interval[0]:.2f}-{interval[1]:.2f})"
    if action == "worked_example":
        return (
            f"The last {RECENT_TRIALS} attempts were all wrong, which the worked-example "
            "evidence covers: a novice acquiring a schema learns more from a worked "
            "example than from equivalent problem practice."
        )
    if action == "spaced_review":
        return (
            f"Mastery is {band}, above the band where another immediate trial helps. "
            f"The spacing evidence supports returning to this in {DEFAULT_REVIEW_DAYS} "
            "days rather than now."
        )
    if value >= 0.40:
        return (
            f"Mastery is {band}, in the band where retrieval practice with desirable "
            "difficulty is the best-evidenced next step; aim for items the learner "
            "solves about 78% of the time."
        )
    return (
        f"Mastery is {band}, below the band where harder items help: retrieval practice "
        "on easier items first, so the learner is succeeding often enough to keep going."
    )


# --- the orchestrated call -------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stored:
    body: dict[str, Any]
    decision_id: uuid.UUID
    replayed: bool
    created: bool


def request_hash(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def decide(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID | None,
    learner_id: str,
    skill: str,
    idempotency_key: str,
    body_hash: bytes,
    now: datetime,
    decision_id: uuid.UUID | None = None,
    reserve: Any = None,
    settle: Any = None,
    test_traffic: bool = False,
) -> Stored:
    """Serve or abstain, store the decision, and charge for exactly one of them.

    `reserve` and `settle` are injected (they are the credits service's own
    functions) so this module does not import money. A **served** decision
    reserves one credit and settles it inside the same transaction — two ledger
    rows, one of which (`settle`) is the only kind billing reads. An
    **abstention** writes nothing: no reserve, no settle, no release, because
    nothing was ever held. Reserving after deciding is deliberate — an abstention
    must never be refused for lack of credits, because a host that is out of
    money is exactly the host that most needs to be told to fix its
    instrumentation rather than to buy more.

    `test_traffic` skips the reservation entirely: `X-Adapt-Test: 1` is unbilled
    and excluded from metrics (METERING §3), so it must not touch the ledger at
    all — a zero-credit reservation that is released still writes rows, and
    "zero rows" is the property the increment is asked to prove.
    """
    stored = _find(conn, tenant_id=tenant_id, idempotency_key=idempotency_key)
    if stored is not None:
        if stored.hash != body_hash:
            raise AdaptError(
                code="conflict",
                message=(
                    "that Idempotency-Key was used for a different request body; "
                    "a key is bound to one logical decision"
                ),
                status_code=409,
                retryable=False,
            )
        return Stored(body=stored.body, decision_id=stored.id, replayed=True, created=False)

    inputs = snapshot(conn, tenant_id=tenant_id, learner_id=learner_id, skill=skill)
    identifier = decision_id or uuid.uuid4()
    outcome = evaluate(inputs, now=now, decision_id=identifier)

    # Reserve, settle and insert inside a savepoint. Two requests carrying the
    # same Idempotency-Key can both miss `_find` above and both try to insert;
    # the loser's INSERT blocks on the unique index, then fails. Without the
    # savepoint that failure aborts the request transaction and the caller gets a
    # 500 for doing exactly what the header is for — this was a real double-charge
    # hazard in reverse: a retry storm got an error instead of the stored answer.
    savepoint = conn.begin_nested()
    try:
        reservation_id: uuid.UUID | None = None
        if not outcome.abstain and not test_traffic:
            if reserve is None or settle is None:
                raise RuntimeError("a served decision needs a credit reservation")
            reservation = reserve(
                conn,
                tenant_id=tenant_id,
                amount=1,
                operation_ref=f"decision:{identifier}",
                idempotency_key=f"{idempotency_key}:reserve",
            )
            # Measured work is known the moment the decision exists, so the hold is
            # closed in the same transaction: `reserve` binds the key, `settle` is
            # the row billing reads. A failure anywhere rolls both back.
            settle(
                conn,
                tenant_id=tenant_id,
                reservation_id=reservation.id,
                amount=DECIDE_WEIGHT,
                idempotency_key=f"{idempotency_key}:settle",
            )
            reservation_id = reservation.id

        _insert(
            conn,
            tenant_id=tenant_id,
            identifier=identifier,
            connection_id=connection_id,
            learner_id=learner_id,
            skill=skill,
            outcome=outcome,
            body_hash=body_hash,
            idempotency_key=idempotency_key,
            inputs=inputs,
            now=now,
            reservation_id=reservation_id,
            test_traffic=test_traffic,
        )
    except IntegrityError as exc:
        if _constraint(exc) != "decisions_once_per_key":
            savepoint.rollback()
            raise
        # The other request won: discard our reservation and serve their answer.
        savepoint.rollback()
        existing = _find(conn, tenant_id=tenant_id, idempotency_key=idempotency_key)
        if existing is None:
            raise
        if existing.hash != body_hash:
            raise AdaptError(
                code="conflict",
                message=(
                    "that Idempotency-Key was used for a different request body; "
                    "a key is bound to one logical decision"
                ),
                status_code=409,
                retryable=False,
            ) from exc
        return Stored(body=existing.body, decision_id=existing.id, replayed=True, created=False)
    except BaseException:
        savepoint.rollback()
        raise
    else:
        savepoint.commit()
    return Stored(body=outcome.body, decision_id=identifier, replayed=False, created=True)


def _constraint(exc: IntegrityError) -> str | None:
    """The violated constraint's name, or None when the driver did not say."""
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _insert(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    identifier: uuid.UUID,
    connection_id: uuid.UUID | None,
    learner_id: str,
    skill: str,
    outcome: Any,
    body_hash: bytes,
    idempotency_key: str,
    inputs: Any,
    now: datetime,
    reservation_id: uuid.UUID | None,
    test_traffic: bool,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO decisions
                (tenant_id, id, connection_id, learner_id, skill, action, abstain,
                 reason, request_hash, idempotency_key, inputs, body, issued_at,
                 expires_at, reservation_id, test_traffic)
            VALUES (:tenant_id, :id, :connection_id, :learner_id, :skill, :action,
                    :abstain, :reason, :hash, :key, CAST(:inputs AS jsonb),
                    CAST(:body AS jsonb), :issued_at, :expires_at, :reservation_id,
                    :test_traffic)
            """
        ),
        {
            "tenant_id": tenant_id,
            "id": identifier,
            "connection_id": connection_id,
            "learner_id": learner_id,
            "skill": skill,
            "action": outcome.action,
            "abstain": outcome.abstain,
            "reason": outcome.reason,
            "hash": body_hash,
            "key": idempotency_key,
            "inputs": _json(inputs.as_dict()),
            "body": _json(outcome.body),
            "issued_at": now,
            "expires_at": now + timedelta(seconds=DECISION_TTL_SECONDS),
            "reservation_id": reservation_id,
            "test_traffic": test_traffic,
        },
    )


@dataclass(frozen=True, slots=True)
class StoredDecision:
    id: uuid.UUID
    hash: bytes
    body: dict[str, Any]
    inputs: dict[str, Any]
    issued_at: datetime
    action: str
    abstain: bool


def _find(conn: Connection, *, tenant_id: uuid.UUID, idempotency_key: str) -> StoredDecision | None:
    row = conn.execute(
        text(
            """
            SELECT id, request_hash, body, inputs, issued_at, action, abstain
              FROM decisions
             WHERE tenant_id = :tenant_id AND idempotency_key = :key
            """
        ),
        {"tenant_id": tenant_id, "key": idempotency_key},
    ).one_or_none()
    return None if row is None else _to_stored(row)


def load(
    conn: Connection, *, tenant_id: uuid.UUID, decision_id: uuid.UUID
) -> StoredDecision | None:
    """One stored decision, by id. ``None`` when it does not exist."""
    row = conn.execute(
        text(
            """
            SELECT id, request_hash, body, inputs, issued_at, action, abstain
              FROM decisions
             WHERE tenant_id = :tenant_id AND id = :id
            """
        ),
        {"tenant_id": tenant_id, "id": decision_id},
    ).one_or_none()
    return None if row is None else _to_stored(row)


def _to_stored(row: Any) -> StoredDecision:
    body = row.body if isinstance(row.body, dict) else json.loads(row.body)
    inputs = row.inputs if isinstance(row.inputs, dict) else json.loads(row.inputs)
    return StoredDecision(
        id=uuid.UUID(str(row.id)),
        hash=bytes(row.request_hash),
        body=body,
        inputs=inputs,
        issued_at=row.issued_at,
        action=str(row.action),
        abstain=bool(row.abstain),
    )


def replay(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    now: datetime,
    test_traffic: bool = False,
) -> StoredDecision | None:
    """Recompute a stored decision from its frozen inputs and assert equality.

    Returns the stored decision, with a `replay_identical` fact attached by the
    route. A mismatch is a bug in the estimator's determinism, not a client
    error, so it raises: a replay endpoint that silently returned the stored body
    would be a test that always passes.
    """
    stored = load(conn, tenant_id=tenant_id, decision_id=decision_id)
    if stored is None:
        return None
    inputs = Inputs.from_dict(stored.inputs)
    recomputed = evaluate(inputs, now=stored.issued_at, decision_id=stored.id)
    if recomputed.body != stored.body:
        raise AdaptError(
            code="internal",
            message="stored decision does not match a recomputation from its own inputs",
            status_code=500,
            retryable=True,
        )
    # Logged, not billed: a replay costs nothing, creates no decision and does not
    # touch the decision row — the stored body is the evidence G1 rests on, and
    # evidence that changes after the fact is not evidence. It writes one audit
    # row, which is what an audit trail is for, and that row is what makes
    # `METRICS.md` §1.2's "replays excluded" a measured number.
    conn.execute(
        text(
            "INSERT INTO decision_replays (tenant_id, decision_id, at, test_traffic)"
            " VALUES (:tenant_id, :decision_id, :at, :test_traffic)"
        ),
        {
            "tenant_id": tenant_id,
            "decision_id": decision_id,
            "at": now,
            "test_traffic": test_traffic,
        },
    )
    return stored


def _round(value: float) -> float:
    return round(value, 4)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def action_level(action: str) -> str:
    """The level a body's action is served at, for the log line."""
    return level_name(1) if action in ACTIONS else level_name(0)
