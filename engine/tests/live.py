"""A real socket in front of the real app — the only way to see ordering.

FastAPI runs a ``yield`` dependency's teardown **after** the response bytes are on
the wire (DECISIONS §24.5). ``TestClient`` cannot observe that: it collects the body
only after the app callable returns, so every teardown has finished before a test
sees anything. A suite made entirely of ``TestClient`` calls is therefore blind to
the question "was this response true when it was sent?", which is the question that
turned a green run and a red run at the same commit into five hours of work.

Two pieces, and both matter:

* :func:`live_server` — uvicorn on a loopback port, exactly as ``adapt console``
  and ``adapt sandbox`` run it.
* :func:`widen_the_window` — holds the door open between "response sent" and
  "transaction committed", so a race that normally lasts one commit becomes a
  third of a second and a test can stand in it. Without it a test of this ordering
  is a coin flip that lands green almost every time.

Nothing here is console- or CLI-specific: any route can be exercised through it.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from adapt_engine.auth import deps as auth_deps


@contextlib.contextmanager
def live_server(app: Any) -> Iterator[str]:
    """Serve ``app`` on a loopback port and yield its base URL."""
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the app did not start within 30 seconds"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def call(
    base: str,
    path: str,
    *,
    key: str,
    body: dict[str, Any] | list[Any] | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """One request, as a client would make it. Returns ``(status, parsed body)``."""
    payload = None if body is None else json.dumps(body).encode()
    all_headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    all_headers.update(headers or {})
    request = urllib.request.Request(
        f"{base}{path}",
        data=payload,
        method=method or ("GET" if body is None else "POST"),
        headers=all_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


@contextlib.contextmanager
def widen_the_window(monkeypatch, delay: float = 0.30) -> Iterator[None]:
    """Delay the commit that ``tenant_transaction`` does in the dependency teardown.

    The delay sits between the handler returning and the transaction committing, so
    it is exactly the window a real client can drive through. A route that commits
    before it returns is unaffected; a route that lets the teardown do it is caught
    holding a response it has not stored.
    """
    real = auth_deps.tenant_transaction

    @contextlib.contextmanager
    def slow(engine, tenant_id):
        with real(engine, tenant_id) as conn:
            yield conn
            time.sleep(delay)

    monkeypatch.setattr(auth_deps, "tenant_transaction", slow)
    yield
