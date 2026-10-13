# Agent 5 — Phase 6: evidence, method comparison, and the "why did I fail" loop

Master brief §9; agent brief in `docs/AGENT_BRIEFS.md` §Agent 5. Everything
below was run for real in this workspace; command output is quoted, and
anything that could not be verified here is in the pending list at the end
rather than described as done.

## What was built

### New files

| file | what it is |
|---|---|
| `backend/src/adapt/learning/failure.py` | The "why did I fail" loop, standalone-callable over plain data: `counterbalanced_assignment` (serve-time, seeded per learner, rotating with exposure), `fatigue_check` (the brief's exact rule), `select_prerequisite_question` (one question on the *direct* prerequisite, chain order, never transitive), `choose_reteach_method` (best measured actual-vs-expected, failed method excluded, seeded-random tie-break), `select_matched_question` (same format as question A or an honest "could not hold format fixed"), `attribution_for` (the update rule), `compare_attempts` (actual vs expected with Wilson range + n), `learner_sentence`, `weighted_method_summary`, `self_selection_detectors`, `strategy_posteriors` (Thompson behind the scenes, seeded; posterior mean + interval + n on the surface), `record_failure_outcome` (controlled evidence event, idempotent by event id). |
| `backend/src/adapt/api/routes/evidence.py` | `GET /api/v1/evidence/methods` (actual vs expected per method, topic-agnostic by default, `?topic_id=` narrows the same computation; per-method raw n, controlled/observational counts, effective weighted n, source; strategy posteriors; self-selection detectors; preference-vs-performance), `GET /api/v1/evidence/preference-vs-performance`, `POST /api/v1/evidence/failure-loop` (runs the loop for the signed-in learner against real bank items and the `TopicPrerequisite` chain, records the controlled evidence event). |
| `backend/tests/test_failure_loop.py` | 32 tests: every update-rule branch, both halves of the fatigue rule, tie-breaking, format-holding, weights, detector labelling, seeded posteriors, the visual detector's two designs, three simulations over synthetic learners, and the loop + both reads end to end over HTTP against the seeded bank. |
| `docs/EVIDENCE.md` | The evidence model as documented behaviour: assignment in code, same format, actual-vs-expected, 0.4 observational weight, the update-rule table, the second-exposure confound, "insufficient evidence". |

### Edits to owned / shared files

- `backend/src/adapt/learning/effect.py` (Agent 5-owned): additive only —
  `INSUFFICIENT_EVIDENCE_LABEL`, `verdict_label()`, and a `verdict_label`
  key in `Effect.as_dict()`. Below `MIN_GROUP_N` the verdict was already
  `insufficient_evidence`; it now also carries the learner-facing words
  "insufficient evidence" so no screen translates (or softens) it.
- `backend/src/adapt/learning/patterns.py` (**outside strict ownership —
  deviation, §9.1 required it**): `detect_visual_benefit` no longer claims
  its variants were "randomly assigned by the diagnostic" while one arm was
  the learner opening the visual. Following the pattern
  `detect_worked_example_need` already uses, it now reports the assigned
  comparison (`randomised`, weight 1.0, "assigned at serve time by
  learning.methods") only when both arms reached `min_n` as assignments,
  and otherwise the self-selected comparison stamped `observational`,
  weight 0.4, source named. The false claim exists nowhere else in the
  function, and `test_detectors.py` passes unchanged.

### Wiring deviations (frozen/integrator files touched, minimal)

1. `backend/src/adapt/api/app.py`: import + register `evidence.router`
   (same one-line-each pattern as the Agent 4 `lesson_plan` registration).
2. `frontend/src/lib/mock.ts`: two mock rows (`GET evidence/methods`,
   `GET evidence/preference-vs-performance`) so the demo-mode progress page
   renders the new card; all demo numbers carry the existing conventions.
3. `frontend/src/app/progress/page.tsx`: one added card, "What you said,
   and what happened" — preference-vs-performance (or the honest
   insufficient-evidence note), strategy posterior mean with interval and
   n, and the self-selection detectors labelled observational with weight
   0.4 next to the number. No new types file entries; types are local to
   the page.

## Note for the integrator: replacing `_reteach_step`'s diagnosis

`services/attempts.py` is shared, so per the brief this change is a note,
not an edit. `learning.failure.run_failure_loop` is callable standalone
(today it is already exercised over HTTP via `routes/evidence.py`). To
adopt it in the submit path:

1. In `_reteach_step`, keep the existing gathering (candidates, prereq
   graph, mastery) and additionally collect, for this session, the ordered
   `time_to_first_ms or time_on_item_ms` list and the matching `correct`
   list, plus the method history rows via
   `methods.rows_from_attempts` (all topics — the comparison is
   topic-agnostic).
2. Call `failure.run_failure_loop(learner_id=…, topic_id=…, first_item=item,
   first_method=effective.method, first_expected=float(expected),
   candidates=…, prereqs=…, prereq_candidates=…, prereq_correct=<result of
   the prerequisite question once asked>, session_latencies_ms=…,
   session_correct=…, method_rows=…)`. With `prereq_correct=None` it
   returns the plan (which prerequisite question, which reteach method,
   which matched question); re-call with the answers to get the comparison.
3. Record with `failure.record_failure_outcome(services.state, student_id,
   result, event_id=…)` — controlled source, attribution weight in the
   payload, idempotent.
4. Surface `result["learner_sentence"]` to the learner; it contains the
   result, range, n, and the second-exposure confound sentence whenever a
   comparison exists. `reteach.plan_reteach` can then be retired or kept
   as the no-prereq-edge fallback; `failure.py` does not depend on it.

Differences from the current `plan_reteach` the integrator should expect:
the prerequisite check is a *question* (not the <0.45 mastery threshold);
fatigue is the brief's rule (last two > 1.5× session median latency with
falling accuracy), not rapid responses; the update rule carries weights
0 / 0.5 / 1.0; tie-breaks are seeded-random.

## Gates (real output)

- `pytest tests/test_failure_loop.py tests/test_detectors.py tests/test_learning_math.py` →
  **89 passed** (32 new + detector/effect regressions).
- Full backend suite excluding the pre-broken `tests/test_baseline.py`
  (hangs on pristine HEAD; Agent 1's documented non-terminating
  play-through, out of scope here) → **840 passed, 0 failed** (64s).
- `npm run lint` in `frontend/` → green, including the route-contract
  check (89 backend routes against 53 frontend calls).
- No learner-facing string contains the internal component names; the new
  copy was checked against `test_learner_facing_copy_has_no_internal_component_names`
  as part of the suite run.

## Simulation results (the brief's gate)

- Synthetic learners whose visual method genuinely works (p=0.85 vs 0.45,
  expected 0.5, methods served by the real counterbalanced assignment):
  `method_report` identifies visual as best in ≥28/40 seeded trials —
  chance is 10/40. Asserted in `test_simulation_recovers_the_true_best_method_better_than_chance`.
- Synthetic learners missing the prerequisite: the miss is attributed to
  the prerequisite at weight 0 in 20/20 runs.
- Format-holding: over randomized candidate sets, whenever a comparison is
  made the matched question has the first question's format; when none
  exists the loop reports `format_held_fixed: false` instead of borrowing.

## Pending checks (not verified here — do not read as done)

- Browser click-through of the new progress card against a live signed-in
  session (Chromium cannot be installed in this sandbox; the card is
  covered by lint, the mock fixture, and the API tests).
- The `attempts._reteach_step` adoption above — deliberately left to the
  integrator per the ownership table.
- `tests/test_baseline.py` still hangs pre-existing; untouched.
