"""Answer submission: grade, record, trace, and feed back -- in that order.

Ordering matters. The Attempt row is written *before* AI 1 is asked to update the
knowledge trace, and the raw event log is written alongside both, so a crash in the
learning step cannot leave a graded answer invisible to the next analysis (and
cannot silently invent one).

The feedback returned to the student is the rule-based grade comment plus AI 2's
optional enrichment; correctness itself is always computed by
:func:`adapt.assessment.grading.grade`, never by a model, because a model that
mis-grades a correct answer teaches a misconception with confidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..assessment import generated as GEN
from ..assessment.grading import grade as grade_answer
from ..assessment.selector import expected_success_rate
from ..db.models import (
    ActiveAdaptation,
    Attempt,
    BktState,
    Item,
    LearningMaterial,
    LearningSession,
    RetentionSchedule,
    Topic,
    TopicPrerequisite,
    utcnow,
)  # noqa: E501
from ..learning import content as CONTENT
from ..learning import methods as M
from ..learning import reteach as RE
from ..learning import telemetry as T
from . import telemetry

log = logging.getLogger("adapt.attempts")

ITEM_FIELDS = (
    "id",
    "topic_id",
    "kind",
    "stem",
    "stem_template",
    "experience_mode",
    "prompt_context",
    "visual_spec",
    "choices",
    "answer_spec",
    "solution_steps",
    "hints",
    "rubric",
    "difficulty",
    "est_seconds",
    "numbas_ref",
)


def item_payload(s: Session, item_id: str) -> dict[str, Any] | None:
    """The *private* item dict (answers included). Server-side only, always."""
    it = s.get(Item, item_id)
    if it is None or not it.active:
        return None
    out = {f: getattr(it, f) for f in ITEM_FIELDS}
    out["tags"] = it.tags
    out["source"] = it.source
    out["active"] = bool(it.active)
    if not out.get("stem_template"):
        out.pop("stem_template", None)
    return out


def item_dict(s: Session, item_id: str) -> dict[str, Any] | None:
    """Alias kept for readability at call sites that only read, never grade."""
    return item_payload(s, item_id)


def active_strategies(s: Session, student_id: str) -> list[dict[str, Any]]:
    rows = s.execute(
        select(ActiveAdaptation).where(ActiveAdaptation.student_id == student_id, ActiveAdaptation.active.is_(True))
    ).scalars().all()
    return [{"strategy": r.strategy, "action": r.action, "payload": r.payload or {}, "id": r.id} for r in rows]


def history_ms(s: Session, student_id: str, *, limit: int = 60) -> list[int]:
    vals = s.execute(
        select(Attempt.time_on_item_ms)
        .where(Attempt.student_id == student_id, Attempt.time_on_item_ms.is_not(None))
        .order_by(Attempt.submitted_at.desc())
        .limit(limit)
    ).scalars().all()
    return [int(v) for v in reversed(vals) if v]


@dataclass
class SubmitResult:
    attempt_id: str
    item_id: str
    topic_id: str
    correct: bool | None
    score: float | None
    method: str
    feedback: str
    trace: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    teachback: dict[str, Any] | None = None
    retention: dict[str, Any] | None = None
    flags: list[str] = field(default_factory=list)
    proposals_expected: bool = False
    #: The teaching method this attempt was measured under, and the design that chose
    #: it (``randomised`` when the serving path assigned it, ``observational`` when the
    #: learner chose it). Both are recorded on the row; the response carries them so
    #: the screen can say what it did and how much it is entitled to conclude.
    teaching_method: str | None = None
    teaching_design: str | None = None
    #: The live fail → diagnose → reteach step, when one applies (``learning/reteach``).
    #: ``blame`` is the important field: on a prerequisite gap or a fatigue signal the
    #: loop refuses to attribute the miss to the method.
    next_step: dict[str, Any] | None = None
    #: Actual-versus-expected per teaching method, accumulated across sessions.
    method_compare: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "item_id": self.item_id,
            "topic_id": self.topic_id,
            "correct": self.correct,
            "score": self.score,
            "grading_method": self.method,
            "feedback": self.feedback,
            "mastery": self.trace,
            "derived_metrics": self.metrics,
            "teachback": self.teachback,
            "retention": self.retention,
            "flags": self.flags,
            "proposals_expected": self.proposals_expected,
            "teaching": {
                "method": self.teaching_method,
                "design": self.teaching_design,
                "label": M.METHODS[self.teaching_method].label if self.teaching_method in M.METHODS else None,
            },
            "next_step": self.next_step,
            "method_compare": self.method_compare,
        }


async def submit(
    services: Any,
    *,
    student_id: str,
    session_id: str,
    item_id: str,
    answer: Any,
    attempt_no: int | None = None,
    teachback_text: str | None = None,
    client_ts: str | None = None,
    method: str | None = None,
    method_design: str | None = None,
    method_arm: str | None = None,
    expected_success_client: float | None = None,
    reteach_of: str | None = None,
) -> SubmitResult:
    """Record one answer and let both intelligences react to it.

    The work happens in short transactions, in this order: write the attempt, ask
    AI 2 for feedback, then let AI 1 update the trace. Each step commits before the
    next one opens its own session. That is not tidiness -- SQLite (even in WAL mode)
    has one writer at a time, so a request that held an open write transaction while
    calling into another component would deadlock against itself and surface as
    "database is locked" exactly when a learner is mid-answer.
    """
    # ---- phase 1: read context, grade, persist the attempt ---------------- #
    with services.scope() as s:
        sess = s.get(LearningSession, session_id)
        if sess is None or sess.student_id != student_id:
            raise ValueError("unknown session for this learner")
        raw_item = item_payload(s, item_id)
        if raw_item is None:
            raise ValueError("unknown or retired item")
        # Grading and feedback read the *rendered* variant, using the same per-(learner, item)
        # seed the plan used to build the question on screen. A parametric item stored as
        # template + ranges has no single correct answer: "6x + 8 = 33" and "3x + 11 = 49"
        # are different questions with different answers, and grading the stored default
        # against an answer to the sampled one marks a correct learner wrong.
        from ..assessment.bank import render_item_for, variant_seed

        item = render_item_for(raw_item, seed=variant_seed(student_id, item_id))
        metrics = telemetry.metrics_for_attempt(s, session_id=session_id, item_id=item_id)
        m: dict[str, Any] = metrics.as_dict() if metrics else {}
        # Which method taught this question. The plan assigned it and put it in the
        # response; the client echoes it back. When it is absent (an older client, a
        # direct API call, a replay), the server *derives* the same decision from
        # (learner, item, exposure) rather than recording nothing -- a null method
        # would silently exclude the attempt from every comparison.
        prior_on_topic = int(
            s.execute(
                select(func.count())
                .select_from(Attempt)
                .where(Attempt.student_id == student_id, Attempt.topic_id == item["topic_id"])
            ).scalar_one()
        )
        served_method = str(method or "").strip().lower() or None
        if served_method not in M.METHODS:
            served_method = None
        assignment = M.assign_method(
            learner_id=student_id,
            item_id=item_id,
            topic_id=item["topic_id"],
            exposure=prior_on_topic,
            directives=[],
        )
        if served_method is None or (
            served_method != assignment.method and str(method_design or "") != "assigned_by_adaptation"
        ):
            # No method was echoed, or an unknown one: use the derived assignment and
            # record that it was derived (which is honest, and keeps the row usable).
            effective = assignment
        else:
            effective = M.MethodAssignment(
                method=served_method,
                arm=str(method_arm or assignment.arm),
                design=str(method_design or assignment.design),
                assigned_by="served" if method_design else assignment.assigned_by,
                seed=assignment.seed,
                exposure=prior_on_topic,
                why=assignment.why,
            )
        graded = grade_answer(item, answer, student_text=teachback_text)
        correct = graded.correct if graded.correct is not None else None
        score = graded.score
        if attempt_no is None:
            attempt_no = int(
                s.execute(
                    select(func.count())
                    .select_from(Attempt)
                    .where(Attempt.student_id == student_id, Attempt.item_id == item_id)
                ).scalar_one()
            ) + 1
        lat_hist = history_ms(s, student_id)
        time_on = m.get("time_on_item_ms")
        z = T.latency_z(int(time_on), lat_hist) if time_on else None
        state = s.get(BktState, (student_id, item["topic_id"]))
        p_know = float(state.p_know) if state is not None else 0.2
        params = services.ai1.params_for(student_id, item["topic_id"], s, kind=item.get("kind"), choices=len(item.get("choices") or []) or None)
        # The model's own prediction for this item, before the answer arrives. This is
        # the number "actual vs expected" compares against, so it is computed the same
        # way the selector computes it and stored with the attempt.
        model_expected = expected_success_rate(p_know, float(item.get("difficulty") or 0.5), params)
        expected = (
            float(expected_success_client)
            if expected_success_client is not None and 0.0 < float(expected_success_client) < 1.0
            else model_expected
        )
        strategies = active_strategies(s, student_id)
        att = Attempt(
            session_id=session_id,
            student_id=student_id,
            item_id=item_id,
            topic_id=item["topic_id"],
            experience_mode=item.get("experience_mode") or "read",
            attempt_no=attempt_no,
            first_answer={"value": m.get("first_answer")} if m.get("first_answer") is not None else None,
            final_answer={"value": answer} if not isinstance(answer, dict) else answer,
            correct=correct,
            score=score,
            graded_by="rules",
            time_to_first_ms=m.get("first_interaction_ms"),
            time_on_item_ms=time_on,
            change_count=int(m.get("change_count") or 0),
            changed_from_correct=bool((m.get("answer_changes") or []) and m.get("first_answer") != m.get("final_answer")),
            hints_used=int(m.get("hints_used") or 0),
            examples_opened=int(m.get("examples_opened") or 0),
            explanation_opened=bool(m.get("explanation_opened")),
            away_ms=int(m.get("away_ms") or 0),
            latency_z=None if z is None else float(z),
            intervention_ids=[x["id"] for x in strategies] or None,
            method=effective.method,
            question_kind=item.get("kind"),
            expected_success=round(float(expected), 6),
            mapping_design=effective.design,
            method_arm=effective.arm,
            reteach_of=reteach_of,
        )
        s.add(att)
        s.flush()
        attempt_id = att.id
        flags: list[str] = []
        if time_on and item.get("est_seconds") and time_on < 1000 * max(3.0, float(item["est_seconds"]) * 0.2):
            flags.append("answered unusually fast -- treat this score as weak evidence")
        if (m.get("hints_used") or 0) >= 2 and correct:
            flags.append("correct after 2+ hints: scaffolding may be doing the work")
        if att.changed_from_correct:
            flags.append("changed from a first answer that was right")
        # ---- generated items: calibrate from the answer we just recorded ------- #
        # A model-written question is measured, not believed. The counters below turn its
        # difficulty into the learners' estimate instead of the writer's, and an item that
        # four people have failed, or drifted out of the band it claimed, or simply been
        # answered enough times, is retired rather than left to keep marking people.
        # Authored items keep their authored difficulty (that number belongs to the
        # author, and `docs/UNVERIFIED.md` is explicit that it is a prior) but still
        # record counts, because those counts are what an item review would start from.
        row = s.get(Item, item_id)
        if row is not None and correct is not None:
            row.n_attempts = int(row.n_attempts or 0) + 1
            row.n_correct = int(row.n_correct or 0) + (1 if correct else 0)
            if row.source == "ai2":
                declared = float(row.prior_difficulty if row.prior_difficulty is not None else row.difficulty)
                row.difficulty = GEN.observed_difficulty(
                    row.prior_difficulty,
                    declared=declared,
                    n_correct=row.n_correct,
                    n_attempts=row.n_attempts,
                )
                row.calibrated_at = utcnow()
                retire, why = GEN.should_retire(
                    declared=declared,
                    observed=row.difficulty,
                    n_attempts=row.n_attempts,
                    n_correct=row.n_correct,
                )
                if retire:
                    row.active = False
                    log.info(
                        "generated item %s retired after %d attempts (%d correct): %s",
                        item_id,
                        row.n_attempts,
                        row.n_correct,
                        why,
                    )
    # ---- phase 2: AI 2 (opens its own sessions; reads the committed attempt) ---- #
    teachback = None
    if item["kind"] == "teachback" and teachback_text:
        teachback = await services.ai2.review_teachback(student_id, item, teachback_text)
    feedback = services.ai2.feedback(student_id, item, answer, correct=bool(correct), score=score)

    # ---- phase 3: persist what AI 2 produced, and close the event log for this Q #
    with services.scope() as s:
        att = s.get(Attempt, attempt_id)
        if teachback is not None:
            att.teachback_score = float(teachback.get("score") or 0.0)
            if correct is None:
                correct = att.teachback_score >= float((item.get("rubric") or {}).get("pass_fraction", 0.6))
                att.correct = correct
                score = score if score is not None else att.teachback_score
        att.ai_feedback = feedback
        telemetry.append_event(
            s,
            session_id=session_id,
            student_id=student_id,
            type="answer_submitted",
            payload={"correct": bool(correct), "attempt_no": attempt_no, "score": score},
            item_id=item_id,
            attempt_id=attempt_id,
            ms_since_question=time_on,
        )
        sess = s.get(LearningSession, session_id)
        if sess is not None:
            end_ts = utcnow()
            total_ms = int((end_ts - sess.started_at).total_seconds() * 1000)
            sess.active_ms = T.active_ms(total_ms, sess.away_seconds * 1000)
    # ---- phase 4: AI 1 (traces, retention schedule, strategy credit) ------ #
    trace = services.ai1.record_attempt(
        student_id=student_id,
        topic_id=item["topic_id"],
        correct=bool(correct),
        score=score,
        difficulty=float(item.get("difficulty") or 0.5),
        self_explain=float(teachback.get("score") or 0.0) if teachback else 0.0,
        active_strategies=[x["strategy"] for x in strategies],
        kind=item.get("kind"),
        choices=len(item.get("choices") or []) or None,
    )
    with services.scope() as s:
        sched = s.get(RetentionSchedule, (student_id, item["topic_id"]))
        review_was_due = bool(sched is not None and sched.due_at is not None and sched.due_at <= utcnow() + timedelta(minutes=5))
        recent_n = int(
            s.execute(
                select(func.count())
                .select_from(Attempt)
                .where(Attempt.student_id == student_id, Attempt.submitted_at >= utcnow() - timedelta(days=7))
            ).scalar_one()
        )
    retention = None
    if review_was_due:
        retention = services.ai1.record_retention_check(
            student_id=student_id,
            topic_id=item["topic_id"],
            item_id=item_id,
            correct=bool(correct),
            score=score,
            latency_ms=int(time_on) if time_on else None,
            kind="scheduled",
        )
        retention["was_due"] = True
    # ---- phase 5: the loop. Fail -> diagnose -> reteach -> matched -> compare ---- #
    next_step, compare = _reteach_step(
        services,
        student_id=student_id,
        session_id=session_id,
        item=item,
        item_id=item_id,
        method=effective.method,
        correct=bool(correct) if correct is not None else None,
        expected=float(expected),
        time_on_item_ms=int(time_on) if time_on else None,
        latency_z=None if z is None else float(z),
    )
    return SubmitResult(
        attempt_id=attempt_id,
        item_id=item_id,
        topic_id=item["topic_id"],
        correct=correct,
        score=score,
        method=graded.method,
        feedback=feedback,
        trace=trace,
        metrics=m,
        teachback=teachback,
        retention=retention,
        flags=flags,
        proposals_expected=recent_n >= services.settings.min_evidence_for_proposal,
        teaching_method=effective.method,
        teaching_design=effective.design,
        next_step=next_step,
        method_compare=compare,
    )


def _reteach_step(
    services: Any,
    *,
    student_id: str,
    session_id: str,
    item: dict[str, Any],
    item_id: str,
    method: str,
    correct: bool | None,
    expected: float,
    time_on_item_ms: int | None,
    latency_z: float | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run the diagnose → reteach → compare loop for the answer just given.

    The plan itself is pure (``learning.reteach.plan_reteach``); this is the part that
    gathers the evidence it needs from the database — what else exists on the topic in
    the same format, the prerequisite graph, the learner's measured prior skills, the
    last few responses of this session (for the fatigue check) and the method record
    *across sessions*, which is what makes the comparison a comparison.
    """
    with services.scope() as s:
        topic_id = str(item["topic_id"])
        candidates = [
            {
                "id": it.id,
                "topic_id": it.topic_id,
                "kind": it.kind,
                "difficulty": it.difficulty,
                "est_seconds": it.est_seconds,
                "active": bool(it.active),
                "choices": it.choices or [],
            }
            for it in s.execute(select(Item).where(Item.topic_id == topic_id, Item.active.is_(True))).scalars().all()
        ]
        prereqs = {topic_id: [row.prerequisite_id for row in s.execute(select(TopicPrerequisite).where(TopicPrerequisite.topic_id == topic_id)).scalars().all()]}
        mastery, observations = {}, {}
        for state in s.execute(select(BktState).where(BktState.student_id == student_id)).scalars().all():
            mastery[state.topic_id] = float(state.p_know)
            observations[state.topic_id] = int(state.n_obs)
        recent = [
            {"time_on_item_ms": a.time_on_item_ms, "est_seconds": (float(it.est_seconds) if it else None)}
            for a, it in (
                (a, s.get(Item, a.item_id))
                for a in s.execute(
                    select(Attempt)
                    .where(Attempt.session_id == session_id, Attempt.correct.is_not(None))
                    .order_by(Attempt.submitted_at.desc())
                    .limit(6)
                ).scalars().all()
            )
        ]
        method_rows = M.rows_from_attempts(
            s.execute(
                select(Attempt)
                .where(Attempt.student_id == student_id, Attempt.correct.is_not(None))
                .order_by(Attempt.submitted_at.desc())
                .limit(200)
            ).scalars().all()
        )
        topic = s.get(Topic, topic_id)
        title = topic.title if topic is not None else topic_id
        material = s.execute(
            select(LearningMaterial).where(LearningMaterial.topic_id == topic_id).limit(1)
        ).scalar_one_or_none()
        source_text = (material.body if material is not None else (topic.description if topic is not None else "")) or ""
        # The parameters the *next* question will be predicted with. Read here, while
        # the session is open: the expected value of the reteach question is part of
        # what makes the comparison "actual vs expected" rather than "right vs wrong".
        topic_params = services.ai1.params_for(student_id, topic_id, s)

    plan = RE.plan_reteach(
        learner_id=student_id,
        topic_id=topic_id,
        item=item,
        method=method,
        correct=correct,
        expected_success=expected,
        candidates=candidates,
        prereqs=prereqs,
        mastery=mastery,
        observations=observations,
        time_on_item_ms=time_on_item_ms,
        latency_z=latency_z,
        recent=recent,
        method_rows=method_rows,
    )
    compare = M.method_delta(method_rows, method=method, baseline=plan.to_method) if plan.to_method else None
    payload = plan.as_dict()
    payload["sentence"] = RE.compare_text(plan)
    if plan.question is not None and plan.assignment is not None:
        # The reteach question is served *now*, with its own method's teaching payload,
        # so the next attempt is a real observation under the new method — and it is a
        # question of the same format, so the difference is the teaching.
        question_item = dict(plan.question)
        question_item["topic_id"] = question_item.get("topic_id") or topic_id
        rendered = dict(question_item)
        teaching = CONTENT.teaching_payload(
            method=plan.assignment.method,
            topic_id=topic_id,
            title=str(question_item.get("title") or title),
            source_text=source_text,
            learner_id=student_id,
        )
        p_know = float(mastery.get(topic_id, 0.2))
        payload["item"] = {
            "id": rendered.get("id"),
            "topic_id": topic_id,
            "kind": rendered.get("kind"),
            "difficulty": rendered.get("difficulty"),
            "est_seconds": rendered.get("est_seconds"),
        }
        payload["presentation"] = {
            "method": plan.assignment.method,
            "method_label": M.METHODS[plan.assignment.method].label,
            "method_assignment": plan.assignment.as_dict(),
            "teaching": teaching,
            "expected_success": round(
                expected_success_rate(
                    p_know,
                    float(rendered.get("difficulty") or 0.5),
                    _format_params(topic_params, rendered),
                ),
                3,
            ),
        }
    return (payload if plan.needed else None), compare


def _format_params(base: Any, item: dict[str, Any]) -> Any:
    """The belief's parameters with the *next* question's format guess rate applied."""
    from ..learning.bkt import params_for_kind

    return params_for_kind(
        str(item.get("kind") or ""),
        choices=len(item.get("choices") or []) or None,
        base=base,
    )


def method_report_for(s: Session, student_id: str, *, limit: int = 400) -> dict[str, Any]:
    """Actual vs expected per teaching method, accumulated across every session."""
    rows = M.rows_from_attempts(
        s.execute(
            select(Attempt)
            .where(Attempt.student_id == student_id, Attempt.correct.is_not(None))
            .order_by(Attempt.submitted_at.desc())
            .limit(limit)
        ).scalars().all()
    )
    report = M.method_report(rows)
    report["note"] = (
        "each method is judged on actual minus expected success, so a method that met harder "
        "material is not punished for it; under 8 observations is labelled an early signal"
    )
    return report


def attempts_for(s: Session, student_id: str, *, limit: int = 200, topic_id: str | None = None) -> list[dict[str, Any]]:
    conds = [Attempt.student_id == student_id]
    if topic_id:
        conds.append(Attempt.topic_id == topic_id)
    rows = s.execute(select(Attempt).where(*conds).order_by(Attempt.submitted_at.desc()).limit(limit)).scalars().all()
    titles = {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}
    out = []
    for a in rows:
        out.append(
            {
                "id": a.id,
                "session_id": a.session_id,
                "item_id": a.item_id,
                "topic_id": a.topic_id,
                "topic_title": titles.get(a.topic_id, a.topic_id),
                "kind": None,
                "experience_mode": a.experience_mode,
                "attempt_no": a.attempt_no,
                "correct": a.correct,
                "score": a.score,
                "graded_by": a.graded_by,
                "time_to_first_ms": a.time_to_first_ms,
                "time_on_item_ms": a.time_on_item_ms,
                "change_count": a.change_count,
                "changed_from_correct": a.changed_from_correct,
                "hints_used": a.hints_used,
                "examples_opened": a.examples_opened,
                "explanation_opened": a.explanation_opened,
                "away_ms": a.away_ms,
                "latency_z": a.latency_z,
                "teachback_score": a.teachback_score,
                "interventions": a.intervention_ids or [],
                "feedback": a.ai_feedback,
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
        )
    return out
