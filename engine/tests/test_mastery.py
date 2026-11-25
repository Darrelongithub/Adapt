"""Increment 3: L1 mastery — calibration, abstention, determinism, the guesser.

The acceptance gates, each as a test:

* **RMSE ≤ 0.08 and ≥90% interval coverage** after 40 observations for synthetic
  learners with known mastery (200 per skill, mastery 0.1-0.9). Two generators
  are measured, because the answer depends on which one the doc means — see
  `test_the_noisy_generator_is_reported_not_hidden`.
* **The guesser**: 200 seeded learners whose answers are correct with
  probability 0.2 *regardless of mastery*, for 60 trials. No learner may ever be
  served a mastery of 0.7 or above, at any horizon. This test is G2 and blocks
  release; it is deliberately independent of the RMSE numbers above, because a
  model can be well calibrated on honest learners and still tell a guesser it is
  a master.
* **Abstention below the floor**: eight trials is the floor, and below it the
  endpoint says `insufficient_evidence` with the count that would change its
  answer — never a low-confidence number.
* **Determinism**: the same history and the same clock produce the same bytes,
  and the golden files in `tests/golden/mastery/` pin three sequences exactly.

Every random draw goes through `random.Random(seed)` with the seed in the test,
so a red run is reproducible and a green run is not luck.
"""

from __future__ import annotations

import json
import math
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from adapt_engine.mastery import bkt, refit
from adapt_engine.mastery.bkt import DEFAULT_PARAMS, State, estimate, update
from adapt_engine.mastery.service import Prior, served_body
from tests.conftest import make_settings
from tests.pg import bootstrap_key, provision_tenant

GOLDEN = Path(__file__).parent / "golden" / "mastery"

#: Learners per skill in the calibration test (the gate says at least 200).
LEARNERS = 200

#: Skills in the calibration test.
SKILLS = 3

#: Observations per learner at the measurement point (the gate says 40).
HORIZON = 40

#: Trials a simulated guesser gets, and the number of seeded guessers.
GUESSER_TRIALS = 60
GUESSERS = 200

#: G2's line: no served value at or above this, ever, for a guesser.
MASTERY_LINE = 0.70


# --- the estimator as a pure function -------------------------------------


def _sequence(correct_rate: float, n: int, rng: random.Random) -> list[bool]:
    return [rng.random() < correct_rate for _ in range(n)]


def _fold(sequence: list[bool], params: bkt.Params = DEFAULT_PARAMS) -> State:
    state = State.cold(params)
    for correct in sequence:
        state = update(state, params, correct=correct)
    return state


def test_the_filter_moves_the_belief_the_right_way() -> None:
    """A correct answer raises the filter's belief, a wrong one lowers it."""
    state = State.cold(DEFAULT_PARAMS)
    after_correct = update(state, DEFAULT_PARAMS, correct=True)
    after_wrong = update(state, DEFAULT_PARAMS, correct=False)
    assert after_correct.p > state.p > after_wrong.p


def test_the_filter_saturates_which_is_why_it_is_not_the_served_value() -> None:
    """The BKT belief is not served, and this is the measurement behind that.

    Forty answers at 55% correct — a learner who is barely above guessing —
    drive the filter's belief to near-certainty. The served estimate stays near
    the observed rate. Both facts are asserted so that "the filter saturates" is
    a number in the suite, not a claim in a docstring.
    """
    rng = random.Random(20261117)
    state = _fold(_sequence(0.55, HORIZON, rng))
    assert state.p > 0.95
    served = estimate(state, DEFAULT_PARAMS)
    assert 0.4 < served.value < 0.7


def test_an_estimate_always_lies_inside_its_own_interval() -> None:
    """The point and the interval come from one posterior, so they cannot disagree."""
    rng = random.Random(7)
    for _ in range(200):
        trials = rng.randint(1, 60)
        correct = rng.randint(0, trials)
        state = State(trials=trials, correct=correct, p=rng.random())
        result = estimate(state, DEFAULT_PARAMS)
        assert result.low <= result.value <= result.high
        assert 0.0 < result.value < 1.0


def test_the_same_state_estimates_the_same_value_twice() -> None:
    """No hidden state, no clock, no RNG: a pure function of the counters."""
    state = State(trials=17, correct=11, p=0.42)
    first = estimate(state, DEFAULT_PARAMS)
    second = estimate(state, DEFAULT_PARAMS)
    assert first.as_dict() == second.as_dict()


def test_beta_quantile_is_the_inverse_of_the_cdf() -> None:
    """The interval is only a credible interval if the quantile is the real one.

    Checked by integrating the exact Beta density up to the returned quantile:
    if `beta_quantile` were an approximation, the interval it builds would be a
    number with no distribution behind it.
    """
    for a, b in ((1.0, 1.0), (2.5, 7.0), (12.0, 3.0)):
        log_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
        for q in (0.025, 0.5, 0.975):
            x = bkt.beta_quantile(a, b, q)
            steps = 20_000
            dx = x / steps
            mass = 0.0
            for i in range(steps):
                t = (i + 0.5) * dx
                mass += math.exp((a - 1) * math.log(t) + (b - 1) * math.log(1 - t) - log_beta)
            assert abs(mass * dx - q) < 0.01


# --- gate 1: calibration on synthetic learners -----------------------------


def _calibration(
    generator: str, *, seed: int, horizon: int = HORIZON
) -> tuple[float, float, float]:
    """RMSE, coverage and the largest error over simulated learners.

    ``generator="clean"``: `p(correct) = mastery`.
    ``generator="noisy"``: `p(correct) = m(1-S) + (1-m)G`, the model's own
    response function — a learner at mastery 0 still answers 20% of items
    correctly, by guessing.
    """
    rng = random.Random(seed)
    squared = 0.0
    covered = 0
    worst = 0.0
    total = 0
    for _ in range(SKILLS):
        for _ in range(LEARNERS):
            mastery = rng.uniform(0.1, 0.9)
            if generator == "clean":
                p_correct = mastery
            else:
                p_correct = (
                    mastery * (1 - DEFAULT_PARAMS.p_slip) + (1 - mastery) * DEFAULT_PARAMS.p_guess
                )
            state = _fold(_sequence(p_correct, horizon, rng))
            result = estimate(state, DEFAULT_PARAMS)
            error = result.value - mastery
            squared += error * error
            worst = max(worst, abs(error))
            covered += 1 if result.low <= mastery <= result.high else 0
            total += 1
    return math.sqrt(squared / total), covered / total, worst


def test_rmse_and_coverage_on_synthetic_learners() -> None:
    """THE calibration gate: RMSE ≤ 0.08, coverage ≥ 0.90, 40 observations.

    Measured on the noiseless generator (`p = mastery`), which is the only
    generator on which the 0.08 gate is attainable — see the next test for why
    that is a finding and not an excuse.
    """
    rmse, coverage, worst = _calibration("clean", seed=20261117)
    assert rmse <= 0.08, f"RMSE {rmse:.4f} over {LEARNERS * SKILLS} learners (worst {worst:.3f})"
    assert coverage >= 0.90, f"coverage {coverage:.3f}"


def test_the_noisy_generator_is_reported_not_hidden() -> None:
    """The same gate on the model's *own* response function, where it cannot pass.

    With `p(correct) = m(1-S) + (1-m)G`, forty Bernoullis carry a 70% signal, so
    the Cramer-Rao floor on the standard error is ~0.11: **no estimator** can
    reach 0.08 there. This test does not assert the gate — it pins the honest
    number (0.11) and the reason, so that the flattering figure above can never
    be quoted without it.

    The interval also under-covers here (0.75), for a reason worth naming: the
    estimate is the *observed* rate, which includes lucky guesses, so it is
    biased upward by `(1-m)G` — most at low mastery, which is exactly where the
    line matters. That bias is the price of not applying a guess correction that
    fails on honest learners (DECISIONS §19.2).
    """
    rmse, coverage, _ = _calibration("noisy", seed=20261118)
    assert 0.09 <= rmse <= 0.13, f"noisy RMSE moved to {rmse:.4f}; update DECISIONS §19"
    assert 0.70 <= coverage <= 0.80, f"noisy coverage moved to {coverage:.3f}"


# --- gate 2: the guesser (release blocker) ---------------------------------


def _guesser_run(seed: int = 424242) -> tuple[float, int, list[float]]:
    """Serve a mastery to a learner answering at 0.2 regardless of mastery.

    Returns the largest value ever served (once at or above the floor), the
    number of serves at or above 0.7, and the value at every horizon.
    """
    rng = random.Random(seed)
    worst_value = 0.0
    breaches = 0
    curve: list[float] = []
    for _ in range(GUESSERS):
        state = State.cold(DEFAULT_PARAMS)
        for trial in range(1, GUESSER_TRIALS + 1):
            state = update(state, DEFAULT_PARAMS, correct=rng.random() < 0.2)
            if trial < bkt.EVIDENCE_FLOOR:
                continue
            value = estimate(state, DEFAULT_PARAMS).value
            worst_value = max(worst_value, value)
            breaches += 1 if value >= MASTERY_LINE else 0
            curve.append(value)
    return worst_value, breaches, curve


def test_a_guesser_is_never_served_mastery() -> None:
    """G2, and it blocks release: 200 guessers, 60 trials, no value ≥ 0.7.

    The guard is the shrinkage toward the cohort prior (`PRIOR_STRENGTH = 12`),
    which means a lucky streak buys a few points of belief, not mastery — see
    the red-then-green run in DECISIONS §19.4, where removing it breaches the
    line dozens of times.
    """
    worst, breaches, _curve = _guesser_run()
    assert breaches == 0, f"{breaches} serves at or above {MASTERY_LINE} (worst {worst:.3f})"
    assert worst < MASTERY_LINE, f"worst served value {worst:.3f}"
    # Not merely under the line: under the line by a margin, because the next
    # change to the estimator should trip this before it trips the gate.
    assert worst < 0.65, f"worst served value {worst:.3f} is too close to {MASTERY_LINE}"


def test_the_guesser_curve_does_not_drift_upward_with_trials() -> None:
    """More evidence must not make a guesser look better.

    A guesser's rate is 0.2 forever, so the served value may wobble but must not
    climb: the last horizon's value is not above the first. This is the
    "inflating evidence indistinguishably from a real answer" failure, measured.
    """
    _, _, curve = _guesser_run()
    early = sum(curve[:200]) / 200
    late = sum(curve[-200:]) / 200
    assert late <= early + 0.05, f"served value climbed from {early:.3f} to {late:.3f}"


def test_where_the_guesser_guarantee_stops() -> None:
    """The limit of G2, measured instead of implied.

    The random property test above is the gate; this is the adversarial
    construct that beats it, and it is here so the guarantee is stated with its
    edge attached rather than discovered later as a bug report.

    A guesser whose first eight answers are all correct (probability 2.6e-6 at
    p=0.2) and then answers at its true rate is served **0.743** — above the
    0.7 line — the moment it happens to get three of its next four right (an
    additional ~3e-2, so about 8e-8 per learner). No estimator that also has to
    report a real master as a master can avoid that: eight straight is the same
    evidence as a 100%-correct learner's first eight. What *can* be avoided, and
    is, is the estimator being credulous in the ordinary case — the assertion
    below is a ceiling on the damage, not a pass mark.

    The slope is the honest headline: the estimator reaches 0.7 at eleven
    straight correct (0.7205) and 0.8 at sixteen (0.8012), so a guesser needs a
    run no guesser has.
    """
    rng = random.Random(2026)
    worst = 0.0
    for _ in range(GUESSERS):
        state = State.cold(DEFAULT_PARAMS)
        for _ in range(bkt.EVIDENCE_FLOOR):
            state = update(state, DEFAULT_PARAMS, correct=True)
        worst = max(worst, estimate(state, DEFAULT_PARAMS).value)
        for _ in range(GUESSER_TRIALS - bkt.EVIDENCE_FLOOR):
            state = update(state, DEFAULT_PARAMS, correct=rng.random() < 0.2)
            worst = max(worst, estimate(state, DEFAULT_PARAMS).value)
    assert worst < 0.80, f"the luckiest-start construct now reaches {worst:.3f}"
    assert worst > MASTERY_LINE, "the documented limit no longer breaches; update DECISIONS"


def test_the_streak_curve_is_published_and_asserted() -> None:
    """G2 part 3: the crossings at 0.5, 0.7 and 0.8, as a test and a document.

    `MEASUREMENT.md` §11 publishes this curve; this test is its assertion. A
    buyer can check "eleven straight correct answers before we show 0.7", and a
    learner can understand it, which is why it is the honest form of the
    guarantee rather than a threshold nobody can verify.
    """
    state = State.cold(DEFAULT_PARAMS)
    crossings: dict[float, int] = {}
    previous = 0.0
    for trial in range(1, 41):
        state = update(state, DEFAULT_PARAMS, correct=True)
        served = estimate(state, DEFAULT_PARAMS).value
        for threshold in (0.5, 0.7, 0.8):
            if threshold not in crossings and served >= threshold:
                crossings[threshold] = trial
        previous = served
    assert crossings == {0.5: 4, 0.7: 11, 0.8: 16}, f"curve moved: {crossings}"
    assert previous > 0.9, "forty straight should be served as mastery"


def test_the_cap_fires_only_when_the_evidence_does_not_clear_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap branch: it lowers a value, it never raises one.

    A learner at 30% correct over 50 trials clears `p_guess + GUESS_CLEARANCE`
    on the median, so the cap does not touch them. Raise the clearance and the
    same learner is held at the floor exactly — which is what the branch is for,
    and what a test of the *mechanism* (rather than of the constant) pins.
    """
    state = _fold([True] * 15 + [False] * 35)
    assert state.correct / state.trials == 0.30
    uncapped = estimate(state, DEFAULT_PARAMS).value
    assert uncapped > DEFAULT_PARAMS.p_guess + bkt.GUESS_CLEARANCE

    monkeypatch.setattr(bkt, "GUESS_CLEARANCE", 1.0)
    assert estimate(state, DEFAULT_PARAMS).value == pytest.approx(
        DEFAULT_PARAMS.p_guess + bkt.GUESS_CAP
    )


def test_the_long_run_guesser_is_held_at_the_guessing_floor() -> None:
    """At 60 trials a guesser's evidence is squarely 'consistent with guessing'."""
    state = State(trials=0, correct=0, p=0.5)
    rng = random.Random(99)
    for _ in range(600):
        state = update(state, DEFAULT_PARAMS, correct=rng.random() < 0.2)
    assert state.correct / state.trials == pytest.approx(0.2, abs=0.03)
    value = estimate(state, DEFAULT_PARAMS).value
    assert value <= DEFAULT_PARAMS.p_guess + bkt.GUESS_CAP + 1e-9


# --- abstention -----------------------------------------------------------


def test_the_floor_abstains_and_names_the_count_that_would_change_it() -> None:
    assert bkt.EVIDENCE_FLOOR == 8
    assert bkt.needs_more_evidence(0) == {"trials": 8}
    assert bkt.needs_more_evidence(3) == {"trials": 5}
    assert bkt.needs_more_evidence(7) == {"trials": 1}
    assert bkt.needs_more_evidence(8) is None


# --- golden files (G1) ----------------------------------------------------


#: Twelve-plus goldens, because three are a sample: G1 is only a gate if a
#: changed *distribution* has somewhere to show up. The set covers every branch
#: of `served_body` — three abstentions with different `needs.trials`, served
#: low/mid/high, the guessing cap, the clamp at a long run, the three streak
#: crossings from G2, and the same evidence under two different constructs.
GOLDEN_CASES: list[dict[str, object]] = [
    {"name": "01_abstain_zero_trials", "skill": "skill.fractions", "sequence": []},
    {
        "name": "02_abstain_three_trials",
        "skill": "skill.fractions",
        "sequence": [True, True, False],
    },
    {
        "name": "03_abstain_seven_trials",
        "skill": "skill.fractions",
        "sequence": [True, False, True, True, False, False, True],
    },
    # 3 of 12: the guessing cap fires (mean would be 0.29, capped to 0.25).
    {
        "name": "04_capped_low",
        "skill": "skill.fractions",
        "sequence": [
            True,
            False,
            False,
            True,
            False,
            False,
            False,
            True,
            False,
            False,
            False,
            False,
        ],
    },
    {
        "name": "05_served_low",
        "skill": "skill.fractions",
        "sequence": [
            True,
            False,
            False,
            True,
            False,
            True,
            False,
            False,
            True,
            False,
            False,
            False,
        ],
    },
    {
        "name": "06_served_mid",
        "skill": "skill.fractions",
        "sequence": [True, False, True, True, False, True, False, True, True, False, True, True],
    },
    {
        "name": "07_served_high",
        "skill": "skill.fractions",
        "sequence": ([True] * 24 + [False] * 3 + [True] * 3),
    },
    # 12 of 60: a learner at exactly the guessing rate, long run, near the floor.
    {
        "name": "08_long_run_guesser",
        "skill": "skill.fractions",
        "sequence": [i % 5 == 0 for i in range(60)],
    },
    # 60 straight: through the clamp, well past the 0.8 crossing.
    {"name": "09_saturated_all_correct", "skill": "skill.fractions", "sequence": [True] * 60},
    # The three G2 crossing points, at the served layer rather than the raw one.
    {"name": "10_floor_all_correct", "skill": "skill.fractions", "sequence": [True] * 8},
    {"name": "11_crossing_0_7", "skill": "skill.fractions", "sequence": [True] * 11},
    {"name": "12_crossing_0_8", "skill": "skill.fractions", "sequence": [True] * 16},
    # The same evidence under a second construct: the body must differ only in
    # `skill`, which is what proves the estimate is not per-construct luck.
    {
        "name": "13_second_construct",
        "skill": "skill.ratios",
        "sequence": [True, False, True, True, False, True, False, True, True, False, True, True],
    },
]


def _golden_body(case: dict[str, object]) -> dict[str, object]:
    """The served body for one golden case, through the same pure function.

    `DEFAULT_PARAMS` and the `documented_default` prior are part of the fixture:
    a golden that silently picked up a refitted prior would be pinned to data.
    """
    params = DEFAULT_PARAMS
    state = _fold(list(case["sequence"]), params)  # type: ignore[arg-type]
    prior = Prior(skill=str(case["skill"]), version=1, params=params, source="documented_default")
    return served_body(
        learner_id="learn-golden-0001",
        skill=str(case["skill"]),
        state=state,
        prior=prior,
        tier="core",
    )


def test_golden_files_match_the_estimator() -> None:
    """G1: every branch of the served body, pinned exactly, compared byte-wise.

    Regenerating a golden to make a red run green is the failure mode these
    files exist to prevent: a changed number here is a changed *served* number,
    and it needs a version bump and a sentence in DECISIONS (AGENT_M1 §8 G1).
    """
    assert len(GOLDEN_CASES) >= 10, "G1 needs a set, not a sample"
    for case in GOLDEN_CASES:
        path = GOLDEN / f"{case['name']}.json"
        assert path.exists(), f"missing golden {path}"
        golden = json.loads(path.read_text())
        assert _golden_body(case) == golden["expected"], case["name"]


def test_the_golden_set_covers_every_branch() -> None:
    """The set's *coverage* is asserted, not left to whoever adds case thirteen.

    Three abstentions, three served bands, the cap, the clamp, three streak
    crossings and two constructs — if a later change removes a branch, this test
    says which shape went missing instead of quietly losing the coverage.
    """
    bodies = {case["name"]: _golden_body(case) for case in GOLDEN_CASES}
    abstentions = [b for b in bodies.values() if b.get("abstain")]
    assert {b["needs"]["trials"] for b in abstentions} == {8, 5, 1}  # type: ignore[index]
    served = [b["value"] for b in bodies.values() if "value" in b]
    assert min(served) < 0.4 and max(served) > 0.8
    assert any(
        b.get("value") == bkt.DEFAULT_PARAMS.p_guess + bkt.GUESS_CAP for b in bodies.values()
    )
    assert {str(b["skill"]) for b in bodies.values()} == {"skill.fractions", "skill.ratios"}


# --- HTTP: fold, abstain, isolate -----------------------------------------

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"mas-{uuid.uuid4().hex[:8]}", name="Mastery Test")


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe", "read_model", "admin"))


@pytest.fixture
def client(app_engine: Engine) -> TestClient:
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=10_000),
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def connection(client: TestClient, admin_key):
    resp = client.post(
        "/v1/admin/connections",
        json={
            "host_name": f"host-{uuid.uuid4().hex[:6]}",
            "environment": "test",
            "event_quota_monthly": 250_000,
            # `read_model` as well as `observe`: the host both posts evidence and
            # reads the estimate back, and the read is gated on its own scope.
            "scopes": ["observe", "read_model"],
        },
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _headers(connection, *, key: str | None = None, idem: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key or connection['key']['full_key']}",
        "Idempotency-Key": idem or f"batch-{uuid.uuid4().hex}",
    }


def _post(
    client: TestClient,
    connection,
    events: list[dict[str, object]],
    *,
    idem: str | None = None,
):
    return client.post(
        "/v1/observe", json={"events": events}, headers=_headers(connection, idem=idem)
    )


def _read(client: TestClient, connection, learner_id: str, skill: str):
    return client.get(
        f"/v1/mastery/{skill}",
        params={"learner_id": learner_id},
        headers={"Authorization": f"Bearer {connection['key']['full_key']}"},
    )


def _events(learner_id: str, answers: list[bool], *, skill: str = "skill.fractions"):
    # The session id is derived from (learner, skill), so two calls for the same
    # skill address the same events (which is how the dedupe test asks for a
    # duplicate) and two skills never collide on (learner, session, seq).
    session = f"s-{learner_id[-3:]}-{skill.replace('.', '')}"
    return [
        {
            "learner_id": learner_id,
            "skill": skill,
            "item_id": f"item-{i:03d}",
            "correct": correct,
            "ts": NOW.isoformat(),
            "seq": i,
            "session_id": session,
        }
        for i, correct in enumerate(answers)
    ]


def test_observations_fold_into_a_served_estimate(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    learner = "learn-mastery-001"
    answers = [True, False, True, True, False, True, False, True, True, False]
    resp = _post(client, connection, _events(learner, answers))
    assert resp.status_code == 202, resp.text

    body = _read(client, connection, learner, "skill.fractions").json()
    # INTEGRATION §6B: `n`, an interval *object* with its mass, `shrunk_rate`.
    assert body["n"] == 10
    assert body["estimator"] == "shrunk_rate"
    assert 0.0 < body["value"] < 1.0
    assert body["interval"]["low"] <= body["value"] <= body["interval"]["high"]
    assert body["interval"]["mass"] == bkt.INTERVAL_MASS
    assert body["prior"] == {"source": "cohort_prior", "strength": 12, "version": 1}
    assert body["served"] == "L1" and body["limited_by"] is None

    with tenant_transaction(app_engine, tenant) as conn:
        state = conn.execute(
            text(
                "SELECT trials, correct FROM learner_mastery"
                " WHERE learner_id = :learner AND skill = 'skill.fractions'"
            ),
            {"learner": learner},
        ).one()
    assert (state.trials, state.correct) == (10, 6)


def test_below_the_floor_the_answer_is_a_count_not_a_number(client: TestClient, connection) -> None:
    learner = "learn-mastery-002"
    assert _post(client, connection, _events(learner, [True, True, False])).status_code == 202

    body = _read(client, connection, learner, "skill.fractions").json()
    assert body["abstain"] is True
    assert body["reason"] == "insufficient_evidence"
    assert body["needs"] == {"trials": 5}
    assert body["n"] == 3
    assert "value" not in body, "an abstention must not carry a number to render"


def test_an_unknown_learner_is_a_404_not_a_fabricated_prior(client: TestClient, connection) -> None:
    resp = _read(client, connection, "learn-never-seen", "skill.fractions")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "learner_unknown"
    assert resp.json()["error"]["details"] == {"learner_id": "learn-never-seen"}


def test_a_replayed_batch_does_not_move_mastery(client: TestClient, connection) -> None:
    learner = "learn-mastery-003"
    events = _events(learner, [True, True, True, True, True, True, True, True])
    first = _post(client, connection, events)
    assert first.status_code == 202
    key = first.request.headers["Idempotency-Key"]
    replay = _post(client, connection, events, idem=key)
    assert replay.status_code == 202
    assert replay.json() == first.json()

    body = _read(client, connection, learner, "skill.fractions").json()
    assert body["n"] == 8, "a replay is not evidence"


def test_duplicate_events_in_a_new_batch_are_not_evidence(client: TestClient, connection) -> None:
    learner = "learn-mastery-004"
    events = _events(learner, [True] * 8)
    assert _post(client, connection, events).status_code == 202
    second = _post(client, connection, events)
    assert second.status_code == 202
    assert second.json()["duplicates"] == 8
    assert second.json()["accepted"] == 0

    body = _read(client, connection, learner, "skill.fractions").json()
    assert body["n"] == 8


def test_the_snapshot_lists_every_skill_this_learner_has(client: TestClient, connection) -> None:
    learner = "learn-mastery-005"
    assert (
        _post(
            client,
            connection,
            _events(learner, [True] * 8, skill="skill.fractions")
            + _events(learner, [False] * 2, skill="skill.ratios"),
        ).status_code
        == 202
    )
    body = client.get(
        "/v1/mastery",
        params={"learner_id": learner},
        headers={"Authorization": f"Bearer {connection['key']['full_key']}"},
    ).json()
    assert [row["skill"] for row in body["skills"]] == ["skill.fractions", "skill.ratios"]
    assert body["skills"][1]["abstain"] is True
    assert body["skills"][1]["reason"] == "insufficient_evidence"
    assert body["skills"][1]["needs"] == {"trials": 6}


def test_mastery_is_not_visible_across_tenants(client: TestClient, connection, database) -> None:
    learner = "learn-mastery-006"
    assert _post(client, connection, _events(learner, [True] * 8)).status_code == 202

    other = provision_tenant(database, slug=f"mas-{uuid.uuid4().hex[:8]}", name="Other")
    other_key = bootstrap_key(database, other, scopes=("observe", "read_model", "admin"))
    resp = client.get(
        "/v1/mastery/skill.fractions",
        params={"learner_id": learner},
        headers={"Authorization": f"Bearer {other_key.full_key}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "learner_unknown"


def test_a_bad_skill_name_names_the_field(client: TestClient, connection) -> None:
    resp = _read(client, connection, "learn-mastery-007", "Skill Fractions")
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == {"field": "skill"}


def test_the_same_history_serves_the_same_bytes(client: TestClient, connection) -> None:
    """Determinism end to end: same events, same clock, byte-identical answers."""
    learner = "learn-mastery-008"
    answers = [True, False, True, True, False, True, False, True]
    assert _post(client, connection, _events(learner, answers)).status_code == 202
    first = _read(client, connection, learner, "skill.fractions")
    second = _read(client, connection, learner, "skill.fractions")
    assert first.content == second.content


# --- the refit ------------------------------------------------------------


def _cohort(rng: random.Random, learners: int, trials: int, *, rate: float) -> list[list[bool]]:
    return [[rng.random() < rate for _ in range(trials)] for _ in range(learners)]


def test_the_refit_refuses_a_cohort_below_the_floor() -> None:
    result = refit.fit(_cohort(random.Random(1), 5, 40, rate=0.6))
    assert isinstance(result, refit.Refusal)
    assert result.reason == "cohort_too_small"
    assert result.learners == 5


def test_the_refit_refuses_a_cohort_with_too_few_observations() -> None:
    result = refit.fit(_cohort(random.Random(2), 20, 5, rate=0.6))
    assert isinstance(result, refit.Refusal)
    assert result.reason == "too_few_observations"


def test_the_refit_fits_a_cohort_and_beats_the_default_prior() -> None:
    """A cohort generated at 0.7 must be fitted to a prior nearer 0.7 than 0.3."""
    rng = random.Random(3)
    sequences = _cohort(rng, 60, 20, rate=0.7)
    result = refit.fit(sequences)
    assert isinstance(result, refit.Fit)
    assert result.params.p_init > DEFAULT_PARAMS.p_init
    assert result.log_likelihood > refit.log_likelihood(sequences, DEFAULT_PARAMS)
    # Deterministic: the same cohort fits to the same point.
    again = refit.fit(sequences)
    assert isinstance(again, refit.Fit)
    assert again.params == result.params


def test_the_refit_writes_a_version_and_logs_the_fit(
    database, app_engine: Engine, tenant: uuid.UUID
) -> None:
    sequences = _cohort(random.Random(4), 40, 12, rate=0.65)
    with tenant_transaction(app_engine, tenant) as conn:
        result = refit.refit_cohort(
            conn,
            tenant_id=tenant,
            skill="skill.fractions",
            band="7d",
            sequences=sequences,
            now=NOW,
        )
    assert isinstance(result, refit.Fit), result

    with tenant_transaction(app_engine, tenant) as conn:
        versions = conn.execute(
            text(
                "SELECT version, source FROM skill_priors"
                " WHERE skill = 'skill.fractions' ORDER BY version"
            )
        ).all()
        logged = conn.execute(
            text("SELECT learners, observations FROM cohort_fits WHERE skill = 'skill.fractions'")
        ).one()
    assert [(row.version, row.source) for row in versions] == [
        (1, "documented_default"),
        (2, "cohort_fit"),
    ], "a refit writes a new version, never an UPDATE of the old one"
    assert (logged.learners, logged.observations) == (40, 480)
