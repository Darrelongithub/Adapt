"""Error envelope tests: every handler returns the one shape."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from adapt_engine.clock import FixedClock
from adapt_engine.main import create_app
from tests.conftest import make_settings


class _EchoBody(BaseModel):
    name: str


def _test_app() -> FastAPI:
    app = create_app(
        make_settings(),
        clock=FixedClock(datetime(2026, 11, 17, 0, 0, 0, tzinfo=UTC)),
        db_check=lambda: True,
        redis_check=lambda: True,
    )

    @app.post("/v1/test-validation")
    def _validation_route(body: _EchoBody) -> dict[str, str]:
        return {"name": body.name}

    @app.get("/v1/test-boom")
    def _boom_route() -> dict[str, str]:
        raise RuntimeError("synthetic-failure-marker")

    return app


def _assert_envelope(body: object, *, code: str, retryable: bool) -> None:
    assert isinstance(body, dict)
    assert set(body.keys()) == {"error"}
    err = body["error"]
    assert isinstance(err, dict)
    assert set(err.keys()) == {"code", "message", "request_id", "retryable"}
    assert err["code"] == code
    assert isinstance(err["message"], str)
    assert err["message"]
    assert isinstance(err["request_id"], str)
    assert err["request_id"]
    assert err["retryable"] is retryable


def test_404_envelope() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/does-not-exist-synthetic")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), code="NOT_FOUND", retryable=False)


def test_422_envelope() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.post("/v1/test-validation", json={})
    assert resp.status_code == 422
    _assert_envelope(resp.json(), code="VALIDATION_ERROR", retryable=False)


def test_500_envelope() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/test-boom")
    assert resp.status_code == 500
    _assert_envelope(resp.json(), code="INTERNAL_ERROR", retryable=True)
    assert "synthetic-failure-marker" not in resp.text


def test_request_id_header_matches_envelope() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/does-not-exist-synthetic")
    assert resp.headers.get("X-Request-Id")
    assert resp.json()["error"]["request_id"] == resp.headers["X-Request-Id"]


def test_success_has_no_error_envelope() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert "error" not in resp.json()


def test_logs_are_allowlist_only(capsys: pytest.CaptureFixture[str]) -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/health?synthetic_marker=do-not-log-this")
    assert resp.status_code == 200
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.strip().startswith("{")]
    assert lines, "expected at least one request log line"
    allowed = {
        "event",
        "level",
        "request_id",
        "tenant_id",
        "route",
        "method",
        "status",
        "duration_ms",
        "error_code",
    }
    for entry in lines:
        keys = set(entry.keys())
        assert keys <= allowed, f"log keys {keys} exceed allowlist"
        for forbidden in (
            "body",
            "key",
            "learner_id",
            "email",
            "provider",
            "query_string",
            "authorization",
        ):
            assert forbidden not in entry
    assert "do-not-log-this" not in out
