# Phase E → Phase G handoff

Phase E owns no application code, so there is nothing in the app to undo or merge from this
branch. What Phase G has to do with this work is small and specific, and one item of it is the
whole point of the phase.

## 1. Make the live run — this is the deliverable Phase E could not produce

There is no key in the environment Phase E worked in, so **the live run has not been made**. The
harness is tested end to end against a scripted fake and against a dataclass-shaped pipeline, but
nothing in this repository has been produced by a model contact.

With the key configured in the console, one command:

```
cd backend && .venv/bin/activate      # or call backend/.venv/bin/python directly
adapt config set-key                  # hidden prompt; the key is never an argument
adapt config test                     # one minimal request: pass or fail
python scripts/live_model_check.py    # from the repository root
```

It writes `MODEL_QUALITY_REPORT.md` in the repository root, replacing the template that is
committed now. **Commit the report it writes, with its exit code.** If the exit code is non-zero,
that is the finding: commit the report anyway, and do not move the thresholds to make a run pass —
they are stated thresholds, not tuned ones, and the report names the one that was missed.

The run saves one sanitised fixture per topic under `backend/tests/fixtures/model/` automatically.
Review one before committing it, then commit them: that is requirement 5 of the phase, and a
fixture replays with no key (`docs/round2/live-check.md` says how).

## 2. Swapping the real modules in needs no change here — but read the two shape notes

`scripts/live_model_check.py` picks the pipeline like this:

* `--pipeline auto` (the default): imports `adapt.content.skeleton` and `adapt.ai.lesson_writer`;
  **if both import, it uses them**; if either is missing (as in this checkout), it falls back to
  `backend/tests/fixtures/model/contract_stub.py` and says so in the report's header;
* `--pipeline real`: the same, but a failure to import is fatal instead of a fallback.

So after B and C merge, the default command in §1 tests the real pipeline with no further work.
Two shapes are worth knowing about:

* **Return types.** Values coming back from B and C may be dicts, dataclasses, pydantic models or
  namedtuples; the harness reads any of them as data and still hands the *original* object back to
  the next call, so a writer that expects its own `Pack` keeps getting it. There is a test for
  that (`test_a_pipeline_that_returns_dataclasses_still_reads_and_still_serialises`). A return
  value that is none of those (a bare object with no fields) will be read as-is and the checks
  that cite fields will fail loudly rather than silently.
* **The snapshot.** `profile_snapshot()` in the harness builds the profile the lesson is written
  for as `{**profile, "levers": {...}, "prefs": {...}, "memory": {}, "evidence": [], "rung": 3}`,
  with the five flat fields (`language`, `tone`, `lead_format`, `step_size`, `max_words_per_block`)
  alongside. If Phase C's `write_lesson` expects a different snapshot object — a dataclass, a
  richer memory, a different name for the levers — the checks for step size, lead format and
  language will read the request, not the writing, and Phase G must either adapt the builder or
  ask Phase E to. `memory` is passed empty on purpose: this is a fresh learner, and a fallback to
  the app's own `simple` lesson is reported as such (`source: simple`), not hidden.

## 3. Three things to check on the first live run, and what to do about each

* **Kiswahili.** The second profile asks for `language="sw"`. If the pipeline cannot serve it, the
  run fails its own language threshold at 50% and says so. That is a real gap worth knowing about;
  the honest fix is in the pipeline or in the profile, not in the threshold.
* **`verification` words.** An item counts as verified only if it says `computed` or
  `cross_checked`. If Phase B names those values differently, the "items that passed verification"
  line means something else; tell Phase E and it is a one-line change in the harness.
* **Judge refusals.** A judge score with no quote behind it is refused. Five refusals in a row
  ("nothing was scored") means the judge is answering in the wrong shape, not that the teaching is
  bad — check the raw reply in the fixture before reporting a quality problem.

## 4. What Phase E did not do

* No live run (no key).
* No application code was touched, so nothing here needs a merge decision beyond the new files.
* No browser work: this phase has no UI.
* The harness's own thresholds have never been read against real model output — the fake is
  check-shaped, so a full green run against it proves the harness works, not that a model would
  pass. That is exactly what §1 settles.
