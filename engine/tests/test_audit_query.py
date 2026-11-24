"""The owner-only audit CLI: it answers, it is read-only, and it needs the owner.

A security review should not require `psql` and a memorised query. These tests
drive the script as an operator would — against a database seeded through the
real paths — and check the two properties that make it safe to hand out: it
runs in a READ ONLY transaction, and it refuses to run without the owner URL.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.audit import AuthFailureAuditor
from adapt_engine.clock import SystemClock
from tests.pg import bootstrap_key, provision_tenant

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_query.py"


def _load_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("audit_query", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"aq-{uuid.uuid4().hex[:8]}", name="Audit Query")


def test_auth_failures_lists_by_source_hash(database, app_engine: Engine) -> None:
    auditor = AuthFailureAuditor(app_engine, SystemClock())
    source = "f" * 32
    for _ in range(3):
        auditor.record(source)

    module = _load_module()
    engine = module._engine.__globals__["create_engine"](database.owner_url, future=True)
    try:
        with engine.connect() as conn:
            module._read_only(conn)
            rows = module.auth_failures(conn, minutes=60)
    finally:
        engine.dispose()

    mine = [row for row in rows if row.ip_hash == source]
    assert len(mine) == 3
    assert all(row.aggregated is False for row in mine)
    assert all("@" not in str(row.ip_hash) for row in mine), "an address leaked into the report"


def test_key_events_lists_issue_and_revoke_with_key_ids(
    database, app_engine: Engine, tenant: uuid.UUID
) -> None:
    from sqlalchemy import text as _text

    from adapt_engine.auth import keys as key_service
    from adapt_engine.db.tenant import tenant_transaction as _tx

    admin = bootstrap_key(database, tenant, scopes=("observe", "admin"))
    with _tx(app_engine, tenant) as conn:
        issued = key_service.insert_api_key(
            conn,
            tenant_id=tenant,
            scopes=["observe"],
            env="test",
            pepper="test-pepper-synthetic-only",
            issuer_key_id=admin.key_id,
        )
        key_service.revoke_api_key(
            conn, tenant_id=tenant, key_id=issued.key_id, actor_key_id=admin.key_id
        )

    module = _load_module()
    engine = module._engine.__globals__["create_engine"](database.owner_url, future=True)
    try:
        with engine.connect() as conn:
            module._read_only(conn)
            # The owner reads a tenant's rows only after declaring that tenant;
            # without the declaration the policy matches nothing.
            assert module.key_events(conn, tenant_id=str(tenant)) == []
            conn.execute(text("SELECT set_config('adapt.tenant_id', :t, true)"), {"t": str(tenant)})
            module._read_only(conn)
            rows = module.key_events(conn, tenant_id=str(tenant))
            other = module.key_events(conn, tenant_id=str(uuid.uuid4()))
    finally:
        engine.dispose()
    assert other == []

    actions = {row.action for row in rows}
    assert {"api_key_issued", "api_key_revoked"} <= actions
    targets = {row.target for row in rows}
    assert str(issued.key_id) in targets
    # Never a key or a prefix: the target column holds key ids.
    assert all(not str(target).startswith("ak_") for target in targets)
    assert all(issued.full_key != str(row.target) for row in rows)
    del _text


def test_system_count_counts_tenant_less_rows(database, app_engine: Engine) -> None:
    AuthFailureAuditor(app_engine, SystemClock()).record("e" * 32)

    module = _load_module()
    engine = module._engine.__globals__["create_engine"](database.owner_url, future=True)
    try:
        with engine.connect() as conn:
            module._read_only(conn)
            assert module.system_count(conn) >= 1
    finally:
        engine.dispose()


def test_the_connection_is_read_only(database) -> None:
    """A bug in the script cannot write: the transaction refuses."""
    module = _load_module()
    engine = module._engine.__globals__["create_engine"](database.owner_url, future=True)
    try:
        with engine.connect() as conn:
            module._read_only(conn)
            with pytest.raises(Exception) as refused:
                conn.execute(text("INSERT INTO tenants (slug, name) VALUES ('x', 'x')"))
            assert "read-only" in str(refused.value).lower()
    finally:
        engine.dispose()


def test_the_script_refuses_to_run_without_an_owner_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADAPT_MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.delenv("ADAPT_DATABASE_URL", raising=False)
    module = _load_module()
    with pytest.raises(SystemExit) as exited:
        module.main(["--system-count"])
    assert exited.value.code == 2


def test_the_cli_prints_a_report(database, app_engine: Engine, capsys) -> None:
    AuthFailureAuditor(app_engine, SystemClock()).record("d" * 32)
    import os

    os.environ["ADAPT_MIGRATION_DATABASE_URL"] = database.owner_url
    try:
        module = _load_module()
        assert module.main(["--auth-failures", "--minutes", "60"]) == 0
    finally:
        del os.environ["ADAPT_MIGRATION_DATABASE_URL"]

    out = capsys.readouterr().out
    assert "ip_hash" in out
    assert "d" * 32 in out
    assert "ak_test_" not in out
