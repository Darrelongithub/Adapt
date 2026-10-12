"""Insertion sort, frame by frame, with the comparisons counted as they happen.

Learners usually mis-train this animation by watching the swaps without seeing the
*scan*: the interesting part is that the sorted prefix is searched from the right, and
that a value moves only while it is bigger than the target. So every shift frame states
which two values were compared and what was found, and the ``cause`` on the frame is what
"why did it move that far?" is answered from.
"""

from __future__ import annotations

from typing import Any

from ._choice import order_choices

KIND = "sorting"
TITLE = "Insertion sort: build a sorted prefix one card at a time"
SUMMARY = (
    "Watch each value get walked back into the sorted part of the list by comparison, and count "
    "how many comparisons and shifts the whole sort really costs."
)
PROMPTS = ("where_it_lands",)
MAX_FRAMES = 400
MIN_ITEMS, MAX_ITEMS = 2, 20


PARAMS: dict[str, dict[str, Any]] = {
    "values": {
        "type": "ints",
        "min_items": MIN_ITEMS,
        "max_items": MAX_ITEMS,
        "min": 0,
        "max": 999,
        "default": None,
        "label": "The list",
        "help": "any order, repeats allowed -- 2 to 20 numbers",
    },
    "n": {"type": "int", "min": MIN_ITEMS, "max": MAX_ITEMS, "default": 8, "label": "List size", "help": "how many values to sort"},
    "seed": {"type": "int", "min": 1, "max": 10_000_000, "default": 11, "label": "Seed", "help": "same seed, same list", "ui": "hidden"},
}


def normalize(params: dict[str, Any]) -> dict[str, Any]:
    seed = int(params.get("seed") or 11)
    n = int(params.get("n") or 8)
    values = params.get("values")
    if not values:
        n = max(MIN_ITEMS, min(n, MAX_ITEMS))
        cur, rng = 0, seed % 89 + 3
        out = []
        for _ in range(n):
            cur = (cur + rng) % 97
            out.append(cur)
            rng = (rng * 17 + 5) % 71
        values = out
    return {"values": [int(v) for v in values], "seed": seed}


def _array(a: list[int], i: int, j: int, holes: list[int], placed: int, held: int | None = None) -> dict[str, Any]:
    """The whole visible state.

    ``held`` is not decoration: the moment the walk starts the card is *out of the array*, so
    ``values`` alone is missing a number -- and at the comparisons before the first shift the card
    is still in its old slot, where a second copy in the state would be a lie. The rule the
    renderer and the tests both rely on is therefore: ``values`` plus ``held`` is always exactly
    the input multiset, and ``held`` is non-null only while the array is short of that card.
    """
    return {"values": list(a), "i": i, "j": j, "holes": list(holes), "placed_upto": placed, "held": held}


def run(params: dict[str, Any]) -> list[dict[str, Any]]:
    p = normalize(params)
    a = list(p["values"])
    n = len(a)
    if not (MIN_ITEMS <= n <= MAX_ITEMS):
        raise ValueError(f"values must hold between {MIN_ITEMS} and {MAX_ITEMS} numbers")
    comparisons = 0
    shifts = 0
    frames: list[dict[str, Any]] = [
        {
            "kind": "init",
            "title": "The first card is already sorted",
            "narration": (
                f"{n} numbers. Insertion sort keeps a sorted prefix and pushes it right by one each "
                "time: take the next value and walk it back until it is in order."
            ),
            "state": _array(a, 0, 0, [], 0),
            "highlights": ["prefix"],
            "cause": None,
            "prompt": None,
        }
    ]
    for i in range(1, n):
        key = a[i]
        # where the walk will come to rest, worked out the same way the walk does it -- from the
        # right, stopping at the first card that is not bigger. Counting "how many are bigger"
        # would look equivalent and is not: on a repeat it puts the held card *before* an equal
        # one, which is exactly the instability this sort must not have.
        land = 0
        for k in range(i - 1, -1, -1):
            if a[k] <= key:
                land = k + 1
                break
        frames.append(
            {
                "kind": "pick",
                "title": f"Card {i}: value {key}",
                "narration": f"Hold {key} out of the list and walk it back through the sorted part.",
                "state": _array(a, i, i, list(range(i + 1)), i - 1, held=None),
                "highlights": ["held"],
                "cause": f"taking a[{i}] = {key}",
                "prompt": {
                    # landing position is a fact about the prefix as it stands, so it can be
                    # worked out -- but it is not on the screen yet, which is the whole point
                    "type": "where_it_lands",
                    "question": f"Where does {key} land once it has been walked back through {a[:i]}?",
                    "choices": order_choices(KIND, len(frames), [f"position {k}" for k in {land, min(land + 1, i), max(land - 1, 0)}]),
                },
            }
        )
        j = i
        while j > 0:
            comparisons += 1
            left = a[j - 1]
            if left <= key:
                frames.append(
                    {
                        "kind": "compare",
                        "title": f"{left} <= {key}: stop",
                        "narration": f"{left} at index {j - 1} is not bigger than {key}, so {key} belongs right after it. That comparison is what stops the walk.",
                        "state": _array(a, i, j, list(range(i + 1)), j - 1, held=(key if j < i else None)),
                        "highlights": ["compare"],
                        "cause": f"a[{j - 1}] = {left} <= {key}",
                        "prompt": None,
                    }
                )
                break
            frames.append(
                {
                    "kind": "compare",
                    "title": f"{left} > {key}: slide it right",
                    "narration": f"{left} at index {j - 1} is bigger than {key}, so {left} moves to index {j} to make room.",
                    "state": _array(a, i, j, list(range(i + 1)), j - 1, held=(key if j < i else None)),
                    "highlights": ["compare"],
                    "cause": f"a[{j - 1}] = {left} > {key}",
                    "prompt": None,
                }
            )
            a[j] = a[j - 1]
            shifts += 1
            j -= 1
            frames.append(
                {
                    "kind": "shift",
                    "title": f"{left} now at index {j + 1}",
                    "narration": f"The hole moves to index {j}. Nothing has been swapped twice -- a shift is one write.",
                    "state": _array(a, i, j, list(range(i + 1)), j, held=(key if j < i else None)),
                    "highlights": ["hole"],
                    "cause": f"moved {left} from {j} to {j + 1}",
                    "prompt": None,
                }
            )
        a[j] = key
        frames.append(
            {
                "kind": "place",
                "title": f"{key} lands at index {j}",
                "narration": f"Put {key} in the hole at index {j}. The sorted prefix is now {j + 1} long.",
                "state": _array(a, i, j, [], j, held=None),
                "highlights": ["placed"],
                "cause": f"inserted at {j}",
                "prompt": None,
            }
        )
    frames.append(
        {
            "kind": "done",
            "title": "Sorted",
            "narration": (
                f"Done: {comparisons} comparisons and {shifts} shifts for {n} values. Nearly sorted input costs "
                f"about {n} comparisons; reverse order costs n(n-1)/2 = {n * (n - 1) // 2}, which is the quadratic part."
            ),
            "state": {**_array(a, n - 1, 0, [], n - 1), "comparisons": comparisons, "shifts": shifts},
            "highlights": [],
            "cause": f"{comparisons} comparisons, {shifts} shifts",
            "prompt": None,
        }
    )
    return frames


def explain(params: dict[str, Any], frame_index: int, question: str = "") -> dict[str, Any] | None:
    q = (question or "").lower()
    p = normalize(params)
    frames = run(p)
    if not (0 <= frame_index < len(frames)):
        return None
    frame = frames[frame_index]
    cause = str(frame.get("cause") or "")
    if frame["kind"] == "shift" or ("why" in q and ("move" in q or "slide" in q or "right" in q)):
        return {
            "answer": f"{cause}. A value only slides right while it is bigger than the held card -- that test is the whole algorithm, and it is why an already-sorted list costs one comparison per card.",
            "source": "frame_cause",
            "frame": frame_index,
        }
    if "stable" in q or "equal" in q or "same" in q:
        return {
            "answer": "Equal values never overtake each other here: a card slides past another only on a strict '>', so if two values are the same it stops. That is what makes insertion sort stable.",
            "source": "invariant",
            "frame": frame_index,
        }
    if "how many" in q or "cost" in q or "fast" in q or "slow" in q:
        n = len(p["values"])
        return {
            "answer": f"This run took {cause}. Worst case for {n} values is {n * (n - 1) // 2} comparisons; best case is {n - 1}. Insertion sort is cheap when little is out of order, which is why it wins on small and nearly-sorted inputs.",
            "source": "count",
            "frame": frame_index,
        }
    return None


def expected(params: dict[str, Any], frame_index: int) -> str | None:
    """The position the held card actually comes to rest at, read off the trace."""
    frames = run(normalize(params))
    if not (0 <= frame_index < len(frames)):
        return None
    frame = frames[frame_index]
    if (frame.get("prompt") or {}).get("type") != "where_it_lands":
        return None
    for later in frames[frame_index + 1 :]:
        if later["kind"] == "place":
            return f"position {(later['state'] or {}).get('j')}"
    return None


def grade(params: dict[str, Any], frame_index: int, choice: Any) -> dict[str, Any]:
    want = expected(params, frame_index)
    given = str(choice or "").strip()
    ok = want is not None and given == want
    why = ""
    if want is not None:
        why = (
            f"{want}: every card in the sorted prefix bigger than the held one slid right by one place, "
            "and it stopped at the first that was not."
        )
    return {"correct": ok, "expected": want, "said": given, "why": why, "frame": frame_index, "state": {}}


def truth(params: dict[str, Any]) -> dict[str, Any]:
    """An independent account of what the sort must cost, computed without sorting.

    For element ``i``: it passes exactly the earlier elements that exceed it (those are the
    shifts), and it spends one more comparison on the element that made it stop -- unless it
    walked all the way to index 0, where there is nobody left to ask.
    """
    p = normalize(params)
    values = list(p["values"])
    n = len(values)
    comparisons = 0
    shifts = 0
    for i in range(1, n):
        passed = sum(1 for k in range(i) if values[k] > values[i])
        shifts += passed
        comparisons += passed + (1 if passed < i else 0)
    return {"sorted": sorted(values), "comparisons": comparisons, "shifts": shifts}
