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

## Round 2: the typed-topic journey

Round 2 added the path a new learner actually walks: account → preferences → mind games →
subject → subject round → snapshot → a first lesson written for a topic they type themselves →
practice. A typed topic is turned into a checked knowledge pack in the background
(`POST /api/v1/content/prepare`), and the lesson screen teaches from that pack: the lesson is
written for the learner, a wrong answer gets an explanation that starts from the answer they
actually gave, and "I don't get it" re-teaches the same idea a different way every time. With
no model key configured, every one of those steps still answers — deterministically, and
labelled "Simple lesson" rather than pretending otherwise. `docs/DEMO.md` is the
three-minute demo script (live path, contrast screen, mock fallback); `docs/AUDIT.md` is the
code audit, and `docs/UNVERIFIED.md` lists what is still unverified. The live model check has
**not** been run — no key has ever been configured on this build; `scripts/live_model_check.py`
is the harness and `MODEL_QUALITY_REPORT.md` is the template it would replace.

## Run it

One-time setup (Python 3.11+ and Node 20.11+):

```bash
cd ~/Adapt
python3 -m venv .venv
.venv/bin/python -m pip install -e "./backend[dev]"
cd frontend && npm ci
```

Then, **one terminal, one command** for development:

```bash
cd ~/Adapt/frontend
npm run dev
```

For normal use or sharing on Wi-Fi, prefer the production-mode launcher:

```bash
npm run host
```

This builds first, then runs both services without page-by-page development compilation.
Stop `npm run dev` before running it; both use `.next`. Use `npm run dev` when editing code.

This finds `../.venv` automatically (no activation needed), starts the API and Next in
development mode, upgrades the database before seeding, and prints `share this:` with
the address for other devices on your Wi-Fi. **Ctrl+C stops both servers.** Open
`http://localhost:3000` on the laptop; open the printed LAN address on your phone.
Stop any old frontend/backend processes before starting this launcher.

If a VPN chooses the wrong interface, use `npm run dev -- --ip 192.168.1.20` with your
actual Wi-Fi IP. `PORT=3001 npm run dev` changes the web port. Only share on a trusted
network; allow that web port through your firewall if necessary, and disable guest-network
client isolation if devices cannot reach each other. Phone microphones need HTTPS, not
plain LAN HTTP (`npm run dev -- --tunnel` opts into a public tunnel).

`ADAPT_PYTHON=/path/to/python` selects another installed Python environment. The launcher
uses the existing `adapt host --dev` supervisor; it never installs dependencies automatically.
For a frontend-only session with a separately managed API, use `npm run dev:web` instead.
For fixture-only previews: `ADAPT_API=offline npm run dev:web`, then open `/?mock=1`.
`npm run start` remains frontend-only production serving.

### Add your Gemini key (round 3)

The key is console-only. It is never read from a route, log line, error body, or bundle, and
the secrecy canary in `backend/tests/test_config_secrecy.py` enforces that.

The one canonical way to set it:

```bash
cd backend
source .venv/bin/activate          # or use the venv the launcher already made
adapt config set-key               # hidden prompt — type, do not paste on the command line
                                   # the key is stored in an owner-only console file
                                   # under the data dir, not in this repo
adapt config status                # one line per service, e.g.
                                   #   gemini: configured · …ab12 · from the console file
                                   #   youtube: not configured (set it with `adapt config set-key`)
adapt config test                  # one minimal request; on failure it names the cause
                                   # (unknown model ID, rejected key, no route) instead of
                                   # repeating one generic sentence
adapt config models                # model IDs this key can call, for pinning
```

Restart `adapt host` so the new value is loaded.  The startup line that says
"Model key: configured / not configured (lessons in simple mode)" is the only place the
key's *presence* is reported — the key itself never leaves the console file.

A plain `.env` at the repo root is the fallback for machines where the console flow is
not desired. It is also the easiest way to configure the database and optional services:

```bash
cd ~/Adapt
cp .env.example .env
$EDITOR .env                       # defaults already work; optionally set ADAPT_GEMINI_API_KEY=…
```

The template is read from the repository root whether the backend is started from `Adapt/`,
`Adapt/backend/`, or by `npm run dev`. Do not put a real key in `frontend/.env.local`: the
frontend loader intentionally ignores provider credentials. If you run the frontend separately,
copy `frontend/.env.local.example` to `frontend/.env.local` for its proxy/port settings.
The `ADAPT_GEMINI_API_KEY` value is read by the same code path whether it came from the
console file or `.env`. Both are checked at startup; the console file wins if both are set.

### Advanced / separate services and checks

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e "./backend[dev]"          # fastapi, sqlalchemy, numpy, cryptography, argon2…
cd backend && pytest -q                  # 1401 passed, 2 skipped (measured 2026-10-17, Gemini-path round); no network, no model call needed
adapt demo --turns 3                     # the whole loop, in a terminal, on a temp database
adapt pilot --student-id <id> --seed     # the pre-demo pilot, written and labelled

uvicorn adapt.api.app:app --port 8000    # the API (docs at /api, health at /healthz)
# In a second terminal:
cd ~/Adapt/frontend && npm run dev:web  # frontend only, proxying to :8000
node ../scripts/demo.mjs                 # the same loop driven through the HTTP surface

cd ../frontend
npm run lint        # the API/origin contract (scripts/check-contract.mjs) + tsc, no server
npm run test:engine # the engine's invariants, against the source that has to keep them
npm run test:server # the launcher: offline refuses with a 503, and a LAN Host reaches the API
npm run probe       # the real UI in jsdom: click, answer, reload, and read what the DB says
../scripts/probe-fresh.sh all            # the whole probe on fresh servers (rate limits reset)
npm run probe:mock  # the same screen on ?mock=1, with nothing but its own refusals asserted
```

The frontend child (`npm run dev:web` or `npm run start`) runs `frontend/server.mjs`, a small Node server that
binds `0.0.0.0`, serves Next, and proxies `/api/*`, `/healthz` and `Upgrade /ws/*` to
`ADAPT_API` (default `http://127.0.0.1:8000`). Set `ADAPT_API=offline` to run the interface
against nothing.

The interface is a permanent left sidebar plus one screen of content: every top-level
destination is visible in plain words on every screen, with the current page marked
(`aria-current="page"`), and below 860px the same list slides in as a drawer behind a
labelled Menu button. Navigation is never hidden behind a corner control.

Signed-out users first see the welcome sentence and then a **non-dismissible consent/login
card**. No sidebar or learning page mounts behind it. Accepting consent unlocks sign-in,
account creation or guest entry—not the rest of the app. A new learner goes to the mind baseline;
a learner with saved baseline history goes to the dashboard on subsequent sign-ins. The dashboard
shows real progress and links to unfinished lessons and checks, without restarting the baseline.

The consent boxes start unchecked. Registration stores the analytics choice; model sharing starts
off and remains a separate setting. Optional research interest is explicitly not stored or acted
on yet. Returning login preserves the learner's existing privacy settings.

On **Subject round**, choose a subject and an optional specific topic. Review the assessment
outline, tick/untick proposed checks, and optionally comment before confirming it. The saved round
uses those checks; excluded ones remain “not measured”. Comments are saved with the encrypted
plan, not automatically treated as instructions to rewrite it.

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
             routing.py (which class of work each task is, and which model that class uses)
             static_context.py (the ten static prompt heads) · context_cache.py (how they are
             cached) · chain.py (one prompt, several answers) · llm.py (client + budget)
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
  lib/debounce.ts the client-side guards: debounce, throttle, repeat window, one-at-a-time submit
  lib/flow.ts     the loop in one hook: session, event queue, attempts, finish + analyse
  lib/mock.ts     the ?mock=1 fixtures (recorded shapes, never a substitute for the API)
  components/     ui.tsx (primitives + EventQueue) · question.tsx (the item) · chrome.tsx
                  scenes/ (stages.tsx: one stage per kind; index.tsx: the dispatcher that says so
                  when a kind has no stage)
  app/            onboarding · diagnose · learn · lesson (the interactive engine) · proposals ·
                  review · progress · settings · voice
scripts/demo.mjs  the loop driven over HTTP, with the contract checks the UI relies on
docs/             AUDIT.md (what each pass ran, found and fixed — the change trail) ·
                  BUGS.md (the defect register: each bug, its proof, its test) ·
                  ENGINE.md (the interaction engine, end to end) · EFFICIENCY.md (what a model
                  call costs: merged prompts, cached heads, model tiers, request guards) ·
                  UNVERIFIED.md (what no one has checked, and how to check it) ·
                  EVIDENCE.md · ACCESSIBILITY.md · DEMO.md · VOICE.md · PILOT.md
frontend/README.md  the web app: ports, dev origins, probes, and what the player guarantees
```

## Environment

Everything has a working default, so `uvicorn adapt.api.app:app` runs with no configuration
and stays in `local` mode. Copy `.env.example` for a known-good starting point. Backend names
always use the `ADAPT_` prefix: `ADAPT_DATA_DIR`, `ADAPT_KEYFILE`, `ADAPT_DB_ENCRYPTION`
(`auto|sqlcipher|field-only|off`), `ADAPT_DB_PASSPHRASE`, `ADAPT_GEMINI_API_KEY`,
`ADAPT_GEMINI_DAILY_CALL_BUDGET`, `ADAPT_RATE_LIMIT_MULTIPLIER`, `ADAPT_MODE`
(`local|tunnel|test`), `ADAPT_VOICE_ENABLED`, `ADAPT_WHISPER_MODEL_SIZE`,
`ADAPT_TTS_ENGINE`, `ADAPT_ALLOWED_ORIGINS`, and `ADAPT_FRONTEND_ORIGIN`.
`GET /api/v1/status` reports only learner-safe protection/lesson information; operator settings
stay in the console. The database passphrase is now a real settings field, so placing
`ADAPT_DB_PASSPHRASE=...` in the copied env file works as well as exporting it in the shell.

Four kinds of setting decide what a model call costs, and every one of them has a safe default
([docs/EFFICIENCY.md](docs/EFFICIENCY.md) is the whole story, including which of them are
inert on a free tier because the provider's floor is higher than the prompt):

* **which model, by class** — `ADAPT_GEMINI_MODEL_LIGHT` / `_STANDARD` / `_DEEP`
  (defaults: flash-lite / flash / pro). `ai/routing.py` says which task is in which class and
  why; `adapt doctor` prints the table. To pin one task, `ADAPT_GEMINI_TASK_MODELS` takes
  `'{"ai2_explain": "<model>"}'` or `ai2_explain=<model>`; `ADAPT_GEMINI_MODEL_AI1` / `_AI2`
  pin every task a role performs. Precedence is explicit argument → task pin → role pin →
  class. `ADAPT_GEMINI_MODEL_FALLBACK` is an optional one-retry model for a deployment whose
  primary ID has been retired (a 404), and `ADAPT_GEMINI_SAFETY_THRESHOLD` states the safety
  threshold sent with every request.
* **how many calls** — `ADAPT_AI_SINGLE_CALL` (default `true`). `false` restores the
  two-call placement path, so the merge can be measured against what it replaced rather than
  believed.
* **how much of the prompt is re-sent** — `ADAPT_AI_CONTEXT_CACHE` and friends (on by
  default): `ADAPT_GEMINI_CONTEXT_CACHE_MIN_TOKENS` is the provider's floor,
  `ADAPT_GEMINI_CONTEXT_CACHE_TTL_S` their lifetime,
  `ADAPT_GEMINI_CONTEXT_CACHE_MAX_ENTRIES` / `_MAX_TOKENS` the bound. A cache that cannot be
  created is one full prompt, never a failed answer.
* **how much runs at once** — `ADAPT_REALTIME_MAX_INFLIGHT`, `ADAPT_REALTIME_BURST_MAX`,
  `ADAPT_REALTIME_BURST_WINDOW_MS` (defaults 4, 20, 1000) cap what one learner may have
  *running* on a real-time route; `ADAPT_WS_DUPLICATE_WINDOW_MS` (900) collapses identical
  websocket frames. `ADAPT_RATE_LIMIT_MULTIPLIER` scales all of these with the rest of the
  table.

A merged prompt, a cached head and a light model are all *attempts*: if any of them is refused,
the answer comes from the local path and says so. `adapt doctor` prints which of them are in
force, and `adapt doctor --json` includes the routing table, the per-block token sizes, the
merge step by step and the concurrency guard's counters.

## Schema changes

API startup and `adapt init` now run Alembic **before any ORM query or curriculum seeding**.
This repairs errors such as `no such column: items.verification` without deleting learner data.
Fresh databases run the full migration chain. Unversioned databases from older `create_all`
boots get missing tables created, are adopted at the `0002` baseline, and replay the
existence-aware revisions from there. Already-versioned databases only upgrade forward.
If required columns are still missing, startup refuses and rolls back the upgrade.

The upgrade uses the application's already-keyed connection, including SQLCipher and custom
`ADAPT_DB_PATH` settings. **Keep a backup of your data directory and encryption key before
upgrading an existing installation**, with both servers stopped. Never delete the database
or key to fix a missing column. No manual `stamp` is needed for normal startup now.

For manual maintenance (stop the app first):

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
the application's engine in `migrations/env.py`, not one rebuilt from
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
test that fails without it) is in [docs/AUDIT.md](docs/AUDIT.md). It is dated and organised as
chronological passes, so treat any "not changed" list in it as a proposal rather than a
decision. The defects themselves — each with the request or command that proved it, the fix, and
the test that fails without the fix — are listed separately in
[docs/BUGS.md](docs/BUGS.md), including the findings that were investigated and closed as *not*
bugs, so a later pass does not re-open them.
