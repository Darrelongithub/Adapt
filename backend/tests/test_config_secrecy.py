"""Console-only credentials and the final public/log redaction boundary."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import pytest
from fastapi import WebSocket

CANARY_KEY = "ADAPT_TEST_CANARY_KEY_9f3a_78e31c"
CANARY_YOUTUBE_KEY = "ADAPT_TEST_YOUTUBE_CANARY_42c6_981fae"
CANARY_MODEL = "custom-model-canary-77"


def _settings(tmp_path: Path, **overrides):
    from adapt.config import Settings

    values = {
        "_env_file": None,
        "data_dir": tmp_path,
        "db_encryption": "field-only",
        "gemini_api_key": CANARY_KEY,
        "youtube_api_key": CANARY_YOUTUBE_KEY,
        "gemini_model_ai1": CANARY_MODEL,
        "gemini_model_ai2": "secondary-model-canary-81",
        "gemini_daily_call_budget": 17,
        "gemini_daily_token_budget": 913,
    }
    values.update(overrides)
    return Settings(**values)


def test_console_key_file_is_atomic_allowlisted_and_owner_only(tmp_path):
    from adapt.config_secrets import (
        ConfigSecretError,
        config_file,
        read_managed_values,
        set_budgets,
        set_key,
        write_managed_values,
    )

    secret = "canary-value-with-quote-' and equals=sign"
    path = set_key(tmp_path, "gemini", secret)
    set_budgets(tmp_path, calls=19, tokens=1200)
    assert path == config_file(tmp_path)
    assert read_managed_values(path) == {
        "ADAPT_GEMINI_API_KEY": secret,
        "ADAPT_GEMINI_DAILY_CALL_BUDGET": "19",
        "ADAPT_GEMINI_DAILY_TOKEN_BUDGET": "1200",
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.suffix == ".env" and "adapt.db" not in str(path)

    with pytest.raises(ConfigSecretError):
        write_managed_values(path, {"ADAPT_UNRELATED_SETTING": "not allowed"})
    with pytest.raises(ConfigSecretError):
        set_key(tmp_path, "gemini", "two\nlines")


def test_settings_merge_console_file_but_explicit_environment_wins(tmp_path, monkeypatch):
    from adapt.config_secrets import set_key, settings_with_managed_values

    for name in ("ADAPT_GEMINI_API_KEY", "ADAPT_YOUTUBE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    set_key(tmp_path, "gemini", CANARY_KEY)
    base = _settings(tmp_path, gemini_api_key=None)
    effective = settings_with_managed_values(base)
    assert effective.gemini_api_key == CANARY_KEY

    monkeypatch.setenv("ADAPT_GEMINI_API_KEY", "environment-override-canary")
    explicit = settings_with_managed_values(base)
    assert explicit.gemini_api_key == "environment-override-canary"


def test_config_cli_hides_key_input_and_masks_status(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from adapt.cli import app
    from adapt.config import reset_settings_cache
    from adapt.config_secrets import config_file

    monkeypatch.setenv("ADAPT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADAPT_DB_ENCRYPTION", "field-only")
    monkeypatch.delenv("ADAPT_GEMINI_API_KEY", raising=False)
    reset_settings_cache()
    runner = CliRunner()
    try:
        missing = runner.invoke(app, ["config", "test"])
        assert missing.exit_code == 1
        assert "Connection test failed" in missing.output
        assert CANARY_KEY not in missing.output

        result = runner.invoke(
            app,
            ["config", "set-key", "--service", "gemini"],
            input=f"{CANARY_KEY}\n{CANARY_KEY}\n",
        )
        assert result.exit_code == 0, result.output
        assert CANARY_KEY not in result.output, "a hidden prompt must not echo its input"
        assert CANARY_KEY in config_file(tmp_path).read_text(encoding="utf-8")

        status = runner.invoke(app, ["config", "status"])
        assert status.exit_code == 0, status.output
        assert CANARY_KEY not in status.output
        assert CANARY_KEY[-4:] in status.output
        assert CANARY_KEY[:-4] not in status.output
    finally:
        reset_settings_cache()


def test_config_remove_and_budget_commands_do_not_emit_a_key(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from adapt.cli import app
    from adapt.config import reset_settings_cache
    from adapt.config_secrets import config_file, read_managed_values, set_key

    monkeypatch.setenv("ADAPT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADAPT_DB_ENCRYPTION", "field-only")
    monkeypatch.delenv("ADAPT_GEMINI_API_KEY", raising=False)
    reset_settings_cache()
    set_key(tmp_path, "gemini", CANARY_KEY)
    runner = CliRunner()
    try:
        budget = runner.invoke(app, ["config", "set-budget", "--calls", "31", "--tokens", "1800"])
        assert budget.exit_code == 0, budget.output
        assert CANARY_KEY not in budget.output
        values = read_managed_values(config_file(tmp_path))
        assert values["ADAPT_GEMINI_DAILY_CALL_BUDGET"] == "31"
        assert values["ADAPT_GEMINI_DAILY_TOKEN_BUDGET"] == "1800"

        replacement = "rotated-secret-canary-42"
        rotated = runner.invoke(
            app,
            ["config", "rotate", "--service", "gemini"],
            input=f"{replacement}\n{replacement}\n",
        )
        assert rotated.exit_code == 0, rotated.output
        assert replacement not in rotated.output
        assert read_managed_values(config_file(tmp_path))["ADAPT_GEMINI_API_KEY"] == replacement

        removed = runner.invoke(app, ["config", "remove", "--service", "gemini"], input="y\n")
        assert removed.exit_code == 0, removed.output
        assert CANARY_KEY not in removed.output and replacement not in removed.output
        assert "ADAPT_GEMINI_API_KEY" not in read_managed_values(config_file(tmp_path))
    finally:
        reset_settings_cache()


def test_structured_and_plain_responses_drop_configuration_and_quota_details(tmp_path):
    from adapt.security.secrecy import scrub_public_body, scrub_public_payload, scrub_text

    settings = _settings(tmp_path)
    payload = {
        "ok": True,
        "provider": "gemini",
        "model": CANARY_MODEL,
        "llm": {
            "provider": "gemini",
            "model": CANARY_MODEL,
            "headroom": {"budget_calls": 17, "calls_left": 15},
        },
        "message": (
            f"key={CANARY_KEY}; youtube={CANARY_YOUTUBE_KEY}; daily call budget (17) reached"
        ),
    }
    sanitized = scrub_public_payload(payload, settings)
    rendered = json.dumps(sanitized)
    for blocked in (
        CANARY_KEY,
        CANARY_YOUTUBE_KEY,
        CANARY_MODEL,
        "gemini",
        "budget_calls",
        "calls_left",
        '"llm"',
    ):
        assert blocked.lower() not in rendered.lower(), rendered
    assert "optional service limit reached" in rendered
    safe_error = scrub_public_payload(
        {
            "detail": {
                "error": "model_budget_exhausted",
                "note": "the free Gemini budget for today is spent",
                "headroom": {"calls_left": 0, "budget": 17},
            }
        },
        settings,
    )
    assert safe_error == {
        "detail": {
            "error": "service_unavailable",
            "note": "This feature is temporarily unavailable.",
        }
    }
    renamed_configuration = scrub_public_payload(
        {
            "gemini_daily_call_budget": 17,
            "gemini_daily_token_budget": 913,
            "custom_provider_name": "hidden-upstream-service",
            "selected_model_id": CANARY_MODEL,
            CANARY_MODEL: "model-name-used-as-a-field-key",
            "secret_api_token": "unknown-secret-canary",
            "access_token": "unknown-access-token",
            "csrf_token": "csrf-token-for-this-browser",
            "sent_to_model": False,
            "learner_model_version": "model-v42",
            "mathematical_model": "a triangular prism",
        },
        settings,
    )
    assert renamed_configuration == {
        "csrf_token": "csrf-token-for-this-browser",
        "sent_to_model": False,
        "learner_model_version": "model-v42",
        "mathematical_model": "a triangular prism",
    }

    json_body = scrub_public_body(
        json.dumps(payload).encode(), "application/json", settings
    ).decode()
    assert (
        CANARY_KEY not in json_body
        and CANARY_YOUTUBE_KEY not in json_body
        and CANARY_MODEL not in json_body
        and "gemini" not in json_body.lower()
    )
    ndjson = "\n".join(
        [
            json.dumps({"provider": "gemini", "model": CANARY_MODEL, "budget": 913}),
            json.dumps({"error": "model_budget_exhausted", "headroom": {"calls_left": 0}}),
        ]
    ).encode()
    safe_ndjson = scrub_public_body(ndjson, "application/x-ndjson", settings).decode()
    blocked_ndjson = ("gemini", CANARY_MODEL, "913", "headroom", "budget")
    assert all(term not in safe_ndjson.lower() for term in blocked_ndjson)
    safe_text_error = scrub_public_body(
        b'{"error":"model_budget_exhausted"}', "text/plain", settings
    ).decode()
    assert json.loads(safe_text_error) == {
        "error": "service_unavailable",
        "note": "This feature is temporarily unavailable.",
    }
    assert CANARY_KEY not in scrub_text(f"upstream failed: {CANARY_KEY}", settings)
    secret_bearing_error = scrub_text(
        "https://api.invalid/search?key=unknown-secret-token Authorization: Bearer another-secret",
        settings,
    )
    assert "unknown-secret-token" not in secret_bearing_error
    assert "another-secret" not in secret_bearing_error
    safe_plain_quota = scrub_text("daily_call_budget=17 calls_left=4 headroom=3 quota=6", settings)
    assert all(amount not in safe_plain_quota for amount in ("17", "4", "3", "6"))


def test_asgi_boundary_sanitizes_http_headers_body_and_websocket_frames(tmp_path):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from adapt.security.secrecy import ConfigurationSecrecyMiddleware

    app = FastAPI()

    @app.get("/leak")
    def leak():
        return JSONResponse(
            {
                "provider": "gemini",
                "model": CANARY_MODEL,
                "llm": {"provider": "gemini", "key": CANARY_KEY},
                "detail": (f"daily token budget (913) reached: {CANARY_KEY} {CANARY_YOUTUBE_KEY}"),
            },
            headers={
                "x-runtime-detail": f"{CANARY_MODEL} {CANARY_KEY} {CANARY_YOUTUBE_KEY}",
                "x-api-key": CANARY_KEY,
                "authorization": "Bearer unknown-secret-token",
            },
        )

    @app.get("/explode")
    def explode():
        raise RuntimeError(
            f"upstream failure: {CANARY_KEY} {CANARY_YOUTUBE_KEY} {CANARY_MODEL}; "
            "daily token budget (913) reached"
        )

    @app.websocket("/socket")
    async def socket(websocket: WebSocket):
        await websocket.accept(headers=[(b"x-runtime-detail", CANARY_KEY.encode())])
        await websocket.send_text(
            json.dumps(
                {
                    "provider": "gemini",
                    "model": CANARY_MODEL,
                    "budget": 913,
                    "detail": f"{CANARY_KEY} {CANARY_YOUTUBE_KEY}",
                    "error": "model_budget_exhausted",
                    "headroom": {"calls_left": 0, "budget": 17},
                }
            )
        )
        await websocket.close()

    boundary = ConfigurationSecrecyMiddleware(app, _settings(tmp_path))
    with TestClient(boundary, raise_server_exceptions=False) as client:
        response = client.get("/leak")
        joined = response.text + json.dumps(dict(response.headers))
        for blocked in (CANARY_KEY, CANARY_YOUTUBE_KEY, CANARY_MODEL, "gemini"):
            assert blocked.lower() not in joined.lower(), joined
        assert "provider" not in response.json() and "model" not in response.json()
        assert "x-api-key" not in response.headers
        assert "authorization" not in response.headers
        error = client.get("/explode")
        assert error.status_code == 500
        error_surface = error.text + json.dumps(dict(error.headers))
        for blocked in (CANARY_KEY, CANARY_YOUTUBE_KEY, CANARY_MODEL, "gemini", "913"):
            assert blocked.lower() not in error_surface.lower(), error_surface
        with client.websocket_connect("/socket") as ws:
            frame = ws.receive_text()
            assert (
                CANARY_KEY not in frame
                and CANARY_YOUTUBE_KEY not in frame
                and CANARY_MODEL not in frame
                and "gemini" not in frame.lower()
                and "913" not in frame
                and "provider" not in frame.lower()
                and "model" not in frame.lower()
                and "headroom" not in frame.lower()
                and "budget" not in frame.lower()
            )
            assert json.loads(frame)["error"] == "service_unavailable"


def test_log_boundary_scrubs_messages_and_drops_raw_tracebacks(tmp_path, caplog):
    import logging

    from adapt.config import get_settings
    from adapt.security.secrecy import install_log_redaction

    settings = _settings(tmp_path)
    install_log_redaction(settings)
    try:
        logger = logging.getLogger("adapt.test.secrecy")
        with caplog.at_level(logging.ERROR, logger="adapt.test.secrecy"):
            logger.error(
                "request failed for key=%s youtube=%s model=%s provider=%s",
                CANARY_KEY,
                CANARY_YOUTUBE_KEY,
                CANARY_MODEL,
                "gemini",
            )
            logger.error("raw quota metadata=%s", {"headroom": {"calls_left": 4}, "budget": 913})
            logger.error('{"error":"model_budget_exhausted","headroom":{"calls_left":1}}')
            logger.error('{"model":"%s","api_key":"%s"}', CANARY_MODEL, CANARY_KEY)
            logger.error(
                "structured metadata",
                extra={
                    "daily_token_budget": 913,
                    "provider": "gemini",
                    "model_name": CANARY_MODEL,
                    "headroom": {"calls_left": 0},
                },
            )
            try:
                raise RuntimeError(f"upstream detail {CANARY_KEY} {CANARY_MODEL}")
            except RuntimeError:
                logger.exception("provider request failed")
        for record in caplog.records:
            assert CANARY_KEY not in record.getMessage()
            assert CANARY_YOUTUBE_KEY not in record.getMessage()
            assert CANARY_MODEL not in record.getMessage()
            assert "gemini" not in record.getMessage().lower()
            assert "913" not in record.getMessage()
            assert "headroom" not in record.getMessage().lower()
            assert "calls_left" not in record.getMessage().lower()
            assert record.exc_info is None
            assert record.exc_text is None
            if hasattr(record, "daily_token_budget"):
                assert record.daily_token_budget == "[withheld]"
                assert record.provider == "[withheld]"
                assert record.model_name == "[withheld]"
                assert record.headroom == "[withheld]"
    finally:
        install_log_redaction(get_settings())


def test_uvicorn_access_log_args_remain_format_compatible_after_redaction(tmp_path):
    """The access formatter consumes a five-value tuple after the secrecy filter runs."""
    import logging

    from uvicorn.logging import AccessFormatter

    from adapt.security.secrecy import _redact_log_record

    settings = _settings(tmp_path)
    secret_url = "/items?api_key=access-log-canary"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:4321", "GET", secret_url, "1.1", 200),
        None,
    )

    _redact_log_record(record, settings)

    assert isinstance(record.args, tuple) and len(record.args) == 5
    rendered = AccessFormatter().format(record)
    assert "/items?" in rendered and "[withheld]" in rendered
    assert "200" in rendered
    assert "access-log-canary" not in rendered
    assert "Logging error" not in rendered


def test_llm_guard_quota_error_is_safe_at_the_live_http_boundary(client, learner, monkeypatch):
    from types import SimpleNamespace

    from adapt.api import deps

    class FakeLimiter:
        def allow(self, _scope, _key):
            return True, 0.0

    class FakeLLM:
        def available(self):
            return {
                "configured": True,
                "provider": "gemini",
                "model": CANARY_MODEL,
                "headroom": {
                    "calls_left": 0,
                    "tokens_left": 0,
                    "budget_calls": 17,
                    "budget_tokens": 913,
                },
            }

    services = SimpleNamespace(
        limiter=FakeLimiter(),
        ai2=SimpleNamespace(llm=FakeLLM()),
    )
    monkeypatch.setattr(deps, "current", lambda: services)
    response = client.post(
        "/api/v1/explain",
        headers=learner["headers"],
        json={"question": "Explain this idea in simple terms."},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "error": "service_unavailable",
            "note": "This feature is temporarily unavailable.",
        }
    }
    surface = response.text + json.dumps(dict(response.headers))
    for blocked in (CANARY_MODEL, "gemini", "headroom", "budget"):
        assert blocked.lower() not in surface.lower()
    for configured_amount in ("17", "913"):
        assert configured_amount not in response.text


def test_ai1_and_ai2_success_metadata_is_private_at_the_public_boundary(client, learner):
    analysis = client.post(
        "/api/v1/analyze?use_llm=false",
        headers=learner["headers"],
    )
    explain_body = {"question": "Explain equivalent fractions in simple terms."}
    explanation = client.post(
        "/api/v1/explain",
        headers=learner["headers"],
        json=explain_body,
    )
    responses = [analysis, explanation]
    for response in responses:
        assert response.status_code == 200, response.text
        payload = response.json()
        serialized = json.dumps(payload).lower()
        for forbidden in (
            '"provider"',
            '"provider_note"',
            '"provider_name"',
            '"model"',
            '"model_name"',
            '"llm"',
            '"llm_meta"',
            "gemini",
            "google",
            "faster-whisper",
            "kokoro",
        ):
            assert forbidden not in serialized, f"{response.url.path} leaked {forbidden}"

    simple_answer = explanation.json()
    assert simple_answer["answer"].strip()
    assert simple_answer["privacy"]["sent_to_model"] is False
    repeated = client.post(
        "/api/v1/explain",
        headers=learner["headers"],
        json=explain_body,
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["answer"] == simple_answer["answer"]


def test_main_route_table_is_walked_through_the_public_boundary(client, learner):
    """Exercise each registered HTTP route/method once, not a stale hand-picked list."""
    from adapt.api.app import app as application
    from adapt.config import get_settings
    from adapt.security.middleware import SecurityHeadersMiddleware
    from adapt.services import current

    routes = [
        route
        for route in application.routes
        if getattr(route, "methods", None) and getattr(route, "path", None)
    ]
    assert routes, "the canary smoke test must discover the app's live route table"
    terms = (
        "gemini",
        "google",
        "openalex",
        "arxiv",
        "open library",
        "youtube",
        "faster-whisper",
        "kokoro",
        "gemini-2.5-flash",
        CANARY_KEY,
        CANARY_YOUTUBE_KEY,
        CANARY_MODEL,
    )
    targets = {id(settings): settings for settings in (get_settings(), current().settings)}
    for middleware in application.user_middleware:
        if middleware.cls is SecurityHeadersMiddleware:
            settings = middleware.kwargs.get("settings")
            if settings is not None:
                targets[id(settings)] = settings
    keys = (
        "gemini_api_key",
        "youtube_api_key",
        "gemini_model_ai1",
        "gemini_model_ai2",
        "gemini_daily_call_budget",
        "gemini_daily_token_budget",
    )
    previous = {
        identity: {key: getattr(settings, key) for key in keys}
        for identity, settings in targets.items()
    }
    try:
        for settings in targets.values():
            settings.gemini_api_key = CANARY_KEY
            settings.youtube_api_key = CANARY_YOUTUBE_KEY
            settings.gemini_model_ai1 = CANARY_MODEL
            settings.gemini_model_ai2 = "secondary-model-canary-81"
            settings.gemini_daily_call_budget = 17
            settings.gemini_daily_token_budget = 913
        with client.websocket_connect("/ws/voice") as websocket:
            hello = websocket.receive_text()
            assert json.loads(hello).get("t") == "hello"
            for term in terms:
                assert term.lower() not in hello.lower(), (
                    f"WebSocket hello leaked {term}: {hello[:500]}"
                )
        for route in routes:
            path = re.sub(r"\{[^{}]+\}", "security-probe", route.path)
            for method in sorted(route.methods):
                if method in {"GET", "HEAD", "OPTIONS"}:
                    response = client.request(method, path)
                else:
                    response = client.request(method, path, json={})
                surface = (
                    response.text
                    + "\n"
                    + "\n".join(f"{k}:{v}" for k, v in response.headers.items())
                )
                for term in terms:
                    assert term.lower() not in surface.lower(), (
                        f"{method} {path} leaked {term}: {surface[:500]}"
                    )
    finally:
        for identity, values in previous.items():
            settings = targets[identity]
            for key, value in values.items():
                setattr(settings, key, value)


def test_synthetic_demo_seed_is_labelled_and_idempotent(services):
    import importlib
    import sys

    from adapt.db.models import Attempt, LearnerEvent, LearningSession, Student

    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    demo_seed = importlib.import_module("scripts.demo_seed")
    try:
        with services.scope() as session:
            first = demo_seed._seed(session, "test-only-synthetic-password")
            assert len(first["learners"]) == 2
            for learner in first["learners"]:
                student_id = learner["student_id"]
                student = session.get(Student, student_id)
                rows = session.query(Attempt).filter(Attempt.student_id == student_id).all()
                history = (
                    session.query(LearningSession)
                    .filter(LearningSession.student_id == student_id)
                    .all()
                )
                events = (
                    session.query(LearnerEvent).filter(LearnerEvent.student_id == student_id).all()
                )
                assert student.display_name.startswith("SYNTHETIC DEMO")
                assert len(rows) == 16 and all(row.pilot for row in rows)
                assert {row.method for row in rows} == {"worked_example", "explanation"}
                assert len(history) == 1 and history[0].mode == "synthetic_demo"
                assert history[0].summary["synthetic_demo"] is True
                assert len(events) >= len(rows) * 3
            second = demo_seed._seed(session, "test-only-synthetic-password")
            assert len(second["learners"]) == 2
            assert (
                session.query(Attempt).filter(Attempt.student_id.like("demo_synthetic_%")).count()
                == 32
            )
            protected = session.get(Student, "demo_synthetic_example")
            original_email = protected.email
            original_password_hash = protected.password_hash
            non_synthetic = LearningSession(student_id=protected.id, mode="practice")
            session.add(non_synthetic)
            session.flush()
            with pytest.raises(RuntimeError, match="non-synthetic learner history"):
                demo_seed._seed(session, "must-not-overwrite-this-account")
            session.refresh(protected)
            assert protected.email == original_email
            assert protected.password_hash == original_password_hash
            session.delete(non_synthetic)
            session.commit()
    finally:
        with services.scope() as session:
            demo_seed._remove_demo_data(session)


def test_baseline_never_shows_a_timer_after_the_learner_asks_for_it_off():
    from adapt.baseline.factors import FACTORS, Factor, levers

    factors = {key: Factor(key=key, title=key, state="not_enough_data") for key in FACTORS}
    stopped = levers(factors, preferences={"timers_help": False})
    allowed = levers(factors, preferences={"timers_help": True})
    assert stopped["timers_visible"]["value"] is False
    assert stopped["timers_visible"]["source"] == "stated"
    assert allowed["timers_visible"]["value"] is True


def test_generated_frontend_bundles_are_checked_when_present():
    """Do not mistake a missing build for a bundle-safety pass; build jobs get scanned."""
    root = Path(__file__).resolve().parents[2]
    bundle_roots = (
        root / "frontend" / ".next" / "static",
        root / "frontend" / "out" / "_next" / "static",
        root / "frontend" / "build" / "static",
    )
    files = [
        p for bundle in bundle_roots if bundle.exists() for p in bundle.rglob("*") if p.is_file()
    ]
    if not files:
        return
    forbidden = (
        CANARY_KEY,
        "gemini-2.5-flash",
        "openalex",
        "faster-whisper",
    )
    for path in files:
        data = path.read_bytes().lower()
        for term in forbidden:
            assert term.lower().encode() not in data, (
                f"{path} contains a private configuration literal"
            )
