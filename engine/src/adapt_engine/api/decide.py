"""``POST /v1/decide`` and the replay that keeps it honest.

Two routes. The first serves or abstains, and the second proves that the first
one can be re-run from its own stored inputs and produce the same bytes.

Three contract facts worth stating in code rather than in prose:

* **A paused connection is refused here too.** `ingest_connection` is the same
  dependency `observe` uses: a host whose connection is paused should not keep
  being advised on the strength of data it can no longer send.
* **`Idempotency-Key` is required**, and a replay of the same key with a
  *different* body is a `409 conflict` — the same rule as ingest, so a host has
  one mental model rather than two.
* **A retry returns the stored decision.** It does not recompute: the learner may
  have answered something in between, and a client that retried a timed-out
  request must not receive different advice from the one already written for it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.engine import Connection

from adapt_engine.api.deps import get_clock, get_settings
from adapt_engine.auth.deps import (
    AuthContext,
    ingest_connection,
    require_scope,
    security_requirement,
)
from adapt_engine.clock import Clock
from adapt_engine.config import Settings
from adapt_engine.credits import service as credits
from adapt_engine.decide import service
from adapt_engine.errors import ERROR_RESPONSES, AdaptError

router = APIRouter(prefix="/v1", tags=["decide"], responses=ERROR_RESPONSES)

IDEMPOTENCY_HEADER = "Idempotency-Key"
MAX_BODY_BYTES = 8 * 1024
FIELDS = {"learner_id", "skill"}


def _body_response(body: dict[str, Any], *, headers: dict[str, str]) -> Response:
    """Serialise a decision body canonically, so a replay is byte-identical.

    `jsonb` does not preserve key order, so a body returned straight from the
    database would differ from the same body returned from `evaluate` by key
    order alone — and "byte-identical replay" would be false in the only sense a
    client can check. Both paths therefore emit sorted keys with fixed
    separators.
    """
    return Response(
        content=json.dumps(body, sort_keys=True, separators=(",", ":")),
        media_type="application/json",
        headers=headers,
    )


def _invalid(message: str, *, field: str) -> AdaptError:
    return AdaptError(
        code="payload_invalid",
        message=message,
        status_code=422,
        retryable=False,
        details={"field": field},
    )


def _unknown(learner_id: str) -> AdaptError:
    return AdaptError(
        code="learner_unknown",
        message="no evidence for this learner on this skill; post observations first",
        status_code=404,
        retryable=False,
        details={"learner_id": learner_id},
    )


@router.post(
    "/decide",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_scope("decide"))],
    openapi_extra=security_requirement("decide"),
    response_model=None,
)
async def decide(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_scope("decide"))],
    conn: Annotated[Connection, Depends(ingest_connection)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
    x_adapt_test: Annotated[str | None, Header(alias="X-Adapt-Test")] = None,
) -> Response:
    """Serve one decision, or abstain with the reason and what would change it."""
    if idempotency_key is None or not 8 <= len(idempotency_key) <= 200:
        raise _invalid(
            "Idempotency-Key is required and must be 8-200 characters; "
            "derive it from the logical decision so a retry reuses it",
            field="Idempotency-Key",
        )
    raw = await request.body()
    return await _run(
        raw,
        idempotency_key=idempotency_key,
        auth=auth,
        conn=conn,
        clock=clock,
        test_traffic=_test_traffic(x_adapt_test, settings),
    )


def _test_traffic(header: str | None, settings: Settings) -> bool:
    """`X-Adapt-Test: 1`, or a staging deployment — METERING §3 rule 3.

    A deployment running with `ADAPT_ENV=staging` exists to be exercised, so it
    bills nothing without every caller remembering a header; production bills
    unless the header says otherwise. Anything other than absent/0/1 is refused
    rather than guessed at: a header that silently means "bill me" when it says
    "yes" is a support ticket.
    """
    if header is None or header == "0":
        return settings.env == "staging"
    if header == "1":
        return True
    raise AdaptError(
        code="payload_invalid",
        message="X-Adapt-Test must be 1 or 0",
        status_code=422,
        retryable=False,
        details={"field": "X-Adapt-Test"},
    )


async def _run(
    raw: bytes,
    *,
    idempotency_key: str,
    auth: AuthContext,
    conn: Connection,
    clock: Clock,
    test_traffic: bool,
) -> Response:
    from fastapi.concurrency import run_in_threadpool

    return await run_in_threadpool(
        _decide,
        raw,
        idempotency_key=idempotency_key,
        auth=auth,
        conn=conn,
        clock=clock,
        test_traffic=test_traffic,
    )


def _decide(
    raw: bytes,
    *,
    idempotency_key: str,
    auth: AuthContext,
    conn: Connection,
    clock: Clock,
    test_traffic: bool,
) -> Response:
    if len(raw) > MAX_BODY_BYTES:
        raise AdaptError(
            code="payload_too_large",
            message=f"the request body is limited to {MAX_BODY_BYTES} bytes",
            status_code=413,
            retryable=False,
        )
    try:
        payload: Any = json.loads(raw)
    except ValueError as exc:
        raise _invalid("the request body must be a JSON object", field="body") from exc
    if not isinstance(payload, dict):
        raise _invalid("the request body must be a JSON object", field="body")
    unknown_fields = sorted(set(payload) - FIELDS)
    if unknown_fields:
        raise _invalid(
            f"unknown field(s): {', '.join(unknown_fields)}; accepted: {', '.join(sorted(FIELDS))}",
            field=unknown_fields[0],
        )

    learner_id = payload.get("learner_id")
    if not isinstance(learner_id, str) or not 8 <= len(learner_id) <= 64:
        raise _invalid("learner_id must be a string of 8-64 characters", field="learner_id")
    skill = payload.get("skill")
    if not isinstance(skill, str) or not skill:
        raise _invalid("skill is required", field="skill")

    try:
        stored = service.decide(
            conn,
            tenant_id=auth.tenant_id,
            connection_id=_connection_id(conn, auth),
            learner_id=learner_id,
            skill=skill,
            idempotency_key=idempotency_key,
            body_hash=service.request_hash(raw),
            now=clock.now().astimezone(UTC),
            reserve=credits.reserve,
            settle=credits.settle,
            test_traffic=test_traffic,
        )
    except LookupError:
        raise _unknown(learner_id) from None

    # A replay bills nothing: the credit was taken when the decision was first
    # served, and the header is the caller's only evidence of that.
    billed = 0 if (test_traffic or stored.replayed or stored.body.get("abstain")) else 1
    return _body_response(
        stored.body,
        headers={
            "X-Adapt-Decision-Id": str(stored.decision_id),
            "X-Adapt-Replayed": "true" if stored.replayed else "false",
            "X-Adapt-Billed": str(billed),
            "X-Adapt-Test-Traffic": "true" if test_traffic else "false",
        },
    )


@router.post(
    "/decisions/{decision_id}/replay",
    dependencies=[Depends(require_scope("decide"))],
    openapi_extra=security_requirement("decide"),
    response_model=None,
)
def replay(
    decision_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require_scope("decide"))],
    conn: Annotated[Connection, Depends(ingest_connection)],
) -> Response:
    """Re-run a stored decision from its own inputs and return the stored body.

    A mismatch raises a 500 rather than returning the stored body quietly: this
    endpoint exists to be able to fail. CI replays a hundred stored decisions and
    asserts byte equality; a decision whose inputs no longer reproduce its body
    is a determinism bug in the estimator, and the only useful place for it to
    show up is here.
    """
    stored = service.replay(conn, tenant_id=auth.tenant_id, decision_id=decision_id)
    if stored is None:
        # INTEGRATION §6 has `decision_unknown` as of the increment-4 review: a
        # replay of something we never served is not a typo'd route.
        raise AdaptError(
            code="decision_unknown",
            message="no stored decision with that id for this tenant",
            status_code=404,
            retryable=False,
            details={"decision_id": str(decision_id)},
        )
    return _body_response(
        stored.body,
        headers={
            "X-Adapt-Decision-Id": str(stored.id),
            "X-Adapt-Replayed": "identical",
            # A replay re-serves a stored body: weight 0, and the header says so
            # rather than leaving a caller to assume it was billed again.
            "X-Adapt-Billed": "0",
            "X-Adapt-Test-Traffic": "false",
        },
    )


def _connection_id(conn: Connection, auth: AuthContext) -> uuid.UUID | None:
    """The connection behind the calling key, or ``None`` for a console key.

    `decisions.connection_id` is a nullable FK on purpose: an onboarding key has
    no connection, and refusing to record a decision for it would make the
    console unable to show what the engine would advise during setup.
    """
    from adapt_engine.connections import service as connections

    return connections.connection_id_for_key(conn, auth.key_id)
