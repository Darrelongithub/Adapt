# ADR-001 — `cognitive_support`: intellectual disability as a profile, not a fork

Status: accepted (pending approval of `SPEC.md`)
Date: 2026-11-16
Supersedes: nothing. Constrains: every profile-scoped decision in v2.

---

## Context

Adapt's engine models how one learner learns and decides what to teach next. A meaningful share of
learners who need that most are adults with intellectual disabilities — in supported employment,
further education, adult literacy, independent-living programmes. A host product for them cannot use
an engine whose instruments, pacing, language and strategy set were designed for neurotypical adult
self-directed learners.

We considered three options:

1. **A separate product line and codebase.** Rejected: duplicates the observe/decide/evidence core,
   doubles the validation burden, and guarantees the two drift.
2. **A settings page on the default engine.** Rejected: "make the font bigger" is not accommodation.
   The evidence base, the metrics and the measurement rules are all different, and hiding that behind
   a toggle would produce confident nonsense.
3. **A profile: a versioned bundle over one engine.** Chosen.

Decision-maker constraints applied here: **adults only** (18+, enforced in code), **same engine**.

---

## Decision

`cognitive_support` is a **profile**: a versioned bundle of configuration and evidence that shares the
engine's core, database, API and loop, but changes what is measured, what is taught, what may be
claimed and what consent looks like.

```yaml
profile: cognitive_support
version: 1
audience: adults aged 18+ with intellectual and developmental disabilities
core: shared (observe, model, decide, evidence)
differs_in: [strategy_catalogue, metrics, instruments, consent_flow, claims_permitted,
            accessibility_contract, pacing, validation_method, default_abstention]
```

A profile is data plus gated code paths. It cannot add endpoints, fork the schema, or bypass the
approval gate.

---

## 1. What is measured instead of ability

The engine does **not** produce ability scores for this profile. Standardised ability tests floor out
for this population (floor scores sit far above the actual range), language and motor demands
contaminate the score and systematically underestimate ability, and single-case research (n=1–6) cannot
supply norms. Producing such a number would be both wrong and harmful.

Measured instead, on **individualised targets**:

| Measure | Definition | Role |
|---|---|---|
| Independence level | highest prompt level at which the learner succeeds | primary outcome |
| Prompt fading rate | change in required prompt level per session | what the engine optimises |
| Time to criterion | trials to criterion on the target | sensitive at any level |
| Errors to acquisition | errors before criterion | near-errorless procedures are the evidence base |
| Maintenance | performance after 1–3 weeks | real learning vs momentary performance |
| Generalisation | new materials, people, settings | the actual goal |
| Engagement/comfort | voluntary participation, breaks taken, requests to stop | a safety signal, never a target to maximise |

**Prohibited in this profile:** ability scores, percentiles, "mental age", comparisons to norms,
cross-learner ranking, diagnosis, screening, and any population inference from one case.

---

## 2. The strategy catalogue

The evidence base for moderate/severe intellectual disability is **instructional**, not
"cognitive training". The catalogue therefore consists of prompting, timing and decomposition
procedures — and the engine's job is to select, time and **fade** them.

| Strategy | Implementation in the engine | What the model learns |
|---|---|---|
| Constant time delay | 0 s delay with a controlling prompt, then a fixed 4–5 s delay | the delay at which independent responding emerges |
| System of least prompts | hierarchy of prompts, least intrusive first | least intrusive prompt that works, and when to step down |
| Task analysis / chaining | target decomposed into steps, forward or backward chaining | which step is the barrier; where to re-enter |
| Embedded trial instruction | trials distributed inside a meaningful activity | trials needed per context, and across contexts |
| Simultaneous prompting | model prompt delivered with the task on every trial, then faded | near-errorless acquisition rate |
| Visual supports / first-then | schedules, sequences, choice boards | which representation makes the step unambiguous |
| Chunking + break scheduling | 3–6 minute blocks, breaks offered | the block length at which accuracy holds |
| Response-mode adaptation | touch, audio, symbol selection, switch, partner-assisted | access vs ability confound |
| Text-to-speech / read-aloud | every instruction available as audio | reading load removed from the construct |
| Error correction | immediate, specific, non-punitive | error patterns per step |

Explicitly **not** in the catalogue: memory-training games, "brain training", speed drills presented
as cognitive improvement, and anything that raises difficulty for engagement.

Technology-aided instruction is treated as **promising but not established** for this population: it
may be used, it may not be cited as an evidence-based practice, and claims about it are marked `low`
certainty in the claims service.

---

## 3. Consent: the learner decides

There is no guardian path, because there are no minors in this product. The harder and more correct
question is **decision-making capacity**, which:

* is **presumed** — a diagnosis does not remove it;
* is **decision-specific** — capacity to consent to a learning app is not capacity to consent to
  surgery;
* is **supportable** — CRPD Article 12 requires support to exercise legal capacity, not substitution
  of judgement.

Implementation:

1. **The learner consents.** Easy-read consent, read aloud, one idea per screen, with a "tell me more"
   at each step. Comprehension checked with simple questions (not a literacy test).
2. **A supporter may be named** — a trusted person who helps understand and communicate. Their scope is
   recorded and limited: they can help the learner decide; they cannot decide instead, and they cannot
   see probe data unless the learner's consent names them as a data recipient.
3. **Consent is continuous.** Re-confirmed at each session start, in one tap. Any sign of reluctance,
   or an explicit "stop", ends the session immediately — no confirmation dialogue, no "are you sure".
4. **Dissent wins.** Withdrawal needs no reason and no co-signature. The learner may keep their learning
   plan while deleting probe data.
5. **Comfort is a signal, not a lever.** Requests to stop, breaks taken, session abandonment: recorded
   to reduce load, never optimised against.

---

## 4. Accessibility contract

The host renders; the engine specifies. Every instrument and decision carries machine-readable
accessibility requirements so the host can comply — and the engine refuses to serve an instrument to a
surface that declares it cannot meet them.

Contract fields: `response_modes[]`, `reading_level`, `instruction_audio_required`,
`time_limit_adjustable`, `max_targets_per_screen`, `distraction_constraints`,
`memory_demand: none|low`, `symbol_support`, `resumable: true`.

Baseline requirements (WCAG 2.2 AA plus W3C COGA guidance):

* consistent help and consistent navigation; no reliance on remembering previous screens;
* redundant entry — never ask for the same information twice;
* timing never forced: adjustable or extendable, with "more time" as a visible control;
* plain language, one idea per paragraph, no italics or all-caps, generous line spacing, short lines;
* errors reversible; confirmations for destructive actions; easy exit always available;
* text-to-speech for every instruction; captions for any audio;
* large targets, high contrast, no motion that cannot be stopped.

---

## 5. Regulation

| Instrument | Requirement | What it means for the build |
|---|---|---|
| EU AI Act Art. 5(1)(b) | prohibited: exploiting age/disability vulnerability to distort behaviour and cause harm | no streaks, no loss-aversion, no engagement maximisation, no dark patterns; "stop for today" is a first-class control |
| EU AI Act Art. 5(1)(f) | prohibited: emotion recognition in education | no camera, no microphone affect inference, no keystroke-emotion |
| EU AI Act Annex III 3(b) | education/assessment = high-risk | conformity work from M0: data governance, logging, human oversight, technical documentation, accuracy and robustness |
| CRPD Art. 24 / General Comment 4 | inclusive education, reasonable accommodation, individualised support; technology must not replace teacher interaction | the engine advises a supporter/teacher, never replaces them; the host must keep a human in the loop |
| Kenya DPA 2019 | disability and health data are sensitive; DPIA for profiling; ODPC registration; s.49 for export | DPIA before first pilot; explicit consent per scope; a documented local processor list |
| Kenya policy (2012 Basic Education Act Art. 44.3; 2009 SNE Policy Framework; 2018 Sector Policy for Learners and Trainees with Disabilities; KICD adapted curricula; EARCs) | assessment and adapted curricula are institutional functions | integration partners are adult education centres, TVET institutions, supported-employment programmes and NGOs — not generic "special needs" |

---

## 6. Validation method

Group-level psychometrics are unavailable (floor effects, small samples, heterogeneous population).
Use **single-case design**:

* multiple baseline across behaviours / settings / participants;
* repeated, frequent measurement of the target behaviour;
* visual analysis plus a randomisation test where feasible;
* replication count reported with every effect — "effective for 4 of 5 participants" is a claim this
  profile can support; "improves learning in intellectual disability" is not.

Claims are therefore **per learner and replicated**, not population-level, and the engine reports
`replications: n` next to any strategy effect in this profile.

Co-design is mandatory before the first pilot: learners with intellectual disabilities, support
workers and teachers review the consent flow, the language and the probe mechanics. Accessibility
audits are done with assistive technology actually in use (screen reader, switch access, AAC), not
simulated.

---

## 7. Consequences

**Positive.** One engine, one model, one evidence trail; a genuinely differentiated product for a
population the market serves badly; a validation story that is honest at its sample sizes; and the
profile forces the engine to separate *access* from *ability*, which improves the default profile too.

**Costs.** A second strategy catalogue with its own validation; instrument specs must carry
accessibility metadata from day one; consent flow is more work than a checkbox; the claims service
needs a curated subset for this population; pilots need institutional partners and longer timelines
(weeks per participant, not one session).

**Risks and mitigations.**

| Risk | Mitigation |
|---|---|
| Being read as a clinical/screening tool | `not_a_diagnosis` on every response, prohibited-claim templates, no ability scores in the schema |
| Facilitator-driven data being mistaken for independence | assistance recorded as a first-class field; independence metrics only from unassisted trials |
| Over-claiming from tiny studies | replication counts mandatory; `certainty: low` default for this profile's claims |
| Engagement pressure creeping in via "adherence" metrics | adherence is a diagnostic of our design, never a target for the learner |
| Scope creep back to minors | age gate at three points; reopening requires a new ADR and legal review |

---

## 8. Explicitly rejected

* IQ-style or ability scoring for this population, in any form, for any partner.
* "Cognitive training improves X" claims.
* Emotion/attention detection from camera or microphone.
* Autism/ID "screening" from interaction patterns.
* Any support for users under 18 in this release.
* Guardian-consent shortcuts that let an institution consent on a competent adult's behalf.
