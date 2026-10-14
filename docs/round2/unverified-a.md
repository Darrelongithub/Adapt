# Round 2 — Part A: unverified items

Honest list of what could **not** be verified in this sandbox, and why.
Nothing below is claimed as passing.

## No real browser exists here

The brief says Chromium is at `/opt/pw-browsers/chromium`. It is not:
`/opt/pw-browsers/` does not exist, and no Chromium, Chrome or Playwright
installation exists anywhere in the sandbox (checked `/opt`, `/usr`,
`~/.cache/ms-playwright`, and the frontend's `node_modules` after
`npm ci` — Playwright is only a peer reference in the lockfile). Per the
brief, `playwright install` was **not** run.

Consequences:

1. **The banned-phrase crawl has not run.**
   `frontend/tests-e2e/banned_phrases.mjs` is written and exits 2 with an
   explanation in this environment (verified: it does exactly that). It
   still needs one real run — signed out and signed in — in an environment
   with a browser, e.g.:
   `cd frontend && ADAPT_BASE_URL=http://127.0.0.1:3000 BANNED_PHRASES_KNOWN=/onboarding node tests-e2e/banned_phrases.mjs`
   (dropping `BANNED_PHRASES_KNOWN` once Phase D applies handoff-a.md §1).
   Backend-produced strings rendered by pages (lesson notes, proposal text,
   voice error details) are the most likely remaining hits; see
   handoff-a.md §2.
2. **Task 1's "verify signed-out in browser" is only half-done.** The
   endpoint itself is verified signed-out three ways: pytest (TestClient,
   no session → 200), and live `curl` against uvicorn → 200 with the full
   list. The jsdom UI probe renders the real home page against the real
   backend and its home scenario passed. What is missing is a real-browser
   look at the consent screen showing the list. Not claimed.
3. **No screenshots.** `docs/screens/a/` contains only a note. There is no
   browser to take them with; inventing or mocking screenshots would be
   worse than none.
4. **Real Gemini / written-for-you output** was never exercised (no key in
   this environment, by design). Part A's text changes are all on the
   deterministic simple-mode path; the claim "Lessons: simple mode" is what
   the live server in fact reported during verification.

## What was used instead of a browser

- pytest (1116 passed) including the new Part A regression files.
- The deterministic teaching-quality gate (exit 0, zero flags, 273 questions).
- Live uvicorn + frontend servers with `curl` (privacy 200 signed out;
  access log renders `?key=[withheld]`, no `Logging error`).
- The repo's jsdom UI probe against the real backend: home, settings,
  voice and contrast scenarios all passed (52/52 in the targeted run; the
  probe later stalls in the baseline/lesson-player scenarios, which are
  other phases' areas — recorded in handoff-a.md §3).
