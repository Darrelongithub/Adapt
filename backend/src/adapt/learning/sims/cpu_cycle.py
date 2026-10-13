"""The fetch-decode-execute cycle, on a machine small enough to check by hand.

A CPU animation is where an engine can most easily lie: it is tempting to draw arrows that do
not correspond to any register transfer. So this module defines a real, tiny machine -- one
accumulator, ``PC``, ``MAR``, ``MDR``, ``IR``, a zero flag, and **one flat memory that holds
the instructions and the data in the same array**. That last part is the lesson: the fetch
reads a cell and does not know whether it is code or data, which is exactly why a program can
be treated as data and why protection exists later.

Two rules follow from that design, and both are enforced here rather than assumed:

* an arithmetic or memory operand (LOAD/STORE/ADD/SUB) must point at a **data cell**, at or
  past ``data_at`` -- otherwise the machine loads the text of an instruction as a number;
* a jump operand must land **inside the program, or one past its end**. One past the end is a
  real and instructive outcome: this machine has nothing else, so the only ways to stop are
  ``HLT``, running off the end, and the step limit.

:func:`truth` runs the same program through a second, deliberately plain interpreter with no
animation in it; the test suite requires the two to agree on random programs.
"""

from __future__ import annotations

import re
from typing import Any

from ._choice import order_choices

KIND = "cpu_cycle"
TITLE = "The CPU instruction cycle: fetch, decode, execute"
SUMMARY = (
    "Step a program through a one accumulator machine and watch PC, MAR, MDR and IR take their "
    "turn in every instruction — one memory for code and data, and no way to stop but HLT."
)
PROMPTS = ("next_register", "accumulator_after", "memory_after", "pc_after")
MAX_FRAMES = 240
MIN_LINES, MAX_LINES = 1, 8
MEMORY_CAP = 32

#: the whole instruction set -- the validator refuses a program that uses anything else
ISA: dict[str, str] = {
    "LOAD": "A <- M[arg]",
    "STORE": "M[arg] <- A",
    "ADD": "A <- A + M[arg]",
    "SUB": "A <- A - M[arg]",
    "JMP": "PC <- arg",
    "JZ": "PC <- arg when A is 0",
    "HLT": "stop fetching",
}
_LINE = re.compile(r"^(LOAD|STORE|ADD|SUB|JMP|JZ)\s+(\d+)$|^HLT$", re.IGNORECASE)


def parse(line: str) -> tuple[str, int | None]:
    m = _LINE.match(str(line).strip())
    if not m:
        raise ValueError(f"{line!r} is not in this machine's instruction set ({', '.join(ISA)})")
    if m.group(1) is None:
        return "HLT", None
    return m.group(1).upper(), int(m.group(2))


PARAMS: dict[str, dict[str, Any]] = {
    "program": {
        "type": "lines",
        "min_items": MIN_LINES,
        "max_items": MAX_LINES,
        "default": None,
        "label": "Program",
        "help": f"one instruction per line, up to {MAX_LINES}: {', '.join(ISA)}",
    },
    "data": {
        "type": "ints",
        "min_items": 1,
        "max_items": MEMORY_CAP,
        "min": -999,
        "max": 999,
        "default": None,
        "label": "Data cells",
        "help": "the numbers the program reads and writes, in cells right after the program",
    },
    "max_steps": {"type": "int", "min": 1, "max": 40, "default": 12, "label": "Stop after this many instructions", "help": "a loop that never ends is cut off, and says so"},
}

DEFAULT_PROGRAM = ["LOAD 4", "ADD 5", "STORE 6", "HLT"]
DEFAULT_DATA = [7, 5, 0]


def normalize(params: dict[str, Any]) -> dict[str, Any]:
    """Canonical cells + the two machine rules, checked before any frame is drawn."""
    program = [str(x).strip().upper() for x in (params.get("program") or DEFAULT_PROGRAM)]
    for line in program:
        parse(line)
    data = [int(v) for v in (params.get("data") or DEFAULT_DATA)]
    data_at = len(program)
    cells = list(program) + data
    if len(cells) > MEMORY_CAP:
        raise ValueError(f"program plus data must fit in {MEMORY_CAP} cells")

    for index, line in enumerate(program):
        op, arg = parse(line)
        if op == "HLT" or arg is None:
            continue
        if op in ("JMP", "JZ"):
            if not (0 <= arg <= data_at):
                raise ValueError(
                    f"line {index} ({line}): a jump has to land inside the program (0-{data_at - 1}) or one past "
                    f"its end ({data_at}, which stops the machine). {arg} lands in the data, which is not code."
                )
        else:
            if not (data_at <= arg < len(cells)):
                raise ValueError(
                    f"line {index} ({line}): {op} has to address a data cell (this machine's data starts at cell "
                    f"{data_at}, and cell {arg} holds {'the instruction being executed' if arg < data_at else 'nothing yet'})."
                )
    return {
        "program": program,
        "data": data,
        "cells": cells,
        "data_at": data_at,
        "max_steps": int(params.get("max_steps") or 12),
    }


def _regs(p: dict[str, Any], cells: list[Any], *, pc: int, a: int, mar: Any, mdr: Any, ir: Any, z: bool, cycle: int) -> dict[str, Any]:
    return {
        "pc": pc,
        "a": a,
        "mar": mar,
        "mdr": mdr,
        "ir": ir,
        "z": bool(z),
        "cells": list(cells),
        "program": list(p["program"]),
        "data_at": p["data_at"],
        "cycle": cycle,
    }


def run(params: dict[str, Any]) -> list[dict[str, Any]]:
    p = normalize(params)
    cells: list[Any] = list(p["cells"])
    program = p["program"]
    pc, a, z = 0, 0, False
    cycle = 0
    n_instr = len(program)
    frames: list[dict[str, Any]] = [
        {
            "kind": "init",
            "title": "One memory: instructions, then data",
            "narration": (
                f"Cells 0-{n_instr - 1} hold the program; the data starts at cell {p['data_at']}. The machine reads "
                "the same memory for both, which is what 'stored program' means — and why it has no idea whether a "
                "cell is code."
            ),
            "state": _regs(p, cells, pc=pc, a=a, mar=None, mdr=None, ir=None, z=z, cycle=0),
            "highlights": [],
            "cause": None,
            "prompt": None,
        }
    ]

    for _ in range(max(1, p["max_steps"])):
        if pc < 0 or pc >= n_instr:
            frames.append(
                {
                    "kind": "off_end",
                    "title": "PC walked past the program",
                    "narration": (
                        f"PC is {pc}, past the last instruction at {n_instr - 1}. Nothing is left to fetch, so the "
                        "machine stops. It had no HLT to run — that is a way to end a program, and a bad one."
                    ),
                    "state": _regs(p, cells, pc=pc, a=a, mar=None, mdr=None, ir=None, z=z, cycle=cycle),
                    "highlights": ["pc"],
                    "cause": "pc past the program",
                    "prompt": None,
                }
            )
            return frames

        word = cells[pc]
        op, arg = parse(str(word))
        cycle += 1
        frames.append(
            {
                "kind": "fetch",
                "title": f"Cycle {cycle} · fetch",
                "narration": (
                    f"MAR <- {pc}. The memory read puts {'the instruction ' + str(word) if isinstance(word, str) else str(word)} "
                    f"in MDR, which latches into IR. PC becomes {pc + 1} as part of the fetch, before the instruction "
                    "has done anything."
                ),
                "state": _regs(p, cells, pc=pc + 1, a=a, mar=pc, mdr=word, ir=str(word), z=z, cycle=cycle),
                "highlights": ["mar", "mdr", "ir"],
                "cause": f"fetched cell {pc}",
                "prompt": {
                    "type": "next_register",
                    "question": f"IR holds '{word}'. What does the machine do next?",
                    "choices": order_choices(KIND, len(frames), ["split it into opcode and operand", "do the memory write", "add 1 to MAR"]),
                },
            }
        )
        frames.append(
            {
                "kind": "decode",
                "title": f"Cycle {cycle} · decode",
                "narration": f"The opcode is {op}, the operand is {arg if arg is not None else 'nothing'}. {ISA[op]}.",
                "state": _regs(p, cells, pc=pc + 1, a=a, mar=pc, mdr=word, ir=str(word), z=z, cycle=cycle),
                "highlights": ["ir"],
                "cause": f"decode {op}",
                # asked with the instruction decoded but not yet run: whatever the answer is
                # (a register, PC, or a memory cell) is a fact about the *next* phase, and
                # nothing on this frame has stated it yet
                "prompt": _decode_prompt(op, arg, a, cells, pc, len(frames)),
            }
        )

        before, note = a, ""
        next_pc = pc + 1
        if op == "HLT":
            frames.append(
                {
                    "kind": "execute",
                    "title": f"Cycle {cycle} · halt",
                    "narration": f"HLT stops the fetch, decode, execute loop. A is {a} and the data cells read {cells[p['data_at']:]}.",
                    "state": _regs(p, cells, pc=pc + 1, a=a, mar=pc, mdr=word, ir=str(word), z=z, cycle=cycle),
                    "highlights": ["a"],
                    "cause": "halt",
                    "prompt": None,
                }
            )
            frames.append(
                {
                    "kind": "done",
                    "title": "Halted",
                    "narration": f"{cycle} instruction{'s' if cycle != 1 else ''} executed; A = {a}.",
                    "state": _regs(p, cells, pc=pc + 1, a=a, mar=pc, mdr=word, ir=None, z=z, cycle=cycle),
                    "highlights": [],
                    "cause": f"{cycle} cycles",
                    "prompt": None,
                }
            )
            return frames
        if op == "LOAD":
            a = int(cells[arg])
            note = f"A <- M[{arg}] = {a}"
        elif op == "STORE":
            cells[arg] = a
            note = f"M[{arg}] <- A = {a}"
        elif op == "ADD":
            a = a + int(cells[arg])
            note = f"A <- A + M[{arg}] = {a}"
        elif op == "SUB":
            a = a - int(cells[arg])
            note = f"A <- A - M[{arg}] = {a}"
        elif op == "JMP":
            next_pc = arg
            note = f"PC <- {arg}"
        elif op == "JZ":
            taken = a == 0
            next_pc = arg if taken else pc + 1
            note = f"A is {a}, so the jump is {'taken: PC <- ' + str(arg) if taken else 'not taken'}"
        z = a == 0
        frames.append(
            {
                "kind": "execute",
                "title": f"Cycle {cycle} · execute",
                "narration": (
                    f"{note}. "
                    + (f"A went from {before} to {a}. " if a != before else "A is unchanged. ")
                    + (f"The zero flag is now {'set' if z else 'clear'}. " if op != "JMP" else "")
                    + f"The next fetch uses PC = {next_pc}."
                ),
                "state": _regs(p, cells, pc=next_pc, a=a, mar=pc, mdr=word, ir=str(word), z=z, cycle=cycle),
                "highlights": ["a"] if op in ("LOAD", "ADD", "SUB") else (["cells"] if op == "STORE" else ["pc"]),
                "cause": note,
                "prompt": None,
            }
        )
        pc = next_pc

    frames.append(
        {
            "kind": "limit",
            "title": "Cut off at the step limit",
            "narration": (
                f"This program had not halted after {p['max_steps']} instructions, so the animation stops here rather "
                "than pretending to finish. A machine in a loop looks exactly like a machine working, which is why a "
                "watchdog — this limit — is part of the story."
            ),
            "state": _regs(p, cells, pc=pc, a=a, mar=None, mdr=None, ir=None, z=z, cycle=cycle),
            "highlights": [],
            "cause": "step limit",
            "prompt": None,
        }
    )
    return frames


def _decode_prompt(op: str, arg: int | None, a: int, cells: list[Any], pc: int, frame_index: int) -> dict[str, Any] | None:
    """The one question worth asking after a decode, chosen by what the instruction does."""
    if op == "HLT":
        return None
    if op in ("LOAD", "ADD", "SUB"):
        return {
            "type": "accumulator_after",
            "question": f"IR holds '{op} {arg}'. What will A be once this has executed?",
            "choices": order_choices(KIND, frame_index, [str(x) for x in _accumulator_candidates(op, arg, a, cells)]),
        }
    if op == "STORE":
        held = cells[arg] if arg is not None and arg < len(cells) else 0
        return {
            "type": "memory_after",
            "question": f"STORE will overwrite cell {arg}. What will that cell hold afterwards?",
            "choices": order_choices(KIND, frame_index, [str(a), str(held), "unchanged"]),
        }
    if op == "JMP":
        return {
            "type": "pc_after",
            "question": "This is an unconditional jump. Which cell does the next fetch read?",
            "choices": order_choices(KIND, frame_index, [f"cell {arg}", f"cell {pc + 1}", "it stops"]),
        }
    if op == "JZ":
        return {
            "type": "pc_after",
            "question": f"A is {a} and this is a jump if zero. Which cell does the next fetch read?",
            "choices": order_choices(KIND, frame_index, [f"cell {arg}", f"cell {pc + 1}", "it stops"]),
        }
    return None


def _accumulator_candidates(op: str, arg: int | None, a: int, cells: list[Any]) -> list[int]:
    """The right answer plus the two mistakes that are actually tempting."""
    value = int(cells[arg]) if arg is not None and isinstance(cells[arg], int) else 0
    if op == "LOAD":
        return [value, a, a + value]
    if op == "ADD":
        return [a + value, a, value]
    if op == "SUB":
        return [a - value, a, a + value]
    if op == "STORE":
        return [a, value, 0]
    if op == "JZ":
        return [a, 0, value]
    return [a]


def _after_decode(frames: list[dict[str, Any]], frame_index: int) -> dict[str, Any] | None:
    """The state the decode frame is predicting -- the next frame that has *done* something."""
    for later in frames[frame_index + 1 :]:
        if later["kind"] in ("execute", "off_end", "done", "limit"):
            return later["state"]
    return None


def expected(params: dict[str, Any], frame_index: int) -> str | None:
    """What the execute phase of this cycle really leaves behind, read off the trace."""
    frames = run(normalize(params))
    if not (0 <= frame_index < len(frames)):
        return None
    frame = frames[frame_index]
    prompt = frame.get("prompt") or {}
    kind = prompt.get("type")
    if kind == "next_register":
        return "split it into opcode and operand"
    state = _after_decode(frames, frame_index)
    if state is None:
        return None
    if kind == "accumulator_after":
        return str(state["a"])
    if kind == "memory_after":
        arg = parse(str(state["ir"] or ""))[1]
        return str(state["cells"][arg]) if arg is not None and arg < len(state["cells"]) else None
    if kind == "pc_after":
        nxt = state["pc"]
        return f"cell {nxt}" if nxt < len(state["cells"]) else "it stops"
    return None


def grade(params: dict[str, Any], frame_index: int, choice: Any) -> dict[str, Any]:
    frames = run(normalize(params))
    if not (0 <= frame_index < len(frames)):
        return {"correct": False, "expected": None, "said": str(choice or ""), "why": "that frame does not exist", "frame": frame_index, "state": {}}
    frame = frames[frame_index]
    want = expected(params, frame_index)
    given = str(choice or "").strip()
    prompt_type = (frame.get("prompt") or {}).get("type")
    if prompt_type == "next_register":
        why = "Fetch fills MAR, MDR and IR; splitting the opcode from the operand is what decode does next."
    elif prompt_type == "accumulator_after":
        why = f"The execute phase of {frame['state'].get('ir')} leaves A = {want}. A store or a jump would leave it alone."
    elif prompt_type == "memory_after":
        why = f"After this instruction, that cell holds {want} — a store copies A into it, it does not clear it."
    elif prompt_type == "pc_after":
        why = f"The next fetch reads {want}: a jump writes PC inside the execute phase, which is why PC moved twice in this cycle."
    else:
        why = "This frame asks nothing."
    return {
        "correct": want is not None and given == want,
        "expected": want,
        "said": given,
        "why": why,
        "frame": frame_index,
        "state": frame["state"],
        "prompt": frame.get("prompt"),
    }


def explain(params: dict[str, Any], frame_index: int, question: str = "") -> dict[str, Any] | None:
    """The parts of this machine that a frame can already account for."""
    q = (question or "").lower()
    p = normalize(params)
    frames = run(p)
    if not (0 <= frame_index < len(frames)):
        return None
    frame = frames[frame_index]
    state = frame["state"]
    if "why" in q and ("pc" in q or "program counter" in q or "next instruction" in q):
        return {
            "answer": (
                "PC is incremented during the fetch, before execute, because the address of the next instruction has "
                "to be safe by the time this one changes A or jumps. A jump does not need an extra fetch: it is just "
                "a write to PC at the end of execute."
            ),
            "source": "machine_rule",
            "frame": frame_index,
        }
    if "mar" in q or "mdr" in q or "latch" in q or "register" in q:
        return {
            "answer": (
                f"MAR holds the address being looked at ({state['mar']}) and MDR holds what came back ({state['mdr']}). "
                "They exist because memory is slower than registers: the address goes out, the word comes back, IR "
                "latches it, and the rest of the cycle no longer depends on memory."
            ),
            "source": "machine_rule",
            "frame": frame_index,
        }
    if "same" in q or "one memory" in q or "von" in q or "store" in q:
        return {
            "answer": (
                f"One array: cells 0-{len(p['program']) - 1} are instructions and the data begins at cell {p['data_at']}. "
                "The fetch does not distinguish them, which is what makes a self modifying program possible — and what "
                "makes 'execute' permissions a thing later."
            ),
            "source": "machine_rule",
            "frame": frame_index,
        }
    if "loop" in q or "halt" in q or "stop" in q or "run off" in q:
        return {
            "answer": (
                f"This machine ends only by HLT, by PC walking past instruction {len(p['program']) - 1}, or by the step "
                f"limit of {p['max_steps']}. A JZ whose test never becomes true keeps cycling, and the animation says "
                "so instead of inventing an ending."
            ),
            "source": "machine_rule",
            "frame": frame_index,
        }
    return None


def truth(params: dict[str, Any]) -> dict[str, Any]:
    """The same program, run by a second interpreter that knows nothing about frames."""
    p = normalize(params)
    cells: list[Any] = list(p["cells"])
    n_instr = len(p["program"])
    pc, a, z, cycles = 0, 0, False, 0
    status = "halted"
    for _ in range(max(1, p["max_steps"])):
        if pc < 0 or pc >= n_instr:
            status = "off_end"
            break
        op, arg = parse(str(cells[pc]))
        cycles += 1
        if op == "HLT":
            status = "halted"
            break
        if op == "LOAD":
            a = int(cells[arg])
        elif op == "STORE":
            cells[arg] = a
        elif op == "ADD":
            a += int(cells[arg])
        elif op == "SUB":
            a -= int(cells[arg])
        elif op == "JMP":
            pc = arg
            z = a == 0
            continue
        elif op == "JZ":
            pc = arg if a == 0 else pc + 1
            z = a == 0
            continue
        z = a == 0
        pc += 1
    else:
        status = "limit"
    return {
        "a": a,
        "cells": cells,
        "cycles": cycles,
        "status": status,
        "zero": z,
        "data": [c if isinstance(c, int) else 0 for c in cells[p["data_at"] :]],
    }
