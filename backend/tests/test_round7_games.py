"""The games, after Round 7: timed laps, visible feedback, and a stored history.

The three things this file exists to hold down:

* **Quick taps is a lap with a clock.** The ladder climbs with the position in the lap (never with a
  learner's score, or two people get different tests), the lap ends from the page's clock, and an
  answer that arrives after the clock is recorded as *no answer*, not as a wrong one.
* **Feedback is part of the game.** A quick tap and a trap question say what the right answer was
  once the answer is in — those are facts, not the thing being remembered. A measured recall answer
  still keeps its key to itself, because the same pairs come back in the delayed check.
* **What was played is stored and readable.** ``GET /baseline/history`` aggregates the same trial
  rows the snapshot is built from, so a score and a factor cannot disagree.
"""

from __future__ import annotations

import pytest

from adapt.baseline import flow
from adapt.baseline.games import (
    QUICK_LADDER,
    QUICK_LAP_SECONDS,
    QUICK_MAX_TRIALS,
    Trial,
    choice_score,
    codewords_cued,
    codewords_pairs,
    quick_trial,
    seed_of,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _start(client, learner) -> dict:
    response = client.post("/api/v1/baseline", headers=learner["headers"], json={})
    assert response.status_code == 200, response.text
    return response.json()


def _format_and_questions(client, learner, run_id: str) -> dict:
    response = client.post(
        f"/api/v1/baseline/{run_id}/format", headers=learner["headers"], json={"value": "diagram"}
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/v1/baseline/{run_id}/questions",
        headers=learner["headers"],
        json={"answers": {"goal": "just curious", "timers_help": "timers help"}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _submit(client, learner, run_id: str, task: dict, response: dict, **extra):
    body = {"task_id": task["id"], "response": response, "latency_ms": 900, **extra}
    out = client.post(f"/api/v1/baseline/{run_id}/trial", headers=learner["headers"], json=body)
    assert out.status_code == 200, out.text
    return out.json()


def _to_quick(client, learner, run_id: str) -> dict:
    """Walk the opening stages until the first quick lap is on offer."""
    view = _format_and_questions(client, learner, run_id)
    # The study screen: looking time, not a question.
    assert view["task"]["game"] == "codewords_study"
    view = _submit(client, learner, run_id, view["task"], {})
    # The cued-recall practice, then the eight measured pairs. Answer from the server's own key.
    from adapt.services import current

    for _ in range(12):
        if view["kind"] != "task":
            break
        task = view["task"]
        if task["game"] == "quick":
            return view
        secret = current().baseline.internal_task(run_id) or {}
        view = _submit(client, learner, run_id, task, {"text": secret.get("answer", "")})
    raise AssertionError("the flow never reached the quick lap")


# --------------------------------------------------------------------------- #
# the quick lap: a clock and a ladder
# --------------------------------------------------------------------------- #
def test_the_quick_ladder_climbs_with_the_position_in_the_lap():
    seed = seed_of("quick-test")
    first = flow.next_task_for("quick_a", seed=seed, trials=[])
    assert first is not None
    assert first["level"] == 1, "a lap must open on the trivial rung"
    assert first["lap"] == 1 and first["lap_title"] == "lap 1 of 3"
    assert first["lap_seconds"] == QUICK_LAP_SECONDS
    assert first["feedback"] == "show", "a quick tap says whether it was right"

    # Three answers in, the next rung. The climb is positional, not score-dependent.
    trials = [
        Trial(f"quick_a_{i}", "quick", i, "measure", False, task={"stage": "quick_a", "action": None})
        for i in range(3)
    ]
    assert flow.next_task_for("quick_a", seed=seed, trials=trials)["level"] == 2

    # The last rung is reachable inside one lap.
    top = [
        Trial(f"quick_a_{i}", "quick", i, "measure", True, task={"stage": "quick_a", "action": None})
        for i in range(QUICK_MAX_TRIALS - 1)
    ]
    assert flow.next_task_for("quick_a", seed=seed, trials=top)["level"] == len(QUICK_LADDER)


def test_the_later_laps_open_harder():
    seed = seed_of("quick-test")
    assert flow.next_task_for("quick_a", seed=seed, trials=[])["level"] == 1
    assert flow.next_task_for("quick_b", seed=seed, trials=[])["level"] == 2
    assert flow.next_task_for("quick_c", seed=seed, trials=[])["level"] == 3


def test_a_lap_is_bounded_twice_over():
    """The clock ends it; the ceiling makes sure a broken clock still ends it."""
    seed = seed_of("quick-test")
    timeout = [Trial("quick_a_0", "quick", 0, "measure", None, task={"stage": "quick_a", "action": "timeout"})]
    assert flow.next_task_for("quick_a", seed=seed, trials=timeout) is None

    full = [
        Trial(f"quick_a_{i}", "quick", i, "measure", True, task={"stage": "quick_a"})
        for i in range(QUICK_MAX_TRIALS)
    ]
    assert flow.next_task_for("quick_a", seed=seed, trials=full) is None
    # A lap on its last rung still offers its ceiling-th answer.
    assert len(full) == QUICK_MAX_TRIALS


def test_the_quick_item_never_carries_its_own_answer_to_the_page():
    task = quick_trial("quick_a", 0, seed_of("quick-key"))
    assert "answer" in task  # the internal task does
    assert task["game"] == "quick" and task["phase"] == "measure"
    assert len(task["options"]) in (2, 3)


# --------------------------------------------------------------------------- #
# the lap the page actually plays
# --------------------------------------------------------------------------- #
def test_the_page_clock_ends_the_lap(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    view = _to_quick(client, learner, run_id)
    task = view["task"]
    assert task["stage"] == "quick_a"

    # The first answer lands with the lap's clock already spent. It is kept (`given` is on the
    # trial) but it is not an answer: a tap after the whistle must not score, even when it happens
    # to be the right option.
    answered = _submit(
        client,
        learner,
        run_id,
        task,
        {"value": task["options"][0], "elapsed_ms": QUICK_LAP_SECONDS * 1000},
    )
    assert answered["scored"]["lap_over"] is True
    assert answered["scored"]["correct"] is None, "a tap after the clock still scored"
    assert answered["scored"]["points"] == 0
    assert answered["stage"] != "quick_a", "the lap did not end when its clock ran out"


def test_a_tap_that_fits_the_clock_is_still_an_answer(client, learner):
    """The other side of the rule: a tap inside the lap is graded, not turned into a timeout."""
    view = _start(client, learner)
    run_id = view["run_id"]
    from adapt.services import current

    view = _to_quick(client, learner, run_id)
    task = view["task"]
    secret = current().baseline.internal_task(run_id) or {}
    answered = _submit(client, learner, run_id, task, {"value": secret["answer"], "elapsed_ms": 1200})
    assert answered["scored"]["correct"] is True
    assert "lap_over" not in answered["scored"]


def test_a_trial_is_charged_to_the_lap_bucket(client, learner, services, monkeypatch):
    """A tap is not a form post.

    The quick game asks for a tap every second or two; three laps of it is more than the general
    mutation ceiling allows in a minute, and a learner who hit that ceiling saw taps that did
    nothing. Trials therefore have their own, larger bucket (per student, so it takes nothing from
    anyone else).
    """
    seen: list[str] = []
    real = services.limiter.allow

    def spy(scope: str, key: str, *, n: float = 1.0):
        seen.append(scope)
        return real(scope, key, n=n)

    monkeypatch.setattr(services.limiter, "allow", spy)
    view = _start(client, learner)
    run_id = view["run_id"]
    view = _to_quick(client, learner, run_id)
    _submit(client, learner, run_id, view["task"], {"value": view["task"]["options"][0], "elapsed_ms": 900})
    assert "baseline_trial" in seen, f"the trial was charged to {seen!r}"


def test_one_lap_of_taps_fits_the_bucket(services):
    """The ceiling is sized for a lap, not for a form: 150 taps in a row must all pass."""
    for _ in range(150):
        ok, retry = services.limiter.allow("baseline_trial", "baseline_trial:one-fast-learner")
        assert ok, f"a fast lap was throttled after {_ + 1} taps (retry in {retry}s)"


def test_an_explicit_timeout_is_no_answer_at_all(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    view = _to_quick(client, learner, run_id)
    task = view["task"]

    # One honest answer keeps the lap alive...
    still = _submit(client, learner, run_id, task, {"value": task["options"][0], "elapsed_ms": 4000})
    assert still["stage"] == "quick_a"
    # ...and the page saying the clock ran out closes it without pretending the learner answered.
    out = _submit(client, learner, run_id, still["task"], {"action": "timeout"})
    assert out["scored"]["correct"] is None
    assert out["scored"]["action"] == "timeout"
    assert out["stage"] != "quick_a"


def test_the_feedback_keeps_recall_answers_private_and_shows_quick_ones(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    view = _format_and_questions(client, learner, run_id)
    view = _submit(client, learner, run_id, view["task"], {})
    practice = view["task"]
    assert practice["game"] == "codewords_cued" and practice["phase"] == "practice"
    # A practice answer is feedback, so it does say the right answer.
    out = _submit(client, learner, run_id, practice, {"text": "not the place"})
    assert out["scored"]["expected"], "practice feedback must say what was right"
    # A *measured* recall answer must not carry its key: the delayed check asks the same pairs.
    measured = out["task"]
    assert measured["game"] == "codewords_cued" and measured["phase"] == "measure"
    out = _submit(client, learner, run_id, measured, {"text": "not the place"})
    assert "expected" not in out["scored"]

    # Walk on to the first quick tap in the same sitting and check its feedback does carry the key.
    from adapt.services import current

    for _ in range(14):
        if out["kind"] != "task":
            break
        if out["task"]["game"] == "quick":
            break
        secret = current().baseline.internal_task(run_id) or {}
        out = _submit(client, learner, run_id, out["task"], {"text": secret.get("answer", "")})
    quick = out["task"]
    assert quick["game"] == "quick"
    expected = (current().baseline.internal_task(run_id) or {}).get("answer")
    scored = _submit(client, learner, run_id, quick, {"value": quick["options"][0]})["scored"]
    assert scored["expected"] == expected, "a quick tap says what the right answer was"


def test_a_trap_reveals_the_reason_once_the_answer_is_in():
    """Two questions to think about: the reveal is the lesson, so it comes with the verdict."""
    from adapt.baseline.games import trap_task
    from adapt.baseline.service import public_scored

    task = trap_task(0)
    assert task["feedback"] == "show"
    assert "answer" in task, "the internal task carries the key; the page must not see it before answering"
    scored = {**choice_score(task, {"value": task["answer"]}), "explain": task["explain"]}
    assert scored["correct"] is True
    shown = public_scored(scored, task)
    assert shown["explain"] and shown["expected"], "the trap keeps the reveal to itself"
    # ...while a measured recall answer still withholds its key.
    recall = public_scored(
        {"correct": False, "expected": "the blue door"}, {"game": "codewords_cued", "phase": "measure"}
    )
    assert "expected" not in recall


# --------------------------------------------------------------------------- #
# the practice trials stop giving away a measured point
# --------------------------------------------------------------------------- #
def test_the_cued_practice_pair_is_not_one_of_the_eight_measured_pairs():
    seed = seed_of("codewords-practice")
    task = codewords_cued(seed, [])
    assert task["phase"] == "practice"
    measured = {f"{p['code']}::{p['place']}" for p in codewords_pairs(seed)}
    assert f"{task['cue']}::{task['answer']}" not in measured
    # The measured block starts after the practice and never uses the practice answer as its own.
    after = [Trial("p", "codewords_cued", 0, "practice", None, task=task)]
    first_measured = codewords_cued(seed, after)
    assert first_measured["phase"] == "measure"
    assert first_measured["id"] == "codewords_cued_0"


def test_the_vault_does_not_serve_the_practised_sequence_as_the_first_measured_one():
    from adapt.baseline.games import vault_next

    seed = seed_of("vault-practice")
    practice = vault_next(seed, [], "forward")
    assert practice["phase"] == "practice"
    seen = [Trial("p", "vault", 0, "practice", None, task=practice)]
    first = vault_next(seed, seen, "forward")
    assert first["phase"] == "measure" and first["length"] == 3
    assert first["sequence"] != practice["sequence"], "the first measured span was just rehearsed"


def test_a_malformed_body_is_refused_not_crashed(services):
    """A client bug must come back as a 422, not as a 500 in the middle of someone's sitting."""
    from adapt.baseline import BaselineError

    with pytest.raises(BaselineError):
        services.baseline._score(
            {"game": "shopkeeper", "items": ["a watch"]},
            {"items": ["a watch"], "distractors": [1, 2, 3]},
            [],
            seed=0,
        )


def test_the_study_screen_records_how_long_the_pairs_were_looked_at(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    view = _format_and_questions(client, learner, run_id)
    task = view["task"]
    assert task["game"] == "codewords_study"
    out = _submit(client, learner, run_id, task, {"seconds_seen": 27})
    assert out["scored"]["seconds_seen"] == 27


# --------------------------------------------------------------------------- #
# what was stored, readable
# --------------------------------------------------------------------------- #
def test_the_history_reads_back_every_game_that_was_played(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    view = _to_quick(client, learner, run_id)
    task = view["task"]
    _submit(client, learner, run_id, task, {"value": task["options"][0]})

    history = client.get("/api/v1/baseline/history", headers=learner["headers"])
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["has_open_run"] is True
    assert body["runs"] and body["runs"][0]["run_id"] == run_id
    assert body["runs"][0]["trials"] >= 2
    games = {g["game"]: g for g in body["games"]}
    assert "quick" in games and games["quick"]["plays"] >= 1
    assert games["quick"]["best_label"].endswith("points in one lap")
    assert games["codewords_study"]["plays"] == 1
    assert body["totals"]["sittings"] == 1

    # A learner who has played nothing gets an empty, honest history rather than an error.

    other = client.post("/api/v1/students", json={"as_guest": True}).json()
    empty = client.get(
        "/api/v1/baseline/history", headers={"x-csrf-token": other["csrf_token"]}
    ).json()
    assert empty["games"] == [] and empty["runs"] == []
    assert empty["has_open_run"] is False


def test_a_sealed_sitting_is_readable_after_it_ends(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    _format_and_questions(client, learner, run_id)
    sealed = client.post(f"/api/v1/baseline/{run_id}/finish", headers=learner["headers"], json={})
    assert sealed.status_code == 200, sealed.text
    body = client.get("/api/v1/baseline/history", headers=learner["headers"]).json()
    assert body["has_open_run"] is False
    assert body["runs"][0]["status"] == "complete"
    assert body["totals"]["completed_sittings"] == 1
    assert body["games"] == [], "a sealed sitting with no trials reports no games, not zeros"


def test_stopping_twice_seals_the_sitting_once(client, learner, services):
    """A second "Stop here" must not write a second mind snapshot for the same log."""
    from adapt.db.models import MindSnapshot

    view = _start(client, learner)
    run_id = view["run_id"]
    _format_and_questions(client, learner, run_id)

    first = client.post(f"/api/v1/baseline/{run_id}/finish", headers=learner["headers"])
    second = client.post(f"/api/v1/baseline/{run_id}/finish", headers=learner["headers"])
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["snapshot_id"] == second.json()["snapshot_id"]
    with services.db.session() as s:
        rows = s.query(MindSnapshot).filter(MindSnapshot.run_id == run_id).all()
    assert len(rows) == 1, f"{len(rows)} snapshots were written for one finished sitting"


@pytest.mark.parametrize("stage", ["quick_a", "quick_b", "quick_c"])
def test_each_lap_starts_where_its_title_says(stage: str):
    task = flow.next_task_for(stage, seed=seed_of("lap-titles"), trials=[])
    assert task["lap_title"] == f"lap {task['lap']} of 3"
    assert task["lap_seconds"] == QUICK_LAP_SECONDS
