"""Migrations: the part ``create_all`` cannot serve.

``Base.metadata.create_all`` builds a *fresh* database correctly and does nothing at all
for an existing one -- which is the only kind that matters once a learner has history. The
spec asks for Alembic, and the dependency was declared for the whole build while
``backend/alembic.ini`` did not exist, i.e. "migrations" were a promise with nothing behind
it. These tests are the floor under that promise:

* ``upgrade head`` on an empty database produces exactly the schema the models describe;
* nothing the models declare is missing from the migration chain (``alembic check``),
  which is the check that catches the next column added to a model without a revision;
* a database the app already created can be stamped and then upgraded without complaint,
  because that is the actual situation for anybody who ran an earlier build.

Everything runs in a subprocess on a scratch ``ADAPT_DATA_DIR``. In-process would let the
migration build a keyring for a *different* directory and overwrite the process-wide
cipher the rest of the session's tests encrypt with -- a cross-test failure that looks
exactly like a data-corruption bug.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from adapt.db.models import Base

BACKEND = Path(__file__).resolve().parents[1]
INI = BACKEND / "alembic.ini"
# ``python -m alembic`` does not exist; the entry point is the console script next to the
# interpreter that has the package installed.
_ALEMBIC = str(Path(sys.executable).parent / ("alembic.exe" if sys.platform == "win32" else "alembic"))


def _env(data_dir: Path) -> dict[str, str]:
    """The environment every subprocess in this file shares: one scratch directory, one key.

    Two details are the whole test. ``ADAPT_KEYFILE``/``ADAPT_DB_*`` are dropped so a scratch
    directory builds *its own* key rather than reading the test session's -- a migration run
    with one key and an inspection run with another produce "file is not a database", which is
    an operator failure mode worth testing but not one these tests are about. And
    ``ADAPT_DB_ENCRYPTION`` is pinned to the deployment's policy instead of inheriting the
    session's ``field-only`` shortcut: the conftest choice keeps the suite fast by putting
    encrypted columns in a plain file, and inheriting it here is what let a migration write a
    plaintext database without anything noticing.
    """
    env = {
        **os.environ,
        "ADAPT_DATA_DIR": str(data_dir),
        "PYTHONPATH": str(BACKEND / "src"),
        "ADAPT_DB_ENCRYPTION": "auto",
    }
    for key in ("ADAPT_KEYFILE", "ADAPT_DB_KEY", "ADAPT_DB_PATH"):
        env.pop(key, None)
    return env


def _run(args: list[str], data_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_ALEMBIC, "-c", str(INI), *args],
        cwd=str(BACKEND),
        env=_env(data_dir),
        capture_output=True,
        text=True,
        timeout=180,
    )


_READER = """
import json, sys
sys.path.insert(0, {src!r})
from sqlalchemy import inspect
from adapt.config import get_settings
from adapt.db.engine import create_db_engine

settings = get_settings()
engine, info = create_db_engine(settings)
try:
    insp = inspect(engine)
    print(json.dumps({{
        "driver": info.name,
        "tables": {{t: sorted(c["name"] for c in insp.get_columns(t)) for t in insp.get_table_names()}},
        "indexes": {{t: sorted(i["name"] for i in insp.get_indexes(t)) for t in insp.get_table_names()}},
    }}))
finally:
    engine.dispose()
"""


def _read(data_dir: Path) -> dict:
    """The schema of a scratch database, opened the way the application opens one.

    Deliberately a subprocess, like the alembic calls: it needs the *scratch* directory's
    keyfile, and doing that in-process would swap the cipher this session encrypts with.
    Reading through ``create_db_engine`` rather than a bare ``sqlite://`` URL is the part
    that matters -- an engine without SQLCipher cannot read the file the app writes, and
    could just as easily write one the app cannot read.
    """
    out = subprocess.run(
        [sys.executable, "-c", _READER.format(src=str(BACKEND / "src"))],
        env=_env(data_dir),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def _schema(data_dir: Path) -> dict[str, list[str]]:
    return _read(data_dir)["tables"]


def _expected_schema() -> dict[str, list[str]]:
    return {t.name: sorted(c.name for c in t.columns) for t in Base.metadata.tables.values()}


def test_upgrade_head_builds_the_schema_the_models_describe(tmp_path):
    assert INI.exists(), "backend/alembic.ini is missing: there are no migrations at all"
    data = tmp_path / "fresh"
    data.mkdir()
    result = _run(["upgrade", "head"], data)
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"

    built = _schema(data)
    expected = _expected_schema()
    missing = sorted(set(expected) - set(built))
    extra = sorted(set(built) - set(expected) - {"alembic_version"})
    assert not missing, f"the migration does not create: {missing}"
    assert not extra, f"the migration creates tables the models do not declare: {extra}"
    for table, columns in expected.items():
        assert built[table] == columns, f"{table}: migration columns differ from the models"
    # a learner's encrypted column is still an encrypted column after a migration
    assert "email_encrypted" in built.get("students", []) or any(
        "email" in c for c in built.get("students", [])
    ), "students lost its email column in translation"


def test_a_migrated_database_is_still_encrypted_and_still_openable(tmp_path):
    """The migration must not quietly change what protects the file.

    Alembic is normally pointed at a URL, and this database is not opened by URL alone: the
    app attaches SQLCipher and issues ``PRAGMA key`` on every connection. A migration engine
    rebuilt from the URL string loses both, and plain sqlite3 then writes -- or reads --
    *plaintext*. On a fresh directory nothing notices, because the inspection reads it back the
    same wrong way. That is not hypothetical: it is what this repo's migration path did until
    the test read the file through the application's own engine.
    """
    data = tmp_path / "cipher"
    data.mkdir()
    up = _run(["upgrade", "head"], data)
    assert up.returncode == 0, up.stderr[-1500:]
    read = _read(data)
    assert read["driver"] == "sqlcipher", (
        f"this environment reports driver {read['driver']!r}: without sqlcipher3 importable this "
        "test cannot say anything about file encryption, and should say nothing"
    )
    header = (data / "adapt.db").read_bytes()[:16]
    assert not header.startswith(b"SQLite format 3"), (
        "alembic wrote a plaintext database where the app expects SQLCipher -- the migration "
        "engine is not the application engine, and the file it just produced cannot be opened"
    )
    # readable through the app's engine is the other half: a file nothing can open would also
    # satisfy "not the plaintext header"
    assert "students" in read["tables"] and "lesson_scenes" in read["tables"], sorted(read["tables"])


def test_a_learner_scene_copy_is_indexed_per_author(tmp_path):
    """The indexes 0003 adds exist after ``upgrade head``, on a migrated database.

    ``create_all`` would build them from the models, so this is the only path that checks
    whether an existing deployment actually gets them -- without the unique pair, a second
    learner deriving an existing scene is handed somebody else's row.
    """
    data = tmp_path / "indexes"
    data.mkdir()
    assert _run(["upgrade", "head"], data).returncode == 0
    indexes = set(_read(data)["indexes"].get("lesson_scenes", []))
    assert {"uq_scene_spec_shared", "uq_scene_spec_author"} <= indexes, sorted(indexes)


def test_no_model_change_is_waiting_for_a_revision(tmp_path):
    """``alembic check`` fails when the models and the migration chain disagree.

    This is the guard against the most common migration defect of all: somebody adds a
    mapped column, ``create_all`` hands every *new* database that column, and an existing
    deployment 500s with ``no such column`` the first time it is read.
    """
    data = tmp_path / "check"
    data.mkdir()
    assert _run(["upgrade", "head"], data).returncode == 0
    checked = _run(["check"], data)
    assert checked.returncode == 0, (
        "models and migrations have drifted; generate a revision\n"
        f"{checked.stdout[-2500:]}\n{checked.stderr[-1500:]}"
    )


def test_an_existing_create_all_database_can_be_stamped_then_upgraded(tmp_path):
    """The path for a database that already has a learner's history in it.

    It was made by ``create_all``, so it has no ``alembic_version`` row: stamping tells
    Alembic "this file is at head", after which further revisions apply normally. Without
    this, the honest instruction would be "delete the database", which is not an upgrade.
    """
    data = tmp_path / "existing"
    data.mkdir()
    boot = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, %r)\n"
            "from adapt.db.engine import get_db\n"
            "get_db().create_all()\n"
            "print('created')\n" % str(BACKEND / "src"),
        ],
        # The *same* environment the alembic commands below get. Not a detail:
        # ADAPT_DB_ENCRYPTION decides whether the file is plain SQLite with encrypted
        # columns or a fully SQLCipher-encrypted database, so a boot run with a stripped
        # env writes a file the migration run cannot read ("file is not a database"),
        # which is a real operator failure mode and not something to test around.
        env=_env(data),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert boot.returncode == 0, boot.stderr[-1500:]
    stamped = _run(["stamp", "head"], data)
    assert stamped.returncode == 0, stamped.stderr[-1500:]
    again = _run(["upgrade", "head"], data)
    assert again.returncode == 0, again.stderr[-1500:]
    built = _schema(data)
    assert set(built) >= set(_expected_schema()), "stamping lost tables"


def test_downgrade_to_base_leaves_an_empty_database(tmp_path):
    """``downgrade base`` must actually undo, or "reversible migrations" is a claim to skip.

    The initial revision is the whole schema, so dropping to base should take every
    application table away and leave only Alembic's own version marker.
    """
    data = tmp_path / "undo"
    data.mkdir()
    assert _run(["upgrade", "head"], data).returncode == 0
    down = _run(["downgrade", "base"], data)
    assert down.returncode == 0, f"{down.stdout[-1500:]}\n{down.stderr[-1500:]}"
    left = set(_schema(data))
    assert left == {"alembic_version"}, f"downgrade left tables behind: {sorted(left)[:6]}"


def test_migrations_are_documented_where_an_operator_would_look():
    """The commands are useless if the only place they exist is this test file."""
    root = BACKEND.parent
    readme = (root / "README.md").read_text() if (root / "README.md").exists() else ""
    docs = "\n".join(p.read_text() for p in (root / "docs").glob("*.md")) if (root / "docs").is_dir() else ""
    assert "alembic" in (readme + docs).lower(), "no docs tell an operator how to migrate an existing database"
