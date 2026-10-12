"""The migration path with whole-file encryption on.

``test_migrations.py`` exercises the same commands in the suite's own configuration,
``ADAPT_DB_ENCRYPTION=field-only``: a plain SQLite file whose sensitive *columns* are
encrypted. That is one of the two ways ADAPT runs, and it is the one that works.

The other way -- the default, ``auto``/``sqlcipher``, a file encrypted end to end by
SQLCipher -- was dead. ``migrations/env.py`` built its connection from
``str(_engine.url)``, and a URL carries no driver: SQLAlchemy fell back to the stock
``sqlite3`` module, so ``alembic upgrade`` on an encrypted installation either wrote a
plaintext database or failed to read the encrypted one the app had already created
("file is not a database"). Both directions were unusable, while the comment next to it
said the cipher and the keyring were set up on purpose.

The sibling file's ``_schema()`` reads the migrated database with plain ``sqlite3`` -- it
has to, because that is what its configuration produces -- so it could not have caught
this. These tests read the database the way the app does, through ``create_db_engine``,
and assert on the driver that reports.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
NEW_ITEM_COLUMNS = {"generated_by", "prior_difficulty", "n_attempts", "n_correct", "calibrated_at"}
_ALEMBIC = str(Path(sys.executable).parent / ("alembic.exe" if sys.platform == "win32" else "alembic"))


@pytest.fixture(autouse=True)
def _needs_sqlcipher():
    pytest.importorskip("sqlcipher3", reason="the encrypted-file path needs SQLCipher")


def _env(data_dir: Path) -> dict[str, str]:
    """A fresh installation's environment, not the test session's.

    ``conftest.py`` runs the suite with ``ADAPT_DB_ENCRYPTION=field-only`` and points
    ``ADAPT_KEYFILE`` at the session's key. Inheriting either would make these tests
    exercise a plaintext database -- the configuration that hid the bug they guard.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ADAPT_DB_KEY", "ADAPT_DB_PASSPHRASE", "ADAPT_KEYFILE", "ADAPT_DB_PATH")
    }
    env.update(
        {
            "ADAPT_DATA_DIR": str(data_dir),
            "ADAPT_MODE": "local",
            "ADAPT_DB_ENCRYPTION": "sqlcipher",
        }
    )
    return env


def _alembic(*args: str, data_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_ALEMBIC, "-c", str(BACKEND / "alembic.ini"), *args],
        cwd=str(BACKEND),
        env=_env(data_dir),
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _boot_the_app(data_dir: Path) -> subprocess.CompletedProcess:
    """Build the schema the way the application does at startup."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(BACKEND / 'src')!r})\n"
            "from adapt.db.engine import get_db\n"
            "get_db().create_all()\n"
            "print('created')",
        ],
        cwd=str(BACKEND),
        env=_env(data_dir),
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _inspect(data_dir: Path, *, orm: bool = True) -> dict:
    """Open the database exactly as the app would, and report what it finds.

    ``orm=False`` skips the ``Item`` query, which is what a downgraded database needs: the
    model declares the five calibration columns and the older table does not have them, so
    running the query there would be asserting the defect.
    """
    query = """
with Session(engine) as s:
    # Emits a SELECT over every column the model declares: a revision that forgot one, or
    # created it under the wrong type, fails here rather than in production.
    rows = len(s.query(Item).limit(1).all())
""" if orm else """
rows = None  # schema-only: this database is deliberately behind the models
"""
    snippet = (
        """
import json, sys
sys.path.insert(0, "src")
import sqlalchemy as sa
from sqlalchemy.orm import Session
from adapt.config import get_settings
from adapt.db.engine import create_db_engine
from adapt.db.models import Item

engine, info = create_db_engine(get_settings())
with engine.connect() as c:
    revision = c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    columns = [r[1] for r in c.execute(sa.text("PRAGMA table_info(items)"))]
"""
        + query
        + """
print(json.dumps({**info.as_dict(), "revision": revision, "columns": columns, "rows": rows}))
"""
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(BACKEND),
        env=_env(data_dir),
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, f"the app could not open its own database:\n{result.stderr[-2500:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture()
def migrated(tmp_path):
    """A database built by the CLI, on an encrypted installation."""
    data_dir = tmp_path / "migrated"
    data_dir.mkdir()
    result = _alembic("upgrade", "head", data_dir=data_dir)
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
    return data_dir


def test_the_migration_writes_a_database_the_app_can_open(migrated):
    """The file must be encrypted the way the app expects, not merely present.

    The failure mode is not a missing file. It is a file of the right shape that the app
    cannot read: ``alembic upgrade`` left a plaintext database, and every later
    ``connect()`` raised "file is not a database".
    """
    found = _inspect(migrated)
    assert found["driver"] == "sqlcipher", found
    assert found["file_encrypted"] is True, found

    assert found["revision"] is not None, "the database was never stamped with a revision"
    missing = NEW_ITEM_COLUMNS - set(found["columns"])
    assert not missing, f"the migrated items table is missing {sorted(missing)}"
    assert found["rows"] == 0, "a fresh migration should hold no items"


def test_an_existing_encrypted_database_can_be_stamped_then_upgraded(tmp_path):
    """The path an operator actually takes: a database already full of learner history.

    It was created by ``create_all``, so it has no ``alembic_version`` row; ``stamp``
    declares it current and later revisions then apply. Every step here is a different
    process, and the file is encrypted end to end, so before the fix the very first
    ``stamp`` died on a database the app had just written.
    """
    data_dir = tmp_path / "existing"
    data_dir.mkdir()

    boot = _boot_the_app(data_dir)
    assert boot.returncode == 0, boot.stderr[-2000:]

    stamped = _alembic("stamp", "head", data_dir=data_dir)
    assert stamped.returncode == 0, f"stamp failed:\n{stamped.stdout[-2000:]}\n{stamped.stderr[-2000:]}"

    again = _alembic("upgrade", "head", data_dir=data_dir)
    assert again.returncode == 0, f"upgrade after stamp failed:\n{again.stderr[-2000:]}"

    found = _inspect(data_dir)
    assert set(found["columns"]) >= NEW_ITEM_COLUMNS, "stamping lost columns"
    assert found["rows"] == 0


def test_a_revision_can_be_undone_and_reapplied(migrated):
    """A downgrade that leaves the columns behind makes the next upgrade fail."""
    down = _alembic("downgrade", "0001", data_dir=migrated)
    assert down.returncode == 0, f"{down.stdout[-1500:]}\n{down.stderr[-1500:]}"
    after_downgrade = set(_inspect(migrated, orm=False)["columns"])
    assert not (NEW_ITEM_COLUMNS & after_downgrade), "downgrade left the calibration columns behind"

    up = _alembic("upgrade", "head", data_dir=migrated)
    assert up.returncode == 0, f"{up.stdout[-1500:]}\n{up.stderr[-1500:]}"
    after_upgrade = set(_inspect(migrated)["columns"])
    assert after_upgrade >= NEW_ITEM_COLUMNS, "re-upgrading did not restore the columns"
