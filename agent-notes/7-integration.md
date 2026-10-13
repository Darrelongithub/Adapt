# Integration note 1 - the landed agent PRs, and the §3 boundary

Written by the integrator session (the one that owns `arena/06d446fc-adapt`). This is the record of
merging the finished agents' PRs and making the merged tree green against brief §3. It is a working
note like the others in `agent-notes/`; the phase-level report is in `docs/FUNCTIONALITY_ROUND.md`.

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
* `scripts/probe-fresh.sh all` - **234/234 checks passed**, exit 0 (226 API calls).
* bundle scan - clean, as above.

## Still open

* Agent 2's phase 3 has not landed; the final merge order is unchanged.
* The `main`-side PRs (#11, #13) still need reconciling at the final merge.
* Everything a browser or a live model key would verify is in `docs/UNVERIFIED.md`.
