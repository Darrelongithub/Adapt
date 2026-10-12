#!/usr/bin/env bash
# Run the UI probe against a freshly started API and web server.
#
# Why fresh servers: the rate-limit buckets live in the API *process*, and the probe is a
# whole term's worth of traffic in a few seconds. Reusing one API for several scenarios makes
# the second scenario pay for the first one's spending -- which reads as flaky app behaviour
# and is not. One process (and one data directory) per run is what makes the result repeatable.
#
# Usage:  scripts/probe-fresh.sh [scenario|all]
#         scripts/probe-fresh.sh all        # the whole probe: 139 checks
#         scripts/probe-fresh.sh learn      # one scenario (learn|diagnose|proposals|...)
#
# Ports 8010/3001 are used so a running dev pair on 8000/3000 is left alone. Needs a production
# build in frontend/.next (`npm run build`); the script says so instead of failing obscurely.
#
# The API runs with ADAPT_RATE_LIMIT_MULTIPLIER set (10 by default, override with
# PROBE_RATE_LIMIT_MULTIPLIER). That is the probe's own advice for walking the whole suite: one
# IP making a term's worth of traffic in half a minute otherwise spends the per-IP bucket, and
# screens get judged while the limiter is holding them -- a paced write then reads as a broken
# button ("timed out waiting for the resume notice"), and security checks expecting 401/403 get
# 429 instead. The limiter itself is still verified, at the real ceiling, by
# `scripts/failure_matrix_check.py` (which wants default limits) and by the probe's own flood
# check, which reads the bucket width from `GET /api/v1/quotas` before flooding it.
set -u

SCEN="${1:-all}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${PROBE_API_PORT:-8010}"
WEB_PORT="${PROBE_WEB_PORT:-3001}"
DATA="${PROBE_DATA_DIR:-/tmp/adapt-probe-$SCEN}"
LOGS="${PROBE_LOG_DIR:-$(mktemp -d)}"
# a caller-supplied log directory is not necessarily there yet; mktemp makes its own, an
# env var does not, and the redirect below would fail before the servers even start
mkdir -p "$LOGS"

if [ ! -f "$ROOT/frontend/.next/BUILD_ID" ]; then
  echo "no production build in frontend/.next -- run: cd frontend && npm run build" >&2
  exit 2
fi

rm -rf "$DATA"
ADAPT_DATA_DIR="$DATA" ADAPT_MODE=local ADAPT_LOG_LEVEL=WARNING \
  ADAPT_RATE_LIMIT_MULTIPLIER="${PROBE_RATE_LIMIT_MULTIPLIER:-10}" \
  "$ROOT/.venv/bin/python" -m uvicorn adapt.api.app:app --app-dir "$ROOT/backend" \
  --host 127.0.0.1 --port "$API_PORT" > "$LOGS/api-$SCEN.log" 2>&1 &
API=$!
# `exec` matters: without it $WEB is the subshell, cleanup kills the subshell, and the node
# server survives holding the port -- the next run then fails with EADDRINUSE and, worse,
# silently measures whatever was left behind instead of what it just started.
(cd "$ROOT/frontend" && exec env ADAPT_API="http://127.0.0.1:$API_PORT" HOST=127.0.0.1 PORT="$WEB_PORT" \
  NODE_ENV=production node server.mjs > "$LOGS/web-$SCEN.log" 2>&1) &
WEB=$!

cleanup() { kill "$WEB" "$API" 2>/dev/null; wait "$WEB" "$API" 2>/dev/null; }
trap cleanup EXIT

ready=0
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "http://127.0.0.1:$API_PORT/healthz" &&
     curl -sf -o /dev/null "http://127.0.0.1:$WEB_PORT/"; then ready=1; break; fi
  sleep 0.5
done
if [ "$ready" != 1 ]; then
  echo "servers did not come up; see $LOGS/api-$SCEN.log and $LOGS/web-$SCEN.log" >&2
  tail -5 "$LOGS/web-$SCEN.log" >&2
  exit 3
fi

if [ "$SCEN" = "all" ]; then
  (cd "$ROOT/frontend" && timeout 600 node scripts-ui-probe.mjs --base "http://127.0.0.1:$WEB_PORT")
else
  (cd "$ROOT/frontend" && timeout 400 node scripts-ui-probe.mjs --base "http://127.0.0.1:$WEB_PORT" --only "$SCEN")
fi
RC=$?
echo "== $SCEN exit=$RC (logs: $LOGS)"
exit "$RC"
