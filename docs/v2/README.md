# Adapt 2.0 — planning set

**There is no v2 code yet.** This directory is the specification, and it is the source of truth for
anyone (human or agent) building it. v1 is frozen at commit `fb655b3` and must stay runnable.

---

## If you are a build agent, read in this order

| Order | File | Why |
|---|---|---|
| 1 | `AGENT_M0.md` (or `PROMPT_M0.md` for a paste-ready version) | **the work order** — scope, six increments, acceptance tests, definition of done |
| 2 | `SPEC.md` §1–§7 | what the engine is, the four planes, milestones, invariants |
| 3 | `PRIVACY.md` §2–§5 | roles, data map, access control, retention, erasure |
| 4 | `METERING.md` §4 | the ledger schema, grants, reservations, idempotency |
| 5 | `ADR-002-tenancy-and-tiering.md` §1 | the three axes: entitlement ≠ capability ≠ validation |

Then implement **increment 1 only**, stop, and report. Do not run increments together.

Hard boundaries: do not modify `backend/`, `frontend/`, `scripts/`, `e2e/`. No LLM in any path at M0.
No PII. No fabricated data. If you cannot read these files, stop and say so rather than improvising.

---

## The full set

| File | Contents | Status |
|---|---|---|
| `SPEC.md` | the engine, three levels, four API planes, five model layers, profiles, M0–M6, success/kill criteria | draft for approval |
| `MEASUREMENT.md` | evidence floors, adaptive stopping, validity checks, the six validation gates, synthetic learners | draft |
| `METRICS.md` | abstention (both grains, reasons, denominator), served rate, coverage, reliability SLOs | draft |
| `METERING.md` | measured cost basis, credit weights, 14 fairness rules, ledger, limits, SDK tooling | draft |
| `PRIVACY.md` | processor position, data map, access control, retention, erasure, aggregation, refusals | draft |
| `CLAIMS.md` | the evidence surface: claim records, endpoints, ingestion, licensing | draft |
| `INTEGRATION.md` | the host contract: five integration paths, quickstart, errors, webhooks, conformance suite, demo | draft |
| `UI.md` | console-first surfaces, blue/cream operator language, the Why panel, `cognitive_support` face | draft |
| `ADR-001-cognitive-support-profile.md` | intellectual disability as a profile: prompting strategies, measurement without floors, consent, regulation | accepted |
| `ADR-002-tenancy-and-tiering.md` | three axes, tiers as entitlements, quotas, the tenant owns learners | accepted |
| `PRICE_TEST.md` | five-conversation instrument for willingness to pay, with a decision rule | draft |
| `AGENT_M0.md` / `AGENT_M1.md` | build orders, six and seven increments, each with a stopping point | ready to hand over |
| `PROMPT_M0.md` | paste-ready kickoff for an agent with no repo access | ready |
| `AUDIT.md` | cross-document audit: 6 inconsistencies and 3 logical holes, all fixed | current |
| `NOTES.md` | decision log, must-not-claim list, corrections to earlier drafts | current |

---

## How to read the conventions

* **`must` / `never` / `is not` are deliberate.** They mark a decision that was argued and closed, not
  emphasis. If a later document contradicts one, that is a defect — see `AUDIT.md`.
* **Numbers marked "measured"** come from benchmarks of the v1 engine, with the method stated
  (`METERING.md` §1.1). Numbers marked **"assumption"** are assumptions and are labelled as such.
* **Anything in a "prohibited" or "do not" list** has a reason attached. Removing one requires an
  argument in `NOTES.md`, not a code change.
* **Claims about people are the narrow part.** The engine may say what it will do next and how sure it
  is; it may not diagnose, score a mind, or infer emotion (`SPEC.md` §2).

---

## Where the commercial decisions live

| Question | Document |
|---|---|
| what we sell and at what levels | `ADR-002` §2 |
| what a credit is and what it costs | `METERING.md` §2, §5 |
| what we may charge for | `METERING.md` §3 |
| how we price it | `PRICE_TEST.md` |
| who owns the learners | `ADR-002` §3, `PRIVACY.md` §2 |
| what happens to a learner's data | `PRIVACY.md` §5 |

---

## Current state

Planning is complete enough to build M0. Four decisions remain open and are listed in `SPEC.md` §10;
none of them blocks the first milestone.
