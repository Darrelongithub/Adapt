"""Learner sharing consent is enforced at the provider boundary."""

from __future__ import annotations

import httpx

from adapt.ai.llm import GeminiLLM
from adapt.services import build
from adapt.config import Settings


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        gemini_api_key="consent-test-key",
        gemini_base_url="https://example.invalid/v1beta",
        gemini_min_interval_ms=0,
        gemini_max_retries=0,
        gemini_daily_call_budget=10,
        gemini_daily_token_budget=10_000,
    )


def test_service_wiring_reads_the_learner_sharing_choice(services, learner):
    settings = services.settings.model_copy(
        update={
            "gemini_api_key": "consent-test-key",
            "gemini_min_interval_ms": 0,
            "gemini_max_retries": 0,
        }
    )
    built = build(settings, db=services.db, with_resources=False)
    checker = built.ai2.llm.consent_checker

    with services.scope() as session:
        from adapt.db.models import Student

        student = session.get(Student, learner["student_id"])
        assert student is not None
        student.consent_share_with_llm = False
    assert checker(learner["student_id"]) is False

    with services.scope() as session:
        student = session.get(Student, learner["student_id"])
        assert student is not None
        student.consent_share_with_llm = True
    assert checker(learner["student_id"]) is True


async def test_a_learner_without_sharing_consent_never_reaches_the_provider():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "should not arrive"}]}}]},
        )

    llm = GeminiLLM(
        _settings(),
        transport=httpx.MockTransport(handler),
        consent_checker=lambda _student_id: False,
    )
    result = await llm.complete(
        system="reply briefly",
        user="hello",
        purpose="ai2_explain",
        student_id="stu-no-consent",
    )
    await llm.aclose()

    assert result.ok is False
    assert result.status == "consent_required"
    assert seen == []


async def test_a_missing_consent_checker_fails_closed_before_the_provider():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"candidates": []})

    llm = GeminiLLM(_settings(), transport=httpx.MockTransport(handler))
    result = await llm.complete(
        system="reply briefly",
        user="hello",
        purpose="ai2_explain",
        student_id="stu-checker-missing",
    )
    await llm.aclose()

    assert result.status == "consent_required"
    assert seen == []


async def test_a_failed_consent_lookup_fails_closed_before_the_provider():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"candidates": []})

    def broken_lookup(_student_id: str) -> bool:
        raise RuntimeError("database unavailable")

    llm = GeminiLLM(
        _settings(),
        transport=httpx.MockTransport(handler),
        consent_checker=broken_lookup,
    )
    result = await llm.complete(
        system="reply briefly",
        user="hello",
        purpose="ai2_explain",
        student_id="stu-lookup-failed",
    )
    await llm.aclose()

    assert result.status == "consent_required"
    assert seen == []


async def test_a_learner_with_sharing_consent_uses_the_normal_authenticated_path():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "OK"}]}}]},
        )

    llm = GeminiLLM(
        _settings(),
        transport=httpx.MockTransport(handler),
        consent_checker=lambda _student_id: True,
    )
    result = await llm.complete(
        system="reply briefly",
        user="hello",
        purpose="ai2_explain",
        student_id="stu-consented",
    )
    await llm.aclose()

    assert result.ok is True and result.text == "OK"
    assert len(seen) == 1
    assert seen[0].headers["x-goog-api-key"] == "consent-test-key"
