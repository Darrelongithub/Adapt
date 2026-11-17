"""Key fixtures: one bootstrap key per tenant, then real minting for the rest."""

from __future__ import annotations

import uuid

from sqlalchemy.engine import Engine

from adapt_engine.auth import keys as key_service
from tests.conftest import TEST_PEPPER


def mint(
    engine: Engine,
    *,
    tenant_id: uuid.UUID,
    issuer_key_id: uuid.UUID | None,
    scopes: tuple[str, ...] = ("observe",),
    env: str = "test",
    expires_in_days: int | None = None,
):
    """Issue a key through ``adapt_issue_api_key`` as ``issuer_key_id``.

    ``issuer_key_id=None`` goes through the provisioning path, which the
    database only allows for a session connected as ``adapt_owner``.
    """
    return key_service.issue_api_key(
        engine,
        tenant_id=tenant_id,
        scopes=list(scopes),
        env=env,
        pepper=TEST_PEPPER,
        issuer_key_id=issuer_key_id,
        expires_in_days=expires_in_days,
    )
