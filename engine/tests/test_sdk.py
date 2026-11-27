"""Increment 7: the generated SDKs, and the promise that they cannot rot.

Three tests, each aimed at a way an SDK goes wrong quietly:

* **`--check` is a gate.** The committed clients are compared byte for byte with a
  fresh generation from the live OpenAPI document, so a route that changes without
  the SDK changing fails CI instead of a customer.
* **The quickstart runs.** The Python block in `sdk/README.md` is extracted and
  executed against a real engine on a real port. A README that is a wish is worse
  than no README.
* **The shape is the contract.** An abstention from the SDK has no `value` key —
  the same absence the API guarantees — and a replay returns the stored body
  unchanged while billing zero.

The generator deliberately declares request bodies itself: the OpenAPI document
carries none (the routes parse JSON by hand), which is recorded in `KNOWN_GAPS`.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adapt_engine.cli import _serving
from adapt_engine.credits import service as credits
from adapt_engine.db.tenant import tenant_transaction
from tests import pg
from tests.conftest import make_settings
from tests.pg import bootstrap_key, provision_tenant

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "sdk"
PY_CLIENT = SDK / "python" / "adapt_sdk" / "client.py"
TS_CLIENT = SDK / "typescript" / "src" / "client.ts"

#: The host-facing surface, written as a literal. If a route is added, this list
#: and the generated client must both change — that is the point of the test.
EXPECTED_METHODS = [
    "create_learner",
    "credits_balance",
    "decide",
    "learner",
    "learners",
    "mastery",
    "mastery_for",
    "observe",
    "replay",
    "whoami",
]


@pytest.fixture
def _sdk_database() -> Iterator[pg.Database]:
    """A database of this file's own: the app's startup gate is global.

    A shared database that once held a tenant rotated to another master key makes
    every later app refuse to start — correctly, because that is what a keyring
    gap means. A unique name keeps this file's failure modes its own.
    """
    database = pg.provision(f"sdk_{uuid.uuid4().hex[:10]}")
    try:
        yield database
    finally:
        pg.drop_database(database)


def test_the_committed_clients_are_generated_from_the_current_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(SDK / "generate.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, (
        "the SDK is stale; run `python sdk/generate.py`\n" + result.stdout + result.stderr
    )


def test_python_client_exposes_one_method_per_operation_and_nothing_else() -> None:
    tree = ast.parse(PY_CLIENT.read_text())
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    client = next(node for node in classes if node.name == "Client")
    public = sorted(
        node.name
        for node in client.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    )
    assert public == sorted(["derive_key", "request", *EXPECTED_METHODS]), public

    # The TypeScript client mirrors it: same operations, camelCased.
    source = TS_CLIENT.read_text()
    for name in EXPECTED_METHODS:
        camel = re.sub(r"_([a-z])", lambda match: match.group(1).upper(), name)
        assert f"async {camel}(" in source, f"the TypeScript client is missing {name}"


def test_an_abstention_from_the_sdk_has_no_value_and_a_replay_is_free(
    _sdk_database,
) -> None:
    database = _sdk_database
    tenant = provision_tenant(database, slug=f"sdk-{uuid.uuid4().hex[:8]}", name="SDK Test")
    key = bootstrap_key(database, tenant, scopes=("observe", "decide", "read_model", "admin"))
    owner = database.engine(database.owner_url)
    with tenant_transaction(owner, tenant) as conn:
        credits.grant_credits(
            conn,
            tenant_id=tenant,
            kind="purchased",
            amount=20,
            expires_at=None,
            idempotency_key=f"grant-{uuid.uuid4().hex}",
        )

    sys.path.insert(0, str(SDK / "python"))
    try:
        from adapt_sdk import AdaptError, Client
    finally:
        sys.path.remove(str(SDK / "python"))

    settings = make_settings(database_url=database.app_url)
    with _serving(settings) as base:
        # `observe` and `decide` belong to a *connection* key; the console key is
        # for the admin reads. The SDK's own `request()` creates the connection.
        created = Client(base, key.full_key).request(
            "POST",
            "/v1/admin/connections",
            body={
                "host_name": "sdk-test",
                "environment": "test",
                "event_quota_monthly": 10_000,
                "scopes": ["observe", "decide", "read_model"],
            },
        )
        client = Client(base, created["key"]["full_key"])
        learner = "learn-sdk-0001"
        now = datetime.now(UTC).isoformat()
        client.observe(
            {
                "events": [
                    {
                        "learner_id": learner,
                        "skill": "skill.fractions",
                        "item_id": f"item-{index:03d}",
                        "correct": index % 3 != 0,
                        "response": f"option-{index}",
                        "ts": now,
                        "seq": index,
                    }
                    for index in range(12)
                ]
            }
        )

        served = client.decide({"learner_id": learner, "skill": "skill.fractions"})
        assert served["abstain"] is False
        assert served["recommendations"][0]["cites"]

        replay = client.replay(served["decision_id"])
        assert replay == served, "a replay returns the stored body, byte for byte"

        # An unknown learner is the contract's 404, raised as an exception with the
        # envelope's fields rather than a message to parse.
        with pytest.raises(AdaptError) as caught:
            client.mastery(learner_id="learn-sdk-unknown-0002")
        assert caught.value.status == 404

        # A learner we know almost nothing about: one observation, then a decision
        # that abstains, names what it needs, and carries no `value` at all.
        client.observe(
            {
                "events": [
                    {
                        "learner_id": "learn-sdk-sparse-0031",
                        "skill": "skill.fractions",
                        "item_id": "item-000",
                        "correct": True,
                        "response": "option-1",
                        "ts": now,
                        "seq": 0,
                    }
                ]
            }
        )
        abstained = client.decide(
            {"learner_id": "learn-sdk-sparse-0031", "skill": "skill.fractions"}
        )
        assert abstained["abstain"] is True
        assert "value" not in abstained
        assert abstained["needs"]["trials"] > 0


def test_the_readme_quickstart_runs(_sdk_database) -> None:
    """The Python block in `sdk/README.md`, executed, against a live engine.

    A quickstart that nobody runs is a quickstart that is wrong. This is also the
    "under fifteen minutes to first call" claim, made checkable: install, export
    two variables, run the block.
    """
    database = _sdk_database
    tenant = provision_tenant(database, slug=f"readme-{uuid.uuid4().hex[:8]}", name="README")
    key = bootstrap_key(database, tenant, scopes=("observe", "decide", "read_model", "admin"))
    owner = database.engine(database.owner_url)
    with tenant_transaction(owner, tenant) as conn:
        credits.grant_credits(
            conn,
            tenant_id=tenant,
            kind="purchased",
            amount=10,
            expires_at=None,
            idempotency_key=f"grant-{uuid.uuid4().hex}",
        )
    readme = (SDK / "README.md").read_text()
    blocks = re.findall(r"```python\n(.*?)```", readme, flags=re.S)
    quickstart = next(block for block in blocks if "adapt_sdk" in block)

    settings = make_settings(database_url=database.app_url)
    with _serving(settings) as base:
        # Evidence through the SDK itself, so the quickstart has something to
        # decide about: twelve observations on the learner the README names.
        sys.path.insert(0, str(SDK / "python"))
        try:
            from adapt_sdk import Client
        finally:
            sys.path.remove(str(SDK / "python"))
        created = Client(base, key.full_key).request(
            "POST",
            "/v1/admin/connections",
            body={
                "host_name": "readme-test",
                "environment": "test",
                "event_quota_monthly": 10_000,
                "scopes": ["observe", "decide", "read_model"],
            },
        )
        now = datetime.now(UTC).isoformat()
        Client(base, created["key"]["full_key"]).observe(
            {
                "events": [
                    {
                        "learner_id": "learn-0001",
                        "skill": "skill.fractions",
                        "item_id": f"item-{index:03d}",
                        "correct": index % 3 != 0,
                        "response": f"option-{index}",
                        "ts": now,
                        "seq": index,
                    }
                    for index in range(12)
                ]
            }
        )
        environment = {
            "ADAPT_BASE_URL": base,
            "ADAPT_KEY": created["key"]["full_key"],
            "PYTHONPATH": str(SDK / "python"),
        }
        result = subprocess.run(
            [sys.executable, "-c", quickstart],
            capture_output=True,
            text=True,
            env={**os.environ, **environment},
            timeout=60,
            check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"next_item_difficulty|worked_example|spaced_review|needs", result.stdout), (
        result.stdout + result.stderr
    )
    assert "Traceback" not in result.stderr
