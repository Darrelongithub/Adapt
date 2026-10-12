"""The simulations the Interactive Learning Engine plays.

Three rules, each of which is enforced here rather than hoped for:

**The simulation is application code, not content.** An animation must show what the
subject actually does, so the steps are computed by tested Python -- never by a model
"describing" an algorithm, and never by a browser script embedded in a scene spec
(:mod:`adapt.learning.scenespec` refuses those). A renderer may interpolate and colour;
it may not decide.

**One trace, many viewers.** :func:`frames_for` returns the *whole* trace for a set of
parameters. Playback -- play, pause, step, scrub, speed -- is the frontend moving an
index through that list, so it costs no network call and no model call. Changing a
variable re-runs this function. Determinism is therefore load-bearing: same params, same
trace, so a learner resuming after a restart sees the lesson they left.

**No answer leaks.** Some frames carry a ``prompt`` -- a prediction to make before the
animation continues. The trace never carries the answer to its own prompt: the check
re-runs the simulation and compares (:func:`expected_after`). A learner looking at the
page source cannot read the future.

Each module in this package exports the same small surface::

    KIND: str
    TITLE: str
    PARAMS: dict[str, dict]          # declared parameters, with bounds -- see scenespec
    MAX_FRAMES: int
    def run(params: dict) -> list[dict]
    def explain(params: dict, frame_index: int, question: str = "") -> dict | None

``explain`` is the deterministic half of AI 3: "why did that happen?" is often answerable
from the frame that already records its own cause, and then no model call is needed.
"""

from __future__ import annotations

from typing import Any, Callable

from . import binary_search, cpu_cycle, logic_gates, sorting

_MODULES: dict[str, Any] = {
    binary_search.KIND: binary_search,
    sorting.KIND: sorting,
    logic_gates.KIND: logic_gates,
    cpu_cycle.KIND: cpu_cycle,
}

#: every scene kind the engine can compute; ``scenespec`` refuses anything else and the
#: contract check asserts the frontend's renderer registry agrees with this set.
KINDS: tuple[str, ...] = tuple(sorted(_MODULES))


def module_for(kind: str) -> Any:
    mod = _MODULES.get(kind)
    if mod is None:
        raise KeyError(f"no simulation named {kind!r}")
    return mod


def catalogue() -> dict[str, dict[str, Any]]:
    """What each scene kind is, what it accepts and what it can answer -- used by the
    validator, the lessons list and AI 3 when it has to choose or configure."""
    return {
        kind: {
            "title": mod.TITLE,
            "summary": mod.SUMMARY,
            "params": {
                name: {k: v for k, v in spec.items() if k != "cast"}
                for name, spec in mod.PARAMS.items()
            },
            "max_frames": mod.MAX_FRAMES,
            "prompts": mod.PROMPTS,
            "answers_why": bool(getattr(mod, "explain", None)),
        }
        for kind, mod in sorted(_MODULES.items())
    }


def param_casters(kind: str) -> dict[str, Callable[[Any], Any]]:
    return {name: spec["cast"] for name, spec in module_for(kind).PARAMS.items()}


#: the most a single frame may say. Narration is spoken aloud by the TTS layer and shipped to a
#: browser on every step, so it is bounded here rather than trusted downstream.
MAX_NARRATION = 320


def frames_for(kind: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """The full trace for one set of parameters. Raises ``ValueError`` on bad input --
    the validator is supposed to have caught that already, so a raise here is a bug in
    the caller, and saying so beats drawing a wrong animation."""
    mod = module_for(kind)
    frames = mod.run(dict(params or {}))
    if len(frames) > mod.MAX_FRAMES:
        raise ValueError(f"{kind} produced {len(frames)} frames, over its limit of {mod.MAX_FRAMES}")
    for i, frame in enumerate(frames):
        frame["i"] = i
        if len(str(frame.get("narration") or "")) > MAX_NARRATION:
            raise ValueError(
                f"{kind} frame {i} ({frame.get('kind')}) narrates {len(str(frame['narration']))} characters, "
                f"over the limit of {MAX_NARRATION}"
            )
    return frames


def grade_prediction(kind: str, params: dict[str, Any], frame_index: int, choice: Any) -> dict[str, Any]:
    """Was the learner's prediction right? Recomputed from the simulation, never stored.

    Each module owns the meaning of its own prompts, which is what keeps the answer out of
    the trace: the frames the learner holds say nothing about what comes next.
    """
    mod = module_for(kind)
    grade = getattr(mod, "grade", None)
    if grade is None:
        raise KeyError(f"{kind} declares no predictions to grade")
    frames = frames_for(kind, params)
    index = int(frame_index)
    prompt = frames[index].get("prompt") if 0 <= index < len(frames) else None
    out = dict(grade(dict(params or {}), index, choice))
    out["prompt"] = prompt
    return out


def expected_after(kind: str, params: dict[str, Any], frame_index: int) -> dict[str, Any] | None:
    """The frame that actually follows ``frame_index`` -- the oracle a prediction is
    checked against. Recomputed rather than stored so nothing can pre-read it."""
    frames = frames_for(kind, params)
    if frame_index + 1 >= len(frames):
        return None
    return frames[frame_index + 1]


__all__ = ["KINDS", "MAX_NARRATION", "catalogue", "expected_after", "frames_for", "grade_prediction", "module_for", "param_casters"]
