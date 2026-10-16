# Bug register

Defects found by the cleanup-and-scan round of **2026-10-16**, on branch
`arena/ce396360-adapt`. This file is the register the round was asked to keep: what was
wrong, how it was proved, what changed, and what was deliberately left alone.

The companion record of *everything* the round touched — including files deleted — is
[`AUDIT.md`](AUDIT.md). This file is only the defects.

**Method.** Nothing below is a guess. Each entry names the command, request or test that
produced it. The starting point was a full run of what the repo already had:

| Check | Before this round | After |
|---|---|---|
| `.venv/bin/python -m pytest backend/tests -q` | 1337 passed, 2 skipped (130 s) | **1341 passed, 2 skipped** (128 s) |
| `npm run lint` (contract + `tsc --noEmit`) | exit 0, 117 routes / 103 calls | exit 0, 117 routes / 103 calls |
| `npm run test:lib` / `test:engine` | 42 pass / 15 pass | 42 pass / 15 pass |
| `npm run build` | exit 0, 19 static pages | exit 0, 19 static pages |
| `ruff check backend/src scripts --select F401,F841,F811,F821` | 10 findings | **0** |

So the suite was already green, and stayed green (+4 tests, all of them regression tests for
defects below). That is the point worth making first:
**every defect below was invisible to the existing checks.** They were found by running the
app and reading what came back, not by running the tests.

---

## P1 — CSRF was enforced on safe methods, which broke two live screens

**Where.** `backend/src/adapt/api/deps.py` (`require_csrf`), reached through `rate(scope)`.

**What happened.** `rate()` depends on `require_csrf`, and `require_csrf` checked the token
on *every* method. So every rate-limited **read** inherited a rule meant for mutations.
Six GET routes were affected:

```
content.py:86   topic_breakdown      GET /api/v1/content/breakdown
content.py:104  concept_pack         GET /api/v1/content/pack
content.py:133  graded_hint          GET /api/v1/content/hint
lesson.py:741   list_frozen_concepts GET /api/v1/lesson/concepts
tutor.py:151    reveal               GET /api/v1/items/{id}/reveal
learning.py:372 retention_check      GET /api/v1/review/check   (false positive — see below)
```

Measured against a running backend with a valid session cookie and no CSRF header:

```
GET /api/v1/content/pack?topic=fractions&concept=adding   -> 403 {"detail":"csrf token missing or wrong"}
GET /api/v1/content/breakdown?topic=fractions             -> 403 {"detail":"csrf token missing or wrong"}
GET /api/v1/lesson/concepts?topic=fractions               -> 403 {"detail":"csrf token missing or wrong"}
--- same requests with x-csrf-token: <token> ---
GET /api/v1/content/breakdown?topic=fractions             -> 200
GET /api/v1/lesson/concepts?topic=fractions               -> 200
```

**Why it reached a learner.** `frontend/src/lib/content.ts` fetches both content routes
through `get()`, and `get()` calls `api(path, { query })` — which attaches the CSRF token
only when `method !== "GET"` or the caller opts in with `csrf: true`. It does not opt in,
because a safe method should not need to. So `fetchPack()` and `fetchBreakdown()` were
**403 in the running app**, and `frontend/src/app/subject/page.tsx:207,214` — the subject
screen's breakdown-and-pack load — was the broken caller.

`first-lesson/page.tsx` had already worked around the same trap for `lesson/concepts` by
calling `api(..., { csrf: true })` with a comment explaining that "the rate guard demands
the CSRF token on every method". That comment was an accurate description of a bug being
treated as a contract.

**Why the suite could not see it.** The `learner` fixture in `backend/tests/conftest.py`
returns `headers = {"x-csrf-token": ...}`, and every test that reads a content route passes
those headers to its GET — e.g. `test_content_engine.py:524,532,540,577,615`. The one shape
that was never tested is the one the browser sends: an authenticated GET with no token.

**Fix.** `require_csrf` now checks only mutating methods, reusing the `MUTATING` set that
`security/middleware.py` already defined:

```python
if row is not None and request.method in MUTATING:
```

This is what the module docstring always claimed ("**Mutations** need the double-submit CSRF
header") and what the existing test is named (`test_mutations_require_the_csrf_header`).

**Is that a security regression?** No, and this was checked rather than assumed. Cross-site
*reads* were never what the GET check was protecting:

* the session cookie is `SameSite=Lax` (`deps.py:121,130`), so a cross-site subresource
  request never carries it;
* a response an attacker cannot read is not a CSRF risk — CSRF is about unintended state
  change, and `GET /items/{id}/reveal` documents itself as "nothing here is a persistent
  change";
* quota burn, the one thing a forged GET could do, is what the token bucket in `rate()` is
  for, and that is untouched.

Proved with a cookie-free client (all 401, "not signed in"):

```
GET /api/v1/content/breakdown?topic=calculus                  -> 401
GET /api/v1/content/pack?topic=calculus&concept=calc_rate     -> 401
GET /api/v1/content/hint?topic=calculus&concept=calc_rate&index=0 -> 401
GET /api/v1/lesson/concepts?topic=calculus                    -> 401
GET /api/v1/items/any/reveal                                  -> 401
GET /api/v1/content/breakdown + x-csrf-token: forged          -> 401
```

Authentication is `student_from_request`'s job and it still does it; CSRF was never the
authn check.

**Tests added** (`backend/tests/test_api_learner_loop.py`):
`test_a_rate_limited_read_needs_no_csrf_header` and
`test_csrf_is_enforced_on_every_mutating_method`.

**Mutation-checked.** With `and request.method in MUTATING` removed, the new read test fails:

```
FAILED test_a_rate_limited_read_needs_no_csrf_header
1 failed, 3 passed
```

Restored: `4 passed`.

---

## P1 — a test claimed to check anonymity using a cookie jar that was never empty

**Where.** `backend/tests/test_content_engine.py::TestContentRoute::test_the_route_refuses_an_unsigned_request`

**What happened.** It read:

```python
# The CSRF middleware runs before the auth dependency, so an anonymous request is refused
# with 403 rather than 401. Either code is a refusal; asserting the exact one here would be
# asserting the middleware's order rather than the route's behaviour.
assert client.get("/api/v1/content/breakdown?topic=calculus").status_code in (401, 403)
```

The reasoning is wrong twice, and the second error made it a **false assurance**:

1. `client` is `@pytest.fixture(scope="session")` and `TestClient` keeps one cookie jar for
   the whole session. By the time this ran, an earlier `learner` fixture had POSTed to
   `/students` and left a valid `adapt_session` cookie in it. The request was never
   anonymous.
2. What was refusing it was the CSRF-on-GET check from the entry above, not authentication.

Fixing P1 made this test fail with `assert 200 in (401, 403)` — which is the test correctly
reporting that it had been measuring the wrong thing.

**This is not a new pattern in the repo; it is the one place that missed an existing one.**
Two other tests already handle the hazard explicitly, and say so in their docstrings:

* `test_api_learner_loop.py::test_private_routes_refuse_anonymous_callers` — "The `client`
  fixture is session-scoped, so its cookie jar carries whatever an earlier test in the same
  run signed in. That made this check pass or fail on test *order*…"
* `test_round2_phase_d_journey.py::test_advance_needs_a_session`

Both clear the jar, assert, then restore the exact cookie objects. **Fix:** this test now
follows the same idiom instead of inventing a second one, and asserts `401` specifically plus
`detail == "not signed in"`, plus that a forged token changes nothing.

---

## P2 — the status chip made two round trips on every page, one waiting on the other

**Where.** `frontend/src/components/chrome.tsx` (`StatusChip`)

**What happened.**

```js
const h = await get<Health>("health");   // wait for this…
if (!alive) return;
setHealth(h.data); setError(h.error);
const s = await get<Status>("status");   // …before starting this
```

`StatusChip` is rendered by `Chrome`, which wraps every route in `app/layout.tsx`. The two
reads are independent — `health` and `status` share nothing — so the second paid a full round
trip it did not owe, on all 16 pages.

**Fix.** `Promise.all([get<Health>("health"), get<Status>("status")])`, one `alive` check
after both. Same state updates, same teardown semantics, one round trip instead of two.

**Measured context, so this is not oversold.** The backend is not the slow part. Against a
running API with a seeded learner, every endpoint answered in single-digit-to-50 ms:

```
/api/v1/me 8.8ms   /api/v1/dashboard 31.5ms   /api/v1/journey 24.2ms
/api/v1/practice 51.2ms   /api/v1/assessment/diagnostic 27.3ms   /healthz 3.4ms
```

and Next served every route in 5–9 ms TTFB (`/` 7.4 ms … `/subject` 8.2 ms). So this was a
client-side waterfall, not a slow server. See **Left alone / F1** for the largest remaining
perceived-latency item.

---

## P2 — `/lesson/concepts` called a topic generic when the chain had it authored

**Where.** `backend/src/adapt/api/routes/lesson.py` (`list_frozen_concepts`)

**What happened.** The two routes that answer "what can I learn about this topic?" resolve it
through different subsystems: `/content/breakdown` through this checkout's authored chain,
`/lesson/concepts` through the frozen skeleton. When the skeleton had nothing prepared,
`list_frozen_concepts` accepted its generic study plan and reported the topic as unmatched —
for topics the authored chain covers completely. Measured on a running backend:

```
GET /api/v1/content/breakdown?topic=calculus  -> available=true, "4 concepts from the authored chain"
                                                 [calc_rate, calc_limit, calc_derivative, calc_rules]
GET /api/v1/lesson/concepts?topic=calculus    -> source=simple, topic_match=false
                                                 [task, information, method, check]
```

That contradicts the route's own docstring — "``source`` is ``authored`` (the typed topic has
its own checked material), ``simple`` (the topic has no detailed lesson offline…)" — and it
reached a learner. `/first-lesson` asks this route what to offer, so typing **calculus**
presented "Read the task", and the lesson that came back was:

```
POST /lesson/plan {topic: calculus, concept_id: task}
  -> "Say what the task asks you to find or explain."  /  check: "What is the main idea?"
```

The mechanism: `_topic_from_frozen()` returned a pack whose `source` was `"simple"`, so the
`elif raw_source == "simple"` branch set `source`/`topic_match` and never consulted the authored
chain. The `_current_authored_pack(topic, "")` bridge above it could not help either — it calls
`breakdown.concept("")`, and `concept()` (`content/breakdown.py:106`) matches on id or title, so
an empty id returns `None` and the bridge 404s. It is only reached when the frozen lookup
returns `None`, which for `calculus` it does not.

**Was it safe to just list the authored ids?** Checked, not assumed — offering an id the lesson
route cannot resolve would trade a wrong label for a dead end:

```
POST /lesson/plan {topic: calculus, concept_id: calc_rate}  -> 200, 25.6 ms
  "How much one quantity changes for each unit change in another: speed is the rate at which
   distance changes as time passes."  check: "A tank holds 40 litres and loses 3 litres every
   minute. How much is in it after 6 minutes?"  (item pk_calc_rate_r3)
GET  /content/pack?topic=calculus&concept=calc_rate         -> 200, "5 of 5 rungs authored"
```

The authored concepts teach properly, so the fix widens what the screen can reach.

**Fix.** Added `_authored_concepts(topic)`, which resolves through `content.breakdown.
breakdown_for` — *the same resolver `/content/breakdown` uses*, so the two routes cannot
disagree by construction. The `simple` branch now tries it first and only falls back to generic
when the chain really has nothing. Any resolution failure is treated as "not authored", so the
helper cannot turn a working generic answer into a 500.

Verified against a running backend after the fix:

```
calculus   source=authored  match=True   [calc_rate, calc_limit, calc_derivative, calc_rules]
biology    source=authored  match=True   [bio_cell, bio_transport, bio_energy, bio_systems]
algebra    source=authored  match=True   [alg_terms, alg_expand, alg_solve, alg_rearrange]
fractions  source=simple    match=False  [task, information, method, check]   <- correctly unchanged
physics    source=simple    match=False  [task, information, method, check]   <- correctly unchanged
```

and `/content/breakdown` agrees on both halves (`calculus` available=true, `fractions`
available=false).

**Tests added** (`test_content_engine.py`):
`test_concept_discovery_agrees_with_the_breakdown_about_authored_material` — asserts the two
routes agree for three authored topics *and* that `POST /lesson/plan` teaches the first concept
each offers; and `test_a_topic_with_no_authored_chain_still_reports_itself_as_generic`, the
control, without which "always claim authored" would also pass.

**Mutation-checked.** With the authored bridge removed:

```
FAILED test_concept_discovery_agrees_with_the_breakdown_about_authored_material
1 failed, 1 passed      <- the control still passes, as it should
```

Restored: `2 passed`.

---

## P2 — two radius systems: a 12px Tailwind layer on a 2px design contract

**Where.** 8 files under `frontend/src`; the contract is in `app/globals.css`.

**What happened.** `globals.css` states a design language and then keeps it: `border-radius:
2px` appears 12×, `4px` 6×, and the shared components follow — `Card` is `rounded-[2px]`,
`Tag` is `rounded-[2px]`, `.panel` is `2px`, `.band-track`/`.band-fill` are `2px`. But a
later layer of pages hand-rolled their containers with Tailwind's scale instead:

```
rounded-xl (12px) ×5   rounded-lg (8px) ×5   rounded-md (6px) ×18   rounded (4px) ×43
```

The split was per-screen, so it was visible as exactly the "UI mismatch" reported: cards on
`/first-lesson`, `/subject`, `/contrast` and in `LessonRenderer` were pillowy at 12px with
solid borders, while `/proposals`, `/review` and `question.tsx` — built from `Card`/`Tag` and
the `pz-`/`ledger-`/`receipt-` classes — were sharp at 2px with dashed borders.

Measured before: `first-lesson` 9 non-contract radii, `subject` 9, `LessonRenderer` 6,
`contrast` 3. After: **70 × `rounded-[2px]` and 2 × `rounded-full` across the whole tree.**

**Fix.** Normalised to `rounded-[2px]` in `first-lesson`, `subject`, `LessonRenderer`,
`contrast`, `lesson`, `review`, `diagnose`, `progress`, `teaching`, `visual`, `account-gate`,
`scenes/index`, `scenes/stages`, `ui.tsx`.

**Two things were preserved on purpose, not swept:**

* **Genuine circles stay round.** `progress/page.tsx:242` (`h-2 w-2 rounded-full`, a status
  dot) and `teaching.tsx:230` (`h-5 w-5 rounded-full`, a circular marker) are circles, and
  `globals.css` itself uses `border-radius: 50%` for dots. The script keyed off a co-located
  `h-N w-N` pair so these were not flattened.
* **Pills that were really tags became 2px.** `LessonRenderer.tsx:176` and
  `first-lesson/page.tsx:317` used `rounded-full` on `px-2 py-1` / `px-3 py-1.5` bordered text
  — i.e. the `Tag` shape at the wrong radius, so they were converted.

Verified: `npm run build` exit 0, `tsc --noEmit` exit 0, contract check exit 0, 19 static
pages, no malformed class strings (`rounded-[2px]-`, `rounded-rounded` → zero matches).

---

## P3 — a learner-facing error message pointed at a file that has never existed

**Where.** `frontend/src/components/ui.tsx:279` (`Err`, the `status === 0` branch)

**What happened.** When the backend is unreachable, every screen renders this:

> Nothing you did caused this and nothing has been lost … (If you are running ADAPT yourself,
> `docs/OPERATIONS.md` has the start-up command.)

There is no `docs/OPERATIONS.md`, and `git log` shows there never was. So the one message
shown at the exact moment someone needs the start-up command sent them to nothing.

**Fix.** Repointed to the "Run it" section of `README.md`, which does carry the commands
(`README.md:82`, `npm run dev` at :97, `adapt host` at :221).

---

## P3 — 19 more source references pointed at missing files

Two distinct causes, one symptom: a path in a docstring or comment that resolves to nothing.

**(a) Referenced files that never existed** — 6, all pre-dating this round:

| Source | Pointed at | Now points at |
|---|---|---|
| `adapt/__init__.py:3` | `docs/BLUEPRINT.md` | `adapt-web-application-prompts.zip` (the actual blueprint artifact) |
| `adapt/ai/prompts.py:15` | `docs/MASTER_PROMPTS.md` §3 | the same zip, prompt 3 |
| `adapt/db/init_db.py:4` | `docs/DATA_MODEL.md` | `adapt/db/models.py` + `backend/migrations/versions` — the model itself, with no prose copy to go stale |
| `adapt/assessment/numbas.py:21` | `docs/VERIFICATION.md` | `docs/UNVERIFIED.md`, which is the surviving register of unrun checks (§"Numbas and the resource providers") |
| `adapt/security/auth.py:3` | `docs/SECURITY.md` | `docs/AUDIT.md`, which carries the threat-model findings |
| `adapt/security/ratelimit.py:209` | `docs/API-QUOTAS.md` | `docs/EFFICIENCY.md`, which documents the guard tables these numbers bound |
| `tests/test_teachback_master_prompt.py:4` | `docs/MASTER_PROMPTS.md` | the zip |

**(b) References orphaned by this round's deletions** — 13, in `P2_subject_round.py`,
`routes/subjects.py` (×2), `security/__init__.py`, `subjects/models.py`,
`fixtures/model/contract_stub.py` (×2), `test_subjects.py`, `scripts-ui-probe.mjs`,
`tests-e2e/banned_phrases.mjs` (×3), `tests-e2e/journey.mjs` (×2), `live_model_check.py`,
`README.md` (×4), `docs/UNVERIFIED.md` (×2), `docs/EVIDENCE.md`, `MODEL_QUALITY_REPORT.md` (×2).
The `agent-notes/*` pointers went to `docs/AUDIT.md`; the `docs/round2/unverified-*.md`
pointers went to `docs/UNVERIFIED.md`.

One related find: `numbas.py` told the reader to run `python scripts/verify_numbas.py` — a
script that is not in the repository. Rather than repoint it at something that is not
equivalent, the docstring now says plainly that no such check script exists and that the table
is therefore unverified.

**Verification.** A repo-wide scan resolving every `docs/`, `agent-notes/` and `scripts/`
path mentioned in `.py/.mjs/.ts/.tsx/.md/.toml/.sh/.ini/.json` against the files that exist:

```
remaining dangling refs: NONE
```

---

## P3 — dead code that read like an unfinished intention

| Where | Finding | Resolution |
|---|---|---|
| `baseline/factors.py:320` | `first_guess_wrong = [g for g in guesses[:3] if not g.correct]` computed, never read | Removed. Not a missing feature: the `detail` dict two lines down already reports the same fact as `first_guess_correct`. Comment left saying so. |
| `cli.py:1407` | `item_ids = {it.id for it in items}` built from a query, never read | Removed. The nearby `topic_ids` **is** used (`used_items` filters on it), so this was a leftover, not a validation that was forgotten. |
| `cli.py:1194` | `except ConfigSecretError as exc:` — `exc` bound, never used | Binding dropped. The generic message is deliberate on the credential path, so the exception text is still not printed; a comment now says why. |
| `baseline/factors.py:20` | `typing.Iterable` imported, unused | Removed |
| `content/pack.py:50` | `typing.Sequence` imported, unused | Removed |
| `content/pipeline.py:34,35,37` | `assessment.worked as W`, `symbolic as S`, `pack.build_pack` — unused | Removed |
| `baseline/service.py:27` | `.games.codewords_pairs` — unused | Removed |
| `cli.py:1350` | `.db.engine.get_db` — unused | Removed |

Each unused import was checked against the module's `__all__` before removal, so none was a
deliberate re-export: `pipeline.py:40` and `service.py:882` both declare an `__all__` that
does not include the names removed.

`ruff check backend/src scripts --select F401,F841,F811,F821` → **All checks passed!** (was 10
findings).

---

## P3 — a conditional with two identical branches

**Where.** `frontend/src/components/scenes/stages.tsx:231` (`CpuStage`)

```js
${k < dataAt ? "rounded-[2px] px-1" : "rounded-[2px] px-1"}
```

Both arms the same, so the ternary was dead logic sitting next to a *real* ternary on the next
line that does vary (`bg-[var(--panel)]` / `bg-[var(--bg)]`). Collapsed to the single class
string; the meaningful branch is untouched.

---

## Reviewed and judged **not** bugs

Recorded so the next scan does not re-open them, with the reason each was closed.

**`zip()` without `strict=` (B905, 12 sites).** Silent truncation is a real hazard, so the
three that touch learner-visible data were traced rather than dismissed:

* `assessment/teachback.py:586,588` — `merge_model_result(scored, judgement.points)` zips the
  model's points against the rule grader's, then recomputes `met_count`, `score` and `verdict`
  off the result. A length mismatch would silently change the denominator of a learner's
  teach-back score, so this one mattered. It cannot happen: `_validated_points` rejects the
  payload at line 493 with `wrong_point_count` unless `len(raw_points) == len(criteria)`, and
  the caller (`ai/ai2.py:687,691`) builds **both** sides from the same `criteria`.
* `api/routes/lesson_plan.py:207` — `zip(profiles, values)` where `values` is written by index
  from the same loop over `profiles`; equal by construction, and followed by
  `assert value is not None`.
* `content/pack.py:354` — `points` is sliced to `TB.CHECKLIST_SIZE` immediately above and
  `criteria` is the matching slice.

Left as-is: adding `strict=True` would convert a proved-safe invariant into a new crash path,
and adding `strict=False` is churn. The invariant is documented here instead.

**`except Exception` (BLE001, 13 sites) and `try/except/pass` (S110, 10 sites).** Real smell,
but each one is a deliberate degradation boundary in this codebase — a provider that is down
must not take the lesson screen with it. Not changed without a specific failure to point at;
the two `S110` sites in `content/skeleton/service.py:213,231` are the ones most worth a
logging pass if this is revisited.

**`Depends(...)` in argument defaults (B008, ~30 sites).** This is FastAPI's documented
dependency-injection idiom, not a mutable-default bug. Ruff's rule does not model FastAPI.

**Unused function arguments (ARG001).** Overwhelmingly FastAPI route signatures (`request`,
`subject_row`, `pack`) where the parameter is part of the injected contract. Not defects.

**3258 × E501 (line-too-long)** and the remaining UP/SIM/RUF style findings: formatting, not
behaviour. Out of scope for a bug round, and reformatting 3258 lines would bury every real
diff above in noise.

---

## Left alone, and flagged

**F1 — the opening splash blocks the landing page for 4.2 s.**
`frontend/src/components/opening.tsx`. On `/`, the app shell is rendered `hidden` and `inert`
behind a full-screen dialog until `setTimeout(dismiss, 4200)` fires, then a further 500 ms
exit transition — so roughly **4.7 s** before the home page is interactive on a first visit in
a session.

This is the single largest perceived-latency item in the app, and it is the first thing anyone
measuring "the app is slow" will meet. It is **not** listed as a defect, and it was not
changed, because it is deliberate product behaviour rather than a mistake, and it is already
built carefully:

* it is once per session (`sessionStorage["adapt:seen-opening"]`), not per navigation;
* it is skippable by click, by any keypress, and by an explicit `Skip →` button that receives
  focus;
* it honours `prefers-reduced-motion`, dropping to 900 ms;
* blocked storage cannot trap anyone behind it — the `catch` sets the decision to `done`;
* the shell and its data requests mount *underneath* it, so the network work is not wasted
  while it plays.

The file also carries an explicit product constraint ("exact wording and blue word preserved
from product requirements; do not reorder or reword without explicit approval"), so shortening
it is the owner's call, not a cleanup's. **If you want the app to feel faster, this is the one
number to change** — `opening.tsx:57`, `4200` → something like `1500`, or gate the splash to
first-ever visit with `localStorage` instead of `sessionStorage` so it is not paid again in
every new tab.
