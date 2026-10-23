"""The FastAPI app: one origin for the browser, all of the logic for the backend.

Wiring decisions worth naming, because the blueprint asked for them:

* The browser talks only to this origin. ``frontend/server.mjs`` proxies ``/api`` and
  ``/ws`` here, so no learner's machine ever needs to know a port for the API, and the
  tunnel link shown to a judge is one URL, not two.
* Every response carries the security headers; every request carries an origin/host
  check, because in tunnel mode this app is briefly *public* and DNS-rebinding is the
  specific attack that a localhost-plus-tunnel setup invites.
* Startup runs Alembic before any ORM reads, adopting older unversioned
  ``create_all`` databases without discarding learner history.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..security.ratelimit import build_limiter
from ..security.middleware import (
    LimitsMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)
from ..services import attach, build
from .routes import (
    adaptations,
    auth,
    baseline,
    check_in,
    content,
    content_prepare,
    curriculum,
    evidence,
    integration,
    journey,
    learning,
    lesson,
    lessons,
    study,
    system,
    tests as tests_routes,
    tutor,
)
from .ws import router as ws_router

log = logging.getLogger("adapt.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    st = get_settings()
    logging.basicConfig(
        level=getattr(logging, str(st.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # One limiter for the whole process: the middleware that refuses flooders and the
    # providers that pace Gemini/OpenAlex share these buckets, so /api/v1/quotas can
    # report what is really in force.
    svcs = build(st, limiter=getattr(app.state, "limiter", None))
    attach(svcs)
    svcs.db.upgrade_schema()
    from ..services import sessions as sessions_service

    with svcs.db.session() as s:
        seeded = sessions_service.ensure_curriculum(s)
    # The interactive engine's built-in scenes, seeded by content id so a restart neither
    # duplicates them nor churns the ids that LessonRun rows point at.
    from ..services import lessons as lessons_service

    with svcs.db.session() as s:
        seeded_scenes = lessons_service.ensure_scenes(s)
    # Key check before anything can serve a learner: see crypto/fields.verify_stored_data.
    from ..crypto.fields import verify_stored_data

    check = verify_stored_data(svcs.db.engine)
    if check["missing"]:
        # Not a key problem, and saying so matters: the encrypted-column check reads through
        # the model, and a database made by an earlier build is missing columns this build
        # declares. SQLite answers the unknown identifier with its own name as a string
        # literal, so without this the operator sees "the key cannot open 4 values" and goes
        # looking for a key that is fine.
        raise RuntimeError(
            "startup refused: this database was made by an earlier build and is missing "
            f"{len(check['missing'])} column(s) this build declares: {', '.join(check['missing'][:4])}. "
            "Do not delete the database or blindly stamp a revision; check the migration "
            "history (see README, Migrations)."
        )
    if check["checked"] and check["unreadable"]:
        # Spelled out here rather than interpolated, because an exception message about key
        # material must never touch the key itself -- and a nested f-string quoting a secret
        # path is how secrets end up in logs.
        key_path = st.keyfile_path
        where = str(key_path) if key_path.exists() else f"no keyfile at {key_path} (a throwaway key was generated)"
        cols = ", ".join(str(c) for c in check["columns_unreadable"])
        raise RuntimeError(
            "startup refused: the key this process loaded cannot open "
            f"{check['unreadable']} of {check['checked']} encrypted value(s) it sampled. "
            f"Columns affected: {cols}. Cipher said: {check['note']}. Key source: {where}. "
            "Restore the key file that was present when this database was written, or start "
            "with ADAPT_DB_KEY set to that deployment's base64 key. Encrypted rows cannot be "
            "recovered without it, and this app will not silently serve a half-readable database."
        )
    if check["checked"]:
        log.info("startup: key check ok (%s encrypted value(s) across %s column(s) read)", check["checked"], check["columns"])
    log.info(
        "startup: db=%s encrypted=%s llm=%s curriculum=%s items, scenes=%s (%s new)",
        st.db_file,
        svcs.db.file_encrypted(),
        "gemini" if st.gemini_api_key else "local-only",
        seeded["items_total"],
        seeded_scenes["total"],
        seeded_scenes["created"],
    )
    if st.mode == "tunnel" and not st.youtube_api_key:
        log.info("YouTube search disabled (no key); the other three providers still run")
    try:
        yield
    finally:
        await svcs.aclose()


def create_app() -> FastAPI:
    st = get_settings()
    app = FastAPI(
        title="ADAPT",
        version="0.1.0",
        description=(
            "Adaptive learning with two cooperating AIs: a Learning Intelligence that measures and "
            "proposes, and an Educational Intelligence that teaches. Persistent changes require the "
            "learner's answer; nothing here is a paid service."
        ),
        lifespan=lifespan,
        docs_url="/api/docs" if st.debug else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    origins = sorted({st.frontend_origin, *(st.allowed_origins or [])})
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["content-type", "x-csrf-token", "x-adapt-demo-passphrase", "x-adapt-debug-student"],
        expose_headers=["x-tts-sample-rate"],
        # Not "*" with credentials: that combination is the bug, not the shortcut.
        allow_origin_regex=None,
    )
    # Order matters: the outermost middleware is added last. Security headers go on
    # every response, including a rejection from the host or rate-limit checks, so
    # they must be outside both.
    limiter = build_limiter(multiplier=st.rate_limit_multiplier, demo_mode=st.demo_mode)
    app.add_middleware(LimitsMiddleware, settings=st, limiter=limiter)
    app.add_middleware(TrustedHostMiddleware, settings=st)
    app.add_middleware(SecurityHeadersMiddleware, settings=st)
    if st.demo_mode:
        from ..security.demo_capacity import DemoCapacityMiddleware

        app.add_middleware(DemoCapacityMiddleware)
    app.state.limiter = limiter

    from .routes import lesson_plan, subjects

    for r in (
        auth.router,
        learning.router,
        lessons.router,
        lesson.router,
        content.router,
        # Part B's prepare job routes. The module's prefix already carries the full
        # /api/v1/content/prepare path, so it is included without an extra prefix.
        content_prepare.router,
        adaptations.router,
        journey.router,
        lesson_plan.router,
        subjects.router,
        # Round 9: the check-in and the curriculum scan/approval gate.
        check_in.router,
        curriculum.router,
        tests_routes.router,
        study.router,
        tutor.router,
        baseline.router,
        evidence.router,
        # The external integration door (POST /api/v1/integration/adapt): one
        # stateless, model-free route other apps call to plug Adapt in as an
        # engine. Self-contained module; see docs/INTEGRATION.md.
        integration.router,
        system.router,
        ws_router,
    ):
        app.include_router(r)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Tell the caller what was wrong without echoing the submitted values back:
        # those values may be a learner's answer text.
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_failed",
                "fields": [
                    {"loc": [str(x) for x in (e.get("loc") or [])], "msg": (e.get("msg") or "")[:120], "type": e.get("type")}
                    for e in exc.errors()[:12]
                ],
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        log.exception("unhandled error on %s (request_id=%s)", request.url.path, rid)
        body: dict[str, Any] = {"error": "internal_error", "request_id": rid}
        if st.mode == "local":
            # The person on the other end of this socket is the operator, so they get the
            # exception. On a public tunnel they get an id to look up in the log instead:
            # a str(e) from a DB or provider call is how file paths and half-sanitised
            # learner text end up in somebody else's HTTP response.
            body["detail"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        else:
            body["note"] = (
                "the server hit an error it did not expect. The operator's log has the detail "
                "for this request id; nothing about your answer was lost by this request failing"
            )
        return JSONResponse(status_code=500, content=body)

    @app.get("/api")
    def root() -> dict[str, Any]:
        """The front door: what the loop is, and which call belongs to each beat.

        ``routes`` is derived from the OpenAPI document rather than written out here,
        so an index that drifts from the real API is not possible.
        """
        loop = {
            "test": "POST /api/v1/attempts",
            "observe": "POST /api/v1/sessions/{session_id}/events",
            "understand": "POST /api/v1/analyze",
            "personalize": "GET /api/v1/adaptations/pending, then POST /api/v1/adaptations/{proposal_id}/decision",
            "learn": "GET /api/v1/practice, GET /api/v1/review/due",
            "repeat": "GET /api/v1/journey",
        }
        try:
            paths = (app.openapi() or {}).get("paths", {})
            routes = sorted(f"{method.upper()} {path}" for path, ops in paths.items() for method in ops)
        except Exception:  # pragma: no cover - a broken schema must not break the index
            routes = []
        return {
            "name": "ADAPT",
            "loop": list(loop),
            "how_to_walk_the_loop": loop,
            "docs": "/api/openapi.json",
            "health": "/api/v1/health",
            "websocket": "/ws/voice",
            "privacy": "/api/v1/privacy",
            "mode": get_settings().mode,
            "routes": routes,
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"ok": "true"}

    return app


app = create_app()
