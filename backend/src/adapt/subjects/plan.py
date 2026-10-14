"""Choosing the tests for a topic: the top leanings, one cross-check, a time cap, and honest
"not measured" states for everything else (§6.3 step 2).

This is the code half of "the AI proposes, code decides". A rating table arrives (authored, or a
model's, already validated against the taxonomy) and this module turns it into a round a learner can
actually sit: four skills, about three minutes, and a list of what was *not* measured.

Three rules are load-bearing:

* **A skipped skill is never zero.** §6.3 says it plainly and the failure it prevents is real: a
  plan that left untested skills out of the response would be read as "you have no number sense",
  and a plan that scored them 0 would put a false number in the learner model. Every skill the round
  did not reach comes back with ``status="not_measured"`` and the reason it was skipped, and
  :func:`TestPlan.as_dict` puts them in the same list shape as the tested ones so no screen can
  render one without the other.
* **The cross-check is reserved, not ranked last.** §2 requires code to "keep the minimum
  cross-check", so when the time cap bites it is the *leaned* skills that get cut. A round that
  measured the same mechanic four times would look thorough and would not be able to falsify itself.
* **The cross-check never tests a skill the topic does not lean on.** Rating ≥ 2 is the floor. The
  brief's own example is "for government, argument, sources and perspective are tested; number sense
  is not" — testing something the topic does not need spends the learner's minutes and produces a
  number nobody will act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .leanings import LEANING_SCALE, Leanings
from .taxonomy import TAXONOMY_VERSION, Skill, require_skill

#: The round tests the top three leanings, a fourth when it is tied with the third, plus one
#: cross-check. Five is the ceiling: a family only has five to seven skills, and a round that tested
#: all of them would have selected nothing.
MAX_LEANED_SKILLS = 4
BASE_LEANED_SKILLS = 3
#: A leaning this weak is not worth one of the round's minutes.
MIN_RATING_TO_TEST = 2
#: The whole skill round, in seconds. Four 40–45 second games plus reading time; §5 puts the mind
#: baseline at about seven minutes, and the subject round has to fit in the same kind of sitting.
SKILL_ROUND_BUDGET_SECONDS = 240

#: What an untested skill is called. One string, used everywhere, because "skipped", "missing" and
#: "zero" are three different claims and only this one is true.
NOT_MEASURED = "not_measured"


@dataclass(frozen=True)
class PlannedSkill:
    """One skill the round will test, and why it was chosen."""

    skill: Skill
    rating: int
    #: ``leaned`` = one of the strongest leanings. ``cross_check`` = the same topic through a
    #: different mechanic, so a false positive on the others has somewhere to show up.
    role: str
    order: int
    reason: str
    #: How many tasks, and how long the game is allowed to take.
    items: int = 3
    seconds: int = 40

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.skill.as_dict(),
            "rating": int(self.rating),
            "rating_label": _label(self.rating),
            "role": self.role,
            "order": self.order,
            "reason": self.reason,
            "status": "planned",
            "items": self.items,
            "seconds": self.seconds,
        }


@dataclass(frozen=True)
class SkippedSkill:
    """One skill the round did not test. Carries a status and a reason, never a score."""

    skill: Skill
    rating: int
    reason: str
    status: str = NOT_MEASURED
    #: Set when the skill was good enough to test and the time cap cut it: the round owes the learner
    #: that distinction, because "we chose not to" and "we ran out of minutes" are different.
    time_capped: bool = False

    @property
    def score(self) -> None:
        """Deliberately unrepresentable. A skipped skill has no score, and a property that raises on
        access is harder to misuse than one that returns ``0``."""
        raise AttributeError(f"{self.skill.id} was not measured; it has no score (status={self.status})")

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.skill.as_dict(),
            "rating": int(self.rating),
            "rating_label": _label(self.rating),
            "role": "not_selected",
            "status": self.status,
            "reason": self.reason,
            "time_capped": self.time_capped,
            "score": None,
            "n": 0,
        }


@dataclass(frozen=True)
class TestPlan:
    """The whole selection, with the reason the learner is shown (§6.3 step 3)."""

    topic_key: str
    topic_title: str
    family: str
    leanings: Leanings
    tested: tuple[PlannedSkill, ...] = ()
    skipped: tuple[SkippedSkill, ...] = ()
    budget_seconds: int = SKILL_ROUND_BUDGET_SECONDS
    estimated_seconds: int = 0
    reason: str = ""
    cross_check: str | None = None
    time_capped: bool = False
    taxonomy_version: str = TAXONOMY_VERSION
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tested_ids(self) -> tuple[str, ...]:
        return tuple(p.skill.id for p in self.tested)

    @property
    def skipped_ids(self) -> tuple[str, ...]:
        return tuple(s.skill.id for s in self.skipped)

    def skill(self, skill_id: str) -> PlannedSkill | SkippedSkill | None:
        return next((p for p in self.tested if p.skill.id == skill_id), None) or next(
            (s for s in self.skipped if s.skill.id == skill_id), None
        )

    def as_dict(self, *, with_tasks: bool = False, tasks: Mapping[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "topic": {
                "key": self.topic_key,
                "title": self.topic_title,
                "family": self.family,
                "subject": self.leanings.subject,
                "source": self.leanings.source,
            },
            "reason": self.reason,
            "leanings_reason": self.leanings.reason,
            "leanings_source": self.leanings.source,
            "taxonomy_version": self.taxonomy_version,
            "scale": [dict(r) for r in LEANING_SCALE],
            "budget_seconds": self.budget_seconds,
            "estimated_seconds": self.estimated_seconds,
            "tested": [p.as_dict() for p in self.tested],
            "not_measured": [s.as_dict() for s in self.skipped],
            "counts": {"tested": len(self.tested), "not_measured": len(self.skipped)},
            "cross_check": self.cross_check,
            "time_capped": self.time_capped,
            "notes": list(self.notes),
            "model_note": self.leanings.model_note,
        }
        if with_tasks and tasks:
            out["tasks"] = {skill_id: list(rows) for skill_id, rows in tasks.items()}
        return out


def _label(rating: int) -> str:
    return next((row["label"] for row in LEANING_SCALE if row["value"] == int(rating)), "")


def rank(leanings: Leanings) -> list[tuple[Skill, int]]:
    """Strongest leaning first; ties broken the same way every time, so two runs cannot differ."""

    def key(pair: tuple[str, int]) -> tuple[int, int, int]:
        skill_id, rating = pair
        same_family = 0 if require_skill(skill_id).family == leanings.family else 1
        return (-int(rating), same_family, _index(skill_id, leanings.family))

    # ``same_family`` before the taxonomy index keeps a family's own skills ahead of a cross-family
    # one at the same rating: a topic's own subject skills are the better measurement, and the
    # cross-family one is the better cross-check. The index last makes the order total, so a tie
    # cannot be broken by dictionary order or by a Python version.
    return [(require_skill(sid), int(value)) for sid, value in sorted(leanings.ratings.items(), key=key)]


def _index(skill_id: str, family: str) -> int:
    from .leanings import _skill_rank

    return _skill_rank(skill_id, family)


def select_cross_check(
    ranked: Sequence[tuple[Skill, int]],
    chosen: Sequence[Skill],
) -> Skill | None:
    """The strongest untested leaning that measures the topic a *different* way.

    "Different" means a different game kind: three choice games can all be beaten by the same habit
    (eliminate the absurd options), and a round that only used one mechanic cannot tell a skill from
    a test-taking trick. A kind not yet used wins; if every remaining leaning reuses a kind, the
    strongest of them is taken anyway, because a cross-check that measures the same way is still a
    second measurement of a different skill.
    """
    used_kinds = {s.kind for s in chosen}
    candidates = [(sk, rating) for sk, rating in ranked if sk not in chosen and rating >= MIN_RATING_TO_TEST]
    if not candidates:
        return None
    for sk, _rating in candidates:
        if sk.kind not in used_kinds:
            return sk
    return candidates[0][0]


def plan_tests(
    leanings: Leanings,
    *,
    budget_seconds: int = SKILL_ROUND_BUDGET_SECONDS,
    max_leaned: int = MAX_LEANED_SKILLS,
    base_leaned: int = BASE_LEANED_SKILLS,
) -> TestPlan:
    """The round: which skills are tested, which are not, and the sentence that says why."""
    ranked = rank(leanings)
    notes: list[str] = []

    leaned: list[tuple[Skill, int]] = ranked[:base_leaned]
    if len(ranked) > base_leaned:
        fourth_skill, fourth_rating = ranked[base_leaned]
        third_rating = leaned[-1][1] if leaned else 0
        # A fourth leaning is included only when it is *as strong* as the third and worth testing:
        # "top 3–4" in §6.3 is a range with a reason, and the reason is a tie at the top.
        if fourth_rating == third_rating and fourth_rating >= MIN_RATING_TO_TEST and len(leaned) < max_leaned:
            leaned.append((fourth_skill, fourth_rating))

    chosen_skills = [sk for sk, _ in leaned]
    cross = select_cross_check(ranked, chosen_skills)
    if cross is None and ranked:
        notes.append(
            "no cross-check was added: every skill this topic leans on at 2 or more is already in the round"
        )

    selected: list[tuple[Skill, int, str]] = [(sk, rating, "leaned") for sk, rating in leaned]
    if cross is not None:
        cross_rating = next((rating for sk, rating in ranked if sk == cross), 0)
        selected.append((cross, cross_rating, "cross_check"))

    # The time cap cuts leaned skills and never the cross-check (§2: code keeps the minimum
    # cross-check), so the cross-check's cost is reserved first and the leaned skills are
    # fitted into what remains. Keeping the cross-check by exceeding the cap would satisfy
    # one rule by breaking the other; a cap the plan itself ignores is not a cap.
    capped: list[tuple[Skill, int, str]] = []
    total = 0
    budget = int(budget_seconds)
    cross_entry = next((entry for entry in selected if entry[2] == "cross_check"), None)
    leaned_budget = budget - (int(cross_entry[0].seconds) if cross_entry else 0)
    for sk, rating, role in selected:
        if role == "cross_check":
            continue
        cost = int(sk.seconds)
        if total + cost > leaned_budget:
            capped.append((sk, rating, "time_capped"))
            continue
        capped.append((sk, rating, role))
        total += cost
    if cross_entry is not None:
        sk, rating, _role = cross_entry
        cost = int(sk.seconds)
        if total + cost <= budget:
            capped.append((sk, rating, "cross_check"))
            total += cost
        else:  # only reachable when the budget is smaller than one game
            notes.append(f"the cross-check ({sk.title}) does not fit a {budget}s budget at all")
            capped.append((sk, rating, "time_capped"))

    tested: list[PlannedSkill] = []
    skipped: list[SkippedSkill] = []
    order = 0
    for sk, rating, role in capped:
        if role == "time_capped":
            skipped.append(
                SkippedSkill(
                    skill=sk,
                    rating=rating,
                    reason=(
                        f"rated {rating} of 3 for this topic, but the round has a {budget // 60}-minute "
                        "limit and this game did not fit — not measured, not zero"
                    ),
                    time_capped=True,
                )
            )
            continue
        order += 1
        tested.append(
            PlannedSkill(
                skill=sk,
                rating=rating,
                role=role,
                order=order,
                reason=(
                    f"{leanings.title} leans on this {_label(rating)} ({rating} of 3)"
                    if role == "leaned"
                    else f"the cross-check: the same topic through a different kind of task ({sk.game.get('kind')}), so a lucky run on the others has somewhere to show up"
                ),
                items=int(sk.game.get("items") or 3),
                seconds=int(sk.seconds),
            )
        )

    chosen_ids = {p.skill.id for p in tested}
    for sk, rating in ranked:
        if sk.id in chosen_ids:
            continue
        if any(s.skill.id == sk.id for s in skipped):
            continue
        skipped.append(
            SkippedSkill(
                skill=sk,
                rating=rating,
                reason=_skip_reason(sk, rating, len(tested)),
            )
        )

    tested.sort(key=lambda p: p.order)
    reason = _plan_reason(leanings, tested, skipped)
    return TestPlan(
        topic_key=leanings.topic_key,
        topic_title=leanings.title,
        family=leanings.family,
        leanings=leanings,
        tested=tuple(tested),
        skipped=tuple(skipped),
        budget_seconds=budget,
        estimated_seconds=total,
        reason=reason,
        cross_check=(next((p.skill.id for p in tested if p.role == "cross_check"), None)),
        time_capped=any(s.time_capped for s in skipped),
        notes=tuple(notes),
    )


def _skip_reason(sk: Skill, rating: int, tested_count: int) -> str:
    if rating <= 0:
        return f"{sk.title}: this topic does not really use it, so it was not tested — not measured, not zero"
    if rating < MIN_RATING_TO_TEST:
        return (
            f"{sk.title}: this topic uses it only sometimes (1 of 3), and the round spends its "
            f"{tested_count} games on what the topic leans on more — not measured, not zero"
        )
    return (
        f"{sk.title}: rated {rating} of 3, but the round tests the strongest leanings plus one "
        "cross-check — not measured, not zero"
    )


def _plan_reason(leanings: Leanings, tested: Sequence[PlannedSkill], skipped: Sequence[SkippedSkill]) -> str:
    """§6.3 step 3, in one sentence the learner can read: what we check, and why these."""
    titles = [p.skill.title.lower() for p in tested if p.role == "leaned"]
    cross = next((p.skill.title.lower() for p in tested if p.role == "cross_check"), None)
    if not titles:
        return "We could not work out what this topic leans on, so the round checks the skills every subject uses."
    joined = ", ".join(titles[:-1]) + f" and {titles[-1]}" if len(titles) > 1 else titles[0]
    count_word = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}.get(len(tested), str(len(tested)))
    why = f"We'll check {count_word} things, because {leanings.title} leans hardest on {joined}"
    if cross:
        why += f", plus {cross} as a cross-check"
    if skipped:
        others = (
            "The other skill in this subject is"
            if len(skipped) == 1
            else f"The other {len(skipped)} skills in this subject are"
        )
        why += (
            f". {others} shown as not measured — the round did not test "
            f"{'it' if len(skipped) == 1 else 'them'}, which is not the same as finding nothing"
        )
    if leanings.fallback and leanings.profile == "universal":
        why = (
            f"We have no written-down ratings for {leanings.title} and could not place it in a subject, "
            "so we'll check the six skills every subject uses and label them as a fallback rather than "
            "a finding about your topic"
        )
    elif leanings.fallback:
        why = (
            f"We have no written-down ratings for {leanings.title}, so nothing is ranked: we'll check "
            f"{count_word} of this subject's own skills and label the choice as a fallback rather than "
            "a finding about your topic"
        )
    return why + "."


__all__ = [
    "BASE_LEANED_SKILLS",
    "MAX_LEANED_SKILLS",
    "MIN_RATING_TO_TEST",
    "NOT_MEASURED",
    "SKILL_ROUND_BUDGET_SECONDS",
    "PlannedSkill",
    "SkippedSkill",
    "TestPlan",
    "plan_tests",
    "rank",
    "select_cross_check",
]
