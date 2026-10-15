"""The engine end to end: open a scene, predict, change the variables, ask why, come back tomorrow.

These run against the real app (middleware, lifespan, one SQLite file), and every claim that
matters is checked twice -- once in the response and once in the database. A 200 that did not
reach the row is exactly the bug class this file exists to catch: "the lesson was lost on
refresh", "the prediction was never graded", "the change was applied anyway".
"""

from __future__ import annotations

import json

from adapt.db.models import Attempt, BktState, EvidenceRecord, LlmCall
from adapt.learning import scenespec, sims


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _lessons(client, learner):
    r = client.get("/api/v1/lessons", headers=learner["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _open(client, learner, ident, **kw):
    r = client.post("/api/v1/lessons/open", json={"scene_id": ident, **kw}, headers=learner["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _post(client, learner, path, body):
    return client.post(path, json=body, headers=learner["headers"])


# --------------------------------------------------------------------------- #
# the list, and what a scene actually is
# --------------------------------------------------------------------------- #


def test_the_built_in_lessons_are_seeded_and_playable(client, learner):
    data = _lessons(client, learner)
    scenes = data["scenes"]
    assert len(scenes) >= len(sims.KINDS), "a simulation shipped without a lesson to show it"
    assert {s["kind"] for s in scenes} == set(sims.KINDS)
    for scene in scenes:
        assert scene["has_renderer"] is True, scene
        assert scene["frame_count"] > 0
        assert scene["prediction_count"] > 0, f"{scene['title']} asks the learner nothing"
        assert scene["citations"], f"{scene['title']} has no source behind it"
        assert scene["origin"] == "builtin"
    assert data["counts"]["engine_kinds"] == len(sims.KINDS)


def test_seeding_survives_a_restart_without_changing_ids(client, learner, settings):
    """A restart re-seeds, and the ids a learner's history points at do not move.

    A second :class:`Database` over the same file is what a restart is, as far as this promise is
    concerned: a new connection pool, new objects, the same bytes on disk.
    """
    from adapt.db.engine import Database
    from adapt.services import lessons as L

    before = {s["spec_id"]: s["id"] for s in _lessons(client, learner)["scenes"]}
    assert before
    db2 = Database(settings)
    with db2.session() as s2:
        again = L.ensure_scenes(s2)
        after = {r.spec_id: r.id for r in s2.query(__import__("adapt.db.models", fromlist=["LessonScene"]).LessonScene).all()}
    db2.dispose()
    assert again["created"] == 0, again
    assert {k: v for k, v in after.items() if k in before} == before, (
        "a restart renumbered the lessons; every LessonRun pointing at them would be orphaned"
    )


def test_the_kinds_endpoint_describes_the_real_limits(client):
    r = client.get("/api/v1/lessons/kinds")
    assert r.status_code == 200
    data = r.json()
    assert set(data["kinds"]) == set(sims.KINDS)
    assert data["limits"]["title"] == scenespec.TITLE_MAX
    for kind, meta in data["kinds"].items():
        module = sims.module_for(kind)
        assert meta["max_frames"] == module.MAX_FRAMES
        assert set(meta["params"]) == set(module.PARAMS)
        for spec in meta["params"].values():
            assert "cast" not in spec  # a callable is not something to send a browser


# --------------------------------------------------------------------------- #
# opening, playing, resuming
# --------------------------------------------------------------------------- #


def test_open_returns_one_trace_and_the_player_needs_nothing_else(client, learner, services):
    scene = _lessons(client, learner)["scenes"][0]
    opened = _open(client, learner, scene["id"])
    assert opened["ok"] and opened["resumed"] is False
    assert opened["frame_count"] == scene["frame_count"] == len(opened["frames"])
    assert opened["frame_index"] == 0
    assert opened["renderer"]["available"] is True
    for i, frame in enumerate(opened["frames"]):
        assert frame["i"] == i and frame["kind"] and frame["narration"]
    # the trace really is the simulation's, byte for byte
    fresh = sims.frames_for(scene["kind"], scene["params"])
    assert json.loads(json.dumps(opened["frames"])) == json.loads(json.dumps(fresh))
    assert opened["prediction_frames"] == sorted(opened["prediction_frames"])
    assert [f["i"] for f in opened["frames"] if f.get("prompt")] == opened["prediction_frames"]
    with services.db.session() as s:
        from adapt.db.models import LessonRun

        run = s.get(LessonRun, opened["run_id"])
        assert run is not None and run.status == "active" and run.frame_index == 0


def test_position_survives_a_refresh_and_a_restart(client, learner, services, settings):
    scene = _lessons(client, learner)["scenes"][0]
    opened = _open(client, learner, scene["id"])
    run_id = opened["run_id"]
    target = min(4, opened["frame_count"] - 1)
    r = _post(client, learner, f"/api/v1/lessons/run/{run_id}/progress", {"frame_index": target, "action": "seek", "seconds": 12})
    assert r.status_code == 200, r.text
    assert r.json()["frame_index"] == target and r.json()["frames_viewed"] >= 1

    again = _open(client, learner, scene["id"])
    assert again["run_id"] == run_id, "resuming the same scene opened a second run instead"
    assert again["resumed"] is True and again["frame_index"] == target

    # and past a real restart: a fresh Database over the same file, reading the same row back
    from adapt.db.engine import Database

    db2 = Database(settings)
    with db2.session() as s2:
        from adapt.db.models import LessonRun

        reloaded = s2.get(LessonRun, run_id)
        assert reloaded is not None and reloaded.frame_index == target, "the lesson was lost on restart"
        assert reloaded.status == "active"
    db2.dispose()


def test_progress_is_clamped_to_the_trace_that_exists(client, learner):
    opened = _open(client, learner, _lessons(client, learner)["scenes"][0]["id"])
    huge = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/progress", {"frame_index": 3000, "action": "seek"})
    assert huge.status_code == 200, huge.text
    assert huge.json()["frame_index"] == opened["frame_count"] - 1
    assert huge.json()["at_end"] is True
    # a negative index is not "go to the start", it is a client bug, and it is refused
    neg = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/progress", {"frame_index": -5, "action": "back"})
    assert neg.status_code == 422, neg.text
    assert neg.json()["fields"][0]["loc"][-1] == "frame_index"
    # an index outside the range the API agrees to look at is refused before it reaches the row
    absurd = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/progress", {"frame_index": 99999, "action": "seek"})
    assert absurd.status_code == 422, absurd.text
    assert absurd.json()["error"] == "validation_failed"


def test_open_refuses_a_scene_that_does_not_exist(client, learner):
    r = _post(client, learner, "/api/v1/lessons/open", {"scene_id": "scene_nope"})
    assert r.status_code == 404, r.text
    assert "no such scene" in json.dumps(r.json())


def test_another_learners_run_is_not_visible(client, learner, services):
    opened = _open(client, learner, _lessons(client, learner)["scenes"][0]["id"])
    other = client.post("/api/v1/students", json={"display_name": "Other", "age_band": "16-18", "as_guest": True}).json()
    headers = {"x-csrf-token": other["csrf_token"]}
    for path in (f"/api/v1/lessons/run/{opened['run_id']}",):
        r = client.get(path, headers=headers)
        assert r.status_code == 404, (path, r.text)
    r = client.post(
        f"/api/v1/lessons/run/{opened['run_id']}/progress", json={"frame_index": 2, "action": "step"}, headers=headers
    )
    assert r.status_code == 404, r.text
    assert client.get("/api/v1/lessons/runs", headers=headers).json()["runs"] == []


# --------------------------------------------------------------------------- #
# predictions
# --------------------------------------------------------------------------- #


def test_a_prediction_is_graded_by_running_the_simulation_again(client, learner, services):
    scenes = [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "binary_search"]
    opened = _open(client, learner, scenes[0]["id"])
    asked = [f for f in opened["frames"] if f.get("prompt")]
    assert asked
    frame = asked[1] if len(asked) > 1 else asked[0]
    right = sims.module_for("binary_search").expected(opened["params"], frame["i"])
    good = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "choice": right, "ms_taken": 4200})
    assert good.status_code == 200, good.text
    body = good.json()
    assert body["ok"] and body["correct"] is True
    assert body["expected"] == right and body["why"]

    wrong_choice = next((c for c in frame["prompt"]["choices"] if c != right), None)
    assert wrong_choice is not None, "the prompt offered only the correct answer"
    bad = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "choice": wrong_choice}).json()
    assert bad["correct"] is False and bad["expected"] == right and bad["said"] == wrong_choice

    with services.db.session() as s:
        from adapt.db.models import LessonRun

        run = s.get(LessonRun, opened["run_id"])
        assert (run.predictions_total, run.predictions_correct) == (2, 1)
        log = (run.payload or {}).get("predictions") or []
        assert len(log) == 2 and log[0]["correct"] is True and log[1]["correct"] is False
        assert log[0]["prompt"] == frame["prompt"]["type"]


def test_a_wrong_prediction_is_never_recorded_as_right_even_with_a_stray_answer(client, learner, services):
    """The grading comes from the trace, so a client cannot claim its way to a correct mark."""
    opened = _open(client, learner, [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "logic_gates"][0]["id"])
    frame = next(f for f in opened["frames"] if f.get("prompt"))
    r = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "choice": "definitely 1 (high)"})
    assert r.status_code == 200
    assert r.json()["correct"] is False
    r2 = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "correct": True, "expected": "1 (high)"})
    assert r2.json()["correct"] is False, "a client supplied the verdict and it was believed"


def test_an_ungradeable_prompt_is_reported_not_silently_dropped(client, learner):
    opened = _open(client, learner, _lessons(client, learner)["scenes"][0]["id"])
    silent = next(f for f in opened["frames"] if not f.get("prompt"))
    r = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": silent["i"], "choice": "anything"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["correct"] is False
    r2 = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": 99999, "choice": "x"})
    assert r2.status_code == 422, r2.text
    r3 = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": 3000, "choice": "x"})
    assert r3.json()["ok"] is True and r3.json()["correct"] is False and r3.json()["expected"] is None


def test_predictions_are_telemetry_and_never_touch_mastery(client, learner, services):
    """The line AI 1 must not be allowed to blur: scene predictions are evidence, not attempts.

    A row in ``attempts`` or a nudge in ``bkt_state`` here would mean a lesson quietly moved a
    mastery number, which is what the approval gate exists to prevent.
    """
    opened = _open(client, learner, _lessons(client, learner)["scenes"][0]["id"])
    with services.db.session() as s:
        before_attempts = s.query(Attempt).filter(Attempt.student_id == learner["student_id"]).count()
        before_bkt = [(r.topic_id, r.p_know) for r in s.query(BktState).filter(BktState.student_id == learner["student_id"]).all()]
    asked = [f for f in opened["frames"] if f.get("prompt")]
    for frame in asked[:4]:
        right = sims.module_for(opened["kind"]).expected(opened["params"], frame["i"])
        r = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "choice": right})
        assert r.json()["correct"] is True
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/finish", {})
    with services.db.session() as s:
        after_attempts = s.query(Attempt).filter(Attempt.student_id == learner["student_id"]).count()
        after_bkt = [(r.topic_id, r.p_know) for r in s.query(BktState).filter(BktState.student_id == learner["student_id"]).all()]
    assert after_attempts == before_attempts, "a lesson wrote Attempt rows"
    assert after_bkt == before_bkt, "a lesson moved a mastery number"


def test_every_event_names_the_scene_the_api_will_take_back(client, learner, services):
    """One session, one identifier per scene, and it is a usable one.

    The events for a run are written from four different places (open, frame, prediction,
    explain, finish), and if some of them report the row id while others report the content
    id, the log the learner reads back describes two scenes. Worse: only the row id is
    accepted by `POST /lessons/open`, so half the trail could not be replayed.
    """
    session_id = client.post("/api/v1/sessions", json={"mode": "lesson"}, headers=learner["headers"]).json()["session_id"]
    opened = _open(client, learner, _lessons(client, learner)["scenes"][0]["id"], session_id=session_id)
    frame = next(f for f in opened["frames"] if f.get("prompt"))
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/progress", {"frame_index": frame["i"], "action": "pause"})
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "choice": frame["prompt"]["choices"][0]})
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/explain", {"frame_index": frame["i"], "question": ""})
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/finish", {})

    with services.db.session() as s:
        from adapt.db.models import LearnerEvent, LessonScene

        rows = s.query(LearnerEvent).filter(LearnerEvent.session_id == session_id, LearnerEvent.type.like("scene_%")).all()
        assert len(rows) >= 4, [r.type for r in rows]
        named = {(r.payload or {}).get("scene_id") for r in rows}
        assert named == {opened["scene_id"]}, {r.type: (r.payload or {}).get("scene_id") for r in rows}
        scene = s.get(LessonScene, opened["scene_id"])
        assert scene is not None and scene.spec_id not in named, "an event logged the content id where the row id belongs"


def test_lesson_events_are_in_the_session_log_with_only_allowed_fields(client, learner, services):
    session_r = client.post("/api/v1/sessions", json={"mode": "lesson"}, headers=learner["headers"])
    assert session_r.status_code == 200, session_r.text
    session_id = session_r.json()["session_id"]
    opened = _open(client, learner, _lessons(client, learner)["scenes"][0]["id"], session_id=session_id)
    frame = next(f for f in opened["frames"] if f.get("prompt"))
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/progress", {"frame_index": frame["i"], "action": "pause"})
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "choice": frame["prompt"]["choices"][0]})
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/finish", {})
    with services.db.session() as s:
        from adapt.db.models import LearnerEvent

        rows = s.query(LearnerEvent).filter(LearnerEvent.session_id == session_id).all()
        types = [r.type for r in rows]
        for expected in ("scene_opened", "scene_frame", "scene_prediction", "scene_finished"):
            assert expected in types, (types, expected)
        for row in [r for r in rows if r.type.startswith("scene_")]:  # scene_* events only: session_start has its own keys
            payload = row.payload or {}
            assert "keystrokes" not in payload and "screen" not in payload
            assert set(payload) <= {"scene_id", "kind", "frame_count", "resumed", "frame", "action", "speed", "prompt", "correct", "ms_taken", "changed", "replayed", "source", "cached", "frames_viewed", "seconds", "predictions"}
    with services.db.session() as s:
        from adapt.db.models import LessonRun

        run = s.get(LessonRun, opened["run_id"])
        assert run.predictions_total == 1 and run.status == "finished"


# --------------------------------------------------------------------------- #
# changing the variables
# --------------------------------------------------------------------------- #


def test_changing_the_numbers_creates_a_scene_of_your_own(client, learner, services):
    scene = [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "binary_search"][0]
    opened = _open(client, learner, scene["id"])
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/progress", {"frame_index": 3, "action": "pause"})
    values = [3, 9, 11, 18, 24, 31, 40, 52]
    r = _post(
        client,
        learner,
        f"/api/v1/lessons/run/{opened['run_id']}/variables",
        {"params": {"values": values, "target": 31}, "note": "I wanted fewer numbers"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    assert body["frame_index"] == 0 and body["run_id"] != opened["run_id"]
    assert [f["state"]["values"] for f in body["frames"]][0] == values
    assert body["derived_from"] == scene["spec_id"]

    with services.db.session() as s:
        from adapt.db.models import LessonRun, LessonScene

        new_scene = s.get(LessonScene, body["scene_id"])
        assert new_scene.origin == "learner" and new_scene.author_student_id == learner["student_id"]
        assert new_scene.derived_from == scene["spec_id"]
        assert (new_scene.learner_note or {}).get("why") == "I wanted fewer numbers"
        old = s.get(LessonRun, opened["run_id"])
        assert old.status == "stopped" and old.frame_index == 3, "the run they paused on was rewritten instead of kept"
        new = s.get(LessonRun, body["run_id"])
        assert new.param_changes == 1
        assert new.params["values"] == values and new.params["target"] == 31
        assert new.params == (new_scene.spec or {})["params"], "the run recorded different numbers from the scene it plays"

    # and it is theirs alone
    other = client.post("/api/v1/students", json={"display_name": "Watcher", "as_guest": True}).json()
    listing = client.get("/api/v1/lessons", headers={"x-csrf-token": other["csrf_token"]}).json()["scenes"]
    assert not any(x["spec_id"] == body["spec_id"] for x in listing), "a learner's own scene leaked into someone else's list"


def test_two_learners_who_make_the_same_copy_each_have_a_copy(client, learner, services):
    """Content-addressing must not become identity-sharing between two people.

    The same eight numbers typed by two learners is the same content id. With one unique index
    on that id, ``store_spec`` handed the second learner the *first* learner's row: their list
    showed no copy of theirs, the note about why they changed it was written onto a stranger's
    scene, and their run pointed at a lesson they could not see. Finding that needs two real
    sessions, so the second learner gets a second client with its own cookie jar rather than a
    header that impersonates one.
    """
    from fastapi.testclient import TestClient

    a = learner
    with TestClient(client.app) as b_client:
        made = b_client.post("/api/v1/students", json={"display_name": "Same Numbers", "as_guest": True})
        assert made.status_code == 200, made.text
        b = {"student_id": made.json()["student_id"], "headers": {"x-csrf-token": made.json()["csrf_token"]}}
        values = [1, 4, 7, 9, 12, 15, 19, 23]
        mine = _derive(client, a, values, target=12, note="I wanted a shorter list")
        theirs = _derive(b_client, b, values, target=12, note="mine was shorter too")
        assert mine["scene_id"] != theirs["scene_id"], "two learners were handed one row for their own copies"

        with services.db.session() as s:
            from adapt.db.models import LessonScene

            rows = {k: s.get(LessonScene, k) for k in (mine["scene_id"], theirs["scene_id"])}
            assert rows[mine["scene_id"]].author_student_id == a["student_id"]
            assert rows[theirs["scene_id"]].author_student_id == b["student_id"]
            assert rows[mine["scene_id"]].spec_id == rows[theirs["scene_id"]].spec_id, (
                "the same content is still the same content: that is what makes a re-run idempotent"
            )
            assert (rows[mine["scene_id"]].learner_note or {}).get("why") == "I wanted a shorter list"
            assert (rows[theirs["scene_id"]].learner_note or {}).get("why") == "mine was shorter too"

        seen = {}
        for who, sess in ((a, client), (b, b_client)):
            scenes = _lessons(sess, who)["scenes"]
            derived = [x for x in scenes if "(your variables)" in x["title"]]
            assert len(derived) == 1, [x["title"] for x in scenes]
            seen[who["student_id"]] = {x["id"] for x in scenes}
        # each sees the builtins and their own copy, and nothing of the other's
        assert seen[a["student_id"]] != seen[b["student_id"]]
        assert len(seen[a["student_id"]] ^ seen[b["student_id"]]) == 2, seen

        # the same learner deriving the same numbers again is still one row, not two
        again = _derive(client, a, values, target=12)
        assert again["scene_id"] == mine["scene_id"], "the same learner's same numbers authored a second copy"

        # opening by content spec_id resolves each learner's own copy instead of MultipleResultsFound
        open_a = _post(client, a, "/api/v1/lessons/open", {"spec_id": mine["spec_id"]})
        open_b = _post(b_client, b, "/api/v1/lessons/open", {"spec_id": theirs["spec_id"]})
        assert open_a.status_code == 200 and open_a.json()["scene_id"] == mine["scene_id"]
        assert open_b.status_code == 200 and open_b.json()["scene_id"] == theirs["scene_id"]

        # and learner B cannot open learner A's private scene row by scene_id
        forbidden = _post(b_client, b, "/api/v1/lessons/open", {"scene_id": mine["scene_id"]})
        assert forbidden.status_code == 404


def test_review_broken_scene_falls_back_to_scene_frame_count_and_shared_ai2_spec_is_idempotent(client, learner, services, monkeypatch):
    from adapt.learning import scenespec
    from adapt.services import lessons as L

    validated = scenespec.validate({"kind": "sorting", "title": "Shared AI2 Sort", "params": {"values": [8, 3, 5, 1]}})
    assert validated.ok
    with services.db.session() as s:
        first = L.store_spec(s, validated.spec, origin="ai2", student_id=learner["student_id"])
        second = L.store_spec(s, validated.spec, origin="ai2", student_id=learner["student_id"])
        assert first.id == second.id
        assert first.author_student_id is None

    scene = _lessons(client, learner)["scenes"][0]
    opened = _open(client, learner, scene["id"])
    monkeypatch.setattr(sims, "frames_for", lambda kind, params: (_ for _ in ()).throw(RuntimeError("corrupt trace")))
    review = client.get(f"/api/v1/lessons/run/{opened['run_id']}", headers=learner["headers"])
    assert review.status_code == 200, review.text
    assert review.json()["frame_count"] == scene["frame_count"]



def _derive(client, who, values, *, target, note=""):
    scene = [x for x in _lessons(client, who)["scenes"] if x["kind"] == "binary_search"][0]["id"]
    opened = _open(client, who, scene)
    out = _post(
        client,
        who,
        f"/api/v1/lessons/run/{opened['run_id']}/variables",
        {"params": {"values": values, "target": target}, "note": note},
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["ok"] is True, body
    return body


def test_a_bad_set_of_numbers_is_refused_and_nothing_changes(client, learner, services):
    scene = [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "binary_search"][0]
    opened = _open(client, learner, scene["id"])
    before = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/progress", {"frame_index": 2, "action": "pause"}).json()
    r = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/variables", {"params": {"values": [9, 1, 7], "target": 1}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert any("sorted" in e for e in body["errors"]), body
    assert "nothing you typed was applied" in body["note"]
    after = client.get(f"/api/v1/lessons/run/{opened['run_id']}", headers=learner["headers"]).json()
    assert after["frame_index"] == before["frame_index"], "a refused edit still moved the lesson"
    with services.db.session() as s:
        from adapt.db.models import LessonScene

        assert s.query(LessonScene).filter(LessonScene.origin == "learner", LessonScene.author_student_id == learner["student_id"]).count() == 0


def test_a_scene_can_be_re_run_from_the_same_numbers_after_a_refresh(client, learner):
    values = [2, 4, 8, 16, 32, 64]
    scene = [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "binary_search"][0]
    opened = _open(client, learner, scene["id"])
    body = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/variables", {"params": {"values": values, "target": 64}}).json()
    mine = body["scene_id"]
    re_opened = _open(client, learner, mine)
    assert re_opened["frame_index"] == 0
    assert [f["state"]["values"] for f in re_opened["frames"]][0] == values
    again = _post(client, learner, f"/api/v1/lessons/run/{re_opened['run_id']}/variables", {"params": {"values": values, "target": 64}}).json()
    assert again["ok"] is True and again["scene_id"] == mine, "the same numbers authored a second copy of the same scene"


# --------------------------------------------------------------------------- #
# AI 3: why
# --------------------------------------------------------------------------- #


def test_why_is_answered_from_the_trace_and_not_from_a_model(client, learner, services):
    opened = _open(client, learner, [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "binary_search"][0]["id"])
    discard = next(f for f in opened["frames"] if f["kind"] == "discard")
    r = _post(
        client,
        learner,
        f"/api/v1/lessons/run/{opened['run_id']}/explain",
        {"frame_index": discard["i"], "question": "why can we throw that half away?"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["source"] == "deterministic" and body["cached"] is False
    assert "orted" in body["text"], body["text"]
    assert body["frame"] == discard["i"]
    assert body["citations"], "an explanation with nothing behind it"
    with services.db.session() as s:
        assert s.query(LlmCall).filter(LlmCall.purpose == "ai3_narrate").count() == 0, "a model call was made for something the sim knows"
        from adapt.db.models import NarrationCache

        # about *this frame's* row, not the size of the table: the cache is shared between
        # learners on purpose (one answer per scene, not one per person), so a count over the
        # whole table measures how many tests ran before this one
        rows = s.query(NarrationCache).filter(
            NarrationCache.spec_id == opened["spec_id"], NarrationCache.frame_index == discard["i"]
        ).all()
        assert len(rows) == 1 and rows[0].source == "deterministic", [(r.frame_index, r.source, r.question_digest) for r in rows]


def test_both_ways_an_ask_is_answered_return_the_same_keys(client, learner, services, settings, monkeypatch):
    """One endpoint, two code paths, one contract.

    The deterministic answer and the "the model is not answering" fallback come from different
    objects, and a player that reads `text` from one and `answer` from the other shows a blank
    box for the half of asks the simulation could not settle.
    """
    opened = _open(client, learner, _lessons(client, learner)["scenes"][0]["id"])
    asked = next(f for f in opened["frames"] if f.get("prompt"))
    # a question every frame of this kind can answer from its own state, so the deterministic
    # path is taken whatever the trace looks like today
    settled = _post(
        client,
        learner,
        f"/api/v1/lessons/run/{opened['run_id']}/explain",
        {"frame_index": asked["i"], "question": "why does sorted order matter here?"},
    ).json()
    assert settled["source"] in ("deterministic", "cache"), settled
    # a question the trace cannot settle, asked while no model is configured
    stuck = _post(
        client, learner, f"/api/v1/lessons/run/{opened['run_id']}/explain", {"frame_index": asked["i"], "question": "does this remind you of anything?"}
    ).json()
    # `needs_model` is the operator's name for this; the learner-facing key is `needs_extra_help`
    # ("the built-in tutor could not settle this from your material"). §3 keeps the model's name
    # and its token accounting off the wire, so those keys are asserted *absent* here.
    assert settled["needs_extra_help"] is False and stuck["needs_extra_help"] is False
    assert stuck["asked_for_extra_help"] is True and settled["asked_for_extra_help"] is False
    assert set(settled) == set(stuck), (sorted(settled), sorted(stuck))
    for key in ("ok", "text", "source", "frame", "citations", "question"):
        assert key in stuck, key
    for leaked in ("model", "tokens_in", "tokens_out", "provider", "llm"):
        assert leaked not in stuck, f"{leaked} reached the learner"
    assert "answer" not in stuck and "answer" not in settled, "the old name leaked back in"
    assert stuck["text"], stuck


def test_a_repeated_ask_is_served_from_the_cache(client, learner, services):
    opened = _open(client, learner, [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "logic_gates"][0]["id"])
    frame = opened["frames"][1]
    first = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/explain", {"frame_index": frame["i"], "question": ""}).json()
    second = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/explain", {"frame_index": frame["i"], "question": ""}).json()
    assert first["ok"] is True and first["text"]
    assert second["cached"] is True and second["text"] == first["text"]
    stats = client.get("/api/v1/lessons/evidence", headers=learner["headers"]).json()["narration_cache"]
    assert stats["entries"] >= 1 and stats["hits"] >= 1
    assert stats["model_calls_saved"] >= 1


def test_a_question_the_simulation_cannot_answer_says_so_instead_of_inventing(client, learner, services):
    """No model is configured in the test environment, and that must be visible, not papered over."""
    opened = _open(client, learner, _lessons(client, learner)["scenes"][0]["id"])
    r = _post(
        client,
        learner,
        f"/api/v1/lessons/run/{opened['run_id']}/explain",
        {"frame_index": 0, "question": "does this remind you of anything in my other subjects?"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["source"] == "template"
    assert "nothing has been invented" in body["text"] or "not answering" in body["note"], body
    assert "answer" not in body, "the second name for the same field is still on the wire"
    # checked by content, not by the label the writer chose: a fallback stored under any name is
    # a fallback that will be served again as if it were an answer
    with services.db.session() as s:
        from adapt.db.models import NarrationCache

        rows = s.query(NarrationCache).all()
        assert not any((r.text or "").startswith(f"Frame {0} of ") for r in rows), "a no-model fallback was cached as if it were the answer"
        assert all(r.source in {"deterministic", "llm"} for r in rows), [(r.source, r.text[:40]) for r in rows]


def test_explaining_a_scene_with_no_renderer_still_works(client, learner, services):
    """An unknown kind must narrate honestly rather than 500 -- including for a stored spec."""
    from adapt.db.models import LessonScene

    spec = scenespec.builtin_specs()[0]
    fake = dict(spec, id="scn_unknownkind00000", kind="hologram", title="Hologram lesson")
    with services.db.session() as s:
        row = LessonScene(spec_id=fake["id"], kind=fake["kind"], title=fake["title"], origin="ai2", spec=fake, frame_count=0, prediction_count=0)
        s.add(row)
        s.commit()
        scene_id = row.id
    opened = _post(client, learner, "/api/v1/lessons/open", {"scene_id": scene_id})
    assert opened.status_code == 422, opened.text
    assert "no longer runs" in json.dumps(opened.json()), opened.json()
    got = client.get("/api/v1/lessons", headers=learner["headers"]).json()["scenes"]
    card = next(x for x in got if x["spec_id"] == fake["id"])
    assert card["has_renderer"] is False, "the list claimed a renderer for a kind the engine does not have"


# --------------------------------------------------------------------------- #
# authoring, review, evidence
# --------------------------------------------------------------------------- #


def test_validate_reports_the_reason_without_storing_anything(client, learner):
    good = client.post("/api/v1/lessons/validate", json={"spec": {"kind": "sorting", "title": "Sorting", "params": {"values": [3, 1, 2]}}}, headers=learner["headers"])
    assert good.status_code == 200 and good.json()["ok"] is True
    bad = client.post(
        "/api/v1/lessons/validate",
        json={"spec": {"kind": "sorting", "title": "<script>hi</script>", "params": {"values": [3, 1, 2]}}},
        headers=learner["headers"],
    )
    assert bad.status_code == 200 and bad.json()["ok"] is False
    assert any("markup" in e for e in bad.json()["errors"]), bad.json()["errors"]
    assert bad.json()["spec"] is None


def test_authoring_without_a_model_returns_a_real_scene_and_says_which(client, learner):
    r = _post(client, learner, "/api/v1/lessons/author", {"request": "I keep getting binary search wrong", "store": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["source"] == "builtin"
    assert "a written-for-you scene is not available" in body["note"], body["note"]
    assert body["stored"]["kind"] == "binary_search"
    assert body["already_existed"] is True, "an identical scene was stored as if it were new"
    assert "nothing was duplicated" in body["stored_note"], body["stored_note"]
    listing = client.get("/api/v1/lessons", headers=learner["headers"]).json()["scenes"]
    assert any(x["id"] == body["stored"]["id"] for x in listing)


def test_the_review_screen_shows_what_was_predicted_and_what_skipped(client, learner):
    opened = _open(client, learner, [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "logic_gates"][0]["id"])
    asked = [f for f in opened["frames"] if f.get("prompt")]
    first = asked[0]
    right = sims.module_for("logic_gates").expected({}, first["i"])
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": first["i"], "choice": right})
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/finish", {})
    review = client.get(f"/api/v1/lessons/run/{opened['run_id']}", headers=learner["headers"]).json()
    assert review["status"] == "finished"
    assert review["predictions"][0]["correct"] is True
    assert review["predictions"][0]["question"] == first["prompt"]["question"]
    assert review["predictions"][0]["choices"] == first["prompt"]["choices"]
    assert review["totals"]["predictions"] == 1 and review["totals"]["accuracy"] == 1.0
    assert first["i"] not in review["skipped_predictions"]
    assert asked[1]["i"] in review["skipped_predictions"], "the screen said nothing was skipped when something was"
    runs = client.get("/api/v1/lessons/runs", headers=learner["headers"]).json()["runs"]
    assert any(r["run_id"] == opened["run_id"] and r["status"] == "finished" for r in runs)


def test_evidence_summary_reports_predictions_without_calling_them_mastery(client, learner):
    opened = _open(client, learner, [s for s in _lessons(client, learner)["scenes"] if s["kind"] == "sorting"][0]["id"])
    asked = [f for f in opened["frames"] if f.get("prompt")]
    for frame in asked[:3]:
        right = sims.module_for("sorting").expected(opened["params"], frame["i"])
        _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "choice": right})
    data = client.get("/api/v1/lessons/evidence", headers=learner["headers"]).json()
    summary = data["summary"]
    assert summary["predictions"] == 3 and summary["correct"] == 3
    assert summary["accuracy"] == 1.0
    assert summary["by_kind"]["sorting"]["runs"] == 1
    assert "not a mastery number" in summary["note"]
    assert any("mastery" in x for x in data["not_evidence"])


def test_weak_predictions_become_evidence_and_nothing_more(client, learner, services):
    """AI 1 may *see* a scene_prediction_gap; only the learner can turn it into a change."""
    # the countdown scene has the most prompts of any built-in, so the evidence floor of eight
    # predictions is met by a real single pass rather than by padding
    scenes = _lessons(client, learner)["scenes"]
    scene = next(x for x in scenes if x["kind"] == "cpu_cycle" and x["prediction_count"] >= 8)
    opened = _open(client, learner, scene["id"])
    asked = [f for f in opened["frames"] if f.get("prompt")]
    assert len(asked) >= 8
    wrong_count = 0
    for frame in asked:
        right = sims.module_for("cpu_cycle").expected(opened["params"], frame["i"])
        wrong = next((c for c in frame["prompt"]["choices"] if c != right), None)
        if wrong is None:
            continue
        graded = _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/predict", {"frame_index": frame["i"], "choice": wrong}).json()
        assert graded["correct"] is False, graded
        wrong_count += 1
    assert wrong_count >= 8
    _post(client, learner, f"/api/v1/lessons/run/{opened['run_id']}/finish", {})
    observations = services.ai1.collect_evidence(learner["student_id"])
    gaps = [o for o in observations if o["detector"] == "scene_prediction_gap"]
    assert gaps, [o["detector"] for o in observations]
    assert gaps[0]["value"] < gaps[0]["baseline"]
    assert gaps[0]["facts"]["predictions"] >= 8
    with services.db.session() as s:
        rows = s.query(EvidenceRecord).filter(EvidenceRecord.student_id == learner["student_id"], EvidenceRecord.detector == "scene_prediction_gap").all()
        assert rows, "the observation was never stored as evidence"
        assert rows[0].facts.get("note") == "measured on the frames *before* the simulation shows the answer"
        from adapt.db.models import ActiveAdaptation, AdaptationProposal

        assert s.query(ActiveAdaptation).filter(ActiveAdaptation.student_id == learner["student_id"]).count() == 0
        assert s.query(AdaptationProposal).filter(AdaptationProposal.student_id == learner["student_id"]).count() == 0, "a proposal appeared without the learner asking for analysis"
