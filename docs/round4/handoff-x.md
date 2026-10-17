# X → Y: lesson interaction

## Delivered
- `LessonRenderer.tsx`, `first-lesson/page.tsx`, `choice-cards.css`: filled ≥48px answer cards with keyboard activation, hover/focus, selected, green correct and red incorrect states (not a blue tick for a wrong choice). Selecting posts to the real grade endpoint immediately.
- Secondary **I still don't get it — try another way** disables during grading/explanation/reteach. Each added moment has `Another way: <method>` rather than a repeated generic section title.
- Comparison-only renderers retain all options as labelled read-only preview cards rather than discarding option text or showing fake grading controls.
- Bounded visual panel + caption for diagram/table/sketch blocks; readable ≤65ch text. Backend emits explanation chunks of at most 60 words. One collapsed **About this lesson** per displayed lesson, not raw per-block “Why this block” controls.
- Frontend ID/stem guard across moments also covers old cached values. Late upgrades are propagated into parent state for deduplication, and cannot replace options after a learner has attempted them.

## Y integration applied / evidence
Real local-API wrong-choice feedback, two different reteaches, Space/Enter via actual Tab traversal, target sizes, in-flight disabled state (transport delayed, response not mocked), no repeated IDs and a single info disclosure are checked in `tests-e2e/round4.mjs`.

Screenshots: `docs/screens/x/{desktop,mobile}-{guest,account,returning}-{lesson,feedback}.png`. The longer full-page images deliberately include feedback and both reteaches; they are not cropped to hide below-fold content. The scripted-provider journey is an additional integration test, not a live-provider claim.
