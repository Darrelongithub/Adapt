"""Regression: forwarding headers cannot choose a direct client's rate-limit identity."""

from types import SimpleNamespace

from starlette.requests import Request

from adapt.config import Settings
from adapt.security.middleware import client_ip


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "client": (peer, 12345), "headers": headers})


def test_direct_peer_ignores_a_spoofed_forwarding_header():
    request = _request("203.0.113.10", "198.51.100.77")
    assert client_ip(request, settings=SimpleNamespace(trusted_proxies=[])) == "203.0.113.10"


def test_trusted_proxy_uses_the_first_untrusted_address_from_the_right():
    settings = SimpleNamespace(trusted_proxies=["192.0.2.10", "192.0.2.11"])
    # The client supplied the leftmost address; the proxy appended the address it observed.
    request = _request("192.0.2.10", "198.51.100.77, 192.0.2.11, 203.0.113.10")
    assert client_ip(request, settings=settings) == "203.0.113.10"


def test_trusted_proxy_control_and_invalid_header_fallback():
    settings = SimpleNamespace(trusted_proxies=["192.0.2.10"])
    assert client_ip(_request("192.0.2.10", "198.51.100.77"), settings=settings) == "198.51.100.77"
    assert client_ip(_request("192.0.2.10", "not-an-ip"), settings=settings) == "192.0.2.10"


def test_proxy_setting_accepts_csv_and_json(monkeypatch):
    monkeypatch.setenv("ADAPT_TRUSTED_PROXIES", "192.0.2.10, ::1")
    assert Settings(_env_file=None).trusted_proxies == ["192.0.2.10", "::1"]
    monkeypatch.setenv("ADAPT_TRUSTED_PROXIES", '["192.0.2.10", "192.0.2.11"]')
    assert Settings(_env_file=None).trusted_proxies == ["192.0.2.10", "192.0.2.11"]


def test_serve_preserves_the_socket_peer_for_the_application_allowlist(monkeypatch):
    import uvicorn
    from adapt.cli import serve

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: captured.update(kwargs))
    serve(host="127.0.0.1", port=9000, reload=False)
    assert captured["proxy_headers"] is False


def test_host_command_also_preserves_the_socket_peer():
    from adapt.hosting import uvicorn_command

    command = uvicorn_command(SimpleNamespace(log_level="INFO"), host="0.0.0.0", port=8000)
    assert "--no-proxy-headers" in command
    assert "--proxy-headers" not in command
