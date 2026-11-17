"""Health and version acceptance tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_MASTER_KEY, TEST_PEPPER, make_client


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
