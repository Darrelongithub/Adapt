"""The shipped catalogue carries prose subjects, not only the maths bank.

`test_subject_agnostic_content.py` proves the *paths* — segmentation, generic visual templates,
quotability checking — on its own fixtures. That is not the same claim as "you can open a history
concept in this app": the seeded catalogue was twenty mathematics topics, so in the running
product "any subject" was still only an assertion about code.

These two tests close that gap on the catalogue that actually ships:

* the bank contains a history and a biology topic, each with source notes and no arithmetic in
  them anywhere;
* every one of their items is gradeable right and wrong through the real grading path (a typed
  answer keyed to a word in the notes, a choice question, a teach-back, and a worked example that
  is never scored);
* the concept-versions path produces the three teaching versions and a matched question pair from
  those notes, with the quotability check passing and nothing left unverified.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from adapt.assessment import bank
from adapt.db.models import Topic
from adapt.services import sessions as S

PROSE_TOPICS = ("history_ww1", "bio_photosynthesis")
ARITHMETIC = "+-*/^=0123456789"


def _items(code: str) -> list[dict]:
    return [item for topic, item in bank.as_rows() if topic == code]


def test_the_catalogue_has_prose_subjects_with_notes_and_no_arithmetic():
    codes = {t["code"] for t in bank.topic_catalog()}
    assert set(PROSE_TOPICS) <= codes, f"catalogue is {sorted(codes)}"

    for code in PROSE_TOPICS:
        notes = bank.materials_for(code)
        assert notes, f"{code} has no source material, so nothing can be taught from it"
        body = notes[0][1]
        assert len(body) > 200, f"{code}'s notes are too thin to segment"
        # The point of the topic: there is no expression to evaluate anywhere in it. A date in a
        # stem is fine; a sum, a formula or a numeric item is not.
        items = _items(code)
        stems = " ".join(str(i.get("stem", "")) for i in items)
        assert "+" not in stems and "x =" not in stems and "=" not in stems, stems
        assert not any(i.get("kind") == "numeric" for i in items), [i.get("kind") for i in items]
        assert not any((i.get("answer_spec") or {}).get("formula") for i in items), "no formula key"
        for token in ("+", "=", "**"):
            assert token not in body, body
        assert len(_items(code)) >= 4, f"{code} has too few items to practise"


@pytest.mark.parametrize("code", PROSE_TOPICS)
def test_every_prose_item_is_gradeable_right_and_wrong(code):
    """A subject the app cannot mark is a subject it cannot teach."""
    from adapt.assessment import grading as G

    scored, unscored = 0, 0
    for item in _items(code):
        kind = item.get("kind")
        if kind == "worked_example":
            unscored += 1
            assert not item.get("answer_spec"), "a worked example must not carry a key"
            continue
        if kind == "teachback":
            # graded against the three-point checklist, on ideas rather than words, so an empty
            # answer covers nothing and an answer that makes the points scores
            empty = G.grade(item, "")
            assert empty.score == 0 and not empty.correct, empty
            full = G.grade(
                item,
                "The alliance system matters, and the Archduke's assassination in Sarajevo was "
                "the trigger; militarism and imperialism were the deeper causes."
                if code == "history_ww1"
                else "Chlorophyll in the chloroplast traps light energy, and the plant uses carbon "
                "dioxide and water to make glucose, releasing oxygen.",
            )
            assert full.score >= 0.6 and full.correct, full
            scored += 1
            continue
        spec = item.get("answer_spec") or {}
        if kind == "mcq":
            right = G.grade(item, spec["correct_index"])
            wrong = G.grade(item, (spec["correct_index"] + 1) % len(item["choices"]))
        else:
            accepted = str(spec["values"][0])
            right = G.grade(item, accepted)
            # something plausible and wrong, with none of the accepted words in it
            wrong = G.grade(item, "Napoleon's ambitions in Europe after the revolution")
        assert right.correct and right.score >= 0.5, (item["stem"], right)
        assert not wrong.correct, (item["stem"], wrong)
        scored += 1
    assert scored >= 3 and unscored == 1, f"{code}: {scored} scored, {unscored} unscored"


@pytest.mark.parametrize("code", PROSE_TOPICS)
def test_a_prose_concept_gets_three_versions_and_a_quotable_pair(services, learner, client, code):
    with services.scope() as s:
        S.ensure_curriculum(s)
        topic_id = s.execute(select(Topic.id).where(Topic.code == code)).scalar_one()
    body = client.get(f"/api/v1/concepts/{topic_id}/versions", headers=learner["headers"]).json()

    assert {"text", "visual", "worked_example"} <= set(body["versions"]), body["versions"].keys()
    assert body["versions"]["text"]["steps"], "the notes must segment into short steps"
    assert body["versions"]["visual"]["kind"] in {
        "flow",
        "comparison_table",
        "timeline",
        "labelled_diagram",
    }, body["versions"]["visual"]
    example = body["versions"]["worked_example"]
    assert example["steps"], "the source sentences are the worked example"

    # The matched pair is generated from the same notes, and its keys are checked by
    # quotability: a key that does not occur in the source is refused and reported, so an
    # empty `unverified` here means every key on this screen can be found in the material.
    pair = (body.get("questions") or {}).get("pair") or []
    assert len(pair) >= 2, f"no matched question pair for {code}: {body.get('questions')}"
    assert {q.get("kind") for q in pair} <= set(body["question_formats_in_bank"]), pair
    assert not body["unverified"], (
        "an item whose key cannot be found in the source must be reported, not served: "
        f"{body['unverified']}"
    )
    assert body["source_chars"] > 200, body["source_chars"]
    assert not body["checks"].get("no_material"), body["checks"]
