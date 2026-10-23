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
    QUICK_MAX_TRIALS,
    TRAPS,
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
    {"key": "quick_a", "kind": "game", "title": "Quick taps", "seconds": 30},
    {"key": "vault", "kind": "game", "title": "Vault lights", "seconds": 90},
    {"key": "shopkeeper", "kind": "game", "title": "Vault lights: shopkeeper", "seconds": 45},
    {"key": "quick_b", "kind": "game", "title": "Quick taps", "seconds": 30},
    {"key": "machine", "kind": "game", "title": "Crack the machine", "seconds": 150},
    {"key": "locked_box", "kind": "game", "title": "The locked box", "seconds": 120},
    {"key": "quick_c", "kind": "game", "title": "Quick taps", "seconds": 30},
    {"key": "trap", "kind": "game", "title": "Two questions to think about", "seconds": 30},
    {"key": "codewords_delayed", "kind": "game", "title": "Spy codewords: later", "seconds": 90},
    {"key": "snapshot", "kind": "snapshot", "title": "What we noticed", "seconds": 60},
)

STAGE_KEYS: tuple[str, ...] = tuple(s["key"] for s in STAGES)

#: The three laps of "Quick taps" and the ceiling on how many taps each can hold. The laps are
#: timed on the page (see :data:`QUICK_LAP_SECONDS`); the ceiling is the safety net for a lap whose
#: clock never reports back, so it is a *ceiling*, not the way the lap ends.
QUICK_BUDGET = {"quick_a": QUICK_MAX_TRIALS, "quick_b": QUICK_MAX_TRIALS, "quick_c": QUICK_MAX_TRIALS}

#: What the three laps call themselves on the page. Each lap is the same game, one rung harder.
QUICK_LAP_TITLES = {1: "lap 1 of 3", 2: "lap 2 of 3", 3: "lap 3 of 3"}


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
        # One puzzle per sitting, and three ways out: solve it, walk away, or run out of attempts.
        # The measurement is the *first* attempt at something hard — replaying it after seeing the
        # answer would measure something else — so the stage ends rather than looping. Every action
        # (a hint, an attempt, walking away) is its own trial and has its own task id.
        done = [t for t in trials if t.game == "locked_box"]
        if any(t.task.get("action") == "walk_away" for t in done) or any(t.correct for t in done):
            return None
        if len([t for t in done if t.task.get("action") == "attempt"]) >= 3:
            return None
        attempts = len([t for t in done if t.task.get("action") == "attempt"])
        bought = [str(t.response.get("hint_id")) for t in done if t.task.get("action") == "hint" and t.response.get("hint_id")]
        return locked_box_task(seed, len(done), attempts_used=attempts, bought_hints=bought)
    if stage in QUICK_BUDGET:
        done = [t for t in trials if t.game == "quick" and t.task.get("stage") == stage]
        # The lap is over when the page says the clock ran out (a `timeout` action), or when the
        # hard ceiling is reached. Either is a normal ending, not a failure.
        if any(str(t.task.get("action") or t.response.get("action") or "") == "timeout" for t in done):
            return None
        if len(done) >= QUICK_BUDGET[stage]:
            return None
        task = quick_trial(stage, len(done), seed)
        task["lap"] = 1 + tuple(QUICK_BUDGET).index(stage)
        task["lap_title"] = QUICK_LAP_TITLES[task["lap"]]
        return task
    if stage == "trap":
        # Two questions, one after the other, as the screen says. (Only the first was ever served.)
        done = [t for t in trials if t.game == "trap"]
        return trap_task(len(done)) if len(done) < len(TRAPS) else None
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
        # The snapshot is what the steps lead to, not one of them: thirteen steps, and "not measured"
        # is only ever said about a step.
        if stage == "snapshot":
            continue
        if stage in ("format", "questions"):
            done = {"format": format_done, "questions": questions_done}[stage]
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
