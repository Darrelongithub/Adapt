"""PostgreSQL test-fixture provider.

The suite does not hard-code a server. It reads ``ADAPT_TEST_DATABASE_URL``,
which must point at an *administrative* connection (a role that may CREATE
DATABASE and CREATE ROLE) on any PostgreSQL instance. Two providers are known
to work and neither is special-cased below:

* ``docker compose up postgres`` — ``postgresql://postgres:adapt@localhost:5432/postgres``
* ``python scripts/dev_postgres.py`` — a dev-only bundled server (the ``dev``
  extra's ``pgserver``), which prints the URL to export.

When the variable is unset the dev bundled server is started as a fallback so
``make test`` works on a laptop with no Docker. That fallback is a convenience,
not the contract: set the variable and the same suite runs against anything.

Each session gets a fresh database, provisioned the way production is:
bootstrap creates the two roles, then ``alembic upgrade head`` runs as the
owner. Nothing here creates tables directly.

Point this at a throwaway cluster. Roles are cluster-wide, so provisioning
re-asserts their attributes (removing SUPERUSER/BYPASSRLS) and sets the dev
passwords below unless ADAPT_TEST_OWNER_PASSWORD / ADAPT_TEST_APP_PASSWORD are
supplied. It also drops and recreates the test database by name.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url

from adapt_engine.db.roles import ensure_roles
from adapt_engine.db.tenant import tenant_transaction

ENGINE_ROOT = Path(__file__).resolve().parents[1]

TEST_DATABASE = "adapt_test"
MIGRATIONS_DATABASE = "adapt_migrations_test"

#: Set by the fallback provider so it is stopped at session end.
_STARTED_SERVER: object | None = None

#: Roles get these passwords when the provider authenticates over TCP with a
#: password. With socket/trust auth (the bundled server) they are unused.
DEV_OWNER_PASSWORD = "adapt-owner-dev-password"
DEV_APP_PASSWORD = "adapt-app-dev-password"


@dataclass(frozen=True)
class Database:
    """A provisioned database and the two URLs into it."""

    name: str
    admin_url: str
    owner_url: str
    app_url: str

    def engine(self, url: str, **kwargs: object) -> Engine:
        return create_engine(url, future=True, **kwargs)  # type: ignore[arg-type]


def _start_bundled_server() -> str:
    """Start the dev-only bundled PostgreSQL and return its admin URL."""
    global _STARTED_SERVER
    try:
        import pgserver
    except ImportError as exc:  # pragma: no cover - dev extra missing
        raise RuntimeError(
            "ADAPT_TEST_DATABASE_URL is not set and the dev-only pgserver extra is "
            "not installed. Set ADAPT_TEST_DATABASE_URL to an administrative "
            "PostgreSQL URL, or `pip install -e '.[dev]'`."
        ) from exc
    data_dir = os.environ.get("ADAPT_TEST_PG_DIR", "/tmp/adapt_pg_bundled")
    _STARTED_SERVER = pgserver.get_server(data_dir)
    return str(_STARTED_SERVER.get_uri())  # type: ignore[attr-defined]


def admin_url() -> str:
    """The administrative connection URL for this session."""
    configured = os.environ.get("ADAPT_TEST_DATABASE_URL", "").strip()
    if configured:
        return configured
    return _start_bundled_server()


#: Hex suffixes can contain a run of 7+ digits, which learner_id forbids
#: (it looks like a national id). Translate digits to letters so synthetic ids
#: are always valid, and obviously synthetic.
_DIGIT_TO_LETTER = str.maketrans("0123456789", "ghijklmnop")


def synthetic_suffix(length: int = 8) -> str:
    """A letters-only suffix: never an accidental digit run."""
    return uuid.uuid4().hex[:length].translate(_DIGIT_TO_LETTER)


def render(url: URL) -> str:
    """Render a URL with its password, which ``str(URL)`` deliberately hides."""
    return url.render_as_string(hide_password=False)


def _recreate_database(admin: Engine, name: str, owner: str) -> None:
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        # Owned by adapt_owner so the owner can create the schema objects and
        # the app role cannot.
        conn.execute(text(f'CREATE DATABASE "{name}" OWNER "{owner}"'))


def provision(database_name: str = TEST_DATABASE, *, migrate: bool = True) -> Database:
    """Create a fresh database, roles, and optionally migrate it."""
    base = admin_url()
    admin_engine = create_engine(render(make_url(base).set(database="postgres")), future=True)
    try:
        with admin_engine.begin() as conn:
            ensure_roles(
                conn,
                owner_password=os.environ.get("ADAPT_TEST_OWNER_PASSWORD", DEV_OWNER_PASSWORD),
                app_password=os.environ.get("ADAPT_TEST_APP_PASSWORD", DEV_APP_PASSWORD),
            )
        from adapt_engine.db.roles import OWNER_ROLE

        _recreate_database(admin_engine, database_name, OWNER_ROLE)
    finally:
        admin_engine.dispose()

    parsed = make_url(base)
    password_for = {
        "adapt_owner": os.environ.get("ADAPT_TEST_OWNER_PASSWORD", DEV_OWNER_PASSWORD),
        "adapt_app": os.environ.get("ADAPT_TEST_APP_PASSWORD", DEV_APP_PASSWORD),
    }
    # Socket-trust providers reject a password on a URL that has none; TCP
    # password providers need one. Keep whatever the provider used.
    owner_url = render(
        parsed.set(
            database=database_name, username="adapt_owner", password=password_for["adapt_owner"]
        )
    )
    app_url = render(
        parsed.set(database=database_name, username="adapt_app", password=password_for["adapt_app"])
    )
    database = Database(name=database_name, admin_url=base, owner_url=owner_url, app_url=app_url)
    if migrate:
        upgrade(database)
    return database


def alembic_config(database: Database) -> Config:
    config = Config(str(ENGINE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ENGINE_ROOT / "migrations"))
    os.environ["ADAPT_MIGRATION_DATABASE_URL"] = database.owner_url
    return config


def upgrade(database: Database) -> None:
    command.upgrade(alembic_config(database), "head")


def downgrade(database: Database, revision: str = "base") -> None:
    command.downgrade(alembic_config(database), revision)


def drop_database(database: Database) -> None:
    admin_engine = create_engine(
        render(make_url(database.admin_url).set(database="postgres")), future=True
    )
    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database.name}" WITH (FORCE)'))
    finally:
        admin_engine.dispose()


def stop_bundled_server() -> None:
    global _STARTED_SERVER
    if _STARTED_SERVER is not None:
        cleanup = getattr(_STARTED_SERVER, "cleanup", None)
        if callable(cleanup):
            cleanup()
        _STARTED_SERVER = None


# --------------------------------------------------------------------------
# Seeding helpers used by the tenancy tests. Everything is synthetic.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeededTenants:
    """Two tenants, one learner each, and the ids the tests attack with."""

    a_id: uuid.UUID
    b_id: uuid.UUID
    a_learner: uuid.UUID
    b_learner: uuid.UUID
    a_learner_id: str
    b_learner_id: str


def engine_for(database: Database, role: str = "app", **kwargs: object) -> Engine:
    """An engine connected as ``adapt_app`` (default) or ``adapt_owner``."""
    url = database.app_url if role == "app" else database.owner_url
    return create_engine(url, future=True, **kwargs)  # type: ignore[arg-type]


def create_tenant(
    engine: Engine,
    *,
    slug: str,
    name: str,
    profile: str = "adult_general",
    status: str = "active",
) -> uuid.UUID:
    """Insert a tenant, setting tenant context to the new row's own id.

    FORCE RLS applies to the owner too, so even seeding has to declare which
    tenant it is writing. The id is generated here rather than by the server
    because the policy needs to see it before the insert.
    """
    tenant_id = uuid.uuid4()
    with tenant_transaction(engine, tenant_id) as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id, slug, name, profile, status) "
                "VALUES (:id, :slug, :name, :profile, :status)"
            ),
            {"id": tenant_id, "slug": slug, "name": name, "profile": profile, "status": status},
        )
    return tenant_id


def create_learner(
    engine: Engine,
    tenant_id: uuid.UUID,
    learner_id: str,
    *,
    profile: str = "adult_general",
) -> uuid.UUID:
    """Insert a learner for ``tenant_id`` inside that tenant's context."""
    learner_pk = uuid.uuid4()
    with tenant_transaction(engine, tenant_id) as conn:
        conn.execute(
            text(
                "INSERT INTO learners (id, tenant_id, learner_id, profile) "
                "VALUES (:id, :tenant_id, :learner_id, :profile)"
            ),
            {
                "id": learner_pk,
                "tenant_id": tenant_id,
                "learner_id": learner_id,
                "profile": profile,
            },
        )
    return learner_pk


def provision_tenant(
    database: Database, *, slug: str, name: str, profile: str = "adult_general"
) -> uuid.UUID:
    """Create a tenant as adapt_owner: provisioning, not a runtime action.

    ``adapt_app`` holds SELECT only on ``tenants`` (migration 0002), so a test
    that created tenants as the app role would be testing a privilege the
    runtime does not have.
    """
    engine = engine_for(database, "owner")
    try:
        return create_tenant(engine, slug=slug, name=name, profile=profile)
    finally:
        engine.dispose()


def seed_two_tenants(database: Database, *, role: str = "app") -> SeededTenants:
    """Two tenants, one learner each. Synthetic identifiers only.

    Tenants are provisioned as the owner; learners are created with the role
    serving requests, which is what the runtime actually does.
    """
    suffix = synthetic_suffix()
    a_id = provision_tenant(database, slug=f"tenant-a-{suffix}", name="Synthetic Tenant A")
    b_id = provision_tenant(database, slug=f"tenant-b-{suffix}", name="Synthetic Tenant B")
    engine = engine_for(database, role)
    try:
        a_learner = create_learner(engine, a_id, f"learner-a-{suffix}")
        b_learner = create_learner(engine, b_id, f"learner-b-{suffix}")
        return SeededTenants(
            a_id=a_id,
            b_id=b_id,
            a_learner=a_learner,
            b_learner=b_learner,
            a_learner_id=f"learner-a-{suffix}",
            b_learner_id=f"learner-b-{suffix}",
        )
    finally:
        engine.dispose()


TEST_PEPPER = "test-pepper-synthetic-only"


def bootstrap_key(
    database: Database,
    tenant_id: uuid.UUID,
    *,
    scopes: tuple[str, ...] = ("observe", "admin"),
    env: str = "test",
    expires_in_days: int | None = None,
) -> object:
    """Create the tenant's first key as the owner: provisioning, not the API.

    Every later key in the tests is minted by this one through the real
    ``adapt_issue_api_key`` path, with its scope-subset rules in force. A test
    that minted keys as the app role would be testing a privilege the runtime
    does not have.
    """
    from adapt_engine.auth import keys as key_service

    engine = engine_for(database, "owner")
    try:
        return key_service.issue_api_key(
            engine,
            tenant_id=tenant_id,
            scopes=list(scopes),
            env=env,
            pepper=TEST_PEPPER,
            expires_in_days=expires_in_days,
        )
    finally:
        engine.dispose()


def grant_credits(
    database: Database,
    tenant_id: uuid.UUID,
    *,
    kind: str,
    amount: int,
    expires_at: object | None = None,
    source_ref: str | None = None,
    role: str = "app",
) -> uuid.UUID:
    """Create a credit grant for a tenant, inside that tenant's context."""
    from adapt_engine.credits import service as credits

    engine = engine_for(database, role)
    try:
        with tenant_transaction(engine, tenant_id) as conn:
            return credits.grant_credits(
                conn,
                tenant_id=tenant_id,
                kind=kind,  # type: ignore[arg-type]
                amount=amount,
                expires_at=expires_at,  # type: ignore[arg-type]
                source_ref=source_ref,
            )
    finally:
        engine.dispose()


def test_database_engine(database: Database, **kwargs: object) -> Engine:
    """An app-role engine with an explicit pool, for concurrency tests."""
    return engine_for(database, "app", **kwargs)
