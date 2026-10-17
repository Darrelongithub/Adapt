# Cost and latency: what this app spends per learner request, and why

Written for the four things the brief asked for: **fewer model calls**, **cached static
context**, **the right model per task**, and **no request floods**. Every number below is
produced by the code rather than copied into it — the tables are what `adapt doctor` prints,
`/api/v1/quotas` summarises, and `tests/test_ai_efficiency.py` checks against the modules they
describe. If a number here goes stale, a test fails before a reader is misled.

Two ground rules shape everything:

* the free tier is the constraint, so the interesting question is never "is this fast" but
  "what does one learner request cost, and what happens when the answer is nothing";
* **a limit must never kill a lesson.** Every mechanism on this page degrades to a labelled
  local answer, and every refusal is a *reason* in someone's log, not a traceback in a
  learner's face.

---

## 1. Fewer calls: one prompt answers the questions that need the same text

`POST /subjects/plan`, `POST /subjects/round` and `POST /topics/resolve` used to ask a model
two questions in sequence:

1. `topic_resolve` — put the learner's free text into one of five subject families;
2. `subject_leanings` — rate that family's skills by how much the topic leans on them.

The second prompt contained the first prompt's answer. That is one question split across two
round trips: two latencies, two slots against the free-tier rate limit, and two chances to fall
back where one would do.

`adapt/subjects/placement.py` merges them into **one** call with one response schema —
`{family, topic_title, level_hint, confidence, ratings[], reason}`: the metadata, the scores and
the rationale in a single JSON object, which is the shape the brief asked for. It is a chain,
not a bigger prompt for its own sake, and the rules are worth stating because each one is a
test:

* **the validators are shared.** `resolve.accept_placement` and
  `leanings._validate_model_ratings` are called on the merged reply, not reimplemented for it,
  so merging cannot accept an answer the separate calls refused (an invented family, a skill
  from another family, a flat rating, a confidence below 0.5);
* **each step falls back on its own.** A usable family with unusable ratings gives the model's
  placement *and* the labelled generic skill profile — the same outcome the second call would
  have produced if it had failed;
* **a merge never adds a call.**
  * a topic the authored tables cover → **zero** calls (the written-down rating is better than
    a sample, and it is free);
  * one open question → exactly one call, for that question. That includes the caller who only
    wants the topic (`need_ratings=False`, which is what the counterbalanced-lesson path passes):
    the ratings are answered locally rather than bought and discarded;
  * two open questions → one merged call;
* **the merge is measurable.** `ADAPT_AI_SINGLE_CALL=0` restores the two-call path, and the
  response's `placement` block reports `chain`, `calls`, `replaces`, `sources`, `hops_saved`
  and the validation notes, so "we asked once" and "we asked twice" are different facts on the
  screen rather than the same one.

The generic machinery is `adapt/ai/chain.py` (~200 lines): a `Chain` is a name, a purpose, a
user-prompt builder and a tuple of `Step`s; `Step.spec` contributes to one merged response
schema, `Step.parse` validates that step's share, and `Step.fallback` answers when the model's
share is missing or rejected. A step's validator also sees the values already accepted for the
steps before it — which is what lets the ratings be checked against *the family the same reply
chose*.

## 2. Cheaper calls: the static head is registered once, not resent every time

Every teaching prompt in this app has the same shape:

```
[ instructions + rubric + subject catalogue ]   <- identical for every learner
[ this learner's material, this item, their answer ]   <- different every time
```

The first part is the expensive one. Ten static heads are registered in
`adapt/ai/static_context.py`; each is built from the same constants the call sites used to
inline, so there is one copy of each rubric in the repository:

| block | chars | est. tokens | explicit provider cache? |
| --- | ---: | ---: | --- |
| `placement` | 5042 | 1400 | yes |
| `scene_author` | 4339 | 1205 | yes |
| `practice_author` | 3299 | 916 | no — below the floor, implicit prefix caching only |
| `tutor` | 2864 | 795 | no — below the floor, implicit prefix caching only |
| `feedback` | 2718 | 755 | no — below the floor, implicit prefix caching only |
| `narrator` | 2688 | 746 | no — below the floor, implicit prefix caching only |
| `proposer` | 2480 | 688 | no — below the floor, implicit prefix caching only |
| `grader` | 1233 | 342 | no — below the floor, implicit prefix caching only |
| `curator` | 709 | 196 | no — below the floor, implicit prefix caching only |
| `retention` | 337 | 93 | no — below the floor, implicit prefix caching only |

**10 blocks, 7136 estimated tokens of instructions — a page and a half of prose that no learner
has to pay for twice.** Token counts are `estimate_tokens` (≈3.6 characters per token), the same
number the daily budget is spent against, so the table and the ledger agree.

Three things about that table are deliberate and worth saying out loud:

* **only two heads clear a 1024-token explicit-cache floor.** On a free tier that is the
  honest outcome: `placement` (the whole skill taxonomy) and `scene_author` (the simulation
  catalogue) are cached server-side and referenced instead of resent; the rest are ordered
  static-first so the provider's own **implicit prefix caching** applies. Lowering
  `ADAPT_GEMINI_CONTEXT_CACHE_MIN_TOKENS` is an operator's call — the code will try, report a
  refusal, and remember not to ask again, which is the behaviour that matters:
* **a cache that cannot be created is one full prompt, never a failed answer.**
  `ContextCache.eligible()` returns `(False, reason)` for "too small", "too big", "switched
  off" and "carries learner text"; `llm.register_static` turns a provider refusal into a
  remembered refusal, so a deployment where caching is unavailable does not pay a failing round
  trip on every call — and a `400 with cached context` is handled by invalidating, refusing and
  resending the full prompt once;
* **no learner text can enter a cache prefix.** The cache is shared by every learner on the
  instance, so `assert_static` refuses any prefix carrying a *populated* learner wrapper
  (`<student_text>…</student_text>`, `<learner_answer>…</learner_answer>`), and
  `tests/test_ai_efficiency.py` applies it to every registered block. An instruction that
  merely *mentions* the tags is fine — the check is about content, not vocabulary.

The saving is measured, not assumed: `cachedContentTokenCount` from the provider is written to
`llm_calls.cached_tokens`, and `/api/v1/quotas` reports it as
`context_reuse.reused_input_tokens` alongside `by_class[].served_from_static_context`.

## 3. The right model for the job

`adapt/ai/routing.py` holds the whole decision in one table: every `purpose=` a call site
passes maps to a **class of work**, and each class is a model name from configuration.
Twenty-one tasks, three classes:

| purpose | class | temperature | max output | static head | model calls | replaces |
| --- | --- | ---: | ---: | --- | ---: | --- |
| `ai1_propose` | deep | 0.25 | 700 | yes | 1 | — |
| `ai1_retention_check` | light | 0.3 | 300 | yes | 1 | — |
| `ai2_curate` | light | 0.2 | 520 | yes | 1 | — |
| `ai2_explain` | standard | 0.45 | 900 | yes | 1 | — |
| `ai2_feedback` | light | 0.3 | 320 | yes | 1 | — |
| `ai2_generate_practice` | deep | 0.7 | 1200 | yes | 1 | — |
| `ai2_scene_fix` | standard | 0.0 | 700 | yes | 1 | — |
| `ai2_scene_spec` | deep | 0.2 | 700 | yes | 1 | — |
| `ai2_teachback` | standard | 0.0 | 420 | yes | 1 | — |
| `ai3_explain_wrong` | standard | 0.35 | 700 | yes | 1 | — |
| `ai3_lesson_plan` | standard | 0.4 | 1400 | yes | 1 | — |
| `ai3_narrate` | light | 0.2 | 320 | yes | 1 | — |
| `ai3_reteach` | standard | 0.4 | 900 | yes | 1 | — |
| `console_connection_test` | light | 0.0 | 8 | no | 1 | — |
| `content_item_verifier` | light | 0.0 | 450 | no | 1 | — |
| `content_items` | deep | 0.2 | 1600 | no | 1 | — |
| `content_skeleton` | deep | 0.2 | 3000 | no | 1 | — |
| `subject_leanings` | light | 0.1 | 520 | yes | 1 | — |
| `subject_placement` | light | 0.0 | 700 | yes | 1 | `topic_resolve`, `subject_leanings` |
| `test_plan` | deep | 0.5 | 1200 | yes | 1 | — |
| `topic_resolve` | light | 0.0 | 160 | yes | 1 | — |

* **light** (8 tasks) — answers checked against a fixed list or a rule, where being wrong is
  cheap because code rejects it: classification, ratings, two sentences over a grade that was
  already computed, one retention question. These are the high-frequency calls, and the class
  whose failure a learner does not see.
* **standard** (3) — the teaching reply, the teach-back judgement, the repair pass. The wording
  *is* the product, but the answer is still checked.
* **deep** (4) — open-ended authoring where a weak answer costs a rewrite: new practice items,
  a starting-check plan of interactive items, a scene specification, a proposal about a
  learner's own learning.

Which model each class is comes from configuration, never from the table:
`ADAPT_GEMINI_MODEL_LIGHT` / `_STANDARD` / `_DEEP`. Two overrides exist for a deployment that
has fewer models than classes: `ADAPT_GEMINI_MODEL_AI1` / `_AI2` pin every task a role
performs, and `ADAPT_GEMINI_TASK_MODELS='{"ai2_explain": "<model>"}'` pins one task.
Precedence is **explicit argument → task pin → role pin → class**, and a pin never changes the
reported *class* — pinning a model does not change what the work is.

### When a configured ID is retired

It happens: the provider retires and renames versioned IDs on its own schedule. Measured on a
real key in October 2026, `gemini-2.5-flash` answered `404 ... no longer available to new
users` **while still being listed by that same key's `/models`**. So the first thing to know
is that **the model list is not a statement of what a key can call** — it is what the key can
*see*, and a retired ID stays on it. Two consequences:

* the class defaults are the provider's rolling aliases (`gemini-flash-lite-latest`,
  `gemini-flash-latest`, `gemini-pro-latest`), which track whatever is served now — the
  trade-off being that an alias moves, so a reproducible evaluation pins a version;
* a `404` is retried on up to **two** other models, in this order:
  1. `ADAPT_GEMINI_MODEL_FALLBACK` when it is set, then a model this process was already
     answered by, then another configured class (standard before light before deep);
  2. otherwise an ID **discovered from the key's own `/models`**, fetched once per process
     behind a lock through the same `httpx` client (no SDK), filtered to
     `generateContent` text models — never TTS, image/nano-banana, lyria, embedding,
     `deep-research` or `antigravity` — and ranked for the tier that failed (light →
     flash-lite/flash, standard → flash, deep → pro; a rolling alias before a versioned ID,
     a stable release before a `-preview`).

Bounded: one discovery request and at most two replacement attempts, never a loop. Both the
replacement and every dead ID are remembered for the process, so the next call goes straight
to what works and pays nothing. A discovery that fails is remembered too and is not an error:
the call takes the local path, and the `404` (not the discovery failure) is the reported
cause. A key that can call nothing fails closed to the local path like any other provider
failure, with the provider's own sentence on the console.

`adapt config models` lists what the key can *see*, grouped so the aliases and current text
models come first, and says that a listed ID is not a promise. `adapt config test` is the one
that proves which ID answers: it prints the model that replied
(`Connection test passed (gemini-flash-latest).`) and, after a `404`, the whole provider
recommendation plus the IDs this key can actually call.

Two failure semantics matter for the same reason. A `200` is not an answer: an empty candidate
list, a safety `blockReason`/`finishReason`, or a reply that is not the requested JSON returns
`ok=False` with `status="empty"`, `"blocked"` or `"parse_error"`, and the call site takes its
local fallback. The safety threshold is sent explicitly as `safetySettings`
(`ADAPT_GEMINI_SAFETY_THRESHOLD`) for all four harm categories, so a filter is a configured
outcome rather than an invisible default.

The table is also a refusal: a `purpose=` literal that is not in it still works (the middle
class, so an unknown call site is not silently cheap) but is *recorded* and reported, and
`tests/test_ai_efficiency.py` walks the source with `ast` and fails on any purpose literal the
table has never heard of. Model names never reach a public response: `routing.public_table()`
carries classes, call counts and the reason per task, and `adapt/security/secrecy` strips model
names from bodies even if a route forgets.

## 4. No request floods

Two token-bucket tables and two in-flight guards, all in `adapt/security/ratelimit.py`, plus a
debounce on the browser side. The distinction that matters: a bucket bounds **how often** work
may start, a guard bounds **how much** is running at once — twenty parallel requests at
3/minute each pass every bucket and still put twenty model calls on the free tier.

| where | guard | default | what it refuses |
| --- | --- | --- | --- |
| any HTTP route | `ip` bucket | table in `ratelimit.py` | one connection asking too often |
| real-time routes (`/tutor`, `/subjects`, `/lessons`, `/voice`, `/attempts`, `/baseline`, `/adaptations`) | `InFlight` per IP | 4 in flight, 20 starts/second | a page firing more work than one person has intentions |
| every websocket control frame | `ws_control` bucket | 60/min, burst 12 | a frame per keystroke |
| a websocket turn (typed text) | `ws_turn` bucket + `conv.busy` | 20/min, burst 6, one turn at a time | a second turn while one is running |
| a websocket connection | `InFlight` per learner | 4 sockets | a reconnect loop |
| identical frames < 900 ms apart | `DuplicateWindow` | 900 ms | a double-click, a held key, a replayed frame |
| identical submissions < 1200 ms apart, or a second in-flight submit | `frontend/src/lib/debounce.ts` | — | two attempts recorded for one answer |

The client half is not decoration: the server's refusal costs a round trip that never needed to
happen, and a second *attempt* in the practice screen is not a wasted request but two rows in
the learner's evidence. `frontend/src/lib/debounce.test.mjs` tests the helpers, and asserts the
voice screen and the shared submit path actually use them.

Everything a client is refused comes back as a **frame or a response it can read**:
`{"t":"throttle"/"deduped"/"busy"}` on the socket, `429` with
`{"error":"too_many_concurrent"|"rate_limited", "retry_after_s": …}` and a `Retry-After` header
on HTTP. `ADAPT_RATE_LIMIT_MULTIPLIER` scales the whole table (including both guards) for a
shared NAT or a load test, and `/api/v1/quotas` reports the effective numbers — a documented
escape hatch rather than a silent edit.

---

## What it costs now, per request

| learner action | model calls | of which cacheable heads | what it falls back to |
| --- | ---: | ---: | --- |
| place a topic (authored) | 0 | — | the written-down table |
| place a topic (free text, both questions open) | 1 | `placement` | deterministic resolver + generic profile, labelled |
| resolve a topic only (no ratings wanted) | 1 | `placement` | deterministic resolver, labelled |
| ask the tutor a question | 1 | `tutor` | grounded local answer from the learner's material |
| submit an answer | 0 | — | rule-based grade and feedback, always |
| teach-back judgement | 1 | `grader` | the rule grader's result and its own reason |
| generate/curate resources | 1 | `curator` / `practice_author` | the shipped bank and search results, uncurated |
| a spoken turn | 1 (+1 narration) | `tutor` / `narrator` | local tutor, browser speech |
| `adapt doctor` and every status probe | 0 | — | — |

`adapt doctor` prints the table above as live checks (`model tier: …`, `static context: …`,
`merged calls: …`, `concurrency guard: …`) and `adapt doctor --json` includes the full
`routing.report()`, the per-block token sizes, the chain steps and the guard's counters.

## What is deliberately *not* optimised

* **The teach-back judgement stays a separate call.** It has to read one thing the previous call
  produced — the learner's answer — and the brief's rule that feedback rests on a grade code
  already computed is what keeps it honest. Merging it into the teaching call would let the
  tutor grade its own explanation.
* **The one retry in `ai2_scene_fix` stays a second call.** It is a repair pass with the
  validator's reasons in the prompt, which cannot be written before the validator has spoken.
  The table records it as redesigned (`calls: 1` + documented retry) rather than pretending it
  is free.
* **Heads below the cache floor are not padded to reach it.** A prompt stuffed with filler to
  clear a provider minimum would cost more than it saved, and would make the token ledger lie.
* **No semantic cache of answers.** Two learners asking similar questions get two answers: a
  shared answer cache would leak one learner's context into another's lesson, which is the one
  trade this app does not make.
