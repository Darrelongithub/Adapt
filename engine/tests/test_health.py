"""Health and version acceptance tests."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from adapt_engine.clock import FixedClock
from adapt_engine.main import create_app
from tests.conftest import TEST_MASTER_KEY, TEST_PEPPER, make_client, make_settings


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"status", "version", "commit", "db", "redis", "capabilities"}
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["redis"] is True
    assert body["version"] == "0.1.0"
    assert body["commit"] == "test-commit-synthetic"


def test_health_capabilities_booleans_only(client: TestClient) -> None:
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    caps = resp.json()["capabilities"]
    assert isinstance(caps, dict)
    assert len(caps) > 0
    for value in caps.values():
        assert isinstance(value, bool)


def test_health_db_down_names_dependency() -> None:
    client = make_client(db_ok=False, redis_ok=True)
    resp = client.get("/v1/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["db"] is False
    assert body["redis"] is True


def test_health_redis_down_names_dependency() -> None:
    client = make_client(db_ok=True, redis_ok=False)
    resp = client.get("/v1/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["db"] is True
    assert body["redis"] is False


def test_health_both_down() -> None:
    client = make_client(db_ok=False, redis_ok=False)
    resp = client.get("/v1/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["db"] is False
    assert body["redis"] is False


def test_health_no_secret_leak(client: TestClient) -> None:
    resp = client.get("/v1/health")
    text = resp.text
    assert TEST_MASTER_KEY not in text
    assert TEST_PEPPER not in text
    assert "adapt_test" not in text
    assert "postgresql://" not in text


def test_version(client: TestClient) -> None:
    resp = client.get("/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"version", "commit", "build_time"}
    assert body["version"] == "0.1.0"
    assert body["commit"] == "test-commit-synthetic"
    assert body["build_time"] == "2026-11-17T00:00:00Z"


def test_version_no_secret_leak(client: TestClient) -> None:
    resp = client.get("/v1/version")
    text = resp.text
    assert TEST_MASTER_KEY not in text
    assert TEST_PEPPER not in text
    assert "postgresql://" not in text


def _start_tarpit() -> tuple[socket.socket, int]:
    """A listener that accepts connections and never answers.

    Standing in for a blackholed database: the TCP handshake completes, so the
    client blocks in the protocol exchange instead of failing fast.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    return server, server.getsockname()[1]


def test_health_is_bounded_when_database_hangs() -> None:
    """A hung database yields 503 quickly instead of holding the request open.

    The database URL points at a tarpit on loopback: the TCP handshake
    succeeds and the connection then hangs in the protocol exchange, which is
    what a blackholed host looks like from the client's side. Measured through
    the ASGI app rather than TestClient, because TestClient's portal adds a
    fixed teardown delay that would mask the real bound.
    """
    server, port = _start_tarpit()
    held: list[socket.socket] = []

    def _accept_in_background() -> None:
        try:
            while True:
                conn, _ = server.accept()
                held.append(conn)  # keep it open, never reply
        except OSError:
            return

    acceptor = threading.Thread(target=_accept_in_background, daemon=True)
    acceptor.start()
    try:
        settings = make_settings(database_url=f"postgresql://u:p@127.0.0.1:{port}/adapt")
        app = create_app(
            settings,
            clock=FixedClock(datetime(2026, 11, 17, 0, 0, 0, tzinfo=UTC)),
            # Real bounded DB check against the tarpit; Redis faked as up so the
            # assertion isolates the hanging dependency.
            redis_check=lambda: True,
        )

        async def _call() -> tuple[int, dict, float]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                started = time.monotonic()
                resp = await client.get("/v1/health")
                return resp.status_code, resp.json(), time.monotonic() - started

        status, body, elapsed = asyncio.run(_call())
        assert status == 503
        assert body["db"] is False
        assert body["status"] == "unavailable"
        assert elapsed < 2.0, f"health took {elapsed:.2f}s; the probe is not bounded"
    finally:
        server.close()
        for conn in held:
            conn.close()
