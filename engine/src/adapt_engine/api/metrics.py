"""``GET /v1/admin/metrics/abstention`` — the honesty metric, with its pair.

The route is a thin wrapper on purpose. Everything that decides what the number
*means* is in `metrics/service.py`, where it can be tested without HTTP, and
everything that decides whether the request is answerable at all — a window that
runs backwards, a grain that does not exist, a construct that could not be
observed — is here, as a `422` with `details.field`.

Two things about this route that are decisions rather than plumbing:

* It reads through `tenant_connection`, not `ingest_connection`. A paused
  connection must not be refused the ability to *look* at why it was paused.
* The window defaults to the last 30 days and is capped at 366: an unbounded
  `from` is a table scan dressed as a query parameter, and a metric endpoint that
  takes a minute is a metric nobody loads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.engine import Connection

from adapt_engine.api.deps import get_clock
from adapt_engine.auth.deps import (
    AuthContext,
    require_scope,
    security_requirement,
    tenant_connection,
)
from adapt_engine.clock import Clock
from adapt_engine.errors import ERROR_RESPONSES, AdaptError
from adapt_engine.metrics import service

router = APIRouter(prefix="/v1", tags=["metrics"], responses=ERROR_RESPONSES)

MAX_WINDOW = timedelta(days=366)
DEFAULT_WINDOW = timedelta(days=30)
MIN_WINDOW = timedelta(seconds=1)


def _invalid(message: str, field: str) -> AdaptError:
    return AdaptError(
        code="payload_invalid",
        message=message,
        status_code=422,
        retryable=False,
        details={"field": field},
    )


@router.get(
    "/admin/metrics/abstention",
    dependencies=[Depends(require_scope("admin"))],
    openapi_extra=security_requirement("admin"),
    response_model=None,
)
def abstention_metrics(
    auth: Annotated[AuthContext, Depends(require_scope("admin"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    clock: Annotated[Clock, Depends(get_clock)],
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    connection: Annotated[uuid.UUID | None, Query()] = None,
    construct: Annotated[str | None, Query()] = None,
    tier: Annotated[Literal["core", "plus", "deep"] | None, Query()] = None,
    grain: Annotated[Literal["request", "construct"], Query()] = "request",
) -> dict[str, Any]:
    """Abstention and served rate from one denominator, at both grains."""
    now = clock.now().astimezone(UTC)
    window_end = _utc(end, "to") if end else now
    window_start = _utc(start, "from") if start else window_end - DEFAULT_WINDOW
    if window_end - window_start < MIN_WINDOW:
        raise _invalid("`to` must be later than `from`", "to")
    if window_end - window_start > MAX_WINDOW:
        raise _invalid("the window may not exceed 366 days", "from")
    if window_start > now:
        raise _invalid("`from` is in the future", "from")

    return service.report(
        conn,
        tenant_id=auth.tenant_id,
        start=window_start,
        end=window_end,
        connection_id=connection,
        construct=construct,
        tier=tier,
        grain=grain,
    )


def _utc(value: datetime, field: str) -> datetime:
    """Naive timestamps are refused: a window boundary is a UTC instant."""
    if value.tzinfo is None:
        raise _invalid("timestamps must carry a UTC offset", field)
    return value.astimezone(UTC)
