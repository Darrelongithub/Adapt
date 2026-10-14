# Phase A report

Branch: `arena/9f6212e5-adapt`. This session cannot create `r2/phase-a` or merge to `main`. The work is committed and pushed only on `arena/9f6212e5-adapt`.

## What landed

1. `GET /api/v1/privacy` is public and read-only. It still opens a database session so the storage claim describes this instance, but it no longer requires a learner. The body has no `student_id` and does not name a service. The home screen does not say the list could not be read while that request is still loading, or after the list has arrived.
2. Learner-facing developer copy was removed from the pages this phase may edit: home, settings, contrast, progress, diagnose, lesson, voice, the status chip, and the question reveal. Status wording is `Lessons: simple mode` (or `Lessons: ready` when a written-for-you reply is actually available). The chip no longer says `protection: not reported`. Onboarding copy is not edited; the exact replacements are in `handoff-A.md`.
3. The secrecy log filter redacts the rendered message and keeps argument shapes. Uvicorn access logs still receive a five-tuple whose status is an `int`. Secrets in the message are redacted. `args` are not cleared.
4. Closing prompts follow item kind: numeric asks to change the numbers; multiple choice asks the learner to cover the options and say which they would pick and why; a recall item asks them to recall without looking; teach-back, worked examples, unknown kinds, and a missing item get no prompt.
5. Multiple-choice hints come from each distractor's misconception instead of `which one is right: <stem>`. `scripts/teaching_quality_eval.py` flags a hint residue that is identical across three or more items after the stem and quoted options are removed.
6. Each of the 22 authored bank topics has a one-sentence `key_idea` and `why_it_works` in `backend/src/adapt/content/concept_why.py`. Deterministic explanations show those sentences before the worked steps. A database topic id, or the placeholder title `the topic`, matches nothing.
7. Stated contrast items with no measured count show `stated`, not `(n=0)`. A demo profile footer says the lesson was planned from a demo profile, not `planned from profile version 0`.

## Files changed

Owned edits:

- `backend/src/adapt/api/routes/system.py` — public privacy list; learner-safe lesson wording
- `backend/src/adapt/security/secrecy.py` — access-log redaction keeps arg shapes
- `backend/src/adapt/assessment/worked.py` — misconception hints
- `backend/src/adapt/assessment/bank.py` — hint helper used by those hints
- `backend/src/adapt/ai/local_tutor.py` — closing prompts and key-idea placement
- `backend/src/adapt/content/concept_why.py` — new authored sentences
- `scripts/teaching_quality_eval.py` — identical-hint residue gate
- `frontend/src/app/contrast/page.tsx` and `frontend/src/lib/learnerCopy.ts` — contrast wording
- `frontend/src/components/chrome.tsx`, `frontend/src/app/page.tsx`, `frontend/src/app/settings/page.tsx`, `frontend/src/app/progress/page.tsx`, `frontend/src/app/diagnose/page.tsx`, `frontend/src/app/lesson/page.tsx`, `frontend/src/app/voice/page.tsx`, `frontend/src/components/question.tsx`, `frontend/src/components/scenes/stages.tsx` — learner copy

New checks:

- `backend/tests/test_round2_phase_a.py`
- `frontend/src/lib/learnerCopy.test.mjs`
- `frontend/src/lib/bannedPhrases.mjs`
- `frontend/tests-e2e/banned_phrases.mjs`

Not edited, by ownership: onboarding page, baseline games, `content/skeleton`, `ai/lesson_writer`, `ai/llm.py`.

## Tests and browser checks

Ran from this machine on 2026-10-14.

| Command | Result |
|---|---|
| `cd backend && /tmp/adapt-venv/bin/pytest -q` | 1103 passed, 1 skipped, 1 warning, 104.24s |
| `cd backend && /tmp/adapt-venv/bin/pytest -q tests/test_round2_phase_a.py` | 6 passed |
| `cd frontend && npm run lint` | contract check ok, `tsc --noEmit` clean |
| `cd frontend && npm run test:engine` | 15 passed |
| `cd frontend && npm run test:server` | 2 passed |
| `cd frontend && npm run test:lib` | 24 passed |
| `/tmp/adapt-venv/bin/python scripts/teaching_quality_eval.py --out /tmp/quality-a.md` | exit 0. Walked 273 questions across 61 named topics. Zero gate flags. Model pass did not run: no key. |

The quality report was written to `/tmp/quality-a.md` and was not committed. `QUALITY_REPORT.md` was not regenerated.

Signed-out `GET /api/v1/privacy` through the web proxy (`http://127.0.0.1:3000/api/v1/privacy`) returned 200, with 5 recorded lines and no `student_id`.

A jsdom walk then loaded every listed route, signed out and as a guest, against that running app. It read visible text plus title, aria-label, placeholder, and alt, and applied the same banned-phrase list as `frontend/tests-e2e/banned_phrases.mjs`. All 14 routes passed in both states. The home screen showed the server's recorded-events list and did not show "could not be read just now". This is not a Chromium run: paint, layout, and real browser chrome were not checked.

The Playwright script itself was **not run**. `/opt/pw-browsers/chromium` is not on this machine, and `playwright install` is forbidden. The script is `frontend/tests-e2e/banned_phrases.mjs`. It never downloads a browser. When a binary is already present:

```
ADAPT_MODE=local ADAPT_BASE_URL=http://127.0.0.1:3000 \
  ADAPT_CHROMIUM=/opt/pw-browsers/chromium \
  node frontend/tests-e2e/banned_phrases.mjs
```

Run the frontend with `node server.mjs` proxying the API on port 8000. Do not use `?mock=1`. The script visits every listed route signed out and as a guest created with `POST /api/v1/students` `{as_guest: true}`. It checks visible text plus title, aria-label, placeholder, and alt. It does not scan bundles. Until Phase D applies the onboarding handoff, `/onboarding` ignores only the three exact sentences listed in `handoff-A.md`.

## Screenshots

None. `docs/screens/a/` has no images because no browser was available. Do not treat a missing image as a passed visual check.

## Unverified

See `unverified-a.md`. Short version: no Chromium crawl, no screenshots, no live Gemini call. The jsdom walk passed, and that is not a browser check.

## Handoffs for Phase G

Read `handoff-A.md` (Phase D is told that exact name). After the onboarding replacements land, delete the three strings in `ONBOARDING_DEFERRED` inside `frontend/src/lib/bannedPhrases.mjs` so the crawl no longer exempts them.

Phase F owns `backend/src/adapt/subjects/leanings.py`. Its learner-visible sentences still contain `model`. A signed-in visit to the subject round can fail the crawl until those sentences change. Suggested replacements are in the handoff.
