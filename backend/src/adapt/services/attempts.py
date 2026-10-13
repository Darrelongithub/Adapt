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
from dataclasses import dataclass, field, replace
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
    LearningSession,
    RetentionSchedule,
    Topic,
    utcnow,
)
from ..learning import methods as M
from ..learning import telemetry as T
from ..state import EvidenceEvent
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
    rows = (
        s.execute(
            select(ActiveAdaptation).where(
                ActiveAdaptation.student_id == student_id, ActiveAdaptation.active.is_(True)
            )
        )
        .scalars()
        .all()
    )
    return [
        {"strategy": r.strategy, "action": r.action, "payload": r.payload or {}, "id": r.id}
        for r in rows
    ]


def history_ms(s: Session, student_id: str, *, limit: int = 60) -> list[int]:
    vals = (
        s.execute(
            select(Attempt.time_on_item_ms)
            .where(Attempt.student_id == student_id, Attempt.time_on_item_ms.is_not(None))
            .order_by(Attempt.submitted_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
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
    #: The checked, resumable recovery, or an honest unavailable-content notice.
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
                "label": M.METHODS[self.teaching_method].label
                if self.teaching_method in M.METHODS
                else None,
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
        from .evidence import offered_assignment

        offer = offered_assignment(services, student_id, item_id, session_id)
        assignment = M.assign_method(
            learner_id=student_id,
            item_id=item_id,
            topic_id=item["topic_id"],
            exposure=0,
            directives=[],
        )
        placement = sess.mode in ("placement", "diagnostic", "assessment")
        placement = placement or bool(offer and offer.get("design") == "placement")
        chosen = (offer or {}).get("method") or method
        if chosen not in M.METHODS:
            chosen = assignment.method
        effective = M.MethodAssignment(
            method=chosen,
            arm=str((offer or {}).get("arm") or "unrecorded"),
            design="placement"
            if placement
            else str((offer or {}).get("design") or "observational"),
            assigned_by="server_delivery" if offer else "unrecorded_delivery",
            seed=assignment.seed,
            exposure=int((offer or {}).get("exposure") or 0),
            why="frozen server delivery" if offer else "no server delivery; observational only",
        )
        graded = grade_answer(item, answer, student_text=teachback_text)
        correct = graded.correct
        score = graded.score
        if attempt_no is None:
            attempt_no = (
                int(
                    s.execute(
                        select(func.count())
                        .select_from(Attempt)
                        .where(Attempt.student_id == student_id, Attempt.item_id == item_id)
                    ).scalar_one()
                )
                + 1
            )
        repeated = s.scalar(
            select(func.count())
            .select_from(Attempt)
            .where(
                Attempt.student_id == student_id,
                Attempt.item_id == item_id,
            )
        )
        extra_support = any(
            m.get(key)
            for key in (
                "hints_used",
                "examples_opened",
                "explanation_opened",
                "visual_opened",
            )
        )
        if not placement and (repeated or extra_support):
            effective = replace(
                effective,
                design="observational",
                assigned_by="learner_choice",
                why="repeat exposure or self-selected support; not a controlled comparison",
            )
        lat_hist = history_ms(s, student_id)
        time_on = m.get("time_on_item_ms")
        z = T.latency_z(int(time_on), lat_hist) if time_on else None
        state = s.get(BktState, (student_id, item["topic_id"]))
        p_know = float(state.p_know) if state is not None else 0.2
        params = services.ai1.params_for(
            student_id,
            item["topic_id"],
            s,
            kind=item.get("kind"),
            choices=len(item.get("choices") or []) or None,
        )
        # The model's own prediction for this item, before the answer arrives. This is
        # the number "actual vs expected" compares against, so it is computed the same
        # way the selector computes it and stored with the attempt.
        model_expected = expected_success_rate(p_know, float(item.get("difficulty") or 0.5), params)
        # The client prediction is deliberately ignored. Preserve the serve-time
        # prediction when available; direct API submissions use the pre-answer model.
        expected = float(offer["expected"]) if offer else model_expected
        strategies = active_strategies(s, student_id)
        att = Attempt(
            session_id=session_id,
            student_id=student_id,
            item_id=item_id,
            topic_id=item["topic_id"],
            experience_mode=item.get("experience_mode") or "read",
            attempt_no=attempt_no,
            first_answer={"value": m.get("first_answer")}
            if m.get("first_answer") is not None
            else None,
            final_answer={"value": answer} if not isinstance(answer, dict) else answer,
            correct=correct,
            score=score,
            graded_by="rules",
            time_to_first_ms=m.get("first_interaction_ms"),
            time_on_item_ms=time_on,
            change_count=int(m.get("change_count") or 0),
            changed_from_correct=bool(
                (m.get("answer_changes") or []) and m.get("first_answer") != m.get("final_answer")
            ),
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
        if (
            time_on
            and item.get("est_seconds")
            and time_on < 1000 * max(3.0, float(item["est_seconds"]) * 0.2)
        ):
            flags.append("answered unusually fast — treat this score as weak evidence")
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
                declared = float(
                    row.prior_difficulty if row.prior_difficulty is not None else row.difficulty
                )
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
                correct = att.teachback_score >= float(
                    (item.get("rubric") or {}).get("pass_fraction", 0.6)
                )
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
        active_strategies=[x["strategy"] for x in strategies] if correct and not placement else [],
        expected_success=expected,
        strategy_weight=1.0 if effective.design in ("randomised", "counterbalanced") else 0.4,
        kind=item.get("kind"),
        choices=len(item.get("choices") or []) or None,
    )
    with services.scope() as s:
        sched = s.get(RetentionSchedule, (student_id, item["topic_id"]))
        review_was_due = bool(
            sched is not None
            and sched.due_at is not None
            and sched.due_at <= utcnow() + timedelta(minutes=5)
        )
        recent_n = int(
            s.execute(
                select(func.count())
                .select_from(Attempt)
                .where(
                    Attempt.student_id == student_id,
                    Attempt.submitted_at >= utcnow() - timedelta(days=7),
                )
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
    # ---- the shared store (round brief 2A) --------------------------------- #
    # The evidence log is the one place a scored observation is written, and it is written *here*
    # because only the code layer may write it. Everything downstream (the versioned model, the
    # failure loop, a rebuild) reads this log rather than the tables the app happens to keep.
    #
    # It is additive: the trace above still lands in ``bkt_states`` and the tests that read that row
    # keep working. What the store adds is that the *same* fact is now addressed by an event id, so
    # re-ingesting it cannot double-count, and carries the weight its source deserves.
    try:
        store = getattr(services, "state", None)
        if store is not None:
            store.ingest(
                student_id,
                EvidenceEvent(
                    event_id=f"att_{attempt_id}",
                    kind="attempt",
                    source_type="controlled",
                    topic_id=item["topic_id"],
                    item_id=item_id,
                    correct=bool(correct) if correct is not None else None,
                    score=float(score) if score is not None else None,
                    session_id=session_id,
                    payload={
                        "method": effective.method,
                        "attempt_no": attempt_no,
                        "hints_used": int(m.get("hints_used") or 0),
                        "grading_method": graded.method,
                        "kind_seen": item.get("kind"),
                    },
                ),
            )
            # The model is a pure fold of the log, so advancing it here is safe and idempotent: an
            # unchanged log produces an identical payload and no new version.
            store.advance_model(student_id, module="ai1", reason="ingest", titles=store.title_map())
            # Step 5: record delivery against the plan that served it.
            state = store.session_state(student_id, session_id) if session_id else {}
            plan_id = state.get("plan_id")
            store.record_delivery(
                student_id,
                block=f"item:{item_id}",
                plan_id=str(plan_id) if plan_id else None,
                seen={
                    "item_id": item_id,
                    "topic_id": item["topic_id"],
                    "kind": item.get("kind"),
                    "answer": graded.method,
                },
                why={
                    "correct": bool(correct) if correct is not None else None,
                    "model_version": store.latest_version(student_id),
                    "reason": "the learner answered this item",
                },
                session_id=session_id,
            )
    except Exception:  # pragma: no cover - the store must never be able to lose an answer
        log.exception(
            "attempts: the state store could not record this attempt (the answer is still stored)"
        )

    # Failed A receives no method/strategy penalty until diagnosis and matched B.
    from ..learning.failure import FailureError, FailureLoop
    from .evidence import start_for_attempt

    next_step, compare = None, None
    loop = FailureLoop(services.state)
    if not placement and correct is False:
        try:
            with services.scope() as s:
                next_step = start_for_attempt(services, s, student_id, attempt_id)
        except FailureError as exc:
            next_step = {"stage": "unavailable", "loop_id": attempt_id, "note": str(exc)}
        services.orchestrator.replan(
            student_id,
            session_id,
            reason="failed answer: diagnose before method attribution",
            blocks=[
                {
                    "kind": "failure_recovery",
                    "topic_id": item["topic_id"],
                    "attempt_id": attempt_id,
                    "stage": next_step["stage"],
                }
            ],
        )
    elif not placement and correct is True:
        loop.record_outcome(
            student_id,
            event_id=f"method:{attempt_id}",
            method=effective.method,
            correct=True,
            expected=expected,
            topic_id=item["topic_id"],
            question_kind=item["kind"],
            design=effective.design,
        )
        services.state.advance_model(student_id, reason="method outcome")
    if not placement:
        compare = loop.report(student_id)
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


def method_report_for(
    s: Session,
    student_id: str,
    *,
    state: Any,
    limit: int = 400,
) -> dict[str, Any]:
    """Compatibility surface backed by attributed outcomes, not undiagnosed misses.

    Pilot rows remain separately labelled. Missing attribution is unavailable, not
    a zero success rate; n_scored counts submitted answers, not method evidence.
    """
    from ..learning.failure import outcome_report

    attempts = s.scalars(
        select(Attempt)
        .where(Attempt.student_id == student_id, Attempt.correct.is_not(None))
        .order_by(Attempt.submitted_at.desc())
        .limit(limit)
    ).all()
    historical = M.rows_from_attempts(attempts)
    report = outcome_report(state.evidence_rows(student_id))
    methods = [
        {
            **row,
            "evidence": M.evidence_label(row["effective_n"]),
            "n_randomised": sum(
                source["n"] for source in row["sources"] if source["source"] == "controlled"
            ),
            "note": report["note"],
        }
        for row in report["methods"]
    ]
    pilots = M.method_report([row for row in historical if row.get("pilot")])["methods"]
    if not methods:
        methods = pilots
    known = {row["method"] for row in methods}
    for row in historical:
        method = row["method"]
        if method in known or row.get("design") == "placement" or row.get("pilot"):
            continue
        known.add(method)
        methods.append(
            {
                "method": method,
                "label": M.METHODS[method].label if method in M.METHODS else method,
                "n": 0,
                "effective_n": 0,
                "n_randomised": 0,
                "sources": [],
                "actual": None,
                "expected": None,
                "residual": None,
                "ci": None,
                "evidence": "no_data",
                "note": "No attributed method outcomes; failed A awaits diagnosis.",
            }
        )
    return {
        **report,
        "methods": methods,
        "pilot_methods": pilots,
        "n_scored": sum(row.get("design") != "placement" for row in historical),
        "n_attributed": sum(row["n"] for row in report["methods"]),
        "min_n_for_a_finding": 8,
        "note": "Attributed actual-minus-expected outcomes only; failed A awaits diagnosis. "
        "n_scored counts answers, not method evidence. Pilot data is labelled separately. "
        + report["note"],
    }


def attempts_for(
    s: Session, student_id: str, *, limit: int = 200, topic_id: str | None = None
) -> list[dict[str, Any]]:
    conds = [Attempt.student_id == student_id]
    if topic_id:
        conds.append(Attempt.topic_id == topic_id)
    rows = (
        s.execute(select(Attempt).where(*conds).order_by(Attempt.submitted_at.desc()).limit(limit))
        .scalars()
        .all()
    )
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
                "kind": a.question_kind,
                "method": a.method,
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
