"""The answers a test builds have to be the answers they claim to be.

``conftest.answer_for`` is how this whole suite answers questions: the public item
payload deliberately carries no answer key, so a test that wanted to be right would
otherwise have to guess. That makes the helper load-bearing — and a helper that builds
a *wrong* answer the grader marks **right** does not weaken an assertion, it inverts it.

It did exactly that. A wrong numeric answer was ``value * 1.5 + 1``, which returns
``value`` itself when ``value == -2`` — and ``a - b - c`` renders to -2 for some
learners. So ``test_feedback_is_written_and_never_empty_even_without_a_model_key``,
whose whole point is "different answers, different sentence", was handed the same
string twice and failed for roughly one learner id in fourteen, while the app itself
was marking that answer correctly. The fault was in the harness, and the harness is
what every other test trusts: a suite that fails at random teaches you to re-run it
instead of reading it.

The teach-back branch had the same disease pointing the other way. It looked for
``rubric["ideas"]`` — a key no item in the bank has — and fell back to one
fractions-specific sentence for every subject, so the "correct" explanation of why the
First World War began was the words "you have to make the parts the same size first".
It now reads the rubric through the grader's own ``checklist_from_rubric`` and writes
one sentence per point, which is the form the checklist actually scores.

Exhaustive over the bank and over variant seeds for the reason
``test_item_variant_rendering.py`` is exhaustive: which item a plan picks, and which
numbers a parametric item renders, both depend on the learner, so a single-item test
passes or fails by luck.
"""

from __future__ import annotations

#: Enough learner ids to walk a parametric item through a spread of its own variants.
SEEDS = tuple(f"stu_answer_guard_{i:02d}" for i in range(10))


def _rendered(services, item_id: str, student_id: str) -> dict:
    """The payload the grader re-derives for this (learner, item) pair."""
    from adapt.assessment.bank import render_item_for, variant_seed
    from adapt.services import attempts as A

    with services.scope() as s:
        payload = A.item_payload(s, item_id)
    assert payload is not None, f"{item_id} is not in the bank"
    return render_item_for(payload, seed=variant_seed(student_id, item_id))


def _all_active_item_ids(services) -> list[str]:
    from sqlalchemy import select

    from adapt.db.models import Item

    with services.scope() as s:
        return [r.id for r in s.execute(select(Item).where(Item.active.is_(True))).scalars().all()]


def test_every_answer_the_suite_builds_is_marked_the_way_it_is_labelled(client, services):
    """Right answers grade right and wrong answers grade wrong, for every markable item.

    Both halves matter. A wrong answer that passes is a test that asserts nothing; a
    right answer that fails is a test that can only ever pass by not running.
    """
    from conftest import answer_for

    from adapt.assessment import grading, selector

    ids = _all_active_item_ids(services)
    assert len(ids) >= 40, "the fixture bank is too small to prove anything here"

    graded = 0
    no_local_grader: list[str] = []
    for item_id in ids:
        for student_id in SEEDS:
            payload = _rendered(services, item_id, student_id)
            if not selector.servable(payload):
                continue  # a worked example has no answer to give, so nothing to build
            right = answer_for(services, item_id, correct=True, student_id=student_id)
            wrong = answer_for(services, item_id, correct=False, student_id=student_id)
            where = f"{item_id} as {student_id}"
            assert str(right) != str(wrong), (
                f"{where}: the 'wrong' answer is the right answer ({right!r})"
            )

            verdict_right = grading.grade(payload, right, student_text=str(right))
            if verdict_right.method == "none":
                # Nothing in this build can mark it (a hosted-Numbas part type). Such an
                # item never reaches a learner -- `selector.gradeable` refuses it -- so
                # there is no answer for the helper to get wrong. Named, not silently
                # skipped: if this list grows, the bank has outrun the grader.
                no_local_grader.append(item_id)
                continue
            verdict_wrong = grading.grade(payload, wrong, student_text=str(wrong))
            assert verdict_right.correct, (
                f"{where}: a 'correct' answer was marked wrong -- {verdict_right.detail}"
            )
            assert not verdict_wrong.correct, (
                f"{where}: a 'wrong' answer was marked right -- {verdict_wrong.detail}"
            )
            graded += 1

    assert graded >= 400, f"only {graded} (item, learner) pairs were actually graded"
    unmarked = sorted(set(no_local_grader))
    assert len(unmarked) <= 6, f"too many items this build cannot mark: {unmarked}"


def test_the_variant_that_used_to_collide_still_gets_two_different_answers(client, services):
    """Pinned on the arithmetic that broke, not on a seed drawn at random.

    ``a - b - c`` reaches -2 inside its own ranges, and -2 was the one value where
    ``value * 1.5 + 1`` gave the value back. The variant is searched for rather than
    hoped for: ``variant_seed`` is a stable hash, so this test looks at the same
    variants on every run and every machine.
    """
    from conftest import answer_for

    from adapt.ai.local_tutor import eval_formula
    from adapt.assessment import grading

    item_id = "itm_integer_ops_00"
    found: tuple[str, float] | None = None
    for i in range(400):
        student_id = f"stu_collision_search_{i:03d}"
        payload = _rendered(services, item_id, student_id)
        spec = payload.get("answer_spec") or {}
        try:
            value = float(eval_formula(str(spec["formula"]), dict(spec.get("variables") or {})))
        except Exception:  # pragma: no cover - a broken variant, not a broken test
            continue
        if value == -2:
            found = (student_id, value)
            break
    assert found, "the bank's own ranges no longer produce the value that broke this"

    student_id, value = found
    payload = _rendered(services, item_id, student_id)
    right = answer_for(services, item_id, correct=True, student_id=student_id)
    wrong = answer_for(services, item_id, correct=False, student_id=student_id)

    assert value == -2
    assert float(right) == -2 and float(wrong) != -2, f"right={right!r} wrong={wrong!r}"
    assert grading.grade(payload, right).correct, "the answer that is -2 has to be marked right"
    assert not grading.grade(payload, wrong).correct, "the one built to be wrong stays wrong"
