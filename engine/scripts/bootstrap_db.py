"""Create the two database roles, and point the database at the owner.

This runs *before* Alembic, as an administrator: a migration cannot create the
role that owns its own tables. It is idempotent and re-asserts the role
attributes, because a role created by hand with ``BYPASSRLS`` would silently
defeat every policy in the schema.

Environment:
    ADAPT_BOOTSTRAP_DATABASE_URL  admin connection (required)
    ADAPT_DATABASE_NAME           database to own with adapt_owner
                                  (default: the database in the admin URL)
    ADAPT_OWNER_PASSWORD          optional; omit for socket/trust auth
    ADAPT_APP_PASSWORD            optional; omit for socket/trust auth
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapt_engine.db.roles import APP_ROLE, OWNER_ROLE, ensure_roles


def main() -> int:
    admin_url = os.environ.get("ADAPT_BOOTSTRAP_DATABASE_URL", "").strip()
    if not admin_url:
        print(
            "error: ADAPT_BOOTSTRAP_DATABASE_URL is required (an administrative connection)",
            file=sys.stderr,
        )
        return 1

    parsed = make_url(admin_url)
    database_name = os.environ.get("ADAPT_DATABASE_NAME", "").strip() or (parsed.database or "")

    engine = create_engine(admin_url, future=True)
    try:
        with engine.begin() as conn:
            ensure_roles(
                conn,
                owner_password=os.environ.get("ADAPT_OWNER_PASSWORD") or None,
                app_password=os.environ.get("ADAPT_APP_PASSWORD") or None,
            )
            if database_name:
                # Owning the database is what lets adapt_owner create tables
                # (and the Alembic version table) without being superuser.
                current_owner = conn.execute(
                    text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :n"),
                    {"n": database_name},
                ).scalar_one_or_none()
                if current_owner is not None and current_owner != OWNER_ROLE:
                    conn.execute(text(f'ALTER DATABASE "{database_name}" OWNER TO {OWNER_ROLE}'))
                    print(f"database {database_name}: owner {current_owner} -> {OWNER_ROLE}")
                elif current_owner is None:
                    print(f"warning: database {database_name} does not exist", file=sys.stderr)
        print(f"roles ready: {OWNER_ROLE} (owner, migrations), {APP_ROLE} (runtime, DML only)")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
