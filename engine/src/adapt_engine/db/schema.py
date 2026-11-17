"""Table definitions and the ``learner_id`` format rule.

The migration is written by hand in ``engine/migrations/versions`` because the
policy and grant statements are the part that matters and autogenerate cannot
produce them. These Core tables exist so application code and tests name
columns once; ``tests/test_schema.py`` checks the live database against what
the migration claims, so the two cannot drift unnoticed.

Pseudo-identifiers, not anonymity: a ``learner_id`` must not be an email
address, a phone number or anything else that re-identifies on sight
(PRIVACY.md §2).
"""

from __future__ import annotations

import re
from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

#: PRIVACY.md §2: lowercase, starts alphanumeric, 8-64 characters. The
#: character class alone rejects ``@`` and ``+``.
LEARNER_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{7,63}$")

#: A run of digits long enough to be a phone number or a national id.
LEARNER_ID_DIGIT_RUN_PATTERN: Final = re.compile(r"[0-9]{7,}")

#: Same two rules as the CHECK constraint in the migration.
LEARNER_ID_SQL_CHECK: Final = (
    "learner_id ~ '^[a-z0-9][a-z0-9_-]{7,63}$' AND learner_id !~ '[0-9]{7,}'"
)

PROFILES: Final = ("adult_general", "cognitive_support")

metadata = sa.MetaData()

tenants = sa.Table(
    "tenants",
    metadata,
    sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    ),
    sa.Column("slug", sa.Text, nullable=False, unique=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("profile", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

learners = sa.Table(
    "learners",
    metadata,
    sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    ),
    sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("learner_id", sa.Text, nullable=False),
    sa.Column("profile", sa.Text, nullable=False),
    sa.Column("date_of_birth", sa.Date, nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("tenant_id", "learner_id", name="learners_tenant_learner_id_key"),
)

audit_log = sa.Table(
    "audit_log",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column(
        "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("actor", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("target", sa.Text, nullable=True),
    sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
)

#: Every table that carries ``tenant_id`` and therefore needs forced RLS.
TENANT_SCOPED_TABLES: Final = ("learners", "audit_log")


class LearnerIdInvalid(ValueError):
    """The caller supplied a learner id that could identify a person."""


def validate_learner_id(value: str) -> str:
    """Return ``value`` if it is an acceptable pseudo-identifier.

    Rejects an email address (``@``), a phone number (``+`` or a long digit
    run), uppercase, and anything shorter than 8 characters. The database
    enforces the same two rules with a CHECK constraint; this raises early so
    the caller gets a clear message instead of a constraint violation.
    """
    if not isinstance(value, str):
        raise LearnerIdInvalid("learner_id must be a string")
    if "@" in value:
        raise LearnerIdInvalid("learner_id must not contain '@' (looks like an email address)")
    if "+" in value:
        raise LearnerIdInvalid("learner_id must not contain '+' (looks like a phone number)")
    if LEARNER_ID_DIGIT_RUN_PATTERN.search(value):
        raise LearnerIdInvalid(
            "learner_id must not contain a run of 7 or more digits (looks like an id number)"
        )
    if not LEARNER_ID_PATTERN.fullmatch(value):
        raise LearnerIdInvalid(
            "learner_id must match ^[a-z0-9][a-z0-9_-]{7,63}$ (lowercase, 8-64 characters)"
        )
    return value
