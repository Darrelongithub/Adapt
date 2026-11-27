# Adapt SDKs

Two thin clients, generated from the engine's OpenAPI document, and one raw
`curl` example. "Thin" is the design: one `request()` and one method per
host-facing operation, request and response bodies as plain objects. The
interesting part of this API is in the data — an abstention carries `needs` and
**no `value` at all** — and a client's job is to get out of the way of it.

Generated, not hand-written:

```sh
python sdk/generate.py          # rewrite both clients
python sdk/generate.py --check  # fail if the committed copies are stale
```

`engine/tests/test_sdk.py` runs `--check` on every CI run, so an endpoint that
changes without the SDK changing fails the build rather than the customer.

## Python: first call in about two minutes

```sh
pip install ./sdk/python            # no dependencies, one module
export ADAPT_BASE_URL=https://api.example.com
export ADAPT_KEY=ak_live_...
```

```python
import os
from adapt_sdk import Client

client = Client(os.environ["ADAPT_BASE_URL"], os.environ["ADAPT_KEY"])
decision = client.decide({"learner_id": "learn-0001", "skill": "skill.fractions"})
if decision["abstain"]:
    print("the engine needs", decision["needs"]["trials"], "more trials")
else:
    rec = decision["recommendations"][0]
    print(rec["action"], rec["confidence"]["interval"], rec["cites"])
```

That block is executed by `tests/test_sdk.py` against a live engine, so the
quickstart cannot rot while the SDK works.

`Idempotency-Key` is derived from the request when you do not pass one — the
method, the path and the body — so a timeout and a retry consume one credit and
write one row (INTEGRATION §7). Pass `idempotency_key=` yourself when the logical
operation is coarser than the request.

## TypeScript

```ts
import { Client } from "@adapt/sdk";

const client = new Client(process.env.ADAPT_BASE_URL!, process.env.ADAPT_KEY!);
const decision = await client.decide({ learner_id: "learn-0001", skill: "skill.fractions" });
if (decision.abstain) {
  console.log("the engine needs", decision.needs.trials, "more trials");
} else {
  console.log(decision.recommendations[0].action, decision.recommendations[0].cites);
}
```

Platform `fetch`, no runtime dependencies, one file. `npm run typecheck` needs
`typescript` only.

## No SDK at all

`sdk/raw/curl.sh` is the same two calls with `curl`, for when you want to see the
bytes:

```sh
ADAPT_KEY=ak_test_... ./sdk/raw/curl.sh http://127.0.0.1:8000
```

## What the clients do not do

No retries, no rate-limit backoff, no pagination, no models. Each of those is a
decision a host should make with its own scheduler and its own idempotency keys,
and a generated client that guessed would be a generated client that is wrong in
a way nobody reads the source to find.
