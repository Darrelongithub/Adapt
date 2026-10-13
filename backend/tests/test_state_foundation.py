"""The Learner State Store (round brief section 2A): the contract, tested.

These are the claims the brief makes about the store, each one turned into something that would
fail if the claim stopped being true:

* one store, typed service layer, and the *only* way an observation gets in is the log
  (``test_an_attempt_lands_in_the_log``);
* the log is append-only and idempotent — a duplicate event id is one row, counted once, including
  when two writers race (``test_...duplicate...``);
* the model is a *derived* thing with versions, and a rebuild equals the incremental fold
  (``test_rebuild_equals_incremental`` / ``test_rebuild_equals_incremental_after_a_real_lesson``);
* the write-permission matrix is enforced, not documented (``test_a_module_cannot_write...``);
* no AI module imports another (``test_no_ai_module_imports_another``);
* plans and content are addressed by id, and a re-plan mid-lesson does not move the learner
  (``test_replan_mid_lesson_...``);
* snapshots are immutable (``test_a_snapshot_cannot_be_edited``);
* the internal component names never reach a learner
  (``test_learner_facing_copy_has_no_internal_component_names``).

Run: ``pytest -q tests/test_state_foundation.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "src" / "adapt"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _store(services):
    return services.state


def _ev(event_id: str, **kw):
    from adapt.state import EvidenceEvent

    kw.setdefault("kind", "attempt")
    kw.setdefault("topic_id", "top_shared_probe")
    return EvidenceEvent(event_id=event_id, **kw)


def _plan_items(client, learner) -> list[dict]:
    """The placement plan, unwrapped: each entry is ``{"item": {...}, "reason": ...}``."""
    response = client.get("/api/v1/assessment/diagnostic", headers=learner["headers"])
    assert response.status_code == 200, response.text
    return [entry["item"] for entry in response.json()["items"]]


def _answer_one(client, learner, item: dict, *, correct=True) -> dict:
    """One answer, posted through the API exactly as the app posts it."""
    from adapt.ai.local_tutor import eval_formula
    from adapt.services import current as _current
    from adapt.services import attempts as AT

    services = _current()
    with services.scope() as s:
        payload = AT.item_payload(s, item["id"])
    assert payload is not None, f"no stored payload for {item['id']}"
    spec = payload.get("answer_spec") or {}
    if spec.get("value") is not None:
        right = spec["value"]
        answer = right if correct else float(right) + 7
    elif spec.get("formula"):
        right = float(eval_formula(str(spec["formula"]), dict(spec.get("variables") or {})))
        answer = right if correct else right + 7
    elif spec.get("choices"):
        answer = spec["value"] if correct else next((c for c in spec["choices"] if c != spec.get("value")), "no")
    else:
        answer = "the right words, apparently" if correct else "a wrong sentence, deliberately"
    session_id = client.post(
        "/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]
    ).json()["session_id"]
    response = client.post(
        "/api/v1/attempts",
        json={"session_id": session_id, "item_id": item["id"], "answer": answer, "attempt_no": 1},
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def _open_session(client, learner, mode="practice"):
    response = client.post("/api/v1/sessions", json={"mode": mode}, headers=learner["headers"])
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


# --------------------------------------------------------------------------- #
# 1. the log is the only way in, and it is idempotent
# --------------------------------------------------------------------------- #
def test_the_store_is_built_with_the_rest_of_the_app(services):
    from adapt.state import LearnerState

    assert isinstance(services.state, LearnerState)
    assert services.state.sf is not None


def test_a_submitted_attempt_is_in_the_evidence_log(client, services, learner):
    before = services.state.evidence_count(learner["student_id"])
    receipt = _answer_one(client, learner, _plan_items(client, learner)[0])
    attempt_id = receipt["attempt_id"]
    rows = services.state.evidence_rows(learner["student_id"])
    assert len(rows) >= before + 1
    scored = [row for row in rows if row["kind"] == "attempt"]
    assert len(scored) == 1, "recovery metadata must not duplicate the scored answer"
    row = scored[0]
    assert row["event_id"] == f"att_{attempt_id}"
    assert row["kind"] == "attempt"
    assert row["source_type"] == "controlled"
    assert row["weight"] == pytest.approx(1.0)
    assert row["topic_id"]
    assert row["correct"] in (True, False)


def test_only_the_code_layer_may_write_evidence(services, learner):
    from adapt.state import WriteNotPermitted

    with pytest.raises(WriteNotPermitted):
        services.state.ingest(learner["student_id"], _ev("ev_ai_write"), module="ai1")


def test_a_duplicate_event_id_is_counted_once(services, learner):
    sid = learner["student_id"]
    first = services.state.ingest(sid, _ev("ev_dup_1", correct=True))
    second = services.state.ingest(sid, _ev("ev_dup_1", correct=True))
    assert first.accepted == 1 and first.duplicates == 0
    assert second.accepted == 0 and second.duplicates == 1
    assert services.state.evidence_count(sid) == 1
    services.state.advance_model(sid)
    model = services.state.get_snapshot(sid).model
    assert model["topics"][0]["n"] == 1, "a duplicate must not become a second observation"


def test_a_batch_with_a_duplicate_inside_is_counted_once(services, learner):
    sid = learner["student_id"]
    result = services.state.ingest(
        sid,
        [_ev("ev_batch_a", correct=True), _ev("ev_batch_b", correct=False), _ev("ev_batch_a", correct=True)],
    )
    assert (result.accepted, result.duplicates) == (2, 1)
    assert services.state.evidence_count(sid) == 2


def test_two_writers_racing_on_the_same_event_produce_one_row(services, learner):
    """The unique index is the arbiter, not a read-then-write in Python.

    Two threads ingest the same eight event ids at the same time. Whichever thread loses the race
    gets an IntegrityError from the index and reports it as a duplicate; the total must still be
    exactly eight rows and eight accepted-or-duplicate events. Without the constraint this test is
    flaky rather than failing, which is also why it asserts the *total* rather than a per-thread
    split.
    """
    import threading

    sid = learner["student_id"]
    events = [_ev(f"ev_race_{i}", correct=bool(i % 2)) for i in range(8)]
    results: list[tuple[int, int]] = []
    errors: list[BaseException] = []

    def worker():
        try:
            r = services.state.ingest(sid, events)
            results.append((r.accepted, r.duplicates))
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"a racing ingest raised instead of reporting a duplicate: {errors}"
    assert len(results) == 2
    assert sum(a for a, _ in results) == 8
    assert sum(d for _, d in results) >= 8
    assert services.state.evidence_count(sid) == 8


def test_an_observational_event_moves_the_estimate_less_than_a_controlled_one(services, learner):
    """``controlled`` weighs 1.0, an observation of the same event weighs about 0.4."""
    from adapt.state import WEIGHT_CONTROLLED, WEIGHT_OBSERVATIONAL, EvidenceEvent

    assert WEIGHT_CONTROLLED == 1.0
    assert 0.3 <= WEIGHT_OBSERVATIONAL <= 0.5
    sid = learner["student_id"]
    services.state.ingest_many(
        sid,
        [
            EvidenceEvent(event_id="ev_w_controlled", kind="attempt", topic_id="top_w_a", correct=True),
            EvidenceEvent(
                event_id="ev_w_observed",
                kind="attempt",
                topic_id="top_w_b",
                correct=True,
                source_type="observational",
            ),
        ],
    )
    services.state.advance_model(sid)
    model = services.state.get_snapshot(sid).model
    by_topic = {t["topic_id"]: t for t in model["topics"]}
    assert by_topic["top_w_a"]["n"] == 1 and by_topic["top_w_b"]["n"] == 1
    assert by_topic["top_w_a"]["p_know"] > by_topic["top_w_b"]["p_know"]
    assert model["observational"] == {"attempt": 1}


# --------------------------------------------------------------------------- #
# 2. versions, conflicts and rebuilds
# --------------------------------------------------------------------------- #
def test_advancing_the_model_with_no_new_evidence_does_not_mint_a_version(services, learner):
    sid = learner["student_id"]
    services.state.ingest(sid, _ev("ev_v1", correct=True))
    v1 = services.state.advance_model(sid)
    v2 = services.state.advance_model(sid)
    assert v1 == v2 == 1


def test_a_stale_expect_version_is_refused(services, learner):
    from adapt.state import ModelConflict

    sid = learner["student_id"]
    services.state.ingest(sid, _ev("ev_conflict_1", correct=True))
    services.state.advance_model(sid)
    services.state.ingest(sid, _ev("ev_conflict_2", correct=False))
    services.state.advance_model(sid)  # -> 2
    with pytest.raises(ModelConflict):
        services.state.advance_model(sid, expect_version=1)


def test_rebuild_equals_incremental(services, learner):
    sid = learner["student_id"]
    events = [_ev(f"ev_rb_{i}", correct=bool(i % 3)) for i in range(9)]
    services.state.ingest_many(sid, events)
    services.state.advance_model(sid)
    report = services.state.rebuild_model(sid, titles=services.state.title_map())
    assert report["equal"] is True, report
    assert report["version"] == report["previous_version"]


def test_rebuild_equals_incremental_after_a_real_lesson(client, services, learner):
    """The same claim, but the evidence was produced by answering items through the API."""
    sid = learner["student_id"]
    plan = _plan_items(client, learner)
    for i in range(4):
        _answer_one(client, learner, plan[i], correct=(i % 2 == 0))
    rows = services.state.evidence_rows(sid)
    assert len(rows) >= 4
    report = services.state.rebuild_model(sid, titles=services.state.title_map())
    assert report["equal"] is True, report
    assert report["evidence_count"] == len(rows)


def test_a_hand_fold_of_the_log_equals_the_stored_model(services, learner):
    from adapt.state.model import build_model

    sid = learner["student_id"]
    services.state.ingest_many(sid, [_ev(f"ev_fold_{i}", correct=bool(i % 2)) for i in range(6)])
    services.state.advance_model(sid)
    snapshot = services.state.get_snapshot(sid)
    from adapt.state import thaw

    folded = build_model(services.state.evidence_rows(sid), titles=services.state.title_map())
    assert thaw(snapshot.model) == folded


def test_the_model_carries_the_topic_title_not_a_bare_id():
    """Titles are carried forward on purpose: a curriculum edit must not silently rewrite history.

    The fold is a function of the log *given* the titles it is handed. What the ``previous``
    argument adds is that a topic the bank no longer lists keeps the title the learner saw — so a
    rebuild after a curriculum edit does not disagree with the incremental model about a title,
    which is the only field in the model that does not come from the learner's evidence.
    """
    from adapt.state.model import build_model

    rows = [
        {
            "id": "1",
            "kind": "attempt",
            "source_type": "controlled",
            "weight": 1.0,
            "topic_id": "top_title_probe",
            "correct": True,
            "occurred_at": "2026-01-01T00:00:00",
            "payload": {},
        }
    ]
    learner_saw_it = build_model(rows, titles={"top_title_probe": "Fractions on a number line"})
    bank_dropped_the_topic = build_model(rows, titles={}, previous=learner_saw_it)
    assert bank_dropped_the_topic == learner_saw_it
    assert bank_dropped_the_topic["topics"][0]["title"] == "Fractions on a number line"
    # A title the bank still supplies wins: keeping the old one forever would freeze a rename.
    renamed = build_model(rows, titles={"top_title_probe": "Fractions (2026 syllabus)"}, previous=learner_saw_it)
    assert renamed["topics"][0]["title"] == "Fractions (2026 syllabus)"


def test_a_rebuild_survives_a_curriculum_edit(services, learner):
    """The end-to-end version of the test above, through ``rebuild_model``."""
    sid = learner["student_id"]
    services.state.ingest(sid, _ev("ev_title_1", correct=True))
    services.state.advance_model(sid, titles={"top_shared_probe": "Fractions on a number line"})
    report = services.state.rebuild_model(sid, titles={})  # the bank no longer lists this topic
    assert report["equal"] is True, report


def test_a_model_version_row_is_immutable_in_practice(services, learner):
    """Reading version 1 after version 2 exists returns version 1's payload, not the newest."""
    from adapt.db.models import ModelVersion

    sid = learner["student_id"]
    services.state.ingest(sid, _ev("ev_imm_1", correct=True))
    services.state.advance_model(sid)
    with services.db.session() as s:
        first = dict(s.get(ModelVersion, (sid, 1)).model)
    services.state.ingest(sid, _ev("ev_imm_2", correct=True))
    services.state.advance_model(sid)
    with services.db.session() as s:
        still_first = dict(s.get(ModelVersion, (sid, 1)).model)
        second = dict(s.get(ModelVersion, (sid, 2)).model)
    assert still_first == first
    assert second != first


def test_the_snapshot_reports_which_version_it_was_built_from(services, learner):
    sid = learner["student_id"]
    services.state.ingest(sid, _ev("ev_ver_1", correct=True))
    services.state.advance_model(sid)
    snap = services.state.get_snapshot(sid)
    assert snap.model_version == 1
    assert snap.evidence_count == 1
    assert snap.evidence_upto


def test_a_new_learner_gets_version_zero_and_no_claims(client, learner, services):
    snapshot = services.state.get_snapshot(learner["student_id"])
    assert snapshot.model_version == 0
    assert snapshot.model == {}


def test_the_attempt_path_advances_the_model(client, services, learner):
    sid = learner["student_id"]
    assert services.state.latest_version(sid) == 0
    _answer_one(client, learner, _plan_items(client, learner)[0])
    assert services.state.latest_version(sid) >= 1
    assert services.state.get_snapshot(sid).model["topics"]


# --------------------------------------------------------------------------- #
# 3. the write-permission matrix
# --------------------------------------------------------------------------- #
def test_the_matrix_is_the_table_in_the_brief():
    from adapt.state.permissions import WRITE_MATRIX

    assert WRITE_MATRIX["ai1"] == {"learner_model", "hypotheses", "proposals"}
    assert WRITE_MATRIX["ai2"] == {"content"}
    assert WRITE_MATRIX["ai3"] == {"plans", "deliveries", "teaching_memory"}
    assert WRITE_MATRIX["code"] >= {"evidence", "scores", "approvals", "preferences", "session_state"}


def test_no_module_writes_outside_its_row():
    from adapt.state.permissions import DOMAINS, MODULES, WRITE_MATRIX, may_write

    for module in MODULES:
        for domain in DOMAINS:
            assert may_write(module, domain) == (domain in WRITE_MATRIX[module])


def test_every_module_may_read_every_domain():
    from adapt.state.permissions import DOMAINS, MODULES, may_read

    assert all(may_read(m, d) for m in MODULES for d in DOMAINS)


def test_a_module_cannot_write_another_modules_domain(services, learner):
    from adapt.state import WriteNotPermitted

    sid = learner["student_id"]
    with pytest.raises(WriteNotPermitted):
        services.state.advance_model(sid, module="ai2")  # content may not touch the learner model
    with pytest.raises(WriteNotPermitted):
        services.state.put_content(concept="x", kind="explanation", payload={}, module="ai1")
    with pytest.raises(WriteNotPermitted):
        services.state.put_plan(sid, plan={}, model_version=0, module="ai1")
    with pytest.raises(WriteNotPermitted):
        services.state.remember(sid, kind="explained", ref="concept", module="ai2")
    with pytest.raises(WriteNotPermitted):
        services.state.advance_session(sid, "lrs_perm", block="practice", module="ai1")


def test_the_permitted_write_goes_through(services, learner):
    sid = learner["student_id"]
    assert services.state.remember(sid, kind="explained", ref="top_x", module="ai3") is True
    assert services.state.memory(sid)["explained"] == ["top_x"]
    # idempotent: the same entry twice is one row
    assert services.state.remember(sid, kind="explained", ref="top_x", module="ai3") is False


def test_the_orchestrator_writes_each_turn_as_the_module_that_owns_it(services, learner):
    from adapt.state.orchestrator import Orchestrator

    sid = learner["student_id"]
    orch = Orchestrator(state=services.state, settings=services.settings, ai1=services.ai1, ai2=services.ai2, ai3=services.ai3)
    turn = orch.cycle(sid, session_id=_open_session_for(services, sid), blocks=[{"kind": "practice"}])
    assert turn.plan_id
    row = services.state.get_plan(turn.plan_id)
    assert row is not None and row["model_version"] == turn.model_version


def _open_session_for(services, sid):
    from adapt.db.models import LearningSession

    with services.db.session() as s:
        row = LearningSession(student_id=sid, mode="practice")
        s.add(row)
        s.flush()
        return row.id


# --------------------------------------------------------------------------- #
# 4. the orchestrator turn cycle
# --------------------------------------------------------------------------- #
def test_the_cycle_does_not_replan_when_the_model_has_not_moved(services, learner):
    from adapt.state.orchestrator import Orchestrator

    sid = learner["student_id"]
    session_id = _open_session_for(services, sid)
    orch = Orchestrator(state=services.state, settings=services.settings)
    first = orch.cycle(sid, session_id=session_id, blocks=[{"kind": "practice"}])
    second = orch.cycle(sid, session_id=session_id, blocks=[{"kind": "practice"}])
    assert first.plan_id == second.plan_id
    assert second.re_planned is False
    assert any("still matches" in n for n in second.notes)


def test_replan_mid_lesson_keeps_the_block_and_the_teaching_memory(services, learner):
    """The brief's "re-plan mid-lesson without restarting" claim, as an assertion."""
    from adapt.state.orchestrator import Orchestrator

    sid = learner["student_id"]
    session_id = _open_session_for(services, sid)
    orch = Orchestrator(state=services.state, settings=services.settings)
    first = orch.cycle(sid, session_id=session_id, blocks=[{"kind": "practice"}])
    # the learner is now two items into the lesson; the block is where they are
    services.state.advance_session(sid, session_id, block="item_3", topic_id="top_shared_probe")
    services.state.remember(sid, session_id=session_id, kind="explained", ref="top_a", module="ai3")
    before = services.state.session_state(sid, session_id)

    turn = orch.replan(
        sid,
        session_id,
        reason="two misses in a row",
        events=[_ev("ev_mid_lesson_miss", correct=False)],
        blocks=[{"kind": "reteach", "topic_id": "top_shared_probe"}],
    )

    assert turn.re_planned is True
    assert turn.plan_id != first.plan_id
    after = services.state.session_state(sid, session_id)
    assert after["block"] == before["block"] == "item_3", "the learner was moved mid-lesson"
    assert after["plan_id"] == turn.plan_id
    assert services.state.get_plan(first.plan_id)["superseded_by"] == turn.plan_id
    assert services.state.memory(sid)["explained"] == ["top_a"], "teaching memory was reset by a re-plan"
    assert turn.model_version > first.model_version, "the re-plan did not carry the new model version"
    assert any("re-plan reason: two misses in a row" in n for n in turn.notes)


def test_a_delivery_records_what_was_shown_and_why(services, learner):
    from adapt.state.orchestrator import Orchestrator

    sid = learner["student_id"]
    session_id = _open_session_for(services, sid)
    orch = Orchestrator(state=services.state, settings=services.settings)
    turn = orch.cycle(sid, session_id=session_id, blocks=[{"kind": "practice"}])
    delivery_id = orch.deliver(
        sid,
        block="practice",
        plan_id=turn.plan_id,
        session_id=session_id,
        seen={"items": ["itm_x"]},
        why={"reason": "weakest topic"},
        advance_to="item_1",
    )
    assert delivery_id
    assert services.state.session_state(sid, session_id)["block"] == "item_1"


def test_the_cycle_ingests_events_it_is_handed(services, learner):
    from adapt.state.orchestrator import Orchestrator

    sid = learner["student_id"]
    orch = Orchestrator(state=services.state, settings=services.settings)
    turn = orch.cycle(sid, session_id=_open_session_for(services, sid), events=[_ev("ev_cycle_1", correct=True)])
    assert turn.ingest.accepted == 1
    assert turn.model_version >= 1
    assert turn.model_advanced is True


# --------------------------------------------------------------------------- #
# 5. content is reused by id
# --------------------------------------------------------------------------- #
def test_a_content_id_is_the_hash_of_its_content(services):
    a = services.state.content_id({"concept": "fractions", "body": "text"})
    b = services.state.content_id({"body": "text", "concept": "fractions"})  # key order must not matter
    c = services.state.content_id({"concept": "fractions", "body": "different"})
    assert a == b
    assert a != c
    assert a.startswith("cpk_") and len(a) == 4 + 40


def test_putting_the_same_pack_twice_stores_one_row(services):
    payload = {"concept": "hash-probe", "body": "unchanged"}
    first = services.state.put_content(concept="hash-probe", kind="explanation", payload=payload, module="ai2")
    second = services.state.put_content(concept="hash-probe", kind="explanation", payload=dict(payload), module="ai2")
    assert first == second
    found = services.state.content_for("hash-probe", kind="explanation")
    assert [f["id"] for f in found] == [first]
    assert services.state.get_content(first)["payload"]["body"] == "unchanged"


def test_a_rejected_pack_is_not_reused(services):
    payload = {"concept": "reject-probe", "body": "bad"}
    cid = services.state.put_content(
        concept="reject-probe", kind="explanation", payload=payload, validation="rejected", module="ai2"
    )
    assert services.state.content_for("reject-probe", kind="explanation") == []
    assert services.state.get_content(cid)["validation"] == "rejected"
    assert services.state.set_validation(cid, "valid", notes=["re-checked"], module="code") is True
    assert [f["id"] for f in services.state.content_for("reject-probe", kind="explanation")] == [cid]
    # ...and the verdict is code's to give: AI 2 cannot certify its own pack.
    from adapt.state import WriteNotPermitted

    with pytest.raises(WriteNotPermitted):
        services.state.set_validation(cid, "rejected", module="ai2")
    # ...and the code layer cannot author content in the first place.
    with pytest.raises(WriteNotPermitted):
        services.state.put_content(concept="code-authored", kind="explanation", payload={}, module="code")


def test_ensure_content_reuses_an_existing_pack_without_calling_the_generator(services):
    from adapt.state.orchestrator import Orchestrator

    calls: list[str] = []

    def generator(**kw):
        calls.append(kw["concept"])
        return {"payload": {"concept": kw["concept"], "body": "generated"}, "notes": []}

    orch = Orchestrator(state=services.state, settings=services.settings, ai2=None)
    first = orch.ensure_content("reuse-probe", generator=generator)
    second = orch.ensure_content("reuse-probe", generator=generator)
    assert first == second
    assert calls == ["reuse-probe"], "the generator ran twice for content that already existed"


def test_ensure_content_refuses_to_invent_an_id(services):
    from adapt.state.orchestrator import Orchestrator

    orch = Orchestrator(state=services.state, settings=services.settings, ai2=None)
    with pytest.raises(LookupError):
        orch.ensure_content("nothing-authored-for-this")


# --------------------------------------------------------------------------- #
# 6. immutability of the snapshot
# --------------------------------------------------------------------------- #
def test_a_snapshot_cannot_be_edited(services, learner):
    sid = learner["student_id"]
    services.state.ingest(sid, _ev("ev_freeze_1", correct=True))
    services.state.advance_model(sid)
    snap = services.state.get_snapshot(sid)
    with pytest.raises(TypeError):
        snap.model["topics"][0]["p_know"] = 0.99  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):  # a tuple has no append
        snap.model["topics"].append({"topic_id": "invented"})  # type: ignore[union-attr]
    with pytest.raises(TypeError):
        snap.session["block"] = "somewhere else"  # type: ignore[index]
    assert snap.model["topics"][0]["p_know"] != 0.99


def test_thaw_gives_back_a_mutable_copy(services, learner):
    from adapt.state import thaw

    sid = learner["student_id"]
    services.state.ingest(sid, _ev("ev_thaw_1", correct=True))
    services.state.advance_model(sid)
    plain = thaw(services.state.get_snapshot(sid).model)
    plain["topics"][0]["p_know"] = 0.99
    assert services.state.get_snapshot(sid).model["topics"][0]["p_know"] != 0.99


# --------------------------------------------------------------------------- #
# 7. architecture rules
# --------------------------------------------------------------------------- #
def test_no_ai_module_imports_another():
    """AI 1, AI 2 and the scene narrator share the database, not Python objects.

    A cross-import is how "the AI proposes, code decides" quietly turns into one module using
    another's decision. Imports of *shared* code (``learning``, ``db``, ``crypto``) are expected.
    """
    ai_dir = SRC / "ai"
    # The intelligences, not every file in the package: ``prompts``/``guardrails`` are shared
    # vocabulary and importing those is the point.
    intelligences = ("ai1", "ai2", "scene_narrator", "local_tutor")
    modules = sorted(p.stem for p in ai_dir.glob("*.py") if p.stem != "__init__")
    assert set(intelligences) <= set(modules)
    offenders: list[str] = []
    for name in intelligences:
        text = (ai_dir / f"{name}.py").read_text(encoding="utf-8")
        for other in intelligences:
            if other == name:
                continue
            # ``from .ai1 import x`` / ``import adapt.ai.ai1`` / ``from adapt.ai.ai1 import x``
            if re.search(rf"^\s*(from|import)\s+[\.\w]*\b{other}\b", text, re.M):
                offenders.append(f"{name} -> {other}")
    assert not offenders, f"an AI module imported another AI module: {offenders}"


def test_the_state_package_imports_no_ai_module():
    """The store is below both intelligences: it must not know they exist."""
    offenders: list[str] = []
    for path in (SRC / "state").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+[\.\w]*\bai\b", text, re.M) or re.search(
            r"\bfrom\s+\.{1,2}ai\b", text
        ):
            offenders.append(path.name)
    assert not offenders, f"the store imported an AI module: {offenders}"


def test_the_scene_narrator_is_not_called_ai3_anywhere():
    """The brief renames the simulation narrator; the old module must be gone, not aliased."""
    assert not (SRC / "ai" / "ai3.py").exists(), "ai/ai3.py is back: 'AI 3' means the adaptation layer"
    text = (SRC / "ai" / "scene_narrator.py").read_text(encoding="utf-8")
    assert "class SceneNarrator" in text
    assert "class Narrator" not in text, "the old class name is still exported"


def test_learner_facing_copy_has_no_internal_component_names(client, learner):
    """No learner-facing string may say "AI 1", "AI 2" or "AI 3"."""
    internal = re.compile(r"\bAI[\s-]?[123]\b")
    checked: list[tuple[str, str]] = []

    placement = client.get("/api/v1/assessment/diagnostic")
    assert placement.status_code == 200, placement.text
    body = placement.json()
    for key in ("intro", "how_it_is_used", "note", "explain", "title"):
        if isinstance(body.get(key), str):
            checked.append((f"placement.{key}", body[key]))
    for i, item in enumerate(body.get("items") or []):
        for key, value in item.items():
            if isinstance(value, str) and key not in {"item_id", "topic_id"}:
                checked.append((f"placement.items[{i}].{key}", value))

    status = client.get("/api/v1/status")
    if status.status_code == 200:
        raw = json.dumps(status.json())
        for i, chunk in enumerate(re.findall(r'"[^"\\]{8,}"', raw)):
            if internal.search(chunk):
                checked.append((f"status.string[{i}]", chunk))

    bad = [(where, text) for where, text in checked if internal.search(text)]
    assert not bad, f"learner-facing copy names an internal component: {bad}"
    assert checked, "this test checked nothing, which is not a pass"


def test_the_status_payload_shows_three_named_components_with_their_roles(client):
    """The names are allowed *internally* — the operator's status view is where they belong."""
    status = client.get("/api/v1/status")
    assert status.status_code == 200, status.text
    payload = status.json()
    assert "narration" in payload, "the operator's status view should report the narrator"
    assert payload["narration"]["component"] == "scene narrator"
    # the audit label stays for continuity with rows already written; the human-facing name is
    # the one a screen prints
    assert payload["narration"]["role"] == "ai3"


# --------------------------------------------------------------------------- #
# 8. the operator's view of the store
# --------------------------------------------------------------------------- #
def test_the_cli_can_rebuild_a_model_and_reports_equality(client, learner):
    """``adapt rebuild-model`` is the brief's required command; this is it, actually run."""
    from typer.testing import CliRunner

    from adapt.cli import app as cli_app

    _answer_one(client, learner, _plan_items(client, learner)[0])
    result = CliRunner().invoke(cli_app, ["rebuild-model", "--for", learner["student_id"]])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    entry = body["learners"][0]
    assert entry["learner_id"] == learner["student_id"]
    assert entry["equal"] is True, entry
    assert entry["evidence_count"] >= 1


def test_the_cli_rebuild_dry_run_leaves_the_stored_model_alone(client, learner, services):
    """``--dry-run`` answers "did it drift?" without moving the version pointer."""
    from typer.testing import CliRunner

    from adapt.cli import app as cli_app

    _answer_one(client, learner, _plan_items(client, learner)[0])
    before = services.state.latest_version(learner["student_id"])
    result = CliRunner().invoke(cli_app, ["rebuild-model", "--for", learner["student_id"], "--dry-run"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)["learners"][0]
    assert body["equal"] is True
    assert body["version"] == before
    assert services.state.latest_version(learner["student_id"]) == before


def test_the_cli_prints_the_write_matrix(client):
    """The rule as data, from a shell: what each module may write, and what it may not."""
    from typer.testing import CliRunner

    from adapt.cli import app as cli_app

    result = CliRunner().invoke(cli_app, ["matrix"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["matrix"]["ai1"]["writes"] == ["hypotheses", "learner_model", "proposals"]
    assert ["ai1", "content"] in [list(pair) for pair in body["refused"]]
    assert ["code", "evidence"] not in [list(pair) for pair in body["refused"]]


def test_the_profile_endpoint_exposes_one_shared_snapshot(client, learner, services):
    """The API's read of the store: the same version number the model row carries."""
    sid = learner["student_id"]
    _answer_one(client, learner, _plan_items(client, learner)[0])
    response = client.get("/api/v1/profile", headers=learner["headers"])
    assert response.status_code == 200, response.text
    shared = response.json()["shared_state"]
    assert shared["learner_id"] == sid
    assert shared["model_version"] == services.state.latest_version(sid)
    assert shared["model"]["topics"], "the profile must show what the store actually holds"


# --------------------------------------------------------------------------- #
# 9. continuity: nothing is asked twice, nothing is explained twice
# --------------------------------------------------------------------------- #
def test_teaching_memory_survives_a_re_plan_and_a_new_session(services, learner):
    """Teaching memory is per learner, not per lesson: a restart must not re-explain."""
    sid = learner["student_id"]
    first = _open_session_for(services, sid)
    second = _open_session_for(services, sid)
    services.state.remember(sid, session_id=first, kind="explained", ref="concept:fractions", module="ai3")
    services.state.remember(sid, session_id=first, kind="example_shown", ref="itm_x", module="ai3")
    everything = services.state.memory(sid)
    assert everything["explained"] == ["concept:fractions"]
    assert everything["example_shown"] == ["itm_x"]
    this_session = services.state.memory(sid, session_id=second)
    assert this_session == {}, "the second session must not inherit the first one's *session* memory"
    snapshot = services.state.get_snapshot(sid, session_id=second)
    assert "concept:fractions" in snapshot.explained, (
        "the snapshot carries the learner's whole memory: skipping what was already explained is a "
        "promise that has to survive the lesson ending"
    )
    assert "concept:fractions" not in (snapshot.this_session.get("explained") or []), (
        "the session slice must not claim the first lesson's work happened in the second one"
    )


def test_the_snapshot_carries_what_was_already_asked(services, learner):
    """``already_asked`` is what stops the app asking for something it already has (brief 2A)."""
    sid = learner["student_id"]
    session_id = _open_session_for(services, sid)
    services.state.remember(
        sid, session_id=session_id, kind="asked", ref="q:how_many_minutes", text="20", module="ai3"
    )
    snapshot = services.state.get_snapshot(sid, session_id=session_id)
    assert "q:how_many_minutes" in snapshot.asked
    assert snapshot.already_asked("q:how_many_minutes") is True
    assert snapshot.already_asked("How many minutes do you have?") is False, (
        "an unanswered question must not be reported as already asked"
    )
    assert "q:how_many_minutes" in (snapshot.this_session.get("asked") or [])


# --------------------------------------------------------------------------- #
# 10. the live serving path writes through the store (brief 2A "migrate the paths")
# --------------------------------------------------------------------------- #
def test_starting_a_lesson_records_a_plan_with_the_model_version(client, learner, services):
    session_id = _open_session(client, learner)
    state = services.state.session_state(learner["student_id"], session_id)
    assert state["plan_id"], "the lesson started without a plan row"
    plan = services.state.get_plan(state["plan_id"])
    assert plan["session_id"] == session_id
    assert plan["model_version"] == services.state.latest_version(learner["student_id"])
    assert plan["plan"]["blocks"], "a plan with no blocks is not a plan"


def test_answering_an_item_records_a_delivery_with_its_why(client, learner, services):
    from adapt.db.models import DeliveryRecord

    sid = learner["student_id"]
    session_id = _open_session(client, learner)
    receipt = _answer_one_with_session(client, learner, session_id)
    with services.db.session() as s:
        rows = (
            s.query(DeliveryRecord)
            .filter(DeliveryRecord.student_id == sid)
            .order_by(DeliveryRecord.created_at.desc())
            .all()
        )
    assert rows, "the attempt was answered but nothing was recorded as delivered"
    row = rows[0]
    assert row.block == f"item:{receipt['item_id']}"
    assert row.seen["item_id"] == receipt["item_id"]
    assert row.why["model_version"] >= 1
    assert row.session_id == session_id


def test_the_practice_queue_records_what_it_served(client, learner, services):
    from adapt.db.models import DeliveryRecord

    response = client.get("/api/v1/practice?count=3", headers=learner["headers"])
    assert response.status_code == 200, response.text
    served = [p["item"]["id"] for p in response.json()["items"]]
    assert served
    with services.db.session() as s:
        rows = (
            s.query(DeliveryRecord)
            .filter(DeliveryRecord.student_id == learner["student_id"], DeliveryRecord.block == "practice_queue")
            .all()
        )
    assert rows, "the queue was served without a delivery row"
    assert rows[-1].seen["items"] == served


def test_ai2_reads_the_learner_level_from_the_shared_snapshot(client, learner, services):
    """AI 2's context carries the store's immutable, versioned view — not a private query."""
    _answer_one(client, learner, _plan_items(client, learner)[0])
    ctx = services.ai2.context(learner["student_id"])
    assert ctx["snapshot"] is not None, "AI 2 has no state store wired in"
    assert ctx["model_version"] == services.state.latest_version(learner["student_id"])
    assert ctx["model_version"] >= 1
    assert ctx["snapshot"].mastery, "AI 2's view of the learner's level is empty after evidence"
    with pytest.raises(TypeError):
        ctx["snapshot"].model["topics"][0]["p_know"] = 0.5  # type: ignore[index]


def _answer_one_with_session(client, learner, session_id: str) -> dict:
    from adapt.services import current as _current
    from adapt.services import attempts as AT

    item = _plan_items(client, learner)[0]
    services = _current()
    with services.scope() as s:
        payload = AT.item_payload(s, item["id"])
    spec = payload.get("answer_spec") or {}
    answer = spec.get("value") if spec.get("value") is not None else "an answer"
    response = client.post(
        "/api/v1/attempts",
        json={"session_id": session_id, "item_id": item["id"], "answer": answer, "attempt_no": 1},
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_wrong_answer_moves_the_plan_without_restarting_the_lesson(client, learner, services):
    """The brief's headline behaviour, end to end on the store: miss an item, and the lesson's
    plan is rebuilt from a new model version while the learner keeps the block they were on."""
    from adapt.state import EvidenceEvent

    sid = learner["student_id"]
    session_id = _open_session(client, learner)
    first = services.state.session_state(sid, session_id)
    assert first["plan_id"]
    services.state.advance_session(sid, session_id, block="item:q3")

    turn = services.orchestrator.replan(
        sid,
        session_id,
        reason="wrong answer on the current item",
        events=[
            EvidenceEvent(
                event_id="ev_failure_loop_1",
                kind="attempt",
                topic_id="top_shared_probe",
                item_id="itm_x",
                correct=False,
                session_id=session_id,
            )
        ],
        blocks=[{"kind": "reteach", "topic_id": "top_shared_probe"}],
    )

    after = services.state.session_state(sid, session_id)
    assert after["plan_id"] == turn.plan_id != first["plan_id"]
    assert after["block"] == "item:q3", "the learner was restarted by the re-plan"
    assert services.state.get_plan(first["plan_id"])["superseded_by"] == turn.plan_id
    old_plan = services.state.get_plan(first["plan_id"])
    new_plan = services.state.get_plan(turn.plan_id)
    assert new_plan["model_version"] > old_plan["model_version"]


def test_a_learner_with_nothing_measured_has_no_version_row(services, learner):
    """Version 0 is the cold start; the first *minted* version is the first with evidence behind it."""
    from adapt.db.models import ModelVersion

    sid = learner["student_id"]
    assert services.state.advance_model(sid) == 0
    report = services.state.rebuild_model(sid, titles={})
    assert report["equal"] is True and report["version"] == 0
    with services.db.session() as s:
        assert s.query(ModelVersion).filter(ModelVersion.student_id == sid).count() == 0
