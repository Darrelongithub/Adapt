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

“AI 1 cannot change your education by itself” is not a policy here, it is a property of the
code: the only HTTP surface that can create a row in `approved_adaptations` is
`POST /api/v1/adaptations/{proposal_id}/decision`, and it refuses to run without a recorded
learner decision (`backend/src/adapt/services/adaptations.py`). Revocation is immediate and
also audited.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e "./backend[dev]"          # fastapi, sqlalchemy, numpy, cryptography, argon2…
cd backend && pytest -q                  # 150 tests, no network needed
adapt demo --turns 3                     # the whole loop, in a terminal, on a temp database

uvicorn adapt.api.app:app --port 8000    # the API (docs at /api, health at /healthz)
cd ../frontend && npm ci && npm run dev  # the app at :3000, proxying /api and /ws to :8000
node ../scripts/demo.mjs                 # the same loop driven through the HTTP surface
```

`npm run dev` and `npm run start` both run `frontend/server.mjs`, a small Node server that
binds `0.0.0.0`, serves Next, and proxies `/api/*`, `/healthz` and `Upgrade /ws/*` to
`ADAPT_API` (default `http://127.0.0.1:8000`). Set `ADAPT_API=offline` to run the interface
against nothing.

Open any page with `?mock=1` to see every screen from fixtures with no backend at all. A red
banner is shown whenever that mode is on, and mock mode is stored per tab so it cannot leak
into a real session.

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
  assessment/ bank (the item bank) · grading · numbas (import/export)
  services/  sessions · attempts · journey (the readable history) · adaptations (the gate)
  api/       app + routes/ (auth, learning, adaptations, tutor, system) · ws.py · deps (rate, csrf)
  crypto/    keys (one master key, HKDF subkeys) · fields (the encrypted column types)
  cli.py     demo · status · keys · export · import
frontend/src/
  lib/api.ts      one fetch wrapper: /api/v1 prefix, CSRF from the cookie, {data,error} returns
  lib/flow.ts     the loop in one hook: session, event queue, attempts, finish + analyse
  lib/mock.ts     the ?mock=1 fixtures (recorded shapes, never a substitute for the API)
  components/     ui.tsx (primitives + EventQueue) · question.tsx (the item) · chrome.tsx
  app/            onboarding · diagnose · learn · proposals · review · progress · settings · voice
scripts/demo.mjs  the loop driven over HTTP, with the contract checks the UI relies on
docs/             UNVERIFIED.md · VOICE.md · PILOT.md
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
../.venv/bin/alembic -c alembic.ini stamp head                                  # once: a create_all database
../.venv/bin/alembic -c alembic.ini revision --autogenerate -m "add a column"    # after editing models
../.venv/bin/alembic -c alembic.ini check                                       # fails if a model awaits a revision
../.venv/bin/alembic -c alembic.ini downgrade base                              # reversible, and tested to be
```

Read every autogenerated revision before you ship it: autogenerate cannot see a data migration,
and the encrypted column types are rendered through a `render_item` hook so a revision does not
turn `EncryptedJSON(context="audit.detail")` into a bare `Text`. The gate is
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
