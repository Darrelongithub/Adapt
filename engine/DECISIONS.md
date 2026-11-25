# Decisions

Every entry records a decision that a reader could reasonably have made
differently, the reason it was made, and what would make it wrong. Entries are
numbered; code and tests reference these numbers where the code is the
enforcement mechanism.

---

## 1. `adapt_app` holds no privilege on `api_keys`; six functions are the only path

**Decision.** `REVOKE ALL ON api_keys FROM adapt_app` (migration 0004). Not
SELECT, not INSERT, not UPDATE, not DELETE. Every access is one of six
`SECURITY DEFINER` functions owned by `adapt_owner`, each granted to
`adapt_app` and to nobody else, each with
`SET search_path = pg_catalog, public` pinned and `PUBLIC` revoked:

| Function | What it does |
|---|---|
| `adapt_resolve_api_key(text, bytea)` | compare prefix and digest, return no hash (increment 4, §11) |
| `adapt_touch_api_key(uuid, int)` | debounced `last_used_at` write, `WHERE`-guarded |
| `adapt_issue_api_key(...)` | insert, enforcing the mint rules (§4) |
| `adapt_revoke_api_key(uuid, uuid)` | set `revoked_at`, tenant-addressed |
| `adapt_set_key_expiry(uuid, uuid, timestamptz)` | shorten/extend an expiry, past refused, audited (§10) |
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

**The invariant is four terms, not three.** `granted = available + held +
consumed + forfeited`, where `available = sum(remaining)` over live grants,
`held` is the sum of open reservations, `consumed = -sum(delta WHERE
entry_type = 'settle')`, and `forfeited = sum(remaining)` over grants that have
expired. The fourth term is the correction that matters: an allowance that
lapses is credits the tenant was granted and never spent, and a three-term
identity reports that as "gone" — or, worse, silently widens `available`. It is
derived on read from the expiry timestamps, so no sweeper has to run for the
books to balance; `test_invariant_holds_after_an_allowance_expires` asserts it
after an allowance lapses *and* after one lapses while a reservation still
holds part of it. Every test that moves money asserts the four-term identity.

**`entry_type`, not `reason`.** Every ledger row names its operation —
`grant`, `reserve`, `release`, `settle`, `expire` — and consumption is read
from `entry_type = 'settle'` only, never from `sum(delta)`. The distinction is
the whole point: `reserve` and `release` write `delta = 0` because they move
nothing, so an unfiltered `sum(delta)` happens to equal consumption today and
would stop equalling it the moment anybody recorded movement. The rule is
enforced the way the codebase enforces things it cares about: a
`security_invoker` view `credit_consumption` is the only sanctioned
aggregation, and `test_consumption_is_never_computed_from_an_unfiltered_sum`
scans `src/` for any `sum(delta)` without the filter.

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


---

## 10. The two CHECK constraints that left, and what replaced each

`expiry_after_creation` and `expiry_after_grant` were dropped in increment 4.
A constraint that was right when written can become the wrong tool; what is not
acceptable is dropping it and trusting a comment.

**`expiry_after_creation` (`expires_at > created_at`) — replaced by
`adapt_set_key_expiry`.** The intent was "a key cannot be born expired or
extend itself". The constraint did not hold that intent under `UPDATE`: it
checked the new row against `created_at`, not the old row, so a shortened
expiry and an extended one were indistinguishable to it. Expiry now changes
through one function, which refuses a timestamp in the past (`22023` →
`422 payload_invalid`), refuses a `NULL`, touches only the caller's tenant, and
writes an `api_key_expiry_set` audit row naming the actor and the target key.
The enforcement moved from "the arithmetic looked right" to "there is exactly
one door, and it is audited". `adapt_app` holds no `UPDATE` on `api_keys`
(`test_shortening_an_expiry_is_a_function_not_a_raw_update`), so the door is
the only way in, not merely the preferred one.

**`expiry_after_grant` (`expires_at > granted_at`) — replaced by
`credit_grants_purchased_never_expires` plus the 90-day default.** The original
constraint allowed a purchased pack to carry an expiry, which is the failure
the business cares about: money that evaporates. The pair of CHECKs that
replaced it states the two commercial invariants directly —
`kind <> 'purchased' OR expires_at IS NULL` and `kind <> 'included' OR
expires_at IS NOT NULL` — so "purchased credits never expire" and "included
credits always do" are facts about the schema rather than conventions about the
grant path. Both directions are tested, including the case where a test's own
blanket `UPDATE` tried to expire a purchased pack and was refused.

---

## 11. No hash leaves the database

**Decision.** `adapt_resolve_api_key(p_prefix text, p_hash bytea)` takes the
digest *in* and returns `key_id, tenant_id, scopes, revoked_at,
expires_at` — no hash, no prefix, no row. The Python-side comparison is gone:
the database compares, and the runtime never holds a stored digest next to the
pepper that makes it useful.

**Why it was wrong before.** The old resolver returned the stored hash for the
runtime to compare. That is a small thing with a large consequence: any read of
process memory, any traceback, any careless `repr` in a log line yields a value
that, combined with the pepper (also in the process), authenticates as that
key. Comparing inside the database means the only thing that ever crosses the
boundary is a yes/no.

**How it is held.** Two assertions, both against the catalogue rather than
against intent: `test_no_definer_function_returns_a_hash` reads
`pg_get_function_result` for every function the owner owns and fails if the
signature mentions `hash`; `test_the_resolver_takes_the_digest_as_an_argument`
pins the argument list and the returned columns. The red step was performed —
the resolver was edited to return `key_hash` again and both tests failed with
`these functions return a hash: ['adapt_resolve_api_key']` — before the change
was restored.

---

## 12. Encryption at rest: envelope, keyring, and what is deliberately not encrypted

**Decision.** Personal-data columns are encrypted with AES-GCM under a
per-tenant data key. The stored form is `v1:<dkv>:<nonce>:<ct>`, where `dkv` is
a **data key version** (`dk1`), not a master key id. The data key is created
when the tenant is provisioned, wrapped by the master key named on its row
(`ADAPT_MASTER_KEY`, or that plus `ADAPT_MASTER_KEY_PREVIOUS`), and kept in
`tenant_data_keys(tenant_id, kid, dkv, wrapped)`.

Two AADs, because the two things being protected are not the same kind of
object:

* a **wrapped data key** — `adapt/tenant/v1:{tenant_id}`. It belongs to a
  tenant.
* a **value** — `adapt/v1:{tenant_id}:{learner_row_id}:{column}`. It belongs to
  one cell of one row.

The row id is the internal uuid, not the host's `learner_id`, which the host may
change. With AAD of the tenant alone, a ciphertext swapped between two learners
of one tenant, or between two columns of one row, decrypted cleanly into wrong
data — silently, in the fields the product makes claims about. All three swaps
raise now (`tests/test_crypto.py`), and the red step for that claim was
performed: reverting `value_aad` to tenant-only turns both same-tenant tests
red.

**Why the header carries no master key id.** The first implementation wrote
`v1:<kid>:...` where `kid` was the master key that wrapped the data key at
*write* time. Re-wrapping does not rewrite rows, so every row stayed pinned to
the key it was born under: the retired key could never be dropped, startup had
to keep accepting it, and the kid catalogue had to grow forever. Rotation worked
in one direction — forward, never to completion. Putting a data key version in
field 2 and the master kid on the wrapped-key row (which re-wrap updates) is
what makes *retirement* possible, and retirement is the only thing rotation is
for. The acceptance test is the one the old design failed: write under k1,
re-wrap everything, delete k1 from the keyring, restart, read the old row
(`test_a_master_key_can_be_retired_after_rewrap`).

**Encrypted:** `learners.profile`, `learners.date_of_birth` — the two columns
that are personal data and are never queried by value. **Not encrypted:**
`tenant_id`, `learner_id`. That is a lookup decision, not an anonymity claim: a
unique constraint, an index and an RLS predicate all read those columns, and a
pseudonymous identifier is still personal data under every regime we would be
held to. Encrypting them would buy a false sense of anonymity and cost every
index. What the design buys instead is that a database dump, a backup or a
stolen replica shows no profile and no date of birth.

**Who may write key material.** `adapt_app` holds `SELECT` on
`tenant_data_keys` and `data_key_ids` and nothing else: a role that serves
requests must not be able to register a master key id that the startup check
then trusts. Creation is provisioning (`scripts/provision_tenant.py`), re-wrap is
rotation (`scripts/rotate_master_key.py`), both as the owner. `data_key_ids` is a
derived index of "kids referenced by a data key row", pruned when its last row
moves away; without pruning there is no retirement (KNOWN_GAPS §12, §15).

**`date_of_birth` changed type, and the profile allowlist left the database.**
A `date` column is plaintext by definition, and a CHECK that enforces
`profile IN ('adult_general', 'cognitive_support')` has to read the column — so
it cannot coexist with encrypting it. `date_of_birth` is now `text` holding an
envelope, and both columns carry a CHECK that the value *is* an envelope. The
allowlist moved to `db/schema.PROFILES`, checked before encryption. The honest
trade-off: the database no longer constrains the profile vocabulary; the API
does, and `test_the_profile_column_stores_an_envelope_not_a_literal` shows the
column refusing plaintext. No deployment held learner rows at this revision, so
the type change discarded nothing — and the downgrade says so by dropping the
column rather than pretending to restore dates.


---

## 13. What "rotation" means operationally

**Decision.** A master-key rotation is four owner-run steps, and the repository
contains the command for each:

| Step | Command | What it touches |
|---|---|---|
| 1. look | `rotate_master_key.py status` | nothing; prints every kid and how many data key rows name it |
| 2. re-wrap | `rotate_master_key.py rewrap` | `tenant_data_keys.kid`, `.wrapped`, `.rotated_at`, and `data_key_ids` — **no learner row, no ciphertext** |
| 3. ask | `rotate_master_key.py preflight k1` | nothing; answers "how many data key rows still name k1?", which must be 0 |
| 4. retire | `rotate_master_key.py retire k1`, then drop `ADAPT_MASTER_KEY_PREVIOUS` and restart | the environment, and the keyring |

The changeover is served by `ADAPT_MASTER_KEY_PREVIOUS` / `_PREVIOUS_ID`: both
keys are in the keyring while values written under the old one are still being
read, and the keyring only ever *unwraps* with the old key — new wraps use the
current one.

**When a master key may be retired: when no data key row references it.** Not
"when no value references it" — values no longer carry a master key id at all
(§12). The preflight query is the authority, and `retire` refuses while the
answer is non-zero (`assert_retirable`). Deleting a key early is not a subtle
failure: startup refuses to boot and names the kids it is missing.

**What rotation does not do.** It does not rotate *data* keys: one data key per
tenant, no version history, so `dkv` is always `dk1` today (KNOWN_GAPS §14).
Field 2 exists for the day that changes, and a row naming a version the tenant
does not hold is refused rather than guessed. It also does not re-encrypt
anything, which is why it is fast, boring, and safe to rehearse.

**Rejected alternative:** rotating by re-encrypting every row with a new master
key. It rewrites every personal-data column (a bulk operation over the data the
product is most careful about), takes an outage or a dual-write path, and turns
a compromise into a race against a rewrite. Wrapping keys once is the standard
shape for the same reason.

## 14. One error code for adults-only, and why the contract guard never patches the doc

**Decision.** A date of birth implying a minor is refused at creation with
`422 minor_not_supported`, not `payload_invalid`.

**Why a distinct code.** `payload_invalid` means "fix the request"; a minor is
not a malformed request, it is a product boundary (`NOTES.md`: adults only,
reopening needs an ADR and legal review). An integrator that cannot tell those
apart will rewrite the payload forever or file a bug. A distinct code also makes
the boundary measurable: counting `minor_not_supported` tells us whether a host
has misunderstood its audience.

**Why the doc owner added the row, not us.** `docs/v2/INTEGRATION.md` §6 is the
authoritative table, and the rule is that every emitted code appears there with
no whitelist (KNOWN_GAPS §5). The first version of this increment shipped the row
as a diff and had `check_openapi.py` apply `docs-patches/*.diff` before
comparing — which was wrong, and the document's owner said so: a guard that
patches the table it checks can be satisfied by the code it is supposed to
police (write the patch, amend the table, rule (c) passes). The table would
follow the implementation instead of the implementation following the table.
The row is now in §6 because the document's owner added it (docs commit
`855d74a`), and the guard reads the document **as written**: no patch step, no
`docs_with_patches.py`, no fallback that quietly edits what it is grading. A new
code means a reviewed doc change in the same change set. `docs-patches/` stays as
a review artefact — the change the code needs, as a diff, applied by a human.

The guard proves this about itself: `test_the_guard_never_patches_the_contract`
fails if a patch step reappears, and
`test_entrypoint_fails_when_the_age_gate_code_leaves_the_doc` shows that deleting
the row from a copy turns the guard red — which is what would have happened had
we shipped the code first and the document never.

**Where the check runs.** In the route, before the write, against the clock the
app was given: the column is ciphertext (§12) and a CHECK cannot read it. The
pure part (`age_on`) has boundary tests — a birthday that falls today is a
birthday, not a year — and the route test asserts that nothing was written when
the gate refuses.

## 15. The contract is a file in this tree, and the guard fails closed without it

**Decision.** `check_openapi.py` resolves `docs/v2/INTEGRATION.md` in the
working tree and nowhere else. `--doc PATH` and `ADAPT_INTEGRATION_DOC` are
explicit escape hatches for a document under review; if neither is given and the
in-tree file is absent or empty, the guard exits 2 naming the path. There is no
`FALLBACK_DOC_REFS`, no `ADAPT_DOCS_REF`, no `git show`, and no branch name in
`check_openapi.py`, `scripts/ci.sh` or `.github/workflows/engine-ci.yml`.

**Why.** M1's premise is that the contract and the engine are in one tree. A
guard that can read the document from a branch silently decouples them: the tree
compiles against a document nobody checked out, so a reviewer diffing the branch
sees nothing, and branch cleanup turns a green build into a red one for a reason
that has nothing to do with the code. Failure mode, measured rather than
imagined: on a worktree at `b008b45` — M0's tree, which has no `docs/v2/` — the
old guard printed `table read from
origin/arena/cb97052d-adapt:docs/v2/INTEGRATION.md (17 codes)` / `no violations`
and exited **0**. It was checking the engine against a document that was not in
the tree it was building.

**What "fail closed" means here.** The tempting middle ground is to fall back to
the last ref that has the file and warn. That is the same bug with a log line:
the build still succeeds on a document outside the tree. Exit 2, name the path,
name the escape hatch, and let a human decide — that is the only version of this
that stays honest when the file goes missing for a real reason.

**Not patched, not fetched, not generated.** The guard reads the file's bytes and
parses the §6 table out of them (DECISIONS §14). `test_the_contract_is_resolved_
in_tree_and_read_verbatim` asserts byte equality with the file, and
`test_the_guard_knows_no_branch` fails if any of the strings above reappear.

## 16. Webhooks: one egress switch, five attempts, one message id, no payload at rest

**Decision.** Deliveries are recorded in the database and attempted by a
dispatcher, not sent from the request path. Five attempts with 1s / 4s / 16s /
64s between them, then `failed`. Every attempt carries the same `webhook-id`.
A duplicate enqueue returns the existing row and sends nothing. The delivery log
stores the event type, an opaque subject id, the attempt count and the last
status code — there is no column for a body.

**The egress contradiction, stated plainly.** `AGENT_M1.md` §0 carries "no
network egress outside `/v1`" from M0; §1 requires webhooks with five retries.
Both cannot be literally true. The resolution is one switch and one choke point:
`ADAPT_WEBHOOK_DELIVERY` defaults to `disabled`, in which case rows are still
written (so the log, the dedupe and the retry schedule are all testable and
observable) and nothing leaves the process. `enabled` constructs exactly one
`UrllibTransport`, an https-only POST with a 5s timeout that refuses redirects.
This is *not* a fourth exception to an invariant (the rule review asked about):
no invariant says "the engine never speaks to a customer endpoint" — the
invariant is that nothing calls a model provider or an unrelated host from the
request path, and this calls a URL the customer configured, from a worker, when
a human turns it on. I have flagged the wording conflict rather than quietly
picking a side.

**Why an outbox.** A webhook is a notification that something happened, and the
something is already committed. Sending inline makes the customer's uptime our
latency and turns a receiver outage into lost events. The row is the record; the
attempt is a consequence.

**Why five attempts and not "until it works".** An unbounded retry is a queue
with no bound and a delivery log nobody can read. Five attempts over 85 seconds
covers a deploy and a restart; a receiver down for longer than that has a
different problem, and the row says `failed` so a human can look.

**Why the same message id on every attempt.** Standard Webhooks puts it in a
header so the receiver can recognise a retry. A fresh id per attempt turns one
event into five at the far end, which is the same bug as double delivery with
more steps.

**Two layers of duplicate suppression, and both are tested.** The derived
message id (tenant, connection, type, dedupe key) plus the application check
returns the existing row; `UNIQUE (tenant_id, webhook_id)` is the last line. The
red-then-green shows what each is worth: with the app check removed the second
enqueue raises `UniqueViolation`; with the constraint dropped as well, one event
becomes `rows=2 same_id=True deliveries=2` — the same message delivered twice.

**Why no payload at rest.** A webhook is a doorbell. Storing the body would make
the delivery log a second copy of learner data with its own retention question,
its own access policy and its own breach surface. The schema has no column for
it, so this is a property rather than a habit.

## 17. `connection_paused` is raised, never inferred

**Decision.** A paused connection answers **403 `connection_paused`** on the
host-facing data routes and keeps answering `200` on `/v1/whoami`. A bare 403 is
`not_entitled` and nothing else; `connection_paused` is only reachable by raising
it explicitly.

**Why it is not `unauthorized`.** The key is fine — the customer paused the
connection themselves. Answering `401` sends an integrator to rotate a key that
works. Why it is not `not_entitled`: nothing needs to be bought. Why identity
still answers: telling a caller who it is, is not ingest.

**Where the check lives.** `auth.deps.ingest_connection`, used by the
host-facing data routes. Admin routes deliberately use the plain
`tenant_connection`, because the console has to be able to resume a connection
that is paused — a key blocked from fixing its own mistake is a support ticket.
A key with no connection row (the onboarding key) is never paused: it is not a
host app.

**Revoking is different.** `DELETE` sets `revoked` and revokes the key in the
same transaction, so the next request is a plain `401`. There is no
`connection_revoked` code, because there is no window in which it would be true.

## 18. Observe ingest: what a batch costs, and what a refusal means

Increment 2 adds `POST /v1/observe` and three tables behind it. The shape of the
handler follows from four rules the review named, and one consequence nobody
asked for but the load test produced.

### 18.1 A batch is one transaction, and the batch row goes in first

An accepted batch is all-or-nothing. Partial acceptance is silent data loss: a
host that receives `202` and a count of 500 believes 500 events are stored, and
a later report that 497 arrived is a bug report nobody can reproduce.

The order inside the transaction is: **idempotency lookup → validation →
event dedupe → quota → batch row → events**. The batch row precedes its events
because `learning_events.batch_id` is a foreign key — an event whose batch does
not exist could never be replayed from the idempotency record, and the FK is
the cheapest way to make that impossible. The response body is already known
before the events are inserted (counts, flags and quota are all computed from
the request), so the row is written once with the final response rather than
inserted empty and patched.

### 18.2 The original response is stored, not re-derived

"A duplicate batch returns the original response and writes nothing" only holds
if the original response still exists. `observe_batches.response` is a `jsonb`
column holding exactly the bytes returned, and a replay returns them verbatim —
same status `202`, same `batch_id`, same counts. Re-deriving would drift: quota
remaining changes as other batches land, and a replay a minute later would
report a different `remaining` for the same batch.

`request_hash` (SHA-256 of the raw body, before parsing) distinguishes a retry
from a client bug. Same key, different body → `409 conflict` with
`X-Adapt-Idempotency-Conflict: body`. Silently treating it as a retry would
accept the second body's events under the first body's identity.

### 18.3 Quota is counted at ingest, in the same statement that checks it

`event_quota_monthly` is enforced by one statement:

```sql
INSERT INTO connection_event_usage (tenant_id, connection_id, period, events)
SELECT …, :count FROM cap WHERE cap.quota >= :count
ON CONFLICT (connection_id, period) DO UPDATE SET events = u.events + :count
WHERE u.events + :count <= (SELECT quota FROM cap)
```

`SELECT` then `UPDATE` is a race, and the race lets a tenant exceed its quota by
exactly the number of concurrent batches. Conditional upsert has no such window.

Over quota is `402 quota_exhausted`, free, with the reset time in three places:
the message, an `X-Adapt-Resets-At` header, and — on success — `usage.resets_at`
so a host can warn before it hits the wall. The month is the **UTC calendar
month**, which is what `resets_at` says; nothing rolls the counter over, because
a new month is a new `period` and the old row is history.

**The loop does not break.** A 402 spends no credits, writes no rows, and does
not touch the connection's stored history: the same key still reads learners,
decisions and reports. That is the whole point of the review's fifth acceptance
point — a host that cannot buy more events must still be able to serve what it
has. `usage.resets_at` on every successful batch is what lets it back off
deliberately instead of discovering the wall by hitting it.

### 18.4 A paused connection: refused, not dropped, not stored

The review asked for a choice and a reason, so: **an event sent to a paused
connection is refused and stored nowhere.**

Refusal is the only option that is honest to both parties. "Dropped" means the
host loses data it believes it sent — the worst outcome, and invisible to the
sender. "Stored anyway" means the pause did not do what the console said it
does: a customer who pauses a connection to stop a runaway client would find the
rows still arriving. `403 connection_paused` is a *refusal*, and a refusal is
actionable: the host keeps the batch, resumes, and replays with the same
`Idempotency-Key`.

Which is why the refusal happens **before** the idempotency row is written and
before any quota is counted. A test drives exactly that: pause → 403 with zero
rows in all three tables → resume → the same key and body → `202`. If the
idempotency record had been written at pause time, the retry would have returned
a stored "success" for events that were never stored.

### 18.5 Unknown keys are rejected, and the error names them

`... event 1: keys: unknown field(s): learner_email`. Rejecting the batch rather
than the event is deliberate: a host that ships one bad field in a batch of 500
should learn immediately, not receive a `202` for 499 and a mystery.

The same rule applies to the envelope (`events`, optional `connection`), and to
each value's *shape* — `learner_id` goes through the same validator the learner
route uses, so an email address in the id field is refused at ingest rather than
stored and quarantined later.

### 18.6 `connection` in the body is optional, and must match the key

The key already identifies the connection, so the field is redundant — but
AGENT_M1 §2 shows it, and a host doing its own wiring will send it. It is
accepted, and if present must equal the connection the key belongs to; a
mismatch is `422 payload_invalid`. Accepting a foreign id would let one
connection's events be charged to another's quota.

### 18.7 Event duplicates are checked in code, because the schema cannot

Events carrying `(session_id, seq)` are identity-keyed, and a duplicate inside a
batch or already stored is counted (`duplicates`) and not inserted, and not
charged.

The check is a query, not a constraint, and the reason is structural: a
partitioned table's unique index must include the partition key, so
`UNIQUE (tenant_id, learner_id, session_id, seq)` is impossible across months.
`learning_events_dedupe_idx` makes the check an index probe; a test asserts the
behaviour. This is the one invariant in M1 enforced in code and not by the
schema — it is in KNOWN_GAPS, not in a comment nobody reads.

### 18.8 Validity flags are computed once, at ingest

Flags are stored on the row, never derived at read time from data that may be
gone. Two rules from MEASUREMENT §4 are visible in the implementation: nothing
is imputed (a flag is absented, not guessed) and nothing is dropped (a flagged
event is stored in full).

`tab_hidden` cannot be set, because the event allowlist has no visibility
channel. The flag exists in the column's CHECK and in the vocabulary, and it is
documented as unreachable rather than quietly removed (KNOWN_GAPS §2b).

The lookback is bounded — sixteen events per learner, joined laterally — not
because the flags need more, but because the unbudgeted version was the load
test's first finding: a window function partitioned by learner scanned every
event the learner had, so a 500-event batch cost a full sort once the table was
large.

### 18.9 The route is `async` only to read the body

`POST /v1/observe` is the only `async def` route in the engine, and it exists
because the raw bytes are needed for hashing. The work happens in
`run_in_threadpool`.

That is not a style choice. The first load test deadlocked at request four: a
blocking database call inside `async def` runs on the event loop, so a request
holding a row lock could not yield to the request waiting for that lock. Every
other route in this app is plain `def` and never had the problem.

### 18.10 A failed batch can be retried with the same key

Because nothing is written until validation passes, and everything after it is
in one transaction, a `413`, `422`, `402` or `409` leaves the key unused. A host
that splits an oversized batch and retries with the original key is fine; a host
that fixes a rejected field and retries is fine too.

### 18.11 Two documented corrections this increment adopted

* **`connection_unknown` (404)** now exists in INTEGRATION §6, so the connections
  API raises it instead of `route_unknown`. The route exists; the connection does
  not; the integrator can now tell that from a typo in the path.
* **§8's five event types** (`decision.created`, `consent.changed`,
  `quota.warning`, `learner.erased`, `instrument.not_renderable`) are accepted by
  `webhook_deliveries` from `0011` onward, and the retry schedule carries jitter
  (derived from `(delivery_id, attempt)`, not `random()`, so a schedule is
  reproducible while two deliveries still fan out).

## 19. L1 mastery: two numbers per learner, and the one the customer sees

### 19.1 The filter is stored, the filter is not served

`learner_mastery` keeps `(trials, correct, model_p)` and the served number is
computed from the first two. `model_p` is the BKT belief, advanced one
observation at a time; it is what increment 4 reads to choose the next item.
It is *not* what a customer is shown, because it saturates: with the documented
parameters (G=0.2, S=0.1) one correct answer is 4.5:1 evidence of mastery, so a
learner answering 55% correctly converges to p≈0.99 within forty trials
(`test_the_filter_saturates_which_is_why_it_is_not_the_served_value`). Serving it
measured RMSE 0.355 with 13.5% interval coverage — a confident number that is
wrong, which is worse than no number.

The served value is the posterior mean of a Beta over the learner's *observed*
success rate, shrunk toward the cohort prior by `w(n) = K²/(K+n)` with K=12, with
a 95% central interval from the same Beta. Point and interval come from one
distribution, so a value outside its own interval is impossible by construction
(`test_an_estimate_always_lies_inside_its_own_interval`).

### 19.2 No guess correction, and a cap instead

The obvious correction — `m = (rate − G)/(1 − G − S)` — was implemented first and
removed. Measured over 600 synthetic learners at 40 observations, mastery
0.1–0.9:

| estimator | clean generator RMSE / coverage | noisy generator RMSE / coverage |
|---|---|---|
| guess-corrected + shrunk | 0.1130 / 0.582 | 0.0952 / 0.743 |
| **observed rate + shrink + cap** | **0.0683 / 0.948** | 0.1102 / 0.753 |
| shrunk over filter `p` | 0.3087 / 0.183 | — |
| raw filter `p` | 0.3552 / 0.135 | — |

The correction *widens* error on honest learners, because dividing by
`1 − G − S` scales the sampling noise by 1.43 while the bias it removes is
`(1−m)G` — and a host's real items are not the lab items G was measured on. So
the served value is the observed rate, and the safety property lives in the cap
instead: if the posterior median does not clear `G + 0.10`, the value is held at
`G + 0.05`. "Your evidence is consistent with guessing" is not a reason to report
a number that says you have mastered it.

### 19.3 The gate has a generator, and the doc does not name it

With `p(correct) = mastery` ("clean"), the estimator passes the acceptance gate:
**RMSE 0.0683 ≤ 0.08, coverage 0.948 ≥ 0.90**. With the model's own response
function `p(correct) = m(1−S) + (1−m)G` ("noisy"), **no estimator** passes:
forty Bernoullis on a 70% signal have a standard error of 0.11, so 0.08 is below
the Cramér–Rao floor — the gate is unattainable there by any estimator, not by
this one. The clean number is the one the gate asks for; the noisy number
(0.1102 / 0.753) is asserted in the suite as well
(`test_the_noisy_generator_is_reported_not_hidden`) so it cannot be dropped from
a report. **Owed to the owner:** the vocabulary the acceptance criteria use does
not say which generator they mean; this is the ambiguity, resolved as "clean for
the gate, both for the report".

### 19.4 The guesser: G2, reported separately, with its edge measured

The honest number is not the RMSE. It is this: 200 seeded learners answering
correctly with probability 0.2 regardless of mastery, 60 trials each, served at
every horizon at or above the floor — **highest served value 0.537, breaches at or
above 0.70: zero**. It is a release blocker, it is independent of the calibration
numbers above, and it is the first thing to read in the increment report.

Three sabotages, each run against the real module, each restored:

| sabotage | what it removes | result |
|---|---|---|
| `EVIDENCE_FLOOR = 1` | the abstention floor | 2 red (`needs.trials`, the HTTP abstention) |
| `PRIOR_STRENGTH = 0` | shrinkage | red: 1 serve at 0.700; the luckiest-start construct reaches 0.923 |
| cap block deleted from `estimate()` | the guessing cap | red: the capped learner is served 0.307 instead of 0.250 |

**The guarantee's edge, stated rather than implied.** Shrinkage is what keeps a
guesser at 0.537, not the cap: the cap only trims a long-run guesser by about a
point. And the guarantee is statistical past ~ten straight correct answers. A
guesser whose first eight answers are right (2.6e-6) and who then happens to get
three of its next four right (≈8e-8 per learner) is served **0.743**. No
estimator that must also report a real master as a master can avoid that — eight
straight is the same evidence either way. What is bounded is the ordinary case,
and the crossover is pinned: the estimator reaches 0.7 at **eleven** straight
correct answers and 0.8 at sixteen
(`test_the_streak_length_the_estimator_believes`,
`test_where_the_guesser_guarantee_stops`).

The random property test is a weak detector on its own — a p=0.2 learner rarely
gets six of its first eight right, so removing shrinkage breaches it once in 200
learners. That is why the two adversarial constructs exist alongside it.

### 19.5 Abstention is a count, not a soft number

Below eight observations (`bkt.EVIDENCE_FLOOR`) the endpoint returns
`{"reason": "insufficient_evidence", "needs": {"trials": 8 − n}, "trials": n}` and
no `value` key at all. A consumer that receives a number will render the number;
"0.31 ± 0.4" reads as 31% on every dashboard, and the abstention is the only shape
that cannot be misread. The count is what makes it actionable.

### 19.6 Determinism, and golden files from this increment

Same history + same clock ⇒ identical bytes
(`test_the_same_history_serves_the_same_bytes`). Everything the estimator reads is
stored (`trials`, `correct`, the prior row); the fold sorts trials by
`(ts, seq, item_id)` so arrival order cannot change `model_p`; nothing in the path
consults the clock except `updated_at`. `tests/golden/mastery/*.json` pin three
sequences to four decimals — an ordinary 12-trial history, the luckiest 8-trial
start, and a guesser at the floor. **Regenerating a golden to make a red run green
is the failure these files exist to prevent**: a changed number is a changed
served value and needs a version bump plus a paragraph here.

### 19.7 Priors are tenant-scoped, versioned, and written by a job that can refuse

`skill_priors` is keyed `(tenant_id, skill, version)`. A refit on one tenant's
cohort must not change another tenant's numbers, and keeping `tenant_id` means
this increment adds no fourth exception to the RLS invariant. Version 1 of a skill
is the documented default, inserted on first use (a migration cannot write a row
for a skill nobody has observed); a refit writes version *n+1*, never an `UPDATE`.
`learner_mastery.prior_version` records what a learner's state was folded under,
so a served number can be explained by a row that still exists.

The refit (`mastery/refit.py`, run nightly by `scripts/refit_cohorts.py`) refuses
a cohort below 20 learners or 200 new observations and leaves the previous version
serving. A nightly job that always writes a version is a job that turns noise into
priors. The fit is a fixed 300-point grid with ties broken by grid order, so the
same cohort produces the same version.

**There is no HTTP refit endpoint.** AGENT_M1 §1.3 calls the refit nightly, not
callable, and an endpoint that runs 300 likelihood evaluations per request is a
denial-of-service with a username. It is an operator script, like the partition
job. That also means the "deep adds cross-cohort mastery modelling" tier promise in
ADR-002 §tiers is **not** a tier gate at M1 — see KNOWN_GAPS.

### 19.8 The fold runs inside the ingest transaction, and a 500 found the clamp

Mastery is folded from the *fresh* events of a batch, in the batch's transaction,
after the events are inserted. A duplicate is not evidence (a replay returns
before the insert; a deduped event never reaches the fold), and a rolled-back
batch cannot leave a counter ahead of its evidence.

The first version stored `model_p` unclamped, and a batch of 500 consecutive
correct answers turned it into exactly `1.0` — which the table refuses
(`CHECK (model_p > 0 AND model_p < 1)`), so a legitimate batch was a 500. The
filter belief is now clamped to `[1e-6, 1−1e-6]`: not a modelling choice, but the
difference between floating point and a probability. Found by
`test_five_hundred_events_is_allowed`, an increment-2 test, which is the argument
for keeping a full-size batch test around.

## 20. `decide` v1: one pure function, five honest abstentions, and a receipt

### 20.1 The split that makes the promises true

`decide/service.py` has two halves. `snapshot()` reads — mastery state, the last
three answers, the share of recent trials that tripped a validity flag, the
tenant's tier, the claim ids. `evaluate(Inputs, now, decision_id)` computes, with
no database, no clock and no randomness. Everything the increment promises
follows from that signature rather than from discipline:

* same history + same clock ⇒ **byte-identical body** (it is a function);
* **replay means something**, because a decision stores its `Inputs` and replay
  folds the snapshot rather than today's evidence (recomputing against live state
  would differ for the honest reason that the learner answered more items, and a
  test comparing those two would either fail constantly or prove nothing);
* the goldens compare a function, not an environment.

### 20.2 Five of the six abstention codes, and the one that is not reachable

`METRICS.md` §1.3 names six: `insufficient_evidence`, `low_confidence`,
`conflicting_signals`, `consent_withheld`, `data_quality`, `unsupported_request`.
Five have fixtures and trigger on real inputs. `consent_withheld` needs a consent
store, which does not exist, so it is named in the closed vocabulary and is
**unreachable** — recorded in KNOWN_GAPS rather than faked with a flag nobody can
set. `does_not_apply_when` is not decoration either: the worked-example claim says
it does not apply to experts, so a high-mastery learner with three consecutive
errors is served `conflicting_signals`, not the action whose evidence excludes
them.

Thresholds, all documented constants in `decide/constructs.py`: interval width
above **0.45** ⇒ `low_confidence`; validity-flag share above **0.30** over the
last 20 trials ⇒ `data_quality`; the success target for difficulty is **0.78**
(AGENT_M1 §4's 70-85% band).

### 20.3 Credits: reserve *after* deciding, and abstention is free

The brief said "reserved on serve, released on abstain". Holding a credit and
then releasing it would make an **abstention able to fail with 402** — the host
that most needs to be told to fix its instrumentation is exactly the one that may
be out of money — so the reservation happens only when there is something to
charge for, after the decision is computed. Nothing is held during an abstention,
so there is nothing to release. Tested both ways: at zero credits an abstention
returns 200 and writes no ledger row, while a serve returns `402 quota_exhausted`
and stores nothing.

### 20.4 Claims: gated at write time, seeded per tenant, no invented identifiers

`claims.construct` is a CHECK against the five constructs this build has evidence
for; `evidence_n >= evidence_floor` makes an under-evidenced claim **refused at
write time** (G3's "test the refusal, not the filter"); `does_not_apply_when` is
`NOT NULL` with a cardinality check, because a claim without a boundary is a
horoscope. All three are proved by attempting the insert and watching PostgreSQL
refuse it.

Two deviations worth naming. Claims are **tenant-scoped copies** of knowledge
that is conceptually global: a truly global table would be a third exception to
the RLS invariant, and duplicating five rows per tenant is cheaper than an
exception. And the seed set carries **no DOIs I did not verify** — the one in
`CLAIMS.md` (Sweller & Cooper 1985) is kept, the rest are author/year references.
A fabricated identifier looks checkable, which is worse than an absent one.

Seeding happens on first use inside the request transaction (a migration cannot
`INSERT`), so a tenant that has never run a job still gets cited advice. If a
claim is missing at serve time the decision **abstains** (`unsupported_request`,
`needs.claims`) instead of shipping an uncited recommendation.

### 20.5 The body is stored twice, and returned canonically

`decisions` holds the body it returned and the inputs it was computed from.
Replay recomputes from the inputs and raises a **500** on a mismatch — the
endpoint exists to be able to fail. It found a real bug: `jsonb` does not preserve
key order, so a body read back from the row serialised with different key order
than the same body from `evaluate`, and "byte-identical" was false in the only
sense a client can check. Both routes now emit sorted keys with fixed separators
(`api/decide.py::_body_response`).

### 20.6 The level object, and an inconsistency in §6B

`decide` serves the ADR-002 `level` object from `levels.resolve_level`, with
capability L1, validated L1 (G1, G2 and G3 all run in CI) and
`validation_ref: "gates/g1_g3@1"`. Mastery serves the same three axes flat, as
§6B spells them, from the same function — so the two surfaces cannot disagree
about the served level, only about where the words sit.

`INTEGRATION.md` §6B's example shows `validation_level: "L0"` together with
`served: "L1"`, which `min()` cannot produce. The code serves consistent values
(validated L1); the example is owed a correction.

### 20.7 The mastery response changed shape to match §6B

Increment 3 shipped `interval: [low, high]`, `interval_mass`, `trials` and
`estimator: "bkt_beta_v1"`. §6B now specifies `interval: {low, high, mass}`,
`n`, `estimator: "shrunk_rate"`, `prior: {source, strength}`, an explicit
`abstain` flag and the flat level axes. The published contract wins: the shape was
conformed, the estimator renamed (a documented version bump per §6B's own rule),
and all thirteen mastery goldens regenerated in the same commit. The goldens'
numbers did not move — only their shape — which is what makes this a rename rather
than a change of answer.

### 20.8 The measurement

200 seeded synthetic learners (mastery 0.1-0.9, 4-40 observations each), one
decision each, in-process over HTTP:

| Served | Abstained | Median interval width (served) | p50 | p95 |
|---|---|---|---|---|
| 167 / 200 (83.5%) | 33 (23 `insufficient_evidence`, 9 `data_quality`, 1 `conflicting_signals`) | **0.326** | 26.4 ms | 32.8 ms |

The abstention count is the product admitting it does not know, and the 0.326
median width is what a served number is allowed to look like. Both belong in the
report next to the latency (see also KNOWN_GAPS §23 on the `data_quality` share).

### 20.9 Red-then-green: the floor is load-bearing

Lowering `EVIDENCE_FLOOR` to 1 — the change that makes a 5-trial learner
servable — turns **six** tests red: `test_below_the_floor_the_answer_is_a_count_
not_a_number` (the abstention test), `test_the_floor_abstains_and_names_the_count_
that_would_change_it`, `test_golden_decisions_match` on
`05_abstain_insufficient_evidence`, `test_the_golden_set_covers_every_action_and_
reachable_reason`, and the two mastery golden tests. Restored: 9 passed. The
golden file is red in the same run as the behavioural test, which is the point of
pinning the body: the floor is asserted twice, once as behaviour and once as a
customer-visible value.

## 21. Increment 5: flags that must not fire on good performance, and money that is counted

### 21.1 `straight_line` is a property of the response, and `undeterminable` when there is none

The rule was "8+ identical *outcomes* in a row", where an outcome was `correct`.
That is a heuristic for inattention that fires hardest on the learners doing best:
eight right answers in a row is a run, so the flag was set, the uninformative
share crossed 0.30, and `decide` answered `data_quality` instead of teaching the
one learner who least needed to be told to slow down.

The rule is now 8+ identical **`response`** values on one skill (compared by
SHA-256 digest, so the text itself is still never stored), carried across batch
boundaries because a batch is a transport unit. When an event arrives without a
response, the honest answer to "was this straight-lining?" is *we cannot tell*:
the event is flagged `undeterminable`, which is a `context`-severity flag and is
**not** in `QUALITY_FLAGS`, so a host that does not send responses never moves a
decision toward abstention. Correctness is not read at all. `undeterminable` is
in the closed vocabulary via migration `0014`, because a flag the code can emit
and the CHECK does not accept is a 500 on the ingest path.

The 200-learner measurement of §20.8, re-run on the same seeded corpus:

| Served | Abstained | Median width (served) | p50 | p95 |
|---|---|---|---|---|
| 176 / 200 (88.0%) | 24 (23 `insufficient_evidence`, 1 `conflicting_signals`) | 0.323 | 29.6 ms | 38.3 ms |

The nine `data_quality` abstentions are gone and **nothing else moved** — the
other 23 `insufficient_evidence` and the single `conflicting_signals` are the
same learners as before. That is the cleanest possible form of the result: the
flag fired on exactly nine learners, all nine were being punished for good
performance, and removing the rule changed no other decision. p50 rose from
26.4 ms to 29.6 ms, which is the price of two ledger writes per served decision
(§21.5) and of the ordering fix in §21.4.

### 21.2 The audit: does any other flag fire on good performance?

Every flag in the vocabulary, what sets it, and whether it can be earned by doing
well — the question the increment asked, answered per flag rather than in general:

| Flag | Set by | Can good performance set it? | Severity | Gates serving? |
|---|---|---|---|---|
| `straight_line` | 8+ identical `response` values | Not any more. A learner can *choose* the same option eight times and be right eight times — that is now flagged, and that is the intended signal (identical responses), not correctness. | `uninformative` | yes, via the 0.30 share |
| `rapid_guess` | latency < 300 ms | **Yes, and it did.** Fixed — §21.3. | `uninformative` | yes, via the 0.30 share |
| `tab_hidden` | nothing can set it | n/a — unreachable (`KNOWN_GAPS` §2b) | `uninformative` | yes, when it can be set |
| `device_change` | device differs from the learner's last event | Yes, and it should: changing device is life, not failure. It cannot gate: `context`. | `context` | no |
| `assisted` | `assisted: true` (hint/prompt observed) | Yes — help-seeking is *good* learning. Published as data (`MEASUREMENT` §4): recorded, excluded only from independence metrics. It cannot gate: `scoring`. | `scoring` | no |
| `repeated_item` | the item was already served to this learner, or earlier in the batch | Yes, and the fault is ours or the host's scheduling, not the learner's. It cannot gate: `scoring`. | `scoring` | no |
| `undeterminable` | no `response` on the event | No — it is an instrument limitation, stated as one. `context`. | `context` | no |

The severity of every flag is published in `observe/flags.py::SEVERITY`, and
`QUALITY_FLAGS` in `decide/service.py` is *derived* from it rather than written
again, so a new flag cannot be added to the vocabulary and silently start gating
serving. A test asserts that the table covers the vocabulary exactly and that
`UNINFORMATIVE_FLAGS` is exactly `{rapid_guess, straight_line, tab_hidden}` — the
third is unreachable today, and it is `uninformative` because `MEASUREMENT` §4
calls a hidden tab a reduced-validity session, so the gate is already right for
the day a visibility channel exists.

### 21.3 `rapid_guess` did fire on good performance, and now does not

`MEASUREMENT` §4 defines the flag as "response < 300 ms **on a non-trivial
item**". The implementation read only the clock: any answer under 300 ms was
uninformative, so a fluent learner answering quickly and correctly filled the
0.30 share and got `data_quality` — the same failure as `straight_line`, one
flag over, and it was not caught because no measurement corpus carried latencies.

The flag now requires all three: under 300 ms, **not correct**, and an item that
is not known-trivial (`difficulty_band` whose high end is ≥ 0.85). Correctness is
used here only to *exonerate* a learner, never to accuse one; that is the
distinction the earlier rule got wrong in the other direction. A fast wrong
answer on a hard item is still a guess, which is what the flag is for.

The cost is stated: on an item with no `difficulty_band`, a fast *correct* answer
is no longer flagged even if the host would call it a guess. Losing a true
positive on a good answer is the trade this project makes deliberately — a false
positive here abstains on the strongest learner in the class.

### 21.4 The tie-break bug the metering corpus found

The corpus abstained for `conflicting_signals` a different number of times on
each run: 3, then 2, then 0, on the same 200 seeded learners. The cause was
ordering: `_recent_answers` and `_flagged_share` read `ORDER BY ts DESC, id DESC`,
and a client may send one timestamp for a whole batch (our fixtures do — every
event in a batch shares `ts`), while `id` is a random uuid. Which three answers
were "the learner's last three" was therefore a coin flip per tenant.

Every tail query is now ordered by `ts DESC, created_at DESC, seq DESC NULLS
LAST, id DESC` — server ingest time, then the client's own sequence, then the id.
The cost is bounded: the lateral in `observe/flags.py` has already filtered to one
learner, so the sort is over that learner's rows in one month partition, not over
the table. The corpus is pinned at 176 served / 24 abstained in
`tests/test_metering.py`, so the next nondeterminism of this kind is a red test
rather than a drifting number in a report.

### 21.5 Metering: what bills, what does not, and how a reader checks it

Per `AGENT_M1` §5 and `METERING` §3, proved by counting rows rather than by
asserting a flag:

| Path | Ledger rows | `credit_consumption` | `decisions` row |
|---|---|---|---|
| served decision | 2 (`reserve` binds the key, `settle` moves the credit) | +1 | yes |
| abstention | **0** | 0 | yes |
| 4xx / 422 / 404 / 403 / 429 | **0** | 0 | no |
| 5xx after the reservation | **0** — the transaction rolls back the hold | 0 | no |
| replay (same `Idempotency-Key`) | **0** | 0 | no new row |
| replay (`POST /v1/decisions/{id}/replay`) | **0** | 0 | no new row |
| read (`/v1/mastery*`, `/v1/credits/balance`) | **0** | 0 | no new row |
| test traffic (`X-Adapt-Test: 1`, or `ADAPT_ENV=staging`) | **0** | 0 | yes, `test_traffic = true` |

Four decisions about this that a reviewer should be able to disagree with:

1. **A served decision settles immediately.** `METERING` §2 charges `decide` one
   credit and §5 lets a tenant hold above one credit without a reservation; the
   honest reading is that the work is measured when the decision exists, so the
   hold is settled in the same transaction. Waiting for a nightly job would mean
   an uncommitted charge visible to nobody, and the arithmetic in this table is
   only checkable if the ledger is written when the answer is.
2. **`X-Adapt-Test` is a deployment property, not a key property.** Keys carry
   only `live`/`test`, and every key outside production is a `test` key — so
   auto-flagging test keys would have made the billing path unreachable in CI and
   in every pre-production environment. `ADAPT_ENV=staging` sets test traffic
   automatically instead, which is what "a staging deployment must not burn a
   budget" actually means. Anything other than absent/`0`/`1` in the header is a
   `422` with `details.field`, not a guess.
3. **`decisions.test_traffic` exists** (migration `0015`) so "excluded from
   metrics" is a stored fact rather than an inference from a missing ledger row,
   and `decisions_served_is_charged` was relaxed to
   `abstain OR test_traffic OR reservation_id IS NOT NULL` — a test-traffic
   decision is served, unbilled and valid, and the old check rejected it.
4. **`credit_consumption` was not re-created.** It already exists in migration
   `0006` as a monthly summary over `entry_type = 'settle'` with
   `security_invoker = true`, which is exactly what §3 asks for; the increment's
   job was to prove it (settle-only, tenant-invisible across tenants, unchanged by
   a reserve/release pair), and a second view would have failed on the duplicate.

The 200-decision corpus, same seed as §21.1: **176 served, 24 abstained, 352
billing ledger rows** (176 `reserve` + 176 `settle`), one `grant` row from the
setup, 176 `credit_reservations` all `settled`, and
`sum(consumed) = 176`. A reader can check three ways: `2 × served = ledger`,
`settles = consumed = served`, `decisions = served + abstained = 200`.

### 21.6 Red-then-green: billing an abstention

Changing `if not outcome.abstain and not test_traffic:` to `if not test_traffic:`
— billing exactly what `METERING` §3 rule 1 forbids — turns
`test_an_abstention_writes_no_ledger_rows` red (its empty ledger becomes
`{ledger: 2, settles: 1, consumed: 1, reservations: 1}`) and takes
`test_decide.py::test_an_abstention_is_free_and_a_serve_is_not` with it. The
sabotage does not touch a billing flag or a header: the tests count rows, so the
only way to make them green is to write none.
