# Adapt 2.0 — the blueprint

*One page to explain the product, the money and the honesty machinery. Detail lives in the sibling
documents; this is the summary to hand to someone who has ten minutes.*

---

## What it is

An **inference engine that learns a person and decides what to do next** — one that tells you when it
does not know. Hosts embed it in their own product through an API; they bring the app, an API key and
credits. We maintain the engine, the evidence and the model, continuously.

It is sold to the **institution**, not the learner. We are a processor: the customer owns the learners,
the data and the relationship. Nothing in the product requires us to see a name, an email or a face.

**What we sell is access to a maintained intelligence engine** — not tokens, not a model, not coverage
of a population.

---

## The three things that must never be confused

Every capability in the system is described on three separate axes, each on the same L0–L3 scale:

| Axis | Question it answers | Who controls it |
|---|---|---|
| **Entitlement** | has this tenant bought it? | commercial — the contract |
| **Capability** | have we built it? | engineering — the release |
| **Validation** | have we *earned* the right to claim it? | evidence — the gates |

What a tenant actually gets is `served = min(entitlement, capability, validation)`, and the binding
constraint is always named in the response. A tenant can own Deep and still be served L1, with
`limited_by: "validation"` — because their own learners have not accumulated the evidence yet.

This is the single most important structural decision in the product. It is what lets us sell a
capability before it is validated without ever overstating it, and it is what stops "we have the
feature" from silently becoming "we have the proof".

## Tiers buy capacity and capability — never coverage

| | Core | Plus | Deep |
|---|---|---|---|
| What it is | the loop, calibrated confidence | strategy identification, diagnostic probes | prospective calibration, longitudinal validation |
| Abstention visibility | always — never paywalled | yes | yes |
| Probes | — | included entitlement | included |
| Deep | — | — | buyable by **any** eligible tenant; L3 claims unlock only by passing gates |

No tier ever buys a better answer about a learner. Deep is not a partnership and not a bespoke
engagement — it is a tier any eligible tenant may purchase, and it still has to clear the same gates.
New tenants always start by collecting their own evidence.

---

## The loop

```
      host product                        Adapt engine
   ┌────────────────┐   decide    ┌──────────────────────────────┐
   │ what should    │ ──────────► │ 1. is there enough evidence? │
   │ this person do │             │ 2. if yes: serve, with the   │
   │ next?          │ ◄────────── │    interval that justifies it│
   └────────────────┘  serve or   │ 3. if no: ABSTAIN, say why,  │
          │            abstain    │    say what would fix it     │
          │                       └──────────────────────────────┘
          │ observe: what actually happened          ▲
          └──────────────────────────────────────────┘
                     the model gets sharper
```

**Abstention is a product feature, not a failure.** A brand-new learner with no history gets
`insufficient_evidence` plus the number of trials that would change the answer — not a confident guess
and not a canned default. It is never billed, at any tier, and it is never counted as an error.

**But abstention alone can hide an empty product** — a system that abstains on everything looks honest
and serves nothing. So the headline console metric is a **pair**: abstention rate *and* served rate,
on the same screen, with the same denominator. High abstention with high served means a careful engine;
high abstention with low served means a broken or unfinished one. Both numbers, always.

---

## What the engine measures about itself

| Metric | What it prevents |
|---|---|
| **Served rate** = recommendations served ÷ valid decide requests | an honest-looking engine that never answers |
| **Abstention rate**, by reason code, with denominator and interval | hiding product gaps inside "uncertainty" |
| **Coverage** of the intervals it prints | precise-sounding numbers that are wrong too often |
| **Calibration by construct × profile × horizon** | one population average standing in for everyone |
| **Refusal to claim** below its evidence floor | a marketing claim outrunning the data |

The evidence floors are gates, not adjectives: no L2 claim until the calibration gate passes on the
tenant's own data, no L3 claim until the prospective gate passes. Below the floor the only honest
answer is `insufficient_evidence`, and that is a valid, first-class response.

## What it must never say

It may say what it will do next and how sure it is. It may **not** diagnose, score a mind, infer
emotion, or characterise a person's ability. Intellectual disability is modelled as a **profile** that
changes how the product behaves — prompting, pacing, fading, fewer options — never as a capability
deficit to be graded. Adults only.

---

## The economics, as measured

| Quantity | Value | How it was obtained |
|---|---|---|
| `decide` | 26.2 µs | microbenchmark, v1 engine |
| probe scoring | 869 µs | microbenchmark |
| one cohort refit | ~90 µs per observation | measured, batch |
| compute per 2,000 learners / month | **1,091 s ≈ 18 CPU-min** | reconciled per operation, refits included |
| share of that which is refits | **99.0%** | — |
| model math as a share of cost of service | ~1% | the rest is HTTP, database, storage, support, validation |

**The pricing unit is a bundle, not a token:** 80 credits ≈ one reference learner-month, priced from
willingness to pay, operating cost and margin — **never** derived from compute. Monthly included
allowance per tier, usage packs on top, no surprise overage, customer-set alerts and caps. Included
allowances expire; purchased packs do not, ever. Annual contracts only after the pilot.

**Customers pay for capabilities and capacity. They do not pay extra because Adapt honestly admits
uncertainty.**

---

## Where the build actually is

| Milestone | Content | State |
|---|---|---|
| **M0** foundations | tenancy, RLS, API keys, credit ledger, encryption, CI | **built through increment 5** — 68 files, 7 migrations, 243 tests, real PostgreSQL 16.2 |
| **M1** the loop | decide → serve/abstain with citation → observe → learn; thin TS/Python SDK; console shell; sandbox demo | next |
| **M2** evidence | calibration gates, claims surface, abstention economics | planned |
| **M3** depth | probes, strategy identification, longitudinal validation | planned |
| **M3.5** profile | `cognitive_support`: prompting, fading, consent, accessibility | planned |
| **M4–M6** | metering at scale, conformance suite, integration surfaces | planned |

Foundations first is deliberate: the invariants that are expensive to retrofit are tenant isolation,
an append-only ledger and encryption at rest. A model built on a database that cannot prove
who-sees-what is a demo, not a product.

## What "done" means for a host

1. **Under 15 minutes from docs to a first successful call** — tested with a developer who has never
   seen them.
2. **A demo you can watch**: a learner with zero history abstains, earns its evidence in six
   interactions, then serves a recommendation with a visible interval. Nothing fabricated.
3. **A conformance suite in their CI** (15 checks) — the reference host scores 100% or the build fails.
4. **Byte-identical replay** of any decision, from its `decision_id`.

## The risks worth naming

| Risk | Why it matters | Mitigation |
|---|---|---|
| Coverage matrix goes stale | the honesty claim becomes decoration | regenerate on release; the matrix is load-bearing |
| Claims outrun evidence under sales pressure | the one thing that cannot be walked back | claims boundary enforced in code; the surface refuses to publish ungated claims |
| Integration friction | a good engine nobody adopts | five integration paths; <15 min gate; thin SDKs, never a heavy one |
| Per-tenant evidence takes time | new tenants get less than they paid for | served level is explicit, `limited_by: validation`, and the path to more is shown |
| Compute cost is 1% and support is unmeasured | margins are not yet known | measured cost basis, deliberate bundle headroom, no compute-derived pricing |

---

*Detail: `SPEC.md` (what and why), `MEASUREMENT.md` (evidence gates), `METRICS.md` (the numbers),
`METERING.md` (credits and cost), `PRIVACY.md`, `CLAIMS.md`, `INTEGRATION.md` (host contract),
`UI.md`, `ADR-001`/`ADR-002` (decisions), `AUDIT.md` (what was found and fixed).*
