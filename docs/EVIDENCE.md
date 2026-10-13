# Evidence and failure recovery (Agent 5, phase 6)

## What is implemented

- Serve-time seeded method assignment, not an authored item property. Per-concept exposure
  sequences rotate through a seeded permutation. The optional **persisted `serve_ordinal`**
  counterbalances a whole comparison run; `balanced_probe` supplies it. Reloads use the saved
  assignment/ordinal, not a new draw. Approved adaptations and reteach are observational.
- A comparison probe keeps one question format throughout; unavailable formats shorten the
  probe rather than silently change the answer format. `method_delta` refuses incompatible
  format sets. Matched failure questions require the same exact kind and difficulty.
- Reports compare actual with the frozen **pre-exposure predicted** success, including the
  learner model, question-format guessing and item difficulty. Topics are not filtered:
  `/api/v1/evidence` accumulates outcomes over all topics and sessions for the signed-in learner.
- Controlled assignments have source weight **1.0**; observational evidence has **0.4**.
  Sources, weights, raw `n`, weighted `n`, ranges and insufficient-evidence labels travel with
  the numbers. Legacy visual openings no longer claim random assignment.
- Strategy estimates use the actual Beta posterior mean `alpha / (alpha + beta)`, with `n`
  and an approximate interval. `StrategyModel.sample(rng=...)` keeps seedable Thompson sampling
  off screen. A zero-weight observation does not move a posterior or its count.
- The new `/evidence` page shows stated preference alongside **real adjusted** outcomes, method
  ranges and weights, strategy estimates, and a resumable recovery check. It does not invent
  comparisons when fewer than two methods have eight effective observations, or when their
  format sets differ. Teaching reuses the existing segmented/example/visual renderer.

**Integration status:** this is a parallel workstream, not a claim that production wiring has
been merged. The router is wired only by the HTTP test fixture. The production registration,
normal-attempt outcome hook, replacement of `_reteach_step`, content-engine schema mapping,
model orchestration and navigation edits are in `agent-notes/5-evidence.md` for the integrator.

## Failure cycle

`FailureLoop(state)` is callable without an HTTP server or AppServices. Its state is derived
from append-only `failure_transition` events in the existing encrypted learner store.

1. Start with a server-graded failed teaching attempt (not placement), its frozen prediction, checked A/B pair and
   available teaching variants. HTTP callers provide **only an attempt ID**: not a grade,
   method, expected score, prerequisite or question key.
2. If a chain has a direct prerequisite, serve **one real question** on it. A missing question
   refuses recovery; low stored mastery is not substituted for a prerequisite check. For
   multiple direct prerequisites, the HTTP adapter checks the lexically first one; this is
   a one-question check, not a claim to have eliminated every possible prerequisite gap.
3. Flag fatigue only when there are at least four usable chronological session answers,
   both final latencies exceed **1.5 times the session median**, and their mean accuracy is
   lower than the preceding answers. Both actual final answers must have timing data; missing
   final times never substitute an older pair. Constant accuracy cannot trigger it. The HTTP
   adapter cuts history off at A, so opening an old receipt cannot attribute later fatigue to it.
4. Reteach the **same sub-concept** using the best adequately measured alternate method.
   Ties (including no data) use seeded pseudorandom selection, not catalog order. Ask the
   explicit matched B question; do not substitute an arbitrary nearby-difficulty item.
5. Grade B in code; record its grade and both method outcomes atomically with the completed
   transition. Replays and device resumes read the same log; they do not repeat updates.
   Conflicting simultaneous B submissions resolve to one consistent winning grade/outcome.
   The source model version and content IDs are frozen in the transition and returned in `why`.

### Attribution versus evidence source

The diagnostic rule is a **multiplier**, separate from the evidence's source weight:

| Diagnosis | Attribution multiplier | Observational source weight | Effective method weight |
|---|---:|---:|---:|
| Prerequisite check fails | 0 | 0.4 | 0 |
| Fatigue, prerequisite passes | 0.5 | 0.4 | 0.2 |
| Prerequisite passes / topic has none | 1 | 0.4 | 0.4 |

Prerequisite failure is logged on the prerequisite topic and does not penalise either method.
Fatigue suggests a break. "Full weight" means the full source weight, not relabelling a second
exposure as a controlled assignment. The first failed A's **method** update is deferred until
attribution is known (knowledge scoring is separate). Do not feed it into the ordinary method
hook first and then again as part of recovery.

Every recovery says: **stronger evidence than a self-selected opening, but not a controlled
experiment, because a second exposure can help on its own**. A single pair shows `n=1`,
"insufficient evidence" and a deliberately conservative possible residual-difference range
`[-2, 2]`. It is **not** a fabricated narrow confidence interval from a one-pair bootstrap.
Accumulated method summaries show conservative Wilson ranges shifted by fixed predictions,
using weighted n; strategy intervals use a bounded normal approximation to the Beta posterior.
Neither is a causal effect estimate. Existing `learning/effect.py` bootstrap evaluation remains
and explicitly says **Insufficient evidence** below its own minimum n.

## Content handoff

The router accepts only registry rows with `validation == "validated"`. The frozen
`content_for` returns metadata, so it uses those IDs to read private `ContentPack.payload`
server-side. No private answer data is returned to the browser. Malformed payload containers are refused
as unavailable content; a validation flag does not bypass structural question checks.

The content-engine adapter must expose this private contract (map the phase-4 engine's schema
rather than manufacturing a B question):

```python
{
    "matched_pairs": [{
        "a": {"id": "original-bank-item-id", "topic_id": "topic-id",
              "concept_id": "sub-concept-id", "pair_id": "pair-id", "pair_role": "A",
              "kind": "numeric", "difficulty": 0.5, "stem": "...",
              "answer_spec": {"value": 4}},
        "b": {"id": "distinct-B-id", "topic_id": "topic-id",
              "concept_id": "sub-concept-id", "pair_id": "pair-id", "pair_role": "B",
              "kind": "numeric", "difficulty": 0.5, "stem": "...",
              "answer_spec": {"value": 6}}
    }],
    "variants": {
        "explanation": {"kind": "segments", "steps": [...]},
        "worked_example": {"kind": "worked_example", "steps": [...]},
        "visual": {"kind": "visual", "visual": {...}}
    }
}
```

This example describes a schema, not teaching content to seed into production. Computational
or source-quotation verification remains the content validator's job. Pair checking here is
an additional structural guard. The HTTP adapter verifies A's rendered kind, stem, answer key
and difficulty against the original bank question using the existing learner variant seed.
A/B may have different numerical values; each grading key must agree with its rendered stem.
A missing or unvalidated pack returns 422 honestly; it never falls back to a fake comparison.

## API (after integrator registration)

- `GET /api/v1/evidence`: weighted method/strategy summaries and preference row.
- `POST /api/v1/evidence/failure`, body `{ "attempt_id": "..." }`: start/resume.
- `GET /api/v1/evidence/failure/{attempt_id}`: resume the server-side check.
- `POST /api/v1/evidence/failure/{attempt_id}/answer`, body
  `{ "question_id": "...", "answer": "..." }`: code grades the current question.

Authentication isolates learners. Mutations require CSRF and the existing rate limiter.
Unowned resources return 404; refused inputs return structured 422 errors. Question output
is whitelisted; MCQ choices expose only text, never correct flags or answer keys.

## Verification and pending work

See the agent report for measured commands/results. Tests cover counterbalancing, strict format
matching, all attribution branches, exact fatigue conditions, seeded tie breaks, posterior means,
source weights, persistence/replay, known-behaviour simulations, real HTTP grading, CSRF and
learner isolation. No test is skipped, loosened or deleted by this change.

**Browser and screenshots are pending.** No Chromium executable is installed here. After merging
all six workstreams and the integration edits:

1. Start API and frontend (`uvicorn adapt.api.app:app --host 0.0.0.0 --port 8000` and
   `npm run dev` in `frontend`), sign in, and open `/evidence`.
2. Verify the empty/insufficient state; complete a real controlled probe with the question format held
   fixed across two methods; verify source weights, intervals and stated preference. Seeded data must say seeded.
3. Miss a checked A item. Enter its receipt ID (or follow the integrated practice link).
   Fail/pass the prerequisite in separate runs; simulate fatigue with six normal responses
   then two slow wrong responses; check every multiplier and break suggestion.
4. Inspect the alternate teaching (especially visual diagrams), submit B, and verify `n=1`,
   the wide range and second-exposure caveat. Reload and sign in on a second device mid-check.
5. Reject an unchecked pack, wrong-format B, wrong attempt ID and unowned loop. Verify calm
   error copy, keyboard use and no duplicated submissions. Capture desktop and mobile screenshots.

### Follow-up verification

The phase-6 test file now contains **59 passing cases**, including malformed-content refusal,
missing/recent timing edge cases, retrospective history cutoff, conflicting concurrent answers,
placement exclusion and frozen provenance. The regression run excluding the previously documented
nonterminating baseline file passes **784 tests**. The unrestricted suite is not claimed green.

Five additional **React SSR** tests enforce weighted-n badges, adjacent source/range copy, honest
missing-measurement state and pilot labelling. SSR is not a browser interaction test. No dependency
was added. `npm run test:lib` now passes 13 tests; engine/server gates, lint and build also pass.

Reports count invalid rows as `excluded_n` rather than producing nonfinite or fabricated estimates.
Valid outcomes attributed to a prerequisite instead are counted as `attributed_elsewhere_n`.
Self-selection detectors are observational by default; an assigned design must be explicit.
Legacy method-difference ranges use interval endpoints and evidence labels use weighted n.

No live model call, teaching-quality claim or browser behaviour is claimed verified here.
