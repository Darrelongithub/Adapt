"""Binary search, as a trace of decisions rather than as code.

The point of the animation is not "the answer is index 7"; it is that *every* comparison
throws away half of what is left, and that this only works because the list is sorted. So
each frame records the range still in play, what was discarded, and -- in ``cause`` -- the
reason, which is what makes "why did we throw the left half away?" answerable without a
model call.

The input is asserted, never repaired: an unsorted or duplicated list would make the
animation show a wrong algorithm, so :func:`run` refuses it and the validator in
:mod:`adapt.learning.scenespec` is what turns a learner's edit into a legal list.
"""

from __future__ import annotations

import math
from typing import Any

from ._choice import order_choices

KIND = "binary_search"
TITLE = "Binary search on a sorted list"
SUMMARY = (
    "Watch the search range halve at every comparison, and see why sorted order is what makes "
    "one look at the middle worth two looks at the ends."
)
#: the prediction kinds this simulation can check
PROMPTS = ("comparison_count", "next_branch", "next_probe_index")
MAX_FRAMES = 200

MIN_ITEMS, MAX_ITEMS = 3, 24
MAX_VALUE = 9999


def _default_values(seed: int, n: int) -> list[int]:
    """A sorted, strictly increasing list, derived from a seed so the same spec replays."""
    rng = seed % 97 + 2
    out, cur = [], 1
    for _ in range(n):
        cur += rng % 9 + 1
        out.append(cur)
        rng = (rng * 31 + 7) % 89
    return out


PARAMS: dict[str, dict[str, Any]] = {
    "values": {
        "type": "ints",
        "min_items": MIN_ITEMS,
        "max_items": MAX_ITEMS,
        "min": 0,
        "max": MAX_VALUE,
        "sorted": True,
        "unique": True,
        "default": None,
        "label": "The list",
        "help": "sorted, no repeats, 3 to 24 numbers. Left empty, the lesson generates one from the seed.",
    },
    "n": {"type": "int", "min": MIN_ITEMS, "max": MAX_ITEMS, "default": 12, "label": "List size", "help": "how many values to search through"},
    "target": {
        "type": "int",
        "min": 0,
        "max": MAX_VALUE,
        "default": None,
        "label": "Target",
        "help": "the value to look for. Any number works, including one that is not in the list.",
    },
    "seed": {"type": "int", "min": 1, "max": 10_000_000, "default": 7, "label": "Seed", "help": "same seed, same list", "ui": "hidden"},
}


def normalize(params: dict[str, Any]) -> dict[str, Any]:
    """Fill in what the caller left out. Called by the validator, so the sim stays strict."""
    seed = int(params.get("seed") or 7)
    n = int(params.get("n") or min(12, MAX_ITEMS))
    values = params.get("values")
    if not values:
        values = _default_values(seed, max(MIN_ITEMS, min(n, MAX_ITEMS)))
    values = [int(v) for v in values]
    if len(values) < MIN_ITEMS or len(values) > MAX_ITEMS:
        raise ValueError(f"values must hold between {MIN_ITEMS} and {MAX_ITEMS} numbers")
    if not _sorted_ok(values):
        raise ValueError("values must be sorted with no repeats -- binary search is only correct on a sorted list")
    target = params.get("target")
    if target is None or target == "":
        target = values[len(values) // 2]
    return {"values": values, "target": int(target), "seed": seed}


def _sorted_ok(values: list[int]) -> bool:
    return all(values[i] < values[i + 1] for i in range(len(values) - 1))


def _state(values: list[int], lo: int, hi: int, target: int, *, mid: int | None, compared: list[int], dropped: list[int], found: int | None = None) -> dict[str, Any]:
    return {
        "values": values,
        "lo": lo,
        "hi": hi,
        "mid": mid,
        "target": target,
        "compared": list(compared),
        "dropped": list(dropped),
        "found": found,
        "live": max(0, hi - lo + 1),
    }


def run(params: dict[str, Any]) -> list[dict[str, Any]]:
    p = normalize(params)
    values = [int(v) for v in p["values"]]
    target = int(p["target"])

    n = len(values)
    lo, hi = 0, n - 1
    compared: list[int] = []
    # indices the search has *proved* cannot hold the target -- the grey part of the screen
    dropped: set[int] = set()
    frames: list[dict[str, Any]] = [
        {
            "kind": "init",
            "title": f"{n} sorted values, and one target",
            "narration": (
                f"Looking for {target}. The list is sorted, so the only thing we need to know at any "
                f"moment is the range where it could still be: index {lo} to {hi}."
            ),
            "state": _state(values, lo, hi, target, mid=None, compared=compared, dropped=dropped),
            "highlights": ["range"],
            "cause": None,
            "prompt": {
                "type": "comparison_count",
                "question": f"For {n} sorted values, how many comparisons does a search like this need, worst case?",
                "choices": order_choices(KIND, 0, ["about log2(n)", "about n/2", "all n of them"]),
            },
        }
    ]
    steps = 0
    found: int | None = None

    while lo <= hi:
        steps += 1
        mid = (lo + hi) // 2
        v = values[mid]
        compared.append(mid)
        left_n, right_n = mid - lo, hi - mid
        if v == target:
            frames.append(
                {
                    "kind": "probe",
                    "title": f"arr[{mid}] = {v}",
                    # deliberately stops short of the conclusion: this frame asks what happens
                    # next, so it must not also say it
                    "narration": f"The middle of the live range is index {mid}, holding {v}.",
                    "state": _state(values, lo, hi, target, mid=mid, compared=compared, dropped=dropped),
                    "highlights": ["mid"],
                    "cause": f"arr[{mid}] == {target}",
                    "prompt": {
                        "type": "next_branch",
                        "question": f"arr[{mid}] is {v}. What happens now?",
                        "choices": order_choices(KIND, len(frames), ["keep the left half", "keep the right half", "stop: found it"]),
                    },
                }
            )
            found = mid
            frames.append(
                {
                    "kind": "found",
                    "title": f"Found at index {mid}",
                    "narration": (
                        f"{target} is at index {mid}. Every comparison in this search removed half of what was "
                        f"left, which is why {n} sorted values take {steps} comparison{'s' if steps != 1 else ''} "
                        f"instead of up to {n} looks."
                    ),
                    "state": _state(values, lo, hi, target, mid=mid, compared=compared, dropped=dropped, found=mid),
                    "highlights": ["found"],
                    "cause": f"arr[{mid}] == {target}",
                    "prompt": None,
                }
            )
            break

        go_right = v < target
        frames.append(
            {
                "kind": "probe",
                "title": f"arr[{mid}] = {v}, target {target}",
                "narration": (
                    f"The live range is index {lo} to {hi}. Its middle is {mid}, which holds {v}. "
                    f"{'Because ' + str(v) + ' is smaller than ' + str(target) + ', the target can only be to the right.' if go_right else 'Because ' + str(v) + ' is larger than ' + str(target) + ', the target can only be to the left.'}"
                ),
                "state": _state(values, lo, hi, target, mid=mid, compared=compared, dropped=dropped),
                "highlights": ["mid"],
                "cause": f"arr[{mid}] {'<' if go_right else '>'} target",
                "prompt": {
                    # The choices are the only three things that can happen, and none of them
                    # is marked -- the answer is recomputed from the next frame at grading time.
                    "type": "next_branch",
                    "question": f"arr[{mid}] is {v} and the target is {target}. Which half survives this comparison?",
                    "choices": order_choices(KIND, len(frames), ["keep the left half", "keep the right half", "stop: found it"]),
                },
            }
        )
        before = hi - lo + 1
        if go_right:
            gone = range(lo, mid + 1)
            lo = mid + 1
            reason = f"arr[{mid}] = {v} < {target}, so index {mid} and everything left of it is out"
        else:
            gone = range(mid, hi + 1)
            hi = mid - 1
            reason = f"arr[{mid}] = {v} > {target}, so index {mid} and everything right of it is out"
        dropped.update(gone)
        frames.append(
            {
                "kind": "discard",
                "title": "half the range removed",
                "narration": f"{reason}. The range goes from {before} place{'s' if before != 1 else ''} to {max(0, hi - lo + 1)}.",
                "state": _state(values, lo, hi, target, mid=None, compared=compared, dropped=sorted(dropped)),
                "highlights": ["dropped"],
                "cause": reason,
                "prompt": {
                    "type": "next_probe_index",
                    "question": "With that half gone, which index gets looked at next?",
                    "choices": order_choices(
                        KIND,
                        len(frames),
                        [
                            f"index {(lo + hi) // 2}" if lo <= hi else "there is no further comparison",
                            f"index {mid}",
                            f"index {max(0, min(n - 1, (lo + hi) // 2 + 1))}",
                        ],
                    ),
                },
            }
        )

    if found is None:
        frames.append(
            {
                "kind": "exhausted",
                "title": "Not in the list",
                "narration": (
                    f"The range is empty -- lo {lo} has passed hi {hi} -- so {target} is not in the list. "
                    f"An empty range is a result: {steps} comparisons ruled out all {n} places."
                ),
                "state": _state(values, lo, hi, target, mid=None, compared=compared, dropped=sorted(dropped)),
                "highlights": ["range"],
                "cause": "lo > hi",
                "prompt": None,
            }
        )
    frames.append(
        {
            "kind": "summary",
            "title": f"{steps} comparisons for {n} values",
            "narration": (
                f"log2({n}) is about {(_log2n(n)):.2f}, and this search used {steps} comparison{'s' if steps != 1 else ''}. "
                "Halving is why sorted data is worth keeping."
            ),
            "state": {**_state(values, lo, hi, target, mid=None, compared=compared, dropped=sorted(dropped), found=found), "steps": steps, "log2": round(_log2n(n), 3)},
            "highlights": [],
            "cause": None,
            # no prompt here: this frame has just stated the answer, so asking it again would be
            # a reading test, not a prediction. The count is asked at frame 0, before any probing.
            "prompt": None,
        }
    )
    return frames





def _log2n(x: float) -> float:
    return math.log2(x) if x > 1 else 1.0


def explain(params: dict[str, Any], frame_index: int, question: str = "") -> dict[str, Any] | None:
    """The deterministic answer, when the frame already knows why it happened.

    AI 3 tries this first: a question about *this* step usually is a question about the
    reason the step records, and answering it from the trace costs nothing and cannot
    hallucinate. Returning ``None`` is the signal to go and ask a model.
    """
    q = (question or "").lower()
    p = normalize(params) if "values" not in params else params
    frames = run(p)
    if not (0 <= frame_index < len(frames)):
        return None
    frame = frames[frame_index]
    state = frame.get("state") or {}
    cause = str(frame.get("cause") or "")
    target = state.get("target")
    values = state.get("values") or []

    if any(w in q for w in ("why", "discard", "throw", "keep", "remove")) and cause and cause != "lo > hi":
        half = "left" if ("left" in q) else ("right" if "right" in q else None)
        note = (
            f"{cause}. Sorted order is what lets us be sure: if the middle value is on one side of the "
            f"target, everything on the other side of the middle is on the wrong side too, so it cannot be hiding."
        )
        if half:
            note += f" The {half} of the middle is exactly the part that cannot contain {target}."
        return {"answer": note, "source": "frame_cause", "frame": frame_index}
    if "sorted" in q and ("why" in q or "need" in q or "matter" in q):
        return {
            "answer": (
                "Without sorted order a comparison tells you nothing about the other elements, so you would have "
                "to look at every place. Sorting is what turns 'is the middle too big?' into 'everything right of "
                "the middle is too big'."
            ),
            "source": "invariant",
            "frame": frame_index,
        }
    if "how many" in q or "how long" in q or "step" in q:
        n = len(values)
        return {
            "answer": (
                f"At most about log2({n}) = {(_log2n(n) if n else 0):.2f} rounded up, i.e. "
                f"{math.ceil(_log2n(n)) if n else 1} comparisons for {n} values -- halving {n} times reaches 1."
            ),
            "source": "invariant",
            "frame": frame_index,
        }
    if "not found" in q or "not there" in q or ("missing" in q):
        return {
            "answer": f"An empty range is the proof: lo {state.get('lo')} has moved past hi {state.get('hi')}, and every element was ruled out by a comparison, so {target} is not present.",
            "source": "invariant",
            "frame": frame_index,
        }
    return None


def expected(params: dict[str, Any], frame_index: int) -> str | None:
    """What the *next* part of this trace makes the right answer, recomputed on the spot.

    The trace carries no answers -- :func:`grade` derives them here, so a learner reading the
    JSON they were sent cannot see the future, and a prompt can never drift out of step with
    the simulation that produced it.
    """
    frames = run(normalize(params))
    if not (0 <= frame_index < len(frames)):
        return None
    frame = frames[frame_index]
    prompt = frame.get("prompt") or {}
    kind = prompt.get("type")
    nxt = frames[frame_index + 1] if frame_index + 1 < len(frames) else None
    state = frame.get("state") or {}
    if kind == "next_branch":
        if nxt is None:
            return None
        if nxt.get("kind") == "found":
            return "stop: found it"
        mid = state.get("mid")
        if nxt["state"].get("lo") == (mid or 0) + 1:
            return "keep the right half"
        return "keep the left half"
    if kind == "next_probe_index":
        later = [f for f in frames[frame_index + 1 :] if f.get("kind") == "probe"]
        if not later:
            return "there is no further comparison"
        return f"index {later[0]['state'].get('mid')}"
    if kind == "comparison_count":
        return "about log2(n)"
    return None


def grade(params: dict[str, Any], frame_index: int, choice: Any) -> dict[str, Any]:
    expected_choice = expected(params, frame_index)
    given = str(choice or "").strip()
    ok = expected_choice is not None and given == expected_choice
    state = (run(normalize(params))[frame_index].get("state") or {}) if 0 <= frame_index < len(run(normalize(params))) else {}
    why = ""
    if expected_choice == "keep the right half":
        why = f"The middle value was below the target, so only the right half could still hold it."
    elif expected_choice == "keep the left half":
        why = "The middle value was above the target, so everything right of it is too."
    elif expected_choice == "stop: found it":
        why = "The comparison matched, and a match ends the search."
    elif expected_choice == "about log2(n)":
        why = "Each comparison halves what is left, so the count grows with log2 of the list size."
    return {"correct": ok, "expected": expected_choice, "said": given, "why": why, "frame": frame_index, "state": state}


def truth(params: dict[str, Any]) -> dict[str, Any]:
    """The independently computed outcome, for the correctness test to compare against."""
    p = normalize(params) if "values" not in params else params
    values = [int(v) for v in p["values"]]
    target = int(p["target"])
    return {"index": values.index(target) if target in values else None, "sorted": _sorted_ok(values), "count": len(values)}
