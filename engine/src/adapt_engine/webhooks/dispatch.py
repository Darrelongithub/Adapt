"""Webhook delivery: an outbox with five attempts, a log, and no payload at rest.

Why an outbox rather than a fire-and-forget POST in the request path: a webhook
is a *notification that something happened*, and the thing that happened has
already been committed. Sending it inline makes the customer's uptime our
latency, and a receiver that is down for a minute silently loses events. So the
row is the record, the attempt is a consequence, and ``run`` is a loop somebody
else can schedule.

Rules this module holds:

* **Five attempts, exponential backoff** (1s, 4s, 16s, 64s between them), then
  ``failed``. Not "retry until it works": an outbox row that retries forever is
  a queue with no bound and a delivery log nobody can read.
* **The same ``webhook-id`` on every attempt.** Standard Webhooks puts the id in
  a header so the receiver can recognise a retry. A new id per attempt would
  turn one event into five events at the far end.
* **A duplicate enqueue is a no-op.** ``webhook_id`` is derived from the event,
  and ``(tenant_id, webhook_id)`` is unique, so the second enqueue finds the
  first row and returns it without contacting anyone.
* **No payload at rest.** ``webhook_deliveries`` stores the type, an opaque
  subject id, the attempt count and the last status code; ``webhook_attempts``
  stores one row per attempt. Neither has a body column, and the error text is a
  token (``timeout``, ``connection_refused``), never a response body.

Time is an argument, not a clock read: ``run(now=...)`` attempts what is due
*at that moment*. That makes the backoff testable without sleeping, and makes a
retry schedule reproducible in a way a ``sleep`` loop never is.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import Connection

from adapt_engine.connections.service import EVENT_TYPES
from adapt_engine.webhooks import signing

#: Seconds to wait before attempt 2, 3, 4 and 5.
BACKOFF_SECONDS: tuple[int, ...] = (1, 4, 16, 64)

#: Five attempts total: the first try plus four retries.
MAX_ATTEMPTS = 5

#: How far a scheduled retry may drift from its base delay, either way.
#: INTEGRATION §8 says "with jitter" and does not say how much; ±20% keeps
#: "roughly 85 seconds" true (68-102 s) while still spreading the thundering
#: herd that a fixed schedule produces when a receiver restarts.
JITTER_FRACTION = 0.2


@dataclass(frozen=True)
class TransportResult:
    """What one HTTP attempt produced — one of the two, never both."""

    status_code: int | None = None
    error_class: str | None = None
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if (self.status_code is None) == (self.error_class is None):
            raise ValueError("a transport result is either a status code or an error class")


class WebhookTransport(Protocol):
    """The one place a delivery leaves the process.

    A protocol rather than a concrete client so tests can count attempts and
    answer 500 on demand without a network, and so the real call has exactly one
    implementation to audit.
    """

    def post(self, url: str, headers: dict[str, str], body: bytes) -> TransportResult: ...


class DeliveryDisabled(Exception):
    """No transport is configured; the delivery is recorded and not attempted."""


@dataclass(frozen=True)
class Delivery:
    id: uuid.UUID
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    event_type: str
    webhook_id: str
    subject_id: str | None
    status: str
    attempts: int
    last_status_code: int | None
    last_error: str | None
    next_attempt_at: datetime | None
    created: bool


COLUMNS = (
    "id, tenant_id, connection_id, event_type, webhook_id, subject_id, status,"
    " attempts, last_status_code, last_error, next_attempt_at"
)


def _delivery(row: object, *, created: bool) -> Delivery:
    m = row._mapping  # type: ignore[attr-defined]
    return Delivery(
        id=m["id"],
        tenant_id=m["tenant_id"],
        connection_id=m["connection_id"],
        event_type=m["event_type"],
        webhook_id=m["webhook_id"],
        subject_id=m["subject_id"],
        status=m["status"],
        attempts=m["attempts"],
        last_status_code=m["last_status_code"],
        last_error=m["last_error"],
        next_attempt_at=m["next_attempt_at"],
        created=created,
    )


def msg_id_for(
    *, tenant_id: uuid.UUID, connection_id: uuid.UUID, event_type: str, dedupe_key: str
) -> str:
    """A stable message id for one logical event.

    Derived, not random: two enqueues of the same event must land on the same
    row, and a retry must keep the id the receiver has already seen. The inputs
    are the things that make two events *the same event* — tenant, connection,
    type, and whatever the caller says identifies the occurrence (a decision id,
    a ledger ref).
    """
    import hashlib

    material = f"{tenant_id}|{connection_id}|{event_type}|{dedupe_key}".encode()
    return "msg_" + hashlib.sha256(material).hexdigest()[:32]


def enqueue(
    conn: Connection,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    event_type: str,
    subject_id: str | None,
    now: datetime,
    dedupe_key: str | None = None,
) -> Delivery:
    """Record one event for delivery. Idempotent on the derived message id."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown webhook event type: {event_type!r}")
    key = dedupe_key or subject_id or uuid.uuid4().hex
    webhook_id = msg_id_for(
        tenant_id=tenant_id,
        connection_id=connection_id,
        event_type=event_type,
        dedupe_key=key,
    )
    existing = conn.execute(
        text(f"SELECT {COLUMNS} FROM webhook_deliveries WHERE webhook_id = :wid"),
        {"wid": webhook_id},
    ).one_or_none()
    if existing is not None:
        # The same message, already queued: no second row, no second delivery.
        return _delivery(existing, created=False)

    row = conn.execute(
        text(
            f"""
            INSERT INTO webhook_deliveries
                (tenant_id, connection_id, event_type, webhook_id, subject_id,
                 status, attempts, next_attempt_at, created_at)
            VALUES (:tenant_id, :connection_id, :event_type, :webhook_id, :subject_id,
                    'pending', 0, :now, :now)
            RETURNING {COLUMNS}
            """
        ),
        {
            "tenant_id": tenant_id,
            "connection_id": connection_id,
            "event_type": event_type,
            "webhook_id": webhook_id,
            "subject_id": subject_id,
            "now": now,
        },
    ).one()
    return _delivery(row, created=True)


def body_for(delivery: Delivery, *, occurred_at: datetime) -> bytes:
    """The wire body: ids and types only.

    No learner id, no skill, no mastery, no free text. A receiver that needs
    more calls back with its own key; a webhook is a doorbell.
    """
    return json.dumps(
        {
            "id": delivery.webhook_id,
            "type": delivery.event_type,
            "created_at": occurred_at.astimezone(UTC).isoformat(),
            "data": {"id": delivery.subject_id} if delivery.subject_id else {},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_attempt(
    conn: Connection,
    *,
    delivery: Delivery,
    attempt: int,
    result: TransportResult,
    now: datetime,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO webhook_attempts
                (tenant_id, delivery_id, attempt, status_code, error_class, duration_ms,
                 attempted_at)
            VALUES (:tenant_id, :delivery_id, :attempt, :status_code, :error_class,
                    :duration_ms, :now)
            """
        ),
        {
            "tenant_id": delivery.tenant_id,
            "delivery_id": delivery.id,
            "attempt": attempt,
            "status_code": result.status_code,
            "error_class": result.error_class,
            "duration_ms": result.duration_ms,
            "now": now,
        },
    )


def _jitter(delivery_id: uuid.UUID, attempt: int) -> float:
    """A reproducible offset in ``[-1, 1]`` for one attempt of one delivery.

    Derived, not random: two runs of the same delivery produce the same
    schedule (so the backoff is testable and a replay is reproducible), while
    two deliveries that fail at the same instant do not retry at the same
    instant. ``random()`` would give the first property up and the second one
    only if it were seeded per delivery anyway.
    """
    digest = hashlib.sha256(f"{delivery_id}:{attempt}".encode()).digest()
    return (int.from_bytes(digest[:8], "big") / float(2**64)) * 2 - 1


def _next_attempt_at(delivery_id: uuid.UUID, attempts: int, now: datetime) -> datetime | None:
    """When attempt ``attempts + 1`` is due, or ``None`` when we are done."""
    if attempts >= MAX_ATTEMPTS:
        return None
    base = BACKOFF_SECONDS[attempts - 1]
    return now + timedelta(seconds=base * (1 + JITTER_FRACTION * _jitter(delivery_id, attempts)))


@dataclass(frozen=True)
class Attempted:
    """What one `attempt` call did."""

    delivery_id: uuid.UUID
    attempt: int
    status_code: int | None
    error_class: str | None
    final_status: str


class Dispatcher:
    """Runs due deliveries through a transport, one attempt each."""

    def __init__(
        self,
        transport: WebhookTransport,
        *,
        target_for: Callable[[Connection, uuid.UUID], tuple[str | None, str | None]],
    ) -> None:
        self._transport = transport
        self._target_for = target_for

    def attempt(self, conn: Connection, delivery: Delivery, *, now: datetime) -> Attempted:
        """One attempt, recorded whatever happens."""
        if delivery.status in ("delivered", "failed"):
            raise ValueError(f"delivery is already {delivery.status}")
        url, secret = self._target_for(conn, delivery.connection_id)
        if url is None or secret is None:
            raise DeliveryDisabled("this connection has no webhook_url")
        body = body_for(delivery, occurred_at=now)
        attempt_number = delivery.attempts + 1
        headers = signing.headers_for(
            secret, msg_id=delivery.webhook_id, timestamp=int(now.timestamp()), body=body
        )
        headers["content-type"] = "application/json"
        headers["user-agent"] = "Adapt-Webhooks/1"

        try:
            result = self._transport.post(url, headers, body)
        except Exception as exc:
            result = TransportResult(error_class=_error_class(exc))

        _record_attempt(conn, delivery=delivery, attempt=attempt_number, result=result, now=now)

        succeeded = result.status_code is not None and 200 <= result.status_code < 300
        next_at = None if succeeded else _next_attempt_at(delivery.id, attempt_number, now)
        status = "delivered" if succeeded else ("failed" if next_at is None else "pending")
        conn.execute(
            text(
                """
                UPDATE webhook_deliveries
                   SET attempts = :attempts,
                       status = :status,
                       last_status_code = :status_code,
                       last_error = :error_class,
                       next_attempt_at = :next_at,
                       delivered_at = CASE WHEN :status = 'delivered' THEN :now ELSE NULL END
                 WHERE id = :id
                """
            ),
            {
                "id": delivery.id,
                "attempts": attempt_number,
                "status": status,
                "status_code": result.status_code,
                "error_class": result.error_class,
                "next_at": next_at,
                "now": now,
            },
        )
        return Attempted(
            delivery_id=delivery.id,
            attempt=attempt_number,
            status_code=result.status_code,
            error_class=result.error_class,
            final_status=status,
        )

    def due(self, conn: Connection, *, now: datetime) -> list[Delivery]:
        rows = conn.execute(
            text(
                f"""
                SELECT {COLUMNS} FROM webhook_deliveries
                 WHERE status = 'pending'
                   AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
                 ORDER BY created_at, id
                """
            ),
            {"now": now},
        ).all()
        return [_delivery(row, created=False) for row in rows]

    def run(self, conn: Connection, *, now: datetime) -> list[Attempted]:
        """Attempt everything due at ``now``, once each.

        A caller schedules ``run``; it does not sleep inside it. Five attempts
        spread over 85 seconds is a schedule, and a scheduler that cannot be
        replayed is a scheduler nobody can test.
        """
        results: list[Attempted] = []
        for delivery in self.due(conn, now=now):
            try:
                results.append(self.attempt(conn, delivery, now=now))
            except DeliveryDisabled:
                # No URL is a configuration state, not a transient failure: the
                # row stays pending and nothing is attempted.
                continue
        return results


def _error_class(exc: Exception) -> str:
    """A token, never a message: exception text can contain a URL or a body."""
    name = type(exc).__name__
    known = {
        "TimeoutError": "timeout",
        "URLError": "url_error",
        "ConnectionRefusedError": "connection_refused",
        "gaierror": "dns_error",
        "socket.timeout": "timeout",
    }
    return known.get(name, "transport_error")


@dataclass(frozen=True)
class AttemptLogRow:
    """One attempt inside a delivery: what the receiver said, and how long."""

    attempt: int
    status_code: int | None
    error_class: str | None
    duration_ms: int
    attempted_at: datetime


@dataclass(frozen=True)
class DeliveryLogRow:
    """One delivery, as the console reads it: outcomes, never a body."""

    delivery_id: uuid.UUID
    event_type: str
    webhook_id: str
    status: str
    attempts: int
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
    attempt_log: list[AttemptLogRow]


def delivery_log(conn: Connection, connection_id: uuid.UUID) -> list[DeliveryLogRow]:
    """The console's view: attempts and outcomes, never a payload."""
    rows = conn.execute(
        text(
            """
            SELECT d.id, d.event_type, d.webhook_id, d.status, d.attempts,
                   d.last_status_code, d.last_error, d.created_at, d.delivered_at,
                   COALESCE(
                       (SELECT json_agg(json_build_object(
                                   'attempt', a.attempt,
                                   'status_code', a.status_code,
                                   'error_class', a.error_class,
                                   'duration_ms', a.duration_ms,
                                   'attempted_at', a.attempted_at
                               ) ORDER BY a.attempt)
                          FROM webhook_attempts a
                         WHERE a.delivery_id = d.id),
                       '[]'::json
                   ) AS attempts_log
              FROM webhook_deliveries d
             WHERE d.connection_id = :connection_id
             ORDER BY d.created_at DESC, d.id
            """
        ),
        {"connection_id": connection_id},
    ).all()
    return [
        DeliveryLogRow(
            delivery_id=row.id,
            event_type=row.event_type,
            webhook_id=row.webhook_id,
            status=row.status,
            attempts=row.attempts,
            last_status_code=row.last_status_code,
            last_error=row.last_error,
            created_at=row.created_at,
            delivered_at=row.delivered_at,
            attempt_log=[AttemptLogRow(**attempt) for attempt in row.attempts_log],
        )
        for row in rows
    ]
