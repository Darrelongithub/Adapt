"""The startup upgrade path, on the application's existing (possibly SQLCipher) engine.

Older app boots used create_all without an Alembic marker. Adopt those using the
same baseline as the documented recovery procedure, then replay existence-aware
revisions. Never stamp an already-versioned database backwards or recreate its data.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from .models import Base

log = logging.getLogger("adapt.db.schema")


def upgrade_schema(engine: Engine) -> None:
    migrations = Path(__file__).resolve().parents[3] / "migrations"
    config = Config()  # No fileConfig: preserve the application's logging configuration.
    config.set_main_option("script_location", str(migrations))
    with engine.connect() as connection:
        # Serialize startup upgrades across workers. An explicit transaction also makes
        # SQLite DDL rollback together with the revision marker on failure.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            config.attributes["connection"] = connection
            revisions = MigrationContext.configure(connection).get_current_heads()
            tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
            if not revisions and tables:
                log.info("Upgrading an older unversioned database; learner data is retained")
                # create_all fills missing tables, not missing columns. Revisions 0003+
                # then repair existing tables (including items.verification in 0005).
                Base.metadata.create_all(connection)
                command.stamp(config, "0002")
            command.upgrade(config, "head")
            inspector = inspect(connection)
            missing = []
            for table in Base.metadata.sorted_tables:
                present = {col["name"] for col in inspector.get_columns(table.name)}
                missing.extend(
                    f"{table.name}.{col.name}" for col in table.columns if col.name not in present
                )
            if missing:
                raise RuntimeError(
                    "Database upgrade is incomplete; missing columns: " + ", ".join(missing)
                    + ". Startup stopped without seeding. Do not delete your database or key."
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
