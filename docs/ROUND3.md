# Round 3 — what shipped

Round 3 turned the round-2 first-run into a screen that teaches, into
a lesson page that does not refuse to teach, and into a flow that
walks a new learner from sign-in to first lesson without a wall of
copy.  Four agents worked in parallel (R, S, T), then one integrated
(U) — this page is the round's summary; the per-track reports are in
`docs/round3/report-r.md`, `report-s.md`, `report-t.md` and
`report-u.md`.

## The shape of it now

* **Choices look like choices.** Every option across the first-run
  flow is a filled, large (≥ 48 px) card with a hover, a focus ring,
  and a clearly different "selected" state.  The format pick is three
  illustrated cards; the 13 questions are a card stack — one at a
  time, a thin progress bar, a back button.  No more 1 px outlines.
* **Body copy is short.** No first-run screen is over 40 words of
  body text.  Privacy, "why we ask", "how it works" all sit behind
  small "why" taps.  Welcome is one sentence and two toggles.
* **The sidebar is honest.** A new learner sees Home, Learn, Review,
  Progress.  The other eight destinations are under a "More"
  disclosure that opens by default when the current route lives there.
* **The lesson writer teaches.** The "no checked material in your
  selected language" refusal is gone — the cause was the teaching
  sentences overlapping a check answer, not the language.  The fix
  picks a different item; the notice is reserved for the actual
  language problem.  The word "Pack" no longer appears in learner
  text.  The four-copies-of-one-MCQ bug in the reteach view is gone:
  the validator rejects plans with duplicate check stems, and the
  reteach rotates to a fresh item when memory says the prior one was
  used.
* **Answers register themselves.** The first-lesson page no longer
  asks the learner to grade their own answer.  A click sends the
  option to the server, the server grades it (the key never reaches
  the browser), the screen reacts: a brief confirmation for a
  correct pick, an automatic misconception-specific explanation for a
  wrong one.
* **The journey is the flow.** A new learner is routed to `/journey`
  after sign-in.  The page is one screen at a time, auto-advance,
  progress dots — not a 324-word explainer of the path.
* **The API key is documented.** README has a new "Add your Gemini
  key" section with the exact commands; `.env.example` ships at the
  repo root as the fallback; `adapt host` prints one line at startup
  saying whether the key is configured, without ever printing it.

## What each track landed

| Track | What landed |
| ----- | ----------- |
| R | Choice cards on every option, format pick with three illustrated cards, 13 questions as a card stack, body copy under 40 words with a static regression check, sidebar 4 + "More". |
| S | Lesson writer no longer withholds key_idea/why_it_works when the key overlaps a check answer; duplicate-stem rejection in the validator; `_INTERNAL_PACK` regex; "Pack" removed from learner text and the system prompt; server-graded `/api/v1/lesson/grade` endpoint; first-lesson page reacts to the server verdict; noisy `authored_item_unverified` log line is now debug-level. |
| T | New learners routed to `/journey` (one-screen flow); `.env.example` at the repo root; README "Add your Gemini key" section; `adapt host` prints "Model key: configured / not configured (lessons in simple mode)". |
| U | Integration: backend 1337 passed, frontend lint/test:engine/test:lib/test:server green, static banned-phrase scan clean, word-count regression check clean across all four first-run screens. |

## What is not proven (and the one-command owner run)

* **Live Gemini output has never run.** This is unchanged from round
  2.  No key has ever been configured on this build.  The owner's
  one-command sequence is unchanged:

  ```bash
  adapt config set-key     # hidden prompt; the key is never a command argument
  adapt config status      # configured · …ab12  /  not configured
  adapt config test        # one minimal request: pass or fail
  python scripts/live_model_check.py   # writes MODEL_QUALITY_REPORT.md
  ```

  The round-3 change is the startup line: `adapt host` now prints
  `Model key: configured` or `Model key: not configured (lessons in
  simple mode)` so the operator knows which mode they are in.

* **The browser banned-phrase crawl and the full-journey Playwright
  could not be run in this sandbox.** Chromium is not installed and
  the Playwright downloader is blocked.  The static
  banned-phrase scan and the static word-count regression check are
  the round-3 fallbacks; both are clean.  On a developer machine with
  Chromium, the existing `e2e/journey-full/journey-full.spec.ts` and
  `frontend/tests-e2e/banned-phrases.mjs` run unchanged.
