"""The measuring instruments: BKT, spacing, effect size, detectors, gaps.

These are the only places a number becomes a claim about a learner, so they are
tested against properties rather than golden strings: monotonicity, saturation,
"no data means no conclusion", and "one observation is not a pattern".
"""

from __future__ import annotations

from datetime import datetime

import pytest


# --------------------------------------------------------------------------- #
# BKT
# --------------------------------------------------------------------------- #


def test_bkt_moves_in_the_right_direction():
    from adapt.learning.bkt import BKTParams, update

    p = BKTParams()
    up = update(p.p_init, True, p)
    down = update(p.p_init, False, p)
    assert up > p.p_init > down
    # A correct answer after a wrong one still moves up: the model has no memory of
    # the sequence other than through p_know itself.
    assert update(down, True, p) > down


def test_bkt_stays_inside_probability_bounds_under_extremes():
    from adapt.learning.bkt import BKTParams, update

    p = BKTParams(p_slip=0.0, p_guess=0.0, p_learn=0.95)
    value = 0.5
    for _ in range(40):
        value = update(value, True, p)
    assert 0.0 < value <= 1.0 and value > 0.99
    for _ in range(40):
        value = update(value, False, p)
    assert 0.0 <= value < 0.02


def test_posterior_of_a_known_stronger_learner_is_higher():
    from adapt.learning.bkt import BKTParams, posterior

    p = BKTParams()
    assert posterior(0.9, True, p) > posterior(0.3, True, p)
    assert posterior(0.9, False, p) > posterior(0.3, False, p)  # one slip does not erase mastery


def test_entropy_and_information_gain_peak_where_they_should():
    from adapt.learning.bkt import BKTParams, entropy, information_gain

    assert entropy(0.5) > entropy(0.9) > entropy(1.0) - 1e-9
    p = BKTParams()
    mid = information_gain(0.5, p)
    assert mid > information_gain(0.95, p)
    assert mid > information_gain(0.05, p)


def test_band_thresholds_are_ordered_and_distinguish_new_from_gapped():
    from adapt.learning.bkt import BKTParams

    fresh = BKTParams()
    seen = BKTParams(n_observations=4)
    assert fresh.band(0.1) == "new", "no evidence yet"
    assert seen.band(0.1) == "gapped", "evidence of failure is a different claim from no evidence"
    assert seen.band(0.6) == "developing"
    assert seen.band(0.85) == "proficient"
    assert seen.band(0.96) == "mastered"
    for p in (0.0, 0.3, 0.7, 1.0):
        assert fresh.band(p) in {"new", "gapped", "developing", "proficient", "mastered"}


def test_em_needs_data_and_says_so():
    from adapt.learning.bkt import BKTParams, fit_em

    params = BKTParams()
    out, diag = fit_em([[1, 0]], init=params)
    assert diag.get("skipped"), "one short sequence must not move the published parameters"
    assert out == params


def test_em_recovers_a_learning_rate_from_synthetic_knows():
    """Fit on data generated *by* the model: recovery within tolerance is the claim.

    The generator below moves a hidden ``know`` state (learning after every
    opportunity) -- the **classic** rule -- so the fit must be asked for that rule
    explicitly. ``fit_em`` defaults to ``model="online"`` because that is what
    ``update()`` does at runtime; see ``test_learner_model_consistency.py``, which
    pins the estimator to the live filter and measures what mixing the two costs.
    """
    import random

    from adapt.learning.bkt import BKTParams, fit_em

    truth = BKTParams(p_init=0.15, p_learn=0.35, p_guess=0.2, p_slip=0.1, p_forget=0.02)
    rng = random.Random(7)
    sequences = []
    for _ in range(140):
        know = rng.random() < truth.p_init
        row = []
        for _step in range(6):
            emission = (rng.random() > truth.p_slip) if know else (rng.random() < truth.p_guess)
            row.append(1 if emission else 0)
            if not know and rng.random() < truth.p_learn:
                know = True
            elif know and rng.random() < truth.p_forget:
                know = False
        sequences.append(row)

    fitted, diag = fit_em(sequences, init=BKTParams(), iters=120, model="classic")
    assert abs(fitted.p_learn - truth.p_learn) < 0.15, fitted
    assert abs(fitted.p_guess - truth.p_guess) < 0.15, fitted
    assert abs(fitted.p_slip - truth.p_slip) < 0.15, fitted
    assert fitted.bounds_ok()
    assert diag["iterations"] >= 2 and "mean_logprob" in diag


def test_naive_mle_is_a_safe_fallback():
    from adapt.learning.bkt import naive_mle_params

    all_correct = naive_mle_params([[1, 1, 1, 1], [1, 1, 1, 1]])
    all_wrong = naive_mle_params([[0, 0, 0, 0], [0, 0, 0, 0]])
    assert all_correct.p_learn >= all_wrong.p_learn
    assert all_correct.bounds_ok() and all_wrong.bounds_ok()


def test_mastery_report_shows_decay_and_uncertainty():
    from adapt.learning.bkt import BKTParams, mastery_report

    fresh = mastery_report(0.8, BKTParams())
    aged = mastery_report(0.8, BKTParams(), half_life_hours=24.0, hours_since=72.0)
    assert aged["p_know_after_decay"] < fresh["p_know_after_decay"]
    assert fresh["half_life_hours"] is None
    assert 0.0 <= aged["uncertainty"] <= 1.0


# --------------------------------------------------------------------------- #
# spacing
# --------------------------------------------------------------------------- #


def test_expected_recall_is_a_halving_function():
    from adapt.learning.spacing import expected_recall

    assert expected_recall(24.0, 0.0) == pytest.approx(1.0)
    assert expected_recall(24.0, 24.0) == pytest.approx(0.5)
    assert expected_recall(24.0, 48.0) == pytest.approx(0.25)
    assert expected_recall(24.0, 12.0) > expected_recall(24.0, 36.0)


def test_lag_for_target_is_the_inverse_relation():
    from adapt.learning.spacing import expected_recall, lag_for_target

    lag = lag_for_target(30.0, 0.85)
    assert expected_recall(30.0, lag) == pytest.approx(0.85, abs=1e-6)
    assert lag_for_target(30.0, 0.95) < lag_for_target(30.0, 0.75)


def test_a_survived_recall_means_a_longer_half_life_than_a_lapse():
    """The direction of this one function decides whether spacing helps or hurts.

    Swapping the factors trains the model to lengthen intervals right after a
    learner forgets, so the invariant -- a memory that survived the lag is at least
    as strong as the lag, a memory that did not is weaker -- is pinned here.
    """
    from adapt.learning.spacing import label_half_life

    assert label_half_life(6.0, True) > 6.0 > label_half_life(6.0, False)
    assert label_half_life(0.0, True) > 0, "a review at the same sitting still has a floor"
    # Monotone in the lag: waiting longer and still recalling is stronger evidence.
    assert label_half_life(12.0, True) > label_half_life(6.0, True)
    assert label_half_life(12.0, False) > label_half_life(6.0, False)


def test_next_schedule_schedules_the_next_review_at_the_target():
    from adapt.learning.spacing import HalfLifeModel, next_schedule

    model = HalfLifeModel()
    now = datetime(2026, 3, 1, 12, 0, 0)
    sched = next_schedule(
        model=model,
        reps_before=2,
        prev_lag_hours=8.0,
        difficulty=0.4,
        recent_success=0.8,
        self_explain=0.5,
        recalled=True,
        now=now,
        target=0.85,
    )
    assert sched.reps == 3
    assert sched.due_at > now
    assert sched.expected_recall_at_due == pytest.approx(0.85, abs=0.02)
    assert sched.half_life_hours >= 0.25
    assert "half-life" in sched.rationale

    lapse = next_schedule(
        model=model,
        reps_before=2,
        prev_lag_hours=8.0,
        difficulty=0.4,
        recent_success=0.2,
        self_explain=0.0,
        recalled=False,
        now=now,
    )
    assert lapse.due_at - now < sched.due_at - now, "a failed recall should shorten the interval"
    assert "lapse" in lapse.rationale


def test_half_life_fit_shrinks_toward_the_default_on_tiny_data():
    from adapt.learning.spacing import DEFAULT_WEIGHTS, features, fit_half_life

    def f(**kw):
        return features(**kw)

    tiny = fit_half_life([(f(reps=1, prev_lag_hours=4.0, difficulty=0.4, recent_success=0.8, self_explain=0.2), 1.0)])
    assert tiny.n == 1
    assert all(abs(a - b) < 1.0 for a, b in zip(tiny.weights, DEFAULT_WEIGHTS)), (
        "one review must not rewrite the model"
    )

    many = [
        (
            f(reps=float(i % 5), prev_lag_hours=1.0 + (i % 7), difficulty=0.4, recent_success=0.8, self_explain=0.3),
            1.0 + (i % 5) * 0.6,
        )
        for i in range(120)
    ]
    fitted = fit_half_life(many)
    assert fitted.n == 120
    assert fitted.rmse_log2 is not None and fitted.rmse_log2 < 1.5


def test_round_trip_through_serialisation():
    from adapt.learning.spacing import HalfLifeModel

    model = HalfLifeModel(n=17)
    again = HalfLifeModel.from_dict(model.as_dict())
    assert again.n == 17
    assert list(again.weights) == list(model.weights)


# --------------------------------------------------------------------------- #
# effect size
# --------------------------------------------------------------------------- #


def test_insufficient_data_is_reported_as_insufficient():
    from adapt.learning.effect import evaluate_change

    out = evaluate_change([1.0, 0.0], [1.0, 1.0, 1.0], metric="accuracy")
    assert out.verdict == "insufficient_evidence"
    assert out.ci_low is None
    assert "need >=" in out.explanation


def test_a_real_improvement_is_detected_and_a_coin_flip_is_not():
    from adapt.learning.effect import evaluate_change

    better = evaluate_change([0.0] * 10 + [1.0] * 5, [1.0] * 15, metric="accuracy", seed=3)
    assert better.mean_delta > 0.3
    assert better.verdict == "supported", better.verdict
    assert better.ci_low > 0 <= better.ci_high
    assert "95% CI" in better.explanation and better.pooled_sd is not None

    worse = evaluate_change([1.0] * 15, [0.0] * 10 + [1.0] * 5, metric="accuracy", seed=3)
    assert worse.verdict == "refuted" and worse.mean_delta < 0

    noisy = evaluate_change([0, 1, 0, 1, 0, 1, 0, 1], [1, 0, 1, 0, 1, 0, 1, 0], metric="accuracy", seed=5)
    assert noisy.verdict == "inconclusive", noisy.verdict


def test_confidence_interval_covers_the_observed_rate():
    from adapt.learning.effect import proportion_ci

    for n in (5, 20, 60):
        lo, hi = proportion_ci(int(n * 0.6), n)
        assert 0.0 <= lo <= 0.6 <= hi <= 1.0
        assert hi - lo > 0  # never a fake point estimate
        if n >= 20:
            assert hi - lo < 0.6


def test_hedges_g_is_signed_from_the_point_of_view_of_the_change():
    """``hedges_g(baseline, after)``: positive means the change helped.

    The order is the one ``evaluate_change`` uses; flipping it would flip every
    verdict reported back to the learner.
    """
    import numpy as np

    from adapt.learning.effect import hedges_g

    base = list(np.random.default_rng(1).normal(0.0, 1.0, 40))
    after = [x + 0.8 for x in np.random.default_rng(2).normal(0.0, 1.0, 40)]
    g, pooled = hedges_g(base, after)
    assert g > 0.4, g
    assert pooled > 0
    same, _ = hedges_g(base, base)
    assert abs(same) < 1e-9


def test_trend_direction_is_reported_with_evidence_count():
    from adapt.learning.effect import trend

    up = trend([0.2, 0.3, 0.45, 0.5, 0.62, 0.7])
    flat = trend([0.5, 0.5, 0.5, 0.5, 0.5, 0.5])
    down = trend(list(reversed([0.2, 0.3, 0.45, 0.5, 0.62, 0.7])))
    assert up["slope"] > 0 and up["direction"] == "rising"
    assert down["direction"] == "falling"
    assert abs(flat["slope"]) < 1e-6 and flat["direction"] == "flat"
    assert up["n"] == 5, "window=5 by default: the slope is over the recent window"


def test_retention_calibration_compares_predicted_to_actual():
    from adapt.learning.effect import retention_calibration

    predictions = [
        (0.9, True), (0.9, True), (0.9, True), (0.9, False),
        (0.5, False), (0.5, False), (0.5, True),
        (0.2, False), (0.2, False), (0.2, False),
    ]
    out = retention_calibration(predictions)
    assert out["n"] == 10
    assert 0.0 <= out["brier"] <= 1.0 and 0.0 <= out["ece"] <= 1.0
    assert out["buckets"], "calibration without buckets is not inspectable"
    for bucket in out["buckets"]:
        assert 0.0 <= bucket["predicted"] <= 1.0 and 0.0 <= bucket["observed"] <= 1.0
    # Overconfident predictions (0.9 predicted, 0.75 observed) and underconfident
    # ones are distinguishable through the mean gap.
    assert abs(out["mean_predicted"] - out["mean_observed"]) < 0.5


# --------------------------------------------------------------------------- #
# detectors and hypotheses
# --------------------------------------------------------------------------- #


def _row(i, *, topic="top_a", correct=None, score=None, hints=0, examples=0, explanation=False, **kw):
    from adapt.learning.patterns import AttemptRow

    return AttemptRow(
        id=f"att_{i:04d}",
        topic_id=topic,
        item_id=f"itm_{i:04d}",
        correct=correct,
        score=score,
        hints_used=hints,
        examples_opened=examples,
        explanation_opened=explanation,
        **kw,
    )


def test_worked_example_detector_needs_both_groups_to_exist():
    from adapt.learning.patterns import EvidenceContext, detect_worked_example_need

    rows = [_row(i, correct=True, examples=1) for i in range(4)] + [
        _row(100 + i, correct=False, explanation=True) for i in range(4)
    ]
    ctx = EvidenceContext(student_id="stu_1", attempts=rows)
    hits = detect_worked_example_need(ctx)
    assert len(hits) == 1
    obs = hits[0]
    assert obs.direction in {"up", "down"}
    assert 0.0 <= obs.strength <= 1.0
    assert obs.n >= 4 and obs.attempt_ids

    thin = EvidenceContext(student_id="stu_1", attempts=rows[:5])
    assert detect_worked_example_need(thin) == [], "below min_n there is no pattern to report"


def test_hint_dependency_is_only_claimed_when_hints_are_present():
    from adapt.learning.patterns import EvidenceContext, detect_hint_dependency

    clean = [_row(i, correct=True) for i in range(8)]
    assert detect_hint_dependency(EvidenceContext(student_id="stu_1", attempts=clean)) == []

    dependent = [_row(i, correct=bool(i % 2), hints=2) for i in range(8)]
    hits = detect_hint_dependency(EvidenceContext(student_id="stu_1", attempts=dependent + clean[:1]))
    assert isinstance(hits, list)


def test_observation_serialisation_is_flat_and_typed():
    from adapt.learning.patterns import EvidenceContext, detect_worked_example_need

    rows = [_row(i, correct=True, examples=1) for i in range(4)] + [
        _row(50 + i, correct=False, explanation=True) for i in range(4)
    ]
    obs = detect_worked_example_need(EvidenceContext(student_id="stu_1", attempts=rows))[0]
    payload = obs.as_dict()
    assert payload["detector"] and payload["candidate_strategy"]
    assert isinstance(payload["facts"], dict)
    assert set(["value", "baseline", "delta", "n", "strength"]) <= set(payload)


def test_hypothesis_draft_carries_citation_and_a_way_to_disprove_it():
    from adapt.learning import hypotheses as H
    from adapt.learning.patterns import EvidenceContext, detect_worked_example_need

    rows = [_row(i, correct=True, examples=1) for i in range(4)] + [
        _row(50 + i, correct=False, explanation=True) for i in range(4)
    ]
    obs = detect_worked_example_need(EvidenceContext(student_id="stu_1", attempts=rows))[0]
    draft = H.draft(obs, evidence_id="evd_1")
    assert draft is not None
    assert draft.statement and draft.mechanism
    assert draft.citations, "an unpinned explanation is a guess"
    assert draft.falsification, "the learner has to be able to see what would disprove it"
    assert draft.alternatives, "competing explanations must be named, not hidden"
    assert draft.strategy
    assert 0.0 < draft.confidence <= 1.0


def test_alternatives_is_always_a_sequence_of_strings():
    """A 1-tuple written as ("text") is a bare string, and joining it spells out letters."""
    from adapt.learning import hypotheses as H

    bad = H.Draft(
        detector="difficulty_misalignment",
        statement="s",
        mechanism="m",
        citations=(),
        falsification="f",
        alternatives=("a single string",),  # deliberately awkward input
        strategy="difficulty_recalibration",
        confidence=0.5,
        evidence_ids=[],
        topic_ids=[],
    )
    text = "; ".join(bad.alternatives)
    assert text == "a single string"
    assert "a;" not in text


def test_language_guard_blocks_labeling_and_learning_style_pseudoscience():
    from adapt.learning import hypotheses as H

    assert H.check_language("You are a lazy learner, honestly bad at this.")
    assert H.check_language("As a visual learner you should avoid text.")
    assert H.check_language("This looks like untreated ADHD rather than a knowledge gap.")
    assert H.check_language("you seem to have a fixed mindset about maths")
    assert not H.check_language("You got 3 of the last 5 right on equivalent fractions.")
    # The banned list is what AI 1 validates *LLM output* against, so it has to
    # cover the words a model reaches for, not only obvious insults.
    for phrase in ("gifted", "ADHD", "visual learner"):
        assert phrase in H.BANNED_PHRASES


def test_posterior_and_decision_thresholds():
    from adapt.learning import hypotheses as H

    strong = H.posterior(9, 1)
    weak = H.posterior(1, 0)
    assert strong > weak
    assert 0.0 <= weak <= 1.0
    # Deliberately slow to believe: a Beta(1.5, 3.5) prior means about a dozen clean
    # replications are needed before the lower 95% bound clears 0.6. Nine is not
    # enough, and saying "supported" at nine would oversell a single good fortnight.
    assert H.decide(supports=14, refutes=0).value == "supported"
    assert H.decide(supports=9, refutes=0).value == "inconclusive"
    assert H.decide(supports=1, refutes=0).value == "active", "one sighting is not yet testable"
    assert H.decide(supports=0, refutes=6).value == "refuted"
    assert H.decide(supports=3, refutes=3).value == "inconclusive"
    assert H.decide(supports=1, refutes=0, status="testing").value == "testing"
    assert H.posterior(9, 0) > H.posterior(3, 3) > H.posterior(0, 9)


def test_summarize_reports_status_counts():
    from adapt.learning import hypotheses as H

    out = H.summarize(
        [
            {"status": "active", "detector": "difficulty_misalignment", "posterior": 0.8},
            {"status": "refuted", "detector": "hint_dependency", "posterior": 0.2},
        ],
        now=datetime(2026, 1, 1),
    )
    assert out["total"] == 2
    assert out["by_status"] == {"active": 1, "refuted": 1}
    assert out["strongest"][0]["detector"] == "difficulty_misalignment"
    assert "not conclusions" in out["note"]


def test_gaps_need_evidence_on_the_prerequisite():
    from adapt.learning.gaps import Graph, detect_gaps

    graph = Graph(topics={"a": "Integers", "b": "Fractions", "c": "Algebra"}, prereqs={"b": ["a"], "c": ["b"]})
    assert graph.dependents["a"] == ["b"] and graph.closure("c") == ["b", "a"] and graph.has_cycle() is None
    # Nothing measured yet: no gap may be claimed.
    assert detect_gaps(graph, {}, {}) == []
    # Measured weak prerequisite under a measured weak topic: gap.
    gaps = detect_gaps(graph, {"a": 0.2, "b": 0.3}, {"a": 5, "b": 5})
    assert gaps and gaps[0].blocking_prereq == "a"
    assert gaps[0].severity > 0
    # A competent prerequisite closes the gap under it (a:0.9 no longer blocks b).
    remaining = detect_gaps(graph, {"a": 0.9, "b": 0.3}, {"a": 5, "b": 5})
    assert [g.blocking_prereq for g in remaining] == ["b"], "b is now the measured weak link, under c"
    # A strong prerequisite chain reports nothing at all.
    assert detect_gaps(graph, {"a": 0.9, "b": 0.9, "c": 0.9}, {"a": 5, "b": 5, "c": 5}) == []
    # A topic that is doing fine is not blocked by a weak prereq claim.
    assert [g for g in detect_gaps(graph, {"a": 0.2, "b": 0.9}, {"a": 5, "b": 5}) if g.topic_id == "b"] == []


def test_gap_payload_is_ui_ready():
    from adapt.learning.gaps import Graph, detect_gaps

    graph = Graph(topics={"a": "Integers", "b": "Fractions"}, prereqs={"b": ["a"]})
    gap = detect_gaps(graph, {"a": 0.2, "b": 0.25}, {"a": 6, "b": 6})[0]
    payload = gap.as_dict()
    assert payload["topic_title"] == "Fractions"
    assert payload["blocking_prerequisite_title"] == "Integers"
    assert payload["why"], "a gap without a reason is not something a learner can act on"
    assert 0.0 <= payload["severity"] <= 1.0


# --------------------------------------------------------------------------- #
# strategies + telemetry on the adaptation itself
# --------------------------------------------------------------------------- #


def test_every_strategy_declares_what_it_may_change_and_how_to_measure_it():
    from adapt.learning import strategies as ST

    seen = list(ST.CATALOG.values())
    assert len(seen) >= 8
    for strategy in seen:
        assert strategy.id and strategy.label and strategy.what and strategy.mechanism
        assert strategy.action and strategy.watch_metric
        assert isinstance(strategy.payload_defaults, dict)
        assert strategy.caveat, "every strategy has to admit its own limits"
        assert strategy.citations, f"{strategy.id} claims an effect with no source"
        for citation in strategy.citations:
            assert citation.ref and citation.claim
        assert isinstance(strategy.persistent, bool)
        assert ST.get(strategy.id) is strategy
    persistent = {s.id for s in seen if s.persistent}
    assert persistent == set(ST.PERSISTENT_STRATEGIES), (
        "the approval gate covers exactly the persistent strategies, no more and no fewer"
    )
    assert "stated_preference" not in persistent, "a learner's own request needs no gate"
    assert ST.band_for(0.9, 12) and ST.catalog_dicts()
    assert ST.strategy_for_gap is not None


def test_strategy_stats_and_effect_are_replayable():
    from adapt.learning.telemetry import metrics_for_events
    from adapt.learning.effect import evaluate_change

    assert callable(metrics_for_events)
    out = evaluate_change([0.2, 0.2, 0.2, 0.2, 0.3], [0.8, 0.9, 0.7, 0.85, 0.95], metric="accuracy", seed=11)
    assert out.after_mean > out.baseline_mean
    assert out.verdict != "insufficient_evidence"


def test_recalibrating_the_difficulty_target_moves_it_the_right_way():
    """The payload a proposal carries has to be derived, not the strategy's default.

    A number the learner approves must be a change: see ``recalibrated_target``.
    """
    from adapt.ai.ai1 import recalibrated_target

    assert recalibrated_target({"direction": "too_hard"}) == 0.85
    assert recalibrated_target({"direction": "too_easy"}) == 0.70
    # no direction recorded, but the measurement is below the band it was measured against
    assert recalibrated_target({"observed": 0.41, "target_success_rate": 0.78}) == 0.85
    assert recalibrated_target({"observed": 0.95, "target_success_rate": 0.78}) == 0.70
    # nothing usable: the current value is left alone rather than guessed at
    assert recalibrated_target({}, current=0.78) == 0.78
    # and the tuned value always stays inside the band the strategy claims to aim at
    for facts in ({"observed": 0.0}, {"observed": 1.0}, {"observed": 0.5, "target_success_rate": 0.5}):
        assert 0.60 <= recalibrated_target(facts) <= 0.92, facts


def test_analysis_and_evidence_context_sequence_defaults_are_lists():
    from adapt.ai.ai1 import Analysis
    from adapt.learning.patterns import EvidenceContext

    analysis = Analysis(student_id="stu_test", session_id=None)
    ctx = EvidenceContext(student_id="stu_test", attempts=[])
    assert isinstance(analysis.gaps, list) and analysis.gaps == []
    assert isinstance(ctx.retention, list) and ctx.retention == []

