# Metrics specification — abstention, tokens, reliability

Three metric families the console reports, each with an explicit definition, denominator, grain and
version. A metric with an ambiguous denominator is a metric that will be argued about instead of used.

Governing rule: **an abstention is a legitimate engine outcome.** It is never an error, never a
silent no-op, and never folded into a failure rate. It is the engine declining to claim more than its
evidence supports, and it is reported as prominently as the decisions themselves.

---

## 1. Abstention

### 1.1 Two grains, both reported

| Grain | Definition | Why both |
|---|---|---|
| **Request-level abstention** | a `/v1/decide` response with `abstain: true` — no recommendation returned at all | the host experience: the customer's app got nothing and fell back to its own default |
| **Construct-level abstention** | one `(request × construct)` pair where that construct abstained while others may have answered | the model experience: which part of the learner model is still empty, and why |

A single request can produce three answers and one abstention. Collapsing the two grains hides exactly
the case that matters — a learner who is well modelled on mastery and unmodelled on calibration.

### 1.2 Denominator

```
decide_requests_valid  =  all /v1/decide responses with status 2xx in the period
                        − idempotent replays (same Idempotency-Key already counted)
                        − requests from connections in status paused | revoked
                        − requests flagged as test traffic (X-Adapt-Test: 1)

abstention_rate        =  abstentions / decide_requests_valid
construct_abstention   =  construct-level abstentions / construct-level evaluations
```

Explicitly **outside** the denominator, and reported as their own series:

| Excluded | Why not an abstention |
|---|---|
| 4xx policy refusals (prohibited claim: diagnosis, emotion, IQ, cross-learner ranking) | These are *refusals*. Counting them as abstentions would let a customer "improve" the rate by making more prohibited requests. |
| 5xx errors | Broken, not honest. Belongs to the reliability SLO, not the model. |
| `not_entitled` (contract) | Commercially meaningful, and it is a contract limit, not the model's. Reported adjacently as **entitlement limits**. |
| `not_capable` (engineering) | The code does not exist yet. An internal delivery metric; never shown to a learner. |
| `not_validated` (measurement) | The capability exists but the gates have not passed. Reported as **validation gaps**, and published as the coverage matrix — this is the honest "not yet" that a Deep customer is buying into. |

Mixing any of these three into the abstention rate would let us improve the honesty metric by changing a
contract, shipping code, or lowering a floor. They are separate series for exactly that reason.
| Test traffic | Otherwise every demo and every integration test inflates the number. |

Time zone: **UTC**, period boundaries at 00:00:00Z. All counts are per tenant, per connection, per
construct, per profile and per tier, and every one of those dimensions is filterable.

### 1.3 Reason codes

Every abstention carries exactly one primary reason and may carry contributing reasons:

| Code | Meaning | Typical fix |
|---|---|---|
| `insufficient_evidence` | below the construct's evidence floor (trials, sessions, judgements) | keep interacting, or run a probe |
| `low_confidence` | floor met, but the interval is too wide to act on | more trials in the same condition |
| `conflicting_signals` | method effects or mastery estimates disagree beyond the stated band | more data; possibly a context layer that is missing |
| `consent_withheld` | the learner's scopes prevent the evidence needed to reach the floor | ask for the scope, or accept L1 |
| `data_quality` | too many trials excluded by validity checks (rapid guessing, hidden tab, device change) | fix the host's instrumentation |
| `unsupported_request` | a construct × level × profile combination that does not exist in any sense | correct the request |

Three further codes come from the axes in `ADR-002` and are **not** abstentions — they are reported as
three separate series, because each has a different owner and a different fix:

| Code | Axis | Owner | Fix |
|---|---|---|---|
| `not_entitled` | contract | commercial | upgrade, or request a lower level |
| `not_capable` | engineering | us | ship the capability; returned as `501` when the code does not exist at all |
| `not_validated` | measurement | us | pass the gates; the public coverage matrix is the status page for this |

`consent_withheld` and `data_quality` are the two that most often look like model weakness and are
actually **customer-side**. They are the first two rows of the drill-down for that reason.

### 1.4 What the response returns

```json
{
  "abstain": true,
  "abstentions": [
    {"construct": "cmp_spatial", "reason": "insufficient_evidence",
     "needs": {"trials": 6, "sessions": 1},
     "contributing": ["low_confidence"],
     "level_requested": "L2", "level_available": "L1"},
    {"construct": "cmp_calibration", "reason": "consent_withheld",
     "needs": {"scope": "probes"}}
  ],
  "answered": [{"construct": "skill.mastery", "level": "L1", "value": 0.41,
                "interval": [0.33, 0.49]}]
}
```

`needs` is mandatory. An abstention without a resolution path is a dead end, and dead ends get
disabled by customers within a week.

### 1.5 Health bands and the trap

Per construct, with the band set at validation time:

| Band | Request-level abstention | Reading |
|---|---|---|
| healthy | within the pre-registered band | normal honesty |
| watch | +3pp week over week, two weeks running | something changed in the data |
| investigate | +8pp, or >60% for a construct older than 30 days | instrumentation, consent, or a broken floor |
| `abstention_polling` | one connection exceeds 5× the normal repeat ratio on the same (learner, construct) | a host retrying `decide` hoping for a non-abstention; free calls are still capped and cached (`METERING.md` §6) |

**The trap, stated explicitly so nobody optimises into it:** a *falling* abstention rate is not
automatically good, and a rate of 0% is a warning, not an achievement. It means either every construct
has passed its gates, or somebody lowered a floor. The console shows the floor configuration next to
the trend, and a floor change is an audited event that annotates the chart.

### 1.6 Resolution and time-to-first-claim

Two companion metrics, because 30% abstention is fine if it resolves fast and fatal if it never does:

* **Resolution rate** — share of constructs that abstained and later produced a claim, within 7 and
  28 days.
* **Time to first claim** — median sessions/events from a learner's first observation to their first
  L1 claim, then L1 → L2. This is the cold-start number, and it is the one a buyer will ask about.

### 1.7 Cross-tenant and longitudinal measurement

* **Definitions are versioned** (`metric_version`). A change to a denominator ships as a new version;
  historical series are recomputed where event data permits, and otherwise labelled as a step change.
  A definition change must never render as a behaviour change.
* **Comparability dimensions** are mandatory on every aggregate: tier, profile, construct, connection
  age. An L1-only tenant will always abstain more than a Deep tenant with probes enabled — comparing
  them raw is a category error, and the console refuses to.
* **Benchmark view** (internal + opt-in tenant): median abstention by tier and construct across
  tenants, shown only when ≥5 tenants contribute, so no single customer is inferable.
* **Export**: CSV and Parquet, with the metric version, the denominator, and the exclusion counts in
  the header so a number in a slide can be traced to the query that made it.

---

### 1.8 Served rate — the metric that stops abstention lying

Abstention excludes `not_validated`, `not_entitled` and `not_capable` from its denominator, which is
correct for attribution and **dangerous alone**: a tenant with no validated constructs could show 0%
abstention while receiving nothing.

```
served_rate = recommendations served / decide_requests_valid
```

Both numbers appear together, always:

| Abstention | Served | Reading |
|---|---|---|
| high | high | a cautious engine earning its claims — the intended state early in a tenant's life |
| low | high | a warm model with good coverage — the target state |
| low | **low** | broken: requests are being refused for entitlement, capability or validation reasons |
| high | low | thin evidence *and* thin coverage — a conversation about onboarding, not about price |

A dashboard showing one without the other can be gamed by accident, so `UI.md` §2.1 shows both in the
same block. **Served rate is the pair to abstention, not a replacement for it:** alone it rewards
guessing, which is the behaviour we removed a revenue incentive for.

### 1.9 Coverage — the other companion metric

Abstention says *we do not know this learner yet*. Coverage says *we have not proven this capability
yet*. Both are published; only one of them is our own debt.

For every (construct × profile):

| Field | Source |
|---|---|
| `entitlement_ceiling` | the commercial contract |
| `capability` | the shipped code (`capability_matrix@version`) |
| `validated` | the gates actually passed, with evidence refs |
| `serving` | `min` of the three |
| `tenants_entitled_above_validation` | how many customers can pay for something not yet demonstrated |

**Validation debt ratio** = constructs where entitlement > validated ÷ constructs offered at that tier.
It is the number that keeps the company honest during a sales push: it should fall every quarter, and if
it rises because a tier was launched early, that is a finding to publish, not to bury.

This is generated from the validation harness, never hand-edited, and the coverage matrix in the
console is rendered from it (`UI.md` §2.8).

## 2. Tokens (credits)

### 2.1 Units and weights

Full derivation, measured cost basis, fairness rules, limits and SDK tooling: **`METERING.md`**.

Summary: one credit ≈ one served decision; `observe` is free within an event quota; an **abstention is
free**; probe scoring is 3; reads, replays and refits are 0. Weights are checked in CI against a
re-measured benchmark, so the price list is a derived artefact rather than an assertion.

Plan structure: a monthly credit grant, an event quota enforced by retention, and a tenant-configured
ceiling that fails closed — at the ceiling `decide` returns `402 quota_exhausted` with a reset date,
while `observe` keeps accepting for 7 days so no learner data is lost at the boundary.

### 2.2 Cost, measured

For the record, because it changes the argument: a `decide` costs 26 µs of CPU, a probe score 869 µs,
and a refit ~90 µs per observation. For a 2,000-learner school that is ≈142,000 credits and **≈18
CPU-minutes a month, 99% of it refits** (`METERING.md` §1.2 — corrected from an earlier and much too
cheerful "4 seconds"). The recurring production cost is storage and support, which is why events are
governed by quota rather than by credits.

That is not a scale problem, it is a *support and liability* problem. Which is why the next section
matters more than any tuning.

### 2.3 Division of labour (do not blur this)

| Adapt | The tenant |
|---|---|
| the engine, the model, the claims, the instrument specs | content, curriculum, pedagogy delivery |
| token metering, quotas, projections | learner accounts, identity, rostering |
| API reliability, versioning, deprecation policy | the UI their learners actually see |
| the JSON contract and the render spec | whether their integration honours it |
| aggregate reliability numbers | support for their own learners and staff |

**Adapt never holds a learner's name, never issues a learner login, never runs a roster, and never
contacts a learner.** Identity stays with the school; Adapt sees an opaque tenant-scoped id. The
tenant is the controller, we are the processor, and that is what makes 2,000 learners one contract
instead of 2,000 support tickets.

### 2.4 Reliability SLOs (the thing we *do* owe them)

| SLO | Target | Window |
|---|---|---|
| API availability (`/v1/decide`, `/v1/observe`) | 99.9% | 30 days rolling |
| `decide` latency | p95 < 150 ms, p99 < 400 ms | 24h |
| `observe` ingest latency | p95 < 100 ms | 24h |
| Error rate (5xx) | < 0.05% | 24h |
| Data durability | zero acknowledged-event loss | continuous |
| Breaking changes | never within `/v1`; deprecation ≥ 6 months with 90-day dual-serve | per release |

Reported in the console with the same rigour as abstention — published, historical, per connection,
and with the exclusions stated. A reliability number nobody can audit is a marketing claim.

---

## 3. How the three families interact

This is worth a paragraph because it is where the product story lives:

* **Abstention is the honesty metric.** It is what a serious buyer checks when they do not believe
  "AI".
* **Tokens are the commercial metric.** They tell the buyer what this will cost and tell us where the
  integration is broken (a host polling `decide` in a loop shows up here first).
* **SLOs are the trust metric.** They are why the other two are believable.

A dashboard that shows only usage is a billing page. One that shows abstention beside it says: *we
know what we don't know, and we can tell you when we don't know it.*
