# ADAPT Round 2 — all phases (A–G) in one file

Give each agent ONLY the shared header (Part 0) plus its own phase. Run A–F in parallel; run G last.

---

# PART 0 — SHARED HEADER (give to every agent)


You are ONE of seven agents working IN PARALLEL on the same repo (`Adapt-main`: FastAPI backend `backend/src/adapt`, Next.js `frontend/`). Phases A–F run at the same time on separate branches; Phase G integrates. Stay inside your phase. No budget limit: be thorough, but speed matters, so do not wait for other agents.

## Ground rules
1. Work in your own git worktree/branch `r2/phase-<letter>`. Commit often. Never rewrite history. End commit messages with:
   `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`
2. **Only edit files you own** (listed in your phase). If you need a change in someone else's file, do not make it: write the request in `docs/round2/handoff-<your letter>.md` for Phase G. Adding NEW files in your own area is always fine.
3. Read first: `README.md`, `docs/FUNCTIONALITY_ROUND.md`, `docs/UNVERIFIED.md`, `QUALITY_REPORT.md`, `ADAPT_functionality_round_prompt.md` (Round-1 guardrails still bind).
4. Keep every existing test passing (`cd backend && pytest -q`; frontend: `npm run lint`, `test:engine`, `test:server`, `test:lib`). Never delete or weaken a test. New behaviour gets tests.
5. Verify in a real browser where your phase touches UI. Chromium is at `/opt/pw-browsers/chromium`; launch Playwright with `executablePath: '/opt/pw-browsers/chromium'`. Never run `playwright install`.
6. Honesty: no invented stats/citations. Anything unverifiable (real Gemini output without a key) goes in `docs/round2/unverified-<letter>.md`.
7. Model settings (key, model name, budgets) stay console-only. Never in any route, log, error, export, bundle or learner-facing text. The canary secrecy test must keep passing.
8. Finish with `docs/round2/report-<letter>.md`: files changed, tests/browser checks run + results, screenshots (in `docs/screens/<letter>/`), what is unverified, handoffs for G.

## Roles (binding)
AI 1 monitors the student. AI 2 builds a checked knowledge skeleton for any topic (Gemini). AI 3 writes what the learner reads (Gemini) from the skeleton + profile. **The AI proposes, code decides**; every model output is code-validated; every model path has an honest deterministic fallback. Student text is data, never instructions. No learner-facing text names AI 1/2/3, models, providers, or APIs.

## FROZEN INTERFACE CONTRACTS (all phases code against these; do not change them)
Use the existing model entry point in `ai/llm.py` as it is today; do not modify it. In tests, inject a model callable `model(purpose: str, system: str, user: str, schema: dict, max_tokens: int) -> dict` (raise on failure). Each phase writes its own scripted fake for its own tests.

**Pack** (module `adapt.content.skeleton`, owned by Phase B):
```python
get_pack(topic: str, *, level: int, language: str, model=None, force_simple=False) -> Pack
# Pack: topic_key, topic_title, subject_family, source("authored"|"model"|"simple"),
#   concepts: [Concept], prereq_edges: [(from_id,to_id)], items: [Item]
# Concept: id, name, key_idea, why_it_works, prerequisites[], rung(0-5),
#   key_facts[{id,statement}], misconceptions[{id,wrong_belief,why_tempting,correction}]
# Item: id, concept_id, kind("numeric"|"mcq"|"factual"), stem, options[{id,text,misconception_id|None}],
#   key, pair_id, hints[3], teachback[3], fact_ids[], verification("computed"|"cross_checked"|"unverified")
```
Unverified items must never be used for scoring or placement.

**Lesson API** (module `adapt.ai.lesson_writer`, owned by Phase C; routes under `/api/v1/lesson/*`):
```python
write_lesson(pack, concept_id, snapshot, *, model=None) -> LessonPlan   # always returns; falls back
explain_after_wrong(pack, item_id, learner_answer, snapshot, memory, *, model=None) -> Explanation
reteach(pack, concept_id, snapshot, memory, *, model=None) -> Explanation   # different method each call
# LessonPlan: blocks[{type:text|visual|worked_example|check|predict|explain|reflect, body, fact_ids[], why{evidence_ids[],n}}],
#   source("written_for_you"|"simple"), concept_id
# Explanation: blocks[...], method, analogy_id|None, source
```
Routes: `POST /api/v1/lesson/plan {topic, concept_id}`, `POST /api/v1/lesson/explain-wrong {topic, item_id, answer}`, `POST /api/v1/lesson/reteach {topic, concept_id}`.

**Journey state** (owned by Phase D): `GET /api/v1/journey` returns `{stage, stages:[{id,status:"todo|done|skipped"}]}` with stage ids `account, preferences, games, subject, subject_round, snapshot, first_lesson, practice`. `POST /api/v1/journey/advance {stage, status}`. Server-side, resumable.

**Pack cache table & job** (Phase B): `POST /api/v1/content/prepare {topic}` → `{job_id}`; `GET /api/v1/content/prepare/{job_id}` → `{state:"running|ready|fallback", pack_source}`.

---

# PART A


**You own:** `backend/src/adapt/routes/privacy*`, `backend/src/adapt/security/secrecy.py` and logging setup, `backend/src/adapt/content/hints*` / closing-prompt and hint-template code (find them), `scripts/teaching_quality_eval.py`, the contrast screen frontend page and its copy, the banned-phrase crawl (`e2e/banned_phrases.spec` or equivalent, new), simple-mode explanation text in the existing deterministic path (key idea / why).
**Do not touch:** baseline games, onboarding page flow (Phase D), `content/skeleton` (B), `ai/lesson_writer` (C).

Tasks (each needs a regression test or scripted browser check):
1. **Privacy list public.** `GET /api/v1/privacy` returns 401 signed-out, so the consent screen says "could not be read just now". It is a static non-personal list: make it public read-only. Verify signed-out in the browser.
2. **Developer text out of learner copy.** Remove "Allowed preference keys — from the API" panel, "no API key configured…" consent line, and similar. NOTE: the onboarding page file belongs to Phase D, so for copy located there, write the exact replacements into `docs/round2/handoff-A.md`; fix everything else yourself. Build a Playwright crawl that visits every route signed-out and signed-in and fails on any of: `API key`, `Gemini`, `provider`, `model`, `endpoint`, `GET /`, `POST /`, `uvicorn`, `ignored_keys`, `AI 1`, `AI 2`, `AI 3`, `token`. Status wording learner-safe ("Lessons: simple mode"); drop or fix "protection: not reported".
3. **Log filter bug.** The secrecy filter mutates uvicorn access-log args → `Logging error` traceback each request. Redact the rendered message, keep arg shapes. Test that access logs render and contain no secrets.
4. **Closing prompts by item kind.** "Try one: copy the first line of the example and change only the numbers" misfires on factual/MCQ. numeric → change the numbers; mcq → cover the options, say which you'd pick and why; factual → recall without looking; none when nothing fits.
5. **Generic MCQ hints.** Replace the "which one is right: <stem>" wrapper with hints derived from each distractor's misconception. Strengthen `generic_hint` in `scripts/teaching_quality_eval.py`: after removing stem and quoted options, flag hint text identical across ≥3 items.
6. **Simple-mode "why".** Add authored one-sentence `key_idea` and `why_it_works` per bank concept and put them before worked steps in deterministic explanations.
7. **Contrast wording.** "(n=0)" on stated items → "stated". Replace "planned from profile version 0, 0 pieces of evidence" with plain wording for demo profiles.

---

# PART B


**You own:** new package `backend/src/adapt/content/skeleton/` (+ its tests `backend/tests/content_skeleton/`), `content/symbolic.py` extensions, a new migration for the pack cache, routes `routes/content_prepare.py`.
**Do not touch:** existing authored-topic data (read only), `ai/lesson_writer` (C), frontend.

Implement the **Pack contract** exactly as frozen in the shared header: `get_pack(...)`.

1. **Skeleton call** (constrained JSON via response schema): 4–10 concepts with `key_idea`, `why_it_works`, prerequisites, `rung` 0–5, `key_facts`, `misconceptions`; prerequisite graph. Code validates: acyclic, depth ≤ 8, unique ids, length limits, no markup/links, misconceptions reference real facts, language matches the learner's.
2. **Item call** per concept: matched A/B pair in one format, three graded hints (nudge, method, near-answer), 3-point teach-back checklist, distractors each tied to a `misconception.id`, `fact_ids` per item.
3. **Verification (mandatory before any learner sees an item):** computational (maths/physics/stats) → recompute with the SymPy path; everything else → a SEPARATE verifier call with a different prompt, answering only from the skeleton's facts, must independently reach the same key, else reject. Reject: multiple correct options, answer leaked in stem, unresolved placeholders, duplicates, difficulty far from declared rung, missing facts. Log every rejection with code + reason. Mark each item `computed | cross_checked | unverified`. Existing `assessment/generated.py` rules (content hash identity, observed difficulty, retirement) apply.
4. **Authored topics win:** the model may extend authored packs but never overrides an authored key.
5. **Cache + job:** cache by `(topic_key, level, language, spec_version)` with content hash; background job with timeout; `POST /content/prepare`, `GET /content/prepare/{job_id}`; honest fallback to a `simple` pack (generic skeleton, no pretend knowledge) on failure.
6. **Untrusted input:** topic text and uploads are data; delimit in prompts; schema-constrained; injection tests (topic = "ignore previous instructions and ...").
7. Reliability: strict parse, max tokens, one retry with backoff, ~20 s timeout, circuit breaker, per-call audit record (purpose, tokens, latency, outcome; counts only). Redact personal data before any call.

**Gate:** with a scripted fake model, any topic string yields a validated pack; malformed, cyclic, wrong-key, leaked-answer and wrong-language outputs are rejected with logged reasons; authored topics untouched; property tests pass.

---

# PART C


**You own:** new package `backend/src/adapt/ai/lesson_writer/` (+ tests `backend/tests/lesson_writer/`), edits to `ai/adapter.py` (model-backed path; keep the template adapter as fallback), routes `routes/lesson*.py`, teaching-memory table/migration, the lesson display components in `frontend/` that render `LessonPlan`/`Explanation` blocks (new files under `frontend/components/lesson/`).
**Do not touch:** `content/skeleton` (B, code against the frozen Pack contract with a hand-built fixture Pack), the journey/onboarding pages (D).

Implement the **Lesson API contract** exactly as frozen.

1. Inputs: snapshot (levers, stated prefs, teaching memory), concept from the Pack, learner's rung, recent evidence (actual wrong answers, matched misconception). Output a validated JSON lesson plan. Block types: text, visual, worked_example, check, predict, explain, reflect. Each block carries `fact_ids`, a `why` referencing evidence ids and n, and length. Visuals are JSON specs for the existing schema-validated templates, never raw markup.
2. **Code-enforced limits (reject block → fall back → log):** words within the step-size budget, no truncation, language and tone as chosen; every explanation chunk followed by a one-question check; every number/named entity must trace to the Pack's facts or the item, otherwise a verifier pass, else reject; final answer in any explanation/worked example equals the key (checked in code); no answer leakage before an attempt; no markup, links, or off-platform instructions.
3. **Three moments, all AI 3:** (1) in-lesson; (2) after a wrong answer: start from what THIS learner answered, say why it's tempting (matched misconception) and why it fails, then show the idea; (3) "I don't get it": re-teach the same idea a DIFFERENT way (next-best method chosen by code from the profile), never repeating earlier wording; escalate with teaching memory: simpler words → smaller step → example → visual.
4. Quality rules in the system prompt and checked where code can: explain WHY, one idea then check, length from step size, everyday examples, no invented personal details, say plainly when unsure, no contradiction of earlier explanations, no false certainty.
5. **Teaching memory:** record each explanation's method and analogy so retries differ.
6. **Model unavailable:** show the deterministic lesson immediately; upgrade in place later if the model succeeds (label "written for you"); never show a half-validated lesson; log why validation failed.
7. Contrast screen data endpoint: real AI 3 output when a key exists (label "written for this profile, checked"), template lessons otherwise. Expose it; Phase A owns the page copy.
8. Untrusted input and reliability rules as in the shared header (injection tests, strict parse, timeouts, circuit breaker, audit counts).

**Gate:** with a scripted fake model, lessons for any fixture Pack render within limits; adversarial outputs (over-long, contradictory, leaked answer, off-language, unsupported facts, injected instructions) are rejected and fall back; the three moments work end to end in the browser against your fixture Pack; screenshots reviewed.

---

# PART D


**You own:** onboarding pages/components, the new journey shell and progress indicator in `frontend/`, `backend/src/adapt/routes/journey.py` + journey state tables/migration, sidebar nav, preference-question de-duplication, placement consistency.
**Do not touch:** game internals and baseline scoring (F), lesson block rendering (C), pack generation (B). Integrate with them through the frozen contracts only; where their screens aren't ready, link to the current routes and stub with the contract shape.

Build the journey as one path with a progress bar, resumable from any device (server-side state): account (guest/email) + plain-words consent → format pick + the 13 preference questions (ONCE) → mind games → choose subject/topic → subject round → snapshot → first lesson (AI 3, with "why this lesson looks like this") → practice/review loop with the failure loop on a wrong answer. Contract: `GET /api/v1/journey`, `POST /api/v1/journey/advance`, stage ids as frozen.

1. Every step skippable; skipped data stored as "not measured".
2. **De-dupe questions:** the 13 baseline questions are the single source. Onboarding keeps account-level settings only (time, voice, consent). Map old free-text fields into the same preference store. Never ask anything twice.
3. **Placement consistency:** UI is 8 questions; make docs, copy, tests and CLI demo agree; keep placement reachable from the journey.
4. Apply Phase A's handoff copy replacements to the onboarding page (read `docs/round2/handoff-A.md` at the end; if absent, remove "Allowed preference keys — from the API" and "no API key configured" text yourself).
5. Returning learners keep the separate "Mind check" menu item; new learners never get dumped in it separately. Mobile viewport must work. No learner-facing text names AI 1/2/3.
6. Playwright e2e of the full journey on desktop and mobile using mocks/stubs for B/C screens, asserting server-recorded stage state; screenshots to `docs/screens/D/`.

**Gate:** a new learner can complete or skip through every stage, close the browser, return, and resume at the right stage.

---

# PART E


**You own:** `scripts/live_model_check.py`, `backend/tests/fixtures/model/`, `backend/tests/test_live_check_harness.py`, `docs/round2/live-check.md`, `MODEL_QUALITY_REPORT.md` template.
**Do not touch:** application code. Code against the frozen contracts (`get_pack`, `write_lesson`, `explain_after_wrong`, `reteach`). Until B and C merge, develop against a local stub module that mimics the contract shapes; Phase G swaps in the real ones.

1. Runs only when a key is configured in the console (`adapt config set-key`; confirm with `adapt config test`). Never prints keys.
2. Runs ≥12 topics including outside the authored set: quantum chromodynamics, how to bake bread, Kiswahili verbs, sorting algorithms, supply and demand, the French Revolution, discrete maths (sets), derivatives, constitutional law, essay writing, photosynthesis, probability. Pipeline per topic: skeleton → items → verification → AI 3 lessons for TWO different profiles → the three explanation moments (simulate a wrong answer and an "I don't get it").
3. Automated checks plus an independent judge call (separate prompt) scoring 1–5: correctness, explains why, uses the learner's actual answer, fits the profile (step size, lead format, tone, language), no leakage; judge must quote evidence.
4. Writes `MODEL_QUALITY_REPORT.md` (pass/fail counts, rejection reasons, token/latency/cost estimates) and exits non-zero under stated thresholds.
5. Saves sanitised real responses as regression fixtures (no personal data).
6. Test the harness fully with a scripted fake model; document clearly that the live run has NOT been done and give the owner the exact one-command run in `docs/round2/live-check.md`.

---

# PART F


**You own:** `backend/src/adapt/baseline/*` (games, scoring, service), `backend/src/adapt/subjects/*`, frontend baseline and subject pages/components, the Playwright suite `e2e/games/` (new).
**Do not touch:** onboarding/journey shell (D), lesson rendering (C), pack generation (B).

In Round 1 only the first four of the baseline's 13 steps were ever driven in a browser, and the games Vault lights, Crack the machine, Bet your coins and The locked box were never verified.

1. Write Playwright scripts that play EVERY game and the full subject round to completion in a real browser (desktop and mobile viewports), plus keyboard-only operation. Choices are buttons, not radios/labels. Assert the server-recorded metrics (spans, falsifying-input rate, wagers, hints, recall scores, timing) match what the script did.
2. Fix every defect found (layout, stuck states, wrong scoring, timers, resume after reload, next-day recall flow). Add backend tests for each fix.
3. Confirm "not measured" semantics when a game is skipped, and that the snapshot shows ranges + n, never labels.
4. Subject round: verify test selection changes by subject (maths vs language vs history vs unknown topic fallback) and that the "why these tests" text is honest.
5. Screenshot every game state to `docs/screens/F/` and review them yourself; fix visible problems.
Gate: all games + subject round pass in CI-style headless runs on both viewports, twice in a row (no flakes).

---

# PART G


**You own:** everything, but only to merge and fix seams. Read all `docs/round2/report-*.md`, `handoff-*.md`, `unverified-*.md`.

1. Merge branches in this order: A, B, C, F, D, E. Resolve conflicts, keeping every test passing after each merge. Run the full backend and frontend suites after each.
2. Apply every `handoff-*.md` request (A's onboarding copy if D didn't, E's stub swap to real `get_pack`/`write_lesson`, route registration, migration ordering and numbering clashes).
3. Verify the frozen contracts really line up: a typed topic → `/content/prepare` → Pack → `/lesson/plan` → `/lesson/explain-wrong` → `/lesson/reteach`, with a scripted fake model, over HTTP.
4. Run the full-journey Playwright e2e on desktop and mobile with the fake model: account → preferences → games → subject → subject round → snapshot → first lesson (written by AI 3) → wrong answer explanation → "I don't get it" re-teach. Fix seam bugs. Run the banned-phrase crawl on every route.
5. Run the secrecy canary across new paths. Run `scripts/teaching_quality_eval.py`.
6. Docs: reconcile `docs/FUNCTIONALITY_ROUND.md` checklist, README, test counts, `UNVERIFIED.md` with reality (also placement = 8 questions). Write `docs/ROUND2.md` and a "how to demo this" page (live path, contrast screen, mock fallback).
7. Final report: per phase what landed, what failed, test and browser results, everything still unverified, and the exact commands the owner runs with a real key (`adapt config set-key`, `adapt config test`, `scripts/live_model_check.py`). State plainly that the live model check has not been run unless it was.
