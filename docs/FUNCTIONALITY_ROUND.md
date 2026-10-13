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
