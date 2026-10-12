"""Migrate the same database the app uses, with the app's own engine settings.

The URL is never duplicated here: it comes from :func:`adapt.db.engine.create_db_engine`,
so ``ADAPT_DATA_DIR`` / ``ADAPT_DB_PATH`` decide what gets migrated and the encrypted
column types (which need the cipher configured before mappers run) are set up exactly as
they are at application startup.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adapt.config import get_settings  # noqa: E402
from adapt.db.engine import create_db_engine  # noqa: E402
from adapt.db.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Emit ADAPT's own column types in generated revisions as imported names.

    Autogenerate writes what it sees, and the encrypted columns are
    ``adapt.crypto.fields.EncryptedJSON(context="audit.detail")`` -- a dotted path that is
    not imported in the revision file, so ``alembic upgrade`` died with ``NameError: name
    'adapt' is not defined``. The context string is part of the column definition (it feeds
    the AAD), so it is rendered too: otherwise ``alembic check`` reports a type change on
    every encrypted column and the one signal that catches a model drifting from the
    migrations becomes noise.
    """
    module = getattr(type(obj), "__module__", "") or ""
    if not module.startswith("adapt."):
        return False
    name = type(obj).__name__
    autogen_context.imports.add(f"from {module} import {name}")
    context = getattr(obj, "context", None)
    if context is not None:
        try:
            type(obj)(context=context)  # does this type take it, or fix it internally?
        except TypeError:
            # BlindIndexedEmail hard-codes its own context, so rendering the arguments
            # would be a lie the type cannot even accept.
            return f"{name}()"
        return f"{name}(context={context!r})"
    return f"{name}()"

# ``alembic -x data_dir=/tmp/foo`` for a scratch database in tests and by hand.
override_dir = context.get_x_argument(as_dictionary=True).get("data_dir") or os.environ.get("ADAPT_DATA_DIR")
if override_dir:
    os.environ["ADAPT_DATA_DIR"] = override_dir

settings = get_settings()
_engine, driver = create_db_engine(settings)


def run_migrations_offline() -> None:
    context.configure(
        url=_engine.url,
        target_metadata=target_metadata,
        render_item=render_item,
        literal_binds=True,
        render_as_batch=True,  # SQLite cannot ALTER most things; batch rebuilds the table
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # The migration connection is the *application's engine*, not one rebuilt from
    # ``alembic.ini``. That engine carries ``module=sqlcipher3`` plus the ``connect``
    # listener that issues ``PRAGMA key`` and the cipher pragmas (``adapt/db/engine.py``);
    # ``engine_from_config(url=...)`` re-derives a plain-sqlite3 engine from the URL alone,
    # and the URL carries neither the module nor the key. What that does, in order of how
    # hard it is to notice: an already-encrypted database cannot be opened at all, and a
    # database created by a migration in a fresh ``ADAPT_DATA_DIR`` is written in plaintext,
    # which the application then cannot read and which nothing on disk protects.
    connectable = _engine
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    _engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
