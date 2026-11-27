#!/usr/bin/env bash
# One raw REST example: no SDK, no client, nothing between you and the contract.
#
#   ADAPT_KEY=ak_test_... ./sdk/raw/curl.sh https://api.example.com
#
# The interesting part of the response is what is absent: an abstention carries
# `needs` and no `value` at all. Render what you are given, not a number you wish
# you had (INTEGRATION §6B).
set -euo pipefail

BASE="${1:?usage: curl.sh BASE_URL}"
KEY="${ADAPT_KEY:?export ADAPT_KEY first}"
LEARNER="${LEARNER:-learn-0001}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "== observe (202, credit-free) =="
curl -sS -X POST "$BASE/v1/observe" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: example-observe-$(date +%Y%m%d)" \
  -d "{\"events\":[{\"learner_id\":\"$LEARNER\",\"skill\":\"skill.fractions\",\"item_id\":\"item-001\",\"correct\":true,\"response\":\"option-2\",\"ts\":\"$NOW\",\"seq\":0}]}" \
  | head -c 400
echo

echo "== decide (200, one credit when it serves) =="
curl -sS -X POST "$BASE/v1/decide" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: example-decide-$(date +%Y%m%d)" \
  -H "X-Adapt-Test: 1" \
  -d "{\"learner_id\":\"$LEARNER\",\"skill\":\"skill.fractions\"}" \
  | python3 -m json.tool
