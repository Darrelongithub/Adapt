"""AI 1 -- the Learning Intelligence.

Responsibilities (blueprint section 3), and where each one lives:

===========================  =========================================
trace topic knowledge         :meth:`LearningIntelligence.record_attempt` (BKT)
learn the model itself        :meth:`LearningIntelligence.train_params` (EM)
model forgetting/retention    :meth:`_update_schedule` (half-life regression)
accumulate evidence           :meth:`collect_evidence` -> detectors
hold hypotheses               :mod:`adapt.learning.hypotheses`
propose adaptations           :meth:`propose` -- *proposals only*
check whether they helped     :meth:`evaluate_pending_adaptations`
share state with AI 2         the DB + :meth:`profile_service.build`
===========================  =========================================

The one thing this class must never do is write ``approved_adaptations``. That
table has exactly one writer, in :mod:`adapt.services.adaptations`, and it refuses
unless a student decision is recorded. :meth:`_assert_no_direct_write` is a runtime
guard so the rule survives a careless future refactor.

LLM use is *phrasing only*: detectors and statistics run locally, then Gemini
writes the five-part proposal in natural language, and if Gemini is unavailable the
local template renders the same numbers. The analysis never depends on the network.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import func, select

from ..config import Settings, get_settings
from ..db.models import (
    ActiveAdaptation,
    AdaptationProposal,
    Attempt,
    BktParams,
    BktState,
    EvidenceRecord,
    Hypothesis,
    HypothesisStatus,
    Item,
    LearnerEvent,
    LearningSession,
    LlmCall,
    ProfileSnapshot,
    ProposalStatus,
    RetentionCheck,
    RetentionSchedule,
    StrategyStat,
    StudentPreference,
    Topic,
    TopicPrerequisite,
    utcnow,
)
from ..learning import effect as E
from ..learning import bkt as B
from ..learning import gaps as G
from ..learning import hypotheses as H
from ..learning import patterns as P
from ..learning import spacing as SP
from ..learning import strategies as ST
from ..learning.effect import evaluate_change, retention_calibration
from ..learning.profile import Directive, LearnerProfile, TopicTrace, to_llm_payload
from ..security.redact import redact_payload

log = logging.getLogger("adapt.ai1")

PROPOSAL_SPEC: dict[str, Any] = {
    "strategy": "str",
    "facts_text": "str",
    "pattern_text": "str",
    "hypothesis_text": "str",
    "caveat_text": "str",
    "change_text": "str",
    "ask_text": "str",
    "expected_effect": "str",
    "action_payload": "object",
}


def recalibrated_target(facts: dict[str, Any], *, current: float = 0.78) -> float:
    """Which way the item-difficulty target has to move, from the measurement that raised the idea.

    ``difficulty_recalibration`` shipped ``{"target": 0.78}`` as its payload -- and 0.78 is what
    the selector already aims at. So the learner was asked to approve a change that, approved,
    changed nothing: the loop proposing its own status quo. The detector that fires knows which
    way the learner is off band, and that direction is the only thing worth turning into a
    number: accuracy *below* the band means the items are too hard, so the target rises toward
    the top of the 70-85% band (easier items); *above* it, the target comes down.

    With no usable direction and no measurement, the current value is returned untouched -- it
    is better to propose nothing new than to invent a number the evidence does not support.
    """
    direction = str(facts.get("direction") or "")
    if direction == "too_hard":
        target = 0.85
    elif direction == "too_easy":
        target = 0.70
    else:
        observed, baseline = facts.get("observed"), facts.get("target_success_rate")
        if isinstance(observed, (int, float)) and isinstance(baseline, (int, float)):
            target = 0.85 if float(observed) < float(baseline) else 0.70
        elif isinstance(observed, (int, float)):
            target = float(observed) + 0.08
        else:
            return float(current)
    return round(min(0.92, max(0.60, target)), 2)

def describe_effect(effect: dict[str, Any], *, metric: str, n_after: int, min_items: int) -> str:
    """One sentence a learner can read, from the numbers the statistics already produced.

    ``Effect.explanation`` is deliberately technical -- intervals, Hedges g, the reason for the
    verdict -- and belongs under a "the numbers" disclosure. What a screen *leads* with has to say
    the same thing in plain words, including the awkward case: not enough evidence yet is a result,
    not a silence.
    """
    label = metric.replace("_", " ")
    verdict = str(effect.get("verdict") or "")

    def pct(value: Any) -> str:
        return "n/a" if value is None else f"{round(float(value) * 100)}%"

    span = f"{pct(effect.get('baseline_mean'))} before the change and {pct(effect.get('after_mean'))} after it"
    if verdict == "insufficient_evidence":
        more = max(int(min_items) - int(n_after), 1)
        return (
            f"Not enough scored answers yet to judge this -- {n_after} so far, about {more} more to go. "
            f"{label.capitalize()} stays under observation rather than being rounded off."
        )
    if verdict == "supported":
        return (
            f"{label.capitalize()} went from {span}, over {n_after} answers since you said yes -- "
            "the direction this change was supposed to move."
        )
    if verdict == "refuted":
        return (
            f"{label.capitalize()} moved the wrong way ({span}) across {n_after} answers. "
            "This change is being switched off, and the finding stays in your history."
        )
    return (
        f"No clear movement in {label} ({span}) across {n_after} answers, so this stays in force "
        "and keeps being measured."
    )




@dataclass
class Analysis:
    student_id: str
    session_id: str | None
    traces: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    evaluated: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=dict)
    plan: list[dict[str, Any]] = field(default_factory=list)
    bkt: dict[str, Any] = field(default_factory=dict)
    retention: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    provider: str = "local"
    llm: dict[str, Any] = field(default_factory=dict)
    model_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class LearningIntelligence:
    def __init__(
        self,
        session_factory,
        *,
        settings: Settings | None = None,
        llm: Any = None,
        limiter: Any = None,
    ) -> None:
        self.sf = session_factory
        self.settings = settings or get_settings()
        self.llm = llm
        self.limiter = limiter

    # ------------------------------------------------------------------ #
    # guard: AI 1 cannot apply persistent changes
    # ------------------------------------------------------------------ #

    @staticmethod
    def _assert_no_direct_write() -> None:
        """Programmers' guard, checked by tests/test_adaptation_gate.py."""
        import inspect

        for name, fn in inspect.getmembers(LearningIntelligence, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            src = inspect.getsource(fn)
            if "ActiveAdaptation(" in src and "student" not in name:
                raise AssertionError(
                    f"AI 1 method {name} constructs ActiveAdaptation; persistent changes "
                    "must go through services.adaptations.approve (student decision required)"
                )

    # ------------------------------------------------------------------ #
    # tracing: BKT + retention, updated per attempt
    # ------------------------------------------------------------------ #

    def params_for(self, student_id: str, topic_id: str, s) -> B.BKTParams:
        """Learner-level params if learned, else cohort params, else priors."""
        row = s.get(BktParams, ("global", "*"))
        topic_row = s.get(BktParams, ("global", topic_id))
        src = topic_row or row
        state = s.get(BktState, (student_id, topic_id))
        if state is not None and state.n_obs > 0:
            return B.BKTParams(
                p_init=0.2,
                p_learn=state.p_learn,
                p_guess=state.p_guess,
                p_slip=state.p_slip,
                p_forget=state.p_forget,
            )
        if src is not None:
            return B.BKTParams(
                p_init=src.p_init,
                p_learn=src.p_learn,
                p_guess=src.p_guess,
                p_slip=src.p_slip,
                p_forget=src.p_forget,
                n_observations=src.n_sequences,
            )
        return B.BKTParams()

    def record_attempt(
        self,
        *,
        student_id: str,
        topic_id: str,
        correct: bool,
        score: float | None = None,
        difficulty: float = 0.5,
        self_explain: float = 0.0,
        opportunity: bool = True,
        active_strategies: Sequence[str] = (),
    ) -> dict[str, Any]:
        """One scored observation -> trace update + schedule update + strategy credit."""
        with self.sf() as s:
            params = self.params_for(student_id, topic_id, s)
            state = s.get(BktState, (student_id, topic_id))
            first = state is None
            if state is None:
                state = BktState(
                    student_id=student_id,
                    topic_id=topic_id,
                    p_know=params.p_init,
                    p_slip=params.p_slip,
                    p_guess=params.p_guess,
                    p_learn=params.p_learn,
                    p_forget=params.p_forget,
                )
                s.add(state)
                s.flush()
            prior = state.p_know
            # Decay toward the pre-practice belief first: knowledge that has not
            # been used for 9 days is not the same belief as knowledge used an
            # hour ago, and pretending otherwise inflates mastery.
            lag_hours = (utcnow() - state.last_practice_at).total_seconds() / 3600.0 if state.last_practice_at else None
            if lag_hours and state.half_life_hours:
                state.p_know = clamp01(state.p_know * decay(state.p_know, lag_hours, state.half_life_hours))
            new_p = B.update(state.p_know, correct, params, opportunity=opportunity)
            delta = new_p - state.p_know
            state.p_know = new_p
            state.n_obs += 1
            state.correct_count += 1 if correct else 0
            state.streak = state.streak + 1 if correct else 0
            state.mastery_band = params.band(
                new_p,
                proficient=self.settings.mastery_proficient,
                developing=self.settings.mastery_developing,
                # The learner's own count on this topic -- never the cohort's, which
                # is what `params` carries when it came from `train_params`. One
                # answer is not a gap; the class's 400 answers are not this
                # learner's evidence either.
                observations=state.n_obs,
            )
            state.last_practice_at = utcnow()
            state.updated_at = utcnow()
            schedule = self._update_schedule(
                s,
                student_id=student_id,
                topic_id=topic_id,
                correct=correct,
                difficulty=difficulty,
                self_explain=self_explain,
                lag_hours=lag_hours,
                prior_correct_count=state.correct_count,
                n_obs=state.n_obs,
            )
            # credit the strategies that were active for this attempt
            if active_strategies:
                for strat in active_strategies:
                    row = s.get(StrategyStat, (student_id, strat))
                    if row is None:
                        row = StrategyStat(student_id=student_id, strategy=strat, alpha=1.0, beta=1.0)
                        s.add(row)
                        s.flush()
                    model = ST.StrategyModel.from_rows([row])
                    model.record(strat, lift=delta if score is None else float(score) - 0.5)
                    upd = model.stats[strat]
                    row.alpha, row.beta, row.n, row.mean_lift = upd.alpha, upd.beta, upd.n, upd.mean_lift
                    row.last_updated_at = utcnow()
            s.commit()
            return {
                "topic_id": topic_id,
                "lag_hours_since_practice": round(lag_hours, 3) if lag_hours else None,
                "p_know_before": round(prior, 4),
                "p_know": round(new_p, 4),
                "delta": round(delta, 4),
                "band": state.mastery_band,
                "observations": state.n_obs,
                "expected_recall": round(params.p_correct(new_p), 4),
                "half_life_hours": round(state.half_life_hours, 2) if state.half_life_hours else None,
                "due_at": schedule.due_at.isoformat() if schedule and schedule.due_at else None,
                "first_observation": first,
                "params": params.as_dict(),
            }

    def _update_schedule(
        self,
        s,
        *,
        student_id: str,
        topic_id: str,
        correct: bool,
        difficulty: float,
        self_explain: float,
        lag_hours: float | None,
        prior_correct_count: int,
        n_obs: int,
    ) -> RetentionSchedule | None:
        """Half-life regression update (PGLR labels from observed outcomes)."""
        sched = s.get(RetentionSchedule, (student_id, topic_id))
        if sched is None:
            sched = RetentionSchedule(student_id=student_id, topic_id=topic_id, half_life_hours=24.0)
            s.add(sched)
            s.flush()
        lag = float(lag_hours) if lag_hours else 0.0
        recent_success = prior_correct_count / max(1, n_obs)
        if n_obs > 1:
            row = SP.ReviewOutcome(
                reps_before=sched.reps,
                lag_hours=max(0.05, lag),
                difficulty=difficulty,
                recent_success=recent_success,
                self_explain=self_explain,
                recalled=correct,
            )
            history = self._review_history(s, student_id=student_id, topic_id=topic_id)
            history.append(row.as_row())
            model = SP.fit_half_life(history[-80:], lam=6.0)
            sched.half_life_hours = model.predict_hours(
                SP.features(
                    reps=row.reps_before + 1,
                    prev_lag_hours=row.lag_hours,
                    difficulty=difficulty,
                    recent_success=recent_success,
                    self_explain=self_explain,
                )
            )
            sched.feature_weights = [round(float(w), 4) for w in model.weights]
            sched.last_lag_hours = row.lag_hours
        sched.reps += 1
        if not correct:
            sched.lapses += 1
            sched.half_life_hours = max(0.5, float(sched.half_life_hours) * 0.5)
        sched.last_recall_at = utcnow()
        sched.last_result = correct
        target = self.settings.retention_target
        sched.due_at = utcnow() + timedelta(hours=SP.lag_for_target(sched.half_life_hours, target))
        sched.expected_recall_at_due = target
        sched.strength = 1.0 / max(0.25, float(sched.half_life_hours))
        sched.updated_at = utcnow()
        return sched

    def _review_history(self, s, *, student_id: str, topic_id: str) -> list[tuple[Any, float]]:
        """Rebuild training rows from the attempt log so the model is re-fittable.

        Deliberately derived rather than stored: the schedule must be recomputable
        from raw history after a bug fix or a schema change.
        """
        rows = (
            s.execute(
                select(Attempt)
                .where(Attempt.student_id == student_id, Attempt.topic_id == topic_id)
                .order_by(Attempt.submitted_at)
            )
            .scalars()
            .all()
        )
        out: list[tuple[Any, float]] = []
        prev_ts: datetime | None = None
        for i, a in enumerate(rows):
            lag = (a.submitted_at - prev_ts).total_seconds() / 3600.0 if (prev_ts and a.submitted_at) else 6.0
            item = s.get(Item, a.item_id)
            diff = float(item.difficulty) if item else 0.5
            row = SP.ReviewOutcome(
                reps_before=i,
                lag_hours=max(0.05, lag),
                difficulty=diff,
                recent_success=sum(1 for x in rows[:i] if x.correct) / max(1, i),
                self_explain=0.0 if a.teachback_score is None else float(a.teachback_score),
                recalled=bool(a.correct),
            )
            out.append(row.as_row())
            if a.submitted_at:
                prev_ts = a.submitted_at
        return out

    # ------------------------------------------------------------------ #
    # cohort-level BKT parameter learning
    # ------------------------------------------------------------------ #

    def train_params(
        self,
        *,
        scope: str = "global",
        min_learners: int = 3,
        min_sequences_per_topic: int = 12,
    ) -> dict[str, Any]:
        """Fit BKT parameters by EM: one cohort row, plus a row per topic with data.

        Two rows matter to :meth:`params_for`, and both are written here: the
        ``(scope, "*")`` row every learner falls back to, and one
        ``(scope, topic_id)`` row per topic that has enough of its own sequences
        to be worth estimating. Per-topic fits are what the small-data literature
        recommends (skills differ in guess/slip; a single cohort value is an
        average over every question in the bank), but they are also where the
        degeneracy problem bites, so each topic fit starts from the *cohort*
        estimate and is only written above ``min_sequences_per_topic``. Below
        that the read path falls through to the cohort row, which is the honest
        "we don't know this topic specifically" answer.
        """
        with self.sf() as s:
            stmt = (
                select(Attempt.student_id, Attempt.topic_id, Attempt.correct, Attempt.submitted_at, Attempt.score)
                .where(Attempt.correct.is_not(None))
                .order_by(Attempt.student_id, Attempt.submitted_at)
            )
            rows = s.execute(stmt).all()
        per: dict[tuple[str, str], list[int]] = {}
        for student_id, topic_id, correct, _ts, score in rows:
            val = 1 if (score if score is not None else (1.0 if correct else 0.0)) >= 0.75 else 0
            per.setdefault((str(student_id), str(topic_id)), []).append(val)
        sequences = [seq for seq in per.values() if len(seq) >= 2]
        learners = len({k[0] for k in per})
        diag: dict[str, Any] = {"learners": learners, "sequences": len(sequences), "scope": scope}
        if learners < min_learners:
            diag["skipped"] = (
                f"need >= {min_learners} learners with >= 2 scored attempts to fit cohort parameters; "
                "using literature-informed priors meanwhile"
            )
            return diag
        # ``model="online"``: fit the transition rule ``update`` actually applies.
        # Fitting the classic rule here and filtering with the online one
        # understates p_learn by roughly 6x (see the consistency test), and those
        # numbers then steer item selection through information_gain().
        params, em = B.fit_em(sequences, init=B.BKTParams(), model="online")
        if not params.bounds_ok():
            # Do not publish a parameter set that contradicts the model's own
            # meaning (guessing beating knowing). The previous row -- or the
            # priors -- stay in force, and the caller is told why.
            diag.update({k: v for k, v in em.items()})
            diag["params"] = params.as_dict()
            diag["sanity"] = (
                "degenerate fit (guess/slip outside cognitive bounds); previous "
                "parameters kept rather than written"
            )
            return diag
        by_topic: dict[str, list[list[int]]] = {}
        for (_student, topic_id), seq in per.items():
            if len(seq) >= 2:
                by_topic.setdefault(topic_id, []).append(seq)

        per_topic: dict[str, dict[str, Any]] = {}
        for topic_id, topic_sequences in sorted(by_topic.items()):
            if len(topic_sequences) < min_sequences_per_topic:
                continue
            topic_params, topic_em = B.fit_em(
                topic_sequences, init=params, model="online"
            )
            if not topic_params.bounds_ok():
                # A degenerate fit is a finding, not a parameter set: keep the
                # cohort row for this topic and say so in the diagnostics.
                diag.setdefault("topics_kept_on_cohort", []).append(topic_id)
                continue
            per_topic[topic_id] = {
                "params": topic_params,
                "sequences": len(topic_sequences),
                "log_likelihood": float(topic_em.get("log_likelihood", 0.0)),
            }

        with self.sf() as s:
            row = s.get(BktParams, (scope, "*")) or BktParams(scope=scope, topic_id="*")
            row.p_learn, row.p_guess, row.p_slip, row.p_forget, row.p_init = (
                params.p_learn,
                params.p_guess,
                params.p_slip,
                params.p_forget,
                params.p_init,
            )
            row.n_sequences = len(sequences)
            row.log_likelihood = float(em.get("log_likelihood", 0.0))
            row.trained_at = utcnow()
            row.note = json.dumps({k: v for k, v in em.items()})[:500]
            s.merge(row)
            for topic_id, fitted in per_topic.items():
                trow = s.get(BktParams, (scope, topic_id)) or BktParams(scope=scope, topic_id=topic_id)
                tp = fitted["params"]
                trow.p_learn, trow.p_guess, trow.p_slip, trow.p_forget, trow.p_init = (
                    tp.p_learn,
                    tp.p_guess,
                    tp.p_slip,
                    tp.p_forget,
                    tp.p_init,
                )
                trow.n_sequences = int(fitted["sequences"])
                trow.log_likelihood = float(fitted["log_likelihood"])
                trow.trained_at = utcnow()
                trow.note = "per-topic fit, shrunken toward the cohort estimate"
                s.merge(trow)
            stale = (
                s.execute(
                    select(BktParams).where(
                        BktParams.scope == scope, BktParams.topic_id != "*"
                    )
                )
                .scalars()
                .all()
            )
            for old_row in stale:
                if old_row.topic_id not in per_topic:
                    # A topic that stopped having enough data must not keep a
                    # parameter row nothing would refresh: fall back to cohort.
                    s.delete(old_row)
            # The parameters belong to the filter, the belief belongs to the learner.
            # Every existing trace is re-pointed at the fit now in force while its
            # p_know is left alone; otherwise `train_params` reaches only learners
            # who have not answered yet, and everyone who practised during the week
            # that produced the data keeps the priors forever. The history stays
            # coherent because anything that reads a trace replays it
            # (`rebuild_traces`) under whatever parameters are current.
            refreshed = 0
            for state in s.execute(select(BktState)).scalars().all():
                chosen = per_topic.get(str(state.topic_id))
                fitted = chosen["params"] if chosen else params
                state.p_learn = fitted.p_learn
                state.p_guess = fitted.p_guess
                state.p_slip = fitted.p_slip
                state.p_forget = fitted.p_forget
                refreshed += 1
            s.commit()
        diag.update({k: v for k, v in em.items()})
        diag["params"] = params.as_dict()
        diag["sanity"] = "ok"
        diag["topics_fitted"] = sorted(per_topic)
        diag["min_sequences_per_topic"] = min_sequences_per_topic
        diag["traces_refreshed"] = refreshed
        return diag

    # ------------------------------------------------------------------ #
    # evidence -> hypotheses -> proposals
    # ------------------------------------------------------------------ #

    def build_context(self, student_id: str, *, since: datetime | None = None) -> P.EvidenceContext:
        with self.sf() as s:
            stmt = select(Attempt).where(Attempt.student_id == student_id).order_by(Attempt.submitted_at)
            if since:
                stmt = stmt.where(Attempt.submitted_at >= since)
            attempts = s.execute(stmt).scalars().all()
            item_ids = {a.item_id for a in attempts}
            items = {
                i.id: i for i in s.execute(select(Item).where(Item.id.in_(item_ids or ["-"]))).scalars().all()
            }
            states = {
                st.topic_id: st
                for st in s.execute(select(BktState).where(BktState.student_id == student_id)).scalars().all()
            }
            prereq_rows = s.execute(select(TopicPrerequisite)).scalars().all()
            sched = {
                r.topic_id: float(r[1])
                for r in s.execute(
                    select(RetentionSchedule.topic_id, RetentionSchedule.half_life_hours).where(
                        RetentionSchedule.student_id == student_id
                    )
                ).all()
            }
            checks = s.execute(
                select(RetentionCheck).where(RetentionCheck.student_id == student_id).order_by(RetentionCheck.created_at)
            ).scalars().all()
            # teach-back score -> later accuracy on the same topic
            tb_pairs: list[tuple[float, float]] = []
            by_topic: dict[str, list[Attempt]] = {}
            for a in attempts:
                by_topic.setdefault(a.topic_id, []).append(a)
            for a in attempts:
                if a.teachback_score is None:
                    continue
                later = [x for x in by_topic.get(a.topic_id, []) if (x.submitted_at or utcnow()) > (a.submitted_at or utcnow())]
                if later:
                    tb_pairs.append((float(a.teachback_score), float(sum(1 for x in later if x.correct) / len(later))))
            rows: list[P.AttemptRow] = []
            for a in attempts:
                item = items.get(a.item_id)
                distractor_topic = None
                chosen = None
                if item and item.kind == "mcq" and a.final_answer:
                    try:
                        chosen = int(a.final_answer.get("index", -1))
                    except (TypeError, ValueError, AttributeError):
                        chosen = None
                    if chosen is not None and chosen >= 0 and item.choices:
                        if not (chosen < len(item.choices) and item.choices[chosen].get("correct")):
                            distractor_topic = (item.tags or [None])[0]
                rows.append(
                    P.AttemptRow(
                        id=a.id,
                        topic_id=a.topic_id,
                        item_id=a.item_id,
                        correct=a.correct,
                        score=a.score,
                        time_to_first_ms=a.time_to_first_ms,
                        time_on_item_ms=a.time_on_item_ms,
                        hints_used=a.hints_used,
                        examples_opened=a.examples_opened,
                        explanation_opened=bool(a.explanation_opened),
                        change_count=a.change_count,
                        away_ms=a.away_ms,
                        experience_mode=a.experience_mode,
                        stem_words=len((item.stem or "").split()) if item else 0,
                        attempt_no=a.attempt_no,
                        teachback_score=a.teachback_score,
                        latency_z=a.latency_z,
                        submitted_at=a.submitted_at,
                        session_id=a.session_id,
                        chosen_distractor=chosen,
                        distractor_topic=distractor_topic,
                    )
                )
            from ..db.models import LessonRun, LessonScene

            lesson_rows_raw = [
                (r, sc.kind, sc.spec_id, int(sc.frame_count or 0))
                for r, sc in s.execute(
                    select(LessonRun, LessonScene)
                    .join(LessonScene, LessonScene.id == LessonRun.scene_id)
                    .where(LessonRun.student_id == student_id)
                    .order_by(LessonRun.last_seen_at.desc())
                    .limit(200)
                ).all()
            ]
            mastery = {t: float(st.p_know) for t, st in states.items()}
            prereqs: dict[str, list[str]] = {}
            for r in prereq_rows:
                prereqs.setdefault(r.topic_id, []).append(r.prerequisite_id)
            ret_rows = [
                P.RetentionRow(
                    topic_id=c.topic_id,
                    lag_hours=float(c.lag_hours),
                    half_life_hours=float(sched.get(c.topic_id, 24.0)),
                    correct=bool(c.correct),
                    proposal_id=c.proposal_id,
                )
                for c in checks
            ]
            lesson_rows = [
                P.LessonRow(
                    run_id=r.id,
                    scene_id=r.scene_id,
                    spec_id=spec_id,
                    kind=kind,
                    topic_id=r.topic_id,
                    frame_count=frames,
                    frames_viewed=int(r.frames_viewed or 0),
                    furthest_frame=int(r.furthest_frame or 0),
                    predictions_total=int(r.predictions_total or 0),
                    predictions_correct=int(r.predictions_correct or 0),
                    param_changes=int(r.param_changes or 0),
                    replays=int(r.replays or 0),
                    pauses=int(r.pauses or 0),
                    seconds_watched=int(r.seconds_watched or 0),
                    finished=r.status == "finished",
                    at=r.last_seen_at,
                )
                for r, kind, spec_id, frames in lesson_rows_raw
            ]
            return P.EvidenceContext(
                student_id=student_id,
                attempts=rows,
                prereqs=prereqs,
                mastery=mastery,
                half_lives=sched,
                retention=ret_rows,
                teachback_later=tb_pairs,
                lessons=lesson_rows,
                min_n=max(3, self.settings.min_evidence_for_proposal // 2),
                now=utcnow(),
            )

    def collect_evidence(self, student_id: str, *, session_id: str | None = None) -> list[dict[str, Any]]:
        ctx = self.build_context(student_id)
        obs = P.run_all(ctx)
        out: list[dict[str, Any]] = []
        with self.sf() as s:
            for o in obs:
                row = EvidenceRecord(
                    student_id=student_id,
                    session_id=session_id,
                    detector=o.detector,
                    topic_ids=o.topic_ids,
                    attempt_ids=o.attempt_ids,
                    metric=o.metric,
                    value=o.value,
                    baseline=o.baseline,
                    n=o.n,
                    facts=o.facts,
                    strength=o.strength,
                    observed_at=utcnow(),
                )
                s.add(row)
                s.flush()
                out.append({"id": row.id, **o.as_dict()})
            s.commit()
        return out

    def update_hypotheses(self, student_id: str, observations: list[dict[str, Any]]) -> list[H.Draft]:
        drafts: list[H.Draft] = []
        with self.sf() as s:
            for obs in observations:
                fake = P.Observation(
                    detector=obs["detector"],
                    metric=obs["metric"],
                    value=obs["value"],
                    baseline=obs["baseline"],
                    n=obs["n"],
                    n_baseline=obs["n_baseline"],
                    facts=obs["facts"],
                    strength=obs["strength"],
                    topic_ids=obs["topic_ids"],
                    attempt_ids=obs["attempt_ids"],
                    candidate_strategy=obs["candidate_strategy"],
                )
                labels = self._topic_labels(s)
                facts = dict(fake.facts or {})
                if facts.get("primary_topic"):
                    facts["primary_topic"] = labels.get(str(facts["primary_topic"]), str(facts["primary_topic"]))
                if facts.get("topic_title") and not facts.get("primary_topic"):
                    facts["primary_topic"] = facts["topic_title"]
                fake.facts = facts
                fake.topic_ids = [labels.get(str(t), str(t)) for t in (fake.topic_ids or [])]
                draft = H.draft(fake, evidence_id=obs.get("id"))
                if draft is None:
                    continue
                draft.statement = self._humanise_topics(s, draft.statement)
                draft.falsification = self._humanise_topics(s, draft.falsification)
                draft.mechanism = self._humanise_topics(s, draft.mechanism)
                drafts.append(draft)
                existing = s.execute(
                    select(Hypothesis).where(Hypothesis.student_id == student_id, Hypothesis.detector == draft.detector)
                ).scalar_one_or_none()
                if existing is None:
                    s.add(
                        Hypothesis(
                            student_id=student_id,
                            detector=draft.detector,
                            statement=draft.statement,
                            mechanism=draft.mechanism,
                            citations=draft.citations,
                            topic_ids=draft.topic_ids,
                            evidence_ids=draft.evidence_ids,
                            status=HypothesisStatus.ACTIVE.value,
                            prior=draft.confidence,
                            posterior=draft.confidence,
                            falsification=draft.falsification,
                            proposed_strategy=draft.strategy,
                        )
                    )
                else:
                    existing.statement = draft.statement
                    existing.mechanism = draft.mechanism
                    existing.posterior = round(H.posterior(existing.supports, existing.refutes), 3)
                    existing.evidence_ids = (existing.evidence_ids or []) + [e for e in draft.evidence_ids if e][:10]
                    existing.last_updated_at = utcnow()
                    if existing.status in (HypothesisStatus.REFUTED.value, HypothesisStatus.RETIRED.value):
                        existing.status = HypothesisStatus.ACTIVE.value  # new evidence reopens it
            s.commit()
        return drafts

    _TOPIC_ID_RE = re.compile(r"\b(?:top|[a-z]{1,4})_[0-9a-f]{12,}\b")

    def _topic_labels(self, s) -> dict[str, str]:
        """id -> title and code -> title, for turning ids into words."""
        out: dict[str, str] = {}
        for tid, code, title in s.execute(select(Topic.id, Topic.code, Topic.title)).all():
            out[str(tid)] = title
            if code:
                out[str(code)] = title
        return out

    def _humanise_topics(self, s, text: str) -> str:
        """Replace any surviving topic id with its title.

        Detectors work on ids (that is what they are given) and the wording templates
        interpolate them, so this is the last gate before anything a learner can read.
        A leftover id in a learner-facing sentence is a bug the UI cannot fix.
        """
        labels = self._topic_labels(s)
        for token in set(self._TOPIC_ID_RE.findall(text or "")) | {t.strip(".:,;") for t in (text or "").split()}:
            title = labels.get(token) or labels.get(token.replace(" ", "_"))
            if title is None and token.startswith(("top_", "top ")):
                probe = token.replace(" ", "_")
                title = s.execute(select(Topic.title).where(Topic.id == probe)).scalar_one_or_none()
                if title is None:
                    continue
                labels[probe] = title
            if title:
                text = text.replace(token, title)
        return text

    def rebuild_traces(self, student_id: str) -> int:
        """Recompute every mastery trace from the attempt history.

        Mastery is a pure function of the observations plus the model, so this is
        always safe to re-run -- and it is what makes a bug fix (or an imported
        history, or a seeded demo) actually show up in the numbers instead of
        living in a stale row. The retention half-life is *not* recomputed here:
        that lives in its own schedule table and is re-fit separately.
        """
        with self.sf() as s:
            rows = s.execute(
                select(
                    Attempt.topic_id,
                    Attempt.correct,
                    Attempt.score,
                    Attempt.submitted_at,
                    Attempt.hints_used,
                    Attempt.examples_opened,
                )
                .where(Attempt.student_id == student_id, Attempt.correct.is_not(None))
                .order_by(Attempt.submitted_at, Attempt.id)
            ).all()
            per_topic: dict[str, list[Any]] = {}
            for row in rows:
                per_topic.setdefault(str(row[0]), []).append(row)
            touched = 0
            for topic_id, items in per_topic.items():
                params = self.params_for(student_id, topic_id, s)
                state = s.get(BktState, (student_id, topic_id))
                if state is None:
                    state = BktState(
                        student_id=student_id,
                        topic_id=topic_id,
                        p_slip=params.p_slip,
                        p_guess=params.p_guess,
                        p_learn=params.p_learn,
                        p_forget=params.p_forget,
                    )
                    s.add(state)
                p = state.p_know if state.n_obs else params.p_init
                half_life = state.half_life_hours
                correct_count = streak = 0
                first_ts: datetime | None = None
                last_ts: datetime | None = None
                for _tid, correct, score, ts, hints, examples in items:
                    if ts is not None:
                        if first_ts is None:
                            first_ts = ts
                        if last_ts is not None and half_life:
                            gap = max(0.0, (ts - last_ts).total_seconds() / 3600.0)
                            p = clamp01(p * decay(p, gap, half_life))
                        last_ts = ts
                    val = 1 if (score if score is not None else (1.0 if correct else 0.0)) >= 0.75 else 0
                    p = B.update(p, bool(val), params, opportunity=not (hints or examples))
                    correct_count += val
                    streak = streak + 1 if val else 0
                state.p_know = clamp01(p)
                state.n_obs = len(items)
                state.correct_count = correct_count
                state.streak = streak
                state.mastery_band = params.band(
                    state.p_know,
                    proficient=self.settings.mastery_proficient,
                    developing=self.settings.mastery_developing,
                    observations=len(items),
                )
                state.first_seen_at = first_ts or state.first_seen_at or utcnow()
                state.last_practice_at = last_ts or state.last_practice_at
                state.updated_at = utcnow()
                touched += 1
            s.commit()
            return touched

    def propose(
        self,
        student_id: str,
        drafts: Sequence[H.Draft],
        *,
        llm_text: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Create pending proposals. Never applies them."""
        if not drafts:
            return []
        with self.sf() as s:
            last = s.execute(
                select(func.max(AdaptationProposal.created_at)).where(AdaptationProposal.student_id == student_id)
            ).scalar()
            cooldown = timedelta(minutes=self.settings.proposal_cooldown_minutes)
            if last and utcnow() - last < cooldown:
                return [{"skipped": "cooldown", "until": (last + cooldown).isoformat()}]
            pending = s.execute(
                select(AdaptationProposal).where(
                    AdaptationProposal.student_id == student_id, AdaptationProposal.status == ProposalStatus.PENDING.value
                )
            ).scalars().all()
            pending_strategies = {p.strategy for p in pending}
            if len(pending) >= 2:
                return [{"skipped": "student already has 2 unanswered proposals"}]
            made: list[dict[str, Any]] = []
            for draft in drafts:
                if draft.strategy in pending_strategies:
                    continue  # same idea already waiting for an answer
                strategy = ST.get(draft.strategy)
                if not strategy.persistent:
                    continue  # in-the-moment help does not need approval, so must not be proposed
                body = self._proposal_text(draft, strategy, llm_text)
                payload = dict(strategy.payload_defaults)
                if "target" in payload:
                    # The number has to come from the evidence, or the "change" the learner is
                    # asked to approve is the number already in use. See `recalibrated_target`.
                    payload["target"] = recalibrated_target(self._evidence_facts(s, draft), current=float(payload["target"]))
                if isinstance(llm_text, dict) and isinstance(llm_text.get("action_payload"), dict):
                    payload.update(
                        {k: v for k, v in llm_text["action_payload"].items() if k in payload}
                    )  # the model may only tune knobs the strategy already exposes
                review_window = {
                    "kind": "next_items",
                    "metric": strategy.watch_metric,
                    "min_items": 6,
                    "max_items": 14,
                }
                row = AdaptationProposal(
                    student_id=student_id,
                    strategy=draft.strategy,
                    facts_text=body["facts_text"],
                    pattern_text=body["pattern_text"],
                    hypothesis_text=body["hypothesis_text"],
                    change_text=body["change_text"],
                    ask_text=body["ask_text"],
                    expected_effect=body["expected_effect"],
                    caveat_text=body["caveat_text"],
                    alternatives=[{"explanation": a} for a in draft.alternatives],
                    action=strategy.action,
                    action_payload=payload,
                    review_metric=strategy.watch_metric,
                    review_window=review_window,
                    status=ProposalStatus.PENDING.value,
                    expires_at=utcnow() + timedelta(days=14),
                )
                hyp = s.execute(
                    select(Hypothesis).where(Hypothesis.student_id == student_id, Hypothesis.detector == draft.detector)
                ).scalar_one_or_none()
                if hyp is not None:
                    row.hypothesis_id = hyp.id
                row.evidence_ids = draft.evidence_ids
                pending_strategies.add(draft.strategy)  # one proposal per idea, per round
                s.add(row)
                s.flush()
                made.append(proposal_dict(row, hypothesis=hyp, strategy=strategy))
            s.commit()
            return made

    def _evidence_facts(self, s, draft: H.Draft) -> dict[str, Any]:
        """The facts the detector recorded for this draft, if any are still on file.

        Read here rather than passed down because the proposal is built from the draft, which
        keeps the sentence and not the numbers; the numbers live on the evidence row.
        """
        ids = list(draft.evidence_ids or [])
        if not ids:
            return {}
        from ..db.models import EvidenceRecord

        for row in s.execute(select(EvidenceRecord).where(EvidenceRecord.id.in_(ids))).scalars().all():
            if isinstance(row.facts, dict) and row.facts:
                return dict(row.facts)
        return {}


    def _proposal_text(self, draft: H.Draft, strategy: ST.Strategy, llm_text: dict[str, Any] | None) -> dict[str, str]:
        """Local rendering by default; the LLM version is used only if it is valid."""
        local = {
            "facts_text": draft.statement,
            "pattern_text": _pattern_sentence(draft, strategy),
            "hypothesis_text": draft.mechanism,
            "caveat_text": "Other explanations we cannot rule out: "
            + "; ".join(draft.alternatives if draft.alternatives else ["sampling noise"]),
            "change_text": f"{strategy.what} {strategy.caveat}".strip(),
            "ask_text": "Want me to try that? You can say no, or change it -- I will not change how you learn without asking.",
            "expected_effect": (
                f"We will watch {strategy.watch_metric.replace('_', ' ')} over the next several items and "
                "compare it with how you have been doing. " + draft.falsification
            ),
        }
        if not llm_text:
            return local
        merged = dict(local)
        for key in ("facts_text", "pattern_text", "hypothesis_text", "change_text", "ask_text", "caveat_text", "expected_effect"):
            val = llm_text.get(key)
            if isinstance(val, str) and 12 <= len(val.strip()) <= 1200:
                merged[key] = " ".join(val.split())
        if not H.check_language(" ".join(merged.values())) and merged["ask_text"].endswith("?"):
            return merged
        return local

    # ------------------------------------------------------------------ #
    # outcome tracking: did the approved change help?
    # ------------------------------------------------------------------ #

    def evaluate_pending_adaptations(self, student_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with self.sf() as s:
            rows = s.execute(
                select(AdaptationProposal).where(
                    AdaptationProposal.student_id == student_id,
                    AdaptationProposal.status.in_([ProposalStatus.APPROVED.value, ProposalStatus.MODIFIED.value]),
                    AdaptationProposal.applied_at.is_not(None),
                )
            ).scalars().all()
            for prop in rows:
                metric = prop.review_metric or "accuracy"
                window = prop.review_window or {"min_items": 6, "max_items": 14}
                base = self._window_stats(s, student_id, metric, before=prop.applied_at)
                after = self._window_stats(s, student_id, metric, after=prop.applied_at)
                effect = evaluate_change(base["values"], after["values"], metric=metric)
                verdict = effect.verdict
                payload: dict[str, Any] = {
                    "effect": effect.as_dict(),
                    "n_after": after["n"],
                    "n_before": base["n"],
                    # the learner-facing sentence, stored with the measurement rather than
                    # composed by whichever screen happens to show it first
                    "verdict_words": describe_effect(
                        effect.as_dict(),
                        metric=metric,
                        n_after=int(after["n"]),
                        min_items=int(window.get("min_items", 6)),
                    ),
                }
                retention = self._retention_delta(s, student_id, prop.id, prop.applied_at)
                if retention:
                    payload["retention"] = retention
                if after["n"] < int(window.get("min_items", 6)):
                    payload["status"] = "gathering_evidence"
                else:
                    prop.evaluation_status = verdict
                    prop.evaluated_at = utcnow()
                    prop.outcome = payload
                    prop.status = ProposalStatus.EVALUATED.value
                    active = s.execute(
                        select(ActiveAdaptation).where(
                            ActiveAdaptation.proposal_id == prop.id, ActiveAdaptation.active.is_(True)
                        )
                    ).scalars().all()
                    for a in active:
                        a.effect_summary = payload
                        if verdict == "refuted":
                            a.active = False
                            a.ended_at = utcnow()
                            a.end_reason = "measured_worse"
                    hyp = s.get(Hypothesis, prop.hypothesis_id) if prop.hypothesis_id else None
                    if hyp is not None:
                        if verdict == "supported":
                            hyp.supports += 1
                            hyp.status = HypothesisStatus.SUPPORTED.value
                        elif verdict == "refuted":
                            hyp.refutes += 1
                            hyp.status = HypothesisStatus.REFUTED.value
                        elif verdict == "inconclusive":
                            hyp.status = HypothesisStatus.INCONCLUSIVE.value
                        hyp.posterior = round(H.posterior(hyp.supports, hyp.refutes), 3)
                    # Events are append-only and belong to a session; the effect of
                    # an adaptation is recorded against the session it is being
                    # measured in, so the journey view can show it in order.
                    anchor = s.execute(
                        select(LearningSession.id)
                        .where(LearningSession.student_id == student_id)
                        .order_by(LearningSession.started_at.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                    if anchor is not None:
                        next_seq = (
                            s.execute(
                                select(func.max(LearnerEvent.seq)).where(LearnerEvent.session_id == anchor)
                            ).scalar_one_or_none()
                            or 0
                        ) + 1
                        s.add(
                            LearnerEvent(
                                session_id=anchor,
                                student_id=student_id,
                                seq=next_seq,
                                type="adaptation_effect",
                                payload={
                                    "strategy": prop.strategy,
                                    "metric": metric,
                                    "verdict": verdict,
                                    "delta": payload["effect"].get("mean_delta"),
                                    "proposal_id": prop.id,
                                },
                            )
                        )
                    payload["status"] = "evaluated"
                out.append({"proposal_id": prop.id, "strategy": prop.strategy, **payload})
            if out:
                s.commit()
        return out

    def _window_stats(self, s, student_id: str, metric: str, *, before: datetime | None = None, after: datetime | None = None) -> dict[str, Any]:
        stmt = select(Attempt).where(Attempt.student_id == student_id, Attempt.correct.is_not(None))
        if before:
            stmt = stmt.where(Attempt.submitted_at < before).order_by(Attempt.submitted_at.desc()).limit(14)
        elif after:
            stmt = stmt.where(Attempt.submitted_at >= after).order_by(Attempt.submitted_at).limit(14)
        rows = list(s.execute(stmt).scalars().all())
        if before:
            rows = rows[::-1]
        values: list[float] = []
        if metric in ("accuracy", "unaided_accuracy", "accuracy_after_read", "accuracy_visual_vs_read", "discrimination_accuracy", "dependent_topic_accuracy"):
            for a in rows:
                if metric == "unaided_accuracy" and (a.hints_used or a.examples_opened or a.explanation_opened):
                    continue
                values.append(float(a.score if a.score is not None else (1.0 if a.correct else 0.0)))
        elif metric == "retention_7d":
            checks = s.execute(
                select(RetentionCheck).where(RetentionCheck.student_id == student_id).order_by(RetentionCheck.created_at)
            ).scalars().all()
            for c in checks:
                if before and c.created_at >= before:
                    continue
                if after and c.created_at < after:
                    continue
                values.append(1.0 if c.correct else 0.0)
        elif metric == "calibration_gap":
            for a in rows:
                values.append(abs(float(a.latency_z or 0.0)))
        else:
            for a in rows:
                values.append(float(a.score if a.score is not None else (1.0 if a.correct else 0.0)))
        return {"values": values, "n": len(values)}

    def _retention_delta(self, s, student_id: str, proposal_id: str, applied_at: datetime | None) -> dict[str, Any] | None:
        checks = s.execute(
            select(RetentionCheck).where(RetentionCheck.student_id == student_id).order_by(RetentionCheck.created_at)
        ).scalars().all()
        before = [(float(c.expected_recall or 0.8), bool(c.correct)) for c in checks if applied_at and c.created_at < applied_at]
        after = [(float(c.expected_recall or 0.8), bool(c.correct)) for c in checks if applied_at and c.created_at >= applied_at]
        if not before or not after:
            return None
        return {
            "before": retention_calibration(before),
            "after": retention_calibration(after),
            "note": "calibration of predicted vs actual recall, before and after the change",
        }

    def record_retention_check(
        self,
        *,
        student_id: str,
        topic_id: str,
        item_id: str | None,
        correct: bool,
        score: float | None,
        latency_ms: int | None,
        kind: str = "scheduled",
        proposal_id: str | None = None,
        reschedule: bool = False,
    ) -> dict[str, Any]:
        with self.sf() as s:
            sched = s.get(RetentionSchedule, (student_id, topic_id))
            was_due_at = sched.due_at.isoformat() if sched is not None and sched.due_at else None
            lag = 0.0
            expected = None
            if sched and sched.last_recall_at:
                lag = max(0.0, (utcnow() - sched.last_recall_at).total_seconds() / 3600.0)
                expected = SP.expected_recall(sched.half_life_hours, lag)
            s.add(
                RetentionCheck(
                    student_id=student_id,
                    topic_id=topic_id,
                    item_id=item_id,
                    lag_hours=round(lag, 3),
                    expected_recall=round(expected, 4) if expected is not None else None,
                    correct=correct,
                    score=score,
                    latency_ms=latency_ms,
                    kind=kind,
                    proposal_id=proposal_id,
                )
            )
            scheduled: dict[str, Any] | None = None
            if reschedule:
                # A check that is not folded back into the schedule is a diary entry: the
                # queue would keep offering the same topic, reps/lapses would never move,
                # and the calibration numbers would have nothing to calibrate against.
                # Scored attempts reschedule through ``record_attempt``, hence the flag.
                state = s.get(BktState, (student_id, topic_id))
                topic = s.get(Topic, topic_id)
                updated = self._update_schedule(
                    s,
                    student_id=student_id,
                    topic_id=topic_id,
                    correct=correct,
                    difficulty=float(getattr(topic, "difficulty", 0.5) or 0.5),
                    self_explain=0.0,
                    lag_hours=lag,
                    prior_correct_count=int(state.correct_count) if state else 0,
                    n_obs=int(state.n_obs) if state else 0,
                )
                if updated is not None:
                    scheduled = {
                        "due_at": updated.due_at.isoformat() if updated.due_at else None,
                        "half_life_hours": round(float(updated.half_life_hours or 0.0), 3),
                        "reps": updated.reps,
                        "lapses": updated.lapses,
                        "expected_recall_at_due": updated.expected_recall_at_due,
                    }
            s.commit()
            out: dict[str, Any] = {
                "lag_hours": round(lag, 2),
                "expected_recall": round(expected, 3) if expected is not None else None,
                "correct": correct,
            }
            if reschedule:
                out["scheduled"] = scheduled
                out["was_due_at"] = was_due_at
            return out

    # ------------------------------------------------------------------ #
    # the full cycle
    # ------------------------------------------------------------------ #

    async def analyze(self, student_id: str, *, session_id: str | None = None, use_llm: bool = True) -> Analysis:
        """Test -> observe -> understand -> personalize, one cycle of the loop."""
        t0 = time.monotonic()
        # Replay the trace first: the analysis must describe the learner's whole
        # history, not only the attempts that happened to arrive while the app was
        # open. Importing a history, or fixing a bug in the update rule, both have
        # to be visible here.
        self.rebuild_traces(student_id)
        evidence = self.collect_evidence(student_id, session_id=session_id)
        drafts = self.update_hypotheses(student_id, evidence)
        graph, mastery, plan = self.plan(student_id)
        llm_meta: dict[str, Any] = {}
        llm_text: dict[str, Any] | None = None
        if use_llm and drafts and self.llm is not None and getattr(self.llm, "configured", False):
            llm_text, llm_meta = await self._phrase_with_llm(student_id, drafts, evidence)
        proposals = self.propose(student_id, drafts, llm_text=llm_text)
        evaluated = self.evaluate_pending_adaptations(student_id)
        with self.sf() as s:
            snapshot = self.snapshot(s, student_id)
            s.add(
                ProfileSnapshot(
                    student_id=student_id,
                    reason="ai1_analyze",
                    profile=snapshot,
                    topic_count=len(snapshot.get("mastery", []) or []),
                    hypothesis_count=len(snapshot.get("hypotheses", []) or []),
                    adaptation_count=len(snapshot.get("active_adaptations", []) or []),
                )
            )
            if session_id:
                sess = s.get(LearningSession, session_id)
                if sess is not None:
                    sess.analyzed_at = utcnow()
            s.commit()
        result = Analysis(
            student_id=student_id,
            session_id=session_id,
            traces=list(snapshot.get("mastery", []) or []),
            evidence=evidence,
            hypotheses=[d.as_dict() for d in drafts],
            proposals=[p for p in proposals if isinstance(p, dict)],
            evaluated=evaluated,
            gaps=[g for g in (plan or {}).get("gaps", [])],
            plan=[s for s in (plan or {}).get("path", {}).get("steps", [])],
            bkt=(plan or {}).get("bkt", {}),
            retention=(plan or {}).get("retention", {}),
            profile=snapshot,
            notes=(plan or {}).get("notes", []),
            llm=llm_meta,
            provider="gemini" if llm_text else "local",
            model_ms=int((time.monotonic() - t0) * 1000),
        )
        return result

    def _llm_prompt(self, drafts: Sequence[H.Draft], evidence: list[dict[str, Any]], profile: dict[str, Any]) -> tuple[str, str]:
        from .prompts import AI1_SYSTEM

        top = drafts[0]
        strategy = ST.get(top.strategy)
        system = AI1_SYSTEM
        user = json.dumps(
            {
                "observed_evidence": evidence[:4],
                "leading_hypothesis": {
                    "statement": top.statement,
                    "mechanism": top.mechanism,
                    "citations": top.citations,
                    "alternative_explanations": top.alternatives,
                    "what_would_disprove_it": top.falsification,
                },
                "proposed_change": {"strategy": strategy.id, "label": strategy.label, "what": strategy.what, "watch_metric": strategy.watch_metric, "tunable": strategy.payload_defaults},
                "learner_profile_summary": to_llm_payload(
                    LearnerProfile(
                        student_id=profile.get("student_id", ""),
                        taken_at=utcnow(),
                        traces=[
                            TopicTrace(
                                topic_id=m.get("topic_id", ""),
                                title=str(m.get("title", "")),
                                p_know=float(m.get("p_know", 0.0)),
                                band=str(m.get("band", "new")),
                                n_obs=int(m.get("observations", 0)),
                                half_life_hours=m.get("half_life_hours"),
                            )
                            for m in (profile.get("mastery") or [])[:12]
                        ],
                    )
                ),
                "task": (
                    "Write the five-part proposal (facts_text, pattern_text, hypothesis_text, change_text, "
                    "ask_text) plus caveat_text and expected_effect, and pick at most 2 tunable knobs in "
                    "action_payload. Reply with JSON only."
                ),
            },
            separators=(",", ":"),
        )[:7000]
        return system, user

    async def _phrase_with_llm(self, student_id: str, drafts: Sequence[H.Draft], evidence: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        profile = self.profile_dict(student_id)
        system, user = self._llm_prompt(drafts, evidence, profile)
        safe_user, dropped, redacted_kinds = redact_payload({"q": user}, max_chars=7000)
        res = await self.llm.complete(
            system=system,
            user=safe_user["q"],
            purpose="ai1_propose",
            role="ai1",
            student_id=student_id,
            temperature=0.25,
            max_output_tokens=700,
            response_spec=PROPOSAL_SPEC,
        )
        if redacted_kinds:
            log.debug("ai1_propose: redacted %s", ", ".join(sorted(set(redacted_kinds))[:8]))
        meta = {**res.as_dict(), "redacted_fields": (res.redacted_fields or 0) + dropped}
        with self.sf() as s:
            s.add(
                LlmCall(
                    student_id=student_id,
                    purpose="ai1_propose",
                    role="ai1",
                    model=res.model or "?",
                    status=res.status,
                    tokens_in=res.tokens_in,
                    tokens_out=res.tokens_out,
                    latency_ms=res.latency_ms,
                    redacted_fields=meta["redacted_fields"],
                    prompt_chars=len(user),
                    day=utcnow().date().isoformat(),
                )
            )
            s.commit()
        if not res.ok or not res.data:
            meta["fell_back_to"] = "local template rendering"
            return None, meta
        data = dict(res.data)
        data["strategy"] = drafts[0].strategy  # AI 1 chooses; the model only phrases
        return data, meta

    # ------------------------------------------------------------------ #
    # profile / plan assembly
    # ------------------------------------------------------------------ #

    def profile_dict(self, student_id: str) -> dict[str, Any]:
        with self.sf() as s:
            return self.snapshot(s, student_id)

    def snapshot(self, s, student_id: str) -> dict[str, Any]:
        profile = self.profile(s, student_id)
        return profile.as_dict()

    def profile(self, s, student_id: str) -> LearnerProfile:
        states = {
            st.topic_id: st
            for st in s.execute(select(BktState).where(BktState.student_id == student_id)).scalars().all()
        }
        titles = {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}
        schedules = {
            r.topic_id: r
            for r in s.execute(select(RetentionSchedule).where(RetentionSchedule.student_id == student_id)).scalars().all()
        }
        traces: list[TopicTrace] = []
        for tid, st in states.items():
            sc = schedules.get(tid)
            hours = (utcnow() - st.last_practice_at).total_seconds() / 3600.0 if st.last_practice_at else None
            traces.append(
                TopicTrace(
                    topic_id=tid,
                    title=titles.get(tid, tid),
                    p_know=float(st.p_know),
                    band=st.mastery_band,
                    n_obs=int(st.n_obs),
                    half_life_hours=float(sc.half_life_hours) if sc else None,
                    due_at=sc.due_at if sc else None,
                    expected_recall=SP.expected_recall(sc.half_life_hours, hours or 0.0) if sc else None,
                    trend=None,
                )
            )
        directives: list[Directive] = []
        for a in s.execute(
            select(ActiveAdaptation).where(ActiveAdaptation.student_id == student_id, ActiveAdaptation.active.is_(True))
        ).scalars().all():
            prop = s.get(AdaptationProposal, a.proposal_id)
            directives.append(
                Directive(
                    strategy=a.strategy,
                    action=a.action,
                    payload=a.payload or {},
                    why=(prop.change_text if prop else "approved adaptation"),
                    source="student_approved" if a.source == "approved" else "approved",
                    proposal_id=a.proposal_id,
                    student_note=prop.student_note if prop else None,
                )
            )
        for note in s.execute(
            select(AdaptationProposal).where(
                AdaptationProposal.student_id == student_id,
                AdaptationProposal.status == ProposalStatus.MODIFIED.value,
            )
        ).scalars().all():
            if note.student_note and not any(d.student_note == note.student_note for d in directives):
                directives.append(
                    Directive(
                        strategy=note.strategy,
                        action=note.action,
                        payload=note.action_payload or {},
                        why="student stated preference",
                        source="student_stated",
                        student_note=note.student_note,
                    )
                )
        # Stated preferences outrank measured guesses, and unlike a measured
        # pattern they need no approval: the learner is the one asking. Keys that
        # name a strategy in the catalog become real directives; the rest ride
        # along as plain preferences so AI 2 can honour them and the UI can show
        # them, without us inventing a fake "learning style" out of them.
        prefs: dict[str, Any] = {}
        for row in s.execute(select(StudentPreference).where(StudentPreference.student_id == student_id)).scalars().all():
            prefs[row.key] = row.value
            mapped = ST.directive_for_preference(row.key, row.value)
            if mapped is not None:
                strat, payload = mapped
                directives.append(
                    Directive(
                        strategy=strat.id,
                        action=strat.action,
                        payload=payload,
                        why=row.note or "the learner asked for this",
                        source="student_stated",
                        student_note=row.note,
                    )
                )
            elif row.key not in ST.PREFERENCE_STRATEGIES:
                directives.append(
                    Directive(
                        strategy="stated_preference",
                        action="respect_preference",
                        payload={"key": row.key, "value": row.value, "source": row.source},
                        why=row.note or "the learner told us this",
                        source="student_stated",
                        student_note=f"{row.key}: {_preference_phrase(row.key, row.value)}",
                    )
                )
        hyps = s.execute(select(Hypothesis).where(Hypothesis.student_id == student_id)).scalars().all()
        pending = s.execute(
            select(AdaptationProposal).where(
                AdaptationProposal.student_id == student_id, AdaptationProposal.status == ProposalStatus.PENDING.value
            )
        ).scalars().all()
        strat_rows = s.execute(select(StrategyStat).where(StrategyStat.student_id == student_id)).scalars().all()
        checks = s.execute(select(RetentionCheck).where(RetentionCheck.student_id == student_id)).scalars().all()
        sess = s.execute(
            select(LearningSession)
            .where(LearningSession.student_id == student_id)
            .order_by(LearningSession.started_at.desc())
            .limit(5)
        ).scalars().all()
        behaviour = {
            "sessions_recent": len(sess),
            "pause_count": sum(x.pause_count for x in sess),
            "focus_change_count": sum(x.focus_change_count for x in sess),
            "away_seconds": sum(x.away_seconds for x in sess),
            "interruptions": sum(x.interruptions for x in sess),
            "voice_turns": sum(x.voice_turns for x in sess),
        }
        counts = {
            "attempts": int(s.execute(select(func.count()).select_from(Attempt).where(Attempt.student_id == student_id)).scalar() or 0),
            "events": int(s.execute(select(func.count()).select_from(LearnerEvent).where(LearnerEvent.student_id == student_id)).scalar() or 0),
            "evidence": int(s.execute(select(func.count()).select_from(EvidenceRecord).where(EvidenceRecord.student_id == student_id)).scalar() or 0),
            "retention_checks": len(checks),
        }
        graph = self.graph(s)
        gaps = G.detect_gaps(
            graph,
            {t: float(st.p_know) for t, st in states.items()},
            {t: int(st.n_obs) for t, st in states.items()},
            threshold=self.settings.gap_threshold,
        )
        calibration = retention_calibration(
            [(float(c.expected_recall or 0.8), bool(c.correct)) for c in checks] if checks else []
        )
        return LearnerProfile(
            student_id=student_id,
            taken_at=utcnow(),
            traces=traces,
            directives=directives,
            preferences=prefs,
            gaps=[g.as_dict() for g in gaps],
            hypotheses=[
                {
                    "detector": h.detector,
                    "statement": h.statement,
                    "status": h.status,
                    "posterior": h.posterior,
                    "supports": h.supports,
                    "refutes": h.refutes,
                    "citations": h.citations,
                    "falsification": h.falsification,
                    "strategy": h.proposed_strategy,
                }
                for h in hyps
            ],
            pending_proposals=[proposal_dict(p, strategy=ST.CATALOG.get(p.strategy)) for p in pending],
            strategy_stats=[
                ST.StrategyStat(strategy=r.strategy, alpha=r.alpha, beta=r.beta, mean_lift=r.mean_lift, n=r.n).as_dict()
                for r in strat_rows
            ],
            session_behaviour=behaviour,
            retention={
                "checks": len(checks),
                "calibration": calibration,
                "due_now": [
                    {"topic_id": tid, "title": titles.get(tid, tid), "due_at": sc.due_at.isoformat()}
                    for tid, sc in schedules.items()
                    if sc.due_at and sc.due_at <= utcnow()
                ],
            },
            outcomes={
                "evaluated": int(s.execute(
                    select(func.count())
                    .select_from(AdaptationProposal)
                    .where(AdaptationProposal.student_id == student_id, AdaptationProposal.status == ProposalStatus.EVALUATED.value)
                ).scalar() or 0),
                "applied": int(s.execute(
                    select(func.count()).select_from(ActiveAdaptation).where(ActiveAdaptation.student_id == student_id)
                ).scalar() or 0),
            },
            data_volume=counts,
        )

    def graph(self, s) -> G.Graph:
        topics = [(t.id, t.title) for t in s.execute(select(Topic)).scalars().all()]
        edges = [(e.topic_id, e.prerequisite_id, float(e.weight)) for e in s.execute(select(TopicPrerequisite)).scalars().all()]
        return G.build_graph(topics, edges)

    def plan(self, student_id: str) -> tuple[G.Graph, dict[str, float], dict[str, Any]]:
        with self.sf() as s:
            graph = self.graph(s)
            prof = self.profile(s, student_id)
            mastery = {t.topic_id: t.p_know for t in prof.traces}
            obs = {t.topic_id: t.n_obs for t in prof.traces}
            gaps = G.detect_gaps(graph, mastery, obs, threshold=self.settings.gap_threshold)
            remediation = G.remediation_plan(graph, mastery, gaps)
            path = G.learning_path(graph, mastery)
            return graph, mastery, {
                "gaps": [g.as_dict() for g in gaps],
                "remediation": remediation,
                "path": path,
                "bkt": {"topics_traced": len(mastery), "params": B.BKTParams().as_dict()},
                "retention": prof.retention,
                "notes": prof.open_questions,
            }


_PREF_PHRASES = {
    "goal": "is studying for",
    "weekly_minutes": "wants to spend about",
    "session_minutes": "prefers sessions of about",
    "quiet_hours": "wants practice prompts only around",
    "max_explanation_words": "wants explanations of at most",
    "avoid": "does not want",
    "encourage": "wants more of",
    "language": "would rather work in",
}


def _preference_phrase(key: str, value: Any) -> str:
    """A short, honest rendering of one stored preference for the prompt/UI."""
    if isinstance(value, (int, float)) and key.endswith("minutes"):
        return f"{int(value)} minutes"
    if isinstance(value, int) and "words" in key:
        return f"{value} words"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value[:6])
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)[:120]


def proposal_dict(p: AdaptationProposal, *, hypothesis: Hypothesis | None = None, strategy: ST.Strategy | None = None) -> dict[str, Any]:
    return {
        "id": p.id,
        "student_id": p.student_id,
        "strategy": p.strategy,
        "strategy_label": strategy.label if strategy else p.strategy,
        "what_it_does": strategy.what if strategy else None,
        "citations": [c.as_dict() for c in (strategy.citations if strategy else ())],
        "action": p.action,
        "action_payload": p.action_payload,
        "facts": p.facts_text,
        "pattern": p.pattern_text,
        "hypothesis": p.hypothesis_text,
        "caveat": p.caveat_text,
        "alternatives": p.alternatives or [],
        "change": p.change_text,
        "ask": p.ask_text,
        "expected_effect": p.expected_effect,
        "review_metric": p.review_metric,
        "review_window": p.review_window,
        "review_window_text": E.describe_window(p.review_window),
        "status": p.status,
        "student_note": p.student_note,
        "modifications": p.modifications,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "baseline": p.baseline,
        "outcome": p.outcome,
        "evaluation_status": p.evaluation_status,
        "hypothesis_ref": {
            "id": hypothesis.id,
            "status": hypothesis.status,
            "posterior": hypothesis.posterior,
        }
        if hypothesis
        else None,
        "evidence_ids": p.evidence_ids or [],
    }


def clamp01(x: float) -> float:
    return max(0.001, min(0.999, float(x)))


def decay(p: float, hours: float | None, half_life: float | None) -> float:
    """Forgetting between practice opportunities (exponential in hours/half-life)."""
    if not hours or not half_life or half_life <= 0:
        return 1.0
    return float(0.5 ** (max(0.0, hours) / float(half_life)) + (1 - 0.5 ** (max(0.0, hours) / float(half_life))) * 0.02)


def _pattern_sentence(draft: H.Draft, strategy: ST.Strategy) -> str:
    """How much a pattern is worth, in words that match the count.

    One measurement is not a pattern, and saying "this has shown up across 1
    measurement" would imply exactly the confidence we do not have yet.
    """
    metric = strategy.watch_metric.replace("_", " ")
    n = len(draft.evidence_ids or [])
    if n <= 1:
        return (
            f"This is the first time {metric} has pointed this way, so treat it as provisional - "
            "a single run of answers can be a bad hour."
        )
    return f"This has shown up across {n} measurements of {metric}."
