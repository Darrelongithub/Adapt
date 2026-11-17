"""Health and version endpoints.

Health reports booleans only. Capabilities are booleans, never values, so no
configuration or secret can leak through this surface.

Dependency probes are bounded: each runs in a worker thread under
``asyncio.wait_for``, so a blackholed database cannot hold the endpoint open.
A timeout is simply ``False``, which yields 503.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from adapt_engine.api.deps import get_db_check, get_redis_check, get_settings
from adapt_engine.config import Settings

router = APIRouter(prefix="/v1", tags=["health"])

#: Per-probe ceiling. Two probes run concurrently, so health answers inside
#: roughly this budget even when a dependency is unreachable.
PROBE_TIMEOUT_SECONDS = 1.0

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


async def bounded_probe(
    check: Callable[[], bool],
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> bool:
    """Run a blocking check in a thread, bounded by ``timeout``.

    A timeout, or any error from the check, is ``False``. The thread itself
    cannot be cancelled; it is abandoned and dies when the driver gives up.
    """

    def _run() -> bool:
        try:
            return bool(check())
        except Exception:
            return False

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
    except TimeoutError:
        return False


@router.get("/health", response_model=HealthResponse)
async def health(
    response: Response,
    settings: Settings = Depends(get_settings),
    db_check: Callable[[], bool] = Depends(get_db_check),
    redis_check: Callable[[], bool] = Depends(get_redis_check),
) -> HealthResponse:
    """Report dependency reachability. 503 names the failed dependency."""
    db_ok, redis_ok = await asyncio.gather(
        bounded_probe(db_check),
        bounded_probe(redis_check),
    )
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
