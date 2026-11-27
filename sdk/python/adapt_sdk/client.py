"""Adapt Python SDK — generated from the engine's OpenAPI document.

Do not edit by hand: `python sdk/generate.py` rewrites this file, and
`tests/test_sdk.py` fails when the committed copy is stale.

    from adapt_sdk import Client

    client = Client("https://api.example.com", os.environ["ADAPT_KEY"])
    decision = client.decide("learn-0001", "skill.fractions")
    if decision["abstain"]:
        wait_for(decision["needs"]["trials"])
    else:
        run(decision["recommendations"][0])

Standard library only: `urllib`, no dependencies, so "install" is one file on the
path and the first call is the host's own code, not our packaging.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
import uuid
from typing import Any


class AdaptError(Exception):
    """The contract's error envelope, raised."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        error = body.get("error") or {}
        self.status = status
        self.code = error.get("code")
        self.message = error.get("message")
        self.request_id = error.get("request_id")
        self.retryable = bool(error.get("retryable"))
        self.details = error.get("details") or {}
        super().__init__(f"{self.status} {self.code}: {self.message} (request {self.request_id})")


class Client:
    """One method per host-facing operation, and nothing else."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        user_agent: str = "adapt-python-sdk/generated",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.user_agent = user_agent

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        test_traffic: bool = False,
    ) -> Any:
        """One HTTP call. Every method below is a thin wrapper around this."""
        url = self.base_url + path
        if params:
            pairs = "&".join(f"{key}={urllib.parse.quote(str(value), safe='')}" for key, value in params.items())
            url = f"{url}?{pairs}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", self.user_agent)
        if idempotency_key is None and method != "GET":
            # Derived from the logical operation: same call, same key, so a retry
            # after a timeout consumes one credit and writes one row. Hashed
            # because a batch body is far longer than the 200-character limit.
            canonical = json.dumps(body, sort_keys=True) if body is not None else ""
            digest = hashlib.sha256(f"{method} {path} {canonical}".encode()).hexdigest()[:32]
            idempotency_key = f"adapt-{digest}"
        if idempotency_key is not None:
            request.add_header("Idempotency-Key", idempotency_key)
        if test_traffic:
            request.add_header("X-Adapt-Test", "1")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as error:
            payload = error.read()
            raise AdaptError(error.code, json.loads(payload) if payload else {}) from None

    @staticmethod
    def derive_key(*parts: object) -> str:
        """A stable `Idempotency-Key` from the logical operation.

        Derived from the operation, never from the attempt: a timeout and a retry
        must consume one credit and write one row (INTEGRATION §7). Hashed so the
        key fits the contract's 8-200 characters whatever the body size.
        """
        digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
        return f"adapt-{digest[:32]}"

    def credits_balance(
        self,
    ) -> Any:
        """Get Balance"""
        return self.request(
            "GET",
            "/v1/credits/balance",
        )

    def decide(
        self,
        body: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Any:
        """Decide

        Body (INTEGRATION §6A; the OpenAPI document declares no requestBody):
        {"learner_id": "learn-0001", "skill": "skill.fractions"}
        """
        return self.request(
            "POST",
            "/v1/decide",
            body=body,
            idempotency_key=idempotency_key,
        )

    def replay(
        self,
        decision_id: str,
        idempotency_key: str | None = None,
    ) -> Any:
        """Replay"""
        return self.request(
            "POST",
            f"/v1/decisions/{decision_id}/replay",
            idempotency_key=idempotency_key,
        )

    def learners(
        self,
    ) -> Any:
        """List Learners"""
        return self.request(
            "GET",
            "/v1/learners",
        )

    def create_learner(
        self,
        body: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Any:
        """Create Learner

        Body (INTEGRATION §6A; the OpenAPI document declares no requestBody):
        {"learner_id": "learn-0001", "date_of_birth": "1994-03-02"}
        """
        return self.request(
            "POST",
            "/v1/learners",
            body=body,
            idempotency_key=idempotency_key,
        )

    def learner(
        self,
        learner_id: str,
    ) -> Any:
        """Get Learner"""
        return self.request(
            "GET",
            f"/v1/learners/{learner_id}",
        )

    def mastery(
        self,
        learner_id: str,
    ) -> Any:
        """Mastery Snapshot"""
        return self.request(
            "GET",
            "/v1/mastery",
            params={"learner_id": learner_id},
        )

    def mastery_for(
        self,
        skill: str,
        learner_id: str,
    ) -> Any:
        """Mastery For Skill"""
        return self.request(
            "GET",
            f"/v1/mastery/{skill}",
            params={"learner_id": learner_id},
        )

    def observe(
        self,
        body: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Any:
        """Observe

        Body (INTEGRATION §6A; the OpenAPI document declares no requestBody):
        {"events": [{"learner_id": "learn-0001", "skill": "skill.fractions", "item_id": "item-014", "correct": true, "response": "option-2", "ts": "2026-11-18T12:00:00Z", "seq": 0}]}
        """
        return self.request(
            "POST",
            "/v1/observe",
            body=body,
            idempotency_key=idempotency_key,
        )

    def whoami(
        self,
    ) -> Any:
        """Whoami"""
        return self.request(
            "GET",
            "/v1/whoami",
        )
