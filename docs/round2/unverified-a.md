# Unverified — Phase A

These items were not observed in the way the brief asks. Do not treat them as passed.

## Chromium

Chromium is not installed at `/opt/pw-browsers/chromium`. `playwright install` was not run. `frontend/tests-e2e/banned_phrases.mjs` was not executed. There are no screenshots in `docs/screens/a/`. Paint, layout, and real browser chrome were not checked.

## What was checked instead

This is not a browser result.

- Signed-out `GET /api/v1/privacy` through the web proxy on port 3000 returned 200, with 5 recorded lines and no `student_id`.
- A jsdom walk loaded `/`, `/learn`, `/lesson`, `/proposals`, `/review`, `/progress`, `/baseline`, `/subject`, `/contrast`, `/evidence`, `/voice`, `/settings`, `/onboarding`, and `/diagnose`, signed out and as a guest. Visible text plus title, aria-label, placeholder, and alt had no banned phrase. The home screen showed the recorded-events list and did not show "could not be read just now".

The subject page's first view did not show the `model` sentences in `backend/src/adapt/subjects/leanings.py`. Starting a subject round was not walked. Those sentences are still in that file, which Phase F owns.

## Live model

No Gemini key is configured. `scripts/teaching_quality_eval.py` reported `Ran: False` for the model pass. No live model output was judged. `adapt config set-key`, `adapt config test`, and `scripts/live_model_check.py` were not run.
