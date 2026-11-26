# Planning notes

Decisions and warnings accumulated while planning v2. Kept because they are the kind of thing that
gets re-litigated or, worse, re-cited.

## Standing decisions

| Date | Decision |
|---|---|
| 2026-11 | v1 frozen at `fb655b3`, kept runnable as reference. v2 is a new `engine/` tree. |
| 2026-11 | Three understanding levels, plain: L1 what you've done, L2 how you learn, L3 what happens next. |
| 2026-11 | Probes sized by information gain with adaptive stopping, not a fixed clock. |
| 2026-11 | Adults only (18+), everywhere, enforced in code. **"Children" in conversation means adults** — a wording artefact, not a scope change. Reopening minors requires a new ADR and legal review. |
| 2026-11 | Intellectual disability is a **profile of the same engine**, not a separate product line. |
| 2026-11 | Evidence floor accepted: no claim until the reliability gate is met; `insufficient_evidence` is a valid answer. |
| 2026-11 | Engine-hosted: customer brings their app, contract, API key, credits. First host is a learning system. |

| 2026-11 | **Console-first**: the customer-facing product is a company-side console — connections, credit usage, configuration, learner inspector, decision replay. The learner UI belongs to the host (one reference implementation). |
| 2026-11 | Palette stays **blue on cream** (v1 identity), re-finished for operator density: 8px radii, hairlines, tabular numerals, serif only on page titles and public model cards. "Stylish" is a requirement, not a nice-to-have. |
| 2026-11 | Reference host: yes, we build it — it is the validation harness and the demo. |
| 2026-11 | Model cards are **public and unauthenticated** — they are the procurement document. |

| 2026-11 | **We do not operate learners.** The tenant is the controller and owns identity, accounts, rosters, content and learner support. The engine holds an opaque `learner_id` and a model. One contract per institution (a 2,000-learner school = one tenant). |
| 2026-11 | **Tiers are entitlements, not promises.** Core/Plus/Deep unlock levels; they never imply coverage. A construct only serves L3 where its gates are passed, and the coverage matrix is published with every tier (`ADR-002`). |
| 2026-11 | **Abstention is a headline metric** with rate + counts + reason mix + explicit denominator, at both request and construct grain, and it is never counted as error or refusal (`METRICS.md` §1). |

| 2026-11 | **Three axes, never collapsed:** entitlement (contract) ≠ capability (code) ≠ validation (gates). Served level = min of the three, with `limited_by` naming the binding constraint. Inspector shows validated; coverage matrix shows entitlement. |
| 2026-11 | Tiers: basic confidence/uncertainty in **Core** (transparency is not a paywall); calibration *analysis*, strategy and probes in **Plus**; prospective calibration, longitudinal validation and intervention effects in **Deep**. Deep purchasable by any eligible tenant — gated by validation, not by a partnership. Quotas included, packs add-on, no surprise overage. |
| 2026-11 | Metering is **derived, not asserted**: weights 1 / 3 / 0 with a CI drift check. Refits are cohort-batched nightly (a single-learner fit is not identifiable), events are quota-governed, **abstentions are free**, paid credits never expire. |
| 2026-11 | Pricing principle: **customers pay for capabilities and capacity, never for the engine admitting uncertainty.** Abstentions free at every tier, including abstained probes. |
| 2026-11 | **80 credits = one reference learner-month bundle**, a commercial reference (not a consumption promise, not a throttle). Currency price from willingness-to-pay and margin, tested with customers — never derived from compute. Probes are a **Plus** entitlement. Monthly billing first; annual terms with included-allowance-only rollover after the pilot. |
| 2026-11 | **Cost-accounting correction (caught in review):** the first METERING draft claimed ≈4 s of CPU per 2,000 learners. Correct figure ≈18.2 CPU-minutes — 270× higher — because refits (99% of compute) were classified free to bill and then wrongly excluded from cost as well, abstentions were dropped from the decides count (they compute but are not billed), and probe scoring was never added into the sum. Logged in `METERING.md` §1.3. Rule: **a cost that is not billed is the easiest cost to forget, and usually the largest.** |
| 2026-11 | **Pseudonymity is not anonymity.** We are a processor of personal data; a processor receiving pseudonyms inherits the full obligation (`PRIVACY.md`). Correction: erasure now deletes prediction records too, not "detached" ones. |

| 2026-11 | **Spec audit** (`AUDIT.md`): 6 textual inconsistencies and 3 logical holes, all fixed. The two that mattered: abstention rate alone can hide an empty product (added **served rate**, always shown with it), and erasure of prediction records means calibration must be aggregated incrementally rather than recomputed. |
| 2026-11 | **Integration options, not one path**: raw REST, thin generated SDK (TS + Python), framework adapters, drop-in probe embed, LMS plugin later. SDKs stay thin; a live key never enters a browser; time-to-first-call <15 min is a release gate. |

## Must-not-claim list (each has evidence behind it)

* Cognitive training produces far transfer — **null** (g≈0.05–0.12; ≈0.03 with active controls).
* A game score equals an ability — game-based assessments correlate r≈0.45–0.56 with established
  measures; usable for prediction, not for labelling a person.
* One task measures one construct — n-back and complex span correlate r≈0.20.
* Browser reaction times are comparable to lab or across devices — they are not.
* Emotion or attention inference from camera/mic in education — **prohibited**, AI Act 5(1)(f).
* Behaving in a way that exploits disability or age vulnerability to drive engagement — **prohibited**,
  AI Act 5(1)(b).
* Standardised ability tests measure adults with intellectual disabilities — they floor out and
  language/motor demands contaminate the score.
* Technology-aided instruction is an *established* practice for intellectual disability — it is
  **promising**; the established practices are constant time delay, embedded trial instruction,
  task analysis, system of least prompts and simultaneous prompting.

## Retraction warning

**Do not cite the MDPI paper "Universal Design for Learning for Children with ADHD"
(PMC10453933) — it is retracted.** It surfaced during the UDL search and is exactly the sort of
plausible-looking source that ends up in a design rationale. This is the reason `CLAIMS.md` makes a
retraction check mandatory at ingestion.

## What the research changed (2026-11-16)

Seven corrections and constraints that came out of checking the plan, several of which were real
defects rather than refinements.

| Area | Finding | Change made |
|---|---|---|
| RLS | `ENABLE ROW LEVEL SECURITY` alone is bypassed by the table owner; session-level `SET` leaks tenant context across pooled connections; `SET LOCAL` outside a transaction silently does nothing | `SPEC.md` §6 and `AGENT_M0.md` increment 2 require FORCE, `WITH CHECK`, `SET LOCAL` in an explicit transaction, a non-superuser/non-owner role, and an index on `tenant_id` |
| Random tie-breaks are nondeterminism | Event tails were ordered `(ts, id)` with `id` a random UUID, so "the last three answers" was a coin flip between equal timestamps and the same corpus abstained `conflicting_signals` 0–3 times on identical input. Any ORDER BY whose tie-break is a random identifier makes a deterministic engine non-reproducible. Order by `(ts, created_at, seq, id)` — a total order over data that does not move | found in M1 increment 5, after a golden test could not be reproduced |
| Prefix entropy | A 12-character key prefix leaves ~4 secret characters, ~1M values; 1,000 keys per test session gives ~40% collision probability, and an unhandled collision is a 500 to a customer. Either widen the prefix or retry inside a savepoint — and test the retry, because it only fails under load | `AGENT_M0.md` §3 |
| Honesty machinery vs success | `straight_line` was implemented as "8+ identical outcomes in a row", which is what a learner who is getting things right looks like. `decide` then abstained `data_quality` on them — 9 of 33 abstentions in the M1 measurement were learners doing well. A validity flag built from *correctness* punishes the fastest-improving users with silence. Flags must be derived from behaviour that is uninformative (`response` patterns, latency), never from success | `AGENT_M1.md` §2, `METRICS.md` §1.3, M1 increment 4 |
| Examples are specifications | `INTEGRATION.md` §6B showed `validation_level: "L0"` next to `served: "L1"`, which the documented `min()` cannot produce. An example in a contract is read as truth by whoever implements against it — and this one was caught by the build agent, not by review of the doc | `INTEGRATION.md` §6B; resolves into platform-level L1 gate vs per-tenant L2/L3 gates |
| The guard you assume is load-bearing | In the M1 mastery estimator the hard cap on served mastery was assumed to be what protects a guesser from being called competent. Sabotage showed it trims about a point; **shrinkage toward the cohort prior** does the work, and removing shrinkage breaches the 0.7 line where removing the cap barely moves it. Every guard gets sabotaged separately, and the test names which one it is actually testing | `AGENT_M1.md` §8 G2, M1 increment 3 |
| Vacuity | A gate that asserts "we are safe" goes quietly green the day the implementation changes. The guesser suite also asserts that the *documented limit* is still reachable, so improving the estimator forces an explicit doc update instead of a silent pass | `AGENT_M1.md` §8 G2.4 |
| Parsers vs documents | Adding the §6A endpoint inventory made `check_openapi.py` read the word `operational` out of the inventory table as a documented error code — 17 codes became 18, silently, because the parser scanned every table in the file. A document that is machine-read must be parsed **by section**, and a new table in it must be assumed hostile until a test says otherwise. Found by the build agent in M1 increment 1 | `AGENT_M0.md` §6; fixed in `check_openapi.py` with `test_only_the_error_section_defines_codes` |
| Guard patches | `check_openapi.py` applying `docs-patches/*.diff` before comparing makes the contract guard self-serveable: the implementation writes the patch, the patch amends the authoritative table, and rule (c) passes. The doc is authoritative as written; a new code is a reviewed doc change, and patches are an artifact for a human to apply | `AGENT_M0.md` §6; `minor_not_supported` added to `INTEGRATION.md` §6 by the doc owner |
| Envelope header | A ciphertext header naming the **master key that wrapped the data key at write time** pins every row to that master key forever: a re-wrap does not rewrite rows, so the retired key can never be dropped and startup must keep accepting it. The master kid belongs on the wrapped data key row (which re-wrap updates); the value header carries no master key, and a data key version later if rotation needs it. Acceptance is a *retirement* test, not a decryption test — re-wrap, delete the old key, restart, read an old row | `AGENT_M0.md` §5, increment 5 review |
| AAD scope | AAD of `tenant_id` alone allows a ciphertext to be swapped between two learners of the same tenant, or between two columns of one row, and decrypt into wrong data silently. AAD binds `tenant_id` + internal learner id + column name | `AGENT_M0.md` §5, increment 5 review |
| RLS × `SECURITY DEFINER` | **Measured, increment 3:** a `SECURITY DEFINER` function owned by the table owner sees **1 row** with `ENABLE` alone and **0 rows** with `ENABLE + FORCE`. So a tenant-scoped key table cannot be read by its own resolver, and the increment-2 rule and the increment-3 resolver are mutually exclusive for that table | `api_keys` is the documented exception: resolver-only access, **no `adapt_app` table privileges at all**, `ENABLE` without `FORCE`, asserted by `has_table_privilege`. RLS keeps tenants apart; grants keep key material from everyone — `AGENT_M0.md` §3 |
| RLS, corrected | An earlier draft of this row claimed `USING` without `WITH CHECK` lets a session write rows into another tenant. **That is wrong**: when `WITH CHECK` is absent PostgreSQL applies `USING` to new rows. `WITH CHECK` is still required — it makes the write constraint explicit and lets it be *narrower* than the read rule, and a later broadening of `USING` for a read reason would otherwise widen writes silently. The sabotage that proves the test has teeth is `WITH CHECK (true)`, not a missing clause | found by the build agent in increment 2; `AGENT_M0.md` increment 2 and `PROMPT_M0_INC2.md` corrected |
| RLS + pooler | In transaction-mode pooling, tenants can share generic query plans | `plan_cache_mode=force_custom_plan` required if a pooler is introduced |
| Adaptive stopping | Standard practice is a minimum *and* maximum, e.g. PROMIS 4–12 items with `SE(θ) < 0.32`; aggressive standard-error-reduction rules cut items but degrade measurement most for respondents at the extremes | Floors in `MEASUREMENT.md` keep minimum item counts; never optimise item count at the tails — that is exactly where `cognitive_support` learners sit |
| Easy Read | First evidence-based standard (Jan 2026, Newton Project, 100+ people with learning disabilities): real photographs preferred 58% vs ~8% drawings vs ~11% line symbols; ≥16pt; ≤15-word sentences; 50–100 words per screen; ≥4.5:1 contrast | `UI.md` §5 face rules; symbol-free engine, licensed assets stay with the host |
| WCAG gap | Studies with users with intellectual disabilities find only 30–50% of their actual accessibility problems are covered by WCAG | `UI.md` mandates design patterns (no scrolling, ≤3 options, 44px targets, audio) beyond conformance |
| European Accessibility Act | Its service list is closed (e-commerce, banking, e-books, transport, telecoms, audiovisual); a B2B engine API is not in scope, but customers' consumer apps may be | We ship the accessibility contract, a statement template and a conformance report, and gate instruments on declared compliance |
| Digital Fairness Act (proposed Nov 2025 / 2026) | Art. 17 would require users to disable "compulsive or excessive use" features; the draft also targets streaks and disengagement penalties, explicitly catching education apps | No streaks, no loss-framing, no return-notifications; an "engagement features off" switch as a setting, and equal-effort stop/cancel |
| Symbols | ARASAAC is CC BY-**NC**-SA (non-commercial); Mulberry is CC BY-SA; SymbolStix/PCS/Widgit are commercial | Engine ships no symbol assets — only a `symbol_set` field and a licence registry |
| Uncertainty display | Intervals/error bars require statistical literacy; frequency framing and plain words work better for lay readers | Console shows intervals with `n`; learner surfaces show three-band words; `cognitive_support` shows no numeric uncertainty at all |

## Confusions to avoid

* **Personalisation is not profiling.** Adapt models *this learner's* response to methods; it does not
  classify people into types. No learning styles, no "visual learner", no brain types.
* **Adherence is a diagnostic of the design, not a target for the learner.** Especially in
  `cognitive_support`, where pressure to continue is a prohibited pattern.
* **The privacy allowlist is a product feature**, not a compliance chore: it is enforceable in code and
  shown to the user verbatim.
* **Licensing gates the roadmap.** ICAR is academic-use only; PROMIS is free-with-attribution but
  health-domain; IPIP is public domain; Pearson/CANTAB are commercial contracts. Our instruments must
  be ours or public domain, validated by us.
