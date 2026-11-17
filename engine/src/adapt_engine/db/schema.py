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

api_keys = sa.Table(
    "api_keys",
    metadata,
    sa.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("prefix", sa.Text, nullable=False, unique=True),
    sa.Column("hash", sa.LargeBinary, nullable=False),
    sa.Column("scopes", sa.ARRAY(sa.Text), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
)

credit_grants = sa.Table(
    "credit_grants",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("amount", sa.Integer, nullable=False),
    sa.Column("remaining", sa.Integer, nullable=False),
    sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("source_ref", sa.Text, nullable=True),
)

credit_ledger = sa.Table(
    "credit_ledger",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("grant_id", UUID(as_uuid=True), nullable=True),
    sa.Column("delta", sa.Integer, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("ref", sa.Text, nullable=True),
    sa.Column("idempotency_key", sa.Text, nullable=False),
    sa.Column("period_start", sa.Date, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

credit_reservations = sa.Table(
    "credit_reservations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("amount", sa.Integer, nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("operation_ref", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
)

credit_reservation_draws = sa.Table(
    "credit_reservation_draws",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("reservation_id", UUID(as_uuid=True), nullable=False),
    sa.Column("grant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("amount", sa.Integer, nullable=False),
)

#: Every table that carries ``tenant_id`` and therefore needs forced RLS.
#: ``api_keys`` is the one deliberate exception: RLS is enabled but not forced,
#: because the SECURITY DEFINER resolver is owned by the table owner and FORCE
#: would make it blind (see DECISIONS.md and tests/test_api_keys.py).
TENANT_SCOPED_TABLES: Final = (
    "learners",
    "audit_log",
    "api_keys",
    "credit_grants",
    "credit_ledger",
    "credit_reservations",
    "credit_reservation_draws",
)

#: Tables with RLS enabled but not forced, and why.
RLS_NOT_FORCED: Final = {
    "api_keys": "SECURITY DEFINER resolver owned by adapt_owner must read by prefix",
}


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
