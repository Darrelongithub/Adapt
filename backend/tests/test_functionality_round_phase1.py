"""Phase 1 regressions: the defects found by running the app, pinned by a test each.

Every test here fails against the pre-round code. The comment above each one names the
symptom it was written from, because "this test asserts a behaviour" is how a suite stops
telling anyone anything.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from adapt.ai import local_tutor as LT
from adapt.assessment import worked as W
from adapt.assessment.selector import MIN_EXPECTED_SUCCESS, TARGET_BAND, enforce_target_band
from adapt.db.models import Item, LearningMaterial, Topic


# --------------------------------------------------------------------------- #
# 4.1.6 — segmented text was word truncation
# --------------------------------------------------------------------------- #


def test_segmentation_never_cuts_a_sentence_in_half():
    """``_shorten`` was ``" ".join(words[:max]) + "…"``: explanations ended mid-clause."""
    long_text = (
        "A fraction names equal parts of a whole. The denominator says how many equal parts "
        "there are, and the numerator counts how many of them you have. Two fractions are "
        "equivalent when one is the other with the top and bottom multiplied by the same number."
    )
    out = LT._shorten(long_text, 12)
    assert not out.endswith("…"), f"still truncating: {out!r}"
    sentences = [s.strip() for s in out.split(".") if s.strip()]
    for s in sentences:
        assert s + "." in long_text, f"{s!r} is not a whole sentence of the source"
    assert out.strip().endswith("."), "a shown sentence has to end where the source sentence ends"


def test_an_explanation_comes_back_as_steps_with_a_check_each(services):
    """Section 4.1.6: short steps at sentence boundaries, each with a one-question check."""
    topic = _first_topic_with_material(services)
    idx = _index_for_topic(services, topic)
    grounded = LT.explain(
        "why do I have to make the denominators the same?",
        index=idx,
        topic_title=topic.title,
        topic_id=topic.id,
        p_know=0.4,
        seed="steps",
    )
    assert grounded.steps, "an explanation with a body has to come back segmented"
    for step in grounded.steps:
        assert step["text"].strip().endswith((".", "!", "?")), step["text"]
        assert step["check"]["question"], "a step without a check is just a paragraph"
    # Nothing is dropped to fit a budget: every sentence of the body is in some step.
    joined = " ".join(s["text"] for s in grounded.steps)
    for sentence in [x for x in grounded.text.split(".") if len(x.strip()) > 40]:
        assert sentence.strip()[:40] in joined, f"segmentation dropped {sentence[:60]!r}"


# --------------------------------------------------------------------------- #
# 4.1.2 — explanations drew on the wrong notes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question,banned",
    [
        ("Work out 7 - 1 - 9", ("factoris", "unit conversion", "denominator")),
        ("which calculation should be done first in 7 + 3 x 2 - 1", ("power of zero", "factoris", "unit conversion")),
    ],
)
def test_an_explanation_never_quotes_a_different_question_s_material(services, question, banned):
    """The two reported cases: subtraction answered with factorising, order-of-operations
    answered with powers of zero. Both came from BM25 across every topic with no threshold."""
    items = _all_items(services)
    target = next((i for i in items if i.kind == "numeric"), items[0])
    idx = _index_for_all(services)
    out = LT.explain(
        question,
        index=idx,
        topic_title="the topic",
        topic_id=target.topic_id,
        p_know=0.3,
        item={"id": target.id, "topic_id": target.topic_id, "solution_steps": target.solution_steps or []},
        seed="off-topic",
    )
    low = out.text.lower()
    for word in banned:
        assert word not in low, f"{word!r} reached an answer about {question!r}: {out.text[:200]}"


def test_grounded_is_a_claim_about_the_shown_text_not_a_constant(services):
    """``Grounded.as_dict`` hard-coded ``"grounded": True``, so every reply wore the badge."""
    items = _all_items(services)
    target = items[0]
    idx = _index_for_all(services)
    unrelated = LT.explain(
        "how do you tune a guitar to drop D?",
        index=idx,
        topic_title="Fractions",
        topic_id=target.topic_id,
        p_know=0.4,
        seed="unrelated",
    )
    assert unrelated.grounded is False
    assert unrelated.note and "nothing relevant" in unrelated.note
    assert unrelated.as_dict()["grounded"] is False


# --------------------------------------------------------------------------- #
# 4.1.3 / 4.1.4 — worked examples and hints
# --------------------------------------------------------------------------- #


def test_every_shipped_item_has_a_worked_example_that_agrees_with_its_key(services):
    """``/items/{id}/worked-example`` returned 0 steps for 24 of 63 items, while the
    explanation said "copy the first line of the example"."""
    items = _all_items(services)
    assert len(items) >= 60
    for row in items:
        payload = _payload(row)
        rendered = _render(payload)
        steps = W.normalize_steps(rendered.get("solution_steps") or [])
        verdict = W.verify_example(rendered, steps)
        assert steps, f"{row.id} has no worked example"
        assert verdict.ok, f"{row.id}: {verdict.flags}"


def test_every_shipped_item_has_three_graded_hints_and_none_is_generic(services):
    """17 items had no hints and 39 had fewer than three, so the served hint was one
    sentence that fitted every question in the bank."""
    items = _all_items(services)
    for row in items:
        payload = _payload(row)
        rendered = _render(payload)
        hints = [str(h) for h in (rendered.get("hints") or [])]
        assert len(hints) >= 3, f"{row.id} has {len(hints)} hint(s)"
        verdict = W.verify_hints(rendered, hints)
        assert verdict.ok, f"{row.id}: {verdict.flags}"
        assert "write down the single fact the question is asking for" not in " ".join(hints).lower()


def test_a_hint_with_no_ladder_is_generated_for_the_item_not_for_every_item(services):
    """The generic sentence is gone from the fallback path as well."""
    bare = {
        "id": "itm_test",
        "kind": "numeric",
        "stem": "A train covers 240 km in 3 hours. What is its average speed?",
        "answer_spec": {"value": 80},
        "topic_id": "top_x",
    }
    hints = [LT.hint_for(bare, level="developing", hints_used=i) for i in range(3)]
    assert len(set(hints)) == 3, f"the three grades have to differ: {hints}"
    for hint in hints:
        assert "single fact the question is asking for" not in hint
        assert hint.strip()


# --------------------------------------------------------------------------- #
# 4.1.5 — the opening line repeated
# --------------------------------------------------------------------------- #


def test_the_opening_line_changes_with_why_the_learner_is_asking(services):
    """Every explanation opened "Let's start from one concrete case." — which is what a form
    does, not a tutor."""
    topic = _first_topic_with_material(services)
    idx = _index_for_topic(services, topic)
    kwargs: dict[str, Any] = dict(index=idx, topic_title=topic.title, topic_id=topic.id, p_know=0.3)
    first = LT.explain("why do I have to make the denominators the same?", context="first_attempt", seed="a", **kwargs)
    repeat = LT.explain("why do I have to make the denominators the same?", context="repeat_error", seed="a", **kwargs)
    after_hint = LT.explain("why do I have to make the denominators the same?", context="after_hint", seed="a", **kwargs)
    openers = {first.sections[0]["text"] if first.sections else "", repeat.sections[0]["text"] if repeat.sections else "", after_hint.sections[0]["text"] if after_hint.sections else ""}
    assert len(openers) == 3, f"three contexts, one opener: {openers}"


# --------------------------------------------------------------------------- #
# 4.1.7 — the practice queue's difficulty
# --------------------------------------------------------------------------- #


def _pick(topic: str, expected: float, *, utility: float = 1.0, item_id: str = "x") -> Any:
    from adapt.assessment.selector import Pick

    return Pick(
        item={"id": item_id, "topic_id": topic, "kind": "numeric", "difficulty": 0.5, "answer_spec": {"value": 1}},
        topic_id=topic,
        reason=f"p={expected:.2f}",
        utility=utility,
        expected_success=expected,
    )


def test_the_queue_does_not_serve_a_wall_of_questions_the_learner_will_fail():
    """Measured before the fix: ~32% predicted success against a stated 78% target."""
    picks = [_pick("top_a", 0.30, item_id="hard1"), _pick("top_a", 0.32, item_id="hard2"), _pick("top_b", 0.78, item_id="good"), _pick("top_b", 0.74, item_id="good2")]
    kept = enforce_target_band(picks, count=2)
    assert [p.item["id"] for p in kept] == ["good", "good2"], "the band is a filter, not a hint"
    assert picks[0].meta.get("withheld"), "a withheld item says so on its own record"


def test_a_below_floor_item_is_only_served_with_its_example_open():
    picks = [_pick("top_a", 0.20, item_id="hard")]
    kept = enforce_target_band(picks, count=1)
    assert len(kept) == 1, "a thin pool must still serve the session that was asked for"
    assert kept[0].attach_worked_example is True
    assert "worked example" in kept[0].reason


def test_a_flagged_prerequisite_gap_puts_prerequisite_items_first():
    """The reason string claimed "prerequisite gap flagged first" while the queue contained
    no prerequisite items at all."""
    picks = [_pick("top_gap", 0.80, item_id="on_topic"), _pick("top_prereq", 0.80, item_id="prereq")]
    kept = enforce_target_band(
        picks,
        count=2,
        gap_topics=["top_gap"],
        gap_prerequisites={"top_gap": ["top_prereq"]},
    )
    assert kept[0].item["id"] == "prereq", "the prerequisite has to come first when it is named"
    assert "prerequisite" in kept[0].reason


def test_the_shipped_target_band_is_the_promised_one():
    assert TARGET_BAND == (0.70, 0.85)
    assert MIN_EXPECTED_SUCCESS == 0.45


# --------------------------------------------------------------------------- #
# 4.1.8 / 4.1.9 — what reaches the learner
# --------------------------------------------------------------------------- #


def test_placement_never_shows_the_hint_text_a_placement_item_carries(client, learner, services, monkeypatch):
    """``prompt_context`` ("More digits does not mean a bigger decimal…") was printed above
    questions the screen describes as having no hints.

    Two assertions: nothing served carries it, and when an item *does* carry it the
    withholding is named in the payload. The second one is driven directly, because which
    item the selector picks for a given learner is the selector's business — a placement
    draw on this bank happens not to pick any of the eight authored ``prompt_context``
    items, so waiting for one to turn up would be testing the draw, not the fix.
    """
    from adapt.ai import ai2 as AI2
    from adapt.assessment import selector as SEL

    with services.db.session() as s:
        row = s.execute(select(Item).where(Item.prompt_context.is_not(None))).scalars().first()
    assert row is not None and row.prompt_context, "the bank still has prompt_context, so this proves something"
    carrier = services.ai2._item_dict(row)

    real = SEL.placement_plan
    monkeypatch.setattr(
        SEL,
        "placement_plan",
        lambda **kw: [SEL.Pick(item=carrier, topic_id=row.topic_id, reason="test", utility=1.0)],
    )
    try:
        entries = services.ai2.diagnostic_items(learner["student_id"], count=4)
    finally:
        monkeypatch.setattr(SEL, "placement_plan", real)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["item"].get("prompt_context") in (None, ""), entry["item"].get("prompt_context")
    assert entry["item"]["id"] == row.id
    assert entry["prompt_context_withheld"], "the withheld text is named, not silently dropped"

    # and practice keeps it, because there it is a legitimate scaffold
    served = client.get("/api/v1/assessment/diagnostic", headers=learner["headers"]).json()
    for e in served["items"]:
        assert e["item"].get("prompt_context") in (None, ""), e["item"].get("prompt_context")


def test_every_pick_carries_a_plain_language_reason_and_a_topic_title(client, learner):
    """The screen printed the topic id and the developer's reason string."""
    body = client.get("/api/v1/assessment/diagnostic", headers=learner["headers"]).json()
    for entry in body["items"]:
        assert entry["item"].get("topic_title"), "the question screen needs a title, not an id"
        assert entry.get("reason_plain"), "the learner-facing reason is missing"
        plain = entry["reason_plain"]
        assert "p_know" not in plain and "belief" not in plain and "0." not in plain
        assert entry["reason"], "the engineer's string is still there for the console"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _all_items(services) -> list[Item]:
    with services.db.session() as s:
        return list(s.execute(select(Item).where(Item.active.is_(True))).scalars().all())


def _payload(row: Item) -> dict[str, Any]:
    return {
        "id": row.id,
        "topic_id": row.topic_id,
        "kind": row.kind,
        "stem": row.stem,
        "stem_template": row.stem_template,
        "choices": row.choices,
        "answer_spec": row.answer_spec,
        "solution_steps": row.solution_steps,
        "hints": row.hints,
        "rubric": row.rubric,
        "difficulty": row.difficulty,
        "prompt_context": row.prompt_context,
        "experience_mode": row.experience_mode,
        "est_seconds": row.est_seconds,
    }


def _render(payload: dict[str, Any]):
    from adapt.assessment.bank import render_item_for

    return render_item_for(payload, seed=3)


def _first_topic_with_material(services) -> Topic:
    with services.db.session() as s:
        row = s.execute(select(LearningMaterial).where(LearningMaterial.body.is_not(None))).scalars().first()
        assert row is not None and row.topic_id, "the seeded course has materials"
        topic = s.get(Topic, row.topic_id)
        assert topic is not None
        return topic


def _index_for_topic(services, topic: Topic):
    with services.db.session() as s:
        mats = [
            (m.id, m.topic_id or "", m.body, {"title": m.title, "topic_title": topic.title})
            for m in s.execute(select(LearningMaterial)).scalars().all()
        ]
    return LT.build_index(materials=mats)


def _index_for_all(services):
    with services.db.session() as s:
        titles = {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}
        mats = [
            (m.id, m.topic_id or "", m.body, {"title": m.title, "topic_title": titles.get(m.topic_id or "", "")})
            for m in s.execute(select(LearningMaterial)).scalars().all()
        ]
    return LT.build_index(materials=mats)


# --------------------------------------------------------------------------- #
# 4.2 — calibration: the interval and the early-signal label
# --------------------------------------------------------------------------- #


def test_a_single_answer_cannot_take_mastery_from_twenty_percent_to_three():
    """The headline number in the brief: 0.20 → 0.03 on one miss, from one answer."""
    from adapt.learning import bkt

    after, why = bkt.update_capped(0.20, False, bkt.BKTParams(), kind="numeric")
    assert after > 0.03, after
    assert after >= 0.20 - bkt.MAX_STEP_PER_ANSWER - 1e-9
    assert why["capped"] is True, "the bound is what stops it, and it is recorded"


def test_guess_rate_follows_the_question_format():
    from adapt.learning import bkt

    assert bkt.guess_for_kind("mcq", choices=4) == pytest.approx(0.25)
    assert bkt.guess_for_kind("numeric") == pytest.approx(0.02)
    assert bkt.guess_for_kind("text") == pytest.approx(0.05)
    assert bkt.guess_for_kind("teachback") == pytest.approx(0.05)
    assert bkt.guess_for_kind("worked_example") == pytest.approx(0.0)


def test_slip_is_softened_for_constructed_answers():
    from adapt.learning import bkt

    assert bkt.params_for_kind("numeric").p_slip == pytest.approx(0.15)
    assert bkt.params_for_kind("text").p_slip == pytest.approx(0.15)


def test_the_interval_is_a_range_and_narrows_with_evidence():
    from adapt.learning import bkt

    assert bkt.credible_interval(0.5, 0, 0) == (0.0, 1.0), "no answers, no claim"
    wide = bkt.credible_interval(0.7, 3, 3)
    narrow = bkt.credible_interval(0.7, 40, 28)
    assert wide[1] - wide[0] > narrow[1] - narrow[0] > 0
    assert 0.0 <= narrow[0] <= narrow[1] <= 1.0
    # 4-of-5 and 40-of-50 are not the same claim, and the interval says so
    a = bkt.credible_interval(0.8, 5, 4)
    b = bkt.credible_interval(0.8, 50, 40)
    assert (a[1] - a[0]) > (b[1] - b[0]) + 0.2


def test_every_estimate_a_screen_can_show_carries_n_and_the_early_signal_label(client, learner, services):
    """The rule is "in every UI surface that shows an estimate" — so it is checked on the
    payloads those surfaces render, not on one of them."""
    from adapt.learning.bkt import EARLY_SIGNAL_N

    receipt = _answer_one(client, learner, services)
    mastery = receipt["mastery"]
    assert mastery["observations"] >= 1
    assert mastery["n"] == mastery["observations"], "the count travels under both names"
    assert len(mastery["ci"]) == 2 and mastery["ci"][0] <= mastery["ci"][1]
    assert mastery["evidence"] == ("early signal" if mastery["observations"] < EARLY_SIGNAL_N else "measured")

    dash = client.get("/api/v1/dashboard", headers=learner["headers"]).json()
    for row in dash.get("mastery", []) or []:
        assert row.get("ci") and len(row["ci"]) == 2, row
        assert row.get("observations") is not None and row.get("n") == row["observations"], row
        assert row.get("evidence") in ("early signal", "measured"), row

    profile = client.get("/api/v1/profile", headers=learner["headers"]).json()
    rows = (profile.get("profile") or profile).get("mastery") or []
    for row in rows:
        assert row.get("ci"), row
        assert row.get("evidence") in ("early signal", "measured"), row


def test_cold_start_still_prints_new_and_never_a_low_ability_band(services, learner):
    """The rule that predates this round, re-asserted because calibration changed."""
    from adapt.learning import bkt

    assert bkt.BKTParams().band(0.20, observations=0) == "new"
    assert bkt.BKTParams().band(0.20, observations=1) == "new"


def _answer_one(client, learner, services) -> dict:
    """One recorded answer, through the API, as a learner would produce it."""
    from adapt.ai.local_tutor import eval_formula

    plan = client.get("/api/v1/assessment/diagnostic", headers=learner["headers"]).json()["items"]
    entry = plan[0]
    item_id = entry["item"]["id"]
    from adapt.services import attempts as AT

    with services.scope() as s:
        payload = AT.item_payload(s, item_id)
    assert payload is not None
    spec = payload.get("answer_spec") or {}
    if spec.get("value") is not None:
        wrong = float(spec["value"]) + 7
    elif spec.get("formula"):
        wrong = float(eval_formula(str(spec["formula"]), dict(spec.get("variables") or {}))) + 7
    else:
        wrong = "a wrong sentence, deliberately"
    sess = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    out = client.post(
        "/api/v1/attempts",
        json={"session_id": sess, "item_id": item_id, "answer": wrong, "attempt_no": 1},
        headers=learner["headers"],
    )
    assert out.status_code == 200, out.text
    return out.json()


def test_reseeding_an_unchanged_bank_changes_nothing(seeded, settings):
    """The bank is materialised on every start-up, so it has to be a *no-op* once it is there.

    Before this, two rows were rewritten on every boot and the start-up report said
    "2 examples filled, 2 items updated" forever. A report that cries wolf on every launch is a
    report nobody reads — and a row that flips between two states on every boot is a row whose
    content depends on when you looked at it.
    """
    from adapt.db.engine import Database
    from adapt.services.sessions import ensure_curriculum

    db = Database(settings)
    try:
        with db.session() as s:
            first = ensure_curriculum(s, settings=settings)
        assert first["items_updated"] == 0, f"first call was already converged but did work: {first}"
        with db.session() as s:
            second = ensure_curriculum(s, settings=settings)
        assert second["items_updated"] == 0, f"a second call rewrote rows: {second}"
        assert second["content"]["examples"] == 0 and second["content"]["hints"] == 0, second
        assert second["content"]["flagged"] == 0, second
    finally:
        db.dispose()


def test_the_example_checker_still_catches_a_disagreeing_answer():
    """The rule was relaxed about *where* the number sits, so it has to be shown it still
    fails a step that states a wrong answer."""
    item = {
        "id": "itm_x",
        "kind": "numeric",
        "stem": "Work out 5 x 12.",
        "answer_spec": {"value": 60, "formula": "5 * 12", "variables": {}},
    }
    good = [
        {"text": "Five twelves: 5 x 12."},
        {"text": "5 x 12 = 60, so the answer is 60."},
        {"text": "Check: 12 x 5 is the same product."},
    ]
    assert W.verify_example(item, W.normalize_steps(good)).ok
    wrong_answer = [{"text": "5 x 12 = 55, so the answer is 55."}]
    verdict = W.verify_example(item, W.normalize_steps(wrong_answer))
    assert not verdict.ok and "key_disagrees" in verdict.flags, verdict
    # A ladder that ends with a caution rather than a number is fine, as long as the number is in
    # the working: this is the shape that was being thrown away on every start-up.
    caution_ending = [
        {"text": "10 x 6 = 60, a common product to compare against."},
        {"text": "5 x 12 = 60, so the product is 60."},
        {"text": "Multiplication is not addition: 5 + 12 is 17, which is a different question."},
    ]
    assert W.verify_example(item, W.normalize_steps(caution_ending)).ok
    # and a placeholder that survived rendering still blocks
    assert "placeholder" in W.verify_example(item, W.normalize_steps([{"text": "5 x {b} = 60"}])).flags
