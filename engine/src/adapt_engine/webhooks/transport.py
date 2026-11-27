"""The one place a webhook leaves the process.

Everything about this module is a boundary decision:

* **One implementation, no client library.** ``urllib.request`` with a timeout,
  so there is no dependency to audit and no connection pool to leak.
* **No redirects.** A 302 to another host is an egress bypass: an attacker who
  can create a connection could point our sender at an internal address and
  have the redirect do the rest. The redirect handler raises instead.
* **https only**, re-checked here rather than trusted from the row.
* **A bounded timeout**, because a hanging receiver must not hang a worker.

The class is only constructed when ``ADAPT_WEBHOOK_DELIVERY`` is on. With it off
deliveries are recorded and not attempted — see ``DECISIONS.md`` §16: AGENT_M1
§0 forbids network egress and §1 requires webhooks, and the switch is how both
are true at once in this sandbox.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from adapt_engine.webhooks.dispatch import TransportResult

DEFAULT_TIMEOUT_SECONDS = 5.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect. A delivery target is a decision, not a hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused ({code})", headers, fp)


class UrllibTransport:
    """POSTs a signed body to an https URL and reports the outcome."""

    def __init__(self, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    def post(self, url: str, headers: dict[str, str], body: bytes) -> TransportResult:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("a webhook target must be an absolute https URL")

        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        started = time.perf_counter()
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return TransportResult(
                    status_code=response.status,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
        except urllib.error.HTTPError as exc:
            # A 4xx/5xx *is* the receiver answering; only the status is kept.
            return TransportResult(
                status_code=exc.code, duration_ms=int((time.perf_counter() - started) * 1000)
            )
        except TimeoutError as exc:
            raise TimeoutError("webhook target timed out") from exc
