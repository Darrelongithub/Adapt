"""The mind baseline (round brief section 5): the games, the flow, and the snapshot.

The tests that matter here are the ones that would catch a *measurement* going wrong, not just a
route 500ing:

* synthetic play through every game, with the expected numbers asserted (a span that grows, a
  falsifying probe, a hint bought, a wager that was overconfident);
* partial completion: stopping is normal and nothing invented for a game that was never played;
* determinism: the same learner gets the same codewords and the same machine rules twice, because
  a measurement that changes between two samples is not a measurement;
* the honest states: "not measured" is a state, and it is never a zero;
* no learning-style label anywhere, and a ``learning_style`` key refused;
* the levers exist and each one names the evidence it came from.

Play is driven through the real HTTP API (``client``), because the flow's cursor, the idempotency
rule and the store wiring are exactly the parts a unit test of the generators would miss.
"""

from __future__ import annotations

import json

import pytest

from adapt.baseline import factors as F
from adapt.baseline import flow
from adapt.baseline.games import (
    CODEWORD_PAIRS,
    LOCKED_BOX_HINTS,
    Trial,
    calibration,
    codewords_delayed,
    codewords_pairs,
    is_falsifying,
    machine_next,
    machine_respond,
    machine_rule,
    seed_of,
    shopkeeper_next,
    trap_practice_task,
    trap_task,
    trial_action,
    vault_next,
    wager_allowed,
)

# --------------------------------------------------------------------------- #
# helpers: play the baseline the way the front end does
# --------------------------------------------------------------------------- #
def _start(client, learner) -> dict:
    response = client.post("/api/v1/baseline", headers=learner["headers"], json={})
    assert response.status_code == 200, response.text
    return response.json()


def _pick_format(client, learner, run_id: str, value: str = "diagram") -> dict:
    response = client.post(
        f"/api/v1/baseline/{run_id}/format", headers=learner["headers"], json={"value": value}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _answer_questions(client, learner, run_id: str, *, timers_help: bool = False) -> dict:
    answers = {
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
        "language": "Kiswahili",
        "timers_help": "timers help" if timers_help else "timers stress me",
        "anything_else": "I get distracted easily.",
    }
    response = client.post(
        f"/api/v1/baseline/{run_id}/questions", headers=learner["headers"], json={"answers": answers}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _submit(client, learner, run_id: str, task: dict, response: dict, *, latency: int | None = 1200, wager: int | None = None):
    body: dict = {"task_id": task["id"], "response": response}
    if latency is not None:
        body["latency_ms"] = latency
    if wager is not None:
        body["wager"] = wager
    out = client.post(f"/api/v1/baseline/{run_id}/trial", headers=learner["headers"], json=body)
    assert out.status_code == 200, out.text
    return out.json()


def _correct_response(task: dict, seed: int) -> dict:
    """A synthetic answer from the deterministic stimulus, not from the public answer key."""
    from adapt.baseline.games import _distractors, codewords_pairs, locked_box_task, quick_trial, trap_practice_task, trap_task

    game = task.get("game")
    if game == "codewords_study":
        return {"seconds_seen": 45}
    if game == "vault":
        seq = list(task["sequence"])
        return {"taps": list(reversed(seq)) if task.get("answer_direction") == "backward" else seq}
    if game == "shopkeeper":
        round_no = 0 if task["id"] == "vault_shop_practice" else int(str(task["id"]).rsplit("_", 1)[-1])
        distractors = _distractors(seed, round_no, len(task.get("distractors") or []))
        return {
            "items": list(task["items"]),
            "distractors": {d["id"]: d["answer"] for d in distractors},
        }
    if game in ("codewords_cued", "codewords_next_day"):
        pair = next(p for p in codewords_pairs(seed) if task.get("cue") in (p["code"], p["place"]))
        answer = pair["place"] if task.get("cue") == pair["code"] else pair["code"]
        return {"text": answer}
    if game == "codewords_delayed":
        if task.get("action") == "predict":
            return {"action": "predict", "value": 6}
        if task.get("kind") == "recognition":
            pair = next(p for p in codewords_pairs(seed) if p["code"] == task.get("cue"))
            return {"text": pair["place"]}
        return {"text": ", ".join(p["code"] for p in codewords_pairs(seed))}
    if game == "quick":
        index = int(str(task["id"]).rsplit("_", 1)[-1])
        return {"value": quick_trial(index)["answer"]}
    if game == "trap":
        answer_key = trap_practice_task()["answer"] if task["id"] == "trap_practice" else next(t["answer"] for t in (trap_task(0), trap_task(1)) if t["id"] == task["id"])
        return {"value": answer_key}
    if game == "machine":
        return {"action": "probe", "value": 0}
    if game == "locked_box":
        if task.get("phase") == "practice":
            return {"action": "start"}
        return {"action": "attempt", "value": locked_box_task(seed)["answer"]}
    return {}


def _run_seed(run_id: str) -> int:
    from adapt.db.models import BaselineRun
    from adapt.services import current

    services = current()
    with services.baseline._sess() as s:
        run = s.get(BaselineRun, run_id)
        assert run is not None
        return int(run.seed or 0)


def _play_until(
    client,
    learner,
    run_id: str,
    *,
    stop_stage: str | None = None,
    max_trials: int = 200,
    timers_help: bool = False,
) -> list[dict]:
    """Answer everything until ``stop_stage`` (or the snapshot), using only offered stimuli.

    The synthetic player asks the same flow what is on offer as the real page. Its answer-key
    helper re-derives the seeded task privately; the HTTP payload itself must not carry answers.
    """
    from adapt.baseline.games import wager_allowed

    seed = _run_seed(run_id)
    played: list[dict] = []
    view = client.get(f"/api/v1/baseline/{run_id}", headers=learner["headers"]).json()
    for _ in range(max_trials):
        if view["kind"] == "format":
            view = _pick_format(client, learner, run_id)
            continue
        if view["kind"] == "questions":
            view = _answer_questions(client, learner, run_id, timers_help=timers_help)
            continue
        if view["kind"] != "task":
            break
        if stop_stage and view["stage"] == stop_stage:
            break
        task = view["task"]
        response = _correct_response(task, seed)
        action = response.get("action") or task.get("action")
        wager = 2 if timers_help and view.get("timers_visible") and wager_allowed(task, action=action) else None
        out = _submit(client, learner, run_id, task, response, wager=wager)
        played.append(out)
        view = out
    return played


def _studied_pairs(client, learner, run_id: str) -> list[dict]:
    """The pairs this run showed. They are in the study task, which the API hands out once."""
    with_retry = []
    for attempt in range(30):
        view = client.get(f"/api/v1/baseline/{run_id}", headers=learner["headers"]).json()
        if view["kind"] != "task":
            break
        task = view["task"]
        if task.get("game") == "codewords_study":
            with_retry = list(task["pairs"])
            break
        if task.get("game") == "vault":
            with_retry = list(task.get("pairs") or [])
        # answer whatever is in the way so the study screen comes round
        if task.get("game") == "codewords_cued":
            return []
        break
    if with_retry:
        return with_retry
    # Fall back to the run's seed through the API's own study task is not available any more:
    # read them from the trial log via the service.
    from adapt.services import current

    services = current()
    trials = services.baseline.trials_of(run_id)
    for t in trials:
        if t.game == "codewords_study":
            return list(t.task.get("pairs") or [])
    return []


# --------------------------------------------------------------------------- #
# 1. the flow
# --------------------------------------------------------------------------- #
def test_the_baseline_opens_with_the_format_pick_not_a_game(client, learner):
    view = _start(client, learner)
    assert view["kind"] == "format"
    assert view["stage"] == "format"
    assert len(view["format_options"]) == 3
    assert not view["task"], "a game was served before the format question"


def test_starting_twice_resumes_the_same_run(client, learner):
    first = _start(client, learner)
    second = _start(client, learner)
    assert first["run_id"] == second["run_id"]
    assert first["resumed"] is False
    assert second["resumed"] is True


def test_a_wager_is_refused_when_the_learner_hid_timers(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    seed = _run_seed(run_id)
    _pick_format(client, learner, run_id)
    view = _answer_questions(client, learner, run_id, timers_help=False)
    assert view["timers_visible"] is False
    for _ in range(2):
        task = view["task"]
        assert task is not None
        response = _correct_response(task, seed)
        view = _submit(client, learner, run_id, task, response)
    assert view["task"]["game"] == "codewords_cued" and view["task"]["phase"] == "measure"
    rejected = client.post(
        f"/api/v1/baseline/{run_id}/trial",
        headers=learner["headers"],
        json={"task_id": view["task"]["id"], "response": {"text": "harbour"}, "wager": 1},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["error"] == "baseline_refused"


def test_the_format_pick_is_a_stated_preference_and_reaches_the_settings_table(client, learner, services):
    view = _start(client, learner)
    out = _pick_format(client, learner, view["run_id"], "worked_example")
    assert out["format_pick"] == "worked_example"
    prefs = services.state.preferences(learner["student_id"])
    assert prefs["preferred_format"] == "worked_example"


def test_a_format_that_is_not_offered_is_refused(client, learner):
    view = _start(client, learner)
    response = client.post(
        f"/api/v1/baseline/{view['run_id']}/format",
        headers=learner["headers"],
        json={"value": "telepathy"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "baseline_refused"


def test_the_questions_are_stored_and_an_unknown_key_is_refused(client, learner, services):
    view = _start(client, learner)
    _pick_format(client, learner, view["run_id"])
    out = _answer_questions(client, learner, view["run_id"])
    assert out["kind"] == "task", "the games should follow the questions"
    prefs = services.state.preferences(learner["student_id"])
    assert prefs["feedback_style"] == "gentle"
    assert prefs["language"] == "sw", "the language the learner tapped is normalised to what the app speaks"
    assert prefs["timers_help"] is False
    assert prefs["session_minutes"] == 20

    refused = client.post(
        f"/api/v1/baseline/{view['run_id']}/questions",
        headers=learner["headers"],
        json={"answers": {"learning_style": "visual"}},
    ).json()
    assert "learning_style" in refused["refused"]
    assert "learning_style" not in services.state.preferences(learner["student_id"])


def test_the_stage_order_is_the_order_the_brief_asks_for(client, learner):
    """Study first, vault, machine, locked box, then the delayed codewords."""
    keys = [s["key"] for s in flow.STAGES]
    assert keys.index("codewords_study") < keys.index("codewords_cued") < keys.index("vault")
    assert keys.index("vault") < keys.index("machine") < keys.index("locked_box") < keys.index("codewords_delayed")
    view = _start(client, learner)
    _pick_format(client, learner, view["run_id"])
    _answer_questions(client, learner, view["run_id"])
    seed = _run_seed(view["run_id"])
    seen: list[str] = []
    for _ in range(120):
        view = client.get(f"/api/v1/baseline/{view['run_id']}", headers=learner["headers"]).json()
        if view["kind"] != "task":
            break
        task = view["task"]
        if not seen or seen[-1] != view["stage"]:
            seen.append(view["stage"])
        response = _correct_response(task, seed)
        if task.get("game") == "codewords_delayed" and task.get("kind") == "free":
            break
        out = _submit(client, learner, view["run_id"], task, response)
        if out["kind"] != "task":
            break
    order = [s for s in seen if s != "quick_a"]
    assert "codewords_study" in order and order.index("codewords_study") == 0
    assert order.index("codewords_cued") < order.index("vault") < order.index("machine")
    assert order.index("machine") < order.index("locked_box")


def test_practice_trials_are_unscored_and_public_tasks_hide_answer_keys(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    seed = _run_seed(run_id)
    _pick_format(client, learner, run_id)
    _answer_questions(client, learner, run_id)
    found: set[str] = set()
    for _ in range(120):
        view = client.get(f"/api/v1/baseline/{run_id}", headers=learner["headers"]).json()
        if view["kind"] != "task":
            break
        task = view["task"]
        if task.get("game") in ("vault", "codewords_cued", "machine", "shopkeeper", "locked_box", "trap") and task.get("phase") == "practice":
            assert "answer" not in task, f"{task['game']} leaked its answer key"
            out = _submit(client, learner, run_id, task, _correct_response(task, seed))
            assert out.get("scored", {}).get("correct") is None, (
                f"a practice trial was exposed as scored in {task['game']}: {out.get('scored')}"
            )
            found.add(task["game"])
            # The flow serves the first requested practice only; keep walking so each game intro
            # is exercised and so the delayed codeword sequence remains reachable.
            view = out
            continue
        _submit(client, learner, run_id, task, _correct_response(task, seed))
    assert {"vault", "codewords_cued", "machine", "shopkeeper", "locked_box", "trap"} <= found


# --------------------------------------------------------------------------- #
# 2. the games, with synthetic play
# --------------------------------------------------------------------------- #
def test_vault_span_grows_after_two_successes_and_stops_after_two_failures():
    seed = seed_of("vault-test")
    trials: list[Trial] = []
    task = vault_next(seed, trials, "forward")
    assert task and task["phase"] == "practice"
    trials.append(Trial("p", "vault", 0, "practice", None, task=task))
    # two successes at length 3
    for i in range(2):
        task = vault_next(seed, trials, "forward")
        trials.append(Trial(task["id"], "vault", len(trials), "measure", True, task=task))
    nxt = vault_next(seed, trials, "forward")
    assert nxt["length"] == 4, "two successes at a length should grow the span"
    # two failures end the block
    for _ in range(2):
        task = vault_next(seed, trials, "forward")
        trials.append(Trial(task["id"], "vault", len(trials), "measure", False, task=task))
    assert vault_next(seed, trials, "forward") is None


def test_vault_grading_is_exact_and_keeps_the_near_miss():
    from adapt.baseline.games import vault_score

    task = {"sequence": [1, 2, 3], "answer_direction": "forward"}
    assert vault_score(task, {"taps": [1, 2, 3]})["correct"] is True
    graded = vault_score(task, {"taps": [1, 2, 4]})
    assert graded["correct"] is False
    assert graded["prefix_len"] == 2
    assert graded["position_errors"] == 1
    assert vault_score({"sequence": [1, 2, 3], "answer_direction": "backward"}, {"taps": [3, 2, 1]})["correct"] is True


def test_the_codewords_are_an_unambiguous_half_of_the_bank_and_stable_per_run(client, learner):
    seed = seed_of("codewords-test")
    pairs = codewords_pairs(seed)
    assert len(pairs) == 8
    assert len({p["code"] for p in pairs}) == 8
    assert {p["code"] for p in pairs} <= {c for c, _ in CODEWORD_PAIRS}
    assert codewords_pairs(seed) == pairs, "the same seed must produce the same eight pairs"
    assert codewords_pairs(seed + 1) != pairs


def test_a_falsifying_probe_is_the_one_that_could_prove_the_rule_wrong():
    assert is_falsifying(0, 0)
    assert is_falsifying(-3, 0)
    assert is_falsifying(1000, 0)
    assert is_falsifying(2.5, 0)
    assert not is_falsifying(4, 0), "another confirming number is not a falsifying test"


def test_the_machine_rule_switches_only_in_round_three_and_to_a_different_family():
    seed = seed_of("machine-test")
    for round_no in (1, 2):
        first, second, _ = machine_rule(seed, round_no)
        assert second is None
        assert first.apply(3) != 3
    first, second, switch_at = machine_rule(seed, 3)
    assert second is not None and switch_at == 3
    assert first.family != second.family, "a switch the learner cannot notice is not a test of noticing"
    assert second.apply(4) != first.apply(4)


def test_machine_progress_uses_response_actions_and_terminates_at_five_probes():
    seed = seed_of("machine-response-action")
    trials: list[Trial] = []
    practice = machine_next(seed, trials, 1)
    assert practice and practice["phase"] == "practice"
    trials.append(Trial(practice["id"], "machine", 0, "practice", None, response={"action": "probe"}, task=practice))
    for attempt in range(5):
        task = machine_next(seed, trials, 1)
        assert task is not None and task["phase"] == "measure"
        response = {"action": "probe", "value": attempt + 1}
        scored = machine_respond(seed, 1, task, response, trials)
        trials.append(Trial(task["id"], "machine", len(trials), "measure", scored["correct"], response={**response, "scored": scored}, task=task))
    assert machine_next(seed, trials, 1) is None
    assert sum(trial_action(t) == "probe" for t in trials) == 6  # one demonstration plus five measured probes


def test_machine_guess_actions_in_responses_end_on_solution_or_two_wrong_guesses():
    seed = seed_of("machine-guess-action")
    practice = machine_next(seed, [], 1)
    assert practice is not None
    trials = [Trial(practice["id"], "machine", 0, "practice", None, response={"action": "probe"}, task=practice)]
    first = machine_next(seed, trials, 1)
    assert first is not None
    wrong = {"action": "guess", "text": "not a rule we accept"}
    scored = machine_respond(seed, 1, first, wrong, trials)
    trials.append(Trial(first["id"], "machine", 1, "measure", scored["correct"], response={**wrong, "scored": scored}, task=first))
    second = machine_next(seed, trials, 1)
    assert second is not None, "one wrong guess should not finish the round"
    scored = machine_respond(seed, 1, second, wrong, trials)
    trials.append(Trial(second["id"], "machine", 2, "measure", scored["correct"], response={**wrong, "scored": scored}, task=second))
    assert machine_next(seed, trials, 1) is None, "two wrong guesses should finish the round"


def test_shopkeeper_progress_is_scoped_to_its_own_game():
    seed = seed_of("shopkeeper-isolation")
    unrelated = [Trial(f"quick_{i}", "quick", i, "measure", True, task={"stage": "quick_a"}) for i in range(8)]
    task = shopkeeper_next(seed, unrelated)
    assert task is not None
    assert task["game"] == "shopkeeper"
    assert task["phase"] == "practice"


def test_delayed_codeword_flow_is_prediction_then_free_recall_then_recognition():
    seed = seed_of("delayed-order")
    noise = [Trial("quick_0", "quick", 0, "measure", True, task={"stage": "quick_a"})]
    prediction = flow.next_task_for("codewords_delayed", seed=seed, trials=noise)
    assert prediction and prediction["action"] == "predict"
    predicted_trial = Trial(prediction["id"], prediction["game"], 1, "measure", None, response={"action": "predict", "value": 4}, task=prediction)
    free = flow.next_task_for("codewords_delayed", seed=seed, trials=[*noise, predicted_trial])
    assert free and free["kind"] == "free"
    free_trial = Trial(free["id"], free["game"], 2, "measure", False, response={"scored": {"hits": 2}}, task=free)
    recognition = flow.next_task_for("codewords_delayed", seed=seed, trials=[*noise, predicted_trial, free_trial])
    assert recognition and recognition["kind"] == "recognition" and recognition["id"] == "codewords_recog_0"
    recognised = Trial(recognition["id"], recognition["game"], 3, "measure", True, task=recognition)
    next_recognition = flow.next_task_for("codewords_delayed", seed=seed, trials=[*noise, predicted_trial, free_trial, recognised])
    assert next_recognition and next_recognition["id"] == "codewords_recog_1"


def test_trap_stage_contains_two_measured_questions_after_practice():
    seed = seed_of("trap-stage")
    practice = flow.next_task_for("trap", seed=seed, trials=[])
    assert practice and practice["id"] == "trap_practice" and practice["phase"] == "practice"
    practice_trial = Trial(practice["id"], "trap", 0, "practice", None, task=practice)
    first = flow.next_task_for("trap", seed=seed, trials=[practice_trial])
    assert first and first["id"] == "trap_bat_ball" and first["phase"] == "measure"
    first_trial = Trial(first["id"], "trap", 1, "measure", False, task=first)
    second = flow.next_task_for("trap", seed=seed, trials=[practice_trial, first_trial])
    assert second and second["id"] == "trap_lily" and second["phase"] == "measure"
    second_trial = Trial(second["id"], "trap", 2, "measure", True, task=second)
    assert flow.next_task_for("trap", seed=seed, trials=[practice_trial, first_trial, second_trial]) is None


def test_wagers_are_only_allowed_for_eligible_measured_questions():
    quick = {"game": "quick", "phase": "measure"}
    assert wager_allowed(quick)
    assert not wager_allowed({**quick, "phase": "practice"})
    assert not wager_allowed({"game": "machine", "phase": "measure"}, action="probe")
    assert wager_allowed({"game": "machine", "phase": "measure"}, action="guess")
    assert not wager_allowed({"game": "codewords_delayed", "phase": "measure", "action": "predict"}, action="predict")


def test_the_locked_box_hints_cost_coins_and_escalate():
    costs = [h["cost"] for h in LOCKED_BOX_HINTS]
    assert costs == sorted(costs) and len(costs) == 3
    levels = [h["level"] for h in LOCKED_BOX_HINTS]
    assert levels == ["nudge", "method", "near answer"]


def test_the_traps_have_an_intuitive_answer_that_is_wrong():
    for i in range(2):
        task = trap_task(i)
        assert task["intuitive"] != task["answer"]
        assert task["intuitive"] in task["options"]
        assert task["explain"]


def test_playing_the_whole_thing_produces_a_sealed_snapshot(client, learner, services):
    view = _start(client, learner)
    run_id = view["run_id"]
    _play_until(client, learner, run_id)
    finished = client.post(f"/api/v1/baseline/{run_id}/finish", headers=learner["headers"])
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["state"] == "sealed"
    assert len(body["factors"]) == 8
    assert body["completion"]["complete"] >= 6
    keys = {f["key"] for f in body["factors"]}
    assert keys == set(F.FACTORS)
    measured = [f for f in body["factors"] if f["state"] == "measured"]
    assert len(measured) >= 4, f"a full play-through measured only {len(measured)} factors"
    for factor in body["factors"]:
        if factor["state"] == "measured":
            assert factor["n"] >= F.MIN_N[factor["key"]]
            assert factor["range"] is not None and factor["range"][0] <= factor["range"][1]
            assert factor["statement"], "a measured factor with no plain-language statement"
        else:
            assert factor["range"] is None, "an unmeasured factor must not carry a number"
            assert "Not enough data yet" in factor["statement"]


def test_the_snapshot_levers_exist_and_each_names_its_evidence(client, learner):
    view = _start(client, learner)
    _play_until(client, learner, view["run_id"])
    body = client.post(f"/api/v1/baseline/{view['run_id']}/finish", headers=learner["headers"]).json()
    levers = body["levers"]
    for key in (
        "step_size",
        "lead_format_prior",
        "pace",
        "review_halflife_prior",
        "hint_policy",
        "confidence_prompting",
        "tone",
        "language",
        "timers_visible",
    ):
        assert key in levers, f"the snapshot does not populate {key}, which AI 3 consumes"
        assert levers[key]["why"], f"{key} does not say where it came from"
        assert levers[key]["source"] in ("measured", "stated", "default")
    assert levers["lead_format_prior"]["value"] == "diagram"
    assert levers["tone"]["value"] == "gentle"
    assert levers["language"]["value"] == "sw"
    assert levers["timers_visible"]["value"] is False


def test_a_wager_records_confidence_and_an_overconfident_answer_shows_up(client, learner, services):
    view = _start(client, learner)
    run_id = view["run_id"]
    _play_until(client, learner, run_id, timers_help=True)
    trials = services.baseline.trials_of(run_id)
    wagered = [t for t in trials if t.wager is not None]
    assert wagered, "no trial recorded a wager"
    cal = calibration(trials)
    assert cal["n"] == len(wagered)
    assert cal["state"] == "measured" and cal["range"][0] <= cal["range"][1]


def test_stopping_early_keeps_what_was_measured_and_invents_nothing(client, learner):
    """The brief's partial-completion rule, asserted: stop after the vault, keep the vault."""
    view = _start(client, learner)
    run_id = view["run_id"]
    _play_until(client, learner, run_id, stop_stage="machine", max_trials=60)
    body = client.post(f"/api/v1/baseline/{run_id}/finish", headers=learner["headers"]).json()
    assert body["state"] == "sealed"
    completion = body["completion"]
    assert "vault" in completion["played"]
    assert "machine" in completion["not_measured"]
    assert "locked_box" in completion["not_measured"]
    wm = next(f for f in body["factors"] if f["key"] == "working_memory")
    assert wm["state"] in ("measured", "not_enough_data")
    assert wm["n"] >= 0
    reasoning = next(f for f in body["factors"] if f["key"] == "reasoning")
    assert reasoning["range"] is None, "a game that was never played produced a number"


def test_a_partial_baseline_is_usable_by_the_rest_of_the_app(client, learner, services):
    """Its trials are evidence, so they are in the shared log with the run's own event ids."""
    view = _start(client, learner)
    run_id = view["run_id"]
    _play_until(client, learner, run_id, stop_stage="machine", max_trials=40)
    rows = services.state.evidence_rows(learner["student_id"])
    baseline_events = [r for r in rows if str(r["event_id"]).startswith(f"base_{run_id}_")]
    assert baseline_events, "no baseline trial reached the evidence log"
    assert all(r["source_type"] in ("controlled", "observational") for r in baseline_events)


def test_the_same_task_cannot_be_measured_twice(client, learner, services):
    """A re-sent trial (a flaky tap, a reload) is one row, and the original score comes back."""
    view = _start(client, learner)
    run_id = view["run_id"]
    _pick_format(client, learner, run_id)
    _answer_questions(client, learner, run_id)
    before = len(services.baseline.trials_of(run_id))
    task = client.get(f"/api/v1/baseline/{run_id}", headers=learner["headers"]).json()["task"]
    response = _correct_response(task, _run_seed(run_id))
    first = _submit(client, learner, run_id, task, response)
    again = _submit(client, learner, run_id, task, response)
    assert again["duplicate"] is True
    assert again["scored"] == first["scored"]
    assert len(services.baseline.trials_of(run_id)) == before + 1


def test_answering_a_task_that_is_not_on_offer_is_refused(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    _pick_format(client, learner, run_id)
    _answer_questions(client, learner, run_id)
    response = client.post(
        f"/api/v1/baseline/{run_id}/trial",
        headers=learner["headers"],
        json={"task_id": "vault_forward_99_9", "response": {"taps": [1, 2, 3]}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "baseline_refused"


def test_the_snapshot_is_available_before_finishing(client, learner):
    view = _start(client, learner)
    body = client.get("/api/v1/baseline/snapshot", headers=learner["headers"]).json()
    assert body["state"] == "in_progress"
    assert len(body["factors"]) == 8
    assert all(f["state"] == "not_enough_data" or f["state"] == "measured" for f in body["factors"])


def test_a_learner_with_no_baseline_gets_a_first_class_empty_state(client, learner):
    body = client.get("/api/v1/baseline/snapshot", headers=learner["headers"]).json()
    assert body["state"] in ("not_started", "in_progress")


def test_a_run_belonging_to_someone_else_is_not_readable(client, learner, services):
    """Another learner's run id is a 404, and the answer is the same whether it exists or not."""
    view = _start(client, learner)
    other = client.post(
        "/api/v1/students",
        json={"display_name": "Someone Else", "age_band": "16-18", "as_guest": True},
    ).json()
    headers = {"x-csrf-token": other["csrf_token"]}
    response = client.get(f"/api/v1/baseline/{view['run_id']}", headers=headers)
    assert response.status_code == 404
    missing = client.get("/api/v1/baseline/base_does_not_exist", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == response.json()["detail"]


# --------------------------------------------------------------------------- #
# 3. the factors themselves, on synthetic trials
# --------------------------------------------------------------------------- #
def _trial(**kw) -> Trial:
    kw.setdefault("game", "vault")
    kw.setdefault("phase", "measure")
    return Trial(
        task_id=kw.pop("task_id", "t"),
        game=kw.pop("game"),
        index=kw.pop("index", 0),
        phase=kw.pop("phase"),
        correct=kw.pop("correct", True),
        latency_ms=kw.pop("latency_ms", None),
        wager=kw.pop("wager", None),
        response=kw.pop("response", {}),
        task=kw.pop("task", {}),
    )


def test_not_measured_is_a_state_and_not_a_zero():
    factors = F.build_factors([], model={}, preferences={})
    for key in F.FACTORS:
        factor = factors[key]
        assert factor.state == "not_enough_data"
        assert factor.low is None and factor.high is None
        assert factor.n == 0
        assert "Not enough data yet" in factor.statement


def test_the_working_memory_statement_says_what_the_app_will_do_differently():
    trials = [
        _trial(game="vault", correct=True, task={"length": 4, "direction": "forward"}),
        _trial(game="vault", correct=True, task={"length": 5, "direction": "forward"}),
        _trial(game="vault", correct=False, task={"length": 6, "direction": "forward"}),
    ]
    factor = F.build_factors(trials)["working_memory"]
    assert factor.state == "measured"
    assert factor.n == 3
    assert "smaller steps" in factor.statement
    assert factor.actions, "a measured factor with no consequence for the next lesson"


def test_shopkeeper_memory_uses_the_nested_score_record():
    trials = [
        _trial(game="vault", correct=True, task={"length": 4, "direction": "forward"}),
        _trial(game="vault", correct=True, task={"length": 3, "direction": "backward"}),
        _trial(game="vault", correct=False, task={"length": 4, "direction": "backward"}),
        _trial(
            game="shopkeeper",
            correct=False,
            task={"items": ["a blue pen", "two batteries", "a loaf of bread"]},
            response={"scored": {"hits": 2, "distractor_accuracy": 2 / 3}},
        ),
    ]
    factor = F.build_factors(trials)["working_memory"]
    assert factor.state == "measured"
    assert factor.detail["recall_under_distraction"] == round(2 / 3, 3)
    assert factor.detail["distractor_accuracy"] == round(2 / 3, 3)


def test_slow_but_accurate_is_not_reported_as_a_problem():
    """Pace is reported with its accuracy beside it; the app must not read slowness as trouble."""
    trials = [
        _trial(game="quick", correct=True, latency_ms=9000, task={"stage": "quick_a"})
        for _ in range(8)
    ]
    factor = F.build_factors(trials)["processing_pace"]
    assert factor.state == "measured"
    assert factor.detail["accuracy"] == 1.0
    assert factor.low <= factor.high
    assert "reaction-time differences are not used" in factor.detail["note"]


def test_the_trap_pause_is_recorded_even_when_the_intuitive_answer_wins():
    trials = [
        _trial(game="trap", correct=False, latency_ms=22_000, response={"value": "24"}, task={"intuitive": "24"}),
        _trial(game="trap", correct=True, latency_ms=31_000, response={"value": "47"}, task={"intuitive": "24"}),
        _trial(game="quick", correct=True, latency_ms=1500),
        _trial(game="quick", correct=True, latency_ms=1600),
        _trial(game="quick", correct=False, latency_ms=1800),
    ]
    factor = F.build_factors(trials)["attention_self_control"]
    assert factor.state == "measured"
    assert factor.detail["traps_intuitive"] == 1
    assert factor.detail["traps_correct"] == 1
    assert factor.detail["median_pause_ms"] == 26_500


def test_the_machine_factor_reports_the_falsifying_rate():
    trials = [
        _trial(game="machine", correct=None, task={"action": "probe"}, response={"scored": {"falsifying": True}}),
        _trial(game="machine", correct=None, task={"action": "probe"}, response={"scored": {"falsifying": False}}),
        _trial(game="machine", correct=True, task={"action": "guess"}),
    ]
    factor = F.build_factors(trials)["reasoning"]
    assert factor.state == "measured"
    assert factor.detail["falsifying_rate"] == 0.5
    assert factor.detail["first_guess_correct"] is True
    assert "proved you wrong" in factor.statement


def test_calibration_needs_eight_wagers_before_it_reports_an_index():
    few = [
        _trial(game="trap", correct=True, wager=3, task={})
        for _ in range(5)
    ]
    cal = calibration(few)
    assert cal["state"] == "not_enough_data"
    assert "index" not in cal
    many = few + [_trial(game="trap", correct=False, wager=3, task={}) for _ in range(4)]
    cal = calibration(many)
    assert cal["state"] == "measured"
    assert 0 <= cal["range"][0] <= cal["range"][1] <= 1


def test_prediction_is_compared_with_free_recall_hits_not_the_number_of_tasks():
    trials = [
        _trial(game="quick", correct=True, wager=2, task={"stage": "quick_a"})
        for _ in range(8)
    ]
    trials.extend([
        _trial(game="codewords_delayed", correct=None, task={"action": "predict"}, response={"value": 6}),
        _trial(game="codewords_delayed", correct=False, task={"kind": "free"}, response={"scored": {"hits": 4}}),
    ])
    factor = F.build_factors(trials)["metacognition"]
    comparison = factor.detail["predicted_vs_actual"]
    assert comparison == {"predicted": 6.0, "actual": 4.0, "of": 8, "n": 8, "gap": 2.0}


def test_overconfidence_is_named_as_overconfidence():
    trials = [_trial(game="trap", correct=False, wager=3, task={}) for _ in range(8)]
    factor = F.build_factors(trials)["metacognition"]
    assert factor.state == "measured"
    assert factor.detail["index"] > 0
    assert "more sure than you were" in factor.statement


def test_long_term_memory_reports_the_recall_vs_recognition_gap():
    trials = [
        _trial(game="codewords_cued", correct=True, task={}),
        _trial(game="codewords_cued", correct=True, task={}),
        _trial(game="codewords_delayed", correct=None, task={"kind": "free"}, response={"scored": {"hits": 2}}, phase="measure"),
        _trial(game="codewords_delayed", correct=True, task={"kind": "recognition"}),
        _trial(game="codewords_delayed", correct=True, task={"kind": "recognition"}),
    ]
    factor = F.build_factors(trials)["long_term_memory"]
    assert factor.state == "measured"
    assert factor.detail["delayed_free"] == 0.25
    assert factor.detail["recall_vs_recognition_gap"] is not None
    assert factor.detail["recall_vs_recognition_gap"] > 0


def test_hint_behaviour_produces_a_hint_policy_with_a_reason():
    trials = [
        _trial(game="locked_box", index=0, correct=None, task={"action": "start"}, latency_ms=0, response={"action": "start"}),
        _trial(game="locked_box", index=1, correct=False, task={}, latency_ms=10_000, response={"action": "attempt", "scored": {"action": "attempt", "correct": False}}),
        _trial(game="locked_box", index=2, correct=None, task={}, latency_ms=20_000, response={"action": "hint", "hint_id": "hint_nudge", "scored": {"action": "hint", "cost": 1}}),
        _trial(game="locked_box", index=3, correct=None, task={}, latency_ms=90_000, response={"action": "walk_away", "scored": {"action": "walk_away"}}),
    ]
    factors = F.build_factors(trials)
    factor = factors["persistence_help_seeking"]
    assert factor.state == "measured"
    assert factor.detail["hints_used"] == 1
    assert factor.detail["hint_cost"] == 1
    assert factor.detail["time_to_first_hint_ms"] == 20_000
    assert factor.detail["walked_away"] is True
    assert factor.detail["minutes_on_puzzle"] == 1.5
    levers = F.levers(factors, preferences={})
    assert levers["hint_policy"]["value"] == "offer early"
    assert "asked for help after" in levers["hint_policy"]["why"]


def test_preferences_decide_tone_and_never_decide_what_works():
    """The brief's rule: preference decides tone/timing/language; evidence decides method."""
    factors = F.build_factors([])
    levers = F.levers(
        factors,
        preferences={"preferred_format": "diagram", "feedback_style": "straight", "timers_help": False, "language": "en"},
    )
    assert levers["tone"]["source"] == "stated"
    assert levers["lead_format_prior"]["source"] == "stated"
    assert levers["lead_format_prior"]["why"].endswith("(stated, not measured)")
    assert levers["step_size"]["source"] == "default", "a preference must not set the step size"


def test_the_preference_vs_performance_row_needs_real_outcomes():
    factors = F.build_factors([])
    assert F.preference_vs_performance(factors, {"preferred_format": "diagram"}) is None
    row = F.preference_vs_performance(
        factors,
        {"preferred_format": "diagram"},
        outcomes={"by_format": {"diagram": {"n": 6, "correct": 5}, "worked_example": {"n": 4, "correct": 2}}},
    )
    assert row is not None
    assert "you did best" in row["sentence"].lower()
    assert "not a test of learning styles" in row["note"]


def test_no_factor_carries_a_score_out_of_a_hundred_or_a_rank():
    trials = [
        _trial(game="vault", correct=True, task={"length": 5, "direction": "forward"}),
        _trial(game="quick", correct=True, latency_ms=1200, task={"stage": "quick_a"}),
    ]
    payload = json.dumps(F.build_factors(trials, model={"topics": []}).__getitem__("working_memory").as_dict())
    assert "score" not in payload
    assert "percentile" not in payload and "rank" not in payload


# --------------------------------------------------------------------------- #
# 4. the next-day check and the resumption picture
# --------------------------------------------------------------------------- #
def test_the_next_day_check_asks_about_the_pairs_this_run_taught(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    _play_until(client, learner, run_id, stop_stage="vault", max_trials=30)
    body = client.get("/api/v1/baseline/next-day", headers=learner["headers"]).json()
    assert body["tasks"], "no next-day check was offered"
    seed = _run_seed(run_id)
    learned_codes = {pair["code"] for pair in codewords_pairs(seed)}
    asked = {task["cue"] for task in body["tasks"]}
    assert body["studied"] is True
    assert len(body["tasks"]) == 3
    assert asked and asked <= learned_codes, "the return check asked about code words this run did not teach"
    assert all(task["game"] == "codewords_next_day" for task in body["tasks"])
    assert all("answer" not in task for task in body["tasks"]), "the return check leaked its answer key"


def test_the_stage_report_names_what_was_not_played(client, learner):
    view = _start(client, learner)
    run_id = view["run_id"]
    _pick_format(client, learner, run_id)
    _answer_questions(client, learner, run_id)
    report = client.get(f"/api/v1/baseline/{run_id}", headers=learner["headers"]).json()["completion"]
    assert report["not_measured"], "an untouched baseline should name every stage as not measured"
    assert "vault" in report["not_measured"]
    states = {row["stage"]: row["state"] for row in report["stages"]}
    assert states["format"] == "complete"


__all__: list[str] = []
