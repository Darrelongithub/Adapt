"""Model-written questions have to be answerable, checked, and measurable.

The failure this file exists for: ``ai2._llm_practice`` returned items carrying
``{"store": True}`` that nothing ever stored, so the tail of a practice session built
from generation was displayed and then refused with "unknown or retired item" when the
learner answered it. Grading resolves an item with ``s.get(Item, item_id)``; an id with
no row cannot be graded, calibrated or retired, however good the question was.

Three things are pinned here, in the order they matter:

1. an item the model wrote can be answered, scored, and stored (end to end, through the
   same HTTP surface the browser uses);
2. it was *checked* before it was stored -- arithmetic that disagrees with its own answer,
   a key computed from numbers that are not in the question, a distractor with no reason,
   a duplicate of an authored question: all refused, none shown;
3. what it claims stops mattering once learners have answered it -- difficulty moves to
   the observed rate and three separate things can retire an item.

The stub in this file is deliberately a *fake*, not a mock of the HTTP client: it returns
the same ``LLMResult`` shape the real provider does, so the code under test takes exactly
the path it takes in production.
"""

from __future__ import annotations

import json

import pytest

from adapt.ai.llm import LLMResult
from adapt.assessment import generated as GEN

ANSWER_KEYS = {"answer_spec", "correct_index", "answer_value", "check_expr", "solution_steps", "hints", "why_wrong"}


def _leaks_answers(payload: object) -> bool:
    if isinstance(payload, dict):
        if ANSWER_KEYS & set(payload):
            return True
        return any(_leaks_answers(v) for v in payload.values())
    if isinstance(payload, list):
        return any(_leaks_answers(v) for v in payload)
    return False


# --------------------------------------------------------------------------- #
# the checker: arithmetic, and what may not be arithmetic
# --------------------------------------------------------------------------- #


def test_the_expression_checker_accepts_arithmetic_and_refuses_everything_else():
    assert GEN.safe_arithmetic("6*7") == 42
    assert GEN.safe_arithmetic("(3 + 4) / 2") == 3.5
    assert GEN.safe_arithmetic("sqrt(16)") == 4
    assert GEN.safe_arithmetic("round(22/7, 2)") == pytest.approx(3.14)

    # Model output is untrusted input, so the checker is an AST walk and not an
    # allowlist around eval(): letters, dots and parentheses are all it takes to reach
    # attribute access, and `2 ** 10**9` is a denial of service rather than a mistake.
    for hostile in (
        "__import__('os').system('id')",
        "().__class__.__bases__",
        "open('/etc/passwd').read()",
        "2 ** 10 ** 9",
        "1 / 0",
        "6*7=",
        "",
    ):
        with pytest.raises(ValueError):
            GEN.safe_arithmetic(hostile)


def test_an_answer_its_own_arithmetic_contradicts_is_refused():
    validated, rejections = GEN.validate_generated(
        {
            "kind": "numeric",
            "stem": "A box holds 6 rows of 7 pencils. How many pencils is that?",
            "answer_value": 41,
            "check_expr": "6*7",
        },
        topic_id="t1",
    )
    assert validated is None
    assert [r.code for r in rejections] == ["check_expr_disagrees"]


def test_an_answer_computed_from_numbers_that_are_not_in_the_question_is_refused():
    """The subtle one: the arithmetic is internally consistent and about a different sum."""
    validated, rejections = GEN.validate_generated(
        {
            "kind": "numeric",
            "stem": "Sara cuts a cake into 8 equal slices and eats 3. What fraction is left?",
            "answer_value": 3,
            "check_expr": "27/9",
        },
        topic_id="t1",
    )
    assert validated is None
    assert [r.code for r in rejections] == ["check_expr_ungrounded"]


def test_a_numeric_item_that_passes_carries_the_tolerance_it_was_checked_with():
    """The grader's default is 1e-6; a generated answer gets rounded, so the two agree."""
    validated, rejections = GEN.validate_generated(
        {
            "kind": "numeric",
            "stem": "Write 3/7 as a decimal, rounded to two places.",
            "answer_value": 0.43,
            "check_expr": "3/7",
            "difficulty": 0.4,
        },
        topic_id="t1",
    )
    assert validated is not None, rejections
    spec = validated.payload["answer_spec"]
    assert spec["tolerance"] == pytest.approx(GEN.TOLERANCE_FLOOR)
    assert spec["value"] == 0.43
    # Difficulty is kept twice on purpose: one value stops being true as soon as someone
    # answers the question, and an observed estimate has to shrink toward something.
    assert validated.payload["prior_difficulty"] == validated.payload["difficulty"] == 0.4


def test_a_multiple_choice_question_needs_a_reason_for_every_wrong_choice():
    """The misconception is the whole reason to write a distractor."""
    base = {
        "kind": "mcq",
        "stem": "Which calculation must be done first in 7 + 3 x 2 - 1?",
        "choices": [
            {"text": "3 x 2", "why_wrong": None},
            {"text": "7 + 3", "why_wrong": "This works left to right and ignores that x binds tighter."},
            {"text": "2 - 1", "why_wrong": None},
        ],
        "correct_index": 0,
    }
    validated, rejections = GEN.validate_generated(base, topic_id="t1")
    assert validated is None
    assert {r.code for r in rejections} == {"distractor_reason_missing"}

    good = {**base, "choices": [base["choices"][0], base["choices"][1], {"text": "2 - 1", "why_wrong": "The subtraction is last, not first."}]}
    validated, rejections = GEN.validate_generated(good, topic_id="t1")
    assert validated is not None, rejections
    assert validated.payload["answer_spec"] == {"correct_index": 0}
    assert [c["correct"] for c in validated.payload["choices"]] == [True, False, False]


def test_a_generated_question_that_repeats_an_authored_one_is_refused():
    validated, rejections = GEN.validate_generated(
        {
            "kind": "text",
            "stem": "Which calculation must be done first in 7 + 3 x 2 - 1?",
            "answer_value": "multiplication",
        },
        topic_id="t1",
        existing_stems=["Which calculation must be done first in 7 + 3 x 2 - 1?"],
    )
    assert validated is None
    assert [r.code for r in rejections] == ["stem_already_in_bank"]


def test_the_same_question_written_twice_is_one_identity():
    """A fresh uuid per call is why generated items could never accumulate evidence."""
    spec = {"value": 42, "tolerance": 0.005}
    first = GEN.content_id(topic_id="t1", kind="numeric", stem="How many pencils?", answer_spec=spec)
    same = GEN.content_id(topic_id="t1", kind="numeric", stem="  How many   pencils?  ", answer_spec=spec)
    other = GEN.content_id(topic_id="t1", kind="numeric", stem="How many pencils are left?", answer_spec=spec)
    assert first.startswith("gen_") and first == same and first != other


def test_difficulty_moves_from_what_was_claimed_to_what_was_measured():
    # Nobody right, 4 attempts: the estimate walks toward 0 but the prior holds it up.
    assert GEN.observed_difficulty(None, declared=0.5, n_correct=0, n_attempts=4) == pytest.approx(1 / 3, abs=0.01)
    # Everybody right, many attempts: it converges on the truth, not on the claim.
    assert GEN.observed_difficulty(None, declared=0.5, n_correct=40, n_attempts=40) > 0.9
    # Declared 0.9, forty answers and none of them right: the estimate sinks to 0.15,
    # not to 0 -- the prior keeps weight 8 no matter how the attempts accumulate, which
    # is what stops one bad session from rewriting a question's difficulty entirely.
    assert GEN.observed_difficulty(None, declared=0.9, n_correct=0, n_attempts=40) == pytest.approx(0.15, abs=0.01)
    # No attempts yet: still exactly what the writer said.
    assert GEN.observed_difficulty(None, declared=0.33, n_correct=0, n_attempts=0) == 0.33


def test_retirement_has_three_separate_reasons_and_a_healthy_item_has_none():
    retire, why = GEN.should_retire(declared=0.5, observed=0.0, n_attempts=4, n_correct=0)
    assert (retire, why) == (True, "never_answered_correctly")

    retire, why = GEN.should_retire(declared=0.2, observed=0.95, n_attempts=6, n_correct=6)
    assert (retire, why) == (True, "difficulty_drifted")

    retire, why = GEN.should_retire(declared=0.5, observed=0.5, n_attempts=12, n_correct=6)
    assert (retire, why) == (True, "exposure_cap")

    # Three attempts with nobody right is a bad run, not a broken question.
    assert GEN.should_retire(declared=0.5, observed=0.0, n_attempts=3, n_correct=0) == (False, None)
    # Drifting, but not enough evidence yet.
    assert GEN.should_retire(declared=0.2, observed=0.9, n_attempts=5, n_correct=5) == (False, None)


# --------------------------------------------------------------------------- #
# end to end: written, stored, shown, answered, graded
# --------------------------------------------------------------------------- #


class StubLLM:
    """The provider's shape, without the provider: ``complete`` returns an LLMResult."""

    def __init__(self, items: list[dict] | None = None, *, batches: list[list[dict]] | None = None) -> None:
        self.configured = True
        self.batches = batches or [items or []]
        self.calls = 0

    async def complete(self, **_: object) -> LLMResult:
        batch = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        return LLMResult(
            ok=True,
            data={"items": batch},
            model="stub-gemini",
            status="ok",
            tokens_in=10,
            tokens_out=10,
            latency_ms=1,
        )


def _numeric_candidate(**overrides: object) -> dict:
    item = {
        "kind": "numeric",
        "stem": "A box holds 6 rows of 7 pencils. How many pencils is that?",
        "answer_value": 42,
        "check_expr": "6*7",
        "difficulty": 0.3,
        "est_seconds": 60,
        "solution_steps": ["Multiply the rows by the pencils in each row."],
        "hints": ["Six groups of seven."],
    }
    item.update(overrides)
    return item


def _starved_topic(services, *, with_items: int = 1) -> str:
    """A topic with one authored item: a session it cannot fill.

    Generation is the last resort by design -- a request is answered from the bank first
    and only the shortfall is written -- so this is the precondition for testing it, and
    it is also the real trigger (a topic with one item, a learner asking for six).
    Deliberately a topic of this test's own rather than a thinly-stocked seeded one: the
    test client is session-scoped, so earlier tests' generated items are still in the
    database, and "the smallest topic" is a moving target.
    """
    from adapt.db.models import Course, Item, Topic

    with services.scope() as s:
        course = Course(title="Generated-item contract tests")
        s.add(course)
        s.flush()
        topic = Topic(course_id=course.id, code="gen_contract", title="Contract topic", difficulty=0.5)
        s.add(topic)
        s.flush()
        for i in range(with_items):
            s.add(
                Item(
                    topic_id=topic.id,
                    kind="numeric",
                    stem=f"How many pairs are in {2 + i} shoes?",
                    answer_spec={"value": 1 + i},
                    difficulty=0.4,
                    source="bank",
                    active=True,
                )
            )
        s.commit()
        return str(topic.id)


def _practice(client, learner, *, count: int | None = None, topic_id: str | None = None):
    body: dict[str, object] = {}
    if count is not None:
        body["count"] = count
    if topic_id is not None:
        body["topic_id"] = topic_id
    response = client.post("/api/v1/practice", json=body, headers=learner["headers"])
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    ids = [e["item"]["id"] for e in items]
    assert len(ids) == len(set(ids)), f"one question appeared twice in one session: {ids}"
    return items


def _stored(services, item_id: str) -> dict | None:
    """The stored row for one generated item, or None.

    Looked up by id rather than by scanning the table: the test client is session-scoped,
    so rows from earlier tests are still in the database, and "the first generated row"
    is a different item by the time the tenth test asks for it.
    """
    from adapt.db.models import Item

    with services.scope() as s:
        row = s.get(Item, item_id)
        return GEN.row_dict(row) if row is not None else None


def test_a_written_question_is_stored_shown_and_answerable(client, services, learner):
    """The bug this file exists for: the item was displayed and then unanswerable."""
    services.ai2.llm = StubLLM([_numeric_candidate()])
    topic_id = _starved_topic(services)

    shown = [
        entry
        for entry in _practice(client, learner, count=6, topic_id=topic_id)
        if entry["decision_trace"].get("source") == "ai2"
    ]
    assert shown, "the generator produced nothing for a topic with no items left"
    entry = shown[0]
    assert entry["item"]["kind"] == "numeric"
    assert entry["decision_trace"]["stored"] is True

    # Nothing that carries the answer may reach the browser.
    assert not _leaks_answers(entry), entry

    # ... and the id resolves, so the answer is recordable at all.
    session = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    submitted = client.post(
        "/api/v1/attempts",
        json={"session_id": session, "item_id": entry["item"]["id"], "answer": 42},
        headers=learner["headers"],
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["correct"] is True
    assert body["topic_id"] == topic_id

    # The response is evidence about the question as well as about the learner.
    row = _stored(services, entry["item"]["id"])
    assert row is not None
    assert row["n_attempts"] == 1 and row["n_correct"] == 1
    assert row["calibrated_at"] is not None
    assert row["difficulty"] > 0.3, "one right answer moves the estimate up from the claim"
    assert row["prior_difficulty"] == 0.3, "the writer's value is kept for the next shrink"


def test_a_question_the_checker_refused_is_never_shown_or_stored(client, services, learner):
    candidate = _numeric_candidate(answer_value=41)  # 6*7 is not 41
    services.ai2.llm = StubLLM([candidate])
    items = _practice(client, learner, count=6, topic_id=_starved_topic(services))
    assert not [e for e in items if e["decision_trace"].get("source") == "ai2"]
    # Not shown *and* not stored: the identity it would have had resolves to nothing.
    would_be = GEN.content_id(
        topic_id=items[0]["topic_id"], kind="numeric", stem=candidate["stem"], answer_spec={"value": 41}
    )
    assert _stored(services, would_be) is None


def test_asking_twice_offers_the_stored_question_before_writing_a_new_one(client, services, learner):
    """Reuse first, write the remainder -- and never let a rewrite become a second row.

    The second call is answered with *different* questions by the stub on purpose: what is
    under test is not that the model sits idle, it is that the stored question comes back
    first (this learner has not answered it), the model is asked only for the shortfall,
    and one question the model writes twice is still one row.
    """
    fresh = _numeric_candidate(
        stem="A shelf holds 9 boxes of 5 pens. How many pens is that?", answer_value=45, check_expr="9*5"
    )
    stub = StubLLM(batches=[[_numeric_candidate()], [fresh, _numeric_candidate()]])
    services.ai2.llm = stub
    topic_id = _starved_topic(services)

    first = [
        e
        for e in _practice(client, learner, count=6, topic_id=topic_id)
        if e["decision_trace"].get("source") == "ai2"
    ]
    assert first, "nothing was written for a topic with nothing left to ask"
    first_id = first[0]["item"]["id"]

    second = _practice(client, learner, count=6, topic_id=topic_id)
    assert first_id in [e["item"]["id"] for e in second], (
        "the stored question this learner has not answered was not offered again"
    )
    # It may arrive as a generated pick (reuse) or as an ordinary one: once stored, a
    # generated question competes in the same pool as an authored question, and the
    # selector is entitled to rank it. Either way it says where it came from.
    assert all(e["decision_trace"].get("source") for e in second), second
    assert stub.calls == 2

    # The rewrite landed on the same row rather than creating a second one for the same
    # question: every returned id resolves to exactly one stored item.
    for entry in first + second:
        row = _stored(services, entry["item"]["id"])
        assert row is not None, entry["item"]["id"]


def test_a_generated_item_is_retired_when_nobody_can_answer_it(client, services, learner):
    """Four answers, none right: the question stops being served, and says why."""
    services.ai2.llm = StubLLM([_numeric_candidate()])
    entry = next(
        e
        for e in _practice(client, learner, count=6, topic_id=_starved_topic(services))
        if e["decision_trace"].get("source") == "ai2"
    )
    item_id = entry["item"]["id"]

    session = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    for _ in range(4):
        response = client.post(
            "/api/v1/attempts",
            json={"session_id": session, "item_id": item_id, "answer": 41},
            headers=learner["headers"],
        )
        assert response.status_code == 200, response.text
    assert response.json()["correct"] is False

    row = _stored(services, item_id)
    assert row is not None
    assert row["n_attempts"] == 4 and row["n_correct"] == 0
    assert row["active"] is False, "a question nobody has ever got right is not a hard question"

    # A retired item is refused rather than served: `item_payload` only returns active rows.
    refused = client.post(
        "/api/v1/attempts",
        json={"session_id": session, "item_id": item_id, "answer": 42},
        headers=learner["headers"],
    )
    assert refused.status_code == 422


def test_an_answer_key_survives_a_restart(client, services, learner):
    """The stored spec is what grades a later attempt, so it has to be complete."""
    services.ai2.llm = StubLLM([_numeric_candidate()])
    entry = next(
        e
        for e in _practice(client, learner, count=6, topic_id=_starved_topic(services))
        if e["decision_trace"].get("source") == "ai2"
    )
    row = _stored(services, entry["item"]["id"])
    assert row is not None
    assert row["answer_spec"]["value"] == 42
    assert row["answer_spec"]["check_expr"] == "6*7"
    assert row["answer_spec"]["tolerance"] > 0
    assert row["source"] == "ai2" and row["generated_by"] == "stub-gemini"
    # ... and the learner-facing payload is built from that row, not from the model's text.
    assert entry["item"]["id"] == row["id"]
    assert set(entry["item"]) & ANSWER_KEYS == set()


def test_the_stub_itself_matches_the_provider_it_stands_in_for():
    """If LLMResult grows a field the path reads, this test is the early warning."""
    result = LLMResult(ok=True, data={"items": []}, model="m")
    for field in ("ok", "data", "model", "status", "tokens_in", "tokens_out", "latency_ms"):
        assert hasattr(result, field)
    assert json.dumps(result.data or {}) == '{"items": []}'
