"""The simulations have to be *correct*, not merely animated.

Every check here is against an independent answer -- Python's own ``in``/``sorted``, the gate's
own lambda, a second hand-written insertion sort, a second interpreter for the CPU -- rather than
against a snapshot of what the simulation happened to produce. A snapshot test would happily
preserve a wrong lesson.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from adapt.learning import sims
from adapt.learning.sims import binary_search, cpu_cycle, logic_gates, sorting


def _plain_json(value: Any) -> None:
    """A frame is stored as JSON and replayed by a browser: no datetimes, no numpy, no NaN."""
    json.dumps(value, allow_nan=False)


# -------------------------------------------------------------------------------------
# binary search -- the acceptance point the brief names out loud
# -------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(40))
def test_binary_search_finds_every_present_target(seed: int) -> None:
    values = binary_search._default_values(seed, 12)
    target = values[seed % len(values)]
    frames = binary_search.run({"values": values, "target": target})
    final = frames[-1]
    assert final["kind"] == "summary"
    assert [f for f in frames if f["kind"] == "found"], [f["narration"] for f in frames]
    assert final["state"]["found"] == values.index(target)
    assert binary_search.truth({"values": values, "target": target})["index"] == values.index(target)


@pytest.mark.parametrize("seed", range(40))
def test_binary_search_says_absent_when_absent(seed: int) -> None:
    values = binary_search._default_values(seed, 12)
    absent = min((t for t in range(values[-1] + 5) if t not in set(values)), key=lambda t: abs(t - values[len(values) // 2]))
    frames = binary_search.run({"values": values, "target": absent})
    kinds = [f["kind"] for f in frames]
    assert "exhausted" in kinds
    assert "is not in the list" in frames[-2]["narration"]
    assert frames[-1]["state"]["found"] is None
    assert absent not in values  # the fixture itself, checked


@pytest.mark.parametrize("seed", range(40))
def test_binary_search_spends_at_most_log2_plus_one_comparisons(seed: int) -> None:
    values = binary_search._default_values(seed, 16)
    target = values[(seed * 5) % len(values)] - (1 if seed % 3 else 0)
    frames = binary_search.run({"values": values, "target": target})
    probes = sum(1 for f in frames if f["kind"] == "probe")
    limit = math.floor(math.log2(len(values))) + 1
    assert 1 <= probes <= limit, f"{probes} probes on {len(values)} values (limit {limit})"
    assert frames[-1]["state"]["steps"] == probes


def test_binary_search_only_ever_discards_values_it_has_proved_wrong() -> None:
    """The invariant that makes the whole algorithm: the target is never in the grey part."""
    for seed in range(40):
        values = binary_search._default_values(seed, 14)
        hit = seed % len(values)
        target = values[hit]
        frames = binary_search.run({"values": values, "target": target})
        for frame in frames:
            state = frame["state"]
            dropped = set(state["dropped"])
            live = set(range(state["lo"], state["hi"] + 1))
            assert dropped & live == set(), (seed, frame["kind"], sorted(dropped & live))
            assert dropped | live == set(range(len(values))), (seed, frame["kind"])
            assert hit not in dropped or hit == state["found"], (seed, frame["kind"], sorted(dropped))


def test_binary_search_halves_the_range_it_claims_to_half() -> None:
    for seed in range(20):
        values = binary_search._default_values(seed, 13)
        target = values[seed % len(values)]
        frames = binary_search.run({"values": values, "target": target})
        for probe, nxt in zip(frames, frames[1:]):
            if probe["kind"] != "probe" or nxt["kind"] != "discard":
                continue
            mid, lo, hi = probe["state"]["mid"], probe["state"]["lo"], probe["state"]["hi"]
            if values[mid] > target:
                assert (nxt["state"]["lo"], nxt["state"]["hi"]) == (lo, mid - 1)
                assert set(range(mid, hi + 1)) <= set(nxt["state"]["dropped"])
            else:
                assert (nxt["state"]["lo"], nxt["state"]["hi"]) == (mid + 1, hi)
                assert set(range(lo, mid + 1)) <= set(nxt["state"]["dropped"])


def test_binary_search_rejects_an_unsorted_array() -> None:
    with pytest.raises(ValueError, match="sorted"):
        binary_search.run({"values": [5, 3, 9], "target": 3})
    with pytest.raises(ValueError, match="sorted"):
        binary_search.run({"values": [5, 5, 4], "target": 5})
    with pytest.raises(ValueError, match="between 3 and 24"):
        binary_search.normalize({"values": [9, 1]})
    with pytest.raises(ValueError, match="sorted"):
        binary_search.normalize({"values": [1, 5, 5, 9]})


def test_binary_search_boundary_sizes_and_targets_all_terminate() -> None:
    assert binary_search.run({"values": [4, 5, 6], "target": 4})[-1]["state"]["found"] == 0
    assert binary_search.run({"values": [4, 5, 6], "target": 6})[-1]["state"]["found"] == 2
    for target in (-500, 500, 7):
        assert binary_search.run({"n": 10, "seed": 3, "target": target})[-1]["kind"] == "summary"


# -------------------------------------------------------------------------------------
# insertion sort
# -------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(40))
def test_sorting_produces_exactly_pythons_sorted_result(seed: int) -> None:
    values = sorting.normalize({"n": 11, "seed": seed})["values"]
    frames = sorting.run({"values": values})
    assert frames[-1]["state"]["values"] == sorted(values)


def test_sorting_counts_match_a_hand_written_insertion_sort() -> None:
    for seed in range(25):
        values = sorting.normalize({"n": 10, "seed": seed})["values"]
        state = sorting.run({"values": values})[-1]["state"]
        a = list(values)
        comparisons = shifts = 0
        for i in range(1, len(a)):
            j = i
            while j > 0:
                comparisons += 1
                if a[j - 1] <= a[j]:
                    break
                a[j - 1], a[j] = a[j], a[j - 1]
                shifts += 1
                j -= 1
        assert state["comparisons"] == comparisons, (seed, state["comparisons"], comparisons)
        assert state["shifts"] == shifts, (seed, state["shifts"], shifts)
        assert a == sorted(values)
        truth = sorting.truth({"values": values})
        assert (truth["comparisons"], truth["shifts"], truth["sorted"]) == (
            state["comparisons"],
            state["shifts"],
            sorted(values),
        )


def test_sorting_animation_frames_add_up_to_the_final_counts() -> None:
    frames = sorting.run({"n": 9, "seed": 6})
    state = frames[-1]["state"]
    assert state["comparisons"] == sum(1 for f in frames if f["kind"] == "compare")
    assert state["shifts"] == sum(1 for f in frames if f["kind"] == "shift")
    assert f"{state['comparisons']} comparisons and {state['shifts']} shifts" in frames[-1]["narration"]


def test_sorting_is_stable_on_equal_keys() -> None:
    """A card never walks past a card it equals -- that is what makes the sort stable.

    Checked against a reference sort over *decorated* cards, so each value carries its original
    position and a card that jumped past an equal one would be visible.
    """
    for values in ([4, 4, 4, 1, 1], [2, 2, 1, 1, 2], [7, 7, 3, 7, 3, 1, 1]):
        deco = [(v, k) for k, v in enumerate(values)]
        expected_places: list[int] = []
        for i in range(1, len(deco)):
            key = deco[i]
            j = i
            while j > 0 and deco[j - 1][0] > key[0]:
                deco[j] = deco[j - 1]
                j -= 1
            deco[j] = key
            expected_places.append(j)
        frames = sorting.run({"values": values})
        assert [f["state"]["j"] for f in frames if f["kind"] == "place"] == expected_places, values
        assert [d[0] for d in deco] == sorted(values)


def test_the_hole_and_the_held_card_always_add_up() -> None:
    """Mid-walk the array physically holds a duplicate: the slot at ``j`` is the hole and the
    card being walked is in ``held``. Putting them back together must reproduce the input
    exactly -- that is the invariant a renderer relies on when it draws "one card is out".
    """
    for seed in range(20):
        values = sorting.normalize({"n": 10, "seed": seed})["values"]
        for frame in sorting.run({"values": values}):
            state = frame["state"]
            held, j = state["held"], state["j"]
            pool = list(state["values"])
            if held is not None:
                assert j < state["i"], (seed, frame["kind"], "a held card with no walk in progress")
                pool[j] = held
            assert sorted(pool) == sorted(values), (seed, frame["kind"], state)
        # and the sim is not fudging it: a card really does leave the array
        out = [f for f in sorting.run({"values": values}) if f["state"]["held"] is not None]
        assert out, "no frame ever had a card out of the array"


# -------------------------------------------------------------------------------------
# logic gates
# -------------------------------------------------------------------------------------
@pytest.mark.parametrize("gate", sorted(logic_gates.GATES))
def test_logic_gate_table_matches_the_stated_rule_exactly(gate: str) -> None:
    arity, rule, _ = logic_gates.GATES[gate]
    frames = logic_gates.run({"gate": gate, "walk": True})
    table = frames[-1]["state"]["table"]
    assert len(table) == 1 << arity
    for row in table:
        inputs = [row["a"]] + ([row["b"]] if arity == 2 else [])
        assert row["out"] == int(bool(rule(*inputs)) if arity == 1 else bool(rule(*inputs)))


def test_logic_gate_output_frames_agree_with_their_drive_frame() -> None:
    for gate in ("AND", "XOR", "NAND", "NOT"):
        params = {"gate": gate, "walk": True}
        frames = logic_gates.run(params)
        rule = logic_gates.GATES[gate][1]
        driven = [(f, n) for f, n in zip(frames, frames[1:]) if f["kind"] == "drive" and n["kind"] == "output"]
        assert driven
        for drive, out in driven:
            assert out["state"]["row"] == drive["state"]["row"]
            assert out["state"]["a"] == drive["state"]["a"]
            inputs = [drive["state"]["a"]] + ([drive["state"]["b"]] if logic_gates.GATES[gate][0] == 2 else [])
            assert out["state"]["out"] == int(rule(*inputs))


def test_logic_gate_table_hides_rows_that_have_not_been_reached() -> None:
    """The truth table is not a spoiler: only rows that have been driven carry an output."""
    frames = logic_gates.run({"gate": "XOR", "walk": True})
    first_drive = [f for f in frames if f["kind"] == "drive"][0]
    assert first_drive["prompt"]["type"] == "output"
    assert first_drive["state"]["table"][0]["out"] is None
    assert all(row["out"] is None for row in first_drive["state"]["table"])
    last_output = [f for f in frames if f["kind"] == "output"][-1]
    assert all(row["out"] is not None for row in last_output["state"]["table"])


def test_logic_gate_single_input_gate_needs_no_b() -> None:
    frames = logic_gates.run({"gate": "NOT", "a": 1, "walk": False})
    assert frames[1]["state"]["a"] == 1 and frames[1]["state"]["b"] is None
    assert [f for f in frames if f["kind"] == "output"][0]["state"]["out"] == 0


def test_logic_gate_walking_shows_every_row_once_in_counting_order() -> None:
    frames = logic_gates.run({"gate": "NAND", "walk": True})
    rows = [f["state"]["row"] for f in frames if f["kind"] == "drive"]
    assert rows == [0, 1, 2, 3]
    driven = {(f["state"]["a"], f["state"]["b"]) for f in frames if f["kind"] == "drive"}
    assert driven == {(0, 0), (0, 1), (1, 0), (1, 1)}


def test_logic_gate_rejects_an_unknown_gate_and_out_of_range_input() -> None:
    with pytest.raises(ValueError):
        logic_gates.run({"gate": "IMPLIES"})
    with pytest.raises(ValueError, match="logic level"):
        logic_gates.run({"gate": "AND", "a": "banana"})


# -------------------------------------------------------------------------------------
# the tiny CPU
# -------------------------------------------------------------------------------------
def test_cpu_runs_a_program_and_writes_the_answer_to_memory() -> None:
    frames = cpu_cycle.run({"program": ["LOAD 4", "ADD 5", "STORE 6", "HLT"], "data": [10, 6, 0]})
    final = frames[-1]
    assert final["kind"] == "done"
    assert final["state"]["a"] == 16
    assert final["state"]["cells"] == ["LOAD 4", "ADD 5", "STORE 6", "HLT", 10, 6, 16]
    assert cpu_cycle.truth({"program": ["LOAD 4", "ADD 5", "STORE 6", "HLT"], "data": [10, 6, 0]})["a"] == 16


def test_cpu_loop_counts_down_and_stops_at_zero() -> None:
    params = {"program": ["LOAD 6", "SUB 7", "STORE 6", "JZ 5", "JMP 0", "HLT"], "data": [7, 1], "max_steps": 40}
    frames = cpu_cycle.run(params)
    assert frames[-1]["kind"] == "done"
    assert frames[-1]["state"]["cells"][6] == 0
    assert cpu_cycle.truth(params)["status"] == "halted"


def test_cpu_stops_honestly_when_the_program_runs_off_the_end() -> None:
    frames = cpu_cycle.run({"program": ["LOAD 2", "ADD 3"], "data": [4, 5, 1, 2]})
    assert any(f["kind"] == "off_end" for f in frames)
    off_end = [f for f in frames if f["kind"] == "off_end"][0]
    assert "past the last instruction" in off_end["narration"]
    assert "no HLT" in off_end["narration"]


def test_cpu_refuses_a_jump_into_the_data() -> None:
    with pytest.raises(ValueError, match="jump"):
        cpu_cycle.normalize({"program": ["JMP 3"], "data": [7]})


def test_cpu_refuses_arithmetic_on_a_cell_holding_an_instruction() -> None:
    with pytest.raises(ValueError, match="data cell"):
        cpu_cycle.normalize({"program": ["LOAD 0"]})
    with pytest.raises(ValueError, match="data cell"):
        cpu_cycle.normalize({"program": ["LOAD 4", "HLT"], "data": [1]})


def test_cpu_refuses_unknown_mnemonics_and_free_text() -> None:
    for bad in (["MULTIPLY 3"], ["# a comment"], ["ADD 1 2"], ["", "HLT"], ["LOAD x"], ["halt"]):
        with pytest.raises(ValueError, match="instruction set"):
            cpu_cycle.normalize({"program": bad})


def test_cpu_step_limit_is_enforced_and_says_so() -> None:
    frames = cpu_cycle.run({"program": ["LOAD 3", "JMP 0", "HLT"], "data": [3, 0, 0, 9], "max_steps": 2})
    assert any(f["kind"] == "limit" for f in frames)
    assert "had not halted after 2 instructions" in frames[-1]["narration"]


def test_cpu_registers_never_leave_the_machine() -> None:
    program, data = ["LOAD 6", "ADD 7", "SUB 8", "JZ 4", "JMP 0", "HLT"], [5, 5, 2, 0, 0, 0, 1]
    frames = cpu_cycle.run({"program": program, "data": data, "max_steps": 40})
    for frame in frames:
        state = frame["state"]
        assert 0 <= state["pc"] <= len(state["cells"])
        assert 0 <= state["cycle"] <= 40
        assert isinstance(state["z"], bool)
        assert len(state["cells"]) == len(program) + len(data)
        assert state["cells"][: len(program)] == program  # code is never overwritten by data


def test_cpu_zero_flag_follows_the_accumulator() -> None:
    frames = cpu_cycle.run({"program": ["LOAD 3", "SUB 3", "HLT"], "data": [9, 0, 9, 9]})
    executed = [f for f in frames if f["kind"] == "execute"]
    assert executed[0]["state"]["a"] == 9 and executed[0]["state"]["z"] is False
    assert executed[1]["state"]["a"] == 0 and executed[1]["state"]["z"] is True


def test_cpu_second_interpreter_agrees_with_the_trace() -> None:
    for program, data in (
        (["LOAD 4", "ADD 5", "STORE 6", "HLT"], [7, 5, 0]),
        (["LOAD 5", "SUB 5", "HLT"], [3, 4, 4]),
        (["LOAD 5", "ADD 6", "ADD 7", "STORE 8", "HLT"], [1, 2, 5, 0]),
    ):
        params = {"program": program, "data": data}
        truth = cpu_cycle.truth(params)
        state = cpu_cycle.run(params)[-1]["state"]
        assert truth["a"] == state["a"]
        assert truth["cells"] == state["cells"]
        assert truth["cycles"] == state["cycle"]


# -------------------------------------------------------------------------------------
# contracts every scene kind must satisfy
# -------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", sims.KINDS)
def test_frames_are_plain_json_short_and_indexed(kind: str) -> None:
    frames = sims.frames_for(kind, {})
    assert frames
    for index, frame in enumerate(frames):
        assert frame["i"] == index
        assert frame["kind"]
        assert isinstance(frame["title"], str) and frame["title"]
        assert 0 < len(frame["narration"]) <= sims.MAX_NARRATION, (kind, index, len(frame["narration"]))
        assert isinstance(frame["cause"], (str, type(None)))
        _plain_json(frame["state"])
        _plain_json(frame["highlights"])
        _plain_json(frame["prompt"])
        assert set(frame) == {"i", "kind", "title", "narration", "state", "highlights", "cause", "prompt"}


@pytest.mark.parametrize("kind", sims.KINDS)
def test_replaying_the_same_spec_gives_the_same_trace(kind: str) -> None:
    assert json.dumps(sims.frames_for(kind, {})) == json.dumps(sims.frames_for(kind, {}))


@pytest.mark.parametrize("kind", sims.KINDS)
def test_every_declared_prompt_actually_appears(kind: str) -> None:
    """A catalogue that advertises a prompt the generator never emits is a lie to the UI."""
    module = sims.module_for(kind)
    seen = {f["prompt"]["type"] for f in sims.frames_for(kind, {}) if f.get("prompt")}
    assert seen, f"{kind} produced no predictions at all"
    assert seen <= set(module.PROMPTS), f"{sorted(seen - set(module.PROMPTS))} not declared by {kind}"


#: prompts whose answer is a phrase rather than a number, checked as prose
_ANSWER_IS_TEXT = {"next_branch", "comparison_count", "next_probe_index", "where_it_lands", "output", "next_row", "next_register"}


@pytest.mark.parametrize("kind", sims.KINDS)
def test_a_question_never_prints_its_own_answer(kind: str) -> None:
    """A prediction is only a prediction if the answer is not already on the screen."""
    module = sims.module_for(kind)
    asked = 0
    for frame in sims.frames_for(kind, {}):
        prompt = frame.get("prompt")
        if not prompt:
            continue
        asked += 1
        expected = module.expected({}, frame["i"])
        assert expected is not None, (kind, frame["i"], "a prompt with no gradeable answer")
        assert expected in prompt["choices"], (kind, frame["i"], expected, prompt["choices"])
        if prompt["type"] in _ANSWER_IS_TEXT:
            assert expected.lower() not in frame["narration"].lower(), (kind, frame["i"], expected, frame["narration"])
        else:
            # numeric answers: the frame must be asking about a phase it has not reached, and for
            # the instructions that really do change A, A on screen must not be the answer yet
            assert frame["kind"] == "decode", (kind, frame["i"], "asks about something other than the next phase")
            op = str(frame["state"].get("ir", "")).split()[0]
            if op in ("LOAD", "ADD", "SUB"):
                assert str(frame["state"]["a"]) != expected, (kind, frame["i"], expected, frame["state"])
    assert asked >= len(sims.KINDS), "the sims stopped asking anything"


@pytest.mark.parametrize("kind", sims.KINDS)
def test_grading_is_right_and_wrong_in_the_right_places(kind: str) -> None:
    module = sims.module_for(kind)
    graded = 0
    for frame in sims.frames_for(kind, {}):
        prompt = frame.get("prompt")
        if not prompt:
            continue
        want = module.expected({}, frame["i"])
        assert module.grade({}, frame["i"], want)["correct"] is True, (kind, frame["i"], want)
        for wrong in (c for c in prompt["choices"] if c != want):
            verdict = module.grade({}, frame["i"], wrong)
            assert verdict["correct"] is False, (kind, frame["i"], wrong)
            assert verdict["expected"] == want
        graded += 1
    assert graded >= 4


def test_the_option_order_is_tied_to_the_frame_not_to_the_alphabet() -> None:
    """A fixed order is a tell. The same three options on the next frame have to be laid out
    differently, or a learner who cannot answer learns which row to press instead."""
    from adapt.learning.sims._choice import order_choices

    options = ["keep the left half", "keep the right half", "stop: found it"]
    orders = {tuple(order_choices("binary_search", i, options)) for i in range(12)}
    assert len(orders) >= 3, "the shuffle is not actually shuffling"
    assert order_choices("binary_search", 4, options) == order_choices("binary_search", 4, list(reversed(options)))
    assert sorted(order_choices("x", 1, options)) == sorted(options)
    assert sorted(order_choices("sorting", 1, ["a", "b", "a"])) == ["a", "b"]  # repeats collapse


def test_a_prediction_made_on_a_silent_frame_is_not_marked_correct() -> None:
    out = sims.grade_prediction("binary_search", {}, 6, "keep the left half")
    assert out["prompt"] is None and out["correct"] is False


def test_prediction_choices_are_not_always_in_the_same_position() -> None:
    """If the right button were always the first one, the lesson would train a click."""
    multi = []
    for kind in sims.KINDS:
        module = sims.module_for(kind)
        for frame in sims.frames_for(kind, {}):
            prompt = frame.get("prompt")
            if prompt and len(prompt["choices"]) > 2:
                multi.append(prompt["choices"][0] == module.expected({}, frame["i"]))
    assert len(multi) >= 5
    assert 0 < sum(multi) < len(multi), "every multi-option answer sat in the same slot"


def test_the_dispatcher_refuses_a_kind_it_does_not_have() -> None:
    with pytest.raises(KeyError):
        sims.grade_prediction("no_such_kind", {}, 0, "x")
    assert sims.module_for("logic_gates") is logic_gates
    assert sims.catalogue()


def test_explain_answers_from_the_trace_or_says_so() -> None:
    params = {"n": 12, "seed": 9}
    frames = sims.frames_for("binary_search", params)
    idx = [f["i"] for f in frames if f["kind"] == "discard"][0]
    answer = binary_search.explain(params, idx, "why can we throw half of it away?")
    assert answer is not None and "orted" in answer["answer"]
    assert answer["frame"] == idx
    assert binary_search.explain(params, idx, "what is the weather like") is None
    assert binary_search.explain(params, 999, "why") is None


def test_catalogue_matches_the_modules_it_lists() -> None:
    table = sims.catalogue()
    assert sorted(table) == sorted(sims.KINDS)
    for kind, meta in table.items():
        module = sims.module_for(kind)
        assert meta["max_frames"] == module.MAX_FRAMES
        assert meta["prompts"] == tuple(module.PROMPTS)
        assert set(meta["params"]) == set(module.PARAMS)
        for name, spec in meta["params"].items():
            assert spec["type"] == module.PARAMS[name]["type"]
            assert "cast" not in spec  # a callable cannot be sent to a browser
        assert isinstance(meta["answers_why"], bool)
        assert meta["summary"]


def test_frame_count_limit_is_a_real_limit() -> None:
    """A learner-authored spec cannot make the server draw a thousand frames."""
    worst = list(range(sorting.MAX_ITEMS, 0, -1))  # reverse order is insertion sort's bad day
    assert len(sorting.run({"values": worst})) > sorting.MAX_FRAMES
    with pytest.raises(ValueError, match="over its limit"):
        sims.frames_for("sorting", {"values": worst})
    for seed in range(12):
        assert len(sims.frames_for("sorting", {"n": sorting.MAX_ITEMS, "seed": seed})) <= sorting.MAX_FRAMES
