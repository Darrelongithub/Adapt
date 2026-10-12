# Review — where the code and the claims disagree, and what to add next

Scope: a first-pass review of `backend/` and `docs/`, read against the project's own
promises (README, `docs/UNVERIFIED.md`, `docs/AUDIT.md`, module docstrings). It is an
*advisory* pass, not a rewrite: section 1 lists defects I fixed because they were
unambiguous bugs, section 2 lists defects I only measured, sections 3–7 are suggestions
ranked by value per unit of work. Every number below was produced in this checkout and
is reproducible with the command shown.

Baseline before this pass: **185 passed** in 22 s. After: **195 passed**, `node
scripts/check-contract.mjs` clean.

---

## 1. Fixed in this pass (with the measurement that proves it)

### 1.1 The estimator and the runtime filter were two different models (`learning/bkt.py`)

`update()` implements *updated*-BKT: a correct answer moves `p_know` through the learning
transition, a wrong one only decays it (the docstring at `bkt.py:124` explains why — the
classic rule makes `p_know` rise on failure, 0.200 → 0.224, so repeated failure reads as
progress). `fit_em` implemented the classic rule: the transition applied after **every**
step, conditioned on nothing.

So `train_params` fitted a process the app never runs, and the numbers went straight into
`information_gain()` (which picks the next question) and `p_correct()` (which tells a
learner what they are likely to remember).

Measured on data generated *by this repo's own* `update()`, 400 sequences × 25 steps,
true `p_learn = 0.30`:

| | old (`fit_em` default) | fixed (`model="online"`) | truth |
|---|---|---|---|
| `p_init` | 0.2819 | 0.2426 | 0.25 |
| **`p_learn`** | **0.0503** | **0.3109** | **0.30** |
| `p_guess` | 0.1153 | 0.1594 | 0.15 |
| `p_slip` | 0.0803 | 0.0834 | 0.08 |
| `p_forget` | 0.0035 | 0.0386 | 0.02 |
| log-likelihood | −4045.54998 | −4045.49952 | — |
| converged in | 20 iters | 14 iters | — |

The last two rows are the point. The two fits disagree by a factor of six on the parameter
that drives scheduling — and their log-likelihoods differ by 0.05 out of 4045. On this data
the models are observationally near-equivalent, which is exactly the identifiability warning
the BKT literature has carried since Beck & Chang: a fit can be healthy, converged, and
reported with a good log-likelihood while a different parameter set explains the same
evidence just as well. Nothing in the output you would normally look at tells you which
rule produced it; only generating data from *your own* filter and asking the estimator to
find it back does.

What `p_learn = 0.05` means in the product: the scheduler believes a learner who answers one
question right has barely learnt anything, so it re-asks material they know
(`ADAPT_RETENTION_TARGET=0.85` is never met quickly) and `information_gain` keeps favouring
items the learner has already mastered.

`fit_em(..., model="classic")` is still available and documented; it is now an explicit
choice rather than the default. The two models are not bit-identical under the online rule
because the runtime chain clamps each step to `[0.001, 0.999]` and the EM forward pass does
not: forward-LL −15.2016422921 vs `sequence_logprob` −15.2016422465, a difference of
4.5e-8. `backend/tests/test_learner_model_consistency.py` pins that agreement at 1e-6 and
pins the size of the mistake you get by mixing the rules (`classic.p_learn < 0.6 × truth`,
`online.p_learn > 2 × classic.p_learn`).

### 1.2 The cohort's observation count was being read as the learner's (`bkt.py:band`)

`band()` decides between `new` / `gapped` / `developing` / `proficient` / `mastered` from
`self.n_observations`, and `params_for` populates that field from `BktParams.n_sequences`
— the *cohort* count. After one `train_params` run, every learner therefore has
`n_observations ≥ 400`, `band()` takes its "this learner has evidence" branch, and a topic
the learner has never been asked about reads `gapped`.

That is the opposite of what the README promises (*"a cold start prints `new`, never
`low-ability`"*) and it is the single most user-visible thing in the app: the dashboard
tells a new learner they have gaps in topics they have not met. The same bug let one wrong
answer on a fresh topic be reported as a skill gap, which is a claim about a person made
from a sample of one.

`band(p, *, proficient, developing, observations: int | None = None)` now takes the
*learner's own* count; both AI 1 call sites pass it (`state.n_obs`, `len(items)`), and
`MIN_OBSERVATIONS_FOR_GAP = 2` makes the intent explicit in the code. Callers with nothing
better to hand keep the old behaviour, so nothing else changed meaning. Pinned by
`test_a_topic_the_learner_has_never_been_asked_about_is_not_a_gap` and an end-to-end check
through `POST /api/v1/attempts` → `GET /api/v1/profile`.

### 1.3 `train_params` wrote only the row that is a fallback (`ai/ai1.py`)

`params_for` reads `s.get(BktParams, ("global", topic_id))` **before** the
`("global", "*")` row — a per-topic branch that nothing ever wrote, so it was dead code
and every learner was traced with one cohort average over the whole bank. Per-topic guess
and slip are the parameters that actually differ between skills; a single average can only
be right by accident.

`train_params` now fits per-topic rows (from the cohort estimate as the starting point,
`min_sequences_per_topic=12`), refuses degenerate fits (`bounds_ok`), deletes topic rows
that no longer have the data to justify them, and reports `topics_fitted`,
`min_sequences_per_topic` and `topics_kept_on_cohort`.

The write also never reached anyone who had already answered. `params_for` short-circuits to
the *stored state's* parameters whenever `n_obs > 0`, so the fit only ever applied to
learners who had not practised yet — precisely the ones with no data to fit on. Existing
traces are now re-pointed at the fit in force while their `p_know` and `n_obs` are left
alone: the parameters are the filter's, the belief is the learner's. History stays coherent
because anything that reads a trace replays it under the current parameters.

### 1.4 The diagnostic was not reproducible across restarts (`assessment/selector.py`)

`random.Random(hash(seed or "diagnostic"))` — and Python salts `str` hashing per process.
Three processes, one seed, three different plans:

```
PYTHONHASHSEED=1  itm6_0,itm5_0,itm1_0,itm0_0,itm7_0,itm2_0,itm3_0,itm4_0
PYTHONHASHSEED=2  itm1_0,itm2_0,itm3_0,itm4_0,itm5_0,itm7_0,itm6_0,itm0_0
PYTHONHASHSEED=3  itm3_0,itm6_0,itm1_0,itm5_0,itm7_0,itm4_0,itm0_0,itm2_0
```

The documented promise is "randomised per learner, reproducible for the same learner"; the
real path seeds with `student_id` (`ai2.py:519-521`), so a learner who reloads the page
after a deploy gets a different placement test, and any debugging story ("why did this
learner get these items?") is unreproducible by construction. `hash()` is also not a seed
scheme — it is a 64-bit value modulo 2^32 here, and it varies by machine as well as by run.

`stable_seed(*parts)` (BLAKE2b, 8 bytes) replaces it in `diagnostic_plan` and `next_items`.
Pinned by a subprocess test that runs the same plan under three `PYTHONHASHSEED` values.

### 1.5 The ordering rule in the diagnostic could not match its own keys

The pool is keyed by `topic["id"] ;` the ordering looked the difficulty up as
`t.get("code") or t.get("id")`. Those are different strings in the bank (`top_algebra` vs
`T1`), so unless a topic's code happened to equal its id, the lookup matched nothing, every
topic scored the 0.5 fallback, and "easiest topic first, one item per topic" silently
became "whatever order the query returned". Fixed by remembering difficulty under the same
key the pool uses; pinned by `test_the_diagnostic_prefers_the_easier_topic_when_the_id_and_the_code_differ`.

### 1.6 Interleaving truncated the session instead of ordering it

`_interleave` `break`-ed out of its loop when the repetition budget ran out, and the
caller's `count` is a promise to the learner. Measured: one topic, 12 items,
`count=11`, `ratio=0.4` → **6 items returned**. The budget is a preference about
ordering, not a filter; the remaining picks are appended in utility order now, and the
regression test asks for 11 and asserts 11.

### 1.7 The selector optimised against a number the model does not believe

```python
expected_success = p_know * (1 - 0.45 * difficulty) + 0.05
```

No `p_guess`, no `p_slip`. For an item at difficulty 0.5 this ignores the model's own
answer, and at `p_know = 0` it claims 5% where the trace knows a non-knower still has a
`p_guess` chance (20% by default). Two different numbers for one question is not a
rounding issue, it is a second model. `expected_success_rate()` now applies a Rasch-style
log-odds shift to `params.p_correct(p_know)`: difficulty 0.5 leaves the prediction exactly
alone, the shift can never leave `(0, 1)`, and a corrupted `p_know` cannot produce a nan.
`DIFFICULTY_LOG_ODDS = 1.0` is deliberately gentle because `difficulty` is an author's
prior, not a calibrated parameter (`docs/UNVERIFIED.md` says so) — it may nudge the target,
it must not dominate it.

### 1.8 Cheap cleanups

`strategies.py:282` used `Any` in an annotation without importing it (fine at runtime only
because of `from __future__ import annotations`; it would fail for any
`typing.get_type_hints()` consumer). `api/deps.py` imported `Header` unused;
`services/sessions.py` imported `PREFERENCE_STRATEGIES` purely to re-export it and nothing
imports it from there; `resources/providers.py` used `l` twice (E741). All four fixed;
`ruff check backend/src --select F,E7,E9,B017` is now clean. The remaining 967 diagnostics
are dominated by E501 (line length) — a formatting decision, not a correctness one.

---

### 1.9 Every LLM prompt path raised before it reached the model (`ai/ai1.py`, `ai/ai2.py`)

`redact_payload` returns `(sanitized, dropped, kinds)`. **All five call sites unpacked two:**

```python
safe, dropped = redact_payload({"q": user}, max_chars=7000)
```

so the first request through AI 1's proposal path, the tutor reply, the teach-back commentary
or generated practice would have raised `ValueError: too many values to unpack (expected 2)`.
Nothing caught it because `conftest.py` pops `ADAPT_GEMINI_API_KEY` and the app falls back to
local rules — the right product decision and the wrong testing one: it meant no test ever
entered a path that five call sites were standing in front of.

`tests/test_llm_plumbing.py` now guards it structurally (an AST walk asserting every assignment
from `redact_payload` unpacks three values) and exercises the teach-back path with a stub model.
The kinds reach the log as well: `LlmCall.redacted_fields` counted removals with no record of
what they were, which is what a count with no shape buys you.

### 1.10 `alembic upgrade` wrote a plaintext database (`migrations/env.py`)

```python
connectable = engine_from_config(..., url=str(_engine.url))   # a URL carries no driver
```

The migration ran on SQLAlchemy's stock `sqlite3` module while the app opens the same file with
SQLCipher. Measured with the app's actual default (`ADAPT_DB_ENCRYPTION=sqlcipher`):

* `upgrade head` on a fresh data directory produced a **plaintext** file, and the app's next
  `connect()` raised `sqlcipher3.dbapi2.DatabaseError: file is not a database`
  (`db/engine.py:128`, at `PRAGMA journal_mode = WAL`);
* against a database the app had already created, the command failed the same way.

So the migration path was unusable in both directions, for every installation using whole-file
encryption, while the comment above it said the cipher and keyring were set up on purpose.

The existing migration tests could not have caught it: they run in the suite's
`ADAPT_DB_ENCRYPTION=field-only` configuration and read the migrated file with plain `sqlite3`
— correct for that configuration, and silently an assertion that the database *is* plaintext.
`tests/test_migration_encryption.py` runs the three operator commands (`upgrade head`; stamp an
existing `create_all` database then upgrade; downgrade and re-upgrade) with whole-file
encryption and reads the result through `create_db_engine`. Reinstating the old construction
fails all three.

## 2. Found, measured, deliberately **not** changed

These change behaviour or thresholds, which is the user's call, not a review's.

### 2.1 The detector gate contradicts its own catalogue

`patterns.py` documents three gates: `min_n = 4` per side, an effect floor of
`|Δ| ≥ 0.12`, and `strength ≥ 0.25` where strength is the gap divided by the width of a
Wilson interval around the group's own proportion (× `min(1, (n_a+n_b)/24)`).

Wilson intervals are widest near p = 0.5 and narrowest near the extremes, so the third gate
silently dominates the other two. A 12-point gap — the documented minimum effect — at
`n = 6` per side gives `0.099` and is suppressed. Measured:

| n per side | strength (0.62 vs 0.50) | fires? |
|---|---|---|
| 6 | 0.099 | no |
| 10 | 0.193 | no |
| 50 | 0.458 | yes |
| 200 | 0.892 | yes |

So the live failure mode is **small-n suppression**, not large-n false positives: with
`min_n` passed as `max(3, min_evidence // 2)` = 3 by `ai1.py:677`, the catalogue advertises
"4 is enough" while the arithmetic needs ~20 per side for the smallest effect worth
reporting. Worse, the same value of Δ produces different "strength" depending on how far
the group's mean sits from 0.5, which contradicts the module docstring's claim that
strength is "a function of both" effect and evidence.

Recommended, in order of preference:

1. Make the gate the thing you can state out loud: fire when
   `|Δ| ≥ max(0.12, z * se(difference))` with a two-proportion Wilson interval on the
   *difference* and `z = 2`, and report `n` alongside strength. One rule, monotone in
   evidence, and the constant is defensible.
2. Or keep `_strength` and raise the documented `min_n` to what it actually implies, so the
   catalogue tells the truth.
3. Or report `strength` as a percentile of a bootstrap null so it has a frequency reading.

### 2.2 The hot path is clean, but only because callers cap the window

I went looking for an O(n²) before writing anything about performance and did not find one:
`spacing.fit_half_life` over the last 80 rows takes 0.07–0.08 ms whether the topic's total
history is 20 rows or 1200, because every caller passes a bounded window. That cap is
load-bearing rather than incidental — the ridge fit is linear in the rows handed to it, so
the first caller that passes a term's worth of attempts turns a per-answer refit into a
per-answer cost that grows all year. Worth a comment at the call site (or a cap inside
`fit_half_life`) rather than a rewrite.

### 2.3 `hierarchical_fit` contains a no-op line

`spacing.py:180`: `gmodel.weights = gmodel.weights  # normalise API surface`. It does
nothing. Either normalise the weights (they are used as a dot product, so scale does affect
`fit_half_life`'s lam) or delete the line; a line that exists to look like it does
something is worse than no line.

### 2.4 Strategy credit mixes two incompatible units

`ai1.record_attempt`, strategy credit:

```python
model.record(strat, lift=delta if score is None else float(score) - 0.5)
```

`delta` is a change in `p_know` (usually ±0.05, and negative after a long gap because
decay is applied first); `score - 0.5` is a centred rubric score in [−0.5, +0.5]. Both go
through `sigmoid(8 × lift)` into the same Beta posterior, so a rules-graded correct answer
and a rubric score of 0.6 are treated as comparable evidence of "this strategy helped".
The deeper problem is attribution: every strategy active for the attempt gets credit for
the outcome, with no counterfactual. If you keep the mechanism, normalise the unit (use a
per-attempt residual against the model's prediction: `correct - p_correct(p_prior)`, which
is already centred and already model-relative) and record which strategies were *new* this
session rather than merely active.

---

## 3. The content contract: four sources, one measurement boundary

An earlier draft of this review treated the 55-item bank as the content ceiling and
recommended authoring ~25 items per topic. That was the wrong model of the system: it read
the *measurement* layer as if it were the *teaching* layer. Withdrawn, and replaced with the
derivation it should have had.

ADAPT does not need a big static bank. It needs a trustworthy measurement spine and a rule
for which content may touch it. Content arrives from three sources — stored, retrieved,
generated — and exactly one question decides how each must be handled: **will the learner's
response to it become an observation in the learner model?**

### 3.1 Why anything has to be stored at all

A stored item is not needed for coverage. It is needed when a *claim about a person* depends
on comparability. The claims ADAPT already makes:

1. **"This topic is at band X."** (`band()`, the dashboard, `services/journey.py`.) Two
   observations only compare if the material behind them is comparable. BKT has no
   item-difficulty parameter — difficulty variance is absorbed by slip/guess — so a pool
   whose difficulty drifts between measurement points turns material change into apparent
   learning.
2. **"You have a gap in T."** (`learning/gaps.py` → proposals.) The claim rests on the answer
   key being right. A mis-keyed item turns a correct learner into a gap.
3. **"Retention of T is due; you recalled it."** (`learning/spacing.py`.) The schedule attaches
   a lag and an outcome to *the same material*; that requires identity.
4. **"The adaptation helped."** (`evaluate_change`.) Pre/post windows must be comparable; if
   the item pool changed between them, the difference is confounded by the material, not
   caused by the adaptation.
5. **"Placement."** (the diagnostic.) An instrument that changes between runs is not an
   instrument.

Generated content can serve all five — but only by *acquiring the properties of a stored
item* first: identity, persistence, a checked key, a difficulty that accumulates. Generation
is therefore not an alternative to storage; it is a supply mechanism for items that then have
to be stored.

**The size floor is derivable, not a round number.** What sets it:

* **Band separation.** Five bands means a claim about any one of them needs items whose
  expected success differs between adjacent bands. With the current `p_correct` and guess/slip,
  that is roughly 3–4 anchors spanning the difficulty range per topic. The bank has 2–3 per
  topic and nothing above difficulty 0.72 — so it cannot separate "proficient" from "mastered"
  *as a matter of arithmetic*, not of volume. That is the real bank problem: too few anchors
  and no ceiling, not too few questions.
* **The memorisation boundary.** Re-asking an item after a week is a legitimate retention probe
  (same identity, long lag). Re-asking it within days measures memory of the item, not the
  skill. The number of distinct items a topic needs is therefore set by session cadence — and
  that is precisely the demand generation is supposed to meet. It is measurable: first-attempt
  correctness on previously seen items versus unseen items at matched declared difficulty. If
  the gap is large, the repeat-derived observations should be down-weighted.

### 3.2 The four sources, and which may become evidence

| Source | What it is in ADAPT | May it become evidence? |
|---|---|---|
| **Stored / authored** | curriculum graph (`TOPICS`, `TopicPrerequisite`); answer-keyed anchor items and their grading truth (numeric specs, MCQ keys, teachback rubrics); `LearningMaterial` prose that AI 2 is allowed to teach from; the telemetry allowlist | Yes — this is the reference against which everything else is judged |
| **Retrieved** | OpenAlex / arXiv / Open Library / YouTube results (cached with `resource_cache_ttl_hours`); Numbas packages | Only through the **importer** (`routes/tutor.py:518`), which assigns identity and a `source` tag. Raw provider links: never |
| **Generated** | explanations, hints, worked examples, rephrasings, feedback, resource curation, teachback commentary — and practice items (`ai2.generate_practice` → `_llm_practice`) | Teaching: freely. Items: only after the contract below |
| **Validated & cached** | anything that was *answered*: identity, provenance, checked key, observed difficulty, retire flag | This is the boundary itself |

The providers give exposition and reference — titles, abstracts, DOIs, URLs, videos. They do
not give curriculum-bound, difficulty-graded, answer-keyed exercises, so they cannot carry
the measurement layer at any price. The one existing route to *retrieved content that is
allowed to be evidence* is the Numbas importer, which is why it is worth treating as the
template for any future content source: validate on the way in, assign identity, tag the
source.

### 3.3 What has to be validated and cached, and why

Generated content is allowed to reach a learner. It is only allowed to reach the *trace* after
five things are true:

1. **Identity and persistence before display.** The item must exist as a row (its own table, or
   `Item` with `source="ai2"`) before it is shown. Otherwise it cannot be answered, graded the
   same way twice, calibrated, or retired.
2. **An answer check independent of the generator.** Numeric: recompute from the stem (SymPy
   locally, which also fits `ADAPT_MODE=local`) and reject on disagreement. MCQ: a key plus
   plausible distractors plus no duplicate keys. Text/teachback: rubric **and** official answer
   attached, graded by rules first and an LLM second — never the generator grading itself. The
   repo already does the right thing for teachback (`review_teachback` scores locally, then asks
   the LLM only for commentary); that pattern should be the norm.
3. **Difficulty: declared, then observed.** A generated item starts at whatever band it was
   written for; its value is replaced — shrunk from observed responses, keyed by item id — once
   it has data. Today generated items are hardcoded to `0.5`, permanently.
4. **Caching and idempotency.** The same request (topic, band, count, prompt version, model
   version, seed) should return the same items, so a reload shows what the learner saw and so
   difficulty accumulates on stable ids. Cache key = the provenance tuple.
5. **Retirement.** Retire an item when its answer fails a re-check, when its observed difficulty
   drifts outside its declared band, or when memorisation is detected. The provenance ledger is
   also the only way to answer "which items did model version X write?" after that model turns
   out to be wrong.

The failure mode these five rules exist to prevent is a **closed loop**: a model writes the
stem, writes the key, writes the feedback, and the trace records its grade as a fact about the
child. Nothing in that loop can detect its own error.

### 3.4 The measured gap in the generated-content path

`POST /api/v1/practice` → `ai2.generate_practice` fills the quota from the bank, then
`_llm_practice` writes the remainder as `gen_<uuid>` items carrying
`decision_trace: {"llm": True, "store": True}`. That flag has **exactly one occurrence in the
tree — the line that writes it.** The only `Item` inserts anywhere are the seeder
(`db/init_db.py`), the Numbas importer (`routes/tutor.py:518`) and the demo path
(`services/sessions.py:351`).

`POST /api/v1/attempts` resolves the item with `item_payload` → `s.get(Item, item_id)`, which
raises `ValueError("unknown or retired item")` for an id with no row — the 422 an existing test
asserts with `itm_nope`.

Composed, those two verified facts mean: **the tail of any practice session that needed
generation is displayed and cannot be answered.** The same function also hardcodes
`difficulty: 0.5` for every generated item, takes `answer_spec` from the model's `answer_value`
with no independent check (the prompt *asks* for computability; nothing verifies it), mints a
fresh uuid per call with a `checksum` computed and never used, and has no retirement path.

It was latent rather than live: `_llm_practice` only runs when an LLM is configured, the test
suite and the demo run in local mode with the key popped, and the frontend calls the bank-only
`GET /api/v1/practice`. So it was API-reachable, and it was the first thing AI 3 would trip
over — wiring generated content into the lesson surface is exactly AI 3's job.

**Closed** (commit `a157e7d`). `assessment/generated.py` is the rulebook, and it is deliberately
free of database access so any other path — AI 3, an importer, a script — can import it instead
of growing a weaker second copy. A generated item is acceptable only if:

1. **the answer is checkable from the stem.** A numeric item must carry a `check_expr` that
   parses as arithmetic, shares a number with the stem, and agrees with `answer_value` within a
   tolerance derived from the answer's magnitude (`max(0.005, 0.005·|expected|)` — the grader's
   1e-6 default is authored-answer precision, not school-rounding precision). The expression is
   evaluated by an AST walk with per-node bounds, not by a character allowlist around `eval`:
   it is model output, and `6*7` passes while `2**10**9` and anything naming a function cannot.
2. **the wrong answers are explained.** Every MCQ distractor needs `why_wrong`, because a
   distractor with no diagnosed mistake behind it is decoration.
3. **the question is not already in the bank**, compared against the authored stems for the
   topic.
4. **the id is derived from the content** (`gen_<sha256(topic, kind, normalised stem, spec)>`),
   so re-generating a question updates one row rather than creating a second one with the same
   text and the same key.
5. **the response is recorded before the item is shown** — `_persist_generated` writes, then the
   payload is rendered through `public_item`, so the browser gets no answer key and the id
   resolves when the answer comes back.

Everything after that is measurement rather than trust. Counters live on the item; difficulty
becomes `(8·declared + n·rate)/(8+n)` clamped to `[0.05, 0.99]`; `calibrated_at` is stamped; and
an item is retired — served to nobody, with the reason logged — when four learners have failed
it, when it drifts more than 0.35 from what its writer claimed after six attempts, or at twelve
attempts regardless. One policy, three reasons, in `should_retire`.

Two deliberate modelling choices, because both look like bugs from the outside:

* **the prior is not chained.** Each update shrinks toward the *declared* difficulty, not toward
  the previous estimate. Chaining would quietly raise the prior's weight on every answer, so a
  question that was unlucky early could never recover.
* **the prior never fully decays.** After 40 failures a question the writer called 0.9 floors
  around 0.15, not 0.05. Eight observations is not enough to conclude that a question is trivial
  for a learner who has never met its like — and the alternative, letting a first batch of wrong
  answers drive an item to "easy", is how a broken answer key becomes a popular question.

What is left for AI 3 is exactly the part that is not a rule: **reuse before writing.** A stored
item this learner has not answered is a better use of the shortfall than a fresh call — it has
attempts behind it, so its difficulty is measured rather than declared — and the same request
twice must not produce new rows. `_reusable_generated` implements that, including the
per-learner part: a question this learner has already seen measures their memory of it, so it is
not a second observation of the skill.

### 3.5 The boundary AI 3 has to respect

One line, and it generalises beyond content:

> If the learner's response becomes evidence, the material must be as fixed as the learner
> model's assumptions.

Everything else can be free: regenerate explanations, rephrase hints, invent worked examples,
fetch a video, change the tone. That is teaching, and a learner who sees nonsense in an
explanation can say so — which is itself data. Measurement items, retention probes and
placement anchors are not free, because a learner cannot tell that a wrong key is wrong. They
will simply believe the app.

## 4. User analysis: what I would add (the part you asked about most)

ADAPT's learner model is BKT + spacing + 12 rule-based detectors. That is a defensible
choice — the 2025 DLKT survey literature is blunt that deep knowledge tracing is
black-box, overconfident and compute-heavy, and recommends starting from BKT and reporting
calibration and trajectories rather than AUC alone. The gaps are not the model class; they
are (a) what the log already contains but nothing reads, (b) the absence of any *validity*
check on the model's own outputs, and (c) no way for the learner to argue with the model.

### 4.1 Use the log you already collect — response-time effort

The allowlist already records `first_interaction.ms_since_shown`, `question_shown`,
`hint_opened.ms_since_shown`, `response_change`, `explanation_opened.seconds_read`,
`answer_submitted.attempt_no`, and `latency_z` already computes a robust z-score against
the learner's own history — documented as "evidence to investigate", and then not used to
gate anything.

The educational-measurement literature has a settled method for exactly this. Wise &
Kong's response-time effort: flag a response as a rapid guess when it is faster than a
threshold, most commonly 10% of the item's mean response time capped at 10 s (NT10; NT15
is the more liberal variant in PISA analyses), and treat flagged responses as
*uninformative* rather than wrong — Wise calls this effort-moderated scoring. Validity
criteria for the detector are specified: accuracy on flagged responses should be near
chance, the index should be stable across items, and it should not correlate with ability.
Reported base rates: RTE ≈ 0.98–0.99 in a large state assessment, "meaningful
disengagement" below 0.90, with some work putting the score-validity concern as low as 6%
of items.

Concretely, for ADAPT:

1. Compute a per-item threshold as `min(10 s, 0.10 × median answer time for that item)`,
   falling back to a fixed 5 s while an item has under ~30 responses.
2. Add `rte` (proportion of effortful responses) per session, per topic, and per learner —
   it is one number, it is interpretable, and it is the honest way to answer "was this
   learner even trying today?".
3. Gate the knowledge update: a rapid guess should not move `p_know` at all. It is missing
   data, not evidence of not knowing. Today one 2-second tap on a hard question lowers
   mastery, raises the `gapped` band, and can trigger a `prerequisite_gap` proposal.
4. Surface it as *context*, not as a trait: "3 of 20 answers today were under a second —
   that session is not good evidence about you" is a legitimate, non-judgemental sentence;
   "low engagement" as a stored label is not.

This is the single highest-value user-analysis change available: it protects every other
number in the app from one careless click, and it costs a feature you already store.

### 4.2 Add wheel-spinning detection before it becomes a UX failure

`detect_answer_oscillation` is the closest thing ADAPT has; there is no notion of
*unproductive persistence*, which is the best-documented disengagement pattern in ITS
research. Beck & Gong operationalised wheel-spinning as failing to get three correct in a
row within the first 10 opportunities on a skill level, and found it is not rare (their
figure: ~38% of students on a given skill; later work with a retention criterion puts
true wheel-spinning at 9.1% of student/problem-set pairs, versus 46.9% under the cruder
definition — the gap between those two numbers is the whole lesson). Matsuda et al.
showed BKT mastery slopes detect it with 0.79 recall but only 0.25 precision, and later
work reports usable early detection after ~4 steps.

For ADAPT that reads as: after ~8–10 scored observations on a topic with no upward slope
in `p_know`, propose *something else* — a worked example, a prerequisite, a different
representation — rather than a 12th item of the same kind. This is a detector, not a label,
and it fits the existing `Observation → Hypothesis → Proposal` machinery exactly: the same
`make()` call, a new `candidate_strategy`, and a falsification sentence ("if p_know rises
by X over the next 8 items, this was wrong").

### 4.3 Validate the model, don't just report it

Nothing currently asks whether the trace predicts the next answer better than a constant.
Three cheap checks, all of which fit `learning/effect.py`'s existing honesty (bootstrap,
Wilson, Brier/ECE):

1. **Calibration and discrimination of `p_correct`.** Brier score and ECE are already
   implemented; run them on held-out attempts *per learner* and per topic. A trace that
   cannot beat "predict the learner's base rate" is decoration, and saying so is the
   project's own stated standard.
2. **A randomised-placement sanity check.** The diagnostic is seeded per learner; log the
   seed and the plan, then later compare item difficulty priors against observed
   proportion-correct. Elo-style online calibration is the standard answer here (Pelánek's
   survey: Elo tracks JMLE item difficulty under adaptive selection, while proportion
   correct is biased *precisely because* selection is adaptive — which is ADAPT's case),
   and difficulty currently has no data-driven value at all: `docs/UNVERIFIED.md`
   concedes every `difficulty` is an author's prior. A simple online update with an
   explicit uncertainty band is ~40 lines and gives you the uncertainty that Elo alone
   lacks.
3. **Reliability before anyone reports a score.** Any claim of the form "this learner is at
   level X" needs at least a KR-20/ω on the item set and a standard error on the estimate.
   `docs/UNVERIFIED.md` already refuses to let bands be reported to anyone; an SE turns
   that refusal into something the UI can display.

### 4.4 Fairness is a user-analysis question, not a compliance footnote

The audit surface is missing entirely, and the literature says the naive version of it
misleads. EDM 2025 work on BKT and reading ability found *aggregate* fairness metrics can
look fine while specific skills are biased for emerging readers, with a 7–9 point mastery
gap; a 2026 cross-architecture audit found socioeconomic ABROCA of 0.018–0.023 on 15.9M
interactions and showed that standard mitigations (reweighting, adversarial debiasing) left
the bias essentially unchanged in every configuration that preserved accuracy.

Minimum viable version for ADAPT, given it collects `age_band` and consent metadata
already:

- **DIF screening per item, not per model.** Mantel–Haenszel is the workhorse for small
  samples and needs no IRT calibration; pair it with an effect-size gate (ETS Δ bands)
  because significance alone is meaningless and "nonsignificant" is not evidence of
  fairness. Flag, then review content — a statistical flag is a request for review, not a
  verdict.
- **ABROCA-style comparisons for the mastery decisions**, reported with subgroup coverage.
  If a subgroup has 6 learners, the honest output is "we cannot tell yet", and the
  e-learning-system access checker should say that rather than print a number.
- **Multiplicity, in one place.** 12 detectors × several metrics × repeated runs is a large
  family; nothing corrects for it. Benjamini–Hochberg across a `analyze()` run is a
  two-line change and turns "13 detectors fired" into a defensible statement.

### 4.5 Let the learner be a source of evidence

The approve/revise/revoke flow plus `student_note` already make this a *negotiable* open
learner model, which is ahead of most systems — the OLM literature's continuum runs
inspectable → negotiable → editable, and the evidence favours the middle one: negotiable
OLMs improve confidence and self-assessment accuracy, whereas a bare band display is
scrutability debt. Two additions from that literature are cheap and research-supported:

- **Judgement of learning.** Ask the learner to predict their score before the result is
  shown, then show both. Delaying the rating (rating Tuesday about Monday's work) is the
  standard advice, because an immediate rating measures freshness, not memory. Reported
  effects: 75% overestimate, 66% revise their self-assessment after feedback, and
  judgement-of-learning accuracy correlates with the system's mastery estimate over time.
  This is the one number in the app that improves *because* it is shown.
- **Confidence ratings per answer.** One tap, 3 levels. It gives you two things ADAPT
  currently cannot distinguish: a fast correct answer that was a guess, and a wrong answer
  from someone who was sure. Both are high-information events for the trace and for the
  proposal copy.

### 4.6 Distractor analysis — the misconceptions are already authored and unread

`bank.mcq()` stores `why_wrong` for every distractor. Exactly one reader exists —
`grading.py:97` — and it uses the text to explain the single wrong choice a learner just
made. Nothing ever aggregates choices *across* learners, which is where the diagnostic
value is. The
multiple-choice literature is unambiguous that distractor-level analysis is where the
diagnostic value is: selection rate per option, discrimination per option (do weaker
learners pick it more?), flagging options chosen by <5% as non-functioning, and treating a
distractor with a higher mean ability than the key as an item-writing error. Two of those
are one SQL query against data you already store:

1. selection rate per choice, per item, over the last N attempts — surfaces "this distractor
   is never picked" and "this distractor is picked by *everyone*" (which usually means the
   key is wrong).
2. mean mastery-band (or `p_know`) of learners choosing each option — surfaces items where a
   wrong answer is *attracting the strong*.

Then map the highest-frequency wrong choice to its `why_wrong` and you have a misconception
report per topic with no LLM and no new authoring. `detect_confusion_between_types` is
doing something adjacent by hand; this is the general version.

### 4.7 Honesty guardrails for the LLM parts

Grading is the place where an LLM can quietly do harm, and the literature gives specific,
cheap guardrails: rubric + official answer materially carry reliability (removing the
answer collapsed ICC from 0.888 to 0.628 in one study); AI–human agreement is below
human–human agreement (ICC 0.763 vs 0.819, 95% limits of agreement ≈ [−4.2, 4.0]); binary
criteria are more reliable than Likert scales; one model self-agreed on only 61% of cases,
so pin the judge prompt *and* the model version; and near-threshold cases should be triaged
to a human. `graded_by` already distinguishes `rules|ai2|teacher` — the missing piece is
logging the judge's agreement with human review over time as a monitored metric, and
refusing to overrule the rule-grader for score bands that decide a proposal.

---

## 5. Tests worth adding, in the order I would add them

| # | Test | Why it pays |
|---|---|---|
| 1 | ✅ **Model consistency** — EM forward-LL vs `sequence_logprob`, recovery under the online rule, classic-rule shortfall asserted | Already added this pass; it is the class of bug that is invisible without it |
| 2 | ✅ **Cross-process determinism** for `diagnostic_plan`/`next_items` under `PYTHONHASHSEED` | Same class as #1: silent, environment-dependent |
| 3 | ✅ **Cold-start band semantics** (unit + end-to-end) | The README's central promise; it had no test |
| 4 | ✅ **Per-topic parameter rows are written, read, and refreshed** | Dead-branch detection |
| 5 | **Hypothesis/spacing invariants** — `expected_recall` monotone in lag, half-life bounds respected, `fit_half_life` finite under degenerate input (all-zero rows, one row, 10⁶-hour lags) | Numerical code with clamps is where nan/∞ hides |
| 6 | **Rapid-guess gating** (once 3.1 exists): a sub-threshold response must not move `p_know` | Pins the validity rule |
| 7 | **Property-based API testing** with Schemathesis over `adapt.api.app` (`from_asgi`, in-process, no server needed) | Finds 5xx and schema violations across 58 routes for the cost of one fixture |
| 8 | **Mutation testing** on `learning/` with `mutmut` (or `cosmic-ray` if you want configuration) | Coverage says 86% for `learning/*`; mutation score says whether those tests assert anything |
| 9 | **No-leak invariant, property-based** — generate random item payloads, assert `PRIVATE_ITEM_KEYS` never serialise | The current test is a fixed string scan; the invariant is stronger |
| 10 | ✅ **Generated-item contract** — accept/reject matrix, content-id idempotence, the three retirement reasons, and a fake model end-to-end: write → store → serve → answer → calibrate | Added with the fix; it is the first test that exercises the generation path with a model configured |
| 11 | ✅ **The migration path under whole-file encryption** | Added with `env.py`'s fix: `upgrade head`, stamp-then-upgrade, downgrade/re-upgrade, all read back through the app's own engine |

Coverage today: 72% overall, 86% for `learning/` + `assessment/`. Weakest: `cli` 21%,
`ai/llm` 22%, `security/redact` 23%, `resources/providers` 38%, `voice` 30–52%, `ai2` 54%.
`security/redact` was the one I would have moved up the list — it is the module that keeps a
learner's name out of an LLM prompt — and §1.9 is the argument: its own function signature was
violated by every caller, and 23% coverage meant nobody noticed. The structural test added there
is a floor, not an adequacy argument.

---

## 6. APIs and integrations worth considering

Grouped by what they buy, not by novelty. ADAPT's only interchange path today is the Numbas
export/import, which is a good start and a narrow one.

**Assessment and content**

- **1EdTech LTI 1.3** (`pylti1p3`) with AGS + NRPS + Deep Linking — the single highest
  leverage integration: it is how ADAPT gets *into* an institution (Moodle, Canvas,
  Blackboard) as a tool, gets rosters without building an SIS integration, and passes
  grades back into the gradebook. Every other standard below becomes more useful once this
  exists.
- **QTI 3.0** for item/test exchange — would let the ~90 authored items move in and out of
  any conformant player, and would make the bank portable if the project is ever handed on.
- **xAPI (Learning Locker / Veracity LRS) or Caliper** for the event stream. The 21-event
  allowlist maps almost 1:1 onto xAPI statements; the known failure mode is verb
  governance, so a profile (or at least a frozen verb list) should exist before the first
  statement is emitted.
- **OneRoster** for rosters/classes if ADAPT ever needs a cohort concept, which
  `train_params` already assumes but the data model does not express.

**Math and explanation quality**

- **SymPy** (local, no key, fits `ADAPT_MODE=local`) as an answer-equivalence checker for
  numeric and symbolic answers — it turns "is `x=2` the same as `2`" from a string
  comparison into a decision, and it can verify worked examples before they are shown.
- **Wolfram|Alpha Short Answers API** as an optional online verifier for the same job, with
  the cost and privacy caveats that metadata-only mode implies (send the expression, never
  the learner).
- **Desmos** (the SAT's own calculator) or **GeoGebra** embeds for the visual/`visual_benefit`
  path — GeoGebra has the richer worksheet/LMS story.

**User analysis and research plumbing**

- **OULAD** and **EdNet** (plus ASSISTments 2017, XES3G5M) are the public benchmarks the
  knowledge-tracing literature uses. They are not ADAPT data and cannot validate ADAPT's
  UI, but they *can* validate the estimator: run `fit_em` over OULAD/EdNet sequences and
  compare recovered parameters and AUC against pyBKT's published numbers. That converts
  "our EM is consistent with our update rule" into "our EM is consistent with the field's
  implementations" — which is what a pilot reviewer will ask.
- **pyBKT** as a cross-check rather than a dependency: same inputs, same parameters within
  tolerance. It also supports the variants (per-skill, forgetting, individualisation) that
  `fit_em` will eventually need.
- **Individually-adaptive testing / Elo with uncertainty** for item calibration (see §4.3).
  Pelánek's survey and the "Tracking with (Un)Certainty" work are the right starting
  points; ~100 learners for solid difficulty estimates is the honest scale bar, so this
  should ship as "provisional, n=<count>" until a pilot exists.

---

## 7. Suggested order of work

| Priority | Item | Effort | Why now |
|---|---|---|---|
| P0 | Accept/review this pass's fixes (§1) | — | Defects in the numbers every other feature trusts |
| ✅ | ~~**Generated-content contract** before AI 3 wires generated items into the lesson surface~~ — done, `a157e7d` (§3.3, §3.4) | — | The tail of a generated practice session could not be answered at all (422), and nothing verified a generated answer key. AI 3's remaining half is reuse-before-writing, not the contract |
| ✅ | ~~Migrations run through the app's own engine~~ — done, `438b379` (§1.10) | — | Every installation using whole-file encryption had an unusable migration path, in both directions |
| P0 | Response-time effort + gate the trace on it (§4.1) | ~1 day | One careless click currently moves mastery and can trigger a proposal |
| P1 | Detector gate: state one rule, or raise `min_n` to match (§2.1) | half day | The catalogue currently misdescribes its own evidence bar |
| P1 | Wheel-spinning detector (§4.2) | ~1 day | Fits the existing detector/proposal machinery; prevents the 12th same-question loop |
| P1 | `p_correct` calibration report per topic (§4.3.1) | ~1 day | Turns "the trace is the product" into a measurable claim |
| P1 | Strategy-credit unit fix (§2.4) | half day | Garbage in the strategy posterior silently reorders strategies |
| P2 | Distractor selection-rate + discrimination report (§4.6) | ~1 day | Unlocks authored `why_wrong` data; no new content needed |
| P2 | Judgement-of-learning + confidence capture (§4.5) | ~2 days | The only feature here that improves the learner's own calibration |
| P2 | LTI 1.3 tool launch + AGS (§6) | ~3–5 days | Institutional distribution |
| P2 | DIF screen + ABROCA with coverage floors (§4.4) | ~2 days | Fairness claims need to be checkable, not asserted |
| P3 | Schemathesis + mutation testing in CI (§5) | ~1 day | Cheap, ongoing evidence about the test suite itself |
| P3 | SymPy answer equivalence (§6) | ~1 day | Removes a class of grading false negatives |

Two things I would *not* change: the "AI 1 cannot change education by itself" property (it
is enforced by the schema, and it is the strongest part of the design), and the refusal in
`docs/UNVERIFIED.md` to let placement bands be reported to anyone. Both are the reason the
rest of the document is worth reading as a list of gaps rather than a list of hopes.

---

## Appendix — how to reproduce

```bash
cd /home/user/Adapt
.venv/bin/python -m pytest backend/tests -q                     # 195 passed
node scripts/check-contract.mjs                                 # contract ok
.venv/bin/python -m ruff check backend/src --select F,E7,E9,B017  # clean
```

Plan stability (the table in §1.4) is reproduced by running
`backend/tests/test_learner_model_consistency.py::test_the_diagnostic_is_the_same_plan_in_another_process`;
parameter recovery is the same file, `test_the_learning_rate_fit_from_live_rule_data_matches_the_rule`.
The detector-strength table in §2.1 comes from calling `_strength(value, baseline, n_a, n_b, min_n=3)`
directly with the values shown.
