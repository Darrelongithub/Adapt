"""Adults only: a date of birth implying a minor is refused at creation.

`NOTES.md` is unambiguous — "Adults only (18+), everywhere, enforced in code" —
and reopening it needs an ADR and legal review. So this is not a validation
detail: it answers `minor_not_supported`, a distinct code, because an integrator
that cannot tell "bad payload" from "this product does not serve minors" will
retry the wrong way.

The gate has to run where the date is still plaintext. `learners.date_of_birth`
is ciphertext (increment 5) and a CHECK cannot read it, so the enforcement is in
the route, and `age_on` is a pure function with its own boundary tests: a
birthday that falls *today* is a birthday, not a year.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from adapt_engine.api.learners import Learner
from adapt_engine.clock import FixedClock, SystemClock
from adapt_engine.db.schema import (
    MINIMUM_AGE,
    PROFILES,
    ProfileMismatch,
    age_on,
    assert_profile,
)
from adapt_engine.main import create_app
from tests.conftest import make_settings
from tests.pg import bootstrap_key, provision_tenant, synthetic_suffix

TODAY = date(2026, 11, 17)


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"age-{uuid.uuid4().hex[:8]}", name="Age Gate")


@pytest.fixture
def client(database, app_engine: Engine, tenant: uuid.UUID) -> tuple[TestClient, dict[str, str]]:
    key = bootstrap_key(database, tenant, scopes=("observe", "admin"))
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
    )
    return TestClient(app, raise_server_exceptions=False), {
        "Authorization": f"Bearer {key.full_key}"
    }


def test_a_minor_is_refused_with_its_own_code(client) -> None:
    http, headers = client
    almost = date.today() - timedelta(days=365 * 17 + 200)
    resp = http.post(
        "/v1/learners",
        headers=headers,
        json={
            "learner_id": f"learner-minor-{synthetic_suffix()}",
            "date_of_birth": almost.isoformat(),
        },
    )
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "minor_not_supported"
    assert body["retryable"] is False
    assert "18" in body["message"]
    # The code is not a synonym for "bad request": nothing about the payload
    # shape was wrong.
    assert body["code"] != "payload_invalid"


def test_an_adult_is_accepted(client) -> None:
    http, headers = client
    adult = date.today() - timedelta(days=365 * 30)
    resp = http.post(
        "/v1/learners",
        headers=headers,
        json={
            "learner_id": f"learner-adult-{synthetic_suffix()}",
            "date_of_birth": adult.isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["date_of_birth"] == adult.isoformat()


def test_no_date_of_birth_is_allowed(client) -> None:
    """The engine can hold a learner without a DOB; the gate is on the value."""
    http, headers = client
    resp = http.post(
        "/v1/learners",
        headers=headers,
        json={"learner_id": f"learner-nodob-{synthetic_suffix()}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["date_of_birth"] is None


def test_the_gate_is_checked_before_anything_is_written(client, app_engine, tenant) -> None:
    from sqlalchemy import text

    from adapt_engine.db.tenant import tenant_transaction

    http, headers = client
    learner_id = f"learner-refused-{synthetic_suffix()}"
    http.post(
        "/v1/learners",
        headers=headers,
        json={"learner_id": learner_id, "date_of_birth": "2015-01-01"},
    )
    with tenant_transaction(app_engine, tenant) as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM learners WHERE learner_id = :lid"), {"lid": learner_id}
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    ("dob", "expected"),
    [
        (date(2008, 11, 18), 17),  # tomorrow is the 18th birthday
        (date(2008, 11, 17), 18),  # the birthday itself counts
        (date(2008, 11, 16), 18),
        (date(1900, 1, 1), 126),
        (TODAY, 0),
    ],
)
def test_age_is_computed_on_birthdays(dob: date, expected: int) -> None:
    assert age_on(dob, TODAY) == expected


def test_the_threshold_is_eighteen() -> None:
    assert MINIMUM_AGE == 18


def test_the_boundary_does_not_use_the_clock_directly(monkeypatch) -> None:
    """A fixed clock decides the age, so the boundary is testable at any date."""
    dob = date(2008, 11, 18)
    clock = FixedClock(datetime(2026, 11, 17, 12, 0, tzinfo=UTC))
    assert age_on(dob, clock.now().date()) == 17
    tomorrow = FixedClock(datetime(2026, 11, 18, 12, 0, tzinfo=UTC))
    assert age_on(dob, tomorrow.now().date()) == 18


# ---------------------------------------------------------------------------
# assert_profile
# ---------------------------------------------------------------------------


def test_assert_profile_accepts_the_expected_profile() -> None:
    for profile in PROFILES:
        assert_profile({"profile": profile}, profile)


def test_assert_profile_rejects_another_profile() -> None:
    with pytest.raises(ProfileMismatch) as refused:
        assert_profile({"profile": "adult_general"}, "cognitive_support")
    assert "cognitive_support" in str(refused.value)


def test_assert_profile_rejects_an_unknown_profile() -> None:
    with pytest.raises(ProfileMismatch):
        assert_profile({"profile": "visual_learner"}, "adult_general")


def test_assert_profile_rejects_a_row_without_one() -> None:
    with pytest.raises(ProfileMismatch):
        assert_profile({"learner_id": "learner-x"}, "adult_general")
    with pytest.raises(ProfileMismatch):
        assert_profile(object(), "adult_general")


def test_assert_profile_works_on_a_response_model() -> None:
    """The API returns `Learner` objects, and the helper is used on those too."""
    learner = Learner(
        id=uuid.uuid4(),
        learner_id="learner-profile-0001",
        profile="cognitive_support",
        date_of_birth=None,
    )
    assert_profile(learner, "cognitive_support")
    with pytest.raises(ProfileMismatch):
        assert_profile(learner, "adult_general")
