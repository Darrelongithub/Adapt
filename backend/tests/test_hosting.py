"""``adapt host``: the LAN trust path, and the refusal that has to survive it.

What is worth testing about a command whose whole job is to widen what the API trusts:

1. **The widening is exactly one host.** With hosting's allowlist in force a LAN-style
   ``Host`` + ``Origin`` is accepted -- and the neighbouring address, a foreign domain and a
   suffix that merely *contains* the allowed address are all still refused. A LAN host that
   silently became a subnet, or a wildcard, would pass a naive "does it work?" check and fail
   this file.
2. **The environment is merged, not replaced.** The operator's ``.env`` keeps every entry it
   had; the multiplier only ever goes up; unrelated keys (the model key!) are passed through
   untouched and never printed.
3. **The pieces that cannot be exercised here are still honest.** Which address is advertised,
   which tunnel is chosen, which URL is read out of a provider's log, and what the banner says.
   A real tunnel, a real second device and a real phone microphone are not in this sandbox --
   see docs/UNVERIFIED.md for the list a human still has to try.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from adapt import hosting

LAN_IP = "192.168.1.50"
NEIGHBOUR_IP = "192.168.1.51"


def hosted_env(monkeypatch, settings, *, lan_ip: str = LAN_IP, web_port: int = 3000, extra_hosts=(), origins=()):
    """Put the app in the state ``adapt host`` puts it in, using the command's own function.

    The allowlist is not hand-written here on purpose: if ``build_api_env`` stopped emitting a
    host the middleware honours, these tests would fail rather than quietly agree with
    themselves.
    """
    env = hosting.build_api_env(
        {},
        lan_ip=lan_ip,
        web_port=web_port,
        api_port=8080,
        extra_hosts=list(extra_hosts),
        origins=list(origins),
    )
    monkeypatch.setattr(settings, "extra_allowed_hosts", hosting.as_list(env["ADAPT_EXTRA_ALLOWED_HOSTS"]))
    monkeypatch.setattr(settings, "allowed_origins", hosting.as_list(env["ADAPT_ALLOWED_ORIGINS"]))
    return env


# --------------------------------------------------------------------------- #
# 1. the widening -- and the refusal that has to survive it
# --------------------------------------------------------------------------- #


def test_a_lan_host_and_origin_are_accepted_when_hosting_is_on(client, settings, services, monkeypatch):
    """A phone on the wifi: ``Host: 192.168.1.50:3000``, ``Origin: http://192.168.1.50:3000``.

    The Host is what the API sees when someone opens the web server's address directly; the
    Origin is what the *web server's proxy* forwards on every mutating request (it rewrites
    Host, not Origin). Both have to be accepted for a room to use the app at all.
    """
    hosted_env(monkeypatch, settings)
    assert client.get("/healthz", headers={"host": f"{LAN_IP}:8080"}).status_code == 200
    created = client.post(
        "/api/v1/students",
        json={"display_name": "Room Guest", "as_guest": True},
        headers={"host": f"{LAN_IP}:3000", "origin": f"http://{LAN_IP}:3000"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["student_id"].startswith("stu_")


def test_the_same_requests_are_refused_without_hosting(client, settings, monkeypatch):
    """The other half of the claim: nothing above is accepted by a default deployment."""
    monkeypatch.setattr(settings, "extra_allowed_hosts", [])
    refused = client.get("/healthz", headers={"host": f"{LAN_IP}:8080"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == "untrusted host"


def test_the_untrusted_host_refusal_survives_hosting(client, settings, monkeypatch):
    """One exact address, not a subnet and not a wildcard.

    ``192.168.1.51`` is the laptop next door on the same wifi; ``192.168.1.50.attacker.net``
    is the classic suffix trick (the allowed string *is* in the Host header, and the request
    is still not for us); ``evil.example.com`` is the DNS-rebinding case the whole middleware
    exists for. All three are refused while hosting is on.
    """
    hosted_env(monkeypatch, settings)
    for host in (
        f"{NEIGHBOUR_IP}:8080",
        f"{LAN_IP}.attacker.net",
        "attacker.net.",
        "evil.example.com",
        "evil.localtunnel.me",  # looks like a tunnel, is not one of the two that are trusted
    ):
        refused = client.get("/healthz", headers={"host": host})
        assert refused.status_code == 400, f"Host {host!r} was trusted"
        assert refused.json()["detail"] == "untrusted host"
        assert "ADAPT_EXTRA_ALLOWED_HOSTS" in refused.json()["hint"]
    # ...and localhost still works, so the refusal is about the Host and not a blanket 400.
    assert client.get("/healthz", headers={"host": "localhost:8000"}).status_code == 200


def test_the_origin_check_accepts_the_lan_origin_and_refuses_a_foreign_one(settings, monkeypatch):
    """``origin_ok`` is the CSRF-adjacent check a cookie-authenticated POST runs.

    It falls back to the host allowlist for origins, so it is the consumer that proves the
    allowlist entry is doing real work on the write path and not just decorating a banner.
    """
    from starlette.requests import Request

    from adapt.security.middleware import origin_ok

    hosted_env(monkeypatch, settings)

    def origin(value: str | None) -> bool:
        headers = [] if value is None else [(b"origin", value.encode())]
        return origin_ok(Request({"type": "http", "method": "POST", "path": "/api/v1/students", "headers": headers}), settings)

    assert origin(f"http://{LAN_IP}:3000") is True
    assert origin(None) is True  # curl and the tests send no Origin at all
    assert origin("http://192.168.1.51:3000") is False
    assert origin("https://evil.example.com") is False


# --------------------------------------------------------------------------- #
# 2. the environment the children get
# --------------------------------------------------------------------------- #


def test_the_child_environment_merges_your_settings_and_only_raises_the_multiplier():
    secret = "test-key-never-print-me"
    env = hosting.build_api_env(
        {
            "ADAPT_EXTRA_ALLOWED_HOSTS": "studio.loca.lt",
            "ADAPT_ALLOWED_ORIGINS": '["http://localhost:3000"]',  # the older JSON spelling
            "ADAPT_RATE_LIMIT_MULTIPLIER": "2",
            "ADAPT_GEMINI_API_KEY": secret,
        },
        lan_ip=LAN_IP,
        web_port=3000,
        api_port=8001,
        data_dir="/tmp/adapt-room",
        configured_multiplier=2.0,
    )
    assert hosting.as_list(env["ADAPT_EXTRA_ALLOWED_HOSTS"]) == ["studio.loca.lt", LAN_IP]
    assert hosting.as_list(env["ADAPT_ALLOWED_ORIGINS"]) == ["http://localhost:3000", f"http://{LAN_IP}:3000"]
    assert env["ADAPT_RATE_LIMIT_MULTIPLIER"] == "10"
    assert env["ADAPT_DATA_DIR"] == "/tmp/adapt-room"
    assert env["ADAPT_PORT"] == "8001"
    # passed through, never rewritten -- and never printed by the banner
    assert env["ADAPT_GEMINI_API_KEY"] == secret
    plan = hosting.HostPlan(web_port=3000, api_port=8001, api_host="0.0.0.0", lan_ip=LAN_IP)
    assert secret not in "\n".join(hosting.host_report(plan))


def test_the_multiplier_is_never_lowered():
    """A tighter setting is respected: the room override can only raise the ceiling."""
    env = hosting.build_api_env({}, lan_ip=LAN_IP, web_port=3000, api_port=8000, configured_multiplier=25.0)
    assert env["ADAPT_RATE_LIMIT_MULTIPLIER"] == "25"
    env = hosting.build_api_env({}, lan_ip=LAN_IP, web_port=3000, api_port=8000, configured_multiplier=1.0, room_multiplier=20.0)
    assert env["ADAPT_RATE_LIMIT_MULTIPLIER"] == "20"


def test_a_missing_lan_address_adds_no_host_and_no_origin():
    env = hosting.build_api_env({"ADAPT_EXTRA_ALLOWED_HOSTS": "studio.loca.lt"}, lan_ip=None, web_port=3000, api_port=8000)
    assert hosting.as_list(env["ADAPT_EXTRA_ALLOWED_HOSTS"]) == ["studio.loca.lt"]
    assert env["ADAPT_ALLOWED_ORIGINS"] == ""


def test_the_web_child_is_pointed_at_the_api_this_command_started():
    env = hosting.build_web_env({"ADAPT_API": "offline", "PORT": "9999"}, api_port=8001, web_port=3000, production=True)
    assert env["ADAPT_API"] == "http://127.0.0.1:8001"  # not "offline": this command owns the API
    assert env["PORT"] == "3000"
    assert env["HOST"] == "0.0.0.0"
    assert env["NODE_ENV"] == "production"


def test_list_settings_accept_the_documented_csv_and_the_older_json(monkeypatch):
    """``ADAPT_EXTRA_ALLOWED_HOSTS=host`` is what the API's own 400 hint tells you to write.

    It used to raise ``SettingsError`` at import time (pydantic-settings json.loads()-ed the
    value before the validator could split it), which meant following the hint killed the
    process. Both spellings are accepted, and the JSON one keeps working for .env files that
    already hold it.
    """
    from adapt.config import Settings

    monkeypatch.setenv("ADAPT_EXTRA_ALLOWED_HOSTS", f"{LAN_IP}, studio.loca.lt")
    assert Settings(_env_file=None).extra_allowed_hosts == [LAN_IP, "studio.loca.lt"]
    monkeypatch.setenv("ADAPT_ALLOWED_ORIGINS", json.dumps(["http://localhost:3000", f"http://{LAN_IP}:3000"]))
    assert Settings(_env_file=None).allowed_origins == ["http://localhost:3000", f"http://{LAN_IP}:3000"]
    monkeypatch.setenv("ADAPT_EXTRA_ALLOWED_HOSTS", f'["{LAN_IP}"]')
    assert Settings(_env_file=None).extra_allowed_hosts == [LAN_IP]


# --------------------------------------------------------------------------- #
# 3. the parts a sandbox can still check
# --------------------------------------------------------------------------- #


def test_a_private_address_beats_link_local_and_loopback_is_never_advertised():
    assert hosting.pick_lan_ip(["127.0.0.1", "169.254.0.21", LAN_IP, "10.0.0.4"]) == LAN_IP
    assert hosting.pick_lan_ip(["127.0.0.1", "127.0.1.1"]) is None
    assert hosting.pick_lan_ip([]) is None
    assert hosting.pick_lan_ip(["169.254.0.21"]) == "169.254.0.21"  # a fallback, not a first choice
    assert hosting.lan_rank(LAN_IP) == 0
    assert hosting.lan_rank("169.254.0.21") == 1
    assert hosting.lan_rank("8.8.8.8") == 2
    assert hosting.lan_rank("127.0.0.1") is None
    assert hosting.lan_rank("not-an-address") is None


def test_an_explicit_address_wins_over_detection():
    assert hosting.detect_lan_ip("10.1.2.3") == ("10.1.2.3", ["10.1.2.3"])
    # detection itself never invents an address: it reports what it considered
    detected, considered = hosting.detect_lan_ip()
    assert detected is None or detected in considered


def test_the_tunnel_url_is_read_from_the_provider_output_and_only_from_a_trusted_domain():
    cloudflared = "2026-10-12T18:04:11Z INF |  https://plain-brook-quiet-frost.trycloudflare.com  |"
    assert hosting.parse_tunnel_url(cloudflared) == "https://plain-brook-quiet-frost.trycloudflare.com"
    assert hosting.parse_tunnel_url("your url is: https://busy-panda-77.loca.lt") == "https://busy-panda-77.loca.lt"
    # a lookalike must not become the shared address (or the tunnel would point at a stranger)
    assert hosting.parse_tunnel_url("https://trycloudflare.com.attacker.net") is None
    assert hosting.parse_tunnel_url("https://plain-frost.trycloudflare.com.attacker.net") is None
    assert hosting.parse_tunnel_url("INF Requesting new quick Tunnel on trycloudflare.com...") is None
    assert hosting.parse_tunnel_url("") is None


def test_tunnel_command_prefers_cloudflared_and_says_so_when_there_is_none():
    cloudflared = hosting.tunnel_command(3000, which=lambda name: "/usr/local/bin/cloudflared" if name == "cloudflared" else None)
    assert cloudflared is not None
    argv, provider, _ = cloudflared
    assert provider == "cloudflared" and "http://127.0.0.1:3000" in argv
    localtunnel = hosting.tunnel_command(3000, which=lambda name: "/usr/local/bin/npx" if name == "npx" else None)
    assert localtunnel is not None
    argv, provider, caveat = localtunnel
    assert provider == "localtunnel" and "3000" in argv
    assert "reminder page" in caveat  # the caveat is printed, not hidden
    assert hosting.tunnel_command(3000, which=lambda name: None) is None
    assert "cloudflared" in hosting.TUNNEL_INSTALL_HINT


def test_the_banner_warns_about_the_microphone_and_the_shared_bucket():
    plain = "\n".join(hosting.host_report(hosting.HostPlan(web_port=3000, api_port=8000, api_host="0.0.0.0", lan_ip=LAN_IP, allowed_hosts=(LAN_IP,), multiplier=10.0, configured_multiplier=1.0)))
    assert f"http://{LAN_IP}:3000" in plain          # the address to share, printed
    assert "MIC OVER PLAIN HTTP" in plain            # the microphone caveat, up front
    assert "https" in plain and "secure context" in plain
    assert "120 ->   1200 per minute" in plain       # the room's real ceiling, from the table
    assert "elevated" not in plain
    secure = "\n".join(hosting.host_report(hosting.HostPlan(web_port=3000, api_port=8000, api_host="0.0.0.0", lan_ip=LAN_IP, allowed_hosts=(LAN_IP,), tunnel_url="https://a-b.loca.lt", tunnel_requested=True)))
    assert "share this:    https://a-b.loca.lt" in secure
    assert "MIC OVER PLAIN HTTP" not in secure
    failed_tunnel = "\n".join(hosting.host_report(hosting.HostPlan(web_port=3000, api_port=8000, api_host="0.0.0.0", lan_ip=LAN_IP, allowed_hosts=(LAN_IP,), tunnel_requested=True, tunnel_note="cloudflared printed no address")))
    assert "cloudflared printed no address" in failed_tunnel
    assert f"http://{LAN_IP}:3000" in failed_tunnel  # the LAN path is still the answer


def test_rate_lines_report_the_ceiling_the_server_enforces():
    from adapt.security.ratelimit import DEFAULT_LIMITS, effective_limits

    text = "\n".join(hosting.rate_lines(10.0, 1.0))
    collapsed = " ".join(text.split())
    effective = effective_limits(10.0)
    assert f"{DEFAULT_LIMITS['ip'][0]:.0f} -> {effective['ip'][0]:.0f} per minute" in collapsed
    assert "x10 for these servers" in text
    # and a higher configured value is never presented as a reduction
    assert "up from" not in "\n".join(hosting.rate_lines(10.0, 10.0))


def test_a_child_process_is_read_for_a_url_and_then_stopped():
    """The tunnel plumbing with a real child, and no network: print a line, be stopped."""
    said: list[str] = []
    session = hosting.HostSession(echo=said.append)
    try:
        session.start("tunnel", [sys.executable, "-c", "import time; print('INF | https://a-b-c.loca.lt |', flush=True); time.sleep(30)"], env={})
        assert session.await_url("tunnel", timeout=10) == "https://a-b-c.loca.lt"
        assert any("a-b-c.loca.lt" in line for line in said)  # ...and the operator saw it too
        assert session.exited() is None
        session.stop_child("tunnel")
        assert session.children == {}
    finally:
        session.stop()


def test_a_child_that_never_prints_a_url_times_out_instead_of_hanging():
    session = hosting.HostSession(echo=lambda line: None)
    try:
        session.start("tunnel", [sys.executable, "-c", "import time; time.sleep(30)"], env={})
        started = time.monotonic()
        assert session.await_url("tunnel", timeout=1.0) is None
        assert time.monotonic() - started < 15
    finally:
        session.stop()


def test_adapt_host_exists_with_the_flags_this_documented():
    result = CliRunner().invoke(hosting_cli(), ["host", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--port", "--data-dir", "--tunnel", "--rate-limit-multiplier", "--ip"):
        assert flag in result.output, f"{flag} is not in `adapt host --help`"
    assert "allowlist" in result.output


def test_adapt_host_runs_from_a_module_invocation_too():
    """``python -m adapt.cli host`` -- the invocation the docs use, not just the console script.

    ``adapt`` and ``python -m adapt.cli`` reach the same Typer app through different doors, and
    the `if __name__ == "__main__"` guard at the end of cli.py is what makes the second one
    work at all: without it the module imports happily and does nothing, with exit code 0.
    """
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-m", "adapt.cli", "host", "--help"],
        cwd=str(backend),
        env={**os.environ, "PYTHONPATH": str(backend / "src")},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-400:]
    assert "--tunnel" in out.stdout


def test_adapt_host_refuses_a_port_that_is_already_serving():
    """A second server on the same port is how a showcase ends up measuring the wrong app."""
    taken = socket.socket()
    taken.bind(("0.0.0.0", 0))
    taken.listen(1)
    port = int(taken.getsockname()[1])
    try:
        result = CliRunner().invoke(hosting_cli(), ["host", "--port", str(port)])
        assert result.exit_code == 2, result.output
        assert "already in use" in result.output
    finally:
        taken.close()


def hosting_cli():
    """The Typer app, imported here so a broken import names this file, not conftest."""
    from adapt.cli import app as cli_app

    return cli_app


@pytest.fixture(autouse=True)
def _no_stray_env(monkeypatch):
    """These tests own the two list settings, so a developer's shell cannot colour them."""
    for name in ("ADAPT_EXTRA_ALLOWED_HOSTS", "ADAPT_ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    yield


def test_repo_root_points_at_the_checkout_that_holds_the_web_app():
    root = hosting.repo_root()
    assert (root / "backend" / "src" / "adapt" / "hosting.py").exists()
    frontend = hosting.Frontend(root / "frontend")
    assert frontend.script.name == "server.mjs"
    assert os.path.basename(str(root)) == "Adapt" or (root / "README.md").exists()


def test_cli_token_honors_hours_and_doctor_emits_no_argon2_deprecation(services):
    import warnings
    from datetime import datetime

    from adapt.db.models import Student, utcnow

    with services.db.session() as s:
        stu = Student(display_name="CLI Token Learner")
        s.add(stu)
        s.commit()
        stu_id = stu.id

    before = utcnow()
    tok_res = CliRunner().invoke(hosting_cli(), ["token", "--for", stu_id, "--hours", "2"])
    assert tok_res.exit_code == 0, tok_res.output
    exp_line = next(line for line in tok_res.stdout.splitlines() if line.startswith("expires: "))
    expires_at = datetime.fromisoformat(exp_line.split("expires: ", 1)[1].strip())
    delta_hours = (expires_at - before).total_seconds() / 3600.0
    assert 1.9 <= delta_hours <= 2.2, f"expected ~2h TTL, got {delta_hours:.2f}h"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        doc_res = CliRunner().invoke(hosting_cli(), ["doctor", "--json"])
    assert doc_res.exit_code == 0, doc_res.output
    argon_warnings = [w for w in caught if "argon2" in str(w.message).lower()]
    assert not argon_warnings, argon_warnings

