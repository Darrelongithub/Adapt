#!/usr/bin/env bash
#
# The one definition of "green". CI runs it, and so does an engineer before a
# push: `make ci`.
#
# It needs two things from the environment:
#   ADAPT_TEST_DATABASE_URL       an administrative PostgreSQL 16.x URL
#                                 (the suite provisions its own databases)
#   ADAPT_MIGRATION_DATABASE_URL  the same server, owner role, for migrations
#
# Nothing here is skipped when it fails; the first failing step stops the run.
#
# The interpreter check is a hard gate: `requires-python >= 3.12` in
# pyproject.toml means the engine is not supported on anything older, and a
# gate that quietly ran on 3.11 would be the only place that lie would live.
# `--allow-unsupported-python` exists for one purpose: verifying the *rest* of
# the gate on a machine whose interpreter is older (this sandbox, for example).
# It prints its own warning and is never passed by CI.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"

step() { printf '\n=== %s ===\n' "$1"; }

ALLOW_OLD_PYTHON="${1:-}"

step "interpreter"
"$PYTHON" - "$ALLOW_OLD_PYTHON" <<'PY'
import sys

allowed = len(sys.argv) > 1 and sys.argv[1] == "--allow-unsupported-python"
version = sys.version.split()[0]
if sys.version_info < (3, 12):
    if not allowed:
        raise SystemExit(
            f"requires Python >= 3.12, this is {version}. "
            "Pass --allow-unsupported-python only to verify the rest of the gate."
        )
    print(f"WARNING: python {version} is unsupported; running the rest of the gate anyway")
else:
    print(f"python {version}")
PY

step "lint (ruff check)"
"$PYTHON" -m ruff check .

step "format (ruff format --check)"
"$PYTHON" -m ruff format --check .

step "types (mypy --strict)"
MYPYPATH=src "$PYTHON" -m mypy src/adapt_engine scripts

step "contract doc (fetch + apply docs-patches)"
DOC="$("$PYTHON" scripts/docs_with_patches.py)"
echo "doc resolved to: $DOC"
grep -q "minor_not_supported" "$DOC"

step "contract guard (OpenAPI vs INTEGRATION.md)"
"$PYTHON" scripts/check_openapi.py --doc "$DOC"
PYTHONPATH=src "$PYTHON" -m pytest -q tests/test_check_openapi.py tests/test_error_envelope.py

step "migration round-trip (up -> down -> up)"
PYTHONPATH=src "$PYTHON" -m pytest -q tests/test_migrations.py

step "tests"
PYTHONPATH=src "$PYTHON" -m pytest -q

step "all green"
