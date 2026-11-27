# Known gaps — unverified claims

Every line here is something the code assumes but nobody has executed in the
environment it claims to be for. They are listed rather than implied: an
unverified acceptance line that nobody wrote down becomes a false claim in a
month.

Last updated: M1 increment 6 (abstention metrics). §23 and the `decision_unknown`
half of §24 are closed; §25-§27 record what the flag fixes and the metering proof
leave open; §28 records what the metrics endpoint cannot count and why.

## Standing gaps at a glance

Every gap below is described in a section further down. This table exists
because a reader should not have to read twelve sections to learn that Redis
has never been reachable.

| Gap | Since | Risk if ignored | Tracked in |
|---|---|---|---|
| `docker compose up postgres` has **never been run** — the compose file and `Dockerfile` are written, not exercised | increment 1 | the documented path to a running database may not work | §1 |
| No **3.12 image**: CI runs a 3.12 interpreter (green, two jobs), the `Dockerfile` is unbuilt | increment 1 | the container is unverified; the local interpreter is 3.11 | §2 |
| **No Redis** has ever answered: the limiter falls back to in-process state | increment 1 | rate limits do not hold across replicas | §3 |
| No **rotation command or job**: `scripts/rotate_master_key.py` exists, nothing schedules or verifies a rotation | increment 5 | a compromised master key has no dated, rehearsed response | §12 |
| **Data-key rotation unsupported**: one data key per tenant, no version history | increment 5 | a single tenant's key cannot be rotated without orphaning rows | §14 |
| **Console screens owed**: credit usage, ledger listing, key management, learner inspector, failure review | increment 4 | operators work from `psql` and a CLI | §8, §9, §11 |
| **Per-process limiter** and in-memory auth cache: nothing is shared | increment 3 | two replicas allow twice the failures | §6 |
| **Webhooks have never reached a real receiver**: the transport is exercised against a fake | increment 1 (M1) | TLS, DNS, a receiver's real behaviour, and a proxy in the way are unverified | §1 |
| **Nothing schedules the dispatcher**: `run(now=...)` is called by tests, not by a worker | increment 1 (M1) | deliveries sit pending until a worker exists | §1 |
| **Webhook delivery is off by default** (`ADAPT_WEBHOOK_DELIVERY`) | increment 1 (M1) | turning it on is the untested path | §1 |
| **`tab_hidden` cannot be set**: the event allowlist has no visibility channel, so one documented flag never fires | increment 2 (M1) | a flag the docs name is silently always false | §2b |
| **Event duplicates are refused in code, not by a constraint**: a partitioned table cannot carry a global unique index | increment 2 (M1) | two concurrent batches could insert the same `(learner, session, seq)` | §2c |
| **Nothing schedules `ensure_event_partitions.py`**: partitions are created 24 months ahead by the migration | increment 2 (M1) | after the window, events land in the default partition and lose pruning | §2d |
| **Observe stores events; nothing reads them back yet** | increment 2 (M1) | 400k rows are accumulated unverified until increment 3 consumes them | §2e |
| **The 400k-event load test ran on a dev PostgreSQL**, not the image or a tuned server | increment 2 (M1) | the µs/event figure is relative, not a capacity plan | §2f |
| **Metering is proved for `decide` only**: `predict` and `probe` are priced and do not exist | increment 5 | a new route could bill without an empty-ledger test | §26 |
| **Credit exhaustion under load is untested**: nothing has run a tenant to zero | increment 5 | the `402` before the reservation, and the fairness codes under concurrency, are unverified | §26 |
| **The metering corpus is one seed**: 176/24 is an arithmetic check, not a distribution | increment 5 | quoting it as a product metric would overstate it | §27 |
| **Five metric series are `null`**: refusals, errors and the three axes have no store | increment 6 | a dashboard will show "unmeasured" where a buyer expects 0 | §28 |
| **`abstention_polling` is calibrated on one fixture**: the baseline is a median, the threshold is 5× by contract | increment 6 | a tenant with uniformly high retries never alerts, and a real polling host in a quiet tenant is invisible | §28 |

## 1a. Webhook delivery has never left the process

**What is claimed:** a webhook reaches the customer's endpoint, signed with
Standard Webhooks, retried five times.

**What was actually verified:** the outbox, the retry schedule, the signature
(ours verifying ours), dedupe in both layers, and the delivery log — all against
a fake transport that counts calls and returns a chosen status. No DNS lookup,
no TLS handshake and no real receiver has ever been involved, because
`ADAPT_WEBHOOK_DELIVERY` defaults to `disabled` and this sandbox has no egress.

**What closes it:** a receiver under our control (the sandbox demo in increment
7 can host one), one real 2xx, one real 500, and one timeout — with the
`webhook-*` headers captured and verified.

**Also open:** nothing calls `Dispatcher.run` on a schedule yet. It is a method
that takes `now`, which is what makes the retry schedule testable; a worker that
calls it every few seconds is later work, and until it exists a delivery stays
`pending`.

## 2b. `tab_hidden` is in the vocabulary and cannot be recorded

**What is claimed:** MEASUREMENT §4 lists six validity flags, including
`tab_hidden` ("the document was not visible mid-trial").

**What was actually verified:** five of the six are computed at ingest and
covered by tests (`rapid_guess`, `straight_line`, `device_change`, `assisted`,
`repeated_item`). `tab_hidden` is in the column's CHECK constraint and in the
documented list, and **no input can set it**: the event allowlist has no
visibility field, and inventing one would be a contract change made by the
implementation.

**What closes it:** a doc-owner decision — either the field is added to the event
allowlist (a contract change with its own review), or the flag is removed from
the vocabulary. Until then a query on `'tab_hidden' = ANY(validity_flags)` is
always empty, by design rather than by accident.

## 2c. Event dedupe is a query, not a constraint

**What is claimed:** an event that the host re-sends (same `learner_id`,
`session_id`, `seq`) is stored once.

**What was actually verified:** a duplicate inside a batch, and a duplicate
against stored events, are both counted as `duplicates`, not inserted, and not
charged — asserted by two tests.

**Why not a constraint:** `learning_events` is partitioned by `ts`, and a unique
index on a partitioned table must include the partition key. A uniqueness rule
over `(tenant_id, learner_id, session_id, seq)` therefore cannot exist across
months; making it work would mean a unique index on the partition key too, which
is not the rule. `learning_events_dedupe_idx` makes the check an index probe.

**What is left open:** two *concurrent* batches carrying the same triple can both
insert it — the check reads before either commits. Closing that needs either a
non-partitioned identity table with a unique constraint, or an advisory lock per
`(learner, session)`; both are redesigns, not bug fixes, and are deliberately not
done here.

## 2d. Nothing schedules the partition maintenance

**What is claimed:** `learning_events` has a month partition for every month in
the window, and a `DEFAULT` partition as the safety net.

**What was actually verified:** migration `0011` creates 24 monthly partitions
from the month it runs, plus the default; `scripts/ensure_event_partitions.py`
creates missing ones and `--check` fails when the default partition holds a row
or fewer than two months are provisioned (both behaviours tested).

**What is left open:** no cron entry, no Kubernetes CronJob, nothing in
`engine/` calls it on a schedule. A deployment that runs for two years without
running it will put events into the default partition — which is why the check
exists and why it fails rather than warns.

## 2e. Nothing reads learning events back yet

**What is claimed:** observe is the write half of the learning loop.

**What was actually verified:** the write half — validation, flags, quota,
idempotency, tenancy — with 36 tests and a 400,000-event load test.

**What is left open:** there is no reader. Increments 3–5 (models, `decide`,
reports) consume these rows; until they land, a bug in the stored *shape* (a
wrong timezone, a mis-set flag) would be found by a test rather than by a
consumer. This is the largest unverified surface in M1 so far, and it is
inherent to building the write side first.

## 2f. The load test ran on a development database

**What is claimed:** `POST /v1/observe` sustains a host's traffic at a known
cost per event.

**What was actually measured:** 400,000 events in 800 batches of 500 through a
real HTTP server, four concurrent clients, against `pgserver` (a bundled
PostgreSQL 16 in this sandbox, default settings, container filesystem):
p50 **468 ms** per batch, p95 789 ms, p95 per event **245 µs**, 4,081
events/s. The event payload came out at 262.1 bytes, against METERING §1.1's
262-byte representative event — the payload model matches the document almost
exactly.

**What is left open:** this is not a capacity plan. The server was a single
uvicorn worker on a shared sandbox CPU, the database was untuned
(`synchronous_commit=on`, default `shared_buffers`), and no TLS, proxy or
multi-replica path was involved. §1.1's 3.5 µs/event prices three BKT updates
and no durable write; the measured number prices a committed row plus three
indexes and two lookback queries. The ratio is reported, not interpreted as a
regression: **245 µs/event is 70× the §1.1 arithmetic baseline**, and that gap
is the cost of existing at all.

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

**Cover now, measured:** `.github/workflows/engine-ci.yml` runs the whole gate
on Python 3.12 and asserts `sys.version_info[:2] == (3, 12)` before it starts.
Both jobs are green at commit `49b2905` — 1m19s with a `postgres:16.4` service,
1m27s with PostgreSQL 16 from PGDG and a `docker` stub that fails if anything
touches it. So the interpreter this code claims to need has executed the suite,
the migrations up→down→up, and the contract guard. What is still unverified is
the **image**: `engine/Dockerfile` has never been built, and neither has
`docker compose`.

This section is also where the first CI failure was diagnosed, and it is worth
keeping: the gate had never run anywhere but this sandbox, and the two things
that broke were the ones only a runner can show — the docs branch had moved
(the patch stopped applying) and a runner has no `.venv`. Both are fixed; a
failure now names its step in a check annotation, because CI log blobs were not
readable from where the fix was being written.

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
17 documented codes at docs ref `855d74a`. It is still absolute: remove the
sentinel row and the guard fails (`test_entrypoint_fails_when_the_sentinel_row_is_removed`).
There is no whitelist in the checker and no patch step: the document is the only
allowlist, read as written (DECISIONS §14). Remove `minor_not_supported` from a
copy and the guard exits 1 — measured against the doc as it stood before
`855d74a`, which is how we know the row is the document owner's, not ours.

**Closed in M1 pre-work.** The document is now in-tree at `docs/v2/INTEGRATION.md`,
and the guard resolves it there: no `FALLBACK_DOC_REFS`, no `ADAPT_DOCS_REF`, no
`git show`, no branch name anywhere in `check_openapi.py`, `ci.sh` or the
workflow. `--doc` / `ADAPT_INTEGRATION_DOC` remain as explicit escape hatches for
a document under review, and a tree without the contract fails closed (exit 2,
naming the path) rather than reaching for a branch.

Measured, because it is the failure this removes: against a worktree at
`b008b45` — M0's tree, no `docs/v2/` — the old guard printed
`table read from origin/arena/cb97052d-adapt:docs/v2/INTEGRATION.md (17 codes) /
no violations` and exited **0**, i.e. it built against a branch that was not in
the tree it was compiling. The same tree with the new guard exits **2** with
`the contract is not in this tree: no file at …/docs/v2/INTEGRATION.md`.

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

## 12. Rotation has a command; it has no schedule

`scripts/rotate_master_key.py` answers the operational questions — `status`,
`rewrap`, `preflight <kid>`, `retire <kid>` — and `ADAPT_MASTER_KEY_PREVIOUS`
makes the keyring accept both keys during the changeover. What does not exist is
a *job*: nothing rotates on a schedule, nothing verifies that a rotation was
finished (old key actually removed), and nothing would notice that a key has
been current for two years. Rotation happens when somebody reads DECISIONS §13
and runs the script, which is a rehearsal-free process.

The startup refusal is the safety net for a premature removal, not a substitute
for a schedule.

The script itself has no test: `rewrap_all`, `preflight`, `assert_retirable` and
`provision_data_key` are covered through the library, and the argparse surface
is type-checked, but nobody has executed `rotate_master_key.py` against a real
cluster in CI. It is a thin wrapper, which is exactly the kind of thing that
breaks quietly for a year.

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

## 16. The mastery estimate is calibrated on one generator, and the other one says so

The acceptance gate (RMSE ≤ 0.08, coverage ≥ 0.90 at 40 observations) is met on
the clean generator `p(correct) = mastery` — 0.0683 / 0.948. On the model's own
response function `p(correct) = m(1−S) + (1−m)G` it is 0.1102 / 0.753, and no
estimator can meet 0.08 there: 40 Bernoullis on a 70% signal have a standard error
of 0.11. Both numbers are asserted in the suite so neither can be quoted alone.
The doc's acceptance vocabulary does not say which generator it means; that is an
owed clarification, not a code defect (DECISIONS §19.3).

## 17. The guesser guarantee holds statistically, not structurally, past ten straight

200 seeded guessers (p=0.2) over 60 trials are never served 0.7 or above — worst
0.537. But a guesser whose *first eight* answers are correct (2.6e-6) and who then
gets three of its next four right (≈8e-8 per learner) is served 0.743. There is no
estimator that can both serve a genuine master and refuse that streak: the
evidence is identical. The crossover — eleven straight correct answers — is pinned
by a test so a change that makes the estimator credulous moves it visibly.

The randomness in the property test means it is a weak detector of a broken
estimator (removing shrinkage breaches it once in 200 learners); the adversarial
constructs next to it are the strong ones. If G2 is ever enforced by an auditor
rather than by this suite, the adversarial constructs are the tests to show.

## 18. Mastery has no rate limit, no pruning, and no capability tier

* `GET /v1/mastery*` is not rate-limited. Ingest is (METERING §6); the read path
  is two indexed lookups, and adding a second limiter was not in this increment.
  A tenant with `read_model` can call it as fast as the app can answer.
* `learner_mastery` rows are never expired, pruned, or erased with a learner's
  data. `PRIVACY.md` §5 is explicit that **derived estimates are erased with the
  learner** — "a derived estimate is still data about a person; 'it is only our
  model's number' is not a lawful basis for keeping it" — and that cohort *fits*
  survive only because they are shared across ≥20 learners. So `learner_mastery`
  (and, from increment 4, `decisions`) **must join the erasure path** in
  increment 7 when DSR lands: the same transaction that erases the learner's
  encrypted fields deletes these rows, and the deletion receipt counts them.
  Until then a per-learner state row survives an erasure request, which is
  exactly what a DSR audit looks for. Recorded here rather than assumed.
* "Deep adds cross-cohort mastery modelling" (ADR-002 §tiers) is not enforced
  anywhere: all four current scopes map to `core`, and the refit is an operator
  script rather than a tiered endpoint (DECISIONS §19.7). A tenant cannot buy or
  be refused the cohort model today.

## 19. The refit counts every flag except `repeat`

`scripts/refit_cohorts.py` excludes events flagged `repeat` (the same item twice
measures the item, not the learner) and counts everything else — including
`hint_used` and `assisted` events, which are arguably weaker evidence of mastery.
At M1 there is no per-flag weight, so a cohort that is 40% hinted items fits its
priors as if it were not. Worth weighting when increment 4 starts consuming the
estimate to make decisions; worth stating now so the fit is not mistaken for
flag-aware.

## 20. Golden files pin branches, not the distribution

`tests/golden/mastery/` now pins thirteen cases at four decimals, and
`test_the_golden_set_covers_every_branch` asserts what the set *covers*: three
abstentions with different `needs.trials`, the capped value, served low/mid/high,
a long-run guesser, a saturated run, the three G2 crossings, and two constructs.
That is a branch gate, not a distribution gate — a shift that leaves every branch
shape intact but moves a percentile is still invisible to it, and the calibration
and guesser tests are what cover that. Nothing guards against someone editing a
golden and the test it feeds in the same commit; the guard is review, and the rule
is in DECISIONS §19.6.

## 21. One unreproduced teardown error, recorded rather than forgotten

On **2026-11-17**, during a full local run of the suite, pytest reported one `ERROR` in the *teardown*
of `tests/test_observe.py::test_duplicate_events_inside_a_batch_are_counted_not_inserted`. The test
body passed; the error was in fixture finalisation, after the assertions. It did not reproduce in
three subsequent full runs of the same tree, nor in CI (run 45607546119, both jobs green).

Unreproduced is not the same as absent. The failure mode it would represent is real: the suite
provisions and drops a PostgreSQL database per session (`tests/conftest.py::database`), and a teardown
that races the next provision would show up exactly like this — one `ERROR`, no failed assertion, and
a green run afterwards. It is recorded here with its date and test name so that a second occurrence is
a pattern rather than a surprise. If it recurs, the first thing to capture is the full teardown
traceback (`pytest -x -q --tb=long`) and whether `pgserver` logged a shutdown timeout.

## 22. `consent_withheld` is in the vocabulary and cannot be served

`METRICS.md` §1.3 lists six abstention reasons. `decide` v1 can reach five; the
sixth, `consent_withheld`, needs a consent store that does not exist yet (the
scope is in `PRIVACY.md` §5 and lands with the console). It is in the closed
vocabulary so that adding it later is a store, not a new word — and it is *not*
simulated with a flag nothing can set, because a reason code nobody can trigger
is indistinguishable from a reason code that does not work.

## 23. ~~`straight_line` flags a genuine run of correct answers, and `decide` then abstains~~ — closed by increment 5

The rule read `correct`, so eight right answers in a row was a run. It now reads
`response` (8+ identical responses on one skill, compared by digest), answers
`undeterminable` when an event carries no response, and never reads correctness
(`DECISIONS.md` §21.1). On the same 200-learner corpus the nine `data_quality`
abstentions disappeared and no other decision changed.

Residual, recorded rather than hidden: a host that sends **no** `response` fields
produces no `straight_line` signal at all. That is the honest state — the flag is
`undeterminable` for every such event, which is `context` severity and does not
gate serving — but it means the flag's coverage depends on a field the contract
lists as optional (`AGENT_M1` §2). A host that wants the flag must send responses.

Also owed: `MEASUREMENT.md` §4 lists six flags and `undeterminable` is a seventh.
The published table needs the row (and the `context` severity beside it), or a
host reading only the contract will not know the value can appear in
`validity_flags`.

## 24. `decide` has some of its codes, and decisions are still not erased

* ~~A replay of a decision id that does not exist answers `404 route_unknown`,
  because the closed code set has no `decision_unknown`.~~ **Closed:** the code
  exists (`INTEGRATION.md` §6), and `POST /v1/decisions/{id}/replay` answers
  `404 decision_unknown` with `details.decision_id`. `check_openapi` counts 23
  documented codes and fails on an undocumented one.
* `decisions` rows are personal data (derived) under `PRIVACY.md` §5 and must be
  erased with the learner. They are not, today — the same gap as §18 for
  `learner_mastery`, and both join the erasure path in increment 7. A decision
  record that survives an erasure request is worse than a mastery row, because it
  also holds the advice that was served.
* `spaced_review` returns a **documented default** interval (7 days) marked
  `basis: "documented_default"`, cited to the spacing claim. There is no retention
  model at L1; presenting a number as if there were one would be the exact failure
  this project keeps naming.
* A `data_quality` abstention carries a `flag_evidence` object (the counted and
  ignored flags, with the severity table they came from). It is additive and
  `INTEGRATION.md` §6 does not show it; the body in the contract should gain the
  field when the doc is next revised, so a host does not discover it by diffing
  responses.
* `decide` and its replay now return `X-Adapt-Billed` and `X-Adapt-Test-Traffic`.
  Neither is in `INTEGRATION.md`; both are additive, and `X-Adapt-Billed` is the
  cheapest way for a host to see §3 rule 1 holding on its own traffic. Owed a row.
* `AGENT_M1` §5 says "staging keys set it automatically". A key carries only
  `live`/`test`, and `expected_key_env` refuses a test key in production, so there
  is no such thing as a staging *key* in production — the flag is set by
  `ADAPT_ENV=staging` instead (`DECISIONS.md` §21.5). The contract sentence should
  say "a staging deployment" rather than "staging keys".
* Claims are per-tenant copies of global knowledge (§20.4), and there is no claim
  ingestion pipeline, no supersession chain and no `prohibited_claims` template
  check (`CLAIMS.md` §1). The five seeded claims are hand-written and reviewed by
  review only.

## 25. `rapid_guess` no longer fires on a fast correct answer, which costs a true positive

`MEASUREMENT.md` §4 says "response < 300 ms on a non-trivial item". The flag now
requires latency < 300 ms **and** an incorrect answer **and** an item that is not
known-trivial (`DECISIONS.md` §21.3). A fast, correct guess on a hard item with no
`difficulty_band` is therefore missed. That is the deliberate direction of the
error — a false positive abstains on the strongest learner — but it is an
unverified trade until a latency-carrying measurement corpus exists: nothing in
this tree measures how many real events fall in that skip. The measurement corpus
sends no latencies (`test_decide.py`), so the fix is covered by unit tests, not by
a corpus number.

## 26. Metering is proved for `decide`, and `decide` is the only billed route

`METERING` §2 prices `predict` at 1 credit and `probe score` at 3. Neither route
exists yet, so the four zero-billing properties (`DECISIONS.md` §21.5) are proved
for `decide` only. When those routes land they need their own empty-ledger tests —
the shape is in `tests/test_metering.py`, and the risk is that a new route is
written straight to `credits.reserve` without one.

Also unexercised: **an actual credit exhaustion under load.** The tests grant
credits and run a handful of decisions; nothing has run a tenant to zero and
checked that the `402` arrives before the reservation, that a rejected decision
writes no ledger row, and that the fairness codes stay distinct (402 vs 429 vs
403) under concurrency. `409`/`429` ordering is tested, exhaustion is not.

## 27. The metering corpus is one seed and one shape

The 176/24 split and the 352 ledger rows in `DECISIONS.md` §21.5 come from one
seeded corpus: 200 learners, mastery 0.1-0.9, 4-40 observations, one decision
each. It is pinned as a test so drift is caught, but it is not a distribution:
learners with 4-7 observations all abstain by construction (`EVIDENCE_FLOOR` = 8),
so the abstention share is a property of the generator as much as of the product.
Quoted as an arithmetic check on billing, never as a product metric.

## 28. The metrics endpoint cannot count refusals, errors, or the three axes

`METRICS.md` §1.2 reports `refusals`, `errors`, `entitlement_limits`,
`not_validated` and `not_capable` as their own series. None of them has a store:

* a 4xx refusal (prohibited claim, missing scope, exhausted credits) returns
  before anything is written, by design;
* a 5xx rolls its transaction back, including any row a request-log would have
  needed;
* the three axes are contract (`tenants.tier`), code (`capability_matrix`) and
  harness output, not per-request facts.

So `GET /v1/admin/metrics/abstention` returns `null` for all five and lists them
in `unmeasured` (`DECISIONS.md` §22.4). The fix is a request-level store — one row
per `/v1/decide` attempt with its status class, written outside the request
transaction — and it is not in this increment because a write on the error path is
where failures compound. Until it exists, the console must render these as
"unmeasured", not as zero: a 0 in a dashboard is read as an achievement.

Two smaller things owe a store:

* **`abstention_polling` is one fixture deep.** The 5× threshold is the
  contract's, but the baseline (median of connections with ≥ 10 pairs) is a
  choice made here, and it has only ever been run against hand-built corpora. A
  tenant whose connections *all* retry heavily never alerts, and a single polling
  host in a quiet tenant alerts on the volume floor alone. Both are the right
  direction; neither is calibrated against real traffic.
* ~~**The endpoint is not in `INTEGRATION.md`.**~~ Closed in increment 7: it is in
  §6A and §6D, with the abstention report's three rules. The `replay_count` half
  of this note is closed differently and better — the column is gone (migration
  `0017`), replaced by the append-only `decision_replays` table, so there is no
  counter for a read route to expose.

## 29. No `data_quality` abstention survives in the 200-learner corpus

After the `straight_line` fix (`DECISIONS.md` §21.1), five consecutive runs of the
seeded 200-learner corpus give **176 served / 24 abstained, with zero
`data_quality`** — the 23 `insufficient_evidence` and the single
`conflicting_signals` are stable run to run. That is the intended outcome and it
leaves a gap worth naming: the reason code that most often means "your
instrumentation is wrong" has no live example in the corpus that the product
numbers come from.

It is not untested: `tests/test_observe.py` raises it through the flag rules,
`tests/golden/decisions/08_abstain_data_quality.json` pins its body, and the
increment-6 fixture (`tests/fixtures/metrics_abstention.json`) builds three
`data_quality` abstentions through the real API at ~37 % flagged trials. What is
missing is a *realistic* one: a host whose instrumentation genuinely fails 30 % of
the time. Until one exists, the threshold (0.30 of a 20-trial sample) is a
contract with tests, not a calibrated boundary.

## 30. The console screenshot is rendered, not screenshotted

There is no headless browser in this environment and none is installed in CI: no
`playwright`, no Chromium, no `librsvg`, no `cairo`. `scripts/console_shot.mjs`
walks the vnode tree the console's own renderer produces and lays the text out as
SVG; `scripts/console_shot.py` draws the same block model to PNG with Pillow.

So `docs/console/*.png` is a faithful picture of the console's **content and
order** and not of its CSS layout, spacing or fonts. When a browser is available
(Playwright in CI, or a developer's machine), the right move is to replace the
rasteriser with a real screenshot and keep the text assertions, which are the ones
that catch a wrong number. The committed SVG/JSON sidecar is the reproducible
artifact; the PNG is generated by hand and should be regenerated when the block
model changes.

## 31. The OpenAPI document has no request bodies

Every route reads JSON itself (allowlisted keys, manual 422), so `requestBody` is
absent from the generated OpenAPI document for `observe`, `decide` and
`POST /v1/learners`. The SDK generator therefore declares those bodies by hand in
one table (`sdk/generate.py`, `BODIES`, taken from `INTEGRATION.md` §6A) instead of
deriving them. That table can drift from the routes: a field added to
`/v1/observe` and not to `BODIES` breaks at runtime, not at generation time.

The fix is pydantic request models on the routes, which would put the bodies in
the document and let `BODIES` be deleted. It is M2 work — it touches every route's
error paths, which the contract's `422 payload_invalid {field}` behaviour depends
on.

## 32. The console cannot show a connection's last event or event volume

`UI.md` §2.3 asks for a connections table with last-event time and volume.
`GET /v1/admin/connections` returns neither, and no endpoint does: the counter
lives in `connection_event_usage`, which is written by ingest and read by quota
checks, and there is no read route for it. Rather than invent a number or join in
the page, the console shows the monthly quota and the decision count from the
metrics endpoint, and says on the screen that the other two are missing. A read
route (`GET /v1/admin/connections/{id}/usage`) is owed before that column can be
filled honestly.

## 33. The sandbox tenant's level sentence is true only because the tier is `deep`

The console's inspector says "Entitled to L3; serving L1 where validation has not
passed", and that sentence is emitted from the response's own
`entitlement_level` / `served` / `validation_level` — not typed into the page. It
reads that way for the sandbox because `adapt sandbox seed` provisions a `deep`
tenant. A `core` tenant gets "Entitled to L1; serving L1 …" and the sentence is
equally true; what is *not* yet exercised is the case where entitlement, capability
and validation disagree in more than one axis.

## 34. A window with two naive timestamps names only one of them

`GET /v1/admin/metrics/abstention` normalises `to` before `from`, so a request with
*both* boundaries naive gets `422 … details.field = "to"` and no mention of
`from`. The refusal is correct and the field it names is genuinely wrong; what is
missing is the second name. Fixing it means collecting all field errors before
raising, which changes the error envelope for every route that validates more than
one parameter — a contract change for a message, so the increment-8 audit records
it here instead. **Not fixed, by choice.**

## 35. `POST /v1/decide` and `POST /v1/decisions/{id}/replay` disagree about their own noun

One route is singular and a verb, the other plural and a noun. Both are documented
and both are in the generated SDK, so renaming either now breaks every client the
SDK has produced — including the two in this repository. It was found by writing
the audit test and getting a 404 from a URL that read as if it should exist, which
is the argument for fixing it at the next breaking version and not tonight.
**Won't fix at M1.**

## 36. The console's `serving` mark is a chip, and §5 does not name a chip

`CONSOLE_STYLE.md` §5 has a *selected row* (`--accent-soft` fill, 2px `--accent`
left border) but no inline variant of it. The inspector needs one: `served` is a
single number in a table cell and the accent budget allows one mark per screen, so
the mark is drawn as a chip — the same fill and the same 2px border as the selected
row, at cell size. It is in the stylesheet, it is counted by gate 3, and it is
**not** in the spec. Either the spec gains a row or the chip goes; the console
should not be the only place that knows the grammar.
