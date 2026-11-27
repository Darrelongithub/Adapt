"""Increment 7: `adapt sandbox seed` and `adapt sandbox demo`, end to end.

The acceptance criterion is a demo a stranger can run on a clean checkout in under
three minutes that shows the loop without a single invented number. So the tests
assert the parts that would let it lie:

* `seed` creates a real tenant, a real key and three *labelled synthetic* learners,
  and says what the engine did with each of them;
* `demo` prints an abstention with `needs`, then serves a cited recommendation only
  after the events `needs` asked for — and the transcript carries timestamps;
* the replay is byte-identical and billed at zero, with the ledger agreeing;
* the whole thing finishes inside the budget, measured here rather than promised.

The CLI talks HTTP to a real uvicorn instance on a loopback port (see
`adapt_engine.cli._serving`), so these tests exercise the surface a host gets.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from adapt_engine import cli
from adapt_engine.db.tenant import tenant_transaction
from tests import pg
from tests.conftest import TEST_MASTER_KEY, TEST_PEPPER


@pytest.fixture
def _isolated() -> Iterator[pg.Database]:
    """A database of this test file's own, with a name nobody else will reuse.

    The default `database` fixture is function-scoped but shares one database
    *name*, so a run that fails to drop it leaves a database behind — and a
    database that once held a tenant rotated to master key `k2` makes the app's
    startup gate refuse to start under `k1`, correctly. A unique name removes the
    ambiguity instead of explaining it in a comment under a failing test.
    """
    database = pg.provision(f"cli_{uuid.uuid4().hex[:10]}")
    try:
        yield database
    finally:
        pg.drop_database(database)


@pytest.fixture
def sandbox_env(_isolated, monkeypatch, tmp_path: Path) -> Path:
    """The environment a host would export, pointed at this test database."""
    monkeypatch.setenv("ADAPT_ENV", "test")
    monkeypatch.setenv("ADAPT_DATABASE_URL", _isolated.app_url)
    monkeypatch.setenv("ADAPT_MIGRATION_DATABASE_URL", _isolated.owner_url)
    monkeypatch.setenv("ADAPT_MASTER_KEY", TEST_MASTER_KEY)
    monkeypatch.setenv("ADAPT_KEY_PEPPER", TEST_PEPPER)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed() -> tuple[int, str]:
    started = time.monotonic()
    code = cli.main(["sandbox", "seed", "--slug", f"cli-{uuid.uuid4().hex[:8]}", "--events", "16"])
    return code, f"{time.monotonic() - started:0.1f}s"


@pytest.fixture
def seeded(sandbox_env: Path, capsys) -> tuple[dict, str]:
    code, _ = _seed()
    out = capsys.readouterr().out
    assert code == 0, out
    state = json.loads((sandbox_env / ".adapt-sandbox.json").read_text())
    return state, out


def test_seed_labels_the_learners_synthetic_and_reports_what_the_engine_did(
    seeded, _isolated
) -> None:
    state, out = seeded

    assert "synthetic" in out.splitlines()[0].lower(), (
        "the first line must say what these learners are"
    )
    assert state["key"].startswith(("ak_test_", "ak_live_"))
    assert state["learners"]["guesser"].startswith("learn-sandbox-")
    assert set(state["learners"]) == {"guesser", "steady", "overconfident", "fresh"}
    assert (Path(".adapt-sandbox.json").stat().st_mode & 0o777) == 0o600

    # The three seeded learners each got a verdict, and each verdict is a word the
    # engine produced rather than a template: "abstains" or "serves <action>".
    verdicts = re.findall(r"(abstains|serves \w+)", out)
    assert len(verdicts) == 3, out

    # The sandbox tenant is `deep`, which is what makes the console's level
    # sentence honest: entitled to L3, served L1, and it says why.
    with tenant_transaction(
        _isolated.engine(_isolated.owner_url), uuid.UUID(state["tenant_id"])
    ) as conn:
        tier = conn.execute(text("SELECT tier FROM tenants")).scalar_one()
    assert tier == "deep"


def test_demo_runs_the_loop_and_never_fabricates_a_number(seeded, capsys, _isolated) -> None:
    state, _ = seeded
    started = time.monotonic()
    code = cli.main(["sandbox", "demo"])
    elapsed = time.monotonic() - started
    out = capsys.readouterr().out
    assert code == 0, out

    # 1. Timestamps on every step, and the budget met.
    assert len(re.findall(r"t\+\d\d\.\d\ds", out)) >= 8
    assert elapsed < 180, f"the demo took {elapsed:0.1f}s"
    assert re.search(r"demo finished in \d+\.\ds", out)

    # 2. The money shot: abstain with `needs`, then serve only after the events
    #    `needs` asked for, citing a gated claim and showing the interval.
    assert "abstain=True" in out
    assert "needs={" in out
    assert re.search(r"serves \w+ confidence \d\.\d\d \[\d\.\d\d-\d\.\d\d\] cites \S+", out)
    assert "level      L1 served" in out

    # 3. Replay: byte-identical, billed at zero.
    assert "identical=True" in out
    assert "billed=0" in out

    # 4. The no-invention rule: no invented company, no placeholder numbers.
    for banned in ("SaaS", "Acme", "lorem", "TODO", "xxx"):
        assert banned not in out

    # 5. The ledger agrees with the bill: one settlement per served decision, and
    #    the replay wrote none. This is the claim "an abstention costs nothing",
    #    read from the database rather than from the transcript.
    with tenant_transaction(
        _isolated.engine(_isolated.owner_url), uuid.UUID(state["tenant_id"])
    ) as conn:
        settles = conn.execute(
            text("SELECT count(*) FROM credit_ledger WHERE entry_type = 'settle'")
        ).scalar_one()
        replays = conn.execute(text("SELECT count(*) FROM decision_replays")).scalar_one()
        served_rows = conn.execute(
            text("SELECT count(*) FROM decisions WHERE (body ->> 'abstain')::boolean IS FALSE")
        ).scalar_one()
        decisions = conn.execute(text("SELECT count(*) FROM decisions")).scalar_one()

    # One settlement per served decision, none for an abstention — read from the
    # ledger rather than taken from the transcript's word for it.
    assert settles == served_rows, f"{settles} settles for {served_rows} served of {decisions}"
    assert decisions > served_rows, "the demo must show at least one abstention"
    assert replays >= 1, "a replay must leave an audit row"


def test_the_demo_can_be_run_twice_in_a_row(seeded, capsys, _isolated) -> None:
    """A demo that only works on a virgin tenant is a demo you can watch once.

    The increment-8 audit found it as a first-run-only bug: `demo` reused the
    seeded `fresh` learner, so on the second run the engine served a confident
    number instead of abstaining, `needs.trials` was 0, and the run died on
    `events must not be empty`. The learner id is now run-scoped, so the money
    shot reproduces — which is the whole point of running it twice.
    """
    for attempt in range(2):
        assert cli.main(["sandbox", "demo"]) == 0, f"run {attempt + 1} failed"
        out = capsys.readouterr().out
        assert "abstain=True" in out, f"run {attempt + 1}: {out}"
        assert re.search(r"sending exactly what needs asked for: [1-9]\d* events", out), (
            f"run {attempt + 1} asked for zero events: {out}"
        )
        assert re.search(r"serves \w+ confidence", out), f"run {attempt + 1}: {out}"


def test_the_demo_refuses_to_run_without_a_seed(sandbox_env: Path, capsys) -> None:
    assert cli.main(["sandbox", "demo"]) == 1
    assert "adapt sandbox seed" in capsys.readouterr().err


def test_seed_refuses_a_slug_that_already_exists(sandbox_env: Path, capsys) -> None:
    slug = f"cli-twice-{uuid.uuid4().hex[:6]}"
    assert cli.main(["sandbox", "seed", "--slug", slug, "--events", "8"]) == 0
    capsys.readouterr()
    assert cli.main(["sandbox", "seed", "--slug", slug, "--events", "8"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_no_state_file_is_written_outside_the_working_directory(sandbox_env: Path) -> None:
    """The key lives in one dotfile, 0600, in the cwd — and nowhere else."""
    slug = f"cli-home-{uuid.uuid4().hex[:6]}"
    assert cli.main(["sandbox", "seed", "--slug", slug, "--events", "8"]) == 0
    assert (sandbox_env / ".adapt-sandbox.json").exists()
    assert not (Path.home() / ".adapt-sandbox.json").exists()
    assert os.stat(sandbox_env / ".adapt-sandbox.json").st_mode & 0o777 == 0o600
