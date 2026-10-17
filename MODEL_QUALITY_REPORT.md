# MODEL_QUALITY_REPORT.md

<!-- template: scripts/live_model_check.py replaces this whole file on a run -->

> **No live run has been made.** This is the template. Nothing below is a measurement of a
> model's output: no key is configured in the copy of the repository this was written in, so the
> harness was tested against a scripted fake instead (`--selftest`), and the report those runs
> wrote says in its first lines that a fake answered every call. The harness is
> `scripts/live_model_check.py`; run it with `--selftest` to reproduce that path.

The command that replaces this file is one command, from the repository root:

```
cd backend && .venv/bin/activate      # or call backend/.venv/bin/python directly
adapt config set-key                  # hidden prompt; the key is never an argument
adapt config test                     # one minimal request: pass or fail
python scripts/live_model_check.py    # from the repository root
```

It refuses to run — exit 2 — when no key is configured, and it does not touch this file in that
case.

## What a run writes here

* the verdict, and every stated threshold that was missed;
* the pass/fail count of the automated checks, per topic and in total;
* why a call was refused, and where the pipeline fell back to its own deterministic content;
* the judge's mean per criterion — correctness, explains_why, uses_learner_answer, fits_profile,
  no_leakage — each with the quotes the judge gave as evidence;
* measured tokens in and out, median latency, refusal counts, and a cost estimate only when
  `--price-in` / `--price-out` are passed;
* the list of fixtures it saved, if any;
* the exit code the script returned.

## What it does not write here

* the key, the model name, or any configured value — every line of the report passes through the
  app's own `scrub_text` before it is written, and a canary test asserts the key cannot appear;
* anything the run did not measure. A figure that was not measured is absent, not estimated.

## Where the numbers in a real report come from

Twelve topics (ten outside the authored bank, two inside it), each through skeleton → items →
verification → a lesson for each of two profiles → the three explanation moments → one judge call.
A threshold that is not met is named in the verdict, and the process exits non-zero. The full
table of thresholds is in `scripts/live_model_check.py` (`THRESHOLDS`), which is what enforces them.

## Status of this file

| field | value |
| --- | --- |
| live run | not made |
| report produced by | nothing — this is the template |
| harness tested against | the scripted fake in `backend/tests/fixtures/model/scripted_model.py` |
| harness tests | `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_live_check_harness.py` |
| last self-test run | 2026-10-17, `python scripts/live_model_check.py --selftest` — PASS, 12/12 topics, 144 scripted calls, report written to `/tmp/adapt-live-check-selftest.md` (a self-test may not overwrite this file without `--force`) |

A self-test is a test of the *harness*, not of any model: the fake answers every call. It is
recorded here so the difference between "the measuring instrument works" and "something was
measured" stays visible in the one file a reader would mistake for a measurement.

Two things worth doing on the machine that does have a key, before the first live run:
`adapt config test` (one minimal request — it now distinguishes "the model id does not exist"
from "the request failed"), and `adapt config models` (which of the configured names the
provider will actually answer to). The request shape the live run will exercise —
`systemInstruction` in camelCase, explicit safety settings, empty candidate lists refused — is
pinned offline by `backend/tests/test_llm_wire.py`, so a live run is a quality measurement and
not a wiring check.
