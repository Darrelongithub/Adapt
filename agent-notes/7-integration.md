# Integration note 1 - the landed agent PRs, and the §3 boundary

Written by the integrator session (the one that owns `arena/06d446fc-adapt`). This is the record of
merging the finished agents' PRs and making the merged tree green against brief §3. It is a working
note like the others in `agent-notes/`; the phase-level report is in `docs/FUNCTIONALITY_ROUND.md`.

## Second wave: phase 5, from `main`

`main` (`a1edc69`) is a **single-commit snapshot with no common ancestor** to this branch - it is not
mergeable with `git merge`, so the reconciliation is a content merge. What it carries that this
branch did not:

| Path | What | Decision |
|---|---|---|
| `ai/adapter.py` (1 280 lines), `lesson/{__init__,templates}.py`, `api/routes/lesson_plan.py`, `tests/test_adapter.py` (594 lines), `agent-notes/4-adaptation.md` | phase 5, the adaptation layer (agent 4) | **ported in**: 83/83 tests pass here once the router is registered |
| `frontend/src/components/visual/{index.tsx,validate.ts}`, `app/contrast/page.tsx` | the seven visual templates and the contrast screen | **ported in**, plus a mock row, two nav entries ("Mind check", "Compare lessons") and a new probe scenario (8 checks) |
| `app/baseline/{[runId],snapshot}/page.tsx` and its `lib/baseline.ts` variant | a *second* phase-2 baseline UI, from another agent-1 run | **left on `main`**: this branch's baseline is the verified one (40 engine tests, 25 probe checks, phase 2 report). Two implementations of one screen is a product decision, not a merge |
| `UI file.txt` (1 273 lines), `adapt-web-application-prompts.zip` | repo cruft | **not carried** |

The only line that had to move in a frozen file is the router registration in `api/app.py`.

## Third wave: agent 2's phase 3 merged (#15), wired, and the phase 4 gate

`arena/01eb08ac-adapt` (agent 2, the subject round) was merged as **PR #15** (`fb51517`). Its own
handoff (`agent-notes/2-subject-round.md`) listed the frozen-file wiring it could not do; all of it
is applied here:

1. `services/__init__.py` — `AppServices` carries a `subjects` service, built from the same session
   factory, store and model client as the rest of the container. The route module's own fallback
   still exists and now finds the real service.
2. `api/app.py` — `subjects.router` is registered in the router tuple. Until this line, the screen
   404'd in the live app (the tests mounted the router themselves).
3. **`db/models.py`** — `from ..subjects import models as _subject_models` at the bottom. This is
   what makes `Base.metadata` the *whole* schema whatever a process imported first: `create_all` on
   app boot and on `adapt init`, and the migration test's expectation, agree from either direction.
   The alternative (agent 2's own note suggested `db/init_db.py`) fixes only the CLI path; the
   migration suite's boot script imports `adapt.db.models`, and the order-dependent failure
   (`test_upgrade_head_builds_the_schema_the_models_describe`, only under a full-suite run) came
   from exactly that gap.
4. The migration keeps the placeholder revision id `P2` on `down_revision = "0008"`: it is the
   single head, the chain is linear, and nothing references the id.
5. `frontend/src/components/chrome.tsx` gained `Subject round`; the screen was an orphan route.

**Phase 4 (agent 3) never landed a branch.** What exists for §7 is the deterministic half —
`assessment/worked.py` (three graded hints and a worked example for every item, verified against the
item's own key) and the subject round's authored pools across five families — plus the gate the
brief names, which did not exist: `scripts/teaching_quality_eval.py` writes `QUALITY_REPORT.md`,
and `tests/test_teaching_quality.py` runs the same walks in the suite. That gap is stated in the
report, not papered over: there is no concept-breakdown pipeline, no per-concept three-variant
generation, no matched A/B pair generation, no symbolic checker, and no model pass (no key here).

### The defect the gate found on its first run

`local_tutor.explain` served another topic's passage while reporting `grounded: true`. The copy was
honest about it (the sentence names the source topic), but the brief's rule is exact — 4.1.2:
"mark `grounded: true` only when the shown text actually matches the item's topic". One line:
grounding now requires `topic_match`, and a cross-topic answer reports `grounded: false` with the
topic named. The test that asserted the old behaviour asserted it in as many words
(`assert answer["meta"]["grounded"] is True`); its expectation is corrected with the §4.1.2 reason
next to it, and the `topic_id` it now checks is what makes the reply's provenance checkable.

## What landed

| PR | Branch HEAD | What it brought |
|---|---|---|
| #9 | `a7503734` | evidence ledger + calibration work (agent 5) |
| #10 | `176833fe` | phase 7 security: the secrecy boundary, ratelimit scopes, exports (agent 6) |
| #12 | `aaa1ac75` | master prompts for the frontend waves (agent 3/4/5/6 spin-up) |
| #14 | `1cdd47ed` | hardening on top of #10 (the tip that was merged last) |
| #11, #13 | `77c6b040`, `701ac036` | landed on `main` instead of the arena branch; reconciled at the final merge |

Agent 2 (phase 3, the subject round) has not landed. The merge order for the final reconciliation is
`1 -> 3 -> 2 -> 6 -> 4 -> 5`; nothing here changes that.

## The one defect class in the merge: the boundary over-dropped

The phase-7 boundary (`backend/src/adapt/security/secrecy.py`, installed by
`security/middleware.py` on HTTP and WebSocket) is keyed by *substring*: a key containing `provider`,
`llm`, `model`, `token`, `budget`, `quota` or `headroom` is dropped from every response body. That is
correct for the operator vocabulary it was written against, and it silently ate learner-safe fields
somebody else had added:

* `voices` (a list of voice ids) and `model_version` (the learner-state version) - re-allowed.
* `needs_model` on lesson explanations - renamed to `needs_extra_help` rather than re-allowed.
* `provider` / `provider_note` on `/explain` and `/analyze` - replaced by `served_by`.
* `llm`, `model`, `tokens_in`, `tokens_out`, `model_ms`, `budget`, `buckets`, `calls_left`,
  `free_tiers`, `rate_limit_multiplier` on `/status`, `/quotas`, `/health` - dropped from the public
  routes altogether; that detail now lives behind `adapt status` on the console.

Two rules follow from this and are now written into the code:

1. **The boundary stays strict; the routes change.** Re-allowing a banned word to make a payload
   survive is the wrong direction: `test_ai1_and_ai2_success_metadata_is_private_at_the_public_boundary`
   and the canary route walk both assert the *absence* of those words, and they are the authority.
   The route-side mapping is `secrecy.public_payload(data, served_locally=...)`: it drops the
   runtime keys and stamps `served_by` with learner wording.
2. **A comparison is not a leak, but a key name is.** `model` as a *key* is gone from every public
   payload (`/profile.shared_state.learner_model` is the learner's own model of themself, not a
   service). Where the code needs to compare `reply.provider != "gemini"` it does so internally and
   only publishes the resulting sentence.

## The public vocabulary (frozen here, because the frontend reads it)

| Surface | Public shape |
|---|---|
| `/health` | `{ok, version, lessons{state,says,note}, database_reachable, database_error?}` |
| `/status` | `{ok, version, lessons, demo_mode, data_protection}` |
| `/quotas` | `{pacing{requests, writes, lesson_requests, new_accounts, voice_frames, exports, sign_in_attempts}, note}` |
| `/privacy` | `sent_to_model{when, identity, redaction, on_refusal}`, `storage: data_protection()`, `voice`, `learner_controls` |
| `/explain`, `/analyze`, `/tutor/message`, WS `reply.meta` | `served_by` in {"your own material", "written for you"} |
| `/profile` | `shared_state.learner_model` (was `model`) |
| resources | `sources[] {kind, ok, source, note, count}` + per-result `kind`; `/resources/status {sources, cache_days, note}` |
| lesson explains | `needs_extra_help`, `asked_for_extra_help`; no `model`, no `tokens_*` |

`data_protection()` keeps one deliberate exception: it names the *engine* of encryption
(`sqlite + field-level encryption`). The claim "the file is encrypted" is unverifiable without it,
and an encryption engine is not a model provider. Everything around it (paths, key source, driver
config, requested mode) stays console-only.

## Test expectations edited, with the reason in each comment

Never `test_config_secrecy.py` or `test_prompt_injection.py` - those are the gate and stayed
untouched. Edited, each with a §3 comment:

* `tests/test_api_learner_loop.py` - health counts/mode, `provider` -> `served_by` on `/analyze` and
  `/explain`, WS meta, the whole `/status` `/quotas` `/privacy` block (rewritten, plus absence
  checks), resource `providers` -> `sources`.
* `tests/test_crypto_and_encryption.py` - the two encryption-honesty tests read
  `data_protection` and still assert what is *not* protected.
* `tests/test_state_foundation.py` - the operator component names are asserted against
  `current().status()` (console), not the HTTP payload; `learner_model` for the shared snapshot.

One test-only bug fixed while integrating, because it was measuring the wrong thing:
`test_private_routes_refuse_anonymous_callers` used the session-scoped client, whose cookie jar
carries a cookie from whichever test signed in earlier in the same run. It passed or failed on file
order. It now clears the jar (and restores the exact cookie objects) so "anonymous" means anonymous.

## Frontend and bundle

The four readers of the changed shapes were updated (`chrome.tsx` status chip, `settings/page.tsx`
privacy block, `question.tsx` provenance line, `lib/types.ts`, `lib/mock.ts`, `engine/types.ts`, and
the probe's quota/rate-limit sizing). Two §3 violations were found by scanning the *built* bundle
rather than the source:

* `app/voice/page.tsx` printed `pip install faster-whisper kokoro` and named both engines in tags and
  fallbacks. The card is replaced by a learner-safe sentence; the commands stay in `docs/VOICE.md`.
* the `/voice/capabilities` mock carried engine names in its `detail` strings.

After the fix, `grep -ri` over `frontend/.next/static` finds no `gemini`, `openalex`, `arxiv`,
`youtube`, `faster-whisper`, `kokoro` or `model_name`. The only remaining hit under `google` is
Next.js's own `Googlebot` bot-detection regex in framework code - not configuration, and not
removable without patching the framework.

Backend-side, `STT.probe()` and `KokoroTTS.probe()` returned sentences naming their package and the
pip command; those travel to a signed-in learner through `/voice/capabilities`. They now return
learner-safe sentences and log the install hint instead (it is also in `docs/VOICE.md`).

## Commands run, with results

* `pytest -q` (all 887) - **887 passed**, 74.7 s, at the tip of this note.
* `npm run lint` (contract check + `tsc --noEmit`) - contract ok, 88 backend routes vs 78 calls.
* `npm run build` - compiled successfully.
* `npm run test:engine`, `test:lib`, `test:server` - 15, 18 and 2 passed.
* `scripts/probe-fresh.sh all` - **234/234 checks passed**, exit 0.
* `pytest -q` after the phase-5 port - **970 passed** (887 + the 83 adapter tests).
* `scripts/probe-fresh.sh contrast`, `voice`, `learn` - 8/8, 8/8 and 26/26.
* bundle scan - clean, as above.

## The frontend items the master-prompts note left for the integrator

`agent-notes/master-prompts-3-4-5-6-frontend.md` listed four; all are applied:

* **Teach-back receipt** — `Receipt.teachback` gains `feedback`, `misconceptions`,
  `feedback_source`; the receipt shows the grader's sentence first, the coverage line under it, and
  "one thing to double-check" only when a misconception survived the verbatim-quote gate.
* **Retention check** — new `RetentionCheck` type and a per-row step on `/review`: "Show me the
  check" fetches the question and why now; the answer key is fetched with `with_notes=1` **only
  after** the learner has rated, which is the honesty rule the endpoint was built with. The probe
  asserts both halves (question visible, key not; then key visible after the rating).
* **Resource picks** — no screen calls that endpoint, so nothing to change; the note stands.
* **Approval script** — nothing to change; `ask_text` was already rendered.

## Still open

* Agent 2's phase 3 has not landed; the final merge order is unchanged.
* Which baseline UI ships (this branch's or `main`'s) - recommend this branch's, since it is the one
  verified end to end here. Flagged for the final merge.
* **Phase 4's content engine is the one phase with no landing.** The gate and the deterministic
  content are in place and green; the generative half (concept breakdown, three variants per
  concept, matched pairs, symbolic verification) is not built. That is the largest remaining gap
  against the brief and it is named in the final report.
* Everything a browser or a live model key would verify is in `docs/UNVERIFIED.md`.
