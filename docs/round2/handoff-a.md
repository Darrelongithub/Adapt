# Handoff A

The replacements Phase D must apply are in [`handoff-A.md`](handoff-A.md). That is the path Phase D is told to read. This file is the same request under the lowercase name.

## Still open after the merge

- Onboarding copy in `frontend/src/app/onboarding/page.tsx` still names an API key and `ignored_keys`. Apply `handoff-A.md`, then delete the three strings in `ONBOARDING_DEFERRED` in `frontend/src/lib/bannedPhrases.mjs`.
- `backend/src/adapt/subjects/leanings.py` still returns learner-visible sentences containing `model`. Phase F owns that file. Suggested replacements are in `handoff-A.md`.
- `frontend/src/lib/mock.ts` still contains `model` in fixture text. Those strings render only under `?mock=1`. The required crawl does not use mock mode.
