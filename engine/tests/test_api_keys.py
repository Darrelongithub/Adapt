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
from datetime import UTC

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
from tests.pg import bootstrap_key, provision_tenant, synthetic_suffix

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


#: Bound per test by the autouse fixture below. Bootstrapping a key needs the
#: owner connection, and threading `database` through every test signature
#: said less than this does.
_DATABASE = None


@pytest.fixture(autouse=True)
def _bind_database(database):
    global _DATABASE
    _DATABASE = database
    yield


def _key(tenant_id: uuid.UUID, scopes=("observe", "admin"), env="test"):
    """A key for ``tenant_id``, minted by provisioning (issuer = NULL)."""
    return bootstrap_key(_DATABASE, tenant_id, scopes=scopes, env=env)


def _revoke(engine: Engine, tenant_id: uuid.UUID, key_id: uuid.UUID) -> bool:
    with tenant_transaction(engine, tenant_id) as conn:
        return key_service.revoke_api_key(conn, tenant_id=tenant_id, key_id=key_id)


def _auth(full_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {full_key}"}


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"keys-{uuid.uuid4().hex[:10]}", name="Key Tenant")


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    """The tenant's first key: minted by provisioning, not over the API."""
    return bootstrap_key(database, tenant)


@pytest.fixture
def tenant_and_audit(app_engine: Engine, tenant: uuid.UUID) -> tuple[uuid.UUID, Engine]:
    """The tenant, and an engine for reading its audit rows under RLS."""
    return tenant, app_engine


def test_issued_key_works_then_revoked_key_fails(app_engine: Engine, tenant: uuid.UUID) -> None:
    """The §3 acceptance sequence: issue → authorised call → revoke → 401."""
    issued = _key(tenant)
    client = _client(app_engine)

    resp = client.get("/v1/whoami", headers=_auth(issued.full_key))
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == str(tenant)

    assert _revoke(app_engine, tenant, issued.key_id)

    # No cache: the very next request is rejected.
    resp = client.get("/v1/whoami", headers=_auth(issued.full_key))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_stored_value_is_not_the_key(owner_engine: Engine, tenant: uuid.UUID) -> None:
    """The database holds a prefix and an HMAC, never the key."""
    issued = _key(tenant)
    # The app role holds no privilege on api_keys (migration 0004), so the
    # stored bytes are inspected — as an operator would — through the owner.
    with owner_engine.connect() as conn:
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
    assert bytes(row.hash) == key_service.hash_key(PEPPER, issued.full_key)


def test_key_format(app_engine: Engine, tenant: uuid.UUID) -> None:
    live = _key(tenant, env="live")
    test = _key(tenant, env="test")
    assert live.full_key.startswith("ak_live_")
    assert test.full_key.startswith("ak_test_")
    assert len(live.full_key) == len("ak_live_") + 32
    assert key_service.key_env(live.full_key) == "live"
    assert key_service.looks_like_key(live.full_key)


def test_revoked_key_fails_immediately_not_at_cache_expiry(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    issued = _key(tenant)
    client = _client(app_engine)
    assert client.get("/v1/whoami", headers=_auth(issued.full_key)).status_code == 200

    _revoke(app_engine, tenant, issued.key_id)

    for _ in range(3):
        resp = client.get("/v1/whoami", headers=_auth(issued.full_key))
        assert resp.status_code == 401


def test_test_env_key_is_rejected_by_a_live_endpoint(app_engine: Engine, tenant: uuid.UUID) -> None:
    """A test key on a production-shaped app is unauthorized, not 403."""
    test_key = _key(tenant, env="test")
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
    live_key = _key(tenant, env="live")
    client = _client(app_engine)  # make_settings() is env=test
    resp = client.get("/v1/whoami", headers=_auth(live_key.full_key))
    assert resp.status_code == 401


def test_scopes_are_enforced_as_not_entitled(app_engine: Engine, tenant: uuid.UUID) -> None:
    observe_only = _key(tenant, scopes=("observe",))
    client = _client(app_engine)

    assert client.get("/v1/learners", headers=_auth(observe_only.full_key)).status_code == 200

    resp = client.post(
        "/v1/learners",
        headers=_auth(observe_only.full_key),
        json={"learner_id": f"learner-{synthetic_suffix()}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"
    # INTEGRATION §6: structured values live in `details`, the message stays a
    # sentence a human can read.
    assert resp.json()["error"]["details"] == {"tier_required": "core"}


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
    issued = _key(tenant)
    revoked = _key(tenant)
    _revoke(app_engine, tenant, revoked.key_id)
    client = _client(app_engine, max_failures=1000)

    unknown_prefix = "ak_test_" + "b" * 32  # shaped, never issued
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
    issued = _key(tenant)
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

    # max_failures is an allowance: three 401s, then 429s.
    for _ in range(3):
        assert client.get("/v1/whoami", headers=bad).status_code == 401

    blocked = client.get("/v1/whoami", headers=bad)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.json()["error"]["retryable"] is True
    assert int(blocked.headers["Retry-After"]) >= 1


def test_a_valid_key_is_never_rate_limited_by_other_tenants_failures(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """A shared NAT full of bad guesses must not lock out a good key.

    This is why validation runs first and counting runs after: the limiter
    can only ever change the answer to a *failed* authentication.
    """
    issued = _key(tenant)
    client = _client(app_engine, max_failures=2)
    bad = {"Authorization": "Bearer ak_test_" + "y" * 32}

    for _ in range(6):
        client.get("/v1/whoami", headers=bad)
    assert client.get("/v1/whoami", headers=bad).status_code == 429

    # Same client address, valid key: still 200.
    for _ in range(5):
        allowed = client.get("/v1/whoami", headers=_auth(issued.full_key))
        assert allowed.status_code == 200, allowed.text


def test_last_used_at_is_written_off_the_request_path(
    app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    """Forty requests, zero writes on the hot path, one write per flush window."""
    issued = _key(tenant)
    app = _client(app_engine)
    recorder = app.app.state.last_used

    def stored() -> object:
        with owner_engine.connect() as conn:
            return conn.execute(
                text("SELECT last_used_at FROM api_keys WHERE id = :id"), {"id": issued.key_id}
            ).scalar_one()

    for _ in range(40):
        assert app.get("/v1/whoami", headers=_auth(issued.full_key)).status_code == 200

    assert stored() is None, "the request path wrote last_used_at"
    assert recorder.pending() == 1, "forty requests should be one pending key"

    assert recorder.flush() == 1
    first = stored()
    assert first is not None

    # Inside the debounce window the flush is a no-op, however many requests.
    for _ in range(40):
        assert app.get("/v1/whoami", headers=_auth(issued.full_key)).status_code == 200
    assert recorder.flush() == 0
    assert stored() == first


def test_key_material_never_reaches_the_log(
    app_engine: Engine, tenant: uuid.UUID, capsys: pytest.CaptureFixture[str]
) -> None:
    issued = _key(tenant)
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


# --- increment 3 fixes: minting rules, expiry, and what gets audited ---------


def test_an_admin_key_may_mint_a_subset_of_itself(
    database, app_engine: Engine, tenant: uuid.UUID
) -> None:
    wide = bootstrap_key(database, tenant, scopes=("observe", "decide", "admin"))
    client = _client(app_engine)
    resp = client.post(
        "/v1/admin/keys",
        headers=_auth(wide.full_key),
        json={"scopes": ["observe", "decide"], "env": "test"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scopes"] == ["decide", "observe"]


def test_an_admin_key_may_never_mint_admin(
    app_engine: Engine, tenant: uuid.UUID, admin_key
) -> None:
    """Otherwise admin is one call away, and the scope means nothing."""
    client = _client(app_engine)
    resp = client.post(
        "/v1/admin/keys",
        headers=_auth(admin_key.full_key),
        json={"scopes": ["admin"], "env": "test"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"
    assert "provisioned" in resp.json()["error"]["message"]


def test_an_admin_key_may_not_mint_a_scope_it_lacks(
    database, app_engine: Engine, tenant: uuid.UUID
) -> None:
    narrow_admin = bootstrap_key(database, tenant, scopes=("observe", "admin"))
    client = _client(app_engine)
    resp = client.post(
        "/v1/admin/keys",
        headers=_auth(narrow_admin.full_key),
        json={"scopes": ["observe", "read_model"], "env": "test"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"
    assert "subset" in resp.json()["error"]["message"]


def test_the_provisioning_path_is_closed_to_the_app_role(
    tenant: uuid.UUID, app_engine: Engine
) -> None:
    """No issuer means provisioning, and provisioning means the owner session."""
    with pytest.raises(key_service.KeyIssueRefused) as refused:
        key_service.issue_api_key(
            app_engine, tenant_id=tenant, scopes=["observe"], env="test", pepper=PEPPER
        )
    assert refused.value.reason == "issuer_required"


def test_every_key_expires_and_the_default_is_ninety_days(
    app_engine: Engine, tenant: uuid.UUID, admin_key
) -> None:
    from datetime import datetime, timedelta

    issued = _key(tenant)
    horizon = issued.expires_at - datetime.now(UTC)
    assert timedelta(days=89) < horizon < timedelta(days=91)

    resp = _client(app_engine).post(
        "/v1/admin/keys",
        headers=_auth(admin_key.full_key),
        json={"scopes": ["observe"], "env": "test", "expires_in_days": 7},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert 6 <= (datetime.fromisoformat(body["expires_at"]) - datetime.now(UTC)).days <= 7


def test_an_expired_key_fails_exactly_like_a_wrong_one(
    database, app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    """Expiry is not an oracle: same status, same body, same code."""
    short = bootstrap_key(database, tenant, expires_in_days=1)
    client = _client(app_engine)

    assert client.get("/v1/whoami", headers=_auth(short.full_key)).status_code == 200

    # Move the expiry into the past — no clock control at the SQL boundary.
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE api_keys SET expires_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": short.key_id},
        )

    expired = client.get("/v1/whoami", headers=_auth(short.full_key))
    unknown = client.get("/v1/whoami", headers=_auth("ak_test_" + "m" * 32))
    assert expired.status_code == unknown.status_code == 401
    a, b = expired.json()["error"], unknown.json()["error"]
    a.pop("request_id")
    b.pop("request_id")
    assert a == b


def test_the_flush_loop_runs_under_the_app_lifespan(
    app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    """The app starts the loop, and the loop writes what the requests noted.

    This is the one test that exercises the background task rather than
    calling `flush()` by hand: if the lifespan is ever dropped, `last_used_at`
    stops moving and nothing else notices.
    """
    import time

    issued = _key(tenant)
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        flush_interval=0.05,
    )

    with TestClient(app) as client:
        assert client.get("/v1/whoami", headers=_auth(issued.full_key)).status_code == 200
        for _ in range(40):
            time.sleep(0.02)
            with owner_engine.connect() as conn:
                seen = conn.execute(
                    text("SELECT last_used_at FROM api_keys WHERE id = :id"),
                    {"id": issued.key_id},
                ).scalar_one()
            if seen is not None:
                break
    assert seen is not None, "the flush loop never wrote last_used_at"


def test_shortening_a_key_expiry_is_audited(
    app_engine: Engine, tenant_and_audit, admin_key
) -> None:
    """The only runtime path to `expires_at` writes a row naming the key."""
    from datetime import datetime, timedelta

    tenant_id, audit_conn = tenant_and_audit
    client = _client(app_engine)
    created = client.post(
        "/v1/admin/keys",
        headers=_auth(admin_key.full_key),
        json={"scopes": ["observe"], "env": "test"},
    ).json()

    new_expiry = datetime.now(UTC) + timedelta(days=3)
    resp = client.post(
        f"/v1/admin/keys/{created['key_id']}/expiry",
        headers=_auth(admin_key.full_key),
        json={"expires_at": new_expiry.isoformat()},
    )
    assert resp.status_code == 200, resp.text

    from sqlalchemy import text as _text

    from adapt_engine.db.tenant import tenant_transaction as _tx

    with _tx(audit_conn, tenant_id) as conn:
        rows = conn.execute(
            _text("SELECT actor, action, target FROM audit_log WHERE action = 'api_key_expiry_set'")
        ).all()
    assert len(rows) == 1
    assert rows[0].target == created["key_id"]
    assert rows[0].actor == f"key:{admin_key.key_id}"


def test_an_expiry_in_the_past_is_refused(app_engine: Engine, tenant: uuid.UUID, admin_key) -> None:
    from datetime import datetime, timedelta

    client = _client(app_engine)
    created = client.post(
        "/v1/admin/keys",
        headers=_auth(admin_key.full_key),
        json={"scopes": ["observe"], "env": "test"},
    ).json()
    resp = client.post(
        f"/v1/admin/keys/{created['key_id']}/expiry",
        headers=_auth(admin_key.full_key),
        json={"expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "payload_invalid"


def test_a_prefix_collision_is_retried_not_a_500(
    app_engine: Engine, tenant: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twelve characters of key is four of secret, so prefixes collide.

    A tenant with a few dozen keys already runs a real birthday problem on ~1M
    prefixes, and the collision surfaced as a 500 on connection-create —
    something the caller can neither cause nor fix. Issuance retries inside a
    savepoint, so the collision does not abort the transaction the audit row
    needs.
    """
    first = _key(tenant)
    real = key_service.generate_key
    draws: list[str] = []

    def collide_once(env: str = "test") -> tuple[str, str]:
        draws.append(env)
        if len(draws) == 1:
            return first.full_key, first.prefix  # a prefix that already exists
        return real(env)

    monkeypatch.setattr(key_service, "generate_key", collide_once)
    with tenant_transaction(app_engine, tenant) as conn:
        second = key_service.insert_api_key(
            conn,
            tenant_id=tenant,
            scopes=["observe"],
            env="test",
            pepper=PEPPER,
            issuer_key_id=first.key_id,
        )
        issued_rows = conn.execute(
            text("SELECT count(*) FROM audit_log WHERE action = 'api_key_issued'")
        ).scalar_one()

    assert len(draws) == 2, "the colliding draw was retried"
    assert second.prefix != first.prefix
    assert second.full_key[:12] == second.prefix
    assert issued_rows == 2, "the transaction survived the collision"
