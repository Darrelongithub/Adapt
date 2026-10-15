# Report — Phase D (the journey shell)

Round 2, Part D. Branch: `arena/a4799880-adapt` (this session is pinned to that branch; the merge
into `main` is a handoff item — see `handoff-d.md`).

## What landed, in one paragraph

The app now has **one path with eight steps**, held by the server and readable from any device:
`account → preferences → games → subject → subject_round → snapshot → first_lesson → practice`.
`backend/src/adapt/journey/` holds the frozen stage tuple and the derive/read/write service;
`GET/POST /api/v1/journey/{state,advance}` is the machine; `frontend/src/app/journey/page.tsx` is
the shell that says where the learner is, why that step is next and what skipping it costs, and then
hands over to the screen that already does the work. The setup screen keeps only the account-level
settings (time, voice, consent, two free-text requests) because the thirteen mind-check questions are
the single source for everything a question can ask. Every step can be skipped, a skip is stored as
*skipped — not measured*, and the first step still to do is what a returning learner resumes on.

## The frozen contract, and the collision it ran into

Part 0 freezes:

```
GET  /api/v1/journey              → {stage, stages: [{id, status}]}
POST /api/v1/journey/advance      {stage, status}
```

`GET /api/v1/journey` **already existed** (the round-1 timeline: nodes for the demo walk). Deleting
it would have broken the demo CLI and Phase G's dashboard; replacing it would have silently changed
what that endpoint means. So:

* the state machine lives at `GET /api/v1/journey/state` and `POST /api/v1/journey/advance`;
* the older `GET /api/v1/journey` **spreads the same keys** (`stage`, `stages`) into its timeline
  payload (`api/routes/learning.py`), so a client written against the frozen line works either way.

`frontend/scripts/check-contract.mjs` passes unchanged (`109 backend routes against 95 frontend
calls`), and the contract checker's rule — every path the frontend calls exists *and* has a `?mock=1`
row — is satisfied by two new rows in `frontend/src/lib/mock.ts`.

## Design decisions worth naming

| decision | why |
| --- | --- |
| **Status is derived, not a cursor** | `done` is read from the row that proves the work (baseline run, subject round, snapshot, lesson run, scored attempt); a stored row only ever holds the learner's own *skip* or a stated "I did that elsewhere". There is no counter that can drift from reality. |
| **Evidence beats a stored answer** | A learner who skipped the games and then played them has played them. The API says so and includes a per-stage `evidence` field (`recorded` / `stated` / `none`) so no screen has to guess which kind of fact it is looking at. |
| **Skipped means not measured** | `not_measured: [ids]` in the payload and "skipped — not measured" in the UI, in the same words the snapshot uses. Nothing downstream adds a zero for a stage the learner waved through. |
| **`stage` = first still to do** | Resume is not a stored cursor either: the same query that draws the list answers "where am I?". With everything skipped it answers `practice` rather than nothing. |
| **Only `done`/`skipped` are settable** | `todo` is the absence of a row. Un-answering a stage is not a state change, it is a rewrite of history. Unknown stage/status → 422, never a silent 200. |
| **The shell links, it does not reimplement** | The games, the subject round, the lesson and practice are other phases' screens. The shell states the step, hands over, and prints "why this lesson looks like this" from the learner's own stated answers and the measured levers, each labelled `you told us` / `measured` — never a claim the data does not make. |
| **Placement is not a stage** | The eight unaided questions are measurement, so they stay reachable from the shell (a card with the link) without becoming a ninth step that would make "practice" mean two things. |
| **First and last steps carry no skip button** | The account is the learner row itself (a guest account *is* an account), and `practice` is the loop the path leads to. The API still accepts a `skipped` for any frozen id — a client that really means it is not refused — but the shell does not offer a button that means nothing. |

## Files

**New — backend**

| file | what it is |
| --- | --- |
| `backend/src/adapt/journey/stages.py` | the frozen eight-stage tuple, `first_todo`, settable statuses; docstring table of what derives each stage |
| `backend/src/adapt/journey/models.py` | `journey_stages` table: `(student_id, stage)` unique, `status`, `note`, timestamps |
| `backend/src/adapt/journey/state.py` | `payload()` / `advance()` / `statuses()`; one query per stage, whole rows for the encrypted `completion` column |
| `backend/src/adapt/journey/__init__.py` | the module's public surface |
| `backend/src/adapt/api/routes/journey.py` | `GET /api/v1/journey/state`, `POST /api/v1/journey/advance` (422 `unknown_stage` / `unknown_status`) |
| `backend/migrations/versions/0009_journey_state.py` | the table; `down_revision = "P2"` (see the migration note below) |
| `backend/tests/test_round2_phase_d_journey.py` | 14 tests: order and shape on both routes, skip → row → resume from a second client, evidence beating a stale skip, per-stage derivation, placement clamps, refusal of `todo`/unknown ids, and a banned-phrase scan of the new copy |

**New — frontend**

| file | what it is |
| --- | --- |
| `frontend/src/lib/journeyPath.ts` | the stage ids, the eight steps (title / action / href / what / what skipping costs), `isBehind`, `percent`. Imports nothing |
| `frontend/src/lib/journey.ts` | the hook: `useJourney` (read the state, advance it) + re-exports of the pure half |
| `frontend/src/components/journey.tsx` | `ProgressBar` (with `aria-valuetext` words, not only a width), `StageList` (numbered rows, `aria-current="step"`), `StageCard`, `SignedOutPath` |
| `frontend/src/app/journey/page.tsx` | the shell route |
| `frontend/src/lib/journey.test.mjs` | 4 tests: the frontend stage order equals `backend/src/adapt/journey/stages.py::STAGES`, every step has n/title/what/action/href/skip note, no banned word in the copy, `isBehind`/`percent` semantics |
| `frontend/tests-e2e/journey.mjs` | the Playwright walk: desktop + mobile, B/C screens stubbed at the network edge, every stage fact read back from the server, screenshots into `docs/screens/D/`. **Not run here — no browser. See `unverified-d.md`** |

**Changed**

| file | change |
| --- | --- |
| `frontend/src/app/onboarding/page.tsx` | rewritten: account-level settings only (minutes a week, two free-text requests, consent ×2, voice + voice choice). The thirteen-question block, the `session_minutes` / words-per-explanation fields and the "Allowed preference keys — from the API" panel are gone; the `not stored:` key list is replaced by one sentence that names nothing |
| `frontend/src/app/page.tsx` | the door and the signed-in home both lead to `/journey`; the home's single primary action reads the path, not a menu |
| `frontend/src/components/chrome.tsx` | the sidebar leads with `/journey` "Your path"; **"Mind check" stays as its own item** for returning learners |
| `frontend/src/lib/bannedPhrases.mjs` | `ONBOARDING_DEFERRED` and `stripDeferred` deleted (A's handoff asked for exactly this once the page was fixed); `bannedHits(text, route)` ignores the route now |
| `frontend/src/lib/learnerCopy.test.mjs` | +3 tests: no developer panel on the setup screen, the screen asks nothing the mind check asks, the shell and the sidebar both lead to the one path |
| `frontend/src/lib/mock.ts` | `journeyState` fixture + rows for `/journey/state` and `/journey/advance`, placed **above** the timeline row (the mock is first-match-wins and the timeline regex matches `/journey/state`) |
| `frontend/scripts-ui-probe.mjs` | the home handoff, the onboarding scenario and the progress sidebar re-pointed; a new `journey` scenario (8 rows in order, the marked step, skip → server, resume in a second page, placement still reachable); a `tickBox` helper because jsdom hydrates concurrently and a press in that window is not received |
| `backend/src/adapt/api/routes/learning.py` | the timeline route spreads `stage`/`stages`/`progress`/`not_measured` from the one state service |
| `backend/src/adapt/api/app.py`, `backend/src/adapt/db/models.py` | the router registered; the model imported so migrations see it |

## De-duplication (task 2)

* The thirteen questions in `backend/src/adapt/baseline/service.py::QUESTIONS` are the single source;
  answering them writes `StudentPreference` rows with `source="baseline"` (unchanged behaviour).
* The setup screen keeps: `weekly_minutes`, the two free-text requests (`encourage`, `avoid`, which
  land in the **same** preference store, typed as lists of phrases), the two consent boxes, and voice
  (`voice_enabled` / `voice_gender` on the learner row, as `POST /me/onboarding` already took them).
* Nothing is asked twice: the setup screen's copy says where the rest is asked, and the page carries
  no field for anything in the thirteen (`session_minutes`, `study_days`, `language`,
  `preferred_format`, …).
* The banned-phrase exemption for `/onboarding` is gone, so a regression on that page fails the
  crawl instead of hiding inside `ONBOARDING_DEFERRED`.

## Placement consistency (task 3)

| where | says |
| --- | --- |
| `frontend/src/app/diagnose/page.tsx` | "four to twelve, eight by default" |
| journey shell's placement card | "Eight unaided questions, no hints and no worked examples" |
| `README.md` | "eight unaided questions, adaptive, no hints" |
| `docs/DEMO.md` | "Eight unaided questions, no hints" |
| `scripts/journey_check.py` | answers the served sequence and accepts ≥ 8 |
| tests | the placed band needs enough answers; the count clamps `max(4, min(12, n))` with 8 the default |

Placement stays reachable from the path (a card with the link, not a step), and the shell says why it
is not one of the eight.

## The gate

> a new learner can complete or skip through every stage, close the browser, return, and resume at the right stage

Proven twice, at two levels:

* **Backend** (`test_round2_phase_d_journey.py`): skip a stage on one client, read it back on a
  *second* `TestClient` with the same cookie, with the row and `not_measured` intact.
* **UI** (`scripts-ui-probe.mjs --only journey`): press the row's own skip button, see the POST go
  out, read `GET /api/v1/journey/state` back from the server, then open a **second page** with the
  account's cookies and check the resumed DOM shows the same stored stage and the same skipped row.
  (`frontend/tests-e2e/journey.mjs` repeats this in a real browser, on both viewports, with a whole
  new browser context as "closed the browser" — written, not run here.)

## Tests and checks run, with results

| command | result |
| --- | --- |
| `cd backend && pytest -q` | **1136 passed, 1 skipped** in 78.21s (session start: 1122 passed, 1 skipped; +14 new) |
| `cd frontend && npm run lint` | **ok** — `checked 109 backend routes against 95 frontend calls`, scene kinds agree, `tsc --noEmit` clean |
| `cd frontend && npm run test:engine` | 15 pass, 0 fail |
| `cd frontend && npm run test:server` | 2 pass, 0 fail |
| `cd frontend && npm run test:lib` | 30 pass, 0 fail (26 before; 4 new in `journey.test.mjs`) |
| `cd frontend && node scripts-ui-probe.mjs --base http://127.0.0.1:3000` (whole suite, API on `:8000` with `ADAPT_RATE_LIMIT_MULTIPLIER=10`) | **308/308 checks passed**, exit 0. The three scenarios this phase moves: `home,onboarding,journey` → 51/51 |
| `alembic upgrade head` / `current` / `check` in a scratch DB (`/tmp/adapt-mig-check`) | `0007 → 0008 → P2 → 0009`, `current = 0009 (head)`, `check` clean |
| `node tests-e2e/journey.mjs` | **not run** — no Chromium in this sandbox (see `unverified-d.md`) |

The probe's `journey` scenario is the honest half of the browser gate: it drives the real page in a
DOM with real fetches, cookies and CSRF, and it is where two genuine harness faults were found and
fixed (an empty row list read a beat before the state arrived; a press landing before jsdom had
hydrated that subtree). What it cannot judge is paint, layout and hit-testing — that is what the
Playwright script is for, and that is what stays unverified here.

## Screenshots

`docs/screens/D/` holds a README describing exactly what `node tests-e2e/journey.mjs` writes and
why the PNGs are absent: no browser in this environment. Nothing was fabricated to fill the folder.

## Handoffs

See `handoff-d.md` (for Phase G) and `unverified-d.md` (what must not be treated as verified).
