"""Database package: engine construction, encryption mode selection, sessions.

``adapt.db`` is the only module the rest of the app needs in order to talk to
SQLite; nothing else opens a connection directly. See :mod:`adapt.db.engine` for
the SQLCipher-vs-field-encryption decision and :mod:`adapt.db.models` for the
schema map onto blueprint §6 (every data category the blueprint lists has a table).
"""

from __future__ import annotations

from .engine import (
    Database,
    DriverInfo,
    create_db_engine,
    get_db,
    get_keyring,
    session_scope,
    set_db,
)
from .models import Base, EventType, ExperienceMode, new_id, utcnow

__all__ = [
    "Base",
    "Database",
    "DriverInfo",
    "EventType",
    "ExperienceMode",
    "create_db_engine",
    "get_db",
    "get_keyring",
    "new_id",
    "session_scope",
    "set_db",
    "utcnow",
]
