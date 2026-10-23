# INTEGRATION — plug Adapt into any app, over one HTTP call

This is the door the pitch walks through: **GenLink (or any other app) sends Adapt
learner activity and content, and gets adapted content back.** One endpoint, one
method, plain JSON in and out — not a WebSocket, not a session, not an SDK.

```
POST /api/v1/integration/adapt
Content-Type: application/json
```

The endpoint lives in its own module (`backend/src/adapt/api/routes/integration.py`),
shares nothing with the learner-facing routes, and is covered by
`backend/tests/test_integration_endpoint.py`.

## What it guarantees

| Property | Meaning for the calling app |
| --- | --- |
| **Stateless** | No account, no cookie, no database row is read or written. `learner.id` is opaque: echoed back, never stored, never joined to anything. |
| **Deterministic** | The same request body always produces byte-identical `adapted_content`. Safe to cache, safe to retry. |
| **Local-only** | The lesson is built by the template adapter (the same engine that teaches in simple mode). It never calls Gemini or any network model, so a slow, throttled or out-of-quota model cannot fail, delay or half-answer this call. `engine.local_only` is `true` by construction. |
| **Fast** | Measured on the demo laptop: engine time 0–1 ms, full HTTP round trip ≈ 10 ms. |
| **Honest** | Every block carries a `why` (the numbers it came from), and content that does not exist is reported in `fallbacks`, never invented. |

## The request

```jsonc
{
  // Optional. Who the activity belongs to, as far as the caller knows.
  "learner": {
    "id": "genlink-42",              // opaque; echoed, never stored
    "language": "en",                // "en" | "sw"/"kiswahili" | "mixed" — scaffolding language
    "levers": { ... },               // explicit overrides; win over the activity read (below)
    "preferences": { ... },          // passed through to the adapter (e.g. tone, reading_level)
    "teaching_memory": {             // what the caller already taught, so this lesson won't repeat it
      "explained": ["fractions_add"], "examples_shown": [], "asked": []
    }
  },

  // Optional. Recent learner events from the calling app (max 500).
  // Every field of an event is optional; unknown fields are ignored.
  "activity": [
    {
      "id": "a1",                    // your event id; echoed into the lesson's evidence refs
      "kind": "attempt",             // "attempt" | "hint" | anything else (counted, not interpreted)
      "topic_id": "fractions_add",   // scopes the topic accuracy/rung read
      "correct": false,              // scored answer — the one field that makes an event an attempt
      "response_ms": 1800,           // how long the learner took
      "hint_used": false,            // or send a separate kind:"hint" event
      "confidence": "certain",       // free text; guessing/fairly sure/certain-style words are read
      "method": "worked_example",    // your teaching-method label for what preceded the attempt
      "expected_success": 0.6        // your prediction for this attempt, if you make one
    }
  ],

  // The material to adapt, in any pack envelope the adapter reads.
  // All keys optional; send what you have.
  "content": {
    "concept": "fractions_add",
    "title": "Adding fractions",
    "topic_id": "fractions_add",
    "source": "genlink",                       // echoed into provenance
    "explanation": "Prose. Split into sentences, chunked to the learner's step size, one check after each chunk.",
    "worked_example": { "steps": [ { "text": "...", "why": "..." } ] },
    "visual": { "kind": "flow", "steps": ["..."] },   // validated picture spec (see lesson/templates)
    "questions": [ { "question": "What is 1/4 + 1/8?", "hint": "...", "format": "numeric" } ],
    "hints": ["..."],
    "facts": ["the parts must be the same size before you add"],
    "answer_values": ["3/8"],
    "language": "en"                           // the language of the material itself
  },

  // Or, instead of a pack: name the concept and get an honest skeleton
  // that says no checked material exists yet (nothing is invented).
  "concept": "photosynthesis",
  "title": "Photosynthesis",
  "topic_id": null,
  "constraints": { "max_words_per_block": 40, "reading_level": "plain" }   // optional; may tighten, never loosen
}
```

## How activity becomes adaptation

The endpoint reads the events with small, stated rules — code, not a model's
opinion — and every derived lever carries the numbers it came from (returned in
`derived_profile`, and per block in `why`):

| Signal in `activity` | Rule (thresholds are named constants in the module) | Effect on the lesson |
| --- | --- | --- |
| Recent accuracy | ≥ 4 attempts and < 50 % right → `step_size=small`; ≥ 6 attempts and ≥ 85 % right → `step_size=large` | Explanation is cut into smaller/larger steps (30 / 50 / 75 words), each followed by exactly one check question |
| Fast wrong answers | ≥ 3 timed attempts and ≥ 40 % wrong within 2500 ms → `early_guessing` flag | The lesson opens by asking the learner to **predict** before it teaches |
| Slowing + slipping | ≥ 6 timed attempts, last-3 median ≥ 1.5× first-half median, ≥ 2 of last 3 wrong → `fatigued` flag | A planned break is added; the learner is not marked down for tiredness |
| Help-seeking | any hint event / `hint_used` → policy from how fast help was asked (< 30 s → `offer early`); none across ≥ 4 attempts → `offer after a first wrong attempt` | Hint policy shown on every check block, and whether hints appear early |
| Confidence vs correctness | ≥ 2 `sure`-bucket answers, ≥ 50 % wrong → `confidence_prompting=before_each_answer`; ≥ 2 `guess`-bucket, ≥ 50 % right → `evidence_of_success` | A calibration question is added, or the close shows "you were right N of M times, including when you felt unsure" |
| Topic accuracy | ≥ 5 attempts on the requested `topic_id` → coarse ladder rung 0–5 | Rung ≥ 4 adds a teach-back ("explain it in your own words"); rung 5 also predicts first |
| `method` + `expected_success` | rows pass through to the adapter's own method ranking (needs ≥ 8 observations per method) | The best-serving teaching method leads the lesson |
| `learner.language` | passed as a preference | Scaffolding (prompts, checks) in English, Kiswahili, or both |

Explicit `learner.levers` always **win over** this read: a caller that measured
its learner properly is not overruled by the endpoint's coarse one. Lever names
and accepted values are the adapter's: `step_size` (`small|medium|large`),
`lead_format_prior` (`visual|worked_example|explanation|retrieval`), `language`,
`tone`, `pace`, `timers_visible`, `confidence_prompting`, `hint_policy`,
`review_halflife_prior`. Plain values or `{"value", "why", "source"}` dicts are
both accepted.

## Example call

Exactly what was run against the demo build (a real request, a real capture):

```bash
curl -sS -X POST "http://<adapt-host>:8000/api/v1/integration/adapt" \
  -H 'Content-Type: application/json' \
  -d '{
  "learner": { "id": "genlink-42", "language": "en" },
  "activity": [
    { "id": "a1", "kind": "attempt", "topic_id": "fractions_add", "correct": false, "response_ms": 1800, "confidence": "certain" },
    { "id": "a2", "kind": "attempt", "topic_id": "fractions_add", "correct": false, "response_ms": 2100, "confidence": "certain" },
    { "id": "a3", "kind": "attempt", "topic_id": "fractions_add", "correct": true,  "response_ms": 9400, "confidence": "guessing" },
    { "id": "a4", "kind": "hint",    "topic_id": "fractions_add", "response_ms": 5000 }
  ],
  "content": {
    "concept": "fractions_add",
    "title": "Adding fractions",
    "topic_id": "fractions_add",
    "source": "genlink",
    "explanation": "To add fractions, the parts must be the same size. That shared size is the denominator. When the denominators differ, rename one fraction so both use the same denominator. Then add the numerators and keep the denominator. The answer can often be simplified.",
    "worked_example": { "steps": [
      { "text": "1/2 + 1/3: the denominators differ, so find a shared size: 6.", "why": "common denominator" },
      { "text": "Rename: 1/2 = 3/6 and 1/3 = 2/6.", "why": "equivalent fractions" },
      { "text": "Add the numerators: 3/6 + 2/6 = 5/6.", "why": "same-size parts add directly" }
    ] },
    "questions": [ { "question": "What is 1/4 + 1/8?", "hint": "Eighths are the shared size.", "format": "numeric" } ],
    "facts": [ "the parts must be the same size before you add" ]
  }
}'
```

## Example response

`200 OK` in ≈ 10 ms (engine: 0 ms). Captured verbatim from the request above;
only the repeated `evidence_ids`/`lever`/`source`/`n` fields inside some `why`
objects are elided with `…` to keep this page readable — every `why` in the live
response carries them.

```jsonc
{
  "ok": true,
  "engine": { "name": "adapt", "version": "0.1.0", "mode": "deterministic", "local_only": true },
  "learner_id": "genlink-42",
  "request_id": "5c711598c7a3",
  "elapsed_ms": 0,
  "derived_profile": {
    "events": 4, "attempts": 3, "correct": 1, "hints": 1, "accuracy": 0.333,
    "levers": {
      "hint_policy": {
        "value": "offer early",
        "why": "the app reported help asked for after 5s, so hints are offered early",
        "source": "measured"
      },
      "confidence_prompting": {
        "value": "before_each_answer",
        "why": "the app's learner felt sure and was wrong 2 of 2 times, so the lesson asks how sure they are before answering",
        "source": "measured"
      }
    },
    "flags": { "early_guessing": true },
    "subject": { "p_know": 0.333, "n": 3 },
    "notes": [
      "early_guessing: 2 of 3 timed answers were wrong within 2500 ms, so the lesson asks for a prediction first",
      "hint_policy=offer early (help was sought quickly)",
      "confidence_prompting=before_each_answer (sure but wrong 2/2)"
    ]
  },
  "adapted_content": {
    "concept": "fractions_add",
    "title": "Adding fractions",
    "topic_id": "fractions_add",
    "content": { "concept": "fractions_add", "title": "Adding fractions", "topic_id": "fractions_add", "content_id": null, "source": "genlink" },
    "lead_format": "worked_example",
    "ladder_rung": 2,
    "subject": { "p_know": 0.333, "ci": null, "n": 3, "band": null },
    "blocks": [
      {
        "block": 1,
        "chunk_id": "chk_ebce02df478a",
        "kind": "predict",
        "content": { "question": "Before you see it: What is 1/4 + 1/8?", "questions": 1 },
        "why": {
          "text": "you tend to commit to an answer early, so the lesson asks for the prediction before it teaches — the guess is what the teaching gets measured against",
          "lever": "confidence_prompting", "source": "measured", "evidence_ids": ["a1", "a2", "a3"], "n": 3
        }
      },
      {
        "block": 2,
        "chunk_id": "chk_180f1c95a22e",
        "kind": "worked_example",
        "content": {
          "title": "Adding fractions",
          "steps": [
            { "index": 1, "text": "1/2 + 1/3: the denominators differ, so find a shared size: 6.", "why": "common denominator" },
            { "index": 2, "text": "Rename: 1/2 = 3/6 and 1/3 = 2/6.", "why": "equivalent fractions" },
            { "index": 3, "text": "Add the numerators: 3/6 + 2/6 = 5/6.", "why": "same-size parts add directly" }
          ],
          "intro": "Work through this example step by step."
        },
        "why": { "text": "no stated or measured lead yet: a worked example is the default first look at a new idea", … }
      },
      {
        "block": 3,
        "chunk_id": "chk_aee43fd75780",
        "kind": "text",
        "content": {
          "text": "To add fractions, the parts must be the same size. That shared size is the denominator. When the denominators differ, rename one fraction so both use the same denominator. Then add the numerators and keep the denominator. The answer can often be simplified.",
          "part": 1, "of": 1
        },
        "why": { "text": "step 1 of 1: sized to at most 50 words before a check", … }
      },
      {
        "block": 4,
        "chunk_id": "chk_3eac4f7efd36",
        "kind": "check",
        "content": {
          "question": "Quick check: what does this step say about denominators?",
          "kind": "recall", "questions": 1, "hint_policy": "offer early"
        },
        "why": { "text": "a one-question check after every step, so a step that did not land is found here and not three steps later", … }
      },
      {
        "block": 5,
        "chunk_id": "chk_32fa2a098e22",
        "kind": "check",
        "content": {
          "question": "How sure are you of that answer — guessing, fairly sure, or certain?",
          "kind": "confidence", "questions": 1
        },
        "why": { "text": "the app's learner felt sure and was wrong 2 of 2 times, so the lesson asks how sure they are before answering", … }
      },
      {
        "block": 6,
        "chunk_id": "chk_60e07f1dd39e",
        "kind": "check",
        "content": { "question": "What is 1/4 + 1/8?", "kind": "matched_question", "questions": 1, "format": "numeric" },
        "why": { "text": "the question this teaching is measured by, in the same format whichever way the concept was taught", … }
      },
      {
        "block": 7,
        "chunk_id": "chk_9385b4dfc5c4",
        "kind": "reflect",
        "content": { "text": "Look back before you move on.", "kind": "review" },
        "why": { "text": "the close: what to look back at, and when this comes round again", … }
      }
    ],
    "limits": {
      "max_words_per_block": 50, "step_size": "medium", "language": "en", "tone": "straight",
      "reading_level": "standard", "max_blocks": 24, "timers_visible": true, "pace": "steady",
      "max_explanation_chunks": null,
      "rules": [
        "every explanation chunk is followed by exactly one check question",
        "no sentence is cut in half to fit a block; a long sentence travels whole and is flagged",
        "a picture that fails validation is shown in words, with the reason"
      ]
    },
    "why_panel": {
      "title": "Why this lesson looks like this",
      "rows": [ … 4 rows: lead choice, step size, ladder rung, confidence prompt — each with its receipts … ],
      "what_would_change_it": [ … 5 sentences … ],
      "fallbacks": [],
      "mode": "deterministic",
      "mode_note": "built by the template adapter from your measured profile (simple mode: no model was involved)"
    },
    "fallbacks": [],
    "mode": "deterministic",
    "provenance": {
      "model_version": null, "content_id": null, "content_source": "genlink",
      "evidence_ids": ["a1", "a2", "a3"], "evidence_n": 3
    }
  }
}
```

### Reading the response

* `adapted_content.blocks` is the lesson, in order. Render each `kind`
  (`predict`, `worked_example`, `text`, `visual`, `check`, `explain`,
  `reflect`) as a card; `content` carries the words, `why` carries the
  receipts you can show or hide.
* `adapted_content.limits` states the caps the lesson was built under, and
  `why_panel` is a ready-made "why does this look like this" screen.
* `derived_profile` is what Adapt read from *your* activity events — echo it
  back to your learner and the adaptation is auditable end to end.

### Same content, different learner — measured

Both calls below send the **identical content pack**; only `activity` differs.
Captured from the same server run:

| | struggling learner (2/8 right, slow) | strong learner (8/8 right, steady) |
| --- | --- | --- |
| `limits.step_size` | `small` (≤ 30 words/step) | `large` (≤ 75 words/step) |
| `ladder_rung` | 1 | 5 |
| explanation | cut into 2 checked chunks | one chunk |
| extras | — | prediction first + a teach-back block ("explain it in your own words") |

## Errors

| Status | When | Body |
| --- | --- | --- |
| `401` | `ADAPT_INTEGRATION_TOKEN` is set and the request does not carry it (or a protected tunnel's passphrase is missing) | `{"detail": {"error": "integration_unauthorized", "next": "send the value of ADAPT_INTEGRATION_TOKEN as a bearer token in the Authorization header, or in X-Adapt-Integration-Token"}}` |
| `422` | Nothing to adapt (no `content`, no `concept`, no `title`) | `{"detail": {"error": "adaptation_refused", "detail": "nothing to adapt: the request carried no content, no concept and no title", "next": "send content (a pack), or at least a concept or title, to adapt"}}` |
| `422` | A field has the wrong shape (e.g. `"correct": "perhaps"`, 501+ events) | `{"error": "validation_failed", "fields": [{"loc": […], "msg": "…", "type": "…"}]}` — submitted values are never echoed back |
| `429` | Per-IP rate limit (the app-wide bucket; 120 req/min default, ×10 under `adapt host`) | `{"detail": {"error": "rate_limited", "retry_after_s": …}, "retry_after_s": …}` plus a `Retry-After` header. The call is idempotent — waiting and retrying is always safe. |

A `200` with `adapted_content.fallbacks` non-empty is not an error: it is the
lesson saying what it did not have ("no checked content exists for this concept
yet, so the lesson is a recall-and-reflect skeleton and says so").

## Security and operations notes

* **Open on the LAN by design** — that is what "any app can call it" means for
  the demo. To require a shared secret, export `ADAPT_INTEGRATION_TOKEN=<token>`
  before starting the server; callers then send it as a bearer token in the
  `Authorization` header or in `X-Adapt-Integration-Token`. The token is read
  per request (no restart needed to rotate), compared in constant time, never
  logged, never committed. On a public tunnel with `ADAPT_DEMO_PASSPHRASE` set,
  the existing `X-ADAPT-Demo-Passphrase` header is honoured instead.
* **No learner data crosses this door.** Adapt stores nothing from these calls;
  the calling app remains the system of record for its learners.
* **Browser callers:** a server-to-server call needs nothing; a *browser* app on
  another origin needs its origin in `ADAPT_ALLOWED_ORIGINS` (the app-wide CORS
  list) — the endpoint itself adds no CORS surface.
* **Body cap:** the app-wide 8 MB request cap applies; 500 activity events is
  the endpoint's own bound.
* **Host allowlist:** the app-wide trusted-host check applies like everywhere
  else; `adapt host` already adds the LAN address it prints.

## Tests

`backend/tests/test_integration_endpoint.py` (18 tests) covers: the docs example
call returning adapted content; no-session/no-cookie/no-database; the profile
read (early guessing → prediction first, sure-but-wrong → confidence prompt,
quick hint → offer early); struggling vs strong learners getting visibly
different lessons; fatigue → planned break; explicit levers winning; Kiswahili
scaffolding; teaching memory suppressing a repeated explanation; determinism;
clean 422s; tolerance of unknown event fields/kinds; the token gate on and off;
and discoverability in the `/api` index. The whole file runs with
`ADAPT_GEMINI_API_KEY` removed, so every pass is also proof that no model is
involved.
