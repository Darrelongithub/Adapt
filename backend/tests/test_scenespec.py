"""The scene spec is the only thing an authored lesson can put on the wire -- so the validator
is the security boundary, not a form check.

These tests are written the way the guard has to work: anything that looks like code is refused
with a reason, anything the simulation cannot actually run is refused *before* it is stored, and
nothing is quietly dropped or truncated (a silent drop is how a lesson ends up playing something
other than what was approved).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from adapt.learning import scenespec, sims

GOOD: dict[str, Any] = {
    "kind": "binary_search",
    "title": "Halving a sorted list",
    "intent": "See why one comparison removes half of the answer space.",
    "framing": {"intro": "Twelve sorted cards, one target.", "outro": "Halving is why sorting pays."},
    "concepts": ["binary_search", "invariant"],
    "citations": [{"ref": "CLRS 2.3.1", "claim": "binary search on a sorted array takes Theta(lg n) comparisons."}],
    "params": {"values": [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37], "target": 29},
}


def test_ordinary_mathematics_in_prose_is_not_mistaken_for_markup() -> None:
    """The guard is against executable content, not against the greater-than sign."""
    raw = copy.deepcopy(GOOD)
    raw["intent"] = "Show that arr[mid] > target means index mid and everything right of it is out."
    raw["framing"]["intro"] = "5 < 7, and a range of 0 places is a result."
    assert scenespec.validate(raw).ok


def test_a_good_spec_is_accepted_and_canonicalised() -> None:
    result = scenespec.validate(GOOD)
    assert result.ok, result.errors
    spec = result.spec
    assert spec["kind"] == "binary_search"
    assert spec["title"] == "Halving a sorted list"
    assert spec["params"]["values"] == GOOD["params"]["values"]
    assert spec["params"]["target"] == 29
    assert spec["frame_count"] == len(spec["narration"])
    assert spec["prediction_frames"], "the accepted spec lost the chance to ask anything"
    assert json.loads(json.dumps(spec)) == spec  # storable as-is


def test_missing_optional_fields_fall_back_to_the_scene_defaults() -> None:
    raw = {"kind": "logic_gates", "title": "", "citations": []}
    result = scenespec.validate(raw)
    assert result.ok, result.errors
    assert result.spec["title"] == sims.module_for("logic_gates").TITLE
    assert result.spec["concepts"] == []
    assert any("citation" in w for w in result.warnings), result.warnings


def test_the_drawn_list_is_frozen_into_the_spec_so_a_restart_replays_the_same_lesson() -> None:
    """The stored params are the *normalised* ones: "generate 8 values from seed 3" has already
    been resolved to the eight values. A learner coming back after a restart must watch the same
    trace, and AI 3 must be able to explain the frames they paused on."""
    raw = copy.deepcopy(GOOD)
    raw["params"] = {"n": "8", "seed": "3", "target": "17"}
    result = scenespec.validate(raw)
    assert result.ok, result.errors
    params = result.spec["params"]
    assert params["target"] == 17 and params["seed"] == 3
    assert len(params["values"]) == 8
    assert "n" not in params, "the spec stored a size instead of the list it resolved to"
    assert scenespec.validate(copy.deepcopy(raw)).spec == result.spec


def test_validation_never_edits_the_callers_payload() -> None:
    raw = copy.deepcopy(GOOD)
    before = json.dumps(raw, sort_keys=True)
    scenespec.validate(raw)
    assert json.dumps(raw, sort_keys=True) == before


# -------------------------------------------------------------------------------------
# nothing executable gets through
# -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        ["title", "<script>alert(1)</script>"],
        ["title", "a list <b>sorted</b> like this"],
        ["title", "{{frames[0].narration}}"],
        ["title", "see `run(arr)` for details"],
        ["title", "javascript:alert(1)"],
        ["intent", "onclick=steal() the frames"],
        ["framing.intro", "<img src=x onerror=document.write('hi')>"],
        ["framing.outro", "() => eval(payload)"],
    ],
)
def test_markup_and_code_shapes_are_refused_in_every_text_field(path: list[str]) -> None:
    key, text = path[0], path[1]
    raw = copy.deepcopy(GOOD)
    if key == "title" or key == "intent":
        raw[key] = text
    else:
        raw.setdefault("framing", {})[key.split(".")[1]] = text
    result = scenespec.validate(raw)
    assert not result.ok and result.spec is None
    assert any(key in e or "plain text" in e for e in result.errors), result.errors


def test_a_concept_or_citation_field_is_checked_too() -> None:
    for field, value in (("concepts", ["<b>halving</b>"]), ("citations", [{"ref": "javascript:void(0)", "claim": "x"}])):
        raw = copy.deepcopy(GOOD)
        raw[field] = value
        result = scenespec.validate(raw)
        assert not result.ok, (field, value, result.errors)


@pytest.mark.parametrize("name", ["renderer", "html", "script", "onload", "code", "template", "callback"])
def test_a_param_named_after_something_executable_is_refused_by_name(name: str) -> None:
    """AI 2 cannot smuggle a renderer by inventing a parameter the engine would obey."""
    raw = copy.deepcopy(GOOD)
    raw["params"][name] = "<b>hi</b>"
    result = scenespec.validate(raw)
    assert not result.ok
    assert any(name in e for e in result.errors), result.errors


def test_an_unknown_param_is_an_error_not_a_dropped_key() -> None:
    raw = copy.deepcopy(GOOD)
    raw["params"]["colour"] = "red"
    result = scenespec.validate(raw)
    assert not result.ok
    assert any("colour" in e and "does not declare" in e for e in result.errors), result.errors


# -------------------------------------------------------------------------------------
# the simulation has to be right, so an impossible lesson is refused up front
# -------------------------------------------------------------------------------------
def test_unsorted_values_are_refused_with_the_reason_not_just_a_rule() -> None:
    raw = copy.deepcopy(GOOD)
    raw["params"]["values"] = [5, 3, 9, 1, 7]
    result = scenespec.validate(raw)
    assert not result.ok
    assert any("binary search is wrong on unsorted data" in e for e in result.errors), result.errors


def test_repeated_values_are_refused_for_a_scene_that_needs_distinct_ones() -> None:
    raw = copy.deepcopy(GOOD)
    raw["params"]["values"] = [2, 2, 3, 4, 5, 6]
    result = scenespec.validate(raw)
    assert not result.ok
    assert any("distinct" in e or "strictly increasing" in e for e in result.errors), result.errors


@pytest.mark.parametrize(
    "values",
    [
        [1, 2],  # too few
        list(range(1, 30)),  # too many
        [1, 2, "three"],  # not numbers
        [1, 2, 99999],  # over the value ceiling
        [],  # empty
        {"0": 1},  # the wrong container
        "9, 5, 1, 7",  # parses, but is not sorted
    ],
)
def test_a_list_param_that_cannot_be_drawn_is_refused(values: Any) -> None:
    raw = copy.deepcopy(GOOD)
    raw["params"]["values"] = values
    assert not scenespec.validate(raw).ok


def test_a_cleared_field_means_use_the_default_rather_than_a_validation_error() -> None:
    raw = copy.deepcopy(GOOD)
    raw["params"] = {"n": 9, "seed": 4, "target": None, "values": ""}
    result = scenespec.validate(raw)
    assert result.ok, result.errors
    values = result.spec["params"]["values"]
    assert len(values) == 9 and all(values[i] < values[i + 1] for i in range(8))
    assert result.spec["params"]["target"] == values[4], "the default target should be the middle value"
    # and the same spec regenerated after a restart gives the same list
    assert scenespec.validate(raw).spec["params"]["values"] == values


def test_a_target_outside_the_value_range_still_works_because_that_is_a_real_lesson() -> None:
    raw = copy.deepcopy(GOOD)
    raw["params"]["target"] = 4_000  # absent from the list: the search must say so honestly
    assert scenespec.validate(raw).ok


def test_an_over_long_text_field_is_refused_rather_than_cut() -> None:
    raw = copy.deepcopy(GOOD)
    raw["title"] = "x" * (scenespec.TITLE_MAX + 1)
    result = scenespec.validate(raw)
    assert not result.ok
    assert any(str(scenespec.TITLE_MAX) in e for e in result.errors), result.errors


@pytest.mark.parametrize("field", ["concepts", "citations"])
def test_lists_that_go_past_their_cap_are_refused(field: str) -> None:
    raw = copy.deepcopy(GOOD)
    n = scenespec.MAX_CONCEPTS + 1 if field == "concepts" else scenespec.MAX_CITATIONS + 1
    raw[field] = [{"source": "openlibrary", "work_id": f"OL{k}M"} for k in range(n)] if field == "citations" else [f"c{k}" for k in range(n)]
    assert not scenespec.validate(raw).ok


def test_an_unknown_kind_names_the_ones_that_exist() -> None:
    result = scenespec.validate({"kind": "raytracer", "title": "Rays"})
    assert not result.ok
    assert "binary_search" in result.errors[0] and "sorting" in result.errors[0]


@pytest.mark.parametrize("raw", [None, "a string", 7, [], {"params": 3}, {"kind": "sorting", "params": "not a dict"}])
def test_shapes_that_are_not_specs_at_all_are_rejected(raw: Any) -> None:
    assert not scenespec.validate(raw).ok


# -------------------------------------------------------------------------------------
# the dry run: a spec is only valid if the animation actually plays
# -------------------------------------------------------------------------------------
def test_a_scene_too_big_to_animate_is_refused_with_the_count() -> None:
    raw = {"kind": "sorting", "title": "All of it", "params": {"values": list(range(20, 0, -1))}}
    result = scenespec.validate(raw)
    assert not result.ok and result.spec is None
    assert any("frames" in e for e in result.errors), result.errors


def test_a_program_the_machine_would_reject_is_refused_before_it_is_stored() -> None:
    for program in (["JMP 9"], ["LOAD 0"], ["MULTIPLY 3", "HLT"], ["HLT"] * 20, ["ADD"]):
        raw = {"kind": "cpu_cycle", "title": "Programs", "params": {"program": program, "data": [1, 2, 3]}}
        result = scenespec.validate(raw)
        assert not result.ok, (program, result.errors)


def test_program_lines_may_arrive_as_one_block_of_text() -> None:
    raw = {"kind": "cpu_cycle", "title": "Textbook", "params": {"program": "LOAD 4\nADD 5\nSTORE 6\nHLT", "data": [6, 1, 0]}}
    result = scenespec.validate(raw)
    assert result.ok, result.errors
    assert result.spec["params"]["program"] == ["LOAD 4", "ADD 5", "STORE 6", "HLT"]


def test_the_stored_spec_records_what_a_player_needs_to_resume() -> None:
    spec = scenespec.validate(GOOD).spec
    assert set(spec) == {
        "id",
        "kind",
        "title",
        "intent",
        "framing",
        "concepts",
        "citations",
        "params",
        "frame_count",
        "prediction_frames",
        "narration",
        "resource_limits",
    }
    assert spec["resource_limits"] == {"max_frames": sims.module_for("binary_search").MAX_FRAMES}
    assert spec["id"].startswith("scn_") and len(spec["id"]) == 24
    frames = sims.frames_for(spec["kind"], spec["params"])
    assert len(frames) == spec["frame_count"] == len(spec["narration"])
    assert [f["narration"] for f in frames] == spec["narration"]
    for index in spec["prediction_frames"]:
        assert frames[index].get("prompt"), f"frame {index} was recorded as asking something, but it does not"


def test_an_id_is_a_fingerprint_of_the_content_only() -> None:
    a = scenespec.validate(GOOD).spec
    again = scenespec.validate(copy.deepcopy(GOOD)).spec
    assert a["id"] == again["id"]
    changed = copy.deepcopy(GOOD)
    changed["params"]["target"] = 31
    assert scenespec.validate(changed).spec["id"] != a["id"]
    messy = copy.deepcopy(GOOD)
    messy["title"] = "  Halving   a\nsorted list "  # the same title, typed untidily
    assert scenespec.validate(messy).spec["id"] == a["id"], "a stray newline should not fork a lesson"
    retitled = copy.deepcopy(GOOD)
    retitled["title"] = "Halving, slowly"
    assert scenespec.validate(retitled).spec["id"] != a["id"], "a new title is a new artifact"


def test_builtin_specs_are_ready_to_seed_and_all_different() -> None:
    specs = scenespec.builtin_specs()
    assert len(specs) >= 4
    assert len({s["id"] for s in specs}) == len(specs)
    assert len({s["title"] for s in specs}) == len(specs)
    kinds = {s["kind"] for s in specs}
    assert kinds <= set(sims.KINDS)
    for kind in sims.KINDS:
        assert kind in kinds, f"{kind} has no built-in lesson"
    for spec in specs:
        assert spec["citations"], f"{spec['title']} teaches from nothing"
        assert spec["prediction_frames"], f"{spec['title']} never asks the learner anything"
        assert all(len(text) <= sims.MAX_NARRATION for text in spec["narration"])


def test_builtin_specs_survive_a_json_round_trip() -> None:
    """They are seeded into a column, so whatever they are cannot be a Python-only type."""
    for spec in scenespec.builtin_specs():
        assert json.loads(json.dumps(spec)) == spec
