"""Every learner-facing string must be the *rendered* variant, not the author's template.

Parametric items are stored as ``stem_template`` + ``ranges`` + hint/step templates, and
:func:`adapt.assessment.bank.render_item_for` substitutes the sampled numbers. Two defects
this file pins down, both found by driving the HTTP surface rather than the maths:

1. the hint ladder handed ``"Undo the +{b} before undoing the x{a}."`` straight to the
   learner, because that one route skipped the render that the worked-example and reveal
   routes did -- a hint with braces in it is worse than no hint, and the feedback line
   "Try starting here: {c} - {b} = {t}" had the same cause;
2. each surface used its own seed (``abs(hash(...))``, ``i * 977``, ``len(used)``), and
   ``hash()`` of a string is salted per process, so after a restart the hint could be
   rendered for a *different* variant than the stem on the learner's screen. The fix is one
   seed function -- :func:`adapt.assessment.bank.variant_seed` -- used by every render of a
   given (learner, item).
"""

from __future__ import annotations

import re

PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
NUMBERS = re.compile(r"\d+(?:\.\d+)?")


def _numeric_entry(client, learner):
    plan = client.get("/api/v1/practice?count=8", headers=learner["headers"]).json()
    for entry in plan.get("items", []):
        item = entry.get("item") or {}
        if item.get("kind") == "numeric" and re.search(r"\d", item.get("stem") or ""):
            return entry, item
    return None, None


def test_the_stem_the_learner_sees_is_rendered(client, learner):
    """A stem full of braces would be the loudest possible failure, so check it first."""
    entry, item = _numeric_entry(client, learner)
    assert item is not None, "the practice queue offered no numeric item with numbers in it"
    assert not PLACEHOLDER.search(item["stem"]), f"unrendered stem: {item['stem']!r}"


def test_hint_text_has_no_placeholders_and_quotes_the_same_numbers(client, learner):
    entry, item = _numeric_entry(client, learner)
    assert item is not None
    item_id = item["id"]
    res = client.post(
        f"/api/v1/items/{item_id}/hint",
        json={"hints_used": 0, "item_id": item_id},
        headers=learner["headers"],
    )
    assert res.status_code == 200, res.text
    hint = res.json()["hint"]
    assert not PLACEHOLDER.search(hint), f"a hint reached the learner unrendered: {hint!r}"
    # If the hint cites numbers at all, they have to be the ones on the screen. A hint
    # about "3" under a question about "6x + 8 = 33" would send them down the wrong path.
    stem_nums = set(NUMBERS.findall(item["stem"]))
    hint_nums = set(NUMBERS.findall(hint))
    if hint_nums:
        assert hint_nums & stem_nums, f"hint numbers {sorted(hint_nums)} do not appear in the stem {item['stem']!r}"


def test_worked_example_and_reveal_agree_with_the_question(client, learner):
    entry, item = _numeric_entry(client, learner)
    assert item is not None
    item_id = item["id"]
    ex = client.post(f"/api/v1/items/{item_id}/worked-example", json={"step": 1}, headers=learner["headers"])
    assert ex.status_code == 200, ex.text
    steps = ex.json()["steps"]
    for st in steps:
        assert not PLACEHOLDER.search(st["text"]), f"worked example step unrendered: {st['text']!r}"
    rv = client.get(f"/api/v1/items/{item_id}/reveal", headers=learner["headers"])
    assert rv.status_code == 200, rv.text
    body = rv.json()
    for st in body.get("steps") or []:
        text = st.get("text") if isinstance(st, dict) else str(st)
        assert not PLACEHOLDER.search(text or ""), f"revealed step unrendered: {text!r}"
    # The reveal is asked for after the stem is on screen: same numbers, not a new sample.
    got = client.get(f"/api/v1/items/{item_id}", headers=learner["headers"])
    if got.status_code == 200:
        body2 = got.json()
        seen = (body2.get("item") or body2)["stem"]
        assert seen == item["stem"], f"two endpoints rendered two variants of one item: {seen!r} vs {item['stem']!r}"


def test_feedback_after_a_wrong_answer_quotes_nothing_unrendered(client, learner):
    entry, item = _numeric_entry(client, learner)
    assert item is not None
    session = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    res = client.post(
        "/api/v1/attempts",
        json={"session_id": session, "item_id": item["id"], "answer": "0.99999"},
        headers=learner["headers"],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert not PLACEHOLDER.search(body["feedback"]), f"feedback leaked a template: {body['feedback']!r}"
    # The nudge is only useful if it points at this question: check the numbers it cites
    # are the numbers in the stem (when it cites any).
    # Feedback may quote the question's numbers or repeat back what the learner typed --
    # both are about *this* answer. What it must not do is cite a number from a different
    # variant of the question (that is the bug this file exists to catch), so the
    # learner's own submission is excluded before comparing.
    stem_nums = set(NUMBERS.findall(item["stem"]))
    asked = set(NUMBERS.findall("0.99999"))
    cited = set(NUMBERS.findall(body["feedback"])) - asked
    if cited:
        assert cited & stem_nums or not stem_nums, f"feedback cites {sorted(cited)}, the question says {item['stem']!r}"


def test_variant_seed_is_stable_across_processes(client, learner):
    """``hash()`` is salted per process; ``variant_seed`` must not be, or the numbers change
    on restart while a learner is mid-question.
    """
    from adapt.assessment.bank import variant_seed

    a, b = variant_seed("stu_x", "itm_y"), variant_seed("stu_x", "itm_y")
    assert a == b and 0 <= a <= 0xFFFFFFFF
    assert variant_seed("stu_x", "itm_y") != variant_seed("stu_z", "itm_y"), "the seed must depend on the learner"
    assert variant_seed("stu_x", "itm_y") != variant_seed("stu_x", "itm_q"), "the seed must depend on the item"
    # And it is the value the routes use: reproduce one from the crc32 definition.
    import zlib

    assert a == (zlib.crc32(b"stu_x:itm_y") & 0xFFFFFFFF)


def _strings(payload: object, out: list[str]) -> None:
    if isinstance(payload, dict):
        for v in payload.values():
            _strings(v, out)
    elif isinstance(payload, list):
        for v in payload:
            _strings(v, out)
    elif isinstance(payload, str):
        out.append(payload)


def test_no_learner_facing_endpoint_shows_an_unrendered_template_for_any_item(
    client, learner, services
):
    """Every active item, every path a learner can see, no ``{placeholders}``.

    Deliberately exhaustive over the bank rather than one representative item: whether
    a parametric item gets picked by a plan changes with the data, so a single-item test
    passes or fails depending on which item the fixture happened to select. That is
    exactly how the hint and grading leaks survived the first version of this fix.
    """
    from sqlalchemy import select

    from adapt.db.models import Item

    with services.scope() as s:
        ids = [r.id for r in s.execute(select(Item).where(Item.active.is_(True))).scalars().all()]
    assert len(ids) >= 10, "the fixture bank is too small to prove anything here"
    checked = 0
    for item_id in ids:
        calls = [
            ("get", f"/api/v1/items/{item_id}"),
            ("get", f"/api/v1/items/{item_id}/reveal"),
            ("get", f"/api/v1/items/{item_id}/worked-example"),
        ]
        for method, url in calls:
            r = client.get(url, headers=learner["headers"])
            assert r.status_code == 200, f"{url} -> {r.status_code}"
            strings: list[str] = []
            _strings(r.json(), strings)
            leaks = [x for x in strings if PLACEHOLDER.search(x)]
            assert not leaks, f"{url} leaked an unrendered template for {item_id}: {leaks[:2]}"
            checked += 1
        for body in ({"item_id": item_id, "hints_used": 0}, {"item_id": item_id, "hints_used": 2}):
            r = client.post(f"/api/v1/items/{item_id}/hint", json=body, headers=learner["headers"])
            assert r.status_code == 200, r.text
            strings = []
            _strings(r.json(), strings)
            leaks = [x for x in strings if PLACEHOLDER.search(x)]
            assert not leaks, f"hint for {item_id} quoted a template instead of numbers: {leaks[:2]}"
            checked += 1
        # A teach-back is graded against the same variant the learner was shown, and the
        # sentences that come back are for them to read.
        r = client.post(
            f"/api/v1/items/{item_id}/teachback",
            json={"item_id": item_id, "text": "I moved the terms to one side and divided by the coefficient."},
            headers=learner["headers"],
        )
        assert r.status_code == 200, r.text
        strings = []
        _strings(r.json(), strings)
        leaks = [x for x in strings if PLACEHOLDER.search(x)]
        assert not leaks, f"teachback feedback for {item_id} quoted a template: {leaks[:2]}"
        checked += 1
    assert checked == len(ids) * 6  # three reads, two hints, one teach-back


def test_the_item_handed_to_the_tutor_is_the_rendered_variant(client, learner, services, monkeypatch):
    """``reveal`` and the hint used to read the stored row; this guards the same mistake.

    A placeholder scan over the *response* cannot catch it in local mode, because the
    fallback tutor returns rubric prose that happens to contain no numbers. So the
    invariant is asserted where it lives: the object AI 2 is asked to talk about.
    """
    import adapt.ai.ai2 as AI2
    from adapt.assessment import bank
    from adapt.services import attempts as A

    target = None
    with services.scope() as s:
        from sqlalchemy import select

        from adapt.db.models import Item

        for row in s.execute(select(Item).where(Item.active.is_(True))).scalars().all():
            if row.stem_template:
                target = row.id
                break
    assert target, "no parametric item in the bank: this test would prove nothing"

    seen: list[dict] = []
    original = AI2.EducationalIntelligence.review_teachback

    async def spy(self, student_id, item, text, **kw):  # noqa: ANN001 - wrapping the real method
        seen.append(dict(item))
        return await original(self, student_id, item, text, **kw)

    monkeypatch.setattr(AI2.EducationalIntelligence, "review_teachback", spy, raising=True)
    r = client.post(
        f"/api/v1/items/{target}/teachback",
        json={"item_id": target, "text": "I isolated x by subtracting then dividing."},
        headers=learner["headers"],
    )
    assert r.status_code == 200, r.text
    assert seen, "AI 2 was never asked to review the teach-back"
    stem = str(seen[0].get("stem") or "")
    assert stem and not PLACEHOLDER.search(stem), f"the tutor was handed a template, not a question: {stem[:80]!r}"

    # Not merely "some" rendering: *this learner's* variant, the one on their screen.
    with services.scope() as s:
        payload = A.item_payload(s, target)
    assert payload and payload.get("stem_template"), "the item's stored stem is already fixed: nothing to prove"
    expected = bank.render_item_for(payload, seed=bank.variant_seed(learner["student_id"], target))
    assert stem == str(expected.get("stem")), (stem[:60], str(expected.get("stem"))[:60])
