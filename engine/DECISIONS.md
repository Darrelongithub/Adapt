# Decisions

Every entry records a decision that a reader could reasonably have made
differently, the reason it was made, and what would make it wrong. Entries are
numbered; code and tests reference these numbers where the code is the
enforcement mechanism.

---

## 1. `adapt_app` holds no privilege on `api_keys`; five functions are the only path

**Decision.** `REVOKE ALL ON api_keys FROM adapt_app` (migration 0004). Not
SELECT, not INSERT, not UPDATE, not DELETE. Every access is one of five
`SECURITY DEFINER` functions owned by `adapt_owner`, each granted to
`adapt_app` and to nobody else, each with
`SET search_path = pg_catalog, public` pinned and `PUBLIC` revoked:

| Function | What it does |
|---|---|
| `adapt_resolve_api_key(text)` | read one row by exact prefix (`STABLE`, no writes) |
| `adapt_touch_api_key(uuid, int)` | debounced `last_used_at` write, `WHERE`-guarded |
| `adapt_issue_api_key(...)` | insert, enforcing the mint rules (§4) |
| `adapt_revoke_api_key(uuid, uuid)` | set `revoked_at`, tenant-addressed |
| `adapt_note_auth_failure(...)` | one tenant-less `audit_log` row, nothing else |

**Why.** RLS keeps *tenants* apart; a key table also has to keep *key material*
from everyone. Increment 3 gave the app role `SELECT, INSERT, UPDATE` and
leaned on RLS for that job, which was the wrong boundary — a `SELECT` on
`api_keys` is a SELECT on every hash of every tenant the policy happens to
allow. With no table privilege at all, a hash is unreachable from a request
connection even if a policy is later mis-written.

**Why resolution has to cross tenants at all.** Authentication happens before
the tenant is known: the key *is* the evidence of which tenant is calling, so
the lookup cannot run under the tenant context it is establishing.
`adapt_resolve_api_key` is therefore the one place in the system that reads
across tenants. It takes the prefix only — never the key — matches with
equality on a unique indexed column (no `LIKE`, no prefix range, so it cannot
be walked), returns the hash rather than anything replayable, and is
`STABLE`. A second cross-tenant read would need its own entry here and its own
grant review.

**Enforced by** `test_app_role_holds_no_privilege_on_api_keys` (all five
privileges, `has_table_privilege`), `test_app_role_keeps_execute_on_the_key_functions`,
`test_resolver_is_not_executable_by_public`,
`test_resolver_matches_the_prefix_exactly` and
`test_nothing_but_the_key_functions_touches_api_keys_as_adapt_app`.

**What would make this wrong.** Granting the app role anything on the table
"temporarily". The test fails in the same commit, which is the point.

---

## 2. `api_keys` keeps RLS **enabled, not forced** — as defence in depth, not as the boundary

**Decision.** `tenants`, `learners`, `audit_log` and the four credit tables are
`ENABLE` + `FORCE`. `api_keys` is `ENABLE` only, with the same tenant policy.

**Why.** `FORCE` subjects the table owner to the policy. `adapt_resolve_api_key`
is owned by `adapt_owner`, and under `FORCE` with no `adapt.tenant_id` set it
returns **zero rows** — measured on this schema, not assumed:

| `api_keys` RLS | rows the definer sees |
|---|---|
| `ENABLE` only | 1 |
| `ENABLE` + `FORCE` | 0 |

Forced RLS and a key-before-tenant resolver are mutually exclusive for this
table. The resolution was not to weaken RLS but to stop relying on it here:
the policy stays for a future grant, while the actual boundary is §1's grants.
With no app-role privilege, the policy is belt-and-braces.

**What would make this wrong.** Re-granting a privilege and treating the policy
as sufficient. `audit_system_insert`/`audit_system_read` (which are role-targeted
`TO adapt_owner`, and permissive) are the only other policies on any table, and
they exist because a failed authentication has no tenant to name (§9).

---

## 3. Keys are HMAC-SHA256 with a pepper, never retrieved, and always expire

**Decision.** Shape `ak_live_<32 base32>` / `ak_test_<32 base32>`; stored as the
first 12 characters (the prefix, unique and indexed) plus
`HMAC-SHA256(pepper, full_key)` as `bytea`. `expires_at` is `NOT NULL`,
defaulting to 90 days, and an expired key fails as `unauthorized`.

**Why not bcrypt.** A password hash is deliberately slow and salted per row,
which is right for low-entropy human secrets. A key is 160 bits from
`secrets.token_bytes`; a fast hash costs an offline attacker nothing, and a
per-row salt would destroy the one property the lookup needs — finding the row
from the presented key without a table scan. The pepper lives in configuration,
so a database dump is useless without it.

**Shown once.** `full_key` leaves the system exactly once, in the issue
response. Nothing reads it back because nothing can: it is not stored.

**Why expiry is not optional.** A key with no end date is a credential that
outlives the person who created it, the integration that used it and often the
company that bought it. 90 days is short enough that a forgotten key dies on
its own, long enough that nobody is forced into a rotation scramble. Expiry is
not an oracle: `test_an_expired_key_fails_exactly_like_a_wrong_one` compares
the bodies and statuses of an expired key and an unknown one.

**Rotation is issue-new-then-revoke-old** — there is no in-place re-key, and
no scheduled rotation job yet (KNOWN_GAPS §7).

---

## 4. The mint rules live in the database, and an admin key can never mint admin

**Decision.** `adapt_issue_api_key` refuses to create a key unless: the issuer
is an unexpired, unrevoked `admin` key **of the same tenant**; every requested
scope is already held by the issuer; and `admin` is not among the requested
scopes. The only other way to mint is `p_issuer_key_id IS NULL`, and that path
checks `session_user = 'adapt_owner'`.

**Why in SQL rather than in the handler.** A rule enforced in one place cannot
be forgotten by the second caller. The handler translates the function's
SQLSTATE into `403 not_entitled`; it does not re-derive the rule from the
caller's scopes, because two implementations of "subset" eventually disagree.

**Why never `admin`.** Admin is the scope that can mint keys. If an admin key
could mint another admin key, scope escalation would be one call away from any
compromised admin key, and revocation — the thing an incident depends on —
would be a race against keys nobody knew existed. Admin keys are created at
onboarding, by provisioning, or in the console with a human in the loop.

**Enforced by** `test_an_admin_key_may_mint_a_subset_of_itself`,
`test_an_admin_key_may_never_mint_admin`,
`test_an_admin_key_may_not_mint_a_scope_it_lacks`,
`test_the_provisioning_path_is_closed_to_the_app_role`.

---

## 5. `last_used_at` is written off the request path, debounced in SQL

**Decision.** A request notes the key id in an in-process set and returns. A
background loop (`auth/touch.py`, 15 s by default) drains the set and calls
`adapt_touch_api_key` per id; that function's `WHERE` clause refuses to write
again within 60 s.

**Why not on the request path.** The first version did the `UPDATE` inline, in
its own transaction. That costs a second connection acquisition on every
authenticated request — the most expensive thing a hot path can do — to record
telemetry nobody reads synchronously.

**Why the debounce is still in SQL.** The in-process set decides *when the
flush runs*; it cannot bound writes across processes or across two flushers.
The `WHERE last_used_at < now() - interval` is what makes "at most once per
key per minute" true under concurrency. Two mechanisms, two jobs.

**What it is not.** `last_used_at` can lag by the flush interval (15 s) or by
the debounce (60 s). It answers "was this key in use", not "when exactly". A
console cannot use it to attribute a specific request; `request_id` logs do
that.

---

## 6. One `unauthorized` for every failure, and the limiter cannot touch a valid key

**Decision.** Missing header, malformed key, unknown prefix, wrong secret,
revoked key, expired key and wrong environment all return `401 unauthorized`
with the same message and shape. Failures are counted **after** validation,
per client address (hashed, §8), and only a *failed* authentication can receive
`429 rate_limited`.

**Why one failure.** Each distinct failure is an oracle: "this prefix exists but
the secret is wrong" tells an attacker the prefix was right; "this key is
revoked" tells them the key was real; "this key expired yesterday" tells them
how close they are. `test_failure_body_is_identical_across_failure_modes` and
the 1000-sample timing test
(`test_unknown_prefix_and_wrong_secret_are_time_equivalent`, measured median
gap 0.104 ms) pin both halves.

**Why validate first, count after.** A shared egress address — a school, an
office NAT, a CI runner — carries many tenants' traffic. If the block were
checked first, one tenant's bad key would lock out every other tenant behind
that address. The limiter may only ever change the answer to a request that
already failed.

**Why scope failures are different.** A valid key that lacks `admin` gets
`403 not_entitled` with `tier_required` in the message (INTEGRATION.md §6):
that reveals only what the caller's own key may do, and the caller already
knows it.

**Known limit.** Per process, so N replicas give an attacker N× the budget
(KNOWN_GAPS §6). Fail-closed on a shared address is the price of not testing
`X-Forwarded-For`, which is spoofable without a trusted proxy.

---

## 7. Failed authentication is audited with a bound the attacker does not control

**Decision.** Every failed authentication writes an `audit_log` row with
`tenant_id IS NULL`, `action = 'auth_failed'`, and metadata holding
`{ip_hash, window_start, count, aggregated}` — never the key, the prefix or the
address. Up to ten rows per `(ip_hash, minute)`; the eleventh writes **one**
aggregate row carrying the count, and the rest of that minute is silent.

**Why not simply one row per failure.** The audit trail is on the
unauthenticated path, so an attacker chooses its write rate. Unbounded, the
audit log becomes the denial of service, and the table that was supposed to
prove what happened becomes the thing that stopped the service from working.
Eleven rows per source per minute is a rate no attacker controls.

**Why a NULL-tenant row needs its own policy.** `audit_log` is `FORCE`d, and
`tenant_isolation` compares against `adapt.tenant_id` — a failed
authentication has no tenant to compare. Two role-targeted permissive policies
(`audit_system_insert`, `audit_system_read`, both `TO adapt_owner`) allow the
owner to write and read exactly those rows and nothing else. The app role
cannot write a tenant-less row even directly, which
`test_app_role_cannot_write_a_tenant_less_audit_row` asserts. The tenant sees
its own audit rows; the owner sees the system ones (PRIVACY.md §4: the audit
trail is visible to the tenant, and system rows are not a tenant's data).

**Why the address is hashed.** An IPv4 address is personal data, and
`audit_log.metadata` carries no PII. The hash is keyed with the same pepper as
key hashes, so it cannot be reversed by enumerating the 4 billion addresses
without the secret.

**Issue and revoke are audited too**, as tenant-scoped rows written by the app
role under RLS: `actor = key:<issuer id>`, `target = <affected key id>`, never
a prefix or a key.

---

## 8. Credits: grants, an append-only ledger, and a balance that is a query

**Decision.** Three tables per `METERING.md` §4 plus `credit_reservation_draws`:

* `credit_grants` — a pile of credits with an identity and, usually, an
  expiry. `remaining` is what is *free*.
* `credit_ledger` — append-only (`REVOKE UPDATE, DELETE` **and** a trigger that
  refuses both for every role, owner included). Deltas record **consumption**,
  not motion: `reserve` and `release` write `delta = 0`, `settle` writes
  `-amount`.
* `credit_reservations` — a hold, with a state machine
  (`held → settled | released`, one way, guarded in SQL).
* `credit_reservation_draws` — which grant each part of a hold came from. Not
  in the spec, and required: without it a release cannot put credits back where
  they came from, and a drawdown order nobody recorded is a drawdown order
  nobody tested.

**The invariant.** `granted = available + held + consumed`, where
`available = sum(remaining)` over live grants, `held` is the sum of open
reservations and `consumed = -sum(delta)`. Every test that moves money asserts
it.

**Drawdown order: expiring-first, then oldest granted.** The credits that are
about to evaporate are spent before the ones that never expire — the order that
protects the customer's money. Expressed once, in the `ORDER BY` of the lock
query, and asserted grant by grant in
`test_drawdown_is_expiring_first_then_purchased_oldest_first`.

**Concurrency is a lock, not a check.** Reservations take
`SELECT … FOR UPDATE` on the tenant's live grants in drawdown order, so they
serialise; 100 concurrent reservations against 10 credits yield exactly ten
successes and a balance of 0. The `remaining >= 0` CHECK is the last line, not
the mechanism.

**Idempotency is a constraint.** `unique (tenant_id, idempotency_key)` decides
replays. Each operation inserts its ledger row **first**, so a concurrent
duplicate blocks on the index, loses, and retries into the replay path rather
than racing the drawdown. A replay returns the original body — the first call's
reported amount, not a fresh balance.

**Zero-delta rows are not decoration.** They are how an idempotency key is
bound to an operation that consumed nothing, which is what makes a replayed
reserve or release free instead of merely harmless.

---

## 9. The router enforces auth; the database enforces tenancy

**Decision.** No route filters by `tenant_id`. Routes open a connection with
`tenant_connection`, i.e. `tenant_transaction` for the authenticated tenant,
and query as though the database held only that tenant's rows.

**Why.** A `WHERE tenant_id = :id` is a rule somebody must remember at every
call site; a policy is a rule that cannot be forgotten. Increment 2 proved the
policy with sabotage tests; `tests/test_auth_http.py` and
`tests/test_credits.py` prove the wiring, including that a learner created with
another tenant's id in the request body still lands in the caller's tenant, and
that another tenant's reservation id is a `404`.

**The stated exceptions.** The five key functions (§1) and, for the credits
path, nothing: credit tables are ordinary tenant-scoped tables with grants.
`adapt_resolve_api_key` is the only read without tenant context.
