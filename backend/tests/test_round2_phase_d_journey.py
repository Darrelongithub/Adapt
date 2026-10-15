"""Round 2, Part D: the journey — its state, its skips, and its resume rule.

What this file is guarding, in one sentence per claim:

* the frozen stage ids exist, in the frozen order, on the path the frozen contract
  names (``GET /api/v1/journey``) *and* on the light read the shell uses;
* a stage's status comes from the row that proves the work happened -- the questions
  answered, the mind check sealed, the round finished, the lesson opened, the answer
  given outside placement -- so the path cannot drift from the data behind it;
* a **skip** is stored, reported as *not measured*, and survives a second client,
  which is what "resumable from any device" means when the state is on the server;
* evidence beats a stale skip: a learner who waved a step through and then did it has
  done it;
* the eight unaided placement questions stay reachable and do not silently count as
  practice, and the placement the UI promises really is eight by default.

Every claim is asserted over HTTP (the real middleware, the real store) rather than by
calling the service directly, because the parts that break in this app are the seams.
"""

from __future__ import annotations

import json

from adapt.journey.stages import STAGE_IDS, STAGES

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
QUESTIONS = {
    "goal": "just curious",
    "session_minutes": 20,
    "study_days": 3,
    "study_when": "evening",
    "motivation": ["seeing progress"],
    "quit_trigger": ["being stuck"],
    "feedback_style": "gentle",
    "challenge_first": "safe success first",
    "when_stuck": "ask someone",
    "device_context": ["phone"],
    "language": "English",
    "timers_help": "timers stress me",
    "anything_else": "I get distracted easily.",
}


def _journey(client, learner) -> dict:
    response = client.get("/api/v1/journey/state", headers=learner["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _by_id(body: dict) -> dict[str, str]:
    return {row["id"]: row["status"] for row in body["stages"]}


def _advance(client, learner, stage: str, status: str, note: str | None = None):
    body = {"stage": stage, "status": status}
    if note is not None:
        body["note"] = note
    return client.post("/api/v1/journey/advance", headers=learner["headers"], json=body)


def _start_mind_check(client, learner) -> str:
    """Open the mind check, take the format pick and the thirteen questions."""
    view = client.post("/api/v1/baseline", headers=learner["headers"], json={}).json()
    run_id = view["run_id"]
    picked = client.post(
        f"/api/v1/baseline/{run_id}/format", headers=learner["headers"], json={"value": "diagram"}
    )
    assert picked.status_code == 200, picked.text
    answered = client.post(
        f"/api/v1/baseline/{run_id}/questions", headers=learner["headers"], json={"answers": QUESTIONS}
    )
    assert answered.status_code == 200, answered.text
    return run_id


# --------------------------------------------------------------------------- #
# the frozen shape
# --------------------------------------------------------------------------- #
def test_journey_state_names_every_frozen_stage_in_order(client, learner):
    body = _journey(client, learner)
    assert [row["id"] for row in body["stages"]] == list(STAGES) == list(STAGE_IDS)
    assert set(_by_id(body).values()) <= {"todo", "done", "skipped"}
    assert body["progress"]["total"] == len(STAGES)


def test_the_frozen_route_carries_the_path_state_as_well_as_the_timeline(client, learner):
    """``GET /api/v1/journey`` is the path the shared header names. It also has a timeline.

    Both live on one answer on purpose: the timeline *is* the evidence the stage list is
    derived from, and two endpoints derived from the same rows are two endpoints that can
    disagree.
    """
    body = client.get("/api/v1/journey", headers=learner["headers"]).json()
    assert [row["id"] for row in body["stages"]] == list(STAGES)
    assert body["stage"] in STAGES
    # the timeline is still there
    assert "nodes" in body and "summary" in body and "stats" in body


def test_a_fresh_learner_has_an_account_and_is_on_the_first_question(client, learner):
    body = _journey(client, learner)
    statuses = _by_id(body)
    assert statuses["account"] == "done", "a learner with a session has an account, guest or not"
    assert statuses["preferences"] == "todo"
    assert body["stage"] == "preferences"
    # ``evidence`` is the extra key that says *why*: derived from a row, or the learner's own word.
    assert {row["id"]: row["evidence"] for row in body["stages"]}["account"] == "recorded"


# --------------------------------------------------------------------------- #
# skips
# --------------------------------------------------------------------------- #
def test_a_skip_is_stored_reported_as_not_measured_and_survives_a_second_client(client, learner, services):
    response = _advance(client, learner, "games", "skipped", note="I have done this before")
    assert response.status_code == 200, response.text
    statuses = _by_id(response.json())
    assert statuses["games"] == "skipped"
    assert "games" in response.json()["not_measured"]
    assert response.json()["stage"] == "preferences", "a skipped stage is not where you are"

    # A second copy of the state: read straight out of SQLite, not out of the response.
    from sqlalchemy import select

    from adapt.journey.models import JourneyStage

    with services.db.session() as s:
        rows = {
            row.stage: row.status
            for row in s.execute(
                select(JourneyStage).where(JourneyStage.student_id == learner["student_id"])
            ).scalars()
        }
    assert rows == {"games": "skipped"}

    # And from a *different* client holding the same session: the state is the server's.
    from fastapi.testclient import TestClient

    from adapt.api.app import app
    from adapt.config import get_settings

    st = get_settings()
    other = TestClient(app)  # no lifespan: a second browser, not a second server
    token = client.cookies.get(st.cookie_name)
    assert token, "the fixture learner's session cookie should be in the client jar"
    fresh = other.get("/api/v1/journey/state", cookies={st.cookie_name: token})
    assert fresh.status_code == 200, fresh.text
    assert _by_id(fresh.json())["games"] == "skipped"


def test_a_stage_cannot_be_marked_todo_and_unknown_stages_are_refused(client, learner):
    bad_status = _advance(client, learner, "games", "todo")
    assert bad_status.status_code == 422, bad_status.text
    assert bad_status.json()["detail"]["error"] == "unknown_status"
    bad_stage = _advance(client, learner, "checkout", "done")
    assert bad_stage.status_code == 422, bad_stage.text
    assert bad_stage.json()["detail"]["error"] == "unknown_stage"
    # Refusals write nothing.
    assert _by_id(_journey(client, learner))["games"] == "todo"


def test_advance_needs_a_session(client):
    jar = client.cookies.jar
    saved = list(jar)
    for cookie in saved:
        jar.clear(cookie.domain, cookie.path, cookie.name)
    try:
        assert client.get("/api/v1/journey/state").status_code == 401
        assert client.post("/api/v1/journey/advance", json={"stage": "games", "status": "skipped"}).status_code == 401
    finally:
        for cookie in saved:
            jar.set_cookie(cookie)


def test_evidence_beats_a_stale_skip(client, learner):
    """Skipped, then actually done: the row says skipped, the run says done, done wins."""
    assert _advance(client, learner, "games", "skipped").status_code == 200
    run_id = _start_mind_check(client, learner)
    sealed = client.post(f"/api/v1/baseline/{run_id}/finish", headers=learner["headers"])
    assert sealed.status_code == 200, sealed.text
    body = _journey(client, learner)
    assert _by_id(body)["games"] == "done"
    assert "games" not in body["not_measured"], "the skip is no longer what happened"


# --------------------------------------------------------------------------- #
# derivation, stage by stage
# --------------------------------------------------------------------------- #
def test_the_thirteen_questions_mark_preferences_done(client, learner):
    before = _journey(client, learner)
    assert _by_id(before)["preferences"] == "todo"
    _start_mind_check(client, learner)
    after = _journey(client, learner)
    assert _by_id(after)["preferences"] == "done"
    assert after["stage"] == "games", "with the questions behind them, the games are next"


def test_sealing_the_mind_check_marks_the_games_and_the_snapshot(client, learner):
    run_id = _start_mind_check(client, learner)
    sealed = client.post(f"/api/v1/baseline/{run_id}/finish", headers=learner["headers"])
    assert sealed.status_code == 200, sealed.text
    statuses = _by_id(_journey(client, learner))
    assert statuses["games"] == "done"
    assert statuses["snapshot"] == "done", "the mind check seals a snapshot; that is what 'noticed' is"


def test_a_subject_round_marks_the_subject_and_the_round(client, learner):
    started = client.post(
        "/api/v1/subjects/round", headers=learner["headers"], json={"topic": "fractions"}
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    statuses = _by_id(_journey(client, learner))
    assert statuses["subject"] == "done", "a round cannot exist for a topic nobody chose"
    assert statuses["subject_round"] == "todo", "opening a round is not finishing it"
    finished = client.post(f"/api/v1/subjects/round/{run_id}/finish", headers=learner["headers"], json={})
    assert finished.status_code == 200, finished.text
    assert _by_id(_journey(client, learner))["subject_round"] == "done"


def test_placement_answers_do_not_count_as_practice_but_a_practice_answer_does(client, learner, items, make_answer):
    """The 8 unaided questions are measurement. Ticking "practice" off them would be a lie."""
    session = client.post("/api/v1/sessions", json={"mode": "diagnostic"}, headers=learner["headers"]).json()
    entry = items[0]
    submitted = client.post(
        "/api/v1/attempts",
        json={"session_id": session["session_id"], "item_id": entry["item"]["id"], "answer": make_answer(entry["item"]["id"])},
        headers=learner["headers"],
    )
    assert submitted.status_code == 200, submitted.text
    assert _by_id(_journey(client, learner))["practice"] == "todo"

    practice = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()
    plan = client.get("/api/v1/practice?count=3", headers=learner["headers"]).json()
    first = (plan.get("items") or [])[0]
    done = client.post(
        "/api/v1/attempts",
        json={
            "session_id": practice["session_id"],
            "item_id": first["item"]["id"],
            "answer": make_answer(first["item"]["id"]),
        },
        headers=learner["headers"],
    )
    assert done.status_code == 200, done.text
    assert _by_id(_journey(client, learner))["practice"] == "done"


def test_opening_a_lesson_marks_the_first_lesson(client, learner):
    """The lesson screen is not this phase's — only the fact that opening one moves the path."""
    assert _by_id(_journey(client, learner))["first_lesson"] == "todo"
    scenes = client.get("/api/v1/lessons", headers=learner["headers"]).json()["scenes"]
    assert scenes, "the built-in scenes are seeded on boot"
    opened = client.post(
        "/api/v1/lessons/open",
        headers=learner["headers"],
        json={"scene_id": scenes[0]["id"]},
    )
    assert opened.status_code == 200, opened.text
    assert _by_id(_journey(client, learner))["first_lesson"] == "done"


def test_placement_is_eight_questions_by_default_and_says_so(client, learner):
    """Eight unaided questions is what the screens promise; this is what the server serves.

    The plan is adaptive, so the number is a *ceiling* rather than a promise of the same
    eight items for everyone -- and a caller may ask for between four and twelve. What the
    copy quotes is the default, so the default is what this pins: a default that drifted
    would make the placement screen, the demo and the docs all wrong about the app.
    """
    default = client.get("/api/v1/assessment/diagnostic", headers=learner["headers"]).json()
    assert len(default["items"]) == 8, f"placement served {len(default['items'])} questions"
    assert default["adaptive"] is True
    assert default["plans_from"]["answered"] == 0
    four = client.get("/api/v1/assessment/diagnostic?count=4", headers=learner["headers"]).json()
    assert len(four["items"]) == 4, "the short end of the range the screen describes"
    twelve = client.get("/api/v1/assessment/diagnostic?count=99", headers=learner["headers"]).json()
    assert len(twelve["items"]) == 12, "a caller cannot buy an unbounded placement"
    none = client.get("/api/v1/assessment/diagnostic?count=0", headers=learner["headers"]).json()
    assert len(none["items"]) == 4, "nor ask for nothing and call it a placement"


def test_the_journey_payload_names_no_machinery(client, learner):
    """The new surface is learner-facing text, so the banned-phrase rule applies to it."""
    blob = json.dumps(_journey(client, learner)).lower()
    for banned in ("gemini", "api key", "provider", "uvicorn", "ignored_keys", "ai 1", "ai 2", "ai 3", "endpoint"):
        assert banned not in blob, banned
    assert "model" not in blob, "the path state must not name a model to a learner"
