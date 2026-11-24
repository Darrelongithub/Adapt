"""Increment 1: connections, pause/resume/revoke, and the webhook delivery log.

The acceptance line from AGENT_M1 §1, asserted rather than described:

    create → auth works → pause → 403 connection_paused → resume → revoke → 401

and the two review corrections: `connection_paused` is raised explicitly (a bare
403 is `not_entitled`), and the delivery log records attempts and status but
never a payload — the last one checked against the catalogue, not against a
convention.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.connections import service as connections
from adapt_engine.crypto.envelope import DecryptionFailed
from adapt_engine.crypto.store import DataKeyStore
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from adapt_engine.webhooks import dispatch, signing
from tests.conftest import make_settings
from tests.pg import TEST_KEYRING, bootstrap_key, provision_tenant

UTC_NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"conn-{uuid.uuid4().hex[:8]}", name="Connections Test")


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe", "admin"))


@pytest.fixture
def observer_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe",))


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


def _create(
    client: TestClient,
    admin_key,
    *,
    host_name: str = "acme-web",
    environment: str = "test",
    webhook_url: str | None = "https://hooks.example.test/adapt",
    scopes: list[str] | None = None,
):
    body: dict[str, object] = {
        "host_name": host_name,
        "environment": environment,
        "event_quota_monthly": 250_000,
        "scopes": scopes or ["observe"],
    }
    if webhook_url is not None:
        body["webhook_url"] = webhook_url
    return client.post("/v1/admin/connections", json=body, headers=_auth(admin_key))


def test_create_issues_a_key_that_authenticates(client: TestClient, admin_key) -> None:
    """The acceptance line's first two steps: create, then auth works."""
    resp = _create(client, admin_key)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["connection"]["status"] == "active"
    assert body["connection"]["webhook_url"] == "https://hooks.example.test/adapt"
    assert body["key"]["full_key"].startswith("ak_test_")
    assert body["webhook_secret"].startswith("whsec_")

    # The key is a real key: it resolves, and it says who it is.
    whoami = client.get(
        "/v1/whoami", headers={"Authorization": f"Bearer {body['key']['full_key']}"}
    )
    assert whoami.status_code == 200
    assert uuid.UUID(whoami.json()["tenant_id"])
    assert whoami.json()["key_id"] == body["key"]["key_id"]
    assert whoami.json()["scopes"] == ["observe"]


def test_the_secret_and_the_key_are_shown_once(
    client: TestClient, admin_key, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    """Neither is readable again: GET returns no secret, no prefix, no key."""
    created = _create(client, admin_key).json()
    connection_id = created["connection"]["id"]

    fetched = client.get(f"/v1/admin/connections/{connection_id}", headers=_auth(admin_key))
    assert fetched.status_code == 200
    text_body = fetched.text
    assert created["webhook_secret"] not in text_body
    assert created["key"]["full_key"] not in text_body
    assert "webhook_secret" not in fetched.json()

    # At rest the secret is a value envelope, not the plaintext. The owner is
    # FORCEd too, so this read needs the tenant context like every other one.
    with tenant_transaction(owner_engine, tenant) as conn:
        stored = conn.execute(
            text("SELECT webhook_secret FROM connections WHERE id = :id"),
            {"id": connection_id},
        ).scalar_one()
    assert stored.startswith("v1:dk")
    assert created["webhook_secret"] not in stored


def test_the_secret_cannot_be_swapped_between_rows(
    client: TestClient, admin_key, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    """AAD binds the secret to (tenant, this row, this column)."""
    first = _create(client, admin_key, host_name="acme-web").json()
    second = _create(client, admin_key, host_name="acme-ios").json()

    store = DataKeyStore(keyring=TEST_KEYRING)
    with tenant_transaction(owner_engine, tenant) as conn:
        rows = dict(
            conn.execute(
                text("SELECT id, webhook_secret FROM connections WHERE tenant_id = :t"),
                {"t": tenant},
            ).all()
        )
        assert len(rows) == 2
        swapped = rows[uuid.UUID(second["connection"]["id"])]
        with pytest.raises(DecryptionFailed):
            store.decrypt(
                conn,
                tenant,
                uuid.UUID(first["connection"]["id"]),
                "webhook_secret",
                swapped,
            )


def test_a_duplicate_host_and_environment_is_a_conflict(client: TestClient, admin_key) -> None:
    assert _create(client, admin_key).status_code == 201
    duplicate = _create(client, admin_key)

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    # A different environment is a different connection.
    assert _create(client, admin_key, environment="live").status_code == 201


def test_a_webhook_url_must_be_https(client: TestClient, admin_key) -> None:
    resp = _create(client, admin_key, webhook_url="http://hooks.example.test/adapt")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "payload_invalid"


def test_creating_a_connection_needs_the_admin_scope(client: TestClient, observer_key) -> None:
    resp = _create(client, observer_key)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"


def test_pause_is_an_explicit_403_and_resume_lifts_it(client: TestClient, admin_key) -> None:
    """The whole acceptance line, in one place.

    A paused connection keeps its key (pausing stops ingest and keeps data), so
    the request authenticates and is then refused with `connection_paused` — not
    `unauthorized` ("your key is wrong") and not a bare 403 ("buy something").
    """
    created = _create(client, admin_key).json()
    key_headers = {"Authorization": f"Bearer {created['key']['full_key']}"}
    connection_id = created["connection"]["id"]

    assert client.get("/v1/learners", headers=key_headers).status_code == 200

    paused = client.patch(
        f"/v1/admin/connections/{connection_id}",
        json={"status": "paused"},
        headers=_auth(admin_key),
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    refused = client.get("/v1/learners", headers=key_headers)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "connection_paused"
    assert refused.json()["error"]["retryable"] is True

    # Identity is not ingest: the key still knows who it is, and the console
    # (a key with no connection) can still resume the connection.
    assert client.get("/v1/whoami", headers=key_headers).status_code == 200
    assert client.get("/v1/learners", headers=_auth(admin_key)).status_code == 200

    resumed = client.patch(
        f"/v1/admin/connections/{connection_id}",
        json={"status": "active"},
        headers=_auth(admin_key),
    )
    assert resumed.status_code == 200
    assert resumed.json()["paused_at"] is None
    assert client.get("/v1/learners", headers=key_headers).status_code == 200


def test_revoke_stops_authentication_and_is_terminal(client: TestClient, admin_key) -> None:
    created = _create(client, admin_key).json()
    key_headers = {"Authorization": f"Bearer {created['key']['full_key']}"}
    connection_id = created["connection"]["id"]

    revoked = client.delete(f"/v1/admin/connections/{connection_id}", headers=_auth(admin_key))
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["revoked_at"] is not None

    # 401, not a fourth code: the key is revoked in the same transaction.
    assert client.get("/v1/learners", headers=key_headers).status_code == 401
    assert client.get("/v1/whoami", headers=key_headers).json()["error"]["code"] == "unauthorized"

    # Revoking is idempotent, and un-revoking is refused.
    again = client.delete(f"/v1/admin/connections/{connection_id}", headers=_auth(admin_key))
    assert again.status_code == 200
    revive = client.patch(
        f"/v1/admin/connections/{connection_id}",
        json={"status": "active"},
        headers=_auth(admin_key),
    )
    assert revive.status_code == 409
    assert revive.json()["error"]["code"] == "conflict"


def test_an_unknown_connection_is_a_documented_404(client: TestClient, admin_key) -> None:
    missing = uuid.uuid4()
    resp = client.get(f"/v1/admin/connections/{missing}", headers=_auth(admin_key))
    assert resp.status_code == 404
    # No `connection_unknown` exists in §6; the vocabulary is not extended here.
    assert resp.json()["error"]["code"] == "route_unknown"


def test_state_changes_are_audited(
    client: TestClient, admin_key, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    created = _create(client, admin_key).json()
    connection_id = created["connection"]["id"]
    client.patch(
        f"/v1/admin/connections/{connection_id}",
        json={"status": "paused"},
        headers=_auth(admin_key),
    )
    client.delete(f"/v1/admin/connections/{connection_id}", headers=_auth(admin_key))

    with tenant_transaction(owner_engine, tenant) as conn:
        actions = [
            row[0]
            for row in conn.execute(
                text("SELECT action FROM audit_log WHERE target = :target ORDER BY id"),
                {"target": connection_id},
            ).all()
        ]
    assert actions == ["connection_created", "connection_paused", "connection_revoked"]


def test_the_delivery_log_has_no_place_for_a_payload(owner_engine: Engine) -> None:
    """Enforced by the catalogue, not by everyone remembering.

    A webhook is a notification that something happened. If a body column
    existed, "we never store payloads" would be a code review habit; with the
    column absent, it is a property of the schema.
    """
    forbidden = ("body", "payload", "data", "learner_id", "skill", "response")
    with owner_engine.connect() as conn:
        for table in ("webhook_deliveries", "webhook_attempts"):
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = 'public' AND table_name = :t"
                    ),
                    {"t": table},
                ).all()
            }
            assert not columns & set(forbidden), f"{table} stores {columns & set(forbidden)}"


class FakeTransport:
    """Counts attempts and answers whatever the test asks for."""

    def __init__(self, outcomes: list[int | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post(self, url: str, headers: dict[str, str], body: bytes):
        self.calls.append((url, headers, body))
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if isinstance(outcome, Exception):
            raise outcome
        return dispatch.TransportResult(status_code=outcome, duration_ms=7)


@pytest.fixture
def dispatcher(owner_engine) -> dispatch.Dispatcher:
    return dispatch.Dispatcher(
        FakeTransport([]),
        target_for=lambda conn, cid: connections.webhook_target(conn, _STORE, cid),
    )


_STORE = DataKeyStore(keyring=TEST_KEYRING)


def _enqueue(conn, tenant: uuid.UUID, connection_id: uuid.UUID, *, key: str = "dcn_1"):
    return dispatch.enqueue(
        conn,
        tenant_id=tenant,
        connection_id=connection_id,
        event_type="decision.created",
        subject_id=key,
        now=UTC_NOW,
    )


def test_a_duplicate_enqueue_is_a_no_op(
    client: TestClient, admin_key, tenant: uuid.UUID, owner_engine: Engine, dispatcher
) -> None:
    """One event, one row, one delivery — however many times it is enqueued."""
    connection_id = uuid.UUID(_create(client, admin_key).json()["connection"]["id"])

    with tenant_transaction(owner_engine, tenant) as conn:
        first = _enqueue(conn, tenant, connection_id)
        second = _enqueue(conn, tenant, connection_id)
        count = conn.execute(text("SELECT count(*) FROM webhook_deliveries")).scalar_one()

    assert first.created is True
    assert second.created is False
    assert second.id == first.id
    assert second.webhook_id == first.webhook_id
    assert count == 1


def test_a_failing_receiver_gets_five_attempts_then_fails(
    client: TestClient, admin_key, tenant: uuid.UUID, owner_engine: Engine, dispatcher
) -> None:
    """5 attempts, exponential backoff, and a log that says what happened."""
    connection_id = uuid.UUID(_create(client, admin_key).json()["connection"]["id"])
    transport = FakeTransport([500, 503, 500, 429, 500])
    dispatcher._transport = transport

    with tenant_transaction(owner_engine, tenant) as conn:
        delivery = _enqueue(conn, tenant, connection_id)

        at = UTC_NOW
        schedule = []
        for _ in range(dispatch.MAX_ATTEMPTS):
            results = dispatcher.run(conn, now=at)
            assert len(results) == 1
            row = conn.execute(
                text("SELECT status, attempts, next_attempt_at FROM webhook_deliveries")
            ).one()
            schedule.append((row.attempts, row.next_attempt_at, at))
            if row.next_attempt_at is not None:
                at = row.next_attempt_at
            if row.status == "failed":
                break

        final = conn.execute(
            text(
                "SELECT status, attempts, last_status_code, last_error"
                " FROM webhook_deliveries WHERE id = :id"
            ),
            {"id": delivery.id},
        ).one()
        attempts = conn.execute(
            text(
                "SELECT attempt, status_code FROM webhook_attempts"
                " WHERE delivery_id = :id ORDER BY attempt"
            ),
            {"id": delivery.id},
        ).all()

    assert final.status == "failed"
    assert final.attempts == 5
    assert final.last_status_code == 500
    assert [row.attempt for row in attempts] == [1, 2, 3, 4, 5]
    assert [row.status_code for row in attempts] == [500, 503, 500, 429, 500]
    # Backoff measured from the attempt that failed: 1s, 4s, 16s, 64s.
    waits = [(next_at - at).total_seconds() for _, next_at, at in schedule[:4]]
    assert waits == [1, 4, 16, 64]
    assert schedule[-1][1] is None


def test_a_delivery_that_succeeds_on_retry_is_delivered(
    client: TestClient, admin_key, tenant: uuid.UUID, owner_engine: Engine, dispatcher
) -> None:
    connection_id = uuid.UUID(_create(client, admin_key).json()["connection"]["id"])
    transport = FakeTransport([500, 202])
    dispatcher._transport = transport

    with tenant_transaction(owner_engine, tenant) as conn:
        delivery = _enqueue(conn, tenant, connection_id)
        dispatcher.run(conn, now=UTC_NOW)
        row = conn.execute(
            text("SELECT status, next_attempt_at FROM webhook_deliveries WHERE id = :id"),
            {"id": delivery.id},
        ).one()
        assert row.status == "pending"
        dispatcher.run(conn, now=row.next_attempt_at)
        final = conn.execute(
            text("SELECT status, attempts, delivered_at FROM webhook_deliveries WHERE id = :id"),
            {"id": delivery.id},
        ).one()

    assert final.status == "delivered"
    assert final.attempts == 2
    assert final.delivered_at is not None
    # The same message id on both attempts: a retry is not a new event.
    assert {call[1]["webhook-id"] for call in transport.calls} == {delivery.webhook_id}


def test_what_we_send_is_what_a_receiver_verifies(
    client: TestClient, admin_key, tenant: uuid.UUID, owner_engine: Engine, dispatcher
) -> None:
    """Our sender and our verifier agree, over the real headers and body."""
    created = _create(client, admin_key).json()
    connection_id = uuid.UUID(created["connection"]["id"])
    transport = FakeTransport([200])
    dispatcher._transport = transport

    with tenant_transaction(owner_engine, tenant) as conn:
        _enqueue(conn, tenant, connection_id)
        dispatcher.run(conn, now=UTC_NOW)

    url, headers, body = transport.calls[0]
    assert url == "https://hooks.example.test/adapt"
    verified = signing.verify(
        created["webhook_secret"],
        headers,
        body,
        now=int(UTC_NOW.timestamp()),
        seen=signing.MemorySeenIds(),
    )
    assert verified.outcome is signing.Outcome.DELIVER
    # The body carries ids and types, not learner data.
    payload = json.loads(body)
    assert set(payload) == {"id", "type", "created_at", "data"}
    assert payload["type"] == "decision.created"
    assert "learner" not in body.decode()


def test_the_delivery_log_is_visible_without_a_payload(
    client: TestClient, admin_key, tenant: uuid.UUID, owner_engine: Engine, dispatcher
) -> None:
    connection_id = uuid.UUID(_create(client, admin_key).json()["connection"]["id"])
    dispatcher._transport = FakeTransport([500, 200])

    with tenant_transaction(owner_engine, tenant) as conn:
        _enqueue(conn, tenant, connection_id)
        dispatcher.run(conn, now=UTC_NOW)
        row = conn.execute(text("SELECT next_attempt_at FROM webhook_deliveries")).one()
        dispatcher.run(conn, now=row.next_attempt_at)

    log = client.get(f"/v1/admin/connections/{connection_id}/deliveries", headers=_auth(admin_key))
    assert log.status_code == 200
    entries = log.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["status"] == "delivered"
    assert entry["attempts"] == 2
    assert [attempt["status_code"] for attempt in entry["attempt_log"]] == [500, 200]
    assert "body" not in entry and "payload" not in entry


def test_connections_are_tenant_scoped(
    client: TestClient, database, admin_key, tenant: uuid.UUID
) -> None:
    """RLS, from the outside: another tenant's connection is not in the list."""
    _create(client, admin_key)
    other_tenant = provision_tenant(
        database, slug=f"conn-other-{uuid.uuid4().hex[:8]}", name="Other"
    )
    other_key = bootstrap_key(database, other_tenant, scopes=("observe", "admin"))

    mine = client.get("/v1/admin/connections", headers=_auth(admin_key)).json()
    theirs = client.get("/v1/admin/connections", headers=_auth(other_key)).json()

    assert len(mine) == 1
    assert theirs == []


def test_a_connection_key_cannot_mint_an_admin_key(client: TestClient, admin_key) -> None:
    """The subset rule still applies through the connection route."""
    resp = _create(client, admin_key, scopes=["observe", "admin"])
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "not_entitled"


def test_a_paused_connection_is_not_paused_for_the_console(client: TestClient, admin_key) -> None:
    """The console key has no connection, so nothing about it can be paused."""
    created = _create(client, admin_key).json()
    connection_id = created["connection"]["id"]
    client.patch(
        f"/v1/admin/connections/{connection_id}",
        json={"status": "paused"},
        headers=_auth(admin_key),
    )

    assert client.get("/v1/admin/connections", headers=_auth(admin_key)).status_code == 200
    assert client.get("/v1/learners", headers=_auth(admin_key)).status_code == 200
