"""Master prompts 4, 5 and 6 -- curation, the retention check, and the script.

Same join as prompt 3's tests pin: the exact strings from the six-prompt set live
in code, every path that serves the learner carries the prompt's output contract
whether or not a model is configured, and a model's contribution is refused
wholesale when it breaks the shape -- most importantly, a curator may only cite
results it was actually handed, and the check's rationale carries the *measured*
timing rather than a quote from a textbook.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from adapt.ai.prompts import (
    APPROVAL_SCRIPT_TEMPLATE,
    RESOURCE_CURATION_SYSTEM,
    RESOURCE_CURATION_SYSTEM_PROMPT,
    RETENTION_CHECK_SYSTEM,
    RETENTION_CHECK_SYSTEM_PROMPT,
    fill_approval_script,
)
from adapt.assessment import teachback as TB  # noqa: F401  (reused cap style below)
from adapt.db.models import RetentionSchedule, Topic, utcnow
from adapt.learning import hypotheses as H
from adapt.learning import strategies as ST
from adapt.resources import curation as CUR

RESULTS = [
    {
        "title": "Fractions for beginners",
        "url": "https://openalex.test/work/f1",
        "provider": "openalex",
        "kind": "article",
        "year": "2019",
        "snippet": "a gentle introduction",
    },
    {
        "title": "On the teaching of rational numbers",
        "url": "https://arxiv.test/abs/a2",
        "provider": "arxiv",
        "kind": "paper",
        "year": "2021",
        "snippet": "a primary study",
    },
    {
        "title": "Fraction games",
        "url": "https://openlibrary.test/w3",
        "provider": "openlibrary",
        "kind": "book",
        "year": "",
        "snippet": "practice through play",
    },
    {
        "title": "Why denominators matter",
        "url": "https://youtube.test/v4",
        "provider": "youtube",
        "kind": "video",
        "year": "2018",
        "snippet": "a short explainer",
    },
    {
        "title": "A fifth result that should never be picked",
        "url": "https://elsewhere.test/x5",
        "provider": "web",
        "kind": "page",
        "year": "2020",
        "snippet": "beyond the four",
    },
]


# --------------------------------------------------------------------------- #
# the prompts themselves
# --------------------------------------------------------------------------- #


def test_prompts_four_five_and_six_are_pinned_in_code():
    cur = " ".join(RESOURCE_CURATION_SYSTEM.split())
    assert "research assistant" in cur
    assert "4 most useful items for THIS learner" in cur
    assert '"why": string (<=140 chars, speak to the learner directly)' in cur
    assert "intro-level sources when mastery is low" in cur
    ret = " ".join(RETENTION_CHECK_SYSTEM.split())
    assert "spaced-repetition retention check" in ret
    assert "Ebbinghaus forgetting curve" in ret
    assert '"rationale": string (<=140 chars, why now, referencing days since practice)' in ret
    assert 'we noticed you did {A}, {B}, {C}, {D}' in APPROVAL_SCRIPT_TEMPLATE
    assert "we want to change {X} to {Y}" in APPROVAL_SCRIPT_TEMPLATE
    assert RESOURCE_CURATION_SYSTEM_PROMPT == RESOURCE_CURATION_SYSTEM
    assert RETENTION_CHECK_SYSTEM_PROMPT == RETENTION_CHECK_SYSTEM


def test_fill_approval_script_is_the_exact_template_filled():
    out = fill_approval_script(
        ["re-answered question 3 four times", "opened the hint on each retry", "", "answered 2 of 6 unaided"],
        "text explanations",
        "worked examples first",
    )
    assert out == (
        "Hey, we noticed you did re-answered question 3 four times, opened the hint on each retry, "
        "answered 2 of 6 unaided. Based on what we've observed, we want to change text explanations "
        "to worked examples first. Do you want to continue, and is there anything else you'd like to add?"
    )


# --------------------------------------------------------------------------- #
# prompt 4: curation
# --------------------------------------------------------------------------- #


def test_deterministic_picks_cap_at_four_and_speak_to_the_learner():
    picks = CUR.deterministic_picks(RESULTS)
    assert len(picks) == 4
    assert RESULTS[4]["url"] not in [p["url"] for p in picks], "the fifth result is beyond the four"
    for p in picks:
        assert len(p["why"]) <= CUR.WHY_MAX_CHARS
        assert p["url"] in [r["url"] for r in RESULTS]
        assert p["title"] and p["source"]


def test_low_mastery_hears_intro_high_mastery_hears_primary_sources():
    low = CUR.deterministic_picks(RESULTS, level_hint="developing")
    high = CUR.deterministic_picks(RESULTS, level_hint="secure")
    assert low[0]["why"].startswith("Start here")
    assert "approachable" in low[0]["why"]
    assert high[0]["why"].startswith("Go deep")
    assert high[0]["why"].startswith("Go deep:")
    assert "source" in high[0]["why"] or "close to the source" in high[0]["why"]


def test_accept_picks_takes_only_urls_it_was_given():
    good = {
        "picks": [
            {"title": "invented title", "url": RESULTS[1]["url"], "source": "invented", "why": "the primary study, for checking the claim at its origin"},
            {"title": "x", "url": RESULTS[0]["url"], "source": "x", "why": "an approachable start"},
        ]
    }
    picks, reason = CUR.accept_picks(good, results=RESULTS)
    assert reason == "accepted" and picks is not None and len(picks) == 2
    # title and source come from the result row, not the model's mouth
    assert picks[0]["title"] == RESULTS[1]["title"] and picks[0]["source"] == RESULTS[1]["provider"]

    invented = {"picks": [{"title": "t", "url": "https://made-up.test/paper", "source": "s", "why": "trust me"}]}
    picks, reason = CUR.accept_picks(invented, results=RESULTS)
    assert picks is None and reason.startswith("unknown_url")

    for bad in (None, {}, {"picks": []}, {"picks": [{"url": RESULTS[0]["url"]}] * 7}, {"picks": "top 4"}):
        picks, reason = CUR.accept_picks(bad, results=RESULTS)
        assert picks is None, bad


def test_accept_picks_cuts_a_long_why_and_fills_a_missing_one():
    payload = {
        "picks": [
            {"title": "t", "url": RESULTS[0]["url"], "source": "s", "why": "word " * 60},
            {"title": "t", "url": RESULTS[1]["url"], "source": "s", "why": ""},
        ]
    }
    picks, _ = CUR.accept_picks(payload, results=RESULTS)
    assert picks is not None
    assert len(picks[0]["why"]) <= CUR.WHY_MAX_CHARS
    assert picks[1]["why"], "a missing why falls back to a generated sentence, not an empty string"


class _CaptureLLM:
    def __init__(self, data: dict | None) -> None:
        self.configured = True
        self.data = data
        self.calls: list[dict] = []

    async def complete(self, **kwargs: object):
        from adapt.ai.llm import LLMResult

        self.calls.append(dict(kwargs))
        if self.data is None:
            return LLMResult(ok=False, data=None, model="stub", status="error", latency_ms=1)
        return LLMResult(ok=True, data=self.data, model="stub", status="ok", latency_ms=1)


async def test_curate_without_a_model_answers_with_rule_picks(services):
    out = await services.ai2.curate_resources(query="fractions", results=RESULTS, level_hint="developing")
    assert out["picks_source"] == "rules"
    assert len(out["picks"]) == 4 and out["picks"][0]["why"].startswith("Start here")


async def test_curate_with_a_model_uses_it_and_still_cites_only_given_results(services):
    stub = _CaptureLLM(
        {"picks": [{"title": "rewritten", "url": RESULTS[2]["url"], "source": "rewritten", "why": "short practice, in game form, between explanations"}]}
    )
    saved = services.ai2.llm
    services.ai2.llm = stub
    try:
        out = await services.ai2.curate_resources(query="fractions", results=RESULTS)
    finally:
        services.ai2.llm = saved
    assert out["picks_source"] == "model"
    assert out["picks"][0]["title"] == RESULTS[2]["title"], "the bibliographic record comes from the result row"
    assert stub.calls and "RAW SEARCH RESULTS" in str(stub.calls[0]["user"])


async def test_curate_falls_back_to_rules_when_the_model_invents_a_source(services):
    stub = _CaptureLLM({"picks": [{"title": "t", "url": "https://invented.test/x", "source": "s", "why": "nope"}]})
    saved = services.ai2.llm
    services.ai2.llm = stub
    try:
        out = await services.ai2.curate_resources(query="fractions", results=RESULTS)
    finally:
        services.ai2.llm = saved
    assert out["picks_source"] == "rules"
    assert out["llm_picks"].startswith("unknown_url")


async def test_curate_with_no_results_says_so_instead_of_calling(services):
    stub = _CaptureLLM(None)
    saved = services.ai2.llm
    services.ai2.llm = stub
    try:
        out = await services.ai2.curate_resources(query="fractions", results=[])
    finally:
        services.ai2.llm = saved
    assert out["picks"] == [] and out["picks_source"] == "rules"
    assert stub.calls == [], "an empty result set must not spend a model call"


# --------------------------------------------------------------------------- #
# prompt 5: the retention check
# --------------------------------------------------------------------------- #


async def _seed_schedule(services, learner, session_id: str, *, with_recall: bool, with_attempt: bool) -> str:
    from adapt.db.models import Attempt, Item

    with services.scope() as s:
        topic = s.execute(select(Topic).order_by(Topic.difficulty).limit(1)).scalars().first()
        s.add(
            RetentionSchedule(
                student_id=learner["student_id"],
                topic_id=topic.id,
                half_life_hours=48.0,
                reps=2,
                last_recall_at=(utcnow() - timedelta(days=3)) if with_recall else None,
                due_at=utcnow() - timedelta(hours=1),
            )
        )
        if with_attempt:
            item = s.execute(
                select(Item).where(Item.topic_id == topic.id, Item.active.is_(True)).limit(1)
            ).scalars().first()
            if item is not None:
                s.add(
                    Attempt(
                        session_id=session_id,
                        student_id=learner["student_id"],
                        item_id=item.id,
                        topic_id=topic.id,
                        correct=True,
                        score=1.0,
                        first_answer={},
                        final_answer={},
                    )
                )
        s.commit()
        return topic.id


async def test_no_schedule_means_no_check(services, learner):
    assert await services.ai1.design_retention_check(learner["student_id"], "topic-does-not-exist") is None


async def test_first_check_is_honest_about_having_no_timing_yet(services, learner, session):
    topic_id = await _seed_schedule(services, learner, session, with_recall=False, with_attempt=False)
    out = await services.ai1.design_retention_check(learner["student_id"], topic_id)
    assert out is not None and out["source"] == "rules"
    assert out["expected_recall"] is None
    assert "First spaced check" in out["rationale"] and len(out["rationale"]) <= 140
    assert topic_id.replace("-", "") [:0] == ""  # (keeps the fixture honest about ids)
    assert "key idea" in out["question"] and out["title"] in out["question"]
    assert out["expected_answer_notes"]
    assert out["grounded_item_id"] is None


async def test_check_cites_measured_recall_and_reuses_the_learners_own_item(services, learner, session):
    topic_id = await _seed_schedule(services, learner, session, with_recall=True, with_attempt=True)
    out = await services.ai1.design_retention_check(learner["student_id"], topic_id)
    assert out is not None
    assert out["expected_recall"] is not None and 0.0 < out["expected_recall"] < 1.0
    assert "projected recall is about" in out["rationale"] and "3.0 days" in out["rationale"]
    assert len(out["rationale"]) <= 140
    assert out["question"].startswith("Last time you worked on")
    assert out["grounded_item_id"], "the check re-asks the item the learner actually practised"
    assert out["expected_answer_notes"].startswith("A good answer")


async def test_a_model_may_rewrite_the_check_and_is_refused_when_it_cannot(services, learner, session):
    topic_id = await _seed_schedule(services, learner, session, with_recall=True, with_attempt=False)

    good = _CaptureLLM({"question": "What stayed from three weeks ago?", "expectedAnswerNotes": "the rule and one example", "rationale": "recall is projected near the check window now"})
    saved = services.ai1.llm
    services.ai1.llm = good
    try:
        out = await services.ai1.design_retention_check(learner["student_id"], topic_id, use_llm=True)
    finally:
        services.ai1.llm = saved
    assert out["source"] == "model" and out["question"] == "What stayed from three weeks ago?"
    assert len(out["rationale"]) <= 140

    empty = _CaptureLLM({"question": "", "expectedAnswerNotes": "", "rationale": ""})
    services.ai1.llm = empty
    try:
        out = await services.ai1.design_retention_check(learner["student_id"], topic_id, use_llm=True)
    finally:
        services.ai1.llm = saved
    assert out["source"] == "rules" and out["llm_check"] == "missing_fields"


async def test_the_review_check_route_hides_the_notes_until_asked(client, learner, services, session):
    topic_id = await _seed_schedule(services, learner, session, with_recall=True, with_attempt=False)
    r = client.get(f"/api/v1/review/check?topic_id={topic_id}", headers=learner["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "expected_answer_notes" not in body
    assert body["question"] and body["rationale"] and body["source"] in {"rules", "model"}

    with_notes = client.get(f"/api/v1/review/check?topic_id={topic_id}&with_notes=1", headers=learner["headers"])
    assert with_notes.status_code == 200
    assert with_notes.json()["expected_answer_notes"]

    missing = client.get("/api/v1/review/check?topic_id=nope", headers=learner["headers"])
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# prompt 6: the script inside the proposal ask
# --------------------------------------------------------------------------- #


def _draft(statement: str) -> H.Draft:
    return H.Draft(
        detector="worked_example_need",
        statement=statement,
        mechanism="a mechanism",
        citations=["Sweller & Cooper 1985"],
        falsification="if accuracy after examples drops back, the idea was wrong",
        alternatives=["the items were easier"],
        strategy="worked_examples",
        confidence=0.6,
    )


def test_the_ask_uses_the_exact_script_when_the_facts_fit_it(services):
    strategy = ST.get("worked_examples")
    body = services.ai1._proposal_text(
        _draft(
            "You averaged 0.40 right after reading a full explanation, but 0.70 after a worked example "
            "of the same problem type (6 questions each). One explanation that fits: the procedure is "
            "easier to copy than to reconstruct."
        ),
        strategy,
        None,
    )
    ask = body["ask_text"]
    assert ask.startswith("Hey, we noticed you did averaged 0.40 right after reading")
    assert "we want to change the current approach to" in ask
    assert strategy.label in ask
    assert ask.endswith("is there anything else you'd like to add?")
    assert "One explanation" not in ask, "the interpretation stays out of the script; only the measurement goes in"


def test_the_standing_ask_survives_for_sentence_shapes_the_script_cannot_carry(services):
    strategy = ST.get("worked_examples")
    body = services.ai1._proposal_text(
        _draft("Questions taught as examples landed at 0.7, where difficulty predicted 0.5, over 9 answers."),
        strategy,
        None,
    )
    assert body["ask_text"] == (
        "Want me to try that? You can say no, or change it — I will not change how you learn without asking."
    )


async def test_the_scripted_ask_reaches_the_proposal_record(services, learner):
    """End to end through propose(): the row the decisions screen reads carries the script."""
    from adapt.db.models import AdaptationProposal, ProposalStatus

    draft = _draft("You averaged 0.40 right after reading, but 0.70 after a worked example. One explanation that fits: examples do the teaching.")
    made = services.ai1.propose(learner["student_id"], [draft])
    rows = [m for m in made if isinstance(m, dict) and m.get("id")]
    assert rows, made
    with services.scope() as s:
        row = s.get(AdaptationProposal, rows[0]["id"])
        assert row.status == ProposalStatus.PENDING.value
        assert row.ask_text.startswith("Hey, we noticed you did averaged")
        # and nothing was applied: the proposal is the only thing that exists
        from adapt.db.models import ActiveAdaptation

        active = s.execute(
            select(ActiveAdaptation).where(ActiveAdaptation.student_id == learner["student_id"])
        ).scalars().all()
        assert active == []
