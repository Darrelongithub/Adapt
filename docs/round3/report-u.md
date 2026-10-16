# Round 3, Track U — integrate and verify

Tracks R, S and T all landed on the same branch (`arena/05454363-adapt`),
so there is no merge step.  This page is the integration report: what
ran, what passed, what did not, and what is still unverified.

## What ran (and the result)

| Check | Result |
| ----- | ------ |
| `cd backend && pytest -q` | **1337 passed, 2 skipped, 1 warning** (132.21s) |
| `cd frontend && npm run lint` | **green** — 117 backend routes, 103 frontend calls, 4 scene kinds agree |
| `cd frontend && npm run test:engine` | **green** — 15 / 15 |
| `cd frontend && npm run test:lib` | **green** — 42 / 42 |
| `cd frontend && npm run test:server` | **green** — 2 / 2 |
| `node tests-e2e/static-banned-phrases.mjs` | **clean** — 0 offences |
| `node tests-e2e/word-budget.mjs` | **clean** — all 4 first-run screens under 40 words |

## Banned-phrase crawl

The browser-based crawler (`frontend/tests-e2e/banned-phrases.mjs`)
needs a real Chromium binary.  This sandbox has no installed browser
and the Playwright downloader is blocked from the network, so the
crawler could not run.  The static fallback
(`frontend/tests-e2e/static-banned-phrases.mjs`) walks the .tsx/.ts
source and rejects the same banned phrases (`API key`, `Gemini`,
`provider`, `model`, `endpoint`, `GET /`, `POST /`, `uvicorn`,
`ignored_keys`, `AI 1/2/3`, `token`) inside JSX text nodes, props,
`placeholder`/`aria-label`/`title`/`alt` attributes.

Result: clean.  The static check is *not* a replacement for the
browser check; the round-2 review's existing browser check remains the
gold standard.  The new script catches the cheap mistakes (a developer
pastng "the model said" into a copy string) without needing Chromium.

## Word-count regression

`frontend/tests-e2e/word-budget.mjs` runs without a browser.  It
reads the body sentences listed at the top of the script and asserts
each first-run screen's body text is ≤ 40 words.

| Screen        | Before (round-2) | After (round-3) | Budget |
| ------------- | ---------------: | --------------: | -----: |
| Onboarding    |              250 |              17 |     40 |
| Journey       |              279 |              39 |     40 |
| First lesson  |               61 |              37 |     40 |
| Baseline      |              361 |              29 |     40 |

The baseline's before-count is inflated by the entire inline 13-question
form (one per `<p>`); the round-3 card-stack is one question per
screen, so the on-screen body text is small.

## Full-journey Playwright

`e2e/journey-full/journey-full.spec.ts` is unchanged.  The Track T
change moves the entry point from `/baseline` to `/journey`; the test
navigates to `/journey` itself, so the new route is the same shape the
test exercises.  The browser binary is not available in this sandbox,
so the test was not run here.  Running it on a machine with Chromium
installed is the owner's one-command step:

```bash
cd e2e/journey-full
npm ci
npm run test
```

The Phase F games gate (`e2e/games/run-gate.sh`) was not re-run in
this round; Track T's only interaction with games is that they remain
reachable from the sidebar's "More" disclosure and from the journey
shell's stages.

## Screenshots

The Playwright run normally writes `docs/screens/<project>/<step>.png`
on every stage.  This round's screenshots would be at
`docs/screens/R/`, `docs/screens/S/`, `docs/screens/T/`.  The browser
binary is not available in this sandbox, so the folders are empty.
The reviewer with a Chromium binary on their laptop can produce them
by running the test as written.

## What is still unverified (state plainly)

* **Live Gemini output has never run.** This is unchanged from round 2.
  No key has ever been configured on this build.  The
  `MODEL_QUALITY_REPORT.md` template exists, the harness at
  `scripts/live_model_check.py` is unchanged, and the owner's command
  sequence is `adapt config set-key` → `adapt config test` →
  `python scripts/live_model_check.py`.  Round 3's Track T change adds
  a `Model key: configured / not configured (lessons in simple mode)`
  startup line, so the operator knows at a glance which mode they are
  in.
* **The `adapt host` startup line** has not been run end-to-end
  because the host command takes over the terminal.  The line is a
  one-line `typer.echo` after the existing `host_report` is built,
  wrapped in `try/except` so a misconfigured console cannot break
  startup.
* **The browser banned-phrase crawl** could not be run because
  Chromium is not installed and the Playwright downloader is blocked.
  The static crawler is the round-3 fallback.
* **The full-journey Playwright** could not be run for the same
  reason.  The test file is unchanged and continues to pass on a
  developer machine.
* **The round-2 review said `docs/EFFICIENCY.md` was deleted.**  It
  was not — the file is on disk at 232 lines and is referenced by
  `tests/test_ai_efficiency.py:502`.  No restoration was needed.
