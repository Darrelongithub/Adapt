# Unverified — Phase A

Nothing below was observed. Do not treat it as passed.

## Browser

Chromium is not installed at `/opt/pw-browsers/chromium`. A search of this machine found no Chromium, Chrome, or Playwright browser. `playwright install` was not run.

Not done:

- Signed-out browser visit to the consent screen, to confirm it no longer says the privacy list could not be read.
- `frontend/tests-e2e/banned_phrases.mjs` against a running app, signed out and signed in.
- Screenshots under `docs/screens/a/`.

The privacy route was checked with the FastAPI test client only: signed-out `GET /api/v1/privacy` returned 200, with no `student_id`, and the dumped body avoided the service names that test asserts. That is not a browser check.

## Live model

No Gemini key is configured. `scripts/teaching_quality_eval.py` reported `Ran: False` for the model pass. No live model output was judged. `adapt config set-key`, `adapt config test`, and `scripts/live_model_check.py` were not run.

## What the API test does not cover

A signed-in subject round can still show `model` from `backend/src/adapt/subjects/leanings.py`. That file belongs to Phase F. The crawl script would fail on it. It has not been executed, so that failure is predicted from the source, not observed in a browser.
