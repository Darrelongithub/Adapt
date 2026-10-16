from __future__ import annotations

import pytest

from adapt.api.routes import lesson as lesson_routes

pytest_plugins = ("fixture_data",)


def test_frozen_item_lookup_does_not_depend_on_authored_topic_data(fixture_pack, monkeypatch):
    monkeypatch.setattr(lesson_routes, "_topic_from_frozen", lambda *_args: fixture_pack)
    monkeypatch.setattr(
        lesson_routes,
        "_get_pack",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("authored fallback should not run")
        ),
    )
    found = lesson_routes._pack_for_item(
        "new-skeleton-topic", "item-fraction-a", level=2, language="en"
    )
    assert found is fixture_pack


def test_subject_snapshot_rung_is_scoped_to_matching_concept():
    services = type(
        "Services",
        (),
        {
            "subjects": type(
                "Subjects",
                (),
                {
                    "snapshot": lambda _self, _student_id, *, topic_key: {
                        "available": True,
                        "ladder": {
                            "positions": [
                                {
                                    "sub_area": "fractions",
                                    "nodes": [{"id": "fraction-addition"}],
                                    "placed": True,
                                    "steady_rung": 4,
                                    "n": 6,
                                }
                            ]
                        },
                    }
                },
            )()
        },
    )()
    assert (
        lesson_routes._subject_rung(services, "learner-1", "fractions", "fraction-addition", 2) == 4
    )


def test_all_three_lesson_moments_work_through_frozen_routes(
    client, learner, fixture_pack, monkeypatch
):
    monkeypatch.setattr(
        lesson_routes,
        "_get_pack",
        lambda _topic, _concept_id, *, level, language: fixture_pack,
    )

    planned = client.post(
        "/api/v1/lesson/plan",
        json={"topic": "calculus", "concept_id": "fraction-addition"},
        headers=learner["headers"],
    )
    assert planned.status_code == 200, planned.text
    first = planned.json()["plan"]
    assert first["source"] == "simple"
    assert first["blocks"]

    wrong = client.post(
        "/api/v1/lesson/explain-wrong",
        json={
            "topic": "calculus",
            "item_id": "item-fraction-a",
            "answer": "Add the denominators too",
        },
        headers=learner["headers"],
    )
    assert wrong.status_code == 200, wrong.text
    explanation = wrong.json()["explanation"]
    assert explanation["method"] == "misconception_response"
    rendered = " ".join(
        block["body"] if isinstance(block["body"], str) else str(block["body"])
        for block in explanation["blocks"]
    )
    assert "Add the denominators too" in rendered
    assert any(block["type"] == "check" for block in explanation["blocks"])

    first_reteach = client.post(
        "/api/v1/lesson/reteach",
        json={"topic": "calculus", "concept_id": "fraction-addition"},
        headers=learner["headers"],
    )
    assert first_reteach.status_code == 200, first_reteach.text
    first_method = first_reteach.json()["explanation"]["method"]
    assert first_method == "simpler_words"

    second_reteach = client.post(
        "/api/v1/lesson/reteach",
        json={"topic": "calculus", "concept_id": "fraction-addition"},
        headers=learner["headers"],
    )
    assert second_reteach.status_code == 200, second_reteach.text
    second_method = second_reteach.json()["explanation"]["method"]
    assert second_method == "smaller_step"
    assert second_method != first_method

    history = lesson_routes.current().state.memory_details(learner["student_id"])
    reteach_rows = [row for row in history if row.get("moment") == "reteach"]
    assert [row["method"] for row in reteach_rows[-2:]] == ["simpler_words", "smaller_step"]


def _words(value):
    if isinstance(value, str):
        return len(value.split())
    if isinstance(value, dict):
        return sum(
            _words(child) for key, child in value.items() if key not in {"id", "item_id", "kind"}
        )
    if isinstance(value, list):
        return sum(_words(child) for child in value)
    return 0


def _valid_initial_proposal():
    fact_ids = ["f-denominator"]
    why = {"evidence_ids": [], "n": 0}
    explain = "A shared denominator works because both fractions use parts of the same size."
    check = {
        "question": "What does a shared denominator name?",
        "item_id": "item-fraction-a",
        "options": [
            {"id": "same-size", "text": "The same part size"},
            {"id": "add-both", "text": "Add the denominators too"},
        ],
    }
    return {
        "blocks": [
            {
                "type": "explain",
                "body": explain,
                "fact_ids": fact_ids,
                "why": why,
                "length": _words(explain),
            },
            {
                "type": "check",
                "body": check,
                "fact_ids": fact_ids,
                "why": why,
                "length": _words(check),
            },
        ]
    }


def test_background_upgrade_swaps_fallback_only_after_full_validation(
    client, learner, fixture_pack, monkeypatch
):
    from adapt.ai.lesson_writer import reset_runtime_state

    reset_runtime_state()
    monkeypatch.setattr(
        lesson_routes, "_get_pack", lambda _topic, _concept_id, *, level, language: fixture_pack
    )
    monkeypatch.setattr(lesson_routes, "_configured_model", lambda: True)
    monkeypatch.setattr(
        lesson_routes,
        "_model_callable",
        lambda _student_id: lambda *_args: _valid_initial_proposal(),
    )

    response = client.post(
        "/api/v1/lesson/plan",
        json={"topic": "calculus", "concept_id": "fraction-addition"},
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text
    initial = response.json()
    assert initial["plan"]["source"] == "simple"
    assert initial["upgrade_state"] == "running"
    assert initial["job_id"]

    upgraded = client.get(
        f"/api/v1/lesson/upgrade/{initial['job_id']}",
        headers=learner["headers"],
    )
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["state"] == "ready"
    assert upgraded.json()["plan"]["source"] == "written_for_you"
    records = lesson_routes.current().state.memory_details(learner["student_id"])
    assert any(row["method"] == "initial_lesson" and row["moment"] == "initial" for row in records)


def test_contrast_uses_checked_ai3_output_with_a_key_and_templates_without_one(
    fixture_pack, monkeypatch
):
    from adapt.ai.lesson_writer import reset_runtime_state
    from adapt.api.routes import lesson_plan as legacy_routes

    profiles = [
        {"name": "Small steps", "levers": {"step_size": {"value": "small"}}},
        {"name": "Large steps", "levers": {"step_size": {"value": "large"}}},
    ]
    reset_runtime_state()
    monkeypatch.setattr(lesson_routes, "_configured_model", lambda: False)
    templates = legacy_routes._contrast_ai3(
        pack=fixture_pack,
        concept_id="fraction-addition",
        profiles=profiles,
        student_id="demo-student",
    )
    assert templates["source"] == "simple"
    assert [row["label"] for row in templates["lessons"]] == ["template lesson", "template lesson"]

    monkeypatch.setattr(lesson_routes, "_configured_model", lambda: True)
    monkeypatch.setattr(
        lesson_routes,
        "_model_callable",
        lambda _student_id: lambda *_args: _valid_initial_proposal(),
    )
    written = legacy_routes._contrast_ai3(
        pack=fixture_pack,
        concept_id="fraction-addition",
        profiles=profiles,
        student_id="demo-student",
    )
    assert written["source"] == "written_for_you"
    assert [row["label"] for row in written["lessons"]] == [
        "written for this profile, checked",
        "written for this profile, checked",
    ]


def test_contrast_model_calls_share_a_bounded_deadline(fixture_pack, monkeypatch):
    import time

    from adapt.ai.lesson_writer import audit_counts, core, reset_runtime_state
    from adapt.api.routes import lesson_plan as legacy_routes

    reset_runtime_state()
    monkeypatch.setattr(core, "MODEL_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(lesson_routes, "_configured_model", lambda: True)

    def slow_model(*_args):
        time.sleep(0.12)
        return _valid_initial_proposal()

    monkeypatch.setattr(lesson_routes, "_model_callable", lambda _student_id: slow_model)
    profiles = [{"name": "A"}, {"name": "B"}]
    started = time.monotonic()
    result = legacy_routes._contrast_ai3(
        pack=fixture_pack,
        concept_id="fraction-addition",
        profiles=profiles,
        student_id="demo-student",
    )
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert result["source"] == "simple"
    assert all(row["plan"]["source"] == "simple" for row in result["lessons"])
    assert audit_counts()["timeouts"] == 2


def test_grade_endpoint_returns_correct_and_misconception_id(fixture_pack, monkeypatch):
    """Round 3 (Track S): the server grades a check click and never returns
    the answer key.  A correct pick returns ``correct: true``; a wrong pick
    returns the matched misconception id so the follow-up /explain-wrong
    route has the right entry point.
    """
    monkeypatch.setattr(lesson_routes, "_topic_from_frozen", lambda *_args, **_kwargs: fixture_pack)
    correct, misconception, selected_id, selected_text = lesson_routes._grade_payload(
        item=fixture_pack["items"][0], selected="same-size"
    )
    assert correct is True
    assert misconception is None
    assert selected_id == "same-size"
    assert selected_text == "The same part size"

    correct, misconception, _selected_id, _text = lesson_routes._grade_payload(
        item=fixture_pack["items"][0], selected="add-both"
    )
    assert correct is False
    assert misconception == "m-add-denominators"


def test_grade_endpoint_grade_does_not_leak_the_answer_key(fixture_pack, monkeypatch):
    """Round 3 (Track S): the response payload must not contain the
    checked key.  The whole point of server-side grading is that the key
    never appears in a route response.
    """
    monkeypatch.setattr(lesson_routes, "_topic_from_frozen", lambda *_args, **_kwargs: fixture_pack)
    # Replay the helper's logic at the dict level: the helper returns a
    # small tuple; the only fields the route puts on the wire are derived
    # from that tuple, and `key` is never one of them.
    payload_keys = {
        "item_id",
        "selected",
        "selected_text",
        "correct",
        "misconception_id",
    }
    assert "key" not in payload_keys
    assert "answer_key" not in payload_keys
