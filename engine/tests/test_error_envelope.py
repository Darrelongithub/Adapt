"""Error envelope tests: one shape, and only documented codes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from adapt_engine.clock import FixedClock
from adapt_engine.errors import (
    DOCUMENTED_CODES,
    EMITTED_CODES,
    STATUS_DEFAULT_CODES,
    AdaptError,
    code_for_status,
)
from adapt_engine.main import create_app
from tests.conftest import make_settings

#: `details` joined the envelope in INTEGRATION §6: optional, structured, and
#: the only place a machine-readable value belongs.
ENVELOPE_KEYS = {"code", "message", "request_id", "retryable", "details"}


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

    @app.get("/v1/test-entitled")
    def _entitled_route() -> dict[str, str]:
        raise AdaptError(
            code="not_entitled",
            message="tier_required: deep",
            status_code=403,
            retryable=False,
        )

    @app.get("/v1/test-paused")
    def _paused_route() -> dict[str, str]:
        raise AdaptError(
            code="connection_paused",
            message="connection paused by the host",
            status_code=403,
            retryable=True,
        )

    @app.get("/v1/test-learner-missing")
    def _learner_missing_route() -> dict[str, str]:
        raise AdaptError(
            code="learner_unknown",
            message="no such learner",
            status_code=404,
            retryable=False,
        )

    @app.get("/v1/test-bare-403")
    def _bare_403_route() -> dict[str, str]:
        raise HTTPException(status_code=403, detail="bare forbidden")

    @app.get("/v1/test-bare-401")
    def _bare_401_route() -> dict[str, str]:
        raise HTTPException(status_code=401, detail="unauthorized")

    return app


def _assert_envelope(body: object, *, code: str, retryable: bool) -> None:
    assert isinstance(body, dict)
    assert set(body.keys()) == {"error"}
    err = body["error"]
    assert isinstance(err, dict)
    # `details` is optional: present only when there is something structured to
    # say, absent otherwise (so an error never carries an empty object).
    assert set(err.keys()) <= ENVELOPE_KEYS
    assert set(err.keys()) >= ENVELOPE_KEYS - {"details"}
    if "details" in err:
        assert isinstance(err["details"], dict)
    assert err["code"] == code
    assert isinstance(err["message"], str)
    assert err["message"]
    assert isinstance(err["request_id"], str)
    assert err["request_id"]
    assert err["retryable"] is retryable


def test_404_route_miss_is_route_unknown() -> None:
    """A typo'd URL is route_unknown, not a statement about a learner."""
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/does-not-exist-synthetic")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), code="route_unknown", retryable=False)


def test_404_learner_lookup_is_learner_unknown() -> None:
    """A domain miss says learner_unknown; the two 404 codes never default."""
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/test-learner-missing")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), code="learner_unknown", retryable=False)


def test_bare_403_is_not_entitled() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/test-bare-403")
    assert resp.status_code == 403
    _assert_envelope(resp.json(), code="not_entitled", retryable=False)


def test_connection_paused_is_explicit_only() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/test-paused")
    assert resp.status_code == 403
    _assert_envelope(resp.json(), code="connection_paused", retryable=True)


def test_401_is_always_unauthorized() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/test-bare-401")
    assert resp.status_code == 401
    _assert_envelope(resp.json(), code="unauthorized", retryable=False)
    assert "invalid_api_key" not in resp.text


def test_422_envelope() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.post("/v1/test-validation", json={})
    assert resp.status_code == 422
    _assert_envelope(resp.json(), code="payload_invalid", retryable=False)


def test_500_envelope() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/test-boom")
    assert resp.status_code == 500
    _assert_envelope(resp.json(), code="internal", retryable=True)
    assert "synthetic-failure-marker" not in resp.text


def test_adapt_error_uses_documented_code() -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.get("/v1/test-entitled")
    assert resp.status_code == 403
    _assert_envelope(resp.json(), code="not_entitled", retryable=False)


def test_unmapped_status_is_sentinel_not_invented() -> None:
    """405 has no documented code, so the sentinel is used, not a new name."""
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.post("/v1/health")
    assert resp.status_code == 405
    _assert_envelope(resp.json(), code="unmapped_status", retryable=False)


def test_unmapped_status_logs_warning(capsys: pytest.CaptureFixture[str]) -> None:
    client = TestClient(_test_app(), raise_server_exceptions=False)
    resp = client.post("/v1/health")
    assert resp.status_code == 405
    entries = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    warnings = [e for e in entries if e.get("event") == "error_code_unmapped"]
    assert len(warnings) == 1, "an unmapped status must log exactly one warning"
    assert warnings[0]["level"] == "warning"
    assert warnings[0]["status"] == 405
    assert warnings[0]["error_code"] == "unmapped_status"


def test_no_screaming_snake_codes() -> None:
    """The retired vocabulary must not reappear anywhere in the emitted set."""
    retired = {
        "VALIDATION_ERROR",
        "RATE_LIMITED",
        "NOT_FOUND",
        "FORBIDDEN",
        "INTERNAL_ERROR",
        "SERVICE_UNAVAILABLE",
        "BAD_REQUEST",
        "METHOD_NOT_ALLOWED",
        "ERROR",
    }
    assert not (EMITTED_CODES & retired)
    for code in EMITTED_CODES:
        assert code == code.lower(), f"{code} is not lowercase"


def test_documented_codes_are_snake_case_and_lowercase() -> None:
    assert set(STATUS_DEFAULT_CODES.values()) <= DOCUMENTED_CODES
    assert DOCUMENTED_CODES <= EMITTED_CODES
    for status in STATUS_DEFAULT_CODES:
        assert 400 <= status <= 599, f"{status} is not a client/server error status"
    for code in DOCUMENTED_CODES:
        assert code.islower(), f"{code} is not lowercase"


def test_adapt_error_refuses_invented_code() -> None:
    with pytest.raises(ValueError, match=r"not in INTEGRATION\.md"):
        AdaptError(code="made_up_code", message="x", status_code=400, retryable=False)


def test_code_for_status_maps_known_status() -> None:
    assert code_for_status(429) == "rate_limited"
    assert code_for_status(402) == "quota_exhausted"
    assert code_for_status(503) == "internal"


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
    assert [line for line in lines if line.get("event") == "request"]
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
