# Adapt engine

The learning-intelligence engine behind Adapt. A host (a school, a learning
product) sends us a learner and an interaction; we answer with what to do next,
how confident we are, and — when we are not — that we abstain. We do not
operate learners: the tenant owns identity, rosters and content, and hands us an
opaque `learner_id`.

This is milestone 0: the skeleton that everything else hangs off. It has
tenancy with row-level security, API keys, credits, encryption at rest, a
health/version surface and no learning science yet. The learning engine is the
next milestone; the shapes it must fit are fixed here on purpose, because the
tenancy and the errors are the parts that are expensive to change later.

Read these three files before writing code:

| File | What it decides |
|---|---|
| `DECISIONS.md` | every choice a reader could reasonably have made differently, with the reason and what would make it wrong |
| `KNOWN_GAPS.md` | what is not built, not tested, or not true yet — one table at the top, detail below |
| `../docs/v2/INTEGRATION.md` | the host-facing contract: error codes, response shapes, what a host may rely on |

## Six minutes from clone to a 200

Linux or macOS, Python 3.12, and a PostgreSQL 16 (Docker is optional; a local
server of any 16.x works).

```bash
# 1. clone and install (about a minute)
git clone <repo> && cd <repo>/engine
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 2. start PostgreSQL (pick one, ~15 s)
.venv/bin/python scripts/dev_postgres.py   # no docker: bundled 16.x, prints a URL
#   ...or, if you have docker (see KNOWN_GAPS §1 — this path is unexercised):
docker compose up -d postgres

# 3. create the two roles this engine depends on (owner and app)
export ADAPT_TEST_DATABASE_URL=postgresql://postgres:adapt@localhost:5432/postgres
.venv/bin/python scripts/bootstrap_db.py

# 4. apply the schema
export ADAPT_MIGRATION_DATABASE_URL=postgresql://adapt_owner:adapt-owner-dev-password@localhost:5432/postgres
.venv/bin/python -m alembic upgrade head

# 5. start it
export ADAPT_ENV=development
export ADAPT_DATABASE_URL=postgresql://adapt_app:adapt-app-dev-password@localhost:5432/adapt
export ADAPT_MASTER_KEY=dev-master-key-32-bytes-long-xxxx
export ADAPT_KEY_PEPPER=dev-pepper-synthetic-only
.venv/bin/python -m uvicorn adapt_engine.main:app --port 8000

# 6. ask it something
curl -s localhost:8000/v1/health | jq
```

`/v1/health` answers 200 when the process is up and its dependencies answer;
`/v1/version` says which build. Neither requires a key — an operator checking a
deploy should not need a credential.

Provision a tenant and get its first key (owner credential):

```bash
export ADAPT_MIGRATION_DATABASE_URL=postgresql://adapt_owner:...@localhost:5432/adapt
.venv/bin/python scripts/provision_tenant.py --slug acme --name "Acme College"
# prints tenant_id and one API key; the key is stored only as a digest
curl -s -H "Authorization: Bearer <key>" localhost:8000/v1/whoami | jq
```

## Where the invariants live

Each of these is enforced by something that fails, not by a comment:

| Invariant | Enforced by | Proved by |
|---|---|---|
| One tenant cannot see another's rows | `FORCE` RLS + `SET LOCAL adapt.tenant_id` in every transaction | `tests/test_tenancy_isolation.py`, sabotage modes in `tests/conftest.py` |
| Routes never filter by tenant themselves | `tenant_connection` dependency | `tests/test_auth_http.py` (another tenant's id is a 404, not a 403) |
| No API-key hash leaves the database | `adapt_resolve_api_key(text, bytea)` returns no hash; no table privileges on `api_keys` | `tests/test_schema.py::test_no_definer_function_returns_a_hash` |
| Credits balance: `granted = available + held + consumed + forfeited` | derived on read; consumption only from `entry_type='settle'` | `tests/test_credits.py` (every money-moving test asserts it) |
| Personal data is ciphertext at rest, bound to its cell | AES-GCM, AAD = tenant + row + column | `tests/test_crypto.py` (swap, move, AAD mismatch all raise) |
| A master key can actually be retired | header carries a data key version; `kid` lives on the wrapped-key row | `tests/test_crypto.py::test_a_master_key_can_be_retired_after_rewrap` |
| A retired key cannot be dropped too early | startup refuses when a kid in use has no key | `tests/test_crypto.py::test_startup_still_refuses_when_a_key_is_dropped_before_rewrap` |
| Errors carry one shape and only documented codes | one handler; `scripts/check_openapi.py` against the contract doc, read as written | `tests/test_check_openapi.py` |
| Migrations are reversible and schema-only | `up → down → up` in CI; no `INSERT`/`UPDATE` in a migration | `tests/test_migrations.py` |
| A paused connection says so, and a revoked one stops authenticating | `403 connection_paused` raised in `ingest_connection`; revoke revokes the key in the same transaction | `tests/test_connections.py::test_pause_is_an_explicit_403_and_resume_lifts_it` |
| A webhook signature cannot be forged or replayed | Standard Webhooks HMAC + a five-minute window + a bounded seen-set | `tests/test_webhooks.py` (forged, other-secret, stale, duplicate) |
| One event is delivered once | derived `webhook-id` + `UNIQUE (tenant_id, webhook_id)` | `tests/test_connections.py::test_a_duplicate_enqueue_is_a_no_op` |
| A delivery log never holds a payload | no body column exists | `tests/test_connections.py::test_the_delivery_log_has_no_place_for_a_payload` |
| A batch is stored once, however many times it is sent | `UNIQUE (tenant_id, idempotency_key)` + the stored response replayed verbatim | `tests/test_observe.py::test_the_same_batch_twice_returns_the_original_response_and_writes_nothing` |
| A guesser is never served mastery (G2, blocks release) | shrinkage toward the cohort prior + a cap at `p_guess + 0.05` when the evidence does not clear guessing | `tests/test_mastery.py::test_a_guesser_is_never_served_mastery` (plus two adversarial constructs) |
| Below eight observations the answer is a count, never a number | `insufficient_evidence` + `needs.trials`, raised before the estimator runs | `tests/test_mastery.py::test_below_the_floor_the_answer_is_a_count_not_a_number` |
| The same history serves the same bytes | stored counters + stored prior version + a total order on trials; no RNG, no ambient clock | `tests/test_mastery.py::test_the_same_history_serves_the_same_bytes`, `tests/golden/mastery/` |
| A refit cannot quietly change what a customer is told | a new `skill_priors` version per fit, refused below 20 learners / 200 observations | `tests/test_mastery.py::test_the_refit_writes_a_version_and_logs_the_fit`, `test_the_refit_refuses_a_cohort_below_the_floor` |
| A replayed or duplicated batch is not evidence | the fold runs on the batch's `fresh` list, inside the batch transaction | `tests/test_mastery.py::test_a_replayed_batch_does_not_move_mastery` |
| Over quota is a free 402 that writes nothing | one conditional upsert; the batch row is written after it returns a seat | `tests/test_observe.py::test_over_quota_is_402_with_resets_at_and_writes_nothing` |
| A paused connection's events are refused, not dropped and not stored | the pause refusal happens before the idempotency row and before quota | `tests/test_observe.py::test_a_paused_connection_cannot_ingest_and_nothing_is_stored` |
| No learner's free text is stored | `response` is hashed at ingest; only `response_digest` and `response_length` exist | `tests/test_observe.py::test_the_free_text_answer_is_hashed_and_not_stored` |
| An unknown field is refused, and the error names it | a closed allowlist in `observe/schema.py` | `tests/test_observe.py::test_an_unknown_key_in_an_event_is_rejected_and_named` |

## Routine operations

```bash
make check          # lint + format + types + contract + tests (what CI runs)
make ci             # the whole gate, including migrations and the in-tree contract
make test           # pytest only
make migrate        # alembic upgrade head (owner URL)
make roundtrip      # alembic up → down → up on a scratch database
```

**Create a connection.** Control-plane: an `admin` key, not a host runtime key.

```bash
curl -s -X POST localhost:8000/v1/admin/connections \
  -H "Authorization: Bearer $ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"host_name":"acme-web","environment":"test","event_quota_monthly":250000,
       "webhook_url":"https://hooks.example.test/adapt"}' | jq
# 201: {connection, key{full_key shown once}, webhook_secret{shown once}}
curl -s -X PATCH localhost:8000/v1/admin/connections/$ID \
  -H "Authorization: Bearer $ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"status":"paused"}'        # ingest now answers 403 connection_paused
curl -s -X DELETE localhost:8000/v1/admin/connections/$ID \
  -H "Authorization: Bearer $ADMIN_KEY"   # its key now answers 401
```

**Send events.** `POST /v1/observe` takes up to 500 events per batch (1 MB), an
`Idempotency-Key` derived from the logical operation, and nothing else. `202`
returns the counts, the flags that fired and the month's usage; the same key
replays the stored response; a key with a different body is `409`.

```bash
curl -s -X POST localhost:8000/v1/observe \
  -H "Authorization: Bearer $CONNECTION_KEY" -H 'content-type: application/json' \
  -H "Idempotency-Key: $DERIVED_KEY" \
  -d '{"events":[{"learner_id":"learner-0001","skill":"skill.fractions",
       "item_id":"item-001","correct":true,"ts":"2026-11-17T09:00:00Z",
       "decision_id":"dcn_…"}]}' | jq
# 202: {batch_id, accepted, duplicates, flags, decision_attribution, usage{…,resets_at}}
# 402 quota_exhausted (free, nothing written) | 403 connection_paused | 413 | 422
```

**Keep the partitions ahead.** `learning_events` is partitioned by month, and the
runtime role cannot run DDL. Run `python -m scripts.ensure_event_partitions`
monthly (as the owner) and `--check` nightly: it exits 1 when an event has landed
in the default partition, which is the only warning the design gives.

**Read a mastery estimate.** `GET /v1/mastery/{skill}?learner_id=...` returns
either a calibrated value with an interval, or an abstention that names the count
that would unlock it. `GET /v1/mastery?learner_id=...` returns every skill the
learner has evidence on. Reads need the `read_model` scope.

```bash
curl -s -H "Authorization: Bearer <key>" \
  "localhost:8000/v1/mastery/skill.fractions?learner_id=learn-0001" | jq
# { "value": 0.54, "interval": [0.3256, 0.7469], "interval_mass": 0.95,
#   "trials": 12, "estimator": "bkt_beta_v1", "prior": {...} }
# { "reason": "insufficient_evidence", "needs": { "trials": 3 }, "trials": 5 }
```

**Refit the priors (nightly, owner credential).** A cohort below 20 learners or
200 new observations is *refused*, not fitted — the previous version keeps
serving and the refusal is printed with its counts.

```bash
.venv/bin/python -m scripts.refit_cohorts --database-url "$ADAPT_MIGRATION_DATABASE_URL"
```

**Golden files.** `tests/golden/mastery/*.json` pin the served number for three
histories. They are not fixtures to regenerate: a changed number is a changed
customer-facing value and needs a version bump (DECISIONS §19.6).

**Measure ingest before believing a number.** `python -m scripts.load_observe
--events 400000 --workers 4 --admin-key …` against a running server prints
p50/p95/p99 per batch and µs/event against METERING §1.1.

**Add a migration.** `migrations/versions/` is hand-written: autogenerate
cannot express `FORCE ROW LEVEL SECURITY`, the two policy clauses or the grants,
and those are the parts that decide whether a tenant can see another's rows.
Number the file, set `down_revision` to the current head, write the `downgrade`
first, and run `make roundtrip`. A migration carries schema, not data.

**Rotate the master key.** Re-wrapping touches one column on one row per tenant
and no learner data. The full lifecycle, the preflight question and its answer
are in `DECISIONS.md` §13 and at the top of `scripts/rotate_master_key.py`:

```bash
.venv/bin/python scripts/rotate_master_key.py status
# start the service with ADAPT_MASTER_KEY=<new> ADAPT_MASTER_KEY_ID=k2
#                and   ADAPT_MASTER_KEY_PREVIOUS=<old> ADAPT_MASTER_KEY_PREVIOUS_ID=k1
.venv/bin/python scripts/rotate_master_key.py rewrap
.venv/bin/python scripts/rotate_master_key.py preflight k1   # must answer 0
# then drop ADAPT_MASTER_KEY_PREVIOUS and restart
```

**Reset.** `dropdb adapt && createdb adapt && alembic upgrade head`, then
provision again. There is no seed data: tenants are created by provisioning,
never by a migration.

**Read the audit log.** `scripts/audit_query.py` is owner-only and read-only:

```bash
.venv/bin/python scripts/audit_query.py --auth-failures --minutes 60
.venv/bin/python scripts/audit_query.py --key-events --tenant <uuid>
```

## What is open

See `KNOWN_GAPS.md` for the table and the detail. The short version: Redis has
never been reachable, the rate limiter is per process, the console screens are
owed, rotation has a command but no scheduler, data-key rotation does not exist,
webhooks have never reached a real receiver and nothing schedules the dispatcher
(§1a), and the learning model itself is increment 3.
