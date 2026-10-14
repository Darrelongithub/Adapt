# Handoff from Phase D

For Phase G (integration). Nothing here is a defect in another phase's work; these are the things
Phase D could not do from its own branch, plus two small tidy-ups in files D does not own.

## 1. Merge the branch (blocking)

Phase D's work is committed on **`arena/a4799880-adapt`** (this session is pinned to that branch and
cannot push `main` or create `r2/phase-d`). It is finished and green on its own:

* `cd backend && pytest -q` → 1136 passed, 1 skipped
* `cd frontend && npm run lint && npm run test:engine && npm run test:server && npm run test:lib` → ok, 15, 2, 30
* `node frontend/scripts-ui-probe.mjs` (needs API `:8000` + web `:3000`) → 308/308

Please fast-forward it into `main` (the intended `r2/phase-d` merge), not force-push anything.

## 2. Migration numbering / head

`backend/migrations/versions/0009_journey_state.py` has `down_revision = "P2"` because `0008` already
had a child (`P2`). Verified in a scratch database: `0007 → 0008 → P2 → 0009`, `current` = `0009
(head)`, `alembic check` clean.

**If another phase adds a migration whose `down_revision` is `P2` (or `0008`), there are two heads
and `alembic upgrade head` will refuse.** Fix by re-pointing the newcomer at `0009`, then re-run
`alembic heads` (expect one) and `backend/tests/test_migrations.py`. D's migration is a single
`CREATE TABLE` for `journey_stages` with a unique `(student_id, stage)` constraint and cannot be
reordered relative to anything else.

## 3. `GET /api/v1/journey` now answers two shapes — that is deliberate

The frozen contract (Part 0) puts the journey *state* at `GET /api/v1/journey`, but that path already
served the round-1 **timeline** (nodes, `summary`), which the demo CLI and the dashboard read. Rather
than break one of them:

* the state machine is at `GET /api/v1/journey/state` / `POST /api/v1/journey/advance`;
* `GET /api/v1/journey` keeps its timeline fields and **spreads the frozen keys** (`stage`,
  `stages`, `progress`, `not_measured`) into the same payload.

So `{stage, stages:[{id,status}]}` is available from the frozen path *and* the timeline still works.
Please do not "tidy" one of the two paths away: both are load-bearing while the demo CLI exists.
If you would rather have a single route, the change is in `backend/src/adapt/api/routes/learning.py`
(the `journey()` handler) — D has no preference, it just did not want to delete a working endpoint.

## 4. Stale note in Phase A's crawl (cosmetic, A's file)

`frontend/tests-e2e/banned_phrases.mjs`'s header still says onboarding copy belongs to another phase
and suggests `BANNED_PHRASES_KNOWN=/onboarding`. D applied all three of `handoff-A.md`'s replacements
and deleted `ONBOARDING_DEFERRED` from `frontend/src/lib/bannedPhrases.mjs`, so the exemption is
dead code the docstring still advertises. The behaviour is correct today (the escape hatch is opt-in
and empty by default); please drop the paragraph when you next touch that file.

## 5. What D deliberately did **not** touch

* `backend/src/adapt/baseline/*`, the games and the subject round — Phase F. The journey shell only
  *reads* their rows (`BaselineRun`, `SubjectRound`, `MindSnapshot`, `LessonRun`, `Attempt`) to
  derive a stage, and only *links* to their screens.
* Lesson rendering and pack generation — Phases C and B. `frontend/tests-e2e/journey.mjs` stubs their
  endpoints at the network edge and asserts only that the shell sends the learner to the right route.
* `frontend/src/app/diagnose/page.tsx` — the eight-question placement screen is consistent as it
  stands ("four to twelve, eight by default"); D changed no copy there.

## 6. One thing to be aware of when you run the UI probe

`frontend/scripts-ui-probe.mjs` gained a `tickBox()` helper. jsdom hydrates the real page
concurrently, and `next-route-announcer` exists a beat before the subtree being pressed has handlers,
so a single press in that window is not received by React (the DOM property moves, then snaps back).
If you see a switch check flap, that is the harness, not the app — and `tickBox` is the fix, not a
retry to be sprinkled back in by hand.
