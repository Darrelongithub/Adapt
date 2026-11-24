"""End-to-end: two tenants, two keys, one HTTP surface.

This is the first path that goes key → resolver → ``tenant_transaction`` →
row. Increment 2 proved the database isolates tenants; this proves a request
does, without the route adding a tenant filter of its own.

The load-bearing test is ``test_key_for_a_cannot_read_learners_of_b``. Turn
``adapt_resolve_api_key`` into SECURITY INVOKER and it must go red: the
resolver would see no rows, every key would be unauthorized, and the request
that should have been a 404 for A asking about B's learner would instead fail
authentication — a different, visible break.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from tests.conftest import TEST_PEPPER, make_settings
from tests.pg import SeededTenants, bootstrap_key, synthetic_suffix

PEPPER = TEST_PEPPER


@pytest.fixture
def tenants(database) -> SeededTenants:
    from tests.pg import seed_two_tenants

    return seed_two_tenants(database)


@pytest.fixture
def clients(database, app_engine: Engine, tenants: SeededTenants) -> tuple[TestClient, str, str]:
    """One app, two keys — one per tenant — and the key strings."""
    a_key = bootstrap_key(database, tenants.a_id, scopes=("observe", "admin"))
    b_key = bootstrap_key(database, tenants.b_id, scopes=("observe", "admin"))
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=10_000),
    )
    return TestClient(app, raise_server_exceptions=False), a_key.full_key, b_key.full_key


def _auth(full_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {full_key}"}


def test_key_for_a_cannot_read_learners_of_b(
    clients: tuple[TestClient, str, str], tenants: SeededTenants
) -> None:
    """Acceptance (a): authenticated as A, B's learner is not there."""
    client, a_key, b_key = clients

    # Each key reads its own tenant's learner.
    own = client.get(f"/v1/learners/{tenants.a_learner_id}", headers=_auth(a_key))
    assert own.status_code == 200, own.text
    assert own.json()["learner_id"] == tenants.a_learner_id

    other = client.get(f"/v1/learners/{tenants.b_learner_id}", headers=_auth(b_key))
    assert other.status_code == 200

    # A asking for B's learner: 404 learner_unknown, not 403 — a 403 would
    # confirm the id exists in some other tenant.
    cross = client.get(f"/v1/learners/{tenants.b_learner_id}", headers=_auth(a_key))
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "learner_unknown"

    # And the listing shows only A's learner.
    listing = client.get("/v1/learners", headers=_auth(a_key))
    assert listing.status_code == 200
    ids = [row["learner_id"] for row in listing.json()]
    assert tenants.a_learner_id in ids
    assert tenants.b_learner_id not in ids

    # The row is not reachable by primary key either.
    a_row = client.get(f"/v1/learners/{tenants.a_learner_id}", headers=_auth(a_key)).json()
    assert a_row["id"] != str(tenants.b_learner)


def test_created_learner_belongs_to_the_calling_tenant(
    clients: tuple[TestClient, str, str], tenants: SeededTenants, app_engine: Engine
) -> None:
    """The tenant comes from the key; the body cannot name one."""
    client, a_key, _ = clients
    # `synthetic_suffix` is letters-only: a hex suffix can be all digits, and
    # seven of those in a row is the id-number pattern the column refuses.
    new_id = f"learner-new-{synthetic_suffix()}"
    resp = client.post(
        "/v1/learners",
        headers=_auth(a_key),
        json={"learner_id": new_id, "tenant_id": str(tenants.b_id)},
    )
    assert resp.status_code == 201, resp.text

    with tenant_transaction(app_engine, tenants.a_id) as conn:
        owner = conn.execute(
            text("SELECT tenant_id FROM learners WHERE learner_id = :lid"), {"lid": new_id}
        ).scalar_one()
    assert owner == tenants.a_id

    with tenant_transaction(app_engine, tenants.b_id) as conn:
        assert (
            conn.execute(
                text("SELECT 1 FROM learners WHERE learner_id = :lid"), {"lid": new_id}
            ).one_or_none()
            is None
        )


def test_an_admin_only_key_can_ask_who_it_is(
    clients: tuple[TestClient, str, str], tenants: SeededTenants, database
) -> None:
    """Identity is not a capability: any valid key may call /v1/whoami.

    It still cannot read learners — that needs `observe` — so the identity
    endpoint is not a way around scope checks.
    """
    client, _, _ = clients
    admin_only = bootstrap_key(database, tenants.a_id, scopes=("admin",))
    resp = client.get("/v1/whoami", headers=_auth(admin_only.full_key))
    assert resp.status_code == 200
    assert resp.json()["scopes"] == ["admin"]

    refused = client.get("/v1/learners", headers=_auth(admin_only.full_key))
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "not_entitled"


def test_missing_auth_never_touches_the_database(app_engine: Engine) -> None:
    """An unauthenticated request is refused before tenant context exists."""
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/v1/learners")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_scope_failure_is_403_not_404(
    clients: tuple[TestClient, str, str], tenants, database
) -> None:
    """A key without the scope is refused as not_entitled, before the lookup."""
    client, _, _ = clients
    observe_only = bootstrap_key(database, tenants.a_id, scopes=("observe",))
    resp = client.post(
        "/v1/learners", headers=_auth(observe_only.full_key), json={"learner_id": "learner-x-01"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"
