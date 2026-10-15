"""What the three model-side optimisations actually are, and the lines they must not cross.

The brief asked for three changes to how this app talks to a model:

* **fewer calls** -- merge the questions that need the same reading of the same text into one
  prompt that answers all of them in one JSON object;
* **cheaper calls** -- stop resending the static head of every prompt (instructions, rubric,
  subject catalogue) on every request;
* **the right model** -- a class of work per task, chosen from a table rather than by habit.

Each of those is only worth having if it is *checkable*, so this file is the floor under them.
The claims, in the order the brief made them:

* a merged call answers every question the separate calls answered, and the merged answer
  cannot be accepted where the separate ones were refused -- the validators are the *same
  functions*, not a second implementation
  (``test_the_merged_call_cannot_accept_what_the_two_calls_refused``);
* a part that fails falls back on its own, and a chain can never be the thing that breaks a
  request (``test_a_chain_step_that_says_nothing_falls_back_on_its_own``,
  ``test_a_chain_is_an_optimisation_and_may_never_be_the_failure``);
* a merge never *adds* a call, and a topic the authored tables already cover costs none
  (``test_a_topic_with_authored_answers_is_never_asked_about``);
* every static head is non-empty, unique, bigger than a request's tail, identical for every
  learner, and never carries learner text
  (``test_no_static_head_can_carry_learner_text``);
* the cache is content-addressed, bounded, honest about what the provider will not take, and
  every refusal is a *reason* rather than an exception
  (``test_a_cache_that_is_switched_off_or_too_small_falls_back_to_a_full_prompt``);
* every ``purpose=`` literal in the source is in the routing table, every entry says which
  class it is and why, an operator can pin a model without editing code, and model names never
  reach a public payload (``test_every_purpose_literal_in_the_source_is_in_the_table`` and the
  rest of the third section).

Run: ``pytest -q tests/test_ai_efficiency.py``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from adapt.ai import chain as CH
from adapt.ai import context_cache as CC
from adapt.ai import routing as RT
from adapt.ai import static_context as SC
from adapt.ai.llm import LLMResult, estimate_tokens, usage_totals
from adapt.subjects import placement as PL
from adapt.subjects.taxonomy import known_skill_ids

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "src" / "adapt"
REPO = BACKEND.parent


class _StubLLM:
    """The client surface the chains use, with the answer scripted by the test.

    ``configured`` is a real attribute because every call site asks before spending a call: a
    chain run against an unconfigured client must fall back *without* reaching a model, and
    that is one of the properties tested below.
    """

    configured = True

    def __init__(self, data=None, *, ok=True, status="ok", raises=None, settings=None) -> None:
        self.data = data
        self.ok = ok
        self.status = status
        self.raises = raises
        self.settings = settings
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return LLMResult(
            ok=self.ok,
            status=self.status,
            data=self.data,
            text="",
            model="stub",
            tier=RT.policy(str(kwargs.get("purpose") or "")).tier,
        )


def _settings(**overrides):
    from types import SimpleNamespace

    base = dict(
        gemini_model_light="model-light",
        gemini_model_standard="model-standard",
        gemini_model_deep="model-deep",
        gemini_model_ai1=None,
        gemini_model_ai2=None,
        gemini_task_models={},
        ai_context_cache=True,
        ai_single_call=True,
        gemini_context_cache_min_tokens=1024,
        rate_limit_multiplier=1.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


#: A whole fused answer: the metadata (family, title, level, confidence), the scores (the
#: ratings) and the rationale -- which is the shape the brief asked for in one call.
HEALTHY_PLACEMENT = {
    "family": "maths",
    "topic_title": "Tides",
    "level_hint": "secondary",
    "confidence": 0.86,
    "ratings": [
        {"skill": "maths.number_sense", "rating": 3},
        {"skill": "maths.symbol_fluency", "rating": 2},
        {"skill": "maths.graph_sense", "rating": 0},
    ],
    "reason": "the questions need number sense before anything periodic",
}

#: Free text that matches no authored topic and (with no bank handed in) no curriculum topic:
#: the one case where both questions are genuinely open, and the case the merge exists for.
UNMATCHED_TEXT = "why do the tides come in twice a day where I live"


# --------------------------------------------------------------------------- #
# 1: one call, several answers
# --------------------------------------------------------------------------- #


def test_the_subject_round_asks_the_model_once_and_gets_both_answers():
    """Two decisions, one reading of one piece of text, one call.

    Placement (which of five families) and leanings (which of that family's skills the topic
    needs) both require understanding the same few words, and the second question used to be
    asked *with the first answer already in its prompt* -- which is the definition of a question
    that belongs in the same call.
    """
    llm = _StubLLM(HEALTHY_PLACEMENT)
    resolved, leanings, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=llm)

    assert len(llm.calls) == 1, f"the merge did not happen: {len(llm.calls)} calls"
    assert llm.calls[0]["purpose"] == "subject_placement"
    assert "cache_prefix" in llm.calls[0] and llm.calls[0]["cache_prefix"], (
        "the merged call sent its static head as part of the request instead of as a cache prefix"
    )
    assert meta["calls"] == 1 and meta["hops_saved"] == 1
    assert meta["chain"] == "subject_placement"
    assert sorted(meta["replaces"]) == ["subject_leanings", "topic_resolve"]
    assert meta["sources"] == {"placement": "model", "ratings": "model"}

    # Both halves are used, and both are validated: the family is one of the closed list, and
    # every skill of *that family* is either rated or recorded as unrated.
    assert resolved.source == "model" and resolved.family == "maths"
    assert resolved.title == "Tides" and resolved.level_hint == "secondary"
    assert leanings.source == "model" and leanings.family == "maths"
    assert set(leanings.ratings) == set(known_skill_ids("maths"))


def test_the_merged_call_cannot_accept_what_the_two_calls_refused():
    """The same validators, so merging cannot loosen anything.

    Three refusals the sequential path already made, replayed against the merged payload:

    * an invented family is not a family -- the deterministic placement stands;
    * a skill id that is not in the chosen family is dropped, not trusted;
    * a flat answer (every skill rated the same) is not a judgement, so the labelled generic
      profile is used instead.

    A chain that accepted any of these would be a quiet relaxation of the rules, which is why
    the merged prompt calls the *same* functions rather than restating them.
    """
    invented = _StubLLM({**HEALTHY_PLACEMENT, "family": "astrology"})
    resolved, _, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=invented)
    assert resolved.family == "generic", "an invented family was accepted"
    assert resolved.source != "model", resolved.source
    assert meta["sources"]["placement"] == "fallback"
    assert "family/level check" in " ".join(meta["notes"]["placement"])
    assert meta["calls"] == 1, "the refusal is the validator's, not a second call's"

    off_list = _StubLLM(
        {
            **HEALTHY_PLACEMENT,
            "ratings": [
                {"skill": "history.empathy", "rating": 3},
                {"skill": "maths.number_sense", "rating": 1},
            ],
        }
    )
    _, leanings, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=off_list)
    assert "history.empathy" not in leanings.ratings, "a skill from another family was accepted"
    assert any("not a skill in taxonomy" in note for note in meta["notes"]["ratings"])

    # Every skill of the family rated the same: the validator fills unrated skills with 0 but
    # refuses a genuinely flat answer, so the "flat" case has to be honest about being flat.
    flat = _StubLLM(
        {
            **HEALTHY_PLACEMENT,
            "ratings": [{"skill": sid, "rating": 2} for sid in known_skill_ids("maths")],
        }
    )
    resolved, leanings, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=flat)
    assert leanings.source == "generic_fallback", "a flat answer was treated as a judgement"
    assert meta["sources"]["ratings"] == "fallback"
    # ...and the placement half of the same reply is still used: the steps fail independently,
    # which is what makes a merged call safe rather than all-or-nothing.
    assert resolved.family == "maths" and meta["sources"]["placement"] == "model"


def test_a_chain_step_that_says_nothing_falls_back_on_its_own():
    """A partial answer is a partial answer, not a failed call."""
    only_placement = _StubLLM(
        {k: v for k, v in HEALTHY_PLACEMENT.items() if k in {"family", "topic_title", "level_hint", "confidence"}}
    )
    resolved, leanings, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=only_placement)
    assert resolved.family == "maths", "the half that was answered was thrown away"
    assert leanings.source == "generic_fallback" and meta["sources"]["ratings"] == "fallback"
    assert meta["notes"]["ratings"], "the missing half has to be explained, not silently filled in"
    assert "rating" in " ".join(meta["notes"]["ratings"]).lower()


def test_a_chain_is_an_optimisation_and_may_never_be_the_failure():
    """A raising client, an error result and a dead provider all end in the local answer."""
    boom = _StubLLM(raises=RuntimeError("provider exploded"))
    resolved, leanings, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=boom)
    assert resolved.source != "model" and leanings.source == "generic_fallback"
    assert meta["status"] == "error" and meta["calls"] == 1
    assert "chain error" in " ".join(meta["notes"]["placement"])

    refused = _StubLLM(None, ok=False, status="quota")
    resolved, leanings, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=refused)
    assert leanings.source == "generic_fallback"
    assert meta["sources"] == {"placement": "fallback", "ratings": "fallback"}, meta["sources"]
    assert all("no model answer" in note for notes in meta["notes"].values() for note in notes)


def test_switching_the_merge_off_gives_back_the_two_calls_it_replaced():
    """``ADAPT_AI_SINGLE_CALL=0`` is how the merge is measured rather than believed."""
    llm = _StubLLM(HEALTHY_PLACEMENT, settings=_settings(ai_single_call=False))
    resolved, leanings, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=llm)
    assert len(llm.calls) == 2, "the sequential path is one call per question"
    assert sorted(call["purpose"] for call in llm.calls) == ["subject_leanings", "topic_resolve"]
    assert meta["chain"] is None and meta["calls"] == 2
    assert resolved.family == "maths" and leanings.family == "maths"
    assert leanings.source != "generic_fallback", "the sequential path's answer still has to be used"


def test_a_topic_with_authored_answers_is_never_asked_about():
    """The strongest form of the promise: some placements cost nothing at all.

    The round's own tables cover a set of named topics with written-down ratings. Asking a
    model about one would replace a written rating with a sample, and it would spend a call to
    do it -- so code's answer stands, and the meta says which half came from where.
    """
    llm = _StubLLM(HEALTHY_PLACEMENT)
    resolved, leanings, meta = PL.place_and_rate("calculus", llm=llm)
    assert resolved.source == "authored" and leanings.source == "authored"
    assert llm.calls == [], f"a model was asked about an authored topic: {[c['purpose'] for c in llm.calls]}"
    assert meta["calls"] == 0
    assert meta["sources"] == {"placement": "authored", "ratings": "authored"}
    assert "nothing else was asked" in meta["note"]


def test_a_caller_that_only_wants_the_topic_does_not_buy_the_ratings():
    """``need_ratings=False``: one question asked, one answered, nothing bought and discarded.

    The counterbalanced-lesson path resolves a topic and never looks at a skill profile, so a
    merged call that always answered both would be paying for the same waste in a new place.
    """
    llm = _StubLLM(HEALTHY_PLACEMENT)
    resolved, leanings, meta = PL.place_and_rate(UNMATCHED_TEXT, llm=llm, need_ratings=False)
    assert resolved.family == "maths", "the placement answer was still used"
    assert len(llm.calls) == 1, f"the ratings were asked for anyway: {[c['purpose'] for c in llm.calls]}"
    assert llm.calls[0]["purpose"] == "topic_resolve", (
        "with no ratings to answer, the smaller single-question prompt is the right one"
    )
    assert leanings.source != "model", "ratings nobody asked for must not look like a model's"
    assert meta["calls"] == 1

    # And the service's own topic-only path goes through it rather than duplicating the route.
    source = (SRC / "subjects" / "service.py").read_text()
    assert "need_ratings=False" in source, "the service resolves topics and buys leanings anyway"


def test_the_endpoint_asks_once_and_sends_the_head_as_a_cache_prefix(client, learner, services):
    """The same claim, over HTTP, with the real router, the real service and a stub model.

    A function-level test can only show that ``place_and_rate`` merges. This one walks the
    request a learner actually makes — ``POST /api/v1/topics/resolve`` — and checks the two
    things the brief is about, at the boundary where they are observable:

    * one call left the process, for a purpose that names the merge;
    * the prompt that left it carried the *dynamic tail* only, with the static head passed as a
      cache prefix (so a provider that supports caching can serve it, and one that does not
      still gets a prompt whose shared half is a genuine prefix).
    """
    stub = _StubLLM(HEALTHY_PLACEMENT)
    subjects = services.subjects
    previous = subjects.llm
    subjects.llm = stub
    try:
        response = client.post(
            "/api/v1/topics/resolve",
            json={"text": UNMATCHED_TEXT},
            headers=learner["headers"],
        )
    finally:
        subjects.llm = previous
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(stub.calls) == 1, f"the endpoint made {len(stub.calls)} model calls"
    call = stub.calls[0]
    assert call["purpose"] == "subject_placement"
    assert call["cache_prefix"] == SC.block("placement"), "the head did not travel as a prefix"
    assert call["system"] not in (None, ""), "the merged prompt has no instructions at all"

    # The response says how it was produced, and the body carries no learner text in the
    # prefix: the tagged text is in the request, the head is cacheable text.
    assert body["placement"]["chain"] == "subject_placement"
    assert body["placement"]["calls"] == 1
    assert sorted(body["placement"]["replaces"]) == ["subject_leanings", "topic_resolve"]
    assert "<topic_name>" in call["user"] or "<learner_text>" in call["user"], (
        "the learner's words have to be tagged as data in the merged prompt too"
    )


def test_a_merged_schema_may_not_have_two_owners_for_one_key():
    """The failure this guards is silent: a second step's answer overwriting the first's."""
    step = CH.Step(
        key="a",
        ask="a",
        spec={"same": "str"},
        parse=lambda data, ctx: (data.get("same"), []),
        fallback=lambda ctx: None,
    )
    other = CH.Step(
        key="b",
        ask="b",
        spec={"same": "str"},
        parse=lambda data, ctx: (data.get("same"), []),
        fallback=lambda ctx: None,
    )
    with pytest.raises(ValueError, match="two owners"):
        CH.merge_specs([step, other])


def test_the_chain_registry_and_the_routing_table_agree():
    """A merged purpose says which calls it replaced, and both halves are registered."""
    summary = PL.chain_summary()
    policy = RT.policy(summary["purpose"])
    assert policy.joins, "the routing table does not record the purposes this call replaced"
    assert sorted(policy.joins) == sorted(summary["replaces"])
    for purpose in policy.joins:
        assert RT.is_registered(purpose), f"{purpose} is not in the task table any more"
    assert policy.calls == 1, "a merged call that still needs two calls is not merged"

    # Every step asks for something, names the keys it wants, and validates the answer.
    for step in PL.PLACEMENT_CHAIN.steps:
        assert step.ask and step.spec, step
        assert callable(step.parse) and callable(step.fallback), step


def test_no_registered_task_is_allowed_to_need_two_calls():
    """``calls`` above one would be a call site nobody finished merging."""
    for task, policy in RT.TASKS.items():
        assert policy.calls == 1, f"{task} is declared as needing {policy.calls} calls"
        assert policy.note.strip(), f"{task} has no justification written down"


# --------------------------------------------------------------------------- #
# 2: the static head, paid for once
# --------------------------------------------------------------------------- #


def test_every_static_block_is_static_non_empty_and_unique():
    """Ten heads, each a page of prose, none of them a copy of another."""
    blocks = SC.all_blocks()
    assert len(blocks) >= 10, f"only {len(blocks)} static heads are registered: {sorted(blocks)}"
    seen: dict[str, str] = {}
    for name, text in blocks.items():
        assert text.strip(), f"{name} is empty"
        assert len(text) > 200, f"{name} is too small to be a prompt head"
        assert text == SC.block(name), "block() is not stable across calls"
        if text in seen:
            pytest.fail(f"{name} is a copy of {seen[text]}: two names, one head, one cache entry")
        seen[text] = name
    with pytest.raises(KeyError):
        SC.block("no_such_block")


def test_no_static_head_can_carry_learner_text():
    """A cache entry is shared by every learner, so this is a privacy rule, not a perf one."""
    for name, text in SC.all_blocks().items():
        CC.assert_static(text)  # raises StaticPrefixError if a populated wrapper is present

    # The positive control: the guard has to refuse the thing it exists to refuse, or the loop
    # above proves nothing.
    with pytest.raises(CC.StaticPrefixError):
        CC.assert_static("<student_text>my answer, in my words</student_text>")
    with pytest.raises(CC.StaticPrefixError):
        CC.assert_static("here is the text: <learner_text>I don't get it</learner_text> and what follows")

    # ...and a mention of the protocol is not a leak: an instruction may legitimately describe
    # where the learner's text will arrive.
    assert CC.assert_static("the learner's answer arrives between <student_text> tags")


def test_every_cache_prefix_in_the_source_names_a_real_block():
    """A typo in a call site would silently send no head at all."""
    used: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        for match in re.finditer(
            r'SC\.block\(\s*"([a-z_]+)"\s*\)|static_context\.block\(\s*"([a-z_]+)"\s*\)',
            path.read_text(),
        ):
            used.add(match.group(1) or match.group(2))
    assert used, "no call site passes a static head"
    unknown = sorted(used - set(SC.names()))
    assert not unknown, f"call sites name blocks that do not exist: {unknown}"


def test_the_heads_that_clear_the_cache_floor_are_named_and_the_rest_are_reported():
    """Honesty about what explicit caching can and cannot do on a free tier.

    A provider will not register a prompt below its documented minimum, and that minimum is
    bigger than several of these heads. Those heads still help -- a static-first prompt gets the
    provider's own implicit prefix caching -- but the operator has to be able to see which is
    which, and the answer has to come from one place.
    """
    from adapt.ai.context_cache import ContextCache

    cache = ContextCache(min_tokens=1024)
    sizes = SC.sizes()
    above = {name for name, row in sizes.items() if row["est_tokens"] >= 1024}
    assert above, "no static head is big enough to be cached explicitly: the feature is inert"

    for name, row in sizes.items():
        ok, reason = cache.eligible(SC.block(name))
        assert ok == (name in above), f"{name}: eligible() disagrees with the floor ({reason})"
        assert row["est_tokens"] == estimate_tokens([SC.block(name)])


def test_the_cache_key_is_content_addressed_bounded_and_forgets_refusals():
    """The book-keeping half, without a network: what is held, when it expires, what it saved."""
    from adapt.ai.context_cache import ContextCache

    cache = ContextCache(ttl_s=1800, min_tokens=8, max_entries=2)
    head = "instructions that never change\n" * 4
    key = cache.key_for("model-a", head)
    assert key == cache.key_for("model-a", head), "the same head must hash to the same entry"
    assert key != cache.key_for("model-b", head), "a cache is only valid for the model that made it"
    assert key != cache.key_for("model-a", head + " and one more sentence")

    ok, reason = cache.eligible(head)
    assert ok, reason
    cache.remember(key, model="model-a", name="cachedContents/1", tokens=64)
    assert cache.lookup(key) is not None
    cache.note_reuse(key, 512)

    # Bounded: registering more entries than the cap evicts, it does not grow.
    for n in range(4):
        other = cache.key_for("model-a", head + str(n))
        cache.remember(other, model="model-a", name=f"cachedContents/{n + 2}", tokens=64)
    assert len(cache.entries) <= 2, cache.entries.keys()

    # A provider that refused this prefix is not asked again on every single call.
    cache.refuse("deadbeef", why="400 cannot cache this content")
    assert cache.refused("deadbeef")
    report = cache.report()
    assert report["floor"] == 8
    assert report["reused_input_tokens"] == 512
    assert report["refused_prefixes"] == 1
    assert "model-a" not in repr(report), "the operator report is provider- and model-neutral"


def test_a_cache_that_is_switched_off_or_too_small_falls_back_to_a_full_prompt():
    """Every refusal is a *reason*, and none of them is an exception."""
    from adapt.ai.context_cache import ContextCache

    off = ContextCache(enabled=False)
    ok, why = off.eligible(SC.block("tutor"))
    assert not ok and "off" in why

    small = ContextCache(min_tokens=10_000)
    ok, why = small.eligible(SC.block("tutor"))
    assert not ok and "floor" in why

    unreasonable = ContextCache(min_tokens=8, max_tokens=16)
    ok, why = unreasonable.eligible(SC.block("tutor"))
    assert not ok and "ceiling" in why

    with pytest.raises(CC.StaticPrefixError):
        off.key_for("model-a", "<learner_answer>mine</learner_answer>")


def test_the_docs_carry_the_same_table_the_code_does():
    """The cost note is written from the code, so a stale table is a failing test."""
    doc = REPO / "docs" / "EFFICIENCY.md"
    assert doc.exists(), "docs/EFFICIENCY.md is where the measurements are written down"
    text = doc.read_text()
    for name in SC.names():
        assert name in text, f"{name} is missing from docs/EFFICIENCY.md"
    for task in RT.TASKS:
        assert task in text, f"{task} is missing from docs/EFFICIENCY.md"
    assert str(len(SC.names())) in text, "the number of static heads is not stated in the docs"


# --------------------------------------------------------------------------- #
# 3: the right model for the job
# --------------------------------------------------------------------------- #


def test_every_purpose_literal_in_the_source_is_in_the_table():
    """AST, not grep: a call site cannot invent a purpose the table has never heard of.

    The same guard ``test_llm_plumbing`` puts on the redaction tuple, applied to the routing
    table -- because the table is only the answer to "which model does this work" if every call
    site is *in* it.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "purpose":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.setdefault(value.value, []).append(str(path.relative_to(SRC)))
    assert found, "no call site passes a purpose: the routing table cannot be doing anything"
    unknown = sorted(name for name in found if not RT.is_registered(name))
    assert not unknown, f"purposes with no class of work: {unknown} (from {[found[u][0] for u in unknown]})"

    merged = {joined for policy in RT.TASKS.values() for joined in policy.joins}
    for purpose in RT.TASKS:
        assert purpose in found or purpose in merged, (
            f"{purpose} is in the table but nothing calls it and nothing merges it"
        )


def test_the_three_classes_are_configured_models_and_every_task_says_why_it_is_in_one():
    settings = _settings()
    assert RT.model_for_tier("light", settings) == "model-light"
    assert RT.model_for_tier("standard", settings) == "model-standard"
    assert RT.model_for_tier("deep", settings) == "model-deep"

    for task, policy in RT.TASKS.items():
        assert policy.tier in RT.TIERS, f"{task} is in no class"
        plan = RT.plan(task, settings)
        assert plan.model == RT.model_for_tier(policy.tier, settings)
        assert plan.registered and not plan.pinned

    # The classes are not decoration: the high-frequency work that is checked against a rule
    # has to be on the light class, or the table has not changed what the app spends.
    for task in (
        "topic_resolve",
        "subject_leanings",
        "subject_placement",
        "ai2_curate",
        "ai1_retention_check",
        "ai2_feedback",
        "ai3_narrate",
    ):
        assert RT.policy(task).tier == "light", f"{task} runs often and is validated in code"
    # ...and the open-ended authoring is not.
    for task in ("ai2_generate_practice", "ai2_scene_spec", "ai1_propose"):
        assert RT.policy(task).tier == "deep", f"{task} writes material a learner will read"


def test_a_task_pin_beats_a_role_pin_and_an_explicit_model_beats_both():
    """Precedence, in the order an operator would expect to be able to override it."""
    pinned = _settings(gemini_model_ai2="model-role", gemini_task_models={"ai2_explain": "model-task"})
    assert RT.model_for_purpose("ai2_explain", pinned, role="ai2") == "model-task"
    assert RT.model_for_purpose("ai2_curate", pinned, role="ai2") == "model-role"
    # A role pin does not leak into the other role: ai1's tasks follow their own class.
    assert RT.model_for_purpose("ai2_curate", pinned, role="ai1") == RT.model_for_tier(
        RT.policy("ai2_curate").tier, pinned
    )
    # An explicit argument beats every pin (a call site whose size genuinely differs).
    assert RT.model_for_purpose("ai2_explain", pinned, role="ai2", model="model-explicit") == "model-explicit"

    plan = RT.plan("ai2_explain", pinned, role="ai2")
    assert plan.pinned and plan.model == "model-task"
    # ...and the *class* is still reported: pinning a model does not change what the work is.
    assert plan.tier == "standard"

    # A malformed pin is a configuration error, not a silent no-op: the settings layer raises
    # before a single call is made (see tests/test_config_secrecy.py for the Settings half).
    assert RT.pinned_models(_settings()) == {}


def test_a_call_records_the_class_it_ran_as_and_the_tokens_the_cache_saved():
    """The audit row is what makes all of this measurable afterwards, not just configured."""

    class Row:
        """The columns ``usage_totals`` reads, and no database."""

        def __init__(self, **kw):
            self.redacted_fields = 0
            self.__dict__.update(kw)

    rows = [
        Row(purpose="ai2_explain", status="ok", tokens_in=900, tokens_out=200, tier="standard", cached_tokens=700),
        Row(purpose="subject_placement", status="ok", tokens_in=500, tokens_out=120, tier="light", cached_tokens=0),
        Row(purpose="ai2_teachback", status="quota", tokens_in=0, tokens_out=0, tier=None, cached_tokens=None),
    ]
    totals = usage_totals(rows)
    assert totals["by_class"] == {"standard": 1, "light": 1, "unclassified": 1}
    assert totals["tokens_in_served_from_static_context"] == 700
    assert totals["tokens_in"] == 1400 and totals["calls"] == 3


def test_the_public_view_of_the_table_carries_no_model_name():
    """§3: classes of work are publishable, provider model names are not."""
    table = RT.public_table()
    assert len(table) == len(RT.TASKS)
    blob = repr(table)
    for tier in RT.TIERS:
        assert RT.model_for_tier(tier, _settings()).startswith("model-")
        assert RT.model_for_tier(tier, _settings()) not in blob
    for row in table:
        assert set(row) == {"task", "class", "one_call", "calls", "joins", "shared_context", "why"}
        assert "model" not in row


def test_an_unregistered_purpose_is_standard_work_and_is_reported():
    """The default has to be the *middle* class, and the operator has to hear about it."""
    policy = RT.policy("something_nobody_registered")
    assert policy is RT.DEFAULT_POLICY
    assert policy.tier == "standard"
    report = RT.report(_settings(), unknown=("something_nobody_registered",))
    assert report["unknown_purposes"] == ["something_nobody_registered"]
    assert set(report["classes"]) == set(RT.TIERS)
    for tier, row in report["classes"].items():
        assert row["model"] and row["meaning"] and row["tasks"], tier


def test_the_client_reports_the_class_the_cache_and_the_prefixless_tasks():
    """``GeminiLLM.available()`` is the console's view: it has to answer all three questions."""
    import asyncio

    from adapt.ai.llm import GeminiLLM

    st = _settings(gemini_api_key="", gemini_base_url="http://127.0.0.1:1")
    st.gemini_timeout_s = 1.0
    st.gemini_daily_call_budget = 10
    st.gemini_daily_token_budget = 1000
    st.gemini_min_interval_ms = 0
    st.gemini_max_retries = 0
    st.gemini_context_cache_ttl_s = 60
    st.gemini_context_cache_min_tokens = 8
    st.gemini_context_cache_max_entries = 4
    st.gemini_context_cache_max_tokens = 100_000
    client = GeminiLLM(st)
    try:
        info = client.available()
        assert info["configured"] is False
        assert set(info["routing"]["classes"]) == set(RT.TIERS)
        assert "floor" in info["context_reuse"] and info["context_reuse"]["enabled"] is True
        plan = client.plan("ai2_explain")
        assert plan.tier == "standard" and plan.model
    finally:
        asyncio.run(client.aclose())


def test_the_days_spend_is_readable_by_class_and_by_what_the_cache_saved(services):
    """``AppServices.quotas()`` is the operator's ledger: three questions, one call.

    It reads the *rows* (``llm_calls.tier`` / ``cached_tokens``) rather than recomputing today's
    answer from the routing table, which is the difference between "what did we spend on" and
    "what would we spend on if this afternoon's configuration had been in force all morning".
    The grouped queries are the part that can break silently, so this exercises them against a
    real database rather than a fake one.
    """
    report = services.quotas()
    assert report["day"] and report["calls_left"] >= 0
    assert set(report["budget"]) == {"calls", "tokens", "models"}
    models = report["budget"]["models"]
    assert set(models["classes"]) == set(RT.TIERS), "the console cannot say which model is which class"
    assert set(models["pins"]) >= set(), "the pins have to be reported even when there are none"
    assert isinstance(report["by_purpose"], dict)
    assert isinstance(report["by_class"], list)
    for row in report["by_class"]:
        assert set(row) == {"class", "calls", "tokens", "served_from_static_context"}
        assert isinstance(row["served_from_static_context"], int)
    reuse = report["context_reuse"]
    assert reuse is None or "reused_input_tokens" in reuse, reuse


def test_the_head_is_the_bulk_of_the_prompt_it_is_removed_from():
    """Why the split is worth doing, in the units the provider bills in.

    Every dynamic tail in this app is a question, an item and an answer -- tens of tokens. The
    heads are pages. The ratio is the whole argument for item 2, so it is measured here rather
    than asserted about: each registered head has to be bigger than a plausible request tail.
    """
    tails = [
        "why do I have to flip the second fraction?",
        "Item: 3/4 ÷ 1/2. The learner answered 3/2 and said: I flipped the first one instead.",
        "Topic query: adding fractions. Learner mastery level: secondary.",
    ]
    biggest_tail = max(estimate_tokens([tail]) for tail in tails)
    for name in SC.names():
        head_tokens = estimate_tokens([SC.block(name)])
        assert head_tokens > biggest_tail, (
            f"{name} is smaller than a typical request tail ({head_tokens} vs {biggest_tail} tokens): "
            "it is not a head, it is part of the question"
        )
    assert estimate_tokens([SC.block("tutor")]) > 500, "the tutor head is a page of instructions"
