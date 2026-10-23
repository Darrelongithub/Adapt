"""Cross-agent wire-contract tests for the pitch demo & external engine API.

Locks down the four cross-agent seams documented in ``docs/PITCH_INTERFACES.md``:
1. Demo Mode flag schema + secrecy survival.
2. Passwordless consent-only guest session on ``POST /api/v1/students``.
3. Three-game baseline sequence (``codewords`` -> ``quick_a`` -> ``vault``) sealed
   via ``POST /api/v1/baseline/{run_id}/finish`` at ``stage == "shopkeeper"``.
4. Deterministic external content adaptation (``adapt_external_content``) for GenLink,
   including full ``ConfigurationSecrecyMiddleware`` key-preservation checks.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adapt.pitch_contracts import (
    DEMO_MEASURED_FACTORS,
    DEMO_STOP_STAGE,
    DEMO_THREE_GAME_STAGES,
    AdaptationError,
    DemoGuestRequest,
    DemoGuestResponse,
    DemoModeFlag,
    EngineActivityItem,
    EngineAdaptRequest,
    EngineContentInput,
    EngineLearnerInput,
    EngineQuestionInput,
    adapt_external_content,
)
from adapt.security.secrecy import scrub_public_payload


@pytest.fixture()
def isolated_client(client: TestClient) -> Iterator[TestClient]:
    """Fresh cookie jar against the session-scoped app (preserves process-wide cipher/services)."""
    fresh = TestClient(client.app, raise_server_exceptions=False)
    fresh.cookies.clear()
    yield fresh


def _sample_content() -> EngineContentInput:
    return EngineContentInput(
        concept="Adding fractions with unlike denominators",
        title="Adding Unlike Fractions",
        topic_id="fractions_add",
        explanation=[
            "Fractions can only be added directly when their denominators match.",
            "Rewrite each fraction as an equivalent fraction over a common denominator.",
            "Add the numerators and keep the common denominator unchanged.",
            "Simplify the resulting fraction if numerator and denominator share a factor.",
        ],
        worked_steps=[
            "Start with 1/2 + 1/3 and find the common denominator 6.",
            "Rewrite 1/2 as 3/6 and 1/3 as 2/6.",
            "Add the numerators: 3/6 + 2/6 = 5/6.",
        ],
        questions=[
            EngineQuestionInput(
                question="What is 1/4 + 1/2 written as a single fraction?",
                answer="3/4",
            )
        ],
        hints=["Rewrite 1/2 with denominator 4 before adding."],
        facts=["Denominators must match before numerators are added."],
    )


def test_all_pitch_response_schemas_survive_secrecy_middleware() -> None:
    """No field in any shared response contract may be stripped by secrecy.py."""
    flag_payload = DemoModeFlag(demo_mode=True).model_dump()
    assert scrub_public_payload(flag_payload) == flag_payload

    guest_payload = DemoGuestResponse(
        student_id="stu_demo123",
        csrf_token="csrf_tok_abc",
        display_name="Guest learner",
        demo_mode=True,
        consent_analytics=True,
    ).model_dump()
    assert scrub_public_payload(guest_payload) == guest_payload

    req = EngineAdaptRequest(
        learner_id="genlink-user-1",
        activity=[
            EngineActivityItem(
                item_id="q1",
                correct=False,
                latency_ms=1500,
                confidence=0.9,
                hint_used=True,
                method="explanation",
            ),
            EngineActivityItem(
                item_id="q2",
                correct=False,
                latency_ms=1900,
                confidence=0.85,
                hint_used=True,
                method="explanation",
            ),
            EngineActivityItem(
                item_id="q3",
                correct=True,
                latency_ms=8200,
                confidence=0.6,
                hint_used=False,
                method="worked_example",
            ),
        ],
        learner=EngineLearnerInput(language="en", tone="gentle", reading_level="plain"),
        content=_sample_content(),
    )
    resp = adapt_external_content(req)
    raw = resp.model_dump()
    scrubbed = scrub_public_payload(raw)
    assert scrubbed == raw


def test_external_adaptation_contrasts_struggling_vs_fluent_learners() -> None:
    """Different learner activity streams deterministically produce distinct adapted plans."""
    struggling_req = EngineAdaptRequest(
        learner_id="learner-struggling",
        activity=[
            EngineActivityItem(
                item_id=f"s-{i}",
                correct=False,
                latency_ms=1400,
                confidence=0.95,
                hint_used=True,
            )
            for i in range(4)
        ],
        learner=EngineLearnerInput(tone="gentle", reading_level="plain"),
        content=_sample_content(),
    )
    fluent_req = EngineAdaptRequest(
        learner_id="learner-fluent",
        activity=[
            EngineActivityItem(
                item_id=f"f-{i}",
                correct=True,
                latency_ms=5200,
                confidence=0.85,
                hint_used=False,
            )
            for i in range(6)
        ],
        learner=EngineLearnerInput(lead_format="diagram", tone="direct"),
        content=_sample_content(),
    )

    t0 = time.perf_counter()
    struggling_out = adapt_external_content(struggling_req)
    fluent_out = adapt_external_content(fluent_req)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 250.0
    assert struggling_out.mode == "deterministic"
    assert fluent_out.mode == "deterministic"

    # Struggling + fast-wrong + overconfident -> small steps, lower rung, predict check
    assert struggling_out.learner_model.step_size == "small"
    assert struggling_out.limits["max_words_per_block"] < fluent_out.limits["max_words_per_block"]
    assert struggling_out.ladder_rung < fluent_out.ladder_rung
    struggling_kinds = [b.kind for b in struggling_out.blocks]
    assert "predict" in struggling_kinds
    assert "early_guessing" in struggling_out.learner_model.signals
    assert "overconfident_errors" in struggling_out.learner_model.signals

    # Fluent + diagram preference -> large steps, visual lead, higher rung (teachback unlocked)
    assert fluent_out.learner_model.step_size == "large"
    assert fluent_out.lead_format == "visual"
    assert fluent_out.blocks[0].kind == "visual"
    assert fluent_out.ladder_rung >= 4
    assert any(b.kind == "explain" for b in fluent_out.blocks)


def test_external_adaptation_rejects_blank_concept() -> None:
    bad = EngineContentInput.model_construct(concept="   ", title="")
    req = EngineAdaptRequest.model_construct(
        learner_id=None,
        activity=[],
        learner=EngineLearnerInput(),
        content=bad,
    )
    with pytest.raises(AdaptationError):
        adapt_external_content(req)


def _answer_for_baseline_task(
    stage: str,
    task: dict[str, Any],
    *,
    quick_taps_done: int = 0,
) -> dict[str, Any]:
    """Deterministic valid response for each stage in DEMO_THREE_GAME_STAGES."""
    kind = task.get("kind")
    if stage == "codewords_study" or kind == "study":
        return {"ready": True}
    if stage == "codewords_cued":
        options = task.get("options") or ["apple"]
        return {"value": options[0]}
    if stage == "quick_a":
        if quick_taps_done >= 4:
            return {"action": "timeout"}
        options = task.get("options") or ["yes"]
        return {"value": options[0], "elapsed_ms": 1200}
    if stage == "vault":
        length = int(task.get("length") or len(task.get("sequence") or []) or 3)
        seq = task.get("sequence")
        if isinstance(seq, list) and seq:
            backward = task.get("answer_direction") == "backward"
            chosen = list(reversed(seq)) if backward else list(seq)
        else:
            chosen = list(range(length))
        return {"taps": chosen}
    return {"ready": True}


def test_guest_session_and_three_game_demo_baseline_flow(isolated_client: TestClient) -> None:
    """Verify the exact phone demo journey: consent-only guest -> 3 games -> finish analysis."""
    # 1. Public health endpoint works while signed out.
    health = isolated_client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    # 2. Passwordless consent-only guest session on POST /api/v1/students.
    guest_req = DemoGuestRequest(
        as_guest=True,
        consent_analytics=True,
        consent_share_with_llm=False,
    )
    reg = isolated_client.post("/api/v1/students", json=guest_req.model_dump())
    assert reg.status_code == 200, reg.text
    reg_body = reg.json()
    parsed_guest = DemoGuestResponse(**reg_body)
    assert parsed_guest.student_id.startswith("stu_")
    assert parsed_guest.csrf_token
    assert parsed_guest.consent_analytics is True
    assert parsed_guest.consent_share_with_ai is False

    me = isolated_client.get("/api/v1/me").json()
    assert me["student_id"] == parsed_guest.student_id
    assert me["display_name"] == "Guest learner"
    assert me["baseline_started"] is False

    headers = {"x-csrf-token": parsed_guest.csrf_token}

    # 3. Start baseline, set format + skip self-report questions, play the 3 demo games.
    started = isolated_client.post("/api/v1/baseline", json={}, headers=headers)
    assert started.status_code == 200, started.text
    view = started.json()
    run_id = view["run_id"]
    assert view["stage"] == "format"

    view = isolated_client.post(
        f"/api/v1/baseline/{run_id}/format",
        json={"value": "diagram"},
        headers=headers,
    ).json()
    assert view["stage"] == "questions"

    view = isolated_client.post(
        f"/api/v1/baseline/{run_id}/questions",
        json={"answers": {}},
        headers=headers,
    ).json()

    seen_stages: list[str] = []
    quick_taps_done = 0
    safety = 0
    while view.get("stage") in DEMO_THREE_GAME_STAGES and safety < 80:
        safety += 1
        stage = str(view["stage"])
        if not seen_stages or seen_stages[-1] != stage:
            seen_stages.append(stage)
        task = view["task"]
        resp = _answer_for_baseline_task(stage, task, quick_taps_done=quick_taps_done)
        if stage == "quick_a" and resp.get("action") != "timeout":
            quick_taps_done += 1
        r = isolated_client.post(
            f"/api/v1/baseline/{run_id}/trial",
            json={"task_id": task["id"], "response": resp, "latency_ms": 420},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        view = r.json()

    assert tuple(seen_stages) == DEMO_THREE_GAME_STAGES
    assert view["stage"] == DEMO_STOP_STAGE

    # 4. Seal immediately after Game 3 (`vault`) and verify the analysis snapshot.
    finished = isolated_client.post(
        f"/api/v1/baseline/{run_id}/finish",
        json={},
        headers=headers,
    )
    assert finished.status_code == 200, finished.text
    snap = finished.json()
    assert snap["state"] == "sealed"
    assert snap["snapshot_id"]
    factors_by_key = {f["key"]: f for f in snap["factors"]}
    for required_key in DEMO_MEASURED_FACTORS:
        assert factors_by_key[required_key]["state"] == "measured", factors_by_key[required_key]
        assert factors_by_key[required_key]["range"] is not None

    # Unplayed later games must be marked not_enough_data, never fabricated as zero.
    assert factors_by_key["reasoning"]["state"] == "not_enough_data"
    assert factors_by_key["reasoning"]["range"] is None
    assert "step_size" in snap["levers"]
    assert "lead_format_prior" in snap["levers"]


def test_demo_ui_three_game_tests_plan_grade_and_evidence_flow(
    isolated_client: TestClient,
) -> None:
    """Verify Agent 2's /demo API path: /students -> /tests/plan -> 3x /grade -> /evidence."""
    reg = isolated_client.post(
        "/api/v1/students",
        json={
            "as_guest": True,
            "display_name": "Demo guest",
            "consent_analytics": True,
            "consent_share_with_llm": False,
        },
    )
    assert reg.status_code == 200, reg.text
    headers = {"x-csrf-token": reg.json()["csrf_token"]}

    plan_res = isolated_client.post(
        "/api/v1/tests/plan",
        json={"subject": "Learning skills", "level": 2, "style": "see", "goal": "skills"},
        headers=headers,
    )
    assert plan_res.status_code == 200, plan_res.text
    plan = plan_res.json()
    demo_kinds = {
        "pattern_pick",
        "quick_recall",
        "fraction_bar",
        "cloze_word_bank",
        "geometry_angle",
        "geometry_congruent",
    }
    chosen = [it for it in plan["items"] if it.get("item_type") in demo_kinds][:3]
    assert len(chosen) == 3

    evidence_rows: list[dict[str, Any]] = []
    for item in chosen:
        params = item.get("parameters") or {}
        ans = (
            str(params.get("answer_indices") or "0,1,2")
            if item["item_type"] == "quick_recall"
            else "0"
        )
        graded = isolated_client.post(
            "/api/v1/tests/grade",
            json={
                "run_id": plan["run_id"],
                "item_id": item["id"],
                "grading_seal": item["grading_seal"],
                "answer": ans,
                "ms": 1350,
                "hints_used": 0,
            },
            headers=headers,
        )
        assert graded.status_code == 200, graded.text
        evidence_rows.append(
            {
                "item_id": item["id"],
                "grading_seal": item["grading_seal"],
                "item_type": item["item_type"],
                "skill": item.get("skill", item["item_type"]),
                "difficulty": int(item.get("difficulty", 2)),
                "answer": ans,
                "correct": bool(graded.json().get("correct")),
                "ms": 1350,
                "hints_used": 0,
                "parameters": params,
            }
        )

    saved = isolated_client.post(
        "/api/v1/tests/evidence",
        json={"run_id": plan["run_id"], "subject_id": None, "rows": evidence_rows},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text


def test_landed_integration_endpoint_end_to_end(isolated_client: TestClient) -> None:
    """Verify Agent 3's landed POST /api/v1/integration/adapt over HTTP + secrecy boundary."""
    res = isolated_client.post(
        "/api/v1/integration/adapt",
        json={
            "learner": {"id": "genlink-post-merge-check", "language": "en"},
            "activity": [
                {
                    "kind": "attempt",
                    "topic_id": "fractions_add",
                    "correct": False,
                    "response_ms": 1600,
                    "confidence": "certain",
                },
                {
                    "kind": "attempt",
                    "topic_id": "fractions_add",
                    "correct": False,
                    "response_ms": 1800,
                    "confidence": "certain",
                },
                {"kind": "hint", "topic_id": "fractions_add"},
                {
                    "kind": "attempt",
                    "topic_id": "fractions_add",
                    "correct": True,
                    "response_ms": 5900,
                },
            ],
            "content": {
                "concept": "Adding fractions with unlike denominators",
                "title": "Adding Unlike Fractions",
                "topic_id": "fractions_add",
                "explanation": [
                    "Fractions can only be added directly when their denominators match.",
                    "Rewrite each fraction over a common denominator, then add numerators.",
                ],
                "questions": [
                    {"question": "What is 1/4 + 1/2 as a single fraction?", "answer": "3/4"}
                ],
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["engine"]["local_only"] is True
    assert body["learner_id"] == "genlink-post-merge-check"
    assert body["adapted_content"]["blocks"]
    assert scrub_public_payload(body) == body
