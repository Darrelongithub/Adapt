"""``adapt.db.session_scope`` — an exported helper has to actually open a session.

Why this file exists: ``session_scope`` is in ``adapt.db.__all__``, so it is public
surface, and it was broken from the moment it was written -- ``yield from`` a
``_GeneratorContextManager`` raises ``TypeError`` on the first call. Nothing inside
the package used it (every service holds its own session factory), so a suite of
600+ tests passed with a public function that could not run. This is the guard that
makes that impossible to repeat quietly.

It reads only. The database is shared across the whole session, so a test that
inserted a row here could move a count another test asserts on; proving the commit
path is not worth that trade, and the rollback path is covered by the raising test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_session_scope_yields_a_usable_session_on_the_shared_engine(client):
    """The call that used to raise ``TypeError`` now returns a real session."""
    from adapt.db import session_scope
    from adapt.db.models import Topic

    with session_scope() as s:
        assert isinstance(s, Session)
        codes = list(s.execute(select(Topic.code)).scalars().all())

    # The curriculum the app booted with, read through the helper rather than
    # around it: an empty list would mean a session on the wrong engine.
    assert len(codes) >= 20
    assert "history_ww1" in codes
    assert "bio_photosynthesis" in codes


def test_a_failure_inside_the_scope_reaches_the_caller(client):
    """Rollback must not swallow the error: the caller sees what went wrong."""
    from adapt.db import session_scope
    from adapt.db.models import Topic

    with pytest.raises(RuntimeError, match="deliberate"), session_scope() as s:
        s.execute(select(Topic.code)).scalars().all()
        raise RuntimeError("deliberate failure inside the scope")


def test_the_helper_is_part_of_the_package_api():
    """It is exported, which is why a broken body was worth fixing rather than deleting."""
    import adapt.db as db

    assert "session_scope" in db.__all__
    assert callable(db.session_scope)
