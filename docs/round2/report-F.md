# Phase F report — baseline games and subject round

Branch: `arena/c6003112-adapt` (from `main` at `4c027b7`). Owned files: `backend/src/adapt/baseline/*`, `backend/src/adapt/subjects/*`, the baseline and subject pages, and the new `e2e/games/` suite. Cross-phase requests are in `handoff-F.md`; what is unverified is in `unverified-F.md`.

## Result

**The gate passed.** All games and the subject round passed headless on desktop and mobile, and the keyboard-only baseline passed, twice in a row (`sh e2e/games/run-gate.sh`, exit 0):

| Run | desktop (1280×900) | mobile (390×844) | keyboard |
|---|---|---|---|
| 1 | 5 / 5 | 3 / 3 | 1 / 1 |
| 2 | 5 / 5 | 3 / 3 | 1 / 1 |

Retries are 0 in the config, so any flake would have failed the run. None did.

## What landed

Commits on `arena/c6003112-adapt`, oldest first:

* `b9d0d33` backend baseline fixes, subject tests, and the test helper that feeds the simulated learner the server's internal answer key.
* `d938704` baseline and subject page fixes.
* `227fd6c` subject choices as pressed buttons; the `e2e/games` suite.
* `99ce6f9` the subject round also runs on the phone.
* `2d27482` styling for the baseline page.

Plus one docs commit with this report.

### Defects found and fixed

Found by the new tests or by the browser suite:

* **Answer keys were served to the page.** The served task carried `answer`, `intuitive`, `explain` and `distractors`. They are now stripped from the task; the server keeps them internally.
* **Vault direction flipped after the first trial**, and the **locked box reused a task id**, which froze the stage.
* **The second trap question was never served.**
* **Delayed recognition index was off by one**, and **machine practice probes were double counted** (and switched off by one).
* **Factors read verdicts from the wrong field.** Falsifying rate, noticed, hint cost and free recall read the top-level response instead of the trial ledger, so they showed zero. Fixed to read the ledger.
* **Next-day check refused answers.** `GET /api/v1/baseline/next-day` now takes `?finished=true`, so the return visit asks about the last finished sitting, not the open one.
* **Hints:** a hint could be bought twice, and an unaffordable hint was accepted. Both are refused now, and the locked box's coins are enforced by the server.
* **The snapshot was listed as a step.** It is no longer a measured step; the flow has 13 steps.
* **Attention band had zero width.** It is clamped at ±0.1.
* **"Tudor history" matched silently.** A non-exact match now carries a near-match note.
* **"Expected" leaked into measured replies**, and **shared free-text state** carried between tasks.
* **Keyboard:** Enter sent twice; the vault's roving index assumed 0; predict could not Tab to its target.
* **Browser page:** a duplicate `<main>` landmark; the step label; choices were radios (now `aria-pressed` buttons); the locked box's state was local rather than the server's; the subject "met" rung had no control, so a learner could not pass it.
* **Visual (found by reviewing the screenshots):** the baseline's choices, inputs, vault tiles and snapshot rows used class names no stylesheet defined. They ran together on screen. Fixed with `frontend/src/app/baseline/baseline.css`, scoped under `.baseline-page`.
* **Visual:** the subject page's choices used `role="radio"`, which the brief rules out. They are now buttons in a labelled group.
* Copy: "0/0 traps spotted", "(next step)", and the "traps not reached" wording.

### Requirement by requirement

1. **Playwright scripts for every game and the full subject round, desktop, mobile and keyboard.** Every baseline game is played through the page on desktop and mobile. The keyboard project plays the whole baseline with Tab, arrows, Enter, Space and typing, and no clicks. The subject round is played on desktop and mobile with the mouse (not keyboard-only). Server-recorded metrics are checked against what the script did: every trial's verdict and stored choice are compared with the answer the suite knows to be right, and the snapshot's numbers (immediate and delayed recall, recognition, next-day, falsifying rate, hints, hint cost, wrong attempts, walked away) are recomputed from the answers. Phone checks: no horizontal scroll and no tap target under 40 px.
2. **Defects fixed, with backend tests for each.** `backend/tests/test_round2_f_baseline_games.py` (27 tests) covers the baseline defects above.
3. **"Not measured" when a game is skipped; ranges and n, never labels.** `snapshot-honesty.spec.ts` stops at once and checks that every game is "not measured" with no number or range; it stops part-way and checks that measured factors show a range and `n=`, and that unreached stages are named. The subject map is checked the same way: the unreached skill reads "not measured", not zero.
4. **Subject round selection varies by subject; the "why these tests" text is honest.** `backend/tests/test_round2_f_subjects.py` (8 tests) covers maths, languages, history and the unknown fallback. Maths and languages test their own subject skills; history uses an authored plan and says so ("leans hardest"); a subject without written ratings is labelled "fallback", "rather than a finding about your topic"; an unplaceable topic gets the universal six skills and says so. A near-match is not described as the learner's own topic. The browser test checks the plan's text against what was found.
5. **Screenshots of every game state in `docs/screens/F/`, reviewed.** 120 PNGs (about 32 MB) across `desktop/`, `mobile/` and `keyboard/`. Reviewing them found the unstyled baseline, which was fixed and re-checked. Not every frame has been viewed by a person; see `unverified-F.md`.

## Tests and checks

* **Backend, full suite:** `1157 passed, 1 skipped` (`pytest backend/tests`). The F files are `test_round2_f_baseline_games.py` (27) and `test_round2_f_subjects.py` (8), and `test_baseline.py` (40) passes too. No backend file changed after this run; the later changes were frontend only.
* **Frontend:** `tsc --noEmit` passes; `npm run lint` (contract check plus tsc) passes; `test:lib` 24 pass; `test:engine` passes; `npm run build` passes.
* **Existing tests:** none deleted or weakened. The one edit to an existing test file (`test_baseline.py`) adds `_with_answer_key`, a helper that gives the simulated learner the server's internal key so that it can still answer correctly. No assertion changed.

## Environment

* Chromium: `/home/user/.cache/chromium-bin/chromium` (Chromium 153, from `@sparticuz/chromium@153` on npm), run with `LD_LIBRARY_PATH=/home/user/.cache/chromium-bin/lib`, flags `--no-sandbox --no-zygote --disable-gpu`. `/opt/pw-browsers/chromium` does not exist here, so it could not be used, and `playwright install` was not run.
* The suite is `@playwright/test` 1.64.0 in `e2e/games/` (`npm install` there). Generated output (`test-results/`, `playwright-report/`, `results.json`) is git-ignored.

## Git and the branch divergence

Two things differ from the brief, and both come from the session constraint:

* **No `r2/phase-a` branch, no fast-forward into `main`, and no push to `main`.** The session is fixed to `arena/c6003112-adapt`. Work is pushed to that branch only, and any pull request comes from it. Merging is your call.
* **The commit hashes in my notes do not exist in this clone.** My earlier notes listed `2d58a1c`, `5e276d7` and `15e13aa` for the first round of fixes. In this sandbox those hashes are not in the object store, and the branch's history starts from `4c027b7`. The workspace had the same changes uncommitted. I re-committed the working tree as the commits above, so the content is present, but the old hashes can no longer be cited.
* Commits are not rewritten and nothing was force-pushed.

## Open items

These are in `unverified-F.md` and `handoff-F.md`:

* `model_note` is part of the subject API. It is not shown on any page, but its wording mentions "the model", and `test_subjects.py` pins that wording.
* The pace measurement reads automated taps as very fast (median about 50–74 ms), so the snapshot's pace and drift numbers are not a finding about a learner.
* The cue appears twice in cued practice (cosmetic, not fixed).
* The snapshot copy runs two sentences together ("…can stay long On the two questions…"). It comes from the baseline service's statement text and was not changed.
* Subject ratings from a real model, Firefox and WebKit, real devices, screen readers, and keyboard-only subject round are not verified.

## Merge with main (after the first PR)

PR #23 conflicted with `main`, which had moved from `4c027b7` to `693dec6` (phase D and cost-round work). The branch was merged with `main` (a merge commit, not a rebase, so no history was rewritten). Conflicts:

* `frontend/src/app/baseline/page.tsx`: six hunks. Main's side was kept (its `section.wrap` wrappers and the snapshot's dashboard and subject links). Two things from this branch were kept on top: the `baseline-page` class on main's wrappers (it scopes `baseline.css`), and an `onChanged` prop on the snapshot call, which main's call site omitted and which the type check requires.
* `backend/src/adapt/subjects/resolve.py` and `frontend/src/app/subject/page.tsx` merged without conflict. Both sides are present: my near-match note and my choice-button fixes survived, and main's outline and selection flow is in place.

Main's subject flow changed: the subject form is now one "Subject" field with "Preview outline", then a checkbox outline, then "Confirm outline and start". `e2e/games/subject-round.spec.ts` was updated to that flow, and `actionTimeout: 20_000` was added to the Playwright config so a missed selector fails in seconds rather than at the 20-minute test limit (the first attempt at the new flow hung for that long).

After the merge: backend `1268 passed, 1 skipped` (main added tests; all F tests included); tsc, lint, `test:lib`, `test:engine`, and the production build pass. The gate passed twice in a row on the merged code (desktop 5/5, mobile 3/3, keyboard 1/1 each run, exit 0).
