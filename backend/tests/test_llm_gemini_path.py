"""The Gemini wire format and failure semantics, exercised without a key or network.

The application tests deliberately run with ``ADAPT_GEMINI_API_KEY`` unset, which means
the common path -- build a request, read a candidate, decide whether the reply is usable
-- never ran in CI. These tests drive it through ``httpx.MockTransport`` instead. The
properties asserted are the ones a learner feels when a model is configured:

* the system prompt travels in the documented ``systemInstruction`` field;
* a 200 with no candidate text is a *failure* (``blocked``/``empty``), not a successful
  empty answer, and so is a structured reply that is not JSON;
* the daily budget is reserved before the request, so concurrent callers cannot all take
  the last slot, and a failed request gives its reservation back;
* an optional field in the compact response spec is not forced into ``required``;
* a retired model ID gets one retry on the configured fallback model.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from adapt.ai.llm import GeminiLLM, QuotaGuard, _schema_for
from adapt.config import Settings


def _settings(**overrides):
    values = {
        "_env_file": None,
        "gemini_api_key": "wire-test-key",
        "gemini_base_url": "https://example.invalid/v1beta",
        "gemini_min_interval_ms": 0,
        "gemini_max_retries": 0,
        "gemini_daily_call_budget": 50,
        "gemini_daily_token_budget": 1_000_000,
        "ai_context_cache": False,
    }
    values.update(overrides)
    return Settings(**values)


def _ok(text: str = "OK") -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


async def _call(llm: GeminiLLM, **overrides):
    return await llm.complete(
        system="SYSTEM RULES", user="hello", purpose="ai2_explain", **overrides
    )


# --------------------------------------------------------------------------- #
# request shape
# --------------------------------------------------------------------------- #


async def test_the_system_prompt_uses_the_documented_camel_case_field():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok())

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    result = await _call(llm)
    await llm.aclose()

    body = json.loads(seen[0].content)
    assert result.ok is True
    assert body["systemInstruction"]["parts"][0]["text"] == "SYSTEM RULES"
    assert "system_instruction" not in body, "the snake_case proto name is not the JSON field"


async def test_the_safety_policy_is_stated_in_every_request():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok())

    llm = GeminiLLM(
        _settings(gemini_safety_threshold="BLOCK_ONLY_HIGH"),
        transport=httpx.MockTransport(handler),
    )
    await _call(llm)
    await llm.aclose()

    body = json.loads(seen[0].content)
    settings = body["safetySettings"]
    assert {row["threshold"] for row in settings} == {"BLOCK_ONLY_HIGH"}
    assert {row["category"] for row in settings} == {
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    }


def test_a_cached_call_references_the_handle_and_omits_the_system_instruction():
    from types import SimpleNamespace

    llm = GeminiLLM(_settings())
    try:
        plan = llm.plan("ai2_explain")
        body = llm._body(
            plan=plan,
            prefix="STATIC HEAD",
            system="DYNAMIC RULES",
            user="the learner's question",
            history=[],
            temperature=0.2,
            max_output_tokens=100,
            response_spec=None,
            cache_entry=SimpleNamespace(name="cachedContents/abc123"),
        )
    finally:
        asyncio.run(llm.aclose())

    assert body["cachedContent"] == "cachedContents/abc123"
    assert "systemInstruction" not in body
    assert body["contents"][0]["parts"][0]["text"].startswith("DYNAMIC RULES")
    assert body["safetySettings"], "cached and uncached calls share the same safety policy"


# --------------------------------------------------------------------------- #
# a 200 is not automatically an answer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"candidates": []}, "empty"),
        ({"candidates": [{"content": {"parts": []}}]}, "empty"),
        ({"candidates": [{"finishReason": "MAX_TOKENS"}]}, "empty"),
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"),
        ({"candidates": [{"finishReason": "SAFETY"}]}, "blocked"),
        ({"candidates": [{"finishReason": "RECITATION"}]}, "blocked"),
        (
            {
                "candidates": [
                    {
                        "finishReason": "SAFETY",
                        "content": {"parts": [{"text": "a partial answer"}]},
                    }
                ]
            },
            "blocked",
        ),
    ],
)
async def test_a_200_without_usable_text_is_a_failure(payload, status):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is False, payload
    assert result.status == status, payload
    assert result.text == ""
    assert result.error
    # The provider still saw the call, so the audit row keeps its usage.
    assert result.tokens_in > 0


async def test_a_structured_call_that_is_not_json_is_a_failure():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok("this is not json"))

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    result = await _call(llm, response_spec={"answer": "str"})
    await llm.aclose()

    assert result.ok is False
    assert result.status == "parse_error"
    assert result.data is None


async def test_thought_parts_are_not_mistaken_for_the_answer():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "deliberating privately", "thought": True},
                                {"text": "the actual answer"},
                            ]
                        }
                    }
                ]
            },
        )

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is True
    assert result.text == "the actual answer"


async def test_a_non_json_http_body_is_a_typed_failure_not_an_exception():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>gateway</html>")

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is False
    assert result.status == "error"
    assert "JSON" in (result.error or "")


# --------------------------------------------------------------------------- #
# budget reservations
# --------------------------------------------------------------------------- #


async def test_budget_is_reserved_before_the_request_not_recorded_after_it():
    guard = QuotaGuard(daily_calls=3, daily_tokens=1_000, min_interval_ms=0)
    results = await asyncio.gather(*(guard.acquire(10) for _ in range(6)))

    allowed = [ok for ok, _ in results if ok]
    assert len(allowed) == 3, "a check that does not reserve lets every concurrent caller through"
    assert guard.calls == 3 and guard.tokens == 30
    assert guard.headroom()["calls_left"] == 0


async def test_a_failed_request_gives_its_reservation_back():
    guard = QuotaGuard(daily_calls=2, daily_tokens=100, min_interval_ms=0)
    assert (await guard.acquire(40))[0]
    guard.release(tokens=40)
    assert guard.calls == 0 and guard.tokens == 0

    assert (await guard.acquire(40))[0]
    guard.settle(40, 12)
    assert guard.calls == 1 and guard.tokens == 12


async def test_spacing_reserves_a_slot_instead_of_sleeping_in_parallel():
    guard = QuotaGuard(daily_calls=10, daily_tokens=1_000, min_interval_ms=40)
    started = time.monotonic()
    results = await asyncio.gather(*(guard.acquire(1) for _ in range(3)))
    elapsed = time.monotonic() - started

    assert all(ok for ok, _ in results)
    # Three callers, two intervals between them. The old implementation reset `_last`
    # before sleeping, so all three would have returned in ~40 ms.
    assert elapsed >= 0.075, elapsed


async def test_a_network_failure_does_not_consume_the_daily_budget():
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is False and result.status == "error"
    assert llm.guard.calls == 0 and llm.guard.tokens == 0


async def test_a_successful_call_settles_the_reservation_with_reported_tokens():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "OK"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 21,
                    "candidatesTokenCount": 3,
                    "cachedContentTokenCount": 0,
                },
            },
        )

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is True
    assert llm.guard.calls == 1
    assert llm.guard.tokens == 24, "the estimate is replaced by the provider's count"


# --------------------------------------------------------------------------- #
# schema and model resilience
# --------------------------------------------------------------------------- #


def test_optional_fields_are_not_required_and_nested_arrays_keep_their_shape():
    schema = _schema_for(
        {
            "answer": "str",
            "?why_not_more": "str",
            "points": [{"id": "str", "met": "bool", "evidence": "str"}],
            "items": ("array", {"type": "OBJECT"}),
        }
    )
    assert schema is not None
    assert schema["required"] == ["answer", "points", "items"]
    assert schema["properties"]["why_not_more"] == {"type": "STRING"}

    points = schema["properties"]["points"]
    assert points["type"] == "ARRAY"
    assert points["items"]["type"] == "OBJECT"
    assert points["items"]["properties"]["met"] == {"type": "BOOLEAN"}
    assert set(points["items"]["required"]) == {"id", "met", "evidence"}


def test_the_teachback_judge_spec_is_expressible_as_an_array_of_objects():
    from adapt.assessment.teachback import MODEL_SPEC

    schema = _schema_for(MODEL_SPEC)
    assert schema is not None
    points = schema["properties"]["points"]
    assert points["type"] == "ARRAY", "the quoted points must not be typed as a string"
    assert points["items"]["type"] == "OBJECT"
    assert set(schema["required"]) == {"points"}


async def test_a_retired_model_id_retries_once_on_the_configured_fallback():
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        return httpx.Response(200, json=_ok("from the fallback"))

    llm = GeminiLLM(
        _settings(gemini_model_fallback="gemini-fallback"),
        transport=httpx.MockTransport(handler),
    )
    result = await _call(llm)
    assert result.ok is True
    assert result.model == "gemini-fallback"
    assert len(seen) == 2, "one failed attempt, one successful fallback"
    assert "gemini-fallback" in seen[1]

    # The 404 is remembered: the next call goes straight to the fallback instead of
    # paying the same failed round trip again.
    second = await _call(llm)
    await llm.aclose()
    assert second.ok is True and second.model == "gemini-fallback"
    assert len(seen) == 3 and "gemini-fallback" in seen[2]


async def test_a_retired_light_model_retries_once_on_another_configured_class():
    """A new key that cannot see the light model must still be able to answer.

    This is the deployment failure the report described: every light task (including the
    console probe) routes to one pinned ID, so a provider rename or a gated model takes
    down the whole class with no way to recover short of editing the environment.
    """
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "flash-lite" in str(request.url):
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        return httpx.Response(200, json=_ok("from the standard model"))

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    # The console probe is a *light* task, which is what the operator's failing command
    # runs; ``ai2_explain`` would route to the standard model and never see the 404.
    result = await llm.complete(
        system="Reply with the single word OK.",
        user="Connection check.",
        purpose="console_connection_test",
        role="ai2",
        temperature=0.0,
        max_output_tokens=8,
    )
    await llm.aclose()

    assert result.ok is True
    assert result.model == "gemini-flash-latest"
    assert len(seen) == 2, "one failed attempt on the retired ID, one on the next class"
    assert "gemini-flash-lite-latest:" in seen[0] and "gemini-flash-latest:" in seen[1]


async def test_no_retry_is_attempted_when_every_class_is_the_same_model():
    """One model configured everywhere: the provider's own list is all that is left.

    It is asked **once**, and when it has nothing to offer the call fails closed to the
    local tutor like any other provider failure -- no exception, no loop.
    """
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404, json={"error": {"message": "model not found"}})

    llm = GeminiLLM(
        _settings(
            gemini_model_light="only-model",
            gemini_model_standard="only-model",
            gemini_model_deep="only-model",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await _call(llm)
    second = await _call(llm)
    await llm.aclose()

    generates = [row for row in seen if ":generateContent" in row]
    discoveries = [row for row in seen if "/models?" in row]
    assert result.ok is False and result.status == "error"
    assert "404" in (result.error or ""), "the honest cause, not a generic failure"
    assert len(generates) == 2, "one configured attempt per call, and nothing to switch to"
    assert len(discoveries) == 1, "discovery is asked once per process, not once per call"
    assert second.ok is False and llm._discovery_failed is True


async def test_a_refused_cache_is_released_and_the_full_prompt_reserves_once():
    generate_bodies: list[dict] = []
    cache_posts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cache_posts
        if request.url.path.endswith("/cachedContents"):
            cache_posts += 1
            return httpx.Response(200, json={"name": "cachedContents/refused"})
        body = json.loads(request.content)
        generate_bodies.append(body)
        if "cachedContent" in body:
            return httpx.Response(400, json={"error": {"message": "cached content unsupported"}})
        return httpx.Response(200, json=_ok("the full-prompt answer"))

    llm = GeminiLLM(
        _settings(
            ai_context_cache=True,
            gemini_context_cache_min_tokens=1,
            gemini_context_cache_max_tokens=100_000,
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await llm.complete(
        system="DYNAMIC",
        user="hello",
        purpose="ai2_explain",
        cache_prefix="STATIC HEAD",
    )
    await llm.aclose()

    assert cache_posts == 1
    assert result.ok is True, result
    assert len(generate_bodies) == 2
    assert "cachedContent" in generate_bodies[0]
    assert "cachedContent" not in generate_bodies[1]
    assert generate_bodies[1]["systemInstruction"]["parts"][0]["text"].startswith("STATIC HEAD")
    assert llm.guard.calls == 1, "the 400 was released; only the successful retry is charged"
    assert llm.cache.refused(llm.cache.key_for(result.model or "", "STATIC HEAD"))


def test_the_fallback_model_name_is_scrubbed_like_every_other_model_name():
    from adapt.security.secrecy import scrub_public_payload, scrub_text

    settings = _settings(gemini_model_fallback="fallback-model-canary")

    assert "fallback-model-canary" not in scrub_text(
        "the request used fallback-model-canary", settings
    )
    assert scrub_public_payload(
        {"fallback_model": "fallback-model-canary"}, settings
    ) == {}


def test_the_cache_can_measure_the_prefix_it_registers():
    from adapt.ai.context_cache import ContextCache

    cache = ContextCache(min_tokens=1)
    assert cache.estimate("a static head that is used for every learner") > 0


# --------------------------------------------------------------------------- #
# runtime model discovery
#
# The provider retires versioned IDs on its own schedule and leaves them on `/models`
# afterwards -- a key created in October 2026 got `404 ... is no longer available to new
# users` for `gemini-2.5-flash` while that ID was still listed by the same key. So the
# list is a source of *candidates to try*, never an answer about what works.
# --------------------------------------------------------------------------- #

#: The provider's own words for a retired ID. Kept as a constant because the point of
#: one test below is that the whole sentence survives to the console.
_RETIRED_MESSAGE = (
    "This model models/gemini-2.5-flash is no longer available to new users. "
    "Please update your code to use models/gemini-3.8-flash for the latest "
    "features and improvements. We recommend you to use a rolling alias so that "
    "this does not happen again."
)

#: A `/models` body shaped like the real one: the retired IDs, the rolling aliases, the
#: current 3.x text models, and every kind of model this build must never ask for text.
_MODEL_LIST = {
    "models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
        {
            "name": "models/gemini-flash-lite-latest",
            "supportedGenerationMethods": ["generateContent"],
        },
        {"name": "models/gemini-pro-latest", "supportedGenerationMethods": ["generateContent"]},
        {
            "name": "models/gemini-3.8-flash",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        },
        {
            "name": "models/gemini-2.5-flash-preview-tts",
            "supportedGenerationMethods": ["generateContent"],
        },
        {
            "name": "models/gemini-2.5-flash-image",
            "supportedGenerationMethods": ["generateContent"],
        },
        {
            "name": "models/gemini-3-pro-image-preview",
            "supportedGenerationMethods": ["generateContent"],
        },
        {
            "name": "models/nano-banana-pro-preview",
            "supportedGenerationMethods": ["generateContent"],
        },
        {"name": "models/lyria-3.5", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        {
            "name": "models/deep-research-pro-preview-12-2025",
            "supportedGenerationMethods": ["generateContent"],
        },
        {"name": "models/antigravity-1", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemma-4-26b-a4b-it", "supportedGenerationMethods": ["generateContent"]},
    ]
}

#: The three classes pinned to retired IDs -- what a deployment that copied the
#: pre-2026-10 defaults into its environment looks like on a key created after them.
_RETIRED = {
    "gemini_model_light": "gemini-2.5-flash-lite",
    "gemini_model_standard": "gemini-2.5-flash",
    "gemini_model_deep": "gemini-2.5-pro",
}


def _retired_transport(seen: list[str], *, models: dict | None = None):
    """404 on every retired ID, answer everything else, and serve `/models`.

    ``models=None`` (or a falsy value) makes discovery fail with a connect error.
    """
    body = models if models is not None else _MODEL_LIST

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        if request.method == "GET":
            if not body:
                raise httpx.ConnectError("no route to host")
            return httpx.Response(200, json=body)
        if "gemini-2.5" in str(request.url):
            return httpx.Response(404, json={"error": {"message": _RETIRED_MESSAGE}})
        return httpx.Response(200, json=_ok("from a model that still exists"))

    return httpx.MockTransport(handler)


def _generates(seen: list[str]) -> list[str]:
    return [row for row in seen if ":generateContent" in row]


async def test_a_retired_model_is_replaced_by_one_discovered_from_the_provider():
    """The operator's failure: 404 on the configured ID, no configuration left to try.

    Discovery supplies a candidate, the retry succeeds, and the answer is remembered --
    so the next call goes straight to what works instead of paying the 404 again.
    """
    seen: list[str] = []
    llm = GeminiLLM(_settings(**_RETIRED), transport=_retired_transport(seen))
    result = await _call(llm)

    assert result.ok is True, result
    assert result.model == "gemini-flash-latest", "a rolling alias, not another retired ID"
    assert result.tier == "standard"
    # The configured ID, the one other configured class left, then one discovery and
    # one retry on what the provider says it will serve. Nothing after that.
    assert [row.split(" ")[0] for row in seen] == ["POST", "POST", "GET", "POST"], seen
    assert "gemini-2.5-flash:" in seen[0], "the configured ID was tried first"
    assert seen[2].endswith("/models?pageSize=200"), "one discovery, on the same base URL"
    assert "gemini-flash-latest:" in seen[3]

    # The dead IDs are never retried: the next call starts from what answered.
    second = await _call(llm)
    await llm.aclose()

    assert second.ok is True and second.model == "gemini-flash-latest"
    assert len(seen) == 5 and "gemini-2.5" not in seen[4], seen


async def test_discovery_runs_once_per_process_not_once_per_call():
    """A second tier retired by the same provider reuses the first discovery."""
    seen: list[str] = []
    llm = GeminiLLM(_settings(**_RETIRED), transport=_retired_transport(seen))
    await _call(llm)
    deep = await llm.complete(system="s", user="u", purpose="content_items", role="ai1")
    await llm.aclose()

    assert deep.ok is True and deep.model == "gemini-pro-latest", deep
    assert len([row for row in seen if row.startswith("GET")]) == 1, seen
    assert len(_generates(seen)) == 5, seen


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param("connect", id="network-error"),
        pytest.param("status", id="non-200"),
        pytest.param("body", id="not-json"),
    ],
)
async def test_a_discovery_that_fails_degrades_to_the_local_tutor(failure):
    """Discovery is an optimisation on top of a failure; its own failure must not raise."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        if request.method == "GET":
            if failure == "connect":
                raise httpx.ConnectError("no route to host")
            if failure == "body":
                return httpx.Response(200, content=b"<html>gateway</html>")
            return httpx.Response(500, json={"error": {"message": "upstream"}})
        return httpx.Response(404, json={"error": {"message": _RETIRED_MESSAGE}})

    llm = GeminiLLM(_settings(**_RETIRED), transport=httpx.MockTransport(handler))
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is False
    assert result.status == "error", "a typed failure the call site falls back on"
    assert result.error and result.error.startswith("404"), "the 404 is the cause"
    assert llm.discovery_error, "the discovery failure is recorded for the console"
    # Bounded: the configured ID plus at most two replacements. Never a loop.
    assert len(_generates(seen)) <= 3, seen
    assert len([row for row in seen if row.startswith("GET")]) == 1, "asked once"
    assert llm._discovery_failed is True


async def test_the_console_does_not_pay_for_a_discovery_of_its_own():
    """A command that is printing a failure must not add a request to print it.

    `known_candidates` is the console's view: it answers only from what the failed
    call already learned, so `adapt config test` cannot turn one failure into two
    requests -- and it is free exactly when a 404 has run discovery and cached it.
    """
    seen: list[str] = []
    llm = GeminiLLM(_settings(**_RETIRED), transport=_retired_transport(seen))
    assert await llm.known_candidates("standard") == []
    assert seen == [], "nothing had been discovered yet, so nothing was asked"

    await _call(llm)
    assert llm._discovered, "the call itself discovered the list"
    before = len(seen)
    ranked = await llm.known_candidates("standard")
    await llm.aclose()

    assert ranked and ranked[0] == "gemini-flash-latest"
    assert len(seen) == before, "the console reads the cached answer"


def test_discovery_never_offers_a_model_that_cannot_answer_with_text():
    """TTS, image, music, embedding, deep-research and antigravity are not tutors."""
    from adapt.ai.llm import rank_models

    names = [
        str(row["name"]).removeprefix("models/")
        for row in _MODEL_LIST["models"]
        if "generateContent" in (row.get("supportedGenerationMethods") or [])
    ]
    ranked = rank_models(names, "standard")
    blocked = ("tts", "image", "nano-banana", "lyria", "embedding", "deep-research", "antigravity")
    for name in ranked:
        assert not any(marker in name for marker in blocked), name
    # Not a filter that threw everything away either: a rolling alias first, because an
    # alias tracks what the provider serves now, and the current versioned ID with it.
    assert ranked[0] == "gemini-flash-latest"
    assert "gemini-3.8-flash" in ranked
    assert "gemini-flash-lite-latest" in ranked


async def test_a_key_that_can_call_nothing_still_degrades_honestly():
    """A list with no text model in it: fail closed, and say why."""
    seen: list[str] = []
    llm = GeminiLLM(
        _settings(**_RETIRED),
        transport=_retired_transport(
            seen,
            models={
                "models": [
                    {"name": "models/lyria-3.5"},
                    {"name": "models/text-embedding-004"},
                ]
            },
        ),
    )
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is False and result.status == "error"
    assert result.error and result.error.startswith("404")
    assert len(_generates(seen)) <= 3, "bounded, then the call site uses the local tutor"
    assert await llm.candidate_models("standard") == []


async def test_a_pinned_role_model_also_discovers_a_replacement():
    """A pin is not a dead end: `ADAPT_GEMINI_MODEL_AI2` on a retired ID recovers too.

    Before this the chain only knew the operator's fallback and the tier defaults, and
    a role pin bypasses the tiers -- so a pin on a retired ID had nothing to try.
    """
    seen: list[str] = []
    llm = GeminiLLM(
        _settings(gemini_model_ai2="gemini-2.5-flash", **_RETIRED),
        transport=_retired_transport(seen),
    )
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is True, result
    assert result.model == "gemini-flash-latest"
    assert "gemini-2.5-flash:" in seen[0]
    assert any(row.endswith("/models?pageSize=200") for row in seen), seen


async def test_a_pinned_role_model_prefers_a_configured_class_over_discovery():
    """With the shipped defaults a stale pin never costs a discovery request."""
    seen: list[str] = []
    llm = GeminiLLM(
        _settings(gemini_model_ai2="gemini-2.5-flash"),
        transport=_retired_transport(seen),
    )
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is True and result.model == "gemini-flash-latest"
    assert [row.split(" ")[0] for row in seen] == ["POST", "POST"], seen


async def test_a_task_pin_also_discovers_a_replacement():
    """`ADAPT_GEMINI_TASK_MODELS` is the most specific pin, and the most easily stale."""
    seen: list[str] = []
    llm = GeminiLLM(
        _settings(gemini_task_models={"ai2_explain": "gemini-2.5-flash"}, **_RETIRED),
        transport=_retired_transport(seen),
    )
    result = await _call(llm)
    await llm.aclose()

    assert result.ok is True and result.model == "gemini-flash-latest"


async def test_the_failure_keeps_the_whole_provider_recommendation():
    """200 characters cut the provider's own advice off mid-sentence."""
    seen: list[str] = []
    llm = GeminiLLM(_settings(**_RETIRED), transport=_retired_transport(seen, models={}))
    result = await _call(llm)
    await llm.aclose()

    assert result.error and "gemini-3.8-flash" in result.error, "the advice survives intact"
    assert result.error and result.error.endswith("does not happen again."), result.error


async def test_the_operator_can_list_the_models_the_key_can_call():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.url.params.get("pageSize") == "200"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-one",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/embedding-only",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                    {"name": "models/gemini-two"},
                    {"nope": "no name"},
                ]
            },
        )

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    models = await llm.list_models()
    await llm.aclose()

    assert models == ["gemini-one", "gemini-two"]


async def test_the_model_list_is_empty_when_no_key_is_configured():
    llm = GeminiLLM(_settings(gemini_api_key=None))
    assert await llm.list_models() == []
    await llm.aclose()
