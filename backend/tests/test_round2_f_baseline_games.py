"""Round 2, phase F: the baseline games, played through the real API.

Round 1 only ever drove the first four baseline steps in a browser, and the games that never ran
(the locked box, the machine, the trap pair, the next-day check) had no test that played them over
HTTP. These tests play every stage the way the page does: ask the server what is on offer, answer
it, repeat. The *answers* come from a server-side oracle (``Baseline.internal_task``), never from
the JSON the page receives, because a test that reads the answer from the served task would pass
on the very leak it is supposed to catch.

Each assertion here was written against a defect in the round-1 code:

* the locked box re-used one task id, so after one hint the server treated every later action as
  a duplicate and the stage never ended;
* the vault's answer direction flipped after the first measured trial of a block;
* a practice probe on the machine counted towards the five-probe limit and the round-3 switch;
* only one of the two trap questions was ever served, while the screen promised two;
* delayed recognition counted the prediction and the free recall as recognition items;
* factors read ``falsifying``, ``noticed``, ``cost`` and ``hits`` from the top of ``response``, but
  the service stores the server's verdict under ``response["scored"]``, so those metrics were always
  zero or False in a real run (the unit tests had been building the wrong shape);
* the served tasks carried their answer keys (quick, trap, cued recall, locked box, distractors).
"""

from __future__ import annotations

from typing import Any

import pytest

from adapt.baseline.games import codewords_pairs, is_falsifying, machine_rule

# --------------------------------------------------------------------------- #
# helpers: play the way the page plays
# --------------------------------------------------------------------------- #


def _headers(learner: dict) -> dict[str, str]:
    return learner["headers"]


def _start(client, learner) -> dict[str, Any]:
    r = client.post("/api/v1/baseline", headers=_headers(learner), json={})
    assert r.status_code == 200, r.text
    return r.json()


def _view(client, learner, run_id: str) -> dict[str, Any]:
    r = client.get(f"/api/v1/baseline/{run_id}", headers=_headers(learner))
    assert r.status_code == 200, r.text
    return r.json()


def _act(client, learner, run_id: str, task: dict, response: dict, *, latency: int = 1500, wager=None):
    body: dict[str, Any] = {"task_id": task["id"], "response": response, "latency_ms": latency}
    if wager is not None:
        body["wager"] = wager
    r = client.post(f"/api/v1/baseline/{run_id}/trial", headers=_headers(learner), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _to_task_stage(client, learner, run_id: str) -> dict[str, Any]:
    """Format pick and questions, the usual way; returns the view that follows."""
    client.post(f"/api/v1/baseline/{run_id}/format", headers=_headers(learner), json={"value": "diagram"})
    client.post(
        f"/api/v1/baseline/{run_id}/questions",
        headers=_headers(learner),
        json={"answers": {"goal": "just curious", "timers_help": "timers help", "language": "English"}},
    )
    return _view(client, learner, run_id)


def _oracle(run_id: str) -> dict | None:
    """The task on offer *with* its answer key. Server-side only; the routes never return this."""
    from adapt.services import current

    return current().baseline.internal_task(run_id)


def _answer_for(task: dict, studied: list[dict]) -> dict:
    """The right response for an oracle task (the key comes from the server, not the page)."""
    game = task["game"]
    if game == "codewords_study" or task.get("phase") == "show":
        return {}
    if game == "vault":
        seq = list(task["sequence"])
        if task.get("answer_direction") == "backward":
            seq = list(reversed(seq))
        return {"taps": seq}
    if game == "shopkeeper":
        return {
            "items": list(task["items"]),
            "distractors": {d["id"]: d["answer"] for d in task["distractors"]},
        }
    if game in ("codewords_cued", "codewords_next_day"):
        return {"text": task["answer"]}
    if game == "codewords_delayed":
        if task.get("action") == "predict":
            return {"value": 8}
        if task.get("kind") == "free":
            return {"text": ", ".join(p["code"] for p in studied)}
        return {"text": task["answer"]}
    if game in ("quick", "trap"):
        return {"value": task["answer"]}
    if game == "machine":
        return {"action": "guess", "text": "double the number"}
    if game == "locked_box":
        return {"action": "attempt", "value": task["answer"]}
    raise AssertionError(f"no answer rule for {game}")


def _studied(run_id: str) -> list[dict]:
    """The pairs this run taught: the server re-derives them from the run's seed."""
    from adapt.db.models import BaselineRun
    from adapt.services import current

    with current().db.session() as s:
        seed = int(s.get(BaselineRun, run_id).seed or 0)
    return codewords_pairs(seed)


def _advance_until_stage(client, learner, run_id: str, stage: str, *, max_steps: int = 400) -> dict:
    """Answer every task correctly until the server says the next task is in ``stage``."""
    view = _view(client, learner, run_id)
    for _ in range(max_steps):
        if view["kind"] != "task":
            raise AssertionError(f"ran out of tasks before {stage}: kind={view['kind']} stage={view['stage']}")
        if view["stage"] == stage:
            return view
        view = _act(client, learner, run_id, view["task"], _answer_for(_oracle(run_id), _studied(run_id)))
    raise AssertionError(f"never reached {stage}")


def _snapshot_factor(client, learner, run_id: str, key: str) -> dict:
    r = client.get(f"/api/v1/baseline/snapshot?run_id={run_id}", headers=_headers(learner))
    assert r.status_code == 200, r.text
    factors = {f["key"]: f for f in r.json()["factors"]}
    return factors[key]


def _recorded(run_id: str) -> list[dict]:
    from adapt.services import current

    return [
        {
            "game": t.game,
            "action": (t.task or {}).get("action") or (t.response or {}).get("action"),
            "correct": t.correct,
            "wager": t.wager,
            "phase": t.phase,
            "response": dict(t.response or {}),
        }
        for t in current().baseline.trials_of(run_id)
    ]


# --------------------------------------------------------------------------- #
# the answer key never reaches the page before an answer
# --------------------------------------------------------------------------- #
_ANSWER_KEYS = ("answer", "intuitive", "explain", "expected")


def _walk_served_tasks(client, learner, run_id: str, max_steps: int = 400):
    """Yield every task the page is shown, answering correctly, as the page would see it."""
    view = _view(client, learner, run_id)
    for _ in range(max_steps):
        if view["kind"] != "task":
            return
        yield view["task"]
        view = _act(client, learner, run_id, view["task"], _answer_for(_oracle(run_id), _studied(run_id)))


def test_no_served_task_carries_its_answer_key(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    for task in _walk_served_tasks(client, learner, run_id):
        leaked = [k for k in _ANSWER_KEYS if k in task]
        assert not leaked, f"{task['id']} ({task['game']}) sent its answer key to the page: {leaked}"
        for d in task.get("distractors") or []:
            assert "answer" not in d, "a shopkeeper distractor sent its true/false answer to the page"


def test_the_oracle_still_knows_every_answer(client, learner):
    """The leak test above would pass trivially if the answers were gone. They must still exist."""
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _view(client, learner, run_id)
    secret = _oracle(run_id)
    assert secret is not None
    assert view["task"]["id"] == secret["id"]


# --------------------------------------------------------------------------- #
# the locked box: one puzzle, three ways out, and it must end
# --------------------------------------------------------------------------- #
def test_a_hint_does_not_freeze_the_locked_box(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "locked_box")
    first_id = view["task"]["id"]

    view = _act(client, learner, run_id, view["task"], {"action": "hint", "hint_id": "hint_nudge"})
    assert view["kind"] == "task" and view["stage"] == "locked_box", "a hint must not end the box"
    assert view["task"]["id"] != first_id, "the locked box re-used its task id after a hint"
    assert not view.get("duplicate"), "the next action was treated as a duplicate of the hint"

    answer = _oracle(run_id)["answer"]
    view = _act(client, learner, run_id, view["task"], {"action": "attempt", "value": answer + 1})
    assert view["scored"]["correct"] is False
    assert not view.get("duplicate")
    assert view["stage"] == "locked_box", "one wrong attempt must not end the box"

    view = _act(client, learner, run_id, view["task"], {"action": "walk_away"})
    assert view["stage"] != "locked_box", "walking away did not end the stage"
    box = [t for t in _recorded(run_id) if t["game"] == "locked_box"]
    assert [t["action"] for t in box] == ["hint", "attempt", "walk_away"]


def test_a_correct_second_attempt_after_a_wrong_one_ends_the_box_and_is_measured(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "locked_box")
    answer = _oracle(run_id)["answer"]
    view = _act(client, learner, run_id, view["task"], {"action": "attempt", "value": answer + 7})
    assert view["stage"] == "locked_box"
    view = _act(client, learner, run_id, view["task"], {"action": "attempt", "value": answer})
    assert view["scored"]["correct"] is True
    assert view["stage"] != "locked_box"
    persistence = _snapshot_factor(client, learner, run_id, "persistence_help_seeking")
    assert persistence["detail"]["wrong_attempts"] == 1
    assert persistence["detail"]["retried_after_wrong"] is True


def test_the_hint_cost_is_read_from_the_recorded_score(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "locked_box")
    view = _act(client, learner, run_id, view["task"], {"action": "hint", "hint_id": "hint_method"})
    view = _act(client, learner, run_id, view["task"], {"action": "hint", "hint_id": "hint_nudge"})
    _act(client, learner, run_id, view["task"], {"action": "walk_away"})
    persistence = _snapshot_factor(client, learner, run_id, "persistence_help_seeking")
    assert persistence["detail"]["hints_used"] == 2
    assert persistence["detail"]["hint_cost"] == 3, "hint cost must be the coins spent"


# --------------------------------------------------------------------------- #
# the vault: the direction of a block is fixed for the whole block
# --------------------------------------------------------------------------- #
def test_every_vault_task_in_a_block_asks_for_that_blocks_direction(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "vault")
    seen: dict[str, set[str]] = {"forward": set(), "backward": set()}
    for _ in range(80):
        if view["kind"] != "task" or view["stage"] != "vault":
            break
        task = view["task"]
        seen[task["direction"]].add(task["answer_direction"])
        view = _act(client, learner, run_id, task, _answer_for(_oracle(run_id), []))
    assert seen["forward"] == {"forward"}, f"forward block asked for {seen['forward']}"
    assert seen["backward"] == {"backward"}, f"backward block asked for {seen['backward']}"


# --------------------------------------------------------------------------- #
# the machine: practice does not count; the rule and the falsifying rate are read from the record
# --------------------------------------------------------------------------- #
def test_a_practice_probe_does_not_use_up_a_machine_probe(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "machine")
    assert view["task"]["phase"] == "practice"
    view = _act(client, learner, run_id, view["task"], {"action": "probe", "value": 4})
    assert view["task"]["phase"] == "measure"
    assert view["task"].get("probes_used", 0) == 0, "the practice probe was counted as a real probe"


def test_the_falsifying_rate_is_computed_from_the_probes_actually_sent(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "machine")
    view = _act(client, learner, run_id, view["task"], {"action": "probe", "value": 4})  # practice
    sent: list[float] = []
    for value in [3, 0, 5, -2, 7]:
        task = view["task"]
        if task.get("round") != 1 or task.get("phase") != "measure":
            break
        view = _act(client, learner, run_id, task, {"action": "probe", "value": value})
        sent.append(value)
    assert len(sent) == 5
    expected = sum(1 for x in sent if is_falsifying(x, 0)) / len(sent)
    rate = _snapshot_factor(client, learner, run_id, "reasoning")["detail"]["falsifying_rate"]
    assert rate == pytest.approx(expected, abs=1e-3), f"falsifying rate {rate}, expected {expected}"


def test_round_three_switch_is_noticed_when_a_probe_matches_the_new_rule(client, learner):
    run_id = _start(client, learner)["run_id"]
    view = _to_task_stage(client, learner, run_id)
    # Play rounds 1 and 2 to their ends (wrong guesses end a round), landing in round 3's measure.
    for _ in range(300):
        if view["kind"] != "task":
            raise AssertionError("ran out of tasks before round 3")
        task = view["task"]
        if task.get("round") == 3 and task.get("phase") == "measure":
            break
        if task["game"] != "machine":
            view = _advance_until_stage(client, learner, run_id, "machine")
            continue
        if task.get("phase") == "practice":
            view = _act(client, learner, run_id, task, {"action": "probe", "value": 1})
        elif task.get("round") == 3:
            view = _act(client, learner, run_id, task, {"action": "probe", "value": 2})
        else:
            view = _act(client, learner, run_id, task, {"action": "guess", "text": "no idea"})
    assert view["task"]["round"] == 3

    _, rule_two, switch_at = machine_rule(_seed(run_id), 3)
    assert rule_two is not None and switch_at == 3
    # Three real probes, then the switch. Probe the fourth with a value whose output the new rule
    # gives; the machine answers with the new rule, so the learner has seen the change.
    for x in (10, 11, 12):
        view = _act(client, learner, run_id, view["task"], {"action": "probe", "value": x})
    fourth = view["task"]
    view = _act(client, learner, run_id, fourth, {"action": "probe", "value": 13})
    assert view["scored"]["output"] == rule_two.apply(13), "the machine did not switch when expected"
    reasoning = _snapshot_factor(client, learner, run_id, "reasoning")
    assert reasoning["detail"]["noticed_the_switch"] is True


def _seed(run_id: str) -> int:
    from adapt.db.models import BaselineRun
    from adapt.services import current

    with current().db.session() as s:
        return int(s.get(BaselineRun, run_id).seed or 0)


# --------------------------------------------------------------------------- #
# the traps: the screen promises two questions and must serve two
# --------------------------------------------------------------------------- #
def test_both_trap_questions_are_served_in_order(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "trap")
    served = []
    for _ in range(4):
        if view["stage"] != "trap" or view["kind"] != "task":
            break
        served.append(view["task"]["id"])
        view = _act(client, learner, run_id, view["task"], _answer_for(_oracle(run_id), []))
    assert served == ["trap_bat_ball", "trap_lily"], f"served {served}"


# --------------------------------------------------------------------------- #
# delayed codewords: prediction, free recall, then all eight recognitions
# --------------------------------------------------------------------------- #
def test_delayed_recognition_covers_every_pair_after_the_prediction_and_free_recall(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "codewords_delayed")
    studied = _studied(run_id)
    recognised: list[str] = []
    for _ in range(40):
        if view["stage"] != "codewords_delayed" or view["kind"] != "task":
            break
        task = view["task"]
        if task.get("kind") == "recognition":
            recognised.append(task["cue"])
        view = _act(client, learner, run_id, task, _answer_for(_oracle(run_id), studied))
    assert sorted(recognised) == sorted(p["code"] for p in studied), recognised


def test_a_full_free_recall_is_scored_as_full_not_zero(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "codewords_delayed")
    studied = _studied(run_id)
    for _ in range(10):
        if view["task"].get("kind") == "free":
            break
        view = _act(client, learner, run_id, view["task"], _answer_for(_oracle(run_id), studied))
    assert view["task"]["kind"] == "free"
    view = _act(client, learner, run_id, view["task"], {"text": ", ".join(p["code"] for p in studied)})
    ltm = _snapshot_factor(client, learner, run_id, "long_term_memory")
    assert ltm["detail"]["delayed_free"] == 1.0, "a full free recall was reported as 0%"


# --------------------------------------------------------------------------- #
# the next-day codeword check: reachable, answerable, and recorded
# --------------------------------------------------------------------------- #
def test_the_next_day_check_can_be_answered_and_is_recorded(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    _advance_until_stage(client, learner, run_id, "codewords_delayed")
    view = _view(client, learner, run_id)
    studied = _studied(run_id)
    for _ in range(40):
        if view["kind"] != "task" or view["stage"] != "codewords_delayed":
            break
        view = _act(client, learner, run_id, view["task"], _answer_for(_oracle(run_id), studied))
    r = client.post(f"/api/v1/baseline/{run_id}/finish", headers=_headers(learner), json={})
    assert r.status_code == 200, r.text

    tasks = client.get(f"/api/v1/baseline/next-day?run_id={run_id}", headers=_headers(learner)).json()["tasks"]
    assert len(tasks) == 3
    first = tasks[0]
    r = client.post(
        f"/api/v1/baseline/{run_id}/trial",
        headers=_headers(learner),
        json={"task_id": first["id"], "response": {"text": _oracle_next_day(run_id)}, "latency_ms": 2000, "wager": 2},
    )
    assert r.status_code == 200, r.text
    assert r.json()["scored"]["correct"] is True
    rows = [t for t in _recorded(run_id) if t["game"] == "codewords_next_day"]
    assert len(rows) == 1 and rows[0]["correct"] is True
    assert rows[0]["wager"] == 2, "a coin wager on a next-day answer was silently dropped"
    ltm = _snapshot_factor(client, learner, run_id, "long_term_memory")
    assert ltm["detail"]["next_day"] == 1.0


def _oracle_next_day(run_id: str) -> str:
    from adapt.services import current

    return current().baseline.internal_next_day(run_id)[0]["answer"]


def test_the_next_day_check_is_offered_for_the_last_finished_sitting_on_return(client, learner):
    """A returning learner's new open baseline must not hide yesterday's codeword check."""
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    _advance_until_stage(client, learner, run_id, "codewords_delayed")
    client.post(f"/api/v1/baseline/{run_id}/finish", headers=_headers(learner), json={})
    again = _start(client, learner)
    assert again["run_id"] != run_id, "a finished baseline should not be reopened"
    body = client.get("/api/v1/baseline/next-day?finished=true", headers=_headers(learner)).json()
    assert body["run_id"] == run_id, "the check was looked up on the new run, which has no codeword trials"
    assert len(body["tasks"]) == 3
    assert body["same_day"] is True, "a check taken on the day of the sitting must say it is a short gap"


def test_a_next_day_answer_on_another_learners_run_is_refused(client, learner):
    run_id = _start(client, learner)["run_id"]
    other = client.post(
        "/api/v1/students",
        json={"display_name": "Someone Else", "age_band": "16-18", "as_guest": True},
    ).json()
    headers = {"x-csrf-token": other["csrf_token"]}
    r = client.post(
        f"/api/v1/baseline/{run_id}/trial",
        headers=headers,
        json={"task_id": "codewords_next_day_0", "response": {"text": "x"}},
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# what the page is told after an answer, and what it is shown again after a reload
# --------------------------------------------------------------------------- #
def test_a_measured_wrong_answer_does_not_reveal_the_expected_answer(client, learner):
    """The reply to a measured answer must not hand back the right answer (the pairs come back later)."""
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "codewords_cued")
    while view["task"]["phase"] == "practice":  # the cued game opens with a practice trial
        view = _act(client, learner, run_id, view["task"], _answer_for(_oracle(run_id), []))
    task = view["task"]
    assert task["phase"] == "measure"
    out = _act(client, learner, run_id, task, {"text": "definitely not a place"})
    assert out["scored"]["correct"] is False
    assert "expected" not in out["scored"], "a measured wrong answer revealed the answer"


def test_a_machine_guess_does_not_reveal_the_rule_after_a_wrong_guess(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "machine")
    view = _act(client, learner, run_id, view["task"], {"action": "probe", "value": 4})  # practice
    out = _act(client, learner, run_id, view["task"], {"action": "guess", "text": "no idea"})
    assert out["scored"]["correct"] is False
    assert "expected" not in out["scored"], "the rule name came back after a wrong guess"


def test_the_machine_task_carries_the_probes_and_outputs_from_the_record(client, learner):
    """A reload must show what the machine has said so far, from the server, not from the page."""
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "machine")
    view = _act(client, learner, run_id, view["task"], {"action": "probe", "value": 4})  # practice
    sent = []
    for value in (3, 0):
        view = _act(client, learner, run_id, view["task"], {"action": "probe", "value": value})
        sent.append((value, view["scored"]["output"]))
    history = view["task"]["history"]
    assert [(h["x"], h["out"]) for h in history] == sent
    assert view["task"]["probes_used"] == 2
    assert [h["falsifying"] for h in history] == [False, True]


def test_the_locked_box_shows_its_attempts_and_bought_hints_after_a_reload(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "locked_box")
    assert view["task"]["attempts_used"] == 0 and view["task"]["bought_hints"] == []
    view = _act(client, learner, run_id, view["task"], {"action": "hint", "hint_id": "hint_nudge"})
    view = _act(client, learner, run_id, view["task"], {"action": "attempt", "value": 0})
    again = _view(client, learner, run_id)
    assert again["task"]["bought_hints"] == ["hint_nudge"]
    assert again["task"]["attempts_used"] == 1


def test_a_hint_cannot_be_bought_twice(client, learner):
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "locked_box")
    view = _act(client, learner, run_id, view["task"], {"action": "hint", "hint_id": "hint_nudge"})
    r = client.post(
        f"/api/v1/baseline/{run_id}/trial",
        headers=_headers(learner),
        json={"task_id": view["task"]["id"], "response": {"action": "hint", "hint_id": "hint_nudge"}},
    )
    assert r.status_code == 422, r.text
    assert "already bought" in r.text


# --------------------------------------------------------------------------- #
# the bands the snapshot shows
# --------------------------------------------------------------------------- #
def test_a_recall_band_never_reports_below_zero(client, learner):
    """Nothing recalled straight away is 0%; the band below it must not read as minus ten points."""
    from adapt.baseline import factors as F

    trials = [_trial_for_ltm("codewords_cued", False) for _ in range(6)]
    factor = F.build_factors(trials)["long_term_memory"]
    assert factor.state == "measured"
    assert factor.low == 0.0, f"the band starts at {factor.low}"
    assert factor.high == pytest.approx(0.1)


def test_the_working_memory_band_is_one_step_either_side_of_the_best_span(client, learner):
    """Best span 5 (the 6 was missed): the band is 4 to 6, one step either side, as the comment says."""
    from adapt.baseline import factors as F

    trials = [_trial_for_span(5, True), _trial_for_span(5, True), _trial_for_span(6, False)]
    factor = F.build_factors(trials)["working_memory"]
    assert factor.state == "measured"
    assert (factor.low, factor.high) == (4.0, 6.0)
    assert factor.detail["best_span"] == 5


def _trial_for_ltm(game, correct):
    from adapt.baseline.games import Trial

    return Trial(task_id="t", game=game, index=0, phase="measure", correct=correct, latency_ms=None, wager=None, response={}, task={})


def _trial_for_span(length, correct):
    from adapt.baseline.games import Trial

    return Trial(
        task_id="v", game="vault", index=0, phase="measure", correct=correct, latency_ms=None, wager=None,
        response={}, task={"length": length, "direction": "forward"},
    )


def test_a_hint_the_learner_cannot_afford_is_refused(client, learner):
    """Three coins, and the shop costs 1, 2 and 3: the first two hints leave nothing for the third."""
    from adapt.baseline.games import LOCKED_BOX_HINTS

    costs = {h["id"]: h["cost"] for h in LOCKED_BOX_HINTS}
    assert costs == {"hint_nudge": 1, "hint_method": 2, "hint_near": 3}, costs
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    view = _advance_until_stage(client, learner, run_id, "locked_box")
    assert view["task"]["coins_left"] == 3
    view = _act(client, learner, run_id, view["task"], {"action": "hint", "hint_id": "hint_nudge"})
    assert view["task"]["coins_left"] == 2
    view = _act(client, learner, run_id, view["task"], {"action": "hint", "hint_id": "hint_method"})
    assert view["task"]["coins_left"] == 0
    r = client.post(
        f"/api/v1/baseline/{run_id}/trial",
        headers=_headers(learner),
        json={"task_id": view["task"]["id"], "response": {"action": "hint", "hint_id": "hint_near"}},
    )
    assert r.status_code == 422, r.text
    assert "not enough coins" in r.text


def test_the_return_check_only_looks_at_finished_sittings(client, learner):
    """Mid-sitting there is no "last time" check: the return lookup must not offer the open run's check."""
    run_id = _start(client, learner)["run_id"]
    _to_task_stage(client, learner, run_id)
    _advance_until_stage(client, learner, run_id, "codewords_delayed")
    assert any(t["game"] == "codewords_cued" for t in _recorded(run_id)), "the test needs cued trials first"
    body = client.get("/api/v1/baseline/next-day?finished=true", headers=_headers(learner)).json()
    assert body["tasks"] == [], "the open baseline offered its own check as if it were from last time"
    assert body["note"] == "no finished baseline to check yet"


# --------------------------------------------------------------------------- #
# what the page is told after an answer, and what it is shown again after a reload
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# the bands the snapshot shows
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# the steps, the attention note, and the width of the bands
# --------------------------------------------------------------------------- #
def test_the_snapshot_is_not_one_of_the_thirteen_steps(client, learner):
    """"Not measured" is said about a step. The snapshot is what the steps lead to."""
    run_id = _start(client, learner)["run_id"]
    report = client.get(f"/api/v1/baseline/{run_id}", headers=_headers(learner)).json()["completion"]
    assert report["total"] == 13, report["total"]
    assert "snapshot" not in report["not_measured"], report["not_measured"]
    assert all(row["stage"] != "snapshot" for row in report["stages"])


def _quick_trial(correct: bool):
    from adapt.baseline.games import Trial

    return Trial(
        task_id="q", game="quick", index=0, phase="measure", correct=correct, latency_ms=1500,
        wager=None, response={}, task={"stage": "quick_a"},
    )


def test_the_attention_note_does_not_count_traps_nobody_reached():
    from adapt.baseline import factors as F

    trials = [_quick_trial(True), _quick_trial(True), _quick_trial(False), _quick_trial(True), _quick_trial(False)]
    factor = F.build_factors(trials)["attention_self_control"]
    assert factor.state == "measured", factor.summary
    assert "traps not reached" in factor.summary, factor.summary
    assert "0/0" not in factor.summary, factor.summary


def test_an_accuracy_band_always_has_width():
    """Two thirds that read the same must not be shown as one number: that is a point, not a range."""
    from adapt.baseline import factors as F

    # Alternating right and wrong: early and late thirds read the same, which is the zero-width case.
    trials = [_quick_trial(i % 2 == 0) for i in range(9)]
    factor = F.build_factors(trials)["attention_self_control"]
    assert factor.state == "measured"
    assert factor.low is not None and factor.high is not None
    assert factor.high - factor.low >= 0.2 - 1e-9, (factor.low, factor.high)
    assert 0.0 <= factor.low <= factor.high <= 1.0
