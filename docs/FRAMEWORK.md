# The check-in framework: estimates with evidence, never labels

Round 9 adds a short "How is this going?" check-in after a test or lesson,
and on demand from Home. It takes about ten seconds, is offered at most once
per browser sitting unless the learner opens it, and exists for one purpose:
to let the *next* lesson be a better size. This document names the published
constructs behind each question, says exactly how the code uses them, and
states what the app does **not** claim.

Three rules hold for everything below:

1. **Estimates, not diagnoses.** A check-in describes *this sitting*, never
   the person. The app runs no clinical test, computes no health score, and
   never attaches a trait word ("anxious", "unmotivated") to a learner.
2. **Code owns the scales.** If a model is configured it may phrase a
   summary, but every value is validated against the code's enums, invalid
   fields are replaced by the deterministic mapping, the recommended action
   is always derived by code, and behavioural metrics are always computed by
   code from the learner's real evidence rows.
3. **The learner can see and delete it.** Check-ins are stored in the same
   encrypted local store as everything else, listed in Settings ("Your
   check-ins"), and deletable one at a time or all at once. The free-text
   note is stored encrypted, is never sent to a model, and is treated as
   data — never as an instruction.

## The constructs

### Mental effort — F. G. W. C. Paas (1992)

*Scale.* One question — "How much effort did that take?" — on Paas's 1–9
rating scale for mental effort, shown with plain words from "very light"
(1) to "very heavy" (9). It is the only load question; a multi-item
workload form (NASA-TLX) was deliberately not used, because it cannot be
answered honestly in the few seconds a check-in is allowed.

*Implementation.* `checkin._load_band` folds 1–3 / 4–6 / 7–9 into
low / medium / high cognitive load. Behavioural corroboration (slow,
retry-heavy, hint-heavy recent work) may raise the band one step; nothing
lowers a learner's own rating.

*Non-claim.* A self-rating of effort, not a measure of ability or of
"working memory capacity".

### Achievement emotions — Reinhard Pekrun control–value theory

*Scale.* One tap on the emotion that fits best, from Pekrun's achievement
emotions: enjoyment, boredom, anxiety, frustration, hope, pride. The
question is asked about the sitting that just happened ("How did it
feel?"), never as a description of the person.

*Implementation.* The chosen emotion seeds starting bands for frustration,
engagement and motivation; recent accuracy from real evidence may nudge a
band (for example accuracy under 40% raises frustration one step). The
primary emotion and a 0–1 intensity derived from effort are stored with
the metrics they were derived from.

*Non-claim.* One self-chosen word about one sitting. It is not affect
recognition and says nothing about the learner's mental health.

### Zone of proximal development and desirable difficulty — L. S. Vygotsky; R. Bjork

*Scale.* No question. Code keeps expected success near a 70–85% band
(`adaptation_policy.SUCCESS_BAND`): hard enough to teach, easy enough to
succeed often.

*Implementation.* `adaptation_policy` is a pure function. High load or high
frustration → a shorter lesson, smaller steps, an encouraging tone,
difficulty −1 (floor 1), and a suggested — never forced — break. High
engagement, high motivation, low load and success above the band →
difficulty +1 (ceiling 5) and the next concept. Otherwise nothing changes.
Both lesson routes verify the shaped lesson (length cap, difficulty clamp,
objectives preserved) and fall back to the unshaped lesson on any
violation. The learner sees the reason ("Made shorter because you said it
felt heavy") and can undo it for that lesson. Nothing about the learner's
persistent plan changes from a check-in; lasting changes still go through
the proposal-and-consent flow.

*Non-claim.* A sizing heuristic for the next lesson, not a placement test.

### "What would help?" — Self-Determination Theory (Ryan & Deci)

*Scale.* Three optional tiles mapping to autonomy, competence and
relatedness: "Let me choose what is next", "Give me a win first", "Work
through it with me".

*Implementation.* The choice is stored with the check-in and shown back in
"what we recorded". It does not silently change settings; it informs the
same consent-based flows as everything else.

*Non-claim.* A preference for this moment, not a needs profile.

### Retrieval practice and spacing — Dunlosky et al. (2013)

*Scale.* No question. Each lesson ends with a few practice items drawn from
the checked material, and the words on screen say why: trying from memory
is what makes it stick.

*Implementation.* Practice items are code-selected from the lesson's
material, cited to approved objectives, and scored server-side against the
item registry — the answer key never reaches the browser.

## What the estimate contains

`{primary_emotion, intensity 0..1, cognitive_load, engagement, frustration,
motivation: low|medium|high, recommended_action:
simplify|encourage|challenge|review|break, summary ≤ 140 chars}` — stored
next to the behavioural metrics it was derived from (`n_recent`,
`accuracy`, retries, hint rate, median time per item, slow-for-this-learner
flag) and the tiles the learner tapped. After a high-frustration check-in
the app may offer an optional "Read more" article link (see
[`CONNECTORS.md`](CONNECTORS.md)); it is reading, never a test or a score,
and its absence changes nothing.
