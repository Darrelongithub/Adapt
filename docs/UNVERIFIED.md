# What has not been verified

Everything in this repository was written and tested in a sandbox with **no outbound network
except PyPI/npm/GitHub** and no browser. That leaves a specific, nameable set of claims that
are implemented but *not* proven. Each item says what was done instead and how to check it.

## Model calls (AI 2, Gemini)

* **Verified:** the request shape, the redaction pass (emails, phone numbers, URLs, addresses
  and id-like fields are stripped before anything is sent), the pseudonym used in place of a
  name, the daily call/token budget and its accounting, the per-purpose call log, the retry
  and backoff rules, and — most importantly — that every consumer has a **local fallback**
  whose response says `provider: "local"` and `grounded: true|false`.
* **Not verified:** whether a real Gemini answer is *good*. No call has ever been made from
  this build. Prompt changes must be judged by the pilot's own outcomes
  (`docs/PILOT.md`), not by how the prompt reads.
* **To check:** `ADAPT_GEMINI_API_KEY=… uvicorn adapt.api.app:app`, then
  `POST /api/v1/explain` and look at `llm`, `model_ms` and `privacy.what_was_sent`.

## Speech (STT and TTS)

* **Verified:** the WebSocket protocol end to end through the app's own proxy — a real
  handshake and a full turn (`hello → state → reply → speak_unavailable → config_ack`), with
  the reply reporting `provider: local` and `adaptations: [stated_preference, segmented_text,
  retrieval_practice]`, so the stated preference is visibly reaching the strategy. Also the
  turn-taking state machine, barge-in flag accounting, latency measurement, and that
  `voice off` means no microphone access at all. A refused handshake now reaches the browser as
  a refusal (`ws_handshake_refused`) instead of hanging — the upgrade proxy had no failure path
  until the voice screen was tested through it.
* **Not verified:** audio in either direction. `faster-whisper` and `kokoro` could not be
  installed here (their weights come from Hugging Face, which is unreachable; `kokoro` also
  needs `espeak-ng` from apt, and pins `python <3.13`). `capabilities()` reports the engines
  as unavailable and the app degrades to browser dictation and `speechSynthesis` — which is
  the tested path. Word error rate, latency and "does it feel interruptible" are unknown.
* **To check:** `docs/VOICE.md` has the install and a 10-turn script.

## Encryption at rest

* **Verified:** field-level AES-256-GCM on the learner's name, email, both answer values,
  event payloads, material bodies and session summaries; HMAC blind index for login; Argon2id
  password hashing; per-purpose HKDF subkeys; that raw file bytes contain no plaintext (a test
  reads the database file itself); that a keyfile which cannot decrypt existing rows stops
  startup with a message naming the file rather than serving 500s.
* **Not verified:** SQLCipher whole-file encryption. `sqlcipher3-binary` is not installed in
  this sandbox, so `db_encryption="auto"` selects field-only and says so. The driver-detection
  code path for a *working* SQLCipher has never run here.
* **To check:** `pip install sqlcipher3-binary`, restart, and confirm
  `GET /api/v1/status → database.file_encrypted` is `true` and
  `sqlite3 var/adapt.db "PRAGMA cipher_version"` answers.

## Item bank and placement quality

* **Verified:** every item's answer key is reachable by the grader, the diagnostic covers one
  band range per topic, the exported Numbas package carries the correct answer for mcq, and
  the learner-facing payloads never contain one (asserted by a string scan in the tests).
* **Not verified:** that the items are *good test questions*. ~90 items across 20 GCSE-maths
  topics were authored here; `difficulty` is an author's prior, not a pilot-derived estimate,
  and nothing has been checked against real cohort data, mark schemes or an exam board's
  content limits. Placement bands are therefore usable for choosing what to practise next and
  **not** usable as a level to report to anyone.
* **To check:** a pilot with ≥15 learners, then replace the priors with observed statistics
  (`docs/PILOT.md` step 5).

## Numbas and the resource providers

* **Verified:** the `.numbas` export/import round trip against our own importer; XML that
  satisfies the schema the code writes; that one-item-per-topic banks and missing answers are
  reported instead of silently exported.
* **Not verified:** opening an export in a real Numbas editor/server, and any live call to
  OpenAlex, arXiv, Open Library or YouTube (all blocked here). Those providers are unit-tested
  against canned JSON, and `resources/status` shows each one's real state at runtime.
* **To check:** upload one exported package to a Numbas instance; then
  `POST /api/v1/resources/search {"query":"fractions equivalence"}` with network available.

## Frontend

* **Verified:** `tsc --noEmit` clean, `next build` clean (10 routes), every endpoint each page
  calls exists on the server (checked against the live route table), and every screen's data
  path exercised over HTTP by `scripts/demo.mjs`.
* **Not verified:** the rendered pixels. No browser was available, so layout, focus order,
  screen-reader labels, `speechSynthesis` voice selection and microphone permission prompts
  have not been observed. The `?mock=1` fixtures are hand-copied from recorded responses, so
  they can drift from the API; `node scripts/demo.mjs --json` is the check that they have not.
* **To check:** `npm run dev`, then each screen once with `?mock=1` and once against the API.

## Learning-science claims

The strategies carry citations (testing effect,worked-example fading, spaced retrieval,
self-explanation). **What was verified is the mechanism, not the effect size**: this build
demonstrates that a change is proposed, gated on a decision, applied, measured and reported —
including when the measurement says it did not help. Whether it helps *you* is what the
`measured_outcomes` block on the progress screen is for, and small samples are labelled
`insufficient_data` rather than smoothed over. A single learner's `evaluated: 0` after one
session is the expected result, not a bug.

## Added by the 2026-10-11 audit

* **Teach-back rendering with a real model.** The route now hands AI 2 the learner's own variant
  instead of the stored template, and a test proves it with a spy. What this sandbox could not
  check is the *model's* behaviour on it: with `ADAPT_GEMINI_API_KEY` set, ask a parametric
  teach-back question and confirm the reply quotes the numbers on screen and no `{placeholder}`.
* **Consent provenance end to end in a browser.** The audit row for a decision now carries
  `answered_by` and `channel` (`web`, `cli_attested`, `synthetic_demo`), and the API test reads it
  back from `/api/v1/audit`. Nobody has watched the browser write one: click *Yes, do it* on
  `/proposals` and then read the last `adaptation_approved` row — `channel` must say `web`.
* **Whole-file encryption off Linux.** `/api/v1/status` reported `driver=sqlcipher`,
  `file_encrypted=true` here because `sqlcipher3-binary` installed cleanly. On macOS/Windows it
  will not, and the app falls back to column-level encryption — honestly reported, but *untested*
  here. Check `storage.file_encrypted` on the machine you deploy to; and do not switch
  `ADAPT_DB_ENCRYPTION` on an existing database, which currently fails with
  `sqlite3.DatabaseError: file is not a database` rather than an explanation.
* **Login lockout on a real deployment.** The buckets are enforced and unit-proven
  (5 failures per address/connection-minute, `Retry-After` set, a correct password refused while
  the budget is empty). What is unproven is the UX around it: on a shared NAT, six failed sign-ins
  a minute across a class will lock the room out, and the sentence the learner sees comes from the
  `note` field. Raise `ADAPT_RATE_LIMIT_MULTIPLIER` for a rehearsal; do not lower it to 0.
* **Upload caps measured from a browser.** `/voice/transcribe` and `/numbas/import` now refuse
  while streaming, verified with a fake upload that counts bytes handed over
  (16,777,216 buffered before the fix; at most ~9 MiB after). A real browser's `fetch(stream)`
  path, and what a 20-minute recording does to memory before the cap bites, is not covered here.
* **`alembic upgrade head` on a database with learner data.** The tests run it against an empty
  file, a `create_all` file and a downgraded one. Nobody has run it against a months-old
  deployment: do that on a copy (`cp adapt.db /tmp/x.db`), because `render_as_batch` rebuilds
  tables and SQLite has no rollback to offer.
* **`scripts/check-contract.mjs` coverage of dynamic paths.** It resolves the 16 literal call
  sites the frontend makes. Paths assembled at runtime (none today) would slip through, and the
  mock-parity rule is "some fixture regex accepts a concrete sample of the path", which cannot
  prove the fixture's *body* is the right shape — `?mock=1` still needs a human eye.

## What the second pass added, and what it did not (2026-10-12)

* **Voice, at the protocol boundary:** 12 checks driven by a raw `websockets` client against the
  running app — `hello` naming both sample rates, `config_ack` echoing what the server actually
  took, `ping`/`status`/unknown-frame behaviour, a typed turn answered by `reply` with
  `speak_unavailable` naming the fallback, 130 binary microphone frames reaching the VAD
  (130 frames, 83,200 bytes, `turns=2`), `stop` returning the turn to listening, a reconnect
  getting a fresh `hello`, and an anonymous socket refused *in a frame the page can read* (F9).
* **Voice, below that boundary — still unverified here:** no microphone, no `faster-whisper`, no
  `kokoro`, no `espeak-ng`, no browser. With no local STT the transcript that comes back is
  `{"text": "", "backend": "browser", "note": "browser STT supplies text, not audio"}` — an empty
  result labelled as such, never invented words; that is the *degradation path* being verified,
  not transcription. Real accuracy, real latency, real barge-in timing and the audible difference
  between `af_heart` and `am_fenrir` have to be judged on a machine that can install them
  (`pip install 'adapt-backend[voice]'`, Python ≥3.11 <3.13).
* **Interaction, without a browser:** `npm run probe` drives the built pages in jsdom (139 checks
  across ten screens). It catches hydration-order bugs, real click→request→DB round trips and
  what the screen says afterwards, and it can break one endpoint on purpose (the `failures`
  scenario answers 429 to the session read and asserts the app does not turn that into a sign-out).
  It cannot catch: CSS/layout at any viewport, focus rings,
  `getUserMedia` prompts, autoplay policies, or anything a screenshot would show.
* **Model quality:** unchanged from above — no key, no egress, so no real Gemini answer has ever
  been produced by this build. Everything asserting about AI 1 and AI 2 in this repository is
  about the local path and the gate, not about model cleverness.

## Added by the 2026-10-12 full-scan pass

* **The UI probe drives jsdom, not a browser.** `npm run probe` (139 checks) exercises the
  real Next.js server over HTTP with a real DOM: routing, forms, controlled inputs,
  fetch/CSRF/error paths, refresh, the voice socket's handshake and the screens' data paths.
  It cannot observe layout, paint, focus order, scroll, `getUserMedia` permission prompts or
  `speechSynthesis` voices. "The screen works" therefore means every interaction and every
  request it makes works -- not that it looks right.
* **A real Gemini answer, still.** With a syntactically valid but unauthorised key,
  `POST /api/v1/explain` answers 200 from the local tutor and reports the failure in `llm`
  (`error: ConnectError …`, `fell_back_to: "local tutor (extractive, grounded in course
  material)"`). That is the honesty property proven; whether a real answer is *good* still
  needs a key and a network.
* **The four resource hosts from here.** All four fail DNS in ~30 ms. The matrix
  (valid/empty/unreachable/bad key/timeout/429/local rate limit) is now covered in
  `backend/tests/test_resource_providers.py` with the real adapter code and a substituted
  transport; the live call remains the unverified half.
* **Audio, still.** `faster-whisper` and `kokoro` remain uninstallable here. What is newly
  verified is everything around them, on synthetic audio: endpointing, barge-in on sustained
  speech, a cough *not* interrupting, `stop()` cancelling obsolete audio, a failed
  transcription surfacing as a recoverable error, and an empty-but-honest transcript when no
  local model exists (`backend/tests/test_voice_pipeline.py`).
* **A real Numbas editor/server.** Still unverified: packages here are round-tripped against
  this build's own importer. Newly verified is that a *foreign* package (a tag naming a topic
  by code, or a part type with no local grader) is now handled instead of answering 500.

## Added by the second half of the 2026-10-12 full-scan pass

* **The new endpoint coverage is exercised, not asserted from the schema.** `journey_check.py`
  §9b/§9c calls `GET /topics`, `/syllabus`, `/materials`, `/strategies`, both revoke forms and the
  choice-grading path against a real server, and fails if any of them stops working. That is still
  *this* build's answers, not a second implementation's — the schedule it prints is unverified
  against an outside planner.
* **What the coverage sweep cannot tell you.** A route can be called by every check here and still
  be wrong for a use the checks do not imagine; the sweep proves something *responds and is
  internally consistent*, not that its judgement is good.
* **Choice grading, both directions.** A correct choice is graded `correct=True, score=1.0` whether
  it arrives as the index the reveal gives or as the choice text the screen sends. What is not
  verified is a learner who picks a *distractor* on purpose: the misconception attached to a wrong
  choice is stored and shown, but whether a human would agree with the misconception map is a
  content question, not a code one.
* **A clean-clone bootstrap is not a deployment.** `venv + pip install -e './backend[dev]'`,
  `npm ci`, `npm run build`, boot — measured on this machine, single process, SQLite on a local
  disk, no TLS, no container, no reverse proxy, no concurrency beyond one learner at a time.
