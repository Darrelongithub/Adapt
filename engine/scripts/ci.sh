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
#
# On failure under GitHub Actions the step name and the tail of the failing
# command are emitted as a check annotation. CI log downloads go through a blob
# store that some environments cannot reach; an annotation comes back through
# the API, so "which step, and why" is never a mystery.
set -uo pipefail

cd "$(dirname "$0")/.."

# The interpreter: an explicit $PYTHON wins, then the local venv, then whatever
# python3 is on PATH — CI installs the package into the setup-python interpreter
# and has no venv at all.
if [ -z "${PYTHON:-}" ]; then
    if [ -x ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON="python3"
    else
        echo "no python interpreter: create .venv (make venv) or set PYTHON" >&2
        exit 2
    fi
fi
if [ ! -x "$PYTHON" ] && ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "PYTHON=$PYTHON does not exist: create .venv (make venv) or set PYTHON" >&2
    exit 2
fi
echo "interpreter: $PYTHON"
STEP_LOG="$(mktemp)"
CURRENT_STEP="startup"
DOC=""


annotate() {
    local title="$1" body="$2"
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
        printf '::error title=%s::%s\n' "$title" "$body"
    fi
}

on_exit() {
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        echo ""
        echo "GATE FAILED at step: ${CURRENT_STEP} (exit ${rc})"
        annotate "gate failed at ${CURRENT_STEP}" \
            "$(tail -n 12 "$STEP_LOG" 2>/dev/null | tr '\n' '|' | cut -c1-900)"
    fi
    rm -f "$STEP_LOG" ${DOC:+"$DOC"}
}
trap on_exit EXIT

step() {
    CURRENT_STEP="$1"
    printf '\n=== %s ===\n' "$CURRENT_STEP"
    : >"$STEP_LOG"
}

failed() {
    echo ""
    echo "--- output of the failing step ---"
    tail -n 40 "$STEP_LOG"
    exit 1
}

ALLOW_OLD_PYTHON="${1:-}"

step "interpreter"
"$PYTHON" - "$ALLOW_OLD_PYTHON" <<'PY' 2>&1 | tee "$STEP_LOG"
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
[ "${PIPESTATUS[0]}" -eq 0 ] || failed

step "lint (ruff check)"
"$PYTHON" -m ruff check . 2>&1 | tee "$STEP_LOG" || failed

step "format (ruff format --check)"
"$PYTHON" -m ruff format --check . 2>&1 | tee "$STEP_LOG" || failed

step "types (mypy --strict)"
MYPYPATH=src "$PYTHON" -m mypy src/adapt_engine scripts 2>&1 | tee "$STEP_LOG" || failed

step "contract doc (in-tree, as written)"
# The contract is a file in this tree at docs/v2/INTEGRATION.md, and the guard
# reads it as written — no patches, no branch, no `git show` (AGENT_M0 §6,
# AGENT_M1 pre-work). This step proves the resolution stayed in-tree: a run that
# had to reach for --doc or ADAPT_INTEGRATION_DOC would print something else.
DOC="$(mktemp)"
"$PYTHON" scripts/check_openapi.py --print-doc >"$DOC" 2>"$STEP_LOG" || failed
cat "$STEP_LOG"
if ! grep -q "docs/v2/INTEGRATION.md" "$STEP_LOG"; then
    echo "the contract did not resolve to the in-tree docs/v2/INTEGRATION.md:"
    cat "$STEP_LOG"
    failed
fi
echo "doc resolved to: $(sed -n 's/^openapi-check: doc read from //p' "$STEP_LOG") ($(wc -l <"$DOC") lines)"

step "contract guard (OpenAPI vs INTEGRATION.md)"
"$PYTHON" scripts/check_openapi.py --doc "$DOC" 2>&1 | tee "$STEP_LOG" || failed
PYTHONPATH=src "$PYTHON" -m pytest -q tests/test_check_openapi.py tests/test_error_envelope.py \
    2>&1 | tee -a "$STEP_LOG" || failed

step "migration round-trip (up -> down -> up)"
PYTHONPATH=src "$PYTHON" -m pytest -q tests/test_migrations.py 2>&1 | tee "$STEP_LOG" || failed

step "tests"
PYTHONPATH=src "$PYTHON" -m pytest -q 2>&1 | tee "$STEP_LOG" || failed

step "all green"
