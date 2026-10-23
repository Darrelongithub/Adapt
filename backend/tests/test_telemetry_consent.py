"""Regression: analytics opt-out is enforced at the shared event write boundary."""

from sqlalchemy import func, select

from adapt.db.models import Attempt, LearnerEvent
from adapt.learning.telemetry import ALLOWED_EVENTS


def _set_consent(client, learner, enabled: bool):
    response = client.post(
        "/api/v1/me/onboarding",
        json={"preferences": {}, "consent_analytics": enabled, "consent_share_with_llm": False},
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text


def _event_count(services, student_id: str) -> int:
    with services.scope() as session:
        return int(session.execute(select(func.count()).select_from(LearnerEvent).where(LearnerEvent.student_id == student_id)).scalar_one())


def test_opt_out_rejects_every_event_type_and_internal_event_writes(client, services, learner):
    session_id = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    _set_consent(client, learner, False)
    before = _event_count(services, learner["student_id"])

    events = [{"type": event_type, "payload": {"reason": "consent off test"}, "client_id": f"off-{event_type}"} for event_type in ALLOWED_EVENTS]
    refused = client.post("/api/v1/events", json={"session_id": session_id, "events": events}, headers=learner["headers"])
    assert refused.status_code == 200, refused.text
    body = refused.json()
    assert body["accepted"] == 0
    assert body["rejected"] == len(events)
    assert set(body["reasons"]) == {"rejected: analytics consent is off"}
    assert _event_count(services, learner["student_id"]) == before

    # A scored attempt is a learning record, not a blanket exception for behavioral telemetry.
    attempt = client.post(
        "/api/v1/attempts",
        json={"session_id": session_id, "item_id": "itm_fractions_intro_00", "answer": 0},
        headers=learner["headers"],
    )
    assert attempt.status_code == 200, attempt.text
    assert _event_count(services, learner["student_id"]) == before
    with services.scope() as session:
        assert session.execute(select(Attempt).where(Attempt.student_id == learner["student_id"])).scalars().one()
    assert client.get("/api/v1/me").status_code == 200

    _set_consent(client, learner, True)
    control = client.post(
        "/api/v1/events",
        json={"session_id": session_id, "events": [{"type": "pause", "payload": {"reason": "consent restored"}}]},
        headers=learner["headers"],
    )
    assert control.status_code == 200 and control.json()["accepted"] == 1
    assert _event_count(services, learner["student_id"]) == before + 1
