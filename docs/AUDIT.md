# Code audit — 2026-10-11

A read-every-file, run-everything audit of this repository, done in the sandbox it was
built in (no browser, no model key, no outbound network beyond PyPI/npm/GitHub). The rule
throughout: a claim is either backed by a command and its output, or it is listed as
unverified. Where a defect was found, the fix is in the source and a test that fails
without the fix was added — several tests below were mutation-checked (fix removed → test
must fail → fix restored → test must pass) and the measured results are quoted.

## Commands run, with results

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest backend/tests -q` | **171 collected, 171 passed** (~22 s). Baseline at the start of this pass was 150 collected / 1 failed. |
| `.venv/bin/python -m pytest backend/tests/test_migrations.py -q` | 5 passed (new file; alembic runs in subprocesses) |
| `cd frontend && npx tsc --noEmit` | exit 0, no diagnostics |
| `cd frontend && npm run lint` | exit 0 — now `node ../scripts/check-contract.mjs && tsc --noEmit`; checked 58 backend routes against 16 frontend calls |
| `cd frontend && npm run build` | exit 0, 12 static pages, all 10 routes present |
| `node scripts/demo.mjs --base-url http://127.0.0.1:8000 --no-llm --strict --decide reject` | exit 0, “no contract violations found” |
| `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000{,/learn,/proposals,/voice}` | 200 200 200 200 |
| `curl -s http://127.0.0.1:3000/api/v1/health` | `{"ok":true,…,"database_reachable":true,"learners":6,"local_only":true}` |
| raw `Upgrade: websocket` GET to `:3000/ws/voice` | `403` + `{"error":"ws_handshake_refused","detail":"the backend refused the voice socket (403): sign in first, then reconnect"}` — fast, explained, no hang |
| `python -m websockets`-style connect to `/ws/voice` with no cookie (direct and via proxy) | refused in both paths, connection closed, no timeout |
| `alembic upgrade head` / `alembic check` / `alembic downgrade base` on a scratch `ADAPT_DATA_DIR` | 0 / 0 / 0; `check` reports no drift between `Base.metadata` and the revision chain |

## Fixed in this pass

Severity is what it does to a learner or a deployment, not how hard it was to fix.

### P1 — declared rate limits that nothing consulted (`security/ratelimit.py`, `api/routes/auth.py`)

`DEFAULT_LIMITS` has configured `auth_fail (5/60s)` and `guest_create (6/60s)` for the whole
build, and `GET /api/v1/quotas` prints `rate_limiter.scopes` as if both protected something —
but no caller ever asked those buckets. What the code actually did: sign-in was governed only
by the shared per-IP bucket (120/min, spent on every request in the site), i.e. roughly two
password guesses per second, indefinitely, per address; and `POST /api/v1/students` created
unbounded learner rows on a world-readable tunnel.

Fix: `Limiter.blocked()` (refill, spend nothing) + `_check_bucket`/`_spend` in the auth routes.
Failures drain the budget — per address **and** per connection — and while it is empty the
password is not read at all; a correct sign-in spends nothing, so a learner is never locked
out by their own activity. Creation costs one token per row, refused before the row is
written. Regression tests: `test_wrong_passwords_lock_the_address_out_before_the_password_is_read`,
`test_signing_in_correctly_never_costs_the_learner_their_own_lockout`,
`test_learner_creation_is_capped_per_connection`, `test_no_declared_rate_limit_scope_is_decoration`
(the last one reads the source and fails if any scope in the table goes unused again).
Mutation-checked: with the calls removed the suite reported
`assert 200 == 429, "the bucket is not enforced"` and accepted the 9th creation.

### P1 — a terminal could answer for the learner, and the demo did (`cli.py`, `services/adaptations.py`, `db/models.py`)

The consent gate itself was sound — `approved_adaptations` is written only by
`adaptations.approve`, reachable only through `decide`, which raises unless the caller passes
a `student_decision`. Two callers undermined it: `adapt decide` hard-coded
`"answered_by": "student"` (an operator at a keyboard could type a learner's yes), and
`adapt demo` auto-accepted the first pending proposal and printed `learner answered -> approved`
while the gate happily accepted `answered_by=None`. Also, nothing durable recorded *who*
answered: the proposal row keeps `status`, timestamps, note and modifications, and
`record_audit` stored the decision and `answered_at` but not `answered_by`, so a scripted
consent and a learner's own were indistinguishable in the database.

Fix: `answered_by` must now be the literal `"student"` (the old `None` acceptance is gone);
the audit detail carries `answered_by` and a `channel`; the web route stamps `web`,
`adapt decide` must say the learner answered (`--learner-answered`, or an interactive
confirm) and is recorded as `cli_attested`/`cli_confirmed`, and the demo's acceptance is
recorded as `synthetic_demo` and printed as “synthetic learner answered … (scripted consent,
recorded as such)”. Tests: `test_cli_will_not_answer_for_a_learner_it_cannot_ask` (exit 2, the
proposal stays pending, no active adaptation appears) and
`test_cli_answer_records_the_channel_that_gave_it` (the audit row says `cli_attested`).
The second test fails with `KeyError`-style output on the field when the audit write is
stripped, so the provenance is load-bearing, not decorative.

### P1 — the Numbas round trip silently downgraded the bank (`assessment/numbas.py`, `api/routes/tutor.py`)

`GET /numbas/export` → edit → `POST /numbas/import` is the spec's editing loop. It was lossy:
`question_export` had nowhere for `answer_spec.formula/variables/ranges/derived`, the teach-back
`rubric`, per-step `why`, `hints` as a list, `prompt_context`, `visual_spec`,
`experience_mode` or `est_seconds`, and the export route did not even pass `numbas_ref`. A
parametric item came back with no variables, so `render_variant` declined to render it and the
learner was handed the template: `GET /api/v1/items/<id>/reveal` returned
`'{a} - {b} = {ab}'`, `'{ab} - {c} = {ans}'`. Because the checksum identity also changed, each
round trip **added a second copy of the whole bank** (55 items became 110).

This is what made a full-suite run intermittently red while every file passed on its own: the
render path chosen for a test depends on which item the practice queue picks, and the imported
duplicates shifted it.

Fix: the export carries an `adapt_item` sidecar (versioned, 16 fields, including `item_id`);
`question_import` trusts it for the fields Numbas cannot express and still parses foreign
Numbas documents as before; the import route upserts by the exported `item_id` — content is
what an editor changes, identity is not — falling back to `numbas_ref` then checksum;
and `NB.renders_clean()` refuses to *activate* an item that still has unfillable
placeholders, reporting it in the import warnings instead of serving it.
Tests: `test_no_learner_facing_endpoint_shows_an_unrendered_template_for_any_item` (all 55
items × 6 learner-facing endpoints, 330 responses scanned),
`test_numbas_export_then_import_is_rerun_safe` (bank size identical before/after,
`imported == 0`, `updated == 55`, nothing deactivated, nothing left unrendered, no temp files)
and `test_the_item_handed_to_the_tutor_is_the_tutor's_variant` (a spy on `review_teachback`
asserting AI 2 is handed this learner's rendered stem). Removing the teach-back render fix makes
it fail with `the tutor was handed a template, not a question: 'Work out {a} …'`.

### P1 — `POST /api/v1/items/{id}/teachback` read the unrendered row (`api/routes/tutor.py`)

The hint, reveal, grading and feedback paths were all fixed to render under
`variant_seed(student, item)`; the teach-back was missed, so with a Gemini key the model is
asked to review an answer to `{a} + {b}` — text the learner never saw — and the local fallback
returns prose that hides the problem (which is why the placeholder scan could not see it and the
spy test exists). One-line fix, consistent with its neighbours.

### P2 — a "size limit" applied after the whole body was in RAM (`api/routes/system.py`, `api/routes/tutor.py`)

`SecurityHeaders`-adjacent docs said `BodySizeGuard` "is wrapped by the telemetry/voice handlers
that read raw bodies". `grep` found one reference to it: its own definition. Meanwhile
`/voice/transcribe` did `await file.read()` and *then* compared against 8 MiB, and
`/numbas/import` did the same against 4 MiB — for a chunked upload (no `Content-Length` for the
middleware to inspect) the cap was decoration.

Fix: the clip is read in 1 MiB pieces and refused the moment it crosses the line; the package
reader uses `BodySizeGuard` for real, with its `ValueError` translated to 413. Measured with
`_FakeUpload.delivered`: before the fix
`the handler buffered 16777216 bytes into RAM before applying the cap`; after, at most 9 MiB
is touched and the model is never handed the clip (`test_a_flooded_clip_is_refused_before_it_is_buffered_or_transcribed`,
`test_an_oversized_clip_is_refused_without_being_buffered_first`,
`test_oversized_package_is_refused`).

### P2 — model work on the event loop (`api/routes/system.py`, `voice/pipeline.py`)

The websocket pipeline already ran transcription through `asyncio.to_thread`; the REST fallback
did not, and neither did speech synthesis in either path. A whisper pass is seconds of CPU, so a
browser using the fallback, or a `/voice/speak` request, froze every other learner's request —
including the barge-in listener, which needs that same loop to notice an interruption.

Fix: `to_thread` for the STT call, for the WAV synthesis call, and for *pulling each chunk* of
the TTS stream (not `list(...)`: buffering the whole utterance would trade the freeze for lost
barge-in). Measured by whether a ticker keeps running during a 300 ms stub: after the fix
`ticks > 30`; with the call inlined, the test reports
`the loop advanced only 0 times during a 300 ms transcription -- it was blocked`
(`test_transcription_runs_off_the_event_loop`, `test_speech_synthesis_runs_off_the_event_loop`).

### P2 — a 500 narrated itself to whoever asked (`api/app.py`)

The catch-all handler returned `{"error": "internal_error", "detail": "BadZipFile: File is not a
zip file"}` in every mode; in `tunnel` mode that is a channel for exception text — paths, SQL
fragments, provider bodies — to reach a stranger. Two fixes: malformed input is now the client
error it always was (400 with `unreadable_package`, naming the exception *class* only, and the
uploaded package is unlinked in a `finally`, so a half-read question bank does not sit in `/tmp`
forever), and an unexpected error in a public deployment answers `internal_error` + the
`request_id` that ties it to the operator's log. In `local` mode the detail is kept, because
there the reader is the operator. Tests: `test_a_corrupt_package_leaves_no_temp_file_and_answers_400`
(asserts the uploaded bytes do not come back in the response).

### P2 — the database could be migrated only by deleting it (`backend/alembic.ini`, `migrations/`)

`alembic` has been a declared dependency from the start, and there was no `alembic.ini` and no
`migrations/` directory: the schema came from `Base.metadata.create_all`, which is correct for an
empty file and does nothing for the only kind of database that matters once a learner has
history. Added: `alembic.ini`, `migrations/env.py` (URL from the app's own `create_db_engine`, so
`ADAPT_DATA_DIR`/`ADAPT_DB_PATH` decide what is migrated and the encrypted types are configured
the same way the app configures them; `render_as_batch=True` because SQLite cannot `ALTER` most
things; a `render_item` hook so `EncryptedJSON(context='audit.detail')` survives generation —
without it `upgrade head` died on `NameError: name 'adapt' is not defined` and `alembic check`
would have reported a type change on every encrypted column), `script.py.mako`, and revision
`0001` generated by autogenerate. Five tests in `test_migrations.py` cover fresh upgrade, model
drift, the `stamp head` path for a database `create_all` already made, `downgrade base`, and
that this is documented where an operator looks.

### P2 — `ADAPT_KEYFILE=` crashed startup with a nonsense error (`config.py`)

An empty environment variable (a systemd `Environment=ADAPT_KEYFILE=`, a compose key left blank)
became `PosixPath('.')`, and the app died inside `with_suffix` before it could do anything:
`ValueError: PosixPath('.') has an empty name`. Blank now means unset for the optional
path/key fields, and `data_dir` refuses to collapse to “the current directory”. Verified on the
live process: `database ready: … driver=sqlcipher file_encrypted=True key=keyfile:…/var/keys/adapt.key`.

### P3 — two rate-limiters, and a status page that could not see the other

`create_app` built a limiter for the middleware and `services.build` built a second one, so
`/api/v1/quotas` reported `active_buckets` for a bucket table that was not refusing requests.
One instance now (`build(..., limiter=app.state.limiter)`), and that unification immediately made
an existing test honest: it had been monkeypatching the *services* limiter and calling the route
level refusal “the middleware's”, which only worked while the two objects were different
(`test_rate_limiting_answers_with_retry_after` now blocks every scope except `ip` and says why).

### Tooling

`npm run lint` ran `next lint`, which Next 16 has removed: the script printed a codemod prompt
and waited for input on a TTY it did not have — a lint that never lints. It now runs
`node ../scripts/check-contract.mjs && tsc --noEmit`, and that script (recreated this pass)
checks four real things: every `/api/...` path the frontend calls exists in the backend's declared
routes, every one has a `?mock=1` fixture row (using `mockLookup`'s own regex semantics, so the
check cannot be satisfied by a string that merely looks similar), route modules export only what
Next's build allows, and browser code contains no hard-coded origin, no non-`NEXT_PUBLIC_`
`process.env` read, and no key-shaped literal. The proxy check accepts the rewrite in
`next.config.mjs` **or** `server.mjs`, because only the custom server can upgrade a websocket.
The checker was proven to fail before it was trusted: a probe added to a page produced exactly

* `GET /api/v1/does/not/exist is not an endpoint the backend declares`
* `GET /api/v1/does/not/exist has no mock row -- ?mock=1 would 404 exactly there`
* `route modules may not export \`__contractProbe\` (next build rejects it; next dev does not)`

## Found, reported, deliberately not changed

1. **`sqlcipher3-binary` is a core dependency** (`backend/pyproject.toml`). Every install path
   works on Linux and the sandbox proves whole-file encryption (`driver=sqlcipher`,
   `file_encrypted: true`); on macOS/Windows the wheel is not available, and the app then falls
   back to column-level AES-GCM while `/api/v1/status` says so honestly. Moving it to an
   `encryption` extra would make installs smoother and weaken the default — a spec decision, so it
   is yours. Related: `ADAPT_DB_ENCRYPTION` decides the on-disk *format*, so a database written
   with `auto` cannot be opened after a switch to `field-only` — the symptom is
   `sqlite3.DatabaseError: file is not a database` on the first PRAGMA. Reproduced here while
   writing the migration tests. A one-line startup sniff of the file header, with a sentence
   naming the policy that wrote it, would turn that into an explanation; not done, since it
   touches the crypto policy.
2. **`requires-python = ">=3.11"` with a comment claiming 3.12.x was verified.** The comment is
   corrected; only 3.11.2 is what this sandbox ran. Note `kokoro` needs `<3.13`, so “3.12+” is not
   a safe blanket claim for the voice stack.
3. **Eleven declared frontend packages that no source file imports** — `@radix-ui/react-*`
   (dialog, label, progress, select, slot, switch, tabs), `class-variance-authority`, `clsx`,
   `tailwind-merge`, `lucide-react`, `react-markdown`. So "styled with shadcn/ui" is not an
   accurate description of this UI: the primitives are in `package.json`, and the components are
   hand-rolled in `src/components/ui.tsx`. Deleting them is a five-line diff, but the blueprint
   asked for shadcn/ui, so removing them is a feature decision — say the word either way.
4. **No browser test runner and no frontend unit tests.** `tsc`, `next build` and the contract
   checker are real gates; nothing clicks a button. `?mock=1` is exercised by its fixture table
   being complete, not by rendering.
5. **Throttle and other anonymous audit rows are not visible to the learner who caused them.**
   `audit_event` for a denied request records `actor_id=None`, and `GET /api/v1/audit` filters
   `actor_id == student.id`, so "you were rate limited" is in the operator's log and not on the
   learner's page. Left alone: widening that filter is a privacy-visible change to what a learner
   can read.
6. **`GET /api` lists every route name to an anonymous caller**, and responses carry
   `X-Elapsed-Ms`. Both are deliberate (a judge's front door, and a latency readout for the demo),
   listed here because they are also information a stranger can use.
7. **Host trust is intentionally loose where a tunnel demands it**: `*.loca.lt`,
   `*.trycloudflare.com`, `*.workers.dev` and `^\d+-[a-z0-9-]+\.e2b\.app$` are accepted as `Host`.
   Cookies are `SameSite=Lax`, so a cross-site page cannot ride them (and a cross-site websocket
   handshake carries no session cookie — verified: an unauthenticated `/ws/voice` connect is
   refused both directly and through the proxy).

## Verified working (executed, not inferred)

* **The consent gate, from the database side.** `approved_adaptations` has exactly one writer;
  `active=True` appears in the seed for `Item`, in `adaptations.approve`, and nowhere else;
  `ai1.py` only ever sets `active=False` on expiry, `sessions.py` deactivates on revoke. The HTTP
  decision path, the CLI path and the demo path each record their channel, and a second answer to
  the same proposal is a 409 rather than a re-application.
* **Mastery and the loop, end to end** on a live server: 12 placement questions moved
  `p_know 0.20 → 0.03/0.62` in the right directions; `analyze` returned
  `provider local · 12 traces · 1 proposals`-shaped output with facts/pattern/hypothesis kept
  separate; `active adaptations: 0 — nothing is applied without your yes`; 13 due reviews;
  16 journey nodes.
* **Telemetry is a schema, not a suggestion:** 21 allowed event types, 48 forbidden payload keys,
  and an unlisted type is refused with a reason
  (`rejected: unknown event type 'keystroke_log'`) rather than truncated and stored.
* **Storage honesty:** startup logs the driver, the key source and how many encrypted values it
  could actually read (`key check ok (5 encrypted value(s) across 2 column(s) read)`), and refuses
  to serve a database it cannot open — with the remedy named and the key never interpolated.
* **Voice plumbing without the models:** the pipeline's VAD/barge-in/turn state machine runs
  against stubs in the suite; a refused handshake answers `ws_handshake_refused` in ~0 ms instead
  of hanging; `/voice/capabilities`, `/voice/speak` (`available: false`,
  `fallback: browser speechSynthesis`) and `/voice/transcribe` all answer.
* **The web app serves and stays contract-checked:** all pages 200, the API proxied through the
  same origin, `tsc` clean, `next build` green, and `npm run lint` green including mock parity.

## What remains unverified

Speech quality/latency (neither `faster-whisper` nor `kokoro` can be installed here), any Gemini
call, the four resource providers against live endpoints, Numbas against a hosted server, real
browser interaction, and `sqlcipher3` on macOS/Windows — all itemised with the exact check to run
in [UNVERIFIED.md](UNVERIFIED.md).

## Buckets

**Verified working** — consent gate incl. provenance; BKT/spacing/retention loop through HTTP;
telemetry allowlist; encrypted columns + startup key check; local fallbacks for both AIs; export →
import round trip (now lossless and idempotent); voice pipeline state machine, VAD, barge-in,
refusal paths; frontend build/typecheck/contract/mock parity.

**Verified broken and fixed here** — dead `auth_fail`/`guest_create` buckets; `answered_by=None`
accepted by the consent gate; CLI/demo forging a learner's answer; no durable record of *who*
answered; lossy Numbas round trip that duplicated the bank and served `{a}` templates;
unrendered payload handed to AI 2 for teach-back; “cap after buffering” on upload and package
import; blocking STT/TTS on the event loop (incl. TTS starving barge-in); 500s leaking exception
text in tunnel mode and a 500 where a 400 belonged; leaked temp files; `ADAPT_KEYFILE=` crash;
two divergent limiter instances; a `lint` script that could not run; a declared-but-absent
migration pipeline.

**Verified broken, unresolved** *(see the follow-up at the end of this file: the first of
these three is now fixed, the second's learner-visible half is fixed, and the third has been
superseded)* — the `ADAPT_DB_ENCRYPTION` policy change makes an existing
database unreadable with an opaque error (recommend a header sniff); throttle denials invisible
to the learner they concern (by design of the audit filter); no frontend test runner, so
interaction bugs are not caught by anything here.

**Not implemented** (declared, not silently faked) — password reset; teacher/class roster; review
notifications; `answer_revealed` telemetry; a shadcn/ui component layer (the packages are
declared, unused); per-learner *rate* budgets beyond the shared buckets; a Numbas *runtime*
(supported subset only, reported as such by `runnable`).

**Unable to verify in this environment** — real model calls (no key, no network); audio (no
whisper/Kokoro install, no microphone, no browser); provider quotas and 429 behaviour against live
APIs; `sqlcipher3-binary` availability off Linux; latency numbers for anything voice; visual
mobile/desktop rendering.

---

## Second pass — the approval gate, the settings screen, the socket (2026-10-12)

The first pass asked "does each element run?". This pass asked the harder question of each fix:
*does it change anything a learner can observe?* Ten defects came back. Six sit in the same gap:
between "the endpoint answered" and "the app did something with it". One is the app answering for a
server that refused to answer at all. The last three are the loop's own closing move -- measuring an
approved change, saying so in words, and the screens that had been showing a payload instead.

| # | Defect | Root cause | Fix | Guard |
|---|---|---|---|---|
| F5 | AI 1 proposed `difficulty_recalibration` with `{"target": 0.78}` — the selector's own default, so approving it changed nothing. The loop asked the learner to ratify the status quo. | the payload came from `strategy.payload_defaults` with no reference to the evidence that raised the pattern | `recalibrated_target()` derives the number from the detector's `facts` (`too_hard` → 0.85, `too_easy` → 0.70, otherwise compared against the observed rate, clamped 0.60–0.92) and **returns the current target unchanged when the evidence says nothing**; read from `EvidenceRecord.facts` via `draft.evidence_ids` | `test_learning_math.py::test_recalibrating_the_difficulty_target_moves_it_the_right_way`, `test_api_learner_loop.py::test_a_proposal_must_propose_something` |
| F6 | The settings screen rendered `avoid` and `encourage` as tick boxes. Ticking one stored the phrase `"True"` in the learner's profile, which AI 1 then quotes back as their own request. | the page's `BOOL` set contained two keys the server types as `list` | those two keys became text boxes that split into phrases on save and join on load; the server now **refuses** a bool or an empty value for a list preference instead of coercing it | `test_api_learner_loop.py::test_a_preference_is_stored_as_the_thing_it_describes` (fails without the guard), plus a UI check that types two phrases and reads them back after a reload |
| F7 | A pending proposal reached the learner with no label, no "what it does", and no citations — while the same proposal seen through `analyze` or the dashboard had them. The learner judged an experiment without being told it was one. | `services/adaptations.py` called `proposal_dict(row)` without `strategy=`, and those three fields are only filled from the catalog entry | the pending list, the history list and both dashboard views resolve the strategy | `test_api_learner_loop.py::test_a_pending_proposal_arrives_with_its_source` (fails without the fix) |
| F8 | Nothing. Recorded to close it: the `channel` field in the *audit* entry is the transport, not the caller's word — the route stamps `web`; the `channel` in the *decision record* keeps what the caller said. Both are true statements about different things. | — | — | checked at both layers, unchanged |
| F9 | `voice/page.tsx` has a branch for close code 4401 that says "sign in again". Browsers never see it: a close sent before `accept()` answers the handshake with HTTP 403, and the page reports a generic transport error. The existing test passed because a protocol-level client does see the code. | refusing a WebSocket and *telling the client why* are two different operations | accept, send one `{"t":"error","where":"auth",…}` frame, then close with 4401 — no data is exchanged and no session is created in between | `test_api_learner_loop.py::test_websocket_refuses_anonymous_sockets_in_a_way_a_browser_can_read` (fails on the old code), and re-checked with a raw `websockets` client, which now receives the frame |
| F10 | `decision: modify` returned a receipt that said only "modified": which of the learner's numbers were applied, which were refused, and what they typed as a reason were all missing from the reply (they were stored correctly). | the reply builder wrote `note` as *its own* explanatory sentence and never echoed `modifications`/`student_note` | the modify branch adds both, matching the key names `proposal_dict` already uses | `test_api_learner_loop.py::test_a_modified_approval_returns_what_the_learner_changed` (fails without it) |

| F11 | (harness, not the app) numeric-answer checks flickered in a *full* probe run and passed in isolation. | jsdom + React: `Event("input")` is not what React listens for, so the value reached the DOM but not React's state; a later render wrote the old state back and the box looked as if the app had erased the learner's typing | the shim dispatches `InputEvent`; and `fillAnswer` now distinguishes `state-reset` (the element is still mounted, so the app really did clear it) from `remounted` (the question was torn down mid-typing), so the next time it flickers the report names the mechanism | the whole suite runs clean at 124/124 with the detector left armed |
| F12 | **A throttled read was reported as a sign-out.** With the per-IP bucket spent (which a busy local run does in seconds), `GET /api/v1/me` answers 429 and the app concluded `me = null`: the changes screen said "nobody is signed in, so there is nothing to approve", the header offered "sign in", and a learner half-way through a placement would have believed their session was gone. | `whoAmI()` and `SessionGate` both collapse "the server refused to answer" into "the server said nobody" — a missing `data` and a refusal are the same value, `null` | the gate now tracks the third state (`unknown`), keeps the last known learner on screen, backs off and retries on its own (bounded, four tries), and only calls a visitor signed out when the answer was 401/403; `NeedSetup` says "still checking who you are — nothing here has been lost" with a retry the learner can press; `Err` explains a 429 as throttling rather than as a sign-in problem; `/proposals` no longer claims a sign-out it was not told about | three checks in the `failures` scenario: the header offers a retry instead of "sign in", the changes screen refuses to announce a sign-out, and the back-off finds the session again without being asked. All three **fail on the pre-fix build** (proven by stashing the files and rebuilding) |
| F13 | Two different 429 bodies. The per-scope refusal nests `{error, retry_after_s, scope}` under `detail`; the middleware's per-IP refusal answered `{"detail": "rate limit exceeded", "retry_after_s": …}` — so a client written to back off politely from one missed the number in the other and retried immediately, which is what a flood looks like to a limiter. | two raise sites, one contract, never compared | the per-IP refusal now carries the structured detail too (keeping the top-level key and a human sentence, so nothing that read the old shape breaks) | `test_api_learner_loop.py::test_the_per_ip_refusal_answers_with_the_same_shape_as_every_other_429` — fails on the old body, passes on the new |

| F14 | The effect of an approved change was measured, stored -- and never *said*. `journey.py` looked for `outcome["verdict_words"]`, which nothing wrote, so the timeline's "what happened" entry carried an empty sentence; `POST /adaptations/evaluate` returned only numbers. | the statistician's sentence (`Effect.explanation`, CIs and Hedges g) was written into `outcome["effect"]` while every learner-facing reader looked one level up | `describe_effect()` composes the plain-words finding for all four verdicts -- including "not enough scored answers yet", which is a result rather than a silence -- and it is stored with the measurement, so the evaluate reply, the live adaptation, the history row and the timeline all say the same thing | `test_an_approved_change_is_measured_and_the_learner_is_told_in_words` (fails with `ai1.py` reverted) |
| F15 | Learner-facing screens rendered structured findings with `JSON.stringify`: the practice and changes screens showed a measured effect as `{"effect":{"baseline_n":14,…}}`, the decision history did the same, and **the privacy screen showed the storage statement as raw JSON** (`{"requested":"auto","file_encrypted":true,"driver":"sqlcipher",…}`). | eight sites stringified whatever the endpoint returned, because the endpoint returns structure and nobody had decided how to say it | `humanize()` in `ui.tsx`: a sentence if the payload has one, otherwise labelled pairs -- no quotes, no braces, numbers to two decimals -- used by the practice row, the applied change, the history line, the alternative-hypotheses list, the settings LLM hint and profile values, the privacy storage block and the voice capability line | 15 tripwires in the probe: every /learn, /proposals, /settings, /voice, /progress page is asserted free of a raw JSON object at close time. Proven non-vacuous by putting one dump back: **it failed on the privacy screen and quoted the blob a learner would have read** |
| F16 | The dashboard kept saying "0 outcomes measured" *after* a change had been evaluated, because it copied `measured_outcomes` out of the last profile snapshot; and it counted switched-off adaptations as applied, disagreeing with the list under its own heading. | two row-counts were treated as profile inferences | the dashboard counts them live (`status = evaluated`, `active IS TRUE`), falling back to the snapshot for anything else that key carries | same measurement test (fails with `dashboard.py` reverted), and the probe's practice/changes screens see the same numbers a learner does |

**Two things that looked like defects and were not.** `GET /api/v1/status` requires a session, so
the header chip used to read "no at-rest encryption" on the signed-out landing page — an
assertion about a database it had not been allowed to describe; the chip now has four states
(`checking the api…` / up / down / unreachable, and "not reported" for the parts a visitor cannot
read). And an accepted adaptation appeared to be missing its start time and its
`waiting_for_effect` sibling: both exist, the keys are `started_at` and a top-level list — my
probe had guessed the shape, which is the same mistake the first pass warned about.

**Harness, in the open.** `frontend/scripts-ui-probe.mjs` drives the *built* pages in jsdom
because no browser can be installed here. Three of its FAILs this pass were its own bugs, each
fixed rather than worked around: a wait anchored on text that is on the page before the data
arrives; a shared cookie jar that left the last manufactured learner signed in for the scenarios
after it; and a text-input shim that produced `Event("input")` where React needs `InputEvent` —
that last one is why the numeric-answer checks flickered only under load. Its `fillAnswer` now
reports *wiped* (React overwrote what was typed, an app fault) separately from *still locked*
(the event never landed, a harness fault); the wiped case never fired after the shim was fixed, and
the detector stays armed. Its 19 page closes now run one shared assertion (F15) rather than each
scenario re-inventing one.

---

## Follow-up — the three unresolved items, re-checked (2026-10-12, later pass)

Each was reproduced (or failed to reproduce) against the merged tree, not re-read.

### The encryption-policy mismatch — **fixed**

Reproduced both directions before touching anything:

| File on disk | `ADAPT_DB_ENCRYPTION` | Before | Now |
| --- | --- | --- | --- |
| SQLCipher ciphertext | `field-only` or `off` | `sqlite3.DatabaseError: file is not a database` | refused before the engine is built, naming the file, the policy, and the two ways out |
| plain SQLite | `auto` or `sqlcipher` | `sqlcipher3.dbapi2.DatabaseError: file is not a database` | same, in the other direction |

The fix is the header sniff this document recommended, and it does **not** touch the crypto policy:
no key, mode, format or pragma changes — `create_db_engine` reads the file's first 16 bytes and
refuses a mismatch with a sentence instead of letting the driver raise from inside SQLAlchemy's
pool. The API's startup failure now ends with that sentence rather than a stack trace.

Two guards, both mutation-checked (fix removed → these fail → fix restored → they pass):

* `test_crypto_and_encryption.py::test_opening_an_encrypted_database_under_field_only_explains_itself`
  and `::test_a_policy_that_cannot_read_the_file_says_so_instead_of_the_driver` (2 failures without
  the sniff), plus a driver-free test of the sniff itself.
* `doctor` had the same blind spot one level up: it reported `[ok] data dir` for a directory whose
  database it cannot open. It now opens what is there and reports `[ok] database: adapt.db: open,
  N learner(s), driver=sqlcipher` or the refusal sentence with a remedy —
  `::test_doctor_reports_a_database_the_policy_cannot_open`, which fails when that check is removed.

The related note in "Found, reported, deliberately not changed" (item 1) is answered by the same
change; the *installation* half of that item (`sqlcipher3-binary` as a core dependency, macOS and
Windows wheels) is untouched and remains a spec decision.

### Throttle denials invisible to the learner — **deliberate, and the visible half is fixed**

`GET /api/v1/audit` still filters `actor_id == student.id` (line 254), so an anonymous throttle row
is the operator's and not the learner's. That is the privacy choice the audit named, and it stays.
What changed since (F12) is the part a learner can actually feel: a 429 now reads "the server is
asking for a moment between requests… nothing is wrong with your session" instead of being
mistaken for a sign-out, and the gate retries instead of blanking the screen.

### "No frontend test runner" — **superseded**

`frontend/scripts-ui-probe.mjs` (jsdom against the built pages, `npm run probe`) now exists and runs
208 checks across eleven scenarios, including the approval-gate phases and the lesson engine. It
still cannot see layout, focus order, `getUserMedia` prompts or paint — that limitation is recorded
in `docs/UNVERIFIED.md`, not here.

---

## Third pass — the fifteen pitch defects (2026-10-12)

Fifteen defects, fixed against the user's own specification of the fix. The table is the map:
what was wrong, where the fix lives, and what fails if the fix is removed. Every test named here
runs in the 645-test suite; the UI claims are checked by `scripts/probe-fresh.sh all` (208 checks after the
visual-redesign pass below; 199 at the time this table was written).

| # | Defect | Root cause | Fix (where) | Guard |
|---|---|---|---|---|
| 1 | Modes authored into items | how a concept *could* be taught was a property of the item — `experience_mode` and `kind` were written into the bank (`bank.visual()` returns `kind: "mcq" if choices else "text"`), so an item could only ever be taught the one way it was authored, and "which method works" was unanswerable | `learning/methods.py` + migration `0005`: the method is chosen at **serve time**, seeded per `(learner, concept, exposure)`, and the served row keeps `method`, `arm`, `design`, `expected_success` — so the same item teaches two people differently | `test_smoke_new_endpoints.py::test_an_attempt_remembers_the_method_it_was_served_under`, `test_the_loop_closes.py::test_the_worked_example_comparison_has_a_design_and_is_not_self_selection` |
| 2 | Method confounded with format | The visual mode was authored as MCQ (`bank.visual()`, above) and the other modes were typed: a "method effect" was also a format effect | `methods.question_format` / `matched_question`: the reteach question is chosen **same-format**, and the format is carried in the comparison (`format_held_fixed`) | `test_the_loop_closes.py::test_the_reteach_teaches_a_different_method_and_asks_the_same_format` |
| 3 | Raw accuracy compared across topics | accuracy is mostly the difficulty of the topic | actual **minus expected** per method, accumulated across sessions (`method_report`, `GET /methods/report`, and the `method_effect` detector) | `test_detectors.py::test_the_method_comparison_is_actual_against_expected_not_raw_accuracy` (two methods, identical accuracy, opposite readings) and `::test_a_method_that_beats_expectation_can_become_a_proposal`, `test_smoke_new_endpoints.py::test_method_report_shows_n_and_labels_early_signals` |
| 4 | Worked-example check read a click | "opened an example" is chosen by the stuck learner: self-selection scored as a treatment | seeded arms (`example_first` / `explanation_first`) in `methods.assign_method`; the click-based comparison still reported but marked `observational` and scaled down | `test_detectors.py::test_a_half_assigned_comparison_is_not_called_randomised` (and the arm rotation in `test_the_loop_closes.py`) |
| 5 | No live fail → diagnose → reteach → compare loop | the pieces existed and were never chained | `learning/reteach.py` (`prerequisite_check` → `fatigue_check` → different method → matched question → compare) wired into `services/attempts.submit`, with `blame` in the receipt | `test_the_loop_closes.py` (five tests, including "a missing prerequisite is not converted into a teaching failure") |
| 6 | Visual strategy was not wired | approving it changed nothing, and only four hand-drawn diagrams existed | `learning/visuals.py`: four generic templates (flow, comparison table, timeline, labelled diagram) built from any source text, and the approval switches the learner onto them (`concept_versions_for`) | `test_subject_agnostic_content.py::test_all_four_visual_templates_can_be_filled_for_any_subject` and `::test_approving_the_visual_strategy_changes_what_is_served` |
| 7 | "Segmented text" was truncation at N words | the key sentence could be dropped by a word count | `learning/segments.py` splits at sentence boundaries into short steps with a check question between them, and the next step stays locked until the check is answered | probe `learn` scenario ("the next step is behind the step check, and the check explains itself" — which asserts a vague answer *stays* locked and that the check "is not marked and is not sent anywhere") |
| 8 | One paragraph per topic, maths only | the catalogue was a maths bank | `learning/content.py`: three versions per concept (text, visual, worked example) plus a matched question pair, generated through the existing rulebook (checked keys, content-hash identity); arithmetic checking is replaced by **quotability from the source** for prose subjects, and unverified items are flagged. The shipped bank then gained two prose concepts (`history_ww1`, `bio_photosynthesis`: notes + a typed question keyed to a word in the notes, a choice question, a teach-back and a worked example), so the claim is checkable in the running app and not only against a fixture | `test_subject_agnostic_content.py` (biology and history fixtures) and `test_shipped_catalogue_has_prose_subjects.py` (the shipped topics, graded through the real path) |
| 9 | Teach-back looked keyword-based | required words + synonyms, text treated as a bag | `assessment/teachback.py`: a fixed three-point checklist with strict output, learner text handled as data, and everything scored on coverage of ideas rather than words | `test_assessment.py::test_teachback_rubric_needs_both_content_and_substance`, `test_api_learner_loop.py::test_teachback_is_scored_on_content_and_says_what_was_missing`, plus the strict-output contracts in `test_llm_plumbing.py` |
| 10 | Mastery overconfident | one wrong answer took 20% → 3%, on default parameters | `bkt`: per-format guess rates (MCQ 1/options ≈ 25%, typed 2%), softened slip (0.15), and `update_capped` bounding any single answer to 0.12 with the arithmetic recorded | `test_the_loop_closes.py::test_one_wrong_answer_cannot_move_mastery_far…` |
| 11 | Twelve unaided questions did two jobs | placement and method comparison on one run | split: placement stays adaptive (`information_gain`), the method probe is its own 6–8 concept run (`selector.method_probe_plan`, `GET /assessment/method-probe`) | `test_smoke_new_endpoints.py::test_placement_is_adaptive_and_not_a_method_probe` and `::test_method_probe_is_clamped_and_says_how_it_is_judged` |
| 12 | Thresholds fired on 3–4 observations | no sample-size vocabulary | `methods.evidence_label` (`early_signal` < 8, `signal`, `established`) propagated to the API rows and the screens, which always print `n` | `test_smoke_new_endpoints.py::test_method_report_shows_n_and_labels_early_signals`, plus probe checks reading `n=` and "early signal" |
| 13 | Effect checks needed 5 per group | a live demo cannot produce them honestly | `effect.MIN_GROUP_N` refused to conclude below it; `adapt pilot --student-id <id> --seed` writes real rows beforehand, flagged `pilot`, and every surface that shows a number derived from them also shows *"pilot data — not evidence of efficacy"* | `test_pilot_labelling.py`; `demo.mjs` prints the label next to the method rows |
| 14 | Never run in a browser; a selected answer was sometimes lost; Gemini never called | jsdom was the only "browser"; the answer lived in one component's own `useState`; no key in the sandbox | `frontend/src/lib/drafts.ts` holds the in-progress answer per item (round trip and reload survive; `Back` keeps the mark); the UI probe walks every screen; `POST /explain` with no key answers from the local tutor and *says so* (`provider: local`, `fell_back_to`) | probe `diagnose` (24/24, and **2 of 24 fail with the draft store disabled** — the non-vacuousness check in `docs/UNVERIFIED.md`); `test_llm_plumbing.py` |
| 15 | Too much to explain | the pitch was the plumbing | `docs/DEMO.md` + the README's "The demo, in the order to say it": the loop in five steps, one privacy line, and an explicit "do not demo" list (voice, Numbas export, encryption internals) | `docs/DEMO.md` rehearsal block; `scripts/demo.mjs` prints the same five sections |

### Found while writing the guards

Nine defects surfaced from the tests rather than from the list, which is the point of writing them
this way — the first three while building the guards, the next three while making the guards run
against a catalogue that is not only mathematics, one from a test that walks the whole chain instead
of one function, and the last two from re-running every gate before opening the pull request: a
public helper that could not be called at all, and the harness itself, which turned out not to be
able to answer its own bank correctly.

* **A detector that never ran.** `patterns.method_comparison` — the one comparison the whole method
  story rests on — took a bare row list while `run_all` passes the shared `EvidenceContext`. It
  raised `TypeError` on **every** analysis; `run_all` swallowed it by design; the catalogue
  advertised the detector and the log said `detector method_comparison failed`. Fixed, and
  `test_detectors.py::test_every_detector_accepts_the_context` now calls every registered detector
  with a real context and no safety net, so a signature cannot drift again.
* **A mixed comparison labelled "randomised".** With one arm assigned and the other falling back to
  learners' own clicks, the worked-example detector still called itself randomised. Both arms now
  have to be assigned *and* big enough, and the fallback says "what the learner chose to do".
* **A finding that was computed and then dropped.** Fixing the signature only got the comparison
  running: `hypotheses.TEMPLATES` had no `method_effect` key, so `draft()` returned `None` and the
  measurement never became a hypothesis or a change a learner could approve. The template now takes
  its strategy *from the observation* — which method won is the finding — and the detector attaches
  one only when the method **beat** what its own questions predicted. A method that fell short stays
  on the record as evidence with the reason beside it (`why_no_proposal`), because "do more of the
  thing that is underperforming" is not a change worth asking for. On the same path,
  `ai1.update_hypotheses` rebuilt each observation from its stored dict without `direction`,
  `design` or `evidence`, so every replayed finding came back as "up / observational / early_signal"
  — the opposite of what a randomised, above-the-bar comparison is.
  (`test_detectors.py::test_a_method_that_beats_expectation_can_become_a_proposal`,
  `::test_a_method_that_fell_short_is_recorded_but_not_proposed`,
  `::test_a_replayed_finding_arrives_with_its_design_and_evidence`.)
* **A second reader of the same measurement.** `dashboard.overview` handed out the stored payload
  nested while `/adaptations/active` handed out the flat one, so the screen that summarises
  everything showed a measured change with no numbers attached. Both now flatten through
  `read_effect` and emit the same four field names the screens and `lib/types.ts` declare
  (pinned in `test_an_approved_change_is_measured_and_the_learner_is_told_in_words`).
* **A test that passed by luck.** `test_approving_the_visual_strategy_changes_what_is_served`
  required *every* served concept to be drawn from material. The test database is shared for the
  whole session, another test creates a topic with none (`gen_contract`), and which concepts a
  learner is served depends on their generated id — so it failed in some full-suite runs and passed
  in others, and adding two topics to the bank is what surfaced it. The honest payload
  (`no_material`: "no course material is linked to this concept yet") is now allowed and named, and
  the test still fails if nothing at all was drawn from material.
* **A pilot number that lost its label on the way to a proposal.** Once the comparison could
  actually produce a hypothesis, the end-to-end test seeded nine pilot rows, read the proposal the
  app files, and found the sentence quoting them with no label: the detector handed `method_report`
  its own row dicts and left out `pilot`, so `n_pilot` counted 0 and the label never travelled.
  Fixed by passing the flag — the row already carried it — and pinned by
  `test_detectors.py::test_a_method_that_keeps_beating_expectation_reaches_the_learner_as_a_change`,
  which asserts the label is *in the sentence the learner is asked to approve*.
* **A measured change rendered as zeroes.** `active()` handed screens the stored payload nested
  (`{"effect": {...}}`) while the component's type was flat (`EffectSummary`), so a judged change
  read "0 answers before and 0 after". One writer and one reader now share the shape
  (`effect_payload` / `read_effect`), the flat form is what screens get, the raw form stays as
  `effect_raw` for the audit, and a window that is still filling up is **stored** with its count and
  sentence (`gathering_evidence`) instead of vanishing — while `evaluation_status` stays empty,
  because an unfinished measurement is not a verdict.
* **A public helper that could not be called.** `adapt.db.session_scope` is exported in
  `adapt.db.__all__` and did `yield from get_db().session()` — but `Database.session` is *already* a
  context manager, so the first call raised `TypeError: '_GeneratorContextManager' object is not
  iterable`. Every service holds its own session factory, so nothing inside the package ever called
  it, and the whole suite passed with a public function that could not run. It now enters the scope
  it delegates to, and `test_db_session_scope.py` calls it for real: reads through it, checks a
  failure inside it still reaches the caller, and pins that it stays exported. The next caller of an
  `__all__` name is a person rather than a test, which is why this one is guarded instead of deleted.
* **The harness could not answer its own bank.** `conftest.answer_for` builds every answer the
  suite submits — the public item payload carries no answer key, so a test that wanted to be right
  would otherwise have to guess. Two of its shapes were wrong, and one was wrong for every subject.
  A *wrong* numeric answer was `value * 1.5 + 1`, which returns `value` itself when `value == -2`,
  and `a - b - c` renders to -2 for some learners — so the app marked that answer correctly and the
  test whose whole point is "different answers, different sentence" compared one string with itself,
  failing for about one learner id in fourteen while nothing in the product was broken. A *correct*
  answer was `f"{value:g}"`: six significant digits, which an item marked to 1e-09 rejects whenever
  its answer is 34/3, so tests measuring what a correct answer does were measuring a wrong one. And
  the teach-back branch looked for `rubric["ideas"]` — a key no item in the bank has — falling back
  to one fractions sentence for every subject, which made the "correct" explanation of why the First
  World War began the words "you have to make the parts the same size first". Wrong answers are now
  built outside the item's own tolerance, correct ones in the exact form the item accepts (short
  decimal, then fraction, then full precision), and explanations from the grader's own
  `checklist_from_rubric`. `test_wrong_answers_are_actually_wrong.py` walks every markable item
  under ten learner ids and grades both answers, so the harness is checked against the bank instead
  of trusted. The same rounding was visible on the learner's side of the glass: a numeric mark
  decided below the printed digits reported `expected 11.3333, got 11.3333` — two identical strings
  either side of the mark — and now names the form the item accepts (`34/3`), while a correct answer
  is never told it was rounded.

## Fourth pass — the visual redesign (2026-10-13)

The redesign brief at the repository root (`UI file.txt`) implemented against a tree whose
gates were measured first, not assumed. Two things were wrong before any pixels moved.

**A gate on `main` could never pass.** `scripts/journey_check.py` asserted `answered >= 10`,
a threshold written for the old fixed twelve-item diagnostic; PR #7 split placement from the
method probe and the default sequence became eight, so the check reported `FAIL answers are
accepted and recorded -> 8/8 accepted` — a failure whose own detail text reads like a pass.
The assertion now says what it was always for (`answered == len(served) and answered >= 5`:
every item served was accepted, and the sequence is long enough to place somebody). The
diagnostic was not lengthened and the threshold was not tuned to the observed value. Fixed in
its own commit (`f8987a1`) before the redesign started; 59/59 passes.

**The corner index tab is cancelled, and it took two probe assertions with it.** Navigation is
now the permanent sidebar (brief 2.3). The two checks that read `button.index-tab` for
`§ 05 · Progress` and `§ 04 · Review` were rewritten, not deleted: the marked item must be this
section *and* the sidebar must offer every other one in plain words. Same count on the screen
that had two, one on the screen that had one, and a stronger claim in both places.

| Command | Result after this pass |
|---|---|
| `cd frontend && npm run build` | exit 0 — 11 routes, shared 103 kB |
| `bash scripts/probe-fresh.sh all` | **208/208 checks, 224 API calls**, exit 0 (was 199/199) |
| `.venv/bin/python scripts/journey_check.py` | **59/59**, exit 0 (was 58/59 and failing) |
| `cd frontend && npm run lint` | contract ok — 74 backend routes / 54 frontend calls, 4 scene kinds agree |
| `cd frontend && npm run test:engine / test:lib / test:server` | 15/15, 8/8, 2/2 |

What changed in the interface: the button contract (`.btn-primary` / `.btn-secondary` /
`.btn-ghost`, plus `.option-card` with a real selection indicator and `.check-row` for
consent); the permanent sidebar with a drawer below 860px; the consent step in front of every
account-creating call; and home rebuilt as status cards from `dashboard` / `review/due` /
`adaptations/pending` with one primary action — the word-field navigation it used to carry is
gone, because a second navigation surface is how the app's own author lost Settings.

**The consent gap this pass does not close, stated plainly.** `Student.consent_analytics`
defaults to `True` (`db/models.py`), and `OnboardingBody` defaults both consents to `True`
(`api/routes/auth.py`), so a learner who is never asked is still recorded as having agreed.
The screen cannot fix that: it can refuse to create an account without the required consent,
and it can carry the answer to `/onboarding` (the first screen with a session to store it on),
but "no decision" and "yes" are the same value in the column. Closing it needs a tri-state
field or a migration that backfills un-asked learners as unknown — and deliberately *not* a
flip of the default to `False`, which would silently stop the event log for every existing
learner and take the proposals with it.

---

## Fifth pass — cleanup round and bug scan (2026-10-16)

Branch `arena/ce396360-adapt`, from `6ff799c`. The brief was to cut the documentation down,
keep an audit of the changes and a register of the bugs, and scan for the defects behind "the
app is slow and buggy and a bit of UI mismatch".

**The defect register is [`BUGS.md`](BUGS.md)** — nine entries, each with the command or
request that proved it, the fix, and where it is tested. This section is the change inventory,
including everything deleted.

### Commands run, with results

| Command | Before | After |
| --- | --- | --- |
| `.venv/bin/python -m pytest backend/tests -q` | 1337 passed, 2 skipped (130 s) | **1341 passed, 2 skipped** (128 s) |
| `cd frontend && npm run lint` | exit 0 — 117 routes / 103 calls | exit 0 — 117 routes / 103 calls |
| `cd frontend && npm run test:lib` / `test:engine` | 42 / 15 pass | 42 / 15 pass |
| `cd frontend && npm run build` | exit 0, 19 static pages | exit 0, 19 static pages |
| `npx tsc --noEmit` | exit 0 | exit 0 |
| `ruff check backend/src scripts --select F401,F841,F811,F821` | 10 findings | **0** |
| repo-wide dangling-doc-path scan | 20 unresolved | **0** |
| `python -m compileall backend/src scripts` | exit 0 | exit 0 |

Four tests were added and one was rewritten, which is the whole of the +4. The suite was green
before this pass and is green after it; **none of the eight defects was visible to it.** That is
the finding worth carrying forward: the checks this repo has are good at proving what they were
written to prove, and the P1 in `BUGS.md` survived 1337 of them because the fixture every test
uses carries the one header the browser does not send.

### Documentation removed

68 tracked `.md` files became 14, and `docs/` went from **36 MB to 180 KB** — 97% of the
documentation bytes were 129 PNG screenshots.

| Removed | Files | Why |
| --- | --- | --- |
| `docs/screens/` | 129 PNG + 2 README (**35 MB**) | Round-2 browser captures. Referenced only by the round-2 reports removed below, so removing those orphaned these. Largest single win in the repo. |
| `docs/round2/` | 22 | Per-agent reports, handoffs and unverified notes for a round that is merged and closed. Their durable content already lives in `AUDIT.md` and `UNVERIFIED.md`. |
| `docs/handoff/` | 7 | Handoff briefs between agents 1–6. Process scaffolding, not documentation of the product. |
| `agent-notes/` | 8 | Same, one level up: per-agent working notes. |
| `docs/round3/` | 4 | Tracks R/S/T/U of a closed round. |
| `docs/superseded/` | 1 README + **2 `.tsx`** | `main-baseline-ui/runId-page.tsx` and `snapshot-page.tsx` — superseded *source files* filed under `docs/`, where no build, lint or type check reaches them. Dead code in a documentation directory. |
| `docs/AGENT_BRIEFS.md`, `FUNCTIONALITY_ROUND.md`, `SCAN_REPORT.md`, `REVIEW.md`, `REDESIGN_REPORT.md`, `SWEEP.md`, `UI_MAP.md`, `ROUND2.md`, `ROUND3.md` | 9 (~250 KB) | Overlapping narratives of past rounds. Five separate files (`SCAN_REPORT`, `SWEEP`, `REVIEW`, `REDESIGN_REPORT`, `FUNCTIONALITY_ROUND`) each claimed to be *the* account of what was found and fixed; `AUDIT.md` is that account. |
| `ADAPT_R2_ALL_PHASES.md`, `ADAPT_functionality_round_prompt.md`, `UI file.txt` | 3 (~130 KB) | Prompt briefs committed at the repo root — instructions *to* agents, not documentation *of* the app. `UI file.txt` was already recorded as "repo cruft" by the integration pass that chose not to delete another agent's files; this pass is not another agent's. |

**Kept, and the reason each survived:**

| Kept | Why it is not removable |
| --- | --- |
| `README.md` | `test_migrations.py:370` asserts "alembic" appears in README+docs; `test_hosting.py:371` asserts README exists. |
| `docs/EFFICIENCY.md` | **Hard-asserted by content**: `test_ai_efficiency.py:502-509` fails if the file is missing *or* if any name in `SC.names()`, any task in `RT.TASKS`, or the static-head count is absent from it. It is a table the code is checked against, not prose. |
| `QUALITY_REPORT.md`, `MODEL_QUALITY_REPORT.md` | **Generated outputs**, not documents: `scripts/teaching_quality_eval.py:810` and `scripts/live_model_check.py:75` write them. Deleting them would just make the next run recreate them. |
| `docs/UNVERIFIED.md` | The live register of what has not been verified; cited by `tests-e2e/placement.mjs`, `tests-e2e/journey.mjs`, `test_hosting.py`. Now also the target the round-2 `unverified-*` pointers were repointed to. |
| `docs/AUDIT.md`, `docs/BUGS.md` | The two files this brief asked to keep: the change trail and the defect register. |
| `docs/VOICE.md`, `EVIDENCE.md`, `ENGINE.md`, `DEMO.md`, `ACCESSIBILITY.md`, `PILOT.md` | Each is referenced from source or config (`pyproject.toml`, `services/evidence.py`) or from README, and each documents something still true about the running system rather than about a past round. `PILOT.md` in particular is operational: install, deployment mode, key backup, and the warning that encrypted columns are unrecoverable without `keys/`. |
| `adapt-web-application-prompts.zip` | The provenance of the six master prompts; `ai/prompts.py` and `test_teachback_master_prompt.py` both cite it as the source of the verbatim prompt text. A user-committed source artifact, not a report. |
| `frontend/README.md`, `e2e/games/README.md` | Local to the trees they document. |

### References repaired

Removing files is only half the job; 13 references pointed at what was removed and 7 more
pointed at files that **never existed** — including one shown to learners. All 20 are listed in
`BUGS.md` §P3 with their old and new targets, and the repo-wide scan now reports zero
unresolved paths.

Two deserve repeating here because they were wrong before this round touched anything:

* `ui.tsx:279` told anyone running ADAPT themselves that `docs/OPERATIONS.md` has the start-up
  command. It does not exist and never did. That message renders in the `status === 0` branch of
  `Err` — i.e. precisely when the backend is down and someone needs the start-up command.
* `numbas.py:21` told the reader that `python scripts/verify_numbas.py` is the check. There is
  no such script. Rather than repoint it at something that is not equivalent, the docstring now
  says the check needs a real Numbas install, that no such script is in the repository, and that
  the part-type table is therefore unverified — which is what `docs/UNVERIFIED.md` already
  recorded.

### Code changed

34 files changed. Everything is in `BUGS.md` with its evidence; the summary is one line each:

| Change | Files |
| --- | --- |
| CSRF limited to mutating methods (P1) | `api/deps.py` |
| `/lesson/concepts` now reports authored concepts, agreeing with `/content/breakdown` (P2) | `api/routes/lesson.py` |
| Two regression tests for that agreement, plus its control | `tests/test_content_engine.py` |
| Anonymity test rewritten to actually clear the cookie jar (P1) | `tests/test_content_engine.py` |
| Two regression tests added for the CSRF contract | `tests/test_api_learner_loop.py` |
| Status chip waterfall collapsed to `Promise.all` (P2) | `components/chrome.tsx` |
| Corner radius normalised to the 2px contract, circles preserved (P2) | 14 files under `frontend/src` |
| Learner-facing dead doc pointer repointed (P3) | `components/ui.tsx` |
| Dead code and unused imports removed (P3) | `baseline/factors.py`, `baseline/service.py`, `cli.py`, `content/pack.py`, `content/pipeline.py` |
| Identical-branch ternary collapsed (P3) | `components/scenes/stages.tsx` |
| Doc references repaired (P3) | 14 source/test/doc files |

`components/ui.tsx` and `lib/api.ts` also had comments corrected that described the CSRF bug as
if it were the intended contract. Those comments were accurate — which is exactly why the bug
lasted: `first-lesson/page.tsx` had been written to work around it, with a note explaining the
workaround, so it read as a quirk of the framework rather than a defect.

### Not changed, and why

**The opening splash still blocks `/` for ~4.7 s** (`components/opening.tsx`, 4200 ms plus a
500 ms exit). It is the largest perceived-latency item in the app and the first thing anyone
investigating "slow" will meet. It is deliberate product behaviour, correctly built — once per
session, skippable three ways, `prefers-reduced-motion` drops it to 900 ms, blocked storage
cannot trap anyone, and the shell's requests mount underneath it so the wait is not wasted — and
the file carries an explicit "do not reorder or reword without explicit approval" constraint.
Shortening it is the owner's decision. `BUGS.md` §F1 names the exact line and the two options.

**The `consent_analytics` default** flagged by the fourth pass is still open and is not
re-litigated here: `"no decision"` and `"yes"` remain the same value in the column.

**Ruff's remaining ~3400 findings** are style (3258 of them `E501`), plus `B008` and `ARG001`
which are FastAPI idioms the rules do not model. The 12 `B905` `zip()` sites and the 13 `BLE001`
blind-except sites were traced individually and are recorded in `BUGS.md` §"Reviewed and judged
not bugs" with the reason each was closed — including the teach-back `zip`, which would have
silently altered a learner's score denominator had its length invariant not been enforced
upstream at `_validated_points:493`.
