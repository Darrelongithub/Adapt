# ADAPT audit fixlist — phase 1 baseline and phase 2 completion

Status: **completed on 2026-10-23**. All 12 FAIL/PARTIAL tickets have passing current checks. F04 study tools and G14 learner export are also built and passing. The current report records 61 PASS and 7 NOT RUN across the same 68 entries; no NOT RUN check was converted into a pass.

| Ticket | Checklist | Current verdict | Commit |
|---|---|---|---|
| FIX-001 | A01 | PASS | `a63867e` |
| FIX-002 | A06 | PASS | `939e23a` |
| FIX-003 | C02 | PASS | `611afad` |
| FIX-004 | C05 | PASS | `a52e8ad` |
| FIX-005 | C06 | PASS | `c2219ed` |
| FIX-006 | C09 | PASS | `ad067f3` |
| FIX-007 | D07 | PASS | `31ff728` |
| FIX-008 | E02 | PASS | `17ca977` |
| FIX-009 | E04 | PASS | `f48bd6f` |
| FIX-010 | G04 | PASS | `29bef0e` |
| FIX-011 | G09 | PASS | `9b6a587` |
| FIX-012 | I03 | PASS | `ef07091`, `628fdbb` |

The problem statements and required outcomes below are the preserved phase-1 baseline. Current purpose/steps/expected/actual/evidence records are in `AUDIT_REPORT.md`; phase-1 evidence remains in `audit_evidence/` alongside the new `fix-*` artifacts.

| Ticket | Priority | Checklist | Problem |
|---|---|---|---|
| FIX-001 | P0 | A01 / FAIL | Analytics consent is granted before consent |
| FIX-002 | P0 | A06 / FAIL | Protected routes auto-provision a guest |
| FIX-003 | P0 | C02 / FAIL | Learner plan exposes answer keys |
| FIX-004 | P0 | C05 / FAIL | Drag-order wrong answer becomes Correct |
| FIX-005 | P1 | C06 / FAIL | Subject refresh loses active work |
| FIX-006 | P0 | C09 / FAIL | Fabricated evidence type is stored |
| FIX-007 | P1 | D07 / PARTIAL | Successful review shortens the schedule |
| FIX-008 | P1 | E02 / PARTIAL | Geometry suggestion leads to generic fallback |
| FIX-009 | P1 | E04 / FAIL | Lesson reflection is discarded |
| FIX-010 | P0 | G04 / FAIL | Analytics opt-out does not stop event writes |
| FIX-011 | P1 | G09 / PARTIAL | Forwarding-header spoof bypasses connection pacing |
| FIX-012 | P2 | I03 / PARTIAL | Browser suites are stale or abort before target checks |

## FIX-001 — Do not grant analytics consent before the learner chooses

- **Priority/status:** P0 / fixed in `a63867e`; current check PASS.
- **Checklist:** A01 — FAIL.
- **Learner impact:** a clean visitor is already a learner with analytics enabled before seeing a consent decision.
- **Evidence/reproduction:** open a clean context. `access-browser.json` records automatic `POST /students` with `consent_analytics:true` and `/me.consent_analytics:true`.
- **Required outcome:** no learner or analytics-enabled session is created before the required consent action. Declining must not create a hidden analytics-enabled fallback account.
- **Smallest change direction:** remove the automatic creation/default-consent path at the entry boundary; keep the existing explicit guest registration path.
- **Regression test:** fresh-context browser test asserting zero `/students` request before consent, unchecked required consent, no analytics-enabled `/me`, and successful explicit guest entry afterward.

## FIX-002 — Require an explicit entry decision on protected routes

- **Priority/status:** P0 / fixed in `939e23a`; current check PASS.
- **Checklist:** A06 — FAIL.
- **Learner impact:** directly opening a learning URL silently creates an identity and bypasses the intended front door.
- **Evidence/reproduction:** visit the 16 signed-out routes in `access-browser.json`; all render as Guest learner instead of a gate.
- **Required outcome:** every protected route waits for explicit sign-in, registration, or guest entry; no automatic guest row is created merely by navigation.
- **Smallest change direction:** enforce the same entry gate in the shared session boundary rather than patching individual pages.
- **Regression test:** parameterized browser test for all 16 routes asserting the gate and zero creation request, followed by one explicit guest entry that unlocks navigation.

## FIX-003 — Remove correct answers from learner-facing test plans

- **Priority/status:** P0 / fixed in `611afad`; current check PASS.
- **Checklist:** C02 — FAIL.
- **Learner impact:** anyone can read the correct answer from the response and manufacture a perfect result.
- **Evidence/reproduction:** `subject-journey-browser.json` exposes `answer_index`; `round6-items-existing.log` also records `target_cells`, `answer_region`, `pairs`, `value`, and `shown` from plan data.
- **Required outcome:** plan responses contain only rendering/input data, never the correct answer or a value that directly reveals it; server grading still works.
- **Smallest change direction:** strip/separate grading data at the server response boundary instead of relying on the UI to hide fields.
- **Regression test:** recursively assert that all 12 plan types contain no answer-bearing key/value, then complete server-graded correct and wrong interactions to prove the split did not break grading.

## FIX-004 — Preserve and grade the learner’s actual drag order

- **Priority/status:** P0 / fixed in `a52e8ad`; current check PASS.
- **Checklist:** C05 — FAIL.
- **Learner impact:** a visibly wrong ordering can be reported as Correct, corrupting both feedback and evidence.
- **Evidence/reproduction:** `drag-order-browser.json` records Shape/Mix/Bake → Mix/Shape/Bake → reverted Shape/Mix/Bake within 300 ms and verdict Correct.
- **Required outcome:** the moved order remains visible, is the order submitted, and a wrong moved order scores wrong.
- **Smallest change direction:** fix the item’s controlled order/reset lifecycle; do not special-case the verdict.
- **Regression test:** real-pointer ordering test that moves an item, waits, asserts the new DOM order, submits it, and requires a wrong verdict plus matching stored answer.

## FIX-005 — Resume an active subject run after refresh

- **Priority/status:** P1 / fixed in `c2219ed`; current check PASS.
- **Checklist:** C06 — FAIL.
- **Learner impact:** an accidental refresh throws away the current check and forces the learner back to subject selection.
- **Evidence/reproduction:** `subject-journey-browser.json` answers one item, reloads, and records the subject-selection screen with no restored run.
- **Required outcome:** the same run, remaining items, cursor, and prior evidence resume after refresh without duplicate storage.
- **Smallest change direction:** persist a run identifier/cursor and restore it during journey initialization; retain the existing idempotent evidence write.
- **Regression test:** answer one item, reload, assert the next item and same run ID, finish, and verify exactly one evidence row per item.

## FIX-006 — Reject invented scored evidence

- **Priority/status:** P0 / fixed in `ad067f3`; current check PASS.
- **Checklist:** C09 — FAIL.
- **Learner impact:** untrusted clients can poison the record used for progress and adaptation.
- **Evidence/reproduction:** POST `item_type:made_up_type`, `correct:true`; API returns 200/`stored:1`. `db-final-verification.json` contains raw row `tevi_4e5a62d9f3c84e5f8eaa` with that type and `correct:true`.
- **Required outcome:** unknown scored types receive 422 and create no row; accepted rows remain readable and idempotent.
- **Smallest change direction:** validate each evidence row against the server’s registered scored-type allowlist before insertion.
- **Regression test:** API+database test for `made_up_type` (422, zero rows), missing type (422), valid row (stored once), and identical retry (zero duplicates).

## FIX-007 — Correct the successful-review schedule direction

- **Priority/status:** P1 / fixed in `31ff728`; current check PASS.
- **Checklist:** D07 — PARTIAL.
- **Learner impact:** telling the app “I still know it” can make the next review arrive sooner through a shorter half-life.
- **Evidence/reproduction:** `learning-loop-browser.json` records half-life 1.14→1.04 and due about 37 seconds earlier. `db-final-verification.json` confirms the check has `correct:1`, `score:1`, and stored half-life 1.041809.
- **Required outcome:** a successful check is recorded, but this positive result does not reduce half-life or pull the due time earlier under the same fixture.
- **Smallest change direction:** correct the successful-retention update/clamping logic, leaving lapse behavior unchanged.
- **Regression test:** reuse the recorded lag/expected-recall fixture; assert `correct:1`, reps increment, half-life does not decrease, and due does not move earlier. Add a lapse control to ensure the opposite path still shortens appropriately.

## FIX-008 — Deliver subject content for the Geometry suggestion

- **Priority/status:** P1 / fixed in `17ca977`; current check PASS.
- **Checklist:** E02 — PARTIAL.
- **Learner impact:** choosing a prominently suggested subject leads to generic study-skills material instead of that subject.
- **Evidence/reproduction:** `lessons-browser.json` records Geometry `topic_match:false`, generic concepts, and an offline fallback notice.
- **Required outcome:** the Geometry suggestion produces validated Geometry concepts and a non-generic first lesson, or the suggestion is not offered where no such lesson exists. Keep fallback disclosure whenever a fallback is genuinely necessary.
- **Smallest change direction:** connect the existing Geometry subject/curriculum content to the simple lesson path rather than adding a model dependency.
- **Regression test:** browser/API test selecting the actual Geometry suggestion, requiring `topic_match:true`, Geometry-specific concepts/text, and a usable check/reflection path.

## FIX-009 — Persist or submit lesson reflection text

- **Priority/status:** P1 / fixed in `f48bd6f`; current check PASS.
- **Checklist:** E04 — FAIL.
- **Learner impact:** writing entered into a lesson silently disappears and cannot contribute to learning or review.
- **Evidence/reproduction:** `lesson-reflection-browser.json` records text in `Your reflection (not marked)` and zero save/submission POSTs.
- **Required outcome:** reflection text is stored or submitted with the lesson and can be read back after completion/reload; “not marked” must not mean “discarded.”
- **Smallest change direction:** include the existing reflection field in the lesson-completion write, avoiding a new model call.
- **Regression test:** type a unique reflection, finish, reload/read the server record, and require exactly one saved value with no duplicate on repeated Finish.

## FIX-010 — Enforce analytics opt-out at event ingestion

- **Priority/status:** P0 / fixed in `29bef0e`; current check PASS.
- **Checklist:** G04 — FAIL.
- **Learner impact:** the privacy switch says analytics is off while new behavioral events continue to be stored.
- **Evidence/reproduction:** `security-browser-api.json` saves consent false then accepts a `pause` event. `db-final-verification.json` shows `consent_analytics:0` and raw sequence 4 with payload `consent off test`.
- **Required outcome:** nonessential analytics/learning telemetry is not written after opt-out, including direct API calls; essential security/account records must be distinguished explicitly rather than used as a blanket exception.
- **Smallest change direction:** enforce current consent in the server ingestion path, not only in the browser.
- **Regression test:** save consent false, POST the recorded `pause` event and representative events of every accepted type, then assert the documented refusal/drop response and zero new raw analytics rows. Repeat with consent true as a control.

## FIX-011 — Trust forwarding headers only from a trusted proxy

- **Priority/status:** P1 / fixed in `9b6a587`; current check PASS.
- **Checklist:** G09 — PARTIAL.
- **Learner impact:** account spraying can evade the shared-connection bucket by changing a client-supplied header, even though per-account and normal per-IP limits work.
- **Evidence/reproduction:** `rate-limit-api.json` records 50×401, request 51=429, another account on the same connection=429, then the same alternate account with new `X-Forwarded-For`=401.
- **Required outcome:** a direct client cannot reset its connection bucket by supplying a forwarding header; deployments behind a trusted proxy still identify the real client correctly.
- **Smallest change direction:** derive IP from the direct peer unless the peer is an explicitly trusted proxy; apply that rule consistently to middleware and auth buckets.
- **Regression test:** repeat the exact 51-request sequence and require 429 after spoofed headers. Add a trusted-proxy control and retain the per-account lockout test.

## FIX-012 — Repair stale browser suites and weak assertions

- **Priority/status:** P2 / fixed in `ef07091 + 628fdbb`; current check PASS.
- **Checklist:** I03 — PARTIAL.
- **Learner impact:** regression suites can be red for obsolete reasons or green without reaching the behavior they claim to protect, making real regressions easier to ship.
- **Evidence/reproduction:** `games-e2e-all.log` (4 pass/5 fail/2 skip), `journey-full-fake-e2e.log` (0/2), `round6-crawl.log` (four old gate failures), `round4-browser.log`, `frontend-probe.log`, and `journey-shell-stubbed.log` show old routes/selectors/consent setup. `word-budget.log` passes with missing tracked strings; `round6-items-existing.log` uses exposed answers.
- **Required outcome:** each suite reaches its stated target against the current learner flow, reports honest counts, and distinguishes harness mismatch from application failure. Do not weaken assertions merely to make them green.
- **Smallest change direction:** update setup/selectors to the current routes and explicit-entry behavior; replace internal-key/text and response-race assertions with user-visible and response-aware assertions; make missing word-budget strings fail.
- **Regression test:** run the repaired suites in desktop/mobile/keyboard as applicable and preserve independent negative fixtures for consent, answer secrecy, and evidence integrity. Record new counts in the audit report.

## Explicit exclusions

- **Still NOT RUN:** A08, E09, E10, F02, F03, G12, H04. These need the stated environment, elapsed-time, populated-fixture, or measurement work before a verdict can be reached.
- **Formerly NOT BUILT, now PASS:** F04 additional study tools (`45a3803`) and G14 learner export (`10402de`). Their current browser evidence is recorded in `AUDIT_REPORT.md`.
