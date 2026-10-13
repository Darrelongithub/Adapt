"""The "why did I fail" loop, with its update rules written as code.

Round brief §9.5. On a failed attempt the loop runs, in order:

a. **Prerequisite check** — one question on the *direct* prerequisite, using
   the chain. Not a mastery guess: a question, whose result is data.
b. **Fatigue check** — the last two answers took more than 1.5× the session
   median latency *and* accuracy is falling. That specific rule, not a vibe:
   slow-and-slipping reads as tired, not as untaught.
c. **Reteach + matched B** — the same sub-concept taught with the next-best
   method (best measured residual; ties broken by a seeded random draw, so
   "random on ties" is reproducible rather than atmospheric), then a second
   question of the **same format** as the first. A comparison that changes
   the answer format along with the method measures the format.
d. **Compare** — the second attempt against the first, actual against
   expected under each method, shown with a range and an ``n``.

And then the part that decides what the loop is allowed to *conclude* — the
update rule, with its weights:

| diagnosis | attributed to | method-evidence weight | break suggested |
|---|---|---|---|
| prerequisite check failed | the prerequisite | **0** (the method is not penalised) | no |
| fatigue fired | fatigue | **0.5** | yes |
| neither | the method | **1.0** | no |

Two honesty rules are part of the mechanism, not the copy deck:

* A reteach comparison is *stronger* evidence than watching alone, but it
  is **not a controlled experiment**: the second attempt is a second
  exposure, so some improvement would be expected either way
  (:data:`SECOND_EXPOSURE_CONFOUND`, carried on every outcome payload).
* Self-selected observations (the learner *chose* to open the example) are
  labelled observational and weighted **0.4** wherever they are shown, next
  to the number, and are never presented as proof. Assigned comparisons
  weigh 1.0. That is §9.4, and it is the same pair of numbers the Learner
  State Store already stamps on its evidence log.

This module is deliberately callable standalone: it takes plain data (the
failed attempt, candidate questions, the prerequisite chain, session
latencies, method history) and returns plain data. Wiring it into
``services/attempts.py`` — replacing the thinner ``_reteach_step`` diagnosis
with these rules — is a call-site change, specified exactly in
``agent-notes/5-evidence.md``; nothing here reaches into a session, which is
what lets the simulation tests drive it directly.
"""

from __future__ import annotations

import random
import statistics
from typing import Any, Mapping, Sequence

from . import effect as EFFECT
from . import methods as M
from .seeds import stable_seed

#: The weights of the update rule (brief §9.4–9.5). Controlled, assigned
#: evidence weighs 1.0; an observation the learner selected into weighs 0.4;
#: a fatigued attempt counts at half weight *as method evidence*; a miss
#: explained by a missing prerequisite counts for nothing against the method.
WEIGHT_CONTROLLED = 1.0
WEIGHT_OBSERVATIONAL = 0.4
WEIGHT_FATIGUE = 0.5
WEIGHT_PREREQUISITE = 0.0

#: The sentence that has to travel with every reteach comparison.
SECOND_EXPOSURE_CONFOUND = (
    "this is stronger evidence than watching alone, but it is not a controlled experiment: "
    "the second question was your second time meeting this idea, so some improvement "
    "would be expected either way"
)

#: Fatigue, exactly as the brief states it (§9.5b).
FATIGUE_LATENCY_RATIO = 1.5


# --------------------------------------------------------------------------- #
# (b) fatigue
# --------------------------------------------------------------------------- #

def fatigue_check(latencies_ms: Sequence[float | None], correct: Sequence[bool | None]) -> dict[str, Any]:
    """Last two answers > 1.5× the session median, with accuracy falling.

    Both halves are required. Slow *and accurate* is a careful learner, not
    a tired one; fast-and-wrong is a different signal (effort, see
    ``reteach.fatigue_check``) and is deliberately not this one. Fewer than
    four timed answers cannot fire it — a median of two is a coin flip, and
    this verdict spends the learner's trust when it suggests a break.
    """
    timed = [(float(lat), bool(ok)) for lat, ok in zip(latencies_ms, correct) if lat is not None and ok is not None]
    if len(timed) < 4:
        return {"fired": False, "median_ms": None, "why": f"only {len(timed)} timed answers this session; four are needed before slowness can be read as fatigue"}
    median = statistics.median(lat for lat, _ok in timed)
    last_two = timed[-2:]
    earlier = timed[:-2]
    slow = all(lat > FATIGUE_LATENCY_RATIO * median for lat, _ok in last_two)
    falling = (sum(ok for _lat, ok in last_two) / 2.0) < (sum(ok for _lat, ok in earlier) / len(earlier))
    fired = slow and falling
    return {
        "fired": fired,
        "median_ms": round(median, 1),
        "last_two_ms": [lat for lat, _ok in last_two],
        "ratio_threshold": FATIGUE_LATENCY_RATIO,
        "accuracy_earlier": round(sum(ok for _lat, ok in earlier) / len(earlier), 3),
        "accuracy_last_two": round(sum(ok for _lat, ok in last_two) / 2.0, 3),
        "why": (
            "the last two answers each took over 1.5 times your usual time and the accuracy fell with them — that reads as tiredness, not as not knowing"
            if fired
            else "either the last two answers were not unusually slow, or accuracy held while they slowed"
        ),
    }


# --------------------------------------------------------------------------- #
# (a) the prerequisite check
# --------------------------------------------------------------------------- #

def direct_prerequisites(topic_id: str, prereqs: Mapping[str, Sequence[str]] | None) -> list[str]:
    """The direct prerequisites of a topic, in chain order. Nothing transitive:
    the check asks about the step immediately underneath, not the whole tree."""
    return [str(p) for p in ((prereqs or {}).get(topic_id) or ())]


def select_prerequisite_question(
    topic_id: str,
    prereqs: Mapping[str, Sequence[str]] | None,
    candidates_by_topic: Mapping[str, Sequence[Mapping[str, Any]]] | None,
    *,
    learner_id: str = "",
    exclude_ids: Sequence[str] = (),
) -> tuple[str | None, Mapping[str, Any] | None]:
    """One question on a direct prerequisite — the first prerequisite that has one.

    Deterministic per learner (seeded ordering across prerequisites), so the
    same learner is asked about the same gap first on a reload.
    """
    direct = direct_prerequisites(topic_id, prereqs)
    ordered = sorted(direct, key=lambda p: stable_seed(learner_id, topic_id, p, "prereq-check"))
    skip = {str(i) for i in exclude_ids}
    for prereq in ordered:
        pool = [c for c in ((candidates_by_topic or {}).get(prereq) or ()) if str(c.get("id") or "") not in skip and c.get("active", True)]
        if pool:
            pool.sort(key=lambda c: stable_seed(learner_id, prereq, str(c.get("id") or "")))
            return prereq, pool[0]
    return (ordered[0] if ordered else None), None


# --------------------------------------------------------------------------- #
# serve-time assignment: counterbalanced, seeded, never authored
# --------------------------------------------------------------------------- #

def counterbalanced_assignment(
    *,
    learner_id: str,
    concept_id: str,
    exposure: int = 0,
    methods: Sequence[str] = M.METHOD_CYCLE,
) -> dict[str, Any]:
    """The method for one exposure, assigned by code at serve time.

    Counterbalanced two ways at once: the starting offset is seeded per
    learner (two learners meet the same concept in different orders), and
    the method advances with exposure (one learner cycles through every
    method across attempts). Nothing about the *item* chooses — an authored
    ``experience_mode`` on an item is a label on the content, and a label
    cannot run an experiment.
    """
    allowed = [m for m in methods if m in M.METHODS] or list(M.METHOD_CYCLE)
    offset = stable_seed(learner_id, concept_id, "counterbalance") % len(allowed)
    method = allowed[(offset + int(exposure)) % len(allowed)]
    return {
        "method": method,
        "method_label": M.METHODS[method].label,
        "design": "counterbalanced",
        "source": "assigned at serve time (seeded per learner, rotating with exposure)",
        "weight": WEIGHT_CONTROLLED,
        "exposure": int(exposure),
        "seed": stable_seed(learner_id, concept_id, str(int(exposure)), "counterbalance"),
        "why": f"exposure {int(exposure) + 1} on this concept for this learner lands on {method} in their seeded rotation",
    }


# --------------------------------------------------------------------------- #
# (c) reteach: next-best method, random on ties; matched B, same format
# --------------------------------------------------------------------------- #

def choose_reteach_method(
    method_rows: Sequence[Mapping[str, Any]],
    *,
    failed_method: str | None,
    learner_id: str,
    topic_id: str,
) -> dict[str, Any]:
    """The next method to try: best actual-vs-expected, seeded-random on ties.

    Only methods with at least :data:`~adapt.learning.methods.EARLY_SIGNAL_N`
    observations are ranked — below that the honest state is "no measured
    best", and every other method is an equal candidate for the seeded draw.
    The failed method is always excluded: reteaching in the method that just
    failed is not a reteach.
    """
    candidates = [m for m in M.METHOD_CYCLE if m != failed_method]
    report = M.method_report(method_rows, methods=candidates) if method_rows else {"methods": []}
    ranked = [r for r in report["methods"] if int(r.get("n") or 0) >= M.EARLY_SIGNAL_N]
    if ranked:
        best_residual = max(float(r.get("residual") or 0.0) for r in ranked)
        tied = [str(r["method"]) for r in ranked if abs(float(r.get("residual") or 0.0) - best_residual) < 1e-9]
        evidence_n = int(next(r["n"] for r in ranked if str(r["method"]) == tied[0]))
    else:
        tied = list(candidates)
        best_residual = None
        evidence_n = 0
    rng = random.Random(stable_seed(learner_id, topic_id, str(failed_method), "reteach-tie"))
    chosen = tied[rng.randrange(len(tied))] if len(tied) > 1 else tied[0]
    return {
        "method": chosen,
        "method_label": M.METHODS[chosen].label,
        "tied": tied,
        "tie_broken_by": "seeded random draw among equals" if len(tied) > 1 else "measured best",
        "residual": best_residual,
        "n": evidence_n,
        "design": "counterbalanced",
        "why": (
            f"{M.METHODS[chosen].label.lower()} is the best measured alternative "
            f"({best_residual:+.2f} against expectation over {evidence_n} questions)"
            if best_residual is not None and len(tied) == 1
            else f"no method has enough measured evidence to rank yet, so {M.METHODS[chosen].label.lower()} was drawn from the {len(tied)} untried alternatives"
            if len(tied) > 1
            else f"{M.METHODS[chosen].label.lower()} is the measured best alternative"
        ),
    }


def select_matched_question(
    candidates: Sequence[Mapping[str, Any]],
    first_item: Mapping[str, Any],
    *,
    learner_id: str = "",
) -> tuple[Mapping[str, Any] | None, str, bool]:
    """Question B: same format as A, closest difficulty, never A itself.

    Returns ``(item, format, format_held)``. ``format_held`` is False rather
    than quietly borrowing another format when no same-format question
    exists: a comparison that could not hold the format fixed says so, and
    the loop does not count it.
    """
    fmt = M.question_format(first_item)
    picked = M.matched_question(
        candidates,
        format=fmt,
        difficulty=float(first_item.get("difficulty", 0.5) or 0.5),
        exclude=[str(first_item.get("id") or "")],
        seed=learner_id,
    )
    return picked, fmt, picked is not None


# --------------------------------------------------------------------------- #
# the update rule
# --------------------------------------------------------------------------- #

def attribution_for(*, prereq_status: str, fatigue_fired: bool) -> dict[str, Any]:
    """What a failed attempt is allowed to blame, and at what weight.

    Priority is the brief's order: a failed prerequisite check explains the
    miss without the method being involved; fatigue next (half weight, and
    a break is the intervention, not a different worksheet); the method
    last, at full weight, only when neither explanation fired.
    """
    if prereq_status == "failed":
        return {
            "attributed_to": "prerequisite",
            "method_weight": WEIGHT_PREREQUISITE,
            "counts_against_method": False,
            "suggest_break": False,
            "explanation": "the prerequisite check was failed, so this miss is attributed to the missing prior skill and does not count against the teaching method",
        }
    if fatigue_fired:
        return {
            "attributed_to": "fatigue",
            "method_weight": WEIGHT_FATIGUE,
            "counts_against_method": True,
            "suggest_break": True,
            "explanation": "the fatigue check fired, so this attempt counts at half weight as evidence about the method, and a break is suggested before any reteach",
        }
    return {
        "attributed_to": "method",
        "method_weight": WEIGHT_CONTROLLED,
        "counts_against_method": True,
        "suggest_break": False,
        "explanation": (
            "the prerequisite check was passed, so the miss is attributed to the teaching method at full weight"
            if prereq_status == "passed"
            else "neither a prerequisite failure nor fatigue explains this miss, so it is attributed to the teaching method at full weight"
        ),
    }


# --------------------------------------------------------------------------- #
# (d) compare
# --------------------------------------------------------------------------- #

def compare_attempts(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    reteach_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """First attempt vs reteach attempt, actual against expected, with a range.

    The range is a Wilson interval over the reteach method's own attempts
    (its history plus this one), and ``n`` is printed with it everywhere
    this payload is shown — an improvement measured on one learner's two
    answers is a signal about *this* pair, and the honest sentence says so.
    """
    def residual(row: Mapping[str, Any]) -> float:
        return (1.0 if row.get("correct") else 0.0) - float(row.get("expected_success", 0.5) or 0.5)

    history = [r for r in reteach_rows if r.get("correct") is not None]
    n = len(history) + 1
    successes = sum(1 for r in history if r.get("correct")) + (1 if second.get("correct") else 0)
    lo, hi = EFFECT.proportion_ci(successes, n)
    return {
        "first": {"correct": bool(first.get("correct")), "expected_success": first.get("expected_success"), "residual": round(residual(first), 3)},
        "reteach": {"correct": bool(second.get("correct")), "expected_success": second.get("expected_success"), "residual": round(residual(second), 3)},
        "improved": bool(second.get("correct")) and not bool(first.get("correct")),
        "residual_difference": round(residual(second) - residual(first), 3),
        "range": [round(lo, 3), round(hi, 3)],
        "n": n,
        "evidence": M.evidence_label(n),
        "verdict_label": EFFECT.verdict_label("insufficient_evidence") if n < M.EARLY_SIGNAL_N else "measured",
        "confound": SECOND_EXPOSURE_CONFOUND,
        "note": "the range is over the reteach method's attempts so far, this one included; it is not a verdict on the method",
    }


# --------------------------------------------------------------------------- #
# the loop itself
# --------------------------------------------------------------------------- #

def run_failure_loop(
    *,
    learner_id: str,
    topic_id: str,
    first_item: Mapping[str, Any],
    first_method: str | None,
    first_expected: float | None = None,
    candidates: Sequence[Mapping[str, Any]] = (),
    prereqs: Mapping[str, Sequence[str]] | None = None,
    prereq_candidates: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    prereq_correct: bool | None = None,
    session_latencies_ms: Sequence[float | None] = (),
    session_correct: Sequence[bool | None] = (),
    method_rows: Sequence[Mapping[str, Any]] = (),
    matched_correct: bool | None = None,
    matched_expected: float | None = None,
) -> dict[str, Any]:
    """Run (a)–(d) over plain data and return the whole outcome as data.

    ``prereq_correct`` / ``matched_correct`` are the results of the two
    questions the loop asks, when the caller has already asked them; with
    neither, the return value is the *plan* (which prerequisite question,
    which method, which matched question) plus the diagnosis so far. The
    same function therefore serves the live path (ask, then re-call with
    the answers) and the tests (all answers supplied at once).
    """
    prereq_topic, prereq_question = select_prerequisite_question(
        topic_id, prereqs, prereq_candidates, learner_id=learner_id, exclude_ids=[str(first_item.get("id") or "")]
    )
    prereq_status = "failed" if prereq_correct is False else "passed" if prereq_correct is True else "not_checked"
    fatigue = fatigue_check(session_latencies_ms, session_correct)
    attribution = attribution_for(prereq_status=prereq_status, fatigue_fired=bool(fatigue["fired"]))

    reteach: dict[str, Any] | None = None
    matched: Mapping[str, Any] | None = None
    fmt = M.question_format(first_item)
    format_held = False
    comparison: dict[str, Any] | None = None

    if prereq_status != "failed":
        choice = choose_reteach_method(method_rows, failed_method=first_method, learner_id=learner_id, topic_id=topic_id)
        matched, fmt, format_held = select_matched_question(candidates, first_item, learner_id=learner_id)
        reteach = {
            **choice,
            "deferred_until_after_break": bool(attribution["suggest_break"]),
            "question": (
                {"item_id": matched.get("id"), "topic_id": matched.get("topic_id") or topic_id, "format": M.question_format(matched), "difficulty": matched.get("difficulty")}
                if matched is not None
                else None
            ),
            "format": fmt,
            "format_held_fixed": format_held,
        }
        # The reteach is taught in the chosen method, full stop — the
        # counterbalanced assignment above records *how the loop serves*
        # (seeded, at serve time); the choice of method is the diagnosis.
        reteach["assignment"] = {
            "method": choice["method"],
            "design": "counterbalanced",
            "source": "chosen by the failure loop from measured evidence, never authored into the item",
            "weight": attribution["method_weight"],
        }
        if matched is not None and matched_correct is not None:
            reteach_rows = [r for r in method_rows if str(r.get("method")) == choice["method"]]
            comparison = compare_attempts(
                {"correct": False, "expected_success": first_expected if first_expected is not None else 0.5},
                {"correct": matched_correct, "expected_success": matched_expected if matched_expected is not None else 0.5},
                reteach_rows=reteach_rows,
            )

    result: dict[str, Any] = {
        "needed": True,
        "topic_id": topic_id,
        "first": {"item_id": first_item.get("id"), "method": first_method, "format": fmt, "correct": False},
        "steps": {
            "prerequisite": {
                "topic_id": prereq_topic,
                "question": (
                    {"item_id": prereq_question.get("id"), "format": M.question_format(prereq_question)}
                    if prereq_question is not None
                    else None
                ),
                "status": prereq_status,
                "chain": direct_prerequisites(topic_id, prereqs),
            },
            "fatigue": fatigue,
        },
        "attribution": attribution,
        "reteach": reteach,
        "comparison": comparison,
        "evidence": {
            "kind": "failure_loop",
            "source_type": "controlled",
            "design": "reteach_comparison",
            "method_weight": attribution["method_weight"],
            "from_method": first_method,
            "to_method": (reteach or {}).get("method"),
            "format": fmt,
            "format_held_fixed": format_held,
            "confound": SECOND_EXPOSURE_CONFOUND if comparison else None,
        },
    }
    result["learner_sentence"] = learner_sentence(result)
    return result


def learner_sentence(result: Mapping[str, Any]) -> str:
    """The outcome in one honest paragraph: result, range, n, and the caveat."""
    attribution = result.get("attribution") or {}
    if attribution.get("attributed_to") == "prerequisite":
        topic = (result.get("steps", {}).get("prerequisite") or {}).get("topic_id")
        return (
            f"The check on the step before this one ({topic}) did not hold, so that gap — not the way this was "
            "taught — is the likely reason for the miss. It does not count against this teaching method. "
            "Next: practise that earlier step, then come back to this one."
        )
    if attribution.get("attributed_to") == "fatigue":
        return (
            "Your last answers slowed down and started slipping together, which reads as tiredness rather than "
            "not knowing. This attempt counts at half weight as evidence about the teaching, and a short break "
            "will hold better than pushing on — the reteach is here when you come back."
        )
    comparison = result.get("comparison")
    reteach = result.get("reteach") or {}
    if comparison:
        improved = "you got the matched question right" if comparison["improved"] else "the matched question did not come right this time"
        return (
            f"We taught it a different way ({reteach.get('method_label', reteach.get('method'))}) and asked the same kind of question: {improved}. "
            f"So far that way of teaching sits between {comparison['range'][0]:.0%} and {comparison['range'][1]:.0%} on questions like these, from {comparison['n']} attempt(s) — "
            f"{str(comparison['evidence']).replace('_', ' ')}. Remember: {SECOND_EXPOSURE_CONFOUND}."
        )
    if reteach:
        return (
            f"Nothing before this step explains the miss, so it counts against the way it was taught. "
            f"Next it will be taught as {reteach.get('method_label', reteach.get('method'))}, with the same kind of question, so the comparison is fair."
        )
    return "This miss has been recorded with its context. Nothing is concluded from one answer."


# --------------------------------------------------------------------------- #
# weights, detectors, posteriors — the surfaces §9.3/§9.4/§9.6 ask for
# --------------------------------------------------------------------------- #

def row_weight(row: Mapping[str, Any]) -> float:
    """The weight one evidence row carries: assigned 1.0, self-selected 0.4,
    and a failure-loop method weight (0 / 0.5 / 1) multiplies either."""
    design = str(row.get("design") or row.get("mapping_design") or "")
    base = WEIGHT_OBSERVATIONAL if design == "observational" else WEIGHT_CONTROLLED
    method_weight = row.get("method_weight")
    if method_weight is not None:
        return base * float(method_weight)
    return base


def weighted_method_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per method: raw n, weighted (effective) n, and where the weight came from.

    The raw count and the effective count are both shown, because showing
    only the weighted one would hide how many observations were discounted
    and why. Topic-agnostic by construction: rows from every topic are
    accumulated, which is what makes "all topics" a real filter value.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        method = str(row.get("method") or "unassigned")
        bucket = buckets.setdefault(method, {"method": method, "n": 0, "n_controlled": 0, "n_observational": 0, "effective_n": 0.0})
        bucket["n"] += 1
        if str(row.get("design") or "") == "observational":
            bucket["n_observational"] += 1
        else:
            bucket["n_controlled"] += 1
        bucket["effective_n"] += row_weight(row)
    out = []
    for bucket in buckets.values():
        bucket["effective_n"] = round(bucket["effective_n"], 2)
        bucket["weights"] = {"controlled": WEIGHT_CONTROLLED, "observational": WEIGHT_OBSERVATIONAL}
        bucket["source"] = (
            "assigned at serve time (controlled)"
            if bucket["n_observational"] == 0
            else "self-selected (observational)"
            if bucket["n_controlled"] == 0
            else "a mix of assigned and self-selected"
        )
        out.append(bucket)
    out.sort(key=lambda b: -b["effective_n"])
    return out


def self_selection_detectors(attempts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The detectors that watch what the learner *chose* to do (§9.4).

    They stay — "opened an example, then got it right" is worth noticing —
    but every payload is stamped observational with its 0.4 weight, its
    source, and the reason it is not proof: the learner who opens help is
    usually the learner who was stuck, so the comparison is confounded by
    construction and no honest surface may present it as anything else.
    """
    def one(name: str, label: str, opened_key: str) -> dict[str, Any]:
        opened = [a for a in attempts if a.get(opened_key)]
        closed = [a for a in attempts if not a.get(opened_key) and a.get("correct") is not None]
        opened = [a for a in opened if a.get("correct") is not None]
        base: dict[str, Any] = {
            "detector": name,
            "label": label,
            "design": "observational",
            "source": "self-selected: the learner chose to do this",
            "weight": WEIGHT_OBSERVATIONAL,
            "is_proof": False,
            "note": "not proof: learners who open help are usually the ones who were stuck, so this can inform and cannot decide",
        }
        if len(opened) < 3 or len(closed) < 3:
            return {**base, "status": EFFECT.verdict_label("insufficient_evidence"), "n": len(opened), "n_baseline": len(closed)}
        rate_open = sum(1 for a in opened if a.get("correct")) / len(opened)
        rate_closed = sum(1 for a in closed if a.get("correct")) / len(closed)
        lo, hi = EFFECT.proportion_ci(sum(1 for a in opened if a.get("correct")), len(opened))
        return {
            **base,
            "status": "measured",
            "n": len(opened),
            "n_baseline": len(closed),
            "value": round(rate_open, 3),
            "baseline": round(rate_closed, 3),
            "range": [round(lo, 3), round(hi, 3)],
        }

    return [
        one("opened_example", "Opened a worked example before answering", "examples_opened"),
        one("opened_explanation", "Opened the explanation before answering", "explanation_opened"),
    ]


def strategy_posteriors(rows: Sequence[Mapping[str, Any]], *, seed: int = 20260101) -> dict[str, Any]:
    """Thompson behind the scenes, posterior means on screen (§9.6).

    The sampling stays where it belongs — inside the model, seeded so a
    demo can reproduce it — and what any surface receives is, per method's
    strategy: the posterior mean, the interval around it, and the ``n``.
    A mean without its interval and count is exactly the claim this round
    keeps refusing to make.
    """
    from . import strategies as ST

    model = ST.StrategyModel()
    for row in rows:
        method = str(row.get("method") or "")
        if method not in M.METHODS or row.get("correct") is None:
            continue
        expected = float(row.get("expected_success", 0.5) or 0.5)
        lift = (1.0 if row.get("correct") else 0.0) - expected
        model.record(M.METHODS[method].strategy, lift=lift, weight=row_weight(row))
    import numpy as np

    rng = np.random.default_rng(seed)
    sampled, _value = model.sample(rng=rng) if model.stats else (None, None)
    return {
        "strategies": [
            {**stat.as_dict(), "posterior_mean": stat.as_dict()["p_helps"]}
            for stat in sorted(model.stats.values(), key=lambda s: -s.p_helps)
        ],
        "thompson_sample": {"strategy": sampled, "seed": seed, "note": "the draw the model would act on next; seeded, so this demo can be re-run and get the same draw"},
        "note": "Thompson sampling chooses behind the scenes; what is shown is the posterior mean with its interval and n, never the draw alone",
    }


def record_failure_outcome(state: Any, learner_id: str, result: Mapping[str, Any], *, event_id: str) -> Any:
    """Append one failure-loop outcome to the evidence log (code writes).

    The event is controlled (the loop ran it), and carries the attribution
    weight in its payload, so the weighted summaries above — and a rebuild
    of the learner model — can reproduce exactly what the loop concluded.
    Idempotent by event id, like every write to that log.
    """
    from ..state import EvidenceEvent

    evidence = dict(result.get("evidence") or {})
    return state.ingest(
        learner_id,
        EvidenceEvent(
            event_id=event_id,
            kind="failure_loop",
            source_type="controlled",
            topic_id=str(result.get("topic_id") or "") or None,
            payload={
                "attribution": dict(result.get("attribution") or {}),
                "method_weight": evidence.get("method_weight"),
                "from_method": evidence.get("from_method"),
                "to_method": evidence.get("to_method"),
                "format": evidence.get("format"),
                "design": evidence.get("design"),
                "learner_sentence": result.get("learner_sentence"),
            },
        ),
        module="code",
    )


__all__ = [
    "SECOND_EXPOSURE_CONFOUND",
    "WEIGHT_CONTROLLED",
    "WEIGHT_FATIGUE",
    "WEIGHT_OBSERVATIONAL",
    "WEIGHT_PREREQUISITE",
    "attribution_for",
    "choose_reteach_method",
    "compare_attempts",
    "counterbalanced_assignment",
    "direct_prerequisites",
    "fatigue_check",
    "learner_sentence",
    "record_failure_outcome",
    "row_weight",
    "run_failure_loop",
    "select_matched_question",
    "select_prerequisite_question",
    "self_selection_detectors",
    "strategy_posteriors",
    "weighted_method_summary",
]
