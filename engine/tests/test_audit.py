"""The audit trail: issue, revoke, and every failed authentication.

Three things matter here, and none of them is "a row exists":

* the row names a **key id**, never a key, a prefix or a hash;
* a failed authentication has no tenant to name, and the row lands with
  ``tenant_id IS NULL`` — visible to the owner, invisible to every tenant;
* the unauthenticated path cannot be used to write rows at an attacker's
  chosen rate. Past ten failures in a minute, one aggregate row replaces the
  rest.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.audit import PER_MINUTE_ROWS, AuthFailureAuditor
from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from tests.conftest import TEST_PEPPER, make_settings
from tests.pg import bootstrap_key, provision_tenant

PEPPER = TEST_PEPPER


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"audit-{uuid.uuid4().hex[:10]}", name="Audit Tenant")


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe", "admin"))


def _client(engine: Engine, *, max_failures: int = 10_000) -> TestClient:
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=max_failures),
    )
    return TestClient(app, raise_server_exceptions=False)


def _auth(full_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {full_key}"}


def _tenant_rows(engine: Engine, tenant_id: uuid.UUID) -> list[object]:
    with tenant_transaction(engine, tenant_id) as conn:
        return conn.execute(
            text("SELECT actor, action, target, metadata FROM audit_log ORDER BY id")
        ).all()


def _system_rows(owner_engine: Engine, *, ip_hash: str | None = None) -> list[object]:
    query = (
        "SELECT tenant_id, actor, action, target, metadata FROM audit_log "
        "WHERE tenant_id IS NULL"
    )
    params: dict[str, str] = {}
    if ip_hash is not None:
        query += " AND metadata->>'ip_hash' = :ip_hash"
        params["ip_hash"] = ip_hash
    with owner_engine.connect() as conn:
        return conn.execute(text(query + " ORDER BY id"), params).all()


def test_issuing_writes_an_audit_row_naming_the_key_id(
    database, app_engine: Engine, tenant: uuid.UUID, admin_key
) -> None:
    client = _client(app_engine)
    resp = client.post(
        "/v1/admin/keys",
        headers=_auth(admin_key.full_key),
        json={"scopes": ["observe"], "env": "test"},
    )
    assert resp.status_code == 200, resp.text
    issued_id = resp.json()["key_id"]

    rows = [
        row
        for row in _tenant_rows(app_engine, tenant)
        if row.action == "api_key_issued" and row.target == issued_id
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row.actor == f"key:{admin_key.key_id}"
    # The issued key's own prefix must not be in the trail.
    assert resp.json()["prefix"] not in json.dumps(dict(row._mapping))


def test_revoking_writes_an_audit_row(
    app_engine: Engine, tenant: uuid.UUID, admin_key
) -> None:
    client = _client(app_engine)
    created = client.post(
        "/v1/admin/keys",
        headers=_auth(admin_key.full_key),
        json={"scopes": ["observe"], "env": "test"},
    ).json()

    revoked = client.post(
        f"/v1/admin/keys/{created['key_id']}/revoke", headers=_auth(admin_key.full_key)
    )
    assert revoked.status_code == 200

    rows = [row for row in _tenant_rows(app_engine, tenant) if row.action == "api_key_revoked"]
    assert len(rows) == 1
    assert rows[0].target == created["key_id"]


def test_a_failed_authentication_writes_a_tenant_less_row_with_no_key_material(
    app_engine: Engine, owner_engine: Engine
) -> None:
    client = _client(app_engine)
    presented = "ak_test_" + "k" * 32
    assert client.get("/v1/whoami", headers=_auth(presented)).status_code == 401

    rows = _system_rows(owner_engine)
    assert rows, "no audit row for a failed authentication"
    row = rows[-1]
    assert row.tenant_id is None
    assert row.actor == "system"
    assert row.action == "auth_failed"
    assert row.target is None
    meta = row.metadata
    assert set(meta) == {"ip_hash", "window_start", "count", "aggregated"}
    assert presented not in json.dumps(meta)
    assert presented[:12] not in json.dumps(meta)
    # The address itself is not stored either.
    assert "127.0.0.1" not in json.dumps(meta)
    assert "testclient" not in json.dumps(meta)


def test_a_tenant_cannot_see_the_tenant_less_rows(
    app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    """The owner sees them; a tenant-scoped connection does not.

    `audit_system_read` is granted to `adapt_owner` only, so the app role's
    SELECT is filtered by `tenant_isolation` alone — and `tenant_id = NULL` is
    not a match for anybody's tenant.
    """
    _client(app_engine).get("/v1/whoami", headers=_auth("ak_test_" + "j" * 32))
    assert _system_rows(owner_engine), "precondition: a system row exists"
    assert all(row.action != "auth_failed" for row in _tenant_rows(app_engine, tenant))


def test_failure_rows_are_aggregated_past_ten_per_minute(
    database, app_engine: Engine, owner_engine: Engine
) -> None:
    """An unbounded audit on the unauthenticated path is the DoS.

    Eleven writes per (ip_hash, minute) at most: ten individual rows and one
    aggregate. A thousand failures are the same eleven.
    """
    auditor = AuthFailureAuditor(app_engine, SystemClock())
    source = "a" * 32

    for _ in range(1000):
        auditor.record(source)

    rows = _system_rows(owner_engine, ip_hash=source)
    assert len(rows) == PER_MINUTE_ROWS + 1, len(rows)
    aggregate = rows[-1]
    assert aggregate.metadata["aggregated"] is True
    assert aggregate.metadata["count"] == PER_MINUTE_ROWS + 1
    assert all(row.metadata["aggregated"] is False for row in rows[:-1])


def test_the_request_path_audits_failures_and_succeeds_silently(
    app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID, admin_key
) -> None:
    client = _client(app_engine)
    before = len(_system_rows(owner_engine))

    for _ in range(3):
        client.get("/v1/whoami", headers=_auth("ak_test_" + "h" * 32))
    client.get("/v1/whoami", headers=_auth(admin_key.full_key))

    assert len(_system_rows(owner_engine)) == before + 3


def test_an_audit_failure_never_turns_a_401_into_a_500(
    app_engine: Engine, tenant: uuid.UUID, admin_key
) -> None:
    """The audit is a side effect of refusing, not a precondition for it."""
    client = _client(app_engine)
    client.app.state.auth_auditor._engine = None  # break the audit path only
    resp = client.get("/v1/whoami", headers=_auth("ak_test_" + "g" * 32))
    assert resp.status_code == 401
