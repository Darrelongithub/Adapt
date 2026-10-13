# Handoff prompts -- paste one file into one agent session

`docs/AGENT_BRIEFS.md` is the *plan*: what each workstream is, what is frozen, and how the branches
merge. This directory is the *handoff*: one self-contained file per agent, written to be opened,
copied whole, and pasted into a fresh session. Each file repeats the shared contract on purpose, so
an agent never needs another file to start.

| File | Workstream | When to start | Rough size |
|---|---|---|---|
| `agent-1-baseline.md` | Finish phase 2 + the baseline UI | **done** by the integrator session - do not hand out | 2-2.5 workstreams |
| `agent-2-subjects.md` | Phase 3, the subject round | now (wave 1) | 3-4x, the largest |
| `agent-3-content.md` | Phase 4, the content engine (AI 2) | now (wave 1) | 2.5-3.5x |
| `agent-4-adapter.md` | Phase 5, AI 3 + the contrast demo | after 2 and 3 merge | 2-3x |
| `agent-5-evidence.md` | Phase 6, evidence + the failure loop | after 4 merges | 2x |
| `agent-6-security.md` | Phase 7, security + demo readiness | now (wave 1) | 2-3x |

**Agent 1 is done, and it was run here.** The Arena session that owns this repo executed it (it had
to land first: a green baseline suite is the floor the other workstreams stand on). The record is
`agent-notes/1-baseline.md` and the phase 2 report in `docs/FUNCTIONALITY_ROUND.md`; its handoff file
stays in the repo as documentation. Hand out the other five.

## Status (updated by the integrator session, 2026-10-13)

Agents 3, 5 and 6 have landed (#9, #10, #12, #14) and their work is merged into
`arena/06d446fc-adapt`; the merged tree is green (887 backend tests, 234 probe checks) after the
integration described in `agent-notes/7-integration.md` and the integration report in
`docs/FUNCTIONALITY_ROUND.md`. **Agent 2 (phase 3) is the one still outstanding** - hand that file
out. PRs #11 and #13 landed on `main` rather than on the arena branch and are reconciled at the
final merge, so hand agents the *arena* branch as their base, as the files say.

If you are starting agent 4 or 5 now: the merged tip includes the §3 boundary and the route
vocabulary it produced (see the integration note), so name it in the brief you paste - an adapter
that publishes a provider name will fail the canary test.

## How to run it

1. Open a fresh agent session per file (own checkout, own branch). Paste the file's contents as the
   first message -- nothing else is needed, and adding the spec text is unnecessary since each brief
   names the spec section to read first.
2. Wave 1: agents 2, 3 and 6 in parallel. Agent 4 waits until 2 and 3 have merged; agent 5 waits for
   4. Everything else about the waves is in `docs/AGENT_BRIEFS.md`.
3. Each agent pushes `agent/<slug>` and opens a PR against `arena/06d446fc-adapt`. They are told not
   to merge their own PR and not to push to the shared branch.
4. Tell the integrator session (this one) when PRs are up. It merges in the order
   1 -> 3 -> 2 -> 6 -> 4 -> 5, wires the frozen-file notes from all agents in one commit, runs the
   whole suite, and produces the final section-12 report.

## What the prompts already handle

* Branch-from-newest-tip instructions, so an agent that starts late still lands on the merged work.
* The frozen contract: store signatures, the write-permission matrix, lever shape, error shape, the
  naming rule, the do-not list, and the integrator-only file list.
* The migration placeholder rule (`P<N>` revision, `down_revision = "0008"`, existence-aware), so
  six branches can each add tables without a revision clash; the integrator re-chains them.
* The honesty rules: no weakened tests, no invented numbers, and -- because **no browser can be
  installed in these environments and there is no model API key** -- browser-level and live-model
  claims go to `docs/UNVERIFIED.md` as pending checks with exact steps, never into a report as done.
* Report format and location (`agent-notes/<N>-<slug>.md` on the agent's own branch).

## Two things to expect in the first hour of each agent

* `git clone` may be slow or the sandbox may have no network to PyPI; the pip install in the brief
  is the step to watch.
* The suite takes about a minute and was **765 passed** when these were written. If an agent finds
  it red before changing anything, its branch base is wrong -- have it re-fetch
  `origin/arena/06d446fc-adapt` rather than fix unrelated tests.
