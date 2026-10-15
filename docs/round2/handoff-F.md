# Handoff — Phase F (baseline games and subject round)

Requests for other phases. Phase F did not edit their files.

## For the owner of the subject API (the `subjects/*` contract, learner JSON)

* **`model_note` is still in the subject plan JSON** (`subjects/leanings.py` and `subjects/plan.py`, sent as the API field `model_note`). The page does not render it, so a learner does not see it, but it is part of the API. Its wording says "the model could not be asked from here" and "no model is configured". `backend/tests/test_subjects.py` pins that wording (`"no model is configured" in leanings.model_note`, `"every skill rated 3" in …`). Phase F was not allowed to weaken that test, so the field was left alone. A rename should come with a change to that test, made by whoever owns it, plus a learner-neutral text for the field.

## For pack generation (B) and lesson rendering (C)

* **Pop-up lessons are missing for most topics.** On the world-history round the map says "the pop-up lesson was skipped rather than invented" because no authored ninety-second lesson pair exists for the topic. That is the honest behaviour, but it means the subject round reaches the map without the pop-up lesson for most topics. Authored pairs for the common subjects would close that gap.

## For the onboarding and journey shell (D)

* **The baseline's return card needs `finished`.** The return visit asks `GET /api/v1/baseline/next-day?finished=true` (and the page does the same through `load_next_day(run_id, finished)`). Any link from the journey shell that opens the baseline return should keep the `finished` flag; without it, the page asks about the open sitting, which is the bug this phase fixed.

## Open for everyone

* **Automated taps look like a very fast learner.** Under Playwright the median quick tap is about 50 ms, so the snapshot's pace reads "short blocks" with a drift of about +189%. The numbers are correct for what was sent; they are an artifact of the suite, not a finding about a learner. Anyone who changes pace measurement should check it against a human-speed run (see `unverified-F.md`).
