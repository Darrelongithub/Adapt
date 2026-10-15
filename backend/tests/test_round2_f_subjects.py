"""Round 2, phase F: the subject round's test selection, and whether its "why these" text is honest.

The brief asks that the skills a round tests depend on the subject, and that the sentence which
explains the choice says what actually happened. These tests check both through the same HTTP
surface the page uses, for four kinds of topic:

* a topic the app has written ratings for (history, sciences);
* a topic recognised as a subject but with no written ratings for it (maths, languages): the round
  uses that subject's own skills, unranked, and labels the choice as a fallback;
* a topic nobody can place: the six universal skills, labelled as a fallback.

A fallback label must never be followed by a claim about the learner's topic, and a near match must
not be described as the learner's own topic.
"""

from __future__ import annotations

import pytest

TOPICS = {
    "history": "world history",  # authored ratings
    "sciences": "photosynthesis",  # authored ratings
    "maths": "adding fractions",  # a recognised subject, with no written ratings for this topic
    "languages": "french past tense",  # a recognised subject, with no written ratings for this topic
    "unknown": "basket weaving",  # nothing recognised
}


def _plan(client, learner, topic: str) -> dict:
    r = client.get("/api/v1/subjects/plan", params={"topic": topic}, headers=learner["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _tested(plan: dict, role: str | None = None) -> set[str]:
    return {row["id"] for row in plan["tested"] if role is None or row["role"] == role}


@pytest.fixture()
def plans(client, learner):
    return {kind: _plan(client, learner, topic) for kind, topic in TOPICS.items()}


def test_each_kind_of_topic_tests_a_different_set_of_skills(plans):
    tested = {kind: _tested(plan) for kind, plan in plans.items()}
    for kind, skills in tested.items():
        assert skills, f"{kind} tested nothing"
    # Different subjects test different skills; the unknown topic falls back to the universal six.
    assert tested["history"].isdisjoint(tested["maths"])
    assert tested["maths"].isdisjoint(tested["languages"])
    assert tested["sciences"].isdisjoint(tested["history"])
    assert tested["unknown"] != tested["maths"] and tested["unknown"] != tested["languages"]


def test_the_history_round_leans_on_history_skills_and_says_it_is_authored(plans):
    plan = plans["history"]
    assert plan["topic"]["source"] == "authored"
    leaned = _tested(plan, role="leaned")
    assert leaned and all(skill_id.startswith("hist.") for skill_id in leaned), leaned
    assert "leans hardest" in plan["reason"], plan["reason"]
    assert "fallback" not in plan["reason"].lower()


def test_a_subject_without_written_ratings_is_labelled_a_fallback_not_a_finding(plans):
    for kind in ("maths", "languages"):
        plan = plans[kind]
        assert plan["topic"]["source"] == "generic_fallback", kind
        assert "fallback" in plan["reason"].lower(), (kind, plan["reason"])
        assert "rather than a finding about your topic" in plan["reason"], (kind, plan["reason"])
        assert "leans hardest" not in plan["reason"], f"{kind}: a fallback was described as a ranking"


def test_maths_and_languages_test_their_own_subject_skills(plans):
    assert all(s.startswith("maths.") for s in _tested(plans["maths"])), _tested(plans["maths"])
    assert all(s.startswith("lang.") for s in _tested(plans["languages"])), _tested(plans["languages"])


def test_an_unplaceable_topic_is_the_universal_fallback_and_says_so(plans):
    plan = plans["unknown"]
    assert plan["topic"]["source"] == "generic_fallback"
    assert "six skills every subject uses" in plan["reason"], plan["reason"]
    assert "rather than a finding about your topic" in plan["reason"], plan["reason"]


def test_a_near_match_is_not_described_as_the_learners_own_topic(client, learner):
    r = client.post("/api/v1/topics/resolve", json={"text": "Tudor history"}, headers=learner["headers"])
    assert r.status_code == 200, r.text
    resolved = r.json()["resolved"]
    assert resolved["title"] == "World history", "this pins the current match; update it if the ratings change"
    assert "near match" in resolved["note"], resolved["note"]
    assert "we have written-down skill ratings and a prerequisite chain for this topic" not in resolved["note"]


def test_an_exact_authored_topic_keeps_its_plain_note(client, learner):
    r = client.post("/api/v1/topics/resolve", json={"text": "world history"}, headers=learner["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["resolved"]["note"] == "we have written-down skill ratings and a prerequisite chain for this topic"


def test_no_learner_visible_plan_text_names_a_model_provider_or_api(plans):
    """The reason strings are shown to the learner; they must not name the machinery behind them."""
    banned = ("gemini", "openai", "anthropic", "claude", " llm", " api", "ai 1", "ai 2", "ai 3")
    for kind, plan in plans.items():
        text = f" {plan['reason']} {plan.get('leanings_reason', '')} ".lower()
        for word in banned:
            assert word not in text, f"{kind}: the learner-facing reason names {word!r}"
