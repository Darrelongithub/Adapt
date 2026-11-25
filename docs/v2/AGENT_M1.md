# Build prompt — Milestone 1 (observe, L1 model, metering, console shell)

You are implementing **Adapt 2.0, milestone 1**, on top of M0. Read `SPEC.md`, `METRICS.md` §1,
`METERING.md`, `ADR-002` and `PRIVACY.md` before writing code. This document is the work order for one
milestone.

M1 is where the engine stops being a schema and starts making claims. Two rules govern everything:

1. **No claim without an interval and an evidence count.**

2. **An abstention is a first-class outcome, is never billed, and is never counted as an error.**

---

## 0. How to work

**Seven increments. Stop and report after each.** Increments 4 and 6 are the ones a deadline eats;
they are not optional and they are not last.

| # | Increment | Stops after |
|---|---|---|
| 1 | Connections (host × env × key × webhook) | a connection can be created, paused, revoked |
| 2 | Observe ingest: schema, allowlist, idempotency, validity flags | 400k events ingest at p95 <100 ms in a load test |
| 3 | L1 mastery model + cohort refit job | synthetic learners recover known mastery within stated error |
| 4 | `decide` v1 with reasons, citations and abstention codes | every recommendation cites; every abstention carries a reason and `needs` |
| 5 | Metering wired end-to-end | abstentions, failures and test traffic bill zero, proven by test |
| 6 | Abstention metrics per `METRICS.md` §1 | the numbers in the console reproduce a hand-computed fixture |
| 7 | Thin SDK (TS + Python) + console shell on real data | one raw REST example and one SDK example both reach a first decision; Overview, Connections, Learner inspector — no fabricated data anywhere |

### Non-negotiables carried from M0

* No LLM, no model provider SDK, no network egress outside `/v1`.
* Deterministic: injected `Clock`, seeded RNG for any sampling, replayable decisions.
* RLS on every new table, `SET LOCAL` per transaction, non-owner app role.
* Logs are an allowlist. Never a payload, never a `learner_id` in plaintext, never a key.
* `learner_id` format validation rejects anything resembling PII.

---

## 1. Increment 1 — connections

A connection is the unit a customer understands: **host app × environment × key × webhook**.

```
connections(id, tenant_id, host_name, environment, key_id, webhook_url NULLABLE,
            status: active|paused|revoked, event_quota_monthly, created_at, created_by)
```

* `POST /v1/admin/connections` issues a key with scopes; the key is shown once, in the response, and
  never again.
* `PATCH` supports pause (stops ingest, keeps data) and `DELETE` supports revoke (stops auth).
* Webhook: `decision.created`, `consent.changed`, `quota.warning`; 5 retries, exponential backoff,
  signature header, delivery log in the console.
* Connections are the filter dimension on every metric (`METRICS.md` §1.2).

**Acceptance:** create → auth works → pause → 403 with `connection_paused` (not `unauthorized`, because
the integrator needs to know the difference) → resume → revoke → `401`. Webhook delivery failure is
retried and visible in the console with the response code.

## 2. Increment 2 — observe

```
POST /v1/observe
{ "connection": "conn_…", "events": [ {…}, … ] }        # ≤500 events, ≤1 MB
```

Event schema — required: `learner_id`, `skill`, `item_id`, `correct`, `ts`. Optional: `response`,
`latency_ms`, `session_id`, `device`, `input_mode`, `hint_used`, `assisted`, `difficulty_band`,
**`decision_id`** (links an outcome to the decision that caused it — without it the engine can never
score its own recommendations, and L2/L3 become impossible; store it, count it, expose the share of
events carrying it per connection). **Any key outside the allowlist is rejected**, not ignored, and the
error names the key.

* Idempotency: `Idempotency-Key` header mandatory; `(tenant_id, idempotency_key)` unique. Event-level
  dedupe on `(learner_id, session_id, seq)` when `seq` is present.
* Batch is all-or-nothing per request: partial accept creates silent data loss.
* Validity flags evaluated at ingest and stored on the event: `rapid_guess` (<300 ms), `tab_hidden`,
  `straight_line` (8+ identical consecutive), `device_change`, `assisted`, `repeated_item`.
  Never imputed, never dropped — flagged (`MEASUREMENT.md` §4).
* Storage: append-only `learning_events`, partitioned by month; retention job enforces the tenant's
  setting, logs what it deleted.

**Acceptance:** duplicate batch returns the original response and writes no new rows; an event with
`email` in the payload is rejected with `422 payload_invalid` naming the key; the retention job's
deletions are observable in the console; and the ingest SLO below is met and re-measured.

### Ingest SLO, and two correctness rules that the schema cannot enforce

**The SLO is stated in a unit we can actually measure.** The earlier line — "500 events at p95
<100 ms" — was written before anyone had written a row to disk, and a durable 500-event batch does
not do that on any hardware we have. Measured on the dev reference (bundled PostgreSQL, default
settings, container I/O, one worker): **p95 789 ms per 500-event batch, 245 µs per event**. That is
the baseline to beat, not evidence of a defect. Targets: hold ≤250 µs/event on the dev reference,
re-measure on production-like hardware where the target is ≤100 µs/event, and run a **10-minute soak**
(the 98 s run did not) watching for degradation or partition-default growth.

**1. Event-level dedupe has no constraint behind it.** A partitioned table cannot carry a global unique
index, so `(learner_id, session_id, seq)` is checked in code — which two concurrent batches can both
pass, storing the same observation twice and inflating that learner's evidence. Either take a
per-`(connection, learner, session)` advisory lock for the duration of the batch, or state plainly in
`DECISIONS.md` that duplicates are tolerated and why the mastery estimator survives them. Silence is
not an option: duplicate observations are indistinguishable from real ones downstream.

**2. Ingest has no throughput limit.** Authentication failures are rate-limited; event volume is not.
A tenant's runaway client can consume the database and degrade every other tenant. Add a per-connection
ingest limiter returning 429 `rate_limited` with `details.retry_after` and `Retry-After`, free, and
count it — and record the limit in `METERING.md` §6 rather than leaving it as a surprise at launch.

## 3. Increment 3 — L1 mastery

Per **(learner, skill)**, BKT with documented priors, updated online at ingest (~0.8 µs) and reported
with an interval.

* Priors per skill, stored and versioned, in a table — not in code.
* Interval: Beta credible interval over the posterior, reported as `[low, high]` with `estimator`.
* **Evidence floor: 8 trials.** Below it the construct abstains with
  `{"reason": "insufficient_evidence", "needs": {"trials": 8 - n}}`.
* **Cohort refit, nightly**: cohort = (subject, prior-knowledge band); only if the cohort has ≥200 new
  observations since the last fit; parameters written with a version and the fit date. **Never fit per
  learner** — a single-learner fit is not identifiable (`METERING.md` §1.1) and the job must refuse it.
* Cold start: the cohort prior, labelled `"source": "cohort_prior"`, never presented as personal.

**Acceptance:** synthetic learners with known mastery (n=200 per skill, mastery sampled 0.1–0.9) recover
their generating values with RMSE ≤0.08 after 40 observations, and the stated interval covers the truth
for ≥90% of learners. The test must also include a **guesser** (p(correct)=0.2 regardless of mastery)
and assert the estimator does *not* report mastery ≥0.7 — a model that reports confident nonsense on a
guesser is a release blocker.

## 4. Increment 4 — `decide` v1

Closed action set at L1 — do not invent more:

| Action | When |
|---|---|
| `next_item_difficulty` | mastery mid-range; target 70–85% success |
| `spaced_review` | mastery above band; interval from retention state |
| `worked_example` | ≥3 consecutive errors on the same skill |
| `abstain` | evidence floor unmet, or signals conflict |

* Response carries the `level` object from `ADR-002` (at M1: `capability: "L1"`, `validated: "L1"`),
  `recommendations[]` with `confidence {point, interval}`, `why`, `cites[]`, `expires_at`.
* `cites[]` must reference seeded claims from the claims service (M1 seeds 5–10 real claims: spacing
  effect, retrieval practice, worked-example effect with expertise reversal, desirable difficulty).
  **A recommendation with no citation is a test failure**, not a style issue.
* Idempotent, deterministic: same event history + same clock ⇒ byte-identical output. A replay endpoint
  re-runs a stored decision and asserts equality.

**Acceptance:** 100 stored decisions replayed produce byte-identical bodies; every recommendation in the
test corpus has ≥1 resolvable claim id; abstentions cover all six reason codes with fixtures.

## 5. Increment 5 — metering

Wire `METERING.md` exactly:

* Weights: `decide` served 1; `decide` **abstained 0**; `predict` 1; `probe score` 3 (stub the endpoint,
  price it now); `observe`, reads, replay 0.
* Reserve → settle above 1 credit; release on failure, idempotently.
* `X-Adapt-Test: 1` ⇒ zero credits **and** excluded from metrics. Staging keys set it automatically.
* `estimate` endpoint and SDK-visible usage: used, remaining, projection date, per-operation split.
* `402 quota_exhausted` (with `resets_at`), `429 rate_limited` (with `Retry-After`),
  `403 not_entitled` (with `tier_required`) — three codes, never interchangeable.

**Acceptance — this is the increment's whole point:** four tests asserting **zero ledger rows** for
(i) a served abstention, (ii) a 4xx, (iii) a 5xx after internal retry, (iv) a test-flagged call; and one
asserting exactly 1 row for a served decision, with the row joining back to the request id.

## 6. Increment 6 — abstention metrics

Implement `METRICS.md` §1 literally: both grains, the denominator and its exclusions, Wilson intervals,
`metric_version`, and per construct/connection/tier dims.

```
GET /v1/admin/metrics/abstention?from=&to=&connection=&construct=&grain=request|construct
→ { rate, numerator, denominator, interval, reasons: {...}, exclusions: {...},
    entitlement_limits, not_validated, not_capable, refusals, errors, metric_version }
```

**Acceptance:** a hand-built fixture (30 decisions: 12 abstentions across 4 reasons, 5 replays, 3 test
calls, 2 paused-connection calls, 1 refusal, 1 5xx) reproduces the expected rate exactly, and the test
asserts the denominator equals 20 — the fixture *is* the specification, so it goes in the repo.

## 7. Increment 7 — console shell

Three screens, real data only.

* **Overview**: credits used/plan with projection date, connection list with last event and volume,
  abstention block with rate **and counts and reason mix and the denominator sentence**.
* **Connections**: the table from `UI.md` §2.3.
* **Learner inspector**: per-construct rows with `serving` / `validated` / estimate / interval /
  evidence count, and the sentence "Entitled to L3; serving L1 where validation has not passed."
  At M1 every construct is L1 — show that honestly rather than hiding the column.

No seeded learners, no "SaaS Inc.", no invented numbers in screenshots. Empty states are written as
sentences with the next action.

Also ship the demo path from `INTEGRATION.md` §13, because this milestone is where a buyer should be able
to *see* the loop:

* `adapt sandbox seed` → three synthetic learners: a **guesser** (correct 20% regardless), a steady
  learner, and an over-confident one.
* `adapt sandbox demo` → the console on a prepared tenant, and the money shot works: a learner with zero
  events **abstains with `needs`**, six events are sent from the demo panel, and the next `decide`
  returns a recommendation with reason and interval.
* No fabricated numbers anywhere in the demo. The sandbox learners are synthetic and labelled as such.

**Acceptance:** every number on every screen traces to an endpoint; a screenshot test fails if any
fixture id appears; `axe` passes; keyboard-only pass documented; **`adapt sandbox demo` runs start to
finish on a clean checkout in under three minutes**, and the abstention→claim sequence appears in the
output.

---

## 8. Release gates — M1 cannot be declared done until all three pass in CI

Plumbing is easy to test hard; a model is not, and this is where rigor usually slips. These three are
gates, not guidance: they run in CI, they fail the build, and they cannot be waived by a report.

**G1 — Deterministic replay against a golden set.** Every `decide` output for a checked-in fixture of
event histories is committed as a golden file (`engine/tests/golden/decisions/*.json`). CI recomputes
and compares byte-for-byte. A changed golden file is a **failing** build unless the change carries an
explicit approval marker and a one-line reason in `engine/DECISIONS.md` — "the numbers moved because X"
is reviewable; "CI is red, regenerate the goldens" is not.

**G2 — The guesser cannot claim mastery.** A synthetic learner answering at chance (p=0.2 regardless of
true mastery) must never be served a mastery estimate ≥0.7, at any horizon, in any construct, and must
be abstained on where the evidence floor is not met. Run as a property test over ≥200 seeded learners,
not a single case. A failure here blocks release: overclaiming on a chance responder is the product's
worst failure mode, and it is invisible to every accuracy metric.

**G3 — Every recommendation cites a claim, and the claim cites evidence.** No `recommendation` ships
without a `claim_id`; no `claim_id` resolves to an ungated construct; and a claim whose evidence floor
is not met is refused at write time, not filtered at read time. Test the refusal, not the filter.

What these three have in common: each one is a promise the product makes to a buyer, converted into
something the build fails on. If a gate can be satisfied by editing a constant, it is not a gate.

---

## 9. Definition of done

- [ ] All seven increments' acceptance tests pass; suite <2 min; load test recorded
- [ ] **G1, G2, G3 pass in CI** (§8) — not in a report, in the build
- [ ] Abstention: six reason codes, both grains, fixture-exact metrics, zero billing
- [ ] `decide` deterministic and replayable; every recommendation cites a claim
- [ ] Guesser test passes — the model refuses to claim mastery it cannot support
- [ ] No PII accepted, no PII logged; retention job observable
- [ ] `git diff --stat fb655b3 -- backend frontend scripts e2e` still empty
- [ ] `engine/DECISIONS.md` updated; `engine/README.md` documents the loop end to end

## 10. Report

1. Increment, `git diff --stat`, test counts, load-test numbers.
2. The measured cost of a `decide` and an `observe` on your implementation, compared to `METERING.md`
   §1.1 — if you are more than 2× off, say so and say why.
3. Deviations and their reasons; anything in the earlier docs your implementation contradicts.
4. What you left out and which milestone owns it.

## 11. Do not

* Add L2 claims, strategy layers, calibration, or probes — they are M2.5/M3.
* Add an LLM for phrasing. The `why` text at M1 is templated from the claim statement and the learner's
  numbers, and that is enough.
* Bill an abstention. Ever. There is a test; do not weaken it to make a number look better.
* Seed demo learners into a migration.
