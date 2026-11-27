"""Increment 7 and 8: the console shell, and the rule that no number may be invented.

Thirteen tests, and each one exists because something could go wrong quietly. The
first three are increment 7's traceability gates; the `test_palette_*` and
`test_the_palette_*` tests are increment 8's `CONSOLE_STYLE.md` §6 gates, which
exist because a palette in a document is a palette until something can fail on it;
and increment 9's display corrections are four more — the five defects a reader
found in the screenshot that the traceability gate could not see, because it
proves provenance and not formatting:

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
* **The palette (§6 gate 1)** — one hex, in `:root`, and nowhere else. A colour
  that drifted into a rule is a second source of truth.
* **Contrast (§6 gate 2)** — the §3 pairs are recomputed from the stylesheet and
  compared with the numbers the document published. A changed hex is a red test
  with the pair printed, not a review comment.
* **The accent budget (§6 gate 3)** — no screen may paint more than three
  elements in terracotta; on a populated screen each of the three screens carries
  at least the one mark it is allowed.
* **Screenshots read the tokens (§6 gate 4)** — the two renderers parse `:root`
  from `styles.css`; neither carries a palette. Proven by rendering with an edited
  stylesheet and finding the edit in the pixels.
* **No number reaches the screen raw (§4)** — every run of text in the render is
  grepped for a third decimal and for a trailing zero beyond the type's own
  precision, so `0.5400` cannot come back through a new cell.
* **The accent is earned (§1)** — a zero prints in ink with no accent, and a rate
  a reader would act on carries exactly one; both halves, or the budget is vacuous.
* **A zero baseline is never a multiple (METRICS §1.5)** — null from the API, words
  on the screen, the repeat count named.
* **One identity per object, and a level sentence that cannot contradict itself
  (§5)** — four level cases, and a short id instead of a UUID.

The renderer runs in Node (`shutil.which("node")` guards it): the sandbox has no
headless browser, and shipping one is not worth it for text assertions.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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
from tests.formats import numeric_sins
from tests.pg import bootstrap_key, provision_tenant

ROOT = Path(__file__).resolve().parents[1]  # engine/
CONSOLE = ROOT / "src" / "adapt_engine" / "console"
DATA_MAP = json.loads((CONSOLE / "data-map.json").read_text())["metrics"]
NODE = shutil.which("node")
STYLES = CONSOLE / "styles.css"

#: `CONSOLE_STYLE.md` §3, as `(ink token, surface token, floor, published ratio)`.
#: The published ratio is checked too: the document and the stylesheet must not
#: drift apart in either direction, because §3 is what a reviewer reads instead
#: of opening the CSS.
CONTRAST = (
    ("--ink", "--bg", 4.5, 13.56),
    ("--ink", "--surface", 4.5, 15.03),
    ("--ink-body", "--bg", 4.5, 8.44),
    ("--ink-body", "--surface", 4.5, 9.36),
    ("--ink-muted", "--bg", 4.5, 5.04),
    ("--ink-muted", "--surface", 4.5, 5.58),
    ("--blue", "--bg", 4.5, 9.89),
    ("--blue", "--surface", 4.5, 10.96),
    ("--surface", "--blue", 4.5, 10.96),
    ("--alert", "--surface", 4.5, 8.73),
    ("--null", "--surface", 4.5, 6.01),
    ("--ok", "--surface", 4.5, 5.29),
    # Terracotta is a mark, not an ink: 3:1 is the UI/large-text floor and 4.5 is
    # explicitly *not* claimed for it (CONSOLE_STYLE §3, consequence 1).
    ("--accent", "--surface", 3.0, 3.78),
    ("--surface", "--accent-fill", 4.5, 5.71),
    # A hairline: deliberately under 3:1, so it can never be the only affordance.
    ("--rule", "--bg", 1.0, 1.27),
)

#: The two raw hues §3 retires from text roles, and what they measured.
RETIRED = (("#C99A2E", 2.19), ("#7F9A83", 2.61))

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


def _render(tmp_path: Path, name: str, state: dict) -> dict:
    """Render a state to the shot's block model — every run of text, with style."""
    model = tmp_path / f"{name}.json"
    result = _node(
        "scripts/console_shot.mjs",
        "--state",
        str(_write(tmp_path, f"{name}-state.json", state)),
        "--out",
        str(tmp_path / f"{name}.svg"),
        "--json",
        str(model),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(model.read_text())


def _texts(shot: dict) -> list[str]:
    """Every string the picture draws — labels, values, sentences, table cells."""
    out: list[str] = []
    for block in shot["blocks"]:
        if block["kind"] == "row":
            out += [block["label"], block["text"]]
        elif block["kind"] == "sentence":
            out.append(block["text"])
        elif block["kind"] == "table":
            out += [cell["text"] for cell in block["header"]]
            for cells in block["rows"]:
                out += [cell["text"] for cell in cells]
        else:
            out.append(str(block.get("text", "")))
    return out


def _rows(shot: dict) -> dict[str, dict]:
    return {block["label"]: block for block in shot["blocks"] if block["kind"] == "row"}


def _variant(state: dict, mutate) -> dict:
    copy = json.loads(json.dumps(state))
    mutate(copy)
    return copy


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
    model = tmp_path / "empty.json"
    result = _node(
        "scripts/console_shot.mjs",
        "--state",
        str(_write(tmp_path, "s.json", empty)),
        "--out",
        str(svg),
        "--json",
        str(model),
    )
    assert result.returncode == 0, result.stderr
    # §6 gate 3 on the screen where it matters most: nothing to report is not a
    # reason to reach for terracotta. "no data" is mustard and italic, not a mark.
    accents = json.loads(model.read_text())["accents"]
    assert accents["overview"] == 0, f"the empty window marks {accents['overview']} elements"
    assert all(count <= 3 for count in accents.values()), accents
    text = svg.read_text()
    assert "No decisions in this window" in text
    assert "no data" in text
    # Card titles render uppercase (§4), so the section is cut on the drawn text.
    abstention = text.split("ABSTENTION", 1)[1].split("CREDITS", 1)[0]
    assert "0.0%" not in abstention, "the empty window must not print a percentage"
    assert len(text) > 2_000

    committed = ROOT.parent / "docs" / "console" / "empty-window.png"
    assert committed.exists(), "the reviewed screenshot is missing from docs/console/"
    assert committed.stat().st_size > 10_000


def test_the_accent_is_earned_by_a_value_you_would_act_on(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID, tmp_path: Path
) -> None:
    """CONSOLE_STYLE §1: the budget is a ceiling, and the mark is earned.

    A terracotta `0.0%` above the sentence that says 0% is not success argues with
    the sentence. So the live state — 0 abstentions in 22 decisions — must carry
    **no** accent on Overview, and a rate a reader would act on must carry exactly
    one. Both halves are asserted, because a budget with only a ceiling is also
    satisfied by a console that never marks anything, and that is a different bug.
    """
    populated, _, _ = _live_state(client, admin_key, app_engine, tenant)
    shot = _render(tmp_path, "earned", populated)

    assert populated["metrics"]["rate"] == 0.0
    assert shot["accents"]["overview"] == 0, f"a zero is not a highlight: {shot['accents']}"
    assert all(count <= shot["maxAccents"] for count in shot["accents"].values()), shot["accents"]
    zero = _rows(shot)["rate"]
    assert zero["style"]["color"].lower() == _root_tokens()["--ink"].lower()
    assert zero["style"]["size"] == 13, "an unearned mark must not be 19px either"

    # The same screen, with a rate a reader would act on: 4 of 22 = 18.2%.
    def severe(state: dict) -> None:
        state["metrics"]["numerator"] = 4
        state["metrics"]["rate"] = 0.1818
        state["metrics"]["interval"] = {"low": 0.0727, "high": 0.3939}

    marked = _render(tmp_path, "severe", _variant(populated, severe))
    assert marked["accents"]["overview"] == 1, marked["accents"]
    earned = _rows(marked)["rate"]
    assert earned["text"] == "18.2%"
    assert earned["style"]["color"].lower() == _root_tokens()["--accent"].lower()
    assert earned["style"]["size"] == 19, "terracotta at 13px is illegal (§3, 3.78:1)"
    assert earned["style"]["weight"] == 700

    # And the other accent a screen may spend (§5): a serving level below its
    # entitlement. The live tenant has no gap, so the chip is blue and costs nothing.
    assert shot["accents"]["learner"] == 0, shot["accents"]

    def gap(state: dict) -> None:
        state["mastery"]["skills"][0]["entitlement_level"] = "L2"

    gapped = _render(tmp_path, "gap", _variant(populated, gap))
    assert gapped["accents"]["learner"] == 1, gapped["accents"]
    chip_runs = [
        run
        for block in gapped["blocks"]
        if block["kind"] == "table"
        for cells in block["rows"]
        for cell in cells
        for run in cell.get("runs") or []
        if run.get("text") == "L1"
    ]
    assert chip_runs, "the serving level is not on the screen"
    assert any(run["style"].get("borderLeft") for run in chip_runs), "the gap chip is not accented"
    assert all(
        run["style"]["color"].lower() == _root_tokens()["--ink"].lower() for run in chip_runs
    )


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


# --------------------------------------------------------------------- palette


def _root_tokens(path: Path = STYLES) -> dict[str, str]:
    """`--token: hex;` from every `:root` block — the one place a hex may live."""
    css = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    tokens: dict[str, str] = {}
    for block in re.findall(r":root\s*\{([^}]*)\}", css):
        for declaration in block.split(";"):
            if ":" in declaration:
                name, value = declaration.split(":", 1)
                tokens[name.strip()] = value.strip()
    return tokens


def _luminance(colour: str) -> float:
    digits = colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    assert len(digits) == 6, f"{colour} is not a hex colour"
    channels = []
    for index in (0, 2, 4):
        value = int(digits[index : index + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _ratio(one: str, two: str) -> float:
    lighter, darker = sorted((_luminance(one), _luminance(two)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def test_the_palette_lives_in_root_and_nowhere_else() -> None:
    """§6 gate 1. One hex, in `:root`; a colour that drifted into a rule is a
    second source of truth and the first one stops being enforceable."""
    stray: list[str] = []
    styles = STYLES.read_text()
    outside = re.sub(r":root\s*\{[^}]*\}", "", styles)
    stray += [f"styles.css outside :root: {hit}" for hit in HEX.findall(outside)]

    for path in sorted(CONSOLE.rglob("*")):
        if not path.is_file() or path == STYLES or path.suffix == ".pyc":
            continue
        stray += [f"{path.relative_to(CONSOLE)}: {hit}" for hit in HEX.findall(path.read_text())]

    # §6 gate 4: the screenshot scripts must not carry their own copy of the
    # palette, so they are held to the same rule as the page.
    for name in ("console_shot.mjs", "console_shot.py"):
        script = (ROOT / "scripts" / name).read_text()
        stray += [f"scripts/{name}: {hit}" for hit in HEX.findall(script)]
        assert '":root"' in script or ":root" in script, f"{name} does not parse :root"

    assert not stray, "hex outside :root: " + ", ".join(stray)


def test_every_published_contrast_pair_still_measures_what_the_document_says() -> None:
    """§6 gate 2. Computed, not asserted: the pair and the ratio are in the message."""
    tokens = _root_tokens()
    assert tokens, "styles.css has no :root block"
    failures: list[str] = []
    for foreground, background, floor, published in CONTRAST:
        one, two = tokens.get(foreground), tokens.get(background)
        assert one and two, f"{foreground} / {background} is declared in §3 but missing from :root"
        measured = _ratio(one, two)
        if measured < floor:
            failures.append(
                f"{foreground} {one} on {background} {two}: {measured:.2f} < {floor:.2f} "
                f"(§3 published {published:.2f})"
            )
        elif abs(measured - published) > 0.011:
            failures.append(
                f"{foreground} on {background}: {measured:.2f} but §3 publishes {published:.2f} "
                "— the stylesheet and the document have drifted"
            )
    # Terracotta is legal as a mark *because* it is under the body-text floor;
    # a future palette that "fixes" that silently would be a different design.
    assert _ratio(tokens["--accent"], tokens["--surface"]) < 4.5
    assert _ratio(tokens["--rule"], tokens["--bg"]) < 3.0

    for retired, published in RETIRED:
        assert retired.lower() not in STYLES.read_text().lower(), (
            f"{retired} is retired from text roles"
        )
        measured = _ratio(retired, tokens["--bg"])
        if measured >= 4.5:
            failures.append(f"{retired} measures {measured:.2f}: it would no longer be retired")
        if abs(measured - published) > 0.011:
            failures.append(f"{retired} on --bg: {measured:.2f}, §3 says {published:.2f}")

    assert not failures, "contrast: " + "; ".join(failures)


def test_the_screenshot_palette_is_parsed_from_the_stylesheet(tmp_path: Path) -> None:
    """§6 gate 4, proven in the pixels: edit `:root`, and the picture follows.

    A script that kept its own copy would render the old beige here, which is the
    lie the gate exists to prevent.
    """
    pytest.importorskip("PIL", reason="Pillow is a dev extra")
    from PIL import Image  # imported after the skip: Pillow is a dev extra

    styles = tmp_path / "styles.css"
    source = STYLES.read_text()
    assert source.count("#F3ECDF") == 1, "the beige must be declared once, in :root"
    styles.write_text(source.replace("#F3ECDF", "#00FF00"))
    blocks = tmp_path / "blocks.json"
    blocks.write_text(
        json.dumps({"width": 1180, "heading": {"title": "t", "subtitle": "s"}, "blocks": []})
    )
    png = tmp_path / "shot.png"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/console_shot.py",
            "--styles",
            str(styles),
            "--blocks",
            str(blocks),
            "--out",
            str(png),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Image.open(png).convert("RGB").getpixel((1, 1)) == (0, 255, 0)

    # And the SVG side: the stylesheet's background is what the vector says.
    state = _write(
        tmp_path,
        "state.json",
        {
            "metrics": {
                "rate": None,
                "numerator": 0,
                "denominator": 0,
                "interval": {"low": None, "high": None},
                "served": {"rate": None},
                "reasons": {},
                "exclusions": {"test_traffic": 0, "paused_or_revoked": 0, "replays": 0},
                "by_connection": [],
                "abstention_polling": {
                    "baseline_repeat_ratio": 0.0,
                    "alerting": [],
                    "multiple": 5,
                    "min_repeats": 10,
                    "by_connection": [],
                },
            },
            "credits": {"available": 0, "held": 0, "consumed": 0, "forfeited": 0, "granted": 0},
            "connections": [],
            "mastery": None,
            "learnerId": None,
        },
    )
    svg = tmp_path / "shot.svg"
    result = _node(
        "scripts/console_shot.mjs",
        "--styles",
        str(styles),
        "--state",
        str(state),
        "--out",
        str(svg),
    )
    assert result.returncode == 0, result.stderr
    assert "#00FF00" in svg.read_text()


# ------------------------------------------------------- formatting by type (§4)


def test_no_number_reaches_the_screen_raw(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID, tmp_path: Path
) -> None:
    """§4's formatting rule, enforced on every run of text the picture draws.

    The traceability gate proves a value came from an endpoint; it cannot see that
    `0.5400` claims four digits of a number the estimator gives two. This test can,
    and it is deliberately a grep over the render rather than a list of known
    cells, so a new cell cannot bring the habit back with it.
    """
    populated, empty, _ = _live_state(client, admin_key, app_engine, tenant)

    for name, state in (("populated", populated), ("empty", empty)):
        shot = _render(tmp_path, name, state)
        offenders = [sin for text in _texts(shot) for sin in numeric_sins(text)]
        assert not offenders, "; ".join(offenders)

    shot = _render(tmp_path, "formats", populated)
    rows = _rows(shot)
    assert rows["rate"]["text"] == "0.0%", "a rate is one decimal and a percent sign"
    assert rows["95% interval"]["text"] == "[0.00, 0.15]", "an interval is two decimals in brackets"
    assert rows["abstentions / decisions"]["text"] == "0 / 22", "a count is an integer"
    assert rows["base" if "base" in rows else "baseline repeat ratio"]["text"] == "0.00"

    cells = [
        cell["text"]
        for block in shot["blocks"]
        if block["kind"] == "table"
        for row in block["rows"]
        for cell in row
    ]
    assert "0.54" in cells, f"an estimate is two decimals: {cells}"
    assert "[0.33, 0.75]" in cells


def test_a_zero_baseline_is_never_rendered_as_a_multiple(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID, tmp_path: Path
) -> None:
    """METRICS §1.5: with no baseline there is no multiplier, and the screen says why.

    The live state has the shape that produced the defect: a quiet connection sets
    the baseline to 0.00, a loud one repeats eleven times, and the console used to
    print "5.0x baseline" from the configured constant — a threshold that did not
    exist, next to a baseline that said it could not.
    """
    populated, _, _ = _live_state(client, admin_key, app_engine, tenant)
    polling = populated["metrics"]["abstention_polling"]
    assert polling["baseline_repeat_ratio"] == 0.0
    assert polling["multiple"] is None, "the API must not send a multiplier at a zero baseline"

    shot = _render(tmp_path, "zero-baseline", populated)
    rows = _rows(shot)
    threshold = rows["alert threshold"]["text"]
    assert threshold.startswith("baseline is zero — decided on the raw ratio"), threshold
    assert "5\u00d7 threshold is not applied" in threshold, "the constant is named, as a constant"
    assert "5\u00d7 baseline" not in threshold, "a multiplier of zero does not exist"

    sentence = next(
        block["text"] for block in shot["blocks"] if "repeating decide" in str(block.get("text"))
    )
    assert "1 of 2 connections is repeating" in sentence, sentence
    assert "are repeating" not in sentence, "the verb agrees with the count that leads the clause"
    assert "raw ratio" in sentence
    assert f"{sum(row['repeats'] for row in polling['by_connection'])} repeats" in sentence, (
        sentence
    )
    assert f"{polling['min_repeats']}-repeat floor" in sentence, sentence

    # A real baseline keeps the multiple, and prints it without a trailing zero.
    def measurable(state: dict) -> None:
        state["metrics"]["abstention_polling"]["baseline_repeat_ratio"] = 0.2
        state["metrics"]["abstention_polling"]["multiple"] = 5.0

    marked = _render(tmp_path, "baseline", _variant(populated, measurable))
    assert _rows(marked)["alert threshold"]["text"] == "5\u00d7 baseline"


def test_the_level_sentence_is_a_function_of_three_axes(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID, tmp_path: Path
) -> None:
    """The sentence most likely to be quoted must not contradict itself.

    It used to read "serving L1 where validation has not passed (capability L1,
    validated L1)" — a gap asserted in one clause and denied in the next. Four
    cases, one clause each.
    """
    populated, _, _ = _live_state(client, admin_key, app_engine, tenant)

    def sentence(**level) -> str:
        def change(state: dict) -> None:
            state["mastery"]["skills"][0].update(level)

        shot = _render(
            tmp_path,
            f"level-{len(level)}-{abs(hash(str(level))) % 10_000}",
            _variant(populated, change),
        )
        return next(
            block["text"]
            for block in shot["blocks"]
            if str(block.get("text", "")).startswith("Entitled to")
        )

    agreed = sentence(entitlement_level="L1", served="L1", validation_level="L1", limited_by=None)
    assert "Entitled to L1; serving L1, validated at L1." in agreed
    assert "No gap between entitlement, capability and validation." in agreed
    assert "where validation has not passed" not in agreed

    unvalidated = sentence(
        entitlement_level="L1", served="L1", validation_level="L0", limited_by=None
    )
    assert "validated at L0" in unvalidated
    assert "The L1 claim has not passed validation at any level." in unvalidated

    held = sentence(entitlement_level="L2", served="L1", validation_level="L1", limited_by=None)
    assert "The L2 claim has not passed validation, so serving stops at L1." in held

    limited = sentence(
        entitlement_level="L3", served="L1", validation_level="L1", limited_by="tier_cap"
    )
    assert 'Limited by "tier_cap", a tenant setting rather than the evidence.' in limited
    assert "No gap" not in limited


def test_one_identity_per_object(
    client: TestClient, admin_key, app_engine: Engine, tenant: uuid.UUID, tmp_path: Path
) -> None:
    """The same connection is named the same way on both screens (§5).

    The by-connection table printed a 36-character UUID while the table above it
    printed the host name — one object, two identities, and the UUID wrapped. A
    connection with no host name (or one the console has not loaded) gets a short
    monospace id instead of the whole thing.
    """
    populated, _, _ = _live_state(client, admin_key, app_engine, tenant)
    hosts = {connection["id"]: connection["host_name"] for connection in populated["connections"]}

    shot = _render(tmp_path, "identity", populated)
    table = next(
        block
        for block in shot["blocks"]
        if block["kind"] == "table" and block["header"][0]["text"] == "connection"
    )
    rendered = [cells[0] for cells in table["rows"]]
    for cell in rendered:
        assert cell["text"] in hosts.values(), f"{cell['text']} is not a host name"
        assert len(cell["text"]) < 36, "a full UUID is not an identity"
        assert cell["style"].get("family") != "serif"

    # A connection the console cannot name falls back to a short mono id, not a UUID.
    def anonymous(state: dict) -> None:
        for connection in state["connections"]:
            connection["host_name"] = ""

    fallback = _render(tmp_path, "anonymous", _variant(populated, anonymous))
    table = next(
        block
        for block in fallback["blocks"]
        if block["kind"] == "table" and block["header"][0]["text"] == "connection"
    )
    for cells in table["rows"]:
        cell = cells[0]
        assert len(cell["text"]) == 9 and cell["text"].endswith("…"), cell["text"]
        runs = cell.get("runs") or []
        assert runs and all(run["style"]["family"] == "sans" for run in runs)
