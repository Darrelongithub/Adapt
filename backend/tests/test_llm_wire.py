"""The Gemini wire, exercised without a key.

CI pops ``ADAPT_GEMINI_API_KEY`` (``conftest.py``), so every model path in the app is tested
through a stub or not at all -- which is the correct product default and the reason five broken
call sites and a wrong request-field name lived here unnoticed. A stub ``complete()`` cannot
catch a bug *in* ``complete()``.

These tests drive the real client: a fake key, an :class:`httpx.MockTransport` that returns
recorded provider payloads, and assertions about the JSON that leaves the process and the
typed result that comes back. Everything here runs offline.

What is pinned, and why each one is worth a test:

* ``systemInstruction`` in camelCase on the path that does not use a context cache. The API
  ignores an unknown key rather than rejecting it, so the snake_case spelling meant a call
  whose system prompt -- the rubric, the guardrails, the "answer in JSON only" line -- was
  silently dropped while the request succeeded. A field name is not observable from a stub.
* A 200 with no text is a failure (``ok=False``, ``status="blocked"``/``"empty"``), because a
  tutor that "answered" with an empty string is worse than the local answer, and every caller
  that only checks ``ok`` used to inherit it.
* The daily budget is reserved before the request and settled after it, so N concurrent
  callers cannot all pass a check against one untouched counter.
* The refusal paths that must not look like successes: unparseable JSON against a schema, a
  model id the provider does not know, a cache the provider won't serve.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from adapt.ai import llm as L
from adapt.config import Settings

MODEL = "gemini-2.5-flash-lite"


def make_settings(**kw: Any) -> Settings:
    """A configured-but-fake deployment: a key that is not one, no pacing, no retries."""
    base: dict[str, Any] = {
        "gemini_api_key": "test-key-not-a-secret",
        "gemini_model_light": MODEL,
        "gemini_model_standard": MODEL,
        "gemini_model_deep": MODEL,
        "gemini_min_interval_ms": 0,
        "gemini_max_retries": 0,
        "gemini_timeout_s": 5.0,
        "ai_context_cache": False,
        "data_dir": kw.pop("data_dir", "/tmp/adapt-wire-test"),
        "db_encryption": "field-only",
    }
    base.update(kw)
    return Settings(**base)


class Recorder:
    """The transport's memory: every request the client sent, parsed."""

    def __init__(self, replies: list[tuple[int, Any]]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content.decode())
            except json.JSONDecodeError:
                body = request.content.decode()
        self.requests.append(
            {"method": request.method, "url": str(request.url), "headers": dict(request.headers), "json": body}
        )
        status, payload = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        if isinstance(payload, (str, bytes)):
            return httpx.Response(status, content=payload if isinstance(payload, bytes) else payload.encode(),
                                 headers={"content-type": "application/json"})
        return httpx.Response(status, json=payload)

    @property
    def body(self) -> dict[str, Any]:
        assert self.requests, "the client never reached the wire"
        return self.requests[0]["json"] or {}

    @property
    def last_body(self) -> dict[str, Any]:
        return self.requests[-1]["json"] or {}


def answer(text: str, **extra: Any) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {
        "candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 12},
    }
    payload.update(extra)
    return (200, payload)


def client_for(recorder: Recorder, *, consent: bool = True, **settings_kw: Any) -> L.GeminiLLM:
    """A client whose only external dependency is the fake transport.

    ``consent`` defaults to True because this file is about the wire; the consent boundary has
    its own suite (``test_llm_consent.py``) and a client built without a checker fails closed,
    which would test that instead.
    """
    settings = make_settings(**settings_kw)
    transport = httpx.MockTransport(recorder.handler)
    return L.GeminiLLM(settings, transport=transport, consent_checker=(lambda _sid: consent) if consent is not None else None)


async def call(llm: L.GeminiLLM, **kw: Any) -> L.LLMResult:
    args: dict[str, Any] = {"system": "Teach briefly.", "user": "What is a fraction?", "purpose": "ai2_explain"}
    args.update(kw)
    return await llm.complete(**args)


# --------------------------------------------------------------------------- #
# 1. the request that leaves the process
# --------------------------------------------------------------------------- #


async def test_the_system_prompt_travels_under_the_field_the_api_reads():
    """``systemInstruction``, not ``system_instruction``.

    The provider accepts a body containing an unrecognised key, answers, and never mentions
    that the key was ignored. So the bug this guards is not a crash: it is every rubric,
    guardrail and output contract in the system prompt quietly not applying, on the path that
    is taken whenever context caching is off or the prefix is under the cache floor.
    """
    recorder = Recorder([answer("A fraction is part of a whole.")])
    llm = client_for(recorder)
    try:
        result = await call(llm, system="Only ever answer in two sentences.")
        assert result.ok, result.error
        body = recorder.body
        assert "systemInstruction" in body, "the camelCase field the REST API reads is missing"
        assert "system_instruction" not in body, (
            "snake_case system_instruction is not a field of this API: the provider ignores it "
            "and the system prompt is dropped while the call still reports success"
        )
        assert "Only ever answer in two sentences." in body["systemInstruction"]["parts"][0]["text"]
    finally:
        await llm.aclose()


async def test_every_field_the_body_carries_is_spelled_the_way_the_api_spells_it():
    """One test for the whole casing rule, because the mistake is a habit, not a typo."""
    recorder = Recorder([answer("ok")])
    llm = client_for(recorder)
    try:
        await call(
            llm,
            response_spec={"answer": "str"},
            max_output_tokens=120,
            temperature=0.2,
            cache_prefix="",
        )
        body = recorder.body
        snake = sorted(k for k in body if "_" in k)
        assert not snake, f"snake_case keys in a generateContent body: {snake}"
        config = body["generationConfig"]
        assert {"temperature", "maxOutputTokens", "responseMimeType", "responseSchema"} <= set(config)
        assert config["maxOutputTokens"] == 120
    finally:
        await llm.aclose()


async def test_the_safety_filter_is_named_in_the_request_instead_of_inherited():
    """Sending the thresholds we rely on, rather than whatever the model defaults to.

    An unset ``safetySettings`` is not "no filtering" and not "the old filtering" -- it is
    whatever the provider decided the default is for that model generation, which for the 2.5
    family is documented as off. The pair of bugs that mattered was that plus an empty
    candidate list reading as a success; this pins the first half so the second half has a
    stable cause to report.
    """
    recorder = Recorder([answer("ok")])
    llm = client_for(recorder, gemini_safety_mode="balanced")
    try:
        await call(llm)
        settings = recorder.body["safetySettings"]
        assert {s["category"] for s in settings} == {
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
        }
        assert {s["threshold"] for s in settings} == {"BLOCK_ONLY_HIGH"}
    finally:
        await llm.aclose()


async def test_the_safety_mode_an_operator_chooses_is_the_mode_the_request_carries():
    recorder = Recorder([answer("ok"), answer("ok")])
    llm = client_for(recorder, gemini_safety_mode="provider")
    try:
        await call(llm)
        assert "safetySettings" not in recorder.body, "`provider` means send nothing, not send less"
    finally:
        await llm.aclose()

    recorder = Recorder([answer("ok")])
    llm = client_for(recorder, gemini_safety_mode="strict")
    try:
        await call(llm)
        assert {s["threshold"] for s in recorder.body["safetySettings"]} == {"BLOCK_MEDIUM_AND_ABOVE"}
    finally:
        await llm.aclose()


def test_a_safety_mode_that_is_not_a_mode_fails_at_start_up():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ADAPT_GEMINI_SAFETY_MODE"):
        make_settings(gemini_safety_mode="banlanced")  # a typo must not become "provider" silently


# --------------------------------------------------------------------------- #
# 2. a response with nothing in it is not a success
# --------------------------------------------------------------------------- #


async def test_a_safety_blocked_reply_is_a_refusal_the_caller_falls_back_on():
    recorder = Recorder(
        [
            (
                200,
                {
                    "promptFeedback": {"blockReason": "SAFETY"},
                    "candidates": [{"finishReason": "SAFETY"}],
                    "usageMetadata": {"promptTokenCount": 30},
                },
            )
        ]
    )
    llm = client_for(recorder)
    try:
        result = await call(llm)
        assert result.ok is False, "a blocked prompt used to arrive as ok=True with text=''"
        assert result.status == "blocked"
        assert result.text == ""
        assert result.data is None
        assert llm.stats["blocked"] == 1
        # The prompt was read, so the day is charged for it even though the answer is empty.
        assert llm.guard.headroom()["calls_used"] == 1
    finally:
        await llm.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {"candidates": []},
        {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]},
        {"candidates": [{"content": {"parts": [{"text": "   "}]}, "finishReason": "STOP"}]},
        {"candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "MAX_TOKENS"}]},
    ],
)
async def test_an_empty_answer_never_reaches_a_learner_as_an_answer(payload):
    """Every spelling of "the model said nothing" lands on the same typed failure.

    ``ok=True`` with an empty string is the state a caller has to defend against by
    accident: AI 2 checks ``res.data``, the tutor checks ``res.text``, the narrator checks
    both, and anything that only checks ``ok`` prints a blank lesson. Making the emptiness
    part of ``ok`` means there is one thing to check.
    """
    recorder = Recorder([(200, {**payload, "usageMetadata": {"promptTokenCount": 25}})])
    llm = client_for(recorder)
    try:
        result = await call(llm)
        assert result.ok is False
        assert result.status in ("empty", "blocked")
        assert result.text.strip() == ""
    finally:
        await llm.aclose()


async def test_a_reply_that_ignores_the_requested_shape_is_a_parse_failure_not_a_text():
    """``response_spec`` asks for JSON; JSON-shaped prose is not the answer to that ask."""
    recorder = Recorder([answer("I think a fraction is a part of a whole, roughly.")] + [answer('{"answer": "x"}')])
    llm = client_for(recorder)
    try:
        result = await call(llm, response_spec={"answer": "str"})
        assert result.ok is False and result.status == "parse_error"
        assert result.data is None
        assert result.text, "the raw reply is kept: the live-check harness saves it for fixtures"
        assert llm.stats["parse_error"] == 1
    finally:
        await llm.aclose()


async def test_a_200_whose_body_is_not_json_falls_back_instead_of_raising():
    """A proxy or captive portal answering 200 with HTML must not 500 a lesson."""
    recorder = Recorder([(200, "<html><body>gateway</body></html>")])
    llm = client_for(recorder)
    try:
        result = await call(llm)
        assert result.ok is False and result.status == "error"
    finally:
        await llm.aclose()


async def test_the_learner_still_gets_an_answer_when_the_model_replies_with_nothing(services, learner):
    """The end of the wire is a call site: with a blank model answer, the tutor answers.

    This is the test that makes the ``ok=False`` change worth something -- the client alone
    only promises that a caller *can* notice. AI 2 is the caller the learner reads.
    """
    recorder = Recorder([(200, {"candidates": [], "usageMetadata": {"promptTokenCount": 20}})])
    llm = client_for(recorder)
    original = services.ai2.llm
    services.ai2.llm = llm
    try:
        assert llm.configured, "the call sites ask before spending a call"
        reply = await services.ai2.explain(learner["student_id"], "Why does flipping the second fraction work?")
        assert reply.text.strip(), "a blocked or empty model reply must not print an empty answer"
        assert reply.provider == "local", reply.provider
        assert recorder.requests, "the learner's question did reach the model the client fakes"
        assert llm.stats["empty"] + llm.stats["blocked"] == 1
    finally:
        services.ai2.llm = original
        await llm.aclose()


# --------------------------------------------------------------------------- #
# 3. the daily budget is spent before the request, not after
# --------------------------------------------------------------------------- #


async def test_concurrent_callers_cannot_all_be_told_there_is_room():
    """The race the guard used to have: check the counter, then add to it later.

    Ten learners arriving at once with a budget of three used to pass the check ten times,
    because nothing was written until a response came back -- and the ten requests that
    arrived afterwards are what a free-tier lockout is made of.
    """
    guard = L.QuotaGuard(daily_calls=3, daily_tokens=100_000, min_interval_ms=0)
    results = await asyncio.gather(*[asyncio.to_thread(guard.reserve, 500) for _ in range(10)])
    allowed = [r for r, _why in results if r is not None]
    assert len(allowed) == 3, "reserve() must hand out exactly as many slices as the budget has"
    assert guard.headroom()["calls_left"] == 0


async def test_reserved_tokens_are_visible_while_a_call_is_in_flight():
    guard = L.QuotaGuard(daily_calls=10, daily_tokens=10_000, min_interval_ms=0)
    reservation, _ = guard.reserve(2_000)
    assert guard.headroom()["tokens_used"] == 2_000
    assert guard.headroom()["tokens_reserved_in_flight"] == 2_000
    # The provider's real number replaces the estimate; it does not add to it.
    guard.settle(reservation, tokens=600)
    assert guard.headroom()["tokens_used"] == 600
    assert guard.headroom()["tokens_reserved_in_flight"] == 0
    assert guard.headroom()["calls_used"] == 1


async def test_a_refund_returns_both_halves_of_the_reservation():
    """An attempt that never reached a model must not eat a learner's day."""
    guard = L.QuotaGuard(daily_calls=2, daily_tokens=10_000, min_interval_ms=0)
    reservation, _ = guard.reserve(3_000)
    guard.refund(reservation)
    assert guard.headroom()["calls_used"] == 0
    assert guard.headroom()["tokens_used"] == 0
    assert guard.headroom()["calls_left"] == 2, "a second caller can still be served"


async def test_the_reservation_is_settled_from_the_providers_own_usage():
    recorder = Recorder([answer("A fraction is part of a whole.")])
    llm = client_for(recorder)
    try:
        result = await call(llm)
        assert result.ok
        headroom = llm.guard.headroom()
        assert headroom["calls_used"] == 1
        assert headroom["tokens_used"] == 40 + 12, "the answer's real usage, not the estimate"
        assert headroom["tokens_reserved_in_flight"] == 0
    finally:
        await llm.aclose()


async def test_a_request_the_provider_never_served_is_handed_back():
    """A 5xx and a refused connection cost the day nothing; a 429 costs one call."""
    recorder = Recorder([(503, {"error": {"message": "upstream busy"}})])
    llm = client_for(recorder)
    try:
        result = await call(llm)
        assert result.ok is False
        assert llm.guard.headroom()["calls_used"] == 0, "an upstream failure was not a model call"
    finally:
        await llm.aclose()

    recorder = Recorder([(429, {"error": {"message": "Resource has been exhausted"}})])
    llm = client_for(recorder)
    try:
        result = await call(llm)
        assert result.ok is False and result.status == "quota"
        assert llm.guard.headroom()["calls_used"] == 1, "the limiter saw this call; counting it is the point"
        assert llm.guard.headroom()["tokens_used"] == 0, "and it generated nothing to charge tokens for"
    finally:
        await llm.aclose()


async def test_pacing_gives_each_caller_its_own_slot_instead_of_a_shared_glance():
    """``_last`` used to be stamped before the sleep, so concurrent callers skipped it."""
    guard = L.QuotaGuard(daily_calls=10, daily_tokens=1_000_000, min_interval_ms=40)
    reserved = [guard.reserve(10)[0] for _ in range(4)]
    slots = [r.slot for r in reserved]
    assert slots == sorted(slots) and len(set(slots)) == 4, "each caller must be queued behind the last"
    gaps = [b - a for a, b in zip(slots, slots[1:])]
    assert all(gap >= 0.039 for gap in gaps), f"inter-call spacing lost: {gaps}"


# --------------------------------------------------------------------------- #
# 4/5/6. the rest of the recommendations
# --------------------------------------------------------------------------- #


def test_an_optional_field_is_offered_without_being_demanded():
    """``"str?"`` in a compact spec = present in ``properties``, absent from ``required``.

    A field the prompt only asks for "if you can" and the schema requires anyway is a field
    the model fills with something invented -- or the honest, shorter answer is rejected by
    the app's own validator and the learner gets the template.
    """
    schema = L._schema_for({"answer": "str", "hint": "str?", "steps": ("array?", {"type": "STRING"})})
    assert set(schema["properties"]) == {"answer", "hint", "steps"}
    assert schema["required"] == ["answer"]
    assert schema["properties"]["steps"] == {"type": "ARRAY", "items": {"type": "STRING"}}
    assert schema["properties"]["answer"]["type"] == "STRING"


def test_a_field_that_is_required_stays_required_so_the_contract_is_not_just_looser():
    schema = L._schema_for({"question": "str", "rationale": "str"})
    assert schema["required"] == ["question", "rationale"]


def test_an_array_of_objects_written_in_the_compact_shape_is_an_array_of_objects():
    """The teach-back rubric writes ``points`` as ``[{...}]`` and reads back a list of points.

    Translating that as a string asked the model for a string, then rejected every answer for
    not being a list -- a call path that could never succeed, and could never fail loudly
    either, because a rejected judgement just keeps the rule grade.
    """
    from adapt.assessment.teachback import MODEL_SPEC

    schema = L._schema_for(MODEL_SPEC)
    points = schema["properties"]["points"]
    assert points["type"] == "ARRAY", "the compact `[{}]` spelling must not become a STRING"
    assert points["items"]["type"] == "OBJECT"
    assert set(points["items"]["properties"]) == {"id", "met", "evidence"}
    assert points["items"]["required"] == ["id", "met", "evidence"]
    assert schema["required"] == ["points"], "the judgement's own score/feedback may be omitted"


def test_a_nested_provider_shaped_fragment_is_passed_through_not_stringified():
    schema = L._schema_for(
        {"picks": ("array", {"type": "OBJECT", "properties": {"title": {"type": "STRING"}}, "required": ["title"]})}
    )
    assert schema["properties"]["picks"]["items"]["properties"]["title"] == {"type": "STRING"}


# --------------------------------------------------------------------------- #
# 7. the cache retry and the model id, which are both "the provider said no" cases
# --------------------------------------------------------------------------- #


async def test_a_refused_cache_costs_a_request_not_two_and_silences_the_cache():
    """400 with ``cachedContent`` → forget the cache, resend the whole prompt, charge once.

    The retry has to be paid for -- it is a second real request -- and the request that was
    refused before generating anything must not be. Both halves matter: the first is honest
    accounting, the second is why a learner's budget survives a cache outage.
    """
    recorder = Recorder(
        [
            # the cachedContents create, in the shape the provider answers a create with
            (200, {"name": "cachedContents/0001", "model": f"models/{MODEL}", "usageMetadata": {"totalTokenCount": 1200}}),
            (400, {"error": {"message": "Cached content is not supported for this model"}}),
            answer("A fraction is part of a whole."),
        ]
    )
    llm = client_for(recorder, ai_context_cache=True, gemini_context_cache_min_tokens=8)
    prefix = "SYSTEM RULES " * 40  # over the (tiny) floor these tests set
    try:
        result = await call(llm, cache_prefix=prefix)
        assert result.ok, result.error
        assert len(recorder.requests) == 3, "one cache create, one refused call, one full resend"
        assert recorder.requests[0]["url"].endswith("/cachedContents")
        assert "systemInstruction" in recorder.requests[0]["json"], (
            "the create path is the same API and the same spelling"
        )
        second, third = recorder.requests[1]["json"], recorder.requests[2]["json"]
        assert "cachedContent" in second and "systemInstruction" not in second
        assert "cachedContent" not in third
        assert prefix.strip() in third["systemInstruction"]["parts"][0]["text"], (
            "the resend carries the head it stopped referencing"
        )
        assert llm.guard.headroom()["calls_used"] == 1, "the refused attempt is not charged; the served one is"
        assert llm.cache.stats.refused, "the prefix is remembered as uncacheable, not retried per call"
    finally:
        await llm.aclose()


async def test_a_model_id_the_provider_does_not_know_is_reported_and_remembered():
    """No runtime probe means a rename reads as "the model is off today", forever.

    The client's job is to fail closed to the local tutor *and* to say which name failed, in
    a shape an operator can act on: ``adapt status`` prints this, and the next call in the
    same process does not have to rediscover it.
    """
    recorder = Recorder([(404, {"error": {"message": "models/gemini-2.5-flash is not found for API"}})])
    llm = client_for(recorder)
    try:
        result = await call(llm)
        assert result.ok is False
        assert result.status == "model_unavailable"
        assert MODEL in llm.unavailable_models
        available = llm.available()
        assert available["models_refused"], "the report has to carry it, not just the object"
    finally:
        await llm.aclose()


async def test_a_configured_alternate_takes_over_after_the_first_refusal():
    recorder = Recorder(
        [(404, {"error": {"message": "model is not found"}}), answer("A fraction is part of a whole.")]
    )
    llm = client_for(recorder, gemini_model_alternates={MODEL: "gemini-flash-latest"})
    try:
        first = await call(llm)
        assert first.ok is False and first.status == "model_unavailable"
        second = await call(llm)
        assert second.ok, second.error
        assert second.model == "gemini-flash-latest"
        assert "/models/gemini-flash-latest:generateContent" in recorder.requests[1]["url"]
        assert llm.stats["model_fallback"] == 1
    finally:
        await llm.aclose()


async def test_no_alternate_configured_means_no_invented_model_name():
    """Silently calling a model this file has never heard of is worse than falling back."""
    llm = client_for(Recorder([answer("ok")]))
    try:
        assert llm._alternate_for(MODEL) == ""
        assert llm._avoid_dead_models(llm.plan("ai2_explain")).model == MODEL, (
            "a refused model with no substitute stays refused: the failure the learner sees "
            "is the local tutor, and the report says why"
        )
    finally:
        await llm.aclose()


async def test_the_model_probe_says_which_configured_names_the_provider_answers_to():
    recorder = Recorder(
        [
            (
                200,
                {
                    "models": [
                        {"name": f"models/{MODEL}"},
                        {"name": "models/some-other-model"},
                    ]
                },
            )
        ]
    )
    llm = client_for(recorder)
    try:
        report = await llm.list_models()
        assert report["checked"] is True and report["count"] == 2
        assert all(report["recognised"].values()), report
    finally:
        await llm.aclose()

    recorder = Recorder([(401, {"error": {"message": "API key not valid"}})])
    llm = client_for(recorder)
    try:
        report = await llm.list_models()
        assert report["checked"] is False and "401" in report["reason"]
    finally:
        await llm.aclose()


# --------------------------------------------------------------------------- #
# and the plumbing the previous round's tests started guarding
# --------------------------------------------------------------------------- #


async def test_a_prefix_that_clears_the_floor_is_registered_and_then_reused():
    """The whole cache round trip, because "caching is off" and "caching crashes" look alike.

    The client used to fail here on *success*: the provider answered with a cache handle and
    the code reading it called a method that did not exist, so the exception -- caught by
    every call site's broad ``except`` -- looked exactly like "no model configured". Two
    things are asserted because of that: that a registered prefix is referenced rather than
    resent, and that a client which cannot register one still answers the question.
    """
    prefix = "GUARDRAILS AND RUBRIC " * 60
    recorder = Recorder(
        [
            (200, {"name": "cachedContents/0001", "model": f"models/{MODEL}"}),
            (
                200,
                {
                    "candidates": [{"content": {"parts": [{"text": "Because division is multiplication upside down."}], "role": "model"}, "finishReason": "STOP"}],
                    "usageMetadata": {
                        "promptTokenCount": 300,
                        "candidatesTokenCount": 9,
                        "cachedContentTokenCount": 1200,
                    },
                },
            ),
            answer("Because division is multiplication upside down.", usageMetadata={"promptTokenCount": 300, "candidatesTokenCount": 9, "cachedContentTokenCount": 1200}),
        ]
    )
    llm = client_for(recorder, ai_context_cache=True, gemini_context_cache_min_tokens=64)
    try:
        first = await call(llm, cache_prefix=prefix)
        assert first.ok, first.error
        assert recorder.requests[0]["url"].endswith("/cachedContents")
        assert first.context_cached is True and first.cached_tokens == 1200
        assert "cachedContent" in recorder.requests[1]["json"]
        assert "systemInstruction" not in recorder.requests[1]["json"], (
            "a call that references a cache must not also send the text it references"
        )
        second = await call(llm, cache_prefix=prefix)
        assert second.ok, second.error
        assert len(recorder.requests) == 3, "the second call reuses the entry instead of creating another"
        assert llm.stats["cache_hits"] >= 1
        assert first.model == second.model
    finally:
        await llm.aclose()


async def test_a_cache_that_cannot_be_registered_still_answers_the_question():
    """A broken optimisation may cost a resent prompt and nothing else."""
    recorder = Recorder([(500, {"error": {"message": "cachedContents unavailable"}}), answer("Half of two is one.")])
    llm = client_for(recorder, ai_context_cache=True, gemini_context_cache_min_tokens=64)
    try:
        result = await call(llm, cache_prefix="RULES " * 60)
        assert result.ok, "the create failed and the answer still arrived"
        assert result.context_cached is False
        body = recorder.last_body
        assert "systemInstruction" in body and "RULES" in body["systemInstruction"]["parts"][0]["text"]
    finally:
        await llm.aclose()


async def test_quota_exhaustion_returns_a_typed_refusal_without_a_request():
    recorder = Recorder([answer("ok")])
    llm = client_for(recorder, gemini_daily_call_budget=0)
    try:
        result = await call(llm)
        assert result.ok is False and result.status == "quota"
        assert not recorder.requests, "the budget is checked before anything leaves the process"
    finally:
        await llm.aclose()


async def test_the_audit_row_records_the_status_a_fallback_was_forced_by():
    """`llm_calls.status` is how an operator distinguishes "no key" from "blank answer"."""
    recorder = Recorder([(200, {"candidates": [], "usageMetadata": {"promptTokenCount": 11}})])
    llm = client_for(recorder)
    try:
        result = await call(llm)
        row = llm.audit_row(purpose="ai2_explain", role="ai2", result=result, student_id="stu_x", prompt_chars=30)
        assert row["status"] == "empty"
        assert row["tokens_in"] == 11, "the prompt was spent, and the audit says so"
    finally:
        await llm.aclose()
