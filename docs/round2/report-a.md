# Phase A report

Branch: `arena/9f6212e5-adapt`. This merge reconciles that branch with the Phase A already on `main` (pull request 17). Both implementations did the same tasks. The merged tree keeps both regression suites.

## What landed

1. `GET /api/v1/privacy` is public and read-only. The home screen does not say the list could not be read while that request is still loading.
2. Learner-facing developer copy is removed from the pages this phase may edit. Onboarding copy is not edited; the replacements are in `handoff-A.md`.
3. The secrecy log filter redacts the rendered message and keeps argument shapes, including nested lists and mappings, so uvicorn access logs still format.
4. Closing prompts follow item kind. Multiple-choice hints come from each distractor's misconception.
5. Each authored bank topic has a one-sentence key idea and why, shown before the worked steps.
6. Stated contrast items show `stated`, not `(n=0)`. Demo profiles do not mention a profile version.

## Tests

Ran on 2026-10-14 after resolving the merge with `main`.

| Command | Result |
|---|---|
| `cd backend && /tmp/adapt-venv/bin/pytest -q` | 1122 passed, 1 skipped |
| `cd frontend && npm run test:lib` | 24 passed |
| `cd frontend && npx tsc --noEmit` | clean |

Both Phase A suites passed together: `tests/test_round2_phase_a.py` and `tests/test_round2_a_public_privacy_and_logs.py` plus `tests/test_round2_a_teaching_text.py` (25 tests).

## Browser

Chromium is not installed at `/opt/pw-browsers/chromium`. `frontend/tests-e2e/banned_phrases.mjs` was not executed, and `playwright install` was not run. There are no screenshots in `docs/screens/a/`.

Earlier on this branch, before the merge, a jsdom walk of the 14 learner routes signed out and as a guest found no banned phrases, and the home screen showed the recorded-events list. That was not a Chromium check, and it was not repeated after this merge.

## Handoffs

See `handoff-A.md` and `handoff-a.md`. See `unverified-a.md` for what was not observed.
