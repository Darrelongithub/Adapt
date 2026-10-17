"""Track F (Round 6): AI 3's scenes come from the template library, and the 4 sims validate.

Four lessons are built the way the product builds them — a lesson writer may only name a
template and bounded parameters (:mod:`adapt.learning.template_library`), and the validator
dry-runs the spec into plain frames before anything reaches a browser. Between them the
lessons use every drawing kind the animator knows: **shapes** (circle/rectangle), **person**,
**car**, **triangle**, **number line** and **graph**.

The four simulations (binary search, insertion sort, logic gates, CPU cycle) are the other
half of the animation surface: they are application code, their parameters are bounded, and
their traces are deterministic with no answer leaked into a prediction prompt.
"""

from __future__ import annotations

from typing import Any

import pytest

from adapt.learning import scenespec, sims
from adapt.learning.scene_api import render_scene, suggest_template
from adapt.learning.template_library import TEMPLATE_IDS, validate_and_dry_run

# The four lessons Track F builds, one scene each (the graph lesson ships two).
LESSONS: list[dict[str, Any]] = [
    {
        "lesson": "The angles inside a triangle",
        "scenes": [
            {"template": "angle-sum-of-triangle", "params": {"angles": [60, 70, 50]}, "captions": ["Three angles.", "They add up."]},
        ],
        "kinds": {"triangle"},
    },
    {
        "lesson": "A car moving at a steady speed",
        "scenes": [
            {"template": "car-motion", "params": {"speed": 30, "seconds": 4}, "captions": ["A car at 30.", "Where is it now?"]},
        ],
        "kinds": {"car"},
    },
    {
        "lesson": "Steps on the number line",
        "scenes": [
            {"template": "number-line-operations", "params": {"start": 0, "steps": [3, -1]}, "captions": ["Start at zero.", "Then step."]},
        ],
        "kinds": {"line"},  # the ticked number line
    },
    {
        "lesson": "Graphs: networks and curves",
        "scenes": [
            {"template": "graph-traversal", "params": {"nodes": ["A", "B", "C"], "edges": [["A", "B"], ["B", "C"]], "algorithm": "BFS"}, "captions": ["Three nodes.", "Breadth first."]},
            {"template": "function-plot", "params": {"kind": "linear", "a": 2, "b": 1}, "captions": ["A straight line.", "Then a curve."]},
        ],
        "kinds": {"node", "graph"},
    },
]

# The remaining drawing kinds travel with the library rather than the four lessons:
# shapes (venn circles, fraction rectangles) and person (the sentence's subject).
EXTRA_KINDS: dict[str, dict[str, Any]] = {
    "shapes": {"template": "venn-sets", "params": {"left": ["a", "b"], "right": ["b", "c"]}},
    "person": {"template": "grammar-sentence-diagram", "params": {"words": ["Learners", "solve", "problems"]}},
}


def _object_kinds(frames: list[dict[str, Any]]) -> set[str]:
    return {str(obj.get("type")) for frame in frames for obj in frame.get("objects", [])}


def test_four_lessons_build_their_scenes_from_the_template_library() -> None:
    """Every lesson scene validates and dry-runs; no lesson needs markup or code."""
    seen_templates: set[str] = set()
    seen_kinds: set[str] = set()
    for lesson in LESSONS:
        for spec in lesson["scenes"]:
            clean, frames, errors = validate_and_dry_run(spec)
            assert not errors, f"{lesson['lesson']}: {errors}"
            assert clean is not None and frames, lesson["lesson"]
            assert clean["template"] in TEMPLATE_IDS
            seen_templates.add(clean["template"])
            seen_kinds |= lesson["kinds"] & _object_kinds(frames)
            assert all(frame.get("caption") for frame in frames), "every frame narrates itself"
    assert len(LESSONS) == 4
    assert seen_kinds == {"triangle", "car", "line", "node", "graph"}, seen_kinds
    assert {"angle-sum-of-triangle", "car-motion", "number-line-operations", "graph-traversal"} <= seen_templates


def test_shapes_and_person_round_out_the_drawing_vocabulary() -> None:
    kinds: set[str] = set()
    for spec in EXTRA_KINDS.values():
        clean, frames, errors = validate_and_dry_run(spec)
        assert not errors and clean is not None
        kinds |= _object_kinds(frames)
    assert "circle" in kinds and "rectangle" in kinds, f"shapes missing from {kinds}"
    assert "person" in kinds, f"person missing from {kinds}"


def test_frames_are_deterministic_and_carry_no_code() -> None:
    """Same spec, same frames — a learner resuming after a restart sees what they left."""
    spec = LESSONS[1]["scenes"][0]
    _, first, _ = validate_and_dry_run(spec)
    _, second, _ = validate_and_dry_run(dict(spec))
    assert first == second
    _, evil, errors = validate_and_dry_run({"template": "car-motion", "params": {"speed": 30, "seconds": 4, "note": "<script>x()</script>"}})
    assert errors or all("<script" not in str(obj) for frame in (evil or []) for obj in frame.get("objects", []))


def test_the_animator_six_kinds_cover_the_whole_library() -> None:
    """Every template in the library draws with the six kinds the animator renders."""
    allowed = {"text", "triangle", "line", "circle", "rectangle", "node", "car", "person", "graph"}
    for template in TEMPLATE_IDS:
        from adapt.learning.template_library import _default_params, dry_run

        frames = dry_run({"template": template, "params": _default_params(template)})
        kinds = _object_kinds(frames)
        assert kinds <= allowed, f"{template} emits {kinds - allowed}"


@pytest.mark.parametrize("kind", sorted(sims.KINDS))
def test_each_simulation_validates_and_replays_deterministically(kind: str) -> None:
    """The 4 sims: bounded parameters, deterministic traces, prompts without answers."""
    spec = {"kind": kind, "title": "t", "intent": "i", "framing": {}, "concepts": [], "citations": []}
    for name, bound in sims.catalogue()[kind]["params"].items():
        spec[name] = bound.get("default") if "default" in bound else (bound.get("min") or 0)
    validated = scenespec.validate(dict(spec))
    assert validated.ok, f"{kind} failed validation: {validated.errors}"
    frames_a = sims.frames_for(kind, {k: v for k, v in spec.items() if k not in {"kind", "title", "intent", "framing", "concepts", "citations"}})
    frames_b = sims.frames_for(kind, {k: v for k, v in spec.items() if k not in {"kind", "title", "intent", "framing", "concepts", "citations"}})
    assert frames_a and frames_a == frames_b
    for frame in frames_a:
        prompt = frame.get("prompt")
        if prompt:
            assert "answer" not in prompt and "correct" not in prompt, f"{kind} prompt leaks its answer: {prompt}"
