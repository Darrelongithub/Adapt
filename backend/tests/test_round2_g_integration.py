"""Round 2, Phase G: the frozen contracts lined up over HTTP.

The whole point of the integration: a typed topic goes in at ``POST /api/v1/content/prepare``,
comes back as a validated Pack (built by Phase B's service, driven here by a scripted fake
model), and Phase C's three lesson moments then teach **that Pack** — over real HTTP routes,
through the real app, with the fake injected only where the app assembles its model client
(``services.ai2.llm``). Before the Phase G seam fix in ``routes/lesson.py``, the middle of this
chain was broken: the lesson routes rebuilt packs with ``force_simple=True`` and could never
see a prepared Pack.
"""

from __future__ import annotations

import importlib.util
import time
import uuid
from pathlib import Path

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "model" / "e2e_model.py"


def _load_e2e_model():
    spec = importlib.util.spec_from_file_location("round2_g_e2e_model", _FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(blocks) -> str:
    return " ".join(
        block["body"] if isinstance(block["body"], str) else str(block["body"])
        for block in blocks
    )


def test_typed_topic_flows_from_prepare_through_all_three_lesson_moments(
    client, learner, services, monkeypatch
):
    e2e = _load_e2e_model()
    fake = e2e.FakeAsyncLLM()
    monkeypatch.setattr(services.ai2, "llm", fake)

    topic = f"kite building {uuid.uuid4().hex[:8]}"

    # -- 1. the frozen prepare contract ------------------------------------ #
    prepared = client.post(
        "/api/v1/content/prepare",
        json={"topic": topic, "level": 2, "language": "en"},
        headers=learner["headers"],
    )
    assert prepared.status_code == 200, prepared.text
    job_id = prepared.json()["job_id"]

    state: dict = {}
    for _ in range(200):
        status = client.get(f"/api/v1/content/prepare/{job_id}", headers=learner["headers"])
        assert status.status_code == 200, status.text
        state = status.json()
        if state.get("state") != "running":
            break
        time.sleep(0.1)
    assert state.get("state") == "ready", state
    assert state.get("pack_source") == "model", state
    # the status surface stays honest: job state and source label, never topic or pack text
    assert topic not in str(state)

    from adapt.content.skeleton import cache_identity, canonical_topic_key, get_cached

    identity = cache_identity(canonical_topic_key(topic), level=2, language="en")
    with services.scope() as session:
        cached = get_cached(session, identity)
    assert cached is not None, "a ready job has to leave the validated pack in the cache"
    pack, _digest = cached
    assert pack.source == "model"
    concept = pack.concepts[0]
    item = next(
        (
            candidate
            for candidate in pack.items
            if candidate.concept_id == concept.id and candidate.options
        ),
        None,
    )
    assert item is not None, "the fake model pack must carry a multiple-choice item"

    # -- 2. moment one: the lesson is written for the PREPARED pack -------- #
    planned = client.post(
        "/api/v1/lesson/plan",
        json={"topic": topic, "concept_id": concept.id},
        headers=learner["headers"],
    )
    assert planned.status_code == 200, planned.text
    body = planned.json()
    plan = body["plan"]
    # The seam: this concept id exists only in the prepared pack. A route still rebuilding
    # with force_simple=True could not answer with it at all.
    assert plan["concept_id"] == concept.id, "the lesson must be written for the prepared concept"
    assert plan["blocks"]

    # The contract's shape: the deterministic lesson answers immediately and the checked
    # model lesson upgrades in place. Poll the upgrade job the response names.
    if plan["source"] == "simple" and body.get("job_id"):
        upgraded: dict = {}
        for _ in range(100):
            response = client.get(
                f"/api/v1/lesson/upgrade/{body['job_id']}", headers=learner["headers"]
            )
            assert response.status_code == 200, response.text
            upgraded = response.json()
            if upgraded.get("state") != "running":
                break
            time.sleep(0.1)
        assert upgraded.get("state") == "ready", upgraded
        plan = upgraded["plan"]
    assert plan["source"] == "written_for_you", plan["source"]
    assert plan["concept_id"] == concept.id
    assert any(block["type"] == "check" for block in plan["blocks"])

    # -- 3. moment two: the wrong answer starts from what was answered ----- #
    wrong = next((option for option in item.options if option.id != item.key), None)
    assert wrong is not None
    explained = client.post(
        "/api/v1/lesson/explain-wrong",
        json={"topic": topic, "item_id": item.id, "answer": wrong.text},
        headers=learner["headers"],
    )
    assert explained.status_code == 200, explained.text
    explanation = explained.json()["explanation"]
    assert explanation["method"] == "misconception_response"
    assert wrong.text in _render(explanation["blocks"])
    assert any(block["type"] == "check" for block in explanation["blocks"])

    # -- 4. moment three: re-teaching differs every time ------------------- #
    first = client.post(
        "/api/v1/lesson/reteach",
        json={"topic": topic, "concept_id": concept.id},
        headers=learner["headers"],
    )
    assert first.status_code == 200, first.text
    first_method = first.json()["explanation"]["method"]

    second = client.post(
        "/api/v1/lesson/reteach",
        json={"topic": topic, "concept_id": concept.id},
        headers=learner["headers"],
    )
    assert second.status_code == 200, second.text
    second_method = second.json()["explanation"]["method"]
    assert first_method != second_method, "a retry must not repeat the earlier method"

    # the fake only ever saw the frozen purposes
    assert set(fake.calls) <= {
        "content_skeleton",
        "content_items",
        "content_item_verifier",
        "ai3_lesson_plan",
        "ai3_explain_wrong",
        "ai3_reteach",
    }, fake.calls
