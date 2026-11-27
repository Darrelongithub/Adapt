"""``adapt`` — the sandbox commands that make the loop demonstrable in one sitting.

Two verbs, and one rule: **nothing here fabricates a number**. Every printed
value is a response field, and the learners are declared synthetic on every line
they appear on. If the engine abstains, the demo says so and prints what it says
it needs; if a learner is a guesser, the demo shows the engine declining to
believe it.

    adapt sandbox seed     # tenant + key + three synthetic learners
    adapt sandbox demo     # the loop, start to finish, with timestamps

`demo` starts the real application on a loopback port and speaks HTTP to it —
urllib, no test client — so what the demo exercises is the surface a host gets,
not a function called directly. `INTEGRATION.md` §13 is the story this implements.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from adapt_engine import provisioning
from adapt_engine.config import Settings
from adapt_engine.db import create_db_engine
from adapt_engine.errors import ConfigError

#: Where `seed` leaves what `demo` needs. A dotfile in the working directory: the
#: sandbox is a throwaway, and putting its key in a global config would leak one
#: tenant's key into another's shell.
STATE_FILE = Path(".adapt-sandbox.json")

SKILL = "skill.fractions"

#: Three learners, each a different way of being wrong about a number.
LEARNERS = {
    "guesser": "learn-sandbox-guesser",
    "steady": "learn-sandbox-steady",
    "overconfident": "learn-sandbox-overconfident",
    "fresh": "learn-sandbox-fresh",
}

SEED = 20261118


@dataclass(frozen=True)
class Sandbox:
    """Two keys, and the difference matters.

    The console key is the tenant's: it holds `admin` and reads the metrics. The
    host key belongs to a *connection*: `observe` and `decide` refuse a console
    key, because a key that can be spent on ingestion is a key that should be
    rotatable per host.
    """

    slug: str
    tenant_id: str
    key: str
    connection_key: str
    learnings: dict[str, str] | None
    learners: dict[str, str]

    def save(self, path: Path = STATE_FILE) -> None:
        path.write_text(json.dumps(self.__dict__, indent=2) + "\n")
        path.chmod(0o600)

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> Sandbox:
        payload = json.loads(path.read_text())
        return cls(**payload)


# --------------------------------------------------------------------------- io


def _owner_engine() -> Engine:
    url = os.environ.get("ADAPT_MIGRATION_DATABASE_URL")
    if not url:
        raise SystemExit(
            "error: ADAPT_MIGRATION_DATABASE_URL is not set (the sandbox needs owner rights "
            "to create a tenant)"
        )
    return create_engine(url, future=True)


def _settings() -> Settings:
    try:
        return Settings.from_env()
    except ConfigError as exc:
        print(f"error: {exc.variable}: {exc.detail}", file=sys.stderr)
        raise SystemExit(1) from exc


# ------------------------------------------------------------------------ server


@contextlib.contextmanager
def _serving(settings: Settings) -> Iterator[str]:
    """Run the real app on a loopback port for the duration of the block."""
    import uvicorn

    from adapt_engine.main import create_app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    # The engine is created here, not inside the app, so it can be disposed on the
    # way out: a leaked pool keeps the database open, and a database that will not
    # close is a database that will not drop.
    engine = create_db_engine(settings.database_url)
    app = create_app(settings, engine=engine)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise SystemExit("error: the app did not start within 30 seconds")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        engine.dispose()


class Caller:
    """A tiny HTTP client: status, headers and body, nothing hidden."""

    def __init__(self, base: str, key: str) -> None:
        self.base = base
        self.key = key

    def __call__(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotent: bool = False,
        test_traffic: bool = False,
    ) -> tuple[int, dict[str, str], Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.key}")
        request.add_header("Content-Type", "application/json")
        if idempotent:
            request.add_header("Idempotency-Key", f"adapt-{uuid.uuid4().hex}")
        if test_traffic:
            request.add_header("X-Adapt-Test", "1")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return (
                    response.status,
                    dict(response.headers),
                    json.loads(response.read() or b"null"),
                )
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), json.loads(error.read() or b"null")


# ---------------------------------------------------------------------- credits


def _grant(engine: Engine, tenant_id: uuid.UUID, amount: int) -> None:
    """Plan credits, with the expiry `included` grants are required to carry.

    The sandbox is a demo tenant, so it starts with plan credits rather than a
    purchase: `included` is what a real plan grants, and 0005 refuses an included
    grant that never expires — credits that never lapse are purchased credits
    wearing the wrong label.
    """
    from adapt_engine.credits import service as credits_service
    from adapt_engine.db.tenant import tenant_transaction

    with tenant_transaction(engine, tenant_id) as conn:
        credits_service.grant_credits(
            conn,
            tenant_id=tenant_id,
            kind="included",
            amount=amount,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            idempotency_key=f"sandbox-{datetime.now(UTC).strftime('%Y%m%d')}-{amount}",
        )


# ---------------------------------------------------------------------- seeding


def _answers(learner: str, count: int, offset: int = 0) -> list[dict[str, Any]]:
    """A synthetic learner's answers, generated from the seed above.

    * `guesser` — right about one time in five, whatever else happens. The point
      of the learner is that no amount of evidence makes a number about them
      *confident*, so the engine must not serve one.
    * `steady` — improving: 40% early, 70% late.
    * `overconfident` — fast and wrong on the harder items, which is the shape
      `data_quality` exists for.
    """
    import random

    rng = random.Random(f"{SEED}:{learner}")
    events = []
    for index in range(offset, offset + count):
        progress = (index - offset) / max(1, count - 1)
        if learner == LEARNERS["guesser"]:
            correct = index % 5 == 0
            latency = 2400
        elif learner == LEARNERS["steady"]:
            correct = rng.random() < 0.4 + 0.3 * progress
            latency = 9000
        else:
            correct = rng.random() < 0.55 and index % 2 == 0
            latency = 800 if not correct else 7000
        events.append(
            {
                "learner_id": learner,
                "skill": SKILL,
                "item_id": f"item-{index:03d}",
                "correct": correct,
                "response": f"option-{(index % 4) + 1}",
                "ts": datetime.now(UTC).isoformat(),
                "seq": index,
                "latency_ms": latency,
            }
        )
    return events


def _observe(call: Caller, learner: str, count: int, offset: int = 0) -> None:
    status, _, body = call(
        "POST", "/v1/observe", {"events": _answers(learner, count, offset)}, idempotent=True
    )
    if status not in (200, 202):
        raise SystemExit(f"error: observe failed for {learner}: {status} {body}")


def _decide(call: Caller, learner: str) -> tuple[int, dict[str, str], Any]:
    return call(
        "POST",
        "/v1/decide",
        {"learner_id": learner, "skill": SKILL},
        idempotent=True,
    )


def cmd_seed(args: argparse.Namespace) -> int:
    settings = _settings()
    engine = _owner_engine()
    started = time.monotonic()
    print(f"synthetic sandbox — learners are generated, not real people (seed {SEED})")
    print(f"tenant slug: {args.slug}")

    try:
        tenant_id, key = provisioning.provision(
            engine, settings, slug=args.slug, name="Adapt sandbox (synthetic)", tier="deep"
        )
    except IntegrityError:
        # The unique constraint is the authority on slugs. A pre-check cannot be:
        # row-level security is forced on `tenants`, so a SELECT with no tenant set
        # sees nothing and would pass for every slug.
        print(
            f"error: tenant {args.slug!r} already exists. Pick another --slug, or delete "
            f"{STATE_FILE} and start again.",
            file=sys.stderr,
        )
        return 1
    _grant(engine, tenant_id, args.credits)
    engine.dispose()

    sandbox = Sandbox(
        slug=args.slug,
        tenant_id=str(tenant_id),
        key=key,
        connection_key="",
        learnings=None,
        learners=dict(LEARNERS),
    )
    with _serving(settings) as base:
        admin = Caller(base, key)
        status, _, created = admin(
            "POST",
            "/v1/admin/connections",
            {
                "host_name": "adapt-sandbox",
                "environment": "test",
                "event_quota_monthly": 250_000,
                "scopes": ["observe", "decide", "read_model"],
            },
        )
        if status != 201:
            raise SystemExit(f"error: could not create the sandbox connection: {status} {created}")
        call = Caller(base, created["key"]["full_key"])
        sandbox = Sandbox(
            slug=sandbox.slug,
            tenant_id=sandbox.tenant_id,
            key=sandbox.key,
            connection_key=created["key"]["full_key"],
            learnings=None,
            learners=sandbox.learners,
        )
        for learner in (LEARNERS["guesser"], LEARNERS["steady"], LEARNERS["overconfident"]):
            _observe(call, learner, args.events)
        wall = time.monotonic()
        for label in ("guesser", "steady", "overconfident"):
            learner = LEARNERS[label]
            status, _, body = _decide(call, learner)
            if status != 200:
                raise SystemExit(f"error: decide failed for {learner}: {status} {body}")
            verdict = "abstains" if body.get("abstain") else f"serves {body.get('action')}"
            reason = body.get("reason") or ", ".join(body.get("reason_codes", []))
            elapsed = time.monotonic() - wall
            print(f"  {label:<13} {learner:<28} {verdict} ({reason}) [{elapsed:0.1f}s]")
        status, _, credits_body = call("GET", "/v1/credits/balance")
        if status != 200:
            raise SystemExit(f"error: could not read the balance: {status} {credits_body}")

    sandbox.save()
    print(f"credits granted (owner step): {credits_body['granted']} — the sandbox seeds a grant")
    print(f"wrote {STATE_FILE} (mode 600, key inside; it is a throwaway tenant)")
    print(f"seed took {time.monotonic() - started:0.1f}s")
    print("\nnext: adapt sandbox demo")
    return 0


# ------------------------------------------------------------------- the demo


def _ts(started: float) -> str:
    return f"t+{time.monotonic() - started:05.2f}s"


def cmd_demo(args: argparse.Namespace) -> int:
    settings = _settings()
    started = time.monotonic()
    if not STATE_FILE.exists():
        print("no sandbox state found; run `adapt sandbox seed` first", file=sys.stderr)
        return 1
    sandbox = Sandbox.load()
    print(f"synthetic sandbox — learners are generated, not real people (seed {SEED})")
    print(f"tenant {sandbox.slug} · {_ts(started)} · clock {datetime.now(UTC).isoformat()}")
    print()

    with _serving(settings) as base:
        _demo_body(Caller(base, sandbox.connection_key), sandbox, settings, started, args)

    elapsed = time.monotonic() - started
    print()
    print(f"demo finished in {elapsed:0.1f}s")
    if elapsed > 180:
        print("error: the demo exceeded its three-minute budget", file=sys.stderr)
        return 1
    return 0


def _demo_body(
    call: Caller, sandbox: Sandbox, settings: Settings, started: float, args: argparse.Namespace
) -> None:

    # 1. The overview: the abstention/served pair from one query.
    now = datetime.now(UTC)
    admin = Caller(call.base, sandbox.key)
    window = urlencode(
        {
            "from": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "to": now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat(),
        }
    )
    status, _, report = admin("GET", f"/v1/admin/metrics/abstention?{window}")
    if status != 200:
        raise SystemExit(f"error: metrics read failed: {status} {report}")
    rate = "no data" if report["rate"] is None else f"{report['rate'] * 100:0.1f}%"
    served = (
        "no data" if report["served"]["rate"] is None else f"{report['served']['rate'] * 100:0.1f}%"
    )
    print(
        f"{_ts(started)} overview   decisions {report['denominator']} · abstention {rate} · "
        f"served {served} · excluded "
        f"{report['exclusions']['test_traffic']} test / "
        f"{report['exclusions']['paused_or_revoked']} paused / "
        f"{report['exclusions']['replays']} replays"
    )
    status, _, balance = admin("GET", "/v1/credits/balance")
    print(
        f"{_ts(started)} credits    granted {balance['granted']} · "
        f"available {balance['available']} · consumed {balance['consumed']} "
        f"· held {balance['held']}"
    )
    print()

    # 2. The money shot: a learner the engine has never seen.
    fresh = sandbox.learners["fresh"]
    status, _, first = call(
        "POST", "/v1/decide", {"learner_id": fresh, "skill": SKILL}, idempotent=True
    )
    if status == 404:
        # A learner must exist before a decision can be made about them; the
        # sandbox registers it with one observation, which the engine will then
        # still decline to score.
        _observe(call, fresh, 1)
        status, _, first = call(
            "POST", "/v1/decide", {"learner_id": fresh, "skill": SKILL}, idempotent=True
        )
    if status != 200:
        raise SystemExit(f"error: decide failed: {status} {first}")
    needs = first.get("needs") or {}
    trials = int(needs.get("trials", 0))
    print(f"{_ts(started)} learner    {fresh} — synthetic, and the engine owes it no number yet")
    print(
        f"{_ts(started)} decide     abstain={first['abstain']} reason={first.get('reason')} "
        f"needs={json.dumps(needs)} billed=0"
    )
    assert "value" not in first, "an abstention must not carry a value"

    # The engine named a number. The demo sends exactly that many, not a round
    # number chosen to make the screenshot look good.
    print(f"{_ts(started)} observe    sending exactly what needs asked for: {trials} events")
    _observe(call, sandbox.learners["fresh"], trials, offset=1)
    status, _, second = call(
        "POST", "/v1/decide", {"learner_id": fresh, "skill": SKILL}, idempotent=True
    )
    if status != 200:
        raise SystemExit(f"error: second decide failed: {status} {second}")
    if second["abstain"]:
        print(
            f"{_ts(started)} decide     still abstains: {second.get('reason')} "
            f"needs={json.dumps(second.get('needs'))} — reported, not retried"
        )
        return
    recommendation = second["recommendations"][0]
    interval = recommendation["confidence"]["interval"]
    print(
        f"{_ts(started)} decide     serves {recommendation['action']} "
        f"confidence {recommendation['confidence']['point']:.2f} "
        f"[{interval['low']:.2f}-{interval['high']:.2f}] "
        f"cites {', '.join(recommendation['cites'])}"
    )
    print(
        f"{_ts(started)} level      {second['level']['served']} served "
        f"(entitlement {second['level']['entitlement_level']}, limited_by "
        f"{second['level']['limited_by']})"
    )
    print()

    # 3. Replay: free, byte-identical, and logged rather than counted on the row.
    status, headers, replay = call("POST", f"/v1/decisions/{second['decision_id']}/replay")
    identical = replay == second
    print(
        f"{_ts(started)} replay     {status} identical={identical} "
        f"billed={headers.get('x-adapt-billed') or headers.get('X-Adapt-Billed')}"
    )
    assert identical, "a replay must return the stored body byte for byte"
    print()

    # 4. The three seeded learners, each read back as what they are.
    for label in ("guesser", "steady", "overconfident"):
        learner = sandbox.learners[label]
        status, _, mastery = admin("GET", f"/v1/mastery?learner_id={learner}")
        if status != 200:
            print(f"{_ts(started)} {label:<11} mastery read failed: {status}")
            continue
        row = mastery["skills"][0]
        if row.get("abstain"):
            summary = f"abstains ({row['reason']}, needs {json.dumps(row['needs'])})"
        else:
            summary = (
                f"value {row['value']:.2f} [{row['interval']['low']:.2f}-"
                f"{row['interval']['high']:.2f}] over {row['n']} trials, serving {row['served']}"
            )
        print(f"{_ts(started)} {label:<11} {summary}")

    status, _, final = admin("GET", f"/v1/admin/metrics/abstention?{window}")
    final_rate = "no data" if final["rate"] is None else f"{final['rate'] * 100:0.1f}%"
    print()
    print(
        f"{_ts(started)} overview   decisions {final['denominator']} · abstention {final_rate} · "
        f"replays {final['exclusions']['replays']} (counted, not billed)"
    )


def cmd_console(args: argparse.Namespace) -> int:
    """Serve the engine so the console is reachable at /console."""
    settings = _settings()
    import uvicorn

    from adapt_engine.main import create_app

    print("console: http://127.0.0.1:8000/console")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adapt", description="Adapt engine sandbox tools")
    sub = parser.add_subparsers(dest="command", required=True)

    sandbox = sub.add_parser("sandbox", help="a synthetic tenant, and the loop end to end")
    actions = sandbox.add_subparsers(dest="action", required=True)

    seed = actions.add_parser("seed", help="create the tenant, its key, and three learners")
    seed.add_argument("--slug", default="adapt-sandbox")
    seed.add_argument("--events", type=int, default=24)
    seed.add_argument("--credits", type=int, default=200)
    seed.add_argument("--force", action="store_true", help="accepted for symmetry; unused")
    seed.set_defaults(func=cmd_seed)

    demo = actions.add_parser("demo", help="run the loop and print it with timestamps")

    demo.set_defaults(func=cmd_demo)

    serve = sub.add_parser("console", help="serve the engine and its console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_console)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
