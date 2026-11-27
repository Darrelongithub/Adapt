"""Tenant provisioning: the tenant row, its data key, and its first API key.

Provisioning is an owner operation. It lives in the package (not only in
`scripts/provision_tenant.py`) because the `adapt` CLI's sandbox needs it too, and
two copies of "mint the tenant's first key" is two places for the key rules to
drift.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.auth.keys import issue_api_key
from adapt_engine.config import Settings
from adapt_engine.crypto.keyring import Keyring
from adapt_engine.crypto.provisioning import provision_data_key
from adapt_engine.db.tenant import tenant_transaction

#: Scopes the first key gets. `admin` is what the console reads with; `observe`
#: and `decide` are what a host needs on day one.
FIRST_KEY_SCOPES = ("observe", "decide", "read_model", "admin")


def provision(
    engine: Engine,
    settings: Settings,
    *,
    slug: str,
    name: str,
    scopes: tuple[str, ...] = FIRST_KEY_SCOPES,
    tier: str = "core",
) -> tuple[uuid.UUID, str]:
    """Create tenant + data key + first key. Returns `(tenant_id, full_api_key)`."""
    keyring = Keyring.from_settings(settings)
    tenant_id = uuid.uuid4()

    with tenant_transaction(engine, tenant_id) as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id, slug, name, profile, status, tier) "
                "VALUES (:id, :slug, :name, 'adult_general', 'active', :tier)"
            ),
            {"id": tenant_id, "slug": slug, "name": name, "tier": tier},
        )
        # The data key is provisioning data: the runtime may only read one, so a
        # tenant created without it would fail its first write.
        provision_data_key(conn, tenant_id, keyring)

    issued = issue_api_key(
        engine,
        tenant_id=tenant_id,
        scopes=list(scopes),
        env=settings.env,
        pepper=settings.key_pepper.get_secret_value(),
        issuer_key_id=None,
    )
    return tenant_id, issued.full_key
