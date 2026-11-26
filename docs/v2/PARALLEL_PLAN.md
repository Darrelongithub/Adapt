# Parallel workstreams — the split, and the rules that keep it mergeable

Multiple agents help only where **write sets are disjoint**. Inside `engine/src/` they are not: every
milestone touches `decide`, `mastery`, `observe`, `errors.py`, `main.py` and the migration chain, and
two agents in there produce conflicting Alembic heads, duplicated error codes and a merge nobody can
review. So: one agent owns the engine, and everything else runs beside it.

## The four workstreams

| | Workstream | Owns | Touches `engine/src/`? | Starts |
|---|---|---|---|---|
| **A** | **Engine** (one agent, serial) | `engine/**` | yes — exclusively | now, increments 6 → 7, then M2 |
| **B** | **Reference host** | `reference-host/**` | no — consumes the HTTP contract | now |
| **C** | **Conformance suite** | `packages/conformance/**` | no — 15 checks against a running host | now, but only checks 1–12 exist yet |
| **D** | **Console** | `console/**` | no — reads `/v1/*` with a control-plane key | after increment 7's shell |
| **E** | **Adversarial review** | nothing; writes a report | no | now, and every increment |

**B is the highest-value parallel work.** `INTEGRATION.md` is written, the engine serves `/v1/decide`,
and a host app is the one artifact that proves the contract is usable — a second agent can build it
today against a frozen doc, and any place it gets stuck is a contract defect I need to know about
before M2. That is genuine parallel progress, not parallel churn.

**C can start but will stall.** A conformance suite needs routes that do not exist yet (probes, export,
erasure). Building checks 1–12 now, against what exists, is fine; the rest waits.

**E is worth more than a second builder.** A read-only agent whose only job is to break the branch —
steal another tenant's row, bill an abstention, forge a webhook, get a chance responder above 0.7 —
costs nothing to merge because it produces a report, and it attacks exactly where my reviews have found
every defect so far.

## Rules that make it mergeable

1. **One agent per tree.** Never two agents in `engine/`. If engine work must be parallel, it is not
   parallel — it is sequential with better scoping.
2. **Reserve migration numbers up front.** Engine owns `0016–0035`. Any other tree that needs schema
   gets its own table namespace and its own directory; it does not add to the engine's chain.
3. **Error codes are mine.** No agent adds a code or a `details` key to `INTEGRATION.md` §6. They
   report the need, I add the row, the guard then passes. A code added by the implementer makes the
   guard mark its own homework (already happened once, with `minor_not_supported` via a patch).
4. **The contract is frozen per milestone.** B, C and D code against the doc as of the merge commit,
   not against the engine branch. If the doc must change, that is a PR against `main`, reviewed, and
   then everyone rebases.
5. **No agent edits `docs/v2/`.** Report the gap; I write it. This has caught a dozen contract defects
   that would otherwise have been silently "fixed" by whoever hit them.
6. **Every agent stops and reports per increment**, same six parts. Parallelism without those
   checkpoints is how you get four half-finished features and no reviewable decision.
7. **Merge cadence: on acceptance, not on completion.** A workstream that has been green for three
   increments and unmerged for two weeks is a liability, not progress.
8. **`docker compose up`, the 3.12 image and Redis stay one owner's job** — whoever has Docker. Two
   agents "verifying" the same environment produce two conflicting reports and no verified claim.

## What does not parallelise, at any agent count

* **The evidence programme.** L2 and L3 are earned by a tenant's own learners accumulating data over
  weeks and months. No number of agents compresses a calendar.
* **Review.** One increment per review cycle, by one reviewer, and every increment so far has produced
  at least one real defect — a forced-RLS conflict, a balance identity that lost forfeited credits,
  hashes leaving the database, flags that punished good learners. Throughput is bounded by catching
  those, not by writing more code.
* **The pricing conversations.** Five of them, with institutions, before M2's shape is locked.

## The order I would run it in

```
now        A: increment 6 → 7        B: reference host        E: adversarial pass 1
then       A: merge M1 to main       B: finish, run against merged main
then       C: conformance 1–12       D: console shell          E: adversarial pass 2
then       A: M2 evidence            B: two-host integration test
```

Two builders and one attacker is the useful shape. Three builders is three times the review debt with
one reviewer.
