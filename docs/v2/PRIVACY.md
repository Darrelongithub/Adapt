# Privacy design

Written after a fair challenge: **opaque learner IDs do not solve privacy.** They reduce exposure;
they do not change the legal status of the data. This document replaces any earlier hand-waving with a
position, a data map, and the controls that have to exist in code.

---

## 1. Position

> **We process personal data. We are a processor. Pseudonymity is a safeguard, not an exit.**

The law is not ambiguous on this. Pseudonymised data is still personal data in the hands of the party
that can attribute it, because attribution is possible with additional information held separately
(GDPR Art. 4(5), Recital 26; Kenya DPA 2019 s.2). And the "relative" reading of the *SRB* judgment —
where data may stop being personal for a recipient who genuinely cannot re-identify — **does not help a
processor**: the EDPB's draft guidelines assess identifiability from the controller's perspective, so
data that is personal for the school remains personal for us. [(1)](https://dat.lt/en/anonymised-data-when-the-gdpr-stops-applying/), [(2)](https://lifebit.ai/blog/anonymisation-vs-pseudonymisation-gdpr/)

Two consequences follow, and they are the spine of this document:

1. **Every obligation attaches**: lawful basis, purpose limitation, minimisation, retention, security,
   access, erasure, breach notification, and records of processing.
2. **Pseudonymity is still worth doing** — it reduces blast radius, supports minimisation, and is
   explicitly the kind of safeguard the law encourages. It is a security measure, not a compliance
   shortcut.

The honest summary for a customer's DPO: *"We hold a model of a person under a code we cannot resolve
to that person. That is pseudonymous personal data, we treat it as such, and here is the data map."*

---

## 2. Roles, and the one thing we never accept

| Party | Role | Holds |
|---|---|---|
| The school / institution | **Controller** | identity, accounts, rosters, consent records, the mapping from `learner_id` to a person |
| Adapt | **Processor** | `learner_id`, events, model state, decisions, probe trials |
| Sub-processors (hosting, email, error tracking) | **Sub-processor** | listed publicly, no learner data except where unavoidable and named |

* A data processing agreement is part of every contract, not a negotiation.
* **We never receive the mapping.** No names, no emails, no rosters, no admission numbers, no
  national IDs. `learner_id` is validated on ingest (`^[a-z0-9][a-z0-9_-]{7,63}$`, rejecting `@`, `+`
  and any 7+ digit run) and rejected with a message telling the integrator not to send PII.
* Cross-border: Kenya DPA s.49 requires appropriate safeguards; EU transfers use SCCs plus a transfer
  impact assessment. Storage region is a contractual field, not a footnote.

---

## 3. Data map

| Data | Class | Purpose | Default retention |
|---|---|---|---|
| `learner_id` | pseudonymous identifier | linkage within tenant | life of the learner record |
| Learning events (item, response, timestamp, device class) | personal data | model state, decisions | 24 months |
| Probe trials + validity flags | personal data | construct estimates | 36 months |
| Model state (mastery, method effects, calibration) | personal data (derived) | the product | recomputed; snapshots 12 months |
| Decision records + citations | personal data (derived) | audit, replay, dispute | 24 months |
| Consent records (scope, version, timestamp) | personal data | lawful basis evidence | life + 6 years |
| Credit ledger | commercial, no learner linkage | billing | 7 years |
| Aggregates (counts, rates, calibration summaries) | **not personal data** once k-anonymised | reporting, benchmarks | indefinite |

Rules that follow from the map:

* **No free-text fields that can carry a person.** Event payloads are schema-validated against an
  allowlist of keys (the v1 pattern, kept), and forbidden keys are rejected at the door rather than
  scrubbed later.
* **No screen content, no images, no audio, no keystroke geometry, no device fingerprinting.**
* **No third-party analytics or ad SDKs anywhere near learner data** — the console uses
  first-party metrics only.
* **Profiling is named as profiling.** Kenya's DPA defines profiling to include evaluating a person's
  *disability* and *health status*, which is precisely what the `cognitive_support` profile does.
  That profile therefore requires a DPIA before the first pilot, per tenant (s.31), not a paragraph in
  a policy.

---

## 4. Access control

| Control | Implementation |
|---|---|
| Tenant isolation | Postgres RLS, forced, `USING` + `WITH CHECK`, tenant set with `SET LOCAL` per transaction. Exception: `api_keys` is reachable only through one `SECURITY DEFINER` resolver and the runtime role holds **no** privilege on the table — key material is protected by grants, not by RLS |
| Encryption at rest | per-tenant data key, envelope-encrypted, AAD bound to `tenant_id`; rotation supported |
| Encryption in transit | TLS 1.3 only; HSTS; no plaintext fallback |
| Least privilege | app role is not owner, not superuser, no `BYPASSRLS`; migrations run as a separate role |
| Staff access to learner data | **none by default.** Support access requires a tenant-granted, time-boxed, per-tenant approval; two-person rule; every read logged |
| Break-glass | separate credential, 24-hour expiry, mandatory post-hoc review, logged in the audit trail the tenant can read |
| Logs | structured allowlist; never a payload, key, `learner_id` in plaintext, or provider name |
| Backups | encrypted with the same envelope; restore drill quarterly; deleted data ages out within 30 days |

The audit trail is **visible to the tenant** in the console. A processor whose access log is private is
asking to be trusted rather than audited.

---

## 5. Retention and deletion

* Retention is configured per data class, per tenant, inside the platform's maximum. Lower is allowed,
  higher is not.
* A daily purge job enforces it; the console shows *what it deleted and when*, so retention is
  observable rather than aspirational. ODPC guidance is explicit that retention schedules are expected,
  not optional. [(3)](https://www.odpc.go.ke/wp-content/uploads/2025/11/ODPC-%E2%80%93-Guidance-Note-on-Processing-by-MSMEs.pdf)
* **Erasure is a tenant API call**, not a support ticket: `DELETE /v1/learners/{id}`.

Erasure semantics, stated precisely because they are easy to get wrong:

| Deleted immediately | Kept | Why |
|---|---|---|
| events, probe trials, decisions, model state, consent records for that learner | aggregate counters that no longer relate to a person | recalibration of the model's own claims must survive, but only in non-identifying form |
| prediction records (prospective claims) | the **count** of predictions issued, and aggregate calibration statistics once k-anonymised | a prediction about a deleted person is still data about them |

This corrects an earlier draft that proposed keeping "detached" prediction records for audit. Detached
or not, it is data about a person after erasure. It goes. What survives is arithmetic.

* Because prediction records are deleted, **calibration statistics are aggregates maintained at serve
  time**, not a cache of deleted rows (`MEASUREMENT.md` §9). If a future feature needs prediction-level
  history, it needs a new lawful basis and a new DPIA — not a quiet change of storage policy.
* The call returns a **deletion receipt**: what was deleted, counts, timestamp, and a hash of the
  audit entry. Backups age out within 30 days and the receipt says so.
* **Export** (`GET /v1/learners/{id}/export`) returns everything held, in a documented JSON schema, so
  the tenant can answer a data subject access request without asking us for a file.

---

## 6. Aggregates, benchmarks, and training

* Benchmark views and published metrics require **≥5 contributing tenants**, so no single customer's
  learners are inferable. (This mirrors the k-threshold already used in `METRICS.md` §1.7.)
* Kenya's DPA asks controllers and processors using personal data for commercial purposes to anonymise
  where possible (s.37(2), s.39(2)) — our benchmarks are built from aggregates only, never from
  record-level data.
* **No cross-tenant model training by default.** Opt-in is contractual, per tenant, revocable, and
  applies only to aggregate statistics — never to an identifiable learner's events.
* No training on learner data to build third-party models. No sale, ever, in any form.

---

## 7. Breach and incident

* Internal detection → tenant notified within **24 hours** of confirmation (ahead of the 72-hour
  statutory window, because the tenant is the one who must notify the ODPC or a supervisory authority).
* Breach record includes scope by tenant, data classes affected, and whether re-identification was
  possible — the last question is the one that decides the notification duty.
* Public post-incident report for anything touching learner data. Not optional.

---

## 8. What to tell a school's DPO

A short, checkable answer — this is a sales document as much as a compliance one:

1. We are your **processor**; you remain the controller and hold identity.
2. Here is the **data map** (§3) and the **sub-processor list**.
3. Here is the **DPIA template** we will complete with you before the pilot.
4. Here is **erasure and export**, self-serve, with receipts.
5. Here is our **retention schedule**, and the platform maximums.
6. Here is the **audit trail of our own access** to your data.
7. Pseudonymous IDs are a safeguard; we do not claim anonymity, and we will not let a procurement
   document claim it either.

If any of those seven is missing at the time of a sales call, the answer is "not yet", never a
reassurance.

---

## 9. Refused, permanently

* PII in any learner identifier, event payload, or webhook.
* Emotion, affect or attention inference from camera, microphone, or input dynamics.
* Biometrics of any kind.
* Screen recording, keystroke logging, reading-time-based attention scoring.
* Third-party analytics, ad, or session-replay tooling touching learner data.
* Selling or licensing learner data in any aggregated or disaggregated form.
* Training on learner data outside an explicit, revocable, per-tenant opt-in.

---

## 10. Open items

1. DPIA template: write it before the first institutional conversation, not after.
2. Transfer mechanism for a Kenya-first region if one exists; otherwise EU + s.49 safeguards.
3. Retention defaults — 24 months for events is a starting position, not a decision.
4. Whether `cognitive_support` requires **explicit** consent (sensitive data) on top of the general
   consent scopes. I think yes, and it should be a separate toggle.
