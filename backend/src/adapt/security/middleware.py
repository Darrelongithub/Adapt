"""HTTP middleware: headers, host/origin checks, body caps, rate limits, audit hook.

The blueprint requires HTTPS for the demo "not merely assumed because the app uses
a web framework", so this module *enforces* the transport posture rather than
describing it:

* ``TrustedHostMiddleware`` rejects a Host header that is not the local dev host,
  the tunnel host, or an explicitly allowed extra -- which also blocks the classic
  host-header poisoning/redirect trick against a tunnel.
* ``SecurityHeadersMiddleware`` sets CSP (no inline scripts, no third-party
  origins), HSTS when TLS is on, nosniff, referrer and permissions policy
  (camera/microphone restricted to the app origin -- the mic is used by voice),
  and frame denial.
* ``LimitsMiddleware`` caps request size and rate before any handler runs.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import Settings, get_settings
from .ratelimit import Limiter, realtime_guard
from .secrecy import ConfigurationSecrecyMiddleware

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

#: Routes where one request can set off a *long* piece of work: a model call, a teaching
#: turn, a graded teach-back, a scene. The token buckets in ``security/ratelimit.py``
#: bound how *often* these may start; this list is what the in-flight guard bounds, and
#: the two are different failures. Twenty simultaneous requests at 3/min each pass every
#: bucket in the file and still put twenty model calls on the free tier at once.
REALTIME_PREFIXES: tuple[str, ...] = (
    "/api/v1/tutor",
    "/api/v1/subjects",
    "/api/v1/lessons",
    "/api/v1/voice",
    "/api/v1/attempts",
    "/api/v1/baseline",
    "/api/v1/adaptations",
)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
# e2b sandbox previews and localtunnel/cloudflare hosts.
HOST_PATTERNS = (
    re.compile(r"^\d+-[a-z0-9-]+\.e2b\.app$"),  # Arena live preview
    re.compile(r"^.*\.loca\.lt$"),  # localtunnel
    re.compile(r"^.*\.trycloudflare\.com$"),  # cloudflared quick tunnel
    re.compile(r"^.*\.workers\.dev$"),
)

CSP_NO_INLINE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "  # Next injects critical styles; scoped to style only
    "img-src 'self' data: blob:; "
    "media-src 'self' blob: data:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "frame-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


# Starlette's TestClient sends this Host. Trusting it only makes sense where there
# is no network exposure to defend, so it is gated on local mode (see doctor/init).
TESTING_HOSTS = {"testserver", "testclient"}


def host_allowed(host: str | None, settings: Settings) -> bool:
    if not host:
        return False
    h = host.split("://")[-1].split(":")[0].strip().lower().rstrip(".")
    if h in LOCAL_HOSTS or h in settings.origins:
        return True
    if settings.mode == "local" and h in TESTING_HOSTS:
        return True
    origin_host = {o.split("://")[-1].split(":")[0].split("/")[0] for o in settings.origins}
    if h in origin_host:
        return True
    if h in set(settings.extra_allowed_hosts):
        return True
    return any(p.match(h) for p in HOST_PATTERNS)


class SecurityHeadersMiddleware:
    """ASGI boundary for security headers and configuration redaction.

    This is pure ASGI rather than ``BaseHTTPMiddleware`` so the same outer boundary
    sees HTTP responses and WebSocket frames.  The secrecy wrapper is installed here
    because this middleware is already registered around every route in ``app.py``.
    """

    def __init__(self, app, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.app = ConfigurationSecrecyMiddleware(app, self.settings)

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def with_security_headers(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                response_headers = MutableHeaders(raw=headers)
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["X-Frame-Options"] = "DENY"
                response_headers["Referrer-Policy"] = "no-referrer"
                response_headers["Cross-Origin-Opener-Policy"] = "same-origin"
                response_headers["Cross-Origin-Resource-Policy"] = "same-origin"
                response_headers["Permissions-Policy"] = (
                    "camera=(), geolocation=(), microphone=(self)"
                )
                response_headers["Content-Security-Policy"] = CSP_NO_INLINE
                response_headers["Cache-Control"] = "no-store"
                request_id = (scope.get("state") or {}).get("request_id")
                response_headers["X-Request-Id"] = request_id or uuid.uuid4().hex[:12]
                if self.settings.require_https:
                    response_headers["Strict-Transport-Security"] = "max-age=300; includeSubDomains"
                message = {**message, "headers": response_headers.raw}
            await send(message)

        await self.app(scope, receive, with_security_headers)


class TrustedHostMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings | None = None) -> None:
        super().__init__(app)
        self.settings = settings or get_settings()

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not host_allowed(request.headers.get("host"), self.settings):
            return JSONResponse(
                {
                    "detail": "untrusted host",
                    "hint": "set ADAPT_EXTRA_ALLOWED_HOSTS or run the demo script",
                },
                status_code=400,
            )
        if self.settings.require_https and request.url.scheme == "http":
            fwd = request.headers.get("x-forwarded-proto", "")
            if fwd and fwd.split(",")[0].strip() != "https":
                return JSONResponse({"detail": "https required"}, status_code=403)
        return await call_next(request)


class LimitsMiddleware(BaseHTTPMiddleware):
    """Body-size cap + per-IP rate limit + in-flight cap, applied before handlers."""

    def __init__(self, app, *, settings: Settings, limiter: Limiter) -> None:
        super().__init__(app)
        self.settings = settings
        self.limiter = limiter

    @staticmethod
    def is_realtime(path: str) -> bool:
        return path.startswith(REALTIME_PREFIXES)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid.uuid4().hex[:12]
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > self.settings.max_body_bytes:
            return JSONResponse(
                {"detail": f"request too large (limit {self.settings.max_body_bytes} bytes)"},
                status_code=413,
            )
        ip = client_ip(request, settings=self.settings)
        ok, retry = self.limiter.allow("ip", ip)
        if not ok:
            wait = round(retry, 2)
            # The per-scope refusals in `deps.py` answer 429 with a structured detail, and
            # clients (the demo, the probe, any page that wants to back off) read the wait
            # time out of it. This one used to answer with a bare sentence and the number
            # beside it, so the same client parsed one and missed the other. Both shapes now
            # agree; the top-level key and the sentence stay, for anything already reading them.
            return JSONResponse(
                {
                    "detail": {
                        "error": "rate_limited",
                        "retry_after_s": wait,
                        "scope": "ip",
                        "message": "rate limit exceeded - this connection is asking too often",
                    },
                    "retry_after_s": wait,
                },
                status_code=429,
                headers={"Retry-After": str(max(1, int(retry) + 1))},
            )
        # A real-time *mutation* holds a slot for as long as it takes. Everything else -- reads,
        # static files, the health probe -- is never counted, because a page loading eight
        # assets is not a spike and a guard that says it is is a guard people turn off.
        key = ""
        # Mutating requests only. A GET on a real-time prefix is a read of the same area --
        # the voice capabilities, a lesson's frames, a run's status -- and counting it would
        # mean a page that loads one card gets paced for it. What the guard is for is work
        # that can start a model call, and that is a mutating request.
        #
        # The guard is looked up per request rather than captured in ``__init__``: it is the
        # process-wide counter, and a middleware holding an *old* instance of it would refuse
        # against numbers nobody else could see (which is exactly the bug that made
        # "/quotas" report a bucket count that had nothing to do with the buckets refusing).
        if request.method in MUTATING and self.is_realtime(request.url.path):
            key = f"rt:{ip}"
            allowed, reason, wait = realtime_guard(self.settings).begin(key)
            if not allowed:
                broker = "already in flight" if reason == "in_flight" else "arrived too fast"
                return JSONResponse(
                    {
                        "detail": {
                            "error": "too_many_concurrent",
                            "reason": reason,
                            "retry_after_s": round(wait, 2),
                            "scope": "realtime",
                            "message": (
                                "too many of your requests are being answered at once "
                                f"({broker}) - wait for the ones already running instead of sending more"
                            ),
                        },
                        "retry_after_s": round(wait, 2),
                    },
                    status_code=429,
                    headers={"Retry-After": str(max(1, int(wait) + 1))},
                )
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            raise
        finally:
            if key:
                # In `finally` so a handler that raised still frees its slot: a leaked
                # slot would permanently lower the cap for that key, which is the kind
                # of bug that only shows up under load.
                realtime_guard(self.settings).end(key)
        response.headers["X-Elapsed-Ms"] = str(int((time.monotonic() - started) * 1000))
        return response


class BodySizeGuard:
    """Streaming cap: a client that lies about content-length still cannot flood us.

    Wrapped by the telemetry/voice handlers that read raw bodies.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit

    async def read(self, request: Request) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > self.limit:
                raise ValueError(f"body exceeds {self.limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)


def _ip_text(value: object) -> str:
    text = str(value or "").strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text


def client_ip(request: Request, *, settings: Settings | None = None) -> str:
    """Direct peer by default; forwarded addresses only across a trusted proxy chain."""
    peer = _ip_text(request.client.host if request.client else "unknown")
    st = settings or get_settings()
    trusted = {_ip_text(value) for value in st.trusted_proxies}
    if peer not in trusted:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    chain = []
    for part in forwarded.split(","):
        try:
            chain.append(str(ipaddress.ip_address(part.strip())))
        except ValueError:
            continue
    # A proxy appends the address it observed. Walk back through trusted proxies so a
    # caller-supplied leftmost value cannot become the identity used by auth buckets.
    for address in reversed(chain):
        if address not in trusted:
            return address
    return chain[0] if chain else peer


def origin_ok(request: Request, settings: Settings) -> bool:
    """CSRF-adjacent check for cookie-authenticated POSTs from a browser."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # non-browser clients (curl, tests, native) send no Origin
    allowed = (
        {o.rstrip("/") for o in settings.origins}
        | {f"https://{h}" for h in settings.extra_allowed_hosts}
        | {f"http://{h}" for h in settings.extra_allowed_hosts}
    )
    origin = origin.rstrip("/")
    if origin in allowed:
        return True
    # Same-origin requests (the Next proxy) forward the tunnel origin; accept any
    # origin whose host we already accept as a Host.
    host = origin.split("://")[-1]
    return host_allowed(host, settings)


def json_preview(body: bytes, limit: int = 400) -> str:
    """Short, PII-free preview for the audit log."""
    try:
        text = body.decode("utf-8", "replace")
        data = json.loads(text)
    except Exception:
        return ""
    if isinstance(data, dict):
        keys = sorted(str(k) for k in data.keys())[:8]
        return "keys=" + ",".join(keys)
    return f"type={type(data).__name__}"


def audit_event(
    request: Request,
    *,
    action: str,
    outcome: str = "ok",
    actor: str | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    """Queue an audit row for the request-scoped DB session to flush at the end.

    Kept off the response path so logging can never block a learner's turn.
    """
    pending: list[dict[str, object]] = getattr(request.state, "audit", [])
    pending.append(
        {
            "action": action,
            "outcome": outcome,
            "actor_id": actor,
            "detail": detail or {},
            "ip_hash": None,
        }
    )
    request.state.audit = pending


def is_api(path: str) -> bool:
    return path.startswith("/api") or path.startswith("/ws")
