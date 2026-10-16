"""FastAPI dependencies: who is this, and are they allowed to ask right now.

Two things are enforced here rather than in each route, because routes forget:

* **Authentication.** An httpOnly, SameSite=Lax cookie holding an opaque token; the
  token is stored only as a hash, and the row keeps an Argon2id-verified session.
  The learner id is *never* taken from a query parameter -- guessing an id must not
  read somebody's learning history.
* **CSRF + rate limits.** Mutations need the double-submit CSRF header, and every
  mutating scope gets a token bucket so a runaway browser tab cannot burn the free
  Gemini quota (which would silently downgrade the demo for the rest of the day).

In ``mode=local`` a loopback caller may pass ``X-ADAPT-Debug-Student`` so a judge
can drive the API with curl. That is the only identity shortcut in the codebase, it
is refused for any non-loopback peer, and it never applies in tunnel mode.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db.engine import get_db
from ..db.models import Student
from ..security import auth
from ..security.middleware import MUTATING, client_ip
from ..services import AppServices, current

log = logging.getLogger("adapt.api.deps")


def services(request: Request | None = None) -> AppServices:
    return current()


def settings(request: Request | None = None) -> Settings:
    return current().settings


def db_session(request: Request) -> Session:
    """One session per request, always closed, never leaked across the await."""
    session = get_db().sessionmaker()
    request.state.db = session
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _cookie_token(request: Request, st: Settings) -> str | None:
    return request.cookies.get(st.cookie_name) or _bearer(request)


def _bearer(request: Request) -> str | None:
    raw = request.headers.get("authorization") or ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip() or None
    return None


def student_from_request(request: Request, s: Session = Depends(db_session)) -> Student:
    st = get_settings()
    debug_id = request.headers.get("x-adapt-debug-student")
    if debug_id and st.mode == "local":
        peer = (client_ip(request) or "") if request.client is None else (request.client.host or "")
        if peer in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1") or peer.startswith("127."):
            student = s.get(Student, debug_id)
            if student is not None and student.deleted_at is None:
                return student
    row = auth.lookup_session(s, _cookie_token(request, st))
    if row is None:
        raise HTTPException(status_code=401, detail="not signed in")
    student = s.get(Student, row.student_id)
    if student is None or student.deleted_at is not None:
        raise HTTPException(status_code=401, detail="learner not found")
    request.state.auth_session = row
    return student


def require_csrf(request: Request, student: Student = Depends(student_from_request)) -> Student:
    """Double-submit CSRF check, on the methods that can change something.

    Only *mutating* methods are checked, which is what the module docstring above has
    always claimed ("Mutations need the double-submit CSRF header"). Checking safe
    methods too was not extra safety, it was a breakage:

    * ``rate(scope)`` depends on this, so every rate-limited **read** inherited the
      requirement. ``GET /content/pack``, ``GET /content/breakdown`` and
      ``GET /lesson/concepts`` answered 403 to any caller that did not know to attach
      a token to a GET — which is what the browser's own ``get()`` helper does, so the
      subject screen's pack/breakdown fetch failed in the running app while the test
      suite passed (``learner["headers"]`` always carries the token, so no test could
      see it).
    * Cross-site reads are already covered: the session cookie is ``SameSite=Lax``, so a
      cross-site subresource request never carries it, and a response an attacker cannot
      read is not a CSRF risk. Quota burn — the one thing a forged GET could do — is what
      the token bucket in ``rate()`` is for.

    ``GET /items/{id}/reveal`` is the read that spoils a question, and it stays
    rate-limited; its own docstring records that reading it persists nothing.
    """
    row = getattr(request.state, "auth_session", None)
    if row is not None and request.method in MUTATING:
        provided = request.headers.get("x-csrf-token") or request.query_params.get("csrf")
        if not auth.check_csrf(row, provided):
            raise HTTPException(status_code=403, detail="csrf token missing or wrong")
    return student


def rate(scope: str):
    """Token bucket per (scope, student) -- see ``security/ratelimit.py``."""

    def _dep(request: Request, student: Student = Depends(require_csrf)) -> Student:
        svcs = current()
        ok, retry = svcs.limiter.allow(scope, f"{scope}:{student.id}")
        if not ok:
            raise HTTPException(
                status_code=429,
                detail={"error": "rate_limited", "retry_after_s": round(retry, 2), "scope": scope},
                headers={"retry-after": str(int(retry) + 1)},
            )
        return student

    return _dep


def issue_login(response: Response, s: Session, student: Student, *, request: Request | None = None) -> dict[str, Any]:
    issued = auth.create_session(s, student.id, ip=(client_ip(request) if request is not None else None), user_agent=(request.headers.get("user-agent") if request is not None else None))
    st = get_settings()
    response.set_cookie(
        st.cookie_name,
        issued.token,
        httponly=True,
        samesite="lax",
        secure=st.mode == "tunnel" or st.require_https,
        max_age=st.session_ttl_hours * 3600,
        path="/",
    )
    response.set_cookie(
        st.csrf_cookie_name,
        issued.csrf,
        httponly=False,  # read by the browser script to echo back in a header
        samesite="lax",
        secure=st.mode == "tunnel" or st.require_https,
        max_age=st.session_ttl_hours * 3600,
        path="/",
    )
    return {
        "student_id": student.id,
        "csrf_token": issued.csrf,
        "expires_at": issued.session.expires_at.isoformat(),
        "mode": st.mode,
    }


def clear_login(response: Response, request: Request) -> None:
    st = get_settings()
    for name in (st.cookie_name, st.csrf_cookie_name):
        response.delete_cookie(name, path="/")


def shared_bank_write(request: Request) -> None:
    """Refuse a *shared*-state mutation from a learner session on a public deployment.

    ``POST /numbas/import`` can add questions the whole bank will serve, and
    ``POST /learning/train`` refits the cohort BKT priors that every mastery number is
    computed from. Both are correct in ``mode=local``, where the person at the terminal
    is the operator and usually the learner. Neither is correct on a tunnel: there, a
    signed-in learner would be able to rewrite what another learner is taught and graded
    on. So a public instance has to be *deliberately* shared -- the same phrase that
    gates guest creation -- or the door stays shut and says so.
    """
    st = get_settings()
    if st.mode != "tunnel":
        return
    from ..security import auth

    if not auth.demo_passphrase_required():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "shared_mutation_locked",
                "note": "this instance is publicly reachable, and this call changes what every learner is taught or measured by",
                "next": "run it on the local instance, or set ADAPT_DEMO_PASSPHRASE and send X-ADAPT-Demo-Passphrase",
            },
        )
    if not auth.check_demo_passphrase(request.headers.get("x-adapt-demo-passphrase")):
        raise HTTPException(
            status_code=403,
            detail={"error": "demo_passphrase_required", "next": "send X-ADAPT-Demo-Passphrase: <the phrase the operator set>"},
        )


def llm_guard(request: Request) -> None:
    """Shared 'don't melt the free tier' check for expensive endpoints."""
    svcs = current()
    llm = getattr(svcs.ai2, "llm", None)
    if llm is None:
        return
    info = llm.available() if callable(getattr(llm, "available", None)) else {}
    head = (info or {}).get("headroom") or {}
    if info.get("configured") and int(head.get("calls_left") or 0) <= 0:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "model_budget_exhausted",
                "note": (
                    "the free Gemini budget for today is spent. Teaching continues from the "
                    "local tutor and your own materials; nothing is lost, it is just less fluent"
                ),
                "headroom": head,
            },
        )


__all__ = [
    "db_session",
    "shared_bank_write",
    "issue_login",
    "llm_guard",
    "rate",
    "require_csrf",
    "services",
    "settings",
    "student_from_request",
]
