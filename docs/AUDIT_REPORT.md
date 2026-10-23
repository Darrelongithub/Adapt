# ADAPT functional audit — phase 1 baseline and phase 2 remediation

Phase-1 date: 2026-10-22  
Phase-1 target: commit `c4bddc4deb70c658873c66831a4fa3fa990dbda9`  
Phase-1 mode: real `adapt host`, simple mode, no model credential, fresh `ADAPT_DATA_DIR=/tmp/audit-data`  
Phase-2 date: 2026-10-23  
Phase-2 mode: real production `adapt host`, fresh `ADAPT_DATA_DIR=/tmp/adapt-phase2`, Chromium at `/tmp/chromium`; scripted-fake model evidence is labelled separately  
Application changes: phase 1 changed no application code. Phase 2 made the separately committed fixes recorded below.

## Method and verdict rules

Checks used real browser interaction, API responses, and offline SQLCipher queries. Screenshots supplement those records; they do not establish a verdict by themselves. Model-dependent checks are **NOT RUN** because no key was configured. The scripted fake-model suite is reported as a suite result, never as real model-mode evidence.

- **PASS**: the stated check ran and met its expected result.
- **PARTIAL**: part worked, but a material part failed or had the wrong effect.
- **FAIL**: the stated behavior failed.
- **FAKE**: the product presented simulated behavior as real. No check required this verdict.
- **NOT RUN**: not exercised; the reason is stated.
- **NOT BUILT**: the feature or route was absent. Counted separately, not converted into a pass.

Exploratory harness mistakes and stale assertions are listed in `audit_evidence/harness-corrections.md`. Raw failed assertions are preserved where they help explain the correction; the verdicts below use the actual responses, not the bad assertion. Unless a full path is shown, evidence filenames below are relative to `docs/audit_evidence/`; screenshot filenames are in its `screenshots/` subdirectory.

## Phase-2 current verdict counts

The phase-1 records below are preserved as the baseline. This section supersedes the verdicts for the 12 remediated FAIL/PARTIAL checks and the two NOT BUILT features. Across the same 68 entries, the current counts are **61 PASS, 0 PARTIAL, 0 FAIL, 0 FAKE, 7 NOT RUN, 0 NOT BUILT**. A PASS below applies only to the stated executed check.

| Area | PASS | PARTIAL | FAIL | FAKE | NOT RUN | NOT BUILT | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| A. Access and sessions | 7 | 0 | 0 | 0 | 1 | 0 | 8 |
| B. Baseline and placement | 6 | 0 | 0 | 0 | 0 | 0 | 6 |
| C. Subject journey and item types | 10 | 0 | 0 | 0 | 0 | 0 | 10 |
| D. Practice, progress, changes, review | 7 | 0 | 0 | 0 | 0 | 0 | 7 |
| E. Lessons, animator, voice | 8 | 0 | 0 | 0 | 2 | 0 | 10 |
| F. AI/key behavior and study tools | 2 | 0 | 0 | 0 | 2 | 0 | 4 |
| G. Privacy, security, database | 13 | 0 | 0 | 0 | 1 | 0 | 14 |
| H. Robustness and mobile | 5 | 0 | 0 | 0 | 1 | 0 | 6 |
| I. Existing suites | 3 | 0 | 0 | 0 | 0 | 0 | 3 |
| **Total** | **61** | **0** | **0** | **0** | **7** | **0** | **68** |

### Phase-2 check records

**A01 — Fresh visitor consent before account creation — PASS**  
Commit `a63867e`. Purpose: prevent account creation from granting analytics by default. Steps: run fresh-browser default-off and explicit-on entry, then call account creation with consent omitted and send telemetry before and after explicit opt-in. Expected: no pre-choice creation, omitted consent false, telemetry refused until opt-in. Actual: both browser contexts sent zero creation requests before choice; payload, response, and `/me` matched the selected false/true value. Omitted API consent returned false and rejected one event; explicit opt-in enabled one event. Evidence: `fix-a01-consent-browser.json`, `fix-a01-consent-api.json`.

**A06 — Protected routes require an entry decision — PASS**  
Commit `939e23a`. Purpose: stop navigation from silently provisioning a learner. Steps: visit 17 protected routes signed out, count creation requests, then explicitly enter as a guest. Expected: current consent gate on every route, zero creation before entry, one after. Actual: all 17 routes reached `auth_step:consent`; counts were zero then one, and `/learn` unlocked. Evidence: `fix-a06-access-entry-current.json`, `fix-a06-access-entry-current.log`.

**C02 — Learner-facing plan hides answers — PASS**  
Commit `611afad`. Purpose: keep server grading secrets out of public plans without breaking scoring. Steps: request five subject-family plans covering all 12 item types, recursively inspect grading fields, submit invalid answers, read storage receipts, and replay them. Expected: no scoring key, server-side false verdicts, one row per answer, no replay duplicate. Actual: 24 items across 12 types had no forbidden grading fields; all 24 returned HTTP 200/`correct:false`, `stored:1`, then `replay_stored:0`. Rendering-only task inputs such as the number-line target and quick-recall study list remain public; the scoring answers do not. Evidence: `fix-c02-server-grading-api.json`, `fix-c02-c05-c06-c09-backend.log` (50 passed).

**C05 — `drag_order` retains and grades the chosen order — PASS**  
Commit `a52e8ad`. Purpose: grade the order actually shown and submitted. Steps: move the first item down, wait, read the DOM order, submit, and read evidence. Expected: moved order persists and a wrong order scores false with the same stored answer. Actual: the moved order remained `Derive…, State…, Conclude…, Assume…`; the verdict was `Not this time`, and the evidence answer exactly matched that order with `correct:false`. Evidence: `fix-c05-drag-order-browser.json`.

**C06 — Subject-run refresh resumes active work — PASS**  
Commit `c2219ed`. Purpose: restore owned in-progress work without client-only run state or duplicates. Steps: answer one item, reload, restore again in a fresh browser profile with the same session, finish, and repeat the final batch. Expected: one plan, owned run restoration, identical results, five rows, zero duplicate storage. Actual: one plan POST and two owned run reads restored `item-1`; fresh-profile result bars were identical, five rows were stored, and the final replay returned `stored:0`. No subject-run localStorage key was used. Evidence: `fix-c06-subject-resume-browser.json`.

**C09 — Server rejects fabricated scored item types — PASS**  
Commit `ad067f3`. Purpose: prevent clients from inventing scored evidence or authoritative metadata. Steps: submit an invalid grading seal and then forge type, skill, verdict, and answer with a valid seal. Expected: invalid seal 422/no row; authored metadata and server grading win over forged fields. Actual: the invalid seal returned 422 with zero stored rows. The forged row was stored as authored `geometry_angle`/`geometry.spatial`, not `made_up_type`, and server grading changed the claimed true verdict to false. Evidence: `fix-c09-fabricated-evidence-api.json`.

**D07 — Successful review does not shorten scheduling — PASS**  
Commit `31ff728`. Purpose: make a successful retention check non-damaging while retaining lapse behavior. Steps: reuse the recorded retention fixture, submit a success, then submit a lapse control. Expected: success stores and increments reps without reducing half-life or moving due earlier; lapse may shorten. Actual: success kept half-life at 12 hours, moved reps 2→3, and did not move due earlier. The lapse control reduced half-life to 0.5 hours and increased lapses. Evidence: `fix-d07-review-direction-api.json`.

**E02 — Suggested subjects have authored first lessons — PASS**  
Commit `17ca977`. Purpose: prevent a prominent suggestion from leading to generic fallback content. Steps: read the actual suggestion controls and request the first lesson for every suggestion. Expected: each offered subject has matched authored content, or is not offered. Actual: Geometry is no longer suggested. Exactly four suggestions remain—Discrete structures, Kiswahili, Chemistry, Biology—and every returned lesson has `topic_match:true` and `source:authored`. This does not claim that arbitrary Geometry input now has an authored lesson. Evidence: `fix-e02-subject-suggestions-browser.json`.

**E04 — Biology reflection is saved and restored — PASS**  
Commit `f48bd6f`. Purpose: stop discarding learner writing at lesson completion. Steps: enter a unique reflection, finish, reload, read journey state, and repeat completion. Expected: exact text persisted and repeat completion idempotent. Actual: the restored input and `journey/state` note exactly matched the submitted reflection; the UI completion POST occurred once and the repeat request returned 200 without changing the saved note. Evidence: `fix-e04-lesson-reflection-browser.json`.

**F04 — Additional study tools — PASS (built)**  
Commit `45a3803`. Purpose: provide functional learner-authored study tools rather than dead controls. Steps: create a note and flashcard, reload, reveal the answer, export, delete the note, reload again, and try a blank save. Expected: exact persistence, working reveal/export/deletion, and invalid blank input blocked. Actual: note and card restored, the exact answer revealed, both appeared in export, deletion survived reload, and blank save was disabled. The migration downgrade/upgrade/check also passed. Evidence: `fix-f04-study-tools-browser.json`, `fix-f04-migration.log`.

**G04 — Analytics opt-out stops server telemetry writes — PASS**  
Commit `29bef0e`. Purpose: enforce consent at ingestion for every accepted telemetry type. Steps: batch all 27 event types after opt-out, submit an essential graded attempt, then restore consent. Expected: no post-opt-out telemetry write, essential grading remains functional, explicit opt-in resumes events. Actual: opt-out returned `accepted:0`/`rejected:27` and the event count stayed at one; the attempt returned 200 with no event-count change. Restoring consent accepted one event and increased the count to two. Evidence: `fix-g04-consent-telemetry-api.json`.

**G09 — Forwarding headers cannot bypass direct-client pacing — PASS**  
Commit `9b6a587`. Purpose: trust forwarding headers only from configured proxies. Steps: repeat 50 failed sign-ins, request 51, try another account, then spoof `X-Forwarded-For`; run trusted-proxy unit controls separately. Expected: the direct connection remains limited and trusted-proxy parsing has explicit coverage. Actual: the sequence was 50×401 followed by 429 for request 51, the other account, and the spoofed request. Six trusted-proxy/config tests passed. Evidence: `fix-g09-trusted-proxy-api.json`, `fix-g09-trusted-proxy-backend.log`.

**G14 — Learner data export — PASS (built)**  
Commit `10402de`. Purpose: provide a learner-controlled, credential-safe export. Steps: populate session, attempt, events, journey, and reflection data; download export; inspect keys; request it signed out. Expected: populated learner data, no credentials/seals, unauthenticated refusal. Actual: export contained one session, one attempt, three events, one journey row, one auth-session record, and the exact reflection. Password, CSRF, token, grading-seal, email-index, and cookie values were absent; signed-out export returned 401. Evidence: `fix-g14-learner-export-browser.json`.

**I03 — Existing browser-suite usefulness and maintenance — PASS**  
Commits `ef07091`, `628fdbb`. Purpose: make every named browser suite reach current, interpretable assertions. Steps: repair and run games, scripted-fake full journey, Round 6 crawl/items, Round 4, real/mock UI probes, banned-copy, word-budget, and journey drivers. Expected: no stale-route/consent aborts and no assertion weakened into a product pass. Actual: games 9 passed/2 intentional skips; scripted-fake journey 2/2; Round 6 crawl 90 clean checks plus rapid switching; item driver 12/12 plus both subject runs; Round 4 functional gates passed; real UI probe 286/286 and mock probe 281/281; journey 30/30; banned-copy and 20/20 tracked-word checks passed. Evidence: `fix-i03-games-e2e-all.log`, `fix-i03-journey-full-scripted-fake.log`, `fix-i03-round6-crawl.log`, `fix-i03-round6-items.log`, `fix-i03-round4-browser-results.json`, `fix-i03-ui-probe.log`, `fix-i03-ui-probe-mock.log`, `fix-i03-banned-phrases.log`, `fix-i03-word-budget.log`, `fix-i03-journey.log`, `harness-corrections.md`.

### Phase-2 suite reruns

| Suite | Current result | Qualification / evidence |
|---|---|---|
| Backend pytest | **1493 passed, 2 skipped, 0 failed** | Full run, 139.42 s; `fix-phase2-backend-pytest.log` |
| Frontend Node + contract/typecheck | **73 passed, 0 failed; contract/typecheck PASS** | Engine 15, lib 49, server 2, env 2, dev 5; `fix-phase2-frontend-tests.log` |
| Games Playwright | **9 passed, 2 skipped, 0 failed** | Desktop, mobile, keyboard; skips are intentional desktop-only snapshot duplicates; `fix-i03-games-e2e-all-results.json` |
| Full journey, scripted fake model | **2 passed, 0 failed** | Desktop/mobile integration only; not real-model evidence; `fix-i03-journey-full-scripted-fake.log` |
| Round 6 crawl | **90 clean page checks + rapid-switch PASS** | 15 routes × two viewports × three session states; `fix-i03-round6-crawl.md` |
| Round 6 items | **12/12 lab types + Geometry/Discrete runs PASS** | Public interactions, server verdicts, stored evidence; `fix-i03-round6-items.log` |
| Round 4 production functional gates | **PASS** | 51 routes, four journeys, two returning journeys, six auth modes, zero recorded page errors; art NOT RUN; `fix-i03-round4-browser-results.json` |
| UI probes | **Real 286/286; mock 281/281** | 283 and 280 API calls respectively; `fix-i03-ui-probe.log`, `fix-i03-ui-probe-mock.log` |
| Journey | **30/30 PASS** | Desktop/mobile server-state, skip, reload, navigation checks; `fix-i03-journey.log` |
| Banned copy | **PASS** | Static scan plus 17 routes signed out/in and public privacy in both states; `fix-i03-static-banned-phrases.log`, `fix-i03-banned-phrases.log` |
| Word budget | **20/20 strings; four screens PASS** | All tracked strings must exist and remain within 40 words; `fix-i03-word-budget.log` |

### Phase-2 explicit limitations

- A08, E09, E10, F02, F03, G12, and H04 remain **NOT RUN** for the original environment, elapsed-time, audio, real-model, populated-fixture, or measurement reasons. No real model credential is configured. The scripted-fake journey is not real-model evidence.
- Round 4 art overlap and gzip verdicts remain **NOT RUN** in functional mode. Their observations are preserved in `fix-i03-round4-browser-results.json`; no visual remediation or art PASS is claimed.
- Phase-1 failures, stale counts, and evidence are retained below as history. They are not current verdicts where a phase-2 record above supersedes them.

## Phase-1 verdict counts (baseline)

There are **68 checklist entries**: 66 entries across PASS/PARTIAL/FAIL/FAKE/NOT RUN, plus 2 separately identified **NOT BUILT** entries.

| Area | PASS | PARTIAL | FAIL | FAKE | NOT RUN | NOT BUILT | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| A. Access and sessions | 5 | 0 | 2 | 0 | 1 | 0 | 8 |
| B. Baseline and placement | 6 | 0 | 0 | 0 | 0 | 0 | 6 |
| C. Subject journey and item types | 6 | 0 | 4 | 0 | 0 | 0 | 10 |
| D. Practice, progress, changes, review | 6 | 1 | 0 | 0 | 0 | 0 | 7 |
| E. Lessons, animator, voice | 6 | 1 | 1 | 0 | 2 | 0 | 10 |
| F. AI/key behavior and study tools | 1 | 0 | 0 | 0 | 2 | 1 | 4 |
| G. Privacy, security, database | 10 | 1 | 1 | 0 | 1 | 1 | 14 |
| H. Robustness and mobile | 5 | 0 | 0 | 0 | 1 | 0 | 6 |
| I. Existing suites | 2 | 1 | 0 | 0 | 0 | 0 | 3 |
| **Total** | **47** | **4** | **8** | **0** | **7** | **2** | **68** |

## Ten highest-impact learner problems

1. **A fresh visitor is enrolled with analytics consent before consent.** Automatic guest creation sends `consent_analytics:true`; all 16 tested signed-out routes provision a guest instead of waiting for an entry decision (A01, A06).
2. **The subject-test plan gives away answers.** Fields including `answer_index`, `target_cells`, `answer_region`, `pairs`, `value`, and `shown` reach the learner (C02).
3. **`drag_order` can mark a wrong answer Correct.** A moved order visibly reverts to the original order and the submitted result is Correct (C05).
4. **Clients can fabricate scored evidence.** `made_up_type` returned HTTP 200/`stored:1`; the raw row says `correct:true` (C09).
5. **Turning analytics off does not stop event writes.** A later `pause` event was accepted and is physically present with payload `consent off test` (G04).
6. **Refreshing subject checks discards the active run.** The learner returns to subject selection and the answered item is not resumed (C06).
7. **Lesson reflection discards learner writing.** The Biology reflection accepts text but emits no save or submission request (E04).
8. **A successful review moves the schedule in the wrong direction.** `correct:1`/`score:1` coincided with half-life 1.14→1.04 hours and an earlier due time (D07).
9. **Connection rate limiting trusts spoofable forwarding headers.** After the connection bucket returned 429, adding `X-Forwarded-For` changed the result back to a normal 401 (G09).
10. **A suggested Geometry subject does not get a Geometry lesson.** The app returns generic study-skills concepts with `topic_match:false`; it does disclose the fallback (E02).

## Phase-1 summary table (baseline)

These are phase-1 area rollups, not additional checklist entries or current phase-2 verdicts.

| Area | What worked | What did not | Area result |
|---|---|---|---|
| Access/session | Registration validation, returning login, logout, guest persistence | Consent and explicit entry are bypassed by auto-provisioning | **FAIL** |
| Baseline/placement | Full desktop/mobile/keyboard baseline; resume; honest unmeasured state; eight graded placement answers | No functional failure in the executed checks | **PASS** |
| Subject/items | Relevant plans, 12 correct interactions, 11 wrong-answer types, evidence read-back, idempotency | Answer exposure, drag-order false Correct, refresh loss, fabricated evidence | **FAIL** |
| Practice/changes/review | Real queue/help/receipts/mastery, proposal gates, accept/revoke, real progress/evidence | Successful review shortens the schedule | **PARTIAL** |
| Lessons/voice | Animator, authored Biology text, reteach/completion, player grading/resume, typed voice turn | Geometry fallback, discarded reflection; audio unrun | **FAIL** |
| AI/study tools | Simple mode is disclosed and local Q&A works | Real model mode unrun; extra study tools absent | **NOT RUN** for model mode; **NOT BUILT** for extra tools |
| Privacy/security/database | IDOR/CSRF/XSS/SQL-injection defenses, transport guards, soft-delete access revocation, populated hard purge, SQLCipher | Consent-off writes continue; forwarding-header bypass; export absent | **FAIL** |
| Robustness/mobile | Delayed double-submit, offline/retry, route stress, rapid navigation, no unexpected page errors | Whole-app tap-target sizing was not separately measured; baseline mobile target checks passed | **PASS** for executed checks; global targets **NOT RUN** |
| Existing suites | Backend and frontend unit suites pass; baseline browser runs pass | Several browser harnesses are stale or abort before their target checks | **PARTIAL** |

## Phase-1 existing suite results and weak-test observations

### Raw suite counts

| Suite | Result | Evidence / qualification |
|---|---|---|
| Backend pytest | **1442 passed, 2 skipped, 0 failed** | `backend-pytest.log`, `backend-pytest.xml`; 132.82 s |
| Frontend Node tests | **70 passed, 0 failed** | engine 15, lib 46, server 2, env 2, dev 5; `frontend-suite-summary.log` |
| Frontend contract + TypeScript lint | **PASS** | `frontend-lint.log` |
| Games Playwright, all projects | **4 passed, 5 failed, 2 skipped** | `games-e2e-all.log`, `games-e2e-all-results.json` |
| └ Full baseline | **desktop PASS, mobile PASS, keyboard PASS** | Each completed every game and server-side recording checks |
| └ Snapshot honesty | **1 passed, 1 failed, 2 skipped** | Failure expects internal text `locked_box`; page shows human-readable “trying the locked box” and “not measured” |
| └ Subject round | **0 passed, 4 failed** | All four stop on removed `/subject` route (404), before subject assertions |
| Full journey, scripted fake model | **0 passed, 2 failed** | Desktop/mobile stop at the old journey-card selector; no real model claim; `journey-full-fake-e2e.log` |
| Round 6 route crawl | **80 clean page checks, 4 failed, rapid-switch PASS** | 84 checks; four failures are old signed-out gate expectations; `round6-crawl.log` |
| Round 6 item driver | **12/12 correct-answer interactions PASS** | Also completed Geometry and Discrete runs and stored evidence; `round6-items-existing.log` |
| Browser banned-copy crawl | **17 passed, 1 setup failure** | Signed-out 16 routes + public privacy passed; signed-in crawl did not start; `banned-phrases-browser.log` |
| Static banned-copy scan | **PASS** | `banned-phrases-static.log` |
| Word-budget script | **4/4 PASS** | It passes with removed strings untracked; see below; `word-budget.log` |
| UI probe, real and mock | **1 passed then crash in each** | Both wait for the old mandatory account card; all later checks NOT RUN |
| Round 4 browser gates | **0 completed; setup crash** | Old account-gate wait; `round4-browser.log` |
| Stubbed journey shell script | **0 completed; setup crash** | Waits for eight old journey rows; `journey-shell-stubbed.log` |
| Placement, response-aware rerun | **9 passed, 0 failed** | Eight HTTP 200 attempts + rendered receipts + Finish/analyse; `placement-browser.json` |
| Demo script | **Incomplete** | Placement/practice/proposal ran, then script crashed at `methods.labels.unit`; `learning-loop-api.log` |
| Other standalone probes | **NOT RUN** | `art-placement.mjs`, `art-zones.mjs`, `round6-shots.mjs`, and `round6-topo.mjs` are visual/art probes outside scope; `banned_phrases.mjs` is an alternate standalone variant, while the package-entry `banned-phrases.mjs` and static scan above were run |

### Why green suites did not clear the product

- The 12-type correct-answer driver reads answer-bearing plan fields and uses them to answer. It proves interaction mechanics, not secrecy or independent assessment integrity.
- `subject-round.spec.ts` still opens `/subject`; its four failures are 404/setup failures, not four additional subject-learning failures.
- `snapshot-honesty.spec.ts` requires the internal key `locked_box` even though the learner-facing text honestly names the unplayed locked-box factor as not measured.
- `journey-full` and `journey.mjs` expect the old eight-row journey card. The current subject wizard does not contain that selector, so lesson/model stages were never reached.
- Round 6, Round 4, the UI probes, and the banned-copy signed-in setup assume a fresh visitor must see the old consent gate. Auto-guest provisioning makes those harnesses abort or label the guest flow incorrectly.
- The standalone placement script raced response rendering and reported “never sent” for early questions. The response-aware rerun recorded eight HTTP 200 responses and eight receipts.
- The word-budget script does not fail when tracked sentences disappear: Journey passed with 2/5 strings present and First lesson with 5/7.
- The 1442 backend passes and 70 frontend passes did not detect the live consent, answer-exposure, drag-order, fabricated-evidence, reflection, review-direction, or forwarding-header failures. Unit-suite green is not evidence that those behaviors work.

## Phase-1 full checklist (baseline)

### A. Access and sessions

**A01 — Fresh visitor consent before account creation — FAIL**  
Purpose: prevent data creation and analytics enrollment before an explicit choice. Steps: open a clean browser context and record account requests and `/me`. Expected: consent gate, zero automatic learner creation. Actual: automatic `POST /students` with `as_guest:true` and `consent_analytics:true`; `/me` returns a guest with analytics enabled. Evidence: `audit_evidence/access-browser.json`, `screenshots/auto-guest-before-consent.png`.

**A02 — Consent controls and research field — PASS**  
Purpose: make consent deliberate and avoid storing an undeclared research answer. Steps: after signing out in the same document, inspect checkbox/agree state, decline, and register with the optional research control. Expected: unchecked required consent, disabled Agree, explanatory Decline with no additional creation, no research field in the creation payload. Actual: all matched. Evidence: `access-browser.json`, `screenshots/access-declined.png`.

**A03 — Registration validation and duplicates — PASS**  
Purpose: reject unusable credentials and account collisions. Steps: submit weak and blank passwords through UI/API and duplicate a username. Expected: native UI validation, API 422, duplicate 409. Actual: all matched. Evidence: `access-browser.json`.

**A04 — Login, returning login, and logout — PASS**  
Purpose: establish and revoke real sessions without breaking returning access. Steps: create an account, sign out, try a wrong password, sign in again, and inspect `/me`. Expected: readable wrong-password error, same learner ID on return, 401 after logout. Actual: all matched. Evidence: `access-browser.json`, `learning-loop-browser.json`.

**A05 — Guest persistence and guest logout — PASS**  
Purpose: preserve a guest across refresh but not after explicit logout. Steps: refresh a guest, sign out, then provision the next guest. Expected: same ID before/after refresh; old session 401 after logout; different next guest. Actual: all matched. Evidence: `access-browser.json`.

**A06 — Protected routes require an entry decision — FAIL**  
Purpose: keep learning routes from silently creating an identity. Steps: visit 16 signed-out routes. Expected: account/consent gate. Actual: every route renders as an automatically provisioned Guest learner. Evidence: `access-browser.json`.

**A07 — Registered dashboard and navigation — PASS**  
Purpose: keep all main sections reachable for a real account. Steps: crawl the registered routes and dashboard links; repeat the 11 main routes on mobile. Expected: content, no 404/error boundary, real learner data. Actual: all registered routes rendered; mobile results are in H03/H04. Evidence: `access-browser.json`, `robustness-browser.json`.

**A08 — Natural session expiry — NOT RUN**  
Purpose: verify that a session stops working at its TTL. Steps not run: the advertised 12-hour lifetime was not waited out and the database clock was not manipulated. Expected: expired cookie receives 401. Actual: unverified; a garbage token does receive 401 under G08. Evidence: `transport-security-api.json` covers only the garbage-token case.

### B. Baseline and placement

**B01 — Full desktop baseline — PASS**  
Purpose: exercise every mind-check game and its server record. Steps: run the existing desktop baseline bot through all games and the next-day check. Expected: every served game completes and recorded verdict/choice matches the submission. Actual: one end-to-end test passed in 2.1 minutes. Evidence: `games-e2e-all.log`, `games-e2e-all-results.json`.

**B02 — Full mobile and keyboard-only baseline — PASS**  
Purpose: keep the complete baseline operable by touch and keyboard. Steps: run the full mobile project and keyboard-only project. Expected: all games complete; keyboard actions send each trial once; mobile layout/tap checks pass. Actual: both passed. Evidence: `games-e2e-all.log`.

**B03 — Immediate stop and unmeasured factors — PASS**  
Purpose: avoid turning unplayed games into zero abilities. Steps: start the baseline and stop immediately; run the existing immediate-stop snapshot test. Expected: all eight game factors `n:0`/not measured. Actual: all eight remained unmeasured; existing desktop test passed. Evidence: `preferences-baseline.json`, `baseline-existing-desktop-results.json`, `games-e2e-all.log`.

**B04 — Baseline reload/resume — PASS**  
Purpose: preserve a partially completed mind check. Steps: answer through `codewords_study`, reload the run, and read it back. Expected: same task ID, 2/13 steps complete, explicit resume notice. Actual: same task and cursor returned. Evidence: `preferences-baseline.json`.

**B05 — Format preference reaches lesson plan — PASS**  
Purpose: apply the learner’s diagram-versus-text choice to actual plan output. Steps: create diagram and text learners, obtain the same fallback concept plan, compare blocks. Expected: diagram learner opens a visual block; text learner opens text; valid IDs/why and no template placeholders. Actual: first block differed as requested; plans use `fact_ids` and `why`, with no unresolved markers. The content is generic fallback, not proof of authored-topic adaptation. Evidence: `preferences-baseline.json`, `harness-corrections.md`.

**B06 — Placement answers and analysis — PASS**  
Purpose: grade and record a real placement sequence. Steps: answer all eight served questions, await each `/attempts` response and receipt, then Finish and analyse. Expected: eight HTTP 200 grading responses and an evidence-based completion view. Actual: eight responses/receipts plus analysis passed. This plan served seven numeric items and one teachback; MCQ itself was exercised separately in practice. Evidence: `placement-browser.json`, `placement-browser.log`, `screenshots/placement-final.png`.

### C. Subject journey and item types

**C01 — Subject plan and relevant code-scored queue — PASS**  
Purpose: build a real subject-specific starting check. Steps: choose Geometry, request a plan, and inspect item types/content. Expected: five valid scored items with Geometry-relevant skills. Actual: five items were served and completed. Evidence: `subject-journey-browser.json`, `screenshots/subject-results.png`.

**C02 — Learner-facing plan hides answers — FAIL**  
Purpose: prevent trivial answer extraction. Steps: inspect Geometry and 12-type plan responses for answer-bearing fields. Expected: no correct answer/key in learner payloads. Actual: `answer_index` and type-specific keys including `target_cells`, `answer_region`, `pairs`, `value`, and `shown` are exposed. Evidence: `subject-journey-browser.json`, `round6-items-existing.log`.

**C03 — Correct interactions for all 12 item types — PASS**  
Purpose: ensure every registered item widget can be answered and scored. Steps: drive all 12 types in the test lab with their correct interaction. Expected: each scores Correct and stores evidence. Actual: 12/12 scored Correct. This is a mechanics check; it used the exposed answers noted in C02. Evidence: `round6-items-existing.log`.

**C04 — Wrong-answer scoring for 11 non-ordering types — PASS**  
Purpose: ensure an incorrect interaction is not rewarded. Steps: submit wrong answers to pattern pick, drag match, odd one out, truth table, Venn, both geometry types, number line, fraction bar, cloze, and quick recall. Expected: wrong verdict, nonzero timing, hint accounting. Actual: all 11 scored wrong with timing and one hint. Evidence: `item-types-wrong-browser.json`.

**C05 — `drag_order` retains and grades the chosen order — FAIL**  
Purpose: grade the order the learner actually made. Steps: move Shape/Mix/Bake to Mix/Shape/Bake, wait, and submit. Expected: moved order persists and differs from the correct sequence, so it scores wrong. Actual: immediate moved order reverted within 300 ms; verdict was Correct; the control did not persist. Evidence: `drag-order-browser.json`, `item-types-wrong-browser.json`, `screenshots/drag-order-after-move.png`.

**C06 — Subject-run refresh resumes active work — FAIL**  
Purpose: avoid losing an in-progress check. Steps: answer one item, read evidence, reload, and compare state. Expected: active run and answered item resume without loss. Actual: page returns to “What do you want to learn?”; no active run is restored. Evidence: `subject-journey-browser.json`.

**C07 — Subject evidence storage and read-back — PASS**  
Purpose: connect completed answers to real learner records. Steps: complete five Geometry items and read evidence/dashboard/progress. Expected: five rows with actual type, skill, score, and timing. Actual: five rows were returned and displayed. Evidence: `subject-journey-browser.json`, `db-final-verification.json`.

**C08 — Duplicate subject-evidence submission is idempotent — PASS**  
Purpose: prevent double-submission from duplicating scored rows. Steps: POST the identical evidence batch twice. Expected: second request stores zero duplicates. Actual: HTTP 200, `stored:0`. Evidence: `subject-journey-browser.json`.

**C09 — Server rejects fabricated scored item types — FAIL**  
Purpose: protect evidence integrity from client-invented results. Steps: POST a row with `item_type:made_up_type` and `correct:true`; query SQLCipher offline. Expected: 422 and no row. Actual: HTTP 200/`stored:1`; raw row `tevi_4e5a62d9f3c84e5f8eaa` exists with `correct:true`. Evidence: `subject-journey-browser.json`, `db-final-verification.json`.

**C10 — Missing item type and malformed client plan handling — PASS**  
Purpose: reject malformed assessment data without rendering a broken card. Expected: missing `item_type` returns 422; malformed client plan shows an error and zero item cards. Actual: both matched. Evidence: `subject-journey-browser.json`.

### D. Practice, progress, changes, and review

**D01 — Chosen subject yields a relevant practice queue — PASS**  
Purpose: practice the subject the learner selected. Steps: choose/activate Biology, complete setup, open Learn. Expected: nonempty Biology/cell queue without an answer key. Actual: three items shared the chosen topic ID, including `itm_bio_photosynthesis_00`; no `answer`, `correct_index`, or `answer_spec` key appeared. Evidence: `practice-browser.json`, `harness-corrections.md`.

**D02 — Practice draft, hint, and worked example — PASS**  
Purpose: preserve work and provide functional help. Steps: enter an unsubmitted draft, reload, open A hint and Worked example. Expected: draft restored and real help displayed. Actual: all matched. Evidence: `practice-browser.json`.

**D03 — Wrong practice answer, mastery receipt, and Back — PASS**  
Purpose: grade server-side and preserve the resulting receipt. Steps: submit `0.999` for the chlorophyll question, inspect receipt, go Next then Previous. Expected: Not yet, grading method, real mastery update, receipt retained. Actual: `TEXT_MATCH`, mastery 0.20→0.08/`n=1`, and the receipt remained on Back. Evidence: `practice-browser.json`.

**D04 — Simple-mode Q&A and session analysis — PASS**  
Purpose: provide honest local help and analysis without a model. Steps: ask why an answer is wrong and finish the session. Expected: local answer with disclosure, then a real analysis or honest insufficient-evidence result. Actual: answer said nothing left the machine; Finish showed “Nothing to propose” with zero hypotheses. Evidence: `practice-browser.json`, `screenshots/practice-analysis.png`.

**D05 — Adaptation proposal decision gates — PASS**  
Purpose: prevent silent or cross-site adaptation. Steps: generate a proposal, attempt apply with no decision and without CSRF, then use the UI decision. Expected: pending initially, 422 without decision, 403 without CSRF, one active row only after explicit approval. Actual: all matched. Evidence: `learning-loop-browser.json`, `screenshots/proposal-applied.png`.

**D06 — Revoke, progress, evidence, and returning data — PASS**  
Purpose: make approved changes reversible and reports evidence-backed. Steps: revoke the active adaptation and open Progress/Evidence/dashboard after returning. Expected: active row ends, real estimates/report appear, dashboard counts match stored data. Actual: all matched. Evidence: `learning-loop-browser.json`, `db-final-verification.json`, `screenshots/progress-real-evidence.png`.

**D07 — Successful review strengthens or at least does not shorten scheduling — PARTIAL**  
Purpose: update spaced repetition in the correct direction. Steps: answer “I still know it,” inspect before/after due data and raw retention rows. Expected: a recorded successful check and no earlier due time caused by a reduced half-life. Actual: check stored with `correct:1`, `score:1`, reps 2→3, but half-life fell 1.14→1.0418 and due moved about 37 seconds earlier. The update happened; its direction is wrong. Evidence: `learning-loop-browser.json`, `db-final-verification.json`, `harness-corrections.md`.

### E. Lessons, animator, and voice

**E01 — Animator controls and reduced motion — PASS**  
Purpose: make visual explanations controllable and accessible. Steps: Step, Play, Pause, Replay, substitute a topic, and emulate reduced motion. Expected: captions advance, pause holds, replay resets, reduced motion shows a static final frame without autoplay. Actual: all matched. Evidence: `lessons-browser.json`.

**E02 — Suggested Geometry receives subject-specific first lesson — PARTIAL**  
Purpose: teach the chosen subject rather than generic study skills. Steps: request Geometry concepts and first lesson. Expected: Geometry-matched content. Actual: `topic_match:false`; concepts are Read the task/Notice information/Choose a method/Check result. An explicit offline fallback notice is shown, so the limitation is disclosed but the subject lesson is not delivered. Evidence: `lessons-browser.json`.

**E03 — Authored Biology lesson content — PASS**  
Purpose: serve real subject content where authored material exists. Steps: request Biology concepts and the cell lesson. Expected: topic match and non-generic Biology text. Actual: `topic_match:true`, four real Biology concepts, and cell-specific text/fact IDs. Evidence: `lessons-browser.json`, `harness-corrections.md`.

**E04 — Biology reflection is saved or submitted — FAIL**  
Purpose: avoid discarding learner writing. Steps: type a reflection in the visible `Your reflection (not marked)` input and monitor POST traffic. Expected: the text is saved, submitted, or explicitly used. Actual: input retained only in the DOM; no save/submission request occurred. Evidence: `lesson-reflection-browser.json`.

**E05 — Reteach and lesson completion — PASS**  
Purpose: provide another explanation and record completion. Steps: request reteach and finish the first lesson. Expected: a real additional moment with changed content/method and saved confirmation. Actual: both matched. Evidence: `lesson-reflection-browser.json`.

**E06 — Interactive player prediction, grading, resume, and review — PASS**  
Purpose: run the computed lesson rather than a canned animation. Steps: open binary search, answer the frame-1 prediction incorrectly, reload, and finish. Expected: real frames, gated prediction, backend grading, server resume, honest review. Actual: eight frames; “about n/2” graded wrong; reload resumed frame 2; review recorded 0/1, expected `about log2(n)`, status finished. Evidence: `lesson-player-browser.json`, `screenshots/lesson-player-review.png`.

**E07 — Voice websocket and typed tutor turn — PASS**  
Purpose: provide a functioning text fallback on the voice screen. Steps: connect the learner websocket and send an editable text turn. Expected: hello/config frames and a tutor reply grounded in local material. Actual: both matched; reply said it came from the learner’s own material. Evidence: `voice-browser.json`, `screenshots/voice.png`.

**E08 — Voice off blocks voice actions — PASS**  
Purpose: make the voice switch functional. Steps: disable voice and attempt Send/Hold to talk. Expected: controls disabled and no outgoing text frame. Actual: both controls disabled and no frame sent. Evidence: `voice-browser.json`.

**E09 — Real microphone dictation — NOT RUN**  
Purpose: verify actual speech-to-text, not merely API presence. Reason: Chromium exposed recognition/media APIs but no audio input device; server reported `local_stt:false`. Expected/actual dictation: unverified. Evidence: `voice-browser.json`.

**E10 — Audible speech output — NOT RUN**  
Purpose: verify that a learner can hear the reply. Reason: `speechSynthesis` existed with zero installed voices; server reported `local_tts:false` and browser returned `speak_unavailable`. Expected/actual audible output: unverified. Evidence: `voice-browser.json`.

### F. AI/key behavior and study tools

**F01 — No-key simple mode is functional and disclosed — PASS**  
Purpose: avoid pretending a remote model exists. Steps: run config status/test, read health, use local lesson/Q&A paths. Expected: no configured credential, explicit simple mode, no external-model claim. Actual: config reports no Gemini/YouTube credential; health says simple mode and nothing is sent anywhere; Q&A discloses local processing. Evidence: `config-status.log`, `practice-browser.json`, `voice-browser.json`.

**F02 — Real model-mode lessons and grading — NOT RUN**  
Purpose: test provider-backed written lessons, upgrades, and model grading. Reason: no model key was configured. The scripted fake-model suite is not a substitute. Expected/actual provider behavior: unverified. Evidence: `config-status.log`; fake-suite limitation in `journey-full-fake-e2e.log`.

**F03 — Model-provider prompt-injection resistance — NOT RUN**  
Purpose: test hostile learner text through a real provider path. Reason: no key. Simple-mode injection handling passed separately under G07; real provider behavior is unverified. Evidence: `security-browser-api.json`, `config-status.log`.

**F04 — Additional study tools — NOT BUILT**  
Purpose: identify flashcards, notes, planner, calculator, timer, or similar tools if present. Steps: crawl all learner navigation and 11 main mobile routes. Expected: exercise any such tools found. Actual: no dedicated additional study-tool surface was present; review, hints, worked examples, and lessons were audited in their own sections. Evidence: `round6-crawl.log`, `robustness-browser.json`.

### G. Privacy, security, and database

**G01 — Cross-tenant object access (IDOR) — PASS**  
Purpose: stop one learner reading another’s objects. Steps: use learner B’s session against learner A’s subject/baseline/object IDs. Expected: refusal, not cross-tenant data. Actual: access was refused. Evidence: `security-browser-api.json`.

**G02 — CSRF on mutations — PASS**  
Purpose: prevent cross-site state changes. Steps: mutate preferences without a CSRF token and attempt adaptation without CSRF. Expected: 403 and no mutation. Actual: both refused; neither adaptation was activated. Evidence: `security-browser-api.json`, `learning-loop-browser.json`.

**G03 — Telemetry allowlist — PASS**  
Purpose: block surveillance fields and unknown event types. Steps: submit allowed events with forbidden fields and an unknown event. Expected: allowlisted data only, unknown event rejected/dropped. Actual: filtering worked as expected. Evidence: `security-browser-api.json`.

**G04 — Analytics opt-out stops server telemetry writes — FAIL**  
Purpose: honor a saved privacy choice. Steps: save `consent_analytics:false`, send a `pause` event with reason `consent off test`, then query offline. Expected: no stored post-opt-out event. Actual: API accepted it; raw sequence 4 remains with that payload despite the student’s consent flag being 0. Evidence: `security-browser-api.json`, `db-final-verification.json`.

**G05 — Stored display-name XSS — PASS**  
Purpose: treat stored markup as text. Steps: save an XSS-shaped display name and render account/dashboard surfaces. Expected: no script execution or injected DOM. Actual: payload remained inert. Evidence: `security-browser-api.json`.

**G06 — SQL-injection login resistance — PASS**  
Purpose: prevent authentication bypass through SQL-shaped identifiers. Steps: submit injection-shaped username/password input. Expected: normal authentication failure. Actual: no bypass occurred. Evidence: `security-browser-api.json`.

**G07 — Simple-mode prompt-injection handling — PASS**  
Purpose: keep hostile instruction-shaped learner text as data when no model is used. Steps: submit injection text through the simple tutor path. Expected: no instruction execution or false provider claim. Actual: handled as data. Real provider injection remains F03/NOT RUN. Evidence: `security-browser-api.json`.

**G08 — Transport, malformed input, and invalid-session guards — PASS**  
Purpose: reject hostile or malformed requests before they become learner state. Steps: use an untrusted Host, malformed JSON, garbage session, and an 8 MiB+1 body. Expected: 400, 422, 401, and 413 respectively. Actual: all matched; security headers were also recorded. Evidence: `transport-security-api.json`, `security-browser-api.json`.

**G09 — Effective rate limits resist brute force and spoofing — PARTIAL**  
Purpose: pace failed sign-ins and account spraying. Steps: read `/quotas`, make 51 failed sign-ins, try another account from the same connection, then supply a new `X-Forwarded-For`. Expected: 50 failures then 429; the same direct connection remains limited regardless of its claimed forwarding header. Actual: the 51st returned 429 with Retry-After and another account from the same IP was limited; spoofing the header returned a normal 401 and bypassed the connection bucket. Per-account limiting still works. Evidence: `rate-limit-api.json`.

**G10 — Soft delete revokes access and follows documented retention — PASS**  
Purpose: make a deleted account unusable immediately while preserving the documented soft-delete semantics. Steps: soft-delete a disposable learner, try old session/new login, then inspect raw rows. Expected: 401 for both access paths; physical residue only as documented. Actual: access revoked; learner has `deleted_at`, one auth session, one learning session, and two events. Evidence: `security-browser-api.json`, `db-final-verification.json`.

**G11 — Hard purge removes populated learner-linked rows — PASS**  
Purpose: exercise the explicit right-to-be-forgotten CLI. Steps: with the host stopped, snapshot a disposable learner, run `adapt db purge-student --student-id … --confirm`, and query every table with a learner ID column. Expected: populated learner rows removed. Actual: before rows existed in 11 tables (student, auth, baseline, events, subject, learning session, lesson plan/run, session state, preferences, subject-test evidence); after, learner and all those rows were absent. Evidence: `purge-account-final.json`, `db-purge-final-before.json`, `db-purge-final-cli.log`, `db-purge-final-after.json`.

**G12 — Hard-purge cascade for attempts and adaptation proposals — NOT RUN**  
Purpose: verify those specific cascades with populated before rows. Reason: the final disposable fixture did not contain attempts or adaptation proposals; their zero counts were therefore not reported as proof. Expected/actual cascade behavior for those populated types: unverified. Evidence: `db-purge-final-before.json` documents the populated scope.

**G13 — Database file encryption — PASS**  
Purpose: prevent plain SQLite disclosure of the learner database. Steps: run encryption verification, try plain SQLite, and inspect the file header. Expected: SQLCipher active, plain reader refused, opaque header. Actual: all matched. Evidence: `db-encryption.log`, `db-header.txt`, `db-final-verification.json`.

**G14 — Learner data export — NOT BUILT**  
Purpose: provide a learner-controlled export. Steps: request `/api/v1/me/export`. Expected: export of the learner’s data. Actual: 404; no endpoint is implemented. Evidence: `security-browser-api.json`.

### H. Robustness and mobile

**H01 — Slow API and double-submit — PASS**  
Purpose: prevent duplicate grading under a slow response and repeated press. Steps: delay `/attempts` by 2.5 seconds and dispatch two Check it presses. Expected: one request/attempt, pending state, then one receipt. Actual: one POST and one Not yet receipt; raw data contains one attempt for that item. Evidence: `robustness-browser.json`, `db-final-verification.json`.

**H02 — Offline failure and online retry — PASS**  
Purpose: avoid fake grading or lost work when connectivity drops. Steps: select an answer, go offline, submit, then reconnect and retry. Expected: visible error, selection retained, zero successful response/storage offline, later grading succeeds. Actual: `ERR_INTERNET_DISCONNECTED` and a visible backend-unreachable message; selection remained; online retry graded Correct. Exactly two attempts exist across H01+H02 despite three request events. Evidence: `robustness-browser.json`, `db-final-verification.json`, `screenshots/practice-offline.png`.

**H03 — Registered mobile navigation and overflow — PASS**  
Purpose: keep the full app reachable at 390×844. Steps: open the drawer, follow Review, and visit 11 main routes. Expected: working drawer/links, content, no route error or horizontal scroll. Actual: all passed; scroll width equaled viewport width on every route. Evidence: `robustness-browser.json`, `screenshots/mobile-voice.png`.

**H04 — Whole-app mobile tap-target size — NOT RUN**  
Purpose: make controls reliably tappable outside the baseline as well as inside it. Reason: a separate target-size measurement of Home, Learn, and Review controls was not completed. Expected: no target below 40×40 px. Actual: unverified for those whole-app controls; H03 verifies navigation/overflow and B02 verifies the existing mobile baseline’s target checks, but neither is substituted for this measurement. Evidence: `robustness-browser.json` and `games-e2e-all.log` record only those separate executed checks.

**H05 — Browser console and uncaught-error triage — PASS**  
Purpose: distinguish real crashes from expected auth/offline responses. Steps: collect page errors and console entries across access, item, learning, and robustness runs. Expected: no unexplained uncaught exception/hydration crash. Actual: no unexpected page error. The 27 access errors are expected 401 auth transitions; two robustness errors are intentional offline failures; remaining entries are CSS preload warnings. Evidence: JSON `console` arrays and `harness-corrections.md`.

**H06 — Rapid navigation stress — PASS**  
Purpose: prevent stale route responses, freezes, and redirect loops. Steps: run 10 routes at under 300 ms apart for three laps and perform the registered mobile route crawl. Expected: no freeze, blank body, loop, or route error. Actual: rapid-switch check and mobile crawl passed. Evidence: `round6-crawl.log`, `round6-crash-log.md`, `robustness-browser.json`.

### I. Existing suites

**I01 — Backend suite execution — PASS**  
Purpose: run the repository’s backend regression suite and count results. Steps: run all backend pytest tests with an isolated data directory. Expected: record actual pass/fail/skip counts. Actual: 1442 passed, 2 skipped, 0 failed. This verdict describes suite execution, not the absence of the live defects above. Evidence: `backend-pytest.log`, `backend-pytest.xml`.

**I02 — Frontend unit/type suites execution — PASS**  
Purpose: run every frontend Node test group and contract/type checks. Steps: run engine, lib, server, env, dev, and lint commands. Expected: record actual counts. Actual: 70 passed/0 failed and lint/typecheck passed. Evidence: `frontend-suite-summary.log`, individual `frontend-*.log` files.

**I03 — Existing browser-suite usefulness and maintenance — PARTIAL**  
Purpose: use existing browser suites as independent regression evidence. Steps: run games, full fake journey, Round 6, Round 4, UI probes, banned-copy, and journey scripts. Expected: suites reach their stated assertions and produce interpretable counts. Actual: full baseline and item runs are useful, but multiple suites abort on removed routes/old consent or journey selectors; raw counts and weak assertions are documented above. Independent real-learner reruns were used instead of converting those aborts into product passes. Evidence: all suite logs listed in the suite table, especially `games-e2e-all.log`, `journey-full-fake-e2e.log`, `round6-crawl.log`, `round4-browser.log`, and `frontend-probe.log`.

## Phase-1 completion and explicit limitations

- All three required deliverables are now present: this report, `docs/audit_evidence/`, and `docs/AUDIT_FIXLIST.md`.
- No application fix has started. Every FAIL/PARTIAL item has a corresponding ticket in the fixlist.
- Real model behavior, natural session expiry, actual audio input/output, populated attempt/proposal purge cascades, and whole-app mobile target sizing remain NOT RUN for the stated reasons.
- Additional study tools and learner export are NOT BUILT; they are not counted as working features.
