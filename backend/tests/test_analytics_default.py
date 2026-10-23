"""Regression: omitted analytics consent never enables telemetry."""

import pytest


@pytest.mark.parametrize("registered", [False, True], ids=["guest", "registered"])
def test_omitted_consent_defaults_off_until_explicit_opt_in(client, registered):
    payload = {"display_name": "Consent default", "age_band": "16-18"}
    if registered:
        payload.update(username="consent.default", password="correct horse 42")
    else:
        payload["as_guest"] = True
    created = client.post("/api/v1/students", json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["consent_analytics"] is False
    headers = {"x-csrf-token": created.json()["csrf_token"]}
    assert client.get("/api/v1/me").json()["consent_analytics"] is False

    onboarded = client.post(
        "/api/v1/me/onboarding",
        json={"preferences": {}, "consent_share_with_llm": False},
        headers=headers,
    )
    assert onboarded.status_code == 200 and onboarded.json()["consent"]["analytics"] is False
    session_id = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=headers).json()["session_id"]
    refused = client.post(
        "/api/v1/events",
        json={"session_id": session_id, "events": [{"type": "pause", "payload": {"reason": "default off"}}]},
        headers=headers,
    )
    assert refused.json()["accepted"] == 0 and refused.json()["rejected"] == 1

    opted_in = client.post(
        "/api/v1/me/onboarding",
        json={"preferences": {}, "consent_analytics": True, "consent_share_with_llm": False},
        headers=headers,
    )
    assert opted_in.json()["consent"]["analytics"] is True
    accepted = client.post(
        "/api/v1/events",
        json={"session_id": session_id, "events": [{"type": "pause", "payload": {"reason": "explicit opt in"}}]},
        headers=headers,
    )
    assert accepted.json()["accepted"] == 1
