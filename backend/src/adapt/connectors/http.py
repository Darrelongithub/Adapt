"""One guarded HTTP path for every connector.

Rules, enforced here rather than per caller:

* **Allowlisted hosts only.** A connector may only talk to the host its spec
  declares; anything else raises before a socket opens. This is what makes
  "no new outbound hosts besides the optional connectors" a property of the
  code instead of a promise in a document.
* **Short timeouts, silent failure.** A connector that is slow or down
  returns ``None``; the caller falls back to local sources. A lesson never
  waits on the internet.
* **Cached in the encrypted store.** Successful responses are cached in
  ``http_cache`` (the table the resource providers already use) so a scan
  fetched once keeps working offline and a lesson stays reproducible.
* **No learner data.** Callers pass subject/theme strings only; this module
  never sees a student id and adds no identifying headers beyond a
  descriptive user agent (Wikimedia's API etiquette asks for one).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

log = logging.getLogger("adapt.connectors.http")

USER_AGENT = "ADAPT-learning-app/1.0 (curriculum outline lookup; local install)"

#: Transport seam for tests: ``(method, url, headers, params, timeout) -> (status, json|None)``.
Transport = Callable[[str, str, dict[str, str], dict[str, Any], float], "tuple[int, Any]"]


class ConnectorError(RuntimeError):
    """A connector refused to run (bad host, bad configuration). Never fatal."""


def _default_transport(method: str, url: str, headers: dict[str, str], params: dict[str, Any], timeout: float) -> tuple[int, Any]:
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.request(method, url, headers={**headers, "user-agent": USER_AGENT}, params=params)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None


def _default_post_transport(
    method: str, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
) -> tuple[int, Any]:
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.request(
            method,
            url,
            headers={"content-type": "application/json", **headers, "user-agent": USER_AGENT},
            json=body,
        )
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None


def auth_headers(api_key: str | None, header_name: str = "Authorization") -> dict[str, str]:
    """Build safe header-only credentials; keys never enter URLs or query strings."""
    secret = str(api_key or "").strip()
    if not secret:
        return {}
    name = str(header_name or "Authorization").strip() or "Authorization"
    if name.lower() == "authorization":
        if secret.lower().startswith(("bearer ", "basic ", "token ")):
            return {"authorization": secret}
        return {"authorization": f"Bearer {secret}"}
    return {name: secret}


def cache_key(connector: str, url: str, params: dict[str, Any]) -> str:
    raw = json.dumps({"c": connector, "u": url, "p": params}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def guarded_get_json(
    *,
    connector: str,
    allowed_host: str,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 8.0,
    cache_session: Any = None,
    cache_ttl_hours: float = 24.0 * 7,
    transport: Transport | None = None,
) -> tuple[Any, str]:
    """GET JSON from one allowlisted host. Returns ``(payload|None, source)``.

    ``source`` is ``"cache"``, ``"network"`` or ``"unavailable"`` — callers
    surface it so "shown from a saved copy" is sayable. Any failure (DNS,
    timeout, non-200, non-JSON) is ``(None, "unavailable")``, never an
    exception: a connector being down must read exactly like a connector
    being off.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != allowed_host:
        raise ConnectorError(f"connector {connector} may only call https://{allowed_host}")
    params = dict(params or {})
    key = cache_key(connector, url, params)

    if cache_session is not None:
        from ..db.models import HttpCacheEntry, utcnow

        row = cache_session.get(HttpCacheEntry, key)
        if row is not None and row.expires_at is not None and row.expires_at > utcnow() and row.status == "ok":
            row.hit_count = int(row.hit_count or 0) + 1
            return row.payload, "cache"

    call = transport or _default_transport
    try:
        status, payload = call("GET", url, dict(headers or {}), params, max(0.5, float(timeout_s)))
    except Exception as exc:  # a connector outage is a fallback, not an error page
        log.debug("connector %s unreachable (%s)", connector, type(exc).__name__)
        return None, "unavailable"
    if status != 200 or payload is None:
        log.debug("connector %s answered %s", connector, status)
        return None, "unavailable"

    if cache_session is not None:
        from ..db.models import HttpCacheEntry, utcnow

        now = utcnow()
        row = cache_session.get(HttpCacheEntry, key)
        if row is None:
            cache_session.add(
                HttpCacheEntry(
                    key=key,
                    provider=connector[:16],
                    request_url=url[:2000],
                    payload=payload,
                    fetched_at=now,
                    expires_at=now + timedelta(hours=max(1.0, cache_ttl_hours)),
                    status="ok",
                )
            )
        else:
            row.payload = payload
            row.fetched_at = now
            row.expires_at = now + timedelta(hours=max(1.0, cache_ttl_hours))
            row.status = "ok"
    return payload, "network"


def guarded_post_json(
    *,
    connector: str,
    allowed_host: str,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_s: float = 15.0,
    transport: Transport | None = None,
) -> tuple[Any, str]:
    """POST JSON to one allowlisted HTTPS host. Returns ``(payload|None, source)``.

    Used exclusively by Slot 3's custom adaptation API when the operator plugs in
    an HTTPS endpoint. Never follows redirects, enforces the exact ``allowed_host``,
    and fails closed to ``(None, "unavailable")`` on any network or HTTP error.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != allowed_host:
        raise ConnectorError(f"connector {connector} may only call https://{allowed_host}")
    call = transport or _default_post_transport
    try:
        status, payload = call("POST", url, dict(headers or {}), dict(body or {}), max(0.5, float(timeout_s)))
    except Exception as exc:
        log.debug("connector %s unreachable (%s)", connector, type(exc).__name__)
        return None, "unavailable"
    if status != 200 or payload is None:
        log.debug("connector %s answered %s", connector, status)
        return None, "unavailable"
    return payload, "network"
