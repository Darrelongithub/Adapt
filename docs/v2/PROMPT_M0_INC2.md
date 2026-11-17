# Paste-ready prompt — Increment 1.5 (remediation) + Increment 2 (tenancy + RLS)

Read `docs/v2/README.md`, then `docs/v2/AGENT_M0.md` §1–§2. Increment 1 is accepted with conditions;
clear them before starting increment 2, because increment 2's tests need a working Postgres and the
error vocabulary must be settled before anything is built on top of it.

---

```
You are continuing Adapt 2.0 M0 on branch `arena/48488ec8-adapt`. Increment 1 is reviewed and
accepted with conditions. Read `docs/v2/AGENT_M0.md` §1–§2 and `docs/v2/INTEGRATION.md` §6 on branch
`arena/cb97052d-adapt` — §6's code table is authoritative.

Do INCREMENT 1.5 first. It is small, and increment 2 depends on all of it.

## INCREMENT 1.5 — CLEAR THE CONDITIONS

1. `engine/.dockerignore` — exclude `.venv`, `node_modules`, `.git`, `frontend`, `docs`, `var`,
   `*.zip`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`. Report the context size
   before and after (`docker build` prints it, or `du`).
2. **Retire the SCREAMING_SNAKE error codes.** Replace `VALIDATION_ERROR`, `RATE_LIMITED`,
   `NOT_FOUND`, `FORBIDDEN`, `INTERNAL_ERROR`, `ERROR` etc. with the lowercase codes in
   `INTEGRATION.md` §6: `payload_invalid`, `rate_limited`, `learner_unknown`, `not_entitled`,
   `quota_exhausted`, `connection_paused`, `not_capable`, `invalid_api_key`, `insufficient_credits`.
   Where a status has no documented code, emit `unmapped_status` and log a warning — do not invent a
   name silently. Keep the status codes unchanged. Update the tests to assert the vocabulary.
3. `engine/scripts/check_openapi.py` — a CI script that fails when:
   (a) any `/v1` route lacks an auth requirement (health and version excepted, listed explicitly);
   (b) any response schema omits the error envelope;
   (c) any error code emitted in `errors.py` is absent from the `INTEGRATION.md` §6 table.
   It must read the table from the doc rather than keeping a second copy, so the two cannot drift.
   Wire it into whatever `make lint` / task runner exists; it must exit non-zero on failure.
4. **Bounded, non-blocking health probes.** Wrap the DB and Redis checks in `asyncio.wait_for(…, 1.0)`
   running in a threadpool (or use async drivers). A timeout is `False`, which yields 503. Test it:
   point `ADAPT_DATABASE_URL` at a blackholed address so the connection hangs, and assert `/v1/health`
   returns 503 in <2 s rather than hanging.
5. **Production build identity.** When `ADAPT_ENV=production`, `ADAPT_COMMIT` and `ADAPT_VERSION` are
   required — refuse to start naming the variable, same as the other four. Test it.
6. **Prove the package installs.** `pip install .` (not editable) inside the 3.12 image, then
   `python -m adapt_engine.main` — increment 1 ran only via `PYTHONPATH=src` on 3.11. Run the full
   suite on 3.12 and report the interpreter version in the output.
7. **`docker compose up` once.** This was unverified. Paste the output of `docker compose up -d`,
   `curl -s localhost:8000/v1/health`, and `docker compose ps`. If Docker is still unavailable, say so
   again and state plainly that increment 2 cannot be verified without it.

Report 1.5 separately and stop before increment 2 if any of the seven is incomplete.

## INCREMENT 2 — TENANCY, RLS, MIGRATIONS

Schema: `tenants`, `learners`, `audit_log`. Plus whatever Alembic needs. No domain tables yet.

Non-negotiable details — these are the traps, not preferences:

* `tenant_id uuid NOT NULL` on every tenant-scoped table, FK to `tenants`, and an index on it.
  The index is not an optimisation: it is the difference between a policy check that scans and one
  that seeks.
* Two roles. `adapt_owner` owns the tables and runs migrations. `adapt_app` is the runtime role, has
  DML only, is **not** the table owner, is **not** superuser, has **no** `BYPASSRLS`. If the app role
  owns a table, RLS is bypassed and every test below passes while the hole stays open.
* On every tenant-scoped table, both:
  ```sql
  ALTER TABLE learners ENABLE ROW LEVEL SECURITY;
  ALTER TABLE learners FORCE  ROW LEVEL SECURITY;   -- FORCE: the owner is subject too
  ```
* Policy with **both** clauses. `WITH CHECK` is not decoration and its absence is not automatically a
  hole — PostgreSQL applies `USING` to new rows when the clause is omitted — but it is the only way to
  make the write rule explicit, and to keep it *narrower* than the read rule when a read reason later
  broadens `USING`:
  ```sql
  CREATE POLICY tenant_isolation ON learners
    USING      (tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid);
  ```
* Tenant context is set **inside an explicit transaction**, per request:
  ```sql
  BEGIN;
  SET LOCAL adapt.tenant_id = '<uuid>';
  ```
  `SET LOCAL` outside a transaction is a silent no-op; session-level `SET` leaks across pooled
  connections. Enforce it in one place — a dependency or `begin` event listener — not per query. With a
  transaction-mode pooler in front, also set `plan_cache_mode=force_custom_plan`.
* `audit_log` is append-only: `adapt_app` holds `INSERT` and `SELECT`, **never** `UPDATE` or `DELETE`.
  Enforce with grants, not convention. Columns: `id, tenant_id, occurred_at, actor, action, target,
  metadata`. No learner content in `metadata`.
* Alembic: review generated migrations by hand — no autogenerate committed unread — and no data in
  migrations. Down must actually work.

## ACCEPTANCE TESTS — all six, none skipped, none xfail

`test_tenancy_isolation.py`:

1. A cannot read B's row by primary key — get returns empty/404, not another tenant's row.
2. A connection with **no** tenant variable set sees **zero** rows.
3. `INSERT` with a foreign `tenant_id` is rejected by `WITH CHECK`.
4. `UPDATE … WHERE tenant_id = <other>` affects **zero** rows and raises no error.
5. A hostile value in a filter (`' OR '1'='1`, `%`, a UUID belonging to B) stays inside tenant A.
6. A **fresh transaction on a recycled connection** shows no residual tenant context. Open a
   transaction as A, commit, return the connection to the pool, check it out again with no
   `SET LOCAL`, and assert zero rows.

`test_migrations.py`: up → down → up on a clean database.

**Red-then-green is mandatory.** For each of the six, demonstrate the test can fail: temporarily
`NO FORCE` the table, recreate the policy `WITH CHECK (true)`, then set the tenant with session-level
`set_config(…, false)` — paste the output showing the relevant test going red each time, then restore
and show green. Dropping the `WITH CHECK` clause entirely will **stay green**, because PostgreSQL uses
`USING` as the write check when it is absent; that is a property of the server, not a passing test, and
it must not be reported as one. A test that cannot fail is not evidence. Include the command and the
output in the report.

Also add: `learners.learner_id` format validation (reject `@`, `+`, and runs of 7+ digits) with its
test, since this is the table where it belongs.

## DEFINITION OF DONE

Compose up from clean clone; suite <60 s; the six isolation tests run 20× consecutively with no flake;
every tenant-scoped table has forced RLS, a `WITH CHECK` policy and a `tenant_id` index; app role is
not an owner; `audit_log` cannot be updated or deleted by the app role; v1 paths untouched
(`git diff --stat fb655b3 -- backend frontend scripts e2e` empty).

## REPORT

Same six-part format as increment 1. Additionally: the red-then-green output, the `du` of the build
context, the interpreter version, and any place where this prompt was ambiguous enough that you had to
choose — ambiguity found now is cheaper than ambiguity found in increment 4.

DO NOT add domain tables, auth middleware, routes or tenancy to `backend/`. Increment 3 (API keys) is
next and it depends on increment 2 being trustworthy.
```

---

## FOLLOW-UP FROM INCREMENT 2 (apply before increment 3)

1. **`unmapped_status` is now a documented row in §6**, and `410 gone` is restored. Earlier it was
   missing because of a botched edit on my side, not a decision — the guard was right to fail.
2. **Derive documented codes from every row of the §6 table, not only rows with a numeric status.**
   The sentinel's status cell reads `any other`. Keep rule (c) absolute and whitelist-free: the doc is
   the only allowlist. Update `parse_doc_codes` (or add a `documented_codes` pass) and its tests.
3. **`adapt.tenant_id` is the canonical setting name.** `AGENT_M0.md` said `app.tenant_id` in one
   place; that was a documentation error and is now corrected.
4. **Tenant creation is provisioning, not runtime.** `adapt_app` holds `SELECT` on `tenants` only —
   remove the `INSERT`/`UPDATE` grants. A tenant is created by the operator or control plane as the
   owner role, never by the request-serving role. Add a schema test asserting the grant set.
5. `learner_id` unique per `(tenant_id, learner_id)` is correct — two tenants may each have `L-42`.
6. CI must fetch the docs ref or pass `--doc`; the checker's exit 2 is correct behaviour and the four
   checker tests will otherwise read as red.

---

## IF DOCKER IS UNAVAILABLE

If the build environment cannot run Docker, do **not** skip increment 2 and do **not** fake it. Stand
up a real PostgreSQL as a **test fixture** and run the same suite against it:

* Pin a bundled-Postgres wheel (e.g. `pgserver`) as a **dev-only** extra. Never a runtime dependency.
* The suite reads `ADAPT_TEST_DATABASE_URL`. The fixture is one provider; `docker compose` is another.
  Hard-coding a server into the tests is a rejection.
* Report `SELECT version()` and the server's auth model. If it is not 16, say so — RLS semantics have
  been stable since 9.5, so the isolation tests remain valid, but the compose target is still 16.
* Two roles still apply (`adapt_owner`, `adapt_app`), created by the fixture exactly as the migration
  would.

`docker compose up` then stays an **open verified-later item**, recorded in the repo
(`engine/KNOWN_GAPS.md`) until someone with Docker runs it. An unverified acceptance line that nobody
wrote down becomes a false claim in a month.

---

## Why 1.5 comes first, and why the red-then-green demand matters

**1.5 is not tidying.** Three of those seven unblock or invalidate increment 2: no Postgres means the
isolation test cannot exist, and a settled error vocabulary means increment 3's auth failures don't
invent a parallel set of names.

**The red-then-green requirement is the whole review.** RLS tests fail in one direction only — they go
green when the policy is missing *and* the test is wrong, and you cannot tell the two apart from a
passing run. Making the agent break the policy on purpose and show the test catching it is the only
cheap proof that the test has teeth. If that output is missing or shows the test staying green, the
increment is not done regardless of what else passes.

**The three traps behind it:** `FORCE` (an owner bypasses its own policy), `WITH CHECK` (`USING` alone
lets a tenant write rows *into* another tenant), and `SET LOCAL` (a session-level `SET` persists on a
pooled connection and hands the next tenant the previous tenant's context). All three produce a system
that passes its own tests and leaks.
