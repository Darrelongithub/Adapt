# Unverified — Phase A

Do not treat these as passed.

## Chromium

Chromium is not installed at `/opt/pw-browsers/chromium`. `playwright install` was not run. `frontend/tests-e2e/banned_phrases.mjs` was not executed after the merge with `main`. There are no screenshots in `docs/screens/a/`. Paint and layout were not checked.

## Live model

No Gemini key is configured. No live model output was judged. `adapt config set-key`, `adapt config test`, and `scripts/live_model_check.py` were not run.

## Still in other phases' files

`backend/src/adapt/subjects/leanings.py` still contains learner-visible `model` sentences. Phase F owns that file. Starting a subject round was not walked.
