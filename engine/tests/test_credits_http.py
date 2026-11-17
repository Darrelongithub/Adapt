"""The credit routes over HTTP: the shapes, the codes, and the replays.

The interesting assertions are about sameness. A replay must return the body
the first call returned — not a freshly computed balance that happens to look
similar — and an exhausted balance must answer with the documented code from
INTEGRATION.md §6 (`quota_exhausted`, 402), because the host's error handling
is written against that table.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
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


def _reserve(client, key, amount: int, idem: str, ref: str = "op-1"):
    return client.post(
        "/v1/credits/reservations",
        headers={**_auth(key), "Idempotency-Key": idem},
        json={"amount": amount, "operation_ref": ref},
    )


def test_a_reservation_needs_a_key(client: TestClient) -> None:
    resp = client.post(
        "/v1/credits/reservations",
        headers={"Idempotency-Key": "no-key-here"},
        json={"amount": 1, "operation_ref": "op"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_an_exhausted_balance_is_402_quota_exhausted(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=2, expires_at=None)
    resp = _reserve(client, admin_key, 5, "idem-quota")
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "quota_exhausted"
    assert resp.json()["error"]["retryable"] is False


def test_the_balance_route_reports_the_invariant(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="included", amount=10, expires_at=_days(30))
    _reserve(client, admin_key, 3, "idem-balance")

    body = client.get("/v1/credits/balance", headers=_auth(admin_key)).json()
    assert (body["available"], body["held"], body["consumed"], body["granted"]) == (7, 3, 0, 10)
    assert body["granted"] == body["available"] + body["held"] + body["consumed"]
    assert body["grants"][0]["kind"] == "included"


def _days(n: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=n)


def test_five_replays_return_one_body_and_one_ledger_row(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)

    first = _reserve(client, admin_key, 4, "idem-fixed", ref="op-replay")
    assert first.status_code == 201, first.text
    body = first.json()

    for _ in range(4):
        again = _reserve(client, admin_key, 4, "idem-fixed", ref="op-replay")
        assert again.status_code == 201
        assert again.json() == body, "a replay returned a different body"

    with tenant_transaction(client.app.state.engine, tenant) as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM credit_ledger WHERE idempotency_key = 'idem-fixed'")
        ).scalar_one()
    assert rows == 1

    balance = client.get("/v1/credits/balance", headers=_auth(admin_key)).json()
    assert (balance["available"], balance["held"]) == (6, 4)


def test_settling_more_than_held_is_409_over_http(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=20, expires_at=None)
    reservation = _reserve(client, admin_key, 2, "idem-hold").json()

    resp = client.post(
        f"/v1/credits/reservations/{reservation['reservation_id']}/settle",
        headers={**_auth(admin_key), "Idempotency-Key": "idem-overdraw"},
        json={"amount": 3},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"

    balance = client.get("/v1/credits/balance", headers=_auth(admin_key)).json()
    assert balance["consumed"] == 0
    assert balance["held"] == 2


def test_settle_then_release_is_refused(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)
    reservation = _reserve(client, admin_key, 2, "idem-seq").json()

    settled = client.post(
        f"/v1/credits/reservations/{reservation['reservation_id']}/settle",
        headers={**_auth(admin_key), "Idempotency-Key": "idem-seq-settle"},
        json={"amount": 2},
    )
    assert settled.status_code == 200
    assert settled.json()["state"] == "settled"

    released = client.post(
        f"/v1/credits/reservations/{reservation['reservation_id']}/release",
        headers={**_auth(admin_key), "Idempotency-Key": "idem-seq-release"},
    )
    assert released.status_code == 409
    assert released.json()["error"]["code"] == "conflict"


def test_release_returns_the_credits(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)
    reservation = _reserve(client, admin_key, 4, "idem-rel").json()

    released = client.post(
        f"/v1/credits/reservations/{reservation['reservation_id']}/release",
        headers={**_auth(admin_key), "Idempotency-Key": "idem-rel-release"},
    )
    assert released.status_code == 200
    assert released.json()["state"] == "released"
    assert released.json()["amount"] == 0

    balance = client.get("/v1/credits/balance", headers=_auth(admin_key)).json()
    assert (balance["available"], balance["held"], balance["consumed"]) == (10, 0, 0)


def test_moving_credits_needs_the_admin_scope(
    client: TestClient, database, tenant: uuid.UUID
) -> None:
    observe_only = bootstrap_key(database, tenant, scopes=("observe",))
    grant_credits(database, tenant, kind="purchased", amount=5, expires_at=None)

    refused = _reserve(client, observe_only, 1, "idem-scope")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "not_entitled"

    assert client.get("/v1/credits/balance", headers=_auth(observe_only)).status_code == 200


def test_a_missing_idempotency_key_is_rejected(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    grant_credits(database, tenant, kind="purchased", amount=5, expires_at=None)
    resp = client.post(
        "/v1/credits/reservations",
        headers=_auth(admin_key),
        json={"amount": 1, "operation_ref": "op"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "payload_invalid"


def test_another_tenants_reservation_is_not_addressable(
    client: TestClient, database, tenant: uuid.UUID, admin_key
) -> None:
    other = provision_tenant(database, slug=f"credit-other-{uuid.uuid4().hex[:8]}", name="Other")
    other_key = bootstrap_key(database, other, scopes=("observe", "admin"))
    grant_credits(database, tenant, kind="purchased", amount=10, expires_at=None)

    reservation = _reserve(client, admin_key, 2, "idem-mine").json()
    resp = client.post(
        f"/v1/credits/reservations/{reservation['reservation_id']}/settle",
        headers={**_auth(other_key), "Idempotency-Key": "idem-theirs"},
        json={"amount": 1},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "route_unknown"


def test_the_openapi_contract_documents_the_credit_routes(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    for path in ("/v1/credits/balance", "/v1/credits/reservations"):
        assert path in schema["paths"], path
    assert json.dumps(schema["paths"]["/v1/credits/reservations"]).count("Idempotency-Key") >= 1
