"""Database engine factory and reachability check.

The check never raises and never logs the URL. Health reports only a
boolean, so a failed check names the dependency without leaking detail.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine, create_engine


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for the given URL."""
    if database_url.startswith("sqlite"):
        return create_engine(database_url)
    return create_engine(
        database_url,
        connect_args={"connect_timeout": 2},
        pool_pre_ping=True,
    )


def check_database(database_url: str) -> bool:
    """Return True when a SELECT 1 succeeds, False otherwise."""
    engine: Engine | None = None
    try:
        engine = create_db_engine(database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        if engine is not None:
            engine.dispose()
