"""Increment 6: abstention metrics, and the two numbers that must travel together.

The specification lives in `tests/fixtures/metrics_abstention.json` and in
`METRICS.md` §1. The tests below build that corpus through the real API — real
observations, a real `decide`, a real pause, a real refusal, a real 5xx — then
assert arithmetic:

* the denominator is **20**, hardcoded here rather than computed from the code,
  because a test that derives its expectation from the implementation asserts
  only self-consistency;
* abstention and served rate come from that one denominator, and both are present
  (§1.8 — showing one alone is a dashboard that can be gamed by accident);
* replays, test traffic and paused-connection rows are excluded, and each
  exclusion carries its own count so a reader can add them back;
* refusals and 5xx write no row at all, so they are outside the population rather
  than divided into it;
* `abstention_polling` fires at 5x the normal repeat ratio and stays quiet when
  every connection repeats alike.
"""

from __future__ import annotations

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
from adapt_engine.credits import service as credits
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from adapt_engine.metrics import service as metrics
from tests.conftest import make_settings
from tests.pg import bootstrap_key, provision_tenant

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "metrics_abstention.json").read_text())
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SKILL = "skill.fractions"

#: The fixture's own answer, written as a literal. If the implementation starts
#: counting something else, this number does not move with it.
DENOMINATOR = 20


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"mtr-{uuid.uuid4().hex[:8]}", name="Metrics Test")


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


def _auth(connection, key: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {key or connection['key']['full_key']}"}


def _new_connection(client: TestClient, admin_key, *, scopes: list[str] | None = None):
    resp = client.post(
        "/v1/admin/connections",
        json={
            "host_name": f"host-{uuid.uuid4().hex[:6]}",
            "environment": "test",
            "event_quota_monthly": 250_000,
            "scopes": scopes or ["observe", "decide", "read_model"],
        },
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def connection(client: TestClient, admin_key):
    return _new_connection(client, admin_key)


def _grant(app_engine: Engine, tenant: uuid.UUID, amount: int = 500) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        credits.grant_credits(
            conn,
            tenant_id=tenant,
            kind="purchased",
            amount=amount,
            expires_at=None,
            idempotency_key=f"grant-{uuid.uuid4().hex}",
        )


def _observe(
    client: TestClient, connection, learner: str, answers: list[bool], *, rapid: bool = False
) -> None:
    events = [
        {
            "learner_id": learner,
            "skill": SKILL,
            "item_id": f"item-{index:03d}",
            "correct": correct,
            "response": f"option-{index}",
            "ts": NOW.isoformat(),
            "seq": index,
            # `rapid_guess` needs a fast *wrong* answer on a non-trivial item.
            **({"latency_ms": 120} if rapid and not correct else {}),
        }
        for index, correct in enumerate(answers)
    ]
    resp = client.post(
        "/v1/observe",
        json={"events": events},
        headers={**_auth(connection), "Idempotency-Key": f"batch-{uuid.uuid4().hex}"},
    )
    assert resp.status_code == 202, resp.text


def _decide(client: TestClient, connection, learner: str, *, key: str | None = None, test=False):
    return client.post(
        "/v1/decide",
        json={"learner_id": learner, "skill": SKILL},
        headers={
            **_auth(connection),
            "Idempotency-Key": key or f"decide-{uuid.uuid4().hex}",
            **({"X-Adapt-Test": "1"} if test else {}),
        },
    )


def _window(**overrides) -> dict[str, str]:
    """The window around *now*: decisions are issued by the real clock, not by NOW.

    `NOW` is the fixture's event timeline (evidence may be backdated); a decision's
    `issued_at` is when the server wrote it, so a window built from `NOW` would
    silently contain nothing.
    """
    now = datetime.now(UTC)
    window = {
        "from": (now - timedelta(days=1)).isoformat(),
        "to": (now + timedelta(days=1)).isoformat(),
    }
    window.update({key: value for key, value in overrides.items() if value is not None})
    return window


def _metrics(client: TestClient, admin_key, **params):
    """The route is `/v1/admin/*`: it needs the admin scope, like every admin read."""
    resp = client.get(
        "/v1/admin/metrics/abstention",
        params=_window(**params),
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rows(app_engine: Engine, tenant: uuid.UUID) -> int:
    with tenant_transaction(app_engine, tenant) as conn:
        return int(conn.execute(text("SELECT count(*) FROM decisions")).scalar_one())


# The shapes that reach each abstention reason, kept as data so the fixture's
# "3 per reason" can be built literally rather than searched for.
ABSTENTION_SHAPES: dict[str, list[bool]] = {
    "insufficient_evidence": [True, False, True],
    "low_confidence": [True, True, False, True, True, False, False, True],
    "conflicting_signals": [True] * 11 + [False] * 3 + [True] * 3 + [False] * 3,
    "data_quality": [True, True, True, False, True, False, True, False],
}
#: Served, with the last three answers right: a confident estimate plus a run of
#: *wrong* answers at the end would be `conflicting_signals` by design (§21.2).
SERVED = [True] * 20 + [False] + [True] * 3


def _build_corpus(
    client: TestClient, admin_key, connection, app_engine: Engine, tenant: uuid.UUID, monkeypatch
) -> dict[str, list[str]]:
    """The 30 calls in the fixture, plus the refusal and the 5xx outside it."""
    _grant(app_engine, tenant)
    served_ids: list[str] = []

    for reason, answers in ABSTENTION_SHAPES.items():
        for index in range(3):
            learner = f"learn-abst-{reason[:6]}-{index}"
            _observe(client, connection, learner, answers, rapid=(reason == "data_quality"))
            body = _decide(client, connection, learner).json()
            assert body["abstain"] is True, (reason, body)
            assert body["reason"] == reason, (reason, body)

    for index in range(8):
        learner = f"learn-served-{index:03d}"
        _observe(client, connection, learner, SERVED)
        resp = _decide(client, connection, learner)
        assert resp.status_code == 200, resp.text
        assert resp.json()["abstain"] is False
        served_ids.append(resp.json()["decision_id"])

    # 3 test calls, each on its own learner so they cannot distort the repeat ratio.
    for index in range(3):
        learner = f"learn-test-{index:03d}"
        _observe(client, connection, learner, SERVED)
        resp = _decide(client, connection, learner, test=True)
        assert resp.status_code == 200 and resp.headers["X-Adapt-Billed"] == "0"

    # 5 replays of decisions already served: counted, never denominated.
    for decision_id in served_ids[:5]:
        replay = client.post(f"/v1/decisions/{decision_id}/replay", headers=_auth(connection))
        assert replay.status_code == 200, replay.text

    # 2 calls on a second connection, paused afterwards: the rows exist, so the
    # exclusion is by status and not by the rows never having been written.
    doomed = _new_connection(client, admin_key)
    for index in range(2):
        learner = f"learn-paused-{index:03d}"
        _observe(client, doomed, learner, ABSTENTION_SHAPES["insufficient_evidence"])
        assert _decide(client, doomed, learner).json()["abstain"] is True
    paused = client.patch(
        f"/v1/admin/connections/{doomed['connection']['id']}",
        json={"status": "paused"},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert paused.status_code == 200, paused.text

    before = _rows(app_engine, tenant)

    # 1 refusal: a key that may observe but not decide. A 4xx is not an abstention.
    observer = _new_connection(client, admin_key, scopes=["observe"])
    refused = _decide(client, observer, "learn-refused-01")
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "not_entitled"

    # 1 5xx: the ledger write fails after the decision was computed.
    _observe(client, connection, "learn-broken-01", SERVED)

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic internal failure")

    monkeypatch.setattr(credits, "settle", explode)
    broken = _decide(client, connection, "learn-broken-01")
    assert broken.status_code == 500
    monkeypatch.undo()

    assert _rows(app_engine, tenant) == before, "a refusal and a 5xx write no decision"
    return {"served": served_ids}


# --- the fixture ---------------------------------------------------------


def test_the_thirty_decision_fixture_reproduces_the_rate(
    client: TestClient, admin_key, connection, app_engine: Engine, tenant: uuid.UUID, monkeypatch
) -> None:
    """AGENT_M1 §6: 30 calls, denominator 20, rate exactly 0.6 — both grains."""
    _build_corpus(client, admin_key, connection, app_engine, tenant, monkeypatch)
    body = _metrics(client, admin_key)

    # The file and the literal agree, and neither is derived from the response.
    assert FIXTURE["expected"]["denominator"] == DENOMINATOR
    assert FIXTURE["stored_rows"] == 25
    assert body["denominator"] == DENOMINATOR, "the fixture's own arithmetic, not the code's"
    assert body["numerator"] == 12
    assert body["rate"] == 0.6
    assert body["reasons"] == {
        "insufficient_evidence": 3,
        "low_confidence": 3,
        "conflicting_signals": 3,
        "data_quality": 3,
    }

    # Replays, test traffic and paused rows are excluded, each with its own count.
    assert body["exclusions"] == {"test_traffic": 3, "paused_or_revoked": 2, "replays": 5}

    # The identity a reader can check: stored rows = denominator + exclusions.
    stored = (
        body["denominator"]
        + body["exclusions"]["test_traffic"]
        + body["exclusions"]["paused_or_revoked"]
    )
    assert stored == 25
    assert _rows(app_engine, tenant) == stored
    assert stored + body["exclusions"]["replays"] == 30, "the fixture's 30 decide calls"

    assert body["metric_version"] == "abstention@1"
    assert body["grain"] == "request"
    # Wilson 95 % for 12/20, computed outside this module.
    assert body["interval"] == {"low": 0.3866, "high": 0.7812, "confidence": 0.95}

    # The construct grain agrees at M1: one request evaluates one construct.
    assert sum(row["denominator"] for row in body["by_construct"]) == DENOMINATOR
    assert sum(row["numerator"] for row in body["by_construct"]) == 12
    assert all(
        row["served"]["numerator"] + row["numerator"] == row["denominator"]
        for row in body["by_construct"]
    )
    assert body["abstention_polling"]["alerting"] == [], "a well-behaved corpus does not poll"


def test_served_rate_is_reported_from_the_same_denominator(
    client: TestClient, admin_key, connection, app_engine: Engine, tenant: uuid.UUID, monkeypatch
) -> None:
    """METRICS §1.8: the pair, or the abstention number is a lie of omission."""
    _build_corpus(client, admin_key, connection, app_engine, tenant, monkeypatch)
    body = _metrics(client, admin_key)

    served = body["served"]
    assert served["denominator"] == body["denominator"] == DENOMINATOR
    assert served["numerator"] == DENOMINATOR - body["numerator"] == 8
    assert served["rate"] == 0.4
    assert served["interval"] == {"low": 0.2188, "high": 0.6134, "confidence": 0.95}
    assert body["rate"] is not None and served["rate"] is not None


def test_the_construct_grain_is_its_own_report(
    client: TestClient, admin_key, connection, app_engine: Engine, tenant: uuid.UUID, monkeypatch
) -> None:
    """`grain=construct` echoes the grain and reports construct-level denominators."""
    _build_corpus(client, admin_key, connection, app_engine, tenant, monkeypatch)
    body = _metrics(client, admin_key, grain="construct")

    assert body["grain"] == "construct"
    assert body["by_construct"] == sorted(body["by_construct"], key=lambda row: row["construct"])
    construct = body["by_construct"][0]
    assert set(construct) == {
        "construct",
        "rate",
        "numerator",
        "denominator",
        "interval",
        "served",
        "reasons",
    }
    assert sum(construct["reasons"].values()) == construct["numerator"]


def test_the_unmeasurable_series_are_null_not_zero(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """`refusals: 0` would be a claim; nothing records refusals yet."""
    body = _metrics(client, admin_key)
    assert body["refusals"] is None and body["errors"] is None
    assert body["entitlement_limits"] is None
    assert body["not_validated"] is None and body["not_capable"] is None
    assert set(body["unmeasured"]) == {
        "entitlement_limits",
        "not_validated",
        "not_capable",
        "refusals",
        "errors",
    }


def test_an_empty_window_is_not_a_zero_percent_abstention(client: TestClient, admin_key) -> None:
    """No rows means no rate. A 0.0 here would be the trap §1.5 names."""
    past = datetime.now(UTC) - timedelta(days=40)
    body = _metrics(
        client,
        admin_key,
        **{
            "from": past.isoformat(),
            "to": (past + timedelta(days=1)).isoformat(),
        },
    )
    assert body["denominator"] == 0
    assert body["rate"] is None
    assert body["interval"] == {"low": None, "high": None, "confidence": 0.95}
    assert body["served"]["rate"] is None


def test_filters_narrow_the_series_without_changing_its_definition(
    client: TestClient, admin_key, connection, app_engine: Engine, tenant: uuid.UUID, monkeypatch
) -> None:
    _build_corpus(client, admin_key, connection, app_engine, tenant, monkeypatch)

    mine = _metrics(client, admin_key, connection=connection["connection"]["id"])
    assert mine["denominator"] == DENOMINATOR

    other = _metrics(client, admin_key, construct="skill.decimals")
    assert other["denominator"] == 0 and other["by_construct"] == []

    core = _metrics(client, admin_key, tier="core")
    assert core["denominator"] == DENOMINATOR
    deep = _metrics(client, admin_key, tier="deep")
    assert deep["denominator"] == 0


def test_a_window_that_runs_backwards_is_refused(client: TestClient, admin_key) -> None:
    now = datetime.now(UTC)
    resp = client.get(
        "/v1/admin/metrics/abstention",
        params={"from": now.isoformat(), "to": (now - timedelta(hours=1)).isoformat()},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == {"field": "to"}

    naive = client.get(
        "/v1/admin/metrics/abstention",
        params={"from": "2026-11-18T00:00:00", "to": now.isoformat()},
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert naive.status_code == 422
    assert naive.json()["error"]["details"] == {"field": "from"}


def test_another_tenant_cannot_see_the_series(
    client: TestClient,
    admin_key,
    connection,
    app_engine: Engine,
    tenant: uuid.UUID,
    database,
    monkeypatch,
) -> None:
    _build_corpus(client, admin_key, connection, app_engine, tenant, monkeypatch)
    other = provision_tenant(database, slug=f"mtr-{uuid.uuid4().hex[:8]}", name="Other tenant")
    other_key = bootstrap_key(database, other, scopes=("admin", "read_model"))
    mine = _metrics(client, admin_key)
    theirs = client.get(
        "/v1/admin/metrics/abstention",
        params=_window(),
        headers={"Authorization": f"Bearer {other_key.full_key}"},
    )
    assert theirs.status_code == 200
    assert theirs.json()["denominator"] == 0
    assert mine["denominator"] == DENOMINATOR


# --- abstention_polling --------------------------------------------------


def _pairs(client: TestClient, connection, *, prefix: str, count: int, repeats: int) -> None:
    """`count` distinct (learner, construct) pairs, `repeats` extra calls on them."""
    for index in range(count):
        learner = f"learn-{prefix}-{index:03d}"
        _observe(client, connection, learner, ABSTENTION_SHAPES["insufficient_evidence"])
        assert _decide(client, connection, learner).json()["abstain"] is True
    for extra in range(repeats):
        learner = f"learn-{prefix}-{extra % count:03d}"
        assert _decide(client, connection, learner).json()["abstain"] is True


def test_abstention_polling_fires_at_five_times_the_repeat_ratio(
    client: TestClient, admin_key
) -> None:
    """METRICS §1.5: a host retrying `decide` hoping for a non-abstention."""
    quiet = [_new_connection(client, admin_key) for _ in range(3)]
    for index, conn in enumerate(quiet):
        _pairs(client, conn, prefix=f"q{index}", count=10, repeats=2)  # ratio 0.2
    talkative = _new_connection(client, admin_key)
    _pairs(client, talkative, prefix="loud", count=1, repeats=12)  # ratio 12.0
    mild = _new_connection(client, admin_key)
    _pairs(client, mild, prefix="mild", count=10, repeats=8)  # ratio 0.8 = 4x

    polling = _metrics(client, admin_key)["abstention_polling"]
    assert polling["multiple"] == 5.0
    assert polling["baseline_repeat_ratio"] == 0.2
    assert polling["min_repeats"] == 10
    alerting = {row["connection_id"] for row in polling["alerting"]}
    assert alerting == {talkative["connection"]["id"]}
    loud = next(
        row
        for row in polling["by_connection"]
        if row["connection_id"] == str(talkative["connection"]["id"])
    )
    assert loud["repeat_ratio"] == 12.0
    assert loud["multiple_of_baseline"] == 60.0
    assert loud["repeats"] == 12
    mild_row = next(
        row
        for row in polling["by_connection"]
        if row["connection_id"] == str(mild["connection"]["id"])
    )
    assert mild_row["multiple_of_baseline"] == 4.0
    assert mild_row["alerting"] is False


def test_abstention_polling_stays_quiet_when_every_connection_repeats_alike(
    client: TestClient, admin_key
) -> None:
    conns = [_new_connection(client, admin_key) for _ in range(3)]
    for index, conn in enumerate(conns):
        _pairs(client, conn, prefix=f"same{index}", count=10, repeats=2)

    polling = _metrics(client, admin_key)["abstention_polling"]
    assert polling["alerting"] == []
    assert {row["repeat_ratio"] for row in polling["by_connection"]} == {0.2}
    assert polling["baseline_repeat_ratio"] == 0.2


def test_polling_needs_volume_before_it_fires(client: TestClient, admin_key) -> None:
    """One retry is a retry: below the repeat floor nothing is alerting."""
    base = [_new_connection(client, admin_key) for _ in range(2)]
    for index, conn in enumerate(base):
        _pairs(client, conn, prefix=f"vol{index}", count=10, repeats=0)
    retrier = _new_connection(client, admin_key)
    _pairs(client, retrier, prefix="retry", count=1, repeats=3)

    polling = _metrics(client, admin_key)["abstention_polling"]
    assert polling["alerting"] == []
    row = next(
        r
        for r in polling["by_connection"]
        if r["connection_id"] == str(retrier["connection"]["id"])
    )
    assert row["repeats"] == 3 and row["alerting"] is False


# --- Wilson --------------------------------------------------------------


@pytest.mark.parametrize(
    ("successes", "total", "low", "high"),
    [
        (12, 20, 0.3866, 0.7812),
        (8, 20, 0.2188, 0.6134),
        (1, 10, 0.0179, 0.4042),
        (0, 10, 0.0, 0.2775),
        (10, 10, 0.7225, 1.0),
    ],
)
def test_the_interval_is_a_wilson_score_band(
    successes: int, total: int, low: float, high: float
) -> None:
    """Values computed outside this module from the closed form."""
    band = metrics.wilson(successes, total)
    assert band["low"] == pytest.approx(low, abs=5e-5)
    assert band["high"] == pytest.approx(high, abs=5e-5)
    assert band["confidence"] == 0.95
    assert band["low"] <= successes / total <= band["high"]


def test_an_empty_sample_has_no_interval() -> None:
    assert metrics.wilson(0, 0) == {"low": None, "high": None, "confidence": 0.95}
