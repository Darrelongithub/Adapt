# Agent 5 — evidence and the failure loop (phase 6)

**Latest verification (production integration):** the unrestricted backend suite passes
**834 tests**, including all **38 baseline tests**, with one dependency deprecation warning.
The **59 failure-loop + 12 new integration cases** pass against the production router.
Frontend lib **18**, engine **15**, server **2**, lint/typecheck/contract and production build pass.
The user's “Finish then merge” authorized the shared edits and merging PR #9. Browser interaction
and screenshots remain unverified. Earlier sections below are historical and are superseded by
the production-integration report at the end.

## Task list / status

- [x] Audit existing assignment, detectors, strategy posterior and thinner failure path.
- [x] Implement persisted prerequisite → fatigue → alternate-method → explicit matched-B loop.
- [x] Add authenticated HTTP adapter and preference/performance screen.
- [x] Test all attribution branches, seeded simulations, persistence, replay, CSRF and HTTP isolation.
- [x] Run frontend gates/build and backend regression checks; document actual limitations.
- [x] Supply integrator-owned edits below without modifying frozen files.
- [x] Production: register router, replace old attempt loop, freeze server deliveries, wire normal
  outcomes, checked-content contract, model/plan provenance, baseline comparison and navigation.
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


## Continuation — hardening after the initial PR

### Fixes and added coverage

Added **33 backend cases** without loosening any previous assertion (phase-6 total: 59).
Added `frontend/src/lib/evidence.test.mjs` with **five real React SSR tests**, using the existing
TypeScript compiler and React packages; the presentational `Tag` wrapper alone is isolated.
No new dependency and no browser claim.

- Missing final latency no longer reuses an older slow-answer pair for fatigue.
- Opening an old attempt receipt only reads session answers at or before that attempt's
  timestamp/ID. Later tiredness cannot be blamed for an earlier failure.
- An unchecked prerequisite always has zero method attribution, even if fatigue is flagged.
- Recovery questions require nonblank identities/stems, finite bounded pair difficulty,
  gradeable structures, valid numeric constants/tolerances and an unambiguous in-range MCQ key.
  The prerequisite must be a distinct question on a different topic.
- Corrupt registry containers are handled as unavailable content rather than server errors.
  Both raw and rendered pairs are guarded. MCQ choices and teach-back rubrics are also checked
  against the original served A, not just its stem and answer key.
- Invalid logged probabilities, weights, multipliers and nonboolean grades never produce
  estimates. `excluded_n` distinguishes them from `attributed_elsewhere_n` (valid zero-weight
  prerequisite attribution). Both counts are surfaced honestly on the evidence page.
- Self-selection detector factory defaults to **observational** rather than implicitly
  randomised. Hint-opening comparisons now carry the lower source weight as promised.
- Legacy method-difference labels use **effective** n, not raw n. Intervals subtract the actual
  endpoints of the two groups' ranges rather than symmetrising an asymmetric Wilson range.
- Evidence badges honour weighted n even when raw n is larger than the threshold. Missing
  actual/expected measurements say unavailable instead of inventing zero-percent results.
  Pilot labels remain visible at large n; new SSR tests enforce all of these copy guarantees.
- Placement cannot enter the new method-outcome hook or start a teaching-benefit recovery.
- Recovery transitions/responses freeze the source `model_version` and content IDs in `why`.
  Resume retains those original references even if the registry/model later changes. They
  complement, but do not replace, the integrator's plan/delivery provenance.
- A concurrent conflicting-B-answer test proves that both callers see the same winning
  completed transition and exactly one matching grade plus two method outcomes are logged.

New corruption tests initially exposed the intended failures. Their HTTP fixture also uncovered
shared-registry cross-test contamination: the older valid pair was masking the malformed pack.
The fixture was isolated to its own registered content ID, retaining the same 422/no-transition
assertions. No production data is deleted and no existing test is weakened.

### Latest measured commands

```text
cd backend && ../.venv/bin/python -m pytest -q tests/test_failure_loop.py
59 passed, 1 warning in 2.24s

cd backend && ../.venv/bin/python -m pytest -q --ignore=tests/test_baseline.py
784 passed, 1 warning in 62.65s (0:01:02)

.venv/bin/ruff check backend/src/adapt/learning/failure.py backend/src/adapt/services/evidence.py backend/src/adapt/api/routes/evidence.py backend/tests/test_failure_loop.py
All checks passed!

cd frontend && npm run lint
contract ok; tsc --noEmit exited 0

cd frontend && npm run test:lib
13 tests / 13 pass / 0 fail / 0 skipped (8 existing + 5 evidence rendering tests)

cd frontend && npm run test:engine
15 tests / 15 pass / 0 fail / 0 skipped

cd frontend && npm run test:server
2 tests / 2 pass / 0 fail / 0 skipped

cd frontend && NEXT_TELEMETRY_DISABLED=1 npm run build
Compiled successfully; generated static pages (14/14)
/evidence: 4.65 kB, 128 kB First Load JS

git diff --check
exit 0
```

**Still pending:** the frozen production integration edits listed above, Agent 1's baseline
nontermination fix, content-engine interoperability, browser acceptance and screenshots. The
unrestricted suite is not claimed green. This follow-up does not edit any frozen shared file.


## Production integration — authorized by “Finish then merge”

### Exact production changes

- Registered `/api/v1/evidence` in `app.py`. Recovery tests no longer mount their own router;
  they assert the endpoint exists in the production OpenAPI contract.
- Practice and method-probe serving persist `method_offered` metadata, assignment/exposure,
  pre-answer model prediction and source model version. Placement offers are session-owned.
  Submission reads this record: client method/design/expected echoes cannot promote observational
  data to controlled evidence or rewrite expectations. Without a recorded delivery, evidence is
  observational. Repeated questions and voluntary hint/example/explanation/visual support are
  observational too, weighted 0.4. Frontend telemetry flushes before grading/attribution.
- Removed the old scalar `_reteach_step` production path. Failed A now starts the checked,
  resumable prerequisite/fatigue/alternate-method/matched-B engine. Missing validated pairs or
  prerequisite questions produce an explicit `unavailable` receipt; no similar-item or fabricated
  comparison is substituted. Failed A is deferred, including legacy active-strategy credit.
- Successful nonplacement attempts log exactly one idempotently addressed method outcome.
  Legacy approved-strategy credit uses actual-minus-expected and the proper source weight.
  Placement updates knowledge only; it is excluded from method effects and recovery.
- `/methods/report` now reads attributed outcomes rather than prematurely penalising undiagnosed
  misses. Unattributed methods have n=0 and unavailable estimates, not zero-percent accuracy.
  Pilot data retains its explicit labels; `n_scored` is distinguished from `n_attributed`.
- The immutable derived model folds in method/strategy estimates from the same evidence log.
  Recovery grades advance that model and replan/record delivery without resetting the current
  block. A replay cannot add another B grade or another pair of method outcomes.
- Content-engine validation now uses the adapter's `validated` status only after an explicit
  checker `ok=True`; absent checkers are `unvalidated`, absent/false verdicts are rejected.
  Explicit A/B identities, answer specifications and teaching variants are still required.
  This does **not** claim that the existing generator supplies checked matched pairs for every
  topic. A bank without them remains honestly unavailable; synthetic packs exist only in tests.
- Baseline draft comparison reads real adjusted method outcomes. Sealing freezes that comparison
  in the existing encrypted snapshot payload, including explicit null when evidence is insufficient;
  subsequent preferences/outcomes cannot rewrite it. No new table or migration was needed.
- Receipt recovery links prefill the attempt ID and resume the saved check. Evidence is in the
  main navigation. Demo reports have no measured outcomes and demo recovery returns 422 honestly.

### Baseline regression repairs uncovered by integration

The previously timed-out file is now included, not ignored or skipped. The root SQLite write
problem was a baseline trial transaction calling a second writer for evidence ingestion. Ingestion
now shares the trial's transaction. Once it ran promptly, seven genuine failures were repaired:

- Persist machine response actions so its bounded rounds actually terminate.
- Suppress practice correctness in the public result as well as the stored measurement.
- Accept wagers only on measured, scored, wager-eligible questions.
- Retain successful trap-resistance pauses separately from all trap pauses (intuitive errors remain).
- Align falsifying-test copy with its stated mechanism and label unmeasured step size as default.
- Filter next-day trials to that game, rather than treating all baseline trials as next-day answers.
- Include the free-recall anchor when generating delayed recognition tasks.

All existing baseline assertions pass unchanged. Two foundational integration assertions were
updated for the new contract: additional recovery metadata must not duplicate the single scored
attempt; practice delivery IDs must be real nested item IDs, not `[None, None, None]`.
The malformed-content tests retain their 422/no-transition assertions, isolating the test's pack
**before** automatic recovery. A new checked-pack fixture initially leaked its validation into the
shared test registry; a finalizer restores its unvalidated status, preserving test isolation rather
than hiding the failing missing-content assertion.

### Final measured commands

```text
cd backend && ../.venv/bin/python -m pytest -q --tb=short
834 passed, 1 warning in 72.04s (0:01:12)

cd backend && ../.venv/bin/python -m pytest -q tests/test_evidence_integration.py tests/test_failure_loop.py --tb=short
71 passed, 1 warning in 3.42s

cd backend && ../.venv/bin/python -m pytest -q tests/test_baseline.py
38 passed, 1 warning in 6.90s

cd frontend && npm run lint
contract ok: 87 routes / 56 calls; tsc --noEmit exited 0

cd frontend && npm run test:lib
18 tests / 18 pass / 0 fail / 0 skipped (includes 10 real React SSR/mock regressions)

cd frontend && npm run test:engine
15 tests / 15 pass / 0 fail / 0 skipped

cd frontend && npm run test:server
2 tests / 2 pass / 0 fail / 0 skipped

cd frontend && NEXT_TELEMETRY_DISABLED=1 npm run build
Compiled successfully; generated static pages 14/14
/evidence: 4.79 kB / 129 kB First Load JS
```

Scoped Ruff and `git diff --check` pass. A broader Ruff inspection of shared legacy files still
reports style findings, so repository-wide Python lint is **not** claimed green. GitHub reported
no PR checks; these are local regression results. The sole pytest warning is Starlette's httpx
TestClient deprecation. No browser automation, screenshots, live model call, symbolic formula
verification or teaching-quality validation is claimed. The user has explicitly authorized
merging PR #9; GitHub is the authoritative record of the merge result.

### Merge with the advanced base branch (phases 2 and 7 landed while this PR was open)

GitHub reported `CONFLICTING` after the base gained Agent 1's baseline screens/fixes and Agent 6's
security work. Resolved locally with `git merge` of the base head `2010b98`, keeping both sides:

- `baseline/factors.py`: the base's test defines `median_pause_ms` as the median over **all** trap
  pauses (26 500 in its fixture); kept that contract and exposed successful-resistance pauses as a
  separate `median_successful_trap_pause_ms` detail. Restored the base's exact falsifying-test
  sentence, which its test asserts verbatim. Lever `step_size` is `default` when unmeasured (both
  sides agreed).
- `baseline/service.py`: the base already shares the trial transaction with the evidence log and
  persists the learner's machine/locked-box action; kept its version plus this branch's scored-action
  stamp, measured-only wagers and the frozen real preference/performance comparison. Dropped this
  branch's suppression of practice `scored.correct` in the HTTP body: the base's test states that a
  practice tap is still graded as feedback while the **stored** measurement stays `None`.
- `frontend/src/lib/mock.ts`: both new row groups kept (evidence rows first, then baseline rows).
- `frontend/src/app/baseline/page.tsx`: the sealed comparison now carries `sentence` from the
  evidence report; the screen reads it (falling back to the older `statement`) and links to `/evidence`.
- Agent 6's `test_config_secrecy.py` was left exactly as merged: its `/status` test deliberately
  skips when the route requires authentication, so the unrestricted run reports **1 skipped**.

Post-merge verification on the merged tree:

```text
cd backend && ../.venv/bin/python -m pytest -q -rs --tb=short
874 passed, 1 skipped, 1 warning in 83.37s (0:01:23)
SKIPPED [1] tests/test_config_secrecy.py:309: /status requires authentication

cd frontend && npm run lint
contract ok: 87 backend routes against 78 frontend calls; tsc --noEmit exited 0

cd frontend && npm run test:lib   -> 18 pass / 0 fail
cd frontend && npm run test:engine -> 15 pass / 0 fail
cd frontend && npm run test:server -> 2 pass / 0 fail
cd frontend && NEXT_TELEMETRY_DISABLED=1 npm run build -> 15/15 pages, /evidence 4.79 kB / 133 kB
```
