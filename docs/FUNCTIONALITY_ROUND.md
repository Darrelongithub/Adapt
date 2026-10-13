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
- [ ] Phase 1.4.3 shared state, orchestrator, `adapt rebuild-model`
- [ ] Phase 2 baseline
- [ ] Phase 3 subject round
- [ ] Phase 4 content engine + `QUALITY_REPORT.md`
- [ ] Phase 5 adaptation layer + contrast demo
- [ ] Phase 6 evidence + failure loop
- [ ] Phase 7 security/demo readiness
- [ ] Final report

## 0.5 Phase reports

Appended here as each phase completes (files changed, commands run with results, what is still
unverified).

---

## Phase 1 report — make the existing loop work, and fix the known teaching defects

Status: **§4.1 complete, §4.2 complete, §4.3 not started** — so the Phase 1 gate (which includes
4.3) is **not** passed yet. Everything below is measured; anything I could not run says so.

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

* §4.3 (shared state, Orchestrator, `adapt rebuild-model`) — not started. It gates Phase 2.
* The Playwright script exists but has never been executed here; until it runs on a machine with
  browsers, no browser-level claim in this round is verified.
* `npm run lint` (the repo's contract checker + tsc) has not been run since the frontend edits; only
  `tsc --noEmit` has.
