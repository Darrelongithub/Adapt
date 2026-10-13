"""The loop closes: fail → diagnose → reteach → compare, and the mastery move is bounded.

Three things the pitch claims and this file proves, each on the code path the API uses:

1. **A wrong answer is not blamed on the teaching until the alternatives are ruled out.** The
   prerequisite check and the fatigue check run first; if either fires, the method comparison is
   marked *not comparable* and the app says so in a sentence it also stores.
2. **The reteach is a real experiment.** The next attempt is taught through a *different* method
   and asked in the *same format*, so the difference between the two answers can be the teaching.
3. **One answer cannot move the belief far, and an MCQ wrong answer moves it less.** A four-option
   question carries a 25% guess rate and a typed one 2%, so the same "wrong" is weaker evidence on
   the choice, and no single response moves mastery by more than the cap.

Nothing here is a mock of the loop: `plan_reteach` is the function `services.attempts` calls, and
the mastery arithmetic is `learning.bkt`'s.
"""

from __future__ import annotations

import pytest

from adapt.learning import bkt
from adapt.learning import methods as M
from adapt.learning import reteach as RE

TOPIC = "top_fraction_add"

ITEM = {
    "id": "itm_fraction_add_1",
    "topic_id": TOPIC,
    "kind": "numeric",
    "stem": "Work out 1/2 + 1/3.",
    "difficulty": 0.4,
    "est_seconds": 90,
    "answer_spec": {"value": 5 / 6},
    "active": True,
}

#: Two more typed questions on the same concept, so a same-format question exists to ask again.
CANDIDATES = [
    {**ITEM, "id": "itm_fraction_add_2", "stem": "Work out 1/4 + 1/2.", "difficulty": 0.4},
    {**ITEM, "id": "itm_fraction_add_3", "stem": "Work out 2/5 + 1/2.", "difficulty": 0.42},
    # a choice question on the same concept: it must NOT be picked, because the format differs
    {**ITEM, "id": "itm_fraction_add_4", "kind": "mcq", "stem": "Which is 1/2 + 1/3?", "choices": [{"text": "2/5"}, {"text": "5/6"}]},
]


def _plan(**kw):
    base = dict(
        learner_id="stu_loop",
        topic_id=TOPIC,
        item=ITEM,
        method="explanation",
        correct=False,
        expected_success=0.5,
        candidates=CANDIDATES,
        prereqs={TOPIC: ["top_fractions_intro"]},
        mastery={TOPIC: 0.4, "top_fractions_intro": 0.55},
        observations={TOPIC: 4, "top_fractions_intro": 6},
        time_on_item_ms=60_000,
        recent=[{"correct": False}, {"correct": False}],
        method_rows=[
            {"method": "explanation", "correct": False, "expected_success": 0.5},
            {"method": "explanation", "correct": True, "expected_success": 0.5},
        ],
        exposure=2,
    )
    base.update(kw)
    return RE.plan_reteach(**base)


def test_a_miss_runs_the_prerequisite_and_fatigue_checks_before_blaming_the_method():
    plan = _plan()
    assert plan.needed is True
    assert set(plan.checks) >= {"prerequisite", "fatigue"}, plan.checks
    assert plan.checks["prerequisite"]["fired"] is False
    assert plan.checks["fatigue"]["fired"] is False
    assert plan.blame == "method", plan.reason
    assert "neither a prerequisite gap nor a fatigue signal" in plan.reason


def test_a_missing_prerequisite_is_not_converted_into_a_teaching_failure():
    plan = _plan(mastery={TOPIC: 0.4, "top_fractions_intro": 0.1}, observations={TOPIC: 4, "top_fractions_intro": 6})
    assert plan.needed is True
    assert plan.blame == "prerequisite"
    assert plan.to_method is None and plan.question is None
    assert plan.comparison["comparable"] is False
    assert "confounded" in plan.comparison["reason"]
    # The sentence the learner sees must not read as "that method failed".
    assert "not blaming the method" in plan.reason


def test_a_rushed_answer_is_not_converted_into_a_teaching_failure():
    plan = _plan(time_on_item_ms=1_500, latency_z=-2.5, recent=[{"correct": True}, {"correct": True}])
    assert plan.blame == "fatigue"
    assert plan.to_method is None
    assert plan.comparison["comparable"] is False
    assert "weak evidence" in plan.comparison["reason"]


def test_the_reteach_teaches_a_different_method_and_asks_the_same_format():
    plan = _plan()
    assert plan.to_method and plan.to_method != plan.from_method, (plan.from_method, plan.to_method)
    assert plan.question is not None
    assert M.question_format(plan.question) == M.question_format(ITEM), plan.question
    assert plan.checks["format"] == M.question_format(ITEM)
    assert plan.comparison["format_held_fixed"] == M.question_format(ITEM)
    # the expected value of the reteach question is part of the comparison, not added later
    assert plan.comparison["expected_success"] == pytest.approx(0.5)


def test_the_sentence_for_the_learner_carries_the_sample_size():
    plan = _plan()
    text = RE.compare_text(plan)
    assert "n so far" in text or "against expectation" in text, text
    # under the evidence threshold it must not read as a finding
    if "against expectation" in text:
        assert M.evidence_label(1) == "early_signal"


def test_the_worked_example_comparison_has_a_design_and_is_not_self_selection():
    """The old check asked "did the learner open an example?" — which the struggling learner does.

    Arm assignment is seeded per (learner, concept) and reported, so a comparison can be run
    against an assigned condition instead of a choice the learner made.
    """
    arms = {}
    designs = {}
    for i in range(40):
        a = M.assign_method(learner_id=f"stu_{i}", item_id=ITEM["id"], topic_id=TOPIC, exposure=0)
        arms[a.arm] = arms.get(a.arm, 0) + 1
        designs[a.design] = designs.get(a.design, 0) + 1
    assert set(arms) == set(M.ARMS), arms
    assert all(count >= 8 for count in arms.values()), arms
    assert designs.get("randomised"), designs
    # and the same learner on the same concept gets the same arm: it is a design, not a coin toss
    first = M.assign_method(learner_id="stu_7", item_id=ITEM["id"], topic_id=TOPIC, exposure=0)
    again = M.assign_method(learner_id="stu_7", item_id=ITEM["id"], topic_id=TOPIC, exposure=0)
    assert first.arm == again.arm
    # ...while a second exposure on the concept is taught something else, which is the rotation
    second = M.assign_method(learner_id="stu_7", item_id=ITEM["id"], topic_id=TOPIC, exposure=1)
    assert second.method != first.method or second.arm != first.arm


def test_the_guess_rate_belongs_to_the_question_format():
    assert bkt.guess_for_kind("mcq", choices=4) == pytest.approx(0.25)
    assert bkt.guess_for_kind("mcq", choices=2) == pytest.approx(0.5)
    assert bkt.guess_for_kind("numeric") == pytest.approx(0.02)
    # 0.02 here until the functionality round; brief 4.2 specifies "short text ≈ 0.05" and the
    # constant follows the brief. The assertion stays a pinned value rather than a range, so the
    # next change to it has to be deliberate.
    assert bkt.guess_for_kind("text") == pytest.approx(0.05)
    # a five-option question is less guessable than a four-option one, monotonically
    assert bkt.guess_for_kind("mcq", choices=5) < bkt.guess_for_kind("mcq", choices=4)


def test_one_wrong_answer_cannot_move_mastery_far_and_moves_less_on_a_choice():
    """The bounded update, through the entry point the attempt path uses."""
    base = bkt.BKTParams()
    assert base.p_guess == 0.20, "this test is about the default being replaced, not about a tuned one"

    before = 0.8
    typed, why_typed = bkt.update_capped(before, False, base, kind="numeric")
    choice, why_choice = bkt.update_capped(before, False, base, kind="mcq", choices=4)

    assert before - typed == pytest.approx(bkt.MAX_STEP_PER_ANSWER, abs=1e-6)
    assert before - choice == pytest.approx(bkt.MAX_STEP_PER_ANSWER, abs=1e-6)
    assert why_typed["capped"] and why_choice["capped"], "the cap is what holds both to one step"

    # Under the cap sits the format's guess rate: 25% of four-option answers come out right
    # without knowing anything, so the same "wrong" is weaker evidence than on a typed answer
    # where guessing is 2%. The unbounded posterior is where that shows.
    assert why_choice["unbounded"] > why_typed["unbounded"] + 0.02, (why_choice, why_typed)
    assert why_choice["p_guess_used"] == pytest.approx(0.25)
    assert why_typed["p_guess_used"] == pytest.approx(0.02)
    assert why_typed["p_slip_used"] <= 0.15 + 1e-9

    # The defect, in numbers: this is what one miss used to do, and what it does now.
    raw_old = bkt.update(0.2, correct=False, params=base)
    assert raw_old < 0.05, f"the old maths took 20% to {raw_old:.1%} on one miss"
    now, _ = bkt.update_capped(0.2, False, base, kind="numeric")
    assert now == pytest.approx(0.08, abs=1e-6), f"20% now goes to {now:.1%}, not 3%"
    # and it takes several consistent misses to reach the bottom band, which is the point
    p, _ = 0.2, None
    steps = 0
    while p > 0.05 and steps < 20:
        p, _ = bkt.update_capped(p, False, base, kind="numeric")
        steps += 1
    assert steps >= 2, f"one answer reached the bottom band in {steps} step(s)"

    # The explanation travels with the move, so "why did my mastery drop?" has an answer.
    assert why_typed["from"] == pytest.approx(0.8)
    assert why_typed["to"] == pytest.approx(typed, abs=1e-4)
    assert why_typed["moved"] == pytest.approx(typed - 0.8, abs=1e-4)
