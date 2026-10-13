# Agent 5 — evidence and the failure loop (phase 6)

## Task list / status

- [x] Audit existing assignment, detectors, strategy posterior and thinner failure path.
- [x] Implement persisted prerequisite → fatigue → alternate-method → explicit matched-B loop.
- [x] Add authenticated HTTP adapter and preference/performance screen.
- [x] Test all attribution branches, seeded simulations, persistence, replay, CSRF and HTTP isolation.
- [x] Run frontend gates/build and backend regression checks; document actual limitations.
- [x] Supply integrator-owned edits below without modifying frozen files.
- [ ] Integrator: register router, replace old attempt loop, wire normal outcomes and content packs.
- [ ] Browser acceptance / screenshots after integration (no browser installed here).

## Files changed

New:
- `backend/src/adapt/learning/failure.py`: pure diagnostic/ranking functions, evidence-derived
  summaries, and persistent standalone `FailureLoop` using the encrypted append-only store.
- `backend/src/adapt/services/evidence.py`: server-only `start_for_attempt(svcs, s, student_id,
  attempt_id)` adapter: owns attempt, checked pack, original rendered A, direct prerequisite,
  chronological session history, frozen A/B predictions. Raises `KeyError` for unowned/missing
  attempts and `FailureError` for unavailable checked content.
- `backend/src/adapt/api/routes/evidence.py`: authenticated/rate-limited, CSRF-protected
  start/answer routes; report/resume routes; whitelisted public questions.
- `backend/tests/test_failure_loop.py`: **26** tests (including parameterised cases).
- `frontend/src/app/evidence/page.tsx`: preference/performance, weighted method evidence,
  posterior means and resumable recovery; relative same-origin HTTP, existing teaching renderer.
- `docs/EVIDENCE.md`: contracts, statistical caveats, attribution/source distinction and
  exact pending browser acceptance steps.
- This report.

Existing:
- `learning/methods.py`: seeded per-concept permutations, optional whole-run counterbalancing
  ordinal; strict-format probe with no different-format filler; source metadata; seeded ties;
  weighted actual/expected summaries, missing-prediction exclusion, placement exclusion;
  reteach labelled observational; refusal of incompatible-format method deltas.
- `learning/patterns.py`: remove false visual random-assignment claim, explicitly observational
  visual detector, source/weight fields in every observation, mixed method groups not controlled.
- `learning/strategies.py`: actual posterior mean, validate weights, zero weight is a no-op.
- `learning/effect.py`: before/after comparison not called an experiment; insufficient-evidence copy.
- `frontend/src/components/evidence.tsx`: existing method panels show interval, weighted n,
  source and source weight alongside their numbers.

No new dependency, table or migration. No frozen file edited. No existing test deleted,
skipped, weakened or xfailed.

## Verification — real commands and results

Interpreter created with `python3 -m venv .venv` and `pip install -e 'backend[dev]'`; frontend
installed with `npm ci --prefix frontend`. Install logs were temporary, not retained in Git.

Final checks:

```text
cd backend && ../.venv/bin/python -m pytest -q tests/test_failure_loop.py
26 passed, 1 warning in 1.77s

cd backend && ../.venv/bin/python -m pytest -q tests/test_failure_loop.py tests/test_detectors.py tests/test_the_loop_closes.py
56 passed, 1 warning in 1.78s

cd backend && ../.venv/bin/python -m pytest -q --ignore=tests/test_baseline.py
751 passed, 1 warning in 66.71s (0:01:06)

.venv/bin/ruff check backend/src/adapt/learning/failure.py backend/src/adapt/api/routes/evidence.py backend/src/adapt/services/evidence.py backend/tests/test_failure_loop.py
All checks passed!

cd frontend && npm run lint
checked 87 backend routes against 55 frontend calls
contract ok; tsc --noEmit exited 0

cd frontend && npm run test:engine
15 tests / 15 pass / 0 fail / 0 skipped

cd frontend && npm run test:server
2 tests / 2 pass / 0 fail / 0 skipped

cd frontend && npm run test:lib
8 tests / 8 pass / 0 fail / 0 skipped

cd frontend && npm run build
Compiled successfully; generated static pages (14/14)
/evidence: 4.56 kB, 128 kB First Load JS

git diff --check
exit 0
```

The Python warning is the dependency's Starlette/httpx deprecation; no application warning.

**Whole suite is NOT reported green.** An unrestricted `pytest -q` stalled after 105 completed
tests; it was stopped after the 180-second wait timed out. A diagnostic
`pytest -vv tests/test_baseline.py` showed five passing tests followed by nontermination at
`test_the_stage_order_is_the_order_the_brief_asks_for`; it too was stopped. This is consistent
with Agent 1's documented `flow.next_task_for` nontermination bug. Those files are unchanged
here. The explicit `--ignore` command above measures the rest of the suite; it does not change
pytest configuration or mark any test skipped. The baseline suite still needs Agent 1's fix.

New tests initially caught a nested diagnostic-item fixture mismatch and, during the service
adapter extraction, a leftover HTTP exception reference; both were corrected and all 26 new
tests rerun. Frontend type checking caught the `get()` response wrapper on resume; fixed and
all frontend gates rerun. No assertion was relaxed.

Known-behaviour simulation: 30 seeded synthetic learners, 240 exposures per method across
12 topic strata, visual method given a known +0.25 success lift; assertion requires recovery
in at least 27/30 runs (chance = 1/4). It passes. This is **synthetic test data**, never seeded
silently into a learner's production evidence log.

The real HTTP test uses the app's actual middleware, authentication, attempt grading, encrypted
store and CSRF handling. It submits a wrong bank answer, first verifies missing checked content
returns 422, registers a test-only validated explicit pair, checks prerequisite where present,
submits B, resumes the saved result and proves another learner gets 404. Router registration is
in a test fixture only, which restores the original route list afterwards.

## Exact integration edits (owned by integrator)

### 1. Register the router (`backend/src/adapt/api/app.py`)

```diff
-from .routes import adaptations, auth, baseline, learning, lessons, system, tutor
+from .routes import adaptations, auth, baseline, evidence, learning, lessons, system, tutor
@@ router tuple in create_app()
         baseline.router,
+        evidence.router,
         system.router,
```

No `AppServices` field or service-builder edit is necessary: `FailureLoop(svcs.state)` has no
private mutable cache. The new service adapter accepts the existing services and DB session.

### 2. Replace the thinner path (`backend/src/adapt/services/attempts.py`)

Replace the call to `_reteach_step` at the comment
`# ---- phase 5: the loop. Fail -> diagnose -> reteach -> matched -> compare ---- #` with:

```python
from ..learning.failure import FailureError, FailureLoop
from .evidence import start_for_attempt  # local import avoids adapter/import cycle

next_step, compare = None, None
if correct is False:
    try:
        with services.scope() as s:
            next_step = start_for_attempt(services, s, student_id, attempt_id)
    except FailureError as exc:
        next_step = {"needed": True, "stage": "unavailable", "reason": str(exc)}
elif correct is True and effective.design != "placement":
    FailureLoop(services.state).record_outcome(
        student_id, event_id=f"method:{attempt_id}",
        method=effective.method, correct=True, expected=float(expected),
        topic_id=item["topic_id"], question_kind=item["kind"],
        design=effective.design,
    )
```

Delete/stop using the old `_reteach_step` only after updating its consumers; no fallback should
silently pick a merely similar B. Its historical outcome claims are not interchangeable with
this loop's `stage/question/outcome` response. The frontend practice integration should either
render that shape or link to `/evidence` with the receipt ID.

**Important strategy-credit hook:** the earlier call to `services.ai1.record_attempt` currently
passes `active_strategies=[...]` before diagnosis. For a failed attempt, pass `[]` there so it
cannot penalise a method before the prerequisite/fatigue rule is known. Preserve ordinary
knowledge tracing. Feed diagnosed method credit from `method_outcome` events, using
`source_weight * payload.multiplier`, and avoid crediting the matched pair twice. The report's
strategy posteriors already fold exactly those rows using `StrategyModel.record`.

Do not backfill historical wrong attempts as controlled method outcomes without diagnosis.
Placement is measurement without prior teaching; it must not become method-benefit evidence.

### 3. Content engine handoff (Agent 3 + adapter)

Map validated phase-4 packs into `matched_pairs` / `variants` as documented in
`docs/EVIDENCE.md`. `a.id` must be the original served bank item ID. Both questions carry the
same `concept_id`, `pair_id`, exact `kind` and difficulty; roles A/B differ, IDs differ, and
answer keys remain server-side. Their arithmetic/source verification remains the content
validator's responsibility, not a claimed consequence of structural matching.

The adapter looks up registry metadata under `concept=attempt.topic_id`, fetches private payload
by registry ID, and requires `validation == 'validated'`. If the final content engine addresses
packs by sub-concept, change this lookup to that authoritative mapping. Do not guess a B from
bank difficulty. Rejected/unvalidated/missing content must continue to refuse.

### 4. Orchestration / immutable model (`backend/src/adapt/state/**`, `services/__init__.py`)

No frozen state implementation is changed here. Prerequisite and matched-B grades are already
code-ingested as `attempt` events. After an answer transition, have the existing Orchestrator
advance the model once and replan remaining blocks when appropriate; do not restart the lesson.
Read `method_outcome` rows into the subject × method derived view with source/attribution weights;
keep the view rebuildable and versioned. No duplicate graded attempt ingestion.

`kind='method_outcome'` intentionally leaves top-level `correct` unset: its correctness is in
`payload`, so a method comparison cannot masquerade as an extra knowledge observation.
Persistent strategy changes still go through approval, never this recovery service.

Pass content IDs and model version to plan/delivery provenance through the existing
Orchestrator when it serves this recovery; the recovery events are not themselves lesson plans.

### 5. Preference snapshot and navigation (Agent 1 / frontend integrator)

- In `baseline/service.py::snapshot`, use
  `FailureLoop(self.state).report(learner_id)['preference_vs_performance']` as the
  `snapshot_payload(..., preference_row=...)` input, after the normal-attempt hook lands.
  Do not use stated preference as performance or compare raw accuracy across difficulty.
- Add a link `<Btn href="/evidence">Preference and performance</Btn>` in the progress page.
- From failed practice receipts, supply the attempt ID to this page or render the returned
  recovery directly. Current standalone screen deliberately requires the receipt ID.
- Frozen `frontend/src/lib/mock.ts`: add an **honest empty** `GET evidence` fixture:
  `{methods: [], strategies: [], preference_vs_performance: null,
  note: 'No measured outcomes yet in this mock.'}`. Recovery mock data, if added, must visibly
  say seeded/demo and must not be mixed with measured data. This change intentionally does
  not edit shared mock/types files.

### 6. Shared reports / pending checks

Copy the browser checklist from `docs/EVIDENCE.md` into the integrator-owned `docs/UNVERIFIED.md`.
Summarise measured results and pending integration in `docs/FUNCTIONALITY_ROUND.md`.

## Not verified, and deviations

- **No browser / screenshots:** `which chromium chromium-browser google-chrome` found none;
  no available browser executable was found in the checked system locations. Did not attempt
  an unavailable-host browser download. Build/HTTP tests are not presented as browser evidence.
- **No live model/real learner study.** Synthetic recoverability tests do not demonstrate teaching
  quality or causal efficacy. Real pack-generation interoperability awaits Agent 3 integration.
- **Whole suite stalls** in the existing baseline loop; the separate regression command is
  explicitly reported, not called a full-suite pass.
- **Integration deliberately pending:** frozen app/store/shared-attempt wiring, shared baseline
  snapshot/mock/report files are not edited. The production `/evidence` page needs the registered
  router to work. HTTP tests prove its handler behaviour with temporary router registration.
- **Source weight versus multiplier:** normal recovery gets the full *observational* weight 0.4,
  fatigue half of that, prerequisite zero. Calling normal recovery weight 1 would contradict
  the requirement not to call second exposure a controlled experiment. Both numbers are shown.
- **Single pair uncertainty:** uses a conservative bounded range, not a fake empirical narrow CI;
  accumulated estimates use approximate statistical intervals with explicit n and caveats.
- **Existing git repository and fixed Arena branch:** did not reinitialise Git or create the
  plan's `agent/5-*` branch; work stays on `arena/a7503734-adapt` as required by this session.
- Report lives here rather than in frozen shared docs. No new heavy dependency.

Next action: integrator lands the six workstreams, applies the edits above, reruns the unrestricted
suite after Agent 1's fix, then runs the browser acceptance checklist before claiming phase 6 done.
