"""Increment 2: the observe batch contract.

AGENT_M1 §2 and the seven acceptance points, each as a test rather than a
paragraph. The interesting ones are the ones that are easy to get subtly wrong:

* a duplicate batch returns *the original response* — so the original response
  is stored, and the test compares the two JSON documents;
* an abstention is not an error — an event with no `decision_id` is accepted,
  counted, and never billed;
* a paused connection's events are **refused, not dropped**: refused means the
  store is untouched and the same `Idempotency-Key` still works after resume;
* quota is counted at ingest and refused with `resets_at`, and the refusal does
  not break the host's loop.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from tests.conftest import make_settings
from tests.pg import bootstrap_key, provision_tenant

NOW = datetime.now(UTC)

#: The session fixture, addressed relative to this file so the suite runs from
#: any working directory (the gate runs from `engine/`, a reader may not).
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"obs-{uuid.uuid4().hex[:8]}", name="Observe Test")


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


@pytest.fixture
def connection(client: TestClient, admin_key):
    """A connection with a generous quota, plus its key."""
    resp = client.post(
        "/v1/admin/connections",
        json={
            "host_name": f"host-{uuid.uuid4().hex[:6]}",
            "environment": "test",
            "event_quota_monthly": 250_000,
            "scopes": ["observe"],
        },
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _scoped_connection(client: TestClient, admin_key, *, quota: int, scopes: list[str]):
    resp = client.post(
        "/v1/admin/connections",
        json={
            "host_name": f"host-{uuid.uuid4().hex[:6]}",
            "environment": "test",
            "event_quota_monthly": quota,
            "scopes": scopes,
        },
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _headers(connection, key: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key or connection['key']['full_key']}",
        "Idempotency-Key": f"batch-{uuid.uuid4().hex}",
    }


def event(
    *,
    learner_id: str = "learn-synthetic-001",
    skill: str = "skill.fractions",
    item_id: str = "item-001",
    correct: bool = True,
    ts: datetime | None = None,
    **extra: object,
) -> dict[str, object]:
    body: dict[str, object] = {
        "learner_id": learner_id,
        "skill": skill,
        "item_id": item_id,
        "correct": correct,
        "ts": (ts or NOW).isoformat(),
    }
    body.update(extra)
    return body


def _post(client: TestClient, connection, events: list[object], **kwargs: object):
    headers = kwargs.pop("headers", _headers(connection))
    return client.post("/v1/observe", json={"events": events}, headers=headers, **kwargs)


def _count(app_engine: Engine, tenant: uuid.UUID, sql: str, **params: object) -> int:
    with tenant_transaction(app_engine, tenant) as conn:
        return int(conn.execute(text(sql), params).scalar_one())


# --- the happy path -------------------------------------------------------


def test_a_batch_is_accepted_and_returns_the_counts(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    resp = _post(
        client,
        connection,
        [
            event(item_id="i-1"),
            event(item_id="i-2", correct=False),
            event(item_id="i-3", decision_id="dcn_synthetic_0001"),
        ],
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["accepted"] == 3
    assert body["duplicates"] == 0
    assert body["usage"]["used"] == 3
    assert body["usage"]["quota"] == 250_000
    assert body["usage"]["remaining"] == 249_997
    assert body["usage"]["resets_at"].endswith("+00:00")
    assert uuid.UUID(body["batch_id"])

    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 3
    assert _count(app_engine, tenant, "SELECT count(*) FROM connection_event_usage") == 1


def test_quota_is_counted_at_ingest_not_at_read_time(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """The counter is a row, written by the same statement that accepts the batch."""
    _post(client, connection, [event(item_id="i-1"), event(item_id="i-2")])
    used = _count(app_engine, tenant, "SELECT sum(events) FROM connection_event_usage")
    assert used == 2


# --- idempotency ----------------------------------------------------------


def test_the_same_batch_twice_returns_the_original_response_and_writes_nothing(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    headers = _headers(connection)
    events = [event(item_id="i-1"), event(item_id="i-2")]

    first = client.post("/v1/observe", json={"events": events}, headers=headers)
    second = client.post("/v1/observe", json={"events": events}, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    # Byte-identical: the replay is the stored response, not a re-derivation.
    assert second.json() == first.json()
    assert second.json()["batch_id"] == first.json()["batch_id"]

    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 2
    assert _count(app_engine, tenant, "SELECT sum(events) FROM connection_event_usage") == 2
    assert _count(app_engine, tenant, "SELECT count(*) FROM observe_batches") == 1


def test_the_same_key_with_a_different_body_is_a_conflict(client: TestClient, connection) -> None:
    headers = _headers(connection)
    first = client.post("/v1/observe", json={"events": [event()]}, headers=headers)
    assert first.status_code == 202

    second = client.post(
        "/v1/observe", json={"events": [event(item_id="different")]}, headers=headers
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"
    assert "Idempotency-Key" in second.json()["error"]["message"]


def test_keys_are_scoped_to_a_tenant(client: TestClient, connection, admin_key, database) -> None:
    """Two tenants may use the same Idempotency-Key without colliding."""
    other = provision_tenant(database, slug=f"obs-{uuid.uuid4().hex[:8]}", name="Other")
    other_admin = bootstrap_key(database, other, scopes=("observe", "admin"))
    other_connection = _scoped_connection(client, other_admin, quota=100, scopes=["observe"])

    shared = f"batch-shared-{uuid.uuid4().hex}"
    first = client.post(
        "/v1/observe",
        json={"events": [event()]},
        headers={
            "Authorization": f"Bearer {connection['key']['full_key']}",
            "Idempotency-Key": shared,
        },
    )
    second = client.post(
        "/v1/observe",
        json={"events": [event()]},
        headers={
            "Authorization": f"Bearer {other_connection['key']['full_key']}",
            "Idempotency-Key": shared,
        },
    )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["batch_id"] != second.json()["batch_id"]


# --- the allowlist --------------------------------------------------------


def test_an_unknown_key_in_an_event_is_rejected_and_named(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    resp = _post(
        client,
        connection,
        [event(item_id="i-1"), event(item_id="i-2", learner_email="a@example.test")],
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "payload_invalid"
    assert "learner_email" in error["message"]
    assert "event 1" in error["message"]
    # And nothing was written: a partial batch is silent data loss.
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 0
    assert _count(app_engine, tenant, "SELECT count(*) FROM observe_batches") == 0


def test_an_unknown_key_in_the_envelope_is_rejected(client: TestClient, connection) -> None:
    resp = client.post(
        "/v1/observe",
        json={"events": [event()], "learner_email": "a@example.test"},
        headers=_headers(connection),
    )
    assert resp.status_code == 422
    assert "learner_email" in resp.json()["error"]["message"]


def test_a_missing_required_field_names_the_field(client: TestClient, connection) -> None:
    broken = event()
    del broken["skill"]
    resp = _post(client, connection, [broken])
    assert resp.status_code == 422
    assert "skill" in resp.json()["error"]["message"]


def test_a_response_that_looks_like_an_email_is_refused(client: TestClient, connection) -> None:
    resp = _post(client, connection, [event(learner_id="ada@example.test")])
    assert resp.status_code == 422
    assert "learner_id" in resp.json()["error"]["message"]


def test_events_without_a_decision_id_are_accepted(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """An abstention is not an error: no decision id, still a real event."""
    resp = _post(client, connection, [event(item_id="i-1"), event(item_id="i-2")])
    assert resp.status_code == 202
    assert resp.json()["accepted"] == 2
    assert resp.json()["decision_attribution"]["share"] == 0.0
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 2


def test_decision_id_coverage_is_measured_not_asserted(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """The number is reported; the 95% line is a contract target, not a gate."""
    events = [
        event(item_id=f"i-{n}", decision_id=f"dcn_synthetic_{n:04d}")
        if n < 15
        else event(item_id=f"i-{n}")
        for n in range(20)
    ]
    body = _post(client, connection, events).json()
    attribution = body["decision_attribution"]
    assert attribution["events"] == 20
    assert attribution["with_decision_id"] == 15
    assert attribution["share"] == 0.75
    stored = _count(
        app_engine, tenant, "SELECT count(*) FROM learning_events WHERE decision_id IS NOT NULL"
    )
    assert stored == 15


def test_an_event_for_an_abstained_decision_is_counted_and_never_billed(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """No decision row, no charge: observe never writes a ledger row."""
    before = _count(app_engine, tenant, "SELECT count(*) FROM credit_ledger")
    resp = _post(
        client,
        connection,
        [event(item_id="i-1", decision_id="dcn_that_was_abstained_or_expired")],
    )
    assert resp.status_code == 202
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 1
    assert _count(app_engine, tenant, "SELECT count(*) FROM credit_ledger") == before


# --- caps -----------------------------------------------------------------


def test_more_than_five_hundred_events_is_413(client: TestClient, connection) -> None:
    events = [event(item_id=f"i-{n}") for n in range(501)]
    resp = _post(client, connection, events)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_five_hundred_events_is_allowed(client: TestClient, connection) -> None:
    events = [event(item_id=f"i-{n}") for n in range(500)]
    resp = _post(client, connection, events)
    assert resp.status_code == 202, resp.text
    assert resp.json()["accepted"] == 500


def test_a_body_over_one_megabyte_is_413(client: TestClient, connection) -> None:
    # A single event whose free-text response is large enough to blow the cap.
    events = [event(item_id=f"i-{n}", response="x" * 4000) for n in range(280)]
    resp = _post(client, connection, events)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_an_empty_batch_is_refused(client: TestClient, connection) -> None:
    resp = _post(client, connection, [])
    assert resp.status_code == 422


def test_a_missing_idempotency_key_is_refused(client: TestClient, connection) -> None:
    resp = client.post(
        "/v1/observe",
        json={"events": [event()]},
        headers={"Authorization": f"Bearer {connection['key']['full_key']}"},
    )
    assert resp.status_code == 422
    assert "Idempotency-Key" in resp.json()["error"]["message"]


# --- quota ----------------------------------------------------------------


def test_over_quota_is_402_with_resets_at_and_writes_nothing(
    client: TestClient, admin_key, app_engine: Engine
) -> None:
    small = _scoped_connection(client, admin_key, quota=5, scopes=["observe"])

    ok = _post(client, small, [event(item_id=f"i-{n}") for n in range(5)])
    assert ok.status_code == 202

    refused = _post(client, small, [event(item_id="i-over")])
    assert refused.status_code == 402
    error = refused.json()["error"]
    assert error["code"] == "quota_exhausted"
    assert error["retryable"] is False
    assert "resets_at" in error["message"]
    assert refused.headers["X-Adapt-Resets-At"].endswith("+00:00")

    # Nothing from the refused batch was stored, and the counter did not move.
    with tenant_transaction(app_engine, uuid.UUID(_tenant_of(client, admin_key))) as conn:
        assert (
            int(conn.execute(text("SELECT sum(events) FROM connection_event_usage")).scalar_one())
            == 5
        )
        assert int(conn.execute(text("SELECT count(*) FROM learning_events")).scalar_one()) == 5


def test_the_loop_does_not_break_when_the_quota_is_gone(client: TestClient, admin_key) -> None:
    """The host can still read what it already has; it just cannot buy more."""
    small = _scoped_connection(client, admin_key, quota=1, scopes=["observe"])
    assert _post(client, small, [event(item_id="i-1")]).status_code == 202
    assert _post(client, small, [event(item_id="i-2")]).status_code == 402

    # Reading the learner history still works with the same key.
    learners = client.get(
        "/v1/learners", headers={"Authorization": f"Bearer {small['key']['full_key']}"}
    )
    assert learners.status_code == 200
    assert learners.json() == []


def _tenant_of(client: TestClient, admin_key) -> str:
    whoami = client.get("/v1/whoami", headers={"Authorization": f"Bearer {admin_key.full_key}"})
    return str(whoami.json()["tenant_id"])


# --- paused and unauthorised ---------------------------------------------


def test_a_paused_connection_cannot_ingest_and_nothing_is_stored(
    client: TestClient, connection, admin_key, app_engine: Engine, tenant: uuid.UUID
) -> None:
    paused = client.patch(
        f"/v1/admin/connections/{connection['connection']['id']}",
        json={"status": "paused"},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert paused.status_code == 200, paused.text

    headers = _headers(connection)
    refused = client.post("/v1/observe", json={"events": [event()]}, headers=headers)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "connection_paused"

    # Refused, not dropped: no event, no batch row, no quota consumed — so the
    # same Idempotency-Key still works once the connection is resumed.
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 0
    assert _count(app_engine, tenant, "SELECT count(*) FROM observe_batches") == 0
    assert _count(app_engine, tenant, "SELECT count(*) FROM connection_event_usage") == 0

    resumed = client.patch(
        f"/v1/admin/connections/{connection['connection']['id']}",
        json={"status": "active"},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resumed.status_code == 200, resumed.text
    retried = client.post("/v1/observe", json={"events": [event()]}, headers=headers)
    assert retried.status_code == 202, retried.text


def test_a_console_key_cannot_ingest(client: TestClient, admin_key) -> None:
    """A key with no connection has no quota to spend and no rows to attribute."""
    resp = client.post(
        "/v1/observe",
        json={"events": [event()]},
        headers={
            "Authorization": f"Bearer {admin_key.full_key}",
            "Idempotency-Key": "batch-console-key-0001",
        },
    )
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "not_entitled"
    # §6 puts the structured value in `details`, not in the message.
    assert error["details"] == {"tier_required": "core"}
    assert "tier_required" not in error["message"]


def test_the_route_declares_the_observe_scope(client: TestClient) -> None:
    """A connection key can only ever hold `observe`, so the scope is the gate.

    (The `admin` scope is not mintable onto a connection — increment 1 — which
    is why the "wrong scope" case is the console key, not a connection key with
    the other scope.)
    """
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/observe"]["post"]
    assert operation["security"] == [{"APIKeyAuth": ["observe"]}]


def test_the_connection_field_must_match_the_key(client: TestClient, connection) -> None:
    resp = client.post(
        "/v1/observe",
        json={"connection": str(uuid.uuid4()), "events": [event()]},
        headers=_headers(connection),
    )
    assert resp.status_code == 422
    assert "connection" in resp.json()["error"]["message"]

    matching = client.post(
        "/v1/observe",
        json={"connection": connection["connection"]["id"], "events": [event()]},
        headers=_headers(connection),
    )
    assert matching.status_code == 202, matching.text


# --- isolation and storage ------------------------------------------------


def test_events_are_invisible_to_another_tenant(client: TestClient, connection, database) -> None:
    _post(client, connection, [event(), event(item_id="i-2")])
    other = provision_tenant(database, slug=f"obs-{uuid.uuid4().hex[:8]}", name="Other")

    from tests import pg

    engine = pg.engine_for(database, "app")
    try:
        with tenant_transaction(engine, other) as conn:
            assert int(conn.execute(text("SELECT count(*) FROM learning_events")).scalar_one()) == 0
    finally:
        engine.dispose()


def test_the_free_text_answer_is_hashed_and_not_stored(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    answer = "the answer the learner typed"
    resp = _post(client, connection, [event(item_id="i-1", response=answer)])
    assert resp.status_code == 202

    with tenant_transaction(app_engine, tenant) as conn:
        row = conn.execute(
            text("SELECT response_digest, response_length FROM learning_events")
        ).one()
    assert row.response_length == len(answer)
    assert row.response_digest == hashlib.sha256(answer.encode()).digest()
    assert answer.encode() not in row.response_digest


def test_an_event_outside_the_window_is_refused(client: TestClient, connection) -> None:
    resp = _post(client, connection, [event(ts=NOW - timedelta(days=500))])
    assert resp.status_code == 422
    assert "ts" in resp.json()["error"]["message"]


def test_a_naive_timestamp_is_refused(client: TestClient, connection) -> None:
    naive = event()
    naive["ts"] = "2026-11-17T12:00:00"
    resp = _post(client, connection, [naive])
    assert resp.status_code == 422
    assert "timezone" in resp.json()["error"]["message"]


def test_the_default_partition_stays_empty(
    client: TestClient, connection, owner_engine: Engine
) -> None:
    """An event outside the created window would land here; the alert is that it is non-empty."""
    _post(client, connection, [event(item_id="i-1")])
    # The default partition is a table of its own, so the tenant policy on the
    # parent does not cover this read; it is a count, not a row.
    with owner_engine.begin() as conn:
        count = int(conn.execute(text("SELECT count(*) FROM learning_events_default")).scalar_one())
    assert count == 0


# --- duplicates and flags -------------------------------------------------


def test_duplicate_events_inside_a_batch_are_counted_not_inserted(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    first = event(item_id="i-1", session_id="sess-1", seq=1)
    again = dict(first)
    body = _post(client, connection, [first, again]).json()

    assert body["accepted"] == 1
    assert body["duplicates"] == 1
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 1
    # And the duplicate is not billed: one credit-of-quota per stored event.
    assert _count(app_engine, tenant, "SELECT sum(events) FROM connection_event_usage") == 1


def test_a_fast_correct_answer_is_fluency_not_a_guess(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """A quick right answer is a learner who knows it: no flag, no abstention.

    `rapid_guess` used to fire on latency alone, so a fluent learner — the one a
    school most wants to see — filled the uninformative share and had decisions
    refused for `data_quality`. Correctness is only ever used to exonerate.
    """
    events = [
        event(item_id=f"q-{n}", latency_ms=110, correct=True, response=f"opt-{n}")
        for n in range(10)
    ]
    body = _post(client, connection, events).json()
    assert body["flags"].get("rapid_guess", 0) == 0

    # Nor does a fast answer on an item everyone gets right count against anyone.
    easy = [
        event(item_id=f"e-{n}", latency_ms=100, correct=False, difficulty_band="0.90-0.98")
        for n in range(3)
    ]
    assert _post(client, connection, easy).json()["flags"].get("rapid_guess", 0) == 0

    # And a fast wrong answer on a genuinely hard item still is a guess.
    hard = [event(item_id="h-1", latency_ms=90, correct=False, difficulty_band="0.20-0.40")]
    assert _post(client, connection, hard).json()["flags"]["rapid_guess"] == 1


def test_an_event_already_stored_is_a_duplicate_on_the_next_batch(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    body = event(item_id="i-1", session_id="sess-1", seq=1)
    _post(client, connection, [body])
    second = _post(client, connection, [dict(body)])
    assert second.json()["duplicates"] == 1
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 1


def test_validation_flags_are_recorded(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    events = [
        event(item_id="i-1", latency_ms=120, correct=False, device="phone"),  # rapid_guess
        event(item_id="i-1", correct=False, device="phone"),  # repeated_item
        event(item_id="i-2", device="tablet", assisted=True),  # device_change + assisted
        event(item_id="i-3", device="tablet"),
    ]
    body = _post(client, connection, events).json()
    assert body["flags"]["rapid_guess"] == 1
    assert body["flags"]["repeated_item"] == 1
    assert body["flags"]["device_change"] == 1
    assert body["flags"]["assisted"] == 1

    with tenant_transaction(app_engine, tenant) as conn:
        rows = conn.execute(
            text("SELECT item_id, validity_flags FROM learning_events ORDER BY ts, item_id")
        ).all()
    by_item: dict[str, list[tuple[str, ...]]] = {}
    for row in rows:
        by_item.setdefault(row.item_id, []).append(tuple(row.validity_flags))
    assert any("rapid_guess" in flags for flags in by_item["i-1"])
    assert any("repeated_item" in flags for flags in by_item["i-1"])
    assert any("device_change" in flags for flags in by_item["i-2"])
    assert any("assisted" in flags for flags in by_item["i-2"])


def test_eight_identical_responses_are_flagged_across_batches(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """`straight_line` is 8+ identical *responses*, and it spans batches."""
    first = [event(item_id=f"i-{n}", correct=True, response="b") for n in range(4)]
    assert _post(client, connection, first).json()["flags"].get("straight_line", 0) == 0
    second = [event(item_id=f"j-{n}", correct=True, response="b") for n in range(4)]
    body = _post(client, connection, second).json()
    # The 8th identical response in the run is the 4th of the second batch.
    assert body["flags"]["straight_line"] == 1

    with tenant_transaction(app_engine, tenant) as conn:
        flagged = conn.execute(
            text("SELECT item_id FROM learning_events WHERE 'straight_line' = ANY(validity_flags)")
        ).all()
    assert [row.item_id for row in flagged] == ["j-3"]


def test_a_run_of_correct_answers_is_never_flagged(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """Correctness is not a flag: ten right answers are a learner doing well.

    The rule that produced this test read "8+ identical outcomes", which flagged
    a run of correct answers as inattention and made `decide` abstain on the
    learners progressing fastest (MEASUREMENT §4, METRICS §1.3). Every event here
    carries a *distinct* response, so no run exists.
    """
    answers = [event(item_id=f"k-{n}", correct=True, response=f"option-{n}") for n in range(10)]
    body = _post(client, connection, answers).json()
    assert "straight_line" not in body["flags"]
    with tenant_transaction(app_engine, tenant) as conn:
        flagged = conn.execute(
            text("SELECT count(*) FROM learning_events WHERE 'straight_line' = ANY(validity_flags)")
        ).scalar_one()
    assert flagged == 0


def test_a_missing_response_is_undeterminable_not_inferred_from_correctness(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """No response ⇒ `undeterminable`, on every event, however correct.

    The honest answer to "was this straight-lining?" without a response is "we
    cannot tell" — not a guess derived from the correctness pattern. The flag is
    in the vocabulary so the gap is *visible* and countable rather than silent.
    """
    answers = [event(item_id=f"m-{n}", correct=True) for n in range(10)]
    body = _post(client, connection, answers).json()
    assert body["flags"].get("undeterminable") == 10
    assert "straight_line" not in body["flags"]
    with tenant_transaction(app_engine, tenant) as conn:
        rows = conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE 'undeterminable' = ANY(validity_flags)),"
                "       count(*) FILTER (WHERE 'straight_line' = ANY(validity_flags))"
                "  FROM learning_events"
            )
        ).one()
    assert (rows[0], rows[1]) == (10, 0)


def test_flag_severity_is_published_for_every_flag() -> None:
    """METRICS §1.3: severity weights are published, and only two gate serving."""
    from adapt_engine.observe import flags as flag_rules

    vocabulary = {
        "rapid_guess",
        "tab_hidden",
        "straight_line",
        "device_change",
        "assisted",
        "repeated_item",
        "undeterminable",
    }
    assert set(flag_rules.SEVERITY) == vocabulary, "every flag has a published severity"
    assert set(flag_rules.UNINFORMATIVE_FLAGS) == {
        "rapid_guess",
        "straight_line",
        "tab_hidden",
    }
    assert flag_rules.SEVERITY["undeterminable"] == "context"


def test_a_hostile_idempotency_key_is_not_a_crash(client: TestClient, connection) -> None:
    resp = client.post(
        "/v1/observe",
        json={"events": [event()]},
        headers={
            "Authorization": f"Bearer {connection['key']['full_key']}",
            "Idempotency-Key": "short",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "payload_invalid"


def test_the_error_envelope_is_the_documented_shape(client: TestClient, connection) -> None:
    resp = _post(client, connection, [{"learner_id": "x"}])
    assert resp.status_code == 422
    error = resp.json()["error"]
    # Four documented fields, plus `details` — and only when there is
    # something structured to say (INTEGRATION §6).
    assert set(error) == {"code", "message", "request_id", "retryable", "details"}
    assert error["details"]["field"].startswith("events[0].")
    assert json.dumps(error)  # serialisable, no leaked objects


# --- partition maintenance ------------------------------------------------


def test_partitions_are_provisioned_ahead_and_the_script_is_idempotent(
    owner_engine: Engine,
) -> None:
    """Twelve months ahead is the migration's promise; the script keeps it true."""
    from scripts import ensure_event_partitions as partitions

    assert partitions.months_ahead_available(owner_engine) >= 12
    assert partitions.default_partition_rows(owner_engine) == 0

    # A month the migration could not have known about gets created, once.
    future = datetime.now(UTC).replace(year=datetime.now(UTC).year + 5)
    created = partitions.ensure_partitions(owner_engine, now=future, months=2)
    assert len(created) == 2
    assert partitions.ensure_partitions(owner_engine, now=future, months=2) == []
    assert partitions.months_ahead_available(owner_engine, now=future) == 2


def test_the_check_mode_fails_when_the_net_has_been_used(
    owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The alert is city-level, not silent: a row in the default partition exits 1."""
    from scripts import ensure_event_partitions as partitions

    monkeypatch.setattr(partitions, "default_partition_rows", lambda engine: 1)
    assert (
        partitions.main(
            ["--check", f"--database-url={owner_engine.url.render_as_string(hide_password=False)}"]
        )
        == 1
    )


# --- the fixture, measured ------------------------------------------------


def test_the_session_fixture_measures_decision_id_coverage(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """The 95% line is a contract target; the number is measured on a fixture.

    `tests/fixtures/observe_session.json` is one hand-built session: 12 events
    that precede any decision, then 100 that follow one — of which 4 carry no
    `decision_id`, because the host substituted its own item when the engine
    abstained. So the honest denominators are two:

        post-decision: 96/100 = 96.0%
        whole session: 96/112 = 85.7%

    Both are asserted here, and the fixture is in the repo so the number can be
    recomputed rather than believed.
    """
    document = json.loads((FIXTURES / "observe_session.json").read_text())
    events = document["events"]
    split = int(document["post_decision_from_index"])

    resp = _post(client, connection, events)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["accepted"] == len(events)

    post_decision = events[split:]
    with_id = sum(1 for event in post_decision if "decision_id" in event)
    assert (with_id, len(post_decision)) == (96, 100)
    assert round(with_id / len(post_decision), 3) == 0.960
    assert round(with_id / len(events), 3) == 0.857

    # And the store agrees with the arithmetic.
    stored = _count(
        app_engine, tenant, "SELECT count(*) FROM learning_events WHERE decision_id IS NOT NULL"
    )
    assert stored == with_id
    assert body["decision_attribution"]["with_decision_id"] == with_id


def test_the_fixture_is_a_realistic_session_not_a_bag_of_events(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """Flags fire where a real host would cause them — hint use, two devices."""
    document = json.loads((FIXTURES / "observe_session.json").read_text())
    body = _post(client, connection, document["events"]).json()

    assert body["flags"].get("device_change", 0) >= 1
    assert body["flags"].get("rapid_guess", 0) == 0  # every latency is human-plausible
    assert body["flagged_events"] >= body["flags"].get("device_change", 0)


# --- §6 `details`: structured values, not English in `message` -------------


def test_quota_refusal_carries_details_resets_at(
    client: TestClient, admin_key, app_engine: Engine
) -> None:
    small = _scoped_connection(client, admin_key, quota=1, scopes=["observe"])
    assert _post(client, small, [event(item_id="i-1")]).status_code == 202
    refused = _post(client, small, [event(item_id="i-2")])

    assert refused.status_code == 402
    details = refused.json()["error"]["details"]
    assert set(details) == {"resets_at"}
    assert details["resets_at"].endswith("+00:00")
    # The header and the body agree, and neither is a second source of truth.
    assert refused.headers["X-Adapt-Resets-At"] == details["resets_at"]


def test_a_rejected_field_names_itself_in_details(client: TestClient, connection) -> None:
    resp = _post(
        client, connection, [event(item_id="i-1"), event(item_id="i-2", learner_email="a@b.test")]
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["details"]["field"] == "events[1].keys"
    assert "learner_email" in error["message"]


def test_a_missing_scope_carries_tier_required(client: TestClient, admin_key) -> None:
    """A console key is `not_entitled` with a tier, not a sentence to parse."""
    resp = client.post(
        "/v1/observe",
        json={"events": [event()]},
        headers={
            "Authorization": f"Bearer {admin_key.full_key}",
            "Idempotency-Key": "batch-console-key-0002",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["details"] == {"tier_required": "core"}


def test_a_validation_error_from_the_framework_also_names_the_field(
    client: TestClient, admin_key
) -> None:
    resp = client.post(
        "/v1/admin/connections",
        json={"host_name": "x"},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 422
    assert "field" in resp.json()["error"]["details"]


# --- the rate limit (METERING §6: 50 rps, burst 100) ----------------------

T0 = datetime(2026, 11, 17, 9, 0, 0, tzinfo=UTC)


class _SteppingClock:
    """A clock a test can move, so a refill can be observed without sleeping."""

    def __init__(self, start: datetime) -> None:
        self.value = start

    def now(self) -> datetime:
        return self.value


@pytest.fixture
def tight_app(app_engine: Engine):
    """The same app with a bucket small enough to empty in a test."""
    from adapt_engine.observe.limits import IngestLimiter

    clock = _SteppingClock(T0)
    limiter = IngestLimiter(clock=clock, rate_per_second=1.0, burst=2.0)
    app = create_app(
        make_settings(),
        clock=clock,
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        auth_limiter=FailureLimiter(clock=clock, max_failures=10_000),
        ingest_limiter=limiter,
    )
    client = TestClient(app, raise_server_exceptions=False)
    return client, clock, limiter


def test_a_burst_is_allowed_and_the_next_request_is_429(tight_app, admin_key) -> None:
    client, _clock, _limiter = tight_app
    connection = _scoped_connection(client, admin_key, quota=100, scopes=["observe"])

    for n in range(2):
        accepted = _post(client, connection, [event(item_id=f"i-{n}", ts=T0)])
        assert accepted.status_code == 202, accepted.text

    refused = _post(client, connection, [event(item_id="i-over", ts=T0)])
    assert refused.status_code == 429
    error = refused.json()["error"]
    assert error["code"] == "rate_limited"
    assert error["retryable"] is True
    assert error["details"] == {"retry_after": 1}
    assert refused.headers["Retry-After"] == "1"


def test_a_rate_limited_batch_is_free_and_writes_nothing(
    tight_app, admin_key, app_engine: Engine, tenant: uuid.UUID
) -> None:
    client, _clock, _limiter = tight_app
    connection = _scoped_connection(client, admin_key, quota=100, scopes=["observe"])
    for n in range(2):
        _post(client, connection, [event(item_id=f"i-{n}", ts=T0)])
    before = _count(app_engine, tenant, "SELECT sum(events) FROM connection_event_usage")

    assert _post(client, connection, [event(item_id="i-over", ts=T0)]).status_code == 429
    assert _count(app_engine, tenant, "SELECT sum(events) FROM connection_event_usage") == before
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 2


def test_the_bucket_refills_at_the_documented_rate(tight_app, admin_key) -> None:
    client, clock, _limiter = tight_app
    connection = _scoped_connection(client, admin_key, quota=100, scopes=["observe"])
    for n in range(2):
        _post(client, connection, [event(item_id=f"i-{n}", ts=T0)])
    assert _post(client, connection, [event(item_id="i-3", ts=T0)]).status_code == 429

    clock.value = T0 + timedelta(seconds=2)
    assert _post(client, connection, [event(item_id="i-4", ts=T0)]).status_code == 202


def test_one_connection_cannot_spend_another_connection_s_bucket(tight_app, admin_key) -> None:
    """The property that matters: a runaway client is contained."""
    client, _clock, _limiter = tight_app
    noisy = _scoped_connection(client, admin_key, quota=100, scopes=["observe"])
    quiet = _scoped_connection(client, admin_key, quota=100, scopes=["observe"])

    for n in range(2):
        _post(client, noisy, [event(item_id=f"i-{n}", ts=T0)])
    assert _post(client, noisy, [event(item_id="i-over", ts=T0)]).status_code == 429
    assert _post(client, quiet, [event(item_id="j-1", ts=T0)]).status_code == 202


# --- the dedupe race ------------------------------------------------------


def test_a_concurrent_batch_with_the_same_session_cannot_double_store(
    app_engine: Engine, database, tenant: uuid.UUID, admin_key, connection
) -> None:
    """The advisory lock is what makes the dedupe check a decision.

    Without it both batches read "not stored" and both insert: the same
    observation twice, which downstream is a learner who answered twice. The
    test opens the first batch, marks its rows, and only then runs a second
    batch with the same `(learner, session, seq)` — the second must see the
    first, wait for its lock, and count a duplicate.
    """
    import threading
    from datetime import datetime as _datetime

    from adapt_engine.db.tenant import tenant_transaction
    from adapt_engine.observe import service
    from adapt_engine.observe.schema import parse_event

    connection_id = uuid.UUID(connection["connection"]["id"])
    now = _datetime.now(UTC)
    raw = event(item_id="i-race", session_id="sess-race", seq=1)
    parsed = parse_event(raw, index=0, now=now)

    results: list[object] = []
    started = threading.Event()

    def second_batch() -> None:
        started.set()
        with tenant_transaction(app_engine, tenant) as other:
            results.append(
                service.ingest(
                    other,
                    tenant_id=tenant,
                    connection_id=connection_id,
                    idempotency_key="batch-race-second",
                    body_hash=b"second",
                    events=[parsed],
                    now=now,
                )
            )

    with tenant_transaction(app_engine, tenant) as first:
        stored = service.ingest(
            first,
            tenant_id=tenant,
            connection_id=connection_id,
            idempotency_key="batch-race-first",
            body_hash=b"first",
            events=[parsed],
            now=now,
        )
        assert stored.response["accepted"] == 1

        worker = threading.Thread(target=second_batch)
        worker.start()
        started.wait(timeout=5)
        # The second batch is blocked on the advisory lock, not racing past it.
        worker.join(timeout=0.3)
        assert worker.is_alive(), "the second batch did not wait for the first"

    worker.join(timeout=10)
    assert not worker.is_alive()
    assert len(results) == 1
    second = results[0]
    assert second.response["accepted"] == 0  # type: ignore[attr-defined]
    assert second.response["duplicates"] == 1  # type: ignore[attr-defined]
    assert _count(app_engine, tenant, "SELECT count(*) FROM learning_events") == 1
