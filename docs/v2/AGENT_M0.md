# Build prompt — Milestone 0 (foundations)

You are implementing **Adapt 2.0, milestone 0**. Read `docs/v2/SPEC.md` first; this is the work
order for one milestone.

Work in a **new directory `engine/`** in this repository. **`backend/`, `frontend/`, `scripts/`,
`e2e/` are read-only.** v1 is the frozen reference and must stay runnable.

---

## 0. How to work

**Six increments. Stop and report after each one.** Do not run them together — each has a
correctness property that a later increment depends on, and a wrong foundation is expensive to
unwind.

| # | Increment | Stops after |
|---|---|---|
| 1 | Skeleton, config, health, error envelope, logging | `docker compose up` serves `/v1/health` |
| 2 | Tenancy + RLS + the isolation test | the adversarial isolation test passes |
| 3 | API keys | issue → use → revoke → rejected, all in one test |
| 4 | Credit ledger + idempotency | the 100-way concurrency test passes |
| 5 | Encryption envelope + rotation | rotation test passes on pre-rotation data |
| 6 | Migrations round-trip, CI, docs | a clean checkout passes everything |

Report after each: what passed, what you changed from this prompt, what you could not do. Then stop.

### Non-negotiables

1. **No LLM, no network egress** in M0. No provider SDKs in dependencies.
2. **Deterministic:** inject a `Clock`; no bare `datetime.now()`, no `random` in business logic.
3. **Fail closed:** missing or weak config → refuse to start, name the variable, exit non-zero.
4. **No fabricated data:** fixtures are obviously synthetic; no PII; no seeded learners.
5. **Every error has one shape:** `{"error": {"code", "message", "request_id", "retryable"}}`.
6. **Logs are an allowlist,** not a redaction pass: `request_id, tenant_id, route, method, status,
   duration_ms, error_code`. Never a body, key, learner id, email, or provider name.
7. Never claim "validated", "proven" or "psychometric" in a comment — M0 has no measurement.

---

## 1. Increment 1 — skeleton

```
engine/
  pyproject.toml          # python 3.12; fastapi, pydantic>=2, sqlalchemy>=2, alembic,
                          # psycopg[binary], redis, structlog, pytest, mypy, ruff
  docker-compose.yml      # api, worker, postgres:16, redis:7
  Dockerfile
  src/adapt_engine/{main,config,clock,db,logging,errors}.py
  src/adapt_engine/api/{health,deps}.py
  tests/{conftest,test_health,test_config_fail_closed,test_error_envelope}.py
```

* `GET /v1/health` → `{"status","version","commit","db","redis","capabilities"}`; 503 naming the
  failed dependency. `capabilities` is booleans only — never values.
* `GET /v1/version` → version, commit, build time. Nothing environmental.
* Config: `ADAPT_` prefix, typed, validated at startup. Refuse to start if `ADAPT_ENV`,
  `ADAPT_DATABASE_URL`, `ADAPT_MASTER_KEY` (≥32 bytes) or `ADAPT_KEY_PEPPER` are missing, or if
  `ADAPT_ENV=production` with any development default.

* **Error codes are lowercase snake_case, exactly as tabulated in `INTEGRATION.md` §6**
  (`payload_invalid`, `rate_limited`, `quota_exhausted`, `not_entitled`, `learner_unknown`, …).
  `VALIDATION_ERROR` is not a code in this system. If a status has no documented code, that is a gap in
  the table — report it rather than inventing a name.
* **`engine/scripts/check_openapi.py`** — fails CI if any route lacks an auth requirement, if any
  response schema omits the error envelope, or if any emitted code is absent from the documented
  vocabulary.
* **`engine/.dockerignore`** — the build context must exclude `.venv`, `node_modules`, `.git`,
  `frontend`, `docs`, `var`, `*.zip`. Without it the context is gigabytes and may carry secrets.
* **Health checks are bounded and non-blocking** — dependency probes have an explicit timeout (~1 s)
  and do not block the event loop; a hung database must produce a 503, not a hung health endpoint.
* **In production, build identity is required** — `ADAPT_COMMIT` and `ADAPT_VERSION` must not default
  to `unknown` when `ADAPT_ENV=production`, or `/v1/version` cannot identify a deployment during an
  incident.

**Acceptance:** `test_config_fail_closed` — one parametrised case per required variable asserting a
non-zero exit and a message naming it. `test_error_envelope` — every registered handler returns the
one shape, including 404, 405, 422 and an unhandled 500, **and every emitted code appears in the
documented vocabulary**. `test_health` — a dependency timeout returns 503 rather than hanging. A live
server check: `/v1/health`, `/v1/version`, one 404 and one 405 envelope, and one allowlist log line per
request with no query string and no client address.

## 2. Increment 2 — tenancy and RLS

Tables (do not invent columns beyond these without saying so):

```
tenants(id uuid pk, slug text unique, name text, profile text, status text,
        created_at timestamptz default now(), check profile in ('adult_general','cognitive_support'))
api_keys(id uuid pk, tenant_id uuid not null references tenants, prefix text unique,
         hash bytea not null, scopes text[] not null, created_at, last_used_at, revoked_at)
credit_ledger(id bigserial pk, tenant_id uuid not null, delta int not null, reason text not null,
              ref text, idempotency_key text not null, created_at timestamptz default now(),
              unique (tenant_id, idempotency_key))
learners(id uuid pk, tenant_id uuid not null, profile text not null, date_of_birth date,
         created_at, deleted_at)
audit_log(id bigserial pk, tenant_id uuid, actor text, action text, subject text,
          meta jsonb, created_at)   -- no PII in meta
```

RLS, exactly this shape per tenant-scoped table:

```sql
ALTER TABLE learners ENABLE ROW LEVEL SECURITY;
ALTER TABLE learners FORCE  ROW LEVEL SECURITY;          -- without FORCE the owner bypasses it
CREATE POLICY tenant_isolation ON learners
  USING      (tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid);
CREATE INDEX ON learners (tenant_id);                     -- policy predicates need the index
```

* **`SET LOCAL`, inside an explicit transaction — never session-level `SET`.** A session-level `SET`
  leaks tenant context to the next request on a pooled connection. `SET LOCAL` outside a
  transaction silently does nothing.
* Set it per request from a dependency; **read it back and assert** it matches before running
  queries.
* App role is **not** superuser, **not** table owner, **not** `BYPASSRLS`. Migrations run as a
  separate role.
* Connection pool in transaction mode; if PgBouncer is ever in front, set
  `plan_cache_mode=force_custom_plan` on the app role so tenants do not share generic plans.
* Whether or not the tenant variable is set, a query must never return another tenant's rows —
  missing variable → zero rows, not an error and definitely not everything.

**Acceptance — `tests/test_tenancy_isolation.py`, and it must be adversarial:**

1. tenant A's session reads only A's rows; B's id is invisible even when queried by primary key.
2. A raw connection with no `adapt.tenant_id` sees zero rows from the table.
3. `INSERT` with a foreign `tenant_id` is rejected. **Note the mechanism:** when `WITH CHECK` is
   omitted, PostgreSQL applies `USING` to new rows, so simply dropping the clause does not open a
   write hole. The hole appears when the check is non-constraining — sabotage with
   `WITH CHECK (true)`, which is the case this test actually proves.
4. `UPDATE ... WHERE tenant_id = <B>` affects zero rows.
5. A filter value containing SQL does not cross tenants.
6. Dropping to a fresh transaction on a recycled connection shows no residual tenant context.

## 3. Increment 3 — API keys

* Format `ak_live_<32 base32>` / `ak_test_…`; store `prefix` (first 12 chars, indexed) +
  `hash = HMAC-SHA256(pepper, full_key)`. **Not bcrypt** — you need O(1) lookup, and the pepper makes
  an offline database dump useless without the secret.
* Shown once, never retrievable. Scopes: `observe`, `decide`, `read_model`, `admin`; enforced per route.
* Revocation is immediate. `last_used_at` written at most once per minute per key.
* Constant-time comparison; identical timing and message for unknown vs revoked keys.

**Acceptance:** one test runs issue → authorised call → revoke → 401; another asserts the stored
value is not the key and cannot be reversed; another asserts `last_used_at` write amplification is
bounded.

### Resolving a key before the tenant is known

A key must be looked up before `adapt.tenant_id` exists, so the lookup cannot run under tenant RLS.
Measured on the increment 2 schema: a `SECURITY DEFINER` function owned by the table owner sees
**1 row with `ENABLE` alone, 0 rows with `ENABLE + FORCE`**. Forced RLS therefore makes a
tenant-scoped key table unreadable by its own resolver. The resolution is not to weaken RLS but to
stop relying on it for this table:

* The resolver is `SECURITY DEFINER`, owned by `adapt_owner`, `SET search_path = pg_catalog, public`,
  `REVOKE ALL … FROM PUBLIC`, `GRANT EXECUTE … TO adapt_app`. Exact match on the indexed prefix — no
  `LIKE`, no prefix scan, so it cannot be used to enumerate keys.
* **`adapt_app` holds no table privilege on `api_keys` at all** — no `SELECT`, no `INSERT`, no
  `UPDATE`, no `DELETE`. The resolver is the only reader and the only writer of `last_used_at`.
  Asserted by `has_table_privilege`.
* `api_keys` keeps `ENABLE ROW LEVEL SECURITY` and a tenant policy without `FORCE`, as defence in
  depth for a future grant. With no app-role privileges this is belt-and-braces, not the boundary.
* **The stated principle:** RLS keeps tenants apart; grants keep key material from everyone. Two
  mechanisms, two problems. Do not let one stand in for the other.

Consequences to implement, not to defer:

* An `admin` key may issue only keys whose scopes are a **subset of its own**, and may never mint
  another `admin` key — admin keys are created by provisioning or the console session, not over the
  API. Otherwise scope escalation is one call away.
* `expires_at` exists on every key: required for `admin`, default 90 days for the rest, nullable only
  by explicit decision. Rotation is issue-new-then-revoke-old, and the console shows `last_used_at`.
* Key issue, revoke and failed authentication write `audit_log` rows **now**. Retro-fitting an audit
  trail leaves the first months of a security-sensitive table with no trail at all.
* The failed-auth limiter counts **failures only, after** validation — a shared NAT with many valid
  keys must never be blocked by other tenants' failures. Per process until Redis exists, with the
  multiplier (`N processes = N× budget`) stated in the code and in `KNOWN_GAPS.md`.

## 4. Increment 4 — credits

Three tables, per `METERING.md` §4 — grants have identities, because a monthly allowance and a
purchased pack behave differently and must be drawable in a published order:

```
credit_grants(id, tenant_id, kind: included|purchased|promo, amount, remaining,
              granted_at, expires_at NULLABLE, source_ref)
credit_ledger(id, tenant_id, grant_id NULLABLE, delta, reason, ref, idempotency_key,
              period_start, created_at)     -- append-only, unique(tenant_id, idempotency_key)
credit_reservations(id, tenant_id, amount, state: held|settled|released, operation_ref)
```

* Balance is a query over non-expired grants, never a mutable column.
* Reserve → settle for anything above 1 credit. Atomic, inside one transaction:

```sql
-- row-lock the tenant's open grants, then insert the reservation
SELECT id, remaining, expires_at FROM credit_grants
 WHERE tenant_id = $1 AND (expires_at IS NULL OR expires_at > now())
 ORDER BY expires_at NULLS LAST, granted_at
 FOR UPDATE;
```

* **Drawdown order is expiring-first, then purchased oldest-first** — the order that protects the
  customer's money. It is a test, not a comment.
* `unique (tenant_id, idempotency_key)` is the correctness mechanism, not application logic. Replays
  return the original result; never double-charge.
* Failed work releases its reservation idempotently; a release never debits.

**Acceptance:** 100 concurrent reservations against a balance of 10 → exactly 10 succeed, final
balance 0, no negative row. Same idempotency key replayed 5× → one ledger row and identical response
body. A grant expiring mid-flight leaves the settled amount debited and the remainder released.

## 5. Increment 5 — encryption

* Per-tenant data key, AES-GCM, wrapped by `ADAPT_MASTER_KEY`; versioned ciphertext header
  (`v1:kid:nonce:ct`) so rotation is possible without re-encrypting data.
* Rotation = re-wrap data keys only; test decrypts pre-rotation ciphertext after rotation.

**Acceptance:** tenant A's ciphertext cannot be decrypted with B's key (must raise, not return
garbage); rotation test passes; an AAD binding of `tenant_id` is asserted.

## 6. Increment 6 — migrations, CI, docs

* Alembic, single head, up **and** down tested (`upgrade head` → `downgrade base` → `upgrade head`).
* CI: ruff, mypy strict on `src/`, pytest, OpenAPI contract lint (`engine/scripts/check_openapi.py`:
  every route declares auth, every response references the error schema), migration round-trip,
  docker build.
* `engine/README.md` (run, rotate, reset, add a migration) and `engine/DECISIONS.md` (each choice
  not specified here, one line, with the rejected alternative).

**Also in this increment (cheap now, expensive later):**

* Age gate: `learners.date_of_birth` rejecting any DOB implying under 18 at creation, error code
  `minor_not_supported`, with a test. There are no learner endpoints yet — write the constraint and
  the test anyway.
* `assert_profile(learner, expected)` helper + test; `ADR-001` depends on it.
* `tenants.tier` (`core | plus | deep`) with a default, plus a **pure function**
  `resolve_level(requested, entitlement, capability, validated) -> (served, limited_by)` and a unit
  test covering every ordering, including the case that matters: entitled L3, capable L3, validated L1
  → served L1, `limited_by="validation"`. This is the architectural spine of `ADR-002`; it costs an
  hour now and a refactor later.
* `learner_id` format validation (reject `@`, `+`, 7+ digit runs) with the error message telling the
  integrator not to send PII (`PRIVACY.md` §2).

---

## 7. Definition of done

- [ ] `docker compose up` works from a clean clone, no manual steps
- [ ] All six increments' acceptance tests pass; suite under 60 s; concurrency test run 20× without a flake
- [ ] Every tenant-scoped table: RLS enabled **and** forced, index on `tenant_id`, policy with `USING` and `WITH CHECK`
- [ ] App role has no `SUPERUSER`, no `BYPASSRLS`, is not the table owner
- [ ] No secret in code, fixtures, logs or git history; `git log -p | grep` for the pepper returns nothing
- [ ] `git diff --stat fb655b3 -- backend frontend scripts e2e` is empty
- [ ] `engine/DECISIONS.md` and `engine/README.md` written

## 8. Report format

1. Increment number, and `git diff --stat` for it.
2. Tests: counts and the names of the isolation, concurrency, idempotency and fail-closed tests.
3. Deviations from this prompt, with the reason.
4. Anything in v1 that contradicts `SPEC.md` — report it, do not fix it.
5. What you deliberately left out and which milestone owns it.

## 9. Do not

* Port v1's 146 routes, 13-strategy catalogue, or provider gateway.
* Add an LLM, vector store, embeddings, or a web UI.
* Build any claims or evidence surface. M1 ships a static seeded claims table; the ingestion pipeline is
  M2. If you find yourself writing a `claims` table here, stop and report the scope question instead.
* Implement `cognitive_support` behaviour beyond the profile column, the age gate and
  `assert_profile` — it belongs to M3.5.
* Add features not listed here "for completeness". Unrequested scope is a defect, not a bonus.
