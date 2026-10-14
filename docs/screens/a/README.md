# Screenshots — Part A

None could be captured in this sandbox: there is no browser here (no
Chromium/Chrome/Playwright anywhere, and `/opt/pw-browsers/` does not
exist), and `playwright install` must not be run. See
`docs/round2/unverified-a.md`.

To capture them where a browser exists, start the API (`:8000`) and web
(`:3000`) servers and screenshot, signed out and signed in:

- `/` — the "What is recorded about you" list readable signed out (task 1),
  and the status line reading "Lessons: simple mode".
- `/contrast` — after running a comparison: stated items labelled "stated",
  the demo-profile provenance sentence, and the plain footer (task 7).
- `/settings` — the consent and recorded panels (task 2).
