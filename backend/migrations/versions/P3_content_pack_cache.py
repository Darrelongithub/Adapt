"""Encrypted generated Pack cache and asynchronous prepare jobs.

Revision ID: P3
Revises: P2
Create Date: 2026-10-14

The cache is keyed by a one-way topic digest plus level, language and spec version; raw topic
text is never stored in the job table. The validated payload contains answer keys and uses the
same field-encryption type as the existing content registry.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Register the cache tables on Base.metadata so ``alembic check`` compares the complete schema.
import adapt.content.skeleton.cache  # noqa: F401
from adapt.crypto.fields import EncryptedJSON

revision: str = "P3"
down_revision: str | None = "P2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {str(row["name"]) for row in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    existing = _tables()
    if "content_pack_cache" not in existing:
        op.create_table(
            "content_pack_cache",
            sa.Column("topic_key_hash", sa.String(length=64), primary_key=True),
            sa.Column("level", sa.Integer(), primary_key=True),
            sa.Column("language", sa.String(length=16), primary_key=True),
            sa.Column("spec_version", sa.String(length=40), primary_key=True),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("payload", EncryptedJSON(context="content.pack_cache"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "ix_content_pack_cache_content_hash" not in _indexes("content_pack_cache"):
        op.create_index(
            "ix_content_pack_cache_content_hash",
            "content_pack_cache",
            ["content_hash"],
            unique=False,
        )
    if "ix_content_pack_cache_source" not in _indexes("content_pack_cache"):
        op.create_index(
            "ix_content_pack_cache_source",
            "content_pack_cache",
            ["source"],
            unique=False,
        )

    if "content_prepare_jobs" not in existing:
        op.create_table(
            "content_prepare_jobs",
            sa.Column("id", sa.String(length=40), primary_key=True),
            sa.Column("cache_key", sa.String(length=64), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False),
            sa.Column("pack_source", sa.String(length=16), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    indexes = _indexes("content_prepare_jobs")
    for name, columns in (
        ("ix_content_prepare_jobs_cache_key", ["cache_key"]),
        ("ix_content_prepare_jobs_state", ["state"]),
        ("ix_content_prepare_jobs_cache_state", ["cache_key", "state"]),
    ):
        if name not in indexes:
            op.create_index(name, "content_prepare_jobs", columns, unique=False)

    if "content_pack_item_calibration" not in _tables():
        op.create_table(
            "content_pack_item_calibration",
            sa.Column("item_id", sa.String(length=40), primary_key=True),
            sa.Column("cache_key", sa.String(length=64), nullable=False),
            sa.Column("declared_difficulty", sa.Float(), nullable=False),
            sa.Column("observed_difficulty", sa.Float(), nullable=False),
            sa.Column("n_attempts", sa.Integer(), nullable=False),
            sa.Column("n_correct", sa.Integer(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("retirement_reason", sa.String(length=48), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    indexes = _indexes("content_pack_item_calibration")
    for name, columns in (
        ("ix_content_pack_item_calibration_cache_key", ["cache_key"]),
        ("ix_content_pack_item_cache_active", ["cache_key", "active"]),
    ):
        if name not in indexes:
            op.create_index(name, "content_pack_item_calibration", columns, unique=False)


def downgrade() -> None:
    tables = _tables()
    if "content_pack_item_calibration" in tables:
        op.drop_table("content_pack_item_calibration")
    if "content_prepare_jobs" in tables:
        op.drop_table("content_prepare_jobs")
    if "content_pack_cache" in tables:
        op.drop_table("content_pack_cache")
