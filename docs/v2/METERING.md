# Metering, credits and limits

How usage is counted, why each weight is what it is, and the rules that make the bill defensible when
a customer questions it.

---

## 0. The pricing principle

> **Customers pay for capabilities and capacity. They do not pay extra because Adapt honestly admits
> uncertainty.**

Everything below is subordinate to that sentence. It is why abstentions are free, why reads are free,
why refits are not metered, and why no weight exists to recover the cost of a decision the engine
declined to make.

This document contains a **correction log** (§1.3) because an earlier version of it got the arithmetic
wrong, and a spec that quietly fixes its own numbers is not auditable.

---

## 1. Measured cost basis

### 1.1 Relative costs (microbenchmark)

v1 engine code, Python 3.11, this sandbox, single core, median of repeated runs. **Indicative and
relative only** — see §1.4 for what this is not.

| Operation | Measured | Unit cost |
|---|---|---|
| BKT posterior update | 0.83 µs | per observation |
| Information gain (item selection) | 3.2 µs | per candidate item |
| Strategy Thompson sample | 0.80 µs | per strategy per decision |
| `ranked()` over 13 strategies | 0.59 µs | per decision |
| **`decide`** (rank + 13 samples + 8 updates + 3 info-gain) | **26.2 µs** | per decision — **including abstentions** |
| **`observe`** (3 updates) | 3.5 µs | per event |
| **Probe scoring** (bootstrap CI, 2000 × 40) | **869 µs** | per construct scored |
| **EM refit** | **≈4.5–5 µs per observation per iteration** (≈90 µs/obs at 20 iters, ≈293 µs/obs at 60) | per observation in the fitted history |
| Event payload (representative) | 262 bytes | per event |

Two findings that changed the design:

**A refit costs ~100× an online update of the same data.** Fitting is not something to do per learner
per day; it is a batch operation amortised over a cohort.

**A single-learner fit is not identifiable.** Fitting one sequence of 40 observations returns in
~0 ms — the estimator refuses to estimate rather than producing four degenerate parameters. So
per-learner parameter fitting is not merely expensive, it is statistically indefensible. Parameters are
shared at **cohort level** (learners in the same subject and tier of prior knowledge), refit nightly,
with each learner's own state updated online at 0.83 µs per observation. This is a better model *and*
a cheaper one, which is the only kind of change that survives contact with a deadline.

### 1.2 Monthly totals — the corrected arithmetic

2,000 learners, 20 decisions/week each, 28% abstention, one probe per learner per month across three
constructs, 200 events per learner.

| Line | Volume | Unit | CPU |
|---|---|---|---|
| `decide` (served **and** abstained — an abstention still computes) | 172,000 | 26.2 µs | **4.5 s** |
| probe scoring | 6,000 | 869 µs | **5.2 s** |
| `observe`, **model math only** (3 BKT updates) | 400,000 | 3.5 µs | 1.4 s |
| `observe`, **end-to-end measured** — validation, two lookback queries, four indexes, FK checks, committed row | 400,000 | **245 µs** | **98 s** |
| cohort refits, nightly, 200 obs × 2,000 learners × 30 nights | 12,000,000 obs | 90 µs | **1,080 s** |
| **Total** | | | **≈1,188 s ≈ 19.8 CPU-minutes ≈ 0.33 CPU-hours** |

**Refits are 91% of it, not 99%** — persisting events is real work and the earlier table hid it by
pricing only the arithmetic. The 245 µs figure is an upper bound rather than a target: 400,000 events
in 800 batches of 500 across 4 concurrent clients, on a **development** PostgreSQL (bundled server,
default settings, container I/O, single worker, no TLS, no proxy). Load-test detail and the three
findings that came out of it are in `engine/DECISIONS.md` §18.9 and `KNOWN_GAPS.md` §2f.

The 262-byte payload model is confirmed: measured 262.1 bytes per event.

### 1.3 Correction log

The first version of this document claimed **"≈4 seconds of CPU"** for this tenant. That was wrong by
about 270×, and the catch came from a reader checking the arithmetic rather than trusting the prose.

| Error | Original | Correct | Cause |
|---|---|---|---|
| Probe scoring omitted from the total | — | 5.2 s | the weight was assigned but never added into the monthly sum |
| **Refits omitted entirely** | — | 1,080 s | refits were classified "free" for billing and then, incorrectly, also excluded from *cost*; free to the customer is not free to us |
| Abstentions excluded from compute | 124,000 decides | 172,000 | an abstention is free to bill but still runs the full decision path |
| **`observe` priced as model math** | 3.5 µs per event, **1.4 s/month** | 245 µs end-to-end, **98 s/month** | the 3.5 µs figure measures three BKT updates and no durable write; the table labelled it "per event", which understated ingest by ~70×. Found by the build agent's 400,000-event load test, on the increment that first wrote events to disk |
| Refit unit mis-chosen | 113.7 ms per "learner refit" | ~90 µs/obs, cohort-level | the 200 × 20 batch benchmark was a *cohort* fit, not a per-learner one; and a per-learner fit is not identifiable anyway |

Lesson recorded because it will recur: **a cost that is not billed is the easiest cost to forget**, and
it is usually the largest.

### 1.4 What this is not

The microbenchmark establishes **relative weights**. It does not establish the cost of running the
service. Those are different claims and are kept apart on purpose:

| Layer | Status |
|---|---|
| Model math (this section) | measured, relative — accurate to within 2× |
| HTTP + JSON + validation + Postgres round trip | **assumption**, 2–5 ms per request; 100× the model math, and the dominant per-call cost |
| Database writes (400,000 events/month) | **assumption**, 1–3 ms each including indexes and WAL; likely the largest compute line in production |
| Storage | ≈600–900 bytes per retained event; ≈320 MB/month for this tenant |
| Backups, observability, egress, control-plane | not measured |
| **Human support and the validation programme** | not measured, and probably the largest line at this scale |

**Stated plainly:** in production the model math is on the order of 1% of cost of service; request
handling, storage and people are the rest. That is why the weights are chosen for predictability and
fairness rather than for cost recovery, and why the price is a value decision (§5).

---

## 2. Units and weights

One **credit** ≈ one served decision. Weights derive from §1.1, then floor in the customer's favour.

| Operation | Credits | Why |
|---|---|---|
| `POST /v1/observe` | **0** (within event quota) | 3.5 µs of math; charging for input suppresses the input we need |
| `POST /v1/decide` — recommendation served | **1** | the product |
| `POST /v1/decide` — **abstention** | **0** | §0 and rule 1 |
| `POST /v1/predict` | **1** | a logged prospective claim |
| `POST /v1/probe_sessions/{id}/score` | **3** | measured 33× a decide, floored from 33 to 3 |
| Probe that returns an abstention | **0** | the abstention rule applies at every tier |
| Model refit | **0** | our own maintenance, cohort-batched nightly; metering it punishes hygiene |
| Reads (`model`, `evidence/*`, `instruments`, `claims`) | **0** | charging for reads teaches customers to cache stale models |
| Replay / audit of a past decision | **0** | customers must be able to verify us for free |

**Weights are checked, not asserted.** `engine/scripts/cost_basis.py` re-runs the benchmark in CI and
fails the build if a weight drifts more than 2× from its measured ratio, or if the §1.2 monthly sum is
not reproducible from the weights. The price list is a derived artefact.

**Probes are a Plus entitlement** (`ADR-002`). Core is mastery, next-item, spacing and uncertainty
visibility; probes are part of what makes Plus meaningfully more capable, and giving them away would
undercut the tier for no reason. A probe that abstains is free at every tier.

## 3. Fairness rules

Contractual, not aspirational. Each exists because some vendor does it the other way and customers
notice.

1. **An abstention is free.** If the engine says "I don't know", it does not get paid. Removing any
   revenue incentive to answer badly is worth more than the credits it costs.
2. **Never billed:** 4xx, 5xx, timeouts, 429s, auth failures, health checks, idempotent replays,
   rejected payloads. The industry default bills 4xx as "the caller's fault"; a 422 is a customer
   learning our API, and an error message is not a service.
3. **Test traffic is free.** `X-Adapt-Test: 1` is unbilled and excluded from metrics, so staging and CI
   cannot burn a school's budget.
4. **Reserve, then settle.** Anything above 1 credit reserves up front, settles against measured work,
   releases the difference idempotently. Failures release; they never debit.
5. **Rounding favours the customer.** Settle on measured usage rounded down; sub-credit work
   accumulates across a cycle instead of rounding up per call.
6. **Paid credits never expire**, subject to applicable law. Monthly *included* allowance expires at
   cycle end, is counted down in the console, and is disclosed before purchase.
7. **Drawdown order is published and customer-favourable:** expiring allowance first, then purchased
   packs oldest-first.
8. **No surprise overage.** Hard cap on by default, customer-configured; alerts at 50/80/95%. At the
   ceiling `decide` returns `402 quota_exhausted` with a reset date, and `observe` keeps accepting for
   7 days so no learner data is lost at the boundary.
9. **Three controls, three codes.** Budget (alert), balance (`402`, checked *before* expensive work),
   rate/quota (`429` with `Retry-After`). Never merged into one "remaining" number, because merged
   numbers make retries, refunds and bursts corrupt each other.
10. **Price changes need 90 days' notice** and never land mid-cycle.
11. **Refunds are individually addressable.** Every debit is a ledger row with an id; a goodwill or
    error refund references that row. A decremented integer cannot be audited.
12. **Billing-grade telemetry:** mandatory idempotency keys, immutable append-only ledger, 72-hour
    acceptance window for late events, daily gateway-to-ledger reconciliation that pages someone on drift.
13. **The reference bundle is a reference, not a promise.** 80 credits is the commercial unit (§5); no
    learner is contractually guaranteed to consume exactly 80, and no learner is throttled to fit it.

## 4. The ledger

```
credit_grants(id, tenant_id, kind: included|purchased|promo, amount, remaining,
              granted_at, expires_at NULLABLE, source_ref)
credit_ledger(id, tenant_id, grant_id NULLABLE, delta, reason, ref, idempotency_key,
              period_start, created_at)        -- append-only, unique(tenant_id, idempotency_key)
credit_reservations(id, tenant_id, amount, state: held|settled|released, operation_ref)
```

* Grants have identities, because a monthly allowance and a purchased pack behave differently and a
  single integer cannot answer "what expires on the 30th".
* Balance = sum over non-expired grants; never a mutable column.
* Concurrency: reservations take `SELECT ... FOR UPDATE` on the tenant's open grants; 100 concurrent
  reservations against a balance of 10 must yield exactly 10 successes.
* Every ledger row joins back to the request that caused it. That is the point.

### The accounting identity — four terms, not three

```
granted = available + held + consumed + forfeited
```

`forfeited` is the unused remainder of a grant that expired: `sum(remaining)` over grants whose
`expires_at` has passed. Without that term the identity **breaks the moment any allowance expires** —
the credits are simply missing from the books, which is both a reconciliation failure and a number the
customer is entitled to see. It is derivable, so no sweeper is needed, but the identity must be
asserted *after* an expiry, not only on the happy path.

* `available` counts **non-expired** grants only. Expired credits are never resurrected by a partial
  settlement: what was used is consumed, the remainder is forfeited.
* `sum(delta)` over the whole ledger is **not** a consumption figure — reserve and release rows carry
  `delta = 0` because they bind an idempotency key without moving a credit. Every row carries
  `entry_type`, and **billing reads `entry_type = 'settle'` only**. A `credit_consumption` view exists
  so that nobody has to remember this at the call site.
* Two commercial invariants live in the schema, not in intent: `kind = 'included'` **must** have an
  expiry, and `kind = 'purchased'` **must not** — "paid credits never expire" is rule 6 above, and a
  column that permits the opposite will eventually be used to do it.

## 5. The commercial unit

**80 credits = one reference learner-month bundle.**

It is a *reference*: the unit an institution budgets in, and the anchor for currency pricing. It is not
a promise that a learner consumes 80, and it is not a throttle.

Currency price is **not** derived from §1. It is set from willingness to pay per learner, the value of
the intelligence delivered, the cost of operating and supporting the service, and the gross margin
needed to sustain it — then tested against prospective customers, not asserted in a spec.

Worked example, with the corrected compute (§1.2):

| Line | Volume | Credits |
|---|---|---|
| `decide` served | ≈124,000 | 124,000 |
| `decide` abstained | ≈48,000 | **0** |
| probe scoring (Plus) | 6,000 × 3 | 18,000 |
| `observe` | 400,000 | 0 (quota) |
| refits | 60,000 | 0 (batch, free) |
| **Total** | | **≈142,000 credits ≈ 71 per learner** |

71 against a reference of 80 is deliberate headroom: the bundle should fit a normal institution with
room for a heavier term, not sit exactly on the observed mean where half of all customers exceed it.

**Billing periods:** monthly at launch. Annual contracts after the pilot, with a published allowance
schedule; unused *included* allowance rollover only, with a disclosed cap; purchased packs kept
separate and non-expiring. No rollover mechanics before there is usage data to model them on.

## 6. Limits

| Limit | Value | Code | Rationale |
|---|---|---|---|
| Rate per key | 50 rps, burst 100 | `429 rate_limited` | protect the shared fleet |
| Concurrency per tenant | 20 | `429` | bounded queues |
| `observe` batch | ≤ 500 events, ≤ 1 MB | `413 payload_too_large` | bounded batch, so a slow ingest is a slow batch and not a slow request |
| **Ingest throughput per connection** | **50 rps, burst 100** | `429 rate_limited` + `details.retry_after` | a runaway client must not degrade other tenants on a shared database; free, and counted |
| Event payload | ≤ 4 KB, allowlisted keys | `422 payload_invalid` | no free-text PII, by construction |
| `decide` constructs per call | ≤ 5 | `422 payload_invalid` | a call wanting 40 constructs wants a batch job |
| Open probe sessions per learner | ≤ 5 | `409 conflict` | resumable, not stackable |
| Probe session trials | ≤ 60 | `422` | above this it is a test, not a probe |
| Refits | cohort-level nightly; on-demand debounced to 1/cohort/hour | `429` | the real compute line; also model stability |
| Repeat `decide` per (learner, construct) | 20/hour; repeats inside the window return the cached decision | `200` + `X-Adapt-Cached: true` at **0 credits** | free abstentions must not become a polling strategy (audit A4) |
| Keys per tenant / environments | 10 / 3 | `409` | keys are not a secrets manager |
| Retention maximum | 36 months | `422` | platform maximum; tenants may set lower |
| Credit ceiling | tenant-configured | `402 quota_exhausted` | fail closed, never bill silently |

Rate limiting and billing are separate subsystems. A 429 costs nothing; a 402 means no credits; neither
is a 5xx, and the SDK distinguishes all three.

## 7. SDK tooling

The SDK's job is that a developer can predict a bill before writing the loop.

```python
client = Adapt(api_key=..., environment="staging")     # staging auto-sets X-Adapt-Test

client.estimate("decide", constructs=["skill.mastery","strategy"])   # -> 1 credit
client.decide(learner, dry_run=True)                                  # -> what it would say, 0 credits

client.observe(learner, events, idempotency_key=client.derive_key("session", sid, n))
#    retries reuse the key: one charge, one ledger row, one result

client = Adapt(..., max_retries=3, retry_on=[429, 500, 502, 503, 504])   # never bills a 5xx

client.usage.current()              # credits used/remaining, projection date, per-operation split
client.usage.assert_under(50_000)   # raises in CI before a test suite burns a budget

except adapt.QuotaExhausted as e:  e.resets_at
except adapt.RateLimited as e:     e.retry_after
except adapt.NotEntitled as e:     e.tier_required      # ADR-002, distinct from quota
```

Plus `adapt.testing.FakeAdapt` with mocked meters, and a `cost-report` CLI that prices a recorded
traffic sample — so "what will this cost at 2,000 learners" is answered by data, not by sales.

## 8. Build order

| Milestone | Metering deliverable |
|---|---|
| M0 | grants, ledger, reservations, idempotency, concurrency test (`AGENT_M0.md` increment 4) |
| M1 | weights wired to real operations; `estimate`; usage endpoints; console credit projection |
| M2.5 | `cost_basis.py` in CI: 2× drift check **and** reproduction of the §1.2 monthly sum |
| M3 | usage screen per operation/connection, CSV export, invoice preview |
| M5 | price book published with derivation; SDK `estimate`/`dry_run`/`FakeAdapt`/`cost-report` |

## 9. Open questions

1. Currency price per bundle — a customer test question, not an engineering one.
2. Annual terms: rollover cap and reconciliation, after the pilot.
3. Whether the learner-month bundle includes probe scoring or prices it separately. Probes are the
   Plus trigger, so bundling them may undercut the tier story.
