"""Shared fixtures.

Two things about this file are deliberate.

The environment is configured *at import time*, before ``adapt`` is imported
anywhere: settings are cached in a process-wide lru_cache and the field cipher is a
module global, so a fixture that set ``os.environ`` later would be ignored and the
tests would quietly run against a developer's real data directory.

Each test gets its **own learner**. Learning state is per-student, so a fresh
student is a cheaper and more honest isolation than rolling back a shared database:
nothing leaks between tests, and the curriculum (topics, items, materials) is
read-only enough to share.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

_DATA_DIR = os.environ.setdefault("ADAPT_DATA_DIR", tempfile.mkdtemp(prefix="adapt-tests-"))
os.environ.setdefault("ADAPT_KEYFILE", os.path.join(_DATA_DIR, "master.key"))
# Tests run in field-only mode so they work on any machine (no libsqlcipher needed).
# The SQLCipher driver itself is exercised in test_db_encryption.py, which skips
# when the package is absent.
os.environ.setdefault("ADAPT_DB_ENCRYPTION", "field-only")
os.environ.setdefault("ADAPT_MODE", "local")
os.environ.setdefault("ADAPT_LOG_LEVEL", "ERROR")
os.environ.setdefault("ADAPT_AUDIT_LOG_ENABLED", "true")
os.environ.setdefault("ADAPT_RATE_LIMIT_MULTIPLIER", "60")
for _secret in ("ADAPT_GEMINI_API_KEY", "ADAPT_YOUTUBE_API_KEY"):
    os.environ.pop(_secret, None)

COLLECTION_ROOTS = ("topics", "items", "prerequisites")


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    if os.environ.get("ADAPT_KEEP_TEST_DIR"):
        return
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def data_dir() -> str:
    return _DATA_DIR


@pytest.fixture(scope="session")
def settings():
    from adapt.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def seeded(settings):
    """Create tables and import the curriculum once, before the app starts."""
    from adapt.db.engine import Database
    from adapt.db.init_db import init

    # Constructing the Database is what installs the process-wide field cipher
    # (engine.build_keyring -> fields.configure_cipher), so nothing here has to.
    db = Database(settings)
    report = init(settings, db=db)
    db.dispose()
    assert report.errors == [], f"init reported errors: {report.errors}"
    return report


@pytest.fixture(scope="session")
def client(seeded):
    """TestClient with the real app (middleware, lifespan, services included)."""
    from fastapi.testclient import TestClient

    from adapt.api.app import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture()
def services(client):
    """The AppServices the running app attached (same objects, one database)."""
    from adapt.services import current

    return current()


@pytest.fixture()
def learner(client):
    """A fresh guest learner plus the CSRF header its session needs."""
    response = client.post(
        "/api/v1/students",
        json={"display_name": "Rowan Test", "age_band": "16-18", "as_guest": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "student_id": body["student_id"],
        "csrf": body["csrf_token"],
        "headers": {"x-csrf-token": body["csrf_token"]},
    }


@pytest.fixture()
def registered_learner(client):
    """A learner with an email + password: the rows used for encryption-at-rest checks.

    The address is per-test because ``POST /students`` refuses to re-register an address
    that already has an account (it must not double as a login path), and the client is
    session-scoped, so a fixed address would only work for one test.
    """
    import uuid

    email = f"rowan-{uuid.uuid4().hex[:8]}@example-invalid.test"
    response = client.post(
        "/api/v1/students",
        json={"display_name": "Rowan Test", "email": email, "password": "correct horse battery"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"student_id": body["student_id"], "email": email, "csrf": body["csrf_token"], "password": "correct horse battery"}


@pytest.fixture()
def session(client, learner):
    """An open learning session for the fixture learner."""
    response = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"])
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


@pytest.fixture()
def make_answer(services, learner):
    """``make_answer(item_id, correct=True)`` -> a submittable answer for that item.

    Rendered under the acting learner's variant seed, because that is the variant the
    grader re-derives from ``(student, item)``: a parametric item has no single correct
    answer, so reading the stored defaults would hand a test an answer to a *different*
    question the moment the plan sampled new numbers.
    """
    sid = learner["student_id"]
    return lambda item_id, *, correct=True: answer_for(services, item_id, correct=correct, student_id=sid)


@pytest.fixture()
def items(client, learner):
    """The diagnostic plan: real items from the seeded bank, in plan order."""
    response = client.get("/api/v1/assessment/diagnostic")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _writable_number(value: float, spec: dict) -> str:
    """A string a learner could type that this item's own tolerance accepts.

    ``f"{value:g}"`` keeps six significant digits, so for an item whose answer is
    34/3 the suite's "correct" answer arrived as 11.3333 and was marked wrong at a
    tolerance of 1e-09 — a helper that cannot answer its own bank correctly makes
    every test that uses it measure the wrong path. Short decimal first (that is
    what most items want), then the exact fraction (what a fraction-of-quantity
    item is really asking for), then full precision.
    """
    from fractions import Fraction

    tol = float(spec.get("tolerance") if spec.get("tolerance") is not None else 1e-6)
    short = f"{value:g}"
    if abs(float(short) - value) <= tol:
        return short
    exact = Fraction(value).limit_denominator(10_000)
    if exact.denominator > 1 and abs(float(exact) - value) <= tol:
        return f"{exact.numerator}/{exact.denominator}"
    return repr(value)


def _wrong_step(spec: dict) -> float:
    """A distance that is certainly outside this item's own mark scheme.

    The wrong answer used to be ``value * 1.5 + 1``, which returns ``value`` itself
    when ``value == -2`` -- and ``a - b - c`` renders to -2 for some learners. The app
    then marked the "wrong" answer right, which is not a test that flaked: it is a test
    comparing a feedback sentence with itself and calling the difference a failure.
    """
    tol = float(spec.get("tolerance") if spec.get("tolerance") is not None else 1e-6)
    return max(1.0, tol * 10)


def answer_for(services, item_id: str, *, correct: bool, student_id: str | None = None):
    """Build a right or wrong answer for a bank item, from its private answer spec.

    The public item payload deliberately does not contain the answer, so a test that
    wanted to answer correctly would have to guess. This reads the same private
    payload the grader reads -- a white-box test, but a *learner-shaped* one: the
    value it submits is a value a learner could type.
    """
    from adapt.ai.local_tutor import eval_formula
    from adapt.services import attempts as A

    with services.scope() as s:
        payload = A.item_payload(s, item_id)
    if payload is None:  # pragma: no cover - guards a broken bank
        raise AssertionError(f"item {item_id} is not in the bank")
    if student_id is not None:
        from adapt.assessment.bank import render_item_for, variant_seed

        payload = render_item_for(payload, seed=variant_seed(student_id, item_id))
    kind = payload["kind"]
    spec = payload.get("answer_spec") or {}

    def expected() -> float | None:
        if spec.get("value") is not None:
            return float(spec["value"])
        if spec.get("formula"):
            try:
                return float(eval_formula(str(spec["formula"]), dict(spec.get("variables") or {})))
            except Exception:  # pragma: no cover - a broken item, not a broken test
                return None
        return None

    if kind == "mcq":
        right = spec.get("correct_index")
        if right is None:
            choices = payload.get("choices") or []
            right = next((i for i, c in enumerate(choices) if c.get("correct")), 0)
        if correct:
            return int(right)
        n = max(2, len(payload.get("choices") or []))
        return (int(right) + 1) % n
    if kind == "numeric":
        value = expected()
        if value is None:
            return "42" if correct else "43"
        if correct:
            return _writable_number(value, spec)
        return f"{value + _wrong_step(spec):g}"
    if kind == "text":
        accepted = [str(v) for v in (spec.get("values") or [])] or ([str(spec["value"])] if "value" in spec else [])
        value = expected()
        if correct:
            if accepted:
                return accepted[0]
            # A text item marked by formula has a number for a key: answer with the
            # number, not with one hardcoded sentence about fractions.
            return _writable_number(value, spec) if value is not None else "the parts must be the same size"
        return "6/5" if value is None else f"{value + _wrong_step(spec):g}"
    if kind == "teachback":
        from adapt.assessment import teachback as TB

        rubric = payload.get("rubric") or {}
        criteria = TB.checklist_from_rubric(rubric)
        if not correct:
            return "not sure"
        if criteria:
            # One *sentence* per point, because that is what the checklist grades: a
            # point is met by a span that carries the idea, so three ideas in a row is
            # a list of words and scores nothing. Reading the rubric through the
            # grader's own function is what keeps this working for a history or a
            # biology concept, whose rubric is `required_concepts` and not `ideas`.
            return " ".join(
                f"The point about {c.idea or c.statement} is that it matters, because "
                f"{c.idea or c.statement} is what makes this work."
                for c in criteria
            )
        ideas = rubric.get("ideas") or rubric.get("required") or []
        return " ".join(str(i) for i in ideas[:3]) or "you have to make the parts the same size first, then count them"
    return "42" if correct else "41"
