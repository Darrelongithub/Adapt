"""Credits over HTTP: one read for hosts, one write for provisioning.

The route surface is the review's correction, so it is asserted rather than
described: reservations, settlement and release have **no** HTTP route (they
are engine-internal), and granting lives at `/v1/admin/credits/grants` with the
`admin` scope. A 404 on `/v1/credits/reservations` is a passing test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.credits import service as credits
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from tests.conftest import make_settings
from tests.pg import bootstrap_key, grant_credits, provision_tenant


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(
        database, slug=f"credit-http-{uuid.uuid4().hex[:8]}", name="Credit HTTP"
    )


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe", "admin"))


@pytest.fixture
def client(app_engine: Engine) -> TestClient:
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=10_000),
    )
    return TestClient(app, raise_server_exceptions=False)


def _auth(key) -> dict[str, str]:
    return {"Authorization": f"Bearer {key.full_key}"}


def _days(n: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=n)


def _reserve_via_service(client: TestClient, tenant: uuid.UUID, amount: int) -> object:
    """Reservations are engine-internal now, so the test drives the service."""
    engine = client.app.state.engine
    return credits.run_idempotent(
        engine,
        tenant,
        lambda conn: credits.reserve(
            conn,
            tenant_id=tenant,
            amount=amount,
            operation_ref=f"op-{uuid.uuid4()}",
            idempotency_key=f"res-{uuid.uuid4()}",
        ),
    )


def test_the_balance_needs_a_key(client: TestClient) -> None:
    resp = client.get("/v1/credits/balance")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_the_balance_reports_four_terms(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="included", amount=10, expires_at=_days(30))
    _reserve_via_service(client, tenant, 3)

    body = client.get("/v1/credits/balance", headers=_auth(admin_key)).json()
    assert set(body) >= {"available", "held", "consumed", "forfeited", "granted", "grants"}
    assert (body["available"], body["held"], body["consumed"], body["forfeited"]) == (7, 3, 0, 0)
    assert body["granted"] == (
        body["available"] + body["held"] + body["consumed"] + body["forfeited"]
    )


def test_the_balance_shows_forfeited_after_an_allowance_expires(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="included", amount=10, expires_at=_days(1))
    _reserve_via_service(client, tenant, 4)

    engine = client.app.state.engine
    with tenant_transaction(engine, tenant) as conn:
        conn.execute(
            text("UPDATE credit_grants SET expires_at = now() - interval '1 minute'")
        )

    body = client.get("/v1/credits/balance", headers=_auth(admin_key)).json()
    # The allowance is gone: what was held is now forfeited, not available.
    assert body["available"] == 0
    assert body["forfeited"] == 6
    assert body["granted"] == (
        body["available"] + body["held"] + body["consumed"] + body["forfeited"]
    )


def test_provisioning_a_grant_needs_admin_and_control_plane_path(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    resp = client.post(
        "/v1/admin/credits/grants",
        headers={**_auth(admin_key), "Idempotency-Key": "grant-0001"},
        json={"kind": "purchased", "amount": 20, "source_ref": "invoice-1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["amount"] == 20
    assert body["expires_at"] is None

    balance = client.get("/v1/credits/balance", headers=_auth(admin_key)).json()
    assert balance["available"] == 20


def test_a_grant_replay_creates_one_grant(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    payload = {"kind": "purchased", "amount": 5}
    first = client.post(
        "/v1/admin/credits/grants",
        headers={**_auth(admin_key), "Idempotency-Key": "grant-replay"},
        json=payload,
    )
    for _ in range(4):
        again = client.post(
            "/v1/admin/credits/grants",
            headers={**_auth(admin_key), "Idempotency-Key": "grant-replay"},
            json=payload,
        )
        assert again.status_code == 201
        assert again.json()["grant_id"] == first.json()["grant_id"]

    balance = client.get("/v1/credits/balance", headers=_auth(admin_key)).json()
    assert balance["available"] == 5, "a replayed grant added credits again"


def test_an_included_allowance_must_expire_and_a_pack_must_not(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    missing = client.post(
        "/v1/admin/credits/grants",
        headers={**_auth(admin_key), "Idempotency-Key": "grant-bad-1"},
        json={"kind": "included", "amount": 10},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "payload_invalid"

    expiring_pack = client.post(
        "/v1/admin/credits/grants",
        headers={**_auth(admin_key), "Idempotency-Key": "grant-bad-2"},
        json={"kind": "purchased", "amount": 10, "expires_at": _days(5).isoformat()},
    )
    assert expiring_pack.status_code == 422
    assert "never expire" in expiring_pack.json()["error"]["message"]


def test_an_observe_only_key_cannot_provision_credits(
    client: TestClient, database, tenant: uuid.UUID
) -> None:
    observe_only = bootstrap_key(database, tenant, scopes=("observe",))
    resp = client.post(
        "/v1/admin/credits/grants",
        headers={**_auth(observe_only), "Idempotency-Key": "grant-refused"},
        json={"kind": "purchased", "amount": 1},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"


def test_there_is_no_http_route_for_reservations(client: TestClient, admin_key) -> None:
    """Holding credits is engine-internal: no route, so no key can hold them."""
    for path, payload in (
        ("/v1/credits/reservations", {"amount": 1, "operation_ref": "op"}),
        ("/v1/credits/reservations/00000000-0000-0000-0000-000000000000/settle", {"amount": 1}),
        ("/v1/credits/reservations/00000000-0000-0000-0000-000000000000/release", None),
    ):
        resp = client.post(
            path, headers={**_auth(admin_key), "Idempotency-Key": "no-route"}, json=payload
        )
        assert resp.status_code == 404, path
        assert resp.json()["error"]["code"] == "route_unknown"


def test_the_openapi_contract_names_both_credit_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/credits/balance" in paths
    assert "/v1/admin/credits/grants" in paths
    assert "/v1/credits/reservations" not in paths
