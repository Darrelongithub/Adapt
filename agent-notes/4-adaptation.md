# Agent 4 — Phase 5: AI 3, the adaptation layer, and the contrast demo

Report in the brief's §12 format. Session note: this work was done on the
session branch `arena/77c6b040-adapt` in a single-agent session, so the
integrator wirings the frozen contract reserves (router registration in
`api/app.py`, mock rows in `frontend/src/lib/mock.ts`, one nav entry in
`frontend/src/components/chrome.tsx`) were applied here and are listed as
deviations below — without them the deliverable could not run at all.

## What was built

**`adapt/ai/adapter.py` (new) — AI 3.** One stateless `LessonAdapter` with a
strict contract: inputs `{levers, subject_map, ladder, content_pack,
recent_evidence, preferences, constraints}` (all plain data; a
`LearnerSnapshot` or its `as_dict()` is also accepted), output one JSON plan
of ordered blocks (`text`, `visual`, `worked_example`, `check`, `predict`,
`explain`, `reflect`), each with content, a content-addressed `chunk_id`, and
a `why` carrying `evidence_ids` and `n`. It imports no other AI module
(pinned by test, in the same style as `test_state_foundation.py`).

* **Levers consumed, each visibly:** `lead_format_prior` / measured method
  evidence (n ≥ 8, actual-vs-expected via `learning.methods.method_report`) /
  approved strategies → what leads; `step_size` → word cap per block
  (30/50/75); `confidence_prompting` → confidence check or evidence-of-success
  reflect; `hint_policy` → carried on every check; `review_halflife_prior` →
  review timing on the closing reflect; `language` (en/sw/mixed) →
  scaffolding language; `tone` → closing words; `pace == short_blocks` → a
  planned mid-lesson break; `timers_visible` → carried in `limits`;
  fatigue flag → a break that explicitly does not mark the learner down.
  Ladder rungs shape the plan (predict-first at rung 5, teach-back
  `explain` at rung ≥ 4, default rung 2). Teaching memory stops the same
  explanation being re-read: recall first instead.
* **Code-enforced limits:** cap may be tightened by constraints but never
  loosened past the measured step size; every explanation chunk is followed
  by exactly one check question; sentences are grouped, never cut (a single
  over-cap sentence travels whole and is flagged `oversize_sentence`); the
  plan-level invariant gate re-checks all of this for deterministic *and*
  model-built plans. With no pack, the adapter builds an honest
  recall/reflect skeleton that says there is no checked content yet — it
  never invents prose.
* **Model proposals:** `validate_model_plan` rejects whole (never repairs)
  on: malformed shape, invented markup, placeholders, injection
  (`ignore the checklist…` included), over-long blocks, a check that is not
  exactly one question, an unchecked final teaching block, duplicate chunk
  ids, an invalid picture spec, wrong language, over-dense reading level,
  contradiction of the pack's stated answer or of a pack fact, and any
  reference to an example the lesson has not shown. Rejection — and a model
  callable that raises — falls back to the deterministic template adapter
  with the reasons recorded on the plan (`rejected_proposal_reasons`,
  transparency panel fallbacks).

**`adapt/lesson/templates.py` (new) — the seven visual templates.**
`flow`, `comparison_table`, `timeline`, `labelled_diagram`, `number_line`,
`venn`, `bar`, with aliases normalised at the door. Schema validation
returns readable `{code, message, path}` errors; `render_svg` re-validates
and draws deterministic, escaped, accessible SVG (the Venn circles do, in
fact, overlap — pinned by a geometry test). Only `flow` is derivable from
plain prose; every other shape needs structure prose doesn't carry, and the
code says so rather than guessing. Invalid specs fall back to the spec's own
words with a note (`fallback_text`).

**HTTP (`api/routes/lesson_plan.py`, new).** `POST /api/v1/lesson-plan`
(levers default to the signed-in learner's baseline snapshot, subject map
to their store snapshot, pack to the body's → the topic's authored material
via `concept_versions_for` → an honest skeleton; the plan is recorded
through `Orchestrator.plan` and teaching memory via `state.remember(...,
module="ai3")`, so a failed record never costs the lesson),
`POST /api/v1/lesson-plan/contrast` (exactly two profiles or a 422 in the
house shape), `GET /api/v1/lesson-plan/visual-kinds`. No model/provider/key
wording anywhere in payloads (§3); the mode is `deterministic` or
`model_validated`, nothing more.

**Frontend.** `components/visual/` (a TS validator mirroring the backend
schema + seven React SVG renderers + words-fallback with note) and
`app/contrast/`: concept picker (fractions / photosynthesis / water cycle,
demo content labelled as written for the screen), two labelled made-up
demo profiles, a differences table read off the returned plans, both
lessons block-by-block with per-block whys (evidence ids and `n`), and the
"Why this lesson looks like this" panel (rows, what-would-change-it,
fallbacks, mode note). A "same profile twice" switch is the honesty check:
the endpoint computes `visibly_different: false` and the screen says the
profiles produced the same lesson. Neither page text nor API copy names an
internal component (pinned by test over the live responses).

## Files changed

| File | Change |
|---|---|
| `backend/src/adapt/ai/adapter.py` | new — AI 3 (above) |
| `backend/src/adapt/lesson/__init__.py`, `lesson/templates.py` | new — visual templates |
| `backend/src/adapt/api/routes/lesson_plan.py` | new — 3 routes |
| `backend/tests/test_adapter.py` | new — 83 tests |
| `frontend/src/components/visual/validate.ts`, `visual/index.tsx` | new |
| `frontend/src/app/contrast/page.tsx` | new |
| `backend/src/adapt/api/app.py` | **wiring (deviation):** import + register `lesson_plan.router` |
| `frontend/src/lib/mock.ts` | **wiring (deviation):** recorded-shape fixtures for the 3 new routes |
| `frontend/src/components/chrome.tsx` | **wiring (deviation):** one nav entry, "Compare lessons" |

No migration: the adapter writes only through the store's existing
`lesson_plans` / `deliveries` / `teaching_memory` tables (0007). No new
dependency. `state/**` was not touched; every store write goes through the
frozen `LearnerState`/`Orchestrator` interfaces with `module="ai3"`.

## Commands run, with real output

| Command | Result |
|---|---|
| `pytest -q tests/test_adapter.py` | **83 passed** (template validation/rendering incl. Venn geometry, plan contract, word caps, sentence integrity, lead selection, approved-visual change, memory, 14 adversarial cases ×2 (rejected + fallback), clean-model acceptance, model-raises fallback, injection-through-content, contrast difference + honesty, re-plan keeps the learner's block, HTTP build/contrast/422/visual-kinds, no-internal-names) |
| `pytest -q` (all of `backend/`, `--ignore=tests/test_baseline.py`) | first run **804 passed, 1 failed** — the failure was mine (see below); after the fix, final run: **808 passed, 0 failed** in 81 s |
| `pytest -q tests/test_baseline.py` | **hangs at the synthetic play-through on pristine `HEAD` too** (verified in a clean worktree: 5 tests then no progress in 60 s). This is Agent 1's documented known bug (`AGENT_BRIEFS.md`: "The full synthetic play-through does not terminate"), not a regression from this work; no baseline code was touched |
| `npm run lint` | contract ok — **86 backend routes vs 53 frontend calls**, scene kinds agree, `tsc` clean |
| `npm run test:engine` / `test:lib` / `test:server` | 15/15, 8/8, pass (server suite green) |
| `npm run build` | succeeds; `/contrast` 7.23 kB |
| `npm run probe -- --only progress` | **20/20** (sidebar carries the new "Compare lessons" entry and still names every section) |
| live API (`uvicorn` :8000, fresh `/tmp` data dir) + `curl` | guest learner → `POST /lesson-plan` 200, visual lead with derived flow spec + SVG; plan recorded |
| jsdom drive of `/contrast` (mock) | differences table (`opens with: visual / worked example`, `words per step: 30 / 75`, …), both why-panels, 1 SVG figure, DEMO DATA banner |
| jsdom drive of `/contrast` (live, signed-in learner) | same screen against the real API: `number of blocks 9 / 7`, first block `visual / predict`, no error card |

**A failure this work caused, and the fix.** This file sorts first
alphabetically, so its HTTP tests signed a learner into the session-scoped
TestClient before `test_api_learner_loop.py::test_private_routes_refuse_
anonymous_callers` ran; `/api/v1/dashboard` then answered 200 to "anonymous".
The existing test was right and was not touched: `test_adapter.py` now
hands the client back anonymous after every test (`client.cookies.clear()`).

**An environment artefact worth recording.** Running `npm run build` while
`npm run dev` was serving corrupted the shared `.next` directory (`Cannot
find module './453.js'`, every page 500, probe mock checks failing). It
looked exactly like a regression. Deleting `frontend/.next` and restarting
the dev server restored 20/20. Never build against a live dev server.

## Deviations from the frozen contract (and why)

1. **`api/app.py`, `lib/mock.ts`, `components/chrome.tsx` edited here.**
   The brief reserves them for the integrator to avoid six-way merge
   conflicts; this session has one agent and no integrator, and without the
   router registration the endpoints 404, without the mock rows
   `npm run lint` fails mock parity, and without the nav entry the headline
   demo is undiscoverable. The edits are the exact wirings an integrator
   would have applied from a note: one import + one tuple entry, three
   fixture rows + their fixtures, one nav item. Nothing else in those
   files was touched.
2. **No `services/__init__.py` change.** The adapter is stateless, so the
   route constructs plans through the module-level `adapter` instance and
   reaches the store/baseline through the existing `current()` services.
   If the integrator prefers it on `AppServices`, adding
   `adapter: LessonAdapter` beside `baseline` in `build()` is a two-line
   change; the routes would read `current().adapter` instead.
3. **Templates also live partly in `learning/visuals.py` (pre-existing).**
   The new `lesson/templates.py` is the schema-validated SVG layer the
   brief names; the adapter converts the older visual dicts (`nodes`,
   `labels`) into template specs at pack normalisation rather than editing
   the older module, which other phases own.

## Not verified (and the exact checks owed)

* **No real browser, no screenshots.** Chromium cannot be installed in
  this sandbox. The contrast screen was verified in jsdom (mock + live),
  which proves hydration, the fetch and the rendered DOM — not layout.
  Owed on a machine with a browser: open `/contrast` at desktop and phone
  widths; switch all three concepts; toggle "the same profile twice" and
  confirm the screen says the lessons are the same; confirm the bar chart
  and the differences table are legible side by side; check the sidebar
  drawer below 860 px with the new eighth entry. Save shots as
  `docs/screens/contrast-*.png` per repo convention.
* **No live model.** There is no API key here, so `model_validated` plans
  were exercised only with stub proposals in tests (including the
  adversarial set). Owed when a key exists: wire a real proposer into
  `LessonAdapter.adapt_with_model` at the call site, run the same
  adversarial suite against its raw output, and confirm rejections fall
  back with readable reasons.
* **The failure loop (Phase 6) re-planning through the adapter** is proven
  at the Orchestrator level (`test_a_plan_replans_mid_lesson_without_
  moving_the_learner`), but the end-to-end "wrong answer → adapter
  re-plans the rest of the lesson in the learner's session" path needs
  Agent 5's `attempts.py` change, which is theirs to make; the adapter
  side is ready: rebuild blocks with `adapter.plan(...)` and hand them to
  `Orchestrator.replan(...)`.
* `npm run probe` **full run** was not re-run end to end (it takes several
  minutes and its baseline-file counterpart hangs for pre-existing
  reasons); the affected surfaces (sidebar, progress mock/lane checks)
  pass, and the contract linter covers every new route and mock row.
