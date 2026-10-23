"""The integration door: POST /api/v1/integration/adapt (docs/INTEGRATION.md).

These tests are the contract another app (GenLink on the projector, or anything
else with an HTTP client) relies on:

* the example call from the docs returns adapted content — no sign-in, no
  cookie, no database row, and no model call anywhere in the path;
* different activity produces visibly different lessons (the claim being pitched);
* the same activity produces the *same* lesson (a pure function, safe to cache);
* explicit levers from the caller win over the endpoint's coarse read;
* bad input is refused cleanly (422) or tolerated (unknown fields), never a 500;
* the optional shared token gates the door when, and only when, it is set.

The suite runs with ``ADAPT_GEMINI_API_KEY`` removed by conftest, so every
passing test here is also proof that the endpoint needs no model at all.
"""

from __future__ import annotations

import copy
from typing import Any

# --------------------------------------------------------------------------- #
# the example request from docs/INTEGRATION.md — kept literal on purpose
# --------------------------------------------------------------------------- #

EXAMPLE_CONTENT: dict[str, Any] = {
    "concept": "fractions_add",
    "title": "Adding fractions",
    "topic_id": "fractions_add",
    "source": "genlink",
    "explanation": (
        "To add fractions, the parts must be the same size. That shared size is the "
        "denominator. When the denominators differ, rename one fraction so both use the "
        "same denominator. Then add the numerators and keep the denominator. The answer "
        "can often be simplified."
    ),
    "worked_example": {
        "steps": [
            {
                "text": "1/2 + 1/3: the denominators differ, so find a shared size: 6.",
                "why": "common denominator",
            },
            {"text": "Rename: 1/2 = 3/6 and 1/3 = 2/6.", "why": "equivalent fractions"},
            {"text": "Add the numerators: 3/6 + 2/6 = 5/6.", "why": "same-size parts add directly"},
        ]
    },
    "questions": [
        {
            "question": "What is 1/4 + 1/8?",
            "hint": "Eighths are the shared size.",
            "format": "numeric",
        }
    ],
    "facts": ["the parts must be the same size before you add"],
}

EXAMPLE_BODY: dict[str, Any] = {
    "learner": {"id": "genlink-42", "language": "en"},
    "activity": [
        {
            "id": "a1", "kind": "attempt", "topic_id": "fractions_add",
            "correct": False, "response_ms": 1800, "confidence": "certain",
        },
        {
            "id": "a2", "kind": "attempt", "topic_id": "fractions_add",
            "correct": False, "response_ms": 2100, "confidence": "certain",
        },
        {
            "id": "a3", "kind": "attempt", "topic_id": "fractions_add",
            "correct": True, "response_ms": 9400, "confidence": "guessing",
        },
        {"id": "a4", "kind": "hint", "topic_id": "fractions_add", "response_ms": 5000},
    ],
    "content": EXAMPLE_CONTENT,
}

URL = "/api/v1/integration/adapt"


def _post(client, body: dict[str, Any], **kw):
    return client.post(URL, json=body, **kw)


def _kinds(plan: dict[str, Any]) -> list[str]:
    return [b["kind"] for b in plan["blocks"]]


def _content_kinds(plan: dict[str, Any]) -> list[str]:
    return [str(b["content"].get("kind") or "") for b in plan["blocks"]]


def _struggling_activity() -> list[dict[str, Any]]:
    """8 answers on the topic, 2 right, taken slowly — struggling, not guessing."""
    out = []
    for i in range(8):
        out.append(
            {
                "id": f"s{i}",
                "kind": "attempt",
                "topic_id": "fractions_add",
                "correct": i >= 6,  # right only on the last two
                "response_ms": 7000 + i * 100,
            }
        )
    return out


def _strong_activity() -> list[dict[str, Any]]:
    """8 answers on the topic, all right, steady pace."""
    return [
        {
            "id": f"g{i}",
            "kind": "attempt",
            "topic_id": "fractions_add",
            "correct": True,
            "response_ms": 5000,
        }
        for i in range(8)
    ]


# --------------------------------------------------------------------------- #
# the happy path: the docs example, answered
# --------------------------------------------------------------------------- #

def test_example_call_returns_adapted_content(client):
    response = _post(client, EXAMPLE_BODY)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["learner_id"] == "genlink-42"
    assert body["engine"]["name"] == "adapt"
    assert body["engine"]["local_only"] is True
    assert body["engine"]["mode"] == "deterministic"

    plan = body["adapted_content"]
    assert plan["mode"] == "deterministic"
    assert plan["concept"] == "fractions_add"
    assert plan["title"] == "Adding fractions"
    assert plan["blocks"], "adapted content must carry at least one block"
    for block in plan["blocks"]:
        assert {"block", "chunk_id", "kind", "content", "why"} <= set(block)
        assert block["why"].get("text"), "every block carries a human-readable why"
    # The caller's own material is what gets taught, chunked and checked.
    joined = " ".join(str(b["content"].get("text") or "") for b in plan["blocks"])
    assert "same size" in joined
    # The worked example led (default lead, and the pack has steps).
    assert plan["lead_format"] == "worked_example"
    assert "worked_example" in _kinds(plan)
    # Every explanation chunk is followed by exactly one check — the adapter's
    # invariant, re-asserted here because it is what makes the output teachable.
    kinds = _kinds(plan)
    for i, kind in enumerate(kinds):
        if kind == "text" and not plan["blocks"][i]["content"].get("fallback_for"):
            assert kinds[i + 1] == "check"


def test_the_example_activity_is_read_into_the_profile(client):
    """The docs example's numbers must produce the profile the docs claim."""
    body = _post(client, EXAMPLE_BODY).json()
    profile = body["derived_profile"]
    assert profile["attempts"] == 3
    assert profile["correct"] == 1
    assert profile["hints"] == 1
    # Two of three timed answers were wrong inside 2.5s -> prediction first.
    assert profile["flags"].get("early_guessing") is True
    assert _kinds(body["adapted_content"])[0] == "predict"
    # A hint asked for after 5s -> hints offered early.
    assert profile["levers"]["hint_policy"]["value"] == "offer early"
    # Sure-and-wrong twice -> the lesson asks how sure the learner is.
    assert profile["levers"]["confidence_prompting"]["value"] == "before_each_answer"
    assert "confidence" in _content_kinds(body["adapted_content"])
    # Every derived lever names the numbers it came from.
    for lever in profile["levers"].values():
        assert lever["why"] and lever["source"] == "measured"


def test_no_session_no_cookie_no_database(client):
    """The door is open and stateless: no auth headers in, no cookies out."""
    response = _post(client, EXAMPLE_BODY)
    assert response.status_code == 200
    assert "set-cookie" not in {k.lower() for k in response.headers}


# --------------------------------------------------------------------------- #
# adaptation: different learner, different lesson — computed, not narrated
# --------------------------------------------------------------------------- #

def test_struggling_and_strong_learners_get_visibly_different_lessons(client):
    struggling = _post(
        client,
        {
            "learner": {"id": "low"},
            "activity": _struggling_activity(),
            "content": EXAMPLE_CONTENT,
        },
    ).json()
    strong = _post(
        client,
        {
            "learner": {"id": "high"},
            "activity": _strong_activity(),
            "content": EXAMPLE_CONTENT,
        },
    ).json()

    a, b = struggling["adapted_content"], strong["adapted_content"]
    assert a["limits"]["step_size"] == "small"
    assert b["limits"]["step_size"] == "large"
    assert a["limits"]["max_words_per_block"] < b["limits"]["max_words_per_block"]
    # The same explanation is cut into more, smaller steps for the struggling learner.
    assert sum(1 for k in _kinds(a) if k == "text") > sum(1 for k in _kinds(b) if k == "text")
    # 8/8 right over 8 answers places the strong learner at the top rung: the
    # lesson asks them to teach it back, and to predict before seeing.
    assert b["ladder_rung"] == 5
    assert "teachback" in _content_kinds(b)
    assert a["ladder_rung"] < 4
    assert "teachback" not in _content_kinds(a)


def test_fast_wrong_answers_put_a_prediction_first(client):
    activity = [
        {"id": f"f{i}", "kind": "attempt", "correct": False, "response_ms": 1200}
        for i in range(3)
    ] + [{"id": "slow", "kind": "attempt", "correct": True, "response_ms": 9000}]
    body = _post(client, {"activity": activity, "content": EXAMPLE_CONTENT}).json()
    assert body["derived_profile"]["flags"]["early_guessing"] is True
    assert _kinds(body["adapted_content"])[0] == "predict"


def test_slowing_slipping_answers_plan_a_break(client):
    activity = [
        {"id": f"h{i}", "kind": "attempt", "correct": True, "response_ms": 2000}
        for i in range(4)
    ]
    activity += [
        {"id": "t1", "kind": "attempt", "correct": False, "response_ms": 9000},
        {"id": "t2", "kind": "attempt", "correct": False, "response_ms": 9500},
        {"id": "t3", "kind": "attempt", "correct": True, "response_ms": 9200},
    ]
    body = _post(client, {"activity": activity, "content": EXAMPLE_CONTENT}).json()
    assert body["derived_profile"]["flags"].get("fatigued") is True
    assert "break" in _content_kinds(body["adapted_content"])


def test_explicit_levers_win_over_the_activity_read(client):
    """The calling app measured its learner properly; this door must not overrule it."""
    body = {
        "learner": {
            "id": "explicit",
            "levers": {"step_size": "large", "lead_format_prior": "visual"},
        },
        "activity": _struggling_activity(),  # would derive step_size=small on its own
        "content": EXAMPLE_CONTENT,
    }
    plan = _post(client, body).json()["adapted_content"]
    assert plan["limits"]["step_size"] == "large"
    assert plan["lead_format"] == "visual"


def test_language_choice_restyles_the_scaffolding(client):
    body = copy.deepcopy(EXAMPLE_BODY)
    body["learner"]["language"] = "sw"
    plan = _post(client, body).json()["adapted_content"]
    assert plan["limits"]["language"] == "sw"
    checks = [b["content"].get("question") or "" for b in plan["blocks"] if b["kind"] == "check"]
    assert any(q.startswith("Swali fupi:") for q in checks)


def test_teaching_memory_suppresses_a_repeated_explanation(client):
    body = copy.deepcopy(EXAMPLE_BODY)
    body["learner"]["teaching_memory"] = {
        "explained": ["fractions_add"],
        "examples_shown": [],
        "asked": [],
    }
    plan = _post(client, body).json()["adapted_content"]
    # The concept was already explained, so the text chunks are replaced by a
    # recall-first check — the adapter's teaching-memory promise, via this door.
    assert "recall" in _content_kinds(plan)
    joined = " ".join(
        str(b["content"].get("text") or "") for b in plan["blocks"] if b["kind"] == "text"
    )
    assert "same size" not in joined


# --------------------------------------------------------------------------- #
# the door as a pure function
# --------------------------------------------------------------------------- #

def test_same_request_same_lesson(client):
    first = _post(client, EXAMPLE_BODY).json()
    second = _post(client, EXAMPLE_BODY).json()
    assert first["adapted_content"] == second["adapted_content"]
    assert first["derived_profile"]["notes"] == second["derived_profile"]["notes"]


# --------------------------------------------------------------------------- #
# refusals and tolerance: never a 500
# --------------------------------------------------------------------------- #

def test_missing_content_and_concept_is_refused_cleanly(client):
    response = _post(client, {"learner": {"id": "nothing-to-adapt"}, "activity": []})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "adaptation_refused"
    assert "concept" in detail["detail"]
    assert detail["next"]

def test_concept_alone_still_returns_an_honest_lesson(client):
    """No pack, no prose invented: the recall skeleton that says what is missing."""
    response = _post(client, {"concept": "photosynthesis", "title": "Photosynthesis"})
    assert response.status_code == 200, response.text
    plan = response.json()["adapted_content"]
    assert plan["fallbacks"], "an empty pack must be reported, not papered over"
    assert "no_content" in _content_kinds(plan)


def test_unknown_event_fields_and_kinds_are_tolerated(client):
    body = copy.deepcopy(EXAMPLE_BODY)
    body["activity"].append(
        {
            "id": "dance1",
            "kind": "dance_break",  # not a kind this endpoint knows
            "correct": None,
            "whatever_the_app_adds": {"nested": [1, 2, 3]},
            "score": 17,
        }
    )
    response = _post(client, body)
    assert response.status_code == 200, response.text
    # The unknown event is counted but moves no lever.
    assert response.json()["derived_profile"]["events"] == 5
    assert response.json()["derived_profile"]["attempts"] == 3


def test_malformed_scalar_is_a_422_not_a_crash(client):
    body = copy.deepcopy(EXAMPLE_BODY)
    body["activity"][0]["correct"] = "perhaps"  # not a bool, not coercible
    response = _post(client, body)
    assert response.status_code == 422
    assert response.json()["error"] == "validation_failed"


def test_garbage_content_pack_is_refused_not_a_crash(client):
    """A pack the normaliser cannot read is the caller's bug, answered as a 422."""
    body = {
        "concept": "x",
        "title": "X",
        "content": {"worked_example": {"steps": 42}},  # not a list of steps
    }
    response = _post(client, body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "adaptation_refused"


def test_garbage_constraints_and_memory_do_not_crash(client):
    body = copy.deepcopy(EXAMPLE_BODY)
    body["constraints"] = {"max_words_per_block": "forty", "reading_level": 7}
    body["learner"]["teaching_memory"] = {"explained": 5}
    response = _post(client, body)
    assert response.status_code == 200, response.text
    # The non-numeric cap is dropped, not forwarded; the lesson is still built.
    assert response.json()["adapted_content"]["blocks"]


def test_oversized_activity_list_is_refused(client):
    body = {
        "activity": [{"kind": "attempt", "correct": True} for _ in range(501)],
        "content": EXAMPLE_CONTENT,
    }
    assert _post(client, body).status_code == 422


# --------------------------------------------------------------------------- #
# the optional shared token
# --------------------------------------------------------------------------- #

def test_token_gates_the_door_when_the_operator_sets_one(client, monkeypatch):
    monkeypatch.setenv("ADAPT_INTEGRATION_TOKEN", "genlink-demo-token")
    # Without the token: refused, with a sentence that says how to send it.
    refused = _post(client, EXAMPLE_BODY)
    assert refused.status_code == 401
    assert refused.json()["detail"]["error"] == "integration_unauthorized"
    assert "ADAPT_INTEGRATION_TOKEN" in refused.json()["detail"]["next"]
    assert "[withheld]" not in refused.text  # the hint must survive the privacy scrub
    # With a wrong token: still refused.
    wrong = _post(client, EXAMPLE_BODY, headers={"authorization": "Bearer not-the-token"})
    assert wrong.status_code == 401
    # Bearer header: admitted.
    ok_bearer = _post(client, EXAMPLE_BODY, headers={"authorization": "Bearer genlink-demo-token"})
    assert ok_bearer.status_code == 200
    assert ok_bearer.json()["ok"] is True
    # Dedicated header: admitted too.
    ok_header = _post(
        client, EXAMPLE_BODY, headers={"x-adapt-integration-token": "genlink-demo-token"}
    )
    assert ok_header.status_code == 200


def test_open_on_the_lan_when_no_token_is_set(client, monkeypatch):
    monkeypatch.delenv("ADAPT_INTEGRATION_TOKEN", raising=False)
    assert _post(client, EXAMPLE_BODY).status_code == 200


# --------------------------------------------------------------------------- #
# discoverability
# --------------------------------------------------------------------------- #

def test_route_is_listed_in_the_api_index(client):
    index = client.get("/api").json()
    assert "POST /api/v1/integration/adapt" in index["routes"]
