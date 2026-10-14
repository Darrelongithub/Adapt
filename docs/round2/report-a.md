# Round 2 — Part A report (privacy, learner copy, log filter, teaching text)

Branch note: the brief asked for `r2/phase-a` merged to `main`; this session is
fixed to `arena/299ac70e-adapt`, so all work landed there and nothing was merged
or force-pushed anywhere else.

## What changed, by task

1. **Privacy list is public.** `GET /api/v1/privacy` (in
   `backend/src/adapt/api/routes/system.py` — there is no `routes/privacy*`
   module) no longer requires a session. It is a static, non-personal list, so
   the consent screen can show it before sign-in. Its learner-facing strings
   were reworded to name no service, route or internal mechanism.
2. **Developer text out of learner copy.** Fixed directly on: home
   (`frontend/src/app/page.tsx` — route label removed from the recorded panel,
   "the API is answering" tag, consent line), settings
   (`frontend/src/app/settings/page.tsx` — consent label/hint, recorded panel
   heading, sharing labels), lesson, progress, diagnose, voice
   (`frontend/src/app/…`), the question component's reveal tooltip, and the
   status chip (`frontend/src/components/chrome.tsx` — "Lessons: simple mode" /
   "Lessons: ready", and the protection segment is omitted until a real
   protection fact exists instead of "protection: not reported").
   Onboarding copy belongs to Phase D: exact replacements are in
   `docs/round2/handoff-a.md`. A Playwright crawl,
   `frontend/tests-e2e/banned_phrases.mjs`, visits every static route signed
   out and signed in (guest) and fails on any banned phrase in visible text;
   it also asserts the signed-out home privacy list renders.
3. **Log filter.** `backend/src/adapt/security/secrecy.py` now redacts the
   format string and each argument by position, preserving tuple/list/mapping
   shapes and non-string argument types, instead of collapsing the record.
   Verified live: uvicorn access lines render
   (`GET /api/v1/health?key=[withheld] HTTP/1.1" 200`) with no
   `Logging error` traceback and no secret.
4. **Closing prompts by kind.** `closing_prompt()` in
   `backend/src/adapt/ai/local_tutor.py`: numeric → change the numbers;
   choice → cover the options, say which you'd pick and why; factual/text →
   recall without looking; teach-back, worked-example, simulation and no item
   → no prompt, and `explain()` omits the "your turn" section then.
5. **MCQ hints from misconceptions.** `backend/src/adapt/assessment/worked.py`
   builds graded choice hints from each distractor's own misconception text;
   the "which one is right: <stem>" wrapper is gone.
   `scripts/teaching_quality_eval.py` gained `residual_hint_text()` /
   `repeated_hint_flags()`: after removing the stem, quoted spans and option
   texts, any residual hint text shared by ≥3 distinct items is a
   `generic_hint` flag, applied to generated (not authored) hints in both the
   bank and content-engine walks.
6. **Simple-mode "why".** `CONCEPT_IDEAS` in `local_tutor.py` authors a
   one-sentence `key_idea` and `why_it_works` for all 22 bank concepts;
   `explain()` inserts them once, before worked steps, on both explanation
   paths.
7. **Contrast wording.** `frontend/src/app/contrast/page.tsx`: stated items
   read "stated" (no "(n=0)"), no-evidence items "no evidence behind it yet",
   demo profiles read "planned for this demo profile from the choices shown
   above, not from a real learner's evidence", and the footer explains lesson
   production in plain words.

## Files changed

Backend: `api/routes/system.py`, `security/secrecy.py`,
`assessment/worked.py`, `ai/local_tutor.py`, `scripts/teaching_quality_eval.py`.
Frontend: `app/contrast/page.tsx`, `app/page.tsx`, `app/settings/page.tsx`,
`app/lesson/page.tsx`, `app/progress/page.tsx`, `app/diagnose/page.tsx`,
`app/voice/page.tsx`, `components/chrome.tsx`, `components/question.tsx`.
New: `backend/tests/test_round2_a_public_privacy_and_logs.py`,
`backend/tests/test_round2_a_teaching_text.py`,
`frontend/tests-e2e/banned_phrases.mjs`, these docs.

## Tests and checks run

- `cd backend && pytest -q` — **1116 passed, 1 skipped** (baseline was
  1097 + 1 skipped; +19 new Part A tests, none deleted or weakened).
- New tests cover: signed-out privacy 200 + payload shape + no developer
  terms; secrecy filter shape preservation, message redaction, and a real
  `uvicorn.logging.AccessFormatter` record formatting after filtering;
  closing prompts per kind and their absence; one-sentence key idea/why for
  every bank concept and their position before worked examples; MCQ hints
  derived from misconceptions; the strengthened repeated-hint gate over the
  whole bank and its stem/quote stripping.
- `python scripts/teaching_quality_eval.py` — exit 0, **zero gate flags
  across 273 questions** (61 topics, 300 hints, 100 worked examples).
- Frontend: `npm run lint` (contract check + `tsc --noEmit`) passes;
  `test:engine` 15/15, `test:server` 2/2, `test:lib` 18/18; `npm run build`
  passes.
- Live servers + jsdom UI probe (`npm run probe`): 271/271 checks passed
  before the probe stalled in the baseline scenario (another phase's area);
  a targeted re-run of home, settings, voice, contrast and lesson passed
  52/52 before stalling in the lesson scene player (also not Part A copy).
  The probe's contrast check still passes with the new wording.
- The Playwright banned-phrase crawl could not run here (see
  `docs/round2/unverified-a.md`); it exits 2 with that message rather than
  passing silently.

## Unverified

Everything in `docs/round2/unverified-a.md`: the real-browser crawl, browser
verification of the signed-out privacy screen, and screenshots
(`docs/screens/a/` contains a note instead — no browser exists in this
sandbox).

## Handoffs for G

`docs/round2/handoff-a.md`: exact onboarding copy replacements for Phase D/G,
plus backend-produced strings other phases own that can still surface
developer wording (voice capability fallbacks, lesson answer notes).
