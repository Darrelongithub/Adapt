# Phase C report — lesson writer, teaching memory, contrast (reconstructed)

> **Why this file says "reconstructed".** Agent C finished and merged its work as pull request
> #24 (`Round 2: AI 3 lesson writer, teaching memory, contrast & journey updates`, branch
> `arena/7efe7537-adapt`, commit `e60f08b`, merge commit `13c6bbe`, merged 2026-10-15 04:07 UTC),
> but its session ended without committing `docs/round2/report-c.md`. Phase G (the integrator)
> reconstructed this report from the merged branch itself — the PR file list, the PR description,
> and re-verification runs on `main` — so the six-report set for Round 2 is complete. Every claim
> below is labelled with its source: **[verified on main]** means Phase G re-ran or re-read it on
> the integrated tree; **[PR #24]** means it is Agent C's own claim from the pull request, which
> Phase G did not independently reproduce.

## What landed

All from PR #24's file list and description.

- `backend/src/adapt/ai/lesson_writer/` — the frozen contract: `write_lesson(pack, concept_id,
  snapshot, *, model=None) -> LessonPlan`, `explain_after_wrong(...)`, `reteach(...)`. Each always
  returns a strictly validated result or a deterministic fallback — never a half-repaired model
  proposal. **[verified on main]**: the package exists and exports all three functions;
  `core.py` is 2,809 lines.
- Strict whole-plan validation: the seven block types, per-block `fact_ids`, evidence-based `why`
  (ids + n), length budgets enforced without truncation, a one-question check after every
  explanation chunk, visuals as validated JSON specs, fact support for numbers/named entities,
  final-answer-equals-key, no answer leakage before an attempt, no markup/links/off-platform
  instructions, provider/AI-named text rejected. **[PR #24]**
- The three moments: initial lesson; misconception-specific wrong-answer response starting from
  the learner's actual answer; code-chosen re-teach with teaching-memory escalation
  (simpler words → smaller step → example → visual), recording method + analogy so retries differ.
  **[PR #24]**
- Teaching-memory persistence and migration `C1_lesson_writer_memory` (method, analogy_id,
  moment). **[verified on main]**: `backend/migrations/versions/C1_lesson_writer_memory.py`
  exists, chained `0010 → C1`.
- Routes under `/api/v1/lesson/*` registered in the app, plus the contrast data endpoint
  `POST /api/v1/lesson-plan/contrast`, which serves real checked AI 3 output when a key exists
  (labelled "written for you") and template lessons otherwise. **[verified on main]**:
  `routes/lesson.py` and `routes/lesson_plan.py` are in the router registry in
  `backend/src/adapt/api/app.py`; the contrast handler is in `lesson_plan.py`.
- Model context is filtered: allowlisted profile/memory fields only, unsafe learner text withheld,
  initial lessons never receive answer keys. **[PR #24]**
- Frontend: `frontend/src/components/lesson/LessonRenderer.tsx` renders `LessonPlan`/`Explanation`
  blocks; contrast, settings and voice pages updated; the banned-phrase crawl extended.
  **[verified on main]**: `LessonRenderer.tsx` exists.

## Verification

| Check | Result | Source |
|---|---|---|
| Backend suite, whole, on C's merged branch | 1138 passed, 1 skipped | **[PR #24]** |
| Focused adapter + lesson-writer + migration suite on C's branch | 128 passed | **[PR #24]** |
| `pytest -q tests/lesson_writer` on integrated `main` (pre-seam-fix) | 33 passed, 2 errors — both errors are the two-head Alembic clash created *after* C merged (Phase B's `P3` against `0009→0010→C1`), not lesson-writer failures; they clear once Phase G re-points the heads | **[verified on main]** |
| Real-model runs | none — no key configured | both |

## Known gaps (carried forward)

- **Browser screenshot review was not done.** Chromium was not available in Agent C's environment
  (stated in PR #24's "Known gap"). Phase G covers the lesson screens in the full-journey browser
  run instead.
- No live model output was ever produced or judged; all model behaviour is proven only against
  scripted fakes. The live run belongs to the owner (`docs/round2/live-check.md`).
