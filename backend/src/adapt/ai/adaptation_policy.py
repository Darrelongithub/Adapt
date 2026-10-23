"""AI 3's adaptation policy: the rule table as code, not model whim.

Given the latest *validated* check-in estimate (Track M), this module
decides how the next lesson may change shape:

* high cognitive load **or** high frustration -> shorter, smaller steps,
  encouraging tone, one notch less challenge;
* high engagement **and** high motivation **and** low load -> one notch
  more challenge, and the lesson may advance;
* a ``break`` recommendation -> a pause is *suggested*, never forced —
  the (shorter) lesson is still delivered;
* otherwise unchanged.

Difficulty also answers to the zone-of-proximal-development /
desirable-difficulty rule (``docs/FRAMEWORK.md``): aim for roughly
70–85% expected success. Below the band the policy eases off; above it,
and only when the sitting does not feel heavy, it stretches.

The model only ever phrases content *inside* these constraints.
:func:`verify_adapted` checks a finished plan against them (length cap,
step cap, difficulty, objectives preserved); a plan that violates them is
replaced by the unadapted deterministic lesson by the caller. Per-lesson
shaping inside a session needs no proposal; anything that would persist
still goes through the existing proposal/consent flow — this module
writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Word budget for a "short" lesson (explanation text + checks together).
SHORT_MAX_TOTAL_WORDS = 500
#: Explanation chunks a short lesson keeps (each still followed by its check).
SHORT_MAX_CHUNKS = 2
#: Step caps mirror adapter.STEP_WORD_LIMITS; kept as words for verification.
STEP_WORD_CAPS = {"small": 30, "medium": 50, "large": 75}
#: The desirable-difficulty band for expected success.
SUCCESS_BAND = (0.70, 0.85)


@dataclass(frozen=True)
class Policy:
    length: str = "unchanged"  # short | unchanged
    step_size: str = "unchanged"  # small | medium | large | unchanged
    tone: str = "unchanged"  # encouraging | stretching | unchanged
    challenge_delta: int = 0
    difficulty: int = 3  # 1..5, after the delta and the clamp
    break_suggested: bool = False
    next_concept: bool = False
    max_total_words: int | None = None
    max_explanation_chunks: int | None = None
    reason: str = ""
    changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "length": self.length,
            "step_size": self.step_size,
            "tone": self.tone,
            "challenge_delta": self.challenge_delta,
            "difficulty": self.difficulty,
            "break_suggested": self.break_suggested,
            "next_concept": self.next_concept,
            "changed": self.changed,
            "reason": self.reason,
        }


def _clamp_difficulty(value: int) -> int:
    return max(1, min(5, int(value)))


def adaptation_policy(
    state: Mapping[str, Any] | None,
    *,
    current_difficulty: int = 3,
    expected_success: float | None = None,
) -> Policy:
    """One validated estimate in, one shaping decision out. Pure function."""
    base = _clamp_difficulty(current_difficulty)
    if not state:
        return Policy(difficulty=base)
    load = str(state.get("cognitive_load") or "medium")
    frustration = str(state.get("frustration") or "low")
    engagement = str(state.get("engagement") or "medium")
    motivation = str(state.get("motivation") or "medium")
    action = str(state.get("recommended_action") or "")
    heavy = load == "high" or frustration == "high"

    delta = 0
    if expected_success is not None:
        if expected_success < SUCCESS_BAND[0]:
            delta = -1  # below the band: ease toward 70–85% success
        elif expected_success > SUCCESS_BAND[1] and not heavy:
            delta = 1

    if action == "break" or (load == "high" and frustration == "high"):
        return Policy(
            length="short",
            step_size="small",
            tone="encouraging",
            challenge_delta=-1,
            difficulty=_clamp_difficulty(base - 1),
            break_suggested=True,
            max_total_words=SHORT_MAX_TOTAL_WORDS,
            max_explanation_chunks=SHORT_MAX_CHUNKS,
            reason="Made shorter, and a pause might help — you said this felt very heavy.",
            changed=True,
        )
    if heavy:
        return Policy(
            length="short",
            step_size="small",
            tone="encouraging",
            challenge_delta=-1,
            difficulty=_clamp_difficulty(base - 1),
            max_total_words=SHORT_MAX_TOTAL_WORDS,
            max_explanation_chunks=SHORT_MAX_CHUNKS,
            reason="Made shorter because you said it felt heavy.",
            changed=True,
        )
    if engagement == "high" and motivation == "high" and load == "low":
        return Policy(
            tone="stretching",
            challenge_delta=1,
            difficulty=_clamp_difficulty(base + 1),
            next_concept=True,
            reason="Pitched a little harder because that felt light and you wanted more.",
            changed=True,
        )
    if delta != 0:
        return Policy(
            challenge_delta=delta,
            difficulty=_clamp_difficulty(base + delta),
            reason=(
                "Eased a little so most answers land — practice works best when about three in four come right."
                if delta < 0
                else "Pitched a little harder — nearly everything was landing, and practice works best when about three in four come right."
            ),
            changed=True,
        )
    return Policy(difficulty=base)


def plan_words(plan: Mapping[str, Any]) -> int:
    """Visible words in a plan's blocks (text, questions, steps)."""
    total = 0
    for block in plan.get("blocks") or []:
        content = block.get("content") or {}
        for key in ("text", "question", "intro", "note"):
            value = content.get(key)
            if isinstance(value, str):
                total += len([w for w in re.split(r"\s+", value) if w])
        for step in content.get("steps") or []:
            if isinstance(step, Mapping):
                for key in ("text", "why"):
                    value = step.get(key)
                    if isinstance(value, str):
                        total += len([w for w in re.split(r"\s+", value) if w])
            elif isinstance(step, str):
                total += len([w for w in re.split(r"\s+", step) if w])
    return total


def plan_objective_ids(plan: Mapping[str, Any]) -> set[str]:
    """Every objective id a plan claims, at plan level and inside blocks."""
    ids: set[str] = set()
    for value in plan.get("objective_ids") or []:
        ids.add(str(value))
    for block in plan.get("blocks") or []:
        for value in (block.get("content") or {}).get("objective_ids") or block.get("objective_ids") or []:
            ids.add(str(value))
    return ids


def verify_adapted(
    plan: Mapping[str, Any],
    policy: Policy,
    *,
    skeleton_objective_ids: Sequence[str] | None = None,
) -> list[str]:
    """Violations of the policy/skeleton in a finished plan. Empty = kept.

    The checks are the ones the brief names: length cap, difficulty clamp,
    objectives preserved (a lesson may only carry objective ids its
    skeleton lists — an empty skeleton list means the lesson must carry
    none).
    """
    violations: list[str] = []
    if policy.max_total_words is not None:
        words = plan_words(plan)
        if words > policy.max_total_words:
            violations.append(f"lesson runs to {words} words; the short cap is {policy.max_total_words}")
    if policy.step_size in STEP_WORD_CAPS and policy.changed:
        cap = STEP_WORD_CAPS[policy.step_size]
        for block in plan.get("blocks") or []:
            if block.get("kind") != "text":
                continue
            body = str((block.get("content") or {}).get("text") or "")
            words = len([w for w in re.split(r"\s+", body) if w])
            oversize_flagged = bool((block.get("content") or {}).get("oversize_sentence"))
            if words > cap and not oversize_flagged:
                violations.append(f"an explanation step runs to {words} words; the {policy.step_size} step cap is {cap}")
                break
    rung = plan.get("ladder_rung")
    if policy.changed and isinstance(rung, int) and rung != policy.difficulty:
        violations.append(f"lesson difficulty is {rung}; the policy set {policy.difficulty}")
    if skeleton_objective_ids is not None:
        outside = plan_objective_ids(plan) - {str(v) for v in skeleton_objective_ids}
        if outside:
            violations.append(f"lesson carries objective(s) outside its skeleton: {', '.join(sorted(outside))}")
    return violations
