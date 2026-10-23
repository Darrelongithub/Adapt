"""LAN demo contracts; the real 50-client HTTP harness lives in scripts/load_demo.py."""

from __future__ import annotations

import asyncio

import pytest

from adapt.baseline import flow
from adapt.baseline.games import Trial
from adapt.security.demo_capacity import DemoCapacityMiddleware
from adapt.security.ratelimit import DEFAULT_LIMITS, build_limiter, effective_limits


def test_demo_changes_only_shared_network_budgets():
    scopes = effective_limits(demo_mode=True)
    assert scopes["guest_create"] == (120, 120)
    assert scopes["ip"] == (6000, 6000)
    for scope in DEFAULT_LIMITS.keys() - {"guest_create", "ip"}:
        assert scopes[scope] == DEFAULT_LIMITS[scope]
    limiter = build_limiter(demo_mode=True)
    assert all(limiter.allow("guest_create", "same-ip")[0] for _ in range(50))
    assert effective_limits()["guest_create"] == DEFAULT_LIMITS["guest_create"]


def test_demo_flow_has_only_three_games_and_at_most_ten_tasks():
    trials = []
    games = set()
    for _ in range(11):
        step = flow.next_step(
            seed=123, trials=trials, format_done=False, questions_done=False, demo_mode=True
        )
        if step.kind == "snapshot":
            break
        task = step.task
        games.add(task["game"])
        trials.append(
            Trial(
                task_id=task["id"],
                game=task["game"],
                index=len(trials),
                phase=task["phase"],
                correct=True,
                task=dict(task),
            )
        )
    assert step.kind == "snapshot"
    assert games == {"quick", "vault", "trap"}
    assert len(trials) <= 10
    # No fabricated answers for the full baseline's skipped questions.
    normal = flow.next_step(seed=123, trials=[], format_done=False, questions_done=False)
    assert normal.kind == "format"


def test_demo_session_requires_mode_and_consent(client, settings, monkeypatch):
    from adapt.api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(settings, "mode", "local")
    monkeypatch.setattr(settings, "demo_mode", False)
    assert client.post("/api/v1/demo/session", json={"consent_analytics": True}).status_code == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    assert client.post("/api/v1/demo/session", json={}).status_code == 422
    assert client.post("/api/v1/demo/session", json={"consent_analytics": False}).status_code == 422
    monkeypatch.setattr(settings, "mode", "tunnel")
    assert (
        client.post(
            "/api/v1/demo/session", json={"consent_analytics": True}, headers={"host": "localhost"}
        ).status_code
        == 404
    )


def test_demo_guest_skips_hashing_and_gets_real_cookie(client, settings, services, monkeypatch):
    from adapt.api.routes import auth as auth_routes
    from adapt.security import auth

    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(services.baseline, "settings", settings)
    monkeypatch.setattr(settings, "mode", "local")
    monkeypatch.setattr(settings, "demo_mode", True)

    def forbidden(*args):
        raise AssertionError("guest entry must never hash a password")

    monkeypatch.setattr(auth, "hash_password", forbidden)
    response = client.post("/api/v1/demo/session", json={"consent_analytics": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["onboarding_complete"] and body["demo"]
    assert settings.cookie_name in response.cookies
    view = client.post("/api/v1/baseline", headers={"x-csrf-token": body["csrf_token"]})
    assert view.json()["stage"] == "quick_a"
    assert client.post("/api/v1/baseline").status_code == 403


def test_demo_disables_even_a_configured_gemini_key(settings, monkeypatch):
    from adapt.ai.llm import GeminiLLM

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "gemini_api_key", "test-not-a-real-key")
    assert not GeminiLLM(settings).configured


@pytest.mark.asyncio
async def test_admission_waits_for_full_asgi_cleanup_and_releases_on_failure():
    active = 0
    peak = 0

    async def app(scope, receive, send):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.001)
            if scope.get("fail"):
                raise RuntimeError("simulated failure")
        finally:
            active -= 1

    gate = DemoCapacityMiddleware(app)
    results = await asyncio.gather(
        *[
            gate({"type": "http", "path": "/api/v1/demo/session", "fail": i == 0}, None, None)
            for i in range(50)
        ],
        return_exceptions=True,
    )
    assert peak == 1 and active == 0
    assert sum(isinstance(result, RuntimeError) for result in results) == 1
