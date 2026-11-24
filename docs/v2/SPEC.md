# Adapt 2.0 — specification

Status: draft for approval. v1 is frozen at `fb655b3` and stays runnable as the reference demo.
This file describes the engine that replaces it. Companion documents: `MEASUREMENT.md` (what may
be claimed), `CLAIMS.md` (the evidence surface), `ADR-001-cognitive-support-profile.md`
(the intellectual-disability track), `ADR-002-tenancy-and-tiering.md` (who owns learners; entitlement vs
capability vs validation), `METRICS.md` (abstention, reliability, coverage), `METERING.md` (units, measured cost basis, fairness
rules, limits, SDK), `PRIVACY.md`
(roles, data map, retention, erasure — pseudonymity is not anonymity), `UI.md` (the three surfaces and
the visual language), `AGENT_M0.md` (the build prompt for milestone 0).

---

## 1. What this is

Adapt 2.0 is a **learning intelligence engine**: a service a host product calls to make its
teaching adapt to one learner over time.

```
   host app ──observe──▶ engine ──decide──▶ host app shows something
       ▲                                          │
       └──────────── outcome ─────────────────────┘
                        │
                 engine updates its model of that learner
```

The engine owns three things and only three things:

1. **A learner model** — what this person knows, how they respond to methods, how well they
   judge their own knowing, in what conditions.
2. **A decision policy** — what to do next, with a stated reason and a citation.
3. **Evidence** — the record that justifies both, and the machinery that refuses to claim more
   than the record supports.

Everything else — content, UI, curriculum, branding, pedagogy delivery — belongs to the host.
The engine never renders a lesson and never writes a report card.

### The one-sentence version

> The engine learns how *this* learner learns, says what it will do next and why, and refuses to
> answer when the evidence is too thin.

### What v1 got right, and what it did not

v1 (frozen) proved: an approval gate can sit between a machine proposal and a learner's
experience; measurement can be honest about what it does not know; a privacy allowlist can be
enforced in code and shown to the user. Those four invariants carry forward unchanged — see §8.

v1 did not solve: a real learner model (it had BKT on a syllabus and a Beta posterior over 13
named strategies), a real measurement layer (no construct validation, no reliability evidence), or
a real product surface (146 routes, provider concerns leaking into the UI). v2 is a rebuild, not a
refactor: the loop is the same shape, the machinery underneath is new.

---

## 2. Non-goals

Written down because each one has been proposed and each one would sink the project.

| Not this | Why |
|---|---|
| A diagnosis, screener, or clinical instrument | Requires clinicians, norming and regulatory standing we do not have. |
| An IQ-like score, "mental age", "brain age", or a "how your brain works" number | Not measurable from learning interactions; the claim is false before the code is written. |
| Cognitive training that claims to improve memory, attention or intelligence | Far transfer from training is ~null (g≈0.05–0.12 across 14 meta-analyses, ~0.03 with active controls). We measure and adapt learning; we do not sell brain gains. |
| Emotion, affect or attention inference from camera or microphone | Prohibited in education under EU AI Act Art. 5(1)(f). Not built, not configurable. |
| A content library or LMS | The host owns content. We own the model of the learner. |
| Support for anyone under 18 | Out of scope by decision, in every profile, enforced in code (§5.4). |
| Claiming anonymity for pseudonymous learner data | It is personal data and we are a processor; pseudonymity is a safeguard, not an exit (`PRIVACY.md` §1). |
| Learner accounts, logins, rosters, names, emails | The tenant is the controller and owns identity. The engine holds an opaque tenant-scoped `learner_id` and a model, nothing else (`ADR-002`). |
| Per-learner support, curricula, or content | One contract per institution. Support is at the integration level; diagnostics are self-serve in the console. |
| Cross-tenant learning by default | Only with explicit contractual opt-in, and never on identifiable data. |
| A "psychological API" that returns scores about a person | The facts surface returns instrument metadata and published effect sizes, not judgements (§4.3). |

---

## 3. The three levels of understanding

Three levels, in plain words, and the engine always states which one it is operating at.

| | **L1 — What you've done** | **L2 — How you learn** | **L3 — What happens next** |
|---|---|---|---|
| **Evidence** | every learning interaction | L1 + short probes + confidence taps | L1 + L2 over weeks, across contexts, plus intervention outcomes |
| **Model** | mastery per skill, item difficulty, spacing | method effects, load and fatigue, calibration (Brier/ECE), strategy use | retention prediction, time-to-mastery, intervention effect, context effects, model drift |
| **Claims** | "likely correct", "next item", "review in 3 days" | "this method works better for you on this skill", "you are over-calibrated here" | "you will retain this ~12 days", "this intervention adds X", "the model is wrong here" |
| **Needs** | one session | validated probes + incremental validity | longitudinal data + calibration + fairness audit |
| **Cold start** | immediate | ~3 sessions, or an optional 3-minute battery | weeks, or a design partner's history |

Rules:

* **No skipping.** A construct at L1 cannot produce an L2 claim. Every claim-bearing response carries
  a `level` object with **entitlement, capability and validated reported separately**, the `served`
  level (`min` of the three) and the `limited_by` field naming the binding constraint (`ADR-002`). When
  the evidence is thin it returns `insufficient_evidence` with what would be needed; when the gap is a
  contract or a gate, it says which.
* **L2 must beat L1.** A probe earns its place only if it improves prediction of the learner's
  outcomes beyond what ordinary interaction already gives (`MEASUREMENT.md` gate 3). No
  incremental validity, no probe.
* **L3 is earned per construct, not per product.** Some constructs reach L3 on one design
  partner's data; others never do. Say which.
* **Every level states its own uncertainty**, as an interval, not an adjective.

---

## 4. The API: four planes

Versioned under one prefix. A host integrates with plane 1 and optionally 2; planes 3 and 4 are
internal or read-only.

### 4.1 Plane 1 — Model and decisions (the product)

Deterministic. No network calls inside a request. No LLM in the path. Same input, same output.

| Endpoint | Purpose |
|---|---|
| `POST /v1/observe` | Record learning events. Batched, idempotent, schema-validated. |
| `POST /v1/decide` | What to do next, with reason, confidence and citations. |
| `GET /v1/model/{learner}` | Current state: mastery, method effects, calibration, level per construct. |
| `POST /v1/predict` | Prospective claim ("probability correct", "days to retention threshold") — logged so it can be scored later. |
| `GET /v1/evidence/{learner}` | Why the model says this: events, updates, decisions, outcomes. |

`decide` returns a ranked list, not a command:

```json
{
  "learner": {"id": "lrn_...", "profile": "adult_general"},
  "level": {
    "requested": "L2", "tier": "deep", "entitlement_level": "L3",
    "capability": "L3", "validated": "L2",
    "served": "L2", "limited_by": null, "validation_ref": "gates/cmp_strategy@2"
  },
  "recommendations": [
    {
      "action": "worked_example_then_fade",
      "target": {"skill": "linear_equations", "difficulty_band": [0.55, 0.75]},
      "confidence": {"point": 0.68, "interval": [0.51, 0.82]},
      "why": "3 consecutive errors after instruction; method effect estimate for this learner favours worked examples on procedural targets.",
      "cites": ["clm_0142", "clm_0310"],
      "evidence_level": "L2",
      "expires_at": "2026-12-01T00:00:00Z"
    }
  ],
  "abstain": false
}
```

When the evidence is thin: `"abstain": true` with a populated
`"needs": {"events": 40, "probes": ["calibration"], "sessions": 3}`. An abstention is a correct
answer, not a failure, and the host is expected to fall back to its own default.

### 4.2 Plane 2 — Instruments

Probes as **render specifications**, not assets. The host draws them; the engine owns the
construct, the scoring and the validity. **This plane is a Plus entitlement** (`ADR-002`) — a probe that
returns an abstention is free at every tier.

| Endpoint | Purpose |
|---|---|
| `GET /v1/instruments` | Registry, filtered by profile, construct, age band, licence. |
| `POST /v1/probe_sessions` | Open a session; returns items/trials sized by information gain. |
| `POST /v1/probe_sessions/{id}/responses` | Trial-level submission, resumable across sit-ins. |
| `POST /v1/probe_sessions/{id}/score` | Score when the evidence floor is met; otherwise `insufficient_evidence`. |

Every instrument carries: construct, mechanic type, a11y metadata (response modes, reading level,
time limit), licence and attribution, version, and `not_a_diagnosis` on any score.

### 4.3 Plane 3 — Evidence and facts (read-only, cached, cited)

The "psychological facts" surface: what is known, from where, with what certainty — never a
statement about a person. Full schema in `CLAIMS.md`.

* `GET /v1/evidence/claims?construct=&population=&certainty=`
* `GET /v1/evidence/claims/{id}`
* `GET /v1/instruments/{id}` — metadata, constructs, norms *if licensed*.
* `GET /v1/constructs` — definitions and what each does not mean.

### 4.4 Plane 4 — Provider gateway (internal)

Model routing, budgets, fallbacks, prompt templates, language handling. **Nothing here is
reachable from a product response and nothing here names a provider.** v1 leaked Gemini into logs
and UI surfaces; v2 makes that structurally impossible: only the internal router can call out, and
its outputs are text, never decisions.

### 4.5 Admin and commercial plane

The customer-facing product. Tenants, **connections** (host app × environment × key × webhook),
tier entitlement, API keys, credit balances and usage records, configuration (profile, evidence
policy, consent scopes, retention, provider budget), webhooks, data export and erasure, and the audit
log. Separate auth surface, separate audit log, no learner identifiers beyond opaque IDs.

The front page answers three questions — *who is connected to what*, *what are they spending*, and
*is the engine honest* — and the third is answered by the abstention block, which ships with its
denominator, counts and reason mix (`METRICS.md` §1). This plane is the commercial face of the engine;
see `UI.md` §2.

---

## 5. Learner model, profiles, consent

### 5.1 Five layers

| Layer | Contents | Level |
|---|---|---|
| Knowledge | mastery per skill, item difficulty, spacing state | L1 |
| Strategy | method effects per (skill × method × condition) | L2 |
| Regulation | load, session fatigue, hint dependence, breaks | L2 |
| Calibration | confidence vs correctness, Brier/ECE, per domain | L2 |
| Context | device, input mode, time of day, session length, assistance | L2/L3 |

Layers are added one at a time. A new layer must show incremental validity before it can influence
`decide`; until then it is recorded and reported but not acted on.

### 5.2 Profiles

A profile is a **versioned bundle**, not a fork: battery, evidence floors, stopping rules, strategy
catalogue, permitted claims, pacing, accessibility metadata, consent flow, metric definitions.
Profiles share one core, one database, one API. A profile may not invent endpoints.

| Profile | Audience | Status |
|---|---|---|
| `adult_general` | adult learners, any subject | default |
| `cognitive_support` | adults with intellectual disabilities (18+) | see `ADR-001` |

Rule: **a claim raised under one profile may not be served under another.** Profile is a
first-class dimension of every claim, threshold and validation record.

### 5.3 Consent

* The learner's own consent, recorded with version, timestamp and the exact text shown.
* Per-scope toggles: interaction log, probe participation, model-assisted summarisation, research
  use. Each reversible; switching one off degrades a specific capability with an explicit message
  rather than silent guessing.
* Consent is re-checked at every session start, not once at signup, because the record must show
  what was agreed at the time of each decision.
* Withdrawal deletes or stops-on-and-retains per the learner's choice. **Erasure removes events, probe
  trials, decisions, model state and prediction records together** — a record of a prediction about a
  deleted learner is still data about that person, so it goes. Only non-identifying aggregates survive
  (`PRIVACY.md` §5).
* Pseudonymous identifiers are a safeguard, not an anonymity claim: we remain a processor of personal
  data and carry the full set of obligations (`PRIVACY.md` §1).

### 5.4 Age

**Eighteen and over, everywhere.** Enforced at three points: tenant configuration, learner
creation, and event ingestion. Any date of birth implying under 18 is rejected — not warned,
rejected — and the error says why. There is no minor path, no guardian consent path and no
"school" mode in this release. Reopening it is a new ADR with its own legal review.

---

## 6. Stack and tenancy

| Concern | Decision |
|---|---|
| Runtime | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Data | Postgres 16 (RLS by `tenant_id`), Redis (queues, rate limits, caches) |
| Client | TypeScript SDK + admin console, generated from OpenAPI |
| Deployment | containers; Kenya region if available, else EU with Kenya DPA s.49 safeguards |
| Tenancy | one database, `tenant_id` on every row, RLS enabled **and forced**, policy with `USING` **and** `WITH CHECK`, tenant set with `SET LOCAL` inside a transaction, app role neither superuser nor table owner; plus per-tenant envelope encryption for learner payloads |
| Keys | `ak_live_`/`ak_test_` prefixed, stored as hashes, shown once, scoped, revocable |
| Metering | credit ledger per tenant; unit = active learner per month, with internal granular metering for cost control |
| Portability | export everything the tenant wrote, in a documented format, on demand |

**Not** multi-region, **not** microservices, **not** event-sourced, **not** Kubernetes-operator
territory at M0. One deployable service and one worker.

---

## 7. Milestones

Each milestone ends with something runnable and a report I can audit.

| # | Deliverable | Done when |
|---|---|---|
| **M0** | Skeleton: service, config, health, tenancy, keys, credits, migrations, CI, contract lint | `docker compose up` serves `/v1/health`; two tenants are cryptographically and legally isolated in tests; `AGENT_M0.md` checklist fully green |
| **M1** | Observe pipeline + L1 model + metering + console shell | Events ingest idempotently through a **connection**; BKT-equivalent mastery with documented priors; synthetic learners recover known mastery within stated error; L1 claims only; credits metered per `METERING.md` (abstention free, failures free, test traffic free) with `estimate` and usage endpoints; console shows real connection and credit data; **`adapt sandbox demo` proves the loop end to end** — synthetic learners, an abstention that resolves into a claim, a byte-identical replay (`INTEGRATION.md` §13) |
| **M2** | `decide` v1 + reason trail + abstention reporting | Every recommendation cites a claim and an evidence record; every abstention carries a reason code and a `needs`; rate/counts/reasons exposed per construct, tier and period per `METRICS.md` §1; deterministic replay reproduces byte-identical output |
| **M2.5** | Measurement lab + coverage generator | Instrument registry, evidence floors and stopping rules, scoring, validity harness, synthetic learners with known ground truth; **no probe ships without a construct, a validation criterion and an incremental-validity plan**; the coverage matrix is generated from gate results, never hand-edited |
| **M3** | L2 layers + full console | Method effects beat an L1-only baseline on held-out learners; calibration reported with intervals; console ships Usage, Configuration, **Coverage matrix (three axes, debt named by failing gate)**, Learner inspector and **public model cards** (no auth: they are the procurement document) |
| **M3.5** | `cognitive_support` profile | Prompt-fading model, accessibility metadata, supported-decision consent flow, single-case-design validation harness (see `ADR-001`) |
| **M4** | L3 + prospective calibration | Predictions logged and scored; retention and intervention-effect claims with intervals; disproof path works |
| **M5** | First integration | A real host app runs the loop in production for 4 weeks with the pilot measures in `PILOT.md`; **`adapt-conformance` passing in the host's own CI** (`INTEGRATION.md` §11) — our reference host scores 100% or our build fails |
| **M6** | Hardening | DPIA, AI Act classification memo, retention, export/erase, incident runbook, load test, pen-test fixes closed |

Measurement lab (M2.5) sits before any learning claim, and it is the milestone most likely to be
skipped under deadline pressure. It is not skippable.

---

## 8. Invariants carried from v1

These are non-negotiable and each already has a working precedent in the frozen demo:

1. **The gate.** A machine proposal is a proposal. A human accepts, modifies or rejects, and the
   rejection reason is data. v2 adds: the learner is that human.
2. **Measurement honesty.** The system states what it does not know, in the interface, at the point
   of the claim. `insufficient_evidence` is a first-class response.
3. **The privacy allowlist.** An explicit list of what may be recorded and an explicit list of
   forbidden payload keys, enforced in code and shown to the user verbatim.
4. **Graceful degradation.** Every optional dependency (model, voice, connectors, network) can be
   absent, and the app says so instead of failing or faking.

---

## 9. Success and kill criteria

**Success (M5, one design partner):** ≥70% of `decide` recommendations accepted or modified rather
than rejected; L2 claims beat an L1-only baseline on held-out learners by a pre-registered margin;
calibration error within its stated interval; zero unresolved privacy or consent incidents; the
model's own optimism contradicted by measured outcomes at least once and handled in the report;
**abstention rate published per construct with its denominator, and never optimised to zero**; SLOs
met and published per connection.

A tenant must be able to answer, from the console alone: who is connected, what they have spent, what
the engine declined to claim and why, and whether it was up. If any of those four needs a support
ticket, the milestone is not done.

**Kill:** if by M4 the strategy layer cannot beat L1-only prediction on real learners, the
"how you learn" product is not supported and we ship L1 + calibration only. That is a legitimate
outcome and must be written down before the data arrives.

---

## 10. Open decisions

1. Postgres RLS + envelope encryption at M0, or RLS only until M1? (Recommend both at M0 — retrofitting
   encryption is the expensive version.)
2. ~~Credit granularity~~ **decided**: credits meter `decide` / `predict` / probe scoring, `observe` is
   free within an event quota (`METRICS.md` §2.1). Open sub-question: is the quota itself a tier
   parameter or a separate add-on?
3. Do we host the pilot ourselves, or does the design partner host with our container? (Recommend we
   host, so measurement is not confounded by their instrumentation.)
4. Retention default: 24 months from last activity, or learner-selected at consent?
5. Tier names and the Core boundary: does calibration belong in Core (it is cheap and it is a strong
   demo) or is it the first reason to buy Plus?
6. Does Deep require a design partner agreement, since its constructs need longitudinal data no
   customer will have on day one?
