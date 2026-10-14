#!/bin/sh
# The phase F gate: every game and the subject round, headless, on both viewports, twice in a row.
# Usage: sh run-gate.sh   (ADAPT_CHROMIUM overrides the binary)   (the servers must be running; see README.md)
set -u
cd "$(dirname "$0")"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-/home/user/.cache/chromium-bin/lib}"
export ADAPT_CHROMIUM="${ADAPT_CHROMIUM:-/home/user/.cache/chromium-bin/chromium}"
status=0
for run in 1 2; do
  echo "== gate run $run: desktop"
  npx playwright test --project=desktop || status=1
  echo "== gate run $run: mobile"
  npx playwright test --project=mobile || status=1
  echo "== gate run $run: keyboard"
  npx playwright test --project=keyboard || status=1
done
echo "== gate exit status: $status"
exit $status
