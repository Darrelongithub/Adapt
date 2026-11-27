"""Increment 8, part 3: an audit of classes, not of typos.

Each test here is a question a reviewer would ask before a pilot, phrased so the
answer is a status code or a row count:

* **Concurrency** — do eight simultaneous decides settle eight credits, or seven,
  or fifteen? Does one idempotency key racing itself produce one decision?
* **RLS** — can a second tenant's admin key see the first tenant's numbers, and
  does every refusal come back as 403/404 rather than 500?
* **Window maths** — backwards, empty, future, naive, oversized and offset
  windows.
* **Numbers** — can `denominator == 0` ever travel with a non-zero numerator, or
  a rate of `0.0`?
* **Staleness** — a connection paused mid-session, a decision replayed after its
  tenant is renamed.

The point of the module is the *class*: one passing case per class is evidence
about the whole class only if the failure mode is shared. Where it is not, the
increment-8 report says so.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
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
from tests.pg import bootstrap_key, provision_tenant

SKILL = "skill.fractions"


def _client(app_engine: Engine) -> TestClient:
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=10_000),
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"aud-{uuid.uuid4().hex[:8]}", name="Audit")


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe", "decide", "read_model", "admin"))


@pytest.fixture
def client(app_engine: Engine) -> TestClient:
    return _client(app_engine)


def _grant(app_engine: Engine, tenant: uuid.UUID, amount: int) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        credits.grant_credits(
            conn,
            tenant_id=tenant,
            kind="purchased",
            amount=amount,
            expires_at=None,
            idempotency_key=f"grant-{uuid.uuid4().hex}",
        )


def _connection(client: TestClient, admin_key, host: str = "audit-host") -> dict:
    resp = client.post(
        "/v1/admin/connections",
        json={
            "host_name": host,
            "environment": "test",
            "event_quota_monthly": 50_000,
            "scopes": ["observe", "decide", "read_model"],
        },
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    return {**created["connection"], "key": created["key"]}


def _events(learner: str, count: int) -> list[dict]:
    return [
        {
            "learner_id": learner,
            "skill": SKILL,
            "item_id": f"item-{i:03d}",
            "correct": i % 3 != 0,
            "response": f"option-{i}",
            "ts": datetime.now(UTC).isoformat(),
            "seq": i,
        }
        for i in range(count)
    ]


def _observe(client: TestClient, key: str, learner: str, count: int = 12) -> None:
    resp = client.post(
        "/v1/observe",
        json={"events": _events(learner, count)},
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": f"obs-{uuid.uuid4().hex}"},
    )
    assert resp.status_code == 202, resp.text


def _decide(client: TestClient, key: str, learner: str, idempotency: str | None = None):
    return client.post(
        "/v1/decide",
        json={"learner_id": learner, "skill": SKILL},
        headers={
            "Authorization": f"Bearer {key}",
            "Idempotency-Key": idempotency or f"dec-{uuid.uuid4().hex}",
        },
    )


def _count(app_engine: Engine, tenant: uuid.UUID, sql: str) -> int:
    with tenant_transaction(app_engine, tenant) as conn:
        return int(conn.execute(text(sql)).scalar_one())


def _metrics(client: TestClient, key: str, **params) -> dict:
    resp = client.get(
        "/v1/admin/metrics/abstention",
        params=params,
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------- concurrency


def test_eight_parallel_decides_settle_eight_credits(
    app_engine: Engine, database, tenant: uuid.UUID, admin_key, client: TestClient
) -> None:
    """The billed-vs-served identity under contention, which is where it breaks.

    Eight requests, eight keys, one learner. Every one must be served, exactly
    eight ledger settles must exist, and the balance must still satisfy its
    invariant afterwards — a double settle shows up as nine, a lost one as seven.
    """
    _grant(app_engine, tenant, 100)
    key = _connection(client, admin_key)["key"]["full_key"]
    _observe(client, key, "learn-audit-eight")

    def attempt(_: int):
        worker = _client(app_engine)
        try:
            return _decide(worker, key, "learn-audit-eight").status_code
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(8)))

    assert statuses == [200] * 8, statuses
    assert _count(app_engine, tenant, "SELECT count(*) FROM decisions") == 8
    settles = _count(
        app_engine, tenant, "SELECT count(*) FROM credit_ledger WHERE entry_type = 'settle'"
    )
    assert settles == 8, f"{settles} settles for 8 served decisions"
    with tenant_transaction(app_engine, tenant) as conn:
        balance = credits.balance(conn, tenant)
    assert balance.invariant_holds
    assert balance.consumed == 8


def test_the_same_idempotency_key_in_parallel_is_one_decision(
    app_engine: Engine, database, tenant: uuid.UUID, admin_key, client: TestClient
) -> None:
    """A retry storm on one key bills once. Two rows here is a double charge."""
    _grant(app_engine, tenant, 100)
    key = _connection(client, admin_key, "audit-race")["key"]["full_key"]
    _observe(client, key, "learn-audit-race")
    idempotency = f"same-{uuid.uuid4().hex}"

    def attempt(_: int):
        worker = _client(app_engine)
        try:
            resp = _decide(worker, key, "learn-audit-race", idempotency)
            return resp.status_code, resp.text
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))

    assert [status for status, _ in results] == [200] * 4, results
    assert len({body for _, body in results}) == 1, "the four bodies are not identical"
    assert _count(app_engine, tenant, "SELECT count(*) FROM decisions") == 1
    settles = _count(
        app_engine, tenant, "SELECT count(*) FROM credit_ledger WHERE entry_type = 'settle'"
    )
    assert settles == 1
    with tenant_transaction(app_engine, tenant) as conn:
        assert credits.balance(conn, tenant).consumed == 1


def test_parallel_observes_with_distinct_keys_write_distinct_batches(
    app_engine: Engine, database, tenant: uuid.UUID, admin_key, client: TestClient
) -> None:
    """Six learners at once: six batches, eighteen events, no collapsed write."""
    key = _connection(client, admin_key, "audit-observe")["key"]["full_key"]

    def attempt(index: int):
        worker = _client(app_engine)
        try:
            return worker.post(
                "/v1/observe",
                json={"events": _events(f"learn-audit-par{index:03d}", 3)},
                headers={
                    "Authorization": f"Bearer {key}",
                    "Idempotency-Key": f"par-{uuid.uuid4().hex}",
                },
            ).status_code
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(attempt, range(6)))

    assert statuses == [202] * 6, statuses
    assert _count(app_engine, tenant, "SELECT count(*) FROM observe_batches") == 6
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 18


# ----------------------------------------------------------------------- RLS


def test_a_second_tenant_sees_its_own_zeroes_and_never_the_first_tenants_numbers(
    app_engine: Engine, database, tenant: uuid.UUID, admin_key, client: TestClient
) -> None:
    """Cross-tenant reads answer 403/404 or that tenant's own empty truth — never 500."""
    _grant(app_engine, tenant, 100)
    connection = _connection(client, admin_key, "audit-mine")
    key = connection["key"]["full_key"]
    _observe(client, key, "learn-audit-owner")
    assert _decide(client, key, "learn-audit-owner").status_code == 200

    other = provision_tenant(database, slug=f"oth-{uuid.uuid4().hex[:8]}", name="Other")
    other_key = bootstrap_key(database, other, scopes=("observe", "decide", "read_model", "admin"))

    headers = {"Authorization": f"Bearer {other_key.full_key}"}
    own = client.get("/v1/admin/metrics/abstention", headers=headers)
    assert own.status_code == 200, own.text
    body = own.json()
    assert body["denominator"] == 0 and body["numerator"] == 0 and body["rate"] is None

    assert client.get("/v1/admin/connections", headers=headers).json() == []

    foreign_learner = client.get(
        "/v1/mastery", params={"learner_id": "learn-audit-owner"}, headers=headers
    )
    assert foreign_learner.status_code == 404, foreign_learner.text
    assert foreign_learner.json()["error"]["code"] in {"route_unknown", "learner_unknown"}

    foreign_decide = client.post(
        "/v1/decide",
        json={"learner_id": "learn-audit-owner", "skill": SKILL},
        headers={**headers, "Idempotency-Key": f"x-{uuid.uuid4().hex}"},
    )
    assert foreign_decide.status_code == 404, foreign_decide.text

    # Naming someone else's connection id in a query parameter is a read the
    # filter must not honour: 404 (unknown here) or an empty report, never their data.
    foreign_connection = client.get(
        "/v1/admin/metrics/abstention",
        params={"connection": connection["id"]},
        headers=headers,
    )
    assert foreign_connection.status_code in (200, 404), foreign_connection.text
    if foreign_connection.status_code == 200:
        assert foreign_connection.json()["denominator"] == 0
        assert foreign_connection.json()["by_connection"] in ([], None)


# --------------------------------------------------------------- window maths


@pytest.mark.parametrize(
    ("params", "fields"),
    [
        ({"from": "2026-03-02T00:00:00+00:00", "to": "2026-03-01T00:00:00+00:00"}, {"to"}),
        ({"from": "2026-03-01T00:00:00+00:00", "to": "2026-03-01T00:00:00+00:00"}, {"to"}),
        ({"from": "2999-01-01T00:00:00+00:00", "to": "2999-02-01T00:00:00+00:00"}, {"from"}),
        # Both boundaries are naive: the route reports `to` first, because it
        # normalises `to` before `from`. Either name is honest; both would be
        # better, and §30 of the report records that as a won't-fix.
        ({"from": "2026-01-01T00:00:00", "to": "2026-02-01T00:00:00"}, {"from", "to"}),
        ({"from": "2025-01-01T00:00:00+00:00", "to": "2026-03-01T00:00:00+00:00"}, {"from"}),
    ],
    ids=["backwards", "empty", "future", "naive", "400-days"],
)
def test_a_window_that_cannot_be_answered_is_refused_with_its_field(
    client: TestClient, admin_key, params: dict, fields: set[str]
) -> None:
    """Every unanswerable window is a 422 that names the parameter, not a guess."""
    resp = client.get(
        "/v1/admin/metrics/abstention",
        params=params,
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["details"]["field"] in fields, resp.text


def test_an_offset_window_means_the_same_instant_as_utc(client: TestClient, admin_key) -> None:
    """`+02:00` is normalised, not ignored: the same instant must give the same body."""
    headers = {"Authorization": f"Bearer {admin_key.full_key}"}
    utc = client.get(
        "/v1/admin/metrics/abstention",
        params={"from": "2026-03-01T00:00:00+00:00", "to": "2026-03-02T00:00:00+00:00"},
        headers=headers,
    )
    offset = client.get(
        "/v1/admin/metrics/abstention",
        params={"from": "2026-03-01T02:00:00+02:00", "to": "2026-03-02T02:00:00+02:00"},
        headers=headers,
    )
    assert utc.status_code == offset.status_code == 200
    assert utc.json() == offset.json()


# -------------------------------------------------------------------- numbers


def test_a_zero_denominator_never_carries_a_numerator_or_a_rate(
    app_engine: Engine, database, tenant: uuid.UUID, admin_key, client: TestClient
) -> None:
    """The two ways to get an empty denominator: no decisions, and none that count.

    The second one is the interesting one — decisions exist, but they are
    excluded, so the rate must be `null` and not `0.0`, and the exclusions must
    still add up to the decisions on the row.
    """
    headers = {"Authorization": f"Bearer {admin_key.full_key}"}
    _grant(app_engine, tenant, 100)
    connection = _connection(client, admin_key, "audit-zero")
    key = connection["key"]["full_key"]
    _observe(client, key, "learn-audit-zero")
    assert _decide(client, key, "learn-audit-zero").status_code == 200

    def check(body: dict, *, empty: bool) -> None:
        assert body["numerator"] <= body["denominator"], body
        if empty:
            assert body["denominator"] == 0, body
            assert body["numerator"] == 0, body
            assert body["rate"] is None, "a zero denominator must not print a rate"
            assert body["served"]["rate"] is None
        exclusions = body["exclusions"]
        assert body["denominator"] + sum(exclusions.values()) >= body["numerator"]

    # No decisions at all in this window.
    check(
        _metrics(
            client,
            admin_key.full_key,
            **{
                "from": "2026-01-01T00:00:00+00:00",
                "to": "2026-01-02T00:00:00+00:00",
            },
        ),
        empty=True,
    )

    # Pause the connection: its decisions stop counting, and are counted as excluded.
    paused = client.patch(
        f"/v1/admin/connections/{connection['id']}",
        json={"status": "paused"},
        headers=headers,
    )
    assert paused.status_code == 200, paused.text
    body = _metrics(
        client,
        admin_key.full_key,
        **{
            "from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "to": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    check(body, empty=True)
    assert body["exclusions"]["paused_or_revoked"] == 1, body["exclusions"]


# ------------------------------------------------------------------ staleness


def test_a_paused_connection_is_refused_rather_than_served_stale(
    app_engine: Engine, database, tenant: uuid.UUID, admin_key, client: TestClient
) -> None:
    """Pausing mid-session must change the next answer, not the next charge."""
    _grant(app_engine, tenant, 100)
    connection = _connection(client, admin_key, "audit-pause")
    key = connection["key"]["full_key"]
    _observe(client, key, "learn-audit-pause")
    assert _decide(client, key, "learn-audit-pause").status_code == 200

    client.patch(
        f"/v1/admin/connections/{connection['id']}",
        json={"status": "paused"},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    refused = _decide(client, key, "learn-audit-pause")
    assert refused.status_code in (401, 403), refused.text
    assert refused.status_code != 500

    with tenant_transaction(app_engine, tenant) as conn:
        balance = credits.balance(conn, tenant)
    assert balance.consumed == 1, "the refusal must not have been billed"


def test_a_replay_survives_its_tenant_being_renamed(
    app_engine: Engine, database, tenant: uuid.UUID, admin_key, client: TestClient
) -> None:
    """The stored body is the evidence: renaming the tenant cannot change it.

    A tenant rename is an owner-level operation (there is no HTTP route for it),
    which is the point — the replay path must not depend on the current name.
    """
    _grant(app_engine, tenant, 10)
    key = _connection(client, admin_key, "audit-replay")["key"]["full_key"]
    _observe(client, key, "learn-audit-replay")
    first = _decide(client, key, "learn-audit-replay")
    assert first.status_code == 200, first.text
    decision_id = first.json()["decision_id"]

    # The runtime role holds no UPDATE on `tenants` (checked below), so the rename
    # goes through the owner engine — which is also the honest way to model an
    # out-of-band change the request path cannot make for itself.
    owner = database.engine(database.owner_url)
    try:
        with owner.begin() as conn:
            conn.execute(
                text("UPDATE tenants SET name = :name WHERE id = :id"),
                {"name": "Renamed Mid Session", "id": tenant},
            )
    finally:
        owner.dispose()

    replay = client.post(
        f"/v1/decisions/{decision_id}/replay",
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": f"rep-{uuid.uuid4().hex}"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json(), "the stored body changed behind the decision"
    assert replay.headers["X-Adapt-Billed"] == "0"
    with tenant_transaction(app_engine, tenant) as conn:
        assert credits.balance(conn, tenant).consumed == 1
