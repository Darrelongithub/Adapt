"""Increment 7: the console shell, and the rule that no number may be invented.

Three tests, and each one exists because something could go wrong quietly:

* **Traceability** — every value the console renders is declared in
  `console/data-map.json` and changes when the data changes. The second half is
  the one that catches a hardcoded number: `scripts/console_trace.mjs` renders
  the same module the browser loads against two states that differ in every leaf
  and fails if any value renders identically. A test that only checked ids would
  pass for a console made entirely of literals.
* **The null state** — an empty window reads "no data", not "0.0%". A console
  that reports 0% abstention on no traffic is a console that reports success for
  silence, which is the exact failure `METRICS.md` §1.8 warns about.
* **The screenshot** — the empty window renders to an image that contains the
  sentence a human needs ("No decisions in this window"), so the artifact under
  review is the real rendering and not a retyped mock.

The renderer runs in Node (`shutil.which("node")` guards it): the sandbox has no
headless browser, and shipping one is not worth it for text assertions.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from adapt_engine.auth.rate_limit import FailureLimiter
from adapt_engine.clock import SystemClock
from adapt_engine.credits import service as credits
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.main import create_app
from tests.conftest import make_settings
from tests.pg import bootstrap_key, provision_tenant

ROOT = Path(__file__).resolve().parents[1]  # engine/
CONSOLE = ROOT / "src" / "adapt_engine" / "console"
DATA_MAP = json.loads((CONSOLE / "data-map.json").read_text())["metrics"]
NODE = shutil.which("node")

SKILL = "skill.fractions"
LEARNER = "learn-console-0001"
#: A learner we know almost nothing about: two observations, so `/v1/mastery`
#: answers with an abstention and the `abstain`/`needs` branches of the renderer
#: are exercised by a live response rather than by a fixture.
ABSTAINING = "learn-console-0002"

#: Repeated decides on one learner: `POLLING_MIN_REPEATS` is 10, so eleven
#: repeats is the smallest number that makes the polling block non-empty.
REPEATS = 11
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


pytestmark = pytest.mark.skipif(NODE is None, reason="node is required to run the console renderer")


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(database, slug=f"con-{uuid.uuid4().hex[:8]}", name="Console Test")


@pytest.fixture
def admin_key(database, tenant: uuid.UUID):
    return bootstrap_key(database, tenant, scopes=("observe", "decide", "read_model", "admin"))


@pytest.fixture
def client(app_engine: Engine) -> TestClient:
    app = create_app(
        make_settings(),
        clock=SystemClock(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
        auth_limiter=FailureLimiter(clock=SystemClock(), max_failures=10_000),
    )
    return TestClient(app, raise_server_exceptions=False)


def _admin(client: TestClient, admin_key, path: str, **params):
    resp = client.get(
        path, params=params, headers={"Authorization": f"Bearer {admin_key.full_key}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(learner: str, count: int) -> list[dict]:
    return [
        {
            "learner_id": learner,
            "skill": SKILL,
            "item_id": f"item-{i:03d}",
            "correct": i % 3 != 0,
            "response": f"option-{i}",
            "ts": NOW.isoformat(),
            "seq": i,
        }
        for i in range(count)
    ]


def _observe(client: TestClient, key: str, learner: str, count: int) -> None:
    resp = client.post(
        "/v1/observe",
        json={"events": _events(learner, count)},
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": f"batch-{uuid.uuid4().hex}"},
    )
    assert resp.status_code == 202, resp.text


def _decide(client: TestClient, key: str, learner: str):
    return client.post(
        "/v1/decide",
        json={"learner_id": learner, "skill": SKILL},
        headers={"Authorization": f"Bearer {key}", "Idempotency-Key": f"decide-{uuid.uuid4().hex}"},
    )


def _connection(client: TestClient, admin_key, host: str) -> str:
    resp = client.post(
        "/v1/admin/connections",
        json={
            "host_name": host,
            "environment": "test",
            "event_quota_monthly": 50_000,
            "scopes": ["observe", "decide", "read_model"],
        },
        headers={"Authorization": f"Bearer {admin_key.full_key}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]["full_key"]


def _live_state(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID
) -> tuple[dict, dict, dict[str, list[object]]]:
    """Two connections, twenty-two decisions, and every block on screen populated.

    The state is built to make *every* console block carry a number that a
    mutation can move:

    * **polling needs two connections.** One connection can never be five times
      its own baseline, so the quiet connection establishes a baseline of 0.0 and
      the repeating one alerts. A single-connection fixture would leave the
      polling block rendering a computed zero that no state change can move —
      exactly where a hardcoded zero would hide.
    * **the abstaining learner** supplies the `abstain`/`needs` branch of the
      inspector and the `skills[].abstain` path in the map.
    * **the empty window** is a day in the past, read through the same endpoint.
    """
    repeaty = _connection(client, admin_key, "console-host")
    quiet = _connection(client, admin_key, "console-host-quiet")

    with tenant_transaction(app_engine, tenant) as conn:
        credits.grant_credits(
            conn,
            tenant_id=tenant,
            kind="purchased",
            amount=100,
            expires_at=None,
            idempotency_key=f"grant-{uuid.uuid4().hex}",
        )

    # The repeating connection: one learner, twelve decides, eleven repeats.
    _observe(client, repeaty, LEARNER, 12)
    for _ in range(REPEATS + 1):
        assert _decide(client, repeaty, LEARNER).status_code == 200

    # The quiet connection: ten distinct learners, one decide each, no repeats.
    for index in range(10):
        learner = f"learn-console-q{index:03d}"
        _observe(client, quiet, learner, 12)
        assert _decide(client, quiet, learner).status_code == 200

    # A learner we know almost nothing about: two observations, read back as
    # `insufficient_evidence`, which is the inspector's abstention branch.
    _observe(client, quiet, ABSTAINING, 2)
    abstained = _admin(client, admin_key, "/v1/mastery", learner_id=ABSTAINING)
    assert abstained["skills"][0]["abstain"] is True

    now = datetime.now(UTC)
    populated = {
        "metrics": _admin(
            client,
            admin_key,
            "/v1/admin/metrics/abstention",
            **{
                "from": (now - timedelta(days=1)).isoformat(),
                "to": (now + timedelta(days=1)).isoformat(),
            },
        ),
        "credits": _admin(client, admin_key, "/v1/credits/balance"),
        "connections": _admin(client, admin_key, "/v1/admin/connections"),
        "mastery": _admin(client, admin_key, "/v1/mastery", learner_id=LEARNER),
        "learnerId": LEARNER,
    }
    quiet_window = _admin(
        client,
        admin_key,
        "/v1/admin/metrics/abstention",
        **{"from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00"},
    )
    assert quiet_window["denominator"] == 0, "the empty window must really be empty"
    empty = {**populated, "metrics": quiet_window, "mastery": None, "learnerId": None}
    # A developer affordance: set ADAPT_CONSOLE_STATE_DIR to dump the two states
    # the screenshot script renders, so the committed image comes from a live
    # response rather than from a hand-written object. Nothing is written unless
    # the variable is set.
    if dump := os.environ.get("ADAPT_CONSOLE_STATE_DIR"):
        for name, payload in (("populated", populated), ("empty", empty)):
            (Path(dump) / f"{name}.json").write_text(json.dumps(payload, indent=2))
    bodies = {
        "/v1/admin/metrics/abstention": [populated["metrics"], quiet_window],
        "/v1/credits/balance": [populated["credits"]],
        "/v1/admin/connections": [populated["connections"]],
        "/v1/mastery": [populated["mastery"], abstained],
    }
    return populated, empty, bodies


def _mutate(value):
    """Every leaf changed, and every list longer — the strongest statement of
    'this is not a literal'.

    Editing leaves alone misses counts: `alerting.length` stays 1 however the
    entries change, so a hardcoded "1" would pass. Duplicating a row moves the
    count as well as the contents, which is what a real second footprint does.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value + 1
    if isinstance(value, str):
        return value + "-x"
    if isinstance(value, list):
        items = [_mutate(item) for item in value]
        if items and isinstance(items[0], dict):
            items = [*items, dict(items[0])]
        return items
    if isinstance(value, dict):
        return {key: _mutate(item) for key, item in value.items()}
    return value


def _node(*args: str) -> subprocess.CompletedProcess[str]:
    if NODE is None:
        pytest.fail("node is required to run the console renderer (install Node 18+)")
    return subprocess.run(
        [NODE, *args],  # type: ignore[list-item]
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def test_every_console_value_traces_to_an_endpoint_and_changes_with_the_data(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID, tmp_path: Path
) -> None:
    """The traceability gate: declared ids, live numbers, and no literals.

    The renderer is handed the *live* responses and a copy with every leaf
    mutated; if any value renders the same text twice it is a hardcoded number
    and this fails. The declared-id check runs first, so an untraceable value is
    reported as untraceable rather than as a mutation problem.
    """
    populated, _, _ = _live_state(client, admin_key, app_engine, tenant)
    result = _node(
        "scripts/console_trace.mjs",
        "--state",
        str(_write(tmp_path, "state.json", populated)),
        "--mutated",
        str(_write(tmp_path, "mutated.json", _mutate(populated))),
        "--map",
        str(CONSOLE / "data-map.json"),
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["values"] >= 20, "the console rendered suspiciously little"

    # And the pair from one denominator: both rates are on screen, from one count.
    assert populated["metrics"]["denominator"] == REPEATS + 1 + 10
    assert populated["metrics"]["rate"] == 0.0
    assert populated["metrics"]["served"]["rate"] == 1.0
    assert populated["metrics"]["abstention_polling"]["alerting"], (
        "the live state must carry a polling alert, or the polling block is all zeros"
    )


def test_every_declared_path_resolves_in_a_live_response(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID
) -> None:
    """A data-map entry that points at nothing is a documentation lie with a test.

    Each declared path is walked against the live body its endpoint returned.
    Arrays are checked element-wise: at least one element must carry the leaf,
    because a list of empty objects would otherwise pass.
    """
    _, _, bodies = _live_state(client, admin_key, app_engine, tenant)

    for metric_id, entry in DATA_MAP.items():
        endpoint, path = entry["endpoint"], entry["path"]
        assert endpoint in bodies, f"{metric_id} names an endpoint with no live response"
        # A path may be carried by only one shape of response: `skills[].abstain`
        # is on the abstained body and `skills[].value` on the served one, so the
        # check is "resolves in at least one live response", not "in every one".
        assert any(_resolves(body, path) for body in bodies[endpoint]), (
            f"{metric_id}: {path} resolves in none of the live {endpoint} responses"
        )


def _resolves(body: object, path: str) -> bool:
    """Walk `a.b[].c`: `[]` means 'some element carries the rest of the path'.

    An empty list resolves — "no connections" is a real answer, and the console
    renders it as a sentence. A path into a list that no element carries is a map
    entry pointing at nothing, which is what this test exists to catch.
    """
    return _walk(body, path.split(".") if path else [])


def _walk(node: object, parts: list[str]) -> bool:
    if not parts:
        return True
    head, rest = parts[0], parts[1:]
    array = head.endswith("[]")
    key = head[:-2] if array else head
    if key:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    if array:
        if not isinstance(node, list):
            return False
        return not node or any(_walk(item, rest) for item in node)
    return _walk(node, rest)


def test_the_empty_window_reads_no_data_not_zero(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID, tmp_path: Path
) -> None:
    """0% on no traffic is not success. The console says so, in words."""
    _, empty, _ = _live_state(client, admin_key, app_engine, tenant)
    assert empty["metrics"]["rate"] is None
    assert empty["metrics"]["served"]["rate"] is None

    state = _write(tmp_path, "empty.json", empty)
    result = _node("scripts/console_trace.mjs", "--state", str(state), "--expect-null")
    assert result.returncode == 0, result.stderr
    rendered = {entry["metric"]: entry["text"] for entry in json.loads(result.stdout)["rendered"]}
    assert rendered["overview.abstention.rate"] == "no data"
    assert "No decisions in this window" in rendered["overview.denominator.sentence"]


def test_the_empty_window_renders_to_an_image(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID, tmp_path: Path
) -> None:
    """The screenshot artifact is generated from the renderer, not from a mock.

    ImageMagick turns the SVG into a PNG when it is installed; the assertions are
    on the text, so the test does not depend on a rasteriser being present.
    """
    _, empty, _ = _live_state(client, admin_key, app_engine, tenant)
    svg = tmp_path / "empty-window.svg"
    result = _node(
        "scripts/console_shot.mjs",
        "--state",
        str(_write(tmp_path, "s.json", empty)),
        "--out",
        str(svg),
    )
    assert result.returncode == 0, result.stderr
    text = svg.read_text()
    assert "No decisions in this window" in text
    assert "no data" in text
    assert "0.0%" not in text.split("Abstention")[1].split("Credits")[0]
    assert len(text) > 2_000

    committed = ROOT.parent / "docs" / "console" / "empty-window.png"
    assert committed.exists(), "the reviewed screenshot is missing from docs/console/"
    assert committed.stat().st_size > 10_000


def test_the_console_is_a_static_page_with_declared_assets_only(client: TestClient) -> None:
    """`/console` ships as files, and only the files it declares.

    The page holds no data and no key: it asks for one, keeps it in
    `sessionStorage`, and reads four endpoints with it. A console that fetched
    server-side would need the key on the server, which is one more place for it
    to leak — and every number would then be ours to fabricate.
    """
    index = client.get("/console")
    assert index.status_code == 200
    assert "Adapt console" in index.text
    assert "/console/app.js" in index.text

    for asset, marker in (
        ("app.js", "renderConsole"),
        ("styles.css", ".card"),
        ("data-map.json", "overview.abstention.rate"),
    ):
        response = client.get(f"/console/{asset}")
        assert response.status_code == 200, asset
        assert marker in response.text, asset

    unknown = client.get("/console/secret.env")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "route_unknown"
