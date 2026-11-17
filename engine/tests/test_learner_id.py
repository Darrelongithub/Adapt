"""learner_id must not be an identifier a human can be recognised by.

Two layers: the CHECK constraint in the database, and ``validate_learner_id``
so a caller gets a clear error instead of a constraint violation. Both are
tested, because application validation alone does not survive a migration or a
script that bypasses the API.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from adapt_engine.db.schema import LearnerIdInvalid, validate_learner_id
from adapt_engine.db.tenant import tenant_transaction
from tests.pg import provision_tenant

VALID = [
    "learner-0a1b2c3d",
    "abc12345",
    "l_0001-abcd",
    "x" * 64,
]

INVALID = {
    "email": "someone@example.com",
    "plus": "learner+tag1234",
    "long digit run": "learner-1234567",
    "phone-like": "254712345678",
    "uppercase": "Learner-ABCD1234",
    "too short": "abc1234",
    "leading separator": "-learner1",
}


@pytest.mark.parametrize("value", VALID)
def test_valid_learner_ids_are_accepted(value: str) -> None:
    assert validate_learner_id(value) == value


@pytest.mark.parametrize(("label", "value"), sorted(INVALID.items()))
def test_invalid_learner_ids_are_rejected(label: str, value: str) -> None:
    with pytest.raises(LearnerIdInvalid):
        validate_learner_id(value)


def test_email_rejection_names_the_reason() -> None:
    with pytest.raises(LearnerIdInvalid, match="email"):
        validate_learner_id("someone@example.com")


@pytest.mark.parametrize(("label", "value"), sorted(INVALID.items()))
def test_database_check_rejects_the_same_values(
    app_engine: Engine, label: str, value: str, database
) -> None:
    """The CHECK constraint is the layer a script cannot skip."""
    tenant_id = provision_tenant(
        database, slug=f"lid-{uuid.uuid4().hex[:10]}", name=f"Learner Id {label}"
    )
    with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
        with tenant_transaction(app_engine, tenant_id) as conn:
            conn.execute(
                text(
                    "INSERT INTO learners (tenant_id, learner_id, profile) "
                    "VALUES (:t, :lid, 'adult_general')"
                ),
                {"t": tenant_id, "lid": value},
            )
    assert "learners_learner_id_format" in str(excinfo.value)


def test_database_accepts_a_valid_learner_id(app_engine: Engine, database) -> None:
    tenant_id = provision_tenant(
        database, slug=f"lid-ok-{uuid.uuid4().hex[:8]}", name="Learner Id Ok"
    )
    with tenant_transaction(app_engine, tenant_id) as conn:
        conn.execute(
            text(
                "INSERT INTO learners (tenant_id, learner_id, profile) "
                "VALUES (:t, 'learner-0a1b2c3d', 'adult_general')"
            ),
            {"t": tenant_id},
        )
        assert conn.execute(text("SELECT count(*) FROM learners")).scalar_one() == 1
