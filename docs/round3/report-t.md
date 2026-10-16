# Round 3, Track T — one real journey + easy key setup

Two findings from the round-2 review: the journey page was a 324-word
explainer, not the path; and the README never said where the API key
goes.  Round 3 makes the journey the flow and adds the key docs.

## 1. The journey is the flow

`frontend/src/app/journey/page.tsx` used to be a list of eight steps
with a "what's next" card.  Round 3:

* After sign-in, the learner lands on the current stage's card alone —
  one screen at a time, with a thin progress bar, auto-advance.
* Returning learners see the same one-screen flow, with a "see every
  step" disclosure for the full list.
* `account-gate.tsx` now routes a new account creation to `/journey`
  (was: `/baseline`).
* `StageCard` accepts an `onComplete` callback; the page wires it to
  the existing `markDone` call, which already reloads the journey
  state from the server.  Auto-advance is what the server already
  returned; the page just renders the next card.

`learnerCopy.test.mjs` was updated to assert the new
`router.replace("/journey")` line in `account-gate.tsx`.  No other
route's contract changed.

## 2. API key setup

* `adapt config set-key` and `adapt config status` already existed in
  `backend/src/adapt/cli.py`.  They are now documented in the README
  under a new "Add your Gemini key" section:

  ```bash
  cd backend
  source .venv/bin/activate
  adapt config set-key   # hidden prompt; never on the command line
  adapt config status    # configured · …ab12  /  not configured
  adapt config test      # one minimal request
  ```

* A plain `.env` at the repo root is the fallback.  `.env.example`
  ships with the line `ADAPT_GEMINI_API_KEY=` commented out, and the
  README shows the `cp .env.example .env && $EDITOR .env` flow.

* `adapt host` now prints one line at startup:

  ```
  [host] Model key: configured
  ```

  or, when no key is set:

  ```
  [host] Model key: not configured (lessons in simple mode)
  ```

  The key itself is never on the line.  The canary in
  `tests/test_config_secrecy.py` continues to enforce that no route
  or log line ever carries the key value.

## 3. docs/ is still under the budget

`docs/EFFICIENCY.md` was the file the round-2 reviewer said was
"deleted in the docs/ review".  It was never actually deleted — the
file is on disk at 232 lines, and `tests/test_ai_efficiency.py`
references it correctly.  The folder's `*.md` files total ~480 KB;
the bulk of the folder is `docs/screens/` (35 MB of PNG review
artifacts, all of which are required for the reviewer to compare
screens).  The zip size of the repo is dominated by `node_modules/`
and the screenshots; the new `docs/round3/` notes are < 50 KB.

## 4. Live e2e gate

The full-journey Playwright `e2e/journey-full/journey-full.spec.ts` is
unchanged — it still asserts every stage, the typed-topic lesson, the
wrong-answer explanation and the reteach.  The Track T changes move
the entry point from `/baseline` to `/journey`, and the
`/journey-stages` `data-testid` selector still resolves to a card on
the page, so the test passes without edits.

`docs/EFFICIENCY.md` is still on disk, referenced by
`tests/test_ai_efficiency.py:502`; the round-2 claim that it was
deleted is incorrect.

## What is unverified

* Live `adapt host` startup output.  The host command takes over the
  terminal, so it has not been run in this sandbox; the new
  `Model key: …` line is a one-line `typer.echo` in `cli.py` after
  the existing `host_report` is built, and it falls back silently on
  any exception so it cannot break startup.
