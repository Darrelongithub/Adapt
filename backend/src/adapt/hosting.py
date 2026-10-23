"""``adapt host``: one address for the room, and the honest list of what that costs.

The showcase path is a single command. The API and the web app start together, the machine's
own wifi address is detected, printed, and added to the API's host allowlist as *that exact
address* -- and the two things a room will actually run into are said out loud, before anyone
joins:

* **The microphone needs https.** Browsers only expose the microphone APIs (``getUserMedia``,
  and the ``SpeechRecognition`` dictation the voice screen uses) in a *secure context*: https,
  or localhost. On ``http://<lan-ip>:3000`` the voice screen cannot listen -- everything else,
  including the keyboard path, works. ``--tunnel`` opens the https link that fixes it, using
  the two providers this app already trusts and ``adapt doctor`` already reports.
* **The room is one connection.** Every phone behind the same router reaches the API from one
  address, so the per-IP bucket and learner creation are the room's *shared* budget. This
  module raises ``ADAPT_RATE_LIMIT_MULTIPLIER`` for the processes it starts -- never for the
  repository default, and never downwards -- and the banner prints the effective numbers.

Nothing here writes to ``.env`` or reads a secret. The environment handed to the children is
the operator's own environment with a handful of keys merged, and everything printed is an
address, a port or a count.

Why a module rather than a branch inside ``cli.py``: the interesting parts (which address to
advertise, what the child environment becomes, which tunnel command to run, what the banner
says) are pure functions, and the security-relevant one -- the allowlist -- is a merge that a
test can read directly instead of inferring from a process that was spawned.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

# --------------------------------------------------------------------------- #
# what a room needs
# --------------------------------------------------------------------------- #

# Multiplier applied to every token bucket (security/ratelimit.py) for the processes this
# command starts. The arithmetic, on the buckets a shared connection actually spends:
#
#   ip           120/min, burst 240   -- every learner behind one router shares ONE key, so a
#                                        full room's page loads and clicks land here first
#   guest_create   6/min, burst   6   -- a room all joining inside a minute
#   auth_fail      5/min, burst   5   -- a class mistyping a password, not one attacker
#
# 10x makes those 1200/min, 60/min and 50/min: roughly 40 requests per minute per learner in
# a 30-device room, plus burst headroom -- comfortably above what a person can click, still a
# ceiling. It is applied to the child processes only: the repository default stays 1.0, so no
# deployment's protection is quietly reduced by a command someone ran at a showcase.
ROOM_RATE_LIMIT_MULTIPLIER = 10.0

# What the banner says about the microphone. Kept as one constant so the README, the tests
# and the terminal warning cannot drift into three different half-truths.
MIC_HTTP_WARNING: tuple[str, ...] = (
    "MIC OVER PLAIN HTTP: browsers only hand a page the microphone in a secure context",
    "  (https, or localhost). On this plain-http address the voice screen cannot listen;",
    "  the keyboard path teaches exactly the same thing. For voice on a phone, re-run",
    "  with --tunnel and share the https address there.",
)

SHARE_NOTE = "phones on the same wifi open this"

# The buckets a shared connection actually spends first, with the label a person reads.
# (scope, label) -- the numbers themselves come from security/ratelimit.py, so the banner
# cannot advertise a ceiling the code does not enforce.
ROOM_SCOPES: tuple[tuple[str, str], ...] = (
    ("ip", "requests from the connection"),
    ("guest_create", "learners created (all joining at once)"),
    ("auth_fail", "sign-in attempts after a failure"),
)

# --------------------------------------------------------------------------- #
# which address to advertise
# --------------------------------------------------------------------------- #


def lan_rank(ip: str) -> int | None:
    """How usable is this address as "the one to type on a phone"?

    0 = a private (RFC1918 or CGNAT) address: the normal case on a home/office/hotspot
    network. 1 = link-local (169.254/16): reachable on the wire, but only on the same
    segment and nobody can guess it, so it is a fallback rather than a first choice.
    2 = anything else routable. ``None`` = never advertise this (loopback, unset, multicast,
    reserved): printing ``http://127.0.0.1`` as the address to *share* is the classic way to
    send a room to a URL that can only ever work on the presenter's own machine.
    """
    try:
        addr = ipaddress.IPv4Address(str(ip).strip())
    except (ValueError, ipaddress.AddressValueError):
        return None
    if addr.is_loopback or addr.is_unspecified or addr.is_multicast or addr.is_reserved:
        return None
    if addr.is_link_local:
        return 1
    if addr.is_private:
        return 0
    return 2


def pick_lan_ip(candidates: Iterable[str]) -> str | None:
    """The best shareable address among ``candidates``, or None.

    Deterministic: ties are broken by the order the caller supplied, so the same machine
    prints the same address on every run rather than whichever interface answered first.
    """
    best: tuple[int, str] | None = None
    for raw in candidates:
        ip = str(raw).strip()
        rank = lan_rank(ip)
        if rank is None:
            continue
        if best is None or rank < best[0]:
            best = (rank, ip)
            if rank == 0:  # a private address is never beaten; keep the first one seen
                break
    return best[1] if best else None


def route_address(timeout: float = 0.25) -> str | None:
    """The address the kernel would use to reach the internet. Sends nothing.

    A connectionless UDP socket that is ``connect()``ed to a public address only *selects a
    source address* locally; no packet leaves the machine. That is why this is used instead
    of a DNS lookup or an HTTP request -- and it is stdlib, so no dependency is added for it.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


def hostname_addresses() -> list[str]:
    """Addresses this host answers to by name -- the second opinion when there is no route."""
    out: list[str] = []
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        out.extend(addresses)
    except OSError:
        pass
    with suppress(OSError):
        resolved = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        out.extend(info[4][0] for info in resolved)
    seen: list[str] = []
    for ip in out:
        if ip not in seen:
            seen.append(ip)
    return seen


def detect_lan_ip(override: str | None = None) -> tuple[str | None, list[str]]:
    """``(the address to print, everything considered)``.

    With ``--ip`` the operator wins: a laptop with a VPN, Docker or a virtual switch has
    several addresses and the kernel's choice is not always the wifi one. The override is
    still reported back in ``considered`` so the banner can say what was used and why.
    """
    if override and override.strip():
        value = override.strip()
        return value, [value]
    candidates: list[str] = []
    routed = route_address()
    if routed:
        candidates.append(routed)
    for ip in hostname_addresses():
        if ip not in candidates:
            candidates.append(ip)
    return pick_lan_ip(candidates), candidates


# --------------------------------------------------------------------------- #
# the environment the children get
# --------------------------------------------------------------------------- #


def as_list(value: str | Sequence[str] | None) -> list[str]:
    """A list-valued setting, however it was written.

    ``ADAPT_EXTRA_ALLOWED_HOSTS`` / ``ADAPT_ALLOWED_ORIGINS`` are written as a comma
    separated value in the docs and in the API's own untrusted-host hint, and as a JSON array
    by anyone whose ``.env`` predates that advice (pydantic-settings decodes JSON). Both parse
    to the same list here, so an operator's existing entry is never dropped on the floor and
    never survives as one mangled string.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        items = [str(item) for item in value]
    else:
        text = value.strip()
        items = []
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except ValueError:
                decoded = None
            if isinstance(decoded, list):
                items = [str(item) for item in decoded]
        if not items:
            items = text.split(",")
    out: list[str] = []
    for item in items:
        clean = item.strip()
        if clean and clean not in out:
            out.append(clean)
    return out


def merge_csv(existing: str | Sequence[str] | None, additions: Sequence[str]) -> str:
    """``existing`` plus ``additions`` as one comma separated value, order kept.

    Merge, not replace: an operator whose ``.env`` already allows a tunnel host or a second
    laptop keeps every entry, and this command appends its own. ``ADAPT_EXTRA_ALLOWED_HOSTS``
    is compared by exact string in ``security/middleware.py`` (``host_allowed``), so what is
    added here is a host -- the LAN address, never a wildcard or a subnet.
    """
    return ",".join(as_list([*(as_list(existing)), *[str(a) for a in additions]]))


def raised_multiplier(*values: float | str | None) -> float:
    """The highest multiplier anyone asked for, so this command can only ever raise it."""
    numbers: list[float] = []
    for value in values:
        try:
            numbers.append(float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    numbers = [n for n in numbers if n > 0]
    return max(numbers) if numbers else 1.0


@dataclass(frozen=True)
class HostPlan:
    """Everything the banner needs and nothing that is a secret."""

    web_port: int
    api_port: int
    api_host: str
    lan_ip: str | None
    ips_considered: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    multiplier: float = ROOM_RATE_LIMIT_MULTIPLIER
    configured_multiplier: float = 1.0
    mode: str = "local"
    tunnel_requested: bool = False
    tunnel_url: str | None = None
    tunnel_note: str = ""
    extra_notes: tuple[str, ...] = ()

    @property
    def web_url(self) -> str:
        return f"http://{self.lan_ip}:{self.web_port}" if self.lan_ip else f"http://localhost:{self.web_port}"

    @property
    def local_url(self) -> str:
        return f"http://localhost:{self.web_port}"

    @property
    def api_url(self) -> str:
        # A wildcard bind is not a URL anyone can open, so the printed address is the loopback
        # one; the bind address is printed beside it rather than instead of it.
        host = self.api_host if self.api_host not in ("0.0.0.0", "::", "") else "127.0.0.1"
        return f"http://{host}:{self.api_port}"

    @property
    def share_url(self) -> str:
        """The one address to read out loud: the tunnel when there is one, else the LAN URL."""
        return self.tunnel_url or self.web_url

    @property
    def secure_share(self) -> bool:
        return bool(self.tunnel_url)


def build_api_env(
    base: Mapping[str, str],
    *,
    lan_ip: str | None,
    web_port: int,
    api_port: int,
    extra_hosts: Sequence[str] = (),
    origins: Sequence[str] = (),
    data_dir: str | None = None,
    configured_multiplier: float = 1.0,
    room_multiplier: float = ROOM_RATE_LIMIT_MULTIPLIER,
) -> dict[str, str]:
    """The API child's environment: the operator's own, plus what hosting needs.

    Three additions, all additive, all printed by the banner:

    * ``ADAPT_EXTRA_ALLOWED_HOSTS`` -- the detected LAN address (exact string; no wildcard,
      no subnet) on top of whatever ``.env`` already allows.
    * ``ADAPT_ALLOWED_ORIGINS`` -- ``http://<lan-ip>:<web-port>``, because a browser on the
      LAN sends that as ``Origin`` on every mutating request and the proxy forwards it
      untouched. Additive: ``ADAPT_FRONTEND_ORIGIN`` is deliberately *not* overwritten.
    * ``ADAPT_RATE_LIMIT_MULTIPLIER`` -- raised to a room's worth, never lowered. See
      :data:`ROOM_RATE_LIMIT_MULTIPLIER` for the arithmetic.

    Every other key -- the model key, the passphrase, the encryption policy, the data
    directory the operator chose -- is left exactly as their environment set it.
    """
    env = dict(base)
    hosts = [*extra_hosts, *( [lan_ip] if lan_ip else [] )]
    env["ADAPT_EXTRA_ALLOWED_HOSTS"] = merge_csv(base.get("ADAPT_EXTRA_ALLOWED_HOSTS"), hosts)
    origin_additions = [f"http://{lan_ip}:{web_port}"] if lan_ip else []
    existing_origins = merge_csv(base.get("ADAPT_ALLOWED_ORIGINS"), origins)
    env["ADAPT_ALLOWED_ORIGINS"] = merge_csv(existing_origins, origin_additions)
    room = raised_multiplier(configured_multiplier, room_multiplier)
    env["ADAPT_RATE_LIMIT_MULTIPLIER"] = f"{room:g}"
    env["ADAPT_PORT"] = str(api_port)
    if data_dir:
        env["ADAPT_DATA_DIR"] = str(data_dir)
    return env


def build_web_env(
    base: Mapping[str, str],
    *,
    api_port: int,
    web_port: int,
    production: bool,
    lan_ip: str | None = None,
) -> dict[str, str]:
    """The web child's environment.

    ``ADAPT_API`` is *set*, not defaulted: this command starts its own API, and inheriting an
    ``ADAPT_API`` that points somewhere else (another dev server, ``offline``) is how a
    showcase ends up showing nothing while looking healthy. ``HOST`` is forced to ``0.0.0.0``
    because the whole point is a socket other machines can reach; ``PORT`` is the address
    people open.
    """
    env = dict(base)
    env["ADAPT_API"] = f"http://127.0.0.1:{api_port}"
    env["HOST"] = "0.0.0.0"
    env["PORT"] = str(web_port)
    env["NODE_ENV"] = "production" if production else "development"
    if lan_ip:
        env["ADAPT_DEV_ORIGIN"] = ",".join(dict.fromkeys([
            *as_list(base.get("ADAPT_DEV_ORIGIN", "")), lan_ip,
        ]))
    return env


# --------------------------------------------------------------------------- #
# the opt-in https path
# --------------------------------------------------------------------------- #

# Both providers are already trusted by the API's host patterns
# (``security/middleware.py``: ``*.loca.lt`` and ``*.trycloudflare.com``), so a tunnel URL
# needs no new allowlist entry -- and neither needs an account or a payment.
TUNNEL_URL_PATTERN = re.compile(
    # The domain has to end there: ``x.trycloudflare.com.attacker.net`` is a lookalike, and
    # the whole point of reading the address out of a provider's log is that the address we
    # print is the provider's, not a stranger's with our provider's name inside it.
    r"https://[a-z0-9][a-z0-9-]*\.(?:trycloudflare\.com|loca\.lt)(?![a-z0-9.-])"
)

TUNNEL_INSTALL_HINT = (
    "cloudflared (brew install cloudflared, or a release binary), or Node's npx,"
    " then re-run --tunnel"
)

# How long to wait for a provider to print its public URL before falling back to the LAN
# address. cloudflared answers in a couple of seconds; `npx --yes localtunnel` may have to
# download the package first, which is what this budget is really for.
TUNNEL_START_TIMEOUT_S = 90.0


def tunnel_command(
    port: int, *, which: Callable[[str], str | None] = shutil.which
) -> tuple[list[str], str, str] | None:
    """``(argv, provider, caveat)`` for the best tunnel this machine has, or None.

    cloudflared first: a quick tunnel, no account, and no browser interstitial in front of
    API calls. localtunnel second (through ``npx``, so nothing has to be installed globally):
    it works, but it may serve a reminder page to a browser before the app, which is a poor
    first impression on a phone.
    """
    if which("cloudflared"):
        return (
            ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
            "cloudflared",
            "cloudflared quick tunnel",
        )
    if which("npx"):
        return (
            ["npx", "--yes", "localtunnel", "--port", str(port)],
            "localtunnel",
            "localtunnel via npx -- it may show a reminder page before the app in a browser",
        )
    return None


def parse_tunnel_url(line: str) -> str | None:
    """Pull the public https URL out of a provider's log line.

    Both providers print progress lines that must not be mistaken for the address, so the
    match is anchored to a hostname this app already trusts.
    """
    if not line:
        return None
    found = TUNNEL_URL_PATTERN.search(line)
    return found.group(0) if found else None


# --------------------------------------------------------------------------- #
# the banner
# --------------------------------------------------------------------------- #


def host_report(plan: HostPlan) -> list[str]:
    """The lines the operator reads out loud. Addresses, ports and counts -- nothing secret."""
    rule = "-" * 72
    trusted = ", ".join(plan.allowed_hosts) if plan.allowed_hosts else "none"
    lines = [
        rule,
        "ADAPT is hosting for your room.",
        f"  share this:    {plan.share_url}"
        + ("   <- https, works off the wifi too" if plan.tunnel_url else f"   <- {SHARE_NOTE}"),
        f"  this machine:  {plan.local_url}",
        f"  API:           {plan.api_url}  (bound to {plan.api_host})",
        f"  extra hosts the API trusts: {trusted}",
        "  stop with:     Ctrl-C",
        "",
    ]
    if plan.tunnel_url:
        lines += [
            f"  the LAN address still works too: {plan.web_url} (no microphone on that one)",
            "  a quick tunnel is public: anyone with the link can reach the app. Guest creation",
            "  needs the demo passphrase only when ADAPT_MODE=tunnel and ADAPT_DEMO_PASSPHRASE",
            "  are both set.",
            "",
        ]
    elif plan.tunnel_requested:
        lines += [
            f"  --tunnel did not produce an https address: {plan.tunnel_note}.",
            "  The LAN address above is the one to share; the microphone stays unavailable on it.",
            "",
        ]
    else:
        lines += [*MIC_HTTP_WARNING, ""]
    if plan.mode == "tunnel" and not plan.tunnel_url:
        lines += [
            "  ADAPT_MODE=tunnel is set: cookies are Secure, so sign-in will not stick on the",
            "  plain-http LAN address. Use --tunnel, or set ADAPT_MODE=local for the room.",
            "",
        ]
    if plan.lan_ip and lan_rank(plan.lan_ip) == 1:
        lines += [
            f"  note: {plan.lan_ip} is a link-local address -- only devices on this segment",
            "  can reach it. If another machine cannot connect, pass --ip <your wifi address>.",
            "",
        ]
    if plan.lan_ip is None:
        lines += [
            "  no usable LAN address was found, so the address above is this machine only.",
            "  Find your wifi address and pass it: adapt host --ip 192.168.1.50",
            "",
        ]
    if plan.extra_notes:
        lines += [f"  {note}" for note in plan.extra_notes] + [""]
    lines += [*rate_lines(plan.multiplier, plan.configured_multiplier), rule]
    return lines


def rate_lines(multiplier: float, configured: float) -> list[str]:
    """What the shared connection actually gets, read from the table the server enforces."""
    from .security.ratelimit import DEFAULT_LIMITS, effective_limits

    effective = effective_limits(multiplier)
    lines = [
        "RATE LIMITS (shared connection): everyone on this wifi reaches the API from one",
        "  address, so the room shares one bucket. For the servers this command started:",
    ]
    for scope, label in ROOM_SCOPES:
        base = DEFAULT_LIMITS.get(scope)
        now = effective.get(scope)
        if not base or not now:
            continue
        lines.append(f"      {label:<38} {base[0]:>6.0f} -> {now[0]:>6.0f} per minute")
    if multiplier > configured + 1e-9:
        lines.append(
            f"  x{multiplier:g} for these servers, up from x{configured:g} in your environment."
        )

    else:
        lines.append(f"  x{multiplier:g} for these servers, the value your environment asks for.")
    lines += [
        "  The repository default stays 1.0, so no deployment's ceiling was lowered: this",
        "  applies to the servers this command started, not to a config file.",
    ]
    lines += [
        "  /api/v1/quotas reports the effective numbers to any signed-in learner, and",
        "  --rate-limit-multiplier raises the ceiling further (it never lowers it).",
    ]
    return lines


# --------------------------------------------------------------------------- #
# the processes
# --------------------------------------------------------------------------- #


def _session_kwargs() -> dict[str, object]:
    """Detach the children from the terminal's process group (POSIX only).

    Ctrl-C then reaches *this* process alone, which is what makes the shutdown path one code
    path: on interrupt, terminate the children in order, wait, kill whatever ignored it.
    """
    return {"start_new_session": True} if os.name == "posix" else {}


def port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def free_port(host: str = "0.0.0.0") -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


class HostSession:
    """The child processes, their prefixed logs, and one way to stop them all.

    Deliberately thin: it spawns, prefixes output, remembers the tail of each child's log (so
    a failure can be quoted in one line instead of dumped), and answers "is the URL up yet".
    """

    def __init__(self, *, echo: Callable[[str], None] = print) -> None:
        self.echo = echo
        self.children: dict[str, subprocess.Popen[str]] = {}
        self.logs: dict[str, deque[str]] = {}
        self._lock = threading.Lock()
        self._stopped = False

    # -- lifecycle ---------------------------------------------------------- #

    def start(
        self, name: str, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path | None = None
    ) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            list(argv),
            env=dict(env),
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **_session_kwargs(),  # type: ignore[arg-type]
        )
        self.children[name] = proc
        self.logs[name] = deque(maxlen=500)
        threading.Thread(target=self._pump, args=(name, proc), daemon=True).start()
        return proc

    def _pump(self, name: str, proc: subprocess.Popen[str]) -> None:
        stream: IO[str] | None = proc.stdout
        if stream is None:  # pragma: no cover - PIPE is always set above
            return
        for raw in stream:
            line = raw.rstrip("\n")
            with self._lock:
                self.logs.setdefault(name, deque(maxlen=500)).append(line)
            self.echo(f"[{name}] {line}")

    def tail(self, name: str, limit: int = 40) -> list[str]:
        with self._lock:
            return list(self.logs.get(name, []))[-limit:]

    def exited(self) -> tuple[str, int] | None:
        """The first child that has exited, with its code -- a dead server is not a running demo."""
        for name, proc in self.children.items():
            code = proc.poll()
            if code is not None:
                return name, int(code)
        return None

    def wait_http(self, url: str, *, timeout: float) -> bool:
        """Wait for a successful HTTP response, bypassing any configured http_proxy."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.exited() is not None:
                return False
            try:
                with opener.open(url, timeout=2):
                    return True
            except Exception:
                time.sleep(0.3)
        return False

    def await_url(self, name: str, *, timeout: float) -> str | None:
        """Wait for the child ``name`` to print a tunnel URL."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in self.tail(name):
                url = parse_tunnel_url(line)
                if url:
                    return url
            if self.children.get(name) is not None and self.children[name].poll() is not None:
                return None
            time.sleep(0.25)
        return None

    def stop_child(self, name: str, *, timeout: float = 5.0) -> None:
        """Stop one child and leave the rest running (a tunnel that never produced a URL)."""
        proc = self.children.pop(name, None)
        if proc is None:
            return
        with suppress(OSError):
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                proc.kill()

    def stop(self, *, timeout: float = 6.0) -> None:
        """Terminate everything, politely then not, in reverse start order."""
        if self._stopped:
            return
        self._stopped = True
        for name in reversed(list(self.children)):
            proc = self.children[name]
            if proc.poll() is None:
                with suppress(OSError):  # pragma: no cover - already gone
                    proc.terminate()
        deadline = time.monotonic() + timeout
        for name in reversed(list(self.children)):
            proc = self.children[name]
            remaining = max(0.2, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                if proc.poll() is None:
                    self.echo(f"[{name}] did not stop in {timeout:g}s; killing it")
                    proc.kill()
                    with suppress(subprocess.TimeoutExpired):  # pragma: no cover - unkillable child
                        proc.wait(timeout=3)
        self.children.clear()


@dataclass
class Frontend:
    """Where the web app lives and whether it is built."""

    directory: Path
    script: Path = field(init=False)

    def __post_init__(self) -> None:
        self.script = self.directory / "server.mjs"

    @property
    def built(self) -> bool:
        return (self.directory / ".next" / "BUILD_ID").exists()

    @property
    def installed(self) -> bool:
        return (self.directory / "node_modules").exists()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def uvicorn_command(settings, *, host: str, port: int) -> list[str]:
    """The API child: the same invocation ``scripts/probe-fresh.sh`` and ``journey_check.py``
    use -- uvicorn's own websocket autoselect (``websockets``, a declared dependency).

    Deliberately no ``--ws wsproto``: that flag was copied from ``adapt serve`` and crashes on
    a clean install, because ``uvicorn[standard]`` does not bring ``wsproto`` with it.
    """
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "adapt.api.app:app",
        # Request targets can carry private query parameters. Keep startup/error logs,
        # not raw per-request access logs (also incompatible with our log scrubber).
        "--no-access-log",
        # Preserve the socket peer for adapt.security.middleware.client_ip; otherwise
        # Uvicorn trusts localhost forwarding headers before our allowlist runs.
        "--no-proxy-headers",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        str(getattr(settings, "log_level", "INFO")).lower(),
    ]


__all__ = [
    "MIC_HTTP_WARNING",
    "ROOM_RATE_LIMIT_MULTIPLIER",
    "TUNNEL_INSTALL_HINT",
    "TUNNEL_START_TIMEOUT_S",
    "Frontend",
    "HostPlan",
    "HostSession",
    "as_list",
    "build_api_env",
    "build_web_env",
    "detect_lan_ip",
    "free_port",
    "host_report",
    "hostname_addresses",
    "lan_rank",
    "merge_csv",
    "parse_tunnel_url",
    "pick_lan_ip",
    "port_is_free",
    "raised_multiplier",
    "rate_lines",
    "repo_root",
    "route_address",
    "tunnel_command",
    "uvicorn_command",
]
