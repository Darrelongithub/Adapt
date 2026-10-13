"""Known-behaviour simulations plus every attribution branch and real HTTP recovery."""

from __future__ import annotations

import json

import numpy as np
import pytest

from adapt.learning.failure import (
    FailureError,
    FailureLoop,
    attribution,
    choose_alternative,
    fatigue_flag,
    outcome_report,
    validate_pair,
)
from adapt.learning.methods import METHODS, assign_method, balanced_probe
from adapt.learning.strategies import StrategyModel, StrategyStat


def pair(topic="fractions"):
    base = {
        "topic_id": topic,
        "concept_id": "same-subconcept",
        "pair_id": "pair-1",
        "kind": "numeric",
        "difficulty": 0.5,
        "answer_spec": {"value": 4},
        "stem": "2 + 2?",
    }
    return dict(base, id="a", pair_role="A"), dict(base, id="b", pair_role="B", stem="1 + 3?")


def variants():
    return {m: {"text": f"Checked teaching for {m}"} for m in METHODS}


def begin(services, learner, *, key="failure-1", prerequisite=True, recent=()):
    loop = FailureLoop(services.state)
    a, b = pair()
    q = {
        "id": "prereq",
        "topic_id": "direct-prereq",
        "kind": "numeric",
        "stem": "1 + 1?",
        "answer_spec": {"value": 2},
    }
    loop.start(
        learner["student_id"],
        attempt_id=key,
        session_id="session-1",
        a=a,
        b=b,
        method="explanation",
        expected_a=0.4,
        expected_b=0.4,
        variants=variants(),
        prerequisite=q if prerequisite else None,
        prerequisite_required=prerequisite,
        recent=recent,
    )
    return loop


def test_counterbalanced_at_serve_time_is_reproducible_and_not_item_authored():
    for learner in range(30):
        for block in range(5):
            assignments = [
                assign_method(
                    learner_id=str(learner),
                    item_id=f"item-{i}",
                    topic_id=f"topic-{i}",
                    exposure=block * 4 + i,
                    serve_ordinal=block * 4 + i,
                )
                for i in range(4)
            ]
            assert {a.method for a in assignments} == set(METHODS)
            assert all(a.design == "randomised" for a in assignments)
            for i, a in enumerate(assignments):
                assert a == assign_method(
                    learner_id=str(learner),
                    item_id=f"item-{i}",
                    topic_id=f"topic-{i}",
                    exposure=block * 4 + i,
                    serve_ordinal=block * 4 + i,
                )
    assert (
        len(
            {
                assign_method(learner_id=str(i), item_id="same", topic_id="same").method
                for i in range(30)
            }
        )
        == 4
    )


def test_probe_never_changes_question_format_to_fill_run():
    concepts = [{"id": str(i)} for i in range(8)]
    items = {
        str(i): [{"id": str(i), "kind": "numeric" if i < 5 else "mcq", "difficulty": 0.5}]
        for i in range(8)
    }
    probe = balanced_probe(concepts=concepts, items_by_concept=items, learner_id="one")
    assert len(probe) == 5
    assert {p["format"] for p in probe} == {"typed_number"}
    assert all(p["format_held"] for p in probe)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "mcq"),
        ("pair_id", "other"),
        ("concept_id", "other"),
        ("topic_id", "other"),
        ("difficulty", 0.9),
        ("pair_role", "A"),
    ],
)
def test_no_merely_similar_b(field, value):
    a, b = pair()
    b[field] = value
    with pytest.raises(FailureError):
        validate_pair(a, b)


def tired():
    return [{"correct": True, "time_on_item_ms": 1000} for _ in range(6)] + [
        {"correct": False, "time_on_item_ms": 2500},
        {"correct": False, "time_on_item_ms": 3000},
    ]


def test_exact_fatigue_definition():
    assert fatigue_flag(tired())
    assert not fatigue_flag(tired()[:3])
    assert not fatigue_flag([{**r, "correct": True} for r in tired()])
    assert not fatigue_flag([{**r, "time_on_item_ms": 1000} for r in tired()])
    assert not fatigue_flag([{**r, "time_on_item_ms": None} for r in tired()])


@pytest.mark.parametrize(
    "prerequisite_ok,fatigue,multiplier,cause",
    [
        (False, False, 0, "prerequisite"),
        (False, True, 0, "prerequisite"),
        (True, True, 0.5, "fatigue"),
        (True, False, 1, "method_comparison"),
        (None, False, 0, "unchecked_prerequisite"),
    ],
)
def test_attribution_branches(prerequisite_ok, fatigue, multiplier, cause):
    rule = attribution(prerequisite_ok, fatigue)
    assert rule["multiplier"] == multiplier
    assert rule["cause"] == cause


@pytest.mark.parametrize(
    "prereq_answer,recent,expected_weight,cause",
    [
        ("3", (), 0, "prerequisite"),
        ("2", tired(), 0.2, "fatigue"),
        ("2", (), 0.4, "method_comparison"),
    ],
)
def test_persisted_loop_all_update_branches(
    services, learner, prereq_answer, recent, expected_weight, cause
):
    loop = begin(services, learner, recent=recent)
    sid = learner["student_id"]
    first = loop.view(sid, "failure-1")
    assert first["stage"] == "prerequisite"
    assert "answer_spec" not in json.dumps(first)
    next_step = loop.answer(sid, "failure-1", question_id="prereq", answer=prereq_answer)
    assert next_step["stage"] == "matched_b"
    assert next_step["method"] != "explanation"
    assert next_step["question"]["kind"] == "numeric"
    # Device/process resume: a fresh service uses only the shared log.
    fresh = FailureLoop(services.state)
    assert fresh.view(sid, "failure-1") == next_step
    result = fresh.answer(sid, "failure-1", question_id="b", answer="4")
    outcome = result["outcome"]
    assert outcome["effective_weight"] == expected_weight
    assert outcome["cause"] == cause
    assert (
        outcome["n"] == 1
        and outcome["interval"][0] <= outcome["residual_difference"] <= outcome["interval"][1]
    )
    assert "not a controlled experiment" in result["note"]
    assert result == fresh.answer(sid, "failure-1", question_id="b", answer="9")
    assert len([r for r in services.state.evidence_rows(sid) if r["kind"] == "method_outcome"]) == 2
    report = fresh.report(sid)
    assert len(report["methods"]) == (0 if cause == "prerequisite" else 2)
    if cause == "prerequisite":
        prereq = [r for r in services.state.evidence_rows(sid) if r["kind"] == "attempt"]
        assert prereq[0]["topic_id"] == "direct-prereq" and prereq[0]["correct"] is False


def test_no_prerequisite_topic_starts_with_b_and_unknown_check_refuses(services, learner):
    loop = begin(services, learner, prerequisite=False)
    assert loop.view(learner["student_id"], "failure-1")["stage"] == "matched_b"
    a, b = pair()
    with pytest.raises(FailureError, match="direct prerequisite"):
        loop.start(
            learner["student_id"],
            attempt_id="new",
            session_id="s",
            a=a,
            b=b,
            method="explanation",
            expected_a=0.4,
            expected_b=0.4,
            variants=variants(),
            prerequisite=None,
            prerequisite_required=True,
            recent=[],
        )


def test_ties_randomised_seeded_not_catalog_order():
    selections = {
        choose_alternative(str(i), "loop", "explanation", variants(), {}) for i in range(40)
    }
    assert selections == set(METHODS) - {"explanation"}
    assert choose_alternative("a", "b", "explanation", variants(), {}) == choose_alternative(
        "a", "b", "explanation", variants(), {}
    )
    report = {
        "methods": [
            {"method": m, "n": 9, "residual": 0.3 if m == "visual" else 0.1} for m in METHODS
        ]
    }
    assert choose_alternative("a", "b", "explanation", variants(), report) == "visual"


def test_observational_sources_have_lower_weight_and_predictions_of_zero_are_not_replaced():
    rows = [
        {
            "kind": "method_outcome",
            "weight": weight,
            "source_type": source,
            "payload": {"method": "visual", "correct": True, "expected": 0, "multiplier": 1},
        }
        for source, weight in (("controlled", 1), ("observational", 0.4))
    ]
    row = outcome_report(rows)["methods"][0]
    assert row["effective_n"] == 1.4 and row["expected"] == 0
    assert {r["weight"] for r in row["sources"]} == {0.4, 1}
    assert row["evidence"] == "insufficient evidence"
    assert not outcome_report(
        [{"kind": "method_outcome", "payload": {"method": "visual", "correct": True}}]
    )["methods"]


def test_posterior_mean_and_thompson_sampling_are_seedable():
    stat = StrategyStat("worked_examples", alpha=2, beta=1, n=1)
    assert stat.p_helps == pytest.approx(2 / 3)
    assert stat.ci[0] <= stat.p_helps <= stat.ci[1]
    model = StrategyModel([stat, StrategyStat("segmented_text")])
    assert model.sample(rng=np.random.default_rng(7)) == model.sample(rng=np.random.default_rng(7))
    before = stat.as_dict()
    model.record("worked_examples", lift=-1, weight=0)
    assert stat.as_dict() == before


def test_synthetic_known_learners_recover_correct_method_better_than_chance():
    wins = 0
    for seed in range(30):
        rng = np.random.default_rng(seed)
        rows = []
        for i in range(240):
            # Varied topic difficulty: use a pre-exposure learner-model prediction.
            baseline = 0.2 if i % 2 else 0.55
            for m in METHODS:
                actual = min(0.95, baseline + (0.25 if m == "visual" else 0))
                rows.append(
                    {
                        "kind": "method_outcome",
                        "source_type": "controlled",
                        "weight": 1,
                        "topic_id": f"topic-{i % 12}",
                        "payload": {
                            "method": m,
                            "correct": bool(rng.random() < actual),
                            "expected": baseline,
                        },
                    }
                )
        wins += outcome_report(rows)["methods"][0]["method"] == "visual"
    assert wins >= 27, f"known visual benefit recovered in {wins}/30 simulations; chance=1/4"


def test_preference_screen_uses_real_adjusted_outcomes(services, learner):
    sid = learner["student_id"]
    loop = FailureLoop(services.state)
    services.state.set_preference(sid, "preferred_format", "explanation")
    assert loop.report(sid)["preference_vs_performance"] is None
    for method, correct, expected in (("visual", True, 0.4), ("explanation", True, 0.9)):
        for i in range(10):
            loop.record_outcome(
                sid,
                event_id=f"outcome-{method}-{i}",
                method=method,
                correct=correct,
                expected=expected,
                topic_id=str(i),
                question_kind="numeric",
                design="randomised",
            )
    row = loop.report(sid)["preference_vs_performance"]
    assert row["stated"] == "explanation" and row["measured"][0]["method"] == "visual"
    assert row["measured"][0]["ci"] and row["measured"][0]["n"] == 10
    assert "actual-minus-expected" in row["sentence"]


@pytest.fixture
def evidence_router(client):
    # Test-only wiring; production app.py is integrator-owned.
    from adapt.api.routes.evidence import router

    original = list(client.app.router.routes)
    client.app.include_router(router)
    yield
    client.app.router.routes[:] = original


def test_http_full_loop_and_isolation(
    client, services, learner, session, items, make_answer, evidence_router
):
    from adapt.services.attempts import item_payload

    item = next(i["item"] for i in items if i["item"]["kind"] == "numeric")
    response = client.post(
        "/api/v1/attempts",
        headers=learner["headers"],
        json={
            "session_id": session,
            "item_id": item["id"],
            "answer": make_answer(item["id"], correct=False),
            "client_events": [],
        },
    )
    assert response.status_code == 200, response.text
    attempt_id = response.json()["attempt_id"]
    missing = client.post(
        "/api/v1/evidence/failure", headers=learner["headers"], json={"attempt_id": attempt_id}
    )
    assert missing.status_code == 422
    assert "matched B" in missing.json()["detail"]["message"]
    with services.scope() as s:
        a = item_payload(s, item["id"])
    a.update(pair_id="http-pair", pair_role="A", concept_id="http-concept")
    b = {
        **a,
        "id": "http-b",
        "pair_role": "B",
        "stem_template": None,
        "stem": "What is two plus two?",
        "answer_spec": {"value": 4},
        "variant_spec": None,
    }
    cid = services.state.put_content(
        concept=a["topic_id"],
        kind="concept_pack",
        topic_id=a["topic_id"],
        payload={"matched_pairs": [{"a": a, "b": b}], "variants": variants()},
    )
    services.state.set_validation(cid, "validated", module="code")
    first = client.post(
        "/api/v1/evidence/failure", headers=learner["headers"], json={"attempt_id": attempt_id}
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert "answer_spec" not in first.text
    if body["stage"] == "prerequisite":
        qid = body["question"]["id"]
        answer = make_answer(qid, correct=True)
        response = client.post(
            f"/api/v1/evidence/failure/{attempt_id}/answer",
            headers=learner["headers"],
            json={"question_id": qid, "answer": answer},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    assert body["stage"] == "matched_b"
    done = client.post(
        f"/api/v1/evidence/failure/{attempt_id}/answer",
        headers=learner["headers"],
        json={"question_id": "http-b", "answer": "4"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["outcome"]["after_correct"] is True
    assert client.get(f"/api/v1/evidence/failure/{attempt_id}").json() == done.json()
    assert client.get("/api/v1/evidence").json()["methods"]
    other = client.post(
        "/api/v1/students", json={"display_name": "Another", "as_guest": True}
    ).json()
    assert client.get(f"/api/v1/evidence/failure/{attempt_id}").status_code == 404
    denied = client.post(
        "/api/v1/evidence/failure",
        headers={"x-csrf-token": other["csrf_token"]},
        json={"attempt_id": attempt_id},
    )
    assert denied.status_code == 404


def test_http_refuses_missing_pair_and_forged_grades(client, learner, evidence_router):
    assert (
        client.post(
            "/api/v1/evidence/failure", headers=learner["headers"], json={"attempt_id": "not-owned"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/evidence/failure",
            headers=learner["headers"],
            json={"attempt_id": "a", "correct": False},
        ).status_code
        == 422
    )
    assert client.post("/api/v1/evidence/failure", json={"attempt_id": "a"}).status_code == 403


def test_comparison_refuses_different_question_formats():
    from adapt.learning.methods import method_delta

    rows = [
        {"method": "visual", "correct": True, "expected_success": 0.5, "question_kind": "numeric"},
        {
            "method": "explanation",
            "correct": False,
            "expected_success": 0.5,
            "question_kind": "mcq",
        },
    ]
    result = method_delta(rows, method="visual", baseline="explanation")
    assert not result["comparable"] and "formats differ" in result["reason"]
    logged = [
        {
            "kind": "method_outcome",
            "source_type": "controlled",
            "weight": 1,
            "payload": {
                "method": r["method"],
                "correct": r["correct"],
                "expected": 0.5,
                "question_kind": r["question_kind"],
            },
        }
        for r in rows
    ]
    assert outcome_report(logged)["format_comparable"] is False
