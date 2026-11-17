"""The two database roles, and why the split is not optional.

``adapt_owner`` owns the tables and runs migrations. ``adapt_app`` is the
runtime role: DML only, never an owner, never superuser, no ``BYPASSRLS``.

If the app role owned a table, PostgreSQL would exempt it from its own
row-level security and every isolation test would pass while the hole stayed
open. The role attributes below are therefore asserted in tests, not just set
here, and the tests connect with the app role rather than as an administrator.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

OWNER_ROLE = "adapt_owner"
APP_ROLE = "adapt_app"

_ROLE_ATTRIBUTES = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"


def _role_exists(conn: Connection, role: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
        ).scalar_one_or_none()
    )


def _quote_literal(value: str) -> str:
    """Quote a string for a utility statement, which cannot take parameters."""
    return "'" + value.replace("'", "''") + "'"


def ensure_roles(
    conn: Connection,
    *,
    owner_password: str | None = None,
    app_password: str | None = None,
) -> None:
    """Create or re-assert both roles. Idempotent, and safe to re-run.

    Runs as an administrator, before migrations: a migration cannot run before
    the role that owns the tables exists, so role creation is bootstrap, not a
    migration step. Passwords are optional — local socket auth does not need
    them, and production passes them from the environment, never from code.
    """
    for role, password in ((OWNER_ROLE, owner_password), (APP_ROLE, app_password)):
        # Roles are identifiers, so they are not parameterisable either; both
        # names are module constants, never request data.
        if not _role_exists(conn, role):
            conn.execute(text(f"CREATE ROLE {role} {_ROLE_ATTRIBUTES}"))
        else:
            # Re-assert: a role created by hand could carry BYPASSRLS or
            # SUPERUSER, which would silently defeat every policy below.
            conn.execute(text(f"ALTER ROLE {role} {_ROLE_ATTRIBUTES}"))
        if password is not None:
            conn.execute(text(f"ALTER ROLE {role} PASSWORD {_quote_literal(password)}"))

    # The app role may use the schema but must not create objects in it.
    conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
    conn.execute(text(f"REVOKE CREATE ON SCHEMA public FROM {APP_ROLE}"))
    conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {OWNER_ROLE}, {APP_ROLE}"))
    conn.execute(text(f"GRANT CREATE ON SCHEMA public TO {OWNER_ROLE}"))

    # Transaction-mode poolers would otherwise reuse one plan across tenants.
    conn.execute(text(f"ALTER ROLE {APP_ROLE} SET plan_cache_mode = force_custom_plan"))
    # A non-owner cannot switch RLS off; make that explicit rather than
    # incidental, so the setting is visible in pg_roles.
    conn.execute(text(f"ALTER ROLE {APP_ROLE} SET row_security = on"))
