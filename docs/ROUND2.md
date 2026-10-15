# Round 2 — what shipped

Round 2 turned the round-1 toolkit into one path a new learner walks, and gave the app the
ability to teach a topic nobody authored in advance. Seven agents worked in parallel (A–F),
then one integrated (G). This page is the summary; the per-phase reports are in
`docs/round2/report-*.md`, the handoffs in `docs/round2/handoff-*.md`, and the integration
report is `docs/round2/report-g.md`.

## The shape of it now

A learner's path is eight stages, held on the server and resumable from any device:
**account → preferences → games → subject → subject round → snapshot → first lesson →
practice**. Every stage can be skipped, and a skip is recorded as *skipped — not measured*,
never as a zero.

Behind the first lesson sits the round's new machinery, built against frozen contracts so
the pieces could be written independently:

* **Packs.** Any typed topic becomes a checked knowledge pack: 4–10 concepts with key ideas,
  why-it-works, prerequisites, key facts and misconceptions, plus matched question pairs
  whose answer keys are verified before a learner can be scored on them — recomputed exactly
  for computational subjects, independently re-derived for everything else. Authored topics
  keep their authored keys. Packs are cached (encrypted) and built by a background job:
  `POST /api/v1/content/prepare {topic}` → `{job_id}`; `GET …/{job_id}` →
  `{state, pack_source}`.
* **Lessons written for the learner.** From a pack plus the learner's snapshot, the lesson
  writer produces a validated lesson: short explanation chunks, each followed by one check,
  every number traced to the pack's facts. Three moments share the machinery: the first
  lesson, the explanation after a wrong answer (which starts from the answer actually
  given), and "I don't get it", which re-teaches a different way every time, escalating
  through teaching memory. Every model proposal is code-validated; anything rejected falls
  back to the deterministic lesson, which is shown immediately and upgraded in place when a
  checked model lesson arrives ("Written for you" vs "Simple lesson" — the label is the
  truth about which one is on screen).
* **The AI proposes, code decides.** No model output reaches a learner unvalidated, no
  unverified item is used for scoring or placement, and learner text is data, never
  instructions. Model settings live only on the console.

## What each phase landed

| Phase | What landed |
|---|---|
| A | Public privacy list; developer wording out of learner copy; the banned-phrase crawl; the secrecy log-filter fix; closing prompts and MCQ hints by item kind; simple-mode key idea/why; honest contrast wording. |
| B | `adapt.content.skeleton`: the Pack contract, strict validation, item verification, the encrypted cache and prepare job, migration P3. |
| C | `adapt.ai.lesson_writer`: the three lesson moments, whole-plan validation, teaching memory, the contrast data endpoint, lesson block components. |
| D | The eight-stage journey shell and server state, preference de-duplication (the 13 questions asked once), placement consistency (eight questions everywhere), mobile. |
| E | `scripts/live_model_check.py`: the live-model harness (12 topics × 2 profiles × 3 moments + judge), scripted-fake fixtures, `MODEL_QUALITY_REPORT.md` template. **The live run has not been made** (see below). |
| F | Every baseline game and the subject round played in a real browser (desktop, mobile, keyboard-only), the defects that found fixed with backend tests, screenshots reviewed. |
| G | Integration: handoffs applied, the seams between B/C/D found and fixed, the frozen contracts proven over HTTP and in a full-journey browser run, the copy crawl and secrecy canary run on the integrated tree, docs reconciled. |

## The numbers (measured 2026-10-15, integrated tree)

* Backend: **1333 passed, 2 skipped** (`cd backend && pytest -q`).
* Frontend: contract lint green (116 backend routes against 102 frontend calls);
  `test:engine` 15, `test:lib` 42, `test:server` 2; production build compiles.
* Browser: full journey green on desktop and mobile (`e2e/journey-full/`); banned-phrase
  crawl green on 16 routes signed out and signed in; Phase F's games gate green twice in a
  row on both viewports plus keyboard-only.
* Teaching quality gate: `scripts/teaching_quality_eval.py` exits 0 — zero gate flags across
  273 deterministic questions; its model pass reports *not run*, because there is no key.

## What is not proven

The honest headline of the round: **nothing here has ever touched a real model.** Every
model-shaped behaviour above was verified with scripted fake models — in unit tests, over
HTTP, and in a real browser — because no API key has ever been configured on this build.
Whether a real pack's facts are right, whether real lessons read well, and what they cost
are unknown. `docs/UNVERIFIED.md` (round-2 section) lists this and the smaller items: the
crawl visits pages but does not play a subject round; first-lesson grading is learner
self-report by design; Phase E's sanitised live fixtures do not exist yet.

The owner's one-command-sequence for the live check is in `docs/round2/live-check.md`:

```bash
adapt config set-key     # hidden prompt; the key is never a command argument
adapt config test        # one minimal request: pass or fail
python scripts/live_model_check.py   # from the repository root; writes MODEL_QUALITY_REPORT.md
```

## Demo

`docs/round2/demo.md` is the three-minute version: the live path with a fake model, the
contrast screen, and the `?mock=1` fallback. `docs/DEMO.md` remains the round-1 script for
the placement → practice → proposals loop.
