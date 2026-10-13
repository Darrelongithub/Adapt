# Handoff: Agent 4 -- Phase 5: AI 3, the adaptation layer, and the contrast demo

> Paste this whole file into a fresh agent session. It is self-contained.

## Your mission

You are one of six agents building **Adapt** in parallel, one workstream each. The other five run in
other windows and the integrator (the session that owns the branch `arena/06d446fc-adapt`) merges
every PR into that branch at the end. Work only on your own branch and open a PR; never push to
`arena/06d446fc-adapt` and never merge your own PR.

## Start here (do this before writing any code)

```bash
git clone https://github.com/Darrelongithub/Adapt.git && cd Adapt
git fetch origin arena/06d446fc-adapt
git checkout -b agent/4-adapter origin/arena/06d446fc-adapt   # always branch from the newest tip
python3 -m venv .venv && .venv/bin/pip install -e "backend[dev]"
cd backend && ../.venv/bin/python -m pytest -q             # must be green before you change anything
```

The suite was **763 passed** on the tip when this brief was written. `README.md` and
`docs/AGENT_BRIEFS.md` (the plan these handoffs come from) are worth a skim, but the operative spec
is `ADAPT_functionality_round_prompt.md` at the repo root: read **your phase's section in full**
before coding, plus section 0 (working rules), 2A (the shared store) and 13 (the do-not list). Where
this message and the spec disagree, the spec wins -- and say so in your report.

## The do-not list (restated because it is what actually goes wrong)

* Never delete, skip, weaken, `xfail` or `-k`-exclude a test to make something pass. A test that
  fails after your change is a fact about your change: report it. If a test's *expectation* is
  genuinely wrong, fix the expectation, add a comment saying why, and list it in your report.
* No learning-style labels anywhere: no "visual learner", no `learning_style` key. A stated
  preference is never presented as a measurement.
* No invented statistics, citations or UI claims. Every number a learner sees is either a
  measurement with an `n` (estimates carry a range) or it is absent.
* No hard-coded secrets or API keys, no secrets in the database, no secrets echoed to a terminal.
* No new heavy dependency (adds >50 MB, a compiled toolchain, or a service) without a written reason
  in your report. Check whether it is already a transitive dependency first.
* Nothing unverifiable here may be claimed as verified. **Chromium cannot be installed in these
  sandboxes** (`npx playwright install` ends in `ECONNRESET`) and **there is no model API key**. Any
  browser-level or live-model claim goes into `docs/UNVERIFIED.md` as a pending check with the exact
  steps to run it -- never into your report as done.
* Do not `git mv` renames into delete+add; it makes review impossible.

## Interfaces you build on (frozen -- already merged, do not change)

**The store** `adapt.state.LearnerState(sf, settings=...)`:

```python
.ingest(learner_id, EvidenceEvent|[...], module="code")   # idempotent on event_id
.evidence_rows(learner_id) -> list[dict]
.advance_model(learner_id, module="ai1", reason=...) -> int
.rebuild_model(learner_id, titles=...) -> {"equal": bool, ...}
.get_snapshot(learner_id, session_id=None) -> LearnerSnapshot   # immutable, versioned
.put_content(*, concept, kind, payload, topic_id=None, validation=..., module="ai2") -> str
.content_for(concept, kind=None) -> list[dict]            # rejected packs hidden
.set_validation(content_id, status, module="code") -> bool # only code may call this
.remember(learner_id, kind=..., ref=..., module="ai3") -> bool
.memory(learner_id, session_id=None) -> dict[str, list[str]]
.set_preference(learner_id, key, value, source="...")     # stated only, code-only writes
.preferences(learner_id) -> dict
.put_plan(learner_id, plan=..., model_version=..., content_ids=...) -> str
.record_delivery(learner_id, block=..., seen=..., why=...)
.session_state(learner_id, session_id) / .advance_session(..., expect_version=...)
```

`LearnerSnapshot` gives you `model_version`, `mastery` (tuple of `TopicMastery` with `p_know`,
`band`, `n`, `ci`, `early`), `p_know(topic_id)`, `already_explained(ref)`, `already_asked(q)`,
`explained`, `asked`, `this_session` -- and it is genuinely immutable, so do not try to mutate it.

**Write permissions (enforced, not advisory).** `code` writes evidence, scores, approvals,
preferences, session_state, and content *validation only*. `ai1` writes learner_model, hypotheses,
proposals. `ai2` writes content. `ai3` writes plans, deliveries, teaching_memory.
`put_content(module="code")` raises; `set_validation(module="ai2")` raises.

**The Orchestrator** `adapt.state.Orchestrator`: `cycle(learner, session_id=..., events=...,
blocks=..., content_ids=...)`, `replan(..., reason=...)`, `ensure_content(concept, kind=...,
generator=...)`, `plan(...)`, `deliver(...)`.

**The baseline levers** (phase 2, already merged): `adapt.baseline.factors.levers(factors,
preferences=...)` returns `{"step_size", "lead_format_prior", "pace", "review_halflife_prior",
"hint_policy", "confidence_prompting", "tone", "language", "timers_visible", ...}` where each lever
is `{"value", "why", "source"}` with `source` in `{measured, stated, default}`. Every downstream
phase reads levers through this shape.

**HTTP conventions.** Routers live in `adapt/api/routes/`. Errors are
`{"detail": {"error": "<slug>", "message": ...}}`; 422 for refused input, 404 for something that is
not the learner's. Match that shape.

**The naming rule.** `AI 1` = Learner Intelligence (measurement, hypotheses, proposals). `AI 2` =
Educational Intelligence (content, teaching). `AI 3` = the adaptation layer (plans, deliveries,
teaching memory). `adapt/ai/scene_narrator.py` is *the scene narrator*, not AI 3. These names are
internal: no learner-facing string may contain "AI 1/2/3" (there is a test that will catch you).

## Files the integrator owns -- do not edit these

Six agents editing the same file is how parallel work becomes conflict resolution. If you need a
change in one of these, write it in `agent-notes/4-4-adapter.md` as an exact instruction ("add `X` to
the tuple at `api/app.py:152`") and do not make the edit:

`backend/src/adapt/services/__init__.py`; `backend/src/adapt/api/app.py`;
`backend/src/adapt/db/models.py` and `db/init_db.py`; `backend/src/adapt/cli.py` (except Agent 6's
own `config` sub-app); `frontend/src/lib/mock.ts` and `frontend/src/lib/types.ts`;
`docs/FUNCTIONALITY_ROUND.md` and `docs/UNVERIFIED.md`; `backend/src/adapt/state/**`.

**If you need a database table:** define it in your own package's `models.py` (importing `Base` and
`utcnow` from `..db.models`), and add `backend/migrations/versions/P4_<slug>.py` with
`revision = "P4"` and `down_revision = "0008"`, copying the *existence-aware* style of
`0008_baseline_tables.py` (`if "table" not in existing:`). The integrator renumbers and chains them
at merge time; do not guess your final revision id. Note the `db/init_db.py` import your package
needs in `agent-notes`, or `create_all` will not see your table.

## How to work

* Commit early and often. A checkpoint commit that says what does not work yet beats a clean tree.
* Drive real behaviour, not mocks, in tests: the API is `TestClient`-driven, the DB is SQLite in a
  temp dir, and the whole suite runs in under a minute -- use it as your inner loop.
* When you finish, run your gates for real and paste the real output into your report. A gate you
  did not run is not a gate.
* Then push your branch and open a PR against `arena/06d446fc-adapt` titled with your phase, and say
  in the PR body: what is done, what is not, which commit you branched from, and the exact commands
  and outputs of your gates.

**Your report** goes in `agent-notes/4-4-adapter.md` on your branch (create the directory), following
spec section 12: files changed; commands run with their real output; what is not verified and why;
anything you deviated from. Not in `docs/FUNCTIONALITY_ROUND.md` -- that file has one writer.

---

## Read first

Spec sections 8, 2A and 6.5 (the levers you consume). Then
`backend/src/adapt/state/orchestrator.py` (the turn cycle you plug into),
`backend/src/adapt/baseline/factors.py::levers` (the lever shape and where each value comes from),
and `frontend/src/app/lesson/page.tsx` (the existing lesson renderer).

## Deliverables

1. **`adapt/ai/adapter.py`** with the strict contract: input `{levers, subject map, ladder position,
   content pack, recent evidence, preferences, constraints}`; output a validated JSON lesson plan of
   ordered blocks (`text`, `visual`, `worked_example`, `check`, `predict`, `explain`, `reflect`),
   each with content, a `chunk_id`, and a `why` that **references evidence ids and sample sizes**.
2. **Code-enforced limits**, all tested: max words per block from `step_size`; every explanation
   chunk followed by a one-question check; no mid-sentence truncation; reading level and language
   respected; model output rejected if it invents markup, contradicts the content pack, or references
   an example that was not shown. **Adversarial tests are part of the gate** -- malformed, over-long,
   contradictory and injected model output must each be rejected, and rejection must fall back to the
   deterministic template adapter.
3. **The deterministic template adapter** -- a real lesson from the same inputs with no model at all.
   This is the path that runs here, so it is the path to make excellent.
4. **Visual templates** (`adapt/lesson/templates.py` + `frontend/src/components/visual/**`):
   schema-validated SVG for flow, comparison table, timeline, labelled diagram, number line/graph,
   set/Venn, bar/strip model. Invalid specs are rejected and fall back to text **with a note**.
   Approving the `visual_representation` strategy must visibly change what the learner sees.
5. **Transparency**: the "Why this lesson looks like this" panel (evidence, n, ranges, what would
   change it).
6. **The contrast demo** -- a screen taking one concept and two stored profiles, rendering the two
   resulting lessons side by side **with the reasons**. This is the headline demo; make it legible to
   someone who has not read the code.
7. **Gates**: unit tests for limits and fallbacks; the adversarial tests; the contrast screen showing
   visibly different lessons for two seeded profiles -- assert the *difference* in a test, do not
   just eyeball it.

**Dependency.** You consume phase 3's ladder position and phase 4's packs. Code against the frozen
shapes and make **both optional**: with no pack, build the lesson from the authored bank; with no
ladder, start at the default rung. That is what lets your branch be testable before those two merge,
and makes the integrator's merge a wiring job rather than a rewrite. Say in your PR which commit you
branched from.

**You own.** `backend/src/adapt/ai/adapter.py`, `backend/src/adapt/lesson/**`,
`backend/tests/test_adapter.py`, `backend/src/adapt/api/routes/lesson_plan.py`,
`frontend/src/app/contrast/**`, `frontend/src/components/visual/**`.
