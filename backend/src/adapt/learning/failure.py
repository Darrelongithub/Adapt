"""Evidence-driven failure recovery, callable without AppServices.

All transitions live in LearnerState's append-only log. Private, code-validated
matched pairs are supplied by the content engine; a merely similar question is
NOT a matched B. Reteach outcomes are observational (second exposure confound),
not a controlled experiment. The attribution multiplier is distinct from the
source weight: prerequisite=0, fatigue=0.5, otherwise=1.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from ..assessment.grading import grade
from ..assessment.selector import gradeable
from ..baseline.factors import preference_vs_performance
from ..state import EvidenceEvent, LearnerState
from .methods import METHODS, _wilson, question_format
from .seeds import stable_seed
from .strategies import StrategyModel

CAVEAT = (
    "This matched reteach comparison is stronger evidence than a self-selected opening, "
    "but not a controlled experiment: a second exposure can help on its own."
)


class FailureError(ValueError):
    pass


def fatigue_flag(recent: Sequence[Mapping[str, Any]]) -> bool:
    """Chronological session answers: both final latencies >1.5 times median, accuracy falls.

    Need at least two earlier answers. Missing/unscored latencies cannot establish fatigue.
    Compare the last-two accuracy with the preceding answers (a binary final pair need not
    be strictly decreasing within itself). This is a signal, never a diagnosis.
    """
    usable = [
        r
        for r in recent
        if r.get("correct") is not None
        and isinstance(r.get("time_on_item_ms"), (int, float))
        and math.isfinite(r["time_on_item_ms"])
        and r["time_on_item_ms"] > 0
    ]
    if len(usable) < 4:
        return False
    median = statistics.median(r["time_on_item_ms"] for r in usable)
    tail, prior = usable[-2:], usable[:-2]
    return all(r["time_on_item_ms"] > 1.5 * median for r in tail) and sum(
        bool(r["correct"]) for r in tail
    ) / 2 < sum(bool(r["correct"]) for r in prior) / len(prior)


def attribution(prerequisite_ok: bool | None, fatigue: bool) -> dict[str, Any]:
    if prerequisite_ok is False:
        return {"cause": "prerequisite", "multiplier": 0.0, "suggest_break": False}
    if fatigue:
        return {"cause": "fatigue", "multiplier": 0.5, "suggest_break": True}
    if prerequisite_ok is None:
        return {"cause": "unchecked_prerequisite", "multiplier": 0.0, "suggest_break": False}
    return {"cause": "method_comparison", "multiplier": 1.0, "suggest_break": False}


def validate_pair(a: Mapping[str, Any], b: Mapping[str, Any]) -> None:
    """Fail closed unless explicitly matched on concept, format, pair id and difficulty."""
    if (
        not a.get("pair_id")
        or a.get("pair_id") != b.get("pair_id")
        or not a.get("concept_id")
        or a.get("concept_id") != b.get("concept_id")
        or a.get("topic_id") != b.get("topic_id")
        or a.get("id") == b.get("id")
        or a.get("pair_role") != "A"
        or b.get("pair_role") != "B"
        or question_format(a) != question_format(b)
        or a.get("kind") != b.get("kind")
        or a.get("difficulty") != b.get("difficulty")
        or not gradeable(a)
        or not gradeable(b)
    ):
        raise FailureError("a validated same-concept, same-format matched A/B pair is required")


def choose_alternative(
    learner: str, loop_id: str, failed: str, variants: Mapping[str, Any], report: Mapping[str, Any]
) -> str:
    available = [m for m in METHODS if m != failed and variants.get(m)]
    if not available:
        raise FailureError("no validated alternate teaching variant for this concept")
    rows = {r["method"]: r for r in report.get("methods", []) if r.get("effective_n", r["n"]) >= 8}
    if report.get("format_comparable") is False:
        rows = {}
    best = max((rows[m]["residual"] for m in available if m in rows), default=None)
    tied = [m for m in available if best is None or (m in rows and rows[m]["residual"] == best)]
    return min(tied, key=lambda m: stable_seed(learner, loop_id, m, "reteach-tie"))


def outcome_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Weighted actual-minus-expected per method across ALL topics; rebuilt from the log.

    Missing predictions are excluded, not replaced by 50%. Intervals are conservative
    Wilson intervals on weighted accuracy, shifted by the fixed mean prediction.
    Effective n = sum of weights; it never exceeds raw n. Reteach and controlled
    sources are visible separately; no point estimate is presented as proof.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("kind") != "method_outcome":
            continue
        p = dict(r.get("payload") or {})
        if p.get("method") not in METHODS or p.get("correct") is None or p.get("expected") is None:
            continue
        weight = float(p.get("multiplier", 1)) * float(r.get("weight", 0.4))
        if weight <= 0:
            continue
        p.update(weight=weight, source_type=r.get("source_type", "observational"))
        groups.setdefault(p["method"], []).append(p)
    out, model = [], StrategyModel()
    for method, group in groups.items():
        n, effective = len(group), sum(p["weight"] for p in group)
        actual = sum(float(p["correct"]) * p["weight"] for p in group) / effective
        expected = sum(float(p["expected"]) * p["weight"] for p in group) / effective
        lo, hi = _wilson(actual * effective, effective)
        sources = []
        for source in sorted({p["source_type"] for p in group}):
            part = [p for p in group if p["source_type"] == source]
            sources.append(
                {
                    "source": source,
                    "n": len(part),
                    "weight": 1.0 if source == "controlled" else 0.4,
                    "effective_n": round(sum(p["weight"] for p in part), 3),
                }
            )
        for p in group:
            model.record(
                METHODS[method].strategy,
                lift=float(p["correct"]) - p["expected"],
                weight=p["weight"],
            )
        out.append(
            {
                "method": method,
                "label": METHODS[method].label,
                "n": n,
                "effective_n": round(effective, 3),
                "actual": round(actual, 3),
                "expected": round(expected, 3),
                "residual": round(actual - expected, 3),
                "ci": [round(lo - expected, 3), round(hi - expected, 3)],
                "sources": sources,
                "formats": sorted({p.get("question_kind", "unknown") for p in group}),
                "evidence": "insufficient evidence" if effective < 8 else "signal",
            }
        )
    format_comparable = len({tuple(m["formats"]) for m in out}) <= 1
    return {
        "format_comparable": format_comparable,
        "methods": sorted(out, key=lambda p: -p["residual"]),
        "strategies": [st.as_dict() for st in model.ranked()],
        "note": "Actual minus predicted success across all topics; associations, not proof.",
        "min_effective_n": 8,
    }


class FailureLoop:
    def __init__(self, state: LearnerState):
        self.state = state

    def _transitions(self, learner: str, loop_id: str) -> list[dict[str, Any]]:
        return [
            r["payload"]
            for r in self.state.evidence_rows(learner)
            if r["kind"] == "failure_transition" and r["payload"].get("loop_id") == loop_id
        ]

    def _save(
        self, learner: str, p: dict[str, Any], step: str, outcomes: Sequence[EvidenceEvent] = ()
    ) -> None:
        # One batch transaction: outcome and completed transition cannot diverge on retry.
        self.state.ingest(
            learner,
            [
                EvidenceEvent(
                    event_id=f"failure:{p['loop_id']}:{step}",
                    kind="failure_transition",
                    source_type="observational",
                    session_id=p["session_id"],
                    payload=p,
                ),
                *outcomes,
            ],
            module="code",
        )

    def start(
        self,
        learner: str,
        *,
        attempt_id: str,
        session_id: str,
        a: dict[str, Any],
        b: dict[str, Any],
        method: str,
        expected_a: float,
        expected_b: float,
        variants: Mapping[str, Any],
        prerequisite: dict[str, Any] | None,
        prerequisite_required: bool,
        recent: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if self._transitions(learner, attempt_id):
            return self.view(learner, attempt_id)
        validate_pair(a, b)
        if method not in METHODS or not all(
            math.isfinite(x) and 0 <= x <= 1 for x in (expected_a, expected_b)
        ):
            raise FailureError("unknown method or invalid frozen prediction")
        next_method = choose_alternative(
            learner, attempt_id, method, variants, self.report(learner)
        )
        if prerequisite_required and prerequisite is None:
            raise FailureError(
                "a question on the direct prerequisite is unavailable; no method attribution"
            )
        if prerequisite is not None and not gradeable(prerequisite):
            raise FailureError("prerequisite question is not gradeable")
        p = {
            "loop_id": attempt_id,
            "session_id": session_id,
            "a": a,
            "b": b,
            "method_a": method,
            "method_b": next_method,
            "expected_a": expected_a,
            "expected_b": expected_b,
            "teaching": variants[next_method],
            "prerequisite": prerequisite,
            "prerequisite_ok": None if prerequisite_required else True,
            "fatigue": fatigue_flag(recent),
            "stage": "prerequisite" if prerequisite_required else "matched_b",
        }
        self._save(learner, p, "start")
        return self.view(learner, attempt_id)

    def view(self, learner: str, loop_id: str) -> dict[str, Any]:
        transitions = self._transitions(learner, loop_id)
        if not transitions:
            raise KeyError(loop_id)
        # Stage ordering is semantic, not dependent on concurrent timestamps.
        p = max(
            transitions,
            key=lambda t: {"prerequisite": 0, "matched_b": 1, "complete": 2}[t["stage"]],
        )
        result = {
            "loop_id": loop_id,
            "stage": p["stage"],
            "note": CAVEAT,
            "fatigue": p["fatigue"],
            "suggest_break": p["fatigue"],
        }
        if p["stage"] == "complete":
            result["outcome"] = p["outcome"]
            return result
        q = p["prerequisite"] if p["stage"] == "prerequisite" else p["b"]
        # Whitelist, not a shallow blacklist: nested private grading data never leaks.
        result["question"] = {
            k: q[k] for k in ("id", "topic_id", "kind", "stem", "difficulty") if k in q
        }
        if q.get("kind") == "mcq":
            result["question"]["choices"] = [
                {"text": c.get("text", "")} if isinstance(c, dict) else {"text": str(c)}
                for c in q.get("choices", [])
            ]
        if p["stage"] == "matched_b":
            result.update(
                method=p["method_b"],
                teaching=p["teaching"],
                attribution=attribution(p["prerequisite_ok"], p["fatigue"]),
            )
        return result

    def answer(
        self, learner: str, loop_id: str, *, question_id: str, answer: Any
    ) -> dict[str, Any]:
        transitions = self._transitions(learner, loop_id)
        if not transitions:
            raise KeyError(loop_id)
        p = dict(
            max(
                transitions,
                key=lambda t: {"prerequisite": 0, "matched_b": 1, "complete": 2}[t["stage"]],
            )
        )
        if p["stage"] == "complete":
            return self.view(learner, loop_id)
        q = p["prerequisite"] if p["stage"] == "prerequisite" else p["b"]
        if q["id"] != question_id:
            # Replayed prerequisite submission after a successful transition is a no-op.
            if p["prerequisite"] and p["prerequisite"]["id"] == question_id:
                return self.view(learner, loop_id)
            raise FailureError("answer the question currently on offer")
        scored = grade(q, answer)
        if p["stage"] == "prerequisite":
            p.update(prerequisite_ok=scored.correct, stage="matched_b")
            event = EvidenceEvent(
                event_id=f"failure:{loop_id}:prerequisite-score",
                kind="attempt",
                topic_id=q["topic_id"],
                item_id=q["id"],
                correct=scored.correct,
                score=scored.score,
                session_id=p["session_id"],
                payload={"question_kind": q["kind"]},
            )
            self._save(learner, p, "prerequisite", [event])
        else:
            rule = attribution(p["prerequisite_ok"], p["fatigue"])
            # One paired observation cannot estimate a population effect: use a conservative
            # bounded range [-2,2], never a degenerate bootstrap interval from one pair.
            delta = float(scored.correct) - p["expected_b"] + p["expected_a"]
            p.update(
                stage="complete",
                outcome={
                    **rule,
                    "n": 1,
                    "interval": [-2.0, 2.0],
                    "before_correct": False,
                    "after_correct": scored.correct,
                    "residual_difference": round(delta, 3),
                    "source": "observational",
                    "source_weight": 0.4,
                    "effective_weight": 0.4 * rule["multiplier"],
                    "evidence": "insufficient evidence",
                    "note": CAVEAT,
                },
            )
            events = [
                EvidenceEvent(
                    event_id=f"failure:{loop_id}:outcome:{role}",
                    kind="method_outcome",
                    source_type="observational",
                    topic_id=p["a"]["topic_id"],
                    session_id=p["session_id"],
                    payload={
                        "method": p[f"method_{role}"],
                        "correct": correct,
                        "expected": p[f"expected_{role}"],
                        "multiplier": rule["multiplier"],
                        "question_kind": p["a"]["kind"],
                        "comparison": "matched_reteach",
                    },
                )
                for role, correct in (("a", False), ("b", scored.correct))
            ]
            events.append(
                EvidenceEvent(
                    event_id=f"failure:{loop_id}:matched-b-score",
                    kind="attempt",
                    topic_id=q["topic_id"],
                    item_id=q["id"],
                    correct=scored.correct,
                    score=scored.score,
                    session_id=p["session_id"],
                    payload={"question_kind": q["kind"], "attribution": rule},
                )
            )
            self._save(learner, p, "complete", events)
        return self.view(learner, loop_id)

    def record_outcome(
        self,
        learner: str,
        *,
        event_id: str,
        method: str,
        correct: bool,
        expected: float,
        topic_id: str,
        question_kind: str,
        design: str = "observational",
        multiplier: float = 1.0,
    ) -> None:
        """Code-only ingestion hook for assigned attempts and self-selected observations.

        A failed first attempt must be deferred until its diagnostic attribution is known;
        use answer() for the matched failure pair, not this hook a second time.
        """
        if method not in METHODS or not math.isfinite(expected) or not 0 <= expected <= 1:
            raise FailureError("invalid method or prediction")
        if not math.isfinite(multiplier) or not 0 <= multiplier <= 1:
            raise FailureError("invalid attribution multiplier")
        source = "controlled" if design in ("randomised", "counterbalanced") else "observational"
        self.state.ingest(
            learner,
            EvidenceEvent(
                event_id=event_id,
                kind="method_outcome",
                source_type=source,
                topic_id=topic_id,
                payload={
                    "method": method,
                    "correct": correct,
                    "expected": expected,
                    "question_kind": question_kind,
                    "multiplier": multiplier,
                },
            ),
            module="code",
        )

    def report(self, learner: str) -> dict[str, Any]:
        result = outcome_report(self.state.evidence_rows(learner))
        # Reuse the baseline's stated-vs-measured contract, but rank the actual-minus-expected
        # evidence (not raw accuracy) and carry ranges, weights and minimum n into the copy.
        methods = result["methods"]
        usable = [m for m in methods if m["effective_n"] >= 8]
        outcomes = {
            "by_format": {
                m["method"]: {"n": m["n"], "correct": m["actual"] * m["n"]} for m in usable
            }
        }
        pref = preference_vs_performance({}, self.state.preferences(learner), outcomes=outcomes)
        if pref and len(usable) >= 2 and result["format_comparable"]:
            pref.update(
                measured=usable,
                sentence=(
                    f"You picked {str(pref['stated']).replace('_', ' ')}. "
                    "The largest actual-minus-expected signal so far is "
                    f"{usable[0]['label'].lower()}; "
                    "the ranges and evidence sources below matter more than the ranking."
                ),
                note=CAVEAT,
            )
        else:
            pref = None
        result["preference_vs_performance"] = pref
        return result
