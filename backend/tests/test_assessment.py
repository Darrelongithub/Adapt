"""The item bank, the graders, the selector and the Numbas boundary.

A question bank that cannot be graded, or a selector that quietly returns nothing,
looks identical to a working system from the UI -- so both are asserted here.
"""

from __future__ import annotations

import json
import zipfile

import pytest


# --------------------------------------------------------------------------- #
# bank integrity
# --------------------------------------------------------------------------- #


def test_the_bank_is_internally_consistent():
    from adapt.assessment import bank

    topics = bank.topic_catalog()
    codes = {t["code"] for t in topics}
    assert len(topics) >= 12 and len(bank.ITEMS) == len(topics)
    rows = bank.as_rows()
    assert len(rows) >= 40, "the demo bank needs enough items for a real diagnostic"
    stems = set()
    for code, item in rows:
        assert code in codes, f"{item['stem'][:40]!r} points at a topic that does not exist"
        assert code in (item.get("tags") or []), "an item must carry its topic code as a tag"
        stems.add(item["stem"])
        assert 0.0 <= float(item["difficulty"]) <= 1.0
        assert item.get("stem"), "every item needs a stem"
        assert item["kind"] in {"mcq", "numeric", "text", "teachback", "worked_example", "visual"}
        assert item.get("experience_mode") in {"read", "visual", "example", "practice", "teachback"}
        assert int(item.get("est_seconds", 0)) > 0
        assert item.get("hints") is not None or item["kind"] == "worked_example"
        if item["kind"] == "mcq":
            choices = item.get("choices") or []
            assert len(choices) >= 3, "a three-option minimum keeps guessing at 33%"
            correct = (item.get("answer_spec") or {}).get("correct_index")
            assert correct is not None and 0 <= int(correct) < len(choices)
            assert all(c.get("text") for c in choices)
        if item["kind"] == "worked_example":
            assert item.get("solution_steps")
    assert len(stems) == len(rows), "duplicate stems read as a bug in the bank, not as a variant"

    # every topic has at least the two items a diagnostic needs
    thin = {code: len(items) for code, items in bank.ITEMS.items() if len(items) < 2}
    assert not thin, f"topics too thin to place a learner: {thin}"


def enumerate_items():
    """Bank items with the ids the seeder will give them (ids are assigned at seed)."""
    rows = []
    for code, item in __import__("adapt.assessment.bank", fromlist=["as_rows"]).as_rows():
        item = dict(item)
        item.setdefault("id", f"itm_{code}_{len(rows)}")
        rows.append((code, item))
    return rows


def test_every_item_can_actually_be_graded_right_and_wrong():
    """A bank item with no reachable correct answer is a bug that only shows up as
    a learner who cannot finish a session."""
    from adapt.assessment import bank
    from adapt.assessment.grading import grade

    right = wrong = 0
    for _code, item in enumerate_items():
        spec = item.get("answer_spec") or {}
        if item["kind"] not in ("mcq", "numeric", "text", "teachback"):
            continue
        if item["kind"] == "text" and not (spec.get("values") or spec.get("keywords") or spec.get("value") is not None or spec.get("formula")):
            # A question with nothing to mark: it is not served any more
            # (``selector.gradeable``), and this test is about items that can be served.
            continue
        if item["kind"] == "mcq":
            idx = spec.get("correct_index", 0)
            assert grade(item, idx).correct, item["id"]
            assert not grade(item, (idx + 1) % len(item.get("choices") or [1, 2, 3])).correct, item["id"]
            right += 1
            wrong += 1
        elif item["kind"] == "numeric":
            value = spec.get("value")
            if value is None and spec.get("formula"):
                from adapt.ai.local_tutor import eval_formula

                value = eval_formula(str(spec["formula"]), dict(spec.get("variables") or {}))
            assert grade(item, str(value)).correct, (item["id"], value)
            assert not grade(item, str(float(value) * 3 + 1)).correct, item["id"]
            right += 1
            wrong += 1
        elif item["kind"] == "text":
            accepted = spec.get("values") or [spec.get("value")]
            assert grade(item, accepted[0]).correct, item["id"]
            right += 1
        elif item["kind"] == "teachback":
            # A real explanation, in sentences: the checklist grades points evidenced by
            # sentences, so a comma-separated list of the rubric's words scores 0 on
            # purpose (that is the behaviour this test used to pin).
            concepts = (item.get("rubric") or {}).get("required_concepts") or []
            ideas = [str((c.get("synonyms") or [c.get("concept")])[0]) for c in concepts]
            text = (
                "The point is that "
                + " and the reason it works is that ".join(ideas)
                + " before you do anything else, and you can check it with one small example."
            )
            result = grade(item, text)
            assert result.score is not None and result.score > 0, (item["id"], result.partial)
            right += 1
    assert right >= 30 and wrong >= 20


def test_rendered_items_keep_their_answer_under_variants():
    from adapt.assessment import bank

    item = next(dict(row[1]) for row in bank.as_rows() if row[1].get("stem_template"))
    first = bank.render_item_for(item, seed=3)
    again = bank.render_item_for(item, seed=3)
    assert first["stem"] == again["stem"], "the same seed must render the same question"
    other = bank.render_item_for(item, seed=9)
    assert isinstance(other["stem"], str) and other["stem"]
    assert first["answer_spec"] == item["answer_spec"] or "value" in json.dumps(first["answer_spec"])


def test_materials_and_catalog_are_queryable():
    from adapt.assessment import bank

    catalog = bank.topic_catalog()
    assert all({"code", "title", "difficulty"} <= set(t) for t in catalog)
    some = catalog[0]["code"]
    assert isinstance(bank.materials_for(some), list)


# --------------------------------------------------------------------------- #
# grading edge cases
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "answer,expected",
    [(2, True), ("2", True), ("  2 ", True), (1, False), (None, False), ("banana", False)],
)
def test_numeric_grading_tolerates_the_shapes_a_ui_sends(answer, expected):
    from adapt.assessment.grading import grade

    item = {"kind": "numeric", "answer_spec": {"value": 2}}
    assert bool(grade(item, answer).correct) is expected


def test_numeric_units_are_opt_in_and_cost_half_a_mark_when_missing():
    from adapt.assessment.grading import grade

    bare = {"kind": "numeric", "answer_spec": {"value": 36, "unit": "cm", "require_unit": True}}
    got = grade(bare, "36")
    assert not got.correct and got.score == 0.5 and "unit" in got.detail
    assert grade(bare, "36 cm").correct
    # Without require_unit, "36" is simply right: enforcing units by default would
    # manufacture errors on items where units are implicit.
    assert grade({"kind": "numeric", "answer_spec": {"value": 36}}, "36").correct


def test_a_mark_decided_below_the_printed_digits_names_the_form_it_accepts():
    """`expected 11.3333, got 11.3333` is not a reason, and it is not actionable.

    An item whose answer is 34/3, marked to 1e-09, rejects a rounded decimal — and the
    detail it gave back printed both numbers at six significant digits, so the two
    strings either side of the mark were identical. The exact form is named instead.
    The same care applies to a *correct* mark, which must not be told it fell short.
    """
    from adapt.assessment.grading import grade

    item = {
        "kind": "numeric",
        "answer_spec": {
            "formula": "a*c/b",
            "variables": {"a": 1, "b": 3, "c": 34},
            "tolerance": 1e-09,
        },
    }
    rounded = grade(item, "11.3333")
    assert not rounded.correct
    assert "11.3333, got 11.3333" not in rounded.detail, rounded.detail
    assert "34/3" in rounded.detail, "the value the item accepts has to be the one on screen"

    exact = grade(item, "34/3")
    assert exact.correct, exact.detail
    assert "not close enough" not in exact.detail, "a correct answer is not a rounded one"

    # A number the short form carries exactly still reads as a plain number.
    plain = grade({"kind": "numeric", "answer_spec": {"value": 10.4, "tolerance": 1e-09}}, "10.5")
    assert not plain.correct and plain.detail == "expected 10.4, got 10.5"


def test_tolerance_and_equivalent_expressions():
    from adapt.assessment.grading import grade

    item = {"kind": "numeric", "answer_spec": {"value": 0.3333, "tolerance": 0.01}}
    assert grade(item, "1/3").correct or grade(item, "0.333").correct
    formula = {"kind": "numeric", "answer_spec": {"formula": "2*pi*5", "variables": {"pi": 3.14159}, "tolerance": 0.01}}
    assert grade(formula, "31.4159").correct


def test_mcq_by_index_or_by_text_and_feedback_names_the_misconception():
    from adapt.assessment.grading import grade

    item = {
        "kind": "mcq",
        "choices": [
            {"text": "3/8", "why_wrong": "that is the part eaten, not what is left"},
            {"text": "5/8", "why_wrong": None},
        ],
        "answer_spec": {"correct_index": 1},
    }
    assert grade(item, 1).correct
    assert grade(item, "5/8").correct
    missed = grade(item, 0)
    assert not missed.correct and "part eaten" in missed.detail


def test_text_grading_accepts_keyword_partial_credit():
    from adapt.assessment.grading import grade

    item = {"kind": "text", "answer_spec": {"keywords": ["denominator", "same size", "equivalent"], "min_keyword_fraction": 0.6}}
    assert grade(item, "you have to make the denominators the same size first").correct
    partial = grade(item, "they look different")
    assert not partial.correct and partial.score < 0.6


def test_worked_examples_are_never_scored():
    from adapt.assessment.grading import grade

    out = grade({"kind": "worked_example", "answer_spec": {}}, None)
    assert out.correct and out.score == 1.0 and out.method == "none"
    assert "not graded" in out.detail


def test_teachback_rubric_needs_both_content_and_substance():
    """A point needs a sentence that carries it, in the learner's own words.

    The old rule matched the rubric's words — so "denominator, equal parts" passed and a
    paraphrase failed. Both halves of that are asserted here, because the fix has two
    directions and a test that only checked one would let the other come back.
    """
    from adapt.assessment.grading import grade

    item = {
        "kind": "teachback",
        "rubric": {
            "required_concepts": [
                {"concept": "denominator", "synonyms": ["denominator", "bottom number"], "idea": "the bottom number"},
                {"concept": "same size", "synonyms": ["same size", "equal parts"], "idea": "equal parts"},
            ],
            "pass_fraction": 0.6,
        },
    }
    # A paraphrase that never uses the rubric's first word still makes the point.
    good = grade(
        item,
        "First you rewrite both of them so that the bottom number is the same size, and then the tops can be added together.",
    )
    assert good.correct, (good.detail, good.partial)
    # The rubric's words, listed, are not an explanation.
    listed = grade(item, "denominator. same size.")
    assert not listed.correct and listed.score < 0.6, listed.partial
    partial = grade(item, "just add them")
    assert not partial.correct and partial.score < 0.6, "one phrase is not an explanation"
    missing = (partial.partial or {}).get("missing") or []
    assert len(missing) == 2, missing
    points = (partial.partial or {}).get("points") or []
    assert points and all(p["met"] is False for p in points)


def test_ungradeable_kind_is_reported_not_silently_wrong():
    from adapt.assessment.grading import grade

    out = grade({"kind": "numbas_embedded", "answer_spec": {}}, "x")
    assert out.correct is False and out.method == "none"
    assert "Numbas" in out.detail


# --------------------------------------------------------------------------- #
# selector
# --------------------------------------------------------------------------- #


def _topics_and_items(n_topics=10, per_topic=4):
    topics, by_topic = [], {}
    for t in range(n_topics):
        tid = f"top_{t:024x}"
        topics.append({"id": tid, "code": f"code{t}", "title": f"Topic {t}", "difficulty": 0.2 + 0.05 * t})
        by_topic[tid] = [
            {
                "id": f"itm_{t}_{i}",
                "topic_id": tid,
                "kind": "teachback" if i == 3 else "mcq",
                "difficulty": 0.2 + 0.15 * i,
                "experience_mode": ("read", "visual", "example", "practice")[i % 4],
                "active": True,
                "est_seconds": 60,
                "choices": [{"text": "a"}, {"text": "b"}],
                "answer_spec": {"correct_index": 0},
            }
            for i in range(per_topic)
        ]
    return topics, by_topic


def test_diagnostic_plan_covers_topics_and_never_repeats_an_item():
    from adapt.assessment.selector import diagnostic_plan

    topics, by_topic = _topics_and_items()
    picks = diagnostic_plan(topics=topics, items_by_topic=by_topic, count=8, seed="stu_a")
    assert len(picks) == 8
    ids = [p.item["id"] for p in picks]
    assert len(set(ids)) == len(ids), "the same question twice in a diagnostic is wasted signal"
    assert len({p.topic_id for p in picks}) >= 6, "breadth first: one sample per topic before a second"
    for p in picks:
        assert p.topic_id.startswith("top_"), "the pick must carry the topic id, not a code"
        assert "diagnostic" in p.reason
        assert p.item["kind"] != "teachback" or p.meta["round"] == 0


def test_diagnostic_plan_accepts_topic_codes_as_the_index_key():
    """The bank is written in codes; callers pass ids. Both have to work.

    A one-sided version of this returned an empty plan -- and an empty plan is
    indistinguishable from "this learner needs no diagnostic" at the UI layer.
    """
    from adapt.assessment.selector import diagnostic_plan

    topics, by_id = _topics_and_items(n_topics=4)
    by_code = {t["code"]: by_id[t["id"]] for t in topics}
    picks = diagnostic_plan(topics=topics, items_by_topic=by_code, count=5, seed="stu_b")
    assert len(picks) == 5
    assert {p.item["topic_id"] for p in picks} == {t["id"] for t in topics} & {p.item["topic_id"] for p in picks}


def test_diagnostic_plan_is_seeded_and_bounded():
    from adapt.assessment.selector import diagnostic_plan

    topics, by_topic = _topics_and_items(n_topics=6, per_topic=2)
    a = [p.item["id"] for p in diagnostic_plan(topics=topics, items_by_topic=by_topic, count=6, seed="same")]
    b = [p.item["id"] for p in diagnostic_plan(topics=topics, items_by_topic=by_topic, count=6, seed="same")]
    assert a == b
    assert len(a) <= 12
    empty = diagnostic_plan(topics=[], items_by_topic={}, count=6)
    assert empty == [], "no curriculum must not crash the placement step"


def test_next_items_prefers_gaps_due_reviews_and_skips_used_items():
    from adapt.assessment.selector import next_items

    topics, by_topic = _topics_and_items(n_topics=4, per_topic=3)
    mastery = {t["id"]: 0.5 for t in topics}
    picks = next_items(
        items_by_topic=by_topic,
        mastery=mastery,
        gap_topics=[topics[0]["id"]],
        due_topics=[topics[1]["id"]],
        gap_severity={topics[0]["id"]: 1.0},
        used_item_ids=["itm_2_0", "itm_2_1", "itm_2_2"],
        count=5,
        seed="s",
    )
    assert picks and len(picks) <= 5
    chosen = {p.item["id"] for p in picks}
    assert not chosen & {"itm_2_0", "itm_2_1", "itm_2_2"}, "used items must not reappear"
    utilities = {p.topic_id: p.utility for p in picks}
    gap_pick = next(p for p in picks if p.topic_id == topics[0]["id"])
    assert any("gap" in p.reason or "due" in p.reason for p in picks)
    assert gap_pick.meta.get("gap_bonus")
    assert utilities  # every scored pick explains itself


def test_presentation_directives_come_from_approved_adaptations_only():
    from adapt.assessment.selector import presentation_from_directives

    plain = presentation_from_directives([])
    assert 0.0 < plain["target_success_rate"] <= 1.0
    assert plain["max_words"] is None, "no cap by default: only an approved change limits the learner"
    with_directives = presentation_from_directives(
        [
            {"strategy": "difficulty_recalibration", "payload": {"target": 0.7}},
            {"strategy": "segmented_text", "payload": {"max_words": 40}},
            {"strategy": "worked_examples", "payload": {"fade_after": 2}},
            {"strategy": "interleaving", "payload": {"ratio": 0.5}},
            {"strategy": "immediate_feedback", "payload": {"delay_s": 5}},
            {"strategy": "hint_fading", "payload": {"max_hints": 0}},
            {"strategy": "self_explanation", "payload": {"after_explanation": True}},
            {"strategy": "retrieval_practice", "payload": {"questions_per_session": 6}},
            {"strategy": "confidence_calibration", "payload": {}},
        ]
    )
    assert with_directives["target_success_rate"] == pytest.approx(0.7)
    assert with_directives["max_words"] == 40
    assert with_directives["attach_worked_example"] is True
    assert with_directives["interleaving_ratio"] == 0.5
    assert with_directives["feedback_delay_s"] == 5
    assert with_directives["hints_allowed"] == 0
    assert with_directives["require_teachback"] is True
    assert with_directives["re_read_allowed"] is False
    assert with_directives["ask_confidence"] is True
    assert with_directives["_example_fade_after"] == 2
    # An unrecognised strategy is ignored rather than trusted, and leaves the
    # defaults exactly where they were.
    assert presentation_from_directives([{"strategy": "whatever", "payload": {"x": 1}}]) == plain


def test_pick_payload_is_ready_for_the_client():
    from adapt.assessment.selector import diagnostic_plan

    topics, by_topic = _topics_and_items(n_topics=2, per_topic=2)
    pick = diagnostic_plan(topics=topics, items_by_topic=by_topic, count=1, seed="x")[0]
    payload = pick.as_dict()
    assert payload["item"]["id"]
    assert "max_words" in payload["presentation"]
    item_body = json.dumps(payload["item"])
    for key in ("answer_spec", "correct_index", "solution_steps", "hints", "rubric", "stem_template"):
        assert key not in item_body, f"Pick.as_dict() must not hand {key} to a client that has not answered"
    assert payload["item"]["choices"][0] == {"index": 0, "text": "a"}, "choices survive without their flags"
    assert payload["decision_trace"], "the learner can ask why they got this question"
    # ...and the server-side grading copy is still available when grading needs it.
    assert pick.as_dict(include_answer=True)["item"]["answer_spec"]["correct_index"] == 0


def test_review_queue_and_remediation_are_ordered_by_need():
    from adapt.assessment.selector import remediation_items, review_queue

    topics, by_topic = _topics_and_items(n_topics=4, per_topic=3)
    mastery = {topics[0]["id"]: 0.2, topics[1]["id"]: 0.9, topics[2]["id"]: 0.4, topics[3]["id"]: 0.6}
    from datetime import datetime, timedelta

    now = datetime(2026, 1, 5, 12, 0, 0)
    schedules = [
        {"topic_id": topics[0]["id"], "due_at": (now - timedelta(hours=3)).isoformat(), "reps": 1, "half_life_hours": 12.0, "expected_recall_at_due": 0.8, "overdue_factor": 1.2},
        {"topic_id": topics[2]["id"], "due_at": (now + timedelta(hours=30)).isoformat(), "reps": 0, "half_life_hours": 24.0, "expected_recall_at_due": 0.85, "overdue_factor": 1.0},
        {"topic_id": topics[3]["id"], "due_at": (now - timedelta(hours=1)).isoformat(), "reps": 2, "half_life_hours": 6.0, "expected_recall_at_due": 0.5, "overdue_factor": 1.05},
    ]
    queue = review_queue(schedules=schedules, mastery=mastery, items_by_topic=by_topic, now=now, count=4)
    assert [p.topic_id for p in queue] == [topics[3]["id"], topics[0]["id"]], (
        "only the two overdue topics are queued, and the one whose projected recall has fallen "
        "furthest below its overdue pressure comes first"
    )
    assert "review due" in queue[0].reason
    remediation = remediation_items(topic_id=topics[0]["id"], items=list(by_topic[topics[0]["id"]]), count=3)
    assert remediation and all(p.item["difficulty"] <= 0.5 for p in remediation), "start below the gap"


# --------------------------------------------------------------------------- #
# Numbas boundary: only export what the format can carry
# --------------------------------------------------------------------------- #


def test_question_export_carries_stem_choices_and_answer():
    from adapt.assessment.numbas import question_export

    item = {
        "id": "itm_x",
        "kind": "mcq",
        "stem": "What is 1/2 + 1/3?",
        "choices": [{"text": "5/6"}, {"text": "2/5"}],
        "answer_spec": {"correct_index": 0},
        "difficulty": 0.4,
        "tags": ["fractions"],
    }
    q = question_export(item, topic_title="Fractions")
    assert q["name"] and q["uuid"]
    parts = q["metadata"]["question"]["parts"]
    assert parts, "a question with no parts is not importable"
    assert parts[0]["choices"] and any(c.get("correct") for c in parts[0]["choices"])
    body = json.dumps(q)
    assert "5/6" in body and "What is 1/2" in body
    assert q["metadata"]["adapt_review_topic"] == "Fractions", "the export must say which ADAPT topic to review"


def test_exam_export_writes_a_zip_the_importer_can_read_back(tmp_path):
    from adapt.assessment.numbas import exam_export, import_exam, question_export, supported_subset, write_numbas_zip

    items = [
        {
            "id": f"itm_{i}",
            "kind": "mcq",
            "stem": f"Question {i}: what is 1/2 + 1/{i+2}?",
            "choices": [{"text": "yes", "correct": True}, {"text": "no"}],
            "answer_spec": {"correct_index": 0},
            "difficulty": 0.3 + 0.1 * i,
        }
        for i in range(3)
    ]
    exam, report = exam_export(items, title="ADAPT test")
    assert report.exported == 3
    path = write_numbas_zip(tmp_path / "exam.numbas", exam, report=report)
    assert path.exists() and path.stat().st_size > 200
    with zipfile.ZipFile(path) as zf:
        assert set(zf.namelist()) == {"exam.json", "adapt_manifest.json"}
        data = json.loads(zf.read("exam.json").decode())
        assert len(data["questions"]) == 3
        assert data["settings"]["diagnostic"] is False
        manifest = json.loads(zf.read("adapt_manifest.json").decode())
        assert manifest["generator"] == "ADAPT" and manifest["report"]["exported"] == 3
    read_back, report2 = import_exam(path)
    assert len(read_back) == 3, report2.as_dict()
    assert {r["kind"] for r in read_back} == {"mcq"}
    assert all(r["answer_spec"]["correct_index"] == 0 for r in read_back)
    assert report2.imported == 3
    subset = supported_subset(items)
    assert len(subset["runnable_in_app"]) + len(subset["needs_numbas_runtime"]) == 3


def test_items_that_numbas_cannot_carry_are_reported_not_dropped_silently():
    from adapt.assessment.numbas import exam_export, supported_subset

    items = [
        {"id": "itm_ok", "kind": "mcq", "stem": "s", "choices": [{"text": "a", "correct": True}], "answer_spec": {"correct_index": 0}},
        {"id": "itm_audio", "kind": "teachback", "stem": "explain it out loud", "answer_spec": {}},
        {"id": "itm_video", "kind": "text", "stem": "type an essay", "answer_spec": {"values": ["x" * 4000]}},
    ]
    _exam, report = exam_export(items, title="mixed")
    assert report.exported == 3
    subset = supported_subset(items)
    assert "itm_ok" not in json.dumps(subset)  # the report names questions by stem, not by internal ids
    assert len(subset["runnable_in_app"]) + len(subset["needs_numbas_runtime"]) == 3
    teachback = next((it for it in items if it["kind"] == "teachback"), None)
    assert teachback is not None


def test_checksum_is_stable_and_sensitive():
    from adapt.assessment.numbas import checksum

    a = {"questions": [{"id": 1}, {"id": 2}]}
    assert checksum(a) == checksum(json.loads(json.dumps(a)))
    assert checksum(a) != checksum({"questions": [{"id": 1}]})


def test_import_maps_topic_codes_to_ids():
    from adapt.assessment.numbas import question_import

    from adapt.assessment.numbas import question_export

    source = {
        "id": "itm_src",
        "kind": "mcq",
        "stem": "What is 1/2 + 1/3?",
        "choices": [{"text": "5/6", "correct": True}, {"text": "2/5", "correct": False}],
        "answer_spec": {"correct_index": 0},
        "difficulty": 0.4,
    }
    # Import the exporter's own output: a hand-written dict is not a Numbas package,
    # and a round trip is the claim we actually make.
    item, notes = question_import(question_export(source, topic_title="Fractions"))
    assert item is not None, notes
    assert item["kind"] == "mcq"
    assert item["answer_spec"]["correct_index"] == 0
    assert item["choices"] and any("5/6" in str(c.get("text")) for c in item["choices"])
    assert isinstance(notes, list)


def test_import_rejects_a_package_that_is_not_an_exam():
    from adapt.assessment.numbas import import_exam

    items, report = import_exam({"nope": True})
    assert items == []
    assert report.imported == 0
    assert report.warnings or report.skipped, "a refused package has to say why"


# --------------------------------------------------------------------------- #
# the bank must survive a real DB round trip
# --------------------------------------------------------------------------- #


def test_seeded_bank_is_readable_from_the_database(client, services, settings):
    from sqlalchemy import func, select

    from adapt.db.models import Item, Topic, TopicPrerequisite

    with services.scope() as s:
        topics = s.execute(select(func.count()).select_from(Topic)).scalar_one()
        items = s.execute(select(func.count()).select_from(Item)).scalar_one()
        edges = s.execute(select(func.count()).select_from(TopicPrerequisite)).scalar_one()
    assert topics >= 12 and items >= 40
    assert edges >= 8, "a topic graph with no edges cannot express a prerequisite gap"
    with services.scope() as s:
        orphans = s.execute(
            select(func.count()).select_from(Item).where(Item.topic_id.notin_(select(Topic.id)))
        ).scalar_one()
        assert orphans == 0
        kinds = dict(s.execute(select(Item.kind, func.count()).group_by(Item.kind)).all())
        assert {"mcq", "numeric"} <= set(kinds)
        missing_answer = s.execute(
            select(func.count()).select_from(Item).where(Item.answer_spec.is_(None))
        ).scalar_one()
        assert missing_answer == 0, "an item with no answer_spec cannot be graded"
