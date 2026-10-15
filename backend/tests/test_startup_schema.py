"""Startup repairs old schemas before ORM reads, without changing encrypted rows."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import sys
from pathlib import Path
from sqlalchemy import text, inspect, select
from adapt.config import get_settings
from adapt.db.engine import Database
from adapt.db.models import Student, Item
from adapt.services.sessions import ensure_curriculum

shape = sys.argv[1]
st = get_settings()
db = Database(st)
if shape != "fresh":
    db.create_all()
    with db.session() as s:
        student = Student(display_name="Keep this learner")
        s.add(student)
        ensure_curriculum(s)
    with db.engine.begin() as c:
        before = c.execute(text("SELECT id, display_name FROM students")).all()
        items_before = c.execute(text("SELECT id FROM items ORDER BY id")).scalars().all()
        c.exec_driver_sql("ALTER TABLE items DROP COLUMN verification")
        if shape == "versioned":
            c.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
            c.exec_driver_sql("INSERT INTO alembic_version VALUES ('0004')")
        elif shape == "invalid":
            # An unsupported old schema must fail and roll back, not falsely mark head.
            c.exec_driver_sql("ALTER TABLE items DROP COLUMN stem")
    if shape == "invalid":
        try:
            db.upgrade_schema()
        except RuntimeError as exc:
            assert "items.stem" in str(exc)
        else:
            raise AssertionError("unsupported schema was accepted")
        assert "verification" not in {c['name'] for c in inspect(db.engine).get_columns('items')}
        assert "alembic_version" not in inspect(db.engine).get_table_names()
        sys.exit(0)

# Exercise the real lifespan, not only the migration helper. Repeated startup must be safe.
from fastapi.testclient import TestClient
from adapt.api.app import app
for _ in range(2):
    with TestClient(app) as client:
        assert client.get('/healthz').status_code == 200

with db.engine.connect() as c:
    assert c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if shape != "fresh":
        assert c.execute(text("SELECT id, display_name FROM students")).all() == before
        assert c.execute(text("SELECT id FROM items ORDER BY id")).scalars().all() == items_before
with db.session() as s:
    assert s.scalars(select(Item)).first().verification
    if shape != "fresh":
        assert s.scalars(select(Student)).one().display_name == "Keep this learner"
if st.db_encryption == 'sqlcipher':
    assert not st.db_file.read_bytes().startswith(b'SQLite format 3')
'''


@pytest.mark.parametrize("encryption", ["field-only", "sqlcipher"])
@pytest.mark.parametrize("shape", ["fresh", "unversioned", "versioned", "invalid"])
def test_startup_migrates_before_seeding(tmp_path, encryption, shape):
    if encryption == "sqlcipher":
        pytest.importorskip("sqlcipher3")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ADAPT_")}
    env.update(
        ADAPT_DATA_DIR=str(tmp_path), ADAPT_DB_ENCRYPTION=encryption, ADAPT_LOG_LEVEL="ERROR"
    )
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, shape], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_upgrade_uses_the_database_instance_not_environment_settings(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ADAPT_")}
    env.update(ADAPT_DATA_DIR=str(tmp_path), ADAPT_DB_ENCRYPTION="field-only")
    script = '''
from adapt.config import get_settings
from adapt.db.engine import Database
from sqlalchemy import inspect
st = get_settings()
custom = st.model_copy(update={"data_dir": st.data_dir / "custom"})
path = custom.data_dir / "explicit.db"
db = Database(custom, db_file=path)
db.upgrade_schema()
db.upgrade_schema()
assert "items" in inspect(db.engine).get_table_names()
assert path.exists()
assert not st.db_file.exists()
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
