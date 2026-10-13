"""The baseline flow: which stage comes next, and what happens when the learner stops early.

Round brief §5.1. The order is fixed by the brief (crossword study → vault → machine → locked box →
delayed codewords → the rest) and this module is the only place that order lives.

Two things are worth stating because they are the reason this file exists at all:

* **The stage is derived, never stored as progress.** ``next_step`` reads the trial log and answers
  "what now?", so a learner who closes the tab on game three and returns tomorrow is served game
  three's next task from the same log. There is no cursor to get out of step with the data.
* **Stopping is normal.** A partial baseline is stored with what was measured and everything else
  marked "not measured" (brief §5.2). Nothing here tries to talk the learner out of stopping, and
  nothing invents a value for a game that was never played.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .games import (
    Trial,
    codewords_cued,
    codewords_delayed,
    codewords_next_day,
    codewords_study,
    locked_box_task,
    machine_next,
    quick_trial,
    shopkeeper_next,
    trap_task,
    vault_next,
)

#: The stages, in order. ``kind`` is what the screen shows; the position in this tuple is the
#: progress bar and the resumption order, so there is exactly one source for both.
STAGES: tuple[dict[str, Any], ...] = (
    {"key": "format", "kind": "choice", "title": "How you like to meet a new idea", "seconds": 20},
    {"key": "questions", "kind": "questions", "title": "How you work", "seconds": 150},
    {"key": "codewords_study", "kind": "study", "title": "Spy codewords: study", "seconds": 45},
    {"key": "codewords_cued", "kind": "game", "title": "Spy codewords: straight after", "seconds": 70},
    {"key": "quick_a", "kind": "game", "title": "Quick taps", "seconds": 20},
    {"key": "vault", "kind": "game", "title": "Vault lights", "seconds": 90},
    {"key": "shopkeeper", "kind": "game", "title": "Vault lights: shopkeeper", "seconds": 45},
    {"key": "quick_b", "kind": "game", "title": "Quick taps", "seconds": 20},
    {"key": "machine", "kind": "game", "title": "Crack the machine", "seconds": 150},
    {"key": "locked_box", "kind": "game", "title": "The locked box", "seconds": 120},
    {"key": "quick_c", "kind": "game", "title": "Quick taps", "seconds": 20},
    {"key": "trap", "kind": "game", "title": "Two questions to think about", "seconds": 30},
    {"key": "codewords_delayed", "kind": "game", "title": "Spy codewords: later", "seconds": 90},
    {"key": "snapshot", "kind": "snapshot", "title": "What we noticed", "seconds": 60},
)

STAGE_KEYS: tuple[str, ...] = tuple(s["key"] for s in STAGES)
QUICK_BUDGET = {"quick_a": 4, "quick_b": 4, "quick_c": 4}


@dataclass(frozen=True)
class Step:
    """What to show next: a task, a set of questions, or the snapshot."""

    kind: str  # task | questions | format | snapshot | done
    stage: str
    task: Mapping[str, Any] | None = None
    progress: float = 0.0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "stage": self.stage,
            "task": dict(self.task) if self.task else None,
            "progress": round(self.progress, 3),
            "note": self.note,
        }


def _index_of(stage: str) -> int:
    return STAGE_KEYS.index(stage) if stage in STAGE_KEYS else len(STAGE_KEYS)


def progress_for(stage: str) -> float:
    return _index_of(stage) / max(1, len(STAGE_KEYS))


def quick_index(trials: Sequence[Trial], stage: str) -> int:
    return len([t for t in trials if t.game == "quick" and t.task.get("stage") == stage])


def next_task_for(stage: str, *, seed: int, trials: Sequence[Trial]) -> dict[str, Any] | None:
    """The next task in one stage, or ``None`` when that stage has nothing left.

    Each generator is a pure function of the seed and the trials so far; this function only decides
    *which* generator a stage uses and stamps the stage on the quick trials so their later scoring
    can tell the three quick blocks apart (the drift between the first and last third is the
    measurement, brief §5.2).
    """
    if stage == "codewords_study":
        return codewords_study(seed, trials)
    if stage == "codewords_cued":
        return codewords_cued(seed, trials)
    if stage == "vault":
        forward = vault_next(seed, [t for t in trials if t.task.get("direction") == "forward"], "forward")
        if forward is not None:
            return forward
        return vault_next(seed, [t for t in trials if t.task.get("direction") == "backward"], "backward")
    if stage == "shopkeeper":
        return shopkeeper_next(seed, trials)
    if stage == "machine":
        for round_no in (1, 2, 3):
            task = machine_next(seed, trials, round_no)
            if task is not None:
                return task
        return None
    if stage == "locked_box":
        if any(t.game == "locked_box" for t in trials):
            # One puzzle per sitting. The measurement is the *first* attempt at something hard;
            # replaying it after seeing the answer would measure something else.
            already = [t for t in trials if t.game == "locked_box"]
            started = any(t.task.get("action") == "start" for t in already)
            finished = any(t.task.get("action") in ("walk_away", "solve") for t in already)
            if finished or (started and len(already) >= 6):
                return None
            if any(t.task.get("action") == "attempt" for t in already):
                return None
        task = locked_box_task(seed)
        if any(t.game == "locked_box" for t in trials):
            return None
        return task
    if stage in QUICK_BUDGET:
        used = quick_index(trials, stage)
        if used >= QUICK_BUDGET[stage]:
            return None
        task = quick_trial(used)
        task["id"] = f"{stage}_{used}"
        task["stage"] = stage
        return task
    if stage == "trap":
        done = [t for t in trials if t.game == "trap"]
        return None if done else trap_task(0)
    if stage == "codewords_delayed":
        free = codewords_delayed(seed, [t for t in trials if t.task.get("kind") == "free"], kind="free")
        if free is not None:
            # Pausing to say how well you think you will do, before a delayed recall: this is the
            # predicted-score half of "bet your coins" (brief §5.2).
            if not any(t.task.get("action") == "predict" for t in trials):
                return {
                    "id": "codewords_predict",
                    "game": "codewords_delayed",
                    "phase": "measure",
                    "action": "predict",
                    "prompt": "Before we check: how many of the eight pairs do you think you will remember, out of 8?",
                    "max_value": 8,
                    "max_latency_ms": 30000,
                }
            return free
        return codewords_delayed(seed, [t for t in trials if t.task.get("kind") in ("free", "recognition")], kind="recognition")
    return None


def next_step(*, seed: int, trials: Sequence[Trial], format_done: bool, questions_done: bool) -> Step:
    """Walk the stages in order and return the first one that still has something to show."""
    if not format_done:
        return Step(kind="format", stage="format", progress=0.0, note="one question before anything else")
    if not questions_done:
        return Step(kind="questions", stage="questions", progress=progress_for("questions"), note="")
    for stage in STAGE_KEYS:
        if stage in ("format", "questions", "snapshot"):
            continue
        task = next_task_for(stage, seed=seed, trials=trials)
        if task is not None:
            return Step(kind="task", stage=stage, task=task, progress=progress_for(stage))
    return Step(kind="snapshot", stage="snapshot", progress=1.0, note="every game has an ending")


def stage_report(trials: Sequence[Trial], *, seed: int, format_done: bool, questions_done: bool) -> dict[str, Any]:
    """Per-stage completion, for the resumption screen and for the snapshot's honesty line.

    A stage is ``complete`` when its generator has nothing left, ``partial`` when some trials exist,
    and ``not_started`` otherwise — and ``not_started`` is what the learner sees as "not measured"
    rather than as a zero (brief §5.4).
    """
    rows: list[dict[str, Any]] = []
    for stage in STAGE_KEYS:
        if stage in ("format", "questions", "snapshot"):
            done = {"format": format_done, "questions": questions_done, "snapshot": False}[stage]
            rows.append({"stage": stage, "state": "complete" if done else "not_started", "trials": 0})
            continue
        count = len([t for t in trials if _stage_of(t) == stage])
        state = "complete"
        if next_task_for(stage, seed=seed, trials=trials) is not None:
            state = "partial" if count else "not_started"
        rows.append({"stage": stage, "state": state, "trials": count})
    done = sum(1 for r in rows if r["state"] == "complete")
    return {
        "stages": rows,
        "complete": done,
        "total": len(rows),
        "played": [r["stage"] for r in rows if r["trials"]],
        "not_measured": [r["stage"] for r in rows if r["state"] != "complete"],
    }


def _stage_of(trial: Trial) -> str:
    """Which stage a trial belongs to. Two games share a stage name with a suffix (quick_a/b/c)."""
    game = trial.game
    if game == "quick":
        return str(trial.task.get("stage") or "quick_a")
    if game == "codewords_cued":
        return "codewords_cued"
    if game == "codewords_study":
        return "codewords_study"
    if game == "codewords_delayed":
        return "codewords_delayed"
    if game == "vault":
        return "vault"
    if game == "shopkeeper":
        return "shopkeeper"
    if game == "machine":
        return "machine"
    if game == "locked_box":
        return "locked_box"
    if game == "trap":
        return "trap"
    return game


def next_day_tasks(seed: int, trials: Sequence[Trial]) -> list[dict[str, Any]]:
    """The next-day codeword check, offered on the learner's next return (brief §5.2).

    It is a separate call rather than part of the flow: the point of the check is the *gap*, and a
    flow that ended yesterday cannot serve it today.
    """
    trials = [t for t in trials if t.game == "codewords_next_day"]
    out: list[dict[str, Any]] = []
    for _ in range(3):
        task = codewords_next_day(seed, [*trials, *[_trial_from(t) for t in out]])
        if task is None:
            break
        out.append(task)
    return out


def _trial_from(task: Mapping[str, Any]) -> Trial:
    return Trial(
        task_id=str(task.get("id")),
        game=str(task.get("game")),
        index=0,
        phase=str(task.get("phase") or "measure"),
        correct=None,
        task=dict(task),
    )


__all__ = [
    "QUICK_BUDGET",
    "STAGES",
    "STAGE_KEYS",
    "Step",
    "next_day_tasks",
    "next_step",
    "next_task_for",
    "progress_for",
    "stage_report",
]
