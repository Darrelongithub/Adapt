# Spec audit — consistency and logical soundness

Date: 2026-11-16. Scope: all 14 documents in `docs/v2/`. Method: cross-check every number, threshold,
code and rule that appears in more than one document; then test the *logic* of each mechanism by asking
what an adversarial customer or a tired engineer would do with it.

Result: **6 textual inconsistencies and 3 logical holes.** All fixed in place; each fix is listed with
the file it changed. Nothing was found that invalidates the architecture — but two of the three logic
holes would have produced a product that looked honest and wasn't.

---

## 1. Logical holes

### A1 — Abstention rate can hide an empty product (severity: high)

`METRICS.md` defines abstention to exclude `not_validated`, `not_entitled` and `not_capable` from the
denominator, and reports them as separate series. That was right for attribution, and it creates a hole:
a tenant with **zero validated constructs** can show **0% abstention** while receiving nothing at all.
The number would be true, and the dashboard would imply the opposite of the truth.

**Fix.** Added a companion headline metric, **served rate** = recommendations served ÷ valid decide
requests, displayed next to abstention. The two together are honest: high abstention + high served rate
is a cautious engine; 0% abstention + 30% served is a broken one. A dashboard that shows only one can
be gamed by accident.

Changed: `METRICS.md` §1.8 (new), `UI.md` §2.1 and §2.2 wireframes.

### A2 — Erasure versus prospective calibration (severity: high)

`PRIVACY.md` §5 correctly deletes **prediction records** on erasure (an earlier draft kept them
"detached", which was wrong). But `MEASUREMENT.md` gate 6 and `METRICS.md` §3 score prospective
predictions *after the fact* — which implies storing them and recomputing later. Erasure then destroys
the evidence base for our own calibration claims, and the loss is silent.

**Fix.** Prospective-claim statistics are **aggregated incrementally at serve time** — running counts
per (construct × profile × horizon bin), updated when a prediction is made and when its outcome lands —
and only those aggregates are retained. Raw prediction records remain deletable. A consequence worth
stating: calibration is computed from a running tally, not recomputed from history, so it cannot be
"backfilled" and a bug in the tally is not repairable from raw data.

Changed: `MEASUREMENT.md` §9, `PRIVACY.md` §5.

### A3 — The three axes are not on the same scale (severity: medium)

`ADR-002` says `served = min(entitlement, capability, validated)`, but entitlement is a **tier**
(`core`/`plus`/`deep`) while capability and validation are **levels** (`L1`/`L2`/`L3`). `min()` over
mixed scales is undefined, and the example JSON in the same document cheerfully says
`"entitlement": "L3"` — contradicting its own table one paragraph above.

**Fix.** Levels are `L0` (nothing) through `L3`; `tier → entitlement_level` is an explicit mapping
(core→L1, plus→L2, deep→L3); responses carry **both** `tier` and `entitlement_level`, and `min()` is
defined over levels only.

Changed: `ADR-002` §1, `SPEC.md` §4.1.

### A4 — Free abstentions create a polling incentive (severity: medium)

Rule 1 (an abstention is free) is right, and it makes a specific behaviour rational: a host repeatedly
polling `decide` for the same learner until it gets a non-abstention costs nothing until it succeeds.
The rate limiter bounds it, but the design should name it.

**Fix.** Per (learner, construct) `decide` calls are capped at 20/hour; repeat calls inside that window
return the cached decision with `X-Adapt-Cached: true` at **0 credits**; the console raises an
`abstention_polling` alert when a connection exceeds 5× its normal repeat ratio. Free stays free — it
just stops being a strategy.

Changed: `METERING.md` §6, `METRICS.md` §1.5.

---

## 2. Textual inconsistencies

| # | Where | Inconsistency | Fix |
|---|---|---|---|
| T1 | `INTEGRATION.md` §14 vs `PRIVACY.md` §5 | export path `GET /v1/export` vs `GET /v1/learners/{id}/export` | unified on the learner-scoped path |
| T2 | `INTEGRATION.md` §6 | error table missing `501 not_capable`, and no statement that `not_validated` is a **200 with a lower served level**, not an error | both added, with the rule stated |
| T3 | `AGENT_M0.md` §9 vs `AGENT_M1.md` §4 | M0 is silent on the claims table, while M1 requires one to cite from — an ambiguity an agent would resolve in whichever direction cost less | boundary now stated in both: M1 ships a **static seeded claims table** (5–10 real cited claims); ingestion and review stay M2; M0 ships none |
| T4 | `SPEC.md` §4.2 | instruments plane does not state that probes are a Plus entitlement, contradicting `ADR-002` | stated inline |
| T5 | `ADR-002` §1, `SPEC.md` §4.1 | `"entitlement": "L3"` mixing tier and level in examples | examples now carry `tier` + `entitlement_level` |
| T6 | `METERING.md` §5 vs `METRICS.md` §2 | the worked example briefly still implied a refit weight existed | refit line says **0 credits, cohort-batched**, matching the weight table |

---

## 3. What held up (checked, no change needed)

Recorded so the next reviewer does not re-litigate them:

* **Abstention is free** — stated identically in `METERING.md` §2/§3, `METRICS.md` §1, `ADR-002`
  §Quotas, `AGENT_M1.md` §5, and enforced by a named test.
* **402 / 429 / 403 are three different things** — consistent across `METERING.md` §6,
  `INTEGRATION.md` §6, `AGENT_M1.md` §5, with distinct SDK exception types.
* **RLS discipline** — `SET LOCAL` inside a transaction, `FORCE`, `USING` + `WITH CHECK`, non-owner
  role, `tenant_id` indexed: identical in `SPEC.md` §6, `PRIVACY.md` §4, `AGENT_M0.md` §2.
* **18+ everywhere** — `SPEC.md` §5.4, `ADR-001` §3, `PRIVACY.md` §3, `AGENT_M0.md` §6, and the age
  gate is enforced at three points, not one.
* **Probe weight 3** — the only figure that appears in three documents, and it is the same in all three.
* **80-credit reference bundle vs 71-credit worked example** — deliberate headroom, stated as such in
  both places rather than looking like an arithmetic slip.
* **Cohort refits only** — `METERING.md` §1.1, §6 and `AGENT_M1.md` §3 agree, including the reason
  (a single-learner fit is not identifiable).
* **Pseudonymity ≠ anonymity** — `PRIVACY.md` §1, `SPEC.md` §5.3, `ADR-002` §3 all say the same thing,
  in the same words.

---

## 4. Findings that are product decisions, not defects

Left open deliberately, with the document that owns the decision:

| Question | Owner | Why it is still open |
|---|---|---|
| Currency price per 80-credit bundle | `PRICE_TEST.md` | a customer-test question; any number in a spec would be invented |
| Probes inside the bundle or an add-on | `METERING.md` §9 | interacts with the Plus tier story; needs the first two contracts |
| Annual rollover cap | `METERING.md` §9 | needs a cycle of real usage before the mechanics are worth building |
| Retention default (24 vs 36 months) | `SPEC.md` §10 | a policy choice for the first DPIA |
| Whether `cognitive_support` needs explicit consent as a separate toggle | `PRIVACY.md` §10 | legal review, not engineering |

---

## 5. Residual risks after the audit

1. **The coverage matrix is now load-bearing.** It is generated from the validation harness and fails CI
   on mismatch (`UI.md` §2.8). If that generator ever lags the harness, the honest "not yet" becomes a
   stale claim — the exact failure the matrix exists to prevent.
2. **`served_rate` must be on the front page, not a drill-down.** A metric that exists but is two clicks
   away will be omitted from the deck that matters.
3. **Two of the fixes (A1, A2) are contracts, not comments.** Both need a test in M1/M2.5 respectively;
   the audit does not build them, it only specifies them.
4. **This audit checked documents against each other, not against reality.** No code exists, so every
   "acceptance test" in the build prompts is a promise. The first M0 report is where promises meet
   evidence, and it should be reviewed against `AGENT_M0.md` §7 line by line rather than skimmed.
