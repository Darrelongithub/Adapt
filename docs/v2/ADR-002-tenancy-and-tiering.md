# ADR-002 — Tenancy, tiers, and the three axes

Status: **accepted** (product decisions taken; pricing numbers not fixed)
Date: 2026-11-16
Supersedes the earlier draft of this ADR.
Related: `SPEC.md` §4.5, `METRICS.md` §1, `PRIVACY.md`, `UI.md` §2

---

## Context

Three product decisions and one architectural correction arrived together:

1. **We do not operate learners.** A school may have 2,000; per-learner accounts, support and
   rostering are not our business. Sell to the school, hand over tokens and an integration path,
   focus on the engine.
2. **L1/L2/L3 are packages**, because not every buyer wants deep understanding.
3. **Quotas are included in tiers**, with add-on packs, no surprise overage.
4. **Entitlement, capability and validation are three different things** and must never be
   represented as one.

The fourth is the one that shapes the architecture, so it comes first.

---

## Decision 1 — Three axes, never collapsed

Every claim-bearing response reports all three, plus what was actually served.

| Axis | Question | Owner | Changes when |
|---|---|---|---|
| **Entitlement** | what this customer's contract allows | commercial | a contract is signed or changed |
| **Capability** | what the engine can technically do, per construct | engineering | code ships |
| **Validation** | what the engine has demonstrated, per construct × profile × population | the measurement programme | gates pass — **L1 is a platform gate** (every tenant is served it once the estimator passes), **L2/L3 are per-tenant gates** measured on that tenant's own learners |

All three axes are expressed on **one scale of levels — L0 (nothing) to L3** — because `min()` over a
tier name and a level is meaningless. The tier maps onto the scale explicitly, and both are returned:

```
tier → entitlement_level:   core → L1    plus → L2    deep → L3
served_level = min(entitlement_level, capability, validated)
```

and the binding constraint is named explicitly:

```json
"level": {
  "requested":  "L3",
  "tier":       "deep",
  "entitlement_level": "L3",
  "capability":  "L3",
  "validated":   "L1",
  "served":      "L1",
  "limited_by":  "validation",
  "since":       "2026-10-01",
  "validation_ref": "gates/cmp_retention@2",
  "profile":     "adult_general"
}
```

Rules:

* `limited_by ∈ {entitlement, capability, validation, null}`. One value, always present.
* **A Deep customer can be served L1**, and the response says so with a reason, not a silently lower
  number. This is the case the correction was about: entitled to retention prediction, construct only
  validated at L1.
* Validation is per **(construct, profile, population)**. `cmp_calibration` may be L2 for
  `adult_general` and L1 only for `cognitive_support`. A single global level would be a lie in one of
  the two.
* Capability is versioned and published (`capability_matrix@v3`), so "the code can do it" is not a
  claim made in a slack message.
* **The learner inspector shows the validated level.** **The coverage matrix shows entitlement
  separately.** They are different screens because they are different facts.

### Where each axis is surfaced

| Surface | Shows |
|---|---|
| `decide` / `model` responses | the full `level` object, `limited_by` included |
| Learner inspector | served/validated level per construct, with the reason when limited |
| Tenant coverage matrix | construct × **your entitlement** × validated × serving, and the delta in plain words |
| Public model cards | capability + validation status per gate, no tenant information |
| Sales collateral | coverage matrix only — never a bare "L3" claim |

---

## Decision 2 — Tiers as entitlements

A tier sets the **level an engine may serve** and the **monthly event allowance**. It never promises
coverage.

| Capability | Core | Plus | Deep |
|---|---|---|---|
| `observe`, `decide`, `model`, L1 mastery / next-item / spacing | ✓ | ✓ | ✓ |
| **Basic confidence** on every estimate: interval, sample size, and what it means | ✓ | ✓ | ✓ |
| Calibration **analysis** (per-domain Brier/ECE, bias patterns), strategy identification, **diagnostic probes** (`/v1/probe_sessions`) | — | ✓ | ✓ |
| Prospective calibration, longitudinal validation, intervention-effect prediction, retention prediction | — | — | ✓ |
| Model cards | public, all tiers | + per-construct coverage matrix | + fairness audit page |

**Why basic confidence is in Core.** If Adapt says a learner has mastered geometry, the buyer must be
able to see how sure the engine is. Hiding uncertainty behind a paywall would make Core a mastery
tracker that quietly launders point estimates — and it would put our transparency claims in the
pricing deck rather than in the product. The split is between *showing* uncertainty (free) and
*analysing* it (paid). That is a defensible line and it keeps the upgrade path intact.

**Deep is purchasable by any eligible tenant.** No design-partner agreement is required to *buy* the
capacity. But the capacity is gated by validation per construct, so a new tenant typically starts on
L1/L2 coverage and accumulates the longitudinal evidence that unlocks the rest. Design partners get
early access and influence over which constructs are validated next; they do not get exclusive rights
to an L3 claim that does not exist yet.

> Deep is an entitlement, not a guarantee of coverage.

### Quotas

Detailed unit weights, the measured cost basis, fairness rules, limits and SDK tooling live in
**`METERING.md`**. Governing principle: **customers pay for capabilities and capacity, never for the
engine admitting uncertainty** — hence abstentions are free at every tier, including abstained probes.

The commercial shape:

* **Probes are a Plus entitlement.** Core is mastery, next-item, spacing and uncertainty visibility;
  probes are what make Plus meaningfully more capable, and giving them away would undercut the tier.
* **80 credits = one reference learner-month bundle** — a commercial reference unit and the anchor for
  currency pricing, not a per-learner promise or a throttle. Currency price is set from willingness to
  pay and margin, then tested with customers; it is not derived from compute (`METERING.md` §5).

* Every tier includes a monthly **event allowance** and a **credit grant**, plus a **learner-month
  bundle** so a school can budget in the unit it actually thinks in.
* An abstention is free; errors, retries, test traffic and reads are never billed.
* Extra capacity is sold as **usage packs** (events or credits) — no surprise overage, no automatic
  card charge at the boundary.
* `observe` gets a deliberately generous allowance: incoming observations are what make the model
  good, and a school that throttles its own data to save money is a school with a weak model.
* At the credit ceiling, `decide` fails closed with `402 quota_exhausted` and a reset date, while
  `observe` keeps accepting for 7 days so no data is lost at the boundary.
* Customers configure alerts at 50/80/95% and an optional hard cap.

Pricing numbers stay out of the spec: they need a cost model and a first buyer, not a guess.

---

## Decision 3 — The tenant owns the learners

**The tenant is the controller, we are the processor** (`PRIVACY.md`). Concretely:

* No learner accounts, logins, invitations, rosters, names or emails. The engine sees an opaque
  tenant-scoped `learner_id`, format-validated to reject anything that looks like PII.
* No per-learner support channel; support is at integration level, diagnostics are self-serve.
* Erasure and export are tenant API calls with receipts, not tickets to us.
* One contract covers 2,000 learners: at 20 decisions/week that is ≈172,000 credits and ≈400,000
  observations a month, which is a database, not a scaling problem.

**The distinction that must not be blurred:** not operating learners is not the same as not modelling
them. We hold per-learner state — that is the product. We refuse identity, curriculum, UI, and human
support for individuals. **We hold the model; they hold the people.**

And per `PRIVACY.md`: pseudonymity reduces exposure, it does not remove obligations.

---

## Consequences

**Positive.** A Deep buyer sees exactly which constructs are L1, so the sales conversation becomes a
roadmap conversation instead of a credibility risk. Uncertainty is free at every tier, which makes the
transparency claim structural. Quota packs remove the renegotiation cliff. Downgrade is lossless —
data retained, model warm, re-upgrade resumes without cold start.

**Negative.** Three axes mean three places to be wrong, and every response grows a field. The coverage
matrix must be generated from gate results automatically, or it will drift from reality and become the
new overclaim. Sales will ask to sell "L3" as a slogan; the `limited_by` field is the answer, and it
has to be non-negotiable.

**Risks.**

| Risk | Mitigation |
|---|---|
| Coverage matrix drifts from real gate state | generated from the validation harness, never hand-edited; a mismatch fails CI |
| Lowering a floor to unlock L3 for a customer | floors are engineering-owned; changes require an ADR, are audited and annotate the metrics charts |
| Cross-tier metric comparison ("why is my abstention higher") | comparability dimensions on every aggregate; the console refuses raw comparison |
| PII smuggled into `learner_id` | ingest validation rejects it, with a message naming the problem |
| Entitlement checked in the client, not the server | server-side check on every claim-bearing response, tested |

---

## Rejected alternatives

1. **One "level" number per tenant** — collapses the three axes, and the collapse always resolves in
   favour of the sales team.
2. **Calibration entirely in Plus** — makes Core dishonest rather than cheap.
3. **Deep gated behind a partnership agreement** — blocks revenue and blocks evidence; gate the
   *claims* instead.
4. **Overage billing at the boundary** — predictable revenue for us, budget incident for a school.
5. **Per-seat pricing or a managed-learner tier** — the headache we are declining, and it would make
   us a controller of identity data.
