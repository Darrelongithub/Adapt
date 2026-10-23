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
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Sequence

from ..db.models import BaselineRun, BaselineTrial, MindSnapshot, utcnow
from . import factors as F
from . import flow
from .games import (
    Trial,
    choice_score,
    locked_box_coins_left,
    locked_box_score,
    machine_respond,
    machine_switched,
    quick_score,
    recall_score,
    seed_of,
    shopkeeper_score,
    vault_score,
    wager_allowed,
)

log = logging.getLogger("adapt.baseline")

#: Every codeword-check task id starts with this, so the submit path can tell it from a flow task.
NEXT_DAY_PREFIX = "codewords_next_day_"


class BaselineError(RuntimeError):
    """Something the caller did that the flow will not accept."""


#: Keys that name the right answer. They are used by the scorer and are never sent to the page
#: before the learner has answered: a served task that carries its own answer measures nothing.
PRIVATE_TASK_KEYS: tuple[str, ...] = ("answer", "intuitive", "explain", "expected")

#: Parts of a *verdict* that are diagnostic rather than feedback. The machine's rule name is the
#: answer to the round, and its switch point is the thing the learner is supposed to discover;
#: sending either back with every probe handed the answer to anyone who opened the network tab.
PRIVATE_SCORED_KEYS: tuple[str, ...] = (
    "rule_name",
    "switch_after_probe",
    "probes_after_switch",
    "noticed",
    "probes_before_noticing",
)


def public_scored(scored: Mapping[str, Any] | None, task: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The verdict on a trial as the page may see it. The stored record keeps everything.

    ``expected`` is the right answer to the item just answered. A learner who is told it has the
    answer to a measured item they got wrong, and the same pairs come back in the delayed check, so
    the reply to a *measured* recall answer does not carry it. (Practice answers are feedback, and
    the practice reply keeps it.) A task that asks for feedback explicitly — the quick sprint and
    the trap questions, where the answer is a fact rather than the thing being remembered — says so
    with ``feedback: "show"``.
    """
    out = {k: v for k, v in dict(scored or {}).items() if k not in PRIVATE_SCORED_KEYS}
    show = bool(task and task.get("feedback") == "show") or bool(task and task.get("phase") == "practice")
    if show:
        return out
    return {k: v for k, v in out.items() if k not in ("expected", "explain")}


def public_task(task: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The task as the page may see it: everything it needs to ask, nothing that tells the answer."""
    if not task:
        return None
    out = {k: v for k, v in dict(task).items() if k not in PRIVATE_TASK_KEYS}
    if isinstance(out.get("distractors"), list):
        # A true/false statement without its answer; the id and text are all the page needs.
        out["distractors"] = [{"id": d["id"], "text": d["text"]} for d in out["distractors"]]
    return out


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
        return self.view(student_id, run_id)

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
                "task": public_task(step.task),
                "progress": step.progress,
                "note": step.note,
                "format_pick": run.format_pick,
                "format_options": [dict(o) for o in FORMAT_OPTIONS],
                "questions": [dict(q) for q in QUESTIONS] if step.kind == "questions" else [],
                "answers": prefs,
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
                    self.state.set_preference(
                        student_id, "preferred_format", value, source="baseline", session=s
                    )
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
                        self.state.set_preference(student_id, mapped, value, source="baseline", session=s)
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
        if task_id.startswith(NEXT_DAY_PREFIX):
            return self._submit_next_day(
                student_id, run_id, task_id=task_id, response=response, latency_ms=latency_ms, wager=wager
            )
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
                    "scored": dict(already.response.get("scored") or {}),
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
            scored = self._score(task, response, trials, seed=int(run.seed or 0))
            # What the learner *did* with the task is part of the trial, not just what the server
            # asked: a machine round is finished when probes and wrong guesses run out, and the
            # locked box is measured by hints bought and attempts made. Both read this back, and
            # without it the machine round had no ending at all (it re-served the same task forever)
            # and the persistence factor saw no attempts — two bugs from one omission.
            action = response.get("action") or task.get("action")
            if action:
                task = {**task, "action": action}
            # A timed lap ends from the clock the page reports, not from a guess: the answer that
            # arrives at or past the lap's budget closes the lap. Recording the closing action on
            # the trial is what lets the flow's next-task function see the lap as finished.
            if _game_of(task) == "quick":
                try:
                    elapsed = int(response.get("elapsed_ms") or 0)
                except (TypeError, ValueError):
                    elapsed = 0
                if elapsed >= int(task.get("lap_seconds") or 0) * 1000:
                    # A tap that landed at or after the whistle is not an answer to that item, even
                    # if it happened to be right: counting it would reward the slowest tap in the
                    # lap. The trial still keeps what was given, so nothing is lost from the record.
                    task["action"] = "timeout"
                    scored = {
                        "correct": None,
                        "action": "timeout",
                        "expected": task.get("answer"),
                        "given": scored.get("given"),
                        "points": 0,
                        "streak": 0,
                        "lap_over": True,
                    }
            index = len(trials)
            if scored.get("action"):
                task["action"] = scored["action"]
            correct = scored.get("correct")
            if task.get("phase") == "practice":
                correct = None  # practice never enters measured correctness
            row = BaselineTrial(
                run_id=run_id,
                student_id=student_id,
                game=_game_of(task),
                task_id=task_id,
                index=index,
                phase=str(task.get("phase") or "measure"),
                correct=correct,
                latency_ms=int(latency_ms) if latency_ms is not None else None,
                wager=int(wager) if wager is not None and correct is not None and wager_allowed(task) else None,
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
                    # ``session=s`` matters more than it looks. One SQLite file allows one writer,
                    # so a store call that opened its *own* session here would block on the write
                    # transaction this method is already holding and, after the 5-second busy
                    # timeout, fail with "database is locked" — mid-baseline, on a learner's answer.
                    # The trial and its evidence row are one transaction, which is also the truth:
                    # if the trial did not land, the evidence should not either.
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
        # The last answer of the last game *is* the end of the sitting. The page used to show the
        # snapshot and leave the run open, so the history said "in progress" forever, the return
        # visit never offered "welcome back", and the mind snapshot was never sealed into a row.
        # Sealing here makes finishing the games enough: no extra click is needed to store it.
        if out["kind"] == "snapshot" and out["status"] == "in_progress":
            self.finish(student_id, run_id)
            out["status"] = "complete"
        out["scored"] = public_scored(scored, task)
        out["duplicate"] = False
        return out

    def _submit_next_day(
        self,
        student_id: str,
        run_id: str,
        *,
        task_id: str,
        response: Mapping[str, Any],
        latency_ms: int | None,
        wager: int | None,
    ) -> dict[str, Any]:
        """Judge one answer to the codeword check, which is offered after the run is over.

        The check is not part of the flow (``next_step`` only serves the open run), so it has its own
        path: the task must be one the check is offering *now*, and a re-sent answer is reported, not
        recorded twice. A new answer changes the measurement, so the sealed snapshot is re-sealed
        as a new row -- the old one stays, as a snapshot should.
        """
        with self._sess() as s:
            run = self._run(s, run_id)
            if run.student_id != student_id:
                raise BaselineError(f"unknown baseline run {run_id!r}")
            trials = self.trials_of(run_id, session=s)
            already = next((t for t in trials if t.task_id == task_id), None)
            if already is not None:
                out_scored: dict[str, Any] = dict(already.response.get("scored") or {})
                status = run.status
                duplicate = True
            else:
                offered = flow.next_day_tasks(int(run.seed or 0), trials)
                task = next((t for t in offered if t.get("id") == task_id), None)
                if task is None:
                    raise BaselineError(f"task {task_id!r} is not the codeword check on offer")
                response = dict(response or {})
                scored = recall_score(task, response)
                out_scored = scored
                status = run.status
                duplicate = False
                row = BaselineTrial(
                    run_id=run_id,
                    student_id=student_id,
                    game="codewords_next_day",
                    task_id=task_id,
                    index=len(trials),
                    phase="measure",
                    correct=bool(scored.get("correct")),
                    latency_ms=int(latency_ms) if latency_ms is not None else None,
                    wager=int(wager) if wager is not None and wager_allowed(task) else None,
                    task=dict(task),
                    response={**response, "scored": scored},
                    score=scored,
                )
                s.add(row)
                s.flush()
                if self.state is not None:
                    try:
                        self.state.ingest(
                            student_id,
                            self._event(run_id, task, scored, bool(scored.get("correct"))),
                            module="code",
                            session=s,
                        )
                    except Exception:  # pragma: no cover - the check must never fail on the store
                        log.exception("baseline: could not ingest a next-day answer into the evidence log")
                s.flush()
        if not duplicate and status == "complete":
            self._reseal(student_id, run_id)
        out = self.view(student_id, run_id)
        out["scored"] = public_scored(out_scored)
        out["duplicate"] = duplicate
        return out

    def _reseal(self, student_id: str, run_id: str) -> str:
        """Write a new snapshot row for a finished run, from the trial log as it is now."""
        model = self.state.get_snapshot(student_id).model if self.state is not None else {}
        with self._sess() as s:
            run = self._run(s, run_id)
            trials = self.trials_of(run_id, session=s)
            payload, report = self._sealed_payload(student_id, run, trials, model=model)
            row = MindSnapshot(
                student_id=student_id,
                run_id=run_id,
                factors=payload["factors"],
                levers=payload["levers"],
                completion={**report, "preference_vs_performance": payload["preference_vs_performance"]},
            )
            s.add(row)
            s.flush()
            return row.id

    def _sealed_payload(self, student_id: str, run: Any, trials: Sequence[Trial], *, model: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """The snapshot payload and the stage report for one run. Used by sealing and re-sealing alike."""
        report = flow.stage_report(
            trials,
            seed=int(run.seed or 0),
            format_done=bool(run.format_pick),
            questions_done=bool((run.completion or {}).get("questions")),
        )
        prefs = dict((run.completion or {}).get("answers") or {})
        built = F.build_factors(trials, model=model, preferences=prefs)
        levers = F.levers(built, preferences=prefs)
        payload = F.snapshot_payload(
            built,
            levers_data=levers,
            preferences=prefs,
            completion=report,
            preference_row=self._method_comparison(student_id),
        )
        return payload, report

    def internal_task(self, run_id: str) -> dict[str, Any] | None:
        """The task on offer *with* its answer key. For the scorer and for tests only: no route returns it."""
        with self._sess() as s:
            run = self._run(s, run_id)
            trials = self.trials_of(run_id, session=s)
            step = flow.next_step(
                seed=int(run.seed or 0),
                trials=trials,
                format_done=bool(run.format_pick),
                questions_done=bool((run.completion or {}).get("questions")),
            )
            return dict(step.task) if step.task else None

    def internal_next_day(self, run_id: str) -> list[dict[str, Any]]:
        """The codeword check with its answers. Same caveat as :meth:`internal_task`."""
        with self._sess() as s:
            run = self._run(s, run_id)
            return flow.next_day_tasks(int(run.seed or 0), self.trials_of(run_id, session=s))

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
            if out.get("action") in ("guess", "probe") and "error" not in out:
                # Noticing the switch is read from the graded outputs, and a probe can be where it
                # happens first, so every machine action carries the read-out, not only guesses.
                switched = machine_switched(
                    seed, int(task.get("round") or 1), [*trials, _self_trial(task, response, out)]
                )
                out.update({k: v for k, v in switched.items() if k != "switched"})
            return out
        if game == "vault":
            return vault_score(task, response)
        if game == "shopkeeper":
            if not isinstance(response.get("distractors") or {}, Mapping):
                raise BaselineError("the distractor answers must be a map from question id to true or false")
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
                    t.task.get("action") == "hint" and t.response.get("hint_id") == hint["id"] for t in trials
                ):
                    raise BaselineError("that hint is already bought")
                bought = [str(t.response.get("hint_id")) for t in trials if t.task.get("action") == "hint"]
                if int(hint["cost"]) > locked_box_coins_left(bought):
                    raise BaselineError("not enough coins for that hint")
                return {"correct": None, "action": "hint", "cost": hint["cost"], "hint": hint["text"]}
            if action == "walk_away":
                return {"correct": None, "action": "walk_away"}
            if action == "start":
                return {"correct": None, "action": "start"}
            return {"action": "attempt", **locked_box_score(task, response)}
        if game == "quick":
            # The streak is read from this lap's own history, not from the page: the HUD can be
            # wrong, the record cannot.
            return quick_score(task, response, streak_before=self._quick_streak(trials, task))
        if game == "trap":
            out = choice_score(task, response)
            out["explain"] = task.get("explain")
            out["intuitive"] = task.get("intuitive")
            return out
        raise BaselineError(f"no scorer for game {game!r}")  # pragma: no cover

    @staticmethod
    def _quick_streak(trials: Sequence[Trial], task: Mapping[str, Any]) -> int:
        """How many quick taps in a row were right *before* this one, in this lap."""
        stage = str(task.get("stage") or "")
        streak = 0
        for t in reversed([t for t in trials if t.game == "quick" and str(t.task.get("stage") or "") == stage]):
            if t.correct is True:
                streak += 1
            else:
                break
        return streak

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
        """Seal the run: an immutable mind snapshot, whatever was and was not measured."""
        # Read the learner's model first, on its own connection: a read that opens a second session
        # while a write transaction is open on the same file waits on the writer's lock.
        model = self.state.get_snapshot(student_id).model if self.state is not None else {}
        with self._sess() as s:
            run = self._run(s, run_id)
            if run.status == "complete":
                # Pressing "Stop here" on a sitting that is already sealed must not write a second
                # mind snapshot for the same trial log. The first one is that sitting's record.
                return self.snapshot(student_id)
            trials = self.trials_of(run_id, session=s)
            report = flow.stage_report(
                trials,
                seed=int(run.seed or 0),
                format_done=bool(run.format_pick),
                questions_done=bool((run.completion or {}).get("questions")),
            )
            payload, report = self._sealed_payload(student_id, run, trials, model=model)
            row = MindSnapshot(
                student_id=student_id,
                run_id=run_id,
                factors=payload["factors"],
                levers=payload["levers"],
                completion={**report, "preference_vs_performance": payload["preference_vs_performance"]},
            )
            s.add(row)
            run.status = "complete"
            run.stage = "snapshot"
            run.finished_at = utcnow()
            s.flush()
            snapshot_id = row.id
        out = self.snapshot(student_id)
        out["snapshot_id"] = snapshot_id
        return out

    # ------------------------------------------------------------------ #
    # what was stored, in the learner's words
    # ------------------------------------------------------------------ #
    #: What each stored game is called on the results panel. The same names the page uses.
    GAME_TITLES: Mapping[str, str] = {
        "codewords_study": "Word memory",
        "codewords_cued": "Word recall",
        "quick": "Quick taps",
        "vault": "Vault lights",
        "shopkeeper": "Shopkeeper",
        "machine": "Crack the machine",
        "locked_box": "The locked box",
        "trap": "Two questions to think about",
        "codewords_delayed": "Delayed recall",
    }

    def history(self, student_id: str, *, limit: int = 10) -> dict[str, Any]:
        """Every sitting this learner has played, and the best of each game.

        The trials have always been stored; what was missing was a way to *see* them after the
        snapshot had scrolled away. This reads the same rows the snapshot is built from, so a score
        here and a factor there can never disagree — there is one record, and this is a view of it.
        """
        with self._sess() as s:
            runs = (
                s.query(BaselineRun)
                .filter(BaselineRun.student_id == student_id)
                .order_by(BaselineRun.started_at.desc())
                .limit(max(1, int(limit)))
                .all()
            )
            run_ids = [r.id for r in runs]
            rows = (
                s.query(BaselineTrial)
                .filter(BaselineTrial.run_id.in_(run_ids))
                .order_by(BaselineTrial.created_at.asc())
                .all()
                if run_ids
                else []
            )
            run_rows = [
                {
                    "run_id": r.id,
                    "status": r.status,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in runs
            ]
            open_run = any(r.status == "in_progress" for r in runs)
            # Read what is needed inside the session: the encrypted columns are decrypted on load.
            plain = [
                {
                    "run_id": row.run_id,
                    "game": row.game,
                    "phase": row.phase,
                    "correct": row.correct,
                    "task": dict(row.task or {}),
                    "response": dict(row.response or {}),
                    "score": dict(row.score or {}),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        by_run: dict[str, list[dict[str, Any]]] = {}
        for row in plain:
            by_run.setdefault(row["run_id"], []).append(row)
        for item in run_rows:
            mine = by_run.get(item["run_id"], [])
            item["trials"] = len(mine)
            item["measured"] = len([r for r in mine if r["phase"] != "practice" and r["correct"] is not None])
            item["games"] = sorted({str(r["game"]) for r in mine})
        games = [
            self._game_result(game, [r for r in plain if r["game"] == game])
            for game in self.GAME_TITLES
        ]
        played = [g for g in games if g["plays"]]
        return {
            "student_id": student_id,
            "has_open_run": open_run,
            "runs": run_rows,
            "games": played,
            "totals": {
                "sittings": len(runs),
                "completed_sittings": len([r for r in runs if r.status == "complete"]),
                "trials": len(plain),
                "measured": len([r for r in plain if r["phase"] != "practice" and r["correct"] is not None]),
            },
        }

    def _game_result(self, game: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """One line of the results panel: how often it was played and the best it ever went."""
        measured = [r for r in rows if r["phase"] != "practice"]
        answered = [r for r in measured if r["correct"] is not None]
        out: dict[str, Any] = {
            "game": game,
            "title": self.GAME_TITLES.get(game, game),
            "plays": len(measured),
            "answered": len(answered),
            "correct": len([r for r in answered if r["correct"]]),
            "last_played_at": rows[-1]["created_at"] if rows else None,
            "best": None,
            "best_label": "",
            "detail": {},
        }
        if game == "codewords_study":
            out["best_label"] = "looking time recorded"
            return out
        if not measured:
            return out
        accuracy = (out["correct"] / len(answered)) if answered else None
        if accuracy is not None:
            out["detail"]["accuracy"] = round(accuracy, 3)
        if game == "quick":
            laps: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
            for r in measured:
                laps.setdefault((str(r["run_id"]), str(r["task"].get("stage") or "")), []).append(r)
            totals = []
            for lap in laps.values():
                totals.append(sum(int(r["score"].get("points") or 0) for r in lap))
            out["best"] = max(totals) if totals else 0
            out["best_label"] = f"{out['best']} points in one lap"
            out["detail"]["laps"] = len(laps)
            out["detail"]["best_streak"] = max([int(r["score"].get("streak") or 0) for r in measured] or [0])
        elif game == "vault":
            spans = [int(r["task"].get("length") or 0) for r in measured if r["correct"]]
            out["best"] = max(spans) if spans else 0
            out["best_label"] = f"remembered {out['best']} tiles in a row" if spans else "no sequence completed yet"
            out["detail"]["best_span"] = out["best"]
        elif game in ("codewords_cued", "codewords_delayed", "trap", "shopkeeper"):
            # Best *sitting* accuracy, so one lucky answer does not read as a perfect memory.
            per_run: dict[str, list[Mapping[str, Any]]] = {}
            for r in answered:
                per_run.setdefault(str(r["run_id"]), []).append(r)
            best = max((len([x for x in v if x["correct"]]) / len(v) for v in per_run.values()), default=0.0)
            out["best"] = round(best, 3)
            out["best_label"] = f"{round(best * 100)}% right in a sitting"
            if game == "codewords_delayed":
                hits = [int(r["score"].get("hits") or 0) for r in measured if r["task"].get("kind") == "free"]
                if hits:
                    out["detail"]["recalled_of_8"] = max(hits)
        elif game == "machine":
            solved = [r for r in answered if r["correct"] and str(r["task"].get("action") or r["response"].get("action") or "") == "guess"]
            out["best"] = len({(r["run_id"], r["task"].get("round")) for r in solved})
            out["best_label"] = f"{out['best']} rule(s) cracked" if out["best"] else "no rule cracked yet"
            out["detail"]["rounds_solved"] = out["best"]
        elif game == "locked_box":
            solved = any(r["correct"] for r in answered)
            hints = len([r for r in rows if str(r["response"].get("action") or r["task"].get("action") or "") == "hint"])
            attempts = len([r for r in rows if str(r["response"].get("action") or r["task"].get("action") or "attempt") == "attempt"])
            out["best"] = 1 if solved else 0
            out["best_label"] = "opened" if solved else ("walked away — also fine" if hints or attempts else "not opened")
            out["detail"].update({"opened": solved, "attempts": attempts, "hints": hints})
        return out

    def snapshot(self, student_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        """The latest stored snapshot, or one computed now from the log if none was ever sealed."""
        model = self.state.get_snapshot(student_id).model if self.state is not None else {}
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
                    "preference_vs_performance": (row.completion or {}).get("preference_vs_performance"),
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
                **F.snapshot_payload(built, levers_data=levers, preferences=prefs, completion=report,
                                     preference_row=self._method_comparison(student_id)),
            }

    def _method_comparison(self, student_id: str) -> dict[str, Any] | None:
        if self.state is None:
            return None
        from ..learning.failure import FailureLoop

        return FailureLoop(self.state).report(student_id)["preference_vs_performance"]

    def next_day(self, student_id: str, *, run_id: str | None = None, finished: bool = False) -> dict[str, Any]:
        """The codeword check for a learner who has come back (brief §5.2).

        Without a run id it is the check for the learner's last *finished* sitting, so a returning
        learner who has already opened a new baseline still gets yesterday's check. Only tasks the
        page may see are returned; the answers stay with the server.
        """
        with self._sess() as s:
            query = s.query(BaselineRun).filter(BaselineRun.student_id == student_id)
            if run_id:
                run = query.filter(BaselineRun.id == run_id).first()
            elif finished:
                # The return visit's check: the last sitting that was *finished*. The open one is not
                # "last time", and its check must not appear on the page the learner is working in.
                run = query.filter(BaselineRun.status == "complete").order_by(BaselineRun.started_at.desc()).first()
            else:
                run = query.order_by((BaselineRun.status == "complete").desc(), BaselineRun.started_at.desc()).first()
            if run is None:
                return {"student_id": student_id, "tasks": [], "note": "no finished baseline to check yet" if finished else "no baseline yet"}
            trials = self.trials_of(run.id, session=s)
            tasks = [public_task(t) for t in flow.next_day_tasks(int(run.seed or 0), trials)]
            studied = any(t.game == "codewords_study" for t in trials)
            # The check measures a gap. Asked on the day of the sitting, the gap is short, and the page
            # says so: "next day" is only a name for the gap when the day has actually passed.
            finished = run.finished_at
            same_day = bool(finished and finished.date() == datetime.now(timezone.utc).replace(tzinfo=None).date())
            return {
                "student_id": student_id,
                "run_id": run.id,
                "tasks": tasks,
                "studied": studied,
                "same_day": same_day,
                "note": (
                    "these are the pairs you learned last time, asked again with no new study"
                    if tasks
                    else "nothing left to re-check"
                ),
            }


def _game_of(task: Mapping[str, Any]) -> str:
    return str(task.get("game") or "")


def _self_trial(task: Mapping[str, Any], response: Mapping[str, Any], scored: Mapping[str, Any]) -> Trial:
    """The trial being scored right now, shaped as it will be stored, so a scorer can see it.

    It carries the action and the verdict the same way a stored trial does (the verdict under
    ``response["scored"]``), because the readers of the trial log look in those places and nowhere else.
    """
    action = str(response.get("action") or task.get("action") or "")
    return Trial(
        task_id=str(task.get("id")),
        game=_game_of(task),
        index=10_000,
        phase=str(task.get("phase") or "measure"),
        correct=scored.get("correct"),
        response={**dict(response), "scored": dict(scored)},
        task={**dict(task), **({"action": action} if action else {})},
    )


__all__ = ["Baseline", "BaselineError", "FORMAT_OPTIONS", "PREFERENCE_MAP", "QUESTIONS", "normalise_answer"]
