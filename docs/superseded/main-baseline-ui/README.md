# `main`'s second baseline UI (superseded, kept as reference)

Two files arrived with the final merge of `main` (`a1edc69`, an unrelated-history snapshot):
`app/baseline/[runId]/page.tsx` and `app/baseline/snapshot/page.tsx`. They are a **second
implementation** of the phase-2 baseline screens, written in a different agent session (agent 1 ran
at least twice: once on this branch, once on `main`).

The branch's baseline (`frontend/src/app/baseline/page.tsx` plus `lib/baseline.ts`) is the one this
repository verified end to end:

* the engine's 40 unit tests (`frontend/src/lib/engine/*.test.mjs`),
* the 25-check probe walk (`scripts/probe-fresh.sh baseline`),
* the phase 2 report in `docs/FUNCTIONALITY_ROUND.md`,
* the vault keyboard path and the shopkeeper-stage fix recorded in `agent-notes/1-baseline.md`.

Two implementations of one screen is a product decision, not a merge conflict, so the choice above
was made and the other one is kept here — out of the app tree, so it cannot half-work behind a URL —
rather than deleted. If you prefer `main`'s version, move these files back under
`frontend/src/app/baseline/` and delete the branch's `page.tsx`, then re-run
`scripts/probe-fresh.sh baseline`: the probe checks are written against learner-visible behaviour
(radio group, skippable questions, the nine-tile vault, "Stop here"), so whichever version passes
them is the one that works.
