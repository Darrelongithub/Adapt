# Agent 1 - finish phase 2 and give the baseline screens

This workstream ran in the integrator session (the one that owns `arena/06d446fc-adapt`) rather than
in a separate agent window, which is why there is no `agent/<1>-*` branch: the commits are on the
shared branch, in order.

## Commits

* `cca50d6` - the lock fix, the flow fixes, and the test corrections (see below).
* `a924f3e` - the shopkeeper filter, the two undefined-session routes, and the whole-playthrough
  regression test.
* (the frontend commit) - `frontend/src/lib/baseline.ts`, `frontend/src/app/baseline/page.tsx`,
  the `mock.ts` fixtures, the `baseline` probe scenario, and the contract-linter narrowing.

## The two bugs worth reading about

1. **The "hang" was not an infinite loop; it was SQLite write-lock contention.** `submit()` held a
   write transaction while `state.ingest()` opened a second session on the same file. Every submit
   waited out the 5s `busy_timeout` and then raised `database is locked`. The rule that came out of
   it, and which every future service must follow: *inside `_sess()`, every store call takes
   `session=s`; read the learner model before the write session opens; a route checks ownership in
   its own short-lived session and closes it before calling the service.*
2. **The shopkeeper stage never ran at all.** `shopkeeper_next` counted every trial in the run
   instead of its own game's, so the stage was reported "complete" with zero trials from the moment
   any other game had been measured.

## Test expectations that were genuinely wrong (fixed in the test, with a comment saying why)

* The median of two pauses is their midpoint (26 500, not 31 000).
* The reason factor's wording is "proved you wrong".
* Calibration counts only scored, measured trials - an unscored trial has no `correct` to compare.
* A perfect vault run needs ~30 trials, so a 60-trial cap cannot assert the stage order (200 now).
* The *HTTP response* for a practice trial may grade the tap; what must stay `None` is the **stored**
  `correct`, and that is what the test asserts.
* `GET /baseline/next-day` has no `state` key (an earlier assertion of mine invented one).

## Gates, with the real output

```
backend:  pytest -q                    -> 765 passed in 55.74s
backend:  pytest -q tests/test_baseline.py -> 40 passed in 5.29s
frontend: npm run lint                 -> contract ok
frontend: npm run build                -> /baseline 8.27 kB
ui probe: scripts/probe-fresh.sh baseline -> 25/25 checks passed
```

## Phase 2 pending browser checks

See the phase 2 section of `docs/UNVERIFIED.md` (flash on a real device, the 45-second clock,
keyboard walk, coins, stop-and-resume, 360px layout, screenshots into `docs/screens/`).

## Frozen-file needs

None left open. `frontend/src/lib/mock.ts` and `scripts/check-contract.mjs` were edited by the
integrator session itself, which is allowed to; no other agent's files were touched.
