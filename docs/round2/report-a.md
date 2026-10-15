# Part A report

## Scope and changes

- Made the static `GET /api/v1/privacy` notice public and read-only; learner-facing privacy wording no longer exposes API paths or storage-engine details.
- Fixed secrecy logging so structured formatted messages are scrubbed while Uvicorn access-log formatting retains its five-value argument tuple. Added a regression that formats the redacted access record and verifies the rendered line has no secret and no logging error.
- Added item-kind closing prompts (numeric, MCQ, factual/recall; no prompt for unclassified items), misconception-specific MCQ hints, and a `generic_hint` evaluator that removes item stems/options and flags reused hint bodies across three or more distinct items in both the bank and generated content.
- Authored one-sentence `key_idea` and `why_it_works` text for all 22 bank topics, exposed those fields in the topic catalog, and inserted them before deterministic worked steps.
- Reworded contrast evidence for stated items and demo profiles; removed developer/configuration copy from the shared status, home, settings, voice, progress, lesson, question, and diagnose surfaces. The Phase D-owned onboarding page is untouched; exact copy is in `handoff-A.md`.
- Added `frontend/tests-e2e/banned-phrases.mjs` and `npm run test:e2e:banned`. The crawler discovers the 14 app page routes, checks visible text, placeholders, titles and aria-labels signed-out and after guest sign-in, and checks the public privacy route plus contrast wording.

## Files changed

- Backend: `backend/src/adapt/ai/local_tutor.py`, `backend/src/adapt/api/routes/system.py`, `backend/src/adapt/assessment/bank.py`, `backend/src/adapt/assessment/worked.py`, `backend/src/adapt/security/secrecy.py`; regression tests in `backend/tests/test_api_learner_loop.py`, `backend/tests/test_config_secrecy.py`, `backend/tests/test_functionality_round_phase1.py`, and `backend/tests/test_teaching_quality.py`; evaluator in `scripts/teaching_quality_eval.py`.
- Frontend: `frontend/package.json`, `frontend/package-lock.json`, `frontend/tests-e2e/banned-phrases.mjs`, `frontend/src/app/contrast/page.tsx`, `frontend/src/app/diagnose/page.tsx`, `frontend/src/app/lesson/page.tsx`, `frontend/src/app/page.tsx`, `frontend/src/app/progress/page.tsx`, `frontend/src/app/settings/page.tsx`, `frontend/src/app/voice/page.tsx`, `frontend/src/components/chrome.tsx`, and `frontend/src/components/question.tsx`.
- Documentation: `docs/round2/report-a.md`, `docs/round2/handoff-A.md`, and `docs/round2/unverified-a.md`.

## Verification

- `cd backend && ./.venv/bin/pytest -q` — **1,103 passed, 1 skipped**, one existing Starlette/httpx deprecation warning.
- From `frontend/`: `npm run lint` — passed (107 backend routes checked against 91 frontend calls); `npm run test:engine` — **15 passed**; `npm run test:server` — **2 passed**; `npm run test:lib` — **18 passed**.
- `./backend/.venv/bin/python scripts/teaching_quality_eval.py --json --out /tmp/adapt-quality-a.md` — **zero gate flags** across 273 questions and 61 named topics; generated-content walk covered 11 topics and 37 concepts. No model pass was run.
- `./backend/.venv/bin/ruff check --select F821 ...` on the changed Python files — passed. A broader Ruff run reported 551 style/line-length findings across touched files; no unrelated formatting sweep was made.
- `git diff --check` and `node --check frontend/tests-e2e/banned-phrases.mjs` — passed.
- `npm audit --json` — reports one moderate Next.js advisory and one high PostCSS advisory nested under Next.js; the suggested fix is a major Next.js update, so no dependency upgrade was attempted in this phase.

## Remaining / not verified

- No real browser crawl, screenshots, or visual review: `/opt/pw-browsers/chromium` is absent. No browser was installed. The Playwright script is syntax-checked only; see `unverified-a.md`.
- The `/onboarding` route still contains copy owned by Phase D, so the route-wide banned-phrase crawl must not be called clean until the handoff is applied.
- Live model-written output remains unverified because no model key is configured.
- An initial full pytest invocation from the repository root did not load the backend pytest configuration and failed async tests; the correct `backend/` invocation above passed.
- This session is pinned to `arena/7efe7537-adapt`. No rebase, merge to `main`, or push was performed.
