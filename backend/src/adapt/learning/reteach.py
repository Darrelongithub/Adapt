"""Fail -> diagnose -> reteach in another method -> matched question -> compare.

The loop the pitch is about, as one function, because "the app notices you got it
wrong and teaches it differently" was prose: the pieces existed (a prerequisite
check in :mod:`adapt.learning.gaps`, a fatigue/effort signal in
:mod:`adapt.learning.telemetry`, a method rotation in :mod:`adapt.learning.methods`)
and nothing called them in sequence.

Two rules are enforced here, and both exist to stop the system blaming the teaching
for something it did not cause:

1. **Diagnose before attributing.** A wrong answer is not evidence that the method
   failed. The prerequisite check runs first (a missing prior skill explains the
   miss without the method being involved at all), then the fatigue check (a
   sub-second response, or a long run of them, is an effort signal — the answer is
   weak evidence, not a teaching failure). Only when neither fires does the plan
   call it a method problem, and it says so explicitly with ``blame``.
2. **Compare actual with expected.** Every reteach step carries the model's own
   prediction for the failed question and chooses the next question so the *format*
   stays the same. The comparison the learner sees is the residual against
   expectation under each method — accumulated across sessions, because it is read
   from the attempts table, not from the session in front of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import methods as M

#: A response faster than this fraction of the item's own estimate is treated as an
#: effort signal rather than as a measurement. Same rule the graded attempt applies
#: when it flags "answered unusually fast"; it lives here too so the loop can act on
#: it before a second question is served.
RAPID_FRACTION = 0.2
#: Three or more unusually fast responses inside one session is fatigue-shaped.
FATIGUE_RUN = 3
#: A prerequisite this far below the working threshold, with at least this much
#: evidence on the learner, is treated as the cause of the miss.
PREREQ_THRESHOLD = 0.45
PREREQ_MIN_OBS = 2


@dataclass
class ReteachPlan:
    needed: bool
    blame: str = "none"  # prerequisite | fatigue | method | insufficient_context
    reason: str = ""
    from_method: str | None = None
    to_method: str | None = None
    question: Mapping[str, Any] | None = None
    assignment: M.MethodAssignment | None = None
    comparison: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "needed": self.needed,
            "blame": self.blame,
            "reason": self.reason,
            "from_method": self.from_method,
            "to_method": self.to_method,
            "comparison": self.comparison,
            "checks": self.checks,
            "note": self.note,
            "question": None
            if self.question is None
            else {
                "item_id": self.question.get("id"),
                "topic_id": self.question.get("topic_id"),
                "kind": self.question.get("kind"),
                "difficulty": self.question.get("difficulty"),
                "format": M.question_format(self.question),
            },
            "assignment": self.assignment.as_dict() if self.assignment else None,
        }


def prereq_check(
    *,
    topic_id: str,
    prereqs: Mapping[str, Sequence[str]] | None,
    mastery: Mapping[str, float],
    observations: Mapping[str, int] | None = None,
    threshold: float = PREREQ_THRESHOLD,
    min_observations: int = PREREQ_MIN_OBS,
) -> dict[str, Any]:
    """Is there a prerequisite the learner is known to be shaky on?

    Returns ``fired=False`` when the learner has no measured prior skill: the
    absence of evidence about a prerequisite is not the presence of a gap, and
    defaulting it to zero would make every miss look like a prerequisite problem.
    """
    observations = observations or {}
    for prereq in (prereqs or {}).get(topic_id, ()) or ():
        level = mastery.get(prereq)
        n = int(observations.get(prereq, 0))
        if level is None or n < min_observations:
            continue
        if float(level) < threshold:
            return {
                "fired": True,
                "prereq_topic_id": prereq,
                "prereq_mastery": round(float(level), 3),
                "n": n,
                "why": (
                    f"the prerequisite is measured at {float(level):.0%} over {n} attempts, "
                    "so this miss may be that gap rather than the teaching"
                ),
            }
    return {"fired": False, "why": "no measured prerequisite below the working threshold"}


def fatigue_check(
    *,
    time_on_item_ms: int | None,
    est_seconds: float | None,
    recent: Sequence[Mapping[str, Any]] = (),
    latency_z: float | None = None,
    run: int = FATIGUE_RUN,
    rapid_fraction: float = RAPID_FRACTION,
) -> dict[str, Any]:
    """Is this wrong answer better explained by effort than by the method?"""
    est_ms = (float(est_seconds) * 1000.0) if est_seconds else None
    this_rapid = bool(
        time_on_item_ms is not None and est_ms is not None and time_on_item_ms < max(1500.0, est_ms * rapid_fraction)
    )
    if latency_z is not None and latency_z <= -2.0:
        this_rapid = True
    fast_recent = 0
    for row in recent:
        elapsed = row.get("time_on_item_ms")
        estimate = row.get("est_seconds")
        if elapsed is None:
            continue
        if estimate and float(elapsed) < max(1500.0, float(estimate) * 1000.0 * rapid_fraction):
            fast_recent += 1
    fired = this_rapid or fast_recent >= run
    return {
        "fired": fired,
        "this_response_was_rapid": this_rapid,
        "rapid_in_recent": fast_recent,
        "time_on_item_ms": time_on_item_ms,
        "why": (
            "answered faster than the question could be read, or a run of such answers: "
            "an effort signal, so this attempt is weak evidence about the teaching"
            if fired
            else "the time on the question is consistent with attempting it"
        ),
    }


def plan_reteach(
    *,
    learner_id: str,
    topic_id: str,
    item: Mapping[str, Any],
    method: str | None,
    correct: bool | None,
    expected_success: float | None = None,
    candidates: Sequence[Mapping[str, Any]] = (),
    prereqs: Mapping[str, Sequence[str]] | None = None,
    mastery: Mapping[str, float] | None = None,
    observations: Mapping[str, int] | None = None,
    time_on_item_ms: int | None = None,
    latency_z: float | None = None,
    recent: Sequence[Mapping[str, Any]] = (),
    method_rows: Sequence[Mapping[str, Any]] = (),
    exposure: int = 1,
) -> ReteachPlan:
    """What to do about the answer that was just given, and why."""
    if correct is None:
        return ReteachPlan(
            needed=False,
            blame="insufficient_context",
            reason="the response was not scored, so there is nothing to reteach yet",
        )
    if correct:
        return ReteachPlan(needed=False, blame="none", reason="answered correctly: no reteach step needed")

    mastery = dict(mastery or {})
    observations = dict(observations or {})
    pre = prereq_check(
        topic_id=topic_id,
        prereqs=prereqs,
        mastery=mastery,
        observations=observations,
    )
    fatigue = fatigue_check(
        time_on_item_ms=time_on_item_ms,
        est_seconds=float(item.get("est_seconds") or 0) or None,
        recent=recent,
        latency_z=latency_z,
    )
    fmt = M.question_format(item)
    if pre["fired"]:
        return ReteachPlan(
            needed=True,
            blame="prerequisite",
            reason=(
                f"not blaming the method: {pre['why']}. Fix the prior skill first."
            ),
            from_method=method,
            to_method=None,
            question=None,
            comparison={
                "comparable": False,
                "reason": "a prerequisite is below the working threshold, so a method comparison here would be confounded",
                "format_held_fixed": fmt,
            },
            checks={"prerequisite": pre, "fatigue": fatigue},
            note="next step: one or two questions on the prerequisite, then come back to this concept",
        )
    if fatigue["fired"]:
        return ReteachPlan(
            needed=True,
            blame="fatigue",
            reason=f"not blaming the method: {fatigue['why']}",
            from_method=method,
            to_method=None,
            question=None,
            comparison={
                "comparable": False,
                "reason": "a rapid response is weak evidence, so it is not counted as a method failure",
                "format_held_fixed": fmt,
            },
            checks={"prerequisite": pre, "fatigue": fatigue},
            note="next step: a shorter question, or stop for now — the attempt is recorded with a flag saying so",
        )

    matched = M.matched_question(
        candidates,
        format=fmt,
        difficulty=float(item.get("difficulty", 0.5) or 0.5),
        exclude=[str(item.get("id") or "")],
        seed=f"{learner_id}:{topic_id}",
    )
    assignment = M.next_method(
        learner_id=learner_id,
        item_id=str((matched or item).get("id") or ""),
        topic_id=topic_id,
        exclude=method,
        exposure=exposure,
        observed=method_rows,
    )
    comparison = M.method_delta(method_rows, method=assignment.method, baseline=method) if method else {"comparable": False}
    if matched is None:
        return ReteachPlan(
            needed=True,
            blame="method",
            reason=(
                "the method is the likeliest explanation, but no question of the same format is free "
                "on this concept, so the matched comparison cannot be run yet"
            ),
            from_method=method,
            to_method=assignment.method,
            assignment=assignment,
            comparison={**comparison, "comparable": False, "reason": "no question in the same format available"},
            checks={"prerequisite": pre, "fatigue": fatigue},
            note="next step: teach it differently on the next item of this concept, and do not count this pair as a comparison",
        )
    return ReteachPlan(
        needed=True,
        blame="method",
        reason=(
            f"neither a prerequisite gap nor a fatigue signal explains this miss, so the next attempt is "
            f"taught through {M.METHODS[assignment.method].label.lower()} instead of "
            f"{M.METHODS.get(method or '', M.Method('', 'the previous method', '', '', '')).label.lower()}"
        ),
        from_method=method,
        to_method=assignment.method,
        question=matched,
        assignment=assignment,
        comparison={**comparison, "format_held_fixed": fmt, "expected_success": expected_success},
        checks={"prerequisite": pre, "fatigue": fatigue, "format": fmt},
        note=(
            "the question is the same format as the one just missed, so the comparison is between "
            "what was taught, not between how the answer was given"
        ),
    )


def compare_text(plan: ReteachPlan) -> str:
    """One sentence for the learner, with the sample size in it."""
    if not plan.needed:
        return plan.reason
    if plan.blame != "method":
        return plan.note or plan.reason
    delta = plan.comparison.get("residual_difference")
    n = min(
        int((plan.comparison.get("method") or {}).get("n") or 0),
        int((plan.comparison.get("baseline") or {}).get("n") or 0),
    )
    if delta is None or not plan.comparison.get("comparable"):
        return f"Taught differently this time ({plan.to_method}), then the same kind of question. n so far: {n}."
    label = M.evidence_label(n)
    return (
        f"{plan.to_method} is {float(delta):+.2f} against expectation compared with {plan.from_method}, "
        f"over {n} matched attempts ({label.replace('_', ' ')})."
    )
