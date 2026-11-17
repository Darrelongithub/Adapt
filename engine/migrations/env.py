"""Alembic environment.

Migrations run as ``adapt_owner`` (the table owner), never as ``adapt_app``
and never as a superuser: a superuser would bypass RLS, and the migration
would then "succeed" in an environment where the app could never read a row.

The URL comes from ``ADAPT_MIGRATION_DATABASE_URL``. There is deliberately no
fallback to ``ADAPT_DATABASE_URL``: running a migration with the runtime role
is exactly the mistake this split exists to prevent.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapt_engine.db.schema import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _database_url() -> str:
    url = os.environ.get("ADAPT_MIGRATION_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "ADAPT_MIGRATION_DATABASE_URL is not set: migrations must run as the "
            "table owner (adapt_owner), not as the runtime role (adapt_app)"
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
