# Unverified — Phase D

Do not treat anything on this page as passed. It is the list of things Phase D could **not** check in
the environment it ran in, with the exact command that would check each one.

## 1. There is no browser here, so nothing was seen

* `/opt/pw-browsers/chromium` does not exist (`/opt` contains only `yarn-v1.22.22`), there is no
  `chromium` / `chromium-browser` / `google-chrome` / `firefox` on `PATH`, and `playwright` is not in
  `frontend/node_modules`. `playwright install` was **not** run (it is forbidden here, and the
  download hosts are not reachable anyway).
* Consequence: **`frontend/tests-e2e/journey.mjs` has never been executed.** It is written, it passes
  `node --check`, and it is the only artefact in this phase that can judge layout — but it is an
  unrun script, not a result. To run it:

  ```
  cd frontend
  npm i -D @playwright/test && npx playwright install chromium   # or point ADAPT_CHROMIUM at a binary
  node tests-e2e/journey.mjs                                     # writes docs/screens/D/*.png
  ```

* Consequence: **there are no screenshots** in `docs/screens/D/`, and none were fabricated. The
  folder explains what the script writes.
* Consequence: Phase A's crawl `frontend/tests-e2e/banned_phrases.mjs` was also not run after D's
  changes. Its route list is derived from the file tree, so `/journey` and `/onboarding` are in scope
  automatically — but "in scope" is not "passed". D's substitute is the source-level scan in
  `frontend/src/lib/{learnerCopy,journey}.test.mjs`, which is narrower: it reads the *source*, not
  the rendered text of a hydrated page.

### What that leaves unverified in the UI

| claim | status |
| --- | --- |
| the shell lays out at 390 × 844 with no horizontal overflow | unverified (asserted only by the unrun script) |
| the mobile drawer opens, covers the page and closes | unverified in a browser; the jsdom probe only proves the drawer and its labelled control exist in the DOM |
| the 860 px breakpoint behaves (jsdom's `matchMedia` is stubbed and there is no viewport) | unverified |
| buttons are where a finger lands, and nothing covers them | unverified. This is the class of defect Phase F's `placement.mjs` exists for (a full-screen overlay ate the first press while every jsdom check stayed green). D's skip buttons were pressed in jsdom, which has no hit-testing — so "the press reaches React" is verified and "the press reaches the button" is not |
| paint, contrast, focus ring, the mobile focus trap | unverified |
| the "why this lesson looks like this" panel with **real measured levers** | not exercised. The panel renders from `GET /api/v1/baseline/snapshot`; the jsdom walk reaches it with no snapshot rows at all, so only the "nothing recorded yet" branch was seen. The `measured` / `you told us` branches need a learner with a sealed baseline (Phase F's walk) |

## 2. Resume is proven, but not across two devices

The gate was verified at two levels: two independent `TestClient`s sharing a cookie (backend test),
and a second jsdom page with its own document sharing the account's cookie jar (probe `journey`
scenario). What is **not** verified is two physical devices, a real network, or a cookie surviving a
browser restart — all of which are the same code path (state read from the server, nothing in the
client) but none of which were observed.

## 3. The screens this phase hands over to are stubbed, not walked

`frontend/tests-e2e/journey.mjs` answers Phases B/C/F's endpoints with contract-shaped stubs at the
network edge. So even when it is run, it verifies **only** that the shell sends the learner to the
right route. It says nothing about the games, the subject round, the generated lesson or practice —
those belong to their own phases' browser checks.

## 4. The model path is untouched and untested here

No key is configured in this sandbox, so nothing about a "written for you" lesson was observed. The
shell's copy avoids claiming it (`source` is not printed as anything but `simple`/`written for you`
on the screen that owns it), but D did not and cannot verify any live model output.
