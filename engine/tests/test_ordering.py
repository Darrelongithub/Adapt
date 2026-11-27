"""Increment 10: a state change is committed before it is reported.

Every route here answers with a *fact about a write* — "this key is revoked", "this
connection is paused", "this learner exists". If the response can reach the client
before the commit does, the client can act on a fact that is not true yet: the very
next request with that key still authenticates, still ingests, still 404s.

That is not a hypothetical. `POST /v1/admin/connections` handed out keys that did
not work yet, and it was invisible to 768 tests because every one of them used
`TestClient`, which collects the response body only after the app callable returns
(DECISIONS §24.5). So these tests run a **real uvicorn server** and, where the race
would otherwise be won by the commit, they hold the window open with
`widen_the_window`.

The first test is the smoke test that should have existed since increment 1: mint a
key and use it on the very next connection. It needs no widened window and no
fixture beyond a tenant — which is exactly why it would have caught the original
defect on the day it landed.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.engine import Engine

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.main import create_app
from tests import pg
from tests.conftest import make_settings
from tests.live import call, live_server, widen_the_window
from tests.pg import bootstrap_key, provision_tenant

DELAY = 0.30
SKILL = "skill.fractions"


@pytest.fixture(scope="module")
def ordering_database() -> Iterator[pg.Database]:
    """A database of this file's own.

    Not merely tidiness: a real server runs the app's **lifespan**, and the lifespan
    refuses to start when the database holds data keys wrapped under a master key
    this process does not have — which is exactly what the rotation tests leave
    behind on the shared session database. `TestClient` never noticed because it only
    runs lifespan inside `with TestClient(app)`. A file that starts real servers gets
    its own database rather than a keyring wide enough to hide the problem.
    """
    database = pg.provision(f"ordering_{uuid.uuid4().hex[:10]}")
    try:
        yield database
    finally:
        pg.drop_database(database)


@pytest.fixture(scope="module")
def ordering_engine(ordering_database: pg.Database) -> Iterator[Engine]:
    engine = ordering_database.engine(ordering_database.app_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def tenant(ordering_database: pg.Database) -> uuid.UUID:
    return provision_tenant(
        ordering_database, slug=f"order-{uuid.uuid4().hex[:8]}", name="Ordering Test"
    )


@pytest.fixture
def admin_key(ordering_database: pg.Database, tenant: uuid.UUID):
    return bootstrap_key(
        ordering_database, tenant, scopes=("observe", "decide", "admin", "read_model")
    )


@pytest.fixture
def base(ordering_engine: Engine) -> Iterator[str]:
    """A live server for this test, on this file's own database."""
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=ordering_engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=10_000),
    )
    with live_server(app) as url:
        yield url


def _mint(base: str, admin_key, *, scopes: tuple[str, ...] = ("observe", "decide")) -> dict:
    status, created = call(
        base,
        "/v1/admin/connections",
        key=admin_key.full_key,
        body={
            "host_name": f"ordering-{uuid.uuid4().hex[:6]}",
            "environment": "test",
            "event_quota_monthly": 10_000,
            "scopes": list(scopes),
        },
    )
    assert status == 201, created
    return created


def _grant(base: str, admin_key, amount: int = 100) -> None:
    """Credits, so a decision can be served. A grant is its own transaction and
    commits before it answers (see the sweep in DECISIONS §24.6)."""
    status, granted = call(
        base,
        "/v1/admin/credits/grants",
        key=admin_key.full_key,
        body={
            "kind": "included",
            "amount": amount,
            "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        },
        headers={"Idempotency-Key": f"grant-{uuid.uuid4().hex}"},
    )
    assert status == 201, granted


def _settle(delay: float = DELAY) -> None:
    """Wait for a window we are not testing to close.

    Set-up requests go through the same late teardown as the ones under test; when
    the set-up's own ordering is not the subject, waiting is honest and keeps the
    test about one thing.
    """
    time.sleep(delay + 0.1)


def test_a_key_works_on_the_very_next_connection(base: str, admin_key) -> None:
    """The smoke test: mint a key, use it immediately, expect it to be a key.

    No widened window, no patched teardown — this is the shape that failed in CI
    (`adapt sandbox seed` mints a connection key and observes with it on the next
    line) and it costs one server and two requests.
    """
    created = _mint(base, admin_key)
    status, who = call(base, "/v1/whoami", key=created["key"]["full_key"])
    assert status == 200, who
    assert who["key_id"] == created["key"]["key_id"]


def test_a_paused_connection_does_not_ingest_the_very_next_request(
    base: str, admin_key, monkeypatch
) -> None:
    """Paused means paused: the next batch with that key is refused, not delayed.

    The sentence in `api/connections.py` is "a connection reported paused must not
    ingest" — this asserts the *very next* request, through a real socket, with the
    commit still in its teardown.
    """
    created = _mint(base, admin_key)
    connection_id = created["connection"]["id"]
    key = created["key"]["full_key"]
    learner = f"order-paused-{uuid.uuid4().hex[:6]}"

    with widen_the_window(monkeypatch, DELAY):
        status, paused = call(
            base,
            f"/v1/admin/connections/{connection_id}",
            key=admin_key.full_key,
            body={"status": "paused"},
            method="PATCH",
        )
        assert status == 200, paused
        assert paused["status"] == "paused"

        status, refused = call(
            base,
            "/v1/observe",
            key=key,
            body={"events": [_event(learner, 0, True)]},
            headers={"Idempotency-Key": f"paused-{uuid.uuid4().hex}"},
        )
    assert status == 403, refused
    assert refused["error"]["code"] == "connection_paused"


def test_a_revoked_connection_does_not_authenticate_the_very_next_request(
    base: str, admin_key, monkeypatch
) -> None:
    """Revoked is terminal, and it is terminal on the next request, not the next second."""
    created = _mint(base, admin_key)
    connection_id = created["connection"]["id"]
    key = created["key"]["full_key"]

    with widen_the_window(monkeypatch, DELAY):
        status, revoked = call(
            base,
            f"/v1/admin/connections/{connection_id}",
            key=admin_key.full_key,
            method="DELETE",
        )
        assert status == 200, revoked
        assert revoked["status"] == "revoked"

        status, refused = call(base, "/v1/whoami", key=key)
    assert status == 401, refused
    assert refused["error"]["code"] == "unauthorized"


def test_a_key_revoked_at_the_api_is_dead_on_the_very_next_request(
    base: str, admin_key, monkeypatch
) -> None:
    """`POST /v1/admin/keys/{id}/revoke` answers with a fact about a key."""
    status, issued = call(
        base, "/v1/admin/keys", key=admin_key.full_key, body={"scopes": ["observe"]}
    )
    assert status in (200, 201), issued
    _settle()

    with widen_the_window(monkeypatch, DELAY):
        status, revoked = call(
            base,
            f"/v1/admin/keys/{issued['key_id']}/revoke",
            key=admin_key.full_key,
            body={},
        )
        assert status == 200, revoked
        status, refused = call(base, "/v1/whoami", key=issued["full_key"])
    assert status == 401, refused


def test_an_expired_key_is_refused_on_the_very_next_request(
    base: str, admin_key, monkeypatch
) -> None:
    """Expiry is a fact stored before it is announced, not after."""
    status, issued = call(
        base, "/v1/admin/keys", key=admin_key.full_key, body={"scopes": ["observe"]}
    )
    assert status in (200, 201), issued
    _settle()

    # Not "in the past" — the route refuses that, correctly. Instead: the key is
    # told it now expires at `soon`, and the very next request with it must report
    # `soon`. Before the fix it reported the old expiry, because the UPDATE was
    # still in the request's teardown.
    soon = datetime.now(UTC) + timedelta(minutes=2)
    with widen_the_window(monkeypatch, DELAY):
        status, changed = call(
            base,
            f"/v1/admin/keys/{issued['key_id']}/expiry",
            key=admin_key.full_key,
            body={"expires_at": soon.isoformat()},
        )
        assert status == 200, changed
        status, who = call(base, "/v1/whoami", key=issued["full_key"])
    assert status == 200, who
    reported = datetime.fromisoformat(who["expires_at"].replace("Z", "+00:00"))
    assert abs((reported - soon).total_seconds()) < 5, (reported, soon)


def test_a_learner_can_be_read_back_the_moment_it_is_created(
    base: str, admin_key, monkeypatch
) -> None:
    """`POST /v1/learners` returns an id; the next request must find that learner.

    Not "the next request must be able to decide about it" — a learner with no
    evidence is legitimately unknown to the estimator, and `decide` says so with a
    404 that means something else. The fact the create response asserts is that
    the learner *exists*, so existence is what the next request reads.
    """
    learner = f"order-learner-{uuid.uuid4().hex[:6]}"
    with widen_the_window(monkeypatch, DELAY):
        status, created = call(
            base, "/v1/learners", key=admin_key.full_key, body={"learner_id": learner}
        )
        assert status == 201, created
        status, read_back = call(base, f"/v1/learners/{learner}", key=admin_key.full_key)
    assert status == 200, read_back
    assert read_back["learner_id"] == learner


def test_a_decision_can_be_replayed_the_moment_it_is_served(
    base: str, admin_key, monkeypatch
) -> None:
    """The decision id in the response is a decision id the very next request can use.

    An abstention is a decision too, and it is stored: replaying it must answer
    `identical`, not `decision_unknown`. That is the route's whole purpose — it
    exists to be able to fail — and it can only do that if the row is committed
    before the id is handed out.
    """
    _grant(base, admin_key)
    conn_key = _mint(base, admin_key, scopes=("observe", "decide"))["key"]["full_key"]
    _settle()

    learner = f"order-replay-{uuid.uuid4().hex[:6]}"
    status, created = call(
        base, "/v1/learners", key=admin_key.full_key, body={"learner_id": learner}
    )
    assert status == 201, created
    _settle()

    status, accepted = call(
        base,
        "/v1/observe",
        key=conn_key,
        body={"events": [_event(learner, index, index % 2 == 0) for index in range(8)]},
        headers={"Idempotency-Key": f"replay-events-{uuid.uuid4().hex}"},
    )
    assert status == 202, accepted
    _settle()

    with widen_the_window(monkeypatch, DELAY):
        status, headers, body = _call_with_headers(
            base,
            "/v1/decide",
            key=conn_key,
            body={"learner_id": learner, "skill": SKILL},
            headers={"Idempotency-Key": f"replay-{uuid.uuid4().hex}"},
        )
        assert status == 200, body
        decision_id = headers.get("X-Adapt-Decision-Id")
        assert decision_id, dict(headers)

        status, replayed = call(base, f"/v1/decisions/{decision_id}/replay", key=conn_key, body={})
    assert status == 200, replayed
    assert replayed == body


def test_observed_events_are_visible_to_the_next_decision(
    base: str, admin_key, monkeypatch
) -> None:
    """`202` says the batch is durable when the response is written. Make it true.

    The observe docstring has claimed this since increment 3 — "the batch is durable
    when the response is written" — while the commit happened after the response was
    on the wire. Read back through `/v1/mastery`, which reports how many trials the
    engine can see.
    """
    # A console key cannot observe: ingest needs a connection key (403 not_entitled).
    conn_key = _mint(base, admin_key, scopes=("observe", "decide", "read_model"))["key"]["full_key"]
    _settle()

    learner = f"order-events-{uuid.uuid4().hex[:6]}"
    status, created = call(
        base, "/v1/learners", key=admin_key.full_key, body={"learner_id": learner}
    )
    assert status == 201, created
    _settle()

    events = [_event(learner, index, index % 2 == 0) for index in range(8)]
    with widen_the_window(monkeypatch, DELAY):
        status, accepted = call(
            base,
            "/v1/observe",
            key=conn_key,
            body={"events": events},
            headers={"Idempotency-Key": f"events-{uuid.uuid4().hex}"},
        )
        assert status == 202, accepted
        status, mastery = call(base, f"/v1/mastery/{SKILL}?learner_id={learner}", key=conn_key)
    assert status == 200, mastery
    assert mastery["n"] == 8, mastery


def _event(learner: str, index: int, correct: bool) -> dict:
    return {
        "learner_id": learner,
        "skill": SKILL,
        "item_id": f"item-{index:03d}",
        "correct": correct,
        "response": f"option-{(index % 4) + 1}",
        "ts": datetime.now(UTC).isoformat(),
        "seq": index,
        "latency_ms": 900,
    }


def _call_with_headers(
    base: str, path: str, *, key: str, body: dict, headers: dict[str, str]
) -> tuple[int, Any, Any]:
    """Like `live.call`, but keeps the response headers (the decision id lives there)."""
    payload = json.dumps(body).encode()
    request = urllib.request.Request(
        f"{base}{path}",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            # The headers object is case-insensitive, like HTTP.
            return response.status, response.headers, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, json.loads(exc.read() or b"null")
