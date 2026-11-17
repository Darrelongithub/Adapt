"""Alembic up → down → up, on a database of its own.

The down migration is the one nobody runs until an incident, so it is
exercised here against a clean database rather than trusted.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from tests import pg

TABLES = (
    "api_keys",
    "audit_log",
    "credit_grants",
    "credit_ledger",
    "credit_reservation_draws",
    "credit_reservations",
    "learners",
    "tenants",
)


def _tables(database: pg.Database) -> list[str]:
    engine = create_engine(database.owner_url, future=True)
    try:
        with engine.connect() as conn:
            return sorted(
                conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def _policies(database: pg.Database) -> list[tuple[str, bool, bool]]:
    engine = create_engine(database.owner_url, future=True)
    try:
        with engine.connect() as conn:
            return sorted(
                conn.execute(
                    text(
                        """
                        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                          FROM pg_class c
                          JOIN pg_namespace n ON n.oid = c.relnamespace
                         WHERE n.nspname = 'public'
                           AND c.relname IN ('tenants', 'learners', 'audit_log')
                        """
                    )
                ).all()
            )
    finally:
        engine.dispose()


def test_up_down_up_on_a_clean_database() -> None:
    database = pg.provision(pg.MIGRATIONS_DATABASE, migrate=False)
    try:
        assert _tables(database) == [], "the fresh database was not clean"

        # up
        pg.upgrade(database)
        assert _tables(database) == list(TABLES)
        assert _policies(database) == [
            ("audit_log", True, True),
            ("learners", True, True),
            ("tenants", True, True),
        ]

        # down
        pg.downgrade(database, "base")
        assert _tables(database) == [], "downgrade left tables behind"

        # up again, on the same database
        pg.upgrade(database)
        assert _tables(database) == list(TABLES)
        assert _policies(database) == [
            ("audit_log", True, True),
            ("learners", True, True),
            ("tenants", True, True),
        ]
    finally:
        pg.drop_database(database)


def _strip_function_bodies(source: str) -> str:
    """Remove dollar-quoted bodies: a function body is schema, not data.

    The rule below exists to keep migrations free of data manipulation —
    a backfill, a seed row, a DELETE written as an afterthought. A SQL function
    that *contains* an INSERT is schema: the statement only runs when the
    function is called at runtime, long after the migration. So the bodies go,
    and what is left has to be clean.
    """
    import re

    return re.sub(r"\$\w*\$.*?\$\w*\$", "", source, flags=re.DOTALL)


def test_migration_contains_no_data_statements() -> None:
    """No INSERT/UPDATE/DELETE/COPY in a migration body: schema only."""
    import re
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    for path in sorted(versions.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        code_only = "\n".join(
            line for line in _strip_function_bodies(source).splitlines()
            if not line.strip().startswith("#")
        )
        for pattern in (
            r"\bINSERT\s+INTO\b",
            r"\bUPDATE\s+\w+\s+SET\b",
            r"\bDELETE\s+FROM\b",
            r"\bCOPY\b",
        ):
            assert re.search(pattern, code_only, re.IGNORECASE) is None, (
                f"{path.name} contains {pattern!r}; migrations carry schema, not data"
            )
