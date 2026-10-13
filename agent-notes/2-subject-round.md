# Agent 2 — Phase 3: the subject round with topic-aware test selection

Spec: `ADAPT_functionality_round_prompt.md` §6; brief: `docs/AGENT_BRIEFS.md` (Agent 2).

## What landed (all inside Agent 2's file ownership)

Backend package `backend/src/adapt/subjects/`:

- `taxonomy.py` — §6.2 skill sets as versioned code constants: 5 families (maths,
  languages, sciences, history/humanities/government, generic fallback), 28 skills, each
  with id, family, title, what it measures, a ~40-second game spec, and a branch-readiness
  implication. `TAXONOMY_VERSION = "subj-taxonomy-2026.10.1"` is stored with every rating
  (`Leanings.as_dict()` / snapshot rows / every evidence payload).
- `topics.py` + `leanings.py` — authored 0–3 lean ratings for the eleven §6.3 topics
  (calculus, discrete maths, algebra, statistics, government/civics, grammar, essay
  writing, biology, chemistry, world history, Kiswahili). `rate_leanings(topic)` returns
  the authored table when present, else asks a model (`role="ai2"`) under a strict JSON
  schema validated and clamped against the taxonomy, else the generic profile
  (`source="generic_fallback"`, labelled as such). Learner text reaches a model only as
  data (`<learner_text>`/`<topic_name>` tagging in `resolve.py`/`leanings.py`).
- `resolve.py` — free text → subject/topic/level hint (authored/curriculum/family/model/
  unresolved), longest-phrase level hints, bank matches against `topic_catalog()`.
- `plan.py` — `plan_tests`: top 3–4 leaned skills (a fourth only on a tie) + one
  cross-check in a different game kind; the cross-check's cost is reserved first and the
  time cap cuts *leaned* skills, never the cross-check and never past the budget. Every
  skipped skill is recorded `not_measured` with a reason; `SkippedSkill.score` raises
  `AttributeError` — skipped ≠ zero by construction, not by convention.
- `ladder.py` + `curriculum.py` — universal ladder, rungs 0–5, 11 authored prerequisite
  chains. Start rung 2 mid-chain, up/down on the staircase, descent down the prerequisite
  chain on rung 1–2 failure, rotation across 3–4 sub-areas, stop after 3 reversals or 9
  items. Graph validation is real: DFS cycle check, depth ≤ 8, missing-prerequisite and
  duplicate checks; cycles, holes and over-deep chains are refused at construction.
- `methodtest.py` — §6.5 pop-up method test: 90-second lessons, 3 matched questions per
  concept, second concept taught another way, same question format across methods.
  Method + order randomised and counterbalanced per learner by a seeded code step
  (12 cells). Gain is measured against *expected* performance (learner-model `p_know` +
  item difficulty via `assessment.selector`), never raw scores; `claim` and
  `learner_style` are always `None`. Methods: text, a real rendered visual spec
  (flow/comparison-table templates), worked example. Next-day recheck (2 of the same
  questions per concept).
- `games.py` — authored per-skill task pools + deterministic/checklist scorers for kinds
  choice / order / match / numeric / text / study.
- `models.py` — `subject_rounds`, `subject_trials`, `method_test_runs`,
  `subject_snapshots`, importing `Base`/`utcnow` from `..db.models`.
- `service.py` — `Subjects.resolve / plan_tests / start_round / view / submit_trial /
  ladder_step / method_test / finish_round / snapshot / due_rechecks / submit_recheck`.
  Reads `p_know` from the store snapshot; **never advances the learner model** (the
  permission matrix forbids `code` writing `learner_model`, and a test asserts the model
  version is unchanged by a round). Evidence goes back via `state.ingest(...,
  module="code", session=s)` with `taxonomy_version` in every payload; kinds
  `subject_skill`, `subject_ladder`, `method_assigned`, `subject_method_test`,
  `subject_method_comparison`. Re-sealing a round raises `round_finished` (HTTP 409);
  a duplicate trial/ladder/recheck submission is answered from the record.

HTTP `backend/src/adapt/api/routes/subjects.py` (one router, prefix `/api/v1`, **not yet
registered** — see wiring below): `POST /topics/resolve`, `GET /subjects/plan`,
`GET /subjects/taxonomy`, `POST /subjects/round`, `GET /subjects/round/{id}`,
`POST /subjects/round/{id}/trial`, `POST /subjects/round/{id}/finish`,
`GET /subjects/snapshot`, `POST /subjects/ladder/{step}`, `POST /subjects/method-test`,
`GET /subjects/recheck`, `POST /subjects/recheck/{id}`. House error shape
`{"detail": {"error": slug, "message": …}}`; 422 refused input, 404 with one message for
missing-or-not-yours, 409 `round_finished`. Every skill-set response carries `reason`.

Migration `backend/migrations/versions/P2_subject_round.py` (`revision="P2"`,
`down_revision="0008"`, existence-aware in the `0008` style; imports
`adapt.subjects.models` so `alembic check` sees the tables).

Frontend:

- `frontend/src/lib/subjects.ts` — typed client for all of the above. Under `?mock=1` it
  serves labelled fixtures from this file (recorded plan sentences for calculus and
  government + a small staged round machine), because `frontend/src/lib/mock.ts` is
  frozen; see the integrator note below.
- `frontend/src/app/subject/page.tsx` — topic box → resolution → plan (reason shown,
  skipped skills greyed as "not measured yet" with no number) → round runner (skill
  tasks incl. order/match/numeric/text kinds, wager picker, ladder with per-sub-area
  rung display, rendered lessons: prose / visual spec / worked steps) → subject map:
  measured skills with `n` and evidence, not-measured skills greyed, ladder position per
  sub-area ("not reached" ≠ rung 0), method legs against expectation, due rechecks.

Probe: `frontend/scripts-ui-probe.mjs` gained a `subject` scenario (17 checks, all
passing): calculus vs government produce visibly different plans with reasons,
not-measured skills carry no number, a full round clicks through (skills → ladder →
pop-up lesson) to the map, and the map repeats the reason and per-sub-area positions.

## Verification (this sandbox)

- `backend/tests/test_subjects.py`: **61 passed** (taxonomy/topic content validated
  programmatically; planted-cycle/deep-chain/hole refusal; staircase properties incl.
  monotone behaviour, termination over all 512 answer sequences × 11 chains, beginner
  bottoming out at rung 0/root; skipped ≠ zero; counterbalance cells; gain-vs-expected;
  HTTP walk; 404-for-strangers; sealed-round 409; recheck; evidence in the shared log
  with `taxonomy_version`; learner model untouched).
- Full backend suite minus `tests/test_baseline.py` (see defect below): **785 passed,
  1 failed** — the one failure is `test_migrations.py::
  test_an_existing_create_all_database_can_be_stamped_then_upgraded`, explained under
  wiring: it is this phase's known pre-integration artifact, not a regression. It passes
  standalone; its sibling `test_upgrade_head_…` fails standalone and passes in-suite, for
  the same import-visibility reason.
- `ruff check src/adapt/subjects src/adapt/api/routes/subjects.py
  tests/test_subjects.py migrations/versions/P2_subject_round.py --select F,E7,E9,B017`:
  clean. `npm run lint` (contract check + `tsc --noEmit`): clean ("checked 95 backend
  routes against 62 frontend calls … contract ok").
- jsdom probe: `node scripts-ui-probe.mjs --only subject` → **17/17 passed**, against
  the real web server with the page in `?mock=1` mode (see unverified list).

## Integrator wiring (frozen files — exact edits requested)

1. **`backend/src/adapt/services/__init__.py`** — give `AppServices` a subjects service:
   add field `subjects: Any = None` (or the concrete type) and construct it in `build()`
   the way the router currently does as a fallback:
   `Subjects(db.sessionmaker, state=state, settings=settings, llm=ai2.llm)`.
   Constructing it before `api/app.py`'s `create_all()` also imports
   `adapt.subjects.models`, which is what makes `create_all` build the four new tables.
   The router's `getattr(services, "subjects", None)` fallback can then be deleted.
2. **`backend/src/adapt/api/app.py`** — register the router: import
   `from .routes import subjects as subjects_routes` (name per house style) and add
   `subjects_routes.router` to the router tuple. Until this lands, `test_subjects.py`
   mounts the router onto the real app itself, and the live subject screen 404s.
3. **`backend/src/adapt/db/init_db.py`** — add
   `from ..subjects import models as _subject_models  # noqa: F401` beside the existing
   model imports, so every `create_all` path (app boot, CLI init) builds
   `subject_rounds`, `subject_trials`, `method_test_runs`, `subject_snapshots`.
4. **`backend/tests/test_migrations.py`** (not frozen, but not mine) — in the `_SHAPE`
   boot script (and the inline boot in
   `test_an_existing_create_all_database_can_be_stamped_then_upgraded`) add
   `import adapt.subjects.models` next to `from adapt.db.models import Base`, mirroring
   what the app will import after edit 3. Without it, that test's create-all-then-stamp
   path can never see the P2 tables while `_expected_schema()` (in a suite process that
   has imported them via `test_subjects.py`) expects them. This is the 1 failure above.
5. **Migration renumbering** — `P2` is a placeholder revision id, currently the single
   head on top of `0008`. If the chain must stay numeric at merge time, rename the file
   to `0009_subject_round.py`, set `revision = "0009"`, keep `down_revision = "0008"`.
   Nothing else references the id.
6. **`frontend/src/lib/mock.ts`** (optional) — the subject screen's `?mock=1` fixtures
   live in `lib/subjects.ts` because this file is frozen for the round. If the integrator
   prefers the shared table, move the calculus/government plan fixtures there as rows
   matching `POST /api/v1/topics/resolve`, `GET /api/v1/subjects/plan`, … and delete the
   mock branch in `subjects.ts`; the probe scenario exercises the screen either way.

## Defect found in someone else's code (reported, not fixed — not my files)

**Baseline evidence is silently lost, and `tests/test_baseline.py` appears to hang.**
`adapt/baseline/service.py` `submit()` calls `state.ingest(...)` *without* `session=`
while its own session holds an uncommitted write. The store opens a second connection,
SQLite's `busy_timeout=5000` expires, `OperationalError: database is locked` is raised and
swallowed by a bare `except` that only logs "could not ingest a trial into the evidence
log". Reproduced standalone: every baseline trial costs ~5 s and **zero** baseline
evidence rows reach the shared log. Consequences: (a) `tests/test_baseline.py` takes
minutes and looks hung — pre-existing on the base commit, it is why the suite numbers
above exclude it; (b) anything downstream of baseline evidence (Phase 1 consumers) is
reading an empty log. Suggested fix for the owner: pass the caller's session
(`state.ingest(..., session=s)`), exactly as `subjects/service.py::_ingest` does and
documents. I did not edit `baseline/` and did not weaken or skip any of its tests.

Also pre-existing and untouched: `test_subject_agnostic_content.py::
test_approving_the_visual_strategy_changes_what_is_served` failed on the base commit in
the earlier floor run; it passes in the final full-suite run recorded above (order
dependent). Flagging it as flaky-adjacent rather than mine to fix.

## Gate status (§6 brief)

- "calculus" vs "government" produce visibly different test sets with displayed reason —
  asserted in `test_subjects.py` (plan contents + reasons) and in the probe (both plans
  rendered, skill sets and reasons differ).
- Skipped skills show as not measured — backend (`not_measured`, `score: null`, `n: 0`)
  and screen (greyed, no number) both assert it.
- Ladder property tests pass (see verification).
- Subject screen covered by the jsdom probe — 17/17.

## Unverified / pending (not claimed as done)

- **Screenshots: pending.** Chromium cannot be installed in this sandbox (no
  libnss3/libnspr4), so there are no screenshots of `/subject`; the jsdom probe is the
  coverage that exists. First thing to do on a machine with a browser: screenshot the
  plan, ladder, lesson (visual leg), and snapshot states, in mock and live modes.
- **The live (non-mock) subject screen** is unverified end-to-end until wiring edits
  1–2 land; the HTTP layer itself is verified by `test_subjects.py`, which mounts the
  real router on the real app.
- **Model paths** (`rate_leanings` via a model, `resolve` refinement) have no API key in
  this sandbox. The strict-schema validation, clamping, degenerate-answer refusal and
  generic-fallback labelling are unit-tested with a fake client; a live model's output
  quality is not verified.
- The method test's "visual" lessons are rendered from authored template specs
  (flow/comparison tables) by the page; pixel-level rendering of those diagrams is
  unverified for the same Chromium reason.
