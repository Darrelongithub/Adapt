"""Teaching methods, assigned at serve time — never authored into an item.

The problem this module exists to remove:

* an item carried ``experience_mode``, so "this concept is taught visually" was a
  property of the *content*, decided when it was written. A learner therefore met
  the same method for the same concept every time, no method could be compared
  against another on a concept nobody had authored twice, and "was the change any
  good?" could only ever be answered about the items that happened to be written
  one way;
* the *format* of the question moved with the method (visual items were
  multiple-choice, the rest typed), so a difference between two methods could
  equally well be a difference between two answer formats.

Two rules follow, and everything in here implements them:

1. **The method is a decision, not a field.** ``assign_method`` picks one of
   :data:`METHODS` for one (learner, item, exposure) triple from a stable seed, so
   every concept can be taught in any method, the same learner gets the same
   method on a reload, and two learners on the same concept do not.
2. **The method and the question format are separate variables.** The question a
   learner answers is chosen by format first (``matched_question``), and the
   method only decides what is taught *before* it. In the method probe every
   method is measured with the same mix of formats.

What a method is *not*: a learning-style label. These are teaching behaviours
(explain, work an example, show the structure, make them retrieve), chosen from
what was measured, and only ever reversed by more measurement — see
:func:`method_report`, which compares **actual against expected** performance per
method rather than raw accuracy, because raw accuracy across topics compares the
difficulty of the topics and calls it the quality of the teaching.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .seeds import stable_seed

#: Below this many observations a difference is a *signal*, not a finding, and
#: every surface that shows one has to say so and print ``n``. Three or four
#: observations are what a single learner produces in a single session; calling
#: that a result is how a demo turns into a false claim.
EARLY_SIGNAL_N = 8


@dataclass(frozen=True)
class Method:
    id: str
    label: str
    strategy: str  # the catalog strategy this method executes
    surface: str  # what the learner sees before the question
    teaches: str  # the mechanism it is supposed to exploit
    question_policy: str = "same_format"

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "strategy": self.strategy,
            "surface": self.surface,
            "teaches": self.teaches,
        }


METHODS: dict[str, Method] = {
    m.id: m
    for m in (
        Method(
            id="explanation",
            label="Explanation first",
            strategy="segmented_text",
            surface="short explanation with a check after each step",
            teaches="segmenting text so working memory is spent on the idea, not the paragraph",
        ),
        Method(
            id="worked_example",
            label="Worked example first",
            strategy="worked_examples",
            surface="a fully solved, step labelled example",
            teaches="lowering intrinsic load on first exposure so the procedure can be seen whole",
        ),
        Method(
            id="visual",
            label="Structure shown visually",
            strategy="visual_representation",
            surface="a generic diagram of the relations in the concept",
            teaches="dual coding: the relations are drawn instead of held in the head",
        ),
        Method(
            id="retrieval",
            label="Retrieval first",
            strategy="retrieval_practice",
            surface="a short attempt before any explanation",
            teaches="retrieval modifies memory; the attempt is the teaching, not the test",
        ),
    )
}

#: The order methods rotate through. Deliberately not alphabetical and not by
#: catalog position: the rotation has to be reproducible, not meaningful.
METHOD_CYCLE: tuple[str, ...] = ("explanation", "worked_example", "visual", "retrieval")

#: An approved adaptation pins a method instead of rotating to it. This is what
#: makes approving "show the structure visually" actually change what the learner
#: sees; without it, approval moved a flag nothing read.
STRATEGY_TO_METHOD: dict[str, str] = {
    "visual_representation": "visual",
    "worked_examples": "worked_example",
    "segmented_text": "explanation",
    "retrieval_practice": "retrieval",
}

#: Arms for the worked-example question. The detector that used to answer "does a
#: worked example help?" read *whether the learner had opened one* — self-selection,
#: because the learner who opens an example is the learner who is struggling. Arm
#: assignment is seeded per (learner, concept), so the comparison has a design.
ARMS: tuple[str, ...] = ("example_first", "explanation_first")

@dataclass
class MethodAssignment:
    """What is taught before this question, and how we know it was that."""

    method: str
    arm: str
    design: str  # randomised | observational | assigned_by_adaptation
    assigned_by: str  # rotation | approved_adaptation | learner_choice
    seed: int
    exposure: int = 0
    why: str = ""

    @property
    def spec(self) -> Method:
        return METHODS[self.method]

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "method_label": self.spec.label,
            "strategy": self.spec.strategy,
            "surface": self.spec.surface,
            "arm": self.arm,
            "design": self.design,
            "assigned_by": self.assigned_by,
            "exposure": self.exposure,
            "source_type": "controlled" if self.design == "randomised" else "observational",
            "weight": 1.0 if self.design == "randomised" else 0.4,
            "why": self.why,
        }


def method_for_directives(directives: Sequence[Mapping[str, Any]] | None) -> str | None:
    """The method an approved adaptation pins, if any.

    ``visual_representation`` wins over the rest when several are approved: it is
    the change the learner was told would alter what they see, and "approving it
    changed nothing" was a real bug with a real cause — nothing in the serving
    path read it.
    """
    approved: list[str] = []
    for d in directives or ():
        sid = str(d.get("strategy") or d.get("action") or "")
        if sid in STRATEGY_TO_METHOD:
            approved.append(sid)
    for sid in ("visual_representation", "worked_examples", "segmented_text", "retrieval_practice"):
        if sid in approved:
            return STRATEGY_TO_METHOD[sid]
    return None


def assign_method(
    *,
    learner_id: str,
    item_id: str,
    topic_id: str,
    exposure: int = 0,
    directives: Sequence[Mapping[str, Any]] | None = None,
    allow: Iterable[str] | None = None,
    prefer: str | None = None,
    serve_ordinal: int | None = None,
) -> MethodAssignment:
    """Choose the teaching method for one exposure. Deterministic for the learner.

    ``serve_ordinal`` counterbalances across a whole comparison run; callers persist
    it with the assignment. Without it, rotation is per learner/concept.
    ``exposure`` is how many times this learner has already met this concept, so a
    second attempt at the same concept is taught differently from the first even
    when the item row is identical. ``prefer`` is not a preference: it is what the
    reteach loop uses to guarantee the next attempt is *not* the method that just
    failed.
    """
    allowed_set = set(allow) if allow is not None else set(METHOD_CYCLE)
    allowed = [m for m in METHOD_CYCLE if m in allowed_set]
    if not allowed:
        raise ValueError("no supported methods allowed")
    pinned = method_for_directives(directives)
    if pinned and pinned in allowed and prefer is None:
        return MethodAssignment(
            method=pinned,
            arm=ARMS[stable_seed(learner_id, topic_id, "arm") % 2],
            design="assigned_by_adaptation",
            assigned_by="approved_adaptation",
            seed=stable_seed(learner_id, item_id, topic_id),
            exposure=exposure,
            why=f"you approved '{METHODS[pinned].strategy}', so every concept is taught that way",
        )
    seed = stable_seed(learner_id, item_id, topic_id, str(int(exposure)), "method")
    if prefer and prefer in allowed:
        method = prefer
    else:
        # One seeded permutation per learner/block, not an independent draw per item.
        # Consecutive serve ordinals use each allowed method exactly once.
        ordinal = int(exposure) if serve_ordinal is None else int(serve_ordinal)
        block, position = divmod(ordinal, len(allowed))
        unit = topic_id if serve_ordinal is None else "comparison-run"
        ordered = sorted(allowed, key=lambda m: stable_seed(learner_id, unit, str(block), m, "balance"))
        method = ordered[position]
    arm_seed = stable_seed(learner_id, topic_id, "arm", str(int(exposure)))
    arm = ARMS[arm_seed % 2]
    return MethodAssignment(
        method=method,
        arm=arm,
        design="observational" if prefer else "randomised",
        assigned_by="rotation",
        seed=seed,
        exposure=exposure,
        why=(
            f"method {method} for attempt {exposure + 1} on this concept "
            f"(seeded rotation per learner, so it differs from learner to learner)"
        ),
    )


def next_method(
    *,
    learner_id: str,
    item_id: str,
    topic_id: str,
    exclude: str | None = None,
    exposure: int = 0,
    observed: Sequence[Mapping[str, Any]] = (),
) -> MethodAssignment:
    """The method to switch to after a failure.

    Never returns ``exclude`` (the method that just failed), so "reteach in the
    next method" is a property of the code rather than of the demo script. When
    there is evidence, the method with the best *actual-vs-expected* residual is
    tried first; when there is none, the rotation decides, which is what keeps the
    probe from becoming a self-fulfilling experiment.
    """
    allowed = [m for m in METHOD_CYCLE if m != exclude]
    report = method_report(observed, methods=allowed) if observed else {"methods": []}
    ranked = [row for row in report.get("methods", []) if row.get("effective_n", 0) >= EARLY_SIGNAL_N]
    if ranked:
        top = max(float(r.get("residual") or 0.0) for r in ranked)
        tied = [r for r in ranked if float(r.get("residual") or 0.0) == top]
        best = min(tied, key=lambda r: stable_seed(learner_id, item_id, str(exposure), str(r["method"])))
        return MethodAssignment(
            method=str(best["method"]),
            arm=ARMS[stable_seed(learner_id, topic_id, "arm", str(int(exposure))) % 2],
            design="observational",
            assigned_by="measured_best",
            seed=stable_seed(learner_id, item_id, topic_id, str(int(exposure)), "reteach"),
            exposure=exposure,
            why=(
                f"your record with {METHODS[str(best['method'])].label.lower()} is "
                f"{float(best.get('residual') or 0.0):+.2f} against expectation over {int(best.get('n') or 0)} attempts"
            ),
        )
    assignment = assign_method(
        learner_id=learner_id,
        item_id=item_id,
        topic_id=topic_id,
        exposure=exposure,
        allow=allowed,
    )
    assignment.design = "observational"
    assignment.assigned_by = "reteach_rotation"
    assignment.why += "; second exposure is not a controlled experiment"
    return assignment


# --------------------------------------------------------------------------- #
# format-matched questions
# --------------------------------------------------------------------------- #


def question_format(item: Mapping[str, Any]) -> str:
    """The *format* of a question: what the learner has to produce.

    Method and format are different columns on purpose. ``kind`` is the format;
    ``method`` is what happened before it.
    """
    kind = str(item.get("kind") or "").lower()
    if kind == "mcq":
        return "choice"
    if kind == "numeric":
        return "typed_number"
    if kind == "teachback":
        return "explain"
    return "typed_text"


def matched_question(
    candidates: Sequence[Mapping[str, Any]],
    *,
    format: str,
    difficulty: float | None = None,
    exclude: Iterable[str] = (),
    seed: str = "",
) -> Mapping[str, Any] | None:
    """Pick the question that keeps the *format* fixed across methods.

    Sorted by how close the candidate's difficulty is to the one asked for, then by
    a stable seed so two calls agree. Returns ``None`` rather than borrowing a
    different format: a comparison that quietly changes answer format along with
    the method measures the format.
    """
    skip = set(exclude)
    pool = [
        c
        for c in candidates
        if question_format(c) == format and str(c.get("id") or "") not in skip and c.get("active", True)
    ]
    if not pool:
        return None
    target = 0.5 if difficulty is None else float(difficulty)
    pool.sort(
        key=lambda c: (
            abs(float(c.get("difficulty", 0.5)) - target),
            stable_seed(seed, str(c.get("id") or "")),
        )
    )
    return pool[0]


def balanced_probe(
    *,
    concepts: Sequence[Mapping[str, Any]],
    items_by_concept: Mapping[str, Sequence[Mapping[str, Any]]],
    learner_id: str,
    count: int = 8,
) -> list[dict[str, Any]]:
    """The method probe: 6–8 concepts, one question each, methods balanced.

    This is the *second* job the 12-item diagnostic used to do badly at the same
    time as placement. Placement wants information about the learner (and is
    adaptive — see ``selector.placement_plan``); the probe wants a comparison, so
    it needs breadth over concepts, one method per concept, and the same answer
    format wherever a concept offers one. Concepts are walked in a stable
    per-learner order and the method rotates, so across the run each method is
    used on a different concept — not the same two easy concepts every time.
    """
    # One format for the *whole* run, not one per concept. Picking a different format on each
    # concept reintroduces the confound the probe exists to remove: if the worked-example
    # concept is a multiple choice and the explanation concept is typed, the difference
    # between them is partly the question, and guessing on a four-option item is worth 25%.
    # So the run format is chosen once — the format the most concepts can be measured in —
    # and every concept in the probe uses it.
    usable: dict[str, list[Mapping[str, Any]]] = {}
    for concept in concepts:
        pool = [
            i
            for i in (items_by_concept.get(str(concept.get("id") or "")) or ())
            if i.get("active", True) and str(i.get("kind") or "").lower() in ("mcq", "numeric", "text")
        ]
        if pool:
            usable[str(concept.get("id") or "")] = pool
    coverage: dict[str, int] = {}
    for pool in usable.values():
        for fmt in {question_format(i) for i in pool}:
            coverage[fmt] = coverage.get(fmt, 0) + 1
    run_format = sorted(coverage.items(), key=lambda kv: (-kv[1], _FORMAT_ORDER.get(kv[0], 99)))[0][0] if coverage else ""

    out: list[dict[str, Any]] = []
    ordered = sorted(concepts, key=lambda c: stable_seed(learner_id, str(c.get("id") or "")))
    held: list[Mapping[str, Any]] = []
    for concept in ordered:
        if len(out) >= count:
            break
        topic_id = str(concept.get("id") or "")
        pool = usable.get(topic_id) or []
        same = [i for i in pool if question_format(i) == run_format]
        # A concept with nothing in the run format is not measured by a *different* format
        # unless it is needed to reach the minimum six; it is held back for that case.
        if not same:
            held.append(concept)
            continue
        anchor = _anchor_item(same, seed=stable_seed(learner_id, topic_id, "anchor"))
        if anchor is None:
            held.append(concept)
            continue
        out.append(_probe_entry(concept, topic_id, anchor, run_format, learner_id, held_format=True, exposure=len(out)))

    # No format-changing fallback: a shorter honest comparison beats a confounded one.
    return out


#: Preference order when two formats cover the same number of concepts. A typed answer is
#: less guessable than a four-option one, so it is preferred for the same coverage.
_FORMAT_ORDER = {"typed_number": 0, "choice": 1, "typed_text": 2, "explain": 3}


def _probe_entry(
    concept: Mapping[str, Any],
    topic_id: str,
    anchor: Mapping[str, Any],
    fmt: str,
    learner_id: str,
    *,
    held_format: bool,
    exposure: int,
) -> dict[str, Any]:
    assignment = assign_method(
        learner_id=learner_id,
        item_id=str(anchor.get("id")),
        topic_id=topic_id,
        exposure=exposure,
        serve_ordinal=exposure,
    )
    what = "the same kind of question each time" if held_format else f"a {fmt} question, which is the only format this concept has"
    return {
        "concept": topic_id,
        "concept_title": concept.get("title"),
        "item": anchor,
        "format": fmt,
        "format_held": held_format,
        "method": assignment.method,
        "assignment": assignment,
        "reason": (
            f"method probe: {what}, taught through {METHODS[assignment.method].label.lower()}"
            + ("" if held_format else " — the format differs here, so this one is a weaker comparison")
        ),
    }


def _anchor_item(pool: Sequence[Mapping[str, Any]], *, seed: int) -> Mapping[str, Any] | None:
    """Prefer the *most common* format in the pool, then the median difficulty.

    The pool the probe passes in is already one format (see ``balanced_probe``); the
    "most common" rule is here for the fallback path, where a concept is used in its own
    format. Median difficulty rather than the hardest available: the probe wants a
    comparable question, not a ceiling test.
    """
    usable = [i for i in pool if i.get("active", True) and str(i.get("kind")) in ("mcq", "numeric", "text")]
    if not usable:
        return None
    counts: dict[str, int] = {}
    for item in usable:
        counts[question_format(item)] = counts.get(question_format(item), 0) + 1
    best_format = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    same = [i for i in usable if question_format(i) == best_format]
    same.sort(key=lambda i: (float(i.get("difficulty", 0.5)), stable_seed(str(seed), str(i.get("id")))))
    return same[len(same) // 2]


# --------------------------------------------------------------------------- #
# actual vs expected, accumulated
# --------------------------------------------------------------------------- #


def _wilson(successes: float, n: float, *, z: float = 1.959963985) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = max(0.0, min(1.0, successes / n))
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def method_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    methods: Iterable[str] | None = None,
    min_n: int = EARLY_SIGNAL_N,
) -> dict[str, Any]:
    """Per method: how often the learner was right against how often we expected it.

    ``rows`` are scored observations with ``method``, ``correct`` and
    ``expected_success`` (the model's own prediction for that item and that
    learner, recorded when the question was served). ``expected_success`` is what
    makes the comparison a comparison: two methods practised on different topics
    have different baselines, and raw accuracy would report the topic as the
    teaching. Anything under ``min_n`` is labelled **early signal** and is never
    ranked against anything.
    """
    wanted = set(methods) if methods is not None else None
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        method = str(row.get("method") or "unassigned")
        if wanted is not None and method not in wanted:
            continue
        if str(row.get("design")) == "placement":
            continue
        if row.get("correct") is None or row.get("expected_success") is None:
            continue
        buckets.setdefault(method, []).append(row)

    out: list[dict[str, Any]] = []
    for method, group in buckets.items():
        n = len(group)
        correct = [1.0 if r.get("correct") else 0.0 for r in group]
        expected = [float(r["expected_success"]) if r.get("expected_success") is not None else 0.5 for r in group]
        weights = [1.0 if str(r.get("design")) in ("randomised", "counterbalanced") else 0.4 for r in group]
        effective_n = sum(weights)
        actual_rate = sum(c * w for c, w in zip(correct, weights, strict=True)) / effective_n
        expected_rate = sum(e * w for e, w in zip(expected, weights, strict=True)) / effective_n
        residual = actual_rate - expected_rate
        # Predictions are fixed at serve time, not another random sample of responses.
        lo, hi = _wilson(actual_rate * effective_n, effective_n)
        randomised = sum(1 for r in group if str(r.get("design")) in ("randomised", "counterbalanced"))
        pilot_n = sum(1 for r in group if r.get("pilot"))
        pilot_label = None
        if pilot_n:
            from .pilot import PILOT_LABEL

            pilot_label = PILOT_LABEL
        out.append(
            {
                "method": method,
                "label": METHODS[method].label if method in METHODS else method,
                "n": n,
                "n_randomised": randomised,
                "actual": round(actual_rate, 3),
                "expected": round(expected_rate, 3),
                "residual": round(residual, 3),
                "ci": [round(lo - expected_rate, 3), round(hi - expected_rate, 3)],
                "effective_n": round(effective_n, 3),
                "sources": [
                    {"source": "controlled", "weight": 1.0, "n": randomised},
                    {"source": "observational", "weight": 0.4, "n": n - randomised},
                ],
                "evidence": evidence_label(effective_n, min_n=min_n),
                # ``n`` counts every scored observation; ``n_pilot`` says how many of them
                # were seeded before the demo. A number that is partly pilot data is not
                # hidden and not softened: it is shown with the count of what it is made of.
                "n_pilot": pilot_n,
                "pilot": bool(pilot_n),
                "pilot_label": pilot_label,
                "note": EVIDENCE_NOTE if effective_n < min_n else "",
            }
        )
    out.sort(key=lambda r: (r["effective_n"] < min_n, -float(r["residual"]), -int(r["n"])))
    return {
        "methods": out,
        "n_scored": sum(len(g) for g in buckets.values()),
        "n_pilot": sum(int(row.get("n_pilot") or 0) for row in out),
        "min_n_for_a_finding": min_n,
        "labels": {"unit": "actual minus expected success rate", "expected": "the model's own prediction for the same items"},
    }


EVIDENCE_NOTE = "early signal: too few observations to call this a finding; treat it as a hint"


def evidence_label(n: float, *, min_n: int = EARLY_SIGNAL_N) -> str:
    """One vocabulary for sample-size honesty, used by every surface."""
    if n <= 0:
        return "no_data"
    if n < min_n:
        return "early_signal"
    if n < 3 * min_n:
        return "signal"
    return "established"


def method_delta(
    rows: Sequence[Mapping[str, Any]],
    *,
    method: str,
    baseline: str | None = None,
) -> dict[str, Any]:
    """One method's residual, optionally against another method's.

    The paired reading is deliberately conservative: it reports both groups' n and
    the difference of residuals with an interval, and it refuses to produce a
    number at all when either side is empty.
    """
    if baseline is not None:
        formats = [{str(r.get("question_kind") or r.get("kind") or "unknown")
                    for r in rows if r.get("method") == m} for m in (method, baseline)]
        if formats[0] and formats[1] and formats[0] != formats[1]:
            return {"comparable": False, "reason": "question formats differ across methods"}
    report = method_report(rows)
    by_id = {row["method"]: row for row in report["methods"]}
    me = by_id.get(method)
    if me is None:
        return {"comparable": False, "reason": f"no scored attempts yet under {method}"}
    if baseline is None:
        return {"comparable": True, "method": me, "baseline": None}
    other = by_id.get(baseline)
    if other is None:
        return {"comparable": False, "reason": f"no scored attempts yet under {baseline}", "method": me}
    diff = float(me["residual"]) - float(other["residual"])
    return {
        "comparable": True,
        "method": me,
        "baseline": other,
        "residual_difference": round(diff, 3),
        "interval": [round(float(me["ci"][0]) - float(other["ci"][1]), 3),
                     round(float(me["ci"][1]) - float(other["ci"][0]), 3)],
        "evidence": evidence_label(min(float(me["effective_n"]), float(other["effective_n"]))),
        "randomised_n": min(int(me.get("n_randomised") or 0), int(other.get("n_randomised") or 0)),
    }


def rows_from_attempts(attempts: Sequence[Any]) -> list[dict[str, Any]]:
    """A uniform view of stored attempts for :func:`method_report`.

    Accepts anything with the attributes the ``Attempt`` row now carries, so the
    analysis does not depend on a session being open.
    """
    out: list[dict[str, Any]] = []
    for a in attempts:
        get = a.get if isinstance(a, Mapping) else lambda k, d=None, _a=a: getattr(_a, k, d)
        correct = get("correct")
        if correct is None:
            continue
        out.append(
            {
                "method": get("method") or "unassigned",
                "correct": bool(correct),
                "expected_success": get("expected_success"),
                "design": get("mapping_design") or get("design") or "observational",
                "question_kind": get("question_kind") or get("kind") or "",
                "topic_id": get("topic_id"),
                "attempt_id": get("id"),
                # Whether these rows came from the pre-demo pilot. The comparison is not
                # changed for them — the label is what changes, and it has to survive the
                # trip from the row to the screen the learner reads.
                "pilot": bool(get("pilot") or False),
            }
        )
    return out
