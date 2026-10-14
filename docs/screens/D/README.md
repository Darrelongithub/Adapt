# Screenshots — Phase D (the journey shell)

**This folder is empty of PNGs on purpose.** No browser exists in the environment this phase was
built in (`/opt/pw-browsers/chromium` is absent, `playwright` is not installed, and `playwright
install` was not run), so nothing was rendered and no picture was invented to fill the gap. This is
recorded in `docs/round2/unverified-d.md` §1.

Running the script below produces exactly these files:

| file | what it shows |
| --- | --- |
| `journey-desktop.png` | the shell at 1280 × 900: the progress bar (with its word count), the eight numbered steps, the current step's card, the placement card |
| `journey-desktop-after-skips.png` | the same page after all six skippable steps were skipped: every row reads "skipped — not measured", the path sits on `practice`, and the "What you skipped" card lists them |
| `journey-desktop-resume.png` | a **second browser context** — a closed-and-reopened browser — opened on `/journey`: the same stored stage and the same six skipped rows, read from the server |
| `journey-mobile.png` | the shell at 390 × 844 |
| `journey-mobile-drawer.png` | the mobile drawer open, with `/journey` marked as the current section (`aria-current="page"`, so it is visible to a screen reader too) |
| `journey-mobile-after-skips.png`, `journey-mobile-resume.png` | the mobile equivalents of the two desktop states above |

How to produce them (needs a Chromium and a running app):

```
# terminal 1 — the API
cd backend && uvicorn adapt.api.app:app --port 8000

# terminal 2 — the web app (proxies /api to :8000)
cd frontend && node server.mjs

# terminal 3
cd frontend
npm i -D @playwright/test && npx playwright install chromium
ADAPT_CHROMIUM=/opt/pw-browsers/chromium node tests-e2e/journey.mjs
```

The script also prints a check-by-check pass/fail list (desktop and mobile), asserts the
server-recorded stage state after every skip, and exits non-zero on any failure — so the pictures are
evidence of a walk that passed, not a substitute for one.
