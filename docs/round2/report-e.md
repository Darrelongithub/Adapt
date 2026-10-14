# Phase E — report

**The live run has not been made.** That is the one thing this phase was asked to be honest about,
and it is the headline: the harness is built and tested, the model contact is not. Everything below
labelled *self-test* was produced by the scripted fake in `backend/tests/fixtures/model/`, and the
reports those runs wrote say so in their first lines.

## What this phase owns, and what it built

| file | what it is |
| --- | --- |
| `scripts/live_model_check.py` | the harness: 12 topics × 2 profiles × 3 moments + judge, 35 automated checks per topic, the report writer, four exit codes |
| `backend/tests/fixtures/model/contract_stub.py` | the frozen contracts (`get_pack`, `write_lesson`, `explain_after_wrong`, `reteach`) as a stub, standing in for Phases B and C, which are not merged on this branch |
| `backend/tests/fixtures/model/scripted_model.py` | the scripted fake, nine red-team variants, and the fixture replayer |
| `backend/tests/test_live_check_harness.py` | 38 tests: the run, the refusals, the red team, the judge's evidence rule, the fixtures, the bridge into `ai/llm.py`, and a dataclass-shaped pipeline |
| `docs/round2/live-check.md` | how to run it, what it checks, the thresholds, the exit codes, and what it cannot see |
| `MODEL_QUALITY_REPORT.md` | the committed template a live run replaces |
| `docs/round2/handoff-e.md` | the small, specific list of what Phase G must do |

No application code was touched: this phase has none of its own, and the harness codes against the
frozen contracts only.

## Tests and runs, with their results

All run in this checkout on `arena/4b39a527-adapt`.

| what | command | result |
| --- | --- | --- |
| the harness's own tests | `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_live_check_harness.py` | **27 passed** |
| the backend suite, whole | `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q tests` | **1147 passed, 1 skipped** |
| the full self-test | `python scripts/live_model_check.py --selftest --out /tmp/e-keypaste.md` | exit **0**, `Verdict: PASS`, 12/12 topics, 144 calls, **432/432** automated checks |
| the red team | `--selftest --variant <v> --topics probability --allow-partial` | `good` exit 0; the other **eight exit 1**, each with its own reason (below) |
| the refusal path | `python scripts/live_model_check.py` with no key | exit **2**, prints `adapt config set-key` / `adapt config test`, creates no file and leaves an existing report alone |
| asking for the real pipeline where B and C are not merged | `--pipeline real` | exit **2**, `refused: … needs adapt.content.skeleton and adapt.ai.lesson_writer`, writes nothing |
| the bridge into `ai/llm.py`, tested against a stand-in client | `pytest -q tests/test_live_check_harness.py` | calls carry the frozen arguments, one loop survives two calls, `quota`/`disabled`/`throttled` become named stops, a non-object reply is `parse_error`, `close()` shuts the client and the loop |
| a run stopped by the allowance, per status | `--selftest --topics probability` with a `quota` / `disabled` / `throttled` factory | exit **3** each, `Verdict: INCOMPLETE` naming the status |
| the per-run stop | `--selftest --max-calls 3` | exit **3**, `Verdict: INCOMPLETE`, "the per-run call limit of 3 is spent" |
| a partial run | `--topics probability` without `--allow-partial` | exit **3**; with `--allow-partial`, exit 0 and the report says `**partial:**` |

The red team, one failure reason each — this is the evidence that the checks can fail:

| variant | the run says |
| --- | --- |
| `good` | PASS, exit 0 |
| `leaky` | `answer_leak: c1_i1: the stem contains the key` |
| `devwords` | `developer_text: API key in lesson[worked example first].text` |
| `placeholder` | `placeholder: lesson[worked example first].text …` |
| `bare_judge` | five "nothing was scored" + "5 judge score(s) were refused for having no quote behind them" |
| `refusing` | automated 88% (want 90%), language 50%, model-built content 0%, judge unscored |
| `key_mismatch` | "the check block answers 'After five heads, a tail is due.' but the item's key is 'A probability is a number from 0 to 1 for how likely an even'" |
| `unverified` | "items that passed verification 0/2 (want 50%)" + "no verified item was available, so moment 2 was not exercised" |
| `wrong_language` | "language 50% (want 90%)" |
| `keypaste` | `credential_in_copy: lesson[…].text: a [withheld] API key` (and the run that echoes the *configured* key into a lesson exits 1 with the report, the fixture and stdout all free of it) |

Three behaviours worth naming because they are the phase's own honesty rules:

* **The refusal leaves nothing behind.** With no key the script exits 2 and does not touch an
  existing report — asserted by a test that puts a written report in the path and compares it byte
  for byte afterwards.
* **The key cannot reach a file.** The tests set a canary key in the environment and assert it
  appears in neither the report, the fixtures, nor stdout, in both the passing run and the failure
  paths.
* **A refusal is a refusal, whichever kind it is.** A missing credential, `--pipeline real` on a
  checkout without Phases B and C, an unknown topic or profile, and a self-test trying to overwrite
  the committed report all exit 2 and write nothing — the same code, so a script driving this can
  tell "would not start" from "started and failed".

Two shape decisions were made and tested this phase, both aimed at the swap Phase G has to do:

* returns from Phases B/C may be dicts, dataclasses, pydantic models or namedtuples — the harness
  reads any of them as data and still hands the **original** object back to the next call, so a
  writer that expects its own `Pack` keeps getting it
  (`test_a_pipeline_that_returns_dataclasses_still_reads_and_still_serialises`);
* fixtures are addressed by a digest of the request, not by call order, so a fixture replays by
  content and a replay model can be built per call without losing its place.

## Screenshots

None. This phase touches no UI, so there is nothing to screenshot; `docs/screens/E/` is
deliberately absent.

## What is unverified

1. **The live run.** No key exists in this environment, so no report, fixture or number in this
   repository came from a model contact. `docs/round2/live-check.md` gives the owner the exact
   one-command run; `docs/round2/handoff-e.md` §1 asks Phase G to make it and commit the result
   with its exit code.
2. **The pipeline under test is the stub.** Phases B and C are not merged on this branch, so
   `--pipeline auto` falls back to `backend/tests/fixtures/model/contract_stub.py` and the report
   header says so. The real modules have never been driven by this harness.
3. **The thresholds have never been read against real output.** A green self-test proves the
   harness works, not that a model would pass: the fake was written to be check-shaped, and its
   judge scores (4.20 per topic) are the fake's own answers, not a reading of quality.
4. **The two profiles are two profiles.** Step size, lead format, tone and language are all under
   load, but the app's real profiling is not what this harness exercises.
5. Everything else in this report is a command with an exit code, reproducible in this checkout.

## Handoff for Phase G

Short version: configure the key in the console, run the one command, commit the report and its
exit code, commit the sanitised fixtures the run saves, and check the three items in
`docs/round2/handoff-e.md` §3 (Kiswahili, `verification` words, judge refusals). Nothing from this
phase needs a merge decision beyond its own new files.

## Branch note

Parts A–F were specified to run on `r2/phase-<letter>` branches. This session is pinned to
`arena/4b39a527-adapt`, so `r2/phase-e` does not exist and could not be created here; every change
in this report is on that one branch, and this commit contains only the seven Phase E files listed
above. The parked Phase A work already sitting uncommitted in the same working tree (24 modified
files plus `docs/round2/handoff-a.md`, `docs/round2/unverified-a.md`,
`backend/tests/test_bank_concept_ideas.py`) was **not** touched, staged or committed by this phase.
