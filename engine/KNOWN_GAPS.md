# Known gaps — unverified claims

Every line here is something the code assumes but nobody has executed in the
environment it claims to be for. They are listed rather than implied: an
unverified acceptance line that nobody wrote down becomes a false claim in a
month.

Last updated: increment 2 (tenancy and RLS).

## 1. `docker compose up` has never been run

**What is claimed:** the stack in `engine/docker-compose.yml` starts, the
`migrate` service bootstraps the two roles and runs `alembic upgrade head`, and
`curl -s localhost:8000/v1/health` answers.

**What was actually verified:** everything except the container run. The same
migration was applied to a real PostgreSQL 16.2 server driven by the test
fixture (`SELECT version()` → `PostgreSQL 16.2 ... Red Hat 10.2.1`), and the
same role bootstrap (`scripts/bootstrap_db.py` uses the same
`ensure_roles`) was exercised by the test suite. What is unverified is the
composition itself: image build, `depends_on` conditions, service DNS names,
and whether `migrate` exits 0 before `api` starts.

**Why:** the build environment has no Docker binary, no daemon socket, and no
route to a package registry (`apt-get update` cannot reach `deb.debian.org`).

**What closes it:** on a machine with Docker:

```sh
cd engine && docker compose up -d --build
docker compose ps                       # migrate must have exited 0
curl -s localhost:8000/v1/health        # 200, db:true, redis:true
docker compose down -v
```

## 2. The image has never been built, so Python 3.12 has never run this code

**What is claimed:** `engine/Dockerfile` builds on `python:3.12-slim`,
`pip install .` succeeds, and `python -m adapt_engine.main` serves.

**What was actually verified:** `pip install .` (non-editable) into a clean
virtualenv, importing from `site-packages`, the entrypoint failing closed with
a non-zero exit, and the full suite green against the installed copy — all on
**Python 3.11.2**.

**Why:** no Docker, and no 3.12 interpreter obtainable — `uv python install
3.12` fails at `release-assets.githubusercontent.com` (TLS `UnknownIssuer`,
host outside the sandbox allowlist), and there are no C headers in the image
(`openssl/ssl.h`, `zlib.h` absent), so a source build could not run pip.

**What closes it:** `docker build engine/` on any machine with Docker, then
`docker run --rm <image> python -m pytest` with the suite mounted, and record
`python -V` in the output. `pyproject.toml` declares `requires-python >= 3.12`,
so a 3.11 install is refused — that refusal is the only 3.12-specific
behaviour observed so far.

## 3. Redis has never been reachable

`/v1/health` reports `redis: false` in every run so far, because no Redis was
available. The probe is bounded and its failure mode is tested; a successful
`PING` path is not. Closes with gap 1 (`docker compose up` brings `redis:7`).

## 4. ~~No HTTP surface is tenant-scoped yet~~ — closed by increment 3

`/v1/learners` and `/v1/learners/{learner_id}` are the first routes to run under
`tenant_transaction`, reached through an API key. `tests/test_auth_http.py`
covers the wiring: a key for tenant A gets a 404 for tenant B's learner id (not
403 — that would confirm the id exists elsewhere), and a learner created with
`tenant_id` in the body still lands in the caller's tenant. The mechanism was
already exercised directly in increment 2; what was untested was the path from
`Authorization` header to `SET LOCAL adapt.tenant_id`.

## 5. The error-code guard now passes, and only because the doc says so

Rule (c) of `scripts/check_openapi.py` parses **every** row of the §6 table,
including the `any other | unmapped_status` sentinel, so the guard exits 0 with
16 documented codes at docs ref `9d1739d`. It is still absolute: remove the
sentinel row and the guard fails (`test_entrypoint_fails_when_the_sentinel_row_is_removed`).
There is no whitelist in the checker; the document is the only allowlist.

## 6. The rate limiter is per process, not per cluster

`FailureLimiter` is in-memory (DECISIONS.md §6), so **N processes = N× the
budget**: with three replicas an attacker gets 60 failures per minute per
address instead of 20, and a restart clears every window. The budget is per
address, not per key, so one busy NAT shares it — but validation runs first, so
a valid key from that address is never refused (DECISIONS.md §6).

No `X-Forwarded-For` handling: behind a load balancer every request looks like
it came from the balancer, and one attacker's failures would then be charged to
everyone. Trusting that header without a configured trusted proxy would be
worse (any client could set it).

**What closes it:** a shared window in Redis, once gap 3 closes, plus a
trusted-proxy configuration for client-address extraction.

## 7. Keys expire, but nothing rotates them and nothing sweeps them

`expires_at` is `NOT NULL` and enforced at authentication, so an expired key is
dead. What does not exist: a rotation workflow (issue-new-then-revoke-old is
described, not implemented), a scheduled job that revokes keys nearing expiry,
and a console view of "keys expiring in 14 days". A tenant finds out a key
expired when a request fails.

## 8. The credits ledger records consumption; nothing lists it back

`GET /v1/credits/balance` answers "how much is there", not "where did it go".
There is no ledger endpoint, no statement, and no `period_start` writer (the
column exists and is always NULL). The `expire` entry type exists in the
ledger CHECK and is never emitted: expiry is derived on read, which is what
makes `forfeited` a query rather than a sweep, and it also means an expired
allowance leaves no row to point at. A tenant can see that it forfeited 40
credits and cannot see which grant they came from without the grant list.

`credit_consumption` (the view, grouped by tenant and month) is the sanctioned
aggregation and nothing reads it yet — no billing job, no report, no route.

Also unbuilt: any link between a reservation and an actual `decide` operation —
`operation_ref` is a caller's string, not a foreign key.

## 9. System audit rows are readable only by the owner

Failed-authentication rows have `tenant_id IS NULL` and are visible to
`adapt_owner` only (DECISIONS.md §7). There is no console screen, no endpoint
and no alerting on them. The one improvement is `scripts/audit_query.py`: an
owner-only, read-only CLI (`--auth-failures`, `--key-events`, `--system-count`)
that answers the three questions a security review asks without a `psql`
session. It is not a console screen, it has no scheduling, and nobody is
paged by it. The aggregation bound (11 rows per source per minute) also
means the count in the aggregate row is a *lower bound* for that minute — it
records the count at the moment of the eleventh failure, not the total.

## 10. `last_used_at` is approximate, and nothing reads it yet

The flush interval (15 s) and the debounce (60 s) mean `last_used_at` can lag
reality by a minute (DECISIONS.md §5). No route or console surface exposes it
yet, so the only reader is a test.

## 11. `/v1/admin/keys/{id}/expiry` exists; nothing calls it yet

Expiry shortening is an audited operation (DECISIONS.md §10), and the audited
path is a route. No console calls it yet, so in practice an operator with the
owner credential can still reach the table — but not through the app role,
which holds no `UPDATE` on `api_keys`. The gap is the caller, not the door.

## 12. Rotation has no command; it has a function

`DataKeyStore.rewrap` and `rewrap_all` exist and are tested, and
`ADAPT_MASTER_KEY_PREVIOUS` makes the keyring accept both keys during the
changeover. What does not exist is an operator entry point: no script, no CLI,
no scheduled job. Rotation today means writing the loop by hand. The startup
refusal is the safety net for that, not a substitute for it.

## 13. The crypto path logs nothing, which is a property of not writing code

`test_no_plaintext_or_key_material_reaches_a_log_line` proves the encrypt,
decrypt and wrap paths emit nothing sensitive, and it guards a future
`logger.debug(envelope)`. It cannot prove a *new* module that starts logging
ciphertext is caught, because there is no redaction layer — the design is "do
not log it", enforced by review and by this test.

## 14. A tenant data key is per tenant, and there is no version history

One data key per tenant, re-wrapped on rotation. If a tenant's data key ever
had to be replaced (suspected compromise of that key alone), old rows would
become unreadable: `tenant_data_keys` keeps the current wrapper, not a list of
versions. Master-key rotation is supported; data-key rotation is not.

## 15. `data_key_ids` is a global table, and it is the second documented RLS exception

Every table except `api_keys` is RLS-enabled and FORCEd. `data_key_ids` has no
tenant column and no RLS: it holds master key ids, which are printed in the
header of every ciphertext they wrote, and it exists so startup can ask a
cross-tenant question without a cross-tenant read. `api_keys` was the first
documented exception (DECISIONS.md §2); this is the second, and it is narrower
— the table has no row that belongs to anyone.
