# Decisions

Every entry records a decision that a reader could reasonably have made
differently, the reason it was made, and what would make it wrong. Entries are
numbered; tests reference these numbers where a test is the enforcement
mechanism.

---

## 1. `adapt_resolve_api_key` exists, and is the only thing that reads across tenants

**Decision.** API keys are resolved by a `SECURITY DEFINER` SQL function
(`engine/migrations/versions/0003_api_keys.py`), owned by `adapt_owner`, taking
one argument — the 12-character prefix — and returning
`(key_id, tenant_id, key_hash, scopes, revoked_at, last_used_at)`.

**Why it has to exist.** Every tenant-scoped read runs inside
`tenant_transaction`, which sets `adapt.tenant_id` and lets forced RLS filter
rows. Authentication cannot work that way: the key *is* the evidence of which
tenant is calling. Before the key is resolved there is no tenant id to set, so
the lookup cannot run under the tenant context it is trying to establish. The
function therefore runs with the table owner's rights, which is the only way to
read `api_keys` by prefix.

**Why the grants are narrow.** This is a deliberate hole in the tenancy model,
so it is kept as small as the problem allows:

- `EXECUTE` is granted to `adapt_app` and to nobody else. `PUBLIC` is revoked
  explicitly — PostgreSQL grants `EXECUTE` to `PUBLIC` by default, and leaving
  that in place would make the hole available to any role in the database.
- The function returns the hash, not the key, and only for one exact prefix.
  A caller that does not already hold a key learns nothing from it: the return
  value cannot be used to authenticate.
- `SET search_path = pg_catalog, public` is pinned in the definition, so a
  session whose `search_path` begins with an attacker-writable schema cannot
  shadow the `api_keys` table inside the function.
- `STABLE`, and the only write in the system's auth path (`last_used_at`) is
  performed by the caller afterwards, in tenant context, not inside the
  function.
- The body matches `WHERE k.prefix = p_prefix` — equality on a unique,
  indexed column. Not `LIKE`, not a range scan: those would read keys that
  nobody asked for, and would return more than one row per lookup.

**"The one place in the system that reads across tenants, and it must be the
only one."** Two tests enforce it: `test_resolver_execute_is_granted_only_to_the_app_role`
and `test_resolver_is_not_executable_by_public` (`tests/test_schema.py`), plus
`test_resolver_matches_the_prefix_exactly`, which asserts the source contains
the equality and no `LIKE`. If a second cross-tenant read is ever needed, it
gets its own decision entry and its own grant review.

---

## 2. `api_keys` has RLS **enabled but not forced** — the one deliberate asymmetry

**Decision.** `tenants`, `learners` and `audit_log` are `ENABLE` + `FORCE`.
`api_keys` is `ENABLE` only.

**Why.** `FORCE ROW LEVEL SECURITY` subjects the table owner to the policy as
well. `adapt_resolve_api_key` is owned by `adapt_owner` and therefore runs as
the owner; under `FORCE` it is subject to the policy, no `adapt.tenant_id` is
set (the whole point), and it returns zero rows. Measured on this schema, not
assumed:

| `api_keys` RLS      | rows the definer sees |
|---------------------|-----------------------|
| `ENABLE` only       | 1                     |
| `ENABLE` + `FORCE`  | 0                     |

**What is still protected.** The role that serves requests is `adapt_app`,
which is not the owner (`test_app_role_owns_nothing`), so the policy applies to
every request-serving connection. `test_app_role_cannot_see_api_keys_without_tenant_context`
and `test_api_keys` show that a connection without tenant context sees nothing
and cannot cross tenants. The asymmetry only means "the owner can read the
table", and the owner already can — it is the role that provisions everything.

**What would make this wrong.** Any code path that connects as `adapt_owner`
and serves requests. There is none; migrations and tenant provisioning are the
only owner connections, and `tests/pg.py` documents that split.

**Rejected alternative.** Dropping `FORCE` from `learners`/`tenants` to "make
things consistent" would remove the property increment 2 exists to prove.

---

## 3. API keys: HMAC-SHA256 with a pepper, not bcrypt

**Decision.** `hash = HMAC-SHA256(pepper, full_key)`, stored as `bytea`; the
prefix (`ak_live_abcd`, first 12 characters) is stored separately, unique and
indexed.

**Why not bcrypt/argon2.** A password hash is deliberately slow and salted per
row, which is right for low-entropy human secrets and wrong here. A key is 32
characters of base32 from `secrets.token_bytes` — 160 bits — so an offline
attacker gains nothing from a fast hash. A per-row salt would also destroy the
one property the lookup needs: the ability to find the row from the presented
key without a table scan. The pepper keeps a database dump useless on its own,
because the pepper is in configuration, not in the database.

**Constant time.** `hmac.compare_digest` is used for the comparison, and an
unknown prefix is compared against a fixed dummy hash so that "no such prefix"
and "wrong secret" take the same path. Measured over 1000 samples: median gap
0.104 ms, p95 gap 0.190 ms, against a 10 ms budget.

**Shown once.** `full_key` leaves the system exactly once, in the response to
the issuing call. Nothing reads it back because nothing can: it is not stored.

---

## 4. One `unauthorized` for every authentication failure

**Decision.** Missing header, malformed key, unknown prefix, wrong secret,
revoked key, and wrong environment all return `401 unauthorized` with the same
message and the same shape. Failures never say which one happened.

**Why.** Each distinct failure is an oracle. "This prefix exists but the secret
is wrong" tells an attacker their guess at the prefix was right; "this key is
revoked" tells them the key was real. `tests/test_api_keys.py` compares the
bodies byte for byte (modulo `request_id`) and their timings, and
`test_live_key_is_rejected_by_a_test_endpoint` pins the environment case.

**Scope failures are different on purpose.** A valid key lacking `admin` gets
`403 not_entitled` with `tier_required` in the message: the caller is
authenticated, the action is not permitted, and INTEGRATION.md §6 says so. That
is not an oracle, because it reveals only what the caller's own key may do.

---

## 5. `last_used_at` is written by the request, not by the resolver

**Decision.** The resolver is read-only. After a successful authentication the
dependency runs one `UPDATE ... WHERE last_used_at IS NULL OR last_used_at < now() - interval`.

**Why.** The debounce has to be atomic to be a bound. Doing the check in Python
would allow a burst of concurrent requests to all decide to write; the `WHERE`
clause makes the database decide, once, so the write amplification is at most
one row update per key per minute regardless of request rate
(`test_last_used_at_write_amplification_is_bounded` runs 40 authenticated
requests and observes one write).

**Why outside the request's transaction.** The write is best-effort telemetry.
Doing it inside the request transaction would let a failed touch abort a
request that had already authenticated, and would hold the row lock for the
duration of the handler. It is wrapped in `contextlib.suppress` for the same
reason.

---

## 6. Failed authentication is rate limited in memory, per process

**Decision.** `FailureLimiter` keeps a sliding window of failures per client IP
in the process, defaulting to 20 failures per 60 seconds, and answers
`429 rate_limited` (documented in INTEGRATION.md §6, `retryable: true`) with a
`Retry-After` header.

**Why not Redis.** Redis is not reachable in this environment (KNOWN_GAPS §3),
and a limiter that cannot be exercised is not a limiter. The in-memory version
is honest about what it is: a brake on credential guessing per process.

**What it is not.** It is not a shared quota, and with N replicas the effective
budget is N × 20 per IP. A shared limiter is the right answer when the credit
ledger arrives and Redis is real; this entry is the reminder that the current
behaviour is per-process.

**Why it counts every failure the same way.** Otherwise the limiter becomes the
oracle the uniform 401 avoids: a blocked IP would learn which guesses were
"closer".

---

## 7. The router enforces auth, the database enforces tenancy

**Decision.** No route filters by `tenant_id`. Routes open a connection with
`tenant_connection`, which is `tenant_transaction` for the authenticated
tenant, and query as if the database held only that tenant's rows.

**Why.** A `WHERE tenant_id = :id` in a handler is a rule that must be
remembered at every call site; a policy is a rule that cannot be forgotten.
Increment 2 proved the policy with sabotage tests; `tests/test_auth_http.py`
proves the wiring end-to-end, including that a learner created with another
tenant's id in the request body still lands in the caller's tenant.

**The one exception, stated.** `adapt_resolve_api_key` (§1) reads `api_keys`
without tenant context, by prefix, and returns one row. It is not a route and
returns no learner data.
