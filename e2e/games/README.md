# Browser suite: the baseline games and the subject round (Round 2, phase F)

Playwright drives the real app: the API on `:8000` and the built web app on `:3000`.

## Run

```sh
cd frontend && npm run build          # the web app must be built (the suite runs `next start`)
cd ../e2e/games && npm install        # @playwright/test 1.64.0
ADAPT_E2E_SERVE=1 npm test
```

* `ADAPT_CHROMIUM` (default `/home/user/.cache/chromium-bin/chromium`) is the Chromium binary: Chromium 153
  from the `@sparticuz/chromium@153` npm package, extracted into `/home/user/.cache/chromium-bin`. It needs
  `LD_LIBRARY_PATH=/home/user/.cache/chromium-bin/lib`. `/opt/pw-browsers/chromium` does not exist in this
  sandbox. Nothing here runs `playwright install`.
* `ADAPT_E2E_SERVE=1` starts the API and the web app if they are not already up (and reuses them if
  they are). `ADAPT_E2E_DATA` picks the API's data directory (default `/tmp/adapt-e2e-data`).
* `ADAPT_CHROMIUM_ARGS` overrides the browser flags (default `--no-sandbox --no-zygote --disable-gpu`).

Projects: `desktop` (1280x900), `mobile` (390x844, touch), `keyboard` (keyboard only).

The gate is: `npm run test:desktop` and `npm run test:mobile` (and the keyboard run) pass headless,
twice in a row. `playwright-report` and `test-results` are generated and git-ignored.

## What is checked

* every game in the baseline, in the order the server serves them, through the page;
* what the server recorded for each trial (the verdict, and the choice it stored) against what was
  sent, one trial at a time;
* the snapshot's numbers (immediate and delayed recall, recognition, next-day check, falsifying rate,
  hints, wrong attempts) against the same numbers computed from the answers;
* "not measured" as a state (never a zero), a range with its n, and no label on the snapshot;
* keyboard only: every action by Tab, arrows, Enter, Space and typing, with no click;
* on the phone: no horizontal scroll, and tap targets of at least 40 px.

Screenshots are written to `docs/screens/F/<viewport>/` at the repository root.
