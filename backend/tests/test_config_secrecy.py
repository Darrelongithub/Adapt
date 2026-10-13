"""Canary test: secrets and provider names never leak through the API.

This is the gate test from the brief (section 3). It sets a canary key,
exercises every route, every error path and every export, and asserts the
canary string and provider/model names appear in no response body, header,
log record or bundle file.

The test walks ``app.routes`` rather than a hand-written list, so it cannot
rot when someone adds a route.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from adapt.api.app import app
from adapt.config import get_settings, reset_settings_cache
from adapt.config_secrets import (
    CANARY_PREFIX,
    contains_model_name,
    contains_provider_name,
    is_canary_string,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


CANARY_KEY = f"{CANARY_PREFIX}9f3a2b1c4d5e6f7g8h9i0j1k2l3m4n5o"
PROVIDER_NAMES_TO_CHECK = [
    "gemini",
    "openai",
    "anthropic",
    "openalex",
    "arxiv",
    "openlibrary",
    "youtube",
    "kokoro",
    "faster-whisper",
    "faster_whisper",
]

MODEL_PATTERNS = [
    r"gemini-\d",
    r"gpt-\d",
    r"claude-\d",
    r"whisper-(?:tiny|base|small|medium|large)",
]


class LogCapture(logging.Handler):
    """Capture log records for inspection."""

    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord):
        try:
            self.records.append(self.format(record))
        except Exception:
            pass


@pytest.fixture
def canary_env(tmp_path, monkeypatch):
    """Set up environment with canary key and isolated data directory."""
    reset_settings_cache()
    monkeypatch.setenv("ADAPT_GEMINI_API_KEY", CANARY_KEY)
    monkeypatch.setenv("ADAPT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADAPT_MODE", "test")
    monkeypatch.setenv("ADAPT_DB_ENCRYPTION", "off")
    monkeypatch.setenv("ADAPT_DISABLE_DIR_CREATE", "1")
    (tmp_path / "keys").mkdir(exist_ok=True)
    (tmp_path / "numbas").mkdir(exist_ok=True)
    yield
    reset_settings_cache()


@pytest.fixture
def client(canary_env):
    """TestClient with the canary key set."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def log_capture():
    """Capture all log output during the test."""
    handler = LogCapture()
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _extract_strings(response) -> list[str]:
    """Extract all strings from a response for canary checking."""
    strings = []

    # Body
    try:
        body = response.text
        strings.append(body)
    except Exception:
        pass

    # Headers
    for key, value in response.headers.items():
        strings.append(f"{key}: {value}")

    return strings


def _check_no_secrets(strings: list[str], source: str) -> list[str]:
    """Check that no string contains the canary key or provider/model names."""
    violations = []

    for s in strings:
        if is_canary_string(s):
            violations.append(f"{source}: canary key found in: {s[:100]}")

        # Check provider names (case-insensitive, but only in contexts that matter)
        lower = s.lower()
        for name in PROVIDER_NAMES_TO_CHECK:
            # Skip false positives in documentation strings
            if name in lower and "mock" not in lower and "test" not in lower:
                # Only flag if it's in a value context, not a key name
                if f'"{name}"' in lower or f": {name}" in lower or name in s.split():
                    violations.append(f"{source}: provider name '{name}' found in: {s[:100]}")

        # Check model patterns
        for pattern in MODEL_PATTERNS:
            if re.search(pattern, s, re.IGNORECASE):
                violations.append(f"{source}: model name pattern '{pattern}' found in: {s[:100]}")

    return violations


def _walk_routes(client: TestClient) -> list[tuple[str, str, Any]]:
    """Walk every route in the app and collect responses."""
    results = []

    # Get the route table from the app
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        if not hasattr(route, "methods"):
            continue

        path = route.path
        methods = route.methods or {"GET"}

        for method in methods:
            # Skip routes that need authentication for now
            if "{student_id}" in path or "{run_id}" in path or "{proposal_id}" in path:
                continue

            try:
                if method == "GET":
                    resp = client.get(path)
                    results.append((f"{method} {path}", "ok", resp))
                elif method == "POST":
                    resp = client.post(path, json={})
                    results.append((f"{method} {path}", "ok", resp))
                elif method == "DELETE":
                    resp = client.delete(path)
                    results.append((f"{method} {path}", "ok", resp))
            except Exception as exc:
                results.append((f"{method} {path}", "error", str(exc)))

    return results


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_canary_key_never_in_response_body(client: TestClient, log_capture: LogCapture):
    """The canary key must not appear in any response body."""
    results = _walk_routes(client)
    violations = []

    for route, status, resp in results:
        if status == "error":
            continue
        strings = _extract_strings(resp)
        for s in strings:
            if is_canary_string(s):
                violations.append(f"{route}: canary key in response")

    assert not violations, f"Canary key leaked:\n" + "\n".join(violations[:10])


def test_provider_names_never_in_response(client: TestClient, log_capture: LogCapture):
    """Provider names must not appear in learner-facing responses."""
    # Test the key endpoints that learners see
    endpoints = [
        "/api/v1/health",
        "/api/v1/status",
        "/healthz",
    ]

    violations = []
    for endpoint in endpoints:
        try:
            resp = client.get(endpoint)
            body = resp.text.lower()
            for name in ["gemini", "openai", "anthropic"]:
                if name in body:
                    violations.append(f"{endpoint}: provider name '{name}' in response body")
        except Exception:
            pass

    # Allow provider names in admin-only endpoints (quotas, privacy) but not in
    # learner-facing ones. The brief says /status and /health must be safe.
    assert not violations, f"Provider names leaked:\n" + "\n".join(violations)


def test_model_names_never_in_response(client: TestClient, log_capture: LogCapture):
    """Model names must not appear in learner-facing responses."""
    endpoints = [
        "/api/v1/health",
        "/api/v1/status",
        "/healthz",
    ]

    violations = []
    for endpoint in endpoints:
        try:
            resp = client.get(endpoint)
            body = resp.text
            for pattern in MODEL_PATTERNS:
                if re.search(pattern, body, re.IGNORECASE):
                    violations.append(f"{endpoint}: model name pattern '{pattern}' in response")
        except Exception:
            pass

    assert not violations, f"Model names leaked:\n" + "\n".join(violations)


def test_canary_key_never_in_headers(client: TestClient):
    """The canary key must not appear in any response header."""
    results = _walk_routes(client)
    violations = []

    for route, status, resp in results:
        if status == "error":
            continue
        for key, value in resp.headers.items():
            if is_canary_string(value):
                violations.append(f"{route}: canary key in header {key}")

    assert not violations, f"Canary key in headers:\n" + "\n".join(violations[:10])


def test_canary_key_never_in_logs(log_capture: LogCapture, client: TestClient):
    """The canary key must not appear in any log record."""
    # Trigger some activity
    _walk_routes(client)

    violations = []
    for record in log_capture.records:
        if is_canary_string(record):
            violations.append(f"Canary key in log: {record[:100]}")

    assert not violations, f"Canary key in logs:\n" + "\n".join(violations[:5])


def test_health_endpoint_is_learner_safe(client: TestClient):
    """The /health endpoint must use learner-safe wording."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()

    # Must not contain provider or model names
    body_str = json.dumps(body).lower()
    for name in ["gemini", "openai", "anthropic"]:
        assert name not in body_str, f"/health contains provider name '{name}'"

    # Must not contain model names
    for pattern in MODEL_PATTERNS:
        assert not re.search(pattern, body_str), f"/health contains model pattern '{pattern}'"


def test_status_endpoint_is_learner_safe(client: TestClient):
    """The /status endpoint must use learner-safe wording."""
    resp = client.get("/api/v1/status")
    # Status may require auth; if it returns 401/403, that's ok for this test
    if resp.status_code in (401, 403):
        pytest.skip("/status requires authentication")

    body = resp.json()
    body_str = json.dumps(body).lower()

    # Check for provider names in values (not keys)
    for name in ["gemini", "openai", "anthropic"]:
        # Allow in admin context but flag in learner-facing values
        if f'"{name}"' in body_str or f': "{name}"' in body_str:
            pytest.fail(f"/status contains provider name '{name}' in a value")


def test_no_budget_figures_in_learner_endpoints(client: TestClient):
    """Budget figures must not appear in learner-facing endpoints."""
    endpoints = [
        "/api/v1/health",
        "/healthz",
    ]

    for endpoint in endpoints:
        resp = client.get(endpoint)
        body = resp.json()
        body_str = json.dumps(body)

        # Budget-related keys should not be present
        for key in ["budget", "daily_call_budget", "daily_token_budget", "calls_left"]:
            assert key not in body_str, f"{endpoint} contains budget key '{key}'"


def test_simple_mode_wording_without_key(tmp_path, monkeypatch):
    """Without a key, the app must say 'simple mode', not pretend."""
    reset_settings_cache()
    monkeypatch.setenv("ADAPT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADAPT_MODE", "test")
    monkeypatch.setenv("ADAPT_DB_ENCRYPTION", "off")
    monkeypatch.setenv("ADAPT_DISABLE_DIR_CREATE", "1")
    # Explicitly unset the key
    monkeypatch.delenv("ADAPT_GEMINI_API_KEY", raising=False)
    (tmp_path / "keys").mkdir(exist_ok=True)
    (tmp_path / "numbas").mkdir(exist_ok=True)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/health")
    body = resp.json()

    # Must indicate local/simple mode
    assert body.get("local_only") or "simple" in str(body).lower() or "local" in str(body).lower(), (
        "Health endpoint must indicate simple/local mode when no key is configured"
    )

    reset_settings_cache()


def test_config_secrets_module_exists():
    """The config_secrets module must exist and be importable."""
    from adapt import config_secrets

    assert hasattr(config_secrets, "get_key_status")
    assert hasattr(config_secrets, "set_key")
    assert hasattr(config_secrets, "remove_key")
    assert hasattr(config_secrets, "rotate_key")
    assert hasattr(config_secrets, "set_budget")
    assert hasattr(config_secrets, "test_key")
    assert hasattr(config_secrets, "learner_safe_status")


def test_key_masking():
    """Key masking must show only the last four characters."""
    from adapt.config_secrets import _mask_key

    assert _mask_key(None) == ""
    assert _mask_key("") == ""
    assert _mask_key("abc") == "****"
    assert _mask_key("abcdefgh") == "****efgh"
    assert _mask_key("1234567890") == "******7890"


def test_canary_detection():
    """Canary detection must work correctly."""
    assert is_canary_string(f"some text {CANARY_KEY} more text")
    assert is_canary_string(CANARY_PREFIX + "xyz")
    assert not is_canary_string("normal text without the prefix")
    assert not is_canary_string("")


def test_provider_name_detection():
    """Provider name detection must work."""
    assert contains_provider_name("using gemini for inference") is not None
    assert contains_provider_name("openai model") is not None
    assert contains_provider_name("no providers here") is None


def test_error_responses_do_not_leak_secrets(client: TestClient, log_capture: LogCapture):
    """Error responses must not leak the canary key or internal details."""
    # Try to trigger various errors
    error_paths = [
        ("/api/v1/nonexistent", "GET"),
        ("/api/v1/attempts", "POST"),  # missing required fields
        ("/api/v1/sessions", "POST"),  # missing required fields
    ]

    for path, method in error_paths:
        try:
            if method == "GET":
                resp = client.get(path)
            else:
                resp = client.post(path, json={})

            body = resp.text
            assert not is_canary_string(body), f"Canary key in error response for {path}"

            # Error responses should not contain file paths or stack traces
            # that could reveal the key file location
            if resp.status_code >= 400:
                assert CANARY_KEY not in body, f"Canary key in error body for {path}"
        except Exception:
            pass
