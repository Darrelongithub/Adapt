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

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from ..learning.bkt import BKTParams, information_gain

MODE_CYCLE = ("read", "visual", "example", "practice", "teachback")


def stable_seed(*parts: str) -> int:
    """An int derived from strings, identical in every process.

    ``hash()`` on a ``str`` is salted per interpreter (PYTHONHASHSEED), so
    ``random.Random(hash(seed))`` produced a *different* plan on every restart
    even for the same learner: the diagnostic claimed to be "randomised per
    learner, reproducible for the same learner" and was not reproducible at all.
    BLAKE2b is stable across processes, platforms and versions, which is the
    property this needs; it is not used for anything secret.
    """
    digest = hashlib.blake2b("\x1f".join(parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")

#: How far one point of item difficulty moves the log-odds of a correct answer.
#: 1.0 means an item authored at difficulty 1.0 is ``e`` times harder (in odds)
#: than the same learner's prediction at difficulty 0.0. Kept gentle on purpose:
#: ``difficulty`` is an author's prior, not a calibrated parameter (see
#: docs/UNVERIFIED.md), so it may nudge the target -- it must not dominate it.
DIFFICULTY_LOG_ODDS = 1.0

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
        return {
            "item": item,
            "topic_id": self.topic_id,
            "reason": self.reason,
            "utility": round(self.utility, 4),
            "presentation": {
                "attach_worked_example": self.attach_worked_example,
                "feedback_delay_s": self.feedback_delay_s,
                "max_words": self.max_words,
                "require_teachback": self.require_teachback,
                "hints_allowed": self.hints_allowed,
                "experience_mode": self.item.get("experience_mode", "read"),
            },
            "decision_trace": self.meta,
        }


def diagnostic_plan(
    *,
    topics: Sequence[dict[str, Any]],
    items_by_topic: dict[str, Sequence[dict[str, Any]]],
    count: int = 12,
    seed: str | None = None,
) -> list[Pick]:
    """Cover breadth first, then vary the delivery mode deterministically per learner."""
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
        "interleaving_ratio": 0.0,
        "ask_confidence": False,
        "target_success_rate": 0.78,
        "re_read_allowed": True,
        "voice_mode": False,
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
    return out


def next_items(
    *,
    items_by_topic: dict[str, Sequence[dict[str, Any]]],
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
) -> list[Pick]:
    """Score every candidate item and return the best ``count`` in a sensible order."""
    rng = random.Random(stable_seed(seed or "select"))
    pres = presentation_from_directives(directives)
    params = params or BKTParams()
    counts = counts or {}
    gap_severity = gap_severity or {}
    used = set(used_item_ids)
    scored: list[Pick] = []
    for code, items in items_by_topic.items():
        p_know = float(mastery.get(code, 0.2))
        gain = information_gain(p_know, params)
        due = code in set(due_topics)
        gap = code in set(gap_topics)
        for item in items:
            if not item.get("active", True) or _key(item) in used:
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
                    meta={**meta, "p_know": round(p_know, 3)},
                )
            )
    scored.sort(key=lambda p: p.utility, reverse=True)
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
    items_by_topic: dict[str, Sequence[dict[str, Any]]],
    now: datetime,
    count: int = 4,
) -> list[Pick]:
    """Due-first queue for the "spaced review" screen (retrieval practice)."""
    due = [s for s in schedules if s.get("due_at") and datetime.fromisoformat(str(s["due_at"])) <= now]
    due.sort(key=lambda s: (s.get("expected_recall_at_due", 1.0) - float(s.get("overdue_factor", 1.0)), str(s.get("due_at"))))
    picks: list[Pick] = []
    for s in due[:count]:
        code = s["topic_id"]
        candidates = [i for i in items_by_topic.get(code, []) if i.get("kind") in ("numeric", "mcq", "text")]
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
                    f"after a {s.get('half_life_hours', 24):.0f}h half-life"
                ),
                meta={"half_life_hours": s.get("half_life_hours"), "reps": s.get("reps", 0)},
            )
        )
    return picks


def remediation_items(
    *, topic_id: str, items: Sequence[dict[str, Any]], count: int = 3, prefer_easy: bool = True
) -> list[Pick]:
    """Easiest items on the prerequisite topic, for the 'back up and fix it' step."""
    ordered = sorted(items, key=lambda i: float(i.get("difficulty", 0.5)))
    if not prefer_easy:
        ordered = ordered[::-1]
    return [
        Pick(item=i, topic_id=topic_id, reason="prerequisite remediation step", utility=1.0)
        for i in ordered[:count]
    ]
