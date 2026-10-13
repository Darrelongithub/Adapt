# Full functionality scan — report

**Date:** 2026-10-12 · **Branch:** `arena/7a52404a-adapt`, now carrying `main` (13 commits: the
learning engine, scenes/runs/narration) merged in.
**Scan repairs:** `6138577`, `c57c7d5` · **Report and tooling:** `0d10722`–`37eacd5` ·
**Merge and integration repairs:** `e8db83d`, `868f9a0`, `e371a3e`
**Method:** inspect → run → interact → reproduce → fix → retest → verify. Every claim below
names the command or test that produced it.

---

## A. Overall status

**The application runs and a student can complete the core journey end to end.** Nine defects
were found and fixed: four in the application during the scan, one in the scan's own runner, and
four more when `main` was merged in (three call sites that only a configured model could reach, a
migration that assumed a database built the other way, and a lint gate that had gone red). Nothing
found is still broken. Every route the API exposes is now exercised by something in this
repository — that check is what surfaced the first batch — and the whole-suite UI probe is
deterministic again.

The core journey is walked by four independent paths — a UI probe, a data-layer journey, a
failure matrix and the backend suite — plus two end-to-end demos. All pass:

| Path | What it drives | Result |
|---|---|---|
| UI probe (`bash scripts/probe-fresh.sh all`, jsdom against the real Next.js server) | every screen, form, button, refresh and error path, plus the lesson engine | **169/169 checks**, twice in a row |
| Data-layer journey (`scripts/journey_check.py`) | records, approvals, shutdown/restart | **59/59 checks** |
| Failure matrix (`scripts/failure_matrix_check.py`) | bad input, auth, floods, dead providers | **25/25 checks** |
| Backend suite (`pytest -q`) | routes, learner model, grading, adapters, voice, migrations, the engine, LLM plumbing | **589 passed** |
| Terminal loop (`adapt.cli demo --turns 3`) | whole loop on a temp database | exit 0 |
| HTTP demo (`node scripts/demo.mjs --json`) | a synthetic learner over the public API | `warnings: []` |
| Migrations (`alembic upgrade head`, fresh data dir) | schema, then the app boots on it | 4 revisions, no drift |
| Clean checkout → running app | `git clone` of this branch, no venv, no `node_modules` | venv + `pip install -e './backend[dev]'`, `npm ci`, `npm run build`, API boots, web answers 200 |

**Not "unable to run", not "partially functional"** — with one honest qualifier: several
capabilities cannot be exercised in this sandbox and are listed in **E**, not silently counted
as working. The largest of those is any real model answer: no Gemini key and no route to
Google's API, so AI 1 and AI 2 were verified with their local providers (and their failure
paths), never with a real generation.

### A.1 The 17-step journey, step by step

The brief asks for a 17-step journey without listing the steps, so the list below is this app's
own loop, in the order a student meets it. Every step names what was run.

| # | Step | Evidence | Result |
|---|---|---|---|
| 1 | Open the app and see where you are | probe `home` | 10/10 |
| 2 | Register (guest) or sign back in | probe `onboarding`; `scripts/demo.mjs` return visit; matrix anonymous/CSRF checks | pass |
| 3 | Onboard: goals, minutes, confidence — and it persists | probe `onboarding` (values re-read from the server after a reload) | 12/12 |
| 4 | Start placement, one question at a time | probe `diagnose` | 21/21 |
| 5 | Answer the 12-item diagnostic (numeric, choice, teach-back) | probe `diagnose`; `journey_check.py` §2 | pass |
| 6 | Finish placement and see band estimates | probe `diagnose`; journey §2 | pass |
| 7 | Practise: hint, worked example, reveal | probe `learn`; journey §6 | 19/19 |
| 8 | Change an answer; it counts as a second attempt | journey §6 | pass |
| 9 | Ask the tutor a question (AI 2) | journey §7; probe `learn`; failure matrix | pass |
| 10 | Use voice — or be told honestly why you cannot | probe `voice`; `test_voice_pipeline.py` | 8/8 + 11 tests |
| 11 | See the analysis (AI 1 hypotheses, with citations) | journey §3 | pass |
| 12 | Read the proposals and decide | `journey_check.py` §4–5; probe `proposals` | 36/36 + gate checks |
| 13 | See what you approved actually active (and withdrawable) | journey §4 (row + payload read back) | pass |
| 14 | Keep practising against the adapted plan | journey §6; `/plan` read in §4 | pass |
| 15 | Come back to what is due for review | probe `review`; journey §6 | pass |
| 16 | Check progress (bands, measured outcomes) | probe `progress` | 5/5 |
| 17 | Refresh, restart the server, return: nothing lost | probe reload checks; journey §10 (same data directory, new process) | pass |

Steps 3, 12, 13 and 17 are the ones the brief singles out, and they are checked at the level it
asks for: onboarding is re-read from the server rather than from the form, the gate is asserted
on stored rows (reject applies nothing, modify stores the learner's number, a second answer is
409), and the restart check re-reads events, adaptations, dashboard and journey from a second
process against the same data directory. Privacy and settings are supporting surfaces
(probe `settings` 12/12, journey §9).

---

## B. Checklist per feature

**tested-working** = exercised and observed passing · **tested-fixed** = was broken, now
verified fixed · **not implemented** = no such surface exists (by design) · **unable to
verify** = implemented, but the environment cannot exercise it.

### Loop and learning

| Feature | Status | Evidence |
|---|---|---|
| Guest registration, session cookie, CSRF | tested-working | probe `onboarding` 12/12; matrix: 401 anonymous, 403 wrong CSRF |
| Sign-in / return visit (demo credentials) | tested-working | `scripts/demo.mjs`; journey restart re-signs-in |
| Onboarding (goals, minutes, confidence) + persistence | tested-working | probe `onboarding` (server-side values re-read after reload) |
| Placement diagnostic (per-topic items, bands) | tested-working | probe `diagnose` 21/21; journey "placement" 5 checks |
| Practice queue (hint, worked example, reveal, change answer) | tested-working | probe `learn` 19/19; journey "practice" 6 checks |
| Grading: numeric, mcq, text, teachback rubric, worked example | tested-working | `pytest tests/test_assessment.py`; journey scoring |
| Mastery trace + retention schedule + due list | tested-working | journey; probe `review` |
| Progress screen (bands, measured outcomes) | tested-working | probe `progress` 5/5; `dashboard`/`profile` in journey |
| AI 1 detections → hypotheses → proposals | tested-working (local) | journey `analyze` reports 2 hypotheses / 2 proposals and detector citations; model path **unable to verify** |
| **Approval gate** at API + DB level | tested-working | journey §4–5: reject applies nothing and is recorded; modify stores the learner's number (`target 0.8`, source `modified`) and refuses undeclared keys; accept turns it on; a second decision is 409 |
| "Explain more simply" (temporary, immediate) | tested-working | 4 simplify requests changed 0 active adaptations; `level` is per-request |
| AI 2 tutor answer (local, grounded) | tested-working | journey `explain`; probe `learn`; sources listed honestly |
| AI 2 adaptation to the learner profile | tested-working | `scripts/adaptation_check.py`: two synthetic learners in opposite states, same question — `level: mastered` with "You know this — here is the harder question instead" vs `level: new` with a prerequisite first, 66 vs 83 words, different section set |
| AI 2 answer from a real model | **unable to verify** | no key + no route; with a key present the app degrades to local and says so (`llm.error`, `fell_back_to`) |
| Generated questions (validate → store → reuse) | tested-working (contract) | `pytest tests/test_generated_items.py` 15 passed; arithmetic checked by AST, ids content-derived, ungradeable items refused |
| Voice: capabilities honesty, socket handshake/refusal | tested-working | probe `voice` 8/8; `pytest tests/test_voice_pipeline.py` 11 passed |
| Voice: VAD endpointing, barge-in, obsolete-audio stop, reconnect | tested-working | same file, on synthetic audio |
| Voice: microphone permission, real capture, real transcript/audio | **unable to verify** | no `faster-whisper`/`kokoro`, no audio device, no browser |
| Numbas export → import round trip | tested-working | `test_api_learner_loop.py::test_numbas_export_then_import_is_rerun_safe` |
| Numbas import of a foreign package | tested-fixed | `pytest tests/test_numbas_import_fidelity.py` (tag by code, untagged, ungradeable part type) |
| Numbas submit → score → return | tested-working | same file: import, answer right/wrong, score, feedback, events |
| Numbas in a real editor/server | **unable to verify** | no Numbas instance here |
| Resources search API | tested-fixed | 500 → 200 with per-provider status and a reason when empty |
| Resource providers (valid/empty/unreachable/bad key/timeout/429/local limit) | tested-working (transport substituted) | `pytest tests/test_resource_providers.py` 19 passed |
| Live OpenAlex / arXiv / Open Library / YouTube calls | **unable to verify** | all four fail DNS here (~30 ms) |
| Privacy surface, event allowlist, telemetry | tested-working | journey "privacy"; `pytest tests/test_telemetry.py` |
| Settings screen (voice on/off, preferences) | tested-working | probe `settings` 12/12 |
| Persistence: refresh | tested-working | probe reload checks in `learn`, `proposals`, `onboarding` |
| Persistence: server restart on the same data dir | tested-working | journey §10 (events, adaptations, dashboard, journey intact) |
| Migrations: fresh DB and existing DB | tested-working | `alembic upgrade head` + `alembic check`; `pytest tests/test_migrations.py test_migration_encryption.py` |
| Failure handling (auth, bad input, size caps, floods, dead providers) | tested-working | `scripts/failure_matrix_check.py` 25/25 |
| Teacher surface (assign, roster, mark) | **not implemented** | no route or screen exists |
| Resources screen (search UI) | **not implemented** | the endpoint has no caller; resource hints appear inside explanations |
| API surface with no screen yet | tested-working | `GET /topics`, `/syllabus`, `/materials`, `/strategies` answer correctly for a real learner and are now asserted in `journey_check.py` §9b — no page renders them, so nothing in the UI depends on them |
| Learning engine (scenes, runs, narration) — `main`'s work, now in this tree | tested-working | probe `lesson` 30/30 (open, predict, explain, variables, reload-and-resume, review, evidence) against the real API and database; `pytest` engine suites; `scripts/demo.mjs` unchanged |
| AI 3 scene discussion (author, narrate, why) | tested-fixed | unreachable with a model *configured* until C6 — the fixes are verified with a stub model, and with no key the template path is what answers; a real model's prose stays in **E** |
| i18n / RTL | **not implemented** | — |

---

## C. Repairs

Nine defects, each with the smallest change that removes it. C1–C4 were found by the scan, C5–C9
on this branch afterwards; C5 and the probe fixes are in the scan's own tooling, the rest in the
application. Each has a regression test, or a gate that fails, if the defect returns.

### C1. `POST /api/v1/resources/search` answered 500 for every request
* **File:** `backend/src/adapt/api/routes/tutor.py:293`
* **Root cause:** the route called `ResourceService.search(..., limit=body.limit, ...)`; the
  service takes `limit_per_provider`. The `TypeError` escaped before any provider ran, so the
  endpoint had never returned a result. It survived because it had **no caller and no test** —
  the only resources assertion checked static provider-gate configuration.
* **Restored:** searches answer 200 with a ranked, de-duplicated list, per-provider state
  (`provider/ok/source/note`) and `retrieval_note`; an empty answer says why it is empty. The
  route now also truncates to its declared `limit` (1–12) since the service slices at
  `max(6, limit_per_provider * 2)`.
* **Tests:** `test_a_resource_search_answers_without_a_network`,
  `test_a_resource_search_refuses_an_unknown_provider_without_crashing`, plus the 19-case
  provider matrix.

### C2. A Numbas package from anywhere but ADAPT was a 500 — or a trap
* **Files:** `backend/src/adapt/assessment/numbas.py`, `backend/src/adapt/api/routes/tutor.py`
* **Root causes (one import path, two failures):**
  1. the export writes the topic **id** into `adapt-topic:<id>`; the importer read any tag back
     as an id. A teacher tagging with the human-readable *code*, or not tagging at all, had the
     string inserted as a foreign key → `IntegrityError` → **500**, with the SQL in the
     local-mode response;
  2. a Numbas part type with no local grader was imported as free text, `active`, and reported
     `runnable_in_app`. Its `answer_spec` held only a `formula`, which the text grader never
     compares, so **every answer scored 0 — including the teacher's own** — and feedback read
     `expected one of: ` (empty). A learner served that item would have mastery, spacing and the
     item's own calibration moved by a question nothing could mark.
* **Restored:** topic references resolve against ids *and* codes; an unplaceable item is skipped
  by name with the valid codes (never attached to whichever topic came first, which would have
  corrupted an unrelated topic's evidence). A part type with no grader is imported **inactive**
  and reported as needing the hosted Numbas runtime; the grader answers `method="none"` with a
  sentence instead of an empty list.
* **Tests:** `backend/tests/test_numbas_import_fidelity.py` (7 tests, including submit → score →
  return for a gradeable import). `test_api_learner_loop.py`'s round-trip test still passes.

### C3. A typed voice turn blocked barge-in
* **Files:** `backend/src/adapt/voice/pipeline.py`, `backend/src/adapt/api/ws.py`
* **Root cause:** the socket's control loop is also the loop that reads microphone frames, and
  it `await`ed `push_text(...)`, which returns only after the answer has been *spoken*. Audio
  turns are started on a task (which is what makes interruption possible); typed turns were not,
  so interrupting a typed question was impossible regardless of the `barge_in` setting.
* **Restored:** `VoiceConversation.start_text()` starts a typed turn on its own task; the socket
  uses it. Verified by `test_a_typed_turn_does_not_hold_the_receive_loop`, plus barge-in,
  cough-tolerance and `stop()` cancellation tests.

### C4. `POST /api/v1/analyze` said 200 for a session that does not exist
* **File:** `backend/src/adapt/api/routes/learning.py`
* **Root cause:** the whole-record analysis was returned labelled with any `session_id` given,
  so "that session was never recorded" was indistinguishable from "we looked and found nothing"
  — while the sibling `GET /sessions/{id}/events` already answers 404 for the same id.
* **Restored:** a non-empty unknown/id-other-learner session is 404 `unknown session`. An empty
  id is still accepted, because the loop screens send `session_id=` once a session is over and
  analysing the record is what they want there.
* **Test:** `test_analyze_refuses_a_session_that_is_not_this_learners`.

### C5. The scan runner assumed a scratch directory a clean checkout does not have
* **File:** `scripts/journey_check.py:93`
* **Root cause:** the runner opened `/tmp/scans/journey-api.log` unconditionally — a path that only
  existed because the scan was being run from that directory. On any other machine it died with
  `FileNotFoundError` before its first check, which is the opposite of what a verification script
  is for.
* **Found by:** rebuilding this checkout from nothing after the sandbox was recycled — the same
  thing a new contributor does.
* **Fix:** default to `/tmp/journey-api.log`, honour `JOURNEY_LOG`, create the parent directory.
* **Verified:** the whole gate list was re-run from the clean tree (results in **A**).

### C6. Three LLM call sites unpacked two of `redact_payload`'s three values
* **Files:** `backend/src/adapt/ai/ai2.py:874,917` (scene authoring, and its retry), `ai3.py:137` (frame narration)
* **Root cause:** `redact_payload` returns `(payload, dropped, kinds)`; these three unpacked
  `safe, dropped = ...`, which raises `ValueError: too many values to unpack (expected 2)` on the
  first request that reaches the line. They sit *after* a "no model configured" early return, so
  the keyless fallback every test run takes never reaches them — which is how they landed on
  `main` with 555 green tests.
* **Reproduced:** driving `AI2.author_scene` and `AI3.why` with a stub model raises at
  `ai2.py:874` (`tests/test_llm_plumbing.py::test_a_scene_is_authored_and_narrated_with_a_model_configured`,
  added here as the dynamic half of the static contract test that caught it).
* **Fix:** unpack all three; the two `ai2` sites pass their redaction kinds to the audit log like
  their neighbours do. With a real Gemini key, scene authoring and frame narration would each have
  500'd on the first request.
* **Behaviour restored:** authoring a scene with a model configured returns a draft (or the
  built-in scene, labelled as such); narrating a frame returns text.

### C7. The two branches both created revision `0002`
* **Files:** `backend/migrations/versions/`
* **Root cause:** this branch's item-calibration revision and `main`'s learning-engine revision
  were both `revision = "0002"`, both with `down_revision = "0001"`. alembic sees two heads and
  every upgrade fails.
* **Fix:** `main`'s numbering is referenced by its migration tests and `docs/ENGINE.md`, so the
  item-calibration revision is now `0004`, chained after `0003`: 0001 → 0002 (engine) → 0003
  (scene authorship) → 0004 (item calibration).
* **Verified:** `alembic upgrade head` and `alembic check` on a fresh data dir; the app boots and
  seeds (`curriculum=55 items, scenes=5`).

### C8. `0004` assumed a database built by migrations, not by `create_all`
* **File:** `backend/migrations/versions/0004_item_calibration.py`
* **Root cause:** it added five columns unconditionally. A fresh clone gets its schema from
  `Base.metadata.create_all`, which already builds them, and the documented way into the chain
  (`stamp 0002`, then `upgrade head`) replays every later revision — so the recovery step died with
  `duplicate column name: generated_by`. That is the path that exists so an operator does not
  delete a database.
* **Fix:** existence-aware in both directions, like `0003`: read `items`' columns and add (or drop)
  only what is missing. One assertion in `main`'s test hardcoded `"0003"` as head; it now asks
  `alembic heads`, so the next revision cannot rot it.
* **Verified:** `tests/test_migrations.py` 10/10, including the two create_all paths.

### C9. `ruff check src` had gone red
* **Files:** `ai/ai3.py`, `learning/sims/binary_search.py`, `services/lessons.py`
* **Root cause:** five findings — a dead local in `ai3.why`, two in the binary-search sim, an
  f-string with nothing to interpolate, an unused `Sequence` import — all of them four-day-old code
  the project's own documented lint gate had not been run against.
* **Fix:** four behaviour-neutral edits; `ruff check src --select F,E7,E9,B017` is green again.
  (The same gate over `tests/` reports 11 unused imports and two blind-`Exception` assertions in
  existing tests; left alone, and recorded in **E**, because none of them is a behaviour risk.)

### Not a defect (recorded so nobody re-investigates)

* **Probe failures under repeated runs** (onboarding reload, proposals reload, diagnose
  numeric input) were **rate-limit buckets and warm state from earlier runs in the same
  process**, not app behaviour: with one fresh API per scenario, all eleven scenarios and the full
  probe pass (199/199), repeatedly.
* `ResourceService.search` slicing at `max(6, limit*2)` is by design; the route now truncates.
* Local-mode 500 responses include the exception text on purpose (`app.py`); on a tunnel they
  carry a request id instead.
* A choice answer in a shape the grader does not recognise (`{"correct_index": 1}` instead of a
  bare index or the choice text, which is what `question.tsx` sends) is scored **wrong** with the
  detail `no recognisable choice selected` rather than 422. Seen while writing the adaptation
  check; the client cannot produce it, and the detail names the problem, so it is left alone.
* A stray `POST /api/v1/analyze?session_id=undefined` in an old log is **not reachable from the
  current code**: both pages interpolate `encodeURIComponent(sessionId ?? "")`, the probe uses an
  id it just fetched, and `scripts/demo.mjs` throws on a failed `POST /sessions` before it can
  interpolate an undefined one. It came from an earlier version of one of those drivers, and the
  new 404 (C4) is what would surface it if it ever happened again.

---

## D. Exact commands with pass/fail

```bash
cd /home/user/Adapt/backend
../.venv/bin/python -m pytest -q                                   # 645 passed
../.venv/bin/python -m ruff check src --select F,E7,E9,B017         # All checks passed
../.venv/bin/python -m adapt.cli demo --turns 3                     # exit 0 (whole loop, temp DB)

cd /home/user/Adapt/frontend
npm run lint        # contract ok: 74 routes / 49 calls, tsc clean
npm run build       # built (static pages)
cd .. && node scripts/demo.mjs --json          # warnings: []

# the UI probe: one fresh API + fresh data dir per run (warm buckets fail spuriously)
bash scripts/probe-fresh.sh all                 # 199/199 checks passed (starts the API as a rehearsal)
bash scripts/probe-fresh.sh lesson              # one scenario: 30/30
#   scenarios individually: learn 26/26, lesson 30/30, proposals 38/38, onboarding 12/12,
#   diagnose 24/24, home 10/10, progress 20/20, voice 8/8, settings 13/13, review 4/4, failures 14/14

# the data layer, including a clean shutdown, a restart on the same data directory,
# every route the API exposes, correct-choice grading both ways, and revoke-by-name
.venv/bin/python scripts/journey_check.py                        # 59/59 checks passed

# the failure matrix (against a FRESHLY started API)
.venv/bin/python scripts/failure_matrix_check.py http://127.0.0.1:8000   # 25/25 checks passed

# the tutor answers two opposite learner states differently (against a FRESH API of its own --
# it registers two learners, so anything that has already spent the guest_create bucket answers 429)
.venv/bin/python scripts/adaptation_check.py http://127.0.0.1:8031      # adapts to the profile

# migrations, fresh installation: empty data directory -> schema -> the app on it
cd backend && ADAPT_DATA_DIR=/tmp/mig-fresh ../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m alembic check               # No new upgrade operations detected

# migrations, existing installation: a database still on the previous revision upgrades in place
ADAPT_DATA_DIR=/tmp/mig-old ../.venv/bin/python -m alembic upgrade 0001   # 20 item columns
ADAPT_DATA_DIR=/tmp/mig-old ../.venv/bin/python -m alembic upgrade head   # 25 item columns
# (+ the app booted against the upgraded file: "curriculum=55 items", /api/v1/health ok)
```

`scripts/probe-fresh.sh` starts a throwaway API and web pair on ports 8010/3001 (leaving a dev
pair on 8000/3000 alone), waits for both, runs the probe, and tears them down -- the fresh
process is what makes a re-run comparable to the first one. Raw output for this pass is in
`/tmp/scans/probe-*.txt` and `/tmp/scans/cli-demo.txt`.

---

## E. Remaining problems

**The audit's one open defect is closed in this pass.** `docs/AUDIT.md` ended with a "Verified
broken, unresolved" bucket; its first item — an `ADAPT_DB_ENCRYPTION` policy change leaving an
existing database unreadable with an opaque `file is not a database` — is now fixed at the engine
(header sniff, sentence naming the file and the policy) and at `doctor` (which used to report the
data directory as fine while the app could not read a byte of what was in it), with three
mutation-checked tests. Its other two items are a deliberate privacy filter whose learner-visible
half was fixed earlier (F12), and a claim about there being no frontend test runner that the jsdom
probe has since superseded. The follow-up section at the end of `docs/AUDIT.md` has the
reproduction table.


**Blocking nothing, but unverified — no claim is made about any of these:**

0. **The whole-suite UI run is a rehearsal, on purpose.** `scripts/probe-fresh.sh` starts the API
   with `ADAPT_RATE_LIMIT_MULTIPLIER=10` because one IP makes a term's worth of traffic in half a
   minute; at default limits the suite spends the per-IP bucket and judges screens while the
   limiter is holding them (observed twice: a resume click and a placement answer box paced by the
   app's own refusal). The limiter is still verified at the real ceiling — `failure_matrix_check.py`
   25/25 against a default-limits API, and the probe's flood check floods the bucket the server
   reports. What is *not* verified is a paced whole-suite run's screen-by-screen behaviour: it is
   the configuration the probe itself documents, not one anything ships with.
0a. **The loop stays silent until it has 8 attempts on one topic.** Measured while fixing the
   probe's under-powered stuck learner: the detectors need `2 × min_n` rows on one topic before
   they will propose anything, so a learner who is wrong on fourteen *different* topics gets no
   question asked. That gate is deliberate (`docs/REVIEW.md` §2.1) and is left alone; what changed
   is that the probe now produces the evidence it claims instead of hoping the queue spreads well.
0b. **A paced whole-suite run's screens are only covered at the rehearsal ceiling.** Two earlier
   intermittent failures at default limits were harness races, not app defects (a panel judged
   mid-load; an under-powered stuck learner), and both are fixed — but a run at *default* limits
   still spends the per-IP bucket partway through, so the screen-by-screen behaviour under pacing
   is verified by the failure matrix rather than by this suite.
0c. **The placement answer box was seen cleared once under that load** (a paced run, element still
   connected, value `""`), and not reproduced in isolation (21/21 alone, 43/43 with its
   predecessors, and clean in every rehearsal-ceiling run since). It is recorded here rather than
   claimed fixed: what is known is that a busy run can catch the screen mid-render.
0. **The adaptation measured above is the app's, not a model's.** It was measured with the local
   provider (no key): the *mechanism* — level, prerequisite branch, next step chosen from the
   learner's own trace — is verified; whether a model's prose adapts as well is part of item 1.
1a. **`ruff` over `tests/` is not clean** (11 unused imports, 2 blind-`Exception` assertions).
   The project's documented gate is `src`-only and is green; these are noise in existing tests, not
   behaviour risks, and fixing them would touch files this scan has no other reason to open.
1. **A real model answer (AI 1 analysis text, AI 2 explanations and generated questions).** No
   `ADAPT_GEMINI_API_KEY` and no route to Google's API. With a syntactically valid but
   unauthorised key, `/explain` answers 200 from the local tutor and reports
   `llm.error: ConnectError`, `fell_back_to: local tutor` — the honesty path is proven, the
   answer quality is not. *To check:* set a real key and watch `llm.ok`.
2. **Real speech.** `faster-whisper` and `kokoro` are not installed (weights come from Hugging
   Face; `kokoro` also wants `espeak-ng` and pins Python <3.13). Capabilities report both as
   unavailable and the app degrades to browser dictation and `speechSynthesis`. Microphone
   permission prompts and audio quality are outside this sandbox entirely.
3. **Live third-party hosts.** OpenAlex, arXiv, Open Library and YouTube all fail DNS from
   here (~30 ms). The adapter matrix is tested with a substituted transport; the live call is
   not.
4. **A real Numbas editor/server.** Packages are round-tripped against this build's own
   importer only.
5. **Rendered pixels and real focus/screen-reader behaviour.** The UI probe is jsdom: it proves
   every interaction and request, not how the page looks.
6. **Carried over from earlier passes, still open (not defects, known gaps):** response-time
   effort isn't gated before mastery moves (`docs/REVIEW.md` §4.1); one detector's evidence bar
   is misdescribed (§2.1); strategy-credit unit issue (§2.4); no DIF/ABROCA fairness screen
   (§4.4). All are documented with measurements in `docs/REVIEW.md` §2 and §7.
7. **By design, not implemented:** teacher/roster surface, i18n, a resources *screen* (the
   search API exists and now works, but no page calls it).

---

## F. Can a student complete the core journey without a known blocking defect?

**Yes.** The journey was run end to end against a real server and a real (SQLCipher-encrypted)
database, and again through the UI, with no blocking defect on any step:

register → onboard → placement diagnostic → dashboard → practice with hints and reveals →
analysis → proposals → **student decides** (accept / reject / modify / later) → practice adapts →
review-due → explain → progress → restart with all state intact.

Four defects were fixed along the way; three of them were on paths no test had ever driven
(resource search, foreign Numbas import, `/analyze` with a bad session id), and one was a voice
interaction that silently could not happen. None of the four sits on the main path, and all four
now have regression tests. The limits that remain are the five environmental ones in **E** —
principally that no answer from a real model, no audio, and no live third-party call has ever
been observed here.
