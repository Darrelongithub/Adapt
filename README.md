# ADAPT

An adaptive learning app where the AI measures how you study, proposes what to change, and
**asks before it changes anything**. Two intelligences, one loop:

```
Test      you answer; the app records the order and timings of what you did
Observe   metrics derived from that log (never from keystrokes or screen content)
Understand  hypotheses with a status, a confidence and a written disproof condition
Personalize a proposal: what changes, why, what else could explain it, how it will be judged
Learn     you answer it (yes / no / change it / later); the change is measured and reported
```

**What is never recorded:** keystrokes, key timing, per-character edits, screenshots, screen
content or other tabs — a fixed allowlist of event types with 48 forbidden payload keys, and an
unlisted type is rejected rather than stored. Answers stay on the machine unless a model call is
opted in, and the response says which one happened.

“AI 1 cannot change your education by itself” is not a policy here, it is a property of the
code: the only HTTP surface that can create a row in `approved_adaptations` is
`POST /api/v1/adaptations/{proposal_id}/decision`, and it refuses to run without a recorded
learner decision (`backend/src/adapt/services/adaptations.py`). Revocation is immediate and
also audited.

## The demo, in the order to say it

Three minutes, and it is about the loop — nothing else. `docs/DEMO.md` is the script; this is
the shape of it:

1. **Placement** (`/diagnose`): eight unaided questions, adaptive, no hints. Say what it is for:
   a band per topic, not a grade.
2. **Practice** (`/learn`): one question, taught one way, and the teaching is visible before the
   question — short steps with a check between them, a diagram, or a worked example. Say the
   thing that is easy to miss: **the method is assigned per learner and per attempt, not baked
   into the item**, so the same question can be taught two ways to two people.
3. **A wrong answer → the loop closes live**: the reteach step checks the prerequisite and
   fatigue first (so a bad night is not read as bad teaching), then re-teaches *in a different
   method*, asks a matched question of the same format, and compares actual against expected.
4. **Changes** (`/proposals`): what was observed, what it might mean, what else could explain it,
   how it would be judged. Nothing applies until the learner answers — and "no" is recorded too.
5. **Which teaching works** (`/progress`): actual minus expected per method, each row carrying
   `n` and the words `early signal` under eight observations. If a number comes from the pre-demo
   pilot it says **pilot data — not evidence of efficacy** next to it. Above it, every topic
   estimate is drawn the way it was measured: position is the estimate, the width of the line
   through it is the uncertainty the server computed, and the size of the circle is how many
   answers it rests on — *a bigger circle is not a better score, it is a more confident one* — with
   the exact figures in a table underneath, because a chart nobody can read as numbers is a chart
   nobody can check.

**Do not demo the plumbing.** Voice, the Numbas import/export, the encryption internals, the
SQLCipher story and the resource providers are all real and all in `docs/` — showing them spends
the room's attention on how it is stored instead of what it does. One privacy line is enough
(the bolded paragraph above), and it is auditable at `/settings`.

### Before you stand up

```bash
adapt pilot --student-id <the learner you will show> --seed   # label: pilot data
node scripts/demo.mjs --as <that learner's email>             # rehearse the same path
```

The pilot exists because the honest answer to "did this help?" needs five scored attempts on each
side of a change, and a live demo does not have them. The bar is not lowered for a demo; real
rows are written beforehand and *labelled*, so the numbers on screen are numbers, and the label
travels with them ("14 of 41 scored answers are pilot data"). See `docs/PILOT.md`.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e "./backend[dev]"          # fastapi, sqlalchemy, numpy, cryptography, argon2…
cd backend && pytest -q                  # 645 tests, no network, no model call needed
adapt demo --turns 3                     # the whole loop, in a terminal, on a temp database
adapt pilot --student-id <id> --seed     # the pre-demo pilot, written and labelled

uvicorn adapt.api.app:app --port 8000    # the API (docs at /api, health at /healthz)
cd ../frontend && npm ci && npm run dev  # the app at :3000, proxying /api and /ws to :8000
node ../scripts/demo.mjs                 # the same loop driven through the HTTP surface

cd ../frontend
npm run lint        # the API/origin contract (scripts/check-contract.mjs) + tsc, no server
npm run test:engine # the engine's invariants, against the source that has to keep them
npm run test:server # the launcher: offline refuses with a 503, and a LAN Host reaches the API
npm run probe       # the real UI in jsdom: click, answer, reload, and read what the DB says
../scripts/probe-fresh.sh all            # the whole probe on fresh servers (rate limits reset)
npm run probe:mock  # the same screen on ?mock=1, with nothing but its own refusals asserted
```

`npm run dev` and `npm run start` both run `frontend/server.mjs`, a small Node server that
binds `0.0.0.0`, serves Next, and proxies `/api/*`, `/healthz` and `Upgrade /ws/*` to
`ADAPT_API` (default `http://127.0.0.1:8000`). Set `ADAPT_API=offline` to run the interface
against nothing.

The interface is a permanent left sidebar plus one screen of content: every top-level
destination is visible in plain words on every screen, with the current page marked
(`aria-current="page"`), and below 860px the same list slides in as a drawer behind a
labelled Menu button. Navigation is never hidden behind a corner control.

Account creation — including "continue as guest", which also writes a learner row — is
gated behind a consent step on `/`: two separate checkboxes, neither pre-ticked, with the
primary action closed until the required one is ticked and a Decline that explains what
will not work rather than dead-ending. The required box becomes `consent_analytics`. The
optional *research* box has no field on this instance yet and the screen says so; it is
deliberately **not** written to `consent_share_with_llm`, which means sharing a summary
with the model. See `docs/UNVERIFIED.md` — "no decision" is still recorded as `true` by
the database default until a tri-state column or migration lands.

Every control that submits, decides, rates, selects or advances state wears one of three
button classes (`globals.css`, "the button contract"), so nothing that acts is styled as
plain coloured or underlined text.

Open any page with `?mock=1` to see every screen from fixtures with no backend at all. A red
banner is shown whenever that mode is on, and mock mode is stored per tab so it cannot leak
into a real session.

## Show someone on your wifi

```bash
adapt host              # API + web app, one address, printed (a room on the same wifi)
adapt host --tunnel     # the same, plus an https link — what the microphone needs
```

`adapt host` starts both servers, detects this machine's LAN address, adds **that one host** to
the API's allowlist (an exact string: never a wildcard, never a subnet — the laptop next door is
still refused, and `backend/tests/test_hosting.py` proves both halves), and prints the address to
read out. Your `.env` is only read — never rewritten, never echoed — and `--port`, `--ip`,
`--data-dir` and `--rate-limit-multiplier` change nothing else.

Two things to say before people open the link:

* **The microphone needs https.** Browsers hand a page the microphone only in a secure context,
  so on `http://<lan-ip>:3000` the `/voice` screen cannot listen; typing works exactly the same.
  `--tunnel` opens an https link through cloudflared (else `npx localtunnel`) — both are hosts
  the API already trusts — and the voice socket follows `location.protocol`, so `wss` needs no
  configuration. Off-wifi people can use that link too.
* **The room is one connection.** Everyone behind the same router reaches the API from one
  address, so the per-IP ceiling and learner creation are a *shared* budget. `adapt host` raises
  `ADAPT_RATE_LIMIT_MULTIPLIER` to ×10 for the servers it starts (120 → 1200 requests/min,
  6 → 60 learners/min) and prints the numbers; the repository default stays 1.0, so no
  deployment's protection is lowered by a command someone ran at a showcase.
  `--rate-limit-multiplier` raises it further, and `GET /api/v1/quotas` reports what is in force.

A real second device, a real phone microphone and a real tunnel were **not** available in the
sandbox this was built in (see `docs/UNVERIFIED.md`). Rehearse once: open the printed address on
a phone, create a learner, answer one question. If `--tunnel` cannot produce an https address,
say so and share the LAN address — the command keeps serving and tells you which one it is.

The interface is built to hold up on a phone (the status readout stays out of the way of the
content and the buttons, typefaces are served from this machine rather than from Google Fonts,
fields do not trigger iOS's zoom-into-the-page, tables scroll inside their own box). None of that
has been seen on real glass from inside the sandbox — at rehearsal, walk one question in portrait
*and* landscape and tap **Check it** once; `docs/UNVERIFIED.md` lists exactly what to look at.

## What is implemented, and what it refuses to do

* **Estimates, not labels.** BKT per topic with a learning-from-failure path, half-life
  spacing scheduled to the point your own recall is projected to fall to ~85%, prerequisite
  gap detection over a topic graph. A cold start prints `new`, never `low-ability`.
* **No learning-styles question.** The onboarding payload has no key for it, and the server
  answers `ignored_keys: ["learning_style"]` with a reason rather than storing it. What you
  learn best is inferred from what happens during practice — and inference is reversible, so
  the app states hypotheses and never traits.
* **Telemetry by allowlist.** 21 event types; 48 forbidden payload keys (`keystrokes`,
  `screenshot`, `other_tabs`, …). An unlisted type is *rejected*; a forbidden field costs the
  field, not the event, and the response counts what was dropped so the client cannot quietly
  lose signal.
* **Answer keys stay server-side.** `PRIVATE_ITEM_KEYS` strips `answer_spec`, `solution_steps`,
  `hints`, `rubric` and `stem_template` from every learner-facing payload; the reveal endpoint
  is the only way to see one, it demands the CSRF token on a GET, and it is not a persistent
  change.
* **Both AIs are real.** AI 2 uses Gemini when a key is configured and falls back to a local
  tutor (BM25 over your uploaded material + the bank's own steps) with `provider: "local"`,
  `grounded: true|false` and `sources[]` on every answer. Nothing is sent to a model when no
  key exists, when consent is off, or when the daily budget is spent — and the response says
  which of those it was.
* **Interactive lessons are computed, not generated.** A scene is a spec the backend validates
  against a per-kind model (`learning/scenespec.py`) and plays as a trace of frames
  (`learning/sims/`, one module per kind) — and the prediction question is *inside* the frame,
  written where the next state is decidable, so a prompt cannot drift out of step with what it
  asks about. The front-end head (`frontend/src/lib/engine/player.ts`) refuses to `step` past an
  unanswered prompt while letting the slider, `back` and "end & review" go anywhere. A lesson run
  earns no mastery, a learner's copy of a scene is *theirs* (`0003`'s two partial unique indexes),
  and a kind this build cannot draw still plays as narration and state values rather than a blank
  box. `docs/ENGINE.md` is the contract; `npm run test:engine`, `npm run lint` and the `lesson`
  probe scenario are what keep it from being prose.
* **Parametric items are rendered per learner, consistently.** One seed function
  (`bank.variant_seed`) drives the stem you are shown, the hint you ask for, the worked
  example, the reveal, and the grading of your answer. Two of those disagreed until the tests
  in `backend/tests/test_item_variant_rendering.py` were written; the numbers now have to
  match or a test fails.
* **Encrypted at rest, honestly.** Field-level AES-256-GCM on names, emails, answers, event
  payloads, material bodies and session summaries, with an HMAC blind index for login;
  SQLCipher is used when a driver is installed and reported as unavailable when it is not.
  `GET /api/v1/status` tells you which one you actually have. A keyfile that cannot decrypt
  the existing rows stops startup with a sentence naming the file, instead of serving 500s.

## Layout

```
backend/src/adapt/
  ai/        ai1.py (analysis, hypotheses, proposals, plan) · ai2.py (tutor, grading, feedback)
             local_tutor.py (fallback teacher, item rendering) · providers (gemini, resources)
  learning/  bkt · spacing · selector · patterns · hypotheses · strategies (the proposal catalog)
             telemetry (allowlist + derived metrics) · gaps · effect (did it help?) · profile
             sims/ (one module per scene kind: run() -> frames, each carrying its own prompt or
             none; grade_prediction, expected, truth, explain) · scenespec.py (validate() + the
             content-addressed spec_id, plain-text limits, builtin_specs() seeded at startup)
  assessment/ bank (the item bank) · grading · numbas (import/export)
  services/  sessions · attempts · journey (the readable history) · adaptations (the gate)
  api/       app + routes/ (auth, learning, adaptations, tutor, system) · ws.py · deps (rate, csrf)
  crypto/    keys (one master key, HKDF subkeys) · fields (the encrypted column types)
  cli.py     demo · status · keys · export · import
frontend/src/
  lib/engine/     player.ts (the gate, the reducer, the timing floor) · types.ts · its own tests
  lib/api.ts      one fetch wrapper: /api/v1 prefix, CSRF from the cookie, {data,error} returns
  lib/flow.ts     the loop in one hook: session, event queue, attempts, finish + analyse
  lib/mock.ts     the ?mock=1 fixtures (recorded shapes, never a substitute for the API)
  components/     ui.tsx (primitives + EventQueue) · question.tsx (the item) · chrome.tsx
                  scenes/ (stages.tsx: one stage per kind; index.tsx: the dispatcher that says so
                  when a kind has no stage)
  app/            onboarding · diagnose · learn · lesson (the interactive engine) · proposals ·
                  review · progress · settings · voice
scripts/demo.mjs  the loop driven over HTTP, with the contract checks the UI relies on
docs/             ENGINE.md (the interaction engine, end to end) · UNVERIFIED.md · VOICE.md · PILOT.md
frontend/README.md  the web app: ports, dev origins, probes, and what the player guarantees
```

## Environment

Everything has a working default, so `uvicorn adapt.api.app:app` runs with no configuration
and stays in `local` mode. Prefix `ADAPT_`: `DATA_DIR`, `KEYFILE`, `DB_ENCRYPTION`
(`auto|sqlcipher|field-only|off`), `GEMINI_API_KEY`, `GEMINI_DAILY_CALL_BUDGET`,
`RATE_LIMIT_MULTIPLIER`, `MODE` (`local|tunnel`), `DEMO_PASSPHRASE`, `VOICE_ENABLED`,
`WHISPER_MODEL_SIZE`, `TTS_ENGINE`, `ALLOWED_ORIGINS`, `FRONTEND_ORIGIN`.
`GET /api/v1/status` reports the effective values, including which encryption you got.

## Schema changes

A **new** database is built by `Base.metadata.create_all` during startup. An **existing** one —
the only kind that matters once a learner has history — is moved with Alembic. There is no second
copy of the path to drift: `env.py` asks the app for its engine, so `ADAPT_DATA_DIR` /
`ADAPT_DB_PATH` decide what gets migrated.

```bash
cd backend
../.venv/bin/alembic -c alembic.ini upgrade head                                # empty or versioned
../.venv/bin/alembic -c alembic.ini stamp 0002 && \
  ../.venv/bin/alembic -c alembic.ini upgrade head          # a create_all database: stamp what it matches
../.venv/bin/alembic -c alembic.ini revision --autogenerate -m "add a column"    # after editing models
../.venv/bin/alembic -c alembic.ini check                                       # fails if a model awaits a revision
../.venv/bin/alembic -c alembic.ini downgrade base                              # reversible, and tested to be
```

A database built by `create_all` has no `alembic_version` row, so a bare `upgrade head` replays
`0001` and dies on `table audit_log already exists`. Stamp first — at the revision the file actually
matches. A database created by *this* build already has every column and index, so it is at head and
`stamp head` is the truth. A database created by an earlier build matches `0002`, and stamping *that*
file to head is the mistake that matters: it looks migrated while `lesson_scenes.spec_id` is still
unique per database, which is the collision `0003` exists to remove. Replaying a revision onto a file
that already matches it is now harmless rather than an `index already exists` failure — `0003` reads
`get_indexes()` and changes only what is absent or wrongly shaped, in both directions — because those
two situations are hard to tell apart from the outside. Confirm through the app's own engine, and
`test_migrations.py` holds the line by comparing an upgraded database with a `create_all` one on
every index name and uniqueness flag:
```bash
../.venv/bin/python -c "from sqlalchemy import inspect; from adapt.db.engine import create_db_engine, get_settings; e,_=create_db_engine(get_settings()); print(inspect(e).get_indexes('lesson_scenes'))"
```

Read every autogenerated revision before you ship it: autogenerate cannot see a data migration,
and the encrypted column types are rendered through a `render_item` hook so a revision does not
turn `EncryptedJSON(context="audit.detail")` into a bare `Text`. The connection Alembic uses is
the application's engine (`connectable = _engine` in `migrations/env.py`), not one rebuilt from
`alembic.ini`: a URL carries neither `module=sqlcipher3` nor the `PRAGMA key` listener, so
rebuilding it means an existing encrypted database cannot be opened and a fresh directory quietly
gets a *plaintext* file the app can never read. `test_migrations.py` pins both directions, with
`ADAPT_DB_ENCRYPTION=auto` set explicitly in every subprocess (the test session runs field-only, and
a guard that inherits that policy inspects a plaintext scratch file and passes no matter what). The
gate is
`backend/tests/test_migrations.py`, which upgrades a scratch database and compares the result
with the models, stamps a `create_all` file, and downgrades to empty.

## Limits worth knowing before you trust it

Read [docs/UNVERIFIED.md](docs/UNVERIFIED.md): it lists what could not be checked in the
sandbox this was built in (model calls, the two speech engines' audio, network resource
providers, Numbas against a live server), what is deliberately *not* implemented (a password
reset, a teacher dashboard, a spaced-review notification), and what a small-sample result
does and does not mean. The pilot protocol is in [docs/PILOT.md](docs/PILOT.md).

A full code audit (what was run, what it found, what was fixed and how each fix is pinned by a
test that fails without it) is in [docs/AUDIT.md](docs/AUDIT.md). It is dated, so treat its
"not changed" list as a proposal rather than a decision.
