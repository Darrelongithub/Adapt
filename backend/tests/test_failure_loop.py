"""Phase 6 (Agent 5): evidence, method comparison, and the "why did I fail" loop.

Three layers, matching the brief's gate:

* **Unit** — every branch of the update rule (prerequisite → weight 0,
  fatigue → 0.5 + break, otherwise → 1.0), the fatigue rule exactly as stated
  (last two > 1.5× session median *with* falling accuracy — either half alone
  must not fire), matched-B same-format selection, seeded-random tie-breaks,
  counterbalanced serve-time assignment, weighted evidence summaries, the
  observational labelling of self-selection detectors, seeded Thompson
  posteriors shown as mean + interval + n, and the "insufficient evidence"
  label below minimum n.
* **Simulation** — synthetic learners of *known* behaviour must let the loop
  recover the right conclusion better than chance: learners whose visual
  method genuinely works get visual identified; learners missing the
  prerequisite get the miss attributed to the prerequisite, every time.
* **HTTP** — the loop and the evidence surfaces exercised end to end through
  the real app: prerequisite-fail, fatigue, and clean-method branches over
  ``POST /evidence/failure-loop``; actual-vs-expected with weights over
  ``GET /evidence/methods``; preference-vs-performance wired to real
  (inserted) attempt outcomes.
"""

from __future__ import annotations

import random

import pytest
from sqlalchemy import select

from adapt.learning import effect as EFFECT
from adapt.learning import failure as F
from adapt.learning import methods as M
from adapt.learning import patterns as P


def _rows(method: str, correct_pattern: list[bool], *, design: str = "randomised", expected: float = 0.5) -> list[dict]:
    return [
        {"method": method, "correct": c, "expected_success": expected, "design": design, "question_kind": "numeric", "topic_id": "top_x"}
        for c in correct_pattern
    ]


FIRST = {"id": "i1", "kind": "numeric", "difficulty": 0.5, "topic_id": "top_b"}
CANDIDATES = [
    {"id": "i2", "kind": "numeric", "difficulty": 0.55, "topic_id": "top_b", "active": True},
    {"id": "i3", "kind": "mcq", "difficulty": 0.5, "topic_id": "top_b", "active": True},
]
PREREQS = {"top_b": ["top_a"]}
PREREQ_CANDIDATES = {"top_a": [{"id": "p1", "kind": "numeric", "difficulty": 0.3, "topic_id": "top_a", "active": True}]}
CALM_LAT = [1000, 1100, 900, 1000, 1050, 950]
CALM_OK = [True, True, False, True, True, False]


def _loop(**overrides):
    kwargs = dict(
        learner_id="st_test",
        topic_id="top_b",
        first_item=FIRST,
        first_method="explanation",
        first_expected=0.6,
        candidates=CANDIDATES,
        prereqs=PREREQS,
        prereq_candidates=PREREQ_CANDIDATES,
        prereq_correct=True,
        session_latencies_ms=CALM_LAT,
        session_correct=CALM_OK,
        method_rows=[],
        matched_correct=None,
    )
    kwargs.update(overrides)
    return F.run_failure_loop(**kwargs)


# --------------------------------------------------------------------------- #
# (b) the fatigue rule, exactly as stated
# --------------------------------------------------------------------------- #

def test_fatigue_fires_only_when_slow_and_slipping():
    fired = F.fatigue_check([1000, 1000, 1100, 900, 3200, 4100], [True, True, True, False, False, False])
    assert fired["fired"] is True and fired["median_ms"] == 1050.0
    assert "tiredness" in fired["why"]


def test_fatigue_does_not_fire_for_slow_but_accurate():
    # Slow and careful is not tired: accuracy held while the last two slowed.
    out = F.fatigue_check([1000, 1000, 1100, 900, 3200, 4100], [True, False, True, True, True, True])
    assert out["fired"] is False


def test_fatigue_does_not_fire_for_fast_and_wrong():
    out = F.fatigue_check([900, 1000, 800, 950, 700, 650], [True, True, True, False, False, False])
    assert out["fired"] is False


def test_fatigue_needs_four_timed_answers():
    assert F.fatigue_check([1000, 5000, 6000], [True, False, False])["fired"] is False
    assert F.fatigue_check([None, None], [True, False])["fired"] is False


# --------------------------------------------------------------------------- #
# (a) the prerequisite check
# --------------------------------------------------------------------------- #

def test_prerequisite_selection_uses_the_direct_chain_only():
    prereqs = {"top_b": ["top_a"], "top_a": ["top_root"]}
    topic, question = F.select_prerequisite_question("top_b", prereqs, PREREQ_CANDIDATES, learner_id="st_1")
    assert topic == "top_a" and question["id"] == "p1"  # never the transitive root
    assert F.direct_prerequisites("top_b", prereqs) == ["top_a"]


def test_prerequisite_failure_attributes_to_prerequisite_at_zero_weight():
    result = _loop(prereq_correct=False)
    attr = result["attribution"]
    assert attr["attributed_to"] == "prerequisite"
    assert attr["method_weight"] == 0.0 and attr["counts_against_method"] is False
    assert result["reteach"] is None and result["comparison"] is None
    assert result["evidence"]["method_weight"] == 0.0
    assert "does not count against" in result["learner_sentence"]


def test_prerequisite_not_yet_checked_still_plans_the_question():
    result = _loop(prereq_correct=None)
    step = result["steps"]["prerequisite"]
    assert step["status"] == "not_checked" and step["question"]["item_id"] == "p1"
    assert step["chain"] == ["top_a"]


# --------------------------------------------------------------------------- #
# the update rule: all three branches
# --------------------------------------------------------------------------- #

def test_attribution_branches_and_weights():
    prereq = F.attribution_for(prereq_status="failed", fatigue_fired=True)
    assert prereq["method_weight"] == 0.0 and prereq["attributed_to"] == "prerequisite"  # prereq wins over fatigue
    fatigue = F.attribution_for(prereq_status="passed", fatigue_fired=True)
    assert fatigue["method_weight"] == 0.5 and fatigue["suggest_break"] is True
    method = F.attribution_for(prereq_status="passed", fatigue_fired=False)
    assert method["method_weight"] == 1.0 and method["suggest_break"] is False


def test_fatigue_branch_defers_the_reteach_and_says_break():
    result = _loop(
        session_latencies_ms=[1000, 1000, 1100, 900, 3200, 4100],
        session_correct=[True, True, True, False, False, False],
    )
    assert result["attribution"]["attributed_to"] == "fatigue"
    assert result["evidence"]["method_weight"] == 0.5
    assert result["reteach"]["deferred_until_after_break"] is True
    assert "half weight" in result["learner_sentence"] and "break" in result["learner_sentence"]


def test_clean_miss_attributes_to_the_method_at_full_weight():
    result = _loop()
    assert result["attribution"]["attributed_to"] == "method"
    assert result["evidence"]["method_weight"] == 1.0
    assert result["reteach"]["method"] != "explanation"  # never reteach in the failed method


# --------------------------------------------------------------------------- #
# (c) reteach choice, ties, and the matched question
# --------------------------------------------------------------------------- #

def test_reteach_picks_the_measured_best_alternative():
    rows = _rows("visual", [True] * 8 + [False] * 2) + _rows("retrieval", [True] * 4 + [False] * 6)
    choice = F.choose_reteach_method(rows, failed_method="explanation", learner_id="st_1", topic_id="top_b")
    assert choice["method"] == "visual" and choice["tie_broken_by"] == "measured best" and choice["n"] == 10


def test_reteach_tie_break_is_seeded_random_and_reproducible():
    rows = _rows("visual", [True] * 5 + [False] * 5) + _rows("retrieval", [True] * 5 + [False] * 5)
    first = F.choose_reteach_method(rows, failed_method="explanation", learner_id="st_1", topic_id="top_b")
    again = F.choose_reteach_method(rows, failed_method="explanation", learner_id="st_1", topic_id="top_b")
    assert first["method"] == again["method"]  # same seed, same draw
    assert set(first["tied"]) == {"visual", "retrieval"} and "seeded random" in first["tie_broken_by"]
    picks = {
        F.choose_reteach_method(rows, failed_method="explanation", learner_id=f"st_{i}", topic_id="top_b")["method"]
        for i in range(24)
    }
    assert picks == {"visual", "retrieval"}  # different learners really do draw differently


def test_matched_question_holds_the_format_fixed():
    picked, fmt, held = F.select_matched_question(CANDIDATES, FIRST, learner_id="st_1")
    assert held is True and fmt == "typed_number" and picked["id"] == "i2" and picked["kind"] == "numeric"


def test_matched_question_refuses_a_different_format():
    picked, _fmt, held = F.select_matched_question([CANDIDATES[1]], FIRST, learner_id="st_1")
    assert picked is None and held is False  # says so instead of borrowing the mcq


# --------------------------------------------------------------------------- #
# serve-time assignment: counterbalanced, seeded, in code
# --------------------------------------------------------------------------- #

def test_counterbalanced_assignment_cycles_and_is_seeded_per_learner():
    seen = {
        F.counterbalanced_assignment(learner_id="st_1", concept_id="top_b", exposure=e)["method"] for e in range(4)
    }
    assert seen == set(M.METHOD_CYCLE)  # one learner cycles through every method
    a = F.counterbalanced_assignment(learner_id="st_1", concept_id="top_b", exposure=0)
    b = F.counterbalanced_assignment(learner_id="st_1", concept_id="top_b", exposure=0)
    assert a == b and a["design"] == "counterbalanced" and a["weight"] == 1.0
    starts = {F.counterbalanced_assignment(learner_id=f"st_{i}", concept_id="top_b", exposure=0)["method"] for i in range(16)}
    assert len(starts) > 1  # different learners start at different methods


# --------------------------------------------------------------------------- #
# (d) the comparison, with range, n, and the confound stated
# --------------------------------------------------------------------------- #

def test_comparison_reports_range_n_and_confound():
    result = _loop(method_rows=_rows("visual", [True] * 8 + [False] * 2), matched_correct=True, matched_expected=0.5)
    comp = result["comparison"]
    assert comp["improved"] is True and comp["n"] == 11
    assert 0.0 <= comp["range"][0] <= comp["range"][1] <= 1.0
    assert comp["confound"] == F.SECOND_EXPOSURE_CONFOUND
    sentence = result["learner_sentence"]
    assert "from 11 attempt" in sentence and "not a controlled experiment" in sentence


def test_comparison_below_minimum_n_says_insufficient_evidence():
    result = _loop(method_rows=_rows("visual", [True]), matched_correct=True, matched_expected=0.5)
    assert result["comparison"]["verdict_label"] == "insufficient evidence"
    assert EFFECT.verdict_label("insufficient_evidence") == "insufficient evidence"
    assert EFFECT.evaluate_change([0.4, 0.5], [0.6, 0.7], metric="accuracy").verdict == "insufficient_evidence"


# --------------------------------------------------------------------------- #
# §9.3/§9.4: weights and self-selection labels
# --------------------------------------------------------------------------- #

def test_row_weights_controlled_observational_and_loop_weights():
    assert F.row_weight({"design": "randomised"}) == 1.0
    assert F.row_weight({"design": "observational"}) == 0.4
    assert F.row_weight({"design": "randomised", "method_weight": 0.5}) == 0.5
    assert F.row_weight({"design": "randomised", "method_weight": 0.0}) == 0.0


def test_weighted_summary_shows_raw_and_effective_n_with_source():
    rows = _rows("visual", [True] * 10) + _rows("explanation", [True] * 5, design="observational")
    summary = {s["method"]: s for s in F.weighted_method_summary(rows)}
    assert summary["visual"]["n"] == 10 and summary["visual"]["effective_n"] == 10.0
    assert summary["explanation"]["n"] == 5 and summary["explanation"]["effective_n"] == 2.0
    assert "self-selected" in summary["explanation"]["source"]


def test_self_selection_detectors_are_labelled_observational_never_proof():
    attempts = (
        [{"examples_opened": 1, "explanation_opened": False, "correct": i < 6} for i in range(8)]
        + [{"examples_opened": 0, "explanation_opened": False, "correct": i < 3} for i in range(8)]
    )
    detectors = {d["detector"]: d for d in F.self_selection_detectors(attempts)}
    opened = detectors["opened_example"]
    assert opened["design"] == "observational" and opened["weight"] == 0.4 and opened["is_proof"] is False
    assert opened["n"] == 8 and "range" in opened and "not proof" in opened["note"]
    thin = F.self_selection_detectors(attempts[:2])[0]
    assert thin["status"] == "insufficient evidence"


def test_strategy_posteriors_are_seeded_and_carry_mean_interval_n():
    rows = _rows("visual", [True] * 9 + [False]) + _rows("explanation", [True] * 2 + [False] * 8)
    first = F.strategy_posteriors(rows, seed=7)
    again = F.strategy_posteriors(rows, seed=7)
    assert first["thompson_sample"] == again["thompson_sample"]  # reproducible draw
    top = first["strategies"][0]
    assert top["strategy"] == "visual_representation"
    assert top["posterior_mean"] == top["p_helps"] and top["n"] == 10.0
    assert top["ci"][0] <= top["posterior_mean"] <= top["ci"][1]


# --------------------------------------------------------------------------- #
# §9.1: the visual detector may only claim assignment when it was assigned
# --------------------------------------------------------------------------- #

def _ctx(rows: list[P.AttemptRow]) -> P.EvidenceContext:
    return P.EvidenceContext(student_id="st_test", attempts=rows)


def _visual_row(i: int, *, correct: bool, **kw) -> P.AttemptRow:
    base = dict(id=f"a{i}", topic_id="top_frac", item_id=f"i{i}", correct=correct, expected_success=0.5)
    base.update(kw)
    return P.AttemptRow(**base)


def test_visual_detector_calls_assigned_arms_randomised():
    rows = [_visual_row(i, correct=i < 6, method="visual", design="randomised") for i in range(8)] + [
        _visual_row(20 + i, correct=i < 2, method="explanation", design="randomised") for i in range(8)
    ]
    obs = P.detect_visual_benefit(_ctx(rows))
    assert obs and obs[0].design == "randomised"
    assert obs[0].facts["weight"] == 1.0 and "assigned at serve time" in str(obs[0].facts["assignment"])


def test_visual_detector_never_calls_opened_visuals_randomised():
    rows = [_visual_row(i, correct=i < 7, visual_opened=True, experience_mode="visual") for i in range(8)] + [
        _visual_row(20 + i, correct=i < 2, experience_mode="read") for i in range(8)
    ]
    obs = P.detect_visual_benefit(_ctx(rows))
    assert obs and obs[0].design == "observational"
    assert obs[0].facts["weight"] == 0.4 and "self-selected" in str(obs[0].facts["assignment"])


# --------------------------------------------------------------------------- #
# simulations: known learners, right conclusions, better than chance
# --------------------------------------------------------------------------- #

def _synthetic_method_learner(rng: random.Random, true_best: str, n: int = 60) -> list[dict]:
    """A learner who genuinely does better under ``true_best`` (p=0.85 vs 0.45).

    Methods arrive by the same counterbalanced assignment the server uses, so
    the simulation tests the real assignment + real comparison together.
    """
    rows = []
    for exposure in range(n):
        method = F.counterbalanced_assignment(learner_id="sim", concept_id="top_sim", exposure=exposure)["method"]
        p = 0.85 if method == true_best else 0.45
        rows.append(
            {"method": method, "correct": rng.random() < p, "expected_success": 0.5, "design": "randomised", "question_kind": "numeric", "topic_id": "top_sim"}
        )
    return rows


def test_simulation_recovers_the_true_best_method_better_than_chance():
    recovered = 0
    trials = 40
    for t in range(trials):
        rows = _synthetic_method_learner(random.Random(1000 + t), "visual")
        report = M.method_report(rows)
        ranked = [r for r in report["methods"] if r["n"] >= M.EARLY_SIGNAL_N]
        best = max(ranked, key=lambda r: r["residual"])
        recovered += best["method"] == "visual"
    # Chance over four methods is 25% (10/40). A comparison that cannot beat
    # that on a learner built to have a real best method is not a comparison.
    assert recovered >= 28, f"recovered the true best method in {recovered}/{trials} synthetic learners"


def test_simulation_prerequisite_gap_is_attributed_to_the_prerequisite():
    for t in range(20):
        result = _loop(learner_id=f"sim_{t}", prereq_correct=False)
        assert result["attribution"]["attributed_to"] == "prerequisite"
        assert result["evidence"]["method_weight"] == 0.0


def test_simulation_same_format_is_held_across_methods():
    rng = random.Random(42)
    for _ in range(30):
        candidates = [
            {"id": f"c{i}", "kind": rng.choice(["mcq", "numeric", "text"]), "difficulty": 0.5, "topic_id": "top_b", "active": True}
            for i in range(6)
        ]
        picked, fmt, held = F.select_matched_question(candidates, FIRST, learner_id="sim")
        if held:
            assert M.question_format(picked) == fmt == M.question_format(FIRST)
        else:  # when no same-format question exists the loop must say so, not borrow one
            assert all(M.question_format(c) != fmt or c["id"] == FIRST["id"] for c in candidates)


# --------------------------------------------------------------------------- #
# HTTP: the loop and the surfaces, end to end
# --------------------------------------------------------------------------- #

def _topic_with_prereq(services) -> tuple[str, str, str]:
    """(topic_id, item_id, prereq_topic_id) with real bank items on both sides."""
    from adapt.db.models import Item, TopicPrerequisite

    with services.scope() as s:
        edges = s.execute(select(TopicPrerequisite)).scalars().all()
        for edge in edges:
            items = s.execute(select(Item).where(Item.topic_id == edge.topic_id, Item.active.is_(True))).scalars().all()
            prereq_items = s.execute(select(Item).where(Item.topic_id == edge.prerequisite_id, Item.active.is_(True))).scalars().all()
            if len(items) >= 2 and prereq_items:
                return edge.topic_id, items[0].id, edge.prerequisite_id
    pytest.skip("no topic with a prerequisite edge and items on both sides in the seeded bank")


def _post_loop(client, learner, **body):
    return client.post("/api/v1/evidence/failure-loop", json=body, headers=learner["headers"])


def test_failure_loop_over_http_prerequisite_branch(client, learner, services):
    topic_id, item_id, prereq_id = _topic_with_prereq(services)
    res = _post_loop(client, learner, topic_id=topic_id, item_id=item_id, method="explanation", expected_success=0.6, prereq_correct=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["attribution"]["attributed_to"] == "prerequisite"
    assert body["evidence"]["method_weight"] == 0.0 and body["recorded"] is True
    assert body["steps"]["prerequisite"]["topic_id"] == prereq_id
    assert body["steps"]["prerequisite"]["question"]["item_id"]


def test_failure_loop_over_http_fatigue_branch(client, learner, services):
    topic_id, item_id, _ = _topic_with_prereq(services)
    res = _post_loop(
        client,
        learner,
        topic_id=topic_id,
        item_id=item_id,
        method="explanation",
        expected_success=0.6,
        prereq_correct=True,
        session_latencies_ms=[1000, 1000, 1100, 900, 3200, 4100],
        session_correct=[True, True, True, False, False, False],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["attribution"]["attributed_to"] == "fatigue" and body["evidence"]["method_weight"] == 0.5
    assert body["reteach"]["deferred_until_after_break"] is True


def test_failure_loop_over_http_method_branch_compares_matched(client, learner, services):
    topic_id, item_id, _ = _topic_with_prereq(services)
    res = _post_loop(
        client,
        learner,
        topic_id=topic_id,
        item_id=item_id,
        method="explanation",
        expected_success=0.6,
        prereq_correct=True,
        matched_correct=True,
        matched_expected=0.5,
        session_latencies_ms=CALM_LAT,
        session_correct=CALM_OK,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["attribution"]["attributed_to"] == "method" and body["evidence"]["method_weight"] == 1.0
    assert body["reteach"]["method"] != "explanation"
    assert body["reteach"]["format_held_fixed"] is True
    assert body["comparison"]["confound"] == F.SECOND_EXPOSURE_CONFOUND
    assert "not a controlled experiment" in body["learner_sentence"]


def test_failure_loop_over_http_rejects_unknown_item(client, learner, services):
    topic_id, _item, _ = _topic_with_prereq(services)
    res = _post_loop(client, learner, topic_id=topic_id, item_id="itm_does_not_exist", prereq_correct=True)
    assert res.status_code == 404


def _insert_attempts(services, learner, session_id: str, topic_id: str, item_id: str, specs: list[tuple[str, bool]]):
    from adapt.db.models import Attempt

    with services.scope() as s:
        for method, correct in specs:
            s.add(
                Attempt(
                    session_id=session_id,
                    student_id=learner["student_id"],
                    item_id=item_id,
                    topic_id=topic_id,
                    correct=correct,
                    method=method,
                    question_kind="numeric",
                    expected_success=0.5,
                    mapping_design="randomised",
                )
            )


def test_evidence_methods_and_preference_over_http(client, learner, session, services):
    topic_id, item_id, _ = _topic_with_prereq(services)
    services.state.set_preference(learner["student_id"], "preferred_format", "diagram", source="test")
    _insert_attempts(
        services,
        learner,
        session,
        topic_id,
        item_id,
        [("visual", True)] * 6 + [("visual", False)] * 2 + [("explanation", True)] * 2 + [("explanation", False)] * 6,
    )
    res = client.get("/api/v1/evidence/methods")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scope"] == "all topics"
    by_method = {m["method"]: m for m in body["methods"]}
    assert by_method["visual"]["residual"] > 0 and by_method["visual"]["n"] == 8
    assert by_method["visual"]["effective_n"] == 8.0 and by_method["visual"]["weights"]["observational"] == 0.4
    assert all(d["design"] == "observational" and d["weight"] == 0.4 for d in body["detectors"])
    strategies = {s_["strategy"]: s_ for s_ in body["strategies"]["strategies"]}
    assert strategies["visual_representation"]["posterior_mean"] > strategies["segmented_text"]["posterior_mean"]

    pref = client.get("/api/v1/evidence/preference-vs-performance")
    assert pref.status_code == 200, pref.text
    pref_body = pref.json()
    assert pref_body["available"] is True and pref_body["stated"] == "diagram"
    assert "learning styles" in pref_body["note"]

    one_topic = client.get("/api/v1/evidence/methods", params={"topic_id": topic_id})
    assert one_topic.json()["scope"] == "one topic"
    empty_topic = client.get("/api/v1/evidence/methods", params={"topic_id": "top_no_such_topic"})
    assert empty_topic.json()["methods"] == []


def test_failure_loop_records_a_controlled_evidence_event(client, learner, services):
    topic_id, item_id, _ = _topic_with_prereq(services)
    before = services.state.evidence_count(learner["student_id"])
    res = _post_loop(client, learner, topic_id=topic_id, item_id=item_id, method="explanation", prereq_correct=False)
    assert res.status_code == 200, res.text
    after = services.state.evidence_count(learner["student_id"])
    assert after == before + 1
    rows = [r for r in services.state.evidence_rows(learner["student_id"]) if r.get("kind") == "failure_loop"]
    assert rows and rows[-1]["payload"]["method_weight"] == 0.0
    # Idempotent: the identical loop outcome is not written twice.
    res2 = _post_loop(client, learner, topic_id=topic_id, item_id=item_id, method="explanation", prereq_correct=False)
    assert res2.status_code == 200
    assert services.state.evidence_count(learner["student_id"]) == after
