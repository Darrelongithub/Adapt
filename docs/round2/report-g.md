# Phase G report — integration

Branch: `arena/2ff4f0d8-adapt` (this session is pinned to it; the phase brief's `r2/phase-g`
name is superseded by the session branch, with the owner's approval). Integration lands on
`main` by pull request from this branch, never by direct push.

**The live model check has not been run.** No key has ever been configured on this build;
everything model-shaped below was verified against scripted fake models — in tests, over
HTTP, and in a real browser. The owner's commands are at the end of this report.

## Gate check before starting

The six phase reports do not all exist under the names the brief expects, so completion was
verified by evidence rather than by file list: PRs **#17/#18 (A), #25 (B), #24 (C), #23 (F),
#19 (D), #21 (E)** are merged; `report-a/b/d/e.md`, `report-F.md` exist (with lowercase
pointer files where the agent used capitals); Agent C's session ended without a report, so
`report-c.md` was reconstructed from PR #24 and labelled as such. Phase E's deliverable is a
harness, not a live run — its "finished" state is *harness tested, live run not made*, and
nothing in this report upgrades that claim.

## 1. Merge order

A, B, C, F, D, E had already landed as the PRs above before G started; there was nothing
left to merge in the brief's order. What remained was the seam work below.

## 2. Handoffs — disposition

| Handoff | Disposition |
|---|---|
| A → D onboarding copy | Applied by D; verified by the crawl (below). The `bannedPhrases.mjs` exemption list is gone — the crawl holds every route to the same list. |
| B §1 register prepare router | Done; `POST/GET /api/v1/content/prepare…` live (proven over HTTP below). |
| B §2 migration bootstrap | Done; single Alembic head (…→ C1 → P3), migration tests pass in the full suite. |
| B §3 scoring on the typed Pack path | Untouched by G; B's fail-closed helpers stand. |
| B §4 SymPy vs exact checker | **Decision: accept the existing exact-`Fraction` checker** as the symbolic verifier. It is exact where SymPy would be used for key-checking, rejects what it cannot bound instead of guessing, and adding a SymPy dependency for this is not justified. Recorded here so the decision is owned, not drifted into. |
| B §5 upload boundary | No upload path exists; none added. |
| D dual journey shape | Kept as designed: `/journey/state` + `/advance` are the machine; `GET /journey` keeps its timeline fields and spreads the frozen keys. Not "tidied". |
| E stub swap | None needed: `scripts/live_model_check.py --pipeline auto` picks the real `adapt.content.skeleton` / `adapt.ai.lesson_writer` now that both import. Verified by import, not by a live run. |
| F `model_note` | Renamed to `ratings_note` with learner-neutral wording; the pinning test was updated to assert the new wording at the same strength. |
| F pop-up lessons for most topics | Not closed: authored ninety-second pairs still exist only for some topics; the round still says "skipped rather than invented". Named in `docs/UNVERIFIED.md`. |
| F baseline `finished` flag | Journey links keep it; covered by F's suite. |

## 3. Seam bugs found by integration (each with a regression test)

These are the bugs no single phase could see, because each phase's tests stubbed its
neighbours:

1. **Lesson routes rebuilt packs with `force_simple=True`**, so a just-prepared pack could
   never be taught from. `_topic_from_frozen` now looks in B's cache first.
2. **Prepare/plan level mismatch** (found by the browser run, after the HTTP test passed):
   the browser prepares with `{topic}` only, so the pack is cached at level 0, while the
   lesson routes resolve at the learner's rung (2). Keyed strictly by level, the prepared
   pack was unreachable and every typed-topic lesson silently downgraded to the simple pack.
   The cache lookup now probes the learner's rung first, then nearest levels for the same
   topic and language. Pinned by
   `test_prepare_without_a_level_still_teaches_the_prepared_pack`.
3. **No concept-discovery route.** The frozen contracts give the lesson routes a
   `concept_id` but no way for a screen to learn one. Added additive
   `GET /api/v1/lesson/concepts?topic=` returning ids and names only — never keys.
4. **No screen for C's three moments.** `frontend/src/app/first-lesson/` is the missing UI:
   prepare → concepts → lesson (upgrading in place to "Written for you") → wrong-answer
   explanation quoting the chosen option → re-teach under a new method each time. The frozen
   contract carries no server grade for pack checks (keys never leave the server), so the
   screen asks the learner what happened — said plainly on the screen.
5. **CSRF on the concepts route**: `rate()` chains `require_csrf`; the page now sends the
   token (the contract checker validates the call).
6. **The banned-phrase crawl predated the consent-first gate** (Phase D): its guest setup
   expected a button that no longer exists. Fixed to drive the real flow (consent → agree →
   Continue as guest). No assertion was weakened; the copy lists are unchanged.
7. **Learner-bound payload notes still naming the machinery** in the subject placement path
   (the same class F flagged for `model_note`, one layer deeper): the resolve response's
   placement/ratings notes, the method-dealing notes, the methods-report label, and the
   scene-authoring notes now use the established "written-for-you" wording. Three tests
   pinned the old sentences; each pin now asserts the new wording at the same strength.
   Contract enums (`source: "model"`, `pack_source`) are unchanged — they are frozen-contract
   values, not copy.

## 4. Frozen contracts over HTTP

`backend/tests/test_round2_g_integration.py` (2 tests, in the suite): a typed topic goes
`POST /content/prepare` → validated Pack (`pack_source: "model"` under the fake) →
`GET /lesson/concepts` → `POST /lesson/plan` → upgrade job → `written_for_you` plan with a
check block → `POST /lesson/explain-wrong` quoting the wrong option actually chosen →
two `POST /lesson/reteach` calls with different methods — all through the real app with the
fake injected only at `services.ai2.llm`, and the fake only ever sees the six frozen
purposes. The second test repeats the flow the way the browser actually calls it (prepare
with no level).

## 5. Browser results (real Chromium, this sandbox)

* **Full journey, desktop and mobile** (`e2e/journey-full/`, 2 passed): account →
  five stages skipped and server-recorded as skipped → first lesson on "kite building" →
  "Written for you" → wrong-answer explanation containing the chosen option's text →
  two re-teaches with different methods → practice; server stage state read back at the
  end; no horizontal overflow on mobile. Screenshots: `docs/screens/G/{desktop,mobile}/`.
* **Banned-phrase crawl** (`frontend/tests-e2e/banned-phrases.mjs`): 16 routes signed out
  and signed in, plus the contrast-wording check and public `/privacy` both ways — **all
  passed**. It visits pages; it does not play a subject round (see Unverified).
* **Secrecy canary**: `tests/test_config_secrecy.py` (14 tests) walks the app's live route
  table — including the new prepare/lesson/concepts routes — with canary key/model values
  set, asserting none surface in any response body or header; the WebSocket hello is checked
  the same way. Passed.
* **Teaching quality**: `scripts/teaching_quality_eval.py` exits 0 — zero gate flags across
  273 deterministic questions, 61 topics; its model pass prints *not run* (no key). This
  regenerated `QUALITY_REPORT.md` byte-identically.

## 6. Test results (integrated tree, 2026-10-15)

| Command | Result |
|---|---|
| `cd backend && pytest -q` | **1333 passed, 2 skipped** |
| `cd frontend && npm run lint` | contract ok — 116 backend routes, 102 frontend calls |
| `npm run test:engine` / `test:lib` / `test:server` | 15 / 42 / 2 passed |
| `npm run build` | compiled successfully |
| `npx playwright test` (e2e/journey-full, both projects) | 2 passed |
| `node tests-e2e/banned-phrases.mjs` | all checks passed |
| `scripts/teaching_quality_eval.py` | exit 0, zero flags; model pass not run |

No test was deleted or weakened. Three tests that pinned learner-facing sentences now pin
the replacement sentences (§3.7); one crawl setup (§3.6) was updated to drive the current
UI. Everything else is additive.

## 7. Per phase: landed / failed

* **A — landed.** Public privacy, copy purge, log filter, hint/closing-prompt fixes all in
  and covered by both A suites. Its crawl needed G's setup fix (§3.6) before it could run
  in Chromium at all; it had previously only run in jsdom.
* **B — landed.** Pack contract, verification, cache/job, P3. The one deliberate spec
  deviation (§4 of its handoff, SymPy) is decided in §2 above.
* **C — landed.** Lesson writer, three moments, teaching memory, contrast endpoint. Its
  report is reconstructed (see Gate check). The moments had no screen until G built one
  (§3.4) — that was a seam, not a C defect: C was forbidden from touching journey pages.
* **D — landed.** Journey shell/state, dedupe, placement consistency. Its e2e stubbed B/C
  screens, as the brief allowed; G's run replaces the stubs with the real screens.
* **E — landed as a harness; the live run failed to happen, by environment, and still has
  not.** Nothing in the repo has been produced by a real model. `MODEL_QUALITY_REPORT.md`
  is still the template.
* **F — landed.** Games + subject round gate passed twice per viewport on its branch and
  remain green in the integrated suite. Its `model_note` flag was actioned (by rename)
  and the same class one layer deeper was cleaned in §3.7.
* **G — this report.** Seams §3, proofs §4–§6, docs: `docs/ROUND2.md`,
  `docs/round2/demo.md`, README counts corrected (645 → 1333 measured), `docs/UNVERIFIED.md`
  round-2 section, `docs/FUNCTIONALITY_ROUND.md` reconciliation addendum (round-1 boxes
  left as round 1 left them).

## 8. Still unverified

See `docs/UNVERIFIED.md`, round-2 section. In short: real-model output quality, cost and
latency (never run); behaviour under a real provider's timeouts/refusals; a banned-phrase
crawl that plays a full subject round; learner honesty in self-reported check results;
Phase E's live fixtures (none exist yet); pop-up lesson pairs for most topics.

## 9. For the owner: the live check

Run with a real key, from the repository root:

```bash
adapt config set-key                  # hidden prompt; never put a key in an argument or a file
adapt config test                     # one minimal request: pass or fail, nothing about the key printed
python scripts/live_model_check.py    # 12 topics × 2 profiles × 3 moments + judge
```

It writes `MODEL_QUALITY_REPORT.md` (replacing the template) and exits non-zero under its
stated thresholds; commit the report it writes, with its exit code, and review one of the
sanitised fixtures it saves under `backend/tests/fixtures/model/` before committing those.
**Until that is run, any claim about real-model teaching quality is unmade, not made.**

`docs/round2/demo.md` is the three-minute demo (fake-model path, contrast screen, mock
fallback); `docs/ROUND2.md` is the round summary.
