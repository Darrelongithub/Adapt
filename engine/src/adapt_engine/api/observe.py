"""``POST /v1/observe`` — the batch ingest.

The endpoint is thin on purpose; the rules live in ``observe/schema.py``,
``observe/flags.py`` and ``observe/service.py`` so they can be tested without
HTTP and reused by an SDK's local validation. What is here is only the HTTP
contract: the size caps, the idempotency header, the two refusals the doc names
by status (``413`` and ``402``), and the shape of the answer.

Two choices worth defending:

* **The body is read raw and hashed** rather than re-serialised. A replay has to
  be byte-identical, and `json.dumps(json.loads(x)) != x` is a fact of life.
* **`connection` is optional, and if present must match the key.** The key
  already identifies the connection, so the field is redundant; letting it
  disagree would let a host attribute its events to a different connection's
  quota. Refusing the mismatch is cheaper than debugging it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Connection

from adapt_engine.api.deps import get_clock, get_ingest_limiter
from adapt_engine.auth.deps import (
    AuthContext,
    ingest_connection,
    require_scope,
    security_requirement,
)
from adapt_engine.clock import Clock
from adapt_engine.connections import service as connections
from adapt_engine.errors import ERROR_RESPONSES, AdaptError
from adapt_engine.observe import service
from adapt_engine.observe.limits import IngestLimiter
from adapt_engine.observe.schema import (
    MAX_BODY_BYTES,
    MAX_EVENTS,
    EventInvalid,
    parse_event,
)

router = APIRouter(prefix="/v1", tags=["observe"], responses=ERROR_RESPONSES)

IDEMPOTENCY_HEADER = "Idempotency-Key"


def _refuse(
    code: str,
    message: str,
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
) -> AdaptError:
    return AdaptError(
        code=code,
        message=message,
        status_code=status_code,
        retryable=retryable,
        headers=headers,
        details=details,
    )


def _invalid(message: str, *, field: str | None = None) -> AdaptError:
    """422 with the offending field in `details.field` (§6)."""
    return _refuse(
        "payload_invalid",
        message,
        422,
        details={"field": field} if field else None,
    )


@router.post(
    "/observe",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_scope("observe"))],
    openapi_extra=security_requirement("observe"),
    response_model=None,
)
async def observe(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_scope("observe"))],
    conn: Annotated[Connection, Depends(ingest_connection)],
    clock: Annotated[Clock, Depends(get_clock)],
    limiter: Annotated[IngestLimiter, Depends(get_ingest_limiter)],
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> JSONResponse:
    """Accept up to 500 events, once.

    The status is ``202``: the batch is durable when the response is written,
    but the *learning* it causes happens later (increment 4 onward). A ``200``
    would promise more than we know.

    The route is ``async`` for exactly one reason — reading the raw body — and
    then hops to the threadpool for the work. A blocking database call inside
    ``async def`` runs on the event loop, and under four concurrent batches
    this deadlocked in the load test: one request held a row lock while the
    loop was blocked in another request's query, waiting for that lock. Every
    other route in this app is plain ``def`` for the same reason; this one
    needs the body first.
    """
    if idempotency_key is None or not 8 <= len(idempotency_key) <= 200:
        raise _invalid(
            "Idempotency-Key is required and must be 8-200 characters; "
            "derive it from the logical operation so a retry reuses it",
            field="Idempotency-Key",
        )

    raw = await request.body()
    return await run_in_threadpool(
        _ingest,
        raw,
        idempotency_key=idempotency_key,
        auth=auth,
        conn=conn,
        clock=clock,
        limiter=limiter,
    )


def _ingest(
    raw: bytes,
    *,
    idempotency_key: str,
    auth: AuthContext,
    conn: Connection,
    clock: Clock,
    limiter: IngestLimiter,
) -> JSONResponse:
    """The blocking half: limit, parse, validate, store. Runs in the threadpool."""
    if len(raw) > MAX_BODY_BYTES:
        raise _refuse(
            "payload_too_large",
            f"the request body is limited to {MAX_BODY_BYTES} bytes",
            413,
        )

    try:
        document: Any = json.loads(raw)
    except ValueError as exc:
        raise _invalid("the request body is not valid JSON", field="body") from exc

    events_raw, declared_connection = _split_batch(document)
    now = clock.now().astimezone(UTC)

    events = []
    for index, raw_event in enumerate(events_raw):
        try:
            events.append(parse_event(raw_event, index=index, now=now))
        except EventInvalid as exc:
            raise _invalid(
                f"event {exc.index}: {exc.field_name}: {exc.detail}",
                field=f"events[{exc.index}].{exc.field_name}",
            ) from exc

    connection_id = _connection_id(conn, auth)
    _spend_rate_limit(limiter, connection_id)
    if declared_connection is not None and declared_connection != str(connection_id):
        raise _invalid(
            "connection does not match the connection this key belongs to",
            field="connection",
        )

    try:
        stored = service.ingest(
            conn,
            tenant_id=auth.tenant_id,
            connection_id=connection_id,
            idempotency_key=idempotency_key,
            body_hash=service.request_hash(raw),
            events=events,
            now=now,
        )
    except service.IdempotencyConflict as exc:
        raise _refuse(
            "conflict",
            "this Idempotency-Key was already used with a different body",
            409,
            headers={"X-Adapt-Idempotency-Conflict": "body"},
        ) from exc
    except service.QuotaExhausted as exc:
        raise _refuse(
            "quota_exhausted",
            (
                "the monthly event quota for this connection is exhausted; "
                f"resets_at is {exc.resets_at.isoformat()}. This response is free "
                "and the learner history already stored is unaffected."
            ),
            402,
            headers={"X-Adapt-Resets-At": exc.resets_at.isoformat()},
            details={"resets_at": exc.resets_at.isoformat()},
        ) from exc

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=stored.response)


def _spend_rate_limit(limiter: IngestLimiter, connection_id: uuid.UUID) -> None:
    """METERING §6's 50 rps / burst 100, per connection.

    A refusal is free (nothing is written, no quota spent) and counted: the log
    line is how a spike becomes visible rather than a mystery. The wait is
    reported twice — `Retry-After` for HTTP clients, `details.retry_after` for
    the ones that cannot read headers — and it is always at least one second,
    because "retry after 0" is not a thing a client can act on.
    """
    wait = limiter.take(connection_id)
    if wait is None:
        return
    structlog.get_logger("adapt_engine").warning(
        "observe_rate_limited", connection_id=str(connection_id), retry_after=wait
    )
    raise AdaptError(
        code="rate_limited",
        message=f"too many requests for this connection; retry after {wait}s",
        status_code=429,
        retryable=True,
        headers={"Retry-After": str(wait)},
        details={"retry_after": wait},
    )


def _split_batch(document: Any) -> tuple[list[Any], str | None]:
    """Check the envelope, the size, and the connection claim."""
    if not isinstance(document, dict):
        raise _invalid("the request body must be an object with an 'events' array", field="body")
    unknown = sorted(set(document) - {"events", "connection"})
    if unknown:
        raise _invalid("unknown field(s): " + ", ".join(unknown), field=unknown[0])
    if "events" not in document:
        raise _invalid("missing field(s): events", field="events")
    events = document["events"]
    if not isinstance(events, list):
        raise _invalid("events must be an array", field="events")
    if not events:
        raise _invalid("events must not be empty", field="events")
    if len(events) > MAX_EVENTS:
        raise _refuse(
            "payload_too_large",
            f"a batch is limited to {MAX_EVENTS} events; split it and reuse the "
            "Idempotency-Key pattern per batch",
            413,
        )
    declared = document.get("connection")
    if declared is not None and not isinstance(declared, str):
        raise _invalid("connection must be a string", field="connection")
    return events, declared


def _connection_id(conn: Connection, auth: AuthContext) -> uuid.UUID:
    found = connections.connection_id_for_key(conn, auth.key_id)
    if found is None:
        # A console key has no connection, so it has no quota to spend and no
        # events to attribute. Refusing loudly beats writing unattributed rows.
        raise _refuse(
            "not_entitled",
            "observe needs a connection key, not a console key (tier: core)",
            403,
            details={"tier_required": "core"},
        )
    return found
