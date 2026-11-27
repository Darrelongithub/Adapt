"""Increment 5: metering — the billing story as arithmetic a reader can check.

METERING §3 is a set of promises about money, and every one of them is proved
here by counting rows rather than by asserting a flag:

* an abstention writes **no ledger rows**;
* a 4xx writes none;
* a 5xx after the reservation writes none (the transaction rolls back, including
  the hold);
* test traffic (`X-Adapt-Test: 1`, or a staging key) writes none and is stored as
  test traffic so metrics can exclude it;
* reads and replays write none;
* a served decision writes exactly two ledger rows — a `reserve` that binds the
  key and a `settle` that is the only kind billing reads — and exactly **one**
  row in `credit_consumption`, which joins back to the request through the
  reservation's `operation_ref`.

The last test does the whole 200-decision corpus, so the report can quote a
ledger count next to a served/abstained split and a reader can do the sum.
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
from adapt_engine.credits import service as credits
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from tests.conftest import make_settings
from tests.pg import bootstrap_key, provision_tenant

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"met-{uuid.uuid4().hex[:8]}", name="Metering Test")


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe", "decide", "read_model", "admin"))


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
    resp = client.post(
        "/v1/admin/connections",
        json={
            "host_name": f"host-{uuid.uuid4().hex[:6]}",
            "environment": "test",
            "event_quota_monthly": 250_000,
            "scopes": ["observe", "decide", "read_model"],
        },
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth(connection, key: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {key or connection['key']['full_key']}"}


def _grant(app_engine: Engine, tenant: uuid.UUID, amount: int = 1000) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        credits.grant_credits(
            conn,
            tenant_id=tenant,
            kind="purchased",
            amount=amount,
            expires_at=None,
            idempotency_key=f"grant-{uuid.uuid4().hex}",
        )


def _answers(total: int, correct: int, *, tail: tuple[bool, ...] = ()) -> list[bool]:
    """A rate, spread so no run of identical *responses* is created."""
    body_total = total - len(tail)
    body_correct = correct - sum(1 for value in tail if value)
    out: list[bool] = []
    seen = 0
    for index in range(body_total):
        target = round((index + 1) * body_correct / body_total) if body_total else 0
        out.append(target > seen)
        seen = max(seen, target)
    return out + list(tail)


def _observe(client: TestClient, connection, learner: str, answers: list[bool]) -> None:
    events = [
        {
            "learner_id": learner,
            "skill": "skill.fractions",
            "item_id": f"item-{index:03d}",
            "correct": correct,
            # A distinct response per trial: straight-lining is a property of the
            # response, and these fixtures are not straight-liners.
            "response": f"option-{index}",
            "ts": NOW.isoformat(),
            "seq": index,
            "session_id": f"s-{learner[-4:]}",
        }
        for index, correct in enumerate(answers)
    ]
    resp = client.post(
        "/v1/observe",
        json={"events": events},
        headers={**_auth(connection), "Idempotency-Key": f"batch-{uuid.uuid4().hex}"},
    )
    assert resp.status_code == 202, resp.text


def _decide(
    client: TestClient,
    connection,
    learner: str,
    *,
    key: str | None = None,
    headers: dict[str, str] | None = None,
):
    return client.post(
        "/v1/decide",
        json={"learner_id": learner, "skill": "skill.fractions"},
        headers={
            **_auth(connection),
            "Idempotency-Key": key or f"decide-{uuid.uuid4().hex}",
            **(headers or {}),
        },
    )


def _counts(app_engine: Engine, tenant: uuid.UUID) -> dict[str, int]:
    with tenant_transaction(app_engine, tenant) as conn:
        row = conn.execute(
            text(
                """
                SELECT (SELECT count(*) FROM credit_ledger
                         WHERE entry_type IN ('reserve', 'release')
                            OR entry_type = 'settle') AS ledger,
                       (SELECT count(*) FROM credit_ledger WHERE entry_type = 'settle') AS settles,
                       (SELECT coalesce(sum(consumed), 0) FROM credit_consumption) AS consumed,
                       (SELECT count(*) FROM credit_reservations) AS reservations,
                       (SELECT count(*) FROM decisions) AS decisions,
                       (SELECT count(*) FROM decisions WHERE test_traffic) AS test_decisions
                """
            )
        ).one()
    return {
        "ledger": int(row.ledger),
        "settles": int(row.settles),
        "consumed": int(row.consumed),
        "reservations": int(row.reservations),
        "decisions": int(row.decisions),
        "test_decisions": int(row.test_decisions),
    }


# --- the four zero-billing cases -----------------------------------------


def test_an_abstention_writes_no_ledger_rows(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """METERING §3 rule 1. Not a flag, not a zero-amount row: nothing."""
    _grant(app_engine, tenant)
    learner = "learn-meter-0001"
    _observe(client, connection, learner, [True, False, True])

    resp = _decide(client, connection, learner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["abstain"] is True
    assert resp.headers["X-Adapt-Billed"] == "0"
    assert _counts(app_engine, tenant) == {
        "ledger": 0,
        "settles": 0,
        "consumed": 0,
        "reservations": 0,
        "decisions": 1,
        "test_decisions": 0,
    }


def test_a_4xx_writes_no_ledger_rows(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """METERING §3 rule 2: a 422 is a customer learning the API, not a service."""
    _grant(app_engine, tenant)
    bad = client.post(
        "/v1/decide",
        json={"learner_id": "short", "skill": "skill.fractions"},
        headers={**_auth(connection), "Idempotency-Key": f"decide-{uuid.uuid4().hex}"},
    )
    assert bad.status_code == 422
    missing = client.post(
        "/v1/decide",
        json={"learner_id": "learn-never-seen", "skill": "skill.fractions"},
        headers={**_auth(connection), "Idempotency-Key": f"decide-{uuid.uuid4().hex}"},
    )
    assert missing.status_code == 404
    assert _counts(app_engine, tenant)["ledger"] == 0


def test_a_5xx_after_the_reservation_writes_no_ledger_rows(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID, monkeypatch
) -> None:
    """The hold is not durable until the whole transaction commits.

    `settle` raising after `reserve` succeeded is the honest simulation of a
    failure *after* the expensive part: the request is a 500, and the ledger must
    look as if it never happened — otherwise a crash would bill a customer for an
    answer they never received.
    """
    _grant(app_engine, tenant)
    learner = "learn-meter-0003"
    _observe(client, connection, learner, _answers(24, 18))

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic internal failure after the reservation")

    monkeypatch.setattr(credits, "settle", explode)
    resp = _decide(client, connection, learner)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal"
    counts = _counts(app_engine, tenant)
    assert counts["ledger"] == 0
    assert counts["reservations"] == 0
    assert counts["decisions"] == 0


def test_test_traffic_writes_no_ledger_rows_and_is_excluded_from_metrics(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """METERING §3 rule 3: `X-Adapt-Test: 1` is unbilled *and* excluded."""
    _grant(app_engine, tenant)
    learner = "learn-meter-0004"
    _observe(client, connection, learner, _answers(24, 18))

    resp = _decide(client, connection, learner, headers={"X-Adapt-Test": "1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["abstain"] is False, "the call is served; only the billing is different"
    assert resp.headers["X-Adapt-Billed"] == "0"
    assert resp.headers["X-Adapt-Test-Traffic"] == "true"

    counts = _counts(app_engine, tenant)
    assert counts["ledger"] == 0
    assert counts["reservations"] == 0
    assert counts["decisions"] == 1
    assert counts["test_decisions"] == 1, "excluded from metrics means stored as test traffic"


def test_a_staging_deployment_sets_test_traffic_without_the_header(
    client: TestClient,
    connection,
    app_engine: Engine,
    tenant: uuid.UUID,
    admin_key,
) -> None:
    """A staging deployment exists to be exercised; it must not burn a budget.

    The automatic flag belongs to the *deployment*, not to the key: keys carry
    only live/test, and a test key still has to bill somewhere or the billing
    path could never run in CI.
    """
    staging_app = create_app(
        make_settings(env="staging"),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=10_000),
    )
    staging = TestClient(staging_app, raise_server_exceptions=False)

    _grant(app_engine, tenant)
    learner = "learn-meter-0005"
    _observe(client, connection, learner, _answers(24, 18))

    resp = staging.post(
        "/v1/decide",
        json={"learner_id": learner, "skill": "skill.fractions"},
        headers={
            **_auth(connection),
            "Idempotency-Key": f"decide-{uuid.uuid4().hex}",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["abstain"] is False
    assert resp.headers["X-Adapt-Test-Traffic"] == "true"
    assert resp.headers["X-Adapt-Billed"] == "0"
    assert _counts(app_engine, tenant)["ledger"] == 0


def test_reads_and_replays_write_no_ledger_rows(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """METERING §2: `observe`, reads and replay are weight 0.

    A replay writes exactly one row — the append-only `decision_replays` audit
    row — and no money and no decision. That is the increment-5 claim, literally.
    """
    _grant(app_engine, tenant)
    learner = "learn-meter-0006"
    _observe(client, connection, learner, _answers(24, 18))
    served = _decide(client, connection, learner).json()

    before = _counts(app_engine, tenant)
    for path in (
        "/v1/mastery/skill.fractions?learner_id=" + learner,
        "/v1/mastery?learner_id=" + learner,
        "/v1/credits/balance",
    ):
        assert client.get(path, headers=_auth(connection)).status_code == 200
    replay = client.post(f"/v1/decisions/{served['decision_id']}/replay", headers=_auth(connection))
    assert replay.status_code == 200
    assert replay.headers["X-Adapt-Billed"] == "0"
    assert _counts(app_engine, tenant) == before, "reads and replays are weight 0"
    with tenant_transaction(app_engine, tenant) as conn:
        logged = conn.execute(text("SELECT count(*) FROM decision_replays")).scalar_one()
    assert logged == 1, "one audit row per replay, and the decision row is untouched"


def test_an_invalid_test_header_is_refused_rather_than_guessed(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    _grant(app_engine, tenant)
    learner = "learn-meter-0007"
    _observe(client, connection, learner, _answers(24, 18))
    resp = _decide(client, connection, learner, headers={"X-Adapt-Test": "yes"})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == {"field": "X-Adapt-Test"}
    assert _counts(app_engine, tenant)["ledger"] == 0


# --- the served path, whose arithmetic the report quotes ------------------


def test_a_served_decision_reserves_and_settles_exactly_once(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """Two ledger rows (reserve binds the key, settle moves the credit), one
    consumption row, and the join back to the decision through `operation_ref`."""
    _grant(app_engine, tenant)
    learner = "learn-meter-0010"
    _observe(client, connection, learner, _answers(24, 18))
    resp = _decide(client, connection, learner)
    assert resp.status_code == 200, resp.text
    decision_id = resp.json()["decision_id"]
    assert resp.headers["X-Adapt-Billed"] == "1"

    counts = _counts(app_engine, tenant)
    assert counts["ledger"] == 2
    assert counts["settles"] == 1
    assert counts["consumed"] == 1

    with tenant_transaction(app_engine, tenant) as conn:
        # The decision id reaches the ledger through the reservation's
        # `operation_ref`, and the settle row points back at the reservation.
        reservation = conn.execute(
            text("SELECT id, state FROM credit_reservations WHERE operation_ref = :ref"),
            {"ref": f"decision:{decision_id}"},
        ).one()
        assert reservation.state == "settled"
        moves = conn.execute(
            text(
                "SELECT entry_type, delta FROM credit_ledger  WHERE ref = :ref ORDER BY entry_type"
            ),
            {"ref": str(reservation.id)},
        ).all()
        month = conn.execute(
            text("SELECT sum(consumed) FROM credit_consumption  WHERE tenant_id = :tenant"),
            {"tenant": tenant},
        ).scalar_one()
    assert [(row.entry_type, row.delta) for row in moves] == [
        ("reserve", 0),
        ("settle", -1),
    ]
    assert month == 1


def test_a_retry_writes_no_additional_ledger_rows(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    _grant(app_engine, tenant)
    learner = "learn-meter-0011"
    _observe(client, connection, learner, _answers(24, 18))
    key = f"decide-{uuid.uuid4().hex}"
    first = _decide(client, connection, learner, key=key)
    second = _decide(client, connection, learner, key=key)
    assert first.content == second.content
    assert first.headers["X-Adapt-Billed"] == "1"
    assert second.headers["X-Adapt-Replayed"] == "true"
    assert second.headers["X-Adapt-Billed"] == "0", "a replay re-serves; it does not bill"
    counts = _counts(app_engine, tenant)
    assert (counts["ledger"], counts["settles"], counts["consumed"]) == (2, 1, 1)


def test_the_consumption_view_ignores_reserve_and_release_rows(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """`sum(delta)` is not consumption; the view is the sanctioned figure."""
    _grant(app_engine, tenant)
    learner = "learn-meter-0012"
    _observe(client, connection, learner, _answers(24, 18))
    assert _decide(client, connection, learner).status_code == 200

    with tenant_transaction(app_engine, tenant) as conn:
        # A hold that is released: two more ledger rows, still no consumption.
        held = credits.reserve(
            conn,
            tenant_id=tenant,
            amount=3,
            operation_ref="synthetic-hold",
            idempotency_key=f"synthetic-{uuid.uuid4().hex}",
        )
        credits.release(
            conn,
            tenant_id=tenant,
            reservation_id=held.id,
            idempotency_key=f"synthetic-release-{uuid.uuid4().hex}",
        )
        ledger_rows = conn.execute(text("SELECT count(*) FROM credit_ledger")).scalar_one()
        consumption = conn.execute(
            text("SELECT coalesce(sum(consumed), 0) FROM credit_consumption")
        ).scalar_one()
        settle_rows = conn.execute(
            text("SELECT count(*) FROM credit_ledger WHERE entry_type = 'settle'")
        ).scalar_one()
    assert ledger_rows == 5, "the grant, reserve+settle, and reserve+release"
    assert settle_rows == 1
    assert consumption == 1, "only settles count, at 1 credit"


def test_consumption_is_invisible_across_tenants(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID, database
) -> None:
    """The view is `security_invoker`; a view that bypassed RLS would leak."""
    _grant(app_engine, tenant)
    learner = "learn-meter-0013"
    _observe(client, connection, learner, _answers(24, 18))
    assert _decide(client, connection, learner).status_code == 200

    other = provision_tenant(database, slug=f"met-{uuid.uuid4().hex[:8]}", name="Other")
    with tenant_transaction(app_engine, other) as conn:
        visible = conn.execute(
            text("SELECT coalesce(sum(consumed), 0) FROM credit_consumption")
        ).scalar_one()
    assert visible == 0


def test_the_200_decision_corpus_bills_exactly_the_served_half(
    client: TestClient, connection, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """The arithmetic the report quotes: 200 decisions, ledger = 2 x served.

    Seeded so the split is reproducible, and asserted so that "abstentions are
    free" is a row count rather than an intention.
    """
    import random

    rng = random.Random(20261118)
    _grant(app_engine, tenant, amount=500)
    learners = [f"learn-corpus-{index:03d}" for index in range(200)]
    for learner in learners:
        mastery = rng.uniform(0.1, 0.9)
        trials = rng.randint(4, 40)
        _observe(client, connection, learner, [rng.random() < mastery for _ in range(trials)])

    served = 0
    abstained = 0
    reasons: dict[str, int] = {}
    for learner in learners:
        resp = _decide(client, connection, learner)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["abstain"]:
            abstained += 1
            reasons[body["reason"]] = reasons.get(body["reason"], 0) + 1
        else:
            served += 1

    counts = _counts(app_engine, tenant)
    summary = {
        "decisions": served + abstained,
        "served": served,
        "abstained": abstained,
        "abstained_by_reason": dict(sorted(reasons.items())),
        "ledger_rows": counts["ledger"],
        "settle_rows": counts["settles"],
        "consumed": counts["consumed"],
    }
    print("\nMETERING CORPUS " + json.dumps(summary, sort_keys=True))

    # Pinned: with a total ordering in `_recent_answers` this split is
    # reproducible. Before that fix the same 200 learners abstained for
    # `conflicting_signals` 0 to 3 times depending on random-uuid tie-breaks.
    assert served == 176, summary
    assert reasons == {"conflicting_signals": 1, "insufficient_evidence": 23}
    assert served + abstained == 200
    assert counts["ledger"] == 2 * served, "one reserve and one settle per served decision"
    assert counts["settles"] == served
    assert counts["consumed"] == served
    assert counts["reservations"] == served
    assert counts["decisions"] == 200
