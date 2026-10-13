"""Item selection: the diagnostic, and adaptive selection afterwards.

Two different jobs, one module:

**Diagnostic** (:func:`diagnostic_plan`). Its purpose is *measurement*, so it
deliberately varies the delivery mode (read / visual / example / practice /
teach-back) and difficulty, and assigns those variants by a seeded rotation --
randomised per learner, reproducible for the same learner. That is what lets AI 1
compare "what happened under condition A vs condition B" instead of asking the
student which condition they like.

**Adaptive selection** (:func:`next_items`). Score for each candidate:

    utility = w1 * information_gain(p_know)      # what the answer tells us
            + w2 * due_review_need               # spacing pressure (retention)
            + w3 * prerequisite_gap_severity     # remediation first
            + w4 * target_success_fit            # stay in the learning zone
            - w5 * repetition_penalty

so the next question is chosen by expected value of information *and* by memory
science, not by "next row in the table". Approved adaptations shift the weights --
which is exactly the visible loop the demo has to show.
"""

from __future__ import annotations

import logging
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from ..learning import content as CONTENT
from ..learning import methods as M
from ..learning.bkt import BKTParams, information_gain, params_for_kind, update
from ..learning.seeds import stable_seed  # re-exported: callers import it from here

log = logging.getLogger("adapt.selector")

MODE_CYCLE = ("read", "visual", "example", "practice", "teachback")

#: How far one point of item difficulty moves the log-odds of a correct answer.
#: 1.0 means an item authored at difficulty 1.0 is ``e`` times harder (in odds)
#: than the same learner's prediction at difficulty 0.0. Kept gentle on purpose:
#: ``difficulty`` is an author's prior, not a calibrated parameter (see
#: docs/UNVERIFIED.md), so it may nudge the target -- it must not dominate it.
DIFFICULTY_LOG_ODDS = 1.0

#: Kinds that are a *question*: something the learner answers and the build can mark.
#: ``worked_example`` is teaching material and ``visual``-only items are diagrams; a
#: plan that serves one has served a screen the learner cannot answer, and the attempt
#: it records is not an observation of anything.
QUESTION_KINDS = ("mcq", "numeric", "text", "teachback")


def gradeable(item: Mapping[str, Any]) -> bool:
    """Can this build actually mark an answer to this item, right or wrong?

    The placement plan chose by information gain alone and put a ``worked_example``
    first — a screen with no answer to give, recorded as a correct attempt, which is
    the worst of both worlds: no measurement and a trace that says the learner knew
    it. Anything served has to be answerable *and* markable, so the check lives here
    and every plan applies it.
    """
    kind = str(item.get("kind") or "")
    spec = item.get("answer_spec") or {}
    if kind not in QUESTION_KINDS:
        return False
    if kind == "mcq":
        choices = item.get("choices") or []
        if not choices:
            return False
        return spec.get("correct_index") is not None or any(c.get("correct") for c in choices)
    if kind == "numeric":
        return spec.get("value") is not None or bool(spec.get("formula"))
    if kind == "text":
        return bool(spec.get("values") or spec.get("keywords") or spec.get("value") is not None or spec.get("formula"))
    if kind == "teachback":
        return bool(item.get("rubric"))
    return False


def servable(item: Mapping[str, Any]) -> bool:
    """Answerable and markable, and not retired."""
    return bool(item.get("active", True)) and gradeable(item)


#: Never sent to a client that has not answered yet.
PRIVATE_ITEM_KEYS = frozenset({"answer_spec", "solution_steps", "hints", "rubric", "stem_template"})


@dataclass
class Pick:
    item: dict[str, Any]
    topic_id: str
    reason: str
    utility: float = 0.0
    attach_worked_example: bool = False
    feedback_delay_s: int = 0
    max_words: int | None = None
    require_teachback: bool = False
    hints_allowed: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # --- the assigned teaching method (see learning/methods.py) ---------------- #
    # These are decisions made *now*, for this learner, not fields of the item. The
    # item's own ``experience_mode`` is only kept for the record.
    assignment: dict[str, Any] | None = None
    teaching: dict[str, Any] | None = None
    expected_success: float | None = None

    def as_dict(self, *, include_answer: bool = False) -> dict[str, Any]:
        """Client-shaped view of this pick.

        The grading keys are stripped unless a caller explicitly asks for them: this
        payload is what goes to the browser *before* the learner has answered, and a
        single forgotten ``public_item()`` call at a new call site would hand them the
        correct index of the question we are about to measure.
        """
        item = self.item
        if not include_answer:
            item = {k: v for k, v in self.item.items() if k not in PRIVATE_ITEM_KEYS}
            if isinstance(item.get("choices"), list):
                item["choices"] = [{"index": i, "text": c.get("text")} for i, c in enumerate(self.item["choices"])]
        assignment = self.assignment or {}
        method = str(assignment.get("method") or self.item.get("experience_mode") or "read")
        presentation: dict[str, Any] = {
            "attach_worked_example": self.attach_worked_example,
            "feedback_delay_s": self.feedback_delay_s,
            "max_words": self.max_words,
            "require_teachback": self.require_teachback,
            "hints_allowed": self.hints_allowed,
            # ``experience_mode`` now reports the method that was *assigned* for this
            # attempt. It used to report the mode the item's author picked, which is
            # why approving a change could not alter it.
            "experience_mode": method,
            "method": assignment.get("method"),
            "method_label": assignment.get("method_label"),
            "method_assignment": assignment or None,
            "teaching": self.teaching,
            "expected_success": None if self.expected_success is None else round(float(self.expected_success), 3),
        }
        return {
            "item": item,
            "topic_id": self.topic_id,
            "reason": self.reason,
            "utility": round(self.utility, 4),
            "presentation": presentation,
            "decision_trace": self.meta,
        }


def placement_plan(
    *,
    topics: Sequence[dict[str, Any]],
    items_by_topic: Mapping[str, Sequence[dict[str, Any]]],
    count: int = 8,
    seed: str | None = None,
    answered: Sequence[dict[str, Any]] = (),
    params: BKTParams | None = None,
    teaching_text: Mapping[str, str] | None = None,
    teaching_titles: Mapping[str, str] | None = None,
) -> list[Pick]:
    """Placement: **adaptive**, and only about placement.

    The 12-item diagnostic was doing two jobs at once (place the learner *and*
    compare teaching methods) and did both badly: it was long, it was the same
    sequence for every learner, and the method differences it measured were
    confounded with topic and treatment order.

    This is the placement half, and it is the adaptive one:

    * beliefs start from the prior (or from ``answered``, the attempts this learner
      has already recorded on this session — each call re-plans, so the *next*
      request depends on the previous answer, which is what makes it adaptive);
    * breadth first: one item per topic before a second from any topic, easiest
      topic first, because an early success is both more informative and less
      discouraging than an early failure on material never taught;
    * the item chosen within a topic is the one with the highest **expected
      information gain** under the current belief, not the next row in the table;
    * a question that has already been used (or answered) is never repeated.
    """
    params = params or BKTParams()
    teaching_text = teaching_text or {}
    teaching_titles = teaching_titles or {}
    rng = random.Random(stable_seed(seed or "placement"))
    beliefs: dict[str, float] = defaultdict(lambda: params.p_init)
    difficulty_by_key: dict[str, float] = {}
    titles: dict[str, str] = {}
    pool: dict[str, list[dict[str, Any]]] = {}
    for topic in topics:
        key = next(
            (k for k in (topic.get("id"), topic.get("code")) if k and items_by_topic.get(k)),
            None,
        )
        if key is None:
            continue
        difficulty_by_key[key] = float(topic.get("difficulty", 0.5) or 0.5)
        titles[key] = str(topic.get("title") or key)
        # Only questions the build can mark. A worked example in a placement plan is a
        # screen with no answer, recorded as a correct attempt (see ``gradeable``).
        pool[key] = [i for i in items_by_topic[key] if servable(i)]
        if not pool[key]:
            pool.pop(key)
    for row in answered:
        topic = str(row.get("topic_id") or "")
        if topic in pool and row.get("correct") is not None:
            beliefs[topic] = update(beliefs[topic], bool(row["correct"]), params)
    if not pool:
        return []
    used: set[str] = {str(r.get("item_id") or "") for r in answered if r.get("item_id")}
    order = sorted(pool, key=lambda key: (difficulty_by_key.get(key, 0.5), key))
    picks: list[Pick] = []
    counts: dict[str, int] = {k: 0 for k in pool}
    while len(picks) < count:
        candidates = [k for k in order if any(_key(i) not in used for i in pool[k])]
        if not candidates:
            break
        # Fewest questions so far first: that is the breadth rule, and among equally
        # covered topics the easier one goes first.
        topic = min(candidates, key=lambda k: (counts[k], difficulty_by_key.get(k, 0.5)))
        belief = beliefs[topic]
        free = [i for i in pool[topic] if _key(i) not in used]
        item = max(
            free,
            key=lambda i: (
                information_gain(belief, _params_for_item(params, i)),
                -abs(float(i.get("difficulty", 0.5)) - (1.0 - belief)),
                -stable_seed(str(seed), _key(i)),
            ),
        )
        used.add(_key(item))
        counts[topic] += 1
        gain = information_gain(belief, _params_for_item(params, item))
        # Placement is measurement, so the method is assigned but *not* applied as a
        # teaching intervention that would change what is measured: the plan records
        # which method is being used so placement attempts are not counted as method
        # evidence later (methods.rows_from_attempts keys on the method, and the probe
        # is where a method comparison belongs).
        assignment = M.assign_method(
            learner_id=str(seed or ""),
            item_id=_key(item),
            topic_id=str(item.get("topic_id") or topic),
            exposure=counts[topic],
        )
        picks.append(
            Pick(
                item=item,
                topic_id=item.get("topic_id") or topic,
                assignment={**assignment.as_dict(), "design": "placement", "assigned_by": "placement"},
                teaching=_teaching_for(
                    assignment,
                    topic_id=str(item.get("topic_id") or topic),
                    title=str(teaching_titles.get(topic) or topic),
                    source_text=teaching_text.get(topic) or "",
                    learner_id=str(seed or ""),
                ),
                reason=(
                    f"placement: {titles.get(topic, topic)}, belief {belief:.2f}, "
                    f"this question carries the most information about it"
                ),
                utility=float(gain),
                feedback_delay_s=0,
                meta={
                    "phase": "placement",
                    "belief_before": round(belief, 3),
                    "information_gain": round(gain, 4),
                    "rng": "seeded",
                    "adaptive": True,
                },
            )
        )
    # A stable per-learner shuffle of the *tail* only: the first pass keeps its
    # easiest-first order (that ordering is what an early success depends on).
    if len(picks) > 3:
        head, tail = picks[:3], picks[3:]
        rng.shuffle(tail)
        picks = head + tail
    return picks


def _params_for_item(params: BKTParams, item: Mapping[str, Any]) -> BKTParams:
    """The item's format has a guess rate; the belief update should know it."""
    kind = str(item.get("kind") or "")
    return params_for_kind(kind, choices=len(item.get("choices") or []) or None, base=params)


def method_probe_plan(
    *,
    topics: Sequence[dict[str, Any]],
    items_by_topic: Mapping[str, Sequence[dict[str, Any]]],
    seed: str | None = None,
    learner_id: str | None = None,
    count: int = 8,
    teaching_text: Mapping[str, str] | None = None,
    teaching_titles: Mapping[str, str] | None = None,
) -> list[Pick]:
    """The method probe: 6–8 concepts, one question each, methods balanced.

    Separate from placement on purpose. Placement asks "what does this learner
    know?" and is adaptive; the probe asks "which way of teaching this concept
    worked better for them?" and must be *balanced* — same answer format for a
    concept across the run, one method per concept, and never the same method twice
    in a row for the same learner if the rotation can avoid it.
    """
    learner = learner_id or seed or "probe"
    by_id: dict[str, list[dict[str, Any]]] = {}
    for topic in topics:
        key = next((k for k in (topic.get("id"), topic.get("code")) if k and items_by_topic.get(k)), None)
        if key is None:
            continue
        by_id[str(topic.get("id") or key)] = [
            {**i, "topic_id": i.get("topic_id") or topic.get("id") or key}
            for i in items_by_topic[key]
            if i.get("active", True)
        ]
    teaching_text = teaching_text or {}
    teaching_titles = teaching_titles or {}
    concept_list = [{"id": str(t.get("id") or t.get("code")), "title": t.get("title")} for t in topics]
    planned = M.balanced_probe(
        concepts=[c for c in concept_list if by_id.get(c["id"])],
        items_by_concept=by_id,
        learner_id=learner,
        count=max(6, min(8, count)),
    )
    out: list[Pick] = []
    for entry in planned:
        item = dict(entry["item"])
        assignment: M.MethodAssignment = entry["assignment"]
        concept_id = str(entry["concept"])
        out.append(
            Pick(
                item=item,
                topic_id=concept_id,
                reason=entry["reason"],
                utility=1.0,
                assignment=assignment.as_dict(),
                teaching=_teaching_for(
                    assignment,
                    topic_id=concept_id,
                    title=str(teaching_titles.get(concept_id) or entry.get("concept_title") or concept_id),
                    source_text=teaching_text.get(concept_id) or "",
                    learner_id=learner,
                ),
                expected_success=None,
                meta={
                    "phase": "method_probe",
                    "format": entry["format"],
                    "method": assignment.method,
                    "design": assignment.design,
                    "arm": assignment.arm,
                },
            )
        )
    # One method per concept, and the run rotates: check the balance here rather than
    # hoping, because an unbalanced probe is a probe that cannot answer its question.
    if out and len({p.meta["method"] for p in out}) < min(3, len(out)):
        log.warning("method probe used fewer than three distinct methods for %d concepts", len(out))
    return out


def diagnostic_plan(
    *,
    topics: Sequence[dict[str, Any]],
    items_by_topic: Mapping[str, Sequence[dict[str, Any]]],
    count: int = 12,
    seed: str | None = None,
) -> list[Pick]:
    """**Legacy** coverage-first plan, kept for callers that want a fixed sequence.

    The app uses :func:`placement_plan` (adaptive) and :func:`method_probe_plan`
    (balanced) instead: this one assigns the delivery mode by cycling the item's
    authored ``experience_mode``, which is the coupling the method rotation exists to
    remove. It is still here because it is the frozen behaviour several tests pin,
    and because a fixed plan is the right thing when a caller genuinely needs the
    same questions every time.
    """
    rng = random.Random(stable_seed(seed or "diagnostic"))
    # Callers key items by topic id (the database identity). Codes are accepted as a
    # fallback because the bank is written in codes and a caller that has not seeded
    # yet should still get a plan rather than an empty list.
    pool: list[tuple[str, dict[str, Any]]] = []
    difficulty_by_key: dict[str, float] = {}
    for topic in topics:
        key = next(
            (k for k in (topic.get("id"), topic.get("code")) if k and items_by_topic.get(k)),
            None,
        )
        if key is None:
            continue
        # Remember the difficulty under the *same* key the pool uses. The old
        # ordering looked this up as (code or id) while the pool is keyed by id,
        # so unless the two strings happened to be equal the lookup matched
        # nothing, every topic scored 0.5, and "easiest topic first" silently
        # became "whatever order the query returned".
        difficulty_by_key[key] = float(topic.get("difficulty", 0.5) or 0.5)
        for item in items_by_topic[key]:
            pool.append((key, item))
    if not pool:
        return []
    # One item per topic (rotating modes) before taking a second from any topic.
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for code, item in pool:
        by_topic.setdefault(code, []).append(item)
    for code, items in by_topic.items():
        items.sort(key=lambda i: (MODE_CYCLE.index(i.get("experience_mode", "read")) if i.get("experience_mode") in MODE_CYCLE else 9, -float(i.get("difficulty", 0.5))))
    picks: list[Pick] = []
    round_no = 0
    used: set[str] = set()
    order = sorted(by_topic, key=lambda key: difficulty_by_key.get(key, 0.5))
    while len(picks) < count and round_no < 6:
        progressed = False
        for code in order:
            if len(picks) >= count:
                break
            candidates = [i for i in by_topic[code] if _key(i) not in used and i.get("active", True)]
            if not candidates:
                continue
            item = candidates[round_no % len(candidates)]
            if round_no > 0 and item.get("kind") == "teachback":
                continue  # one teach-back per learner in the diagnostic is plenty
            used.add(_key(item))
            picks.append(
                Pick(
                    item=item,
                    topic_id=item.get("topic_id") or code,
                    reason=(
                        "diagnostic coverage: one topic sample under a rotated delivery "
                        f"mode ({item.get('experience_mode', 'read')}) so we can compare performance, not preference"
                    ),
                    utility=1.0,
                    feedback_delay_s=0,
                    meta={"round": round_no, "mode": item.get("experience_mode", "read"), "rng": "seeded"},
                )
            )
            progressed = True
        if not progressed:
            break
        round_no += 1
    rng.shuffle(picks) if len(picks) > 2 else None
    return picks


def _key(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("numbas_ref") or item.get("stem", "")[:64])


def presentation_from_directives(directives: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Translate approved adaptations into concrete quiz behaviour."""
    out: dict[str, Any] = {
        "attach_worked_example": False,
        "feedback_delay_s": 0,
        "max_words": None,
        "require_teachback": False,
        "hints_allowed": None,
        "visual_mode": False,
        "interleaving_ratio": 0.0,
        "ask_confidence": False,
        "target_success_rate": 0.78,
        "re_read_allowed": True,
        "voice_mode": False,
        # The method an approved adaptation pins, if any (learning/methods.py reads
        # this). Without it, approving "show the structure visually" moved a boolean
        # that no serving path consulted.
        "method": None,
        "segments": False,
    }
    for d in directives or ():
        strat = d.get("strategy") or d.get("action")
        payload = d.get("payload") or {}
        if strat == "worked_examples":
            out["attach_worked_example"] = True
            out["_example_fade_after"] = int(payload.get("fade_after", 3))
        elif strat == "segmented_text":
            out["max_words"] = int(payload.get("max_words", 90))
        elif strat == "immediate_feedback":
            out["feedback_delay_s"] = int(payload.get("delay_s", 0))
        elif strat == "hint_fading":
            out["hints_allowed"] = int(payload.get("max_hints", 1))
        elif strat == "self_explanation":
            out["require_teachback"] = bool(payload.get("after_explanation", True))
        elif strat == "interleaving":
            out["interleaving_ratio"] = float(payload.get("ratio", 0.4))
        elif strat == "difficulty_recalibration":
            out["target_success_rate"] = float(payload.get("target", 0.78))
        elif strat == "retrieval_practice":
            out["re_read_allowed"] = False
            out["items_per_session"] = int(payload.get("questions_per_session", 4))
        elif strat == "confidence_calibration":
            out["ask_confidence"] = True
        elif strat == "voice_explanation":
            out["voice_mode"] = True
        elif strat == "visual_representation":
            out["visual_mode"] = True
    out["method"] = M.method_for_directives(directives)
    out["segments"] = out["method"] == "explanation" or bool(out.get("max_words"))
    return out


def next_items(
    *,
    items_by_topic: Mapping[str, Sequence[dict[str, Any]]],
    mastery: dict[str, float],
    counts: dict[str, int] | None = None,
    due_topics: Sequence[str] = (),
    gap_topics: Sequence[str] = (),
    gap_severity: dict[str, float] | None = None,
    directives: Sequence[dict[str, Any]] = (),
    used_item_ids: Iterable[str] = (),
    count: int = 5,
    params: BKTParams | None = None,
    seed: str | None = None,
    now: datetime | None = None,
    teaching_text: Mapping[str, str] | None = None,
    teaching_titles: Mapping[str, str] | None = None,
) -> list[Pick]:
    """Score every candidate item and return the best ``count`` in a sensible order.

    Each returned pick carries the **teaching method assigned for this learner at
    this moment** (``learning.methods.assign_method``) plus the payload that method
    puts on the screen, drawn from ``teaching_text`` (topic id -> source material).
    The item's own ``experience_mode`` is not consulted: that is what made every
    concept meetable in exactly one way.
    """
    rng = random.Random(stable_seed(seed or "select"))
    pres = presentation_from_directives(directives)
    teaching_text = teaching_text or {}
    teaching_titles = teaching_titles or {}
    params = params or BKTParams()
    counts = counts or {}
    gap_severity = gap_severity or {}
    used = set(used_item_ids)
    skipped_unmarkable: list[str] = []
    scored: list[Pick] = []
    for code, items in items_by_topic.items():
        p_know = float(mastery.get(code, 0.2))
        gain = information_gain(p_know, params)
        due = code in set(due_topics)
        gap = code in set(gap_topics)
        for item in items:
            if not item.get("active", True) or _key(item) in used:
                continue
            if not gradeable(item):
                # Not offered at all: an unmarkable item takes a slot in the session and
                # teaches the model nothing about the learner.
                skipped_unmarkable.append(_key(item))
                continue
            topic_id = item.get("topic_id") or code  # the id is the identity; the dict key may be a code
            difficulty = float(item.get("difficulty", 0.5))
            expected_success = expected_success_rate(p_know, difficulty, params)
            fit = 1.0 - abs(expected_success - pres["target_success_rate"])
            utility = 2.2 * gain + 0.9 * fit
            meta = {
                "information_gain": round(gain, 4),
                "expected_success": round(expected_success, 3),
                "fit": round(fit, 3),
                # Where the question came from ("bank", "ai2", "numbas"). A generated item
                # joins the same pool as an authored one once it is stored, so without
                # this the only way to tell them apart downstream is the id prefix -- and
                # anything that wants to check its own calibration or audit a model
                # version needs to be able to ask.
                "source": item.get("source", "bank"),
            }
            reason_bits = [f"p_know={p_know:.2f}"]
            if due:
                utility += 1.3
                reason_bits.append("spaced review is due")
                meta["due_bonus"] = 1.3
            if gap:
                utility += 1.1 + 1.4 * float(gap_severity.get(code, 0.5))
                reason_bits.append("prerequisite gap flagged first")
                meta["gap_bonus"] = round(1.1 + 1.4 * float(gap_severity.get(code, 0.5)), 3)
            if counts.get(code, 0) >= 3:
                utility -= 0.7  # stop hammering one topic in a single session
                meta["repetition_penalty"] = -0.7
            kind = item.get("kind")
            if kind == "teachback" and not pres["require_teachback"]:
                utility -= 0.5
            if kind == "worked_example":
                utility -= 0.4  # examples are shown alongside items, not chosen as items
                meta["example_as_item_penalty"] = -0.4
            assignment = M.assign_method(
                learner_id=str(seed or ""),
                item_id=_key(item),
                topic_id=topic_id,
                exposure=counts.get(code, 0),
                directives=directives,
            )
            teaching = _teaching_for(
                assignment,
                topic_id=topic_id,
                title=str(
                    teaching_titles.get(topic_id)
                    or teaching_titles.get(code)
                    or item.get("topic_title")
                    or topic_id
                ),
                source_text=teaching_text.get(topic_id) or teaching_text.get(code) or "",
                learner_id=str(seed or ""),
            )
            scored.append(
                Pick(
                    item=item,
                    topic_id=topic_id,
                    utility=utility + rng.random() * 0.02,  # tie-break only
                    reason="; ".join(reason_bits + [f"expected success {expected_success:.0%} vs target {pres['target_success_rate']:.0%}"]),
                    attach_worked_example=bool(pres["attach_worked_example"]) and p_know < 0.6,
                    feedback_delay_s=int(pres["feedback_delay_s"]),
                    max_words=pres["max_words"],
                    require_teachback=bool(pres["require_teachback"]) and kind != "teachback",
                    hints_allowed=pres["hints_allowed"],
                    assignment=assignment.as_dict(),
                    teaching=teaching,
                    expected_success=expected_success,
                    meta={
                        **meta,
                        "p_know": round(p_know, 3),
                        "method": assignment.method,
                        "method_design": assignment.design,
                        "arm": assignment.arm,
                        "question_format": M.question_format(item),
                    },
                )
            )
    scored.sort(key=lambda p: p.utility, reverse=True)
    if skipped_unmarkable:
        log.info("selector skipped %d item(s) this build cannot mark: %s", len(skipped_unmarkable), skipped_unmarkable[:5])
    if pres["interleaving_ratio"] > 0:
        scored = _interleave(scored, ratio=pres["interleaving_ratio"])
    return scored[:count]


def expected_success_rate(p_know: float, difficulty: float, params: BKTParams) -> float:
    """P(correct on this item | current belief), using the traced model.

    The BKT prediction ``p_correct`` is what the trace actually believes; item
    difficulty makes the *same* prediction harder or easier by shifting it in
    log-odds (a Rasch-style adjustment, so difficulty 0.5 leaves it alone and the
    shift can never produce a probability outside (0, 1)).

    The previous version was `p_know * (1 - 0.45*difficulty) + 0.05`, which
    ignores ``p_guess`` and ``p_slip`` entirely -- it claimed 5% success on an
    item the model knows a non-knower has a 20% chance of guessing -- and it
    disagreed with the number the trace would report for the same learner.
    """
    base = params.p_correct(max(0.001, min(0.999, float(p_know))))
    odds = base / max(1e-9, 1.0 - base)
    shifted = odds * math.exp(-DIFFICULTY_LOG_ODDS * (float(difficulty) - 0.5))
    return max(0.02, min(0.98, shifted / (1.0 + shifted)))


def _interleave(picks: Sequence[Pick], *, ratio: float) -> list[Pick]:
    """Spread topics across the sequence, without ever dropping a pick.

    The repetition budget is a *preference* about ordering, not a filter: when it
    runs out (one topic is the only one with items left, or the pool is small)
    the remaining picks are appended in utility order. The old version ``break``-ed
    out of the loop instead, so approving "interleaving" silently cut a
    session short -- asking for 11 questions and getting 6.
    """
    out: list[Pick] = []
    remaining = list(picks)
    last: str | None = None
    twice = 0
    budget = 2 + int(ratio * 5)
    while remaining:
        pick_idx = 0
        for i, p in enumerate(remaining):
            if p.topic_id != last:
                pick_idx = i
                break
        p = remaining.pop(pick_idx)
        if p.topic_id == last:
            twice += 1
            if twice > budget:
                # Stop choosing; do not stop returning.
                out.append(p)
                out.extend(remaining)
                return out
        out.append(p)
        last = p.topic_id
    return out


def review_queue(
    *,
    schedules: Sequence[dict[str, Any]],
    mastery: dict[str, float],
    items_by_topic: Mapping[str, Sequence[dict[str, Any]]],
    now: datetime,
    count: int = 4,
) -> list[Pick]:
    """Due-first queue for the "spaced review" screen (retrieval practice)."""
    due = [s for s in schedules if s.get("due_at") and datetime.fromisoformat(str(s["due_at"])) <= now]
    due.sort(key=lambda s: (s.get("expected_recall_at_due", 1.0) - float(s.get("overdue_factor", 1.0)), str(s.get("due_at"))))
    picks: list[Pick] = []
    for s in due[:count]:
        code = s["topic_id"]
        candidates = [i for i in items_by_topic.get(code, []) if servable(i)]
        if not candidates:
            continue
        item = candidates[int(s.get("reps", 0)) % len(candidates)]
        picks.append(
            Pick(
                item=item,
                topic_id=code,
                utility=1.0 + max(0.0, 0.85 - float(s.get("expected_recall_at_due", 0.85))),
                reason=(
                    f"review due: model expects {s.get('expected_recall_at_due', 0.85):.0%} recall "
                    f"after about {s.get('half_life_hours', 24):.0f}h"
                ),
                meta={"half_life_hours": s.get("half_life_hours"), "reps": s.get("reps", 0)},
            )
        )
    return picks


def remediation_items(
    *, topic_id: str, items: Sequence[dict[str, Any]], count: int = 3, prefer_easy: bool = True
) -> list[Pick]:
    """Easiest items on the prerequisite topic, for the 'back up and fix it' step."""
    ordered = sorted([i for i in items if servable(i)], key=lambda i: float(i.get("difficulty", 0.5)))
    if not prefer_easy:
        ordered = ordered[::-1]
    return [
        Pick(item=i, topic_id=topic_id, reason="prerequisite remediation step", utility=1.0)
        for i in ordered[:count]
    ]


def _teaching_for(
    assignment: M.MethodAssignment,
    *,
    topic_id: str,
    title: str,
    source_text: str,
    learner_id: str,
) -> dict[str, Any]:
    """The teaching payload for a method, or an honest note when there is no material.

    Retrieval deliberately gets nothing: the attempt *is* the teaching. A concept
    with no source material still gets its method recorded — the comparison needs it
    — but the payload says so rather than showing an empty box.
    """
    if assignment.method == "retrieval":
        return CONTENT.teaching_payload(
            method="retrieval", topic_id=topic_id, title=title, source_text="", learner_id=learner_id
        )
    if not source_text:
        return {
            "method": assignment.method,
            "kind": "no_material",
            "text": "No course material is linked to this concept yet, so nothing is shown before the question.",
        }
    return CONTENT.teaching_payload(
        method=assignment.method,
        topic_id=topic_id,
        title=title,
        source_text=source_text,
        learner_id=learner_id,
    )
