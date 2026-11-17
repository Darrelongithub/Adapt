"""Health and version endpoints.

Health reports booleans only. Capabilities are booleans, never values,
so no configuration or secret can leak through this surface.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from adapt_engine.api.deps import get_db_check, get_redis_check, get_settings
from adapt_engine.config import Settings

router = APIRouter(prefix="/v1", tags=["health"])

# Booleans only: True means the surface is served by this build.
_CAPABILITIES: dict[str, bool] = {
    "health": True,
    "version": True,
}


class HealthResponse(BaseModel):
    """Health payload. Exactly these keys, nothing environmental."""

    status: str
    version: str
    commit: str
    db: bool
    redis: bool
    capabilities: dict[str, bool]


class VersionResponse(BaseModel):
    """Version payload. Nothing environmental."""

    version: str
    commit: str
    build_time: str


@router.get("/health", response_model=HealthResponse)
def health(
    response: Response,
    settings: Settings = Depends(get_settings),
    db_check: Callable[[], bool] = Depends(get_db_check),
    redis_check: Callable[[], bool] = Depends(get_redis_check),
) -> HealthResponse:
    """Report dependency reachability. 503 names the failed dependency."""
    db_ok = bool(db_check())
    redis_ok = bool(redis_check())
    ok = db_ok and redis_ok
    if not ok:
        response.status_code = 503
    return HealthResponse(
        status="ok" if ok else "unavailable",
        version=settings.version,
        commit=settings.commit,
        db=db_ok,
        redis=redis_ok,
        capabilities=dict(_CAPABILITIES),
    )


@router.get("/version", response_model=VersionResponse)
def version(settings: Settings = Depends(get_settings)) -> VersionResponse:
    """Report build identity only."""
    return VersionResponse(
        version=settings.version,
        commit=settings.commit,
        build_time=settings.build_time,
    )
