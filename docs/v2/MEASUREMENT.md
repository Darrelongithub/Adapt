# Measurement specification

What the engine is allowed to claim, on what evidence, and how it refuses.

The central rule: **a probe exists to improve a decision, not to produce a number.** If a construct
cannot beat the L1 baseline at predicting a learner's outcome, it is removed — even if the probe is
fun and the data is clean.

---

## 1. Units

| Term | Meaning |
|---|---|
| **Construct** | The thing being modelled, defined by what it predicts, not by the task. |
| **Instrument** | A versioned mechanic + item generator + scoring rule for one construct. |
| **Trial** | One scored response, with timing, response mode and validity flags. |
| **Evidence floor** | Minimum trials/observations before the engine may state a claim for a construct. |
| **Session chunk** | How much a learner does before a break is offered. Resumable across days. |
| **Claim** | A stated, falsifiable sentence with an interval and an evidence level. |

Time is not a unit of evidence. A learner who needs 30 trials gets 30 trials; the sitting is chunked,
the evidence is not capped.

---

## 2. Construct registry

Every construct is a row with these fields, and a construct cannot be used by an instrument until
all are filled:

```yaml
id: cmp_calibration
name: confidence calibration
definition: agreement between stated confidence and observed accuracy on the same task
predicts: [persistence after error, choice of study strategy, help-seeking]
does_not_mean: [self-esteem, humility, personality, honesty]
level_available: L2
construct_irrelevant: [reading load, unfamiliar mechanic, time pressure]
observables: [confidence tap 0-100, correctness]
scoring: Brier score + ECE, within-person, interval via bootstrap over trials
floor: {min_judgements: 20, min_sessions: 2}
stops_when: brier_ci_width <= 0.08 or judgements >= 60
prohibited_claims: [trait overconfidence, "poor self-awareness"]
```

`does_not_mean` and `prohibited_claims` are what stop the product drifting into personality
judgements. They are enforced: a claim whose template hits `prohibited_claims` cannot be emitted.

---

## 3. Evidence floors and adaptive stopping

| Construct | Minimum evidence | Stop early when | Never |
|---|---|---|---|
| Reasoning (matrix/series) | 12 trials | posterior SD ≤ 0.30, or 24 trials | report a score below the floor |
| Working memory (complex-span-like) | 16 trials, ≥2 sessions | split-half ≥ 0.70 | call it "your working memory" |
| Attention/inhibition | 40 trials | commission-error CI width ≤ 0.10 | compare across devices |
| Planning (move-limited puzzle) | 10 puzzles | stable over 2 sessions | infer executive function broadly |
| Spatial (mental rotation) | 16 trials | split-half ≥ 0.70 | claim 3D aptitude |
| Calibration | 20 judgements, ≥2 sessions | Brier CI width ≤ 0.08 | call overconfidence a trait |
| Strategy | 8 decision points | stable pattern over 3 sessions | label a "learning style" |
| Prompt/support level (cognitive_support) | 10 trials per level | 3 consecutive independent successes | infer ability from prompt use alone |
| Trial-level mastery (any profile) | 8 trials per target | criterion met twice across contexts | generalise from one setting |

**Session chunk:** 8–12 minutes of probe work, break offered (not enforced), fully resumable. An
optional 15–20 minute "assessment mode" may be offered for L3 constructs, clearly labelled as
assessment and never mixed with learning content.

**Abstention:** if the floor is not met, the response is

```json
{"error": "insufficient_evidence", "needs": {"trials": 7, "sessions": 1},
 "would_support": ["cmp_calibration"], "current_level": "L1"}
```

Not a low-confidence number, not a prior-based guess labelled as a measurement.

---

## 4. Validity checks at ingest

Recorded on every trial; never imputed; used to exclude or downweight.

| Flag | Rule | Effect |
|---|---|---|
| `rapid_guess` | response < 300 ms on a non-trivial item | excluded from accuracy estimates |
| `tab_hidden` | document visibility lost during the trial | session marked reduced-validity |
| `straight_line` | identical response across 8+ consecutive items | pattern flagged, not excluded blindly |
| `device_change` | input mode or viewport class changed mid-session | RT and latency metrics dropped for that session |
| `assisted` | help, prompt or third-party support observed | **recorded as data**; excluded only from independence metrics |
| `repeated_item` | same generated item served twice | deduplicated in scoring |

In `cognitive_support`, assistance is normal and expected. It is never treated as contamination of
ability — it is the variable being modelled (see `ADR-001`).

---

## 5. The validation gates

Six gates. An instrument passes them in order; failing any one removes it from the shipped set.

| Gate | Test | Pass criterion |
|---|---|---|
| 1. Reliability | ω ≥ 0.70; test–retest ICC ≥ 0.70 over 2–4 weeks; alternate forms r ≥ 0.70 | all three |
| 2. Construct validity | EFA/CFA on expected structure; convergent r ≥ 0.50 with an established measure; discriminant r ≤ 0.30 with unrelated constructs | pre-registered |
| 3. **Incremental validity** | does the construct improve prediction of *learning outcomes* over L1 features alone? | ΔAUC / ΔR² with CI excluding zero, on held-out learners |
| 4. Fairness | measurement invariance across device, input mode, language, education, gender, age band; differential prediction; accessibility audit | no non-invariance that changes decisions |
| 5. Engagement | completion rate, dropout point, time-on-task, abstention rate | completion ≥ 80%, no forced sessions |
| 6. Prospective calibration | pre-registered predictions scored after the fact; Brier/ECE with intervals | calibration within stated interval |

Gates 1, 2 and 4 need **hundreds of participants**. That is a research programme with a design
partner and ethics approval, not an MVP feature, and the plan must say so out loud rather than
quietly shipping probe scores while the validation is "in progress".

**Interim rule:** until a construct has passed gates 1–4, its instrument may run (to collect the
data) but its output may not influence `decide`, and the learner sees "this activity helps Adapt
understand how to teach you" — never a score.

---

## 6. Scoring rules

* **No norm-referenced ability scores.** No IQ-equivalents, no percentiles, no "mental age".
* Prefer **raw and ability scores**; norm-referenced scores are unusable at the extremes and the
  extremes are where our users are.
* **No IRT before N is large.** Below ~500 responses per item family, use ordinal/Bayesian mastery
  estimation with documented priors, and say which estimator produced a number.
* **Latency is a covariate, not a score.** Browser timing is not comparable to lab timing; reaction
  times are modelled within-person, never compared between people or devices.
* **Within-person always.** Every claim is about change in one learner, not position among others.
* **Every number carries an interval and an estimator name.**

---

## 7. `cognitive_support` measurement

Norm-referenced instruments floor out in intellectual disability: standardised ability tests bottom
out (floor scores far above the population's actual range), and language and motor demands
contaminate the score, systematically underestimating ability. Single-case studies with a handful of
participants cannot supply norms. So this profile measures something else:

| Measure | Definition | Why it is right here |
|---|---|---|
| **Independence level** | highest prompt level at which the learner succeeds | the outcome special education actually targets |
| **Time to criterion** | trials/sessions to reach a criterion on an individualised target | sensitive at any ability level |
| **Maintenance** | performance after 1–3 weeks without practice | distinguishes learning from momentary performance |
| **Generalisation** | performance with new materials, people or settings | the point of instruction |
| **Prompt fading rate** | change in required prompt level per session | the thing the engine is optimising |
| **Errors per acquisition** | errors before criterion | near-errorless procedures are the evidence base |

Design: **single-case design** — multiple baseline across behaviours, settings or participants;
repeated measurement; visual analysis plus randomisation test where feasible. The engine models
per-learner effects and reports replication counts, not group means.

Absolute prohibitions for this profile: no ability scores, no comparisons to norms, no diagnosis,
no "mental age", no cross-learner ranking, no population claim from a single case.

---

## 8. Synthetic learners

Before any real learner data, M2.5 ships **simulated learners with known ground truth**: fixed
mastery vectors, known method effects, known calibration bias, known prompt-fading curves, plus
adversarial cases (always-guessers, position-huggers, learners who improve only on familiar items).

Acceptance: the estimator recovers the generating parameters within stated error at stated N, and
*fails loudly* when it cannot. A model that reports confident nonsense on a simulated guesser is a
release blocker — the guesser is the cheapest possible test of the abstention rule.

---

## 9. Reporting contract

Every claim-bearing response carries:

```json
{"value": 0.68, "interval": {"low": 0.51, "high": 0.82, "mass": 0.95},
 "estimator": "shrunk_rate", "evidence_level": "L2", "n": 34, "sessions": 3,
 "validity": {"excluded_trials": 4, "reduced_validity_sessions": 1},
 "cites": ["clm_0142"], "not_a_diagnosis": true}
```

and every prospective claim is recorded before it is served, so it can be scored later. **A prediction
that was never recorded cannot be claimed as validated** — this is the single mechanism that keeps the
product honest over time.

One constraint follows from erasure and is easy to miss (`PRIVACY.md` §5): prediction-level records are
deleted with the learner, so calibration **cannot** be recomputed from stored history. It is aggregated
**incrementally at serve time and at outcome time** — running counts per (construct × profile × horizon
bin) — and only those non-identifying aggregates are retained. Two consequences: calibration cannot be
backfilled, and a bug in the tally is not repairable from raw data, only from new predictions. Both are
acceptable; silently losing the calibration history is not.

---

## 10. What would make me cut a probe

Any one of these:

* it does not beat L1 at predicting outcomes (gate 3);
* it is not invariant to response mode, so a learner with a different input method looks less able;
* learners complete it but the data is unusable (fast-guessing) — then the mechanic is wrong, not the learner;
* it only works when a facilitator is present, which makes it unmeasurable at scale;
* it needs a claim we have forbidden, to be worth running at all.
