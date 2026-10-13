# Evidence: how ADAPT knows what works (Phase 6)

This document describes the evidence machinery behind the method comparison
and the "why did I fail" loop, as implemented in
`backend/src/adapt/learning/failure.py`, `backend/src/adapt/learning/effect.py`,
and `backend/src/adapt/api/routes/evidence.py`. The rule of the house: every
number a learner sees is a measurement with an `n`; every estimate carries a
range; nothing below its minimum `n` is called anything but what it is.

## 1. Assignment happens in code, at serve time (§9.1)

Which teaching method a concept is taught with is decided by
`failure.counterbalanced_assignment` (and `learning.methods.assign_method`):
seeded per learner, rotating with exposure, so two learners meet the same
concept in different orders and one learner cycles through every method.
Nothing about the *item* chooses. An authored label on content cannot run an
experiment, so there is none.

`patterns.detect_visual_benefit` previously claimed its variants were
"randomly assigned by the diagnostic" while one arm was defined by the
learner opening the visual. That claim was false and has been removed: the
detector now reports the **assigned** comparison (both arms assigned at
serve time, design `randomised`, weight 1.0) when both arms have enough
assigned rows, and otherwise the **self-selected** comparison, stamped
`observational` with weight 0.4 and its source named. Comments and facts now
describe the design that actually produced the rows.

## 2. Same format across methods (§9.2)

A method comparison that changes the answer format along with the method
measures the format. The loop therefore asks a matched question B of the
**same format** as question A (`failure.select_matched_question`, closest
difficulty, never A itself). If no same-format question exists, the loop
says so (`format_held_fixed: false`) instead of quietly borrowing a
different format, and the comparison is not counted. A unit test
(`test_simulation_same_format_is_held_across_methods`) asserts the format is
held whenever a comparison is made.

## 3. Actual vs expected, accumulated across topics (§9.3)

Per method, the number is **actual minus expected**: what the learner did,
minus what the model predicted for that exact question before it was
served. Raw accuracy is not shown, because a method that met harder
material would be punished for the material. Rows accumulate across all
sessions and topics; `GET /api/v1/evidence/methods` computes exactly that by
default ("all topics") and narrows the identical computation with
`?topic_id=`. The Thompson-sampled strategy model stays behind the scenes
(seeded, so a demo can reproduce its draw); surfaces receive the posterior
mean with its interval and `n` — never the draw alone.

## 4. Self-selection is labelled, weighted 0.4, and never proof (§9.4)

Detectors that watch what the learner *chose* (opened an example, opened an
explanation, opened a visual) are kept, because the observation is worth
having. But the learner who opens help is usually the learner who was
stuck, so the comparison is confounded by construction. Every such payload
carries `design: "observational"`, its source ("self-selected: the learner
chose to do this"), `weight: 0.4` next to 1.0 for assigned evidence, and
`is_proof: false`. Weighted summaries show the raw `n` beside the
effective (weighted) `n`, so a discount is never hidden.

## 5. The "why did I fail" loop and its update rule (§9.5)

On a failed attempt (`failure.run_failure_loop`):

1. **Prerequisite check** — one question on the *direct* prerequisite,
   using the chain. A question, not a mastery guess.
2. **Fatigue check** — the last two answers each took more than 1.5× the
   session median latency *and* accuracy is falling. Slow-and-accurate
   does not fire it; fast-and-wrong does not fire it; fewer than four
   timed answers cannot fire it.
3. **Reteach + matched B** — the same sub-concept with the next-best
   method by measured actual-vs-expected (the failed method excluded;
   ties broken by a seeded random draw, reproducible per learner), then a
   same-format question.
4. **Compare** — first vs second attempt, actual against expected under
   each method, shown with a Wilson range and the `n` behind it.

The update rule — what the failure is allowed to blame:

| diagnosis | attributed to | method-evidence weight | break suggested |
|---|---|---|---|
| prerequisite check failed | the prerequisite | **0** — no method penalty | no |
| fatigue fired | fatigue | **0.5** | yes; reteach deferred until after it |
| neither | the method | **1.0** | no |

Every outcome is recorded on the evidence log as a `controlled` event
carrying the attribution weight (`failure.record_failure_outcome`,
idempotent by event id), and the learner sees one sentence with the result,
the range, the `n`, and — whenever a reteach comparison exists — the
honest caveat that is part of the mechanism, not the copy deck:

> this is stronger evidence than watching alone, but it is not a controlled
> experiment: the second question was your second time meeting this idea,
> so some improvement would be expected either way

(the *second-exposure confound*, `failure.SECOND_EXPOSURE_CONFOUND`).

Standalone by design: the loop takes plain data and returns plain data, so
tests drive it directly. Wiring it into `services/attempts.py` in place of
the thinner `_reteach_step` diagnosis is specified step-by-step in
`agent-notes/5-evidence.md` (attempts.py is integrator-owned).

## 6. Preference vs performance (§9.7)

The progress screen shows what the learner *said* at baseline next to what
was *measured*: `baseline.factors.preference_vs_performance` fed with real
attempt outcomes grouped by format (`GET /api/v1/evidence/preference-vs-performance`,
also embedded in `GET /evidence/methods`). When there is not enough
measured data on both sides, the surface says "insufficient evidence" and
shows no verdict — inventing one is the failure this section exists to
prevent.

## 7. Below minimum n: "insufficient evidence" (§9.8)

`learning.effect.evaluate_change` has always returned the verdict
`insufficient_evidence` below `MIN_GROUP_N = 5` per side; Phase 6 adds the
learner-facing words for it (`effect.verdict_label` →
"insufficient evidence") on the payload, so screens show the phrase rather
than translating — or softening — it themselves. The same phrase appears in
the failure-loop comparison (`verdict_label`) below the evidence bar.

## Tests

`backend/tests/test_failure_loop.py`: every update-rule branch, both
halves of the fatigue rule (and each half failing alone), tie-breaking,
format-holding, weights, detector labelling, seeded posteriors, and three
simulations over synthetic learners of known behaviour — a learner whose
visual method genuinely works gets it identified in ≥28/40 trials (chance
is 10/40); a learner missing the prerequisite gets the miss attributed to
the prerequisite every time. The loop and both evidence reads are also
exercised end to end over HTTP against the seeded bank.
