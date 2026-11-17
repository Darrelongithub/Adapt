"""API keys: issue → use → revoke, and the failures that must look identical.

The interesting tests here are the negative ones. An authentication failure
that is *distinguishable* is an oracle: it tells an attacker whether a prefix
exists, whether a key is revoked, or which environment issued it. So unknown
prefix, bad secret, revoked key and wrong environment all produce the same
status, the same body (request_id aside) and the same amount of work.
"""

from __future__ import annotations

import json
import statistics
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth import keys as key_service
from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.config import Settings
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from tests.conftest import TEST_PEPPER, make_settings
from tests.pg import provision_tenant

PEPPER = TEST_PEPPER


def _client(
    engine: Engine,
    *,
    settings: Settings | None = None,
    max_failures: int = 20,
) -> TestClient:
    active = settings if settings is not None else make_settings()
    app = create_app(
        active,
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=max_failures),
    )
    return TestClient(app, raise_server_exceptions=False)


def _key(engine: Engine, tenant_id: uuid.UUID, scopes=("observe", "admin"), env="test"):
    return key_service.issue_api_key(
        engine, tenant_id=tenant_id, scopes=list(scopes), env=env, pepper=PEPPER
    )


def _auth(full_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {full_key}"}


@pytest.fixture
def tenant(app_engine: Engine, database) -> uuid.UUID:
    return provision_tenant(
        database, slug=f"keys-{uuid.uuid4().hex[:10]}", name="Key Tenant"
    )


@pytest.fixture
def admin_key(app_engine: Engine, tenant: uuid.UUID):
    return _key(app_engine, tenant)


def test_issued_key_works_then_revoked_key_fails(app_engine: Engine, tenant: uuid.UUID) -> None:
    """The §3 acceptance sequence: issue → authorised call → revoke → 401."""
    issued = _key(app_engine, tenant)
    client = _client(app_engine)

    resp = client.get("/v1/whoami", headers=_auth(issued.full_key))
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == str(tenant)

    assert key_service.revoke_api_key(
        app_engine, tenant_id=tenant, key_id=issued.key_id
    )

    # No cache: the very next request is rejected.
    resp = client.get("/v1/whoami", headers=_auth(issued.full_key))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_stored_value_is_not_the_key(app_engine: Engine, tenant: uuid.UUID) -> None:
    """The database holds a prefix and an HMAC, never the key."""
    issued = _key(app_engine, tenant)
    with tenant_transaction(app_engine, tenant) as conn:
        row = conn.execute(
            text("SELECT prefix, hash, scopes FROM api_keys WHERE id = :id"),
            {"id": issued.key_id},
        ).one()

    assert row.prefix == issued.full_key[:12]
    assert issued.full_key not in row.prefix
    assert bytes(row.hash) != issued.full_key.encode()
    assert len(bytes(row.hash)) == 32
    # The hash is not reversible: the secret part is not recoverable from it.
    assert issued.full_key[12:].encode() not in bytes(row.hash)
    assert key_service.hashes_match(
        bytes(row.hash), key_service.hash_key(PEPPER, issued.full_key)
    )


def test_key_format(app_engine: Engine, tenant: uuid.UUID) -> None:
    live = _key(app_engine, tenant, env="live")
    test = _key(app_engine, tenant, env="test")
    assert live.full_key.startswith("ak_live_")
    assert test.full_key.startswith("ak_test_")
    assert len(live.full_key) == len("ak_live_") + 32
    assert key_service.key_env(live.full_key) == "live"
    assert key_service.looks_like_key(live.full_key)


def test_revoked_key_fails_immediately_not_at_cache_expiry(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    issued = _key(app_engine, tenant)
    client = _client(app_engine)
    assert client.get("/v1/whoami", headers=_auth(issued.full_key)).status_code == 200

    key_service.revoke_api_key(app_engine, tenant_id=tenant, key_id=issued.key_id)

    for _ in range(3):
        resp = client.get("/v1/whoami", headers=_auth(issued.full_key))
        assert resp.status_code == 401


def test_test_env_key_is_rejected_by_a_live_endpoint(app_engine: Engine, tenant: uuid.UUID) -> None:
    """A test key on a production-shaped app is unauthorized, not 403."""
    test_key = _key(app_engine, tenant, env="test")
    production_settings = make_settings(
        env="production",
        commit="abc1234",
        version="2.0.0",
        database_url="postgresql://adapt:secret@db.internal:5432/adapt",
        redis_url="redis://cache.internal:6379/0",
    )
    client = _client(app_engine, settings=production_settings)
    resp = client.get("/v1/whoami", headers=_auth(test_key.full_key))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_live_key_is_rejected_by_a_test_endpoint(app_engine: Engine, tenant: uuid.UUID) -> None:
    live_key = _key(app_engine, tenant, env="live")
    client = _client(app_engine)  # make_settings() is env=test
    resp = client.get("/v1/whoami", headers=_auth(live_key.full_key))
    assert resp.status_code == 401


def test_scopes_are_enforced_as_not_entitled(app_engine: Engine, tenant: uuid.UUID) -> None:
    observe_only = _key(app_engine, tenant, scopes=("observe",))
    client = _client(app_engine)

    assert client.get("/v1/learners", headers=_auth(observe_only.full_key)).status_code == 200

    resp = client.post(
        "/v1/learners",
        headers=_auth(observe_only.full_key),
        json={"learner_id": f"learner-{uuid.uuid4().hex[:8]}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"
    assert "tier_required" in resp.json()["error"]["message"]


def test_missing_and_malformed_headers_are_unauthorized(app_engine: Engine) -> None:
    client = _client(app_engine, max_failures=1000)
    for headers in (
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not-a-key"},
        {"Authorization": f"Bearer ak_test_{'a' * 31}"},
        {"Authorization": "Basic abc"},
    ):
        resp = client.get("/v1/whoami", headers=headers)
        assert resp.status_code == 401, headers
        assert resp.json()["error"]["code"] == "unauthorized"


def test_failure_body_is_identical_across_failure_modes(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """Same status, same code, same message, same keys — only request_id differs."""
    issued = _key(app_engine, tenant)
    revoked = _key(app_engine, tenant)
    key_service.revoke_api_key(app_engine, tenant_id=tenant, key_id=revoked.key_id)
    client = _client(app_engine, max_failures=1000)

    unknown_prefix = "ak_test_" + "b" * 32            # shaped, never issued
    wrong_secret = issued.full_key[:-1] + ("a" if issued.full_key[-1] != "a" else "b")

    bodies = {}
    for label, presented in (
        ("unknown", unknown_prefix),
        ("wrong_secret", wrong_secret),
        ("revoked", revoked.full_key),
    ):
        resp = client.get("/v1/whoami", headers=_auth(presented))
        assert resp.status_code == 401
        payload = resp.json()
        payload["error"].pop("request_id")
        bodies[label] = json.dumps(payload, sort_keys=True)

    assert len(set(bodies.values())) == 1, bodies


def test_unknown_prefix_and_wrong_secret_are_time_equivalent(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """1000 samples, two failure modes, within 10 ms of each other.

    Both paths do one HMAC, one indexed lookup and one constant-time
    comparison. If the lookup short-circuited on "not found", the difference
    would be visible here.
    """
    issued = _key(app_engine, tenant)
    client = _client(app_engine, max_failures=100_000)

    unknown = "ak_test_" + "c" * 32
    wrong = issued.full_key[:-1] + ("d" if issued.full_key[-1] != "d" else "e")

    def sample(presented: str, n: int) -> list[float]:
        timings: list[float] = []
        for _ in range(n):
            started = time.perf_counter()
            assert client.get("/v1/whoami", headers=_auth(presented)).status_code == 401
            timings.append((time.perf_counter() - started) * 1000.0)
        return timings

    unknown_timings = sample(unknown, 500)
    wrong_timings = sample(wrong, 500)

    median_gap = abs(statistics.median(unknown_timings) - statistics.median(wrong_timings))
    p95_gap = abs(
        statistics.quantiles(unknown_timings, n=20)[-1]
        - statistics.quantiles(wrong_timings, n=20)[-1]
    )
    assert median_gap < 10.0, f"median gap {median_gap:.2f} ms over 1000 samples"
    assert p95_gap < 10.0, f"p95 gap {p95_gap:.2f} ms over 1000 samples"


def test_failed_auth_is_rate_limited_per_ip(app_engine: Engine, tenant: uuid.UUID) -> None:
    client = _client(app_engine, max_failures=3)
    bad = {"Authorization": "Bearer ak_test_" + "z" * 32}

    for _ in range(3):
        assert client.get("/v1/whoami", headers=bad).status_code == 401

    blocked = client.get("/v1/whoami", headers=bad)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.json()["error"]["retryable"] is True
    assert int(blocked.headers["Retry-After"]) >= 1


def test_last_used_at_write_amplification_is_bounded(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    issued = _key(app_engine, tenant)
    client = _client(app_engine)

    for _ in range(20):
        assert client.get("/v1/whoami", headers=_auth(issued.full_key)).status_code == 200

    with tenant_transaction(app_engine, tenant) as conn:
        first = conn.execute(
            text("SELECT last_used_at FROM api_keys WHERE id = :id"), {"id": issued.key_id}
        ).scalar_one()
    assert first is not None, "the first successful use should record last_used_at"

    # The debounce means the next burst inside the window writes nothing.
    for _ in range(20):
        assert client.get("/v1/whoami", headers=_auth(issued.full_key)).status_code == 200
    with tenant_transaction(app_engine, tenant) as conn:
        second = conn.execute(
            text("SELECT last_used_at FROM api_keys WHERE id = :id"), {"id": issued.key_id}
        ).scalar_one()
    assert second == first, "last_used_at was rewritten inside the debounce window"


def test_key_material_never_reaches_the_log(
    app_engine: Engine, tenant: uuid.UUID, capsys: pytest.CaptureFixture[str]
) -> None:
    issued = _key(app_engine, tenant)
    client = _client(app_engine, max_failures=1000)
    client.get("/v1/whoami", headers=_auth(issued.full_key))
    client.get("/v1/whoami", headers=_auth("ak_test_" + "q" * 32))
    out = capsys.readouterr().out

    assert issued.full_key not in out
    assert issued.prefix not in out
    assert "ak_test_" not in out
    for line in out.splitlines():
        if not line.startswith("{"):
            continue
        entry = json.loads(line)
        for forbidden in ("key", "hash", "prefix", "presented"):
            assert forbidden not in entry


def test_issuing_through_the_api_returns_the_key_once(
    app_engine: Engine, tenant: uuid.UUID, admin_key
) -> None:
    client = _client(app_engine)
    resp = client.post(
        "/v1/admin/keys",
        headers=_auth(admin_key.full_key),
        json={"scopes": ["observe"], "env": "test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["full_key"].startswith("ak_test_")
    assert body["prefix"] == body["full_key"][:12]

    # The new key works, and no endpoint ever returns it again.
    assert client.get("/v1/whoami", headers=_auth(body["full_key"])).status_code == 200
    again = client.post(
        "/v1/admin/keys",
        headers=_auth(admin_key.full_key),
        json={"scopes": ["observe"], "env": "test"},
    )
    assert again.json()["full_key"] != body["full_key"]


def test_unknown_scope_is_rejected_at_issue(app_engine: Engine, tenant: uuid.UUID) -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        key_service.issue_api_key(
            app_engine, tenant_id=tenant, scopes=["observe", "root"], env="test", pepper=PEPPER
        )
