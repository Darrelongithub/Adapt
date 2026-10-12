"""The HTTP surface, walked the way the frontend walks it.

This is the file that answers "does the loop actually run?": register -> onboard ->
diagnose -> answer -> analyze -> propose -> decide -> teach again -> review later,
with the events in between. Every assertion is about a *number the learner can see*,
not about a response arriving with a 200.

Two rules kept this honest while it was being written:

* the expected shapes were taken from the contract the frontend needs, then checked
  against the running app -- where the two disagreed, the source was read before the
  test was edited, and four of the disagreements turned out to be product defects
  (see ``test_registering_an_existing_email_cannot_be_used_to_sign_in_as_that_person``);
* nothing here needs a model key. ``conftest.py`` pops the Gemini key, so every
  "local mode" claim below is the real fallback path, not a mock.
"""

from __future__ import annotations

import json

import pytest

# Fields that only ever exist on the *private* copy of an item. A substring scan over
# serialised JSON is the wrong tool here: "hint_count" and "hints_allowed" are public,
# so only exact keys mean anything.
ANSWER_KEYS = {"answer_spec", "correct_index", "solution_steps", "hints", "rubric", "stem_template"}

BANDS = {"new", "gapped", "developing", "some", "secure", "mastered"}
TONES = {"neutral", "good", "warn", "bad", "think", "ask", "muted"}


def _leaks_answers(payload: object) -> bool:
    if isinstance(payload, dict):
        if ANSWER_KEYS & set(payload):
            return True
        return any(_leaks_answers(v) for v in payload.values())
    if isinstance(payload, list):
        return any(_leaks_answers(v) for v in payload)
    return False


# --------------------------------------------------------------------------- #
# doorkeeping
# --------------------------------------------------------------------------- #


def test_health_says_why_when_its_database_probe_fails(client, monkeypatch):
    """"The database is unreachable" is not an action; "no such column: X" is.

    The probe used to swallow its exception behind a `pragma: no cover`, so a healthy file with a
    stale connection read exactly like a missing table or a wrong key. Dev work hits this path for
    real: the CLI and the API share one file, and a schema written by one while the other holds a
    pool open produces a failure nobody can name.
    """
    from adapt.api.routes import system

    class Broken:
        class db:  # noqa: N801 - the two attributes the route reaches for
            @staticmethod
            def session():
                raise RuntimeError("no such column: lesson_scenes.author_student_id")

            @staticmethod
            def student_count():
                raise AssertionError("must not be reached")

    monkeypatch.setattr(system, "current", lambda: Broken())
    body = client.get("/api/v1/health").json()
    assert body["ok"] is False and body["database_reachable"] is False
    reason = body["database_error"]
    assert "RuntimeError" in reason and "author_student_id" in reason, reason


def test_health_and_index_are_public(client):
    assert client.get("/healthz").status_code == 200
    body = client.get("/api/v1/health").json()
    assert body["ok"] is True and "learners" in body and body["mode"] == "local"
    # a working probe stays quiet: no error field to make a healthy payload look sick
    assert "database_error" not in body, body
    index = client.get("/api").json()
    assert index["websocket"] == "/ws/voice"
    # The index is the front door for a human judge: the loop, and the call for each beat.
    assert index["loop"] == ["test", "observe", "understand", "personalize", "learn", "repeat"]
    assert set(index["how_to_walk_the_loop"]) == set(index["loop"])
    routes = index["routes"]
    assert routes, "the index must list what is callable"
    for needed in ("/api/v1/attempts", "/api/v1/analyze", "/api/v1/adaptations/pending", "/api/v1/journey"):
        assert any(needed in r for r in routes), f"{needed} is in the API but not in the index"


def test_private_routes_refuse_anonymous_callers(client):
    for path in ("/api/v1/dashboard", "/api/v1/profile", "/api/v1/plan", "/api/v1/journey", "/api/v1/attempts"):
        response = client.post(path) if path.endswith("attempts") else client.get(path)
        assert response.status_code == 401, (path, response.status_code)
        assert response.json()["detail"] == "not signed in"


def test_registration_sets_a_httponly_cookie_and_hands_back_the_csrf_token(client):
    response = client.post("/api/v1/students", json={"display_name": "Ada", "as_guest": True})
    assert response.status_code == 200
    body = response.json()
    assert body["student_id"].startswith("stu_") and len(body["csrf_token"]) >= 16
    assert body["created"] is True and body["mode"] == "local"
    cookie = response.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie, "the session cookie must not be readable from script"
    assert "samesite=lax" in cookie, "cross-site POSTs must not carry the session cookie"
    assert "max-age" in cookie or "expires" in cookie, "a session that never expires is a lost session"
    assert "adapt_csrf" in cookie, "the CSRF token is a readable cookie on purpose (double submit)"


def test_registering_an_existing_email_cannot_be_used_to_sign_in_as_that_person(client):
    """Registration must never double as login.

    ``get_or_create_student`` returns the existing row for a known address, so an
    unguarded ``POST /students`` would hand back a live session cookie for somebody
    else's account -- knowing an email address would be enough to read that learner's
    whole history. This is the test that found it.
    """
    owner = client.post(
        "/api/v1/students",
        json={"display_name": "Owner", "email": "owner@example-invalid.test", "password": "correct horse battery"},
    )
    assert owner.status_code == 200, owner.text
    owner_id = owner.json()["student_id"]

    attacker = client.post(
        "/api/v1/students",
        json={"display_name": "Attacker", "email": "owner@example-invalid.test", "password": "guessing here"},
    )
    assert attacker.status_code == 409, attacker.text
    detail = attacker.json()["detail"]
    assert detail["error"] == "email_registered" and "/login" in detail["next"]
    assert "student_id" not in detail and "csrf_token" not in detail, "a refusal may not carry a session"

    # The owner's own session still works, and the guess did not become a password.
    assert client.get("/api/v1/me", headers={"x-csrf-token": owner.json()["csrf_token"]}).status_code == 200
    assert client.get("/api/v1/me").json()["student_id"] == owner_id
    assert client.post(
        "/api/v1/login", json={"email": "owner@example-invalid.test", "password": "guessing here"}
    ).status_code == 401


def test_weak_passwords_are_refused_at_the_door(client):
    response = client.post("/api/v1/students", json={"display_name": "Ada", "email": "a@b.test", "password": "abc"})
    assert response.status_code == 422
    response2 = client.post("/api/v1/students", json={"display_name": "Ada", "email": "a@b.test"})
    assert response2.status_code == 422, "an email account has to have a password"


def test_email_and_password_login_works_after_registering(client, registered_learner):
    """The blind index has to be written at registration or nobody can ever sign in."""
    ok = client.post(
        "/api/v1/login", json={"email": registered_learner["email"], "password": registered_learner["password"]}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["student_id"] == registered_learner["student_id"]
    assert ok.json()["created"] is False and ok.json()["csrf_token"]

    bad = client.post("/api/v1/login", json={"email": registered_learner["email"], "password": "wrong wrong wrong"})
    assert bad.status_code == 401
    assert "wrong" in str(bad.json()["detail"]).lower()

    no_user = client.post("/api/v1/login", json={"email": "nobody@example.test", "password": "whatever else"})
    assert no_user.status_code == 401, "an unknown address must not be distinguishable from a wrong password"
    assert no_user.json()["detail"] == bad.json()["detail"], "the two answers must read identically"


def test_logout_revokes_the_session_row_not_just_the_cookie(client, services, registered_learner):
    from sqlalchemy import select

    from adapt.db.models import AuthSession

    def live_sessions() -> int:
        with services.scope() as s:
            rows = s.execute(
                select(AuthSession).where(AuthSession.student_id == registered_learner["student_id"])
            ).scalars().all()
        assert rows, "the session row stays for the audit trail rather than being deleted"
        return sum(1 for r in rows if r.revoked_at is None)

    login = client.post(
        "/api/v1/login", json={"email": registered_learner["email"], "password": registered_learner["password"]}
    )
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]
    assert client.get("/api/v1/me").status_code == 200
    before = live_sessions()

    # Logout changes state, so it is CSRF-guarded like every other mutation.
    assert client.post("/api/v1/logout").status_code == 403
    out = client.post("/api/v1/logout", headers={"x-csrf-token": csrf})
    assert out.status_code == 200 and out.json()["ok"] is True
    assert client.get("/api/v1/me").status_code == 401, "logging out must end the session, not just the cookie"
    assert live_sessions() == before - 1, "the token itself has to die, or a stolen cookie keeps working"
    assert client.get("/api/v1/me", headers={"x-csrf-token": csrf}).status_code == 401


def test_mutations_require_the_csrf_header(client, learner):
    response = client.post("/api/v1/me/preferences", json={"preferences": {"goal": "gcse maths"}})
    assert response.status_code == 403
    assert "csrf" in response.json()["detail"]
    ok = client.post("/api/v1/me/preferences", json={"preferences": {"goal": "gcse maths"}}, headers=learner["headers"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["accepted"]["goal"] == "gcse maths"


def test_validation_errors_point_at_the_field_without_echoing_the_answer(client, learner, items):
    """A 422 has to say which field, and must not repeat what was sent."""
    response = client.post("/api/v1/attempts", json={"session_id": "lrs_x"}, headers=learner["headers"])
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_failed"
    assert [f["loc"] for f in body["fields"]] == [["body", "item_id"]]
    assert all(f["type"] == "missing" for f in body["fields"])

    # Preferences are value-checked too -- but the answer here is "we did not store it",
    # not a hard 422, because a settings form may legitimately send a half-filled field.
    onboard = client.post(
        "/api/v1/me/onboarding",
        json={"preferences": {"weekly_minutes": "not a number"}},
        headers=learner["headers"],
    )
    assert onboard.status_code == 200
    assert "weekly_minutes" in onboard.json()["ignored_keys"]
    assert onboard.json()["rejected_reasons"]["weekly_minutes"] == "expected int"
    assert "weekly_minutes" not in client.get("/api/v1/me/preferences", headers=learner["headers"]).json()["preferences"], (
        "a rejected value must not be stored anyway"
    )


def test_a_long_session_can_be_closed_and_is_then_readonly(client, learner, session):
    done = client.post(f"/api/v1/sessions/{session}/finish", headers=learner["headers"])
    assert done.status_code == 200 and done.json()["ok"]
    first = done.json()
    assert {"duration_seconds", "active_ms", "mode", "next"} <= set(first)

    again = client.post(f"/api/v1/sessions/{session}/finish", headers=learner["headers"])
    assert again.status_code == 200, again.text
    assert again.json()["duration_seconds"] == first["duration_seconds"], "closing twice must not re-time the session"

    unknown = client.post("/api/v1/sessions/lrs_nope/finish", headers=learner["headers"])
    assert unknown.status_code == 404, "200-with-ok:false is how a client believes a save worked"


def _answer_plan(client, learner, session, items, make_answer, *, how_many, correct=False):
    """Answers the first `how_many` placement questions, the way the page does it."""
    for entry in items[:how_many]:
        item_id = entry["item"]["id"]
        r = client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=correct)},
            headers=learner["headers"],
        )
        assert r.status_code == 200, r.text
    return how_many


def test_finishing_placement_is_what_marks_a_learner_placed(client, learner, items, make_answer):
    """Placement used to end without ever touching `diagnostic_complete`.

    Only the CLI set that flag, and only for its own seeded demo learner. So a person who
    answered the whole sequence in the browser had their answers recorded and their
    placement *forgotten*: the landing page went back to "not placed yet" and offered the
    diagnostic again, forever.
    """
    from adapt.config import get_settings

    headers = learner["headers"]
    assert client.get("/api/v1/me", headers=headers).json()["diagnostic_complete"] is False
    session = client.post("/api/v1/sessions", json={"mode": "diagnostic"}, headers=headers).json()["session_id"]
    _answer_plan(client, learner, session, items, make_answer, how_many=int(get_settings().min_evidence_for_proposal))

    done = client.post(f"/api/v1/sessions/{session}/finish", headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["placed"] is True, done.text

    me = client.get("/api/v1/me", headers=headers).json()
    assert me["diagnostic_complete"] is True, "finishing placement has to be visible on the learner record"
    assert client.get("/api/v1/me", headers=headers).json()["diagnostic_complete"] is True, (
        "the flag is stored, not derived fresh per request"
    )


def test_a_placement_stopped_early_says_it_did_not_place_the_learner(client, learner, items, make_answer):
    """Two answers is not a placement. Saying so is better than banding someone on them."""
    headers = learner["headers"]
    session = client.post("/api/v1/sessions", json={"mode": "diagnostic"}, headers=headers).json()["session_id"]
    _answer_plan(client, learner, session, items, make_answer, how_many=2)

    body = client.post(f"/api/v1/sessions/{session}/finish", headers=headers).json()
    assert body["ok"] is True and body["placed"] is False
    assert "placement needs" in body["note"], body
    assert client.get("/api/v1/me", headers=headers).json()["diagnostic_complete"] is False
    assert client.get("/api/v1/dashboard", headers=headers).json() is not None, (
        "the two answers still count as evidence; stopping early must not discard them"
    )


def test_practice_never_marks_a_learner_placed(client, learner, items, make_answer, session):
    """`session` is practice mode: no number of practice answers can stand in for placement."""
    _answer_plan(client, learner, session, items, make_answer, how_many=min(len(items), 10), correct=True)
    body = client.post(f"/api/v1/sessions/{session}/finish", headers=learner["headers"]).json()
    assert "placed" not in body, "only a diagnostic session decides placement"
    assert client.get("/api/v1/me", headers=learner["headers"]).json()["diagnostic_complete"] is False


# --------------------------------------------------------------------------- #
# onboarding: no learning-style question, and stated preferences applied
# --------------------------------------------------------------------------- #


def test_onboarding_never_asks_how_you_learn(client, learner):
    response = client.post(
        "/api/v1/me/onboarding",
        json={
            "preferences": {
                "goal": "gcse maths",
                "weekly_minutes": 150,
                "max_explanation_words": 80,
                "learning_style": "visual",
                "bogus_key": "ignored",
            }
        },
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preferences"]["goal"] == "gcse maths"
    assert body["preferences"]["weekly_minutes"] == 150
    assert "bogus_key" in body["ignored_keys"], "silently dropping an unknown key is how a client rots"
    assert body["rejected_reasons"]["bogus_key"] == "unknown preference key"
    assert "learning_style" in body["ignored_keys"], "the app must not collect a learning-style label at all"
    assert "learning-style" in body["note"] and "measured" in body["note"]
    assert body["consent"] == {"analytics": True, "share_summary_with_ai": True}

    me = client.get("/api/v1/me", headers=learner["headers"]).json()
    assert me["onboarding_complete"] is True
    assert "learning_style" not in json.dumps(me)
    prefs = client.get("/api/v1/me/preferences", headers=learner["headers"]).json()
    assert prefs["preferences"]["max_explanation_words"] == 80
    assert "weekly_minutes" in prefs["allowed_keys"] and "learning_style" not in prefs["allowed_keys"]


def test_a_stated_preference_applies_immediately_and_is_labelled_as_such(client, learner, services):
    """Student-requested changes skip the approval gate -- the learner is the one asking."""
    client.post(
        "/api/v1/me/onboarding",
        json={"preferences": {"max_explanation_words": 35}},
        headers=learner["headers"],
    )
    with services.scope() as s:
        prof = services.ai1.profile(s, learner["student_id"])
    directives = {d.strategy: d for d in prof.directives}
    assert "segmented_text" in directives, "a stated word limit has to reach the tutor as a directive"
    assert directives["segmented_text"].source == "student_stated"
    assert directives["segmented_text"].payload.get("max_words") == 35 or "35" in json.dumps(
        directives["segmented_text"].payload
    )
    assert directives["segmented_text"].why, "every directive says why it is on"


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #


def test_a_preference_is_stored_as_the_thing_it_describes(client, learner):
    """A tick box used to become the phrase "True" in the learner's profile.

    ``avoid`` and ``encourage`` are lists of phrases the tutor reads back to the learner, and the
    settings screen rendered them as checkboxes. ``True`` is a value for a checkbox and not a
    value for a phrase, so the store had to refuse it -- silently coercing it put a fiction in
    the prompt and then presented it as the learner's own request.
    """
    headers = learner["headers"]
    refused = client.post(
        "/api/v1/me/preferences", json={"preferences": {"avoid": True}, "source": "settings"}, headers=headers
    )
    assert refused.status_code == 200
    body = refused.json()
    assert "avoid" not in (body.get("accepted") or {}), body
    assert body["rejected_keys"] == ["avoid"] and "phrase" in body["rejected_reasons"]["avoid"], body

    blank = client.post("/api/v1/me/preferences", json={"preferences": {"avoid": "   "}}, headers=headers)
    assert "avoid" not in (blank.json().get("accepted") or {}), blank.json()

    typed = client.post(
        "/api/v1/me/preferences", json={"preferences": {"avoid": ["word problems", "being rushed"]}}, headers=headers
    )
    assert typed.json()["accepted"] == {"avoid": ["word problems", "being rushed"]}, typed.json()
    stored = client.get("/api/v1/me/preferences").json()["preferences"]
    assert stored["avoid"] == ["word problems", "being rushed"], stored

    # a genuine boolean preference still round-trips, so the guard did not narrow the type map
    flag = client.post("/api/v1/me/preferences", json={"preferences": {"push_me": True}}, headers=headers)
    assert flag.json()["accepted"] == {"push_me": True}, flag.json()
    assert client.get("/api/v1/me/preferences").json()["preferences"]["push_me"] is True
def test_a_proposal_must_propose_something(client, learner, items, make_answer):
    """AI 1 used to propose the number already in force, so "yes" changed nothing.

    ``difficulty_recalibration`` carried ``{"target": 0.78}`` -- and 0.78 is the selector's own
    default. A learner who read "meet the current level", said yes, and approved the status quo
    would have concluded the loop was decoration. The tuned value has to come from the evidence.
    """
    headers = learner["headers"]
    sid = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=headers).json()["session_id"]
    # everything wrong, twice over: accuracy far below band is the pattern this detector needs
    for _ in range(2):
        for entry in items[:8]:
            item_id = entry["item"]["id"]
            r = client.post(
                "/api/v1/attempts",
                json={"session_id": sid, "item_id": item_id, "answer": make_answer(item_id, correct=False)},
                headers=headers,
            )
            assert r.status_code == 200, r.text

    analyzed = client.post(f"/api/v1/analyze?session_id={sid}", headers=headers)
    assert analyzed.status_code == 200, analyzed.text
    pending = client.get("/api/v1/adaptations/pending").json().get("proposals") or []
    rec = next((p for p in pending if p.get("strategy") == "difficulty_recalibration"), None)
    assert rec, f"no recalibration among the {len(pending)} proposals on offer: {[p.get('strategy') for p in pending]}"
    target = float((rec.get("action_payload") or {}).get("target", 0.78))
    assert target != 0.78, "the proposal asks the learner to approve the number already in use"
    assert target > 0.78, f"every answer was wrong, so the items were too hard: the target must rise, got {target}"

    decided = client.post(
        f"/api/v1/adaptations/{rec['id']}/decision",
        json={"decision": "accept", "answered_by": "student"},
        headers=headers,
    )
    assert decided.status_code == 200, decided.text
    active = client.get("/api/v1/adaptations/active", headers=headers).json()["active"]
    row = next((a for a in active if a["strategy"] == "difficulty_recalibration"), None)
    assert row, f"accepting did not turn anything on: {[a.get('strategy') for a in active]}"
    assert abs(float((row["payload"] or {}).get("target", -1)) - target) < 1e-9, (
        f"the applied change must be the number the learner was shown: row says {row['payload']}"
    )



def test_diagnostic_returns_a_broad_plan_with_no_answers_attached(client, learner, items):
    assert len(items) >= 8
    topics = {i["topic_id"] for i in items}
    assert len(topics) >= 6, "one question per topic before a second: breadth, not depth"
    for entry in items:
        assert not _leaks_answers(entry["item"]), "a placement plan must not carry the answer key"
        assert entry["item"]["id"] and entry["reason"] and entry["presentation"]
        assert 0 < entry["position"] <= entry["of"]
        assert entry["decision_trace"], "the client has to be able to show why this question was chosen"
    why = client.get("/api/v1/assessment/diagnostic").json()
    assert "not to grade you" in why["purpose"]
    assert "your answer" not in json.dumps(why["items"]).lower()


def test_events_are_accepted_by_type_and_junk_is_named_not_swallowed(client, learner, session):
    response = client.post(
        f"/api/v1/sessions/{session}/events",
        json={
            "session_id": session,
            "events": [
                {"type": "question_shown", "payload": {"stem_words": 12}},
                {"type": "first_interaction", "payload": {"ms_since_shown": 2100}},
                {"type": "response_change", "payload": {"from": "1/2", "to": "2/4", "ms_since_shown": 8000}},
                {"type": "hint_opened", "payload": {"hint_no": 1, "keystrokes": "everything typed"}},
                {"type": "answer_changed", "payload": {"whatever": True}},
            ],
        },
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 4, "four known types, one unknown"
    assert body["rejected"] == 1 and body["dropped_fields"] == 1
    assert any("answer_changed" in r for r in body["reasons"]), "the client is told which type it got wrong"
    assert any("keystrokes" in r for r in body["reasons"])
    assert "hint_opened" in body["event_types"] and "answer_changed" not in body["event_types"]

    stream = client.get(f"/api/v1/sessions/{session}/events", headers=learner["headers"]).json()["events"]
    types = [e["type"] for e in stream]
    assert "answer_changed" not in types
    assert types.count("hint_opened") == 1, "a forbidden field must cost the field, not the event"
    hint_event = next(e for e in stream if e["type"] == "hint_opened")
    assert hint_event["payload"]["hint_no"] == 1 and "keystrokes" not in hint_event["payload"]
    assert "keystrokes" not in json.dumps(stream), "nothing like a keystroke log may be stored"


def test_events_for_a_session_you_do_not_own_are_refused(client, learner, session):
    """Another learner's session id must read as unknown, never as someone else's."""
    other = client.post("/api/v1/students", json={"display_name": "Other", "as_guest": True})
    headers = {"x-csrf-token": other.json()["csrf_token"]}
    probe = client.post(
        f"/api/v1/sessions/{session}/events", json={"session_id": session, "events": [{"type": "pause", "payload": {}}]}, headers=headers
    )
    assert probe.status_code in (404, 200)
    if probe.status_code == 200:
        assert probe.json()["accepted"] == 0 and probe.json()["rejected"] == 1
        assert "unknown session" in " ".join(probe.json()["reasons"])
    mine = client.get(f"/api/v1/sessions/{session}/events", headers=headers)
    assert mine.status_code == 404, "the event stream is not a global read"


def test_a_batch_larger_than_the_schema_is_refused_not_truncated(client, learner, session):
    events = [{"type": "pause", "payload": {}} for _ in range(501)]
    response = client.post(
        f"/api/v1/sessions/{session}/events",
        json={"session_id": session, "events": events},
        headers=learner["headers"],
    )
    assert response.status_code == 422


def test_answers_move_mastery_in_the_right_direction(client, learner, services, items, make_answer, session):
    """The claim in one test: 5 correct answers on one topic read as growth, and a
    run of wrong answers on another reads as a gap -- not the same number."""
    by_topic: dict[str, dict] = {}
    for entry in items:
        by_topic.setdefault(entry["topic_id"], entry)
    picks = list(by_topic.values())[:2]
    assert len(picks) == 2, "the diagnostic has to cover more than one topic"

    end_points: dict[bool, dict] = {}
    for correct in (True, False):
        entry = picks[0] if correct else picks[1]
        item_id = entry["item"]["id"]
        topic_id = entry["topic_id"]
        last = None
        for _ in range(5):
            r = client.post(
                "/api/v1/attempts",
                json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=correct)},
                headers=learner["headers"],
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["correct"] is correct
            assert body["item_id"] == item_id and body["topic_id"] == topic_id
            assert body["grading_method"] in {"mcq_index", "numeric", "text_match", "teachback_rubric"}
            assert body["attempt_id"].startswith("att_")
            last = body["mastery"]
        assert last is not None
        assert last["observations"] == 5, "the five answers must all be in the estimate, not just the last"
        end_points[correct] = last

    up, down = end_points[True], end_points[False]
    assert up["p_know"] > down["p_know"], f"five right ({up['p_know']}) must read stronger than five wrong ({down['p_know']})"
    assert up["delta"] > 0, "a correct answer has to raise the estimate"
    assert down["delta"] <= 0 and down["p_know"] < 0.05, "five misses has to read as a hole, not a shrug"
    assert up["band"] in BANDS and down["band"] in BANDS
    assert up["due_at"] > down["due_at"], "forgotten material is due sooner, not later"
    assert up["first_observation"] is False, "five attempts cannot all be the first"

    dash = client.get("/api/v1/dashboard", headers=learner["headers"]).json()
    by_id = {m["topic_id"]: m for m in dash["mastery"]}
    assert abs(by_id[picks[0]["topic_id"]]["p_know"] - up["p_know"]) < 1e-6, (
        "the dashboard and the answer receipt must be the same number"
    )


def test_an_attempt_needs_a_session_and_a_real_item(client, learner, items, make_answer, session):
    item_id = items[0]["item"]["id"]
    bad_session = client.post(
        "/api/v1/attempts",
        json={"session_id": "lrs_nope", "item_id": item_id, "answer": make_answer(item_id)},
        headers=learner["headers"],
    )
    assert bad_session.status_code == 422
    assert "session" in json.dumps(bad_session.json()).lower()
    bad_item = client.post(
        "/api/v1/attempts", json={"session_id": session, "item_id": "itm_nope", "answer": 0}, headers=learner["headers"]
    )
    assert bad_item.status_code == 422
    missing = client.post("/api/v1/attempts", json={"session_id": session}, headers=learner["headers"])
    assert missing.status_code == 422
    assert missing.json()["fields"][0]["loc"][0] == "body"


def test_teachback_is_scored_on_content_and_says_what_was_missing(client, learner, services, session):
    from adapt.db.models import Item

    with services.scope() as s:
        row = s.query(Item).filter(Item.kind == "teachback", Item.active.is_(True)).first()
    if row is None:  # pragma: no cover - the bank ships teachbacks
        pytest.skip("no teachback item in the bank")
    item_id, rubric = row.id, dict(row.rubric or {})
    concepts = [str(c.get("concept")) for c in (rubric.get("required_concepts") or [])]
    assert concepts, "a teachback item has to carry the ideas it is listening for"

    r = client.post(
        "/api/v1/attempts",
        json={"session_id": session, "item_id": item_id, "answer": "it just works", "teachback_text": "it just works"},
        headers=learner["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grading_method"] == "teachback_rubric"
    assert body["teachback"]["method"] == "rubric_concept_coverage"
    assert body["teachback"]["score"] < 0.6 and not body["correct"]
    assert set(body["teachback"]["missing"]) == set(concepts)
    assert body["teachback"]["followup"], "the learner gets a next step, not a verdict"
    assert "not fluency" in body["teachback"]["note"], "spelling must not be the thing being measured"
    assert 0.0 <= body["score"] <= 1.0

    # Say the ideas back in your own words and the score has to move, with the
    # missing list shrinking -- this is a rubric, not a word count.
    good = ". ".join(concepts) + ". You check it against one example afterwards, and if the check fails you go back to the definition."
    again = client.post(
        "/api/v1/attempts",
        json={"session_id": session, "item_id": item_id, "answer": good, "teachback_text": good, "attempt_no": 2},
        headers=learner["headers"],
    )
    assert again.status_code == 200, again.text
    second = again.json()["teachback"]
    assert second["score"] > body["teachback"]["score"]
    assert second["matched"] == concepts and second["missing"] == []
    assert second["coverage"] == 1.0 and second["followup"] is None

    standalone = client.post(f"/api/v1/items/{item_id}/teachback", json={"text": good}, headers=learner["headers"])
    assert standalone.status_code == 200, standalone.text
    assert standalone.json()["score"] == second["score"], "the same words must score the same either way"


def test_feedback_is_written_and_never_empty_even_without_a_model_key(client, learner, items, make_answer, session):
    item_id = items[0]["item"]["id"]
    wrong = client.post(
        "/api/v1/attempts",
        json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=False)},
        headers=learner["headers"],
    ).json()
    text = wrong["feedback"]
    assert isinstance(text, str) and text.strip(), "no key configured, so the local tutor has to write it"
    assert not text.lower().startswith(("wrong", "incorrect")), "it must not stop at judging"
    assert not _leaks_answers(wrong["mastery"]), "the receipt must not carry the answer"

    right = client.post(
        "/api/v1/attempts",
        json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=True)},
        headers=learner["headers"],
    ).json()
    assert right["feedback"].strip() and right["feedback"] != text, "different answers, different sentence"


def test_analyze_reports_evidence_hypotheses_and_a_question_not_a_change(client, learner, services, items, make_answer, session):
    for i, entry in enumerate(items[:10]):
        item_id = entry["item"]["id"]
        client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=i % 3 == 0)},
            headers=learner["headers"],
        )
    client.post(f"/api/v1/sessions/{session}/finish", headers=learner["headers"])

    response = client.post("/api/v1/analyze", params={"session_id": session, "use_llm": "false"}, headers=learner["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == session, "the route takes the session as a query param; a JSON-only body must not silently analyse everything"
    assert body["provider"] == "local", "no key configured, so nothing may claim a model wrote it"
    assert body["llm"].get("used") in (False, None)
    assert body["traces"], "10 attempts have to leave a mastery trace somewhere"
    for hyp in body["hypotheses"]:
        assert hyp["detector"] and hyp["statement"]
        assert "citations" in hyp and "falsification" in hyp, "a hypothesis without a disproof condition is a mood"
        assert any(k in hyp for k in ("confidence", "posterior"))
    for proposal in body["proposals"]:
        assert proposal["status"] == "pending"
    # The loop is described back to the client, with counts that match.
    steps = {row["step"]: row for row in body["loop"]}
    assert list(steps) == ["test", "observe", "understand", "personalize", "learn"]
    assert str(len(body["evidence"])) in steps["observe"]["evidence"]
    assert str(len(body["hypotheses"])) in steps["understand"]["evidence"]
    assert str(len(body["evaluated"])) in steps["learn"]["evidence"]
    assert steps["personalize"]["done"].startswith("proposals")
    assert "not applied" in steps["personalize"]["done"]
    assert any(k in body for k in ("gaps", "plan")) and "bkt" in body and "retention" in body


def test_proposals_separate_observation_from_claim(client, learner, services, items, make_answer, session):
    for i, entry in enumerate(items[:12]):
        item_id = entry["item"]["id"]
        client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=False)},
            headers=learner["headers"],
        )
    analyzed = client.post("/api/v1/analyze", params={"session_id": session, "use_llm": "false"}, headers=learner["headers"]).json()
    pending = client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()
    assert len(pending["proposals"]) == len([p for p in analyzed["proposals"] if not p.get("skipped")]) or pending["proposals"]
    if not pending["proposals"]:
        pytest.skip("this learner's pattern did not clear the evidence bar")
    assert "Nothing here has been applied" in pending["explain"] or "not been applied" in pending["explain"]
    proposal = pending["proposals"][0]
    for key in ("facts", "pattern", "hypothesis", "change", "ask", "caveat", "review_metric", "review_window_text"):
        assert proposal.get(key), f"{key} is missing from the proposal the learner reads"
    assert proposal["status"] == "pending"
    assert proposal["strategy"] and proposal["strategy_label"]
    assert proposal["action"], "the consent screen has to say what will actually happen"
    assert proposal["alternatives"], "other explanations must be named, not hidden"
    assert proposal["evidence_ids"] or proposal["hypothesis_ref"], "a claim has to point at its observations"
    assert "citations" in proposal
    assert proposal["baseline"] is None or isinstance(proposal["baseline"], dict)
    assert proposal["decided_at"] is None
    assert not client.get("/api/v1/adaptations/active", headers=learner["headers"]).json()["active"], (
        "nothing may be applied before the learner answers"
    )


def test_an_answer_applies_the_change_and_revoking_it_is_immediate(client, learner, session, items, make_answer):
    for i, entry in enumerate(items[:12]):
        item_id = entry["item"]["id"]
        client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=False)},
            headers=learner["headers"],
        )
    client.post("/api/v1/analyze", params={"session_id": session, "use_llm": "false"}, headers=learner["headers"])
    pending = client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()["proposals"]
    if not pending:
        pytest.skip("no proposal this run")
    pid = pending[0]["id"]

    typo = client.post(f"/api/v1/adaptations/{pid}/decision", json={"decision": "approve"}, headers=learner["headers"])
    assert typo.status_code == 422, "the verbs are named, and a wrong one must not be guessed at"
    assert set(typo.json()["detail"]["allowed"]) == {"accept", "reject", "modify", "later"}

    accepted = client.post(
        f"/api/v1/adaptations/{pid}/decision", json={"decision": "accept", "note": "ok let's try that"}, headers=learner["headers"]
    )
    assert accepted.status_code == 200, accepted.text
    outcome = accepted.json()
    assert outcome["status"] == "approved" and outcome["decision"] == "accept"
    assert outcome["active_adaptation_id"] and outcome["proposal_id"] == pid
    assert outcome["review"]["metric"]
    assert outcome["review"]["window"]["min_items"] >= 4, "a window too small to judge is not a window"
    assert "baseline" in outcome["review"]["note"].lower(), "it says how it will be judged"

    active = client.get("/api/v1/adaptations/active", headers=learner["headers"]).json()["active"]
    assert len(active) == 1 and active[0]["proposal_id"] == pid
    assert active[0]["source"] == "approved"
    assert active[0]["watch_metric"] and active[0]["mechanism"] and "label" in active[0]
    assert active[0]["effect_summary"] is None, "no effect can be claimed before the window runs"

    # A second answer to the same question is refused, not silently re-applied.
    twice = client.post(f"/api/v1/adaptations/{pid}/decision", json={"decision": "accept"}, headers=learner["headers"])
    assert twice.status_code == 409
    assert twice.json()["detail"]["reason"] == "already answered"

    revoked = client.post(f"/api/v1/adaptations/{active[0]['id']}/revoke", headers=learner["headers"])
    assert revoked.status_code == 200 and revoked.json()["revoked"] == 1
    assert revoked.json()["strategies"] == [active[0]["strategy"]]
    assert client.get("/api/v1/adaptations/active", headers=learner["headers"]).json()["active"] == []

    history = client.get("/api/v1/adaptations/history", headers=learner["headers"]).json()["decisions"]
    assert any(row["id"] == pid and row["status"] == "approved" for row in history)
    assert client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()["proposals"] == [] or all(
        p["id"] != pid for p in client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()["proposals"]
    ), "an answered proposal leaves the queue"

    # The consent decision is in the learner's own audit trail, with the strategy named.
    entries = client.get("/api/v1/audit?limit=20", headers=learner["headers"]).json()["entries"]
    actions = {e["action"] for e in entries}
    assert {"adaptation_approved", "adaptation_revoked"} <= actions, sorted(actions)
    approval = next(e for e in entries if e["action"] == "adaptation_approved")
    assert approval["outcome"] == "ok" and approval["detail"]["strategy"] == active[0]["strategy"]
    assert approval["object"] == f"adaptation_proposal:{pid}"


def test_deferring_is_a_decision_and_change_its_own_kind_is_recorded(client, learner, session, items, make_answer):
    for entry in items[:12]:
        item_id = entry["item"]["id"]
        client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=False)},
            headers=learner["headers"],
        )
    client.post("/api/v1/analyze", params={"session_id": session, "use_llm": "false"}, headers=learner["headers"])
    pending = client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()["proposals"]
    if not pending:
        pytest.skip("no proposal this run")
    pid = pending[0]["id"]
    later = client.post(f"/api/v1/adaptations/{pid}/decision", json={"decision": "later"}, headers=learner["headers"])
    assert later.status_code == 200, later.text
    assert later.json()["active_adaptation_id"] is None, "later means nothing is applied"
    assert client.get("/api/v1/adaptations/active", headers=learner["headers"]).json()["active"] == []


def test_declining_is_recorded_and_changes_teaching_immediately(client, learner, services, items, make_answer, session):
    """'No' is data: it must be stored as a decision, and teaching must carry on."""
    for entry in items[:12]:
        item_id = entry["item"]["id"]
        client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=False)},
            headers=learner["headers"],
        )
    client.post("/api/v1/analyze", params={"session_id": session, "use_llm": "false"}, headers=learner["headers"])
    pending = client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()["proposals"]
    if pending:
        pid = pending[0]["id"]
        declined = client.post(
            f"/api/v1/adaptations/{pid}/decision",
            json={"decision": "reject", "note": "keep it hard, I need the practice"},
            headers=learner["headers"],
        )
        assert declined.status_code == 200, declined.text
        assert declined.json()["status"] == "rejected"
        assert declined.json()["active_adaptation_id"] is None
        assert client.get("/api/v1/adaptations/active", headers=learner["headers"]).json()["active"] == []
        stored = client.get("/api/v1/adaptations/history", headers=learner["headers"]).json()["decisions"]
        row = next(r for r in stored if r["id"] == pid)
        assert row["student_note"] == "keep it hard, I need the practice", "what the learner said is part of the record"

    next_plan = client.get("/api/v1/practice?count=4", headers=learner["headers"]).json()
    assert next_plan["items"], "the next set of questions still has to arrive after a refusal"
    assert next_plan["why"], "and it has to say why these"


def test_practice_review_plan_and_dashboard_agree_with_each_other(client, learner, services, session, items, make_answer):
    for entry in items[:6]:
        item_id = entry["item"]["id"]
        client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=False)},
            headers=learner["headers"],
        )
    client.post("/api/v1/analyze", params={"session_id": session, "use_llm": "false"}, headers=learner["headers"])

    plan = client.get("/api/v1/plan", headers=learner["headers"]).json()
    assert {"gaps", "remediation", "path", "bkt", "retention", "notes"} <= set(plan)
    steps = plan["path"]["steps"]
    assert steps, "a plan with no next step is not a plan"
    for step in steps:
        assert step["topic_id"] and step["status"] and step["action"]
        if step["status"] == "blocked":
            assert step["needs_first"], "a blocked topic has to name the prerequisite"

    practice = client.get("/api/v1/practice?count=3", headers=learner["headers"]).json()
    assert len(practice["items"]) <= 3
    for entry in practice["items"]:
        assert not _leaks_answers(entry["item"])
        assert entry["item"]["topic_id"] and entry["reason"] and entry["presentation"]
        assert entry["decision_trace"]

    due = client.get("/api/v1/review/due", headers=learner["headers"]).json()
    assert due["count"] == len(due["due"])
    assert "85" in due["rule"], "the queue says which rule produced it"
    dash = client.get("/api/v1/dashboard", headers=learner["headers"]).json()
    assert all(m["band"] in BANDS for m in dash["mastery"])
    assert sum(dash["band_summary"].values()) == len(dash["mastery"]), "the band counts must be the mastery list"
    assert dash["reviews"]["due_now"], "six wrong answers must have opened a gap worth reviewing"
    queued = {row["topic_id"] for row in due["due"]}
    assert {row["topic_id"] for row in dash["reviews"]["due_now"]} <= queued, (
        "the dashboard may not show a review that the queue does not have"
    )

    topic_id = dash["reviews"]["due_now"][0]["topic_id"]
    was_due_at = next(row["due_at"] for row in due["due"] if row["topic_id"] == topic_id)
    moved = client.post(
        "/api/v1/review/complete",
        json={"topic_id": topic_id, "correct": True, "score": 1.0, "latency_ms": 5200, "kind": "probe"},
        headers=learner["headers"],
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["correct"] is True and 0.0 <= moved.json()["expected_recall"] <= 1.0
    after = client.get("/api/v1/review/due", headers=learner["headers"]).json()["due"]
    now_due_at = {row["topic_id"]: row["due_at"] for row in after}.get(topic_id)
    assert now_due_at is None or now_due_at > was_due_at, "a recall you got right has to be scheduled further out"
    missing_topic = client.post("/api/v1/review/complete", json={"correct": True}, headers=learner["headers"])
    assert missing_topic.status_code == 422, "a review result needs the topic it is about"


def test_journey_lists_the_loop_in_order_with_types_the_ui_can_render(client, learner, items, make_answer, session):
    for entry in items[:3]:
        item_id = entry["item"]["id"]
        client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=True)},
            headers=learner["headers"],
        )
    client.post("/api/v1/analyze", params={"session_id": session, "use_llm": "false"}, headers=learner["headers"])
    client.post(f"/api/v1/sessions/{session}/finish", headers=learner["headers"])
    body = client.get("/api/v1/journey?days=7&limit=80", headers=learner["headers"]).json()
    assert body["window_days"] == 7.0 and body["student_id"] == learner["student_id"]
    nodes = body["nodes"]
    assert nodes, "the journey must show what actually happened"
    kinds = {n["kind"] for n in nodes}
    assert {"session", "attempt"} <= kinds, sorted(kinds)
    for node in nodes:
        assert node["ts"] <= body["generated_at"], "a journey cannot contain the future"
        assert node["title"] and node["tone"] in TONES, node["tone"]
        assert isinstance(node.get("meta"), dict)
    ts = [n["ts"] for n in nodes]
    assert ts == sorted(ts), "a journey that reads backwards is not a journey"
    assert any("Answered" in n["title"] or "answered" in n["title"] for n in nodes)
    assert body["stats"]["attempts"] >= 3
    assert isinstance(body["summary"], str) and body["summary"]
    assert set(body) >= {"counts", "phases", "milestones", "stats"}

    # A window that excludes the events has to say so rather than show a stale list.
    tiny = client.get("/api/v1/journey?days=0&limit=80", headers=learner["headers"]).json()
    assert tiny["window_days"] == 0.0 and len(tiny["nodes"]) <= len(nodes)


def test_explain_hints_and_worked_examples_are_available_on_demand(client, learner, items, services, session):
    with services.scope() as s:
        from adapt.db.models import Item

        # An item the bank actually wrote a hint ladder for, so "escalate" can be
        # tested rather than assumed: hint 1 and hint 2 must be different sentences.
        hinted_id = next(
            (cand.id for cand in s.query(Item).filter(Item.active.is_(True)).all() if len(cand.hints or []) >= 2), None
        )
    assert hinted_id, "the bank has to contain at least one item with a hint ladder"

    item_id = items[0]["item"]["id"]
    explained = client.post(
        "/api/v1/explain",
        json={
            "question": "why do I have to make the denominators the same?",
            "session_id": session,
            "topic_id": items[0]["topic_id"],
        },
        headers=learner["headers"],
    )
    assert explained.status_code == 200, explained.text
    answer = explained.json()
    assert answer["provider"] == "local" and answer["answer"].strip()
    assert answer["privacy"]["sent_to_model"] is False, "no key configured, so nothing may claim to have sent it"
    assert answer["level"] in BANDS
    assert isinstance(answer["adaptations_applied"], list) and isinstance(answer["sections"], list)
    assert isinstance(answer["sources"], list)
    assert answer["sources"], "the local tutor grounds answers in the learner's own material"
    assert answer["meta"]["grounded"] is True
    for src in answer["sources"]:
        assert {"kind", "ref", "title"} <= set(src)
        if src["kind"] == "material":
            assert src["material_id"], "so the citation opens the learner's own note"
        else:
            assert src.get("url"), "an external citation has to be clickable"
    assert "meta" in answer and answer["provider_note"]
    assert answer["message_id"].startswith("msg_")

    first = client.post(f"/api/v1/items/{item_id}/hint", json={"session_id": session}, headers=learner["headers"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["hint"].strip() and body["hint_no"] == 1 and body["item_id"] == item_id
    assert not _leaks_answers(body), "a hint may not be the answer"
    assert body["exhausted"] is (body["hints_available"] <= 1)

    second = client.post(
        f"/api/v1/items/{hinted_id}/hint", json={"hints_used": 1, "session_id": session}, headers=learner["headers"]
    ).json()
    third = client.post(
        f"/api/v1/items/{hinted_id}/hint", json={"hints_used": 0, "session_id": session}, headers=learner["headers"]
    ).json()
    assert second["hint"] != third["hint"], "the second hint has to add something"
    assert second["hint_no"] == 2 and second["item_id"] == hinted_id
    # The item has three written hints: the fourth tap has nothing left to give, and
    # has to say so and name the next thing, rather than repeat itself.
    with services.scope() as s:
        from adapt.db.models import Item as _I

        hint_count = len(s.get(_I, hinted_id).hints or [])
    beyond = client.post(
        f"/api/v1/items/{hinted_id}/hint",
        json={"hints_used": hint_count, "session_id": session},
        headers=learner["headers"],
    ).json()
    assert beyond["exhausted"] is True and beyond["hints_available"] == hint_count
    assert "worked example" in beyond["hint"].lower(), "out of hints must name the next thing, not repeat itself"
    assert beyond["hint"] != third["hint"]

    over = client.post(f"/api/v1/items/{hinted_id}/hint", json={"hints_used": 99}, headers=learner["headers"])
    assert over.status_code == 422, "the counter is bounded, and the client is told which field"
    assert over.json()["fields"][0]["loc"] == ["body", "hints_used"]

    example = client.get(f"/api/v1/items/{item_id}/worked-example", headers=learner["headers"]).json()
    assert "steps" in example and example["note"]
    for step in example["steps"]:
        assert step["index"] and step["text"]

    with services.scope() as s:
        from adapt.db.models import Item as I

        steps_id = next((c.id for c in s.query(I).filter(I.active.is_(True)).all() if c.solution_steps), None)
    if steps_id:
        steps_out = client.get(f"/api/v1/items/{steps_id}/worked-example", headers=learner["headers"]).json()
        assert steps_out["steps"], "an item with a solution must show it when asked"

    reveal = client.get(f"/api/v1/items/{item_id}/reveal", headers=learner["headers"])
    assert reveal.status_code == 200, reveal.text
    revealed = reveal.json()
    assert revealed["correct_index"] is not None or revealed["answer"] is not None, (
        "revealing after a question is allowed; it is the plan that must not leak"
    )
    if revealed["choices"]:
        assert sum(1 for ch in revealed["choices"] if ch.get("correct")) == 1, "exactly one choice is the right one"
        assert all(ch.get("why_wrong") for ch in revealed["choices"] if not ch.get("correct")), (
            "a reveal has to teach the mistake, not just show the letter"
        )
    else:
        assert revealed["answer"] is not None and revealed["steps"], "a numeric item reveals its value and its working"
    assert revealed["why_it_is_ok_to_look"]


def test_hint_and_body_item_must_agree(client, learner, items):
    a, b = items[0]["item"]["id"], items[1]["item"]["id"]
    response = client.post(f"/api/v1/items/{a}/hint", json={"item_id": b}, headers=learner["headers"])
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "item_mismatch"
    assert detail["path_item_id"] == a and detail["body_item_id"] == b
    assert detail["note"]
    unknown = client.post("/api/v1/items/itm_nope/hint", json={}, headers=learner["headers"])
    assert unknown.status_code == 404


def test_an_open_question_records_an_event_and_a_row_the_learner_can_read(client, learner, session):
    before = client.get(f"/api/v1/sessions/{session}/events", headers=learner["headers"]).json()["events"]
    explained = client.post("/api/v1/explain", json={"question": "why flip the second fraction", "session_id": session}, headers=learner["headers"]).json()
    assert explained["message_id"].startswith("msg_")
    after = client.get(f"/api/v1/sessions/{session}/events", headers=learner["headers"]).json()["events"]
    assert len(after) > len(before), "asking a question has to leave a trace in the learner's own log"
    assert any(e["type"] == "explanation_opened" for e in after)


# --------------------------------------------------------------------------- #
# voice
# --------------------------------------------------------------------------- #


def test_voice_capabilities_are_an_honest_account_of_the_machine(client, learner):
    body = client.get("/api/v1/voice/capabilities", headers=learner["headers"]).json()
    assert body["stt"]["local_stt"] in (True, False)
    assert body["tts"]["local_tts"] in (True, False)
    assert body["stt"]["detail"], "if it is missing here, the machine has to say why and what to install"
    assert body["stt"]["fallback"] and body["tts"]["fallback"]
    assert body["path"] == "/ws/voice", "the client dials what the server advertises"
    assert "/ws/voice" in body["transport"]
    assert set(body["voices"]) == {"female", "male"}, "the client builds the voice picker from this"
    assert body["barge_in"] is True
    assert "measured separately" in body["note"]

    # The REST fallbacks exist so a browser without a websocket still talks.
    spoken = client.post("/api/v1/voice/speak", json={"text": "Try one more question.", "gender": "female"}, headers=learner["headers"])
    assert spoken.status_code == 200, spoken.text
    said = spoken.json()
    assert said.get("audio") is None or said.get("ok") is not True
    assert "browser" in json.dumps(said).lower() or "speechSynthesis" in json.dumps(said), (
        "with no local TTS the answer must point at the browser's own voice"
    )

    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 8000)
    transcribe = client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("clip.wav", buf.getvalue(), "audio/wav")},
        data={"sample_rate": "16000"},
        headers=learner["headers"],
    )
    assert transcribe.status_code == 200, transcribe.text
    heard = transcribe.json()
    assert heard["backend"] and "segments" in heard and heard["duration_s"] >= 0
    if not body["stt"]["local_stt"]:
        assert heard["text"] == "", "no local model, so claiming a transcript would be a lie"
        assert heard["note"]


def test_websocket_refuses_anonymous_sockets_in_a_way_a_browser_can_read():
    """The reason has to arrive as a frame, not only as a close code.

    A close sent before `accept()` answers the handshake with HTTP 403, so a browser sees code
    1006 with no explanation -- and `voice/page.tsx`, which switches on 4401 to print "sign in
    again", could never reach that branch. A protocol-level client saw 4401 fine, which is
    exactly why the gap survived its own test.
    """
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from adapt.api.app import app

    # A fresh client: the shared jar would authenticate the socket for us.
    with TestClient(app) as anon:
        with anon.websocket_connect("/ws/voice") as ws:
            said = json.loads(ws.receive_text())
            assert said["t"] == "error" and said["where"] == "auth", said
            assert "authentication" in said["detail"], said
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_text()
            assert excinfo.value.code == 4401, "the code the page switches on"


def test_websocket_text_turn_answers_counts_the_turn_and_keeps_the_transcript(client, learner):
    with client.websocket_connect("/ws/voice?sr=16000&encoding=s16le&voice_on=false") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["t"] == "hello"
        assert hello["sample_rate_in"] == 16000 and hello["sample_rate_out"] >= 8000
        assert hello["encoding"] == "s16le"
        status = hello["status"]
        assert status["state"] == "idle" and status["turns"] == 0
        assert status["stt"] and status["tts"] and status["vad"], "which engine answered is part of the answer"
        assert status["voice_enabled"] is False, "voice_on=false has to be honoured by this socket"

        ws.send_text(json.dumps({"t": "text", "text": "I keep forgetting to flip the fraction when I divide"}))
        seen: list[str] = []
        reply = None
        for _ in range(12):
            try:
                message = json.loads(ws.receive_text())
            except Exception:  # pragma: no cover - socket closed early
                break
            seen.append(message["t"])
            if message["t"] == "reply":
                reply = message
                break
        assert reply is not None, seen
        assert reply["text"].strip() and "meta" in reply
        assert "fraction" in reply["text"].lower(), "the answer has to be about the topic that was asked about"
        assert reply["meta"].get("provider") in {"local", "gemini"}
        assert reply["ms"] >= 0
        # The voice path renders items through the same substitution the cards use, so the two
        # ways a template can leak (a `{token}` left in, or an answer key riding along) have to
        # be checked here as well as on the screen.
        import re as _re

        assert not _re.search(r"\{[a-z_]+\}", reply["text"]), f"unrendered template in the spoken answer: {reply['text'][:140]}"
        assert "{" not in reply["text"] and "}" not in reply["text"], reply["text"][:140]
        blob = json.dumps(reply)
        assert "answer_spec" not in blob and "correct_index" not in blob, "the voice reply carried an answer key"
        # Speech comes after the words: with no local TTS the socket names the
        # fallback rather than leaving the client waiting for audio that never comes.
        follow_up = json.loads(ws.receive_text())
        assert follow_up["t"] in {"speak_unavailable", "tts_chunk"}, follow_up
        if follow_up["t"] == "speak_unavailable":
            assert follow_up["reason"] == "voice_off" and follow_up["text"] == reply["text"]

        ws.send_text(json.dumps({"t": "status"}))
        report = None
        for _ in range(6):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "status":
                report = frame
                break
        assert report is not None
        assert report["turns"] >= 1 and report["state"] in {"idle", "listening", "thinking"}

        # Turning speech back on has to be acknowledged with the values now in force --
        # a config frame that is quietly ignored is the worst outcome for a client.
        ws.send_text(json.dumps({"t": "config", "gender": "male", "voice_on": True}))
        ack = json.loads(ws.receive_text())
        assert ack["t"] == "config_ack", ack
        assert ack["gender"] == "male" and ack["voice_enabled"] is True and ack["barge_in"] is True

        ws.send_text(json.dumps({"t": "status"}))
        for _ in range(6):
            frame = json.loads(ws.receive_text())
            if frame["t"] == "status":
                assert frame["gender"] == "male" and frame["voice_enabled"] is True
                break
        else:  # pragma: no cover
            pytest.fail("no status frame after a config change")


# --------------------------------------------------------------------------- #
# the machine's own account of itself
# --------------------------------------------------------------------------- #


def test_status_quotas_and_privacy_explain_the_running_configuration(client, learner):
    status = client.get("/api/v1/status", headers=learner["headers"]).json()
    assert status["mode"] == "local" and status["version"]
    enc = status["encryption"]
    assert enc["requested"] == "field-only" and enc["field_level_encryption"] is True
    assert enc["driver"] == "sqlite" and "SQLCipher" in enc["driver_detail"], (
        "no sqlcipher wheel here, so the status page must say column-level only"
    )
    assert enc["file_encrypted"] is False
    assert any("answers" in f.lower() for f in enc["fields_encrypted"])
    assert status["llm"]["configured"] is False and status["llm"]["provider"] == "none"
    assert status["llm"]["note"]
    assert status["database"]["plaintext_header"] in (True, False)
    assert status["voice"]["stt"]["local_stt"] in (True, False)
    assert "mutation" in status["rate_limits"]["scopes"]

    quotas = client.get("/api/v1/quotas", headers=learner["headers"]).json()
    assert quotas["rate_limit_multiplier"] == 60  # raised for the suite; 1.0 in production
    assert quotas["buckets"]["guest_create"][0] == 360.0
    assert quotas["budget"]["calls"] == 120 and quotas["calls_left"] == quotas["budget"]["calls"] - quotas["used"]["calls"]
    assert set(quotas["free_tiers"]) >= {"gemini"}
    assert quotas["day"]

    privacy = client.get("/api/v1/privacy", headers=learner["headers"]).json()
    assert privacy["telemetry"]["allowed_event_types"]
    assert "keystrokes" in " ".join(privacy["telemetry"]["never_recorded"])
    forbidden = privacy["telemetry"]["forbidden_keys"]
    assert "keystrokes" in forbidden and "clipboard" in forbidden
    assert privacy["sent_to_gemini"] and privacy["storage"] and privacy["learner_controls"]
    assert any("delete" in str(x).lower() for x in privacy["learner_controls"])

    allow = client.get("/api/v1/events/allowlist", headers=learner["headers"]).json()
    assert "hint_no" in allow["allowed"]["hint_opened"], "the allowlist names the fields per type"
    assert "keystrokes" not in json.dumps(allow["allowed"]), "no event type may accept a keystroke field"
    assert "keystrokes" in allow["forbidden_keys"] and allow["rule"]


def test_the_learner_reads_their_own_audit_trail(client, learner, services):
    """Nothing about the learner may be visible only to the operator."""
    empty = client.get("/api/v1/audit?limit=20", headers=learner["headers"]).json()["entries"]
    client.post("/api/v1/me/onboarding", json={"preferences": {"goal": "papers"}}, headers=learner["headers"])
    after = client.get("/api/v1/audit?limit=20", headers=learner["headers"]).json()["entries"]
    assert all(e["actor_id"] == learner["student_id"] for e in after), "no other learner's rows in my trail"
    assert isinstance(empty, list)
    limited = client.get("/api/v1/audit?limit=1", headers=learner["headers"]).json()["entries"]
    assert len(limited) <= 1
    capped = client.get("/api/v1/audit?limit=100000", headers=learner["headers"]).json()["entries"]
    assert len(capped) <= 200, "the limit has a ceiling; this table can be large"


def test_a_learner_can_delete_their_own_data(client, services, registered_learner):
    login = client.post(
        "/api/v1/login", json={"email": registered_learner["email"], "password": registered_learner["password"]}
    )
    assert login.status_code == 200, login.text
    csrf = {"x-csrf-token": login.json()["csrf_token"]}
    out = client.delete("/api/v1/me", headers=csrf)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["deleted"] is True
    assert "purge-student" in body["hard_purge_available_via"], "and the way to make it permanent is named"
    assert "every query path" in body["note"]

    with services.scope() as s:
        from adapt.db.models import Student

        row = s.get(Student, registered_learner["student_id"])
        assert row is not None and row.deleted_at is not None, "tombstoned now, purged by the operator's command"
    assert client.get("/api/v1/me").status_code == 401, "the cookie is cleared too, not only the flag"

    # Deleting a learner must also log the *login* out: the address no longer works.
    again = client.post(
        "/api/v1/login", json={"email": registered_learner["email"], "password": registered_learner["password"]}
    )
    assert again.status_code == 401, "a deleted learner cannot sign back in'' and their history stays unreachable"


def test_rate_limiting_answers_with_retry_after(client, learner, services, items, make_answer, monkeypatch):
    """The wiring is what is tested, so the limiter is forced to say no.

    The refusal has to come from the *route's* bucket, not the middleware's: one
    limiter now serves both (see ``create_app``), so blocking every scope would make
    this assert that the per-IP flood cap works instead, with a different shape.
    """
    monkeypatch.setattr(
        services.limiter, "allow", lambda scope, key, *, n=1.0: (True, 0.0) if scope == "ip" else (False, 3.0)
    )
    response = client.post(
        "/api/v1/attempts",
        json={"session_id": "lrs_whatever", "item_id": items[0]["item"]["id"], "answer": 0},
        headers=learner["headers"],
    )
    assert response.status_code == 429, response.text
    body = response.json()["detail"]
    assert body["error"] == "rate_limited"
    assert body["retry_after_s"] == 3.0
    assert body["scope"] == "mutation"
    assert response.headers.get("retry-after") == "4", "a client needs the header, not only the body"


def test_an_approved_change_is_measured_and_the_learner_is_told_in_words(client, learner, items, make_answer):
    """The loop promises "and then we check whether it helped" -- this is that promise at the API.

    The statistics were unit-tested; what was never checked is the *wiring*: that an insufficient
    sample is labelled insufficient rather than rounded into a verdict, that a verdict is written
    onto the live change and the proposal, that a sentence comes back with it (the payload had a
    technical ``explanation`` and the journey looked for ``verdict_words``, which nothing wrote),
    and that the learner's own screens can show the result.
    """
    headers = learner["headers"]
    bad = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=headers).json()["session_id"]
    for _ in range(2):
        for entry in items[:8]:
            client.post(
                "/api/v1/attempts",
                json={"session_id": bad, "item_id": entry["item"]["id"], "answer": make_answer(entry["item"]["id"], correct=False)},
                headers=headers,
            )
    assert client.post(f"/api/v1/analyze?session_id={bad}", headers=headers).status_code == 200
    pending = client.get("/api/v1/adaptations/pending", headers=headers).json()["proposals"]
    target = next((p for p in pending if p.get("strategy") == "difficulty_recalibration"), None)
    assert target, [p.get("strategy") for p in pending]
    accepted = client.post(
        f"/api/v1/adaptations/{target['id']}/decision",
        json={"decision": "accept", "answered_by": "student"},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text

    # too few answers to conclude anything, and it says so instead of guessing
    early = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=headers).json()["session_id"]
    for entry in items[:2]:
        client.post(
            "/api/v1/attempts",
            json={"session_id": early, "item_id": entry["item"]["id"], "answer": make_answer(entry["item"]["id"], correct=True)},
            headers=headers,
        )
    first = client.post("/api/v1/adaptations/evaluate", headers=headers)
    assert first.status_code == 200, first.text
    rows = first.json()["evaluated"]
    mine = next((r for r in rows if r["proposal_id"] == target["id"]), None)
    assert mine, rows
    assert mine["status"] == "gathering_evidence", mine
    assert int(mine["n_after"]) < 6, mine
    assert isinstance(mine.get("verdict_words"), str) and "not enough" in mine["verdict_words"].lower(), mine
    history = client.get("/api/v1/adaptations/history", headers=headers).json()["decisions"]
    row = next(h for h in history if h["id"] == target["id"])
    assert row["status"] == "approved", "an insufficient sample must not be filed as a verdict"
    assert not row.get("evaluation_status"), row

    # enough answers, every one of them right where the before-window was all wrong
    later = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=headers).json()["session_id"]
    for _ in range(2):
        for entry in items[:8]:
            client.post(
                "/api/v1/attempts",
                json={"session_id": later, "item_id": entry["item"]["id"], "answer": make_answer(entry["item"]["id"], correct=True)},
                headers=headers,
            )
    second = client.post("/api/v1/adaptations/evaluate", headers=headers).json()
    mine = next((r for r in second["evaluated"] if r["proposal_id"] == target["id"]), None)
    assert mine and mine["status"] == "evaluated", mine
    assert int(mine["n_after"]) >= 6, mine
    assert mine["effect"]["verdict"] == "supported", mine
    assert float(mine["effect"]["mean_delta"]) > 0, mine
    words = mine["verdict_words"]
    assert "%" in words and "before the change" in words, words
    assert "{" not in words and '"' not in words, f"a sentence, not a payload: {words}"

    # the finding is kept where the learner looks: on the live change, in history, in the timeline
    live = next(a for a in client.get("/api/v1/adaptations/active", headers=headers).json()["active"] if a["proposal_id"] == target["id"])
    assert live["effect_summary"] and live["effect_summary"]["verdict_words"] == words, live["effect_summary"]
    again = client.get("/api/v1/adaptations/history", headers=headers).json()["decisions"]
    row = next(h for h in again if h["id"] == target["id"])
    assert row["status"] == "evaluated" and row["evaluation_status"] == "supported", row
    assert row.get("outcome", {}).get("verdict_words") == words, row
    dash = client.get("/api/v1/dashboard", headers=headers).json()
    assert int(dash["measured_outcomes"]["evaluated"]) >= 1, dash["measured_outcomes"]
    milestones = [m for m in client.get("/api/v1/journey?days=7&limit=40", headers=headers).json()["milestones"] if m.get("type") == "adaptation_effect"]
    assert milestones and milestones[-1]["verdict_words"] == words, milestones[-1] if milestones else "no effect milestone on the timeline"


def test_the_per_ip_refusal_answers_with_the_same_shape_as_every_other_429(client, monkeypatch, services):
    """One contract for "slow down", whichever bucket said it.

    The per-scope refusal nests the wait under ``detail``; the middleware's per-IP refusal
    used to answer with a bare sentence and the number beside it. A client written to back
    off politely from one therefore hammered through the other -- including the shipped demo,
    which computed ``detail.retry_after_s`` and got NaN, i.e. no wait at all.
    """
    monkeypatch.setattr(services.limiter, "allow", lambda scope, key, *, n=1.0: (False, 2.5) if scope == "ip" else (True, 0.0))
    response = client.get("/api/v1/health")
    assert response.status_code == 429, response.text
    body = response.json()
    detail = body["detail"]
    assert isinstance(detail, dict), f"detail is a bare sentence again: {detail!r}"
    assert detail["error"] == "rate_limited"
    assert detail["scope"] == "ip"
    assert float(detail["retry_after_s"]) == 2.5
    assert float(body["retry_after_s"]) == 2.5, "the old top-level key was dropped"
    assert isinstance(detail.get("message"), str) and len(detail["message"]) > 10, "a person still gets a sentence"
    assert response.headers.get("retry-after") == "3", response.headers.get("retry-after")


# --------------------------------------------------------------------------- #
# sign-in abuse: the buckets DEFAULT_LIMITS declares
# --------------------------------------------------------------------------- #


def _real_limits(monkeypatch, services):
    """Restore production bucket sizes for one test.

    The suite runs with ``ADAPT_RATE_LIMIT_MULTIPLIER=60`` so that creating a learner
    fifty times is not mistaken for flooding. A throttle test has to see the numbers a
    deployed instance uses, so it puts its own limiter on the attached services.
    """
    from adapt.security.ratelimit import build_limiter

    limiter = build_limiter(multiplier=1.0)
    monkeypatch.setattr(services, "limiter", limiter)
    return limiter


def test_wrong_passwords_lock_the_address_out_before_the_password_is_read(client, monkeypatch, services, registered_learner):
    """``auth_fail`` existed in the config table with nothing consulting it.

    So the endpoint accepted about two guesses per second for as long as an attacker
    liked -- the shared per-IP bucket (120/min) is spent on the whole site, not on
    sign-in. This is the regression test for that: five failures, then the *correct*
    password is refused too, because a lockout that still verifies the password is not
    a lockout.
    """
    _real_limits(monkeypatch, services)
    for i in range(5):
        r = client.post("/api/v1/login", json={"email": registered_learner["email"], "password": f"wrong guess {i} xx"})
        assert r.status_code == 401, r.text
    blocked = client.post("/api/v1/login", json={"email": registered_learner["email"], "password": registered_learner["password"]})
    assert blocked.status_code == 429, f"the bucket is not enforced: {blocked.text}"
    detail = blocked.json()["detail"]
    assert detail["error"] == "too_many_attempts" and detail["scope"] == "auth_fail"
    assert float(detail["retry_after_s"]) >= 1
    assert blocked.headers.get("retry-after"), "a client has to know how long to wait"


def test_signing_in_correctly_never_costs_the_learner_their_own_lockout(client, monkeypatch, services, registered_learner):
    """The other half of the design: successes do not spend the failure budget."""
    _real_limits(monkeypatch, services)
    for _ in range(8):
        r = client.post("/api/v1/login", json={"email": registered_learner["email"], "password": registered_learner["password"]})
        assert r.status_code == 200, f"a learner was locked out for logging in: {r.text}"
    still = client.post("/api/v1/login", json={"email": registered_learner["email"], "password": "wrong password entirely"})
    assert still.status_code == 401, "a first failure must not be pre-punished"


def test_learner_creation_is_capped_per_connection(client, monkeypatch, services):
    """A public tunnel must not become a learner factory.

    The row must not be written when the bucket is empty: a refused request that still
    creates a learner would only hide the flooding, not stop it.
    """
    limiter = _real_limits(monkeypatch, services)
    codes = []
    for i in range(9):
        r = client.post("/api/v1/students", json={"display_name": f"Flood {i}", "as_guest": True})
        codes.append(r.status_code)
    assert codes[:6] == [200] * 6, codes
    assert codes[-1] == 429, codes
    assert client.post("/api/v1/students", json={"display_name": "Flood 9", "as_guest": True}).json()["detail"]["scope"] == "guest_create"
    snapshot = limiter.snapshot()
    assert snapshot["scopes"]["guest_create"] == {"rate_per_min": 6.0, "burst": 6.0}


def test_no_declared_rate_limit_scope_is_decoration():
    """Every bucket in ``DEFAULT_LIMITS`` must be consulted by some caller.

    ``auth_fail`` and ``guest_create`` sat in the table for the whole build with no
    caller, while ``/api/v1/quotas`` printed them as if they protected the app. The
    provider buckets are the exception: they are keyed by ``ResourceService.name``, so
    the generic gate in ``resources/base.py`` stands in for four literals.
    """
    import pathlib
    import re

    from adapt.security.ratelimit import DEFAULT_LIMITS

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "adapt"
    src = "\n".join(p.read_text() for p in root.rglob("*.py") if p.name != "ratelimit.py")
    by_name = re.search(r"limiter\.allow\(\s*self\.name", src) is not None
    assert by_name, "the provider gate disappeared from resources/base.py"
    for scope in DEFAULT_LIMITS:
        if scope in {"openalex", "arxiv", "openlibrary", "youtube"}:
            continue
        assert f'"{scope}"' in src, f"rate-limit scope {scope!r} is configured but never used"


# --------------------------------------------------------------------------- #
# consent provenance: who answered, and through what
# --------------------------------------------------------------------------- #


def _proposal_for(client, learner, items, make_answer, session):
    """Twelve wrong answers, then AI 1 -- which is how a proposal actually appears."""
    for entry in items[:12]:
        item_id = entry["item"]["id"]
        client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": make_answer(item_id, correct=False)},
            headers=learner["headers"],
        )
    client.post("/api/v1/analyze", params={"session_id": session, "use_llm": "false"}, headers=learner["headers"])
    rows = client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()["proposals"]
    assert rows, "no proposal to answer: the fixture stopped exercising the gate"
    return rows[0]["id"]


def test_cli_will_not_answer_for_a_learner_it_cannot_ask(client, learner, items, make_answer, session):
    """The terminal is not the learner.

    ``adapt decide`` is the only way to turn a change on outside the browser, so if it
    could type a decision on somebody's behalf, the invariant the whole design rests on
    would be a UI detail. With no terminal to ask in, it must refuse and change nothing.
    """
    from typer.testing import CliRunner

    from adapt.cli import app as cli_app

    proposal_id = _proposal_for(client, learner, items, make_answer, session)
    before = client.get("/api/v1/adaptations/active", headers=learner["headers"]).json()
    result = CliRunner().invoke(
        cli_app,
        ["decide", "--for", learner["student_id"], "--proposal", proposal_id, "--decision", "accept"],
        input="",
    )
    assert result.exit_code == 2, result.output
    assert "learner" in result.output.lower()
    after = client.get("/api/v1/adaptations/active", headers=learner["headers"]).json()
    assert after == before, "a refused CLI call still applied the change"
    still_pending = client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()["proposals"]
    assert proposal_id in [p["id"] for p in still_pending], "the proposal was consumed by a call that refused"


def test_cli_answer_records_the_channel_that_gave_it(client, learner, services, items, make_answer, session):
    """An attested answer is allowed -- and is marked as attested, forever.

    The proposal row itself stores only status and timestamps, so the durable proof of
    *who* said yes lives in the audit log. If that ever stops carrying the channel, a
    scripted consent becomes indistinguishable from a learner's, which is the failure
    mode this project exists to avoid.
    """
    from typer.testing import CliRunner

    from adapt.cli import app as cli_app

    proposal_id = _proposal_for(client, learner, items, make_answer, session)
    result = CliRunner().invoke(
        cli_app,
        [
            "decide",
            "--for",
            learner["student_id"],
            "--proposal",
            proposal_id,
            "--decision",
            "accept",
            "--learner-answered",
        ],
        input="",
    )
    assert result.exit_code == 0, result.output
    entries = client.get("/api/v1/audit", params={"limit": 50}, headers=learner["headers"]).json()["entries"]
    consent = [e for e in entries if e["action"].startswith("adaptation_") and (e.get("object") or "").endswith(proposal_id)]
    assert consent, [e["action"] for e in entries]
    row = consent[0]["detail"]
    assert row["answered_by"] == "student", row
    assert row["channel"] == "cli_attested", row
    assert row["decision"] == "accept" and row["answered_at"], row


# --------------------------------------------------------------------------- #
# the event loop belongs to the learners, not to the models
# --------------------------------------------------------------------------- #


class _FakeUpload:
    """A stand-in for Starlette's ``UploadFile``: sequential reads, and it counts them.

    ``endless`` models the attack -- a client that keeps sending and never lets
    ``Content-Length`` say how big it is.
    """

    def __init__(self, data: bytes = b"", *, chunk: int = 1 << 20, endless: bool = False) -> None:
        self.data = data
        self.chunk = chunk
        self.endless = endless
        self.pos = 0
        self.reads = 0
        self.closed = False

    delivered = 0

    async def read(self, size: int = -1) -> bytes:
        self.reads += 1
        # size == -1 means "the rest, however long it takes" -- which is exactly what
        # ``await file.read()`` asks a server for, and what the cap has to survive.
        take = (1 << 24) if size in (-1, 0) else min(size, self.chunk)
        if self.endless:
            out = b"\x00" * take
        else:
            out = self.data[self.pos : self.pos + take]
        self.pos += len(out)
        _FakeUpload.delivered += len(out)
        return out

    async def close(self) -> None:
        self.closed = True


async def _ticks_during(coro, *, interval: float = 0.005, seconds: float = 0.35) -> tuple[object, int]:
    """Run ``coro`` while a ticker counts loop iterations; return its result and the ticks."""
    import asyncio

    ticks = 0
    spinning = True

    async def ticker() -> None:
        nonlocal ticks
        while spinning:
            ticks += 1
            await asyncio.sleep(interval)

    task = asyncio.create_task(ticker())
    try:
        result = await coro
    finally:
        spinning = False
        await task
    return result, ticks


async def test_an_oversized_clip_is_refused_without_being_buffered_first(monkeypatch):
    """The cap has to be read *while* streaming.

    ``await file.read()`` and then compare was a limit a chunked request could walk
    straight past, because the middleware only inspects ``Content-Length``.
    """
    from adapt.api.routes.system import _read_capped

    _FakeUpload.delivered = 0
    file = _FakeUpload(endless=True)
    with pytest.raises(Exception) as exc:
        await _read_capped(file, 8 * 1024 * 1024)
    assert exc.value.__class__.__name__ == "HTTPException"
    assert exc.value.status_code == 413
    assert "8388608" in str(exc.value.detail), exc.value.detail
    assert file.reads <= 10, f"it read the whole flood before objecting: {file.reads} megabyte chunks"
    # A finite clip that is simply too big is refused too, and nothing about that
    # depends on the client lying about Content-Length.
    big = _FakeUpload(b"\x00" * (9 * 1024 * 1024))
    with pytest.raises(Exception) as too_big:
        await _read_capped(big, 8 * 1024 * 1024)
    assert too_big.value.status_code == 413
    small = _FakeUpload(b"\x01\x00" * 4000)
    assert await _read_capped(small, 8 * 1024 * 1024) == b"\x01\x00" * 4000
    assert small.closed is False  # the *caller* owns closing; _read_capped must not double-close


async def test_a_flooded_clip_is_refused_before_it_is_buffered_or_transcribed(client, monkeypatch):
    """The cap has to hold at the handler, not just in a helper somebody could stop calling.

    A client that never sends ``Content-Length`` walks past the middleware's header check,
    so this is the request that used to be answered by reading everything into memory and
    *then* deciding it was too large. ``delivered`` is the number that proves the
    difference: a buffered implementation takes the whole flood, a capped read stops a
    megabyte past the line.
    """
    from types import SimpleNamespace

    from adapt.api.routes import system as SYS

    class MustNotRun:
        def transcribe(self, samples, *, sample_rate):  # noqa: ANN001 - stub of the real engine
            raise AssertionError("the model was handed a clip that exceeded the cap")

    monkeypatch.setattr(SYS.STT, "get_stt", lambda st: MustNotRun())
    _FakeUpload.delivered = 0
    with pytest.raises(Exception) as exc:
        await SYS.transcribe(file=_FakeUpload(endless=True), sample_rate=16000, s=None, student=None)
    assert exc.value.status_code == 413, exc.value
    assert _FakeUpload.delivered <= 9 * 1024 * 1024, (
        f"the handler buffered {_FakeUpload.delivered} bytes into RAM before applying the cap"
    )


async def test_transcription_runs_off_the_event_loop(client, monkeypatch):
    """A whisper pass is seconds of CPU: on the loop it silences every other learner.

    The websocket pipeline already used ``asyncio.to_thread``; the REST fallback did not,
    so the same model work blocked only on the path a browser uses when the socket is
    down. Measured by whether the loop keeps ticking, which is the property that matters.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    from adapt.api.routes import system as SYS

    class SlowSTT:
        def transcribe(self, samples, *, sample_rate):  # noqa: ANN001 - stub of the real engine
            time.sleep(0.3)
            return SimpleNamespace(as_dict=lambda: {"text": "hello", "backend": "stub", "ms": 300})

    monkeypatch.setattr(SYS.STT, "get_stt", lambda st: SlowSTT())
    clip = _FakeUpload(b"\x01\x00" * 16000)  # 64 KB of 16-bit mono samples
    result, ticks = await _ticks_during(SYS.transcribe(file=clip, sample_rate=16000, s=None, student=None))
    assert clip.closed, "the upload is left open on a handler that can fail mid-read"
    assert result["backend"] == "stub"
    assert ticks > 30, f"the loop advanced only {ticks} times during a 300 ms transcription -- it was blocked"
    await asyncio.sleep(0)


async def test_speech_synthesis_runs_off_the_event_loop(client, monkeypatch):
    """Same property for /voice/speak (Kokoro synthesis, not I/O)."""
    import time

    from adapt.api.routes import system as SYS

    def slow_synthesize(settings, text, *, gender="female"):  # noqa: ANN001 - mirrors the real signature
        time.sleep(0.3)
        return None

    # The handler imports it inside the function (a deliberate lazy import for a heavy
    # optional dependency), so the patch has to go on the module it comes from.
    import adapt.voice.tts as TTS

    monkeypatch.setattr(TTS, "synthesize_or_none", slow_synthesize)
    result, ticks = await _ticks_during(SYS.speak(body={"text": "One more."}, s=None, student=None))
    assert result["available"] is False and result["fallback"] == "browser speechSynthesis"
    assert ticks > 30, f"synthesis ran on the loop: {ticks} ticks in 300 ms"


# --------------------------------------------------------------------------- #
# the item bank: export -> edit -> import, and who is allowed to do it
# --------------------------------------------------------------------------- #


def test_numbas_export_then_import_is_rerun_safe(client, learner, services):
    """The blueprint's editing loop, end to end, including the second time through.

    Re-importing must update the same rows rather than duplicating them: a teacher
    edits, exports, imports, edits again, and a bank that grows a copy per round is
    unusable.
    """
    import tempfile
    from pathlib import Path

    from sqlalchemy import func, select

    from adapt.db.models import Item

    with services.scope() as s:
        before_total = s.execute(select(func.count()).select_from(Item)).scalar_one()

    exported = client.get("/api/v1/numbas/export", headers=learner["headers"])
    assert exported.status_code == 200, exported.text
    pack = exported.json()
    assert pack["items"] >= 10, pack
    blob = Path(pack["path"]).read_bytes()

    first = client.post(
        "/api/v1/numbas/import",
        content=blob,
        headers={**learner["headers"], "content-type": "application/octet-stream"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["imported"] + body["updated"] == pack["items"], body
    # Our own export says which row each question came from, so the first import of an
    # unedited export must update in place rather than add a second copy of the bank.
    assert body["imported"] == 0, f"an unedited round trip created {body['imported']} new items: {body}"
    assert body["updated"] == pack["items"], body

    second = client.post(
        "/api/v1/numbas/import",
        content=blob,
        headers={**learner["headers"], "content-type": "application/octet-stream"},
    )
    again = second.json()
    assert second.status_code == 200, second.text
    assert again["imported"] == 0, f"a re-import duplicated the bank: {again}"
    assert again["updated"] == body["updated"], again

    with services.scope() as s:
        from sqlalchemy import func, select

        from adapt.db.models import Item

        n = s.execute(select(func.count()).select_from(Item)).scalar_one()
    assert n == before_total, f"the round trip changed the size of the bank: {before_total} -> {n}"
    assert not list(Path(tempfile.gettempdir()).glob("tmp*.numbas")), "imports leave their temp files behind"

    # A *fresh* export has to carry the same identity as the last one. It used not to:
    # the export dropped ``numbas_ref``, so every round trip minted new uuids and added
    # another copy of the whole bank to the item table.
    again2 = client.get("/api/v1/numbas/export", headers=learner["headers"]).json()
    blob2 = Path(again2["path"]).read_bytes()
    third = client.post(
        "/api/v1/numbas/import",
        content=blob2,
        headers={**learner["headers"], "content-type": "application/octet-stream"},
    )
    assert third.status_code == 200, third.text
    assert third.json()["imported"] == 0, f"re-export then re-import duplicated the bank: {third.json()}"

    # and nothing the round trip produced was quietly deactivated or left unrendered
    with services.scope() as s:
        from sqlalchemy import select

        from adapt.db.models import Item

        from adapt.assessment import numbas as NB

        rows = list(s.execute(select(Item).where(Item.source == "numbas")).scalars().all())
        inactive = [r.id for r in rows if not r.active]
        dirty = [
            r.id
            for r in rows
            if not NB.renders_clean(
                {c.name: getattr(r, c.name) for c in Item.__table__.columns}
            )[0]
        ]
    assert not inactive, f"the round trip deactivated items: {inactive[:4]}"
    assert not dirty, f"imported items still contain unrendered templates: {dirty[:4]}"


def test_a_corrupt_package_leaves_no_temp_file_and_answers_400(client, learner):
    """Cleanup on the failure path, because that is when a leak actually happens."""
    import tempfile
    from pathlib import Path

    before = set(Path(tempfile.gettempdir()).glob("*.numbas"))
    r = client.post(
        "/api/v1/numbas/import",
        content=b"PK\x03\x04not really a zip",
        headers={**learner["headers"], "content-type": "application/octet-stream"},
    )
    assert r.status_code == 400, f"a broken package answered {r.status_code}: {r.text[:120]}"
    body = r.json()["detail"]
    assert body["error"] == "unreadable_package" and "not really a zip" not in r.text
    after = set(Path(tempfile.gettempdir()).glob("*.numbas"))
    assert not (after - before), f"left behind: {sorted(x.name for x in (after - before))[:3]}"


def test_oversized_package_is_refused(client, learner):
    from adapt.api.routes.tutor import PACKAGE_CAP_BYTES

    r = client.post(
        "/api/v1/numbas/import",
        content=b"x" * (PACKAGE_CAP_BYTES + 1024),
        headers={**learner["headers"], "content-type": "application/octet-stream"},
    )
    assert r.status_code == 413, r.text[:200]
    assert "too large" in str(r.json()["detail"])


def test_on_a_public_tunnel_shared_mutations_need_the_phrase(client, learner, services, monkeypatch):
    """``mode=tunnel`` is a shipped deployment, and it makes the bank *shared*.

    Any signed-in learner could otherwise add questions another learner is taught and
    graded on, or refit the cohort priors every mastery number comes from. Local mode
    stays open -- there, the person at the terminal is the operator.
    """
    monkeypatch.setattr(services.settings, "mode", "tunnel")
    # A tunnel does not trust the test harness's Host header (that is correct behaviour,
    # and this test would otherwise be refused before it reached the route).
    monkeypatch.setattr(services.settings, "extra_allowed_hosts", ["testclient", "testserver"])
    r = client.post(
        "/api/v1/numbas/import",
        content=b'{"questions": []}',
        headers={**learner["headers"], "content-type": "application/json"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"] == "shared_mutation_locked"
    assert "ADAPT_DEMO_PASSPHRASE" in r.json()["detail"]["next"]
    train = client.post("/api/v1/learning/train", headers=learner["headers"])
    assert train.status_code == 403, train.text

    monkeypatch.setenv("ADAPT_DEMO_PASSPHRASE", "judge-phrase")
    wrong = client.post(
        "/api/v1/numbas/import",
        content=b'{"questions": []}',
        headers={**learner["headers"], "content-type": "application/json"},
    )
    assert wrong.status_code == 403 and wrong.json()["detail"]["error"] == "demo_passphrase_required"
    right = client.post(
        "/api/v1/numbas/import",
        content=b'{"questions": []}',
        headers={**learner["headers"], "content-type": "application/json", "x-adapt-demo-passphrase": "judge-phrase"},
    )
    assert right.status_code == 200, right.text
    assert right.json()["imported"] == 0 and right.json()["updated"] == 0


def test_a_pending_proposal_arrives_with_its_source(client, learner, items, make_answer):
    """The list the learner decides from has to carry what the catalog knows about the change.

    ``proposal_dict`` can be called without the strategy, and when it is, the label, the
    "what it does" line and the citations are all silently empty -- so the pending card showed a
    proposal with no source while other views of the same proposal had one, and a learner was
    left to judge an experiment without being told it was one.
    """
    from adapt.learning import strategies as ST

    headers = learner["headers"]
    sid = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=headers).json()["session_id"]
    for _ in range(2):
        for entry in items[:8]:
            client.post(
                "/api/v1/attempts",
                json={"session_id": sid, "item_id": entry["item"]["id"], "answer": make_answer(entry["item"]["id"], correct=False)},
                headers=headers,
            )
    assert client.post(f"/api/v1/analyze?session_id={sid}", headers=headers).status_code == 200

    pending = client.get("/api/v1/adaptations/pending").json()["proposals"]
    assert pending, "the manufactured struggle produced nothing to look at"
    for prop in pending:
        assert prop["strategy_label"], f"{prop['strategy']} reached the learner without a label"
        assert prop["what_it_does"], f"{prop['strategy']} reached the learner with no description"
        if ST.CATALOG[prop["strategy"]].citations:
            assert [c["ref"] for c in prop["citations"]], f"{prop['strategy']} has citations in the catalog but none reached the pending list"

    decided = client.get("/api/v1/adaptations/history").json()["decisions"]
    for prop in decided:
        assert prop["strategy_label"], "the history list lost the label too"

    dash = client.get("/api/v1/dashboard").json()
    for prop in dash.get("pending_proposals") or []:
        assert prop["strategy_label"] and prop["what_it_does"], prop["strategy"]


def test_a_modified_approval_returns_what_the_learner_changed(client, learner, items, make_answer):
    """``decision: modified`` used to be the whole receipt.

    Editing a proposal is the one answer where the learner's numbers replace the model's, so the
    response has to say which of them were applied and which were refused -- and the note they
    typed with it has to come back, not only sit in the history endpoint.
    """
    headers = learner["headers"]
    sid = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=headers).json()["session_id"]
    for _ in range(2):
        for entry in items[:8]:
            client.post(
                "/api/v1/attempts",
                json={"session_id": sid, "item_id": entry["item"]["id"], "answer": make_answer(entry["item"]["id"], correct=False)},
                headers=headers,
            )
    assert client.post(f"/api/v1/analyze?session_id={sid}", headers=headers).status_code == 200
    pending = client.get("/api/v1/adaptations/pending").json()["proposals"]
    target = next((p for p in pending if p.get("strategy") == "difficulty_recalibration"), None)
    assert target, [p.get("strategy") for p in pending]

    out = client.post(
        f"/api/v1/adaptations/{target['id']}/decision",
        json={
            "decision": "modify",
            "answered_by": "student",
            "note": "two at a time, not four",
            "modifications": {"target": 0.9, "questions_per_session": 3},
        },
        headers=headers,
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["decision"] == "modify" and body["status"] == "modified", body
    assert body["modifications"]["applied"] == {"target": 0.9}, body
    assert body["modifications"]["refused_keys"] == ["questions_per_session"], "a key the strategy does not expose must be refused, and say so"
    assert body["refused_keys"] == ["questions_per_session"], body
    assert body["student_note"] == "two at a time, not four", body

    # and what took effect is the learner's number, not the one AI 1 proposed
    active = client.get("/api/v1/adaptations/active").json()["active"]
    assert len(active) == 1 and active[0]["payload"]["target"] == 0.9, active
    assert active[0]["source"] == "modified", active[0]
    hist = client.get("/api/v1/adaptations/history").json()["decisions"]
    row = next((r for r in hist if r["id"] == target["id"]), None)
    assert row is not None and "two at a time" in json.dumps(row), hist
