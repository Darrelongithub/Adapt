"""The subject round: one service the API calls, and nothing else (round brief §6).

Shaped like :mod:`adapt.baseline.service` on purpose — same ``_sess`` discipline, same
trials-are-the-truth reconstruction, same "a re-sent answer is a duplicate and not a second
measurement" rule — because two services that behave differently about the same kind of event is how
an evidence log ends up double-counting.

The four methods the brief names are here with those names:

* :meth:`Subjects.resolve` — free text to subject, topic and level hint (§6.3 step 1);
* :meth:`Subjects.plan_tests` — the top three or four leanings plus a cross-check under a time cap,
  with every skipped skill recorded ``not_measured`` (§6.3 step 2);
* :meth:`Subjects.ladder_step` — one answer on the staircase (§6.4);
* :meth:`Subjects.method_test` — the pop-up lesson, its seeded counterbalance and its gain-against-
  expectation comparison (§6.5).

Three things this service deliberately does not do:

* **It does not compute mastery.** ``p_know`` is read from a store snapshot; the estimate the app
  shows comes from AI 1's fold over the evidence log. A second learner model built beside the first
  is the failure §2A warns about.
* **It does not advance the learner model.** Evidence is written with ``module="code"``, which is the
  only write domain this code has (:mod:`adapt.state.permissions` refuses ``code`` from writing
  ``learner_model``), and the model advance stays with AI 1.
* **It does not invent content.** No authored chain, no authored lesson pair, no ratings: the round
  says what it does not have. A confident plan for a topic the app knows nothing about would be worse
  than an honest gap.

Every store call made inside an open session passes that session. Not doing so is a live defect in
the baseline service today: ``state.ingest`` opens its own connection, SQLite's write lock is held by
the caller's uncommitted flush, and the ingest waits out a five-second ``busy_timeout`` and then
fails silently — so no baseline evidence ever reaches the shared log, and every trial costs five
seconds. Passing ``session=s`` makes it one transaction and one commit.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Iterator, Mapping, Sequence

from sqlalchemy import select

from ..assessment import bank as BANK
from ..db.models import Topic, utcnow
from . import curriculum as CUR
from . import games as G
from . import ladder as L
from . import methodtest as M
from . import plan as P
from .leanings import Leanings, family_of_skill, rate_leanings
from .models import MethodTestRun, SubjectRound, SubjectSnapshot, SubjectTrial
from .resolve import Resolved, refine_with_model, resolve_topic
from .taxonomy import TAXONOMY_VERSION, skills_for

log = logging.getLogger("adapt.subjects.service")

#: The round's stages, in order. A learner can stop at any point and still get a snapshot of what was
#: measured — the same promise the mind baseline makes.
STAGES: tuple[tuple[str, str], ...] = (
    ("skills", "The skills this topic leans on"),
    ("ladder", "Where you are in this topic"),
    ("method", "One short lesson, taught a second way"),
    ("snapshot", "Your subject map"),
)

#: How many seconds a day is, for the next-day recheck (§6.5).
RECHECK_AFTER = timedelta(hours=20)
RECHECK_WINDOW = timedelta(hours=52)

#: The maximum a learner may wager on one answer, matching the baseline's coin economy so the two
#: rounds are comparable.
MAX_WAGER = 3


class SubjectsError(RuntimeError):
    """Refused input, or a call that does not belong to this learner. Never a 500."""


def seed_of(*parts: Any) -> int:
    """A stable seed, masked to 62 bits so it fits a SQLite INTEGER (a seed that cannot be stored is
    not a seed: the learner resuming on another device must get the same tasks)."""
    from ..learning.seeds import stable_seed

    return stable_seed(*parts) & ((1 << 62) - 1)


@dataclass(frozen=True)
class Step:
    """What the round is offering right now."""

    stage: str
    kind: str  # task | lesson | ladder | done
    #: What the learner may see: the task with its answer key stripped.
    task: Mapping[str, Any] | None = None
    #: The server-side copy, with the key. Scoring uses this and never the public copy.
    task_full: Mapping[str, Any] | None = None
    #: The token ``POST /api/v1/subjects/ladder/{step}`` expects, when the step is a ladder one.
    step_id: str | None = None
    progress: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""


class Subjects:
    """The subject round. Constructed with a session factory and the shared state store."""

    def __init__(
        self,
        sf: Callable[[], Any],
        *,
        state: Any = None,
        settings: Any = None,
        llm: Any = None,
    ) -> None:
        self.sf = sf
        self.state = state
        self.settings = settings
        #: Optional. With no key configured this is ``None`` or unconfigured, and every model path
        #: falls back to the authored or generic answer and says so.
        self.llm = llm

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #
    @contextmanager
    def _sess(self) -> Iterator[Any]:
        """A session that commits on the way out and always closes.

        ``with self.sf() as s`` would not do this: SQLAlchemy's session context manager closes the
        session and rolls back anything uncommitted.
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

    def _run(self, s: Any, run_id: str, student_id: str) -> SubjectRound:
        row = s.get(SubjectRound, run_id)
        # One message for "does not exist" and "is not yours": a probe for somebody else's run must
        # not learn that it exists.
        if row is None or row.student_id != student_id:
            raise SubjectsError(f"unknown_round:{run_id}")
        return row

    def _trials(self, run_id: str, *, session: Any = None) -> list[SubjectTrial]:
        """The round's trials, oldest first. Always called with the caller's session: a second
        session here is a second connection to the same SQLite file."""
        if session is None:
            with self._sess() as own:
                return self._trials(run_id, session=own)
        return list(
            session.execute(
                select(SubjectTrial).where(SubjectTrial.run_id == run_id).order_by(SubjectTrial.index)
            ).scalars()
        )

    def _topic_ids(self, s: Any) -> dict[str, str]:
        """Bank code → topic row id, so evidence can land on a topic the app already knows."""
        try:
            rows = s.execute(select(Topic.code, Topic.id)).all()
        except Exception:  # pragma: no cover - an unseeded database has no topics, which is fine
            return {}
        return {str(code): str(tid) for code, tid in rows if code}

    # ------------------------------------------------------------------ #
    # §6.3 step 1: resolve
    # ------------------------------------------------------------------ #
    def resolve(self, student_id: str, text: str) -> dict[str, Any]:
        """Free text in, one subject/topic/level answer out, with what it matched and what it suggests.

        AI 2 does the normalising when a model is configured; code holds the decision either way, so
        the family a model names has to be one of the five in the taxonomy or the deterministic
        answer stands.
        """
        if not str(text or "").strip():
            raise SubjectsError("a topic is needed: type the subject or topic you want to be placed in")
        if len(str(text)) > 240:
            raise SubjectsError("that is too long to be a topic name — a subject or a topic, in a few words")
        with self._sess() as s:
            ids = self._topic_ids(s)
        resolved = resolve_topic(str(text), bank=BANK.topic_catalog(), topic_ids=ids)
        resolved = refine_with_model(resolved, llm=self.llm, student_id=student_id)
        leanings = rate_leanings(resolved.as_dict(), llm=self.llm, student_id=student_id)
        chain = CUR.chain_for(resolved)
        pair = M.pair_for(resolved.topic_key)
        return {
            "resolved": resolved.as_dict(),
            "suggestions": _suggestions(resolved),
            "leanings": leanings.as_dict(with_scale=True),
            "ladder": {
                "available": chain is not None,
                "chain": chain.as_dict(with_nodes=False) if chain else None,
                "note": (
                    f"{chain.depth} nodes across {len(chain.sub_areas())} sub-areas, depth limit {L.MAX_DEPTH}, checked acyclic"
                    if chain
                    else "we have no authored prerequisite chain for this topic, so the ladder will not run and will not invent one"
                ),
            },
            "method_test": {
                "available": pair is not None,
                "concepts": [c.title for c in pair.concepts] if pair else [],
                "why_matched": pair.why_matched if pair else "",
                "methods": M.methods_payload(),
                "note": (
                    "two matched concepts, three questions each, one format for both, and the method and order dealt by seeded code"
                    if pair
                    else "we have no authored ninety-second lesson pair for this topic yet, so the pop-up lesson is skipped rather than invented"
                ),
            },
            "taxonomy_version": TAXONOMY_VERSION,
        }

    # ------------------------------------------------------------------ #
    # §6.3 step 2: plan
    # ------------------------------------------------------------------ #
    def plan_tests(self, topic: str | Mapping[str, Any] | Resolved, *, student_id: str | None = None) -> dict[str, Any]:
        """Which skills the round tests, which it does not, and why — for any topic, run or not."""
        resolved = self._resolve_any(topic, student_id=student_id)
        leanings = rate_leanings(resolved.as_dict(), llm=self.llm, student_id=student_id)
        plan = P.plan_tests(leanings)
        tasks: dict[str, list[dict[str, Any]]] = {}
        seed = seed_of(student_id or "anonymous", resolved.topic_key, "subject-round")
        for chosen in plan.tested:
            generated = G.tasks_for_skill(
                chosen.skill.id,
                seed=seed,
                topic_title=resolved.title,
                level_hint=resolved.level_hint,
                count=chosen.items,
            )
            tasks[chosen.skill.id] = [G.public_task(t) for t in generated.tasks]
        chain = CUR.chain_for(resolved)
        pair = M.pair_for(resolved.topic_key)
        payload = plan.as_dict(with_tasks=True, tasks=tasks)
        payload["topic"] = {**payload["topic"], **{"level_hint": resolved.level_hint, "resolved_from": resolved.source, "bank_code": resolved.bank_code}}
        payload["round"] = {
            "stages": [{"key": k, "title": t} for k, t in STAGES],
            "estimated_seconds": plan.estimated_seconds
            + (L.MAX_ITEMS * L.SECONDS_PER_RUNG if chain else 0)
            + (2 * (M.LESSON_SECONDS + 3 * 40) if pair else 0),
            "ladder_available": chain is not None,
            "method_test_available": pair is not None,
            "ladder_note": (
                f"{chain.depth} nodes, {len(chain.sub_areas())} sub-areas, up to {L.MAX_ITEMS} items"
                if chain
                else "no authored chain for this topic: the ladder is skipped and says so"
            ),
            "method_note": (
                f"two matched concepts ({pair.concepts[0].title}, {pair.concepts[1].title})"
                if pair
                else "no authored lesson pair for this topic: the pop-up lesson is skipped and says so"
            ),
        }
        return payload

    def _resolve_any(self, topic: str | Mapping[str, Any] | Resolved, *, student_id: str | None = None) -> Resolved:
        if isinstance(topic, Resolved):
            return topic
        if isinstance(topic, Mapping):
            if topic.get("normalised") or topic.get("source"):
                return Resolved(**{k: v for k, v in topic.items() if k in Resolved.__dataclass_fields__})  # type: ignore[arg-type]
            return self._resolve_text(student_id, str(topic.get("topic") or topic.get("text") or ""))
        return self._resolve_text(student_id, str(topic or ""))

    def _resolve_text(self, student_id: str | None, text: str) -> Resolved:
        if not str(text or "").strip():
            raise SubjectsError("a topic is needed before anything can be planned")
        with self._sess() as s:
            ids = self._topic_ids(s)
        resolved = resolve_topic(str(text), bank=BANK.topic_catalog(), topic_ids=ids)
        return refine_with_model(resolved, llm=self.llm, student_id=student_id)

    # ------------------------------------------------------------------ #
    # the round: start, view, answer, finish
    # ------------------------------------------------------------------ #
    def start_round(self, student_id: str, *, topic: str, seed: int | None = None,
                    selected_skills: list[str] | None = None, comments: str = "") -> dict[str, Any]:
        """Open a round for a topic, or hand back the unfinished one for the same topic.

        One open round per (learner, topic): two would be two ladders climbing at once and one
        snapshot that could not say which sitting it described.
        """
        resolved = self._resolve_text(student_id, topic)
        leanings = rate_leanings(resolved.as_dict(), llm=self.llm, student_id=student_id)
        plan = P.plan_tests(leanings)
        if selected_skills is not None:
            from dataclasses import replace

            selected = set(selected_skills)
            offered = {item.skill.id for item in plan.tested}
            if not selected or not selected <= offered:
                raise SubjectsError("Select at least one skill from the proposed outline before starting")
            kept = tuple(item for item in plan.tested if item.skill.id in selected)
            removed = tuple(
                P.SkippedSkill(skill=item.skill, rating=item.rating, reason="You chose not to include this in the outline")
                for item in plan.tested if item.skill.id not in selected
            )
            plan = replace(
                plan, tested=kept, skipped=(*plan.skipped, *removed),
                estimated_seconds=sum(item.seconds for item in kept),
                cross_check=plan.cross_check if plan.cross_check in selected else None,
                reason="Your reviewed outline: " + ", ".join(item.skill.title for item in kept) + ". Unchecked skills are not measured.",
            )
        review = {"selected_skills": list(plan.tested_ids), "comments": comments.strip()[:2000]}
        with self._sess() as s:
            existing = s.execute(
                select(SubjectRound)
                .where(
                    SubjectRound.student_id == student_id,
                    SubjectRound.topic_key == resolved.topic_key,
                    SubjectRound.status == "in_progress",
                )
                .order_by(SubjectRound.started_at.desc())
            ).scalars().first()
            if existing is not None:
                stored_review = (existing.plan or {}).get("learner_review", {})
                if selected_skills is not None and (
                    set(selected_skills) != {item["id"] for item in (existing.plan or {}).get("tested", [])}
                    or review["comments"] != stored_review.get("comments", "")
                ):
                    raise SubjectsError("This topic already has an unfinished round. Resume it from Home before changing its saved outline")
                return self.view(student_id, existing.id)
            row = SubjectRound(
                student_id=student_id,
                topic_key=resolved.topic_key,
                topic_title=resolved.title,
                family=resolved.family,
                seed=int(seed if seed is not None else seed_of(student_id, resolved.topic_key, "subject-round")),
                stage="skills",
                resolved=resolved.as_dict(),
                leanings=leanings.as_dict(),
                plan={**plan.as_dict(), "learner_review": review},
                completion={"stages_seen": ["skills"]},
            )
            s.add(row)
            s.flush()
            run_id = row.id
        return self.view(student_id, run_id)

    def view(self, student_id: str, run_id: str) -> dict[str, Any]:
        """Everything a screen needs: what is next, how far along, and what has been measured so far."""
        with self._sess() as s:
            run = self._run(s, run_id, student_id)
            trials = self._trials(run_id, session=s)
            resolved = _resolved_of(run)
            leanings = _leanings_of(run)
            plan = _plan_of(run, leanings)
            step = self._next_step(run, trials, resolved, plan, s)
            return {
                "run_id": run.id,
                "student_id": run.student_id,
                "status": run.status,
                "stage": step.stage,
                "stage_title": dict(STAGES).get(step.stage, step.stage),
                "kind": step.kind,
                "task": dict(step.task) if step.task else None,
                "step_id": step.step_id,
                "progress": dict(step.progress or {}),
                "note": step.note,
                "topic": {
                    "key": run.topic_key,
                    "title": run.topic_title,
                    "family": run.family,
                    "level_hint": resolved.level_hint,
                    "resolved_from": resolved.source,
                    "raw": resolved.raw,
                },
                "plan": {**plan.as_dict(), "learner_review": (run.plan or {}).get("learner_review")},
                "reason": plan.reason,
                "taxonomy_version": TAXONOMY_VERSION,
                "measured_trials": len([t for t in trials if t.phase == "measure" and t.correct is not None]),
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "ladder": self._ladder_payload(run, trials, resolved, s) if step.stage in ("ladder", "method", "snapshot") else None,
                "method_test": self._method_payload(run, trials, resolved, s, with_items=step.stage == "method")
                if step.stage in ("method", "snapshot")
                else None,
            }

    # -- the task stream ------------------------------------------------ #
    def _skill_tasks(self, run: SubjectRound, plan: P.TestPlan, resolved: Resolved) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for chosen in plan.tested:
            generated = G.tasks_for_skill(
                chosen.skill.id,
                seed=int(run.seed or 0),
                topic_title=resolved.title,
                level_hint=resolved.level_hint,
                count=chosen.items,
            )
            out.extend(generated.tasks)
        return out

    def _next_step(self, run: SubjectRound, trials: Sequence[SubjectTrial], resolved: Resolved, plan: P.TestPlan, s: Any) -> Step:
        answered = {str(t.task_id) for t in trials}
        progress: dict[str, Any] = {
            "skills_done": len({t.skill for t in trials if t.stage == "skills" and t.correct is not None}),
            "skills_planned": len(plan.tested),
            "ladder_items": len([t for t in trials if t.stage == "ladder"]),
            "ladder_max_items": L.MAX_ITEMS,
            "method_items": len([t for t in trials if t.stage in ("method_test",)]),
            "answered": len(trials),
            "stage": run.stage,
            # Always present, rung or not: a screen reading the position after the ladder has
            # finished should get "no rung" rather than a missing key.
            "rung": None,
            "why": "",
        }

        if run.status != "in_progress":
            return Step(stage="snapshot", kind="done", progress=progress, note="this round is finished; the subject map is what it produced")

        # 1) skills
        tasks = self._skill_tasks(run, plan, resolved)
        for index, task in enumerate(tasks):
            if str(task.get("id")) not in answered:
                progress["skill_item"] = index + 1
                progress["skill_items"] = len(tasks)
                return Step(
                    stage="skills",
                    kind="task",
                    task=G.public_task(task),
                    task_full=task,
                    progress=progress,
                    note=f"skill {index + 1} of {len(tasks)}: {_skill_of(task).title}",
                )
        # 2) ladder
        chain = CUR.chain_for(resolved)
        if chain is not None:
            state = self._ladder_state(chain, trials)
            if not state.done:
                step = L.next_step(state, chain)
                if step is not None:
                    progress["ladder_items"] = state.index
                    progress["rung"] = step.rung
                    progress["sub_area"] = step.sub_area
                    progress["why"] = step.why
                    return Step(
                        stage="ladder",
                        kind="task",
                        task=step.public(),
                        task_full=dict(step.task_full),
                        step_id=f"{run.id}.{state.index}",
                        progress=progress,
                        note=step.why,
                    )
            else:
                progress["ladder_items"] = state.index
                progress["ladder_stop"] = state.stop_reason
        else:
            progress["ladder_skipped"] = "no authored prerequisite chain for this topic"

        # 3) the pop-up lesson
        pair = M.pair_for(run.topic_key)
        if pair is not None:
            mt_tasks = self._method_tasks(run, pair, s)
            if mt_tasks is None:
                progress["method_skipped"] = "the lesson could not be built for this topic"
            else:
                for position, task in enumerate(mt_tasks):
                    if str(task.get("id")) not in answered:
                        progress["method_item"] = position + 1
                        progress["method_items"] = len(mt_tasks)
                        kind = "lesson" if task.get("phase") == "show" else "task"
                        return Step(
                            stage="method",
                            kind=kind,
                            task=G.public_task(task) if kind == "task" else _lesson_payload(task),
                            task_full=task,
                            progress=progress,
                            note=str(task.get("note") or task.get("instructions") or ""),
                        )
        else:
            progress["method_skipped"] = "no authored lesson pair for this topic"

        return Step(stage="snapshot", kind="done", progress=progress, note="everything this round can measure has been measured; finish it to get the subject map")

    # -- answering ------------------------------------------------------ #
    def submit_trial(
        self,
        student_id: str,
        run_id: str,
        *,
        task_id: str,
        response: Mapping[str, Any],
        latency_ms: int | None = None,
        wager: int | None = None,
    ) -> dict[str, Any]:
        """Judge the answer to whatever the round is offering, and hand back the next thing.

        Two stages come through here — the skill games and the pop-up lesson — because both are
        "answer the task on offer" and both need the same three guarantees: the task really is the one
        on offer, a re-sent answer is a duplicate rather than a second measurement, and the score is
        computed from the server's copy of the task (never from what the browser was sent).

        Ladder answers go to :meth:`ladder_step` instead, because a ladder answer moves the staircase
        and the response has to carry the new position.

        Idempotency is by ``task_id``: the trial table's unique ``(run_id, task_id, index)`` is the
        same rule the evidence log keeps by ``event_id``, applied where the row is written.
        """
        duplicate = False
        scored: dict[str, Any] = {}
        method_done = False
        with self._sess() as s:
            run = self._run(s, run_id, student_id)
            if run.status != "in_progress":
                raise SubjectsError(f"round_finished:{run_id}")
            trials = self._trials(run_id, session=s)
            already = next((t for t in trials if t.task_id == task_id), None)
            if already is not None:
                duplicate = True
                scored = dict(already.score or {})
            else:
                resolved = _resolved_of(run)
                plan = _plan_of(run, _leanings_of(run))
                step = self._next_step(run, trials, resolved, plan, s)
                offered = str((step.task or {}).get("id") or (step.task_full or {}).get("id") or "")
                if step.kind == "done" or not offered:
                    raise SubjectsError(
                        f"task {task_id!r} is not on offer: the round has measured everything it can"
                    )
                if offered != task_id:
                    raise SubjectsError(f"task {task_id!r} is not the task on offer (on offer: {offered!r})")
                if step.stage == "ladder":
                    raise SubjectsError(
                        "the task on offer is a ladder step, which goes to POST /api/v1/subjects/ladder/{step}"
                    )
                if wager is not None and not 0 <= int(wager) <= MAX_WAGER:
                    raise SubjectsError(f"a wager is 0 to {MAX_WAGER} coins")
                full = dict(step.task_full or {})
                scored = G.score_task(full, dict(response or {}))
                row = self._record(
                    s,
                    run,
                    trials,
                    stage="skills" if step.stage == "skills" else "method_test",
                    task=full,
                    response=response or {},
                    scored=scored,
                    skill=str(full.get("skill") or "") or None,
                    method=str(full.get("method") or "") or None,
                    concept=str(full.get("concept") or "") or None,
                    latency_ms=latency_ms,
                    wager=wager,
                )
                self._ingest(s, student_id, run, row, resolved, scored)
                s.flush()
                if step.stage == "method":
                    method_done = self._settle_method(s, run, resolved, student_id)
        out = self.view(student_id, run_id)
        out["scored"] = scored
        out["duplicate"] = duplicate
        if method_done:
            out["method_settled"] = True
        return out

    def _settle_method(self, s: Any, run: SubjectRound, resolved: Resolved, student_id: str, *, partial: bool = False) -> bool:
        """Score both legs once all six answers are in, and book the next-day recheck.

        Called from the trial path (a round that walks the lesson one task at a time) and, with
        ``partial=True``, from :meth:`finish_round` — a learner who stops part-way still gets a result,
        with what was not answered reported as not measured rather than as wrong. Without ``partial``
        an unfinished lesson is left alone: settling it early would freeze a comparison the design has
        not earned, and the row could not be settled again when the rest of the answers arrived.
        """
        row = self._method_row(s, run, create=False)
        if row is None or row.status != "open":
            return False
        pair = M.pair_for(run.topic_key)
        if pair is None:
            return False
        trials = self._trials(run.id, session=s)
        legs = [dict(leg) for leg in (row.legs or [])]
        results: list[M.LegResult] = []
        p_know, p_note = self._p_know(s, student_id, resolved)
        answered_all = True
        for leg in legs:
            concept = next((c for c in pair.concepts if c.key == leg.get("concept")), None)
            if concept is None:
                return False
            rows = [
                t
                for t in trials
                if t.stage == "method_test" and t.concept == concept.key and t.phase == "measure"
            ]
            if len(rows) < len(concept.questions):
                answered_all = False
            ordered = sorted(rows, key=lambda row: row.index)
            answers = []
            for t in ordered:
                given = {k: v for k, v in dict(t.response or {}).items() if k != "scored"}
                given["latency_ms"] = t.latency_ms
                answers.append(given)
            # The scores already made when each answer arrived, against the task the learner was
            # actually shown. Re-scoring here would mean re-deriving the shuffle, and any drift
            # between the two would mark a right answer wrong.
            results.append(
                M.score_leg(
                    leg=str(leg.get("leg") or "a"),
                    concept=concept,
                    method=str(leg.get("method") or "text"),
                    answers=answers,
                    p_know=p_know,
                    lesson_seconds=M.LESSON_SECONDS,
                    scored=[dict(t.score or {}) for t in ordered],
                )
            )
        if not answered_all and not partial:
            return False
        if not answered_all:
            # Part-way: score what exists, but say plainly that the comparison is not the one the
            # design asks for, rather than reporting a half-answered test as a finding.
            for r in results:
                object.__setattr__(
                    r,
                    "note",
                    (r.note + ". " if r.note else "")
                    + "the round stopped before both lessons were answered, so this is not the matched comparison §6.5 asks for",
                )
        comparison = M.compare(results, p_know=p_know)
        comparison["p_know_basis"] = p_note
        comparison["complete"] = answered_all
        row.results = comparison
        row.status = "complete"
        row.finished_at = utcnow()
        row.recheck_at = utcnow() + RECHECK_AFTER
        row.recheck = {
            "due_at": row.recheck_at.isoformat(),
            "items": M.recheck_for(results, pair, seed=int(row.seed or 0)),
            "why": "a gain that has gone by tomorrow was not a gain: the same questions come back once, the next day",
        }
        s.flush()
        self._ingest_method(s, student_id, row, resolved, results, comparison)
        s.flush()
        return True

    def ladder_step(
        self,
        student_id: str,
        step: str,
        *,
        response: Mapping[str, Any] | None = None,
        latency_ms: int | None = None,
        wager: int | None = None,
    ) -> dict[str, Any]:
        """``POST /api/v1/subjects/ladder/{step}``: one answer on the staircase, and the next position.

        ``step`` is the token the previous response issued (``<run_id>.<item number>``). It names the
        run so the server can check the round belongs to the caller, and the item number so a re-sent
        answer is recognised as a duplicate rather than recorded twice.

        The position is rebuilt from the recorded trials on every call (:meth:`_ladder_state`), so
        nothing is held in memory between requests: a learner can close the tab mid-climb, come back
        on another device, and be offered the same rung of the same node.
        """
        run_id, _, raw_index = str(step).partition(".")
        if not run_id or not raw_index.isdigit():
            raise SubjectsError("a ladder step is the token the last response gave you")
        index = int(raw_index)
        duplicate = False
        scored: dict[str, Any] = {}
        peek_only = response is None
        with self._sess() as s:
            run = self._run(s, run_id, student_id)
            if run.status != "in_progress":
                raise SubjectsError(f"round_finished:{run_id}")
            resolved = _resolved_of(run)
            chain = CUR.chain_for(resolved)
            if chain is None:
                raise SubjectsError("this topic has no authored prerequisite chain, so there is no ladder to climb")
            trials = self._trials(run_id, session=s)
            ladder_trials = [t for t in trials if t.stage == "ladder"]
            state = self._ladder_state(chain, trials)
            already = next((t for t in ladder_trials if t.index == index), None)
            if already is not None:
                duplicate = True
                scored = dict(already.score or {})
            elif peek_only:
                current = L.next_step(state, chain)
                scored = {}
            else:
                if index != state.index:
                    raise SubjectsError(
                        f"step {index} is not the step on offer (the ladder is at item {state.index}); "
                        "a re-sent answer is answered from the record, not recorded twice"
                    )
                current = L.next_step(state, chain)
                if current is None:
                    raise SubjectsError("the ladder has finished; finish the round to see the subject map")
                if wager is not None and not 0 <= int(wager) <= MAX_WAGER:
                    raise SubjectsError(f"a wager is 0 to {MAX_WAGER} coins")
                scored = L.score_step(current, dict(response or {}))
                row = self._record(
                    s,
                    run,
                    trials,
                    stage="ladder",
                    task=dict(current.task_full),
                    response=response or {},
                    scored=scored,
                    node_id=current.node_id,
                    sub_area=current.sub_area,
                    rung=current.rung,
                    latency_ms=latency_ms,
                    wager=wager,
                )
                self._ingest(s, student_id, run, row, resolved, scored)
                s.flush()
            step_payload = None
            if not duplicate:
                nxt = L.next_step(self._ladder_state(chain, self._trials(run_id, session=s)), chain)
                step_payload = nxt.public() if nxt else None
        out = self.view(student_id, run_id)
        out["scored"] = scored
        out["duplicate"] = duplicate
        out["step"] = step_payload
        return out

    def _ladder_state(self, chain: L.Chain, trials: Sequence[SubjectTrial]) -> L.LadderState:
        """Replay the staircase over the recorded answers. A pure function of the trials."""
        state = L.start(chain)
        rows = sorted((t for t in trials if t.stage == "ladder"), key=lambda t: t.index)
        for row in rows:
            step = L.next_step(state, chain)
            if step is None:
                break
            scored = dict(row.score or {})
            state = L.advance(
                state,
                chain,
                step=step,
                scored=scored,
                latency_ms=row.latency_ms,
                wager=row.wager,
            )
        return state

    # -- the pop-up lesson --------------------------------------------- #
    def _method_row(self, s: Any, run: SubjectRound, *, create: bool = True) -> MethodTestRun | None:
        row = s.execute(
            select(MethodTestRun).where(MethodTestRun.run_id == run.id).order_by(MethodTestRun.created_at.desc())
        ).scalars().first()
        if row is not None or not create:
            return row
        pair = M.pair_for(run.topic_key)
        if pair is None:
            return None
        assignment = M.assign(run.student_id, run.topic_key)
        legs_a, legs_b = assignment.legs(pair)
        row = MethodTestRun(
            run_id=run.id,
            student_id=run.student_id,
            topic_key=run.topic_key,
            pair=run.topic_key,
            cell=assignment.cell,
            seed=int(run.seed or 0),
            status="open",
            assignment=assignment.as_dict(pair=pair),
            legs=[legs_a, legs_b],
        )
        s.add(row)
        s.flush()
        return row

    def _method_tasks(self, run: SubjectRound, pair: M.Pair, s: Any) -> list[dict[str, Any]] | None:
        """The lesson stream: lesson A, its three questions, lesson B, its three questions."""
        row = self._method_row(s, run)
        if row is None:
            return None
        legs = list(row.legs or [])
        out: list[dict[str, Any]] = []
        for n, leg in enumerate(legs):
            concept = next((c for c in pair.concepts if c.key == leg.get("concept")), None)
            if concept is None:
                return None
            lesson = dict(leg.get("lesson") or {})
            out.append(
                {
                    "id": f"{concept.key}_lesson",
                    "stage": "method_test",
                    "kind": "lesson",
                    "phase": "show",
                    "leg": leg.get("leg"),
                    "concept": concept.key,
                    "concept_title": concept.title,
                    "method": leg.get("method"),
                    "method_label": leg.get("method_label"),
                    "seconds": M.LESSON_SECONDS,
                    "lesson": lesson,
                    "instructions": "Ninety seconds, then three questions. Read it as it is given: the format is part of what is being measured.",
                    "note": (
                        f"taught with {leg.get('method_label')} — dealt by seeded code (cell "
                        f"{int(row.cell) + 1} of {M.N_CELLS}), not chosen by you and not chosen by a model"
                    ),
                    "wagerable": False,
                    "questions_match": pair.question_format,
                }
            )
            served = M.served_items(M.assign(run.student_id, run.topic_key), concept)
            for q_index, question in enumerate(served):
                task = {
                    **dict(question),
                    "id": f"{concept.key}_q{q_index + 1}",
                    "stage": "method_test",
                    "kind": "choice",
                    "phase": "measure",
                    "leg": leg.get("leg"),
                    "concept": concept.key,
                    "concept_title": concept.title,
                    "method": leg.get("method"),
                    "method_label": leg.get("method_label"),
                    "seconds": 40,
                    "instructions": "Three questions after each lesson, in the same format whichever way the lesson was taught.",
                    "wagerable": True,
                    "item_of": q_index + 1,
                    "items_total": M.QUESTIONS_PER_CONCEPT,
                }
                # ``served_items`` already shuffled the options for this learner, so the answer index
                # here is the one that matches what they are shown.
                out.append(task)
        return out

    def method_test(
        self,
        student_id: str,
        *,
        topic: str | None = None,
        run_id: str | None = None,
        answers: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """``POST /api/v1/subjects/method-test``: open the pop-up lesson, or score the answers to it.

        Without ``answers`` it opens (or resumes) the test and returns both legs: the two lessons and
        the six questions, with the assignment that says which method taught which concept and that a
        seeded step in code chose it. With ``answers`` it scores them, compares each leg against what
        was *expected* of this learner, and books the next-day recheck.

        The comparison is between residuals (actual minus expected), never between raw scores: a
        strong learner who gets three out of three on both legs has learned nothing measurable from
        either format, and calling that a preference would be the exact error §6.5 rules out.
        """
        if not run_id and not topic:
            raise SubjectsError("a topic (or a run) is needed before a lesson can be dealt")
        resolved = None if run_id else self._resolve_text(student_id, str(topic))
        with self._sess() as s:
            run = self._run(s, run_id, student_id) if run_id else None
            if run is not None:
                resolved = _resolved_of(run)
            assert resolved is not None
            pair = M.pair_for(resolved.topic_key)
            if pair is None:
                return {
                    "available": False,
                    "topic": resolved.as_dict(),
                    "note": (
                        f"we have no authored ninety-second lesson pair for {resolved.title or 'this topic'} yet. "
                        "The pop-up lesson is skipped rather than invented: a lesson written on the spot "
                        "could not be matched to its partner, and an unmatched pair measures nothing."
                    ),
                    "methods": M.methods_payload(),
                    "topics_with_a_lesson": sorted(M.PAIRS.keys()),
                }
            if run is None:
                # A standalone pop-up lesson, not inside a round: it gets its own row so the recheck
                # can still find it tomorrow.
                row = s.execute(
                    select(MethodTestRun)
                    .where(
                        MethodTestRun.student_id == student_id,
                        MethodTestRun.topic_key == resolved.topic_key,
                        MethodTestRun.run_id.is_(None),
                        MethodTestRun.status == "open",
                    )
                    .order_by(MethodTestRun.created_at.desc())
                ).scalars().first()
                if row is None:
                    assignment = M.assign(student_id, resolved.topic_key)
                    leg_a, leg_b = assignment.legs(pair)
                    row = MethodTestRun(
                        run_id=None,
                        student_id=student_id,
                        topic_key=resolved.topic_key,
                        pair=resolved.topic_key,
                        cell=assignment.cell,
                        seed=seed_of(student_id, resolved.topic_key, "subject-round"),
                        status="open",
                        assignment=assignment.as_dict(pair=pair),
                        legs=[leg_a, leg_b],
                    )
                    s.add(row)
                    s.flush()
            else:
                row = self._method_row(s, run)
            if row is None:  # pragma: no cover - pair is not None above, so a row is always made
                raise SubjectsError("the lesson could not be opened")
            legs = [dict(leg) for leg in (row.legs or [])]
            payload: dict[str, Any] = {
                "available": True,
                "method_test_id": row.id,
                "run_id": row.run_id,
                "status": row.status,
                "topic": {"key": row.topic_key, "title": resolved.title, "family": resolved.family},
                "assignment": dict(row.assignment or {}),
                "matched": pair.why_matched,
                "question_format": pair.question_format,
                "legs": legs,
                "lesson_seconds": M.LESSON_SECONDS,
                "questions_per_concept": M.QUESTIONS_PER_CONCEPT,
                "note": (
                    "Two concepts of the same difficulty, three questions each in the same format. The "
                    "method and the order were dealt by a seeded step in code, so no learner picks their "
                    "own format and the twelve combinations are spread evenly across learners."
                ),
                "taxonomy_version": TAXONOMY_VERSION,
                "results": dict(row.results or {}) or None,
                "recheck": dict(row.recheck or {}) or None,
                "recheck_at": row.recheck_at.isoformat() if row.recheck_at else None,
            }
            if answers is None:
                return payload

            # --- scoring ------------------------------------------------ #
            if row.status == "complete":
                payload["duplicate"] = True
                payload["note"] = "this lesson was already scored; here is the result it produced"
                return payload
            given = [dict(a) for a in answers]
            if len(given) < 6:
                raise SubjectsError(
                    f"six answers are needed to score both legs (three per concept); {len(given)} were sent"
                )
            p_know, p_note = self._p_know(s, student_id, resolved)
            results: list[M.LegResult] = []
            for leg, chunk in zip(legs, (given[:3], given[3:6])):
                concept = next((c for c in pair.concepts if c.key == leg.get("concept")), None)
                if concept is None:
                    raise SubjectsError(f"the answer refers to a concept ({leg.get('concept')}) this topic's pair does not have")
                results.append(
                    M.score_leg(
                        leg=str(leg.get("leg") or "a"),
                        concept=concept,
                        method=str(leg.get("method") or "text"),
                        answers=chunk,
                        p_know=p_know,
                        lesson_seconds=int(leg.get("seconds") or M.LESSON_SECONDS),
                        # The same seeded shuffle that produced the items this learner was sent, so
                        # the answers are read against the order they were shown in.
                        assignment=M.assign(student_id, resolved.topic_key),
                    )
                )
            comparison = M.compare(results, p_know=p_know)
            comparison["p_know_basis"] = p_note
            row.results = comparison
            row.status = "complete"
            row.finished_at = utcnow()
            row.recheck_at = utcnow() + RECHECK_AFTER
            row.recheck = {
                "due_at": row.recheck_at.isoformat(),
                "items": M.recheck_for(results, pair, seed=int(row.seed or 0)),
                "why": "a gain that has gone by tomorrow was not a gain: the same questions come back once, the next day",
            }
            s.flush()
            self._ingest_method(s, student_id, row, resolved, results, comparison)
            s.flush()
            payload.update(
                {
                    "status": row.status,
                    "results": comparison,
                    "recheck": row.recheck,
                    "recheck_at": row.recheck_at.isoformat(),
                    "duplicate": False,
                }
            )
            return payload

    def _p_know(self, s: Any, student_id: str, resolved: Resolved) -> tuple[float, str]:
        """The learner's own estimate for this topic, read from the store snapshot — never recomputed.

        With no evidence yet the app's cold-start prior (0.20) is used, and the result says so: an
        expectation nobody can see the basis of is an invented statistic.
        """
        if self.state is None:
            return 0.2, "no store is attached, so the expectation starts from the cold-start prior of 0.20"
        try:
            snapshot = self.state.get_snapshot(student_id)
        except Exception:  # pragma: no cover - a store that cannot be read must not break a lesson
            log.exception("subjects: could not read the learner snapshot")
            return 0.2, "the learner model could not be read, so the expectation starts from the cold-start prior of 0.20"
        topic_id = resolved.bank_topic_id
        if topic_id:
            value = snapshot.p_know(topic_id, default=0.2)
            return float(value), (
                f"your own estimate for {resolved.title} from the learner model (p_know {round(float(value), 3)}), "
                "read from the store rather than recomputed here"
            )
        return 0.2, (
            f"there is no topic row for {resolved.title or 'this topic'} in the curriculum yet, so the "
            "expectation starts from the app's cold-start prior of 0.20 and says so"
        )

    # -- recording and evidence ---------------------------------------- #
    def _record(
        self,
        s: Any,
        run: SubjectRound,
        trials: Sequence[SubjectTrial],
        *,
        stage: str,
        task: Mapping[str, Any],
        response: Mapping[str, Any],
        scored: Mapping[str, Any],
        skill: str | None = None,
        node_id: str | None = None,
        sub_area: str | None = None,
        rung: int | None = None,
        method: str | None = None,
        concept: str | None = None,
        latency_ms: int | None = None,
        wager: int | None = None,
    ) -> SubjectTrial:
        correct = scored.get("correct")
        phase = str(task.get("phase") or "measure")
        if phase != "measure":
            correct = None  # a lesson or a study card is not a measurement and is never reported as one
        row = SubjectTrial(
            run_id=run.id,
            student_id=run.student_id,
            stage=stage,
            skill=skill or (str(task.get("skill")) if task.get("skill") else None),
            node_id=node_id or (str(task.get("node")) if task.get("node") else None),
            sub_area=sub_area or (str(task.get("sub_area")) if task.get("sub_area") else None),
            rung=rung if rung is not None else (int(task["rung"]) if task.get("rung") is not None else None),
            method=method or (str(task.get("method")) if task.get("method") else None),
            concept=concept or (str(task.get("concept")) if task.get("concept") else None),
            task_id=str(task.get("id") or ""),
            index=len(trials),
            phase=phase,
            correct=bool(correct) if correct is not None else None,
            latency_ms=int(latency_ms) if latency_ms is not None else None,
            wager=int(wager) if wager is not None else None,
            task=dict(task),
            response={**dict(response or {}), "scored": dict(scored)},
            score=dict(scored),
        )
        s.add(row)
        s.flush()
        return row

    def _event(self, run: SubjectRound, row: SubjectTrial, resolved: Resolved, scored: Mapping[str, Any]) -> Any:
        """One evidence event for one trial. The event id is the trial id, which is what makes a
        replayed round a no-op rather than a second measurement (§2A's idempotency rule)."""
        from ..state import EvidenceEvent

        kind = {
            "skills": "subject_skill",
            "ladder": "subject_ladder",
            "method_test": "subject_method_test",
            "method_recheck": "subject_method_recheck",
        }.get(row.stage, f"subject_{row.stage}")
        payload: dict[str, Any] = {
            "stage": row.stage,
            "topic_key": run.topic_key,
            "taxonomy_version": TAXONOMY_VERSION,
            "phase": row.phase,
        }
        if row.skill:
            payload.update({"skill": row.skill, "family": family_of_skill(row.skill)})
        if row.node_id:
            payload.update({"node": row.node_id, "rung": row.rung, "sub_area": row.sub_area})
        if row.method:
            payload.update({"method": row.method, "concept": row.concept})
        payload["scored"] = {k: v for k, v in dict(scored).items() if isinstance(v, (str, int, float, bool, type(None)))}
        return EvidenceEvent(
            event_id=f"{row.id}",
            kind=kind,
            source_type="controlled",
            # Evidence lands on a real topic row when the ladder node is one; a skill is not a topic,
            # and inventing a topic id for it would put a number in the learner model that nothing
            # else in the app can interpret.
            topic_id=_topic_id_for(row, resolved),
            item_id=str(row.task_id),
            correct=bool(row.correct) if row.correct is not None else None,
            score=None,
            payload=payload,
        )

    def _ingest(self, s: Any, student_id: str, run: SubjectRound, row: SubjectTrial, resolved: Resolved, scored: Mapping[str, Any]) -> None:
        """Write the trial into the shared evidence log, in the caller's transaction.

        ``session=s`` is the whole point: the store opens its own connection when it is not given one,
        and SQLite's write lock is already held by this transaction, so the ingest would wait out a
        five-second timeout and fail. Passing the session makes it one commit.
        """
        if self.state is None or row.phase != "measure":
            return
        try:
            self.state.ingest(student_id, self._event(run, row, resolved, scored), module="code", session=s)
        except Exception:  # pragma: no cover - the round must never fail because the store did
            log.exception("subjects: could not ingest a trial into the evidence log")

    def _ingest_method(self, s: Any, student_id: str, row: MethodTestRun, resolved: Resolved, results: Sequence[M.LegResult], comparison: Mapping[str, Any]) -> None:
        """The pop-up lesson's evidence: what was assigned, and what happened against expectation."""
        if self.state is None:
            return
        from ..state import EvidenceEvent

        events: list[Any] = []
        assignment = dict(row.assignment or {})
        for result in results:
            events.append(
                EvidenceEvent(
                    event_id=f"{row.id}_{result.leg}_assigned",
                    kind="method_assigned",
                    source_type="controlled",
                    topic_id=resolved.bank_topic_id,
                    item_id=f"{result.concept}_lesson",
                    correct=None,
                    payload={
                        "method": result.method,
                        "concept": result.concept,
                        "topic_key": row.topic_key,
                        "design": assignment.get("design") or "counterbalanced by seeded code",
                        "cell": assignment.get("cell"),
                        "of_cells": assignment.get("of_cells"),
                        "assigned_by": "code",
                        "taxonomy_version": TAXONOMY_VERSION,
                    },
                )
            )
            events.append(
                EvidenceEvent(
                    event_id=f"{row.id}_{result.leg}_result",
                    kind="subject_method_test",
                    source_type="controlled",
                    topic_id=resolved.bank_topic_id,
                    item_id=f"{result.concept}_leg_{result.leg}",
                    # A leg is not one right-or-wrong observation, so it is recorded with no
                    # ``correct`` and its numbers in the payload: folding a three-question average
                    # into a topic's p_know as if it were one item would overstate the evidence.
                    correct=None,
                    payload={
                        "method": result.method,
                        "concept": result.concept,
                        "n": result.n,
                        "correct": result.correct,
                        "accuracy": round(result.accuracy, 3),
                        "expected": round(result.expected, 3),
                        "residual": round(result.residual, 3),
                        "range": [round(result.ci[0], 3), round(result.ci[1], 3)],
                        "evidence": result.evidence,
                        "taxonomy_version": TAXONOMY_VERSION,
                    },
                )
            )
        events.append(
            EvidenceEvent(
                event_id=f"{row.id}_comparison",
                kind="subject_method_comparison",
                # Derived by code from the two controlled legs, in the same transaction: it is a
                # computed comparison of controlled answers, not an observational signal.
                source_type="controlled",
                topic_id=None,
                item_id=row.id,
                correct=None,
                payload={
                    "n": comparison.get("n"),
                    "residual_difference": comparison.get("residual_difference"),
                    "evidence": comparison.get("evidence"),
                    "better_method": comparison.get("better_method"),
                    "topic_key": row.topic_key,
                    "taxonomy_version": TAXONOMY_VERSION,
                },
            )
        )
        try:
            self.state.ingest(student_id, events, module="code", session=s)
        except Exception:  # pragma: no cover
            log.exception("subjects: could not ingest the method test into the evidence log")

    # -- payloads ------------------------------------------------------- #
    def _ladder_payload(self, run: SubjectRound, trials: Sequence[SubjectTrial], resolved: Resolved, s: Any) -> dict[str, Any] | None:
        chain = CUR.chain_for(resolved)
        if chain is None:
            return {
                "available": False,
                "note": "we have no authored prerequisite chain for this topic, so there are no ladder positions and none have been invented",
            }
        state = self._ladder_state(chain, trials)
        return L.placement_payload(state, chain)

    def _method_payload(self, run: SubjectRound, trials: Sequence[SubjectTrial], resolved: Resolved, s: Any, *, with_items: bool) -> dict[str, Any] | None:
        row = self._method_row(s, run, create=False)
        if row is None:
            return {
                "available": False,
                "note": "we have no authored ninety-second lesson pair for this topic yet, so the pop-up lesson was skipped rather than invented",
            }
        out: dict[str, Any] = {
            "available": True,
            "method_test_id": row.id,
            "status": row.status,
            "assignment": dict(row.assignment or {}),
            "results": dict(row.results or {}) or None,
            "recheck_at": row.recheck_at.isoformat() if row.recheck_at else None,
        }
        if with_items:
            out["legs"] = [dict(leg) for leg in (row.legs or [])]
        return out

    # -- finishing ------------------------------------------------------ #
    def finish_round(self, student_id: str, run_id: str) -> dict[str, Any]:
        """Seal the round into a subject map: what was measured, what was not, and where the ladder settled."""
        with self._sess() as s:
            run = self._run(s, run_id, student_id)
            existing = s.execute(
                select(SubjectSnapshot).where(SubjectSnapshot.run_id == run.id)
            ).scalars().first()
            already_sealed = existing is not None
            if already_sealed or run.status == "complete":
                # Sealing twice would either duplicate the map or quietly re-stamp it; the
                # round is done and the route turns this into the house 409.
                raise SubjectsError(f"round_finished:{run.id}")
            trials = self._trials(run_id, session=s)
            resolved = _resolved_of(run)
            leanings = _leanings_of(run)
            plan = _plan_of(run, leanings)
            # A learner who stops during the pop-up lesson still gets its result: whatever was
            # answered is scored, and what was not is reported as not measured.
            self._settle_method(s, run, resolved, student_id, partial=True)
            s.flush()
            trials = self._trials(run_id, session=s)
            skills = self._skill_results(trials, plan, leanings)
            ladder_payload = self._ladder_payload(run, trials, resolved, s) or {}
            method_payload = self._method_payload(run, trials, resolved, s, with_items=False) or {}
            if already_sealed:
                completion = dict(existing.completion or {})
            else:
                completion = _completion(trials, plan, ladder_payload, method_payload, run)
            row = None if already_sealed else SubjectSnapshot(
                student_id=student_id,
                run_id=run.id,
                topic_key=run.topic_key,
                topic_title=run.topic_title,
                family=run.family,
                taxonomy_version=TAXONOMY_VERSION,
                skills=skills,
                leanings=leanings.as_dict(),
                ladder=ladder_payload,
                method_test=method_payload,
                completion=completion,
            )
            if row is not None:
                s.add(row)
            run.status = "complete"
            run.stage = "snapshot"
            run.finished_at = run.finished_at or utcnow()
            run.completion = completion
            s.flush()
            topic_key = run.topic_key
        return self.snapshot(student_id, topic_key=topic_key, run_id=run_id)

    def _skill_results(self, trials: Sequence[SubjectTrial], plan: P.TestPlan, leanings: Leanings) -> list[dict[str, Any]]:
        """One row per skill in the family: measured rows and ``not_measured`` rows in the same shape.

        The two lists are merged on purpose. A screen that renders only what was measured cannot show
        the greyed "not measured yet" rows §6 asks for, and a learner who sees four skills and no
        others concludes the subject has four skills.
        """
        rows: list[dict[str, Any]] = []
        for chosen in plan.tested:
            skill_trials = [t for t in trials if t.stage == "skills" and t.skill == chosen.skill.id and t.phase == "measure"]
            scored = [t for t in skill_trials if t.correct is not None]
            n = len(scored)
            correct = sum(1 for t in scored if t.correct)
            points = sum(float((t.score or {}).get("points") or 0) for t in scored)
            max_points = sum(float((t.score or {}).get("max_points") or 0) for t in scored)
            latencies = [float(t.latency_ms) / 1000.0 for t in scored if t.latency_ms is not None]
            wagers = [int(t.wager) for t in scored if t.wager is not None]
            rows.append(
                {
                    **chosen.skill.as_dict(),
                    "status": "measured" if n else "not_measured",
                    "role": chosen.role,
                    "rating": chosen.rating,
                    "rating_label": chosen.as_dict().get("rating_label"),
                    "reason": chosen.reason,
                    "n": n,
                    "observations": n,
                    "correct": correct,
                    "points": round(points, 2),
                    "max_points": round(max_points, 2),
                    "accuracy": round(correct / n, 3) if n else None,
                    "evidence": "early signal" if n and n < 8 else ("measured" if n >= 8 else "not measured"),
                    "seconds": round(sum(latencies), 1) if latencies else None,
                    "mean_seconds": round(sum(latencies) / len(latencies), 1) if latencies else None,
                    "wagers": wagers,
                    "wager_accuracy": _wager_accuracy(scored),
                    "note": (
                        f"early signal: {n} answer(s) is under the 8 the app asks for before it calls an estimate measured"
                        if n and n < 8
                        else ("nothing was answered here, so this is not measured rather than failed" if not n else "")
                    ),
                    "score": None,
                    "taxonomy_version": TAXONOMY_VERSION,
                }
            )
        for skipped in plan.skipped:
            rows.append(
                {
                    **skipped.skill.as_dict(),
                    "status": P.NOT_MEASURED,
                    "role": "not_selected",
                    "rating": skipped.rating,
                    "rating_label": skipped.as_dict().get("rating_label"),
                    "reason": skipped.reason,
                    "n": 0,
                    "observations": 0,
                    "correct": 0,
                    "points": None,
                    "max_points": None,
                    "accuracy": None,
                    "evidence": "not measured",
                    "seconds": None,
                    "mean_seconds": None,
                    "wagers": [],
                    "wager_accuracy": None,
                    "time_capped": skipped.time_capped,
                    "note": "not measured is not zero: this round did not test it, and nothing here says anything about it",
                    "score": None,
                    "taxonomy_version": TAXONOMY_VERSION,
                }
            )
        # Unrated skills in the same family, so the map is the whole subject and not just the plan.
        seen = {r["id"] for r in rows}
        for sk in skills_for(leanings.family):
            if sk.id in seen:
                continue
            rows.append(
                {
                    **sk.as_dict(),
                    "status": P.NOT_MEASURED,
                    "role": "not_rated",
                    "rating": None,
                    "rating_label": "",
                    "reason": "this topic has no rating for this skill, so the round could not rank it",
                    "n": 0,
                    "observations": 0,
                    "correct": 0,
                    "points": None,
                    "max_points": None,
                    "accuracy": None,
                    "evidence": "not measured",
                    "seconds": None,
                    "mean_seconds": None,
                    "wagers": [],
                    "wager_accuracy": None,
                    "note": "not measured is not zero",
                    "score": None,
                    "taxonomy_version": TAXONOMY_VERSION,
                }
            )
        return rows

    def snapshot(self, student_id: str, *, topic_key: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        """The subject map screen's payload: the latest snapshot for a topic, or the latest at all."""
        with self._sess() as s:
            stmt = select(SubjectSnapshot).where(SubjectSnapshot.student_id == student_id)
            if run_id:
                stmt = stmt.where(SubjectSnapshot.run_id == run_id)
            if topic_key:
                stmt = stmt.where(SubjectSnapshot.topic_key == topic_key)
            row = s.execute(stmt.order_by(SubjectSnapshot.taken_at.desc())).scalars().first()
            if row is None:
                return {
                    "available": False,
                    "student_id": student_id,
                    "topic_key": topic_key,
                    "note": (
                        "no subject round has been finished yet, so there is no subject map. Nothing here "
                        "is a zero and nothing is missing: it has not happened."
                    ),
                    "taxonomy_version": TAXONOMY_VERSION,
                }
            skills = list(row.skills or [])
            return {
                "available": True,
                "snapshot_id": row.id,
                "run_id": row.run_id,
                "student_id": row.student_id,
                "topic": {"key": row.topic_key, "title": row.topic_title, "family": row.family},
                "taxonomy_version": row.taxonomy_version,
                "taken_at": row.taken_at.isoformat() if row.taken_at else None,
                "skills": skills,
                "measured": [r for r in skills if r.get("status") == "measured"],
                "not_measured": [r for r in skills if r.get("status") != "measured"],
                "counts": {
                    "measured": len([r for r in skills if r.get("status") == "measured"]),
                    "not_measured": len([r for r in skills if r.get("status") != "measured"]),
                },
                "reason": str((row.leanings or {}).get("reason") or ""),
                "leanings": dict(row.leanings or {}),
                "ladder": dict(row.ladder or {}),
                "method_test": dict(row.method_test or {}),
                "completion": dict(row.completion or {}),
                "note": (
                    "Every number here says how many answers are behind it. What the round did not test "
                    "is listed as not measured, which is not the same as finding nothing."
                ),
            }

    # -- the next-day recheck ------------------------------------------- #
    def due_rechecks(self, student_id: str) -> dict[str, Any]:
        """Method tests taken about a day ago and not yet rechecked (§6.5)."""
        now = utcnow()
        with self._sess() as s:
            rows = list(
                s.execute(
                    select(MethodTestRun).where(
                        MethodTestRun.student_id == student_id,
                        MethodTestRun.status == "complete",
                        MethodTestRun.recheck_done_at.is_(None),
                    )
                ).scalars()
            )
            due = []
            for row in rows:
                if row.recheck_at is None or row.recheck_at > now or now - row.recheck_at > RECHECK_WINDOW:
                    continue
                pair = M.pair_for(row.topic_key)
                if pair is None:
                    continue
                due.append(
                    {
                        "method_test_id": row.id,
                        "topic_key": row.topic_key,
                        "due_at": row.recheck_at.isoformat(),
                        "items": list((row.recheck or {}).get("items") or []),
                        # The same rows grouped by the concept they re-ask, which is the
                        # shape the recheck screen renders.
                        "concepts": list((row.recheck or {}).get("items") or []),
                        "why": str((row.recheck or {}).get("why") or ""),
                    }
                )
            return {"due": due, "count": len(due), "checked_at": now.isoformat()}

    def submit_recheck(self, student_id: str, method_test_id: str, *, answers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Score the next-day recheck and close it."""
        with self._sess() as s:
            row = s.get(MethodTestRun, method_test_id)
            if row is None or row.student_id != student_id:
                raise SubjectsError(f"unknown_method_test:{method_test_id}")
            if row.recheck_done_at is not None:
                return {"duplicate": True, "status": row.status, **dict(row.recheck or {}), "result": dict(row.results or {})}
            pair = M.pair_for(row.topic_key)
            if pair is None:
                raise SubjectsError("this topic has no authored lesson pair, so there is no recheck to score")
            items = list((row.recheck or {}).get("items") or [])
            given = list(answers or [])
            results = []
            for n, item in enumerate(items):
                chunk = given[n] if n < len(given) else []
                results.append(M.score_recheck(item, pair, chunk if isinstance(chunk, Sequence) else []))
            row.recheck = {**(row.recheck or {}), "results": results, "done_at": utcnow().isoformat()}
            row.recheck_done_at = utcnow()
            row.status = "rechecked"
            s.flush()
            return {
                "duplicate": False,
                "status": row.status,
                "method_test_id": row.id,
                "topic_key": row.topic_key,
                "results": results,
                "first_round": dict(row.results or {}),
                "note": (
                    "The recheck asks the same questions again the next day, because a gain that has "
                    "gone by then was not a gain. Two answers is a signal, not a measurement, and it is "
                    "labelled that way."
                ),
            }


# --------------------------------------------------------------------------- #
# module helpers
# --------------------------------------------------------------------------- #


def _lesson_payload(task: Mapping[str, Any]) -> dict[str, Any]:
    """A lesson as the learner sees it: the whole thing, because there is nothing to hide in it.

    A lesson is a ``phase="show"`` task, so it is never scored; passing it through ``public_task``
    would strip the ``why`` a learner is entitled to see next to a picture.
    """
    return {k: v for k, v in dict(task).items() if k not in ("answer", "answer_order", "answer_pairs", "pairs")}


def _skill_of(task: Mapping[str, Any]):
    from .taxonomy import require_skill

    return require_skill(str(task.get("skill") or ""))


def _wager_accuracy(scored: Sequence[SubjectTrial]) -> dict[str, Any] | None:
    """Did the learner's confidence match their accuracy? Two buckets, and only if both have answers."""
    rows = [(int(t.wager), bool(t.correct)) for t in scored if t.wager is not None]
    if not rows:
        return None
    confident = [c for w, c in rows if w >= 2]
    unsure = [c for w, c in rows if w < 2]
    out: dict[str, Any] = {
        "n": len(rows),
        "confident_n": len(confident),
        "unsure_n": len(unsure),
        "confident_accuracy": round(sum(confident) / len(confident), 3) if confident else None,
        "unsure_accuracy": round(sum(unsure) / len(unsure), 3) if unsure else None,
        "evidence": "early signal",
    }
    if out["confident_accuracy"] is not None and out["unsure_accuracy"] is not None:
        gap = out["confident_accuracy"] - out["unsure_accuracy"]
        out["gap"] = round(gap, 3)
        out["note"] = (
            "confidence and accuracy move together here, which is what calibrated confidence looks like"
            if gap > 0.15
            else "confidence and accuracy are close here, which at this sample size says nothing either way"
        )
    else:
        out["note"] = "only one confidence bucket was used, so there is nothing to compare yet"
    return out


def _resolved_of(run: SubjectRound) -> Resolved:
    data = dict(run.resolved or {})
    fields = set(Resolved.__dataclass_fields__)
    kwargs = {k: v for k, v in data.items() if k in fields}
    kwargs.setdefault("raw", run.topic_title)
    kwargs.setdefault("normalised", run.topic_key)
    kwargs.setdefault("topic_key", run.topic_key)
    kwargs.setdefault("title", run.topic_title)
    kwargs.setdefault("subject", "")
    kwargs.setdefault("family", run.family)
    kwargs["alternatives"] = tuple(kwargs.get("alternatives") or ())
    return Resolved(**kwargs)


def _leanings_of(run: SubjectRound) -> Leanings:
    """The ratings the round was planned with, read back from the run row.

    Re-deriving them from the taxonomy instead would silently re-plan a round that is already under
    way the day somebody edits a rating, and the learner would be answering a round that no longer
    matches the plan they were shown.
    """
    data = dict(run.leanings or {})
    if not data:
        return rate_leanings({"topic_key": run.topic_key, "title": run.topic_title, "family": run.family})
    return Leanings(
        topic_key=run.topic_key,
        title=str(data.get("title") or run.topic_title),
        subject=str(data.get("subject") or ""),
        family=str(data.get("family") or run.family),
        ratings={str(k): int(v) for k, v in (data.get("ratings") or {}).items()},
        source=str(data.get("source") or "authored"),
        taxonomy_version=str(data.get("taxonomy_version") or TAXONOMY_VERSION),
        reason=str(data.get("reason") or ""),
        validation=tuple(data.get("validation") or ()),
        model_note=str(data.get("model_note") or ""),
    )


def _plan_of(run: SubjectRound, leanings: Leanings) -> P.TestPlan:
    """The plan frozen at the start of the round, re-read rather than recomputed.

    Recomputing it from the ratings would be fine today and wrong the day somebody edits a rating:
    the learner would be answering a round that no longer matches the plan they were shown.
    """
    data = dict(run.plan or {})
    tested = [
        P.PlannedSkill(
            skill=_require(row["id"]),
            rating=int(row.get("rating") or 0),
            role=str(row.get("role") or "leaned"),
            order=int(row.get("order") or 0),
            reason=str(row.get("reason") or ""),
            items=int(row.get("items") or 3),
            seconds=int(row.get("seconds") or 40),
        )
        for row in (data.get("tested") or [])
    ]
    skipped = [
        P.SkippedSkill(
            skill=_require(row["id"]),
            rating=int(row.get("rating") or 0),
            reason=str(row.get("reason") or ""),
            status=str(row.get("status") or P.NOT_MEASURED),
            time_capped=bool(row.get("time_capped")),
        )
        for row in (data.get("not_measured") or [])
    ]
    if not tested and not skipped:
        return P.plan_tests(leanings)
    return P.TestPlan(
        topic_key=str((data.get("topic") or {}).get("key") or run.topic_key),
        topic_title=str((data.get("topic") or {}).get("title") or run.topic_title),
        family=str((data.get("topic") or {}).get("family") or run.family),
        leanings=leanings,
        tested=tuple(tested),
        skipped=tuple(skipped),
        budget_seconds=int(data.get("budget_seconds") or P.SKILL_ROUND_BUDGET_SECONDS),
        estimated_seconds=int(data.get("estimated_seconds") or 0),
        reason=str(data.get("reason") or ""),
        cross_check=data.get("cross_check"),
        time_capped=bool(data.get("time_capped")),
        taxonomy_version=str(data.get("taxonomy_version") or TAXONOMY_VERSION),
        notes=tuple(data.get("notes") or ()),
    )


def _require(skill_id: str):
    from .taxonomy import require_skill

    return require_skill(str(skill_id))


def _topic_id_for(row: SubjectTrial, resolved: Resolved) -> str | None:
    """The topic row an event belongs to, or ``None`` when it belongs to no topic.

    A ladder node that *is* a curriculum topic contributes evidence to that topic. A skill game does
    not: skills are not topics, and attaching them to one would move an estimate the app shows
    elsewhere on the strength of an answer about something else.
    """
    if row.stage == "ladder" and row.node_id:
        chain = CUR.chain_for(resolved)
        if chain is not None and chain.has(row.node_id):
            node = chain.node(row.node_id)
            if node.bank_code:
                return resolved.bank_topic_id if node.bank_code == resolved.bank_code else None
    return None


def _completion(
    trials: Sequence[SubjectTrial],
    plan: P.TestPlan,
    ladder_payload: Mapping[str, Any],
    method_payload: Mapping[str, Any],
    run: SubjectRound,
) -> dict[str, Any]:
    """What the round did and did not reach, in words, with counts next to each claim."""
    skill_trials = [t for t in trials if t.stage == "skills" and t.phase == "measure"]
    skills_answered = len({t.skill for t in skill_trials if t.correct is not None})
    ladder_items = len([t for t in trials if t.stage == "ladder"])
    method_items = len([t for t in trials if t.stage == "method_test" and t.correct is not None])
    lines: list[dict[str, str]] = []
    lines.append(
        {
            "stage": "skills",
            "done": f"{skills_answered} of {len(plan.tested)} planned skills were answered",
            "evidence": f"{len([t for t in skill_trials if t.correct is not None])} scored answer(s)",
        }
    )
    if plan.skipped:
        lines.append(
            {
                "stage": "skills",
                "done": f"{len(plan.skipped)} skill(s) in this subject were not tested and are recorded as not measured",
                "evidence": "none, and none is claimed",
            }
        )
    if ladder_payload.get("available"):
        state = dict(ladder_payload.get("state") or {})
        lines.append(
            {
                "stage": "ladder",
                "done": str(ladder_payload.get("stop_reason") or "the ladder finished"),
                "evidence": f"{ladder_items} item(s) across {ladder_payload.get('measured_sub_areas')} of {ladder_payload.get('sub_areas')} sub-areas",
            }
        )
        if state.get("bottomed_out"):
            lines.append(
                {"stage": "ladder", "done": "the ladder reached the bottom of the chain and asked whether you had met the idea", "evidence": "rung 0, a self-report"}
            )
    else:
        lines.append({"stage": "ladder", "done": "skipped: no authored prerequisite chain for this topic", "evidence": "none, and none is claimed"})
    if method_payload.get("available"):
        results = dict((method_payload.get("results") or {}))
        lines.append(
            {
                "stage": "method",
                "done": f"the pop-up lesson was {results.get('direction', 'dealt')}" if results else "the pop-up lesson was dealt but not answered",
                "evidence": f"{method_items} answer(s); {results.get('evidence', 'early signal')}",
            }
        )
    else:
        lines.append({"stage": "method", "done": "skipped: no authored lesson pair for this topic", "evidence": "none, and none is claimed"})
    return {
        "stages": lines,
        "answered": len([t for t in trials if t.correct is not None]),
        "trials": len(trials),
        "topic_key": run.topic_key,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": utcnow().isoformat(),
        "note": (
            "A subject round places you; it does not grade you. Everything it did not reach is listed "
            "as not measured rather than left out."
        ),
    }


def _suggestions(resolved: Resolved) -> list[dict[str, Any]]:
    from .resolve import suggestions

    return suggestions(resolved)


__all__ = ["MAX_WAGER", "STAGES", "Step", "Subjects", "SubjectsError", "seed_of"]
