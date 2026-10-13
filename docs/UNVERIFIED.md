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
* **Verified later, and it changed the answer:** the audit pass ran in an environment where
  `sqlcipher3-binary` could not be imported, so `db_encryption="auto"` selected field-only and said
  so — and that is the state several of the first documents describe. The package is a declared
  dependency, so once `pip install -e "./backend[dev]"` has run the whole-file path is the one that
  runs: the database begins with random bytes, `GET /api/v1/status → database.file_encrypted` is
  `true`, `PRAGMA cipher_version` answers, and `alembic upgrade head` connects through the same
  engine rather than a URL (an older `env.py` rebuilt one from `alembic.ini`, which quietly wrote a
  *plaintext* database into a fresh `ADAPT_DATA_DIR`; `test_migrations.py` now fails on that shape).
* **Still not verified here:** whether the KDF and page-size defaults match a deployment you would
  actually run — nothing in this codebase tunes cipher parameters — and any behaviour that needs a
  second process holding the same encrypted file open (WAL under concurrency, backup while live).
## Item bank and placement quality

* **Verified:** every item's answer key is reachable by the grader, the diagnostic covers one
  band range per topic, the exported Numbas package carries the correct answer for mcq, and
  the learner-facing payloads never contain one (asserted by a string scan in the tests).
* **Not verified:** that the items are *good test questions*. 63 items across 22 topics (20
  GCSE-maths, plus one history and one biology concept) were authored here; `difficulty` is an
  author's prior, not a pilot-derived estimate, and nothing has been checked against real
  cohort data, mark schemes or an exam board's content limits. Placement bands are therefore usable for choosing what to practise next and
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
* **`scripts/check-contract.mjs` coverage of dynamic paths.** It resolves the 49 call
  sites the frontend makes against the 71 backend routes. Paths assembled at runtime (none today) would slip through, and the
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
* **Interaction, without a browser:** `npm run probe` drives the built pages in jsdom (169 checks
  across eleven scenarios). It catches hydration-order bugs, real click→request→DB round trips and
  what the screen says afterwards, and it can break one endpoint on purpose (the `failures`
  scenario answers 429 to the session read and asserts the app does not turn that into a sign-out).
  It cannot catch: CSS/layout at any viewport, focus rings,
  `getUserMedia` prompts, autoplay policies, or anything a screenshot would show.
* **Model quality:** unchanged from above — no key, no egress, so no real Gemini answer has ever
  been produced by this build. Everything asserting about AI 1 and AI 2 in this repository is
  about the local path and the gate, not about model cleverness.

## Added by the 2026-10-12 full-scan pass

* **The UI probe drives jsdom, not a browser.** `npm run probe` (199 checks) exercises the
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

## Added when `main` was merged into this branch (2026-10-12, later the same day)

* **`main`'s learning engine is now in this tree and was exercised, but not by a person.** The
  probe drives the lesson screen end to end against the real API and database (30/30: open, a
  prediction judged, an explanation, changing the variables, a reload that offers the frame you
  stopped on, the review, the evidence list), and the backend suite covers the engine's own
  modules. What no check here can see is whether the *animation* looks right, whether the frames
  read well in sequence, or whether the narration timing is pleasant — that is a screenshot and a
  person, and neither is available in this sandbox.
* **AI 3 with a real model.** Three of its call sites could only be reached with a model
  configured, and they raised (see C6). They are fixed and now covered with a stub model that
  answers nothing useful, so what is verified is the *plumbing and the fallback*: a scene is
  authored through the built-in path, a frame is narrated through the template path, and each says
  which one the learner got. Whether a real Gemini answer narrates a frame well is unverified for
  the same reason as everything else model-shaped here: no key, no route.
* **The whole-suite probe run is a rehearsal, and that is now explicit.** It starts the API with
  `ADAPT_RATE_LIMIT_MULTIPLIER=10`; at default limits the suite spends the per-IP bucket mid-run
  and screens are judged while the limiter holds them. The limiter is verified at its real ceiling
  elsewhere (failure matrix, and the probe's own flood check, which reads the effective bucket from
  `GET /api/v1/quotas`). A paced whole-suite run's screen-by-screen behaviour is *not* verified.
* **All three whole-suite flakes were races, now fixed.** (a) The proposals check read the "On
  right now" panel while it was still loading and reported an applied change as missing — it now
  waits for the panel. (b) The "clearly struggling" learner answered 14 items spread across
  topics while the detectors need 8 attempts on *one* topic, so the evidence the check claimed did
  not exist — it now concentrates its wrong answers. (c) The placement answer box (`numeric-state-reset`
  in `diagnose`) was seen read back empty once, with the element still connected, in a
  default-limits run. **Closed in this pass, and it was a real defect.** The answer lived in
  `Question`'s own `useState` and was cleared in a passive effect or ref check on item changes:
  leaving a question and coming back, or a parent re-render replacing the subtree, threw it away,
  and `Back` also discarded the mark (so `Next` stayed disabled). It is now kept per item in
  `frontend/src/lib/drafts.ts` (sessionStorage + an in-memory fallback) with the receipt held per
  item by the page, and `frontend/src/components/question.tsx` reconciles per-item state
  synchronously during render via a derived `shownKey` (`${item.id}|${item.hint_count ?? 0}`).
  The probe checks both directions — the answer survives a round trip, and the way forward is not
  locked — and the check is **not vacuous**: with the draft store disabled (`loadDraft` returning
  the empty draft, which is the pre-fix behaviour) the diagnose scenario fails 2 of its 24 checks
  and prints the loss — `before=text:1/2 after=text:` and `before=text:The denominator says…
  after=teachback:` — while passing 24/24 with it restored. What remains unproven is only what
  jsdom cannot see: a real browser's input events at a real frame rate.
* **The 0002 collision is a real-world migration hazard for anyone who ran the pre-merge branch.**
  A database stamped `0002` by *this* branch before the merge (item calibration) cannot be told
  apart from one stamped `0002` by `main` (learning engine); the merged chain reads it as the
  latter and then fails on 0004 with a duplicate column. These were both unmerged branches, so no
  deployment is in that state, but a scratch data directory from before the merge needs to be
  recreated rather than upgraded. Nothing in the repository can distinguish the two.

## Added by the `adapt host` work (2026-10-12)

* **Verified in the sandbox:** both servers started by one command and answering on the
  detected address; a request carrying `Host: 192.168.1.50:3000` reaching the web server, the
  `/api/*` proxy and `/healthz` with 200s; a guest `POST` through the proxy carrying
  `Origin: http://192.168.1.50:3000` answering 200; the API accepting that exact host while
  refusing `192.168.1.51`, `192.168.1.50.attacker.net` and a foreign domain with 400
  `untrusted host`; the raised multiplier visible in `GET /api/v1/quotas` (120 → 1200/min,
  6 → 60 new learners/min); `.env` values merged and never printed; and the voice socket
  upgraded through the proxy with a LAN `Host`, `hello` received, and a tutor reply over it
  (a strict client, so this also covers the `101` header bug fixed alongside).
* **Not verified:** a **real second device** on a real wifi (no second device, and this
  sandbox's only non-loopback address is link-local `169.254.0.21` — the LAN path was driven
  with `--ip 192.168.1.50` and LAN-style `Host` headers over loopback); a **real phone
  microphone** (no browser here, so "the voice screen cannot listen on plain http and can on
  https" is a secure-context rule from the platform, not something this build watched happen);
  and a **real tunnel** (`cloudflared` is not installed and `localtunnel.me` is unreachable, so
  what is tested is the failure path: no runner on PATH, and a runner that prints no URL both
  leave the LAN address serving and say so).
* **To check at rehearsal (~5 minutes):** `adapt host`, open the printed address on a phone,
  create a learner, answer one question; then `adapt host --tunnel` from a machine with
  cloudflared installed, confirm the https link appears on screen, open `/voice` on the phone
  and say one sentence — the microphone should prompt rather than refuse, and the tutor's reply
  should come back. If the mic is still refused on https, the problem is the browser's
  permission for that origin, not this command.

## Added by the mobile-viewport repair pass (2026-10-12)

The first report of the UI from a real phone was blunt: it broke and was unusable, and the app
felt slow and weird even served over the LAN. That is now a *repaired, still-unverified-on-glass*
area. What changed, and what a phone still has to confirm:

* **Repaired (in code, verified only by reasoning, build and the jsdom probe):**
  * The status readout (`StatusChip`) was a `position: fixed` bar over the bottom-left of every
    screen. On a phone it covered content and — being a solid overlay above the page — ate taps
    aimed at whatever was underneath it, including the practice screen's primary button. It now
    sits in the footer's flow on touch devices and only becomes the prototype's fixed corner on
    `min-width: 641px and (hover: hover)` (so a landscape phone does not get it back), and it is
    `pointer-events: none` everywhere.
  * Typefaces were fetched from `fonts.googleapis.com` by a render-blocking CSS `@import` — two
    internet round trips before first paint, and a wrong-fallback or blank-text screen on any
    network that cannot reach Google (a school LAN, an offline room, a tunnel hiccup). The three
    families are now self-hosted `@fontsource-variable` woff2 files bundled by Next, with
    metric-similar system fallbacks. Also fixed: iOS Safari's zoom-into-any-field-under-16px
    behaviour (`input, textarea, select` are 16px on narrow screens), 44px minimum tap targets
    on `pointer: coarse`, `100dvh` instead of `100vh`, safe-area insets, the review "horizon"
    plot stepping aside below 700px (its absolute labels overlapped into soup; the cards below
    carry the same facts), audit/calibration tables scrolling inside their own box instead of
    stretching the document sideways, band rows wrapping instead of overflowing, and the paper
    grain being a pre-rasterized PNG tile instead of a full-viewport SVG `feTurbulence` filter
    (re-rasterized on every pinch-zoom/toolbar resize on iOS — felt as scroll jank).
* **Not verified:** any of it on actual glass. There is still no browser in this sandbox — no
  headless Chromium can be installed here — so "the probe is green" means interactions and data
  are right at jsdom fidelity, *not* that a 375×812 Safari/Chrome viewport looks right. The
  specific things to look at on a real phone remain: text sizes and tap targets on `/learn`, the
  filmstrip pan on `/lesson`, no sideways scroll on `/settings` (audit table) and `/review`,
  the fixed status corner absent (footer copy present instead), and first paint being immediate
  with the right serif/mono faces even in airplane mode after the first visit.
* **The progress chart is new and has not been seen on glass either.** `/progress` now draws the
  estimate chart from the design snapshot (position = estimate, line width = the server's 90%
  interval, circle size = answers behind it) with a table of the exact figures underneath. Its
  styles had shipped in `globals.css` with nothing rendering them. The drawing scales by
  `viewBox` and the table sits in the same `.table-scroll` box as the audit tables, so the
  reasoning says a portrait phone gets a legible chart and no sideways page scroll — on glass,
  check that the lane labels are still readable at 375px wide (they are clipped at 27 characters,
  and the full title is in the table) and that the interval lines are distinguishable from the
  estimate ticks without colour.
* **To check (~5 minutes, same rehearsal as `adapt host`):** open the LAN address on a phone in
  portrait *and* landscape, walk Practise → answer → Check it (the button must respond on the
  first tap), open Settings and Review and try to scroll the page sideways (it must not), then
  reload with wifi off — the typefaces must still be the same ones.

## Added by the 2026-10-12 pitch pass (15 defects)

* **A real Gemini answer, still — and now provably not needed for the pitch to be honest.** No
  key and no egress here, so the model half of AI 2 remains exactly as unverified as above. What
  *is* verified is that the demo path never depends on it: `scripts/demo.mjs` prints
  `provider=local grounded=true sent_to_model=false`, and `POST /api/v1/explain` with a
  syntactically valid but unauthorised key answers from the local tutor and reports the failure in
  `llm` (`backend/tests/test_llm_plumbing.py`). The one-line claim to make on stage is "no key, so
  this is the local tutor — the same interface a model would answer through".
* **The pre-demo pilot is seeded, not simulated in the stats.** `adapt pilot --student-id <id>
  --seed` writes ordinary `Attempt` rows flagged `pilot`; `learning/effect.evaluate_change` still
  refuses to conclude under five per group, and the method report still calls under eight
  observations an early signal. The label travels: `pilot: true`, `n_pilot`, and the sentence
  *"pilot data — not evidence of efficacy"* appear in the same payload as the number. Pinned by
  `backend/tests/test_pilot_labelling.py` and asserted by the `probe`'s progress scenario.
* **The subject-agnostic content path is exercised on prose, not on mathematics.** The
  quotability check (an answer key has to occur in the source material) and the three versions
  (text steps with a check each, a filled visual template, a worked example from the source's own
  sentences) are pinned in `backend/tests/test_subject_agnostic_content.py` against a biology and
  a history paragraph. The shipped catalogue now carries two prose concepts of its own —
  `history_ww1` (why the First World War began) and `bio_photosynthesis` (how plants make their
  food), each with course notes and four items — and
  `backend/tests/test_shipped_catalogue_has_prose_subjects.py` runs the real grading and the real
  concept-versions endpoint over them, so "any subject" is no longer only a fixture claim.
  What is *still* not verified is a curriculum of that shape: two prose concepts demonstrate the
  path, they are not a term's worth of imported material, and their notes were written here
  rather than taken from a textbook, a syllabus or a mark scheme.
* **The per-step check is not a mark, and the app says so.** The check between two teaching steps
  is compared against the words the step is about, never posted, and cannot move a mastery
  estimate. Its wording and its gate are asserted in the probe; how a *learner* reacts to being
  asked to write a sentence before the next step is a pilot question, not a sandbox one.

## Added by the visual-redesign pass (2026-10-13): navigation, consent, buttons

* **Verified (jsdom, 208/208):** that the consent step renders before the account form and
  before any `POST /students` on either the account or the guest path; that neither checkbox is
  pre-ticked; that the primary control is `disabled` until the required box is, and enabled
  after; that the optional box is a separate control and stays unticked; that Decline explains
  itself and creates nothing; that every screen's sidebar contains all seven destinations and
  marks the current one with `aria-current="page"`; that signed in, home offers exactly one
  primary action (the next step in that learner's journey) with no word-field menu, and says
  "Nothing due yet" / "Not measured yet" / "No answers yet" instead of printing zeros; that
  every migrated control carries a `.btn-*` class with an accessible name; and that the whole existing flow (placement,
  practice, the approval gate, lessons, review, settings, voice refusals) still passes.
* **Not verified, and not verifiable here — layout and paint.** There is no browser in this
  sandbox, and jsdom applies no CSS, so none of the following is evidence of anything on glass:
  the fixed 232px sidebar against the body's scrolling (including whether the drawer's
  `translateX(-100%)` state is correctly off-screen below 860px, and whether the topbar's 52px
  offset lines up with the drawer's `top`); the `position: fixed` sidebar over a long
  `/progress` chart; `main-content`'s 232px left margin and the footer inside it; the custom
  `appearance: none` consent checkbox with its `::after` tick; hover/pressed/focus-visible
  affordances on the new button classes; whether `color-mix(in oklab, …)` produces the intended
  tints in the browser a school actually runs (a plain alpha colour is the fallback if not);
  and whether the `--line` token at the brief's 72% still reads as a boundary rather than as
  text at 100% zoom.
* **Not verified — focus order and keyboard operation as a sequence.** The elements are
  operable buttons with names and states (checked by role and attribute), the roving tabindex
  and focus trap are the same utilities that already existed, and the drawer is a focus-trapped
  container while open — but with no browser, the tab order that a learner would actually walk
  (skip link → hamburger → sidebar → main) has not been demonstrated.
* **A functional gap, not a rendering one.** The optional research-consent answer is not
  persisted anywhere: there is no field for it, the screen says so, and the decision is
  deliberately not written into `consent_share_with_llm` (which means "a model may read a
  summary"). And "no decision" is still stored as consent by the database default
  (`Student.consent_analytics = True`) — see `docs/AUDIT.md`, fourth pass, for the shape of the
  fix and why flipping the default is not it.
* **Added after the door became username + password (same day):** creating an account and
  signing in with it are exercised over HTTP and asserted in the probe (`home`, 20/20), but the
  *stored* identifier and its blind index are only covered by the backend suite; a laptop
  check should make one account, sign out, sign in with the same name typed in a different
  case, and confirm the greeting keeps the capitalisation that was typed.
* **To check (~5 minutes on a laptop, then a phone):** load `/` and confirm the sidebar is
  visible without hovering anything and Settings is one click away; narrow the window past
  860px and confirm the Menu button opens the drawer and Escape closes it; tab through the
  consent step with the keyboard only and confirm the tick and the primary button are
  reachable and visibly focused; create an account and confirm `/onboarding` shows the consent
  answer carried from the door; then open `/progress` and confirm the chart, its
  table and the marked sidebar item are all visible together at 1280px and at 375px.

## Round: functionality (Phase 1, §4.3 shared learner state) — 2026-10-13

* **Not verified — anything in a browser.** Chromium cannot be installed in this environment
  (`npx playwright install` ends in `ECONNRESET`), so `frontend/tests-e2e/placement.mjs` still has
  never been executed and no screenshot has been taken. Every claim in the §4.3 report is from
  `pytest`, the CLI, or HTTP calls against a running server — none of them is a rendering claim.
* **Not verified — the §4.3 store under genuine multi-process concurrency.** The race test uses two
  threads against one database file; two *processes* on the same SQLCipher file (the way a second
  uvicorn worker would) are not exercised. The unique index and the optimistic `expect_version`
  checks are the mechanism either way, but "two processes" is an assertion, not a measurement.
* **Not verified — a *migration* of an existing encrypted database with real learner history.**
  The seven tables were added to `.data_dev/adapt.db` by `create_all` at startup and the data was
  intact (65 students, 76 attempts), but that is one database, on this machine, upgraded by
  `create_all` rather than by `alembic upgrade head`. The Alembic path is covered by
  `tests/test_migrations.py` on scratch databases only.
* **Not verified — model-version history growing without bound.** Every advance writes a row and
  nothing prunes them. For a demo-scale database that is a feature (the history is what
  `rebuild-model` checks against); at scale it would need a retention policy, which does not exist.
* **Known partial migration, deliberately.** AI 1's analysis path (`ai1.analyze`, `bkt_states`,
  hypotheses, proposals) still writes its own tables and reads its own rows; the store is updated
  alongside it from the same attempts. AI 2 now reads the learner's level from the store snapshot
  (`ai2.context()["snapshot"]`, `model_version`), and the serving path writes plans and deliveries
  through the Orchestrator. So "no AI reaches into another's tables" is true for the parts that were
  moved and not yet true for AI 1's analysis internals.
* **Not verified — the encrypted database after an abrupt kill.** The `disk I/O error` seen during
  this phase was caused by a *plain* `sqlite3` client touching the SQLCipher file while the server
  held it open; a clean shutdown cleared it and the data was intact. What an operator sees after a
  hard kill (SIGKILL, power loss) with a WAL left behind has not been tested.


## Round: functionality (Phase 2, section 5, the mind baseline) - 2026-10-13

Verified here (see the phase 2 report in `docs/FUNCTIONALITY_ROUND.md` for the numbers): the engine
and its 40 tests, the flow order end to end, the HTTP surface, the screens driven in jsdom against
the live API (25 checks) and against `?mock=1`, the contract linter and `next build`.

**Not verified here: the screens in a real browser.** Chromium cannot be installed in this sandbox
(`npx playwright install` ends in `ECONNRESET`), so nothing below has been seen with eyes or with a
real timer. jsdom exercises state, fetch traffic and keyboard handlers; it does not paint, and it
does not run a real 45-second clock.

To check on a machine with a browser (any of these that fails is a bug worth reporting):

1. `cd backend && ../.venv/bin/python -m uvicorn adapt.api.app:app --port 8000` and
   `cd frontend && npm run dev`, then open `/baseline`.
2. **The flash is watchable.** On the vault, the nine tiles light one at a time; on a phone-sized
   viewport the grid must not scroll during the flash. `prefers-reduced-motion: reduce` shortens
   the flash rather than removing it - check in devtools that the sequence is still shown.
3. **The study clock.** The study screen counts down from 45s and the ready button works at any
   point. Then answer "timers stress me" on the questions screen in a *new* run and confirm no
   clock appears anywhere in the baseline (latency is still measured - that is stated on screen).
4. **Keyboard only.** Tab to the vault grid, arrow to a tile, press Enter; then press the number
   keys 1-9. Both must tap. The machine and locked-box text fields must submit on Enter.
5. **Coins.** A 0-3 coin control appears on scored question tasks (cued recall, quick taps, traps)
   and never on a practice trial or the study screen.
6. **Stopping.** Press "Stop here" mid-vault and confirm the snapshot arrives; reload `/baseline`
   and confirm the resume line names the stage and does not restart the games.
7. **The snapshot screen reads correctly at 360px wide**, and no factor row overflows its card.
8. **Audio/latency claims**: none are made - the baseline is silent.
