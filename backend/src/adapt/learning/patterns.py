"""Evidence detectors (AI 1's "observe" + "understand" steps).

Each detector is a pure function over an ``EvidenceContext`` (attempts + items +
topics + retention rows) that returns :class:`Observation` records. Three rules
are baked into every one of them, because they are what separates this from a
dashboard:

1. **Facts first.** An Observation carries numbers and sample size. The
   interpretation is a separate object (:mod:`adapt.learning.hypotheses`) with its
   own status, so a hypothesis can never masquerade as a measurement.
2. **Minimum evidence.** Nothing fires below ``min_n``; strength is a function of
   both effect size and n (Wilson interval width), so 3 lucky answers cannot
   outrank 30 consistent ones.
3. **Falsifiable.** Every detector states what observation would make it back off,
   which is what the outcome tracker then checks.

These are behavioural patterns, not psychological claims: "accuracy is 24 points
lower on items with stems over 60 words" is a measurement; "you are a poor
reader" is not something ADAPT is entitled to say.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Sequence

from .effect import proportion_ci
from .spacing import expected_recall


@dataclass
class AttemptRow:
    """Normalised view of one scored attempt (built from Attempt + events)."""

    id: str
    topic_id: str
    item_id: str
    correct: bool | None = None
    score: float | None = None
    time_to_first_ms: int | None = None
    time_on_item_ms: int | None = None
    hints_used: int = 0
    examples_opened: int = 0
    explanation_opened: bool = False
    visual_opened: bool = False
    change_count: int = 0
    away_ms: int = 0
    experience_mode: str = "read"
    stem_words: int = 0
    attempt_no: int = 1
    # --- the teaching method, and how it was chosen ---------------------------- #
    # ``method`` is what was taught before this question; ``question_kind`` is the
    # *format* it was measured with (kept separate: a method comparison that varies
    # the answer format too is a format comparison); ``design`` is ``randomised`` when
    # the serving path assigned the method and ``observational`` when the learner
    # chose it; ``arm`` is which way round the worked-example question was put.
    method: str = "unassigned"
    question_kind: str = ""
    design: str = "observational"
    arm: str = ""
    expected_success: float | None = None
    pilot: bool = False
    teachback_score: float | None = None
    latency_z: float | None = None
    submitted_at: datetime | None = None
    active_adaptations: list[str] = field(default_factory=list)
    session_id: str | None = None
    # wrong-option signature for confusion detection (index of chosen distractor)
    chosen_distractor: int | None = None
    distractor_topic: str | None = None

    @property
    def ok(self) -> float | None:
        if self.score is not None:
            return float(self.score)
        return None if self.correct is None else (1.0 if self.correct else 0.0)

    @property
    def aided(self) -> bool:
        return self.hints_used > 0 or self.explanation_opened or self.examples_opened > 0


@dataclass
class RetentionRow:
    topic_id: str
    lag_hours: float
    half_life_hours: float
    correct: bool
    proposal_id: str | None = None


@dataclass
class LessonRow:
    """One learner pass through one interactive scene, as the detectors see it.

    Deliberately *not* an ``AttemptRow``: a scene has no score, and inventing one would push
    prediction data into BKT, which counts it as a right/wrong observation and quietly corrupts
    a mastery estimate. What a scene does measure is whether the learner can say what happens
    next before the simulation says it, so that is what is carried here.
    """

    run_id: str
    scene_id: str
    spec_id: str
    kind: str
    topic_id: str | None
    frame_count: int
    frames_viewed: int
    furthest_frame: int
    predictions_total: int
    predictions_correct: int
    param_changes: int
    replays: int
    pauses: int
    seconds_watched: int
    finished: bool
    at: datetime | None = None

    @property
    def coverage(self) -> float:
        return min(1.0, self.frames_viewed / max(1, self.frame_count))

    @property
    def prediction_accuracy(self) -> float | None:
        return None if self.predictions_total <= 0 else self.predictions_correct / self.predictions_total


@dataclass
class EvidenceContext:
    student_id: str
    attempts: Sequence[AttemptRow]
    prereqs: dict[str, list[str]] = field(default_factory=dict)
    mastery: dict[str, float] = field(default_factory=dict)  # topic -> p_know
    half_lives: dict[str, float] = field(default_factory=dict)  # topic -> hours
    retention: Sequence[RetentionRow] = field(default_factory=list)
    teachback_later: list[tuple[float, float]] = field(default_factory=list)  # (score, later acc)
    lessons: Sequence[LessonRow] = field(default_factory=list)
    min_n: int = 4
    now: datetime = field(default_factory=datetime.utcnow)

    def usable(self) -> list[AttemptRow]:
        return [a for a in self.attempts if a.ok is not None]


#: A difference seen in fewer than this many observations on either side is a signal
#: about where to look next, not a finding. Every detector states ``n``; every surface
#: that shows an observation shows this label next to it.
EARLY_SIGNAL_N = 8


@dataclass
class Observation:
    detector: str
    metric: str
    value: float
    baseline: float
    n: int
    n_baseline: int
    facts: dict[str, object]
    strength: float
    topic_ids: list[str]
    attempt_ids: list[str]
    candidate_strategy: str
    direction: str = "up"  # does the intervention target raising `value`?
    #: ``randomised`` when the comparison came from an assigned condition,
    #: ``observational`` when it came from what the learner chose to do.
    design: str = "observational"
    #: ``early_signal`` under ``EARLY_SIGNAL_N``, then ``signal``, then ``established``.
    evidence: str = "early_signal"

    def as_dict(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "metric": self.metric,
            "value": round(self.value, 4),
            "baseline": round(self.baseline, 4),
            "n": self.n,
            "n_baseline": self.n_baseline,
            "delta": round(self.value - self.baseline, 4),
            "strength": round(self.strength, 3),
            "topic_ids": self.topic_ids,
            "attempt_ids": self.attempt_ids,
            "candidate_strategy": self.candidate_strategy,
            "direction": self.direction,
            "design": self.design,
            "source_type": "controlled" if self.design == "randomised" else "observational",
            "weight": 1.0 if self.design == "randomised" else 0.4,
            "evidence": self.evidence,
            "facts": self.facts,
        }


def _mean(xs: Sequence[float]) -> float:
    xs = [float(x) for x in xs]
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: Sequence[float]) -> float:
    v = sorted(float(x) for x in xs)
    if not v:
        return 0.0
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _strength(value: float, baseline: float, n_a: int, n_b: int, *, min_n: int, design: str = "randomised") -> float:
    """0..1 blend of effect size and evidence volume (interval-width aware).

    ``design`` scales the result: a difference between two groups the *learner chose*
    is weaker evidence than the same difference between two groups the app assigned,
    because the reason they chose one is usually the reason they did better — that is
    the self-selection that made "learners who opened an example improved" worthless.
    """
    if min(n_a, n_b) < min_n:
        return 0.0
    delta = abs(value - baseline)
    lo_a, hi_a = proportion_ci(int(round(value * n_a)), n_a)
    width = max(0.02, (hi_a - lo_a))
    # effect relative to the uncertainty band around the effect
    raw = max(0.0, min(1.0, (delta / max(0.05, width)) * min(1.0, (n_a + n_b) / 24)))
    return raw if design == "randomised" else raw * 0.6


def evidence_label(n_a: int, n_b: int, *, min_n: int = EARLY_SIGNAL_N) -> str:
    """One vocabulary for sample size, used by every detector and every screen."""
    n = min(n_a, n_b)
    if n <= 0:
        return "no_data"
    if n < min_n:
        return "early_signal"
    if n < 3 * min_n:
        return "signal"
    return "established"


def make(
    *,
    detector: str,
    metric: str,
    value: float,
    baseline: float,
    group: Sequence[AttemptRow],
    other: Sequence[AttemptRow],
    facts: dict[str, object],
    strategy: str,
    min_n: int,
    topics: Sequence[str] | None = None,
    design: str = "randomised",
) -> Observation | None:
    if len(group) < min_n or len(other) < min_n:
        return None
    delta = value - baseline
    if abs(delta) < 0.12:
        return None
    strength = _strength(value, baseline, len(group), len(other), min_n=min_n, design=design)
    if strength < 0.25:
        return None
    label = evidence_label(len(group), len(other))
    return Observation(
        detector=detector,
        metric=metric,
        value=value,
        baseline=baseline,
        n=len(group),
        n_baseline=len(other),
        facts={
            **facts,
            "delta": round(delta, 3),
            # The label and the counts travel with the observation, so a screen cannot
            # show the interesting half (the effect) without the honest half (n, and
            # whether the groups were assigned or chosen).
            "evidence": label,
            "n": len(group),
            "n_baseline": len(other),
            "design": design,
            "note": (
                "early signal: fewer than "
                f"{EARLY_SIGNAL_N} observations on one side, so this says where to look rather than what is true"
                if label == "early_signal"
                else None
            ),
        },
        strength=round(strength, 3),
        topic_ids=sorted(set(topics or [a.topic_id for a in group] or [])),
        attempt_ids=[a.id for a in group][:40],
        candidate_strategy=strategy,
        direction="up" if delta > 0 else "down",
        design=design,
        evidence=label,
    )


# --------------------------------------------------------------------------- #
# detectors
# --------------------------------------------------------------------------- #


def detect_worked_example_need(ctx: EvidenceContext) -> list[Observation]:
    """Does a worked example help this learner? Asked with an arm, not with a click.

    The old version compared *learners who opened an example* with learners who did
    not — self-selection, because the learner who opens an example is the learner who
    is stuck. The first comparison here is therefore between the two **assigned** arms
    (``example_first`` vs ``explanation_first``, randomly assigned per concept by
    ``learning.methods``); the click-based comparison is still reported, but marked
    ``observational`` and scaled down, so it can inform and cannot decide.
    """
    rows = ctx.usable()
    assigned = [a for a in rows if a.design == "randomised" and a.arm in ("example_first", "explanation_first")]
    after_ex = [a for a in assigned if a.arm == "example_first"] or [a for a in rows if a.examples_opened > 0]
    after_read = [
        a for a in assigned if a.arm == "explanation_first"
    ] or [a for a in rows if a.explanation_opened and a.examples_opened == 0 and a.hints_used == 0]
    if len(after_read) < ctx.min_n or len(after_ex) < ctx.min_n:
        return []
    # Both arms have to be assigned *and* big enough. One assigned side with the other side
    # falling back to clicks is a mixed comparison, and a mixed comparison is not randomised --
    # calling it one is exactly the mislabelling this detector was rewritten to stop.
    randomised = (
        len([a for a in assigned if a.arm == "example_first"]) >= ctx.min_n
        and len([a for a in assigned if a.arm == "explanation_first"]) >= ctx.min_n
    )
    v = _mean([a.ok or 0 for a in after_ex])
    b = _mean([a.ok or 0 for a in after_read])
    obs = make(
        detector="worked_example_need",
        metric="accuracy",
        value=v,
        baseline=b,
        group=after_ex,
        other=after_read,
        design="randomised" if randomised else "observational",
        facts={
            "after": "worked example then answer" if randomised else "an example was opened (self selected)",
            "before": "explanation then answer" if randomised else "no example was opened (self selected)",
            "comparison": "assigned arm" if randomised else "what the learner chose to do",
            "median_seconds_to_first_answer_after_read": round(
                _median([a.time_to_first_ms or 0 for a in after_read]) / 1000, 1
            ),
            "median_seconds_to_first_answer_after_example": round(
                _median([a.time_to_first_ms or 0 for a in after_ex]) / 1000, 1
            ),
        },
        strategy="worked_examples",
        min_n=ctx.min_n,
    )
    return [obs] if obs else []


def method_comparison(ctx: EvidenceContext) -> list[Observation]:
    """Actual vs expected per teaching method — the comparison that is *not* raw accuracy.

    Raw accuracy across topics compares the difficulty of the topics and calls the
    difference the quality of the teaching. Here each attempt is scored against the
    model's own prediction for that item (``expected_success``, recorded when the
    question was served), so a method that met harder material is not punished for it,
    and only same-format attempts inside a method are used.

    Takes the shared ``ctx`` like every other detector. It used to take a bare row list,
    which is not what ``run_all`` passes — so this detector was dead code: it raised
    ``TypeError`` on every analysis, ``run_all`` swallowed it by design, and the one
    comparison the whole method story rests on was never computed. ``test_detectors.py``
    now calls every registered detector with a real context so a signature cannot drift
    again.

    Nothing below the evidence bar is emitted (module contract: *nothing fires below*
    ``min_n``). The rows under it are not hidden — ``method_report`` returns them with
    ``n`` and the words "early signal" — they are simply not turned into findings.
    """
    from . import methods as M

    rows = list(ctx.usable())
    scored = [
        {
            "method": a.method,
            "correct": bool(a.ok),
            "expected_success": a.expected_success,
            "design": a.design,
            "question_kind": a.question_kind,
            "topic_id": a.topic_id,
            "attempt_id": a.id,
            # Without this the report cannot count pilot rows, and a sentence quoted from
            # seeded data would carry no label -- the one thing the label exists to prevent.
            "pilot": bool(a.pilot),
        }
        for a in rows
        if a.ok is not None and a.method and a.method != "unassigned" and a.expected_success is not None
    ]
    report = M.method_report(scored)
    out: list[Observation] = []
    for entry in report["methods"]:
        method = str(entry["method"])
        group = [a for a in rows if a.method == method]
        if not group or len(group) < 2:
            continue
        if entry.get("evidence") == "early_signal":
            # Under the bar this is a direction, not a finding: it stays in the method report,
            # where the count is on screen, and out of the evidence list, where a proposal is made.
            continue
        residual = float(entry["residual"])
        # A method that beat its own expectation is a candidate change ("do more of this"). A
        # method that fell short is still recorded -- the report shows it with its n, and the
        # reteach loop is what responds by teaching differently next time -- but it carries no
        # candidate strategy, because "do more of the thing that is underperforming" is not a
        # proposal worth asking a learner to approve. ``hypotheses.draft`` refuses a finding
        # with nothing to propose, so this is evidence without a hypothesis attached.
        strategy = M.METHODS[method].strategy if method in M.METHODS and residual > 0 else ""
        out.append(
            Observation(
                detector="method_effect",
                metric="actual_minus_expected",
                value=float(entry["actual"]),
                baseline=float(entry["expected"]),
                n=int(entry["n"]),
                n_baseline=int(entry["n"]),
                facts={
                    "method": method,
                    "label": entry["label"],
                    "residual": entry["residual"],
                    "ci": entry["ci"],
                    "evidence": entry["evidence"],
                    "formats": sorted({a.question_kind for a in group if a.question_kind}),
                    "n_randomised": entry.get("n_randomised"),
                    "note": entry.get("note") or None,
                    # The label travels with the numbers: a sentence quoted from seeded pilot
                    # rows has to say so, wherever it ends up (see `docs/UNVERIFIED.md`).
                    "n_pilot": entry.get("n_pilot") or 0,
                    "pilot": bool(entry.get("pilot")),
                    "pilot_label": entry.get("pilot_label"),
                    "why_no_proposal": (
                        None if strategy else "fell short of what these questions predicted: reported, not proposed"
                    ),
                },
                strength=float(min(1.0, abs(residual) / 0.2)),
                topic_ids=sorted({a.topic_id for a in group}),
                attempt_ids=[a.id for a in group][:40],
                candidate_strategy=strategy,
                direction="up" if residual >= 0 else "down",
                design="randomised" if int(entry.get("n_randomised") or 0) == int(entry["n"]) else "observational",
                evidence=str(entry["evidence"]),
            )
        )
    return out


def detect_hint_dependency(ctx: EvidenceContext) -> list[Observation]:
    rows = ctx.usable()
    unaided = [a for a in rows if not a.aided]
    hinted = [a for a in rows if a.hints_used > 0 and a.examples_opened == 0]
    if len(unaided) < ctx.min_n or len(hinted) < ctx.min_n:
        return []
    v = _mean([a.ok or 0 for a in hinted])
    b = _mean([a.ok or 0 for a in unaided])
    # Only flag when *aided* is better (dependence), not when hints are useless.
    if v <= b:
        return []
    obs = make(
        detector="hint_dependency",
        metric="unaided_accuracy",
        value=b,
        baseline=v,
        group=unaided,
        other=hinted,
        facts={
            "hint_rate": round(len(hinted) / max(1, len(rows)), 3),
            "median_hints_per_question": _median([a.hints_used for a in hinted]),
            "note": "accuracy with hints is higher than without, and hints are opened on most questions",
        },
        strategy="hint_fading",
        min_n=ctx.min_n,
    )
    return [obs] if obs else []


def detect_long_text_overload(ctx: EvidenceContext) -> list[Observation]:
    rows = ctx.usable()
    long_ = [a for a in rows if a.stem_words >= 60]
    short = [a for a in rows if 0 < a.stem_words < 60]
    if len(long_) < ctx.min_n or len(short) < ctx.min_n:
        return []
    v = _mean([a.ok or 0 for a in short])
    b = _mean([a.ok or 0 for a in long_])
    obs = make(
        detector="long_text_overload",
        metric="accuracy",
        value=v,
        baseline=b,
        group=short,
        other=long_,
        facts={
            "long_stem_words_median": int(_median([a.stem_words for a in long_])),
            "short_stem_words_median": _median([a.stem_words for a in short]),
            "latency_penalty_ms": int(
                _median([a.time_on_item_ms or 0 for a in long_])
                - _median([a.time_on_item_ms or 0 for a in short])
            ),
        },
        strategy="segmented_text",
        min_n=ctx.min_n,
    )
    return [obs] if obs else []


def detect_answer_oscillation(ctx: EvidenceContext) -> list[Observation]:
    """Repeated changes -- including changing a correct answer to a wrong one."""
    rows = ctx.usable()
    shaky = [a for a in rows if a.change_count >= 2]
    steady = [a for a in rows if a.change_count == 0]
    flipped = [a for a in rows if a.change_count >= 1 and (a.ok or 0) < 0.5]
    if len(shaky) < 2 or len(steady) < ctx.min_n:
        return []
    v = _mean([a.ok or 0 for a in steady])
    b = _mean([a.ok or 0 for a in shaky])
    obs = make(
        detector="answer_oscillation",
        metric="accuracy",
        value=v,
        baseline=b,
        group=steady,
        other=shaky,
        facts={
            "questions_with_2plus_changes": len(shaky),
            "changed_after_answering_correctly": len(flipped),
            "median_changes": _median([a.change_count for a in shaky]),
            "note": "changing answers is normal; we only look at whether it tracks with getting them right",
        },
        strategy="confidence_calibration",
        min_n=2,
    )
    return [obs] if obs else []


def detect_prerequisite_gap(ctx: EvidenceContext) -> list[Observation]:
    """Errors on a topic whose prerequisites are weakly traced."""
    out: list[Observation] = []
    rows = ctx.usable()
    for topic, prereqs in ctx.prereqs.items():
        weak = [p for p in prereqs if ctx.mastery.get(p, 1.0) < 0.5]
        if not weak:
            continue
        here = [a for a in rows if a.topic_id == topic]
        if len(here) < ctx.min_n:
            continue
        acc = _mean([a.ok or 0 for a in here])
        worst = min(ctx.mastery.get(p, 0.0) for p in weak)
        if acc > 0.6:
            continue  # not actually struggling: gap hypothesis not needed
        lo, hi = proportion_ci(int(round(acc * len(here))), len(here))
        out.append(
            Observation(
                detector="prerequisite_gap",
                metric="accuracy",
                value=acc,
                baseline=worst,
                n=len(here),
                n_baseline=len(weak),
                facts={
                    "topic": topic,
                    "primary_topic": topic,
                    "weak_prerequisites": weak,
                    "prerequisite_mastery": {p: round(ctx.mastery.get(p, 0.0), 3) for p in weak},
                    "accuracy_ci": [round(lo, 3), round(hi, 3)],
                },
                strength=round(min(0.9, max(0.3, (0.6 - acc) / 0.4 * min(1.0, len(here) / 8))), 3),
                topic_ids=sorted({topic, *weak}),
                attempt_ids=[a.id for a in here][:40],
                candidate_strategy="prerequisite_remediation",
                direction="up",
            )
        )
    return out


def detect_retention_decay(ctx: EvidenceContext) -> list[Observation]:
    """Predicted recall vs actual on delayed checks, where the spacing model does not fit."""
    if len(ctx.retention) < ctx.min_n:
        return []
    preds = [(expected_recall(r.half_life_hours, r.lag_hours), r.correct) for r in ctx.retention]
    actual = _mean([1.0 if c else 0.0 for _, c in preds])
    predicted = _mean([p for p, _ in preds])
    if predicted - actual < 0.15 or actual > 0.9:
        return []
    topics = sorted({r.topic_id for r in ctx.retention})
    n = len(preds)
    return [
        Observation(
            detector="retention_decay",
            metric="retention_7d",
            value=actual,
            baseline=predicted,
            n=n,
            n_baseline=n,
            facts={
                "mean_lag_hours": round(_median([r.lag_hours for r in ctx.retention]), 1),
                "lapses": int(sum(1 for _, c in preds if not c)),
                "note": "the spacing model expected these to still be retrievable",
            },
            strength=min(1.0, (predicted - actual) / 0.3 * min(1.0, n / 10)),
            topic_ids=topics,
            attempt_ids=[r.topic_id for r in ctx.retention][:40],
            candidate_strategy="spaced_practice" if actual > 0.4 else "retrieval_practice",
            direction="up",
        )
    ]


def detect_slow_start(ctx: EvidenceContext) -> list[Observation]:
    """Long time to first interaction combined with low accuracy = overload."""
    rows = [a for a in ctx.usable() if a.time_to_first_ms is not None]
    slow = [a for a in rows if (a.time_to_first_ms or 0) > 30_000]
    fast = [a for a in rows if (a.time_to_first_ms or 0) <= 30_000]
    if len(slow) < ctx.min_n or len(fast) < ctx.min_n:
        return []
    v = _mean([a.ok or 0 for a in fast])
    b = _mean([a.ok or 0 for a in slow])
    obs = make(
        detector="slow_start",
        metric="accuracy",
        value=v,
        baseline=b,
        group=fast,
        other=slow,
        facts={
            "median_seconds_before_first_input": round(_median([a.time_to_first_ms or 0 for a in slow]) / 1000, 1),
            "interpretation": "reading/stalling before any input; we treat this as a load signal, not a motivation signal",
        },
        strategy="worked_examples",
        min_n=ctx.min_n,
    )
    return [obs] if obs else []


def detect_visual_benefit(ctx: EvidenceContext) -> list[Observation]:
    """Measured difference between the visual and text variants of the same topic.

    Legacy delivery variants and opening a visual are observational/self-selected.
    This detector does not establish a causal effect; assigned method comparisons
    are analysed separately using their recorded design.
    """
    rows = ctx.usable()
    visual = [a for a in rows if a.experience_mode in ("visual",) or a.visual_opened]
    plain = [a for a in rows if a not in visual and a.experience_mode in ("read", "practice")]
    if len(visual) < ctx.min_n or len(plain) < ctx.min_n:
        return []
    v = _mean([a.ok or 0 for a in visual])
    b = _mean([a.ok or 0 for a in plain])
    obs = make(
        detector="visual_representation_benefit",
        design="observational",
        metric="accuracy",
        value=v,
        baseline=b,
        group=visual,
        other=plain,
        facts={
            "assignment": "observational: legacy variants and learner-opened visuals",
            "note": "a measured difference on this topic, not a general 'visual learner' claim",
        },
        strategy="visual_representation",
        min_n=ctx.min_n,
        topics=sorted({a.topic_id for a in visual} | {a.topic_id for a in plain}),
    )
    return [obs] if obs else []


def detect_retrieval_over_reread(ctx: EvidenceContext) -> list[Observation]:
    """Delayed recall after re-reading vs after being quizzed."""
    rows = ctx.usable()
    reread = [a for a in rows if a.explanation_opened and a.attempt_no == 1 and a.examples_opened == 0]
    quizzed = [a for a in rows if a.attempt_no >= 2 and not a.explanation_opened]
    if len(reread) < ctx.min_n or len(quizzed) < ctx.min_n:
        return []
    v = _mean([a.ok or 0 for a in quizzed])
    b = _mean([a.ok or 0 for a in reread])
    obs = make(
        detector="retrieval_over_reread",
        metric="retention_7d",
        value=v,
        baseline=b,
        group=quizzed,
        other=reread,
        facts={
            "note": "second attempts after being tested vs second attempts after reading it again",
            "re_read_openings": len([a for a in reread if a.explanation_opened]),
        },
        strategy="retrieval_practice",
        min_n=ctx.min_n,
    )
    return [obs] if obs else []


def detect_confusion_between_types(ctx: EvidenceContext) -> list[Observation]:
    """Distractors chosen that belong to a neighbouring topic -> discrimination problem."""
    rows = [a for a in ctx.usable() if (a.ok or 0) < 0.5 and a.distractor_topic]
    if len(rows) < ctx.min_n:
        return []
    counts: dict[str, int] = {}
    for a in rows:
        counts[a.distractor_topic or "?"] = counts.get(a.distractor_topic or "?", 0) + 1
    top_topic, top_n = max(counts.items(), key=lambda kv: kv[1])
    share = top_n / len(rows)
    if share < 0.5:
        return []
    return [
        Observation(
            detector="type_confusion",
            metric="discrimination_accuracy",
            value=1 - share,
            baseline=0.0,
            n=len(rows),
            n_baseline=len(rows),
            facts={
                "confused_with_topic": top_topic,
                "share_of_errors": round(share, 3),
                "note": "errors concentrate on one neighbouring method, which is a discrimination problem rather than a skill problem",
            },
            strength=min(1.0, share * min(1.0, len(rows) / 6)),
            topic_ids=sorted({a.topic_id for a in rows} | {top_topic}),
            attempt_ids=[a.id for a in rows][:40],
            candidate_strategy="interleaving",
            direction="up",
        )
    ]


def detect_difficulty_misalignment(ctx: EvidenceContext) -> list[Observation]:
    rows = ctx.usable()
    if len(rows) < 2 * ctx.min_n:
        return []
    acc = _mean([a.ok or 0 for a in rows])
    if 0.65 <= acc <= 0.92:
        return []
    too_hard = acc < 0.65
    return [
        Observation(
            detector="difficulty_misalignment",
            metric="accuracy",
            value=acc,
            baseline=0.78,
            n=len(rows),
            n_baseline=0,
            facts={
                "target_success_rate": 0.78,
                "observed": round(acc, 3),
                "direction": "too_hard" if too_hard else "too_easy",
                "median_seconds_per_question": round(_median([a.time_on_item_ms or 0 for a in rows]) / 1000, 1),
            },
            strength=min(1.0, abs(acc - 0.78) / 0.25 * min(1.0, len(rows) / 12)),
            topic_ids=sorted({a.topic_id for a in rows}),
            attempt_ids=[a.id for a in rows][:40],
            candidate_strategy="difficulty_recalibration",
            direction="up" if too_hard else "down",
        )
    ]


def detect_teachback_gain(ctx: EvidenceContext) -> list[Observation]:
    """Does explaining in your own words predict later accuracy on that topic?"""
    pairs = [(s, a) for s, a in ctx.teachback_later if s is not None and a is not None]
    if len(pairs) < max(4, ctx.min_n):
        return []
    hi = [a for s, a in pairs if s >= 0.6]
    lo = [a for s, a in pairs if s < 0.6]
    if len(hi) < 2 or len(lo) < 2:
        return []
    v, b = _mean(hi), _mean(lo)
    obs = make(
        detector="teachback_gain",
        metric="accuracy",
        value=v,
        baseline=b,
        group=[a for a in ctx.usable() if (a.teachback_score or 0) >= 0.6],
        other=[a for a in ctx.usable() if 0 < (a.teachback_score or 0) < 0.6],
        facts={
            "teachbacks": len(pairs),
            "note": "coverage scored explanations (rubric), not writing quality",
        },
        strategy="self_explanation",
        min_n=2,
    )
    return [obs] if obs else []


def detect_scene_prediction_gap(ctx: EvidenceContext) -> list[Observation]:
    """Predictions inside an interactive scene, compared with chance for that scene.

    A learner who watches a simulation can look like they understand it. The only honest signal
    the player produces is *before* the frame that shows the answer: can they say what happens
    next? Consistently wrong predictions with the mechanism in front of them is a different
    problem from a missing worked example, so this reports it separately rather than dressing it
    up as an accuracy number. ``chance`` is per option count (2 options is not as impressive as
    4), and the strategy suggested is the one that actually matches: say why, out loud.
    """
    # a scene must have been *watched* to count: a learner who made three predictions and left is
    # not reporting a gap in understanding, they are reporting a dropped tab
    rows = [r for r in ctx.lessons if r.predictions_total > 0 and (r.finished or r.coverage >= 0.5)]
    if not rows:
        return []
    made = sum(r.predictions_total for r in rows)
    right = sum(r.predictions_correct for r in rows)
    if made < max(ctx.min_n * 2, 8):
        return []
    chance = 1.0 / 3.0  # the engine's prompts are two or three options; 1/3 is the fair floor
    accuracy = right / made
    topics = sorted({r.topic_id for r in rows if r.topic_id})
    if accuracy >= chance + 0.12:
        return []
    n_scenes = len(rows)
    return [
        Observation(
            detector="scene_prediction_gap",
            metric="prediction_accuracy",
            value=accuracy,
            baseline=chance,
            n=made,
            n_baseline=n_scenes,
            strength=min(1.0, (chance + 0.12 - accuracy) / 0.35 * min(1.0, made / 16)),
            topic_ids=topics,
            attempt_ids=[],
            candidate_strategy="self_explanation",
            direction="up",
            facts={
                "predictions": made,
                "correct": right,
                "scenes": n_scenes,
                "kinds": sorted({r.kind for r in rows}),
                "chance": round(chance, 3),
                "watched_but_not_predicting": [
                    {"kind": r.kind, "frames_viewed": r.frames_viewed, "predictions": r.predictions_total}
                    for r in rows
                    if r.coverage > 0.6 and r.predictions_total <= 1
                ][:6],
                "note": "measured on the frames *before* the simulation shows the answer",
            },
        )
    ]


DETECTORS: dict[str, Callable[[EvidenceContext], list[Observation]]] = {
    "worked_example_need": detect_worked_example_need,
    "method_effect": method_comparison,
    "hint_dependency": detect_hint_dependency,
    "long_text_overload": detect_long_text_overload,
    "answer_oscillation": detect_answer_oscillation,
    "prerequisite_gap": detect_prerequisite_gap,
    "retention_decay": detect_retention_decay,
    "slow_start": detect_slow_start,
    "visual_representation_benefit": detect_visual_benefit,
    "retrieval_over_reread": detect_retrieval_over_reread,
    "type_confusion": detect_confusion_between_types,
    "difficulty_misalignment": detect_difficulty_misalignment,
    "teachback_gain": detect_teachback_gain,
    "scene_prediction_gap": detect_scene_prediction_gap,
}


def run_all(ctx: EvidenceContext) -> list[Observation]:
    """Run every detector; sort by evidence strength (weakest last, never hidden)."""
    out: list[Observation] = []
    for fn in DETECTORS.values():
        try:
            out.extend(fn(ctx))
        except Exception:  # a broken detector must not sink the analysis
            import logging

            logging.getLogger("adapt.ai1").exception("detector %s failed", fn.__name__)
    return sorted(out, key=lambda o: (o.strength, o.n), reverse=True)


def catalog() -> list[dict[str, object]]:
    """Exposed via the API so the analysis is auditable, not a black box."""
    return [
        {
            "detector": name,
            "looks_for": doc,
            "min_evidence": "ADAPT_MIN_EVIDENCE (default 6 events/attempts)",
        }
        for name, doc in [
            ("worked_example_need", "accuracy after a worked example vs after reading an explanation"),
            ("hint_dependency", "accuracy with hints vs unaided, plus how often hints were opened"),
            ("long_text_overload", "accuracy on long stem vs short stem items"),
            ("answer_oscillation", "answer changes and their relation to correctness"),
            ("prerequisite_gap", "low accuracy on a topic with weakly traced prerequisites"),
            ("retention_decay", "predicted vs actual recall on delayed checks"),
            ("slow_start", "time to first interaction combined with accuracy"),
            ("visual_representation_benefit", "randomised visual vs text delivery comparison"),
            ("retrieval_over_reread", "later accuracy after testing vs after reading it again"),
            ("type_confusion", "errors concentrated on one neighbouring method"),
            ("difficulty_misalignment", "success rate far from the 65-92% band"),
            ("teachback_gain", "teach back coverage vs later accuracy"),
            ("method_effect", "actual vs expected success per teaching method, accumulated across sessions"),
        ]
    ]
