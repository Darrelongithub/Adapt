"""Connections: host app x environment x key x webhook.

The row is small; the rules around it are not, so they live here rather than in
the handler:

* **Create issues the key.** One transaction: ``adapt_issue_api_key`` runs with
  the caller as issuer (so the scope-subset rule applies unchanged), the
  connection references the key, and the audit row names both. A connection
  without a working key is not a connection.
* **Pause stops ingest and keeps data.** Nothing is deleted, nothing is
  rewritten; the check happens on the host-facing routes
  (``auth.deps.ingest_connection``) and admin routes stay open, because the
  console has to be able to resume a connection that is paused.
* **Revoke stops authentication.** The key is revoked in the same transaction,
  so the next request is a plain ``401 unauthorized`` — there is no "revoked but
  still accepted" window, and no second code for it. Pausing is the case that
  needs its own answer (``403 connection_paused``); revoking does not.
* **Nothing is hard-deleted.** ``DELETE`` revokes. A customer's event history
  belongs to a connection, and a connection row that vanished would orphan it.

RLS does the tenant filtering (the migration FORCEs it and the app role is not
the owner), so no query here carries a tenant predicate of its own.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

Status = Literal["active", "paused", "revoked"]

#: The closed event set. Kept next to the schema CHECK, and used to refuse an
#: unknown type before the database has to.
EVENT_TYPES: frozenset[str] = frozenset({"decision.created", "consent.changed", "quota.warning"})

STATUSES: frozenset[str] = frozenset({"active", "paused", "revoked"})


class ConnectionStateError(Exception):
    """A connection operation the state forbids."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ConnectionRow:
    id: uuid.UUID
    tenant_id: uuid.UUID
    host_name: str
    environment: str
    key_id: uuid.UUID
    webhook_url: str | None
    status: str
    event_quota_monthly: int
    created_at: datetime
    created_by: uuid.UUID | None
    paused_at: datetime | None
    revoked_at: datetime | None


#: Every read selects the same columns, so the row type cannot drift between
#: the list, the detail and the pause path.
COLUMNS = (
    "id, tenant_id, host_name, environment, key_id, webhook_url, status,"
    " event_quota_monthly, created_at, created_by, paused_at, revoked_at"
)


def _row(row: object) -> ConnectionRow:
    mapping = row._mapping  # type: ignore[attr-defined]
    return ConnectionRow(
        id=mapping["id"],
        tenant_id=mapping["tenant_id"],
        host_name=mapping["host_name"],
        environment=mapping["environment"],
        key_id=mapping["key_id"],
        webhook_url=mapping["webhook_url"],
        status=mapping["status"],
        event_quota_monthly=mapping["event_quota_monthly"],
        created_at=mapping["created_at"],
        created_by=mapping["created_by"],
        paused_at=mapping["paused_at"],
        revoked_at=mapping["revoked_at"],
    )


def new_webhook_secret() -> str:
    """A Standard Webhooks secret: ``whsec_`` + base64 of 24 random bytes."""
    import base64

    return "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode("ascii")


def insert_connection(
    conn: Connection,
    *,
    connection_id: uuid.UUID,
    tenant_id: uuid.UUID,
    host_name: str,
    environment: str,
    key_id: uuid.UUID,
    webhook_url: str | None,
    webhook_secret: str | None,
    event_quota_monthly: int,
    created_by: uuid.UUID | None,
    now: datetime,
) -> ConnectionRow:
    """Insert one connection. Callers issue the key first, in the same transaction."""
    row = conn.execute(
        text(
            f"""
            INSERT INTO connections (id, tenant_id, host_name, environment, key_id,
                                     webhook_url, webhook_secret, status,
                                     event_quota_monthly, created_at, created_by)
            VALUES (:id, :tenant_id, :host_name, :environment, :key_id,
                    :webhook_url, :webhook_secret, 'active',
                    :event_quota_monthly, :created_at, :created_by)
            RETURNING {COLUMNS}
            """
        ),
        {
            "id": connection_id,
            "tenant_id": tenant_id,
            "host_name": host_name,
            "environment": environment,
            "key_id": key_id,
            "webhook_url": webhook_url,
            "webhook_secret": webhook_secret,
            "event_quota_monthly": event_quota_monthly,
            "created_at": now,
            "created_by": created_by,
        },
    ).one()
    return _row(row)


def webhook_target(
    conn: Connection, store: object, connection_id: uuid.UUID
) -> tuple[str | None, str | None]:
    """The (url, secret) a delivery needs, with the secret decrypted on demand.

    The secret is ciphertext at rest under the tenant data key, bound by AAD to
    ``(tenant, this row, 'webhook_secret')`` — the same rule as every other
    encrypted column, so a secret copied into another row's cell fails to open.
    """
    row = conn.execute(
        text("SELECT tenant_id, webhook_url, webhook_secret FROM connections WHERE id = :id"),
        {"id": connection_id},
    ).one_or_none()
    if row is None or row.webhook_url is None:
        return None, None
    secret = store.decrypt(  # type: ignore[attr-defined]
        conn, row.tenant_id, connection_id, "webhook_secret", row.webhook_secret
    )
    return row.webhook_url, secret


def get_connection(conn: Connection, connection_id: uuid.UUID) -> ConnectionRow | None:
    row = conn.execute(
        text(f"SELECT {COLUMNS} FROM connections WHERE id = :id"), {"id": connection_id}
    ).one_or_none()
    return None if row is None else _row(row)


def list_connections(conn: Connection) -> list[ConnectionRow]:
    rows = conn.execute(text(f"SELECT {COLUMNS} FROM connections ORDER BY created_at, id")).all()
    return [_row(row) for row in rows]


def status_for_key(conn: Connection, key_id: uuid.UUID) -> str | None:
    """The presenting key's connection status, or ``None`` for a non-connection key.

    A console key (issued at onboarding, not through a connection) has no
    connection, and must not be treated as paused — that would make the console
    unable to operate while a customer's app is paused.
    """
    return conn.execute(
        text("SELECT status FROM connections WHERE key_id = :key_id"), {"key_id": key_id}
    ).scalar_one_or_none()


def set_status(
    conn: Connection,
    *,
    connection_id: uuid.UUID,
    status: Status,
    now: datetime,
) -> ConnectionRow:
    """Pause, resume, or revoke — with the timestamps the CHECKs require.

    ``revoked`` is terminal: a second revoke is a no-op that returns the row,
    and un-revoking is refused. Pausing an already-paused connection is a no-op
    too, which makes the endpoint safe to retry, and resuming clears
    ``paused_at`` so the CHECK holds.
    """
    current = get_connection(conn, connection_id)
    if current is None:
        raise ConnectionStateError("unknown")
    if current.status == "revoked" and status != "revoked":
        raise ConnectionStateError("revoked_is_terminal")

    if status == "paused":
        if current.status == "paused":
            return current
        row = conn.execute(
            text(
                f"UPDATE connections SET status = 'paused', paused_at = :now"
                f" WHERE id = :id RETURNING {COLUMNS}"
            ),
            {"id": connection_id, "now": now},
        ).one()
    elif status == "active":
        if current.status == "active":
            return current
        row = conn.execute(
            text(
                f"UPDATE connections SET status = 'active', paused_at = NULL"
                f" WHERE id = :id RETURNING {COLUMNS}"
            ),
            {"id": connection_id},
        ).one()
    else:
        if current.status == "revoked":
            return current
        # A revoked connection is in exactly one state, so `paused_at` is
        # cleared: revocation is what happened last, and keeping both would
        # need a CHECK that allows two statuses at once — which is the same as
        # having no status column at all.
        row = conn.execute(
            text(
                f"UPDATE connections SET status = 'revoked', revoked_at = :now,"
                f" paused_at = NULL WHERE id = :id RETURNING {COLUMNS}"
            ),
            {"id": connection_id, "now": now},
        ).one()
    return _row(row)


def audit(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    actor_key_id: uuid.UUID | None,
    action: str,
    target: uuid.UUID,
    meta: dict[str, object],
) -> None:
    """One audit row per state change, with no payload and no secret."""
    conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor, action, target, metadata) "
            "VALUES (:tenant_id, :actor, :action, :target, CAST(:metadata AS jsonb))"
        ),
        {
            "tenant_id": tenant_id,
            "actor": f"key:{actor_key_id}" if actor_key_id else "provisioning",
            "action": action,
            "target": str(target),
            "metadata": json.dumps(meta, sort_keys=True),
        },
    )
