# Agent 1 — baseline implementation note

## Scope and ownership

The implementation adds the baseline run screens and typed helpers under `frontend/src/app/baseline/` and `frontend/src/lib/baseline.ts`, and finishes the baseline service, task progression, scoring/factor handling, and regressions under `backend/src/adapt/baseline/` and `backend/tests/test_baseline.py`. The Mind check is linked from the shared navigation. Baseline answers are code-scored; no live model is used to judge these tasks.

`frontend/src/lib/mock.ts` and the shared frontend types are frozen for this workstream and were not edited. Existing integrator-owned API/server wiring was left in place. The contract linter reports the baseline mock gap rather than silently treating the real API as available under `?mock=1`.

The UI probe's `baseline_screen` scenario is intentionally run against the real local API, not `--mock`: it creates a learner, tests start/format/context/study/resume/stop/snapshot, and then uses clearly synthetic responses to visit the task stages and prove the run terminates. Synthetic probe data is test traffic, not a learner result or evidence about any learner.

## BASELINE MOCK FIXTURE INTEGRATION

Do not edit the frozen file in Agent 1's patch. `node scripts/check-contract.mjs` currently identifies these seven missing fixture routes:

```text
GET  /api/v1/baseline/{runId}
GET  /api/v1/baseline/snapshot
POST /api/v1/baseline
POST /api/v1/baseline/{runId}/format
POST /api/v1/baseline/{runId}/questions
POST /api/v1/baseline/{runId}/trial
POST /api/v1/baseline/{runId}/finish
```

When the integrator releases `frontend/src/lib/mock.ts`, implement a **stateful, in-memory, explicitly synthetic** baseline fixture. Do not add seven independent static rows and call the flow complete: `mockLookup(method, url)` currently has no request-body argument, and the `trial` response must advance the run. The route dispatch must be before the generic `/baseline/{runId}` matcher so `snapshot` is not mistaken for a run ID. A precise merge plan is:

```diff
*** Begin Patch
*** Update File: frontend/src/lib/mock.ts
@@
 const rows: Row[] = [
+  // Keep the recorded API table for stateless routes. Baseline is stateful, so dispatch
+  // it in mockLookup before this table rather than adding a static POST /trial row.
@@
 export function mockLookup(method: string, url: string) {
+  const baseline = baselineMockLookup(method, url);
+  if (baseline) return baseline;
   for (const row of rows) {
*** End Patch
```

Implement `baselineMockLookup` in that file with one fixture run ID (`base_mock_0001`) and one cursor over a short recorded screen sequence. Match the exact method/path pairs above, with these responses/transitions:

| Request | Fixture behavior |
| --- | --- |
| `POST /api/v1/baseline` | Reset the fixture cursor and return a `BaselineView` in `kind: "format"`, `stage: "format"`, `status: "in_progress"`, `run_id: "base_mock_0001"`, `student_id: learner.student_id`, the three real format options, empty `history`/`answers`, `timers_visible: true`, and the format/question/task completion report. |
| `GET /api/v1/baseline/{runId}` | Return the cursor's current view; preserve the same run ID so the resume button returns to the saved synthetic step. |
| `POST /api/v1/baseline/{runId}/format` | Move to `kind: "questions"` and include the 13 optional questions with their real keys/options. |
| `POST /api/v1/baseline/{runId}/questions` | Move to a `codewords_study` task with eight fictional code/place pairs and `study_ms: 45000`; no correct-answer key should be included. |
| `POST /api/v1/baseline/{runId}/trial` | Advance one deterministic fixture task at a time through at least one example of each rendered game (`codewords_cued`, `quick`, `vault`, `shopkeeper`, `machine`, `locked_box`, `trap`, `codewords_delayed`). Every returned body must be a complete `BaselineView`. For machine/vault/locked-box fixtures, omit hidden answers/rules; for shopkeeper, omit distractor answer keys. |
| `POST /api/v1/baseline/{runId}/finish` | Set the fixture to `sealed` and return its partial `BaselineSnapshot`; this must remain true if finish is pressed from any task screen. |
| `GET /api/v1/baseline/snapshot` (and `?run_id=...`) | Return `not_started` before the mock run, `in_progress` while open, or the sealed snapshot after finish. |

Use the local `BaselineView`/`BaselineSnapshot` contracts in `frontend/src/lib/baseline.ts` as the exact payload shape. Each view needs `run_id`, `student_id`, `status`, `stage`, `stage_title`, `kind`, `task`, `progress`, `format_options`, `questions`, `answers`, `timers_visible`, `history`, `completion`, and `measured_trials`. Each snapshot needs all eight `factors`, `state`, `measured`, `total`, and `completion`. For unmeasured fixture factors set `state: "not_enough_data"`, `range: null`, `n: 0`, a plain-language `statement`, and empty `actions`/`detail`; never fill a gap with zero or a made-up range. A measured mock factor, if included, must have a finite two-number `range`, a positive `n`, and a sentence that names the sample count. Keep the existing mock-mode banner and mark every fixture-only value as synthetic; do not describe fixture results as saved server data or learner evidence.

The current probe's contract assertion only verifies that this exact mock integration plan is documented. It does not claim that `?mock=1` supports baseline yet.

## Verification status

- Targeted baseline suite: `cd backend && ../.venv/bin/python -m pytest -q tests/test_baseline.py --disable-warnings --maxfail=1` — **47 passed, 1 warning**.
- Full backend suite: `cd backend && ../.venv/bin/python -m pytest -q --disable-warnings` — **772 passed, 1 warning in 73.41s**.
- Frontend `npm run lint` passed: contract scan checked **83 backend routes against 75 frontend calls**, then `tsc --noEmit` passed. The only mock gaps are the seven documented frozen-file routes above.
- `npm run probe -- --only baseline_screen` against the real local API/web services — **81/81 checks passed**. This includes all baseline task stages, start/resume/stop/snapshot, keyboard arrow+Enter vault interaction, eligible/ineligible wagers, hidden timers, and honest factor cards, using synthetic test responses only.
- `node --check frontend/scripts-ui-probe.mjs && node --check scripts/check-contract.mjs` passed.
- No real-browser, visual-layout, accessibility-tree/screen-reader, screenshot, or live-model verification has been performed. Do not report those checks as verified. Baseline marking is local code, not a live-model feature.

## Phase 2 pending browser checks

Run the real API and web server from the repository with a disposable local test account, not production learner data:

```sh
cd backend && ../.venv/bin/uvicorn adapt.api.app:app --host 0.0.0.0 --port 8000
# in another shell
cd frontend && npm run dev
# in a third shell, for the real-API jsdom integration probe
cd frontend && npm run probe -- --only baseline_screen
```

The jsdom probe has passed, but it is **not** an actual-browser check. On a machine with a browser, perform these exact manual checks:

1. Open `http://localhost:3000/baseline`, sign in with a disposable account, click **Start the mind check**, choose one format, and click **Continue**. Leave the 13 context questions blank and click **Save answers and start**.
2. On **Spy codewords · study**, verify eight pairs render, **Ready** is disabled, and the 45-second clock counts down. Before it reaches zero, try the button once (it must remain disabled); after it reaches zero, click **Ready — continue**.
3. On the codeword practice prompt, verify there is no wager. Answer it, then verify a 0–3 **Confidence wager (optional)** appears on the measured codeword question. Select 2, submit an answer, and verify the next task loads. Continue through the four **Quick taps** items.
4. At **Vault lights**, wait until the flashing ends. Use Tab to enter the grid, press **ArrowRight** and **ArrowDown**, then **Enter**. Confirm focus visibly moves, Enter increments “You have tapped”, and the focus indicator remains visible. Use Tab to leave the grid; verify the separate **Check sequence** and **Clear taps** controls work.
5. Use the footer **Leave this screen**, then click **Continue where I left off** on `/baseline`. Verify the same run and next task resume. On a task screen click **Stop here and see my snapshot**; confirm the run seals and the snapshot has eight factor cards, honest `n`/ranges where measured, plain-language statements, and “Not measured yet” where evidence is insufficient (no fabricated zero range).
6. Start another disposable run and choose **timers stress me**. On later measured tasks, confirm timers and wagers are hidden. Confirm practice, the delayed-recall prediction, and machine probes do not show a wager; eligible measured choice/recall questions do.
7. Repeat at a narrow phone width and a wide desktop width. Keyboard through the format radios, optional questions, game controls, and stop action; inspect the accessibility tree/screen-reader labels for the nine vault tiles and their arrow/Enter instructions.

Save screenshots from that actual-browser pass using the repository convention `docs/screens/baseline-*.png` (for example `baseline-start.png`, `baseline-vault-keyboard.png`, and `baseline-snapshot.png`). No screenshots or actual-browser results are being claimed here. Do not use synthetic probe values to characterize a real learner. No live-model verification was performed; baseline tasks are code-scored and are not sent to a model.
