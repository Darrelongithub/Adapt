Kickoff prompt — paste-ready derivative of AGENT_M0.md. If they ever differ, AGENT_M0.md wins.

---

You are implementing **Adapt 2.0, milestone 0 (foundations)**, in the repository you have open.

CONTEXT
- Branch `arena/cb97052d-adapt`. v1 code is frozen at commit `fb655b3` and must stay runnable.
- Read, in this order, before writing code: `docs/v2/AGENT_M0.md` (the work order — authoritative),
  `docs/v2/SPEC.md` §1–§7, `docs/v2/PRIVACY.md` §2–§5, `docs/v2/METERING.md` §4, `docs/v2/ADR-002`.
- If you cannot read those files, stop and say so. Do not improvise the specification from this summary.

SCOPE
Build a new `engine/` directory: one FastAPI service + one worker, with tenancy, API keys, a credit
ledger, envelope encryption, migrations and CI. Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2,
Alembic, Postgres 16, Redis. No LLM, no model-provider SDK, no network egress, no web UI.

HARD RULES
1. `backend/`, `frontend/`, `scripts/`, `e2e/` are read-only. At the end,
   `git diff --stat fb655b3 -- backend frontend scripts e2e` must be empty.
2. Deterministic: inject a `Clock`; no bare `datetime.now()`; no unseeded randomness in business logic.
3. Fail closed: missing or weak config → refuse to start, name the variable, exit non-zero.
4. One error shape everywhere: `{"error":{"code","message","request_id","retryable"}}`.
5. Logs are an allowlist: `request_id, tenant_id, route, method, status, duration_ms, error_code`.
   Never a body, key, learner id, email or provider name.
6. No fabricated data in migrations or fixtures. No PII anywhere.
7. Never write "validated", "proven" or "psychometric" in a comment. M0 measures nothing.

WORK IN SIX INCREMENTS. STOP AND REPORT AFTER EACH. Do not run them together.

1. SKELETON — app factory, config, `Clock`, error envelope, logging, `/v1/health`, `/v1/version`,
   OpenAPI contract-lint script.
2. TENANCY + RLS — `tenants`, `learners`, `audit_log`; `tenant_id` NOT NULL everywhere; RLS
   **enabled and forced**; policy with **both** `USING` and `WITH CHECK`; tenant set via
   **`SET LOCAL` inside an explicit transaction** (never session-level `SET`); app role is not
   superuser, not table owner, no `BYPASSRLS`; index on `tenant_id`.
3. API KEYS — `ak_live_`/`ak_test_`, store `prefix` + `HMAC-SHA256(pepper, key)` (not bcrypt — you need
   O(1) lookup), shown once, scopes, immediate revoke, constant-time compare, bounded `last_used_at`.
4. CREDITS — three tables: `credit_grants` (kind, amount, remaining, `expires_at` NULLABLE),
   append-only `credit_ledger` (unique `(tenant_id, idempotency_key)`), `credit_reservations`.
   Balance is a query. Reserve → settle; release on failure, idempotently. Drawdown order:
   expiring allowance first, then purchased oldest-first.
5. ENCRYPTION — per-tenant data key, AES-GCM, wrapped by `ADAPT_MASTER_KEY`, AAD bound to `tenant_id`,
   versioned header `v1:kid:nonce:ct`, rotation re-wraps only data keys.
6. MIGRATIONS + CI + DOCS — alembic up/down/up; ruff, mypy strict, pytest, contract lint, docker build;
   `engine/README.md`, `engine/DECISIONS.md`.
   Also here (cheap now, expensive later): age gate rejecting any DOB implying under 18 with code
   `minor_not_supported`; `assert_profile()`; `learner_id` format validation rejecting `@`, `+` and 7+
   digit runs; `tenants.tier` plus a pure `resolve_level(tier, capability, validated) -> (served,
   limited_by)` with a unit test for entitled-L3/capable-L3/validated-L1 → served L1.

ACCEPTANCE TESTS THAT MUST EXIST
- `test_tenancy_isolation.py`, adversarial: (a) A cannot read B by primary key; (b) a connection with no
  tenant variable sees zero rows; (c) `INSERT` with a foreign `tenant_id` is rejected by `WITH CHECK`;
  (d) `UPDATE ... WHERE tenant_id = <other>` affects zero rows; (e) an injection attempt in a filter
  value does not cross tenants; (f) a fresh transaction on a recycled connection shows no residual
  tenant context.
- 100 concurrent reservations against a balance of 10 → exactly 10 succeed, balance 0, never negative.
- Same idempotency key replayed 5× → one ledger row, identical response body.
- One fail-closed config test per required variable (non-zero exit + message naming it).
- Tenant A's ciphertext cannot be decrypted with B's key (must raise, not return garbage).

DEFINITION OF DONE
`docker compose up` works from a clean clone with no manual steps; suite <60 s; concurrency test run 20×
without a flake; every tenant-scoped table has forced RLS, `tenant_id` index and a `WITH CHECK` policy;
no secret in code, fixtures, logs or history; v1 paths untouched; README and DECISIONS written.

REPORT AFTER EACH INCREMENT (and again at the end)
1. Increment number and `git diff --stat`.
2. Test counts plus the names of the isolation, concurrency, idempotency and fail-closed tests.
3. Every deviation from this prompt, with the reason.
4. Anything in v1 that contradicts `SPEC.md` — report it, do not fix it.
5. What you left out and which milestone owns it.

DO NOT
Port v1's 146 routes, 13-strategy catalogue or provider gateway. Add an LLM, vector store, embeddings or
web UI. Build any claims/evidence surface (that is M1/M2). Implement `cognitive_support` behaviour beyond
the profile column, the age gate and `assert_profile`. Add unrequested features — unrequested scope is a
defect, not a bonus.
