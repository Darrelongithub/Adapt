"""Combinational logic gates: inputs in, one output out, and the rule stated as it fires.

Nothing here is approximated: the gate table is the definition, and the test suite checks
every entry against Python's own operators. The frames show *why* the output is what it is
(a NAND is "AND, then invert") because that is the thing learners get wrong when they only
see a wire light up.

Sequential circuits are deliberately not here. A latch needs a clock model and a feedback
trace, and pretending otherwise would be an animation of the wrong subject.
"""

from __future__ import annotations

from typing import Any, Callable

from ._choice import order_choices

KIND = "logic_gates"
TITLE = "Logic gates: inputs, rule, output"
SUMMARY = (
    "Drive the inputs of one gate and watch the rule decide the output, with the whole truth "
    "table beside it so you can predict the next row before it is shown."
)
PROMPTS = ("output", "next_row")
MAX_FRAMES = 60

#: name -> (arity, python rule, the sentence the narration uses)
GATES: dict[str, tuple[int, Callable[..., int], str]] = {
    "AND": (2, lambda a, b: int(bool(a) and bool(b)), "high only when both inputs are high"),
    "OR": (2, lambda a, b: int(bool(a) or bool(b)), "high when at least one input is high"),
    "XOR": (2, lambda a, b: int(bool(a) != bool(b)), "high when the inputs differ"),
    "NAND": (2, lambda a, b: int(not (bool(a) and bool(b))), "AND, then inverted — low only when both are high"),
    "NOR": (2, lambda a, b: int(not (bool(a) or bool(b))), "OR, then inverted — high only when both are low"),
    "XNOR": (2, lambda a, b: int(bool(a) == bool(b)), "high when the inputs agree"),
    "NOT": (1, lambda a: int(not bool(a)), "the input, inverted"),
    "BUFFER": (1, lambda a: int(bool(a)), "the input, passed through"),
}
ARITY = {name: shape[0] for name, shape in GATES.items()}


PARAMS: dict[str, dict[str, Any]] = {
    "gate": {
        "type": "choice",
        "values": sorted(GATES),
        "default": "NAND",
        "label": "Gate",
        "help": "which gate to drive",
    },
    "walk": {
        "type": "bool",
        "default": True,
        "label": "Walk the whole truth table",
        "help": "off: only the inputs you set. on: every row, in counting order.",
    },
    "a": {"type": "bool", "default": 1, "label": "Input A", "help": "0 or 1"},
    "b": {"type": "bool", "default": 0, "label": "Input B", "help": "ignored by a single input gate"},
}


def normalize(params: dict[str, Any]) -> dict[str, Any]:
    gate = str(params.get("gate") or "NAND").upper()
    if gate not in GATES:
        raise ValueError(f"unknown gate {gate!r}")
    arity = GATES[gate][0]
    a = 1 if _bit(params.get("a", 1)) else 0
    b = 1 if _bit(params.get("b", 0)) else 0
    return {"gate": gate, "a": a, "b": b if arity == 2 else 0, "arity": arity, "walk": bool(params.get("walk", True))}


def _bit(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) else 0
    text = str(value).strip().lower()
    if text in ("1", "true", "high", "on", "yes"):
        return 1
    if text in ("0", "false", "low", "off", "no", ""):
        return 0
    raise ValueError(f"{value!r} is not a logic level")


def _rows(arity: int) -> list[tuple[int, int]]:
    if arity == 1:
        return [(0, 0), (1, 0)]
    return [(a, b) for b in (0, 1) for a in (0, 1)]


def _inputs(p: dict[str, Any], a: int, b: int) -> int:
    rule = GATES[p["gate"]][1]
    return int(rule(a, b)) if p["arity"] == 2 else int(rule(a))


def run(params: dict[str, Any]) -> list[dict[str, Any]]:
    p = normalize(params)
    gate = p["gate"]
    rule_says = GATES[gate][1]
    table = []
    for a, b in _rows(p["arity"]):
        out = int(rule_says(a, b)) if p["arity"] == 2 else int(rule_says(a))
        table.append({"a": a, "b": b if p["arity"] == 2 else None, "out": out})
    if p["walk"]:
        driving = [(row["a"], row["b"] or 0) for row in table]
    else:
        driving = [(p["a"], p["b"])]

    revealed = -1  # rows whose output the learner is allowed to see yet

    def table_view() -> list[dict[str, Any]]:
        # The table is the answer key. Until a row has been driven, its output is withheld --
        # otherwise the "predict the output" prompt is a reading-comprehension question, and a
        # learner could get it right without knowing anything about gates.
        return [dict(row, out=(row["out"] if idx <= revealed else None)) for idx, row in enumerate(table)]

    frames: list[dict[str, Any]] = [
        {
            "kind": "init",
            "title": f"{gate}: {GATES[gate][2]}",
            "narration": f"The {gate} gate is {GATES[gate][2]}. {'Its inputs are A and B.' if p['arity'] == 2 else 'It has one input, A.'}",
            "state": {"gate": gate, "table": table_view(), "a": p["a"], "b": p["b"], "out": None, "row": None},
            "highlights": [],
            "cause": None,
            "prompt": None,
        }
    ]
    for idx, (a, b) in enumerate(driving):
        out = int(rule_says(a, b)) if p["arity"] == 2 else int(rule_says(a))
        row_no = next(i for i, r in enumerate(table) if r["a"] == a and (r["b"] or 0) == (b if p["arity"] == 2 else 0))
        lhs = f"A={a}" + (f", B={b}" if p["arity"] == 2 else "")
        frames.append(
            {
                "kind": "drive",
                "title": f"{lhs} in",
                "narration": f"Drive {lhs} into the {gate}. Read the row before looking at the output.",
                "state": {"gate": gate, "table": table_view(), "a": a, "b": b if p["arity"] == 2 else None, "out": None, "row": row_no},
                "highlights": ["inputs"],
                "cause": f"{lhs} applied",
                "prompt": {
                    "type": "output",
                    "question": f"{gate} with {lhs}: what is the output?",
                    "choices": order_choices(KIND, len(frames), ["0 (low)", "1 (high)"]),
                },
            }
        )
        revealed = row_no
        frames.append(
            {
                "kind": "output",
                "title": f"{lhs} -> {out}",
                "narration": f"{lhs} gives {out}, because a {gate} gate is {GATES[gate][2]}.",
                "state": {"gate": gate, "table": table_view(), "a": a, "b": b if p["arity"] == 2 else None, "out": out, "row": row_no},
                "highlights": ["output"],
                "cause": f"{gate}({', '.join(str(x) for x in ([a, b] if p['arity'] == 2 else [a]))}) = {out}",
                "prompt": {
                    "type": "next_row",
                    "question": "Next row in counting order: what comes out?",
                    "choices": order_choices(KIND, len(frames), ["0 (low)", "1 (high)"]),
                }
                if p["walk"] and idx + 1 < len(driving)
                else None,
            }
        )
    frames.append(
        {
            "kind": "done",
            "title": f"{gate} truth table, complete",
            "narration": "; ".join(
                ("A=" + str(r["a"]) + (", B=" + str(r["b"]) if r["b"] is not None else "") + " gives " + str(r["out"])) for r in table
            ) + ".",
            "state": {"gate": gate, "table": table_view(), "a": p["a"], "b": p["b"], "out": None, "row": None},
            "highlights": [],
            "cause": None,
            "prompt": None,
        }
    )
    return frames


def expected(params: dict[str, Any], frame_index: int) -> str | None:
    frames = run(normalize(params))
    if not (0 <= frame_index < len(frames)):
        return None
    frame = frames[frame_index]
    prompt = frame.get("prompt") or {}
    if prompt.get("type") == "output":
        out = (frame["state"] or {}).get("out")
        nxt = frames[frame_index + 1] if frame_index + 1 < len(frames) else None
        out = (nxt or frame)["state"].get("out")
        return None if out is None else f"{out} ({'high' if out else 'low'})"
    if prompt.get("type") == "next_row":
        for later in frames[frame_index + 1 :]:
            if later["kind"] == "output":
                out = later["state"].get("out")
                return None if out is None else f"{out} ({'high' if out else 'low'})"
        return None
    return None


def grade(params: dict[str, Any], frame_index: int, choice: Any) -> dict[str, Any]:
    want = expected(params, frame_index)
    given = str(choice or "").strip()
    ok = want is not None and given == want
    why = ""
    if want is not None:
        why = f"A {normalize(params)['gate']} gate is {GATES[normalize(params)['gate']][2]}, so that row gives {want}."
    return {"correct": ok, "expected": want, "said": given, "why": why, "frame": frame_index, "state": {}}


def explain(params: dict[str, Any], frame_index: int, question: str = "") -> dict[str, Any] | None:
    """Every "why" about a gate is in its definition, so no model call is needed."""
    q = (question or "").lower()
    p = normalize(params)
    frames = run(p)
    if not (0 <= frame_index < len(frames)):
        return None
    frame = frames[frame_index]
    gate = p["gate"]
    state = frame["state"]
    out = state.get("out")
    if "why" in q or "because" in q or gate.lower() in q:
        answer = f"A {gate} gate is {GATES[gate][2]}."
        if out is not None:
            answer += f" With A={state['a']}" + (f" and B={state['b']}" if p["arity"] == 2 else "") + f", that gives {out}."
        if "nand" in q or "universal" in q:
            answer += " NAND (and NOR) are functionally complete: every other gate, including NOT, can be built from them alone."
        return {"answer": answer, "source": "gate_rule", "frame": frame_index}
    if "complete" in q or "nand" in q or "nor" in q:
        return {
            "answer": "Yes for NAND and NOR: both are functionally complete. NOT is NAND with its inputs tied together, AND is NAND followed by a NOT, and OR is a NAND of two inverted inputs.",
            "source": "gate_rule",
            "frame": frame_index,
        }
    return None


def truth(params: dict[str, Any]) -> dict[str, Any]:
    """Every gate, every row, computed from Python's own operators."""
    out = {}
    for name, (arity, rule, _) in GATES.items():
        rows = []
        for a, b in _rows(arity):
            rows.append({"a": a, "b": b if arity == 2 else None, "out": int(rule(a, b)) if arity == 2 else int(rule(a))})
        out[name] = rows
    return out
