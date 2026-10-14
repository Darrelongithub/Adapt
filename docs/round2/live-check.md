# Phase E — the live-model quality check

**The live run has NOT been made.** No key is configured in this environment, so nothing in this
repository was produced by a model contact. Everything below that has a number in it comes from a
run driven by a scripted fake (`--selftest`), and the file those runs wrote says so in its first
lines. The one command that makes the real run is in [Running it for
real](#running-it-for-real) — it is one command, and it has not been run yet.

What exists:

| file | what it is |
| --- | --- |
| `scripts/live_model_check.py` | the harness: the topics, the checks, the judge, the report |
| `backend/tests/fixtures/model/contract_stub.py` | the frozen contracts (`get_pack`, `write_lesson`, `explain_after_wrong`, `reteach`) standing in for Phases B and C, which are not merged here |
| `backend/tests/fixtures/model/scripted_model.py` | the scripted fake, its nine red-team variants, and the fixture replayer |
| `backend/tests/test_live_check_harness.py` | 27 tests: the harness against the fake, the red team, the refusal path, the fixtures |
| `MODEL_QUALITY_REPORT.md` | the template a real run replaces |

## What a run does

Every one of the twelve topics the brief names goes through the same pipeline, once per profile:

* **skeleton** — `get_pack` for the topic at level 3, in the profile's language;
* **items and verification** — the pack's items, each one marked `computed`, `cross_checked` or
  `unverified`, because an unverified item may never be used to score or place a learner;
* **a lesson** — `write_lesson` for the pack's first concept, written for that profile;
* **the three explanation moments** — the lesson itself, then `explain_after_wrong` over a
  simulated wrong answer of the kind the item's own misconception names, then `reteach` twice,
  because the second time has to be a different explanation, not the same one again;
* **the judge** — one more call, with its own system prompt, scoring what came back 1–5.

The twelve topics are the brief's list. Ten are outside the authored bank — quantum
chromodynamics, how to bake bread, Kiswahili verbs, sorting algorithms, supply and demand, the
French Revolution, discrete maths (sets), derivatives, constitutional law, essay writing — and two
are inside it (photosynthesis, probability), which is how the check tells an authored pack from a
generated one in the same run.

The two profiles pull on every lever the judge scores:

| profile | language | tone | opens with | step size |
| --- | --- | --- | --- | --- |
| worked example first | English | plain | a worked example | up to 90 words per block |
| picture first | Kiswahili | warm | a visual | up to 45 words per block |

The second profile is in Kiswahili on purpose: a language lever that is never asked for is a lever
nobody has seen honoured.

A topic costs about 12 or 13 calls (two packs, two lessons, three moments, one judge), so the full
run is around 150. `--max-calls` (default 400) stops the run rather than overspending, and a stop
is reported as an incomplete run, never as a verdict.

## What is checked, and by whom

**35 automated checks per topic** — 420 over a full run — of which the model's own text is judged
by code, because code is what decides. They cover:

* the pack: concepts exist, ids are unique, the source is one of `authored`/`model`/`simple`, a key
  idea is a sentence, a misconception says why it is tempting, an option points at a real
  misconception, an item cites a fact that exists, an item's verification is one of the three
  allowed words;
* the lesson: it has blocks, it says whether a model wrote it, every block is inside the profile's
  step size, it opens with the profile's lead format, every explanation is followed by a check, the
  text is in the profile's language;
* everything a learner could read, in both languages and all three moments: no developer text, no
  unrendered placeholder, no markup or links, and no printed answer sitting in the stem of the
  question that asks for it;
* the moments: the wrong-answer explanation starts from the answer the learner actually gave, and
  the second reteach is a different explanation from the first.

**The judge** is a separate call with a separate prompt. It scores `correctness`, `explains_why`,
`uses_learner_answer`, `fits_profile` and `no_leakage` from 1 to 5, and **every score has to quote
the artefact**: a quote that is not in the teaching text is not accepted, and a score with no quote
behind it is refused and reported. The judge sees the finished artefact, not the prompt that asked
for it, so quoting an instruction cannot stand in for evidence.

## What makes it fail

Exit code 0 is a pass, and a pass is a list, not a judgement call:

| exit | meaning |
| --- | --- |
| 0 | every threshold below was met, no gate flag was raised |
| 1 | a threshold was missed, a gate flag was raised, or a judge score was refused |
| 2 | refused to run: no credential configured, bad usage, or a self-test trying to overwrite the report in the repository |
| 3 | incomplete: the per-run call limit or the configured daily quota stopped the run, or fewer than all twelve topics ran and `--allow-partial` was not passed |

| measure | threshold |
| --- | --- |
| topics completed | 12 |
| automated checks passed | 90% |
| topics the model built content for (not the fallback) | 75% |
| items that passed verification | 50% |
| step size honoured | 95% |
| lead format honoured | 90% |
| language honoured | 90% |
| the wrong-answer moment starts from the learner's answer | 90% |
| the second reteach differs from the first | 90% |
| judge · correctness | mean ≥ 3.5 |
| judge · explains_why, uses_learner_answer, fits_profile | mean ≥ 3.0 each |
| judge · no_leakage | mean ≥ 4.0 |

Each measure keeps its own threshold on purpose: "the language lever was ignored on a fifth of the
lessons" fails the run *as a language failure*, not as a mysterious drop in an overall rate.

## Running it for real

One command, from the repository root, after the key is configured in the console:

```
cd backend && .venv/bin/activate      # or call backend/.venv/bin/python directly
adapt config set-key                  # hidden prompt; the key is never an argument
adapt config test                     # one minimal request: pass or fail
python scripts/live_model_check.py    # from the repository root
```

That writes `MODEL_QUALITY_REPORT.md` in the repository root. Useful variations:

```
python scripts/live_model_check.py --out /tmp/live-run.md      # leave the repository's report alone
python scripts/live_model_check.py --json                      # the run record as JSON as well
python scripts/live_model_check.py --price-in 0.30 --price-out 2.50   # add a cost estimate
python scripts/live_model_check.py --save-fixtures             # also write regression fixtures
```

Without a configured key the script refuses, prints the two console commands above, exits 2, and
**does not touch an existing report** — there is a test for that, because a refused run writing a
file would be worse than no run at all.

Cost: the report carries the measured tokens in and out, the median latency per call, and the
refusal counts. It prints a money figure only if you pass the two prices; it invents none.
`--save-fixtures` writes one file per topic under `backend/tests/fixtures/model/`; a live run saves
them automatically.

## Running it without a key (what has actually been done here)

```
python scripts/live_model_check.py --selftest
```

drives the whole run with `backend/tests/fixtures/model/scripted_model.py`. The fake is
check-shaped rather than good teaching: the `good` variant is the smallest thing that satisfies
every automated check, so a failure in a run that uses it is a failure of the harness, not of a
model. The report's first line then says, in bold, that a scripted fake answered every call.

`--selftest` refuses to overwrite `MODEL_QUALITY_REPORT.md` unless `--force` is passed; it defaults
to `/tmp/adapt-live-check-selftest.md`.

The **red team** is the other half of the test. Nine variants each break exactly one rule, and the
suite asserts that the run fails, with that rule's own reason in the report:

| variant | what it breaks | what the run says |
| --- | --- | --- |
| `good` | nothing | PASS, exit 0 |
| `leaky` | an item's key is printed in its own stem | `answer_leak: c1_i1: the stem contains the key` |
| `devwords` | learner copy names the developer's own machinery | `developer_text: API key … Gemini` |
| `placeholder` | an unrendered `{topic}` reaches the learner | `placeholder: …{topic}…` |
| `bare_judge` | the judge scores without quoting | five "nothing was scored" |
| `refusing` | the model refuses every call | automated 88%, model-built content 0%, judge unscored |
| `key_mismatch` | a check block's answer is not the item's key | "the check block answers 'After five heads, a tail is due.'" |
| `unverified` | items that no verification passed | "items that passed verification 0/2 (want 50%)" |
| `wrong_language` | the Kiswahili profile is answered in English | "language 50% (want 90%)" |

## Fixtures

A saved fixture holds, per topic: the replies (purpose, the reply object, the raw text, tokens,
latency), the packs, the lessons, the moments, the judge's scores and the checks. Each reply is
addressed by `prompt_key`, a short digest of the request, so a fixture replays by content rather
than by call order, and a fixture written before those keys existed still replays by purpose in
recorded order.

Every fixture passes through the app's own two passes before it touches the disk — `redact_text`
(identity-shaped strings) and `scrub_text` (configured secrets, including the key itself) — and the
harness's own tests put a canary key in the environment and assert it reaches neither the report,
the fixtures, nor stdout. The prompt text itself is not stored; only the digest is.

Replaying needs no key:

```python
import sys; sys.path.insert(0, "backend/tests/fixtures/model")
from scripted_model import load_fixture
model = load_fixture("backend/tests/fixtures/model/probability.json")
```

and the harness's checks are the assertion. A fixture is regression material, not a quality
verdict: it says what the model answered once, so that a later change that makes the run worse
fails a test rather than a reviewer.

**No fixture from a live run is committed here**, because no live run has been made.

## What this run cannot see

* Everything here is a *quality* judgement of text the pipeline produced; it is not a claim that
  the app renders it, or that a learner would understand it. Phase F's browser work and the
  frontend probes are where rendering is checked.
* The two profiles are two profiles. They are chosen to load every lever, but a real learner's
  snapshot carries more than these five fields, and the run does not exercise the app's own
  profiling.
* The judge is one more model call. It scores against quotes and it is scored by code, but a judge
  that is wrong in the same direction as the writer will not be caught here; that is why the
  automated checks exist and why the report prints both.
* Until Phases B and C merge, the pipeline under test is the contract stub in
  `backend/tests/fixtures/model/`, not the real `adapt.content.skeleton` /
  `adapt.ai.lesson_writer`. The harness prefers the real modules when they are importable
  (`--pipeline auto`) and can be made to insist on them (`--pipeline real`); see
  `docs/round2/handoff-e.md`.
