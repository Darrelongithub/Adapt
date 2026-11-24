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
| Errors carry one shape and only documented codes | one handler; `scripts/check_openapi.py` against the contract doc | `tests/test_check_openapi.py` |
| Migrations are reversible and schema-only | `up → down → up` in CI; no `INSERT`/`UPDATE` in a migration | `tests/test_migrations.py` |

## Routine operations

```bash
make check          # lint + format + types + contract + tests (what CI runs)
make ci             # the whole gate, including migrations and the docs patch
make test           # pytest only
make migrate        # alembic upgrade head (owner URL)
make roundtrip      # alembic up → down → up on a scratch database
```

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
and nothing in this milestone does learning science yet.
