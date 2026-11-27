"""The console shell: three screens, served as static files by the engine.

Deliberately a static page rather than a rendered app: the console reads four
endpoints with the tenant's admin key, and a page that ships its own server-side
data access would need that key on the server — one more place for it to leak.
`INTEGRATION.md` §6A records that `/console` is not part of the API contract.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

HERE = Path(__file__).parent

router = APIRouter(tags=["console"], include_in_schema=False)

FILES = {
    "": ("index.html", "text/html; charset=utf-8"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/data-map.json": ("data-map.json", "application/json"),
}


@router.get("/console", include_in_schema=False)
@router.get("/console/", include_in_schema=False)
def console_index() -> FileResponse:
    return FileResponse(HERE / "index.html", media_type="text/html; charset=utf-8")


@router.get("/console/{asset}", include_in_schema=False)
def console_asset(asset: str) -> FileResponse:
    """Only the three declared assets: no traversal, no directory listing."""
    entry = FILES.get(f"/{asset}")
    if entry is None:
        from adapt_engine.errors import AdaptError

        raise AdaptError(
            code="route_unknown",
            message="no such console asset",
            status_code=404,
            retryable=False,
        )
    name, media = entry
    return FileResponse(HERE / name, media_type=media)
