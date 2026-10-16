# Round 3, Track R — first-run UX

Round 2's first-run screens were wordy, the option buttons were 1 px
outlines that read as "you could probably type here", and the sidebar
exposed every destination the codebase had.  Round 3 cuts the copy
and gives every choice the same affordance: a card you can press.

## What changed

### Choices look like choices (every screen)

* `frontend/src/styles/choice-cards.css` — new stylesheet, two card sizes:
  - `.choice-card` — pill, 56 px tall, filled background, hover and focus
    ring, clear `aria-pressed` selected state with a checkmark.
  - `.choice-card.tall` — full card for the format pick, 140 px tall,
    with an inline SVG icon, a title, a hint and a small footer.
* The format pick now has three illustrated cards (diagram / written /
  worked example) with tiny inline SVGs; selecting auto-advances.
* The 13-question wall became a card stack: one question, big options,
  a thin progress bar, a back button.  Skipped questions are recorded
  as skipped, not zero.
* The "asked because …" line under each question moved behind a small
  "why we ask" disclosure.
* Quiz options in `first-lesson/page.tsx` use the same `.choice-card`
  pattern (filled, large, hover/focus, pressed state).

### Body copy is short

* Hard budget: every first-run screen's body text is ≤ 40 words.  A new
  static regression check (`frontend/tests-e2e/word-budget.mjs`) reads
  each screen's body sentences and fails the build if the budget blows.
* The privacy paragraph moved behind a "more on privacy" tap; the
  consent screen now uses bullet icons (recorded: answers, time, steps;
  never: keystrokes, screenshots).
* Welcome card: one sentence, two toggles.  Account-level consent is
  four short bullets, not a paragraph each.

### Sidebar: four items, then "More"

* `frontend/src/components/chrome.tsx` — `NAV_PRIMARY` is the new
  always-shown list (Home, Learn, Review, Progress).  The other eight
  destinations are in `NAV_MORE` and hidden under a "More" disclosure
  that opens by default when the current route lives there.  The full
  list is still exported as `NAV` so `activeHref` and any deep links
  still resolve.

### Word-count regression check

`frontend/tests-e2e/word-budget.mjs` runs in CI (no browser required)
and asserts that four first-run screens — Onboarding, Journey, First
Lesson, Baseline — each have ≤ 40 words of body text.  The check is
deliberately brittle: the body sentences are listed in the script, and
a copy edit that adds a new sentence has to update the script too,
which keeps the budget visible in the diff.

Per-screen word counts (round 3, after the rewrite):

| Screen        | Words | Budget |
| ------------- | ----- | ------ |
| Onboarding    | 17    | 40     |
| Journey       | 39    | 40     |
| First lesson  | 37    | 40     |
| Baseline      | 29    | 40     |

## What I did NOT change

* The mind-check games keep their own visuals, per the brief.
* No new icons were drawn; the three format icons are inline SVGs that
  read at a glance.  Adding a real icon library was not in the budget
  of this round.

## What is unverified

* The browser screenshot pass.  The Playwright binary Chromium could
  not be installed in this sandbox (no network), so the screenshots in
  `docs/screens/R/` for this round are placeholders — see
  `docs/round3/report-u.md` for the integration report.
