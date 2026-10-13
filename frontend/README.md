# ADAPT web

Next 16 (App Router) and React 19, one client tree per screen, Tailwind 4, and no data layer of
its own: every screen talks to the FastAPI backend through `src/lib/api.ts`, which is the only file
allowed to know what a session cookie, a CSRF token and the `/api/v1` prefix are.

```bash
npm ci
npm run dev          # node server.mjs — binds 0.0.0.0:3000, serves Next in dev
npm run build && npm run start   # the same server, production build
```

`server.mjs` is not `next dev`. It is a small Node server that serves Next and proxies `/api/*`,
`/healthz` and the `Upgrade /ws/voice` handshake to the backend, so the browser only ever talks to
one origin. It rewrites `Host` to the API's own address while forwarding — which is why the API's
untrusted-host refusal never meets a learner's LAN address, and why `adapt host` can serve a room
without trusting a subnet — and it forwards a WebSocket `101` **header for header**, so an
extension the backend negotiated (permessage-deflate) is not silently dropped on the way back.
`PORT` / `HOST` move the listener; **`ADAPT_API`** moves the backend (default
`http://127.0.0.1:8000`; `ADAPT_API=offline` starts the interface with nothing behind it, and every
screen has to say so rather than spinning). Nothing in `src/` reads `process.env` — browser code
that did would ship `undefined` — and `player.test.mjs` fails the build if engine code starts naming
a host.

## The five commands that gate this directory

| command | what it proves |
| --- | --- |
| `npm run lint` | `node ../scripts/check-contract.mjs && tsc --noEmit`. The contract check reads both sides as *source* — no server, no key, no network: every path the frontend calls exists in the API; `?mock=1` has a fixture for each; route modules export only what Next allows; no hard-coded origin, no `process.env` in browser code, no key material; `/ws/voice` really is proxied; `allowedDevOrigins` covers `localhost`, `127.0.0.1` and the preview domain; and the sim modules, the stage registry and the mock's kinds agree on which scene kinds exist. |
| `npm run typecheck` | `tsc --noEmit`. |
| `npm run test:engine` | `node --test src/lib/engine/*.test.mjs` — the player's rules (gate, clamping, timing, reduce-motion) plus two source assertions over `player.ts`, `types.ts` and `components/scenes/*`. |
| `npm run test:server` | `node --test server.test.mjs`: the launcher itself — `ADAPT_API=offline` must refuse with a 503 and keep serving the UI (on its own port and its own output directory), a request carrying a LAN `Host` must reach the API as `127.0.0.1`, and an upgrade must come back with the `101`'s own headers intact. |
| `npm run probe` | jsdom driving the real pages against a running dev server + API: click, type, answer, reload, then compare what the screen says with what the API says. `--only diagnose,lesson` and `--base http://host:port` are the two knobs. |
| `npm run probe:mock -- --only lesson` | the same lesson screen on `?mock=1`, asserting the things the mock *can* honestly show — and that it touches no `/api/v1` endpoint at all. |

The probe creates a throwaway learner through `POST /api/v1/students` (waiting out the signup limit
rather than tripping it), so it needs a live API and a live dev server, and it never reuses a real
learner's rows. `PROBE_FIRST_ITEM_KIND=<kind>` pins which item placement starts on — several
scenarios walk a sequence whose length depends on the answer pattern, so without it a fresh database
can send the tour down a different branch (this is the flakiness `docs/AUDIT.md` F11 recorded, and
the reason a full-suite number is quoted with the flag it was taken with).

## Origins, hydration, and two traps worth knowing

`next.config.mjs` sets `allowedDevOrigins`. To Next's host check `127.0.0.1` is **not** an alias of
`localhost`: leave it out and a page loaded by IP gets its HTML and its CSS, has its `/_next/*`
chunks refused, and paints a *complete-looking* screen whose buttons do nothing — because React never
hydrated. Add whatever a tunnel hands you with `ADAPT_DEV_ORIGIN=https://host,another` (comma
separated); `check-contract.mjs` fails if the three origins above ever disappear.

Two operational traps, both paid for:

* **Do not run `npm run build` while `npm run dev` is serving.** Both use `.next`; a production build
  under a live dev server drops it mid-flight and every page answers 500 with
  `Could not find the module … in the React Client Manifest` until `rm -rf .next` and a restart. When a
  probe run suddenly reports `Resource was not loaded. Status: 500`, look here first. The way to have
  both is a second output directory: `ADAPT_NEXT_DIST=.next-build npm run build` (the same knob
  `server.test.mjs` uses so it never disturbs a running preview).
* **A page that paints is not a page that works.** Dev-mode React under jsdom will happily render
  markup for a component that never hydrated, which is why the probe's checks act (click, answer,
  reload) instead of counting nodes, and why the contract check treats an unhydrated origin as a
  failure rather than a cosmetic detail.

## The lesson screen

`src/app/lesson/` is the interactive engine: a catalogue of scenes, a run opened against
`POST /api/v1/lessons/open`, a stage that draws the backend's own frame, a prediction panel that
grades through `sims/*`, a variables panel whose edit produces *a new scene the server derived*, and
a review that refuses to call itself a grade. The rules — what blocks advancing, how long a caption
stays up, why reduce-motion stops autoplay, what is a row and what is state — are documented in
[`../docs/ENGINE.md`](../docs/ENGINE.md), and `src/lib/engine/player.ts` is the only place they live.

Three things a reviewer of this directory should keep in mind:

* `src/lib/mock.ts` is a **review** surface, not a second backend. It cannot store a run, so in mock
  mode there is no resume, no evidence count, and no derived scene in the list — `probe:mock`
  asserts that absence rather than pretending, and the red banner says it on screen.
* A scene kind this build cannot draw still plays: `components/scenes/index.tsx` routes it to
  `UnknownStage`, which shows the frame's narration and its state values and says the picture is
  missing. Nothing is invented to fill the gap, and the contract check is what keeps the set of
  drawn kinds equal to the set the engine computes.
* `stages.tsx` renders frame text as text nodes only — no `dangerouslySetInnerHTML`, ever — because
  scene narration is authored content. `test:engine` reads the file and fails if that changes.

## What the summary line tells you

The last line prints the number of API calls the harness itself made (a full walk: about 110, plus
whatever each page fetches on its own) and, if the app's limiter paced any of them, says so with the
bucket sizes: `ip` sustains 120/min and learner creation 6/min, and the suite mints a dozen
accounts, so two walks started back to back can cross a window. That is the app working as
designed; the knob the API itself names is `ADAPT_RATE_LIMIT_MULTIPLIER`. A run that fails at a
*different* check each time, with the limiter line absent, is not pacing -- it is a finding.

## Closed: the diagnostic walk selection loss (`numeric-state-reset`)

`diagnose` previously failed its submit checks intermittently (~1 in 10 runs) while every API call
answered 200. Two separate React effects were wiping the learner's in-progress answer:

1. Both `/diagnose` and `/learn` now blank only a screen that is still empty (`painted` ref), so a
   re-running load effect never unmounts a question card mid-answer.
2. `src/components/question.tsx` previously cleared `picked`, `typed`, `teach` and `hintState`
   inside a passive `useEffect([item.id, item.hint_count])`. When the probe (or a fast learner)
   typed into the newly mounted `<input>` inside the commit→effect window, the passive effect fired
   afterwards and wiped the input back to `""`. `Question` now resets its per-item state
   synchronously during render using a derived `shownKey` (`${item.id}|${item.hint_count ?? 0}`)
   and keys `startedItemIdRef` by `item.id` without mutating refs during render (10/10 passes on
   `bash scripts/probe-fresh.sh diagnose` and 5/5 with `PROBE_FIRST_ITEM_KIND=numeric`).

## `npm run test:server` snapshots and restores `tsconfig.json`

Next adds `<the distDir it was given>/types/**/*.ts` to `tsconfig.json`'s `include` whenever it
starts a project it has not seen at that path. `server.test.mjs` snapshots `tsconfig.json` before
spawning `server.mjs`, restores it inside `stopAndSweep`, and asserts that `tsconfig.json` is
byte-identical after each test.

Revert it before committing: `git checkout -- frontend/tsconfig.json`. This is how 1391 scratch
files and three dead `include` paths got committed once already; `tmp/` is now ignored, but the
`tsconfig.json` edit is not something the test can undo for you. Candidates for removing the
symptom rather than the note -- `typescript.tsconfigPath` pointed inside the temp distDir, or
running the test copy with type generation off -- were not tried here, so treat them as open.
