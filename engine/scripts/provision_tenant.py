#!/usr/bin/env python3
"""Provision a tenant: the tenant row, its data key, and its first API key.

Provisioning is an owner operation, and this script is the owner's tool. It is
deliberately not an HTTP route: a route that creates tenants would be a route
that creates *billing counterparties*, and that is an onboarding decision, not
an API capability.

    export ADAPT_MIGRATION_DATABASE_URL=postgresql://adapt_owner:...@host/adapt
    export ADAPT_MASTER_KEY=...            # same key the service runs with
    export ADAPT_KEY_PEPPER=...
    python scripts/provision_tenant.py --slug acme --name "Acme College"

Prints the API key **once**. It is stored only as an HMAC digest, so a lost key
is re-issued, never recovered.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from adapt_engine.auth.keys import issue_api_key
from adapt_engine.config import Settings
from adapt_engine.crypto.keyring import Keyring
from adapt_engine.crypto.provisioning import provision_data_key
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.errors import ConfigError


def _owner_engine() -> Engine:
    url = os.environ.get("ADAPT_MIGRATION_DATABASE_URL")
    if not url:
        print("error: ADAPT_MIGRATION_DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(2)
    return create_engine(url, future=True)


def _settings() -> Settings:
    try:
        return Settings.from_env()
    except ConfigError as exc:
        print(f"error: {exc.variable}: {exc.detail}", file=sys.stderr)
        raise SystemExit(1) from exc


def provision(engine: Engine, settings: Settings, *, slug: str, name: str) -> tuple[uuid.UUID, str]:
    """Create tenant + data key + first key. Returns (tenant_id, full_api_key)."""
    keyring = Keyring.from_settings(settings)
    tenant_id = uuid.uuid4()

    with tenant_transaction(engine, tenant_id) as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id, slug, name, profile, status) "
                "VALUES (:id, :slug, :name, 'adult_general', 'active')"
            ),
            {"id": tenant_id, "slug": slug, "name": name},
        )
        # The data key is provisioning data: the runtime may only read one, so
        # a tenant created without it would fail its first write.
        provision_data_key(conn, tenant_id, keyring)

    # The first key goes through the same provisioning path the tests use, so
    # there is one implementation of "mint the tenant's first key", not two.
    issued = issue_api_key(
        engine,
        tenant_id=tenant_id,
        scopes=["observe", "admin"],
        env=settings.env,
        pepper=settings.key_pepper.get_secret_value(),
        issuer_key_id=None,
    )
    return tenant_id, issued.full_key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision an Adapt tenant (owner only)")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)

    settings = _settings()
    engine = _owner_engine()
    try:
        tenant_id, full_key = provision(engine, settings, slug=args.slug, name=args.name)
    finally:
        engine.dispose()

    print(f"tenant_id: {tenant_id}")
    print(f"api key:   {full_key}")
    print("\nThis key is shown once and stored only as a digest. Save it now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
