# Paste-ready prompt — Milestone 1 kickoff (against `main`)

Everything is in one tree now: `main` = `10191a9`, engine (79 files, 9 migrations, 548 tests) **and**
`docs/v2/` (21 files). Verified on a clean worktree of `main`: `bash scripts/ci.sh
--allow-unsupported-python` is green end to end — ruff, mypy strict, contract guard ("resolve as
written"), migration round-trip, 548 tests.

```
You are starting Adapt 2.0 **Milestone 1** on `main` (`e3a8436`). M0 is complete and verified;
`engine/` and `docs/v2/` are in the same tree, which is the point — the contract the guard checks
against is the contract in your working directory.

Read, in this order, before writing code:
1. `docs/v2/AGENT_M1.md` — the work order: seven increments, stopping points, release gates §8
2. `docs/v2/SPEC.md` §2–§4 — the four planes, the three levels, what a `decide` is
3. `docs/v2/MEASUREMENT.md` §2–§5 — evidence floors, adaptive stopping, the gates
4. `docs/v2/METRICS.md` §1 — abstention at both grains, served rate, denominators
5. `docs/v2/INTEGRATION.md` §3–§6 — the host contract, the error table, the webhook rules
6. `engine/README.md` and `engine/DECISIONS.md` — how M0 is built and why

If any of those is unreadable, stop and say so. Do not improvise the specification.

## PRE-WORK — housekeeping from the merge (do this first, report separately)

1. **Drop the branch dependency in the contract guard.** `check_openapi.py` still carries
   `FALLBACK_DOC_REFS = ("origin/arena/cb97052d-adapt",)` and `ci.sh` fetches that ref. The document is
   now in-tree at `docs/v2/INTEGRATION.md`. Delete the ref fallback: resolve the in-tree path, allow
   `--doc` / `ADAPT_INTEGRATION_DOC` as explicit escape hatches, and **fail closed** if the in-tree
   document is missing — never reach for a branch. A build that depends on somebody's branch surviving
   is a build that breaks on branch cleanup, and CI must not know any branch names.
2. `engine/docs-patches/0001-endpoint-inventory.diff` stays as a review artefact. Do not apply it.
   List the routes it contains in your report; the inventory is a doc change I will make, not you.
3. Confirm the three environmental gaps are still recorded in `engine/KNOWN_GAPS.md`: docker compose
   never run, no 3.12 execution, no Redis. They are not yours to close in this sandbox, but they must
   not silently disappear either.

Stop after the pre-work and report it. Then start increment 1.

## HARD RULES — carried from M0, plus two new ones

1. `backend/`, `frontend/`, `scripts/`, `e2e/` stay read-only. `git diff --stat fb655b3 -- backend
   frontend scripts e2e` must stay empty.
2. Deterministic: injected `Clock`, seeded RNG, no wall-clock reads in business logic, replayable
   decisions.
3. Fail closed: missing or weak config refuses to start, names the variable, exits non-zero.
4. One error shape. **Codes come from `INTEGRATION.md` §6 only** — the guard fails the build otherwise,
   and you may not add a code or a patch to make it pass. Ask.
5. Logs are an allowlist. Never a body, key, learner id, email, provider name, or plaintext of any
   encrypted column.
6. No fabricated data in migrations, fixtures or demo output. No PII.
7. **NEW — do not add a fourth documented exception to an invariant.** M0 already carries two RLS
   exceptions (`api_keys`, `data_key_ids`) and one narrowed AAD rule. Each was justified on its own.
   If an invariant blocks an M1 design, change the design or stop and report; do not add an exception
   and document it after the fact. That pattern is the one thing about M0 I would change.
8. **NEW — G1/G2/G3 are gates, not tests you report.** They run in CI and they fail the build:
   - **G1 golden replay** — a checked-in set of decision outputs, compared byte-for-byte. A changed
     golden fails unless the change carries an approval marker plus a one-line reason in
     `DECISIONS.md`. Regenerating goldens to get green is a defect, not a fix.
   - **G2 guesser** — property test over ≥200 seeded learners at p(correct)=0.2; mastery ≥0.7 at any
     horizon, in any construct, is a release blocker.
   - **G3 citation** — no recommendation without a `claim_id`; an ungated claim is refused at write
     time, not filtered at read time. Test the refusal.

## THE SEVEN INCREMENTS

Per `AGENT_M1.md` §1–§7: connections → observe → L1 mastery → `decide` v1 → metering → abstention
metrics → console shell + thin SDK + sandbox demo.

**Stop and report after each.** Do not run two increments together; each report is reviewed before the
next one starts. If an increment's acceptance cannot be met, stop and say which line and why, rather
than shipping a weaker version and explaining it afterwards.

## WHAT I WILL CHECK IN EACH REPORT

- Increment 3: RMSE ≤0.08 and ≥90% interval coverage after 40 observations on synthetic learners with
  known ground truth — and the guesser result, explicitly.
- Increment 4: byte-identical replay over ≥100 stored decisions, `decision_id` on ≥95% of
  post-decision events, and the reason codes matching `METRICS.md` §1.2.
- Increment 5: **zero** ledger rows for abstentions, errors, retries, test traffic, reads and replays —
  four separate tests, because that is the commercial promise.
- Increment 6: the 30-decision fixture whose denominator must come out at exactly 20, and the
  abstention-rate/served-rate pair rendered from the same denominator.
- Increment 7: the sandbox demo — zero-event learner abstains, six events, cited recommendation with
  an interval — in under three minutes on a clean checkout, with no fabricated numbers anywhere.

## REPORT FORMAT — same six parts as M0

1. Increment and `git diff --stat`.
2. Test counts plus the names of the tests for that increment's acceptance lines.
3. Every deviation, with the reason.
4. Anything in the docs that contradicts the code — report, do not fix.
5. What you left out and which milestone owns it.
6. Any place this prompt was ambiguous enough that you had to choose.

## DO NOT

Port v1's 146 routes, strategy catalogue or provider gateway. Add an LLM, embeddings or a vector store
anywhere in the decide path. Claim a capability above its gate. Add console screens beyond the shell.
Bill anything in the "never billed" list. Touch `docs/v2/` — if the contract needs to change, report it
and I will change it.
```
