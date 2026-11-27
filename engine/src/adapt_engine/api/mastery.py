"""``GET /v1/mastery`` — read a learner's calibrated estimate, or an abstention.

Two routes, one shape: the collection returns every skill the learner has
evidence on, the item returns one. Both return ``200`` for an abstention,
because "we do not know yet" is a successful answer to "what do you know about
this learner" — a ``404`` would say the learner does not exist and a ``409``
would say the request was wrong, and neither is true.

A learner we have never seen *is* a ``404 learner_unknown``: there is no
fabricated 0.5 prior for a stranger, since the host would rather learn that its
``learner_id`` is misspelled than receive a confident number about nobody.

The reads are hot and cheap (two indexed lookups), so they are plain ``def``
routes on the threadpool — the same rule as every other route in this app: a
blocking query inside ``async def`` blocks the loop for every other request.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Connection

from adapt_engine.auth.deps import (
    AuthContext,
    require_scope,
    security_requirement,
    tenant_connection,
)
from adapt_engine.errors import ERROR_RESPONSES, AdaptError
from adapt_engine.mastery import service

router = APIRouter(prefix="/v1", tags=["mastery"], responses=ERROR_RESPONSES)

#: The same shape `learning_events` accepts, so a skill that can be observed can
#: be read back and nothing else can be asked for.
SKILL_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _invalid_field(message: str, *, field: str) -> AdaptError:
    """422 with the offending field in `details.field` (INTEGRATION §6)."""
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
        message="no mastery state for this learner; post observations before reading a model",
        status_code=404,
        retryable=False,
        details={"learner_id": learner_id},
    )


def _learner(learner_id: str) -> str:
    if not 8 <= len(learner_id) <= 64:
        raise _invalid_field("learner_id must be 8-64 characters", field="learner_id")
    return learner_id


def _skill(skill: str) -> str:
    if not SKILL_PATTERN.match(skill):
        raise _invalid_field("skill must match ^[a-z][a-z0-9_.-]{0,63}$", field="skill")
    return skill


@router.get(
    "/mastery",
    dependencies=[Depends(require_scope("read_model"))],
    openapi_extra=security_requirement("read_model"),
    response_model=None,
)
def mastery_snapshot(
    auth: Annotated[AuthContext, Depends(require_scope("read_model"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    learner_id: Annotated[str, Query(min_length=8, max_length=64)],
) -> JSONResponse:
    """Every skill this learner has evidence on, in skill order."""
    learner = _learner(learner_id)
    try:
        rows = service.snapshot(conn, tenant_id=auth.tenant_id, learner_id=learner)
    except LookupError:
        raise _unknown(learner) from None
    return JSONResponse({"learner_id": learner, "skills": rows})


@router.get(
    "/mastery/{skill}",
    dependencies=[Depends(require_scope("read_model"))],
    openapi_extra=security_requirement("read_model"),
    response_model=None,
)
def mastery_for_skill(
    skill: str,
    auth: Annotated[AuthContext, Depends(require_scope("read_model"))],
    conn: Annotated[Connection, Depends(tenant_connection)],
    learner_id: Annotated[str, Query(min_length=8, max_length=64)],
) -> JSONResponse:
    """One learner's mastery on one skill, or the count that would unlock it."""
    learner = _learner(learner_id)
    known = _skill(skill)
    try:
        body: dict[str, Any] = service.estimate_for(
            conn, tenant_id=auth.tenant_id, learner_id=learner, skill=known
        )
    except LookupError:
        raise _unknown(learner) from None
    return JSONResponse(body)
