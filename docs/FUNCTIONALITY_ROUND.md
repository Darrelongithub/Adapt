# Functionality round — what I found, what I am building, and how to check it

This is the working document for the functionality round described in
`ADAPT_functionality_round_prompt.md`. It is written *before* the code changed, and updated at
the end of each phase with what was actually run. Where a claim in here is not backed by a
command and its output, it says so.

Baseline state when this round started (measured, not quoted from the README):

| Check | Command | Result |
| --- | --- | --- |
| Backend tests | `.venv/bin/python -m pytest -q` (in `backend/`) | **650 passed** (~49 s) |
| Frontend gates | `npm run lint`, `npm run test:engine`, `npm run test:server` | run per phase below |
| Real browser | `npx playwright install chromium` | **refused**: `cdn.playwright.dev` is not reachable from this sandbox (`ECONNRESET`). No Chromium could be installed, so every browser check below is the repo's own jsdom probe (`frontend/scripts-ui-probe.mjs`), which drives the hydrated React app against the real API. That is weaker than a browser and is listed as such in `docs/UNVERIFIED.md`. |
| Model key | `ADAPT_GEMINI_API_KEY` | not set, and no outbound network to a model provider. Everything below runs on the deterministic path; the model path is exercised with stub LLMs in tests. |

## 0.1 What the codebase is

FastAPI + SQLAlchemy/SQLite(SQLCipher) backend (`backend/src/adapt`), Next.js 15 app router
frontend (`frontend/src`), ~26k lines of Python and ~9k of TypeScript. The parts that matter for
this round:

* `learning/` — BKT (`bkt.py`), spacing, prerequisite gaps, methods (assigned teaching methods),
  reteach, effect evaluation, hypotheses, patterns, telemetry allowlist, profile.
* `ai/` — `ai1.py` (analysis, hypotheses, proposals, plan), `ai2.py` (tutor, grading, authoring),
  `local_tutor.py` (the deterministic fallback teacher), `ai3.py` (**a scene narrator, not an
  adaptor** — the naming collision section 2 of the brief calls out), `llm.py` (Gemini plumbing,
  quota, redaction), `prompts.py`.
* `assessment/` — `bank.py` (authored items), `generated.py` (model-authored items + validation),
  `selector.py` (placement and adaptive selection), `grading.py`, `numba` import/export.
* `services/` — sessions, attempts, lessons (the interactive engine), adaptations (the approval
  gate), journey, dashboard, telemetry.
* `api/routes/` — auth, learning, lessons, adaptations, tutor, system; `security/` for auth,
  middleware, CSRF, rate limits, redaction, audit.
* Frontend: `lib/flow.ts` (one hook for the loop), `lib/drafts.ts` (answer drafts that survive a
  re-render), `components/question.tsx` (the item), `lib/engine/` (the scene player), one page per
  destination.

**How the two existing intelligences are wired today:** there is no shared typed state layer.
`ai1` and `ai2` each open their own session through a `sessionmaker` and reach into each other's
domain indirectly (both import the same helpers and both migrate the same rows). That is the gap
section 2A asks to close.

## 0.2 The plan, in the order the brief requires (each phase gated by the one before)

**Phase 1 — make the loop work.**
1. Fix the verified defects (4.1.1–4.1.10), each with a regression test or a scripted probe check.
2. Calibration fixes in `learning/bkt.py` (4.2).
3. The shared-state foundation (4.3): `LearnerState` with immutable snapshots and
   `model_version`, the write-permission matrix enforced in code, the append-only evidence log
   with idempotent ingestion, teaching memory, the orchestrator turn cycle, `adapt rebuild-model`,
   an architecture test that no AI module imports another, and a mid-lesson re-plan.

**Phase 2 — sign-up and the universal mind baseline (six games).** Format pick before the games,
preference questions, the six games scored by deterministic code, the snapshot screen with eight
factors, ranges and n, and the levers AI 3 consumes.

**Phase 3 — subject round with topic-aware test selection.** Versioned subject skill taxonomy,
AI 2 topic resolution, AI 1's 0–3 lean ratings (authored table plus a validated model gap-filler),
the universal ladder, and the 90-second pop-up method test.

**Phase 4 — content engine.** Concept breakdown, three variants per concept, the mandatory
validation (symbolic/quotable), deterministic fallback content for a starter topic set, caching,
and `scripts/teaching_quality_eval.py` writing `QUALITY_REPORT.md`.

**Phase 5 — the adaptation layer (`ai/adapter.py`) and the contrast demo.**

**Phase 6 — evidence, method comparison, the "why did I fail" loop.**

**Phase 7 — security, robustness, demo readiness.**

## 0.3 Working rules I am holding myself to

* Every phase ends with a commit, a report in this file, and the full test suite re-run.
* No existing test is weakened or deleted. New behaviour gets new tests.
* Anything I could not run (a real browser, a real model call, a real second device) is listed in
  `docs/UNVERIFIED.md`, not implied to work.

## 0.4 Task list (kept up to date as the work lands)

- [x] Read the codebase, run the suite, reproduce the reported defects
- [x] Baseline commit (repository was already a git repo at `18ae7b5`)
- [x] Phase 1.1 browser-answer defect — root cause found: the `Opening` overlay (`position: fixed; inset: 0; z-index: 200`, rendered from `app/layout.tsx` on **every** route) swallows the first click on `/diagnose`; the same component concatenates its words (`.opening-word { display: inline-block }` trims the trailing space) and the placement intro starts mid-sentence because the backend string does
- [ ] Phase 1.1–1.10 fixes, with tests
- [ ] Phase 1.4.2 BKT calibration
- [x] Phase 1.4.3 shared state, orchestrator, `adapt rebuild-model`
- [x] Phase 2 baseline (backend + screens; browser checks pending, see `docs/UNVERIFIED.md`)
- [ ] Phase 3 subject round (agent 2 - handoff is `docs/handoff/agent-2-subjects.md`)
- [ ] Phase 4 content engine + `QUALITY_REPORT.md` (agent 3 landed; integration below)
- [ ] Phase 5 adaptation layer + contrast demo (agent 4)
- [ ] Phase 6 evidence + failure loop (agent 5 landed)
- [ ] Phase 7 security/demo readiness (agent 6 landed; integrated and green - see the integration report)
- [ ] Final report

Everything below the line "## Integration report 1" was landed by the agent workstreams on their own
PRs and merged here (`docs/AGENT_BRIEFS.md` says how the merge is run).

## 0.5 Phase reports

Appended here as each phase completes (files changed, commands run with results, what is still
unverified).

---

## Phase 1 report — make the existing loop work, and fix the known teaching defects

Status: **§4.1 complete, §4.2 complete, §4.3 complete** (see the §4.3 report below). The Phase 1
gate is passed on everything that can be executed in this environment: the suite is green, the live
app was driven over HTTP against its real encrypted database, and the two things that cannot run
here (Playwright/Chromium, an actual model provider key) are listed in `docs/UNVERIFIED.md`.

### Files changed in this phase

* `backend/src/adapt/assessment/worked.py` (**new**) — the content-completion engine: derived
  worked examples and three graded hints, built from each item's own stem, distractors, rubric and
  answer key, with `verify_example` / `verify_hints` as the gate on every path that serves them.
* `backend/src/adapt/services/sessions.py` — `complete_content()` (fills examples/hints, compares
  *content* not structure, keeps `verification` honest) and the `ensure_curriculum` upsert guard
  (`_bank_content_covered`, empty bank values never overwrite completed rows).
* `backend/src/adapt/ai/local_tutor.py` — retrieval rebuilt: a light stemmer, a three-gate
  relevance filter (numbers decisive, half the content words shared, competitive score), two
  queries (the question, and the question + the item on screen) merged by agreement, one topic per
  answer, cross-topic text labelled as such, honest refusal, the item's own worked example as the
  labelled fallback, varied openers by level × context, sentence-boundary `_shorten`, and a
  `steps` segmentation with a check per step.
* `backend/src/adapt/ai/ai2.py` — `Reply.steps`; `context()` gains `last_attempt_correct` and topic
  titles; `ask_context` derived from the evidence; the guarded meta (`grounded`, `topic_match`,
  `topic_id`, `note`).
* `backend/src/adapt/assessment/selector.py` — `TARGET_BAND = (0.70, 0.85)`, `MIN_EXPECTED_SUCCESS
  = 0.45`, `enforce_target_band()` (prerequisites first when one is named, band as a filter,
  below-floor items served only with the worked example open), `Pick.plain_reason()`.
* `backend/src/adapt/learning/bkt.py` — `credible_interval()` (Beta posterior on the observed
  success rate), `evidence_label()`, `EARLY_SIGNAL_N`, guess rates per format (text 0.02 → 0.05 per
  the brief), slip 0.15 for constructed answers, the 0.12 per-answer cap.
* `backend/src/adapt/ai/ai1.py`, `services/dashboard.py`, `learning/profile.py` — every estimate
  now travels with its interval, its n and its `early signal`/`measured` label; the dashboard's
  hand-rolled interval was replaced by the shared one so two screens cannot disagree.
* `backend/src/adapt/api/routes/tutor.py` — the worked-example route completes and verifies on
  demand and persists what it derived.
* Frontend: `components/opening.tsx` (overlay only on the front door, space between animated
  words), `components/question.tsx` (topic titles, the interval and the early-signal badge on the
  receipt, placement shows no `prompt_context`), `app/page.tsx` (no shell commands on the front
  page; the summary line carries n), `app/diagnose/page.tsx` (the intro is the server's own
  sentence), `app/globals.css` (marginalia only ≥1400px), `components/ui.tsx` (an error a learner
  can read), `lib/types.ts`.
* Tests: `backend/tests/test_functionality_round_phase1.py` (**new**, 23 tests, one per defect),
  `frontend/tests-e2e/placement.mjs` (**new** — the Playwright script the brief asks for; cannot be
  run here, see below), `frontend/scripts-ui-probe.mjs` (the choice-press check).

### Commands run, with results

| What | Command | Result |
| --- | --- | --- |
| Backend suite | `cd backend && ../.venv/bin/python -m pytest -q` | **673 passed**, 1 warning, 50.6 s (650 at baseline; 23 new) |
| Phase 1 regressions | `… -m pytest -q tests/test_functionality_round_phase1.py` | 23 passed |
| Frontend types | `cd frontend && npx tsc --noEmit` | clean |
| UI probe (jsdom, real API) | `npm run probe -- --only home,diagnose,learn` | **71/71 checks passed** |
| UI probe, choice path | `PROBE_FIRST_ITEM_KIND=mcq npm run probe -- --only learn` | 27/27, including "pressing the choice itself unlocks the submit control" |
| Reseed convergence | 3 × `ensure_curriculum` on a fresh DB | boot 1 `{examples: 26, hints: 56, flagged: 0}`, boots 2 and 3 `{items_updated: 0, examples: 0, hints: 0, flagged: 0}` — converged |
| Detector sweep | `worked.verify_example` over all 63 authored examples | 0 flagged (the 24 unflagged-by-absence items get derived examples, which are checked the same way) |

### The ten defects, and what was actually wrong

1. **Answer selection.** The welcome overlay is `position: fixed; inset: 0; z-index: 200` and is
   dismissed by a click *on itself*; it rendered on every route, so on `/diagnose` the learner's
   first press on an option was received by the overlay. jsdom has no hit-testing, which is why the
   suite was green. Fixed by scoping the overlay to `/`. **Measured caveat:** with the overlay
   forced back on for every route, the jsdom probe *still* passes the press check (26/26), so the
   probe proves the button→unlock wiring and not the occlusion. The occlusion needs a browser:
   `frontend/tests-e2e/placement.mjs` (written, not run — no Chromium in this sandbox).
2. **Wrong-notes explanations.** BM25 unconstrained across the course. Fixed as described above;
   the denominators question now answers from the fractions notes *and says so* (`topic_match:
   false`), a subtraction question answers from integer material, and an unrelated question gets an
   honest "I do not have anything in your material that answers this" rather than a nearby passage.
3. **Missing worked examples.** 24 of 63 items returned `steps: []`; every item now has a verified
   example, and the route derives + persists one on demand. Re-asserted by test across the bank.
4. **Generic hints.** 17 items had no hints and 39 fewer than three; every item now has three
   graded hints (nudge / method / near-answer), authored where they exist, and the generic sentence
   is gone from the fallback path too.
5. **Repeated opener.** Now four levels × five contexts (`first_attempt`, `repeat_error`,
   `after_hint`, `after_reveal`, `refused`), seeded so a reply is stable within a conversation.
6. **Word truncation.** `_shorten` returns whole sentences; bodies are segmented into steps, each
   with a one-question check, and every sentence of the body survives into a step.
7. **Practice queue difficulty.** Measured before: ~32% predicted success against a stated 78%
   target, with a reason string claiming "prerequisite gap flagged first" while no prerequisite
   item was in the queue. Now the band is enforced (70–85%), a named prerequisite leads the queue,
   and nothing below 45% is served unless its worked example is attached.
8. **Placement hint leak.** The placement payload no longer carries `prompt_context`; when an item
   has one the withheld text is named in the payload (so the client can say so) rather than dropped
   silently. Practice keeps it — there it is a legitimate scaffold. **Found while fixing it:** on
   this bank the placement draw happens not to select any of the eight authored `prompt_context`
   items, so the leak was visible on the *practice* screen too and would have needed a different
   learner to show on placement; the fix covers both paths, and the test drives the placement path
   directly rather than waiting for a lucky draw.
9. **Internals on screen.** Topic ids, `d0.4`, the developer reason string ("belief 0.20, this
   question carries the most information about it") and the front-page shell-command card are gone;
   `reason_plain` is a translation of the same decision with no ids, probabilities or metric names,
   and `topic_title` travels with every item.
10. **Layout.** Overlay/word-abutment fixed (`margin-right` on the animated word, since a trailing
    space inside an `inline-block` is trimmed); the marginalia is absolute only from 1400px, where
    the arithmetic holds, and flows under the question below that; the placement intro is a
    sentence, not a clause glued to a footnote. **Not verified:** that the 1280px overlap is gone
    *visually* — that is a paint-level claim and there is no browser here. It is listed in
    `docs/UNVERIFIED.md` with the arithmetic that motivated the breakpoint.

### Calibration (4.2)

* `p_guess` by format: mcq `1/n` (0.25 for four options), numeric 0.02, short text 0.05, teachback
  0.05, worked example 0.0. `p_slip` 0.15 for constructed answers. One answer may move `p_know` by
  at most 0.12, so the brief's 0.20 → 0.03 is now 0.20 → 0.08.
* Every estimate carries `ci` (a Beta posterior on the observed success rate, 10th–90th
  percentile, seeded with the model's own reading) and `evidence` (`early signal` under 8
  observations, `measured` after). Shown on the receipt, the dashboard and the home summary; the
  count is printed next to each one.
* The cold-start rule is re-asserted: a new learner prints `new`, never a low-ability band.
* **Honest limit of the interval:** it is an interval on the *observed* success rate, not a
  posterior over the hidden knowledge state. Computing the latter needs the forward–backward
  machinery and would be wider; this is stated in the docstring and in `docs/UNVERIFIED.md`.

### Still open in this phase

* The Playwright script exists but has never been executed here; until it runs on a machine with
  browsers, no browser-level claim in this round is verified (`docs/UNVERIFIED.md`).
* `npm run lint` was re-run after the last frontend edit and passes (see the §4.3 report).
* §4.3 migrations to the *old* paths are partial by design: the evidence log, plans, deliveries and
  the learner model are written by the live app, while AI 1's own analysis path (`ai1.analyze`,
  `bkt_states`, hypotheses, proposals) still writes its own tables. Both are updated from the same
  attempts; the store is additive rather than a rewrite of phase-4 machinery. What remains is for
  AI 1's analysis to *read its inputs from the store*; that is listed in `docs/UNVERIFIED.md`.

---

## 1.3 Phase 1 — §4.3 report: the shared learner state, and the Orchestrator

**What §2A asked for, and what was built.** One store, one turn cycle, one snapshot per read.

| §2A requirement | Where it lives now | How it is proved |
|---|---|---|
| One source of truth, typed service layer | `backend/src/adapt/state/store.py` (`LearnerState`) | `tests/test_state_foundation.py` (52 tests) |
| Immutable snapshot per read | `state/snapshot.py` (`LearnerSnapshot`, `MappingProxyType` + tuples), `LearnerState.get_snapshot` | `test_a_snapshot_cannot_be_edited`, `test_thaw_gives_back_a_mutable_copy` |
| Append-only evidence log, idempotent ingestion | `state_evidence` table (unique `(student_id, event_id)`), `LearnerState.ingest` / `ingest_many` | `test_a_duplicate_event_id_is_counted_once`, `test_two_writers_racing_on_the_same_event_produce_one_row` |
| Weighted sources (controlled 1.0, observational ≈0.4) | `WEIGHT_CONTROLLED` / `WEIGHT_OBSERVATIONAL` in `state/store.py`, applied in `state/model.py` | `test_an_observational_event_moves_the_estimate_less_than_a_controlled_one` |
| Derived, versioned learner model | `model_versions` table (PK `(student_id, version)`, rows never updated), `state/model.py` fold | `test_a_model_version_row_is_immutable_in_practice`, `test_a_learner_with_nothing_measured_has_no_version_row` |
| Rebuildable from the log (`adapt rebuild-model`) | `LearnerState.rebuild_model`, `adapt rebuild-model [--for X | --all | --dry-run]` | `test_rebuild_equals_incremental`, `test_rebuild_equals_incremental_after_a_real_lesson`, `test_the_cli_can_rebuild_a_model_and_reports_equality` |
| Write-permission matrix, enforced and tested | `state/permissions.py`; every write goes through `PERM.check_write` | `test_the_matrix_is_the_table_in_the_brief`, `test_a_module_cannot_write_another_modules_domain`, `test_no_module_writes_outside_its_row` |
| No AI module imports another | architecture test, static scan of `adapt/ai/*.py` | `test_no_ai_module_imports_another`, `test_the_state_package_imports_no_ai_module` |
| Content registry, hashed ids, validation status | `content_packs` (PK = `cpk_sha256…`), `put_content` / `content_for` / `set_validation`, `Orchestrator.ensure_content` | `test_a_content_id_is_the_hash_of_its_content`, `test_ensure_content_reuses_an_existing_pack_without_calling_the_generator` |
| Teaching memory | `teaching_memory` table (unique `(student, session, kind, ref)`), `remember` / `memory`, carried in the snapshot | `test_teaching_memory_survives_a_re_plan_and_a_new_session`, `test_the_snapshot_carries_what_was_already_asked` |
| Lesson plans, deliveries, `why` provenance | `lesson_plans` / `deliveries` tables, `Orchestrator.plan` / `deliver` | `test_a_delivery_records_what_was_shown_and_why`, `test_answering_an_item_records_a_delivery_with_its_why` |
| Session state (topic, block, pace, fatigue) | `session_states` (optimistic `version`, `ModelConflict` on a stale expect) | `test_a_stale_expect_version_is_refused`, `test_replan_mid_lesson_keeps_the_block_and_the_teaching_memory` |
| Deterministic turn cycle 1–5 | `state/orchestrator.py` (`cycle`, `ingest`, `advance_model`, `ensure_content`, `plan`, `deliver`) | `test_the_cycle_does_not_replan_when_the_model_has_not_moved`, `test_the_cycle_ingests_events_it_is_handed` |
| Re-plan mid-lesson without a restart | `Orchestrator.replan` (supersedes the plan, leaves block and memory alone) | `test_replan_mid_lesson_keeps_the_block_and_the_teaching_memory`, `test_a_wrong_answer_moves_the_plan_without_restarting_the_lesson` |
| "AI 1/2/3" out of learner-facing copy | placement intro rewritten; tutor/resource/status notes rewritten; frontend mock strings cleaned | `test_learner_facing_copy_has_no_internal_component_names` |
| `ai/ai3.py` renamed — it is a scene narrator, not AI 3 | `ai/scene_narrator.py` (`SceneNarrator`), `SCENE_NARRATOR_SYSTEM` | `test_the_scene_narrator_is_not_called_ai3_anywhere` |
| Console-only configuration hooks | `adapt matrix` added; `rebuild-model` added | `test_the_cli_prints_the_write_matrix` |

**Files changed in §4.3.**

* new: `backend/src/adapt/state/{__init__,permissions,snapshot,model,store,orchestrator}.py`
* new: `backend/migrations/versions/0007_learner_state_store.py` (the same seven tables, for an
  existing database — `create_all` cannot alter tables, and `alembic check` fails a model change
  with no revision)
* new: `backend/tests/test_state_foundation.py` (52 tests, listed above)
* `backend/src/adapt/db/models.py` — seven new tables
* `backend/src/adapt/services/__init__.py` — `AppServices.state`, `AppServices.orchestrator`, AI 2
  gets the store; the narration component is named in the status payload
* `backend/src/adapt/services/attempts.py` — every scored attempt is appended to the evidence log
  and recorded as a delivery, and the model is advanced from the log (idempotently)
* `backend/src/adapt/api/routes/learning.py` — starting a lesson records its plan; serving the
  practice queue records the delivery; `/profile` additionally returns `shared_state`
* `backend/src/adapt/ai/ai2.py` — AI 2's context carries the store snapshot and its
  `model_version` instead of assembling a private view
* `backend/src/adapt/ai/ai3.py` → `ai/scene_narrator.py`, `ai/prompts.py` (constant renamed,
  old name kept as an alias for one release), `services/lessons.py`, `api/routes/lessons.py`
* `backend/src/adapt/cli.py` — `adapt rebuild-model`, `adapt matrix`
* `frontend/src/lib/mock.ts`, `frontend/src/app/proposals/page.tsx` — learner-facing strings

**Commands run, and what they returned.**

| Command | Result |
|---|---|
| `pytest -q` | **725 passed**, 1 warning, 52.4 s (673 before §4.3) |
| `pytest -q tests/test_state_foundation.py` | **52 passed** |
| `pytest -q tests/test_migrations.py` | 10 passed (the new revision is picked up; `alembic check` is clean) |
| `npm run lint` | contract ok — 75 backend routes vs 52 frontend calls, scene kinds agree, tsc clean |
| `npm run probe` (frontend, jsdom + live API) | **209/210** on a freshly restarted API. The single failure (`a flooded learner is told to wait, with a number`) **reproduces at baseline `39849f8`**: the flood loop requires the refusal to arrive *after* at least one success, and by that point in the run the suite's own LLM calls have drained the bucket, so the first flood call is the refused one (`limited = 0`, check wants `> 0`). The server did refuse with `{"error": "rate_limited", "retry_after_s": …}` and a `retry-after` header — measured directly: 12 successes then a 429 on the 13th call. It is a probe artefact of bucket state, not a regression, and the check is left as it is. |
| `python -m adapt.cli matrix` | prints the matrix and the 21 refused `(module, domain)` pairs |
| `ADAPT_DATA_DIR=.data_dev python -m adapt.cli rebuild-model --all` | `{"learner_id": "stu_…", "version": 2, "previous_version": 2, "equal": true, "evidence_count": 1}` |
| live API on :8000 (`uvicorn`), driven over HTTP with a cookie jar | guest student → session → diagnostic (8 items) → one deliberately wrong attempt → `/profile`: `shared_state.model_version = 2`, `evidence_count = 1`, `Integer operations p=0.08 n=1 ci=[0.001, 0.437] band=new`; `/practice?count=3` returned 3 items and recorded a delivery |

**The dev database upgrade is real, not assumed.** `.data_dev/adapt.db` was created *before* these
tables existed. Restarting the app against it added all seven tables at startup (verified by
inspecting the schema with the app's own `Database`), which is the path a returning operator takes.

**Two things worth recording, because they cost time and would cost it again:**

* Opening the SQLCipher file with a *plain* `sqlite3` client while the server was running left a
  zero-byte `-wal` behind, and every new SQLCipher connection then failed with `disk I/O error`
  ("file is not a database" from the plain client was the first clue). Nothing was lost — a clean
  shutdown checkpointed the WAL away and the data was intact (65 students, 76 attempts) — but the
  lesson is: probe that database with the app's own `Database`, never with `sqlite3`.
* A test caught a real defect in the first cut of the attempt-path wiring: the store call used a
  variable (`hints_used`) that does not exist in that scope, and because the store call is wrapped
  so that it can never lose an answer, the failure was silent apart from a log line. That is the
  argument for the "an attempt lands in the log" test existing at all.

### Deviations in §4.3 (and why)

1. **`code` may write `content`, AI 2 cannot set its own validation verdict.** The brief's table
   gives content to AI 2 and "the AI proposes, code decides" needs the code layer able to reject a
   pack. The narrowing is enforced both ways: `put_content(module="code")` raises (the deterministic
   layer cannot author content) and `set_validation(module="ai2")` raises (an author cannot certify
   itself). Both directions are tested.
2. **`code` also holds `approvals`, `preferences` and `session_state`.** The brief lists "evidence
   log, scores"; the other three are the learner's own decisions and the app's own state, which no
   AI may write. This is a superset for the code layer, not a relaxation for an AI: AI 1, AI 2 and
   AI 3 write exactly the sets the brief gives them.
3. **Seven new tables instead of columns on existing ones.** `create_all` cannot `ALTER TABLE`, so a
   column-based design would exist only on a fresh database. The store is therefore additive, and
   revision `0007` makes the same tables exist for an upgraded database.
4. **The old phase-4 path still runs.** Attempts are written to `learner_events`, `bkt_states`,
   `evidence` *and* to the new log. Removing the old writes would break the phase-4 tests that
   read them; the brief says not to weaken tests, so the migration is additive and the remaining
   work (AI 1's analysis reading its inputs from the store) is recorded rather than half-done.


---

## Phase 2 report - the universal mind baseline (section 5)

Status: **complete on everything this environment can execute.** The engine is 40/40 green, the
whole suite is green, the screens exist and were driven in jsdom against the real API, and the
browser-level and live-model claims are in `docs/UNVERIFIED.md` as pending checks with steps.

### Files changed in this phase

* `backend/src/adapt/baseline/{service,flow,games,factors}.py` - the engine (this phase started
  here; the earlier checkpoint commit has the first half).
* `backend/src/adapt/api/routes/baseline.py` - the eight routes, with the ownership check that
  opens its own short-lived session.
* `backend/tests/test_baseline.py` - 40 tests.
* `backend/migrations/versions/0008_baseline_tables.py` - the run/trial tables.
* `frontend/src/lib/baseline.ts` - the typed client and the pure presentation helpers.
* `frontend/src/app/baseline/page.tsx` - every screen of the flow.
* `frontend/src/lib/mock.ts` - the baseline fixtures (the run advances, so `?mock=1` walks it).
* `frontend/scripts-ui-probe.mjs` - the `baseline` scenario: 25 checks.
* `scripts/check-contract.mjs` - see deviation 2.

### Commands run, with results

```
cd backend && ../.venv/bin/python -m pytest -q
  765 passed, 1 warning in 55.74s

cd backend && ../.venv/bin/python -m pytest -q tests/test_baseline.py
  40 passed, 1 warning in 5.29s

cd frontend && npm run lint          # contract + tsc
  contract ok: every path the frontend calls exists, ?mock=1 covers them, ...

cd frontend && npm run build
  compiled; /baseline  8.27 kB

scripts/probe-fresh.sh baseline      # jsdom + live API + real encrypted DB
  25/25 checks passed
```

The full synthetic play-through (a perfect learner) now reaches every stage in the brief's order:
study, cued recall, quick taps, vault, shopkeeper, quick taps, machine, locked box, quick taps,
traps, delayed codewords, snapshot.

### What was actually wrong (each one found by a test or by the play-through)

1. **The run "hung" on SQLite write-lock contention, not an infinite loop.** `Baseline.submit` held
   a write transaction while `state.ingest` opened a *second* session on the same file: every submit
   waited out `busy_timeout = 5000` and then raised `database is locked`. Fixed by passing the
   caller's session to `ingest`/`set_preference` and reading the learner model before the write
   session opens. The route ownership check now uses its own session and nothing holds a request
   session across a service call.
2. **The shopkeeper stage never ran.** `shopkeeper_next` counted *every* trial in the run rather
   than its own game's, so the stage looked complete the moment anything else had been measured: it
   was reported "complete, 0 trials", the learner never saw the distraction task, and "recall under
   distraction" was silently missing from the working-memory factor.
3. **Two routes had an undefined session.** `GET /baseline/snapshot?run_id=...` and
   `GET /baseline/next-day?run_id=...` called `_owned(s, ...)` with a session that no longer existed
   - a `NameError` behind a branch no test entered. Both covered now.
4. **The locked box re-served itself forever** and recorded nothing about what the learner did: the
   trial never stored the action (probe/hint/attempt/walk away). Both fixed; the box now ends on a
   solve, a walk-away, or three attempts.
5. **`step_size` claimed "measured"** with no working-memory measurement behind it, and the
   machine's "noticed the switch" read a typed sentence instead of the scored probe output.

### Deviations (and why)

1. **The vault tiles accept number keys (1-9) as well as arrows and Enter.** The brief requires
   keyboard operation, which the tiles have. The number keys were added because the jsdom probe
   cannot drive a React synthetic key handler, and because they are genuinely faster for a keyboard
   learner. Asserted in the probe.
2. **`scripts/check-contract.mjs`'s generic-argument matcher was narrowed** from a paren-balanced
   run to `<[^()<>]*>`. The old pattern was greedy and matched across lines, so
   `post<BaselineView>("/api/v1/baseline", {})` followed by another generic call parsed as one call
   whose path was the *later* string - which meant real call sites were never checked against the
   route table or the mock table. After the fix the checker sees 72 frontend calls instead of 53.
3. **The study screen is labelled "looking time - not scored", not "measured".** A `phase: "show"`
   task is looking time; calling it measured would claim a trial that was never scored.

### Still open in this phase

The browser checks in `docs/UNVERIFIED.md` (flash on a real device, 45-second clock, 360px layout),
and screenshots into `docs/screens/` - both need a browser, which cannot be installed here.

---

## Integration report 1 - the landed agent PRs, and §3 on the wire

*Integrator session, 2026-10-13. PRs #9 (evidence/calibration), #10 + #14 (phase 7 security) and #12
(master prompts) merged into `arena/06d446fc-adapt`; #11 and #13 landed on `main` and are reconciled
at the final merge. Agent 2 (phase 3) has not landed. The working note with the defect analysis is
`agent-notes/7-integration.md`; what follows is the report.*

### What was merged, and what was actually wrong

The merges themselves were clean. What was not clean was the interaction between the phase-7 secrecy
boundary and the routes the other phases had added. `secrecy.py` drops any response key whose
normalised name contains `provider`, `llm`, `model`, `token`, `budget`, `quota` or `headroom` - and
that silently ate learner-safe fields: `voices`, `model_version`, `needs_model`, `provider_note`, the
lesson explain payload, `/status`, `/quotas`, `/health` and the resource search. Ten tests failed at
the merged tip for this one reason.

The fix direction matters and is now a rule in the code: **the boundary stays strict and the routes
change**. Re-allowing a banned word so a payload survives is wrong - the canary route walk in
`tests/test_config_secrecy.py` and the metadata test at :439 assert those words are *absent*, and
those tests are the authority. So the routes now publish learner vocabulary instead:

* `served_by` ("your own material" / "written for you") replaces `provider` and `provider_note` on
  `/explain`, `/analyze`, `/tutor/message` and the WebSocket reply metadata.
* `/health` and `/status` carry `lessons{state, says, note}` - "Lessons: ready" / "Lessons: simple
  mode" - and `data_protection`, which is the same encryption facts as the operator's report minus
  paths, key source and driver configuration (the engine stays: "the file is encrypted" is not
  checkable without it, and an encryption engine is not a model provider).
* `/quotas` reports `pacing` in learner words (`lesson_requests`, `voice_frames`, `exports`).
* `/profile.shared_state.learner_model` is the learner's own model of themself; the bare `model` key
  is reserved for runtime model identifiers and is gone from every public payload.
* Lesson explains carry `needs_extra_help` and `asked_for_extra_help`; `tried_model` and
  `needs_model` are operator vocabulary and are stripped if they reach the wire.

The operator's view still exists - it is `adapt status` on the console, which prints exactly what
the HTTP surface may not.

### Files changed

* `backend/src/adapt/security/secrecy.py` - `_MODEL_WORD_ALLOWED` reduced to the learner-safe keys;
  new `_PRIVATE_PAYLOAD_KEYS` and `public_payload(data, *, served_locally)`.
* `backend/src/adapt/api/routes/system.py` - `/health`, `/status`, `/quotas`, `/privacy`,
  `lessons_state()`, `data_protection()`.
* `backend/src/adapt/api/routes/tutor.py`, `learning.py`, `lessons.py` - the public shapes above;
  `lessons.py` explain returns the same key set whichever branch answers.
* `backend/src/adapt/ai/ai2.py`, `services/lessons.py` (`as_public_dict`).
* `backend/src/adapt/voice/stt.py`, `tts.py` - engine capability sentences are learner-safe; the
  install hint goes to the log (`docs/VOICE.md` has the commands).
* `frontend/src/lib/types.ts`, `mock.ts`, `engine/types.ts`, `components/chrome.tsx`,
  `components/question.tsx`, `app/page.tsx`, `app/settings/page.tsx`, `app/voice/page.tsx`,
  `frontend/scripts-ui-probe.mjs`.

### Test expectations corrected (each carries a §3 comment saying why)

`tests/test_api_learner_loop.py` (health, analyze, explain, WebSocket meta, the status/quotas/privacy
block rewritten with absence checks, resource sources), `tests/test_crypto_and_encryption.py` (the
two encryption-honesty tests read `data_protection` and still assert what is *not* protected),
`tests/test_lessons_api.py` (public explain key set), `tests/test_state_foundation.py` (component
names asserted against `current().status()`, `learner_model` in the shared snapshot).
`tests/test_config_secrecy.py` and `tests/test_prompt_injection.py` were **not** touched.

One test-only defect was fixed because it was order-dependent rather than app-dependent:
`test_private_routes_refuse_anonymous_callers` used the session-scoped client, whose cookie jar holds
whatever an earlier test signed in - so `/dashboard` answered 200 to a supposedly anonymous caller
whenever a valid cookie happened to be present, and 401 otherwise. It now clears the jar and restores
the exact cookie objects around the check.

### Commands run, with results

| Command | Result |
|---|---|
| `pytest -q` (backend) | **887 passed**, 74.7 s |
| `npm run lint` (contract + `tsc --noEmit`) | contract ok - 88 routes, 78 calls |
| `npm run build` | compiled successfully |
| `npm run test:engine` / `test:lib` / `test:server` | 15 / 18 / 2 passed |
| `scripts/probe-fresh.sh all` | **234/234 checks passed**, exit 0 |
| bundle scan of `frontend/.next/static` | no provider/model name (only Next's own `Googlebot` regex) |

### Found by scanning the built bundle, not the source

Two §3 violations lived only in shipped client code: `/voice` printed
`pip install faster-whisper kokoro` with both engine names in tags and fallbacks, and the mock's
capability strings named them too. Both are gone; the commands are in `docs/VOICE.md`. The same scan
found the *backend* capability sentences naming their package on an authenticated HTTP surface -
also fixed, and the probe now asserts the payload names no engine.

### Still open

* Agent 2's phase 3 (the subject round) has not landed.
* `main`'s #11 and #13 wait for the final merge.
* Anything only a real browser or a live model key can settle is in `docs/UNVERIFIED.md`.
