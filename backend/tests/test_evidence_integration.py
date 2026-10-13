"""Production-only wiring: no test router registration and no client-owned evidence."""

from types import SimpleNamespace

import pytest

from adapt.learning.failure import FailureLoop
from adapt.learning.methods import METHODS
from adapt.services.attempts import item_payload


def practice(client, learner):
    response = client.get("/api/v1/practice?count=12", headers=learner["headers"])
    assert response.status_code == 200, response.text
    return next(p for p in response.json()["items"] if p["item"]["kind"] == "numeric")


def submit(client, learner, session, item_id, answer, **extra):
    response = client.post(
        "/api/v1/attempts",
        headers=learner["headers"],
        json={"session_id": session, "item_id": item_id, "answer": answer, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def outcomes(services, learner):
    return [
        r
        for r in services.state.evidence_rows(learner["student_id"])
        if r["kind"] == "method_outcome"
    ]


def test_server_delivery_owns_method_design_and_prediction(
    client,
    learner,
    session,
    services,
    make_answer,
):
    served = practice(client, learner)
    item_id = served["item"]["id"]
    offer = next(
        r["payload"]
        for r in services.state.evidence_rows(learner["student_id"])
        if r["kind"] == "method_offered" and r["item_id"] == item_id
    )
    fake_method = next(m for m in METHODS if m != offer["method"])
    receipt = submit(
        client,
        learner,
        session,
        item_id,
        make_answer(item_id, correct=True),
        method=fake_method,
        method_design="assigned_by_adaptation",
        expected_success=0.99,
    )
    assert receipt["teaching"]["method"] == offer["method"]
    assert receipt["teaching"]["design"] == offer["design"]
    rows = outcomes(services, learner)
    assert len(rows) == 1
    assert rows[0]["payload"]["expected"] == pytest.approx(offer["expected"])
    assert rows[0]["payload"]["expected"] != 0.99
    assert rows[0]["source_type"] == "controlled"
    assert rows[0]["weight"] == 1
    assert sum(m["n"] for m in receipt["method_compare"]["methods"]) == 1
    model = services.state.get_snapshot(learner["student_id"]).model
    assert model["method_evidence"]["methods"][0]["n"] == 1
    assert services.state.rebuild_model(learner["student_id"])["equal"] is True


def test_unserved_echo_cannot_manufacture_controlled_evidence(
    client,
    learner,
    session,
    services,
    items,
    make_answer,
):
    item_id = items[0]["item"]["id"]
    receipt = submit(
        client,
        learner,
        session,
        item_id,
        make_answer(item_id, correct=True),
        method="visual",
        method_design="randomised",
        expected_success=0.99,
    )
    assert receipt["teaching"]["design"] == "observational"
    (row,) = outcomes(services, learner)
    assert row["source_type"] == "observational" and row["weight"] == 0.4
    assert row["payload"]["expected"] != 0.99


def test_placement_is_excluded_even_after_a_practice_offer(
    client,
    learner,
    services,
    make_answer,
):
    item_id = practice(client, learner)["item"]["id"]
    session = client.post(
        "/api/v1/sessions",
        headers=learner["headers"],
        json={"mode": "diagnostic"},
    ).json()["session_id"]
    receipt = submit(
        client,
        learner,
        session,
        item_id,
        make_answer(item_id, correct=True),
        method_design="randomised",
    )
    assert receipt["teaching"]["design"] == "placement"
    assert receipt["next_step"] is None and receipt["method_compare"] is None
    assert not outcomes(services, learner)


def test_missing_checked_content_defers_failed_a_everywhere(
    client,
    learner,
    session,
    services,
    make_answer,
    monkeypatch,
):
    item_id = practice(client, learner)["item"]["id"]
    monkeypatch.setattr(services.state, "content_for", lambda *a, **k: [])
    receipt = submit(client, learner, session, item_id, make_answer(item_id, correct=False))
    assert receipt["next_step"]["stage"] == "unavailable"
    assert "no comparison was invented" in receipt["next_step"]["note"]
    assert not outcomes(services, learner)
    assert not receipt["method_compare"]["methods"]
    report = client.get("/api/v1/methods/report", headers=learner["headers"]).json()
    assert report["n_attributed"] == 0 and report["n_scored"] == 1
    assert all(m["n"] == 0 and m["actual"] is None for m in report["methods"])


def test_failed_a_starts_registered_recovery_then_replans_without_duplicate_credit(
    client,
    learner,
    session,
    services,
    make_answer,
    monkeypatch,
    request,
):
    sid = learner["student_id"]
    item_id = practice(client, learner)["item"]["id"]
    with services.scope() as s:
        a = item_payload(s, item_id)
    a.update(pair_id="integration-pair", pair_role="A", concept_id="integration-concept")
    b = {
        **a,
        "id": "integration-b",
        "pair_role": "B",
        "stem_template": None,
        "stem": "What is two plus two?",
        "answer_spec": {"value": 4},
        "variant_spec": None,
    }
    # Synthetic checked content is explicitly test-only; never a production fallback.
    cid = services.state.put_content(
        concept=a["topic_id"],
        topic_id=a["topic_id"],
        kind="concept_pack",
        payload={
            "matched_pairs": [{"a": a, "b": b}],
            "variants": {m: {"text": f"Checked test teaching: {m}"} for m in METHODS},
        },
    )
    services.state.set_validation(cid, "validated", module="code")
    # Registry is shared across the test session; do not leak this checked fixture.
    request.addfinalizer(lambda: services.state.set_validation(cid, "unvalidated", module="code"))
    lookup = services.state.content_for
    monkeypatch.setattr(
        services.state,
        "content_for",
        lambda concept: [r for r in lookup(concept) if r["id"] == cid],
    )
    original_plan = services.state.session_state(sid, session)["plan_id"]
    services.state.advance_session(sid, session, block="item:keep-my-place")
    receipt = submit(client, learner, session, item_id, make_answer(item_id, correct=False))
    recovery = receipt["next_step"]
    assert recovery["stage"] in ("prerequisite", "matched_b")
    assert recovery["why"]["content_ids"] == [cid]
    assert not outcomes(services, learner), "A must await prerequisite/fatigue diagnosis"
    assert services.state.session_state(sid, session)["plan_id"] != original_plan
    loop_id = receipt["attempt_id"]
    url = f"/api/v1/evidence/failure/{loop_id}/answer"
    if recovery["stage"] == "prerequisite":
        qid = recovery["question"]["id"]
        answer = client.post(
            url,
            headers=learner["headers"],
            json={"question_id": qid, "answer": make_answer(qid, correct=True)},
        )
        assert answer.status_code == 200, answer.text
        recovery = answer.json()
    assert recovery["stage"] == "matched_b"
    request = {"question_id": recovery["question"]["id"], "answer": 4}
    done = client.post(url, headers=learner["headers"], json=request)
    assert done.status_code == 200, done.text
    assert done.json()["stage"] == "complete"
    assert len(outcomes(services, learner)) == 2
    replay = client.post(url, headers=learner["headers"], json=request)
    assert replay.status_code == 200 and replay.json() == done.json()
    assert len(outcomes(services, learner)) == 2
    state = services.state.session_state(sid, session)
    assert state["block"] == "item:keep-my-place"
    model = services.state.get_snapshot(sid).model
    assert sum(m["n"] for m in model["method_evidence"]["methods"]) == 2
    assert services.state.rebuild_model(sid)["equal"] is True


def test_baseline_freezes_real_method_comparison_not_a_format_guess(services, learner):
    sid = learner["student_id"]
    services.state.set_preference(sid, "preferred_format", "diagram", source="baseline")
    loop = FailureLoop(services.state)
    for method, correct in (("visual", True), ("explanation", False)):
        for i in range(8):
            loop.record_outcome(
                sid,
                event_id=f"freeze:{method}:{i}",
                method=method,
                correct=correct,
                expected=0.5,
                topic_id="test-only-topic",
                question_kind="numeric",
                design="randomised",
            )
    run = services.baseline.start(sid, seed=2)
    draft = services.baseline.snapshot(sid)
    assert draft["preference_vs_performance"]["measured"][0]["method"] == "visual"
    sealed = services.baseline.finish(sid, run["run_id"])
    comparison = sealed["preference_vs_performance"]
    assert comparison == draft["preference_vs_performance"]
    assert comparison["stated"] == "diagram"
    services.state.set_preference(sid, "preferred_format", "retrieval", source="settings")
    loop.record_outcome(
        sid,
        event_id="freeze:later",
        method="visual",
        correct=False,
        expected=0.5,
        topic_id="test-only-topic",
        question_kind="numeric",
        design="randomised",
    )
    assert services.baseline.snapshot(sid)["preference_vs_performance"] == comparison


def test_content_pipeline_does_not_claim_validation_without_a_checker(
    services,
    learner,
    monkeypatch,
):
    def generator(**kwargs):
        return {"payload": {"text": "test-only content"}}

    concept = f"unchecked:{learner['student_id']}"
    cid = services.orchestrator.ensure_content(concept, generator=generator)
    assert services.state.get_content(cid)["validation"] == "unvalidated"
    monkeypatch.setattr(
        services.orchestrator,
        "ai2",
        SimpleNamespace(
            validate_pack=lambda payload: {"ok": True, "notes": ["test-only checker"]},
        ),
    )
    checked = services.orchestrator.ensure_content(f"checked:{concept}", generator=generator)
    assert services.state.get_content(checked)["validation"] == "validated"
    monkeypatch.setattr(
        services.orchestrator,
        "ai2",
        SimpleNamespace(
            validate_pack=lambda payload: {},
        ),
    )
    refused = services.orchestrator.ensure_content(f"refused:{concept}", generator=generator)
    assert services.state.get_content(refused)["validation"] == "rejected"


@pytest.mark.parametrize(
    "event", ["hint_opened", "example_opened", "visual_opened", "explanation_opened"]
)
def test_voluntary_support_is_observational_not_randomised(
    client,
    learner,
    session,
    services,
    make_answer,
    event,
):
    item_id = practice(client, learner)["item"]["id"]
    recorded = client.post(
        "/api/v1/events",
        headers=learner["headers"],
        json={
            "session_id": session,
            "events": [
                {"type": "question_shown", "item_id": item_id, "payload": {}},
                {"type": event, "item_id": item_id, "payload": {}},
            ],
        },
    )
    assert recorded.status_code == 200, recorded.text
    receipt = submit(client, learner, session, item_id, make_answer(item_id, correct=True))
    assert receipt["teaching"]["design"] == "observational"
    (row,) = outcomes(services, learner)
    assert row["source_type"] == "observational" and row["weight"] == 0.4


def test_repeating_the_same_question_does_not_create_another_controlled_exposure(
    client,
    learner,
    session,
    services,
    make_answer,
):
    item_id = practice(client, learner)["item"]["id"]
    for _ in range(2):
        submit(client, learner, session, item_id, make_answer(item_id, correct=True))
    rows = outcomes(services, learner)
    assert [row["source_type"] for row in rows] == ["controlled", "observational"]
    assert [row["weight"] for row in rows] == [1, 0.4]
