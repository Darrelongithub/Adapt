"""The baseline service: the run lifecycle on top of the flow and the games.

This is the layer the API talks to. It owns three things:

* the **run row** (who, which seed, where they are, whether they finished or stopped);
* **judging a response** — dispatching to the right scorer, in code, never through a model;
* **sealing the run** into an immutable mind snapshot, and publishing the same trials into the
  shared evidence log so the rest of the app can use a partial baseline (brief §2A + §5).

The seed is the run's, stored on the row. Every codeword and every machine rule derives from it, so
a learner who moves from a phone to a laptop mid-baseline is asked the same things.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, Sequence

from ..db.models import BaselineRun, BaselineTrial, MindSnapshot, utcnow
from . import factors as F
from . import flow
from .games import (
    Trial,
    choice_score,
    locked_box_score,
    machine_respond,
    machine_switched,
    recall_score,
    seed_of,
    shopkeeper_score,
    trial_action,
    vault_score,
    wager_allowed,
)

log = logging.getLogger("adapt.baseline")


class BaselineError(RuntimeError):
    """Something the caller did that the flow will not accept."""


#: Questions asked before the games (brief §5.3). Each one records *why* it is asked, because a
#: preference with no downstream use is a form nobody should have to fill in. ``options`` are the
#: choices the screen offers; ``multi`` marks the ones where more than one answer is allowed.
QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "goal",
        "question": "What are you learning this for?",
        "options": ["an exam with a date", "a course I am taking", "work or a job", "just curious"],
        "why": "decides what the app calls progress",
    },
    {
        "key": "session_minutes",
        "question": "How long would you like one sitting to be?",
        "options": [10, 20, 30, 45],
        "why": "sizes a lesson to a sitting you actually have",
    },
    {
        "key": "study_days",
        "question": "How many days a week can you study?",
        "options": [1, 2, 3, 4, 5, 6, 7],
        "why": "sets the review schedule",
    },
    {
        "key": "study_when",
        "question": "When do you usually study?",
        "options": ["early morning", "midday", "afternoon", "evening", "late night", "it varies"],
        "why": "times reminders, and never sends them at the wrong hour",
    },
    {
        "key": "motivation",
        "question": "What keeps you going?",
        "options": ["seeing progress", "a streak", "competing", "curiosity", "finishing things"],
        "multi": True,
        "why": "picks what the progress screen shows first",
    },
    {
        "key": "quit_trigger",
        "question": "What makes you stop?",
        "options": ["boredom", "being stuck", "no time", "feeling behind"],
        "multi": True,
        "why": "tells the app what to notice and work around",
    },
    {
        "key": "feedback_style",
        "question": "How do you want to hear that you got something wrong?",
        "options": ["straight", "gentle", "just show the answer"],
        "why": "sets the tone of every correction",
    },
    {
        "key": "challenge_first",
        "question": "Would you rather start with something hard, or something you can do?",
        "options": ["challenge me first", "safe success first"],
        "why": "sets the opening difficulty of a lesson",
    },
    {
        "key": "when_stuck",
        "question": "What do you do when you are stuck?",
        "options": ["ask someone", "search for it", "keep pushing", "move on and come back"],
        "why": "compared later with what you actually did in the locked box",
    },
    {
        "key": "device_context",
        "question": "Where and on what do you study?",
        "options": ["phone", "laptop", "shared device", "limited data", "often offline"],
        "multi": True,
        "why": "decides how light the pages are and whether things must work offline",
    },
    {
        "key": "language",
        "question": "Which language should explanations be in?",
        "options": ["English", "Kiswahili", "mixed"],
        "why": "the language of every explanation",
    },
    {
        "key": "timers_help",
        "question": "Do you like seeing a timer, or does it stress you?",
        "options": ["timers help", "timers stress me"],
        "why": "shows or hides the clock; the app still measures quietly either way",
    },
    {
        "key": "anything_else",
        "question": "Anything else about how you learn? (one line, optional)",
        "options": [],
        "free_text": True,
        "why": "read by you later, and shown next to your settings",
    },
)

#: The format pick, asked before any game (brief §5.1 step 2). It is a *stated* choice: it would be
#: a learning-style label if it decided anything by itself, so it does not — it sets a starting
#: point that is compared with measured performance later (see ``factors.preference_vs_performance``).
FORMAT_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "diagram", "label": "a diagram", "hint": "a picture or graph of the idea"},
    {"value": "text", "label": "a written explanation", "hint": "a short paragraph that explains it"},
    {"value": "worked_example", "label": "a worked example", "hint": "one worked through, step by step"},
)

#: Preferences whose answers map straight onto the existing preference table. The rest are stored
#: here on the run and surfaced in the snapshot, because the settings screen does not own them yet.
PREFERENCE_MAP = {
    "goal": "goal",
    "session_minutes": "session_minutes",
    "language": "language",
    "timers_help": "timers_help",
    "feedback_style": "feedback_style",
    "preferred_format": "preferred_format",
    "device_context": "device_context",
    "study_days": "study_days",
    "study_when": "study_when",
    "motivation": "motivation",
    "quit_trigger": "quit_trigger",
    "challenge_first": "challenge_first",
    "when_stuck": "when_stuck",
    "anything_else": "anything_else",
}


def normalise_answer(key: str, value: Any) -> Any:
    """Coerce an answer into the shape the rest of the app expects, or refuse it.

    Free text is trimmed and clamped; the language names the app actually speaks are mapped from
    what a person would tap ("Kiswahili" → ``sw``). Anything unknown is refused rather than stored:
    a preference the app cannot honour is worse than no answer, because the learner will believe it
    was applied.
    """
    if key == "anything_else":
        text = " ".join(str(value or "").split())
        return text[:280]
    if key == "language":
        raw = str(value or "").strip().lower()
        return {"english": "en", "en": "en", "kiswahili": "sw", "sw": "sw", "mixed": "mixed"}.get(raw, "en")
    if key == "timers_help":
        raw = str(value or "").strip().lower()
        if raw in ("timers help", "help", "yes", "true", "1"):
            return True
        if raw in ("timers stress me", "stress", "no", "false", "0"):
            return False
        raise BaselineError(f"timers_help: {value!r} is not an answer this question offers")
    if key == "session_minutes":
        try:
            minutes = int(value)
        except (TypeError, ValueError) as exc:
            raise BaselineError("session_minutes needs a number of minutes") from exc
        if minutes not in (10, 20, 30, 45):
            raise BaselineError(f"session_minutes: {minutes} is not one of the offered lengths")
        return minutes
    if key in ("motivation", "quit_trigger", "device_context"):
        items = value if isinstance(value, (list, tuple)) else [value]
        allowed = {str(o).lower() for o in next(q for q in QUESTIONS if q["key"] == key)["options"]}
        out = [str(x).strip().lower() for x in items if str(x).strip().lower() in allowed]
        if not out:
            raise BaselineError(f"{key}: none of {items!r} is one of the offered answers")
        return out
    if key == "goal":
        allowed = {str(o).lower() for o in next(q for q in QUESTIONS if q["key"] == "goal")["options"]}
        raw = str(value or "").strip().lower()
        if raw not in allowed:
            raise BaselineError(f"goal: {value!r} is not one of the offered answers")
        return raw
    return str(value or "").strip()[:120]


def _public_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Project a generated task into the stimulus the learner needs, not the answer key."""
    out = dict(task)
    game = str(out.get("game") or "")
    if game in ("quick", "trap", "codewords_cued", "codewords_delayed", "codewords_next_day", "locked_box"):
        out.pop("answer", None)
    if game == "trap":
        out.pop("intuitive", None)
        out.pop("explain", None)
    if game == "machine":
        # The third round's switch is supposed to be silent.
        out.pop("switches", None)
    if game == "shopkeeper":
        out["distractors"] = [
            {key: value for key, value in dict(row).items() if key != "answer"}
            for row in out.get("distractors", [])
        ]
    return out


def _public_score(task: Mapping[str, Any], scored: Mapping[str, Any]) -> dict[str, Any]:
    """Do not let a task response disclose a hidden rule or a solution before the learner acts."""
    game = str(task.get("game") or "")
    action = str(scored.get("action") or task.get("action") or "")
    if task.get("phase") == "practice":
        if game == "machine":
            return {key: scored[key] for key in ("action", "value", "output") if key in scored}
        if game == "locked_box":
            return {"action": action} if action else {}
        return {key: scored[key] for key in ("action", "seconds_seen") if key in scored}
    if game == "machine":
        keys = ("action", "value", "output") if action == "probe" else ("action", "given", "correct")
        return {key: scored[key] for key in keys if key in scored}
    if game == "locked_box":
        if action == "hint":
            return {key: scored[key] for key in ("action", "cost", "hint") if key in scored}
        if action == "attempt":
            keys = ("action", "given", "correct")
            if scored.get("correct") is True:
                keys += ("expected",)
            return {key: scored[key] for key in keys if key in scored}
        return {"action": action} if action else {}
    return dict(scored)


def _public_history_row(trial: Trial) -> dict[str, Any]:
    """Small, answer-safe history needed to resume multi-action games on another device."""
    stored = trial.response if isinstance(trial.response, Mapping) else {}
    raw_score = stored.get("scored", {})
    raw_score = raw_score if isinstance(raw_score, Mapping) else {}
    action = trial_action(trial)
    score = _public_score(trial.task, raw_score)
    row: dict[str, Any] = {
        "task_id": trial.task_id,
        "game": trial.game,
        "phase": trial.phase,
        "action": action,
        "correct": trial.correct,
        "latency_ms": trial.latency_ms,
        "wager": trial.wager,
        "scored": score,
    }
    if trial.game == "machine":
        if action == "probe":
            row["input"] = score.get("value")
            row["output"] = score.get("output")
        elif action == "guess":
            row["guess"] = str(stored.get("text") or "")
    if trial.game == "locked_box" and action == "hint":
        row["hint_id"] = stored.get("hint_id")
        row["hint"] = score.get("hint")
        row["cost"] = score.get("cost")
    return row


class Baseline:
    """The run lifecycle. Constructed with a session factory and the shared state store."""

    def __init__(self, sf: Callable[[], Any], *, state: Any = None, settings: Any = None) -> None:
        self.sf = sf
        self.state = state
        self.settings = settings

    @contextmanager
    def _sess(self) -> Iterator[Any]:
        """A session that commits on the way out and always closes.

        ``with self.sf() as s`` would *not* do this: SQLAlchemy's session context manager closes the
        session and rolls back anything uncommitted, so a run created inside one existed only until
        the request ended. That mistake is why every write in this service goes through here.
        """
        s = self.sf()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ------------------------------------------------------------------ #
    # runs
    # ------------------------------------------------------------------ #
    def start(self, student_id: str, *, seed: int | None = None) -> dict[str, Any]:
        """Open a run (or hand back the unfinished one — no learner should get two baselines)."""
        with self._sess() as s:
            existing = (
                s.query(BaselineRun)
                .filter(BaselineRun.student_id == student_id, BaselineRun.status == "in_progress")
                .order_by(BaselineRun.started_at.desc())
                .first()
            )
            resumed = existing is not None
            if existing is not None:
                run = existing
            else:
                run = BaselineRun(
                    student_id=student_id,
                    seed=int(seed if seed is not None else seed_of(student_id, "baseline", utcnow().isoformat())),
                    status="in_progress",
                    stage="format",
                    completion={},
                )
                s.add(run)
                s.flush()
            run_id = run.id
        out = self.view(student_id, run_id)
        out["resumed"] = resumed
        return out

    def _run(self, s: Any, run_id: str) -> BaselineRun:
        run = s.get(BaselineRun, run_id)
        if run is None:
            raise BaselineError(f"unknown baseline run {run_id!r}")
        return run

    def trials_of(self, run_id: str, *, session: Any = None) -> list[Trial]:
        own = session is None
        s = session or self.sf()
        try:
            rows = (
                s.query(BaselineTrial)
                .filter(BaselineTrial.run_id == run_id)
                .order_by(BaselineTrial.index, BaselineTrial.created_at)
                .all()
            )
            return [
                Trial(
                    task_id=r.task_id,
                    game=r.game,
                    index=int(r.index or 0),
                    phase=str(r.phase or "measure"),
                    correct=r.correct,
                    latency_ms=r.latency_ms,
                    wager=r.wager,
                    response=dict(r.response or {}),
                    task=dict(r.task or {}),
                )
                for r in rows
            ]
        finally:
            if own:
                s.close()

    def view(self, student_id: str, run_id: str) -> dict[str, Any]:
        """Everything a screen needs: what is next, how far along, and what is measured so far."""
        with self._sess() as s:
            run = self._run(s, run_id)
            trials = self.trials_of(run_id, session=s)
            step = flow.next_step(
                seed=int(run.seed or 0),
                trials=trials,
                format_done=bool(run.format_pick),
                questions_done=bool((run.completion or {}).get("questions")),
            )
            prefs = dict((run.completion or {}).get("answers") or {})
            report = flow.stage_report(
                trials,
                seed=int(run.seed or 0),
                format_done=bool(run.format_pick),
                questions_done=bool((run.completion or {}).get("questions")),
            )
            return {
                "run_id": run.id,
                "student_id": run.student_id,
                "status": run.status,
                "stage": step.stage,
                "stage_title": next((x["title"] for x in flow.STAGES if x["key"] == step.stage), step.stage),
                "kind": step.kind,
                "task": _public_task(step.task) if step.task else None,
                "progress": step.progress,
                "note": step.note,
                "format_pick": run.format_pick,
                "format_options": [dict(o) for o in FORMAT_OPTIONS],
                "questions": [dict(q) for q in QUESTIONS] if step.kind == "questions" else [],
                "answers": prefs,
                "timers_visible": bool(prefs.get("timers_help", True)),
                "history": [_public_history_row(t) for t in trials],
                "completion": report,
                "measured_trials": len([t for t in trials if t.measured and t.correct is not None]),
                "started_at": run.started_at.isoformat() if run.started_at else None,
            }

    # ------------------------------------------------------------------ #
    # answers
    # ------------------------------------------------------------------ #
    def set_format(self, student_id: str, run_id: str, value: str) -> dict[str, Any]:
        """Store the pre-game format choice as a *stated* preference, in both places it belongs."""
        allowed = {o["value"] for o in FORMAT_OPTIONS}
        if value not in allowed:
            raise BaselineError(f"format: {value!r} is not one of {sorted(allowed)}")
        with self._sess() as s:
            run = self._run(s, run_id)
            run.format_pick = value
            completion = dict(run.completion or {})
            completion["format"] = True
            answers = dict(completion.get("answers") or {})
            answers["preferred_format"] = value
            completion["answers"] = answers
            run.completion = completion
            run.stage = "format_done"
            if self.state is not None:
                # The shared store, so a lesson built tomorrow reads the same choice. Provenance is
                # ``stated``: this is the learner's instruction, not a measurement.
                try:
                    self.state.set_preference(student_id, "preferred_format", value, source="baseline")
                except Exception:  # pragma: no cover - a preference write must never block the flow
                    log.exception("baseline: could not write the format preference to the store")
        return self.view(student_id, run_id)

    def answer_questions(self, student_id: str, run_id: str, answers: Mapping[str, Any]) -> dict[str, Any]:
        """Store the stated questions. Unknown keys are reported, not stored (existing rule)."""
        known = {q["key"] for q in QUESTIONS}
        refused = sorted(set(answers) - known)
        stored: dict[str, Any] = {}
        for key, value in answers.items():
            if key not in known:
                continue
            stored[key] = normalise_answer(key, value)
        if "preferred_format" in stored:  # the format pick has its own step and its own validation
            stored.pop("preferred_format")
        with self._sess() as s:
            run = self._run(s, run_id)
            completion = dict(run.completion or {})
            merged = {**dict(completion.get("answers") or {}), **stored}
            completion["answers"] = merged
            completion["questions"] = True
            run.completion = completion
            run.stage = "questions_done"
            if self.state is not None:
                for key, value in stored.items():
                    mapped = PREFERENCE_MAP.get(key)
                    if not mapped:
                        continue
                    try:
                        self.state.set_preference(student_id, mapped, value, source="baseline")
                    except Exception:  # pragma: no cover
                        log.exception("baseline: could not write preference %s to the store", key)
        out = self.view(student_id, run_id)
        out["refused"] = refused
        out["refused_note"] = "unknown question keys are refused rather than stored" if refused else ""
        return out

    def submit(
        self,
        student_id: str,
        run_id: str,
        *,
        task_id: str,
        response: Mapping[str, Any],
        latency_ms: int | None = None,
        wager: int | None = None,
    ) -> dict[str, Any]:
        """Judge one response and return the next step. Idempotent by ``task_id``."""
        with self._sess() as s:
            run = self._run(s, run_id)
            trials = self.trials_of(run_id, session=s)
            step = flow.next_step(
                seed=int(run.seed or 0),
                trials=trials,
                format_done=bool(run.format_pick),
                questions_done=bool((run.completion or {}).get("questions")),
            )
            already = next((t for t in trials if t.task_id == task_id), None)
            if already is not None:
                # Re-sent (a flaky tap, a reload): report the original score, do not record again.
                return {
                    **self.view(student_id, run_id),
                    "scored": _public_score(already.task, already.response.get("scored") or {}),
                    "duplicate": True,
                }
            task = dict(step.task or {})
            if not task or task.get("id") != task_id:
                raise BaselineError(
                    f"task {task_id!r} is not the task on offer (on offer: {task.get('id')!r}, stage {step.stage})"
                )
            if step.kind != "task":
                raise BaselineError(f"stage {step.stage} does not take answers")

            response = dict(response or {})
            answers = dict((run.completion or {}).get("answers") or {})
            timers_visible = bool(answers.get("timers_help", True))
            action = str(response.get("action") or task.get("action") or "") or None
            if wager is not None and (
                not timers_visible or not wager_allowed(task, action=action)
            ):
                raise BaselineError("a coin wager is only accepted on an eligible question when visible timers are on")
            scored = self._score(task, response, trials, seed=int(run.seed or 0))
            index = len(trials)
            correct = scored.get("correct")
            if task.get("phase") == "practice":
                correct = None  # a practice trial is not a measurement, and is never reported as one
            row = BaselineTrial(
                run_id=run_id,
                student_id=student_id,
                game=_game_of(task),
                task_id=task_id,
                index=index,
                phase=str(task.get("phase") or "measure"),
                correct=correct,
                latency_ms=int(latency_ms) if latency_ms is not None else None,
                wager=int(wager) if wager is not None else None,
                task=task,
                response={**response, "scored": scored},
                score=scored,
            )
            s.add(row)
            s.flush()

            # A measured trial is evidence, so it goes into the shared log as well. The event id is
            # the trial id, which is what makes a replayed baseline a no-op rather than a second
            # measurement (brief 2A's idempotency rule, applied here).
            if self.state is not None and row.phase == "measure":
                try:
                    self.state.ingest(
                        student_id,
                        self._event(run_id, task, scored, correct),
                        module="code",
                        session=s,
                    )
                except Exception:  # pragma: no cover - the baseline must never fail on the store
                    log.exception("baseline: could not ingest a trial into the evidence log")
            s.flush()
        out = self.view(student_id, run_id)
        out["scored"] = _public_score(task, scored)
        out["duplicate"] = False
        return out

    # ------------------------------------------------------------------ #
    # scoring dispatch (all of it in code)
    # ------------------------------------------------------------------ #
    def _score(self, task: Mapping[str, Any], response: Mapping[str, Any], trials: Sequence[Trial], *, seed: int) -> dict[str, Any]:
        game = _game_of(task)
        action = str(task.get("action") or response.get("action") or "")
        if task.get("phase") == "show":
            # A study screen is not a question. The learner saying "I have read it" is all the
            # information there is, and scoring it would be scoring them for looking.
            return {"correct": None, "action": "studied", "seconds_seen": response.get("seconds_seen")}
        if game == "machine":
            out = machine_respond(seed, int(task.get("round") or 1), task, response, trials)
            if out.get("action") == "guess":
                switched = machine_switched(seed, int(task.get("round") or 1), [*trials, _self_trial(task, response, out)])
                out.update({k: v for k, v in switched.items() if k != "switched"})
            return out
        if game == "vault":
            return vault_score(task, response)
        if game == "shopkeeper":
            return shopkeeper_score(task, response)
        if game in ("codewords_cued", "codewords_next_day"):
            return recall_score(task, response)
        if game == "codewords_delayed":
            if action == "predict":
                try:
                    value = float(response.get("value"))
                except (TypeError, ValueError):
                    raise BaselineError("a predicted score needs a number") from None
                return {"correct": None, "action": "predict", "predicted": value, "of": task.get("max_value")}
            if task.get("kind") == "free":
                response = {**dict(response), "seed": seed}
                return recall_score(task, response)
            return recall_score(task, response)
        if game == "locked_box":
            action = str(response.get("action") or "attempt")
            if action == "hint":
                hint = next((h for h in task.get("hint_shop") or [] if h["id"] == response.get("hint_id")), None)
                if hint is None:
                    raise BaselineError("that hint is not in this shop")
                if any(
                    t.game == "locked_box"
                    and trial_action(t) == "hint"
                    and t.response.get("hint_id") == hint["id"]
                    for t in trials
                ):
                    raise BaselineError("you have already used that hint")
                return {"correct": None, "action": "hint", "cost": hint["cost"], "hint": hint["text"]}
            if action == "walk_away":
                return {"correct": None, "action": "walk_away"}
            if action == "start":
                return {"correct": None, "action": "start"}
            return {"action": "attempt", **locked_box_score(task, response)}
        if game in ("quick", "trap"):
            return choice_score(task, response)
        raise BaselineError(f"no scorer for game {game!r}")  # pragma: no cover

    def _event(self, run_id: str, task: Mapping[str, Any], scored: Mapping[str, Any], correct: Any) -> Any:
        from ..state import EvidenceEvent

        game = _game_of(task)
        return EvidenceEvent(
            event_id=f"base_{run_id}_{task.get('id')}",
            kind=f"baseline_{game}",
            source_type="controlled" if task.get("phase") != "observational" else "observational",
            topic_id=None,
            item_id=str(task.get("id")),
            correct=bool(correct) if correct is not None else None,
            score=None,
            payload={
                "stage": task.get("stage"),
                "phase": task.get("phase"),
                "game": game,
                "scored": {k: v for k, v in dict(scored).items() if isinstance(v, (str, int, float, bool, type(None)))},
            },
        )

    # ------------------------------------------------------------------ #
    # finishing
    # ------------------------------------------------------------------ #
    def finish(self, student_id: str, run_id: str) -> dict[str, Any]:
        """Seal the run: an immutable mind snapshot, whatever was and was not measured.

        Repeated clicks/retries are idempotent: one run has one sealed snapshot, and finishing it
        again returns that snapshot instead of adding another row.
        """
        with self._sess() as s:
            run = self._run(s, run_id)
            existing = (
                s.query(MindSnapshot)
                .filter(MindSnapshot.student_id == student_id, MindSnapshot.run_id == run_id)
                .order_by(MindSnapshot.taken_at.desc())
                .first()
            )
            if existing is not None and run.status == "complete":
                snapshot_id = existing.id
            else:
                trials = self.trials_of(run_id, session=s)
                report = flow.stage_report(
                    trials,
                    seed=int(run.seed or 0),
                    format_done=bool(run.format_pick),
                    questions_done=bool((run.completion or {}).get("questions")),
                )
                prefs = dict((run.completion or {}).get("answers") or {})
                model = self.state.get_snapshot(student_id).model if self.state is not None else {}
                built = F.build_factors(trials, model=model, preferences=prefs)
                levers = F.levers(built, preferences=prefs)
                payload = F.snapshot_payload(
                    built,
                    levers_data=levers,
                    preferences=prefs,
                    completion=report,
                    preference_row=F.preference_vs_performance(built, prefs),
                )
                row = MindSnapshot(
                    student_id=student_id,
                    run_id=run_id,
                    factors=payload["factors"],
                    levers=payload["levers"],
                    completion=report,
                )
                s.add(row)
                run.status = "complete"
                run.stage = "snapshot"
                run.finished_at = utcnow()
                s.flush()
                snapshot_id = row.id
        out = self.snapshot(student_id, run_id=run_id)
        out["snapshot_id"] = snapshot_id
        return out

    def snapshot(self, student_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        """The latest stored snapshot, or one computed now from the log if none was ever sealed."""
        with self._sess() as s:
            query = s.query(MindSnapshot).filter(MindSnapshot.student_id == student_id)
            if run_id:
                query = query.filter(MindSnapshot.run_id == run_id)
            row = query.order_by(MindSnapshot.taken_at.desc()).first()
            if row is not None:
                return {
                    "student_id": student_id,
                    "run_id": row.run_id,
                    "snapshot_id": row.id,
                    "taken_at": row.taken_at.isoformat() if row.taken_at else None,
                    "factors": list(row.factors or []),
                    "levers": dict(row.levers or {}),
                    "completion": dict(row.completion or {}),
                    "state": "sealed",
                }
            run = (
                s.query(BaselineRun)
                .filter(BaselineRun.student_id == student_id)
                .order_by(BaselineRun.started_at.desc())
                .first()
            )
            if run is None:
                return {"student_id": student_id, "state": "not_started", "factors": [], "levers": {}}
            trials = self.trials_of(run.id, session=s)
            prefs = dict((run.completion or {}).get("answers") or {})
            model = self.state.get_snapshot(student_id).model if self.state is not None else {}
            built = F.build_factors(trials, model=model, preferences=prefs)
            levers = F.levers(built, preferences=prefs)
            report = flow.stage_report(
                trials,
                seed=int(run.seed or 0),
                format_done=bool(run.format_pick),
                questions_done=bool((run.completion or {}).get("questions")),
            )
            return {
                "student_id": student_id,
                "run_id": run.id,
                "snapshot_id": None,
                "taken_at": None,
                "state": "in_progress",
                **F.snapshot_payload(built, levers_data=levers, preferences=prefs, completion=report),
            }

    def next_day(self, student_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        """The codeword check for a learner who has come back (brief §5.2)."""
        with self._sess() as s:
            run = (
                s.query(BaselineRun).filter(BaselineRun.id == run_id).first()
                if run_id
                else s.query(BaselineRun)
                .filter(BaselineRun.student_id == student_id)
                .order_by(BaselineRun.started_at.desc())
                .first()
            )
            if run is None:
                return {"student_id": student_id, "tasks": [], "note": "no baseline yet"}
            trials = self.trials_of(run.id, session=s)
            studied = any(t.game == "codewords_study" and t.phase == "show" for t in trials)
            tasks = flow.next_day_tasks(int(run.seed or 0), trials) if studied else []
            return {
                "student_id": student_id,
                "run_id": run.id,
                "tasks": [_public_task(task) for task in tasks],
                "studied": studied,
                "note": (
                    "these are the pairs you learned last time, asked again with no new study"
                    if tasks
                    else "no codeword pairs were studied in this run, or nothing is left to re-check"
                ),
            }


def _game_of(task: Mapping[str, Any]) -> str:
    return str(task.get("game") or "")


def _self_trial(task: Mapping[str, Any], response: Mapping[str, Any], scored: Mapping[str, Any]) -> Trial:
    """The trial being scored right now, so a scorer can see it alongside the earlier ones."""
    return Trial(
        task_id=str(task.get("id")),
        game=_game_of(task),
        index=10_000,
        phase=str(task.get("phase") or "measure"),
        correct=scored.get("correct"),
        response=dict(response),
        task=dict(task),
    )


__all__ = ["Baseline", "BaselineError", "FORMAT_OPTIONS", "PREFERENCE_MAP", "QUESTIONS", "normalise_answer"]
