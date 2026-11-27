# Integration — the host contract

What a learning platform actually does to use Adapt, what Adapt requires of it, and how to see the loop
working before writing any code.

**Gap this closes:** until now, integration was a milestone ("M5: a real host app runs the loop"), not
a contract. That is the wrong shape — a customer's engineer cannot build from a milestone, and nobody
can watch a milestone work. This document is the contract; `AGENT_M1.md` exists so the loop is
demonstrable from milestone 1, not milestone 5.

---

## 1. Three layers, and only one of them is REST

| Layer | What it is | Who builds it | Conformance |
|---|---|---|---|
| **Contract** | the API: observe, decide, model, learners, webhooks | Adapt | OpenAPI + contract lint in CI |
| **Render spec** | the probe mechanics and accessibility requirements the host draws | the host | the engine refuses to serve an instrument the host declares it cannot render |
| **Conformance** | proof the host wired it correctly | both | `adapt-conformance` suite, badge + report |

An integration that only does layer 1 will appear to work and quietly produce a bad model. Layer 3 is
how we find that out in staging instead of in a renewal meeting.

---

## 2. What the host owns, and what Adapt owns

| Host | Adapt |
|---|---|
| content, curriculum, question banks | the learner model |
| identity, accounts, `learner_id` generation | model state per learner under that id |
| the UI the learner sees | the decision, its reason, its confidence |
| consent capture and records | consent **scopes** enforced at the API |
| rostering, classes, teachers | nothing |
| their own accessibility compliance | the accessibility **requirements** an instrument needs |

The host remains the data controller (`PRIVACY.md`). Adapt is the processor and never receives names,
emails or rosters.

---

## 3. Quickstart — fifteen minutes, one learner

### 3.0 Five ways in, and how to choose

A host should never be forced into the way *we* would build it. Five options, all hitting the same
contract, all graded by the same conformance suite:

| Path | Best for | Time to first call | Ships |
|---|---|---|---|
| **Raw REST + curl examples** | any language, agents, serverless/edge runtimes, teams with strict dependency budgets | **15 min** | M1 |
| **Thin typed SDK** (TypeScript, Python) | mainstream app teams; generated from the OpenAPI spec | **10 min** | M1 |
| **Framework adapters** — `@adapt/next` (route handler + server action), `adapt-react` (hooks + render-spec components) | Next.js/React hosts, which is most of the market | **10 min** | M2.5 |
| **Drop-in probe embed** — one `<script>`, renders probes without the host drawing render specs | hosts that will never build a probe renderer | **30 min** | M3.5 |
| **LMS plugin** (Moodle first) | institutions with no engineering team | days | post-M5 |

Rules that keep "easy" from becoming "fragile":

* **SDKs stay thin.** Generated from OpenAPI, types only, no bundled HTTP library, no transitive tree —
  the runtime's `fetch`/`httpx` does the work. Heavy vendor SDKs are being abandoned because they blow
  up serverless bundles and edge runtimes, and we are not going to ship one.
* **Two first-class languages, not nine.** TypeScript and Python at M1. PHP, Dart/Flutter and Kotlin
  only when a paying partner's stack demands it, and then generated rather than hand-written.
* **Keep one raw REST call in the repo even if you use the SDK**, so the contract stays visible when
  something behaves oddly. The SDK docs say this out loud.
* **A live key never enters a browser.** The embed path works by the host's server minting a
  short-lived, learner-scoped, origin-bound token (`POST /v1/probe_sessions/{id}/token`); the widget
  holds only that. Anything else is a key leak waiting for a screenshot.
* **The embed must pass the same accessibility gate** as a hand-built renderer — axe, keyboard-only, and
  the `cognitive_support` no-scroll rules (`UI.md` §4.1). A drop-in that fails the a11y contract is not
  a shortcut, it is a liability for the customer.
* **Time to first successful call is a release gate, measured with a developer who has never seen the
  docs**, against a 15-minute target. If it takes an hour, the docs are the product and they are broken.

```bash
npm i @adapt/sdk        # or: pip install adapt-sdk
```

```python
from adapt import Adapt

client = Adapt(api_key=os.environ["ADAPT_KEY"], environment="sandbox")

# 1. Register the learner. The id is YOURS, generated once, stable forever.
learner = client.learners.create(
    learner_id="stu_9f31c2",            # opaque; no names, no emails
    profile="adult_general",            # or "cognitive_support" (ADR-001)
    date_of_birth="2001-04-12",         # used only for the 18+ gate
    scopes=["events", "probes"],        # what consent the learner gave
)

# 2. Tell Adapt what happened. Timestamps must be ISO-8601 UTC.
client.observe(learner="stu_9f31c2", events=[{
    "skill": "linear_equations", "item_id": "itm_00417",
    "correct": True, "ts": "2026-11-16T09:14:22Z",
    "latency_ms": 8412, "session_id": "ses_77ab", "input_mode": "touch",
}])

# 3. Ask what to do next.
decision = client.decide(learner="stu_9f31c2", constructs=["skill.mastery"],
                         context={"session_minutes_left": 6})
print(decision.abstain, decision.recommendations)
```

Expected on the first call with one event: `abstain=True`, reason `insufficient_evidence`,
`needs.trials=7`. That is correct behaviour, not a setup error — see §4.

---

## 4. The loop, and the branch everyone gets wrong

```python
def next_step(learner_id, session):
    decision = client.decide(
        learner=learner_id,
        constructs=["skill.mastery", "cmp_calibration"],
        context={"session_minutes_left": session.minutes_left, "device": session.device},
    )

    if decision.abstain:
        # YOUR default teaching policy runs here. Not a spinner, not an empty screen,
        # not a silent guess. Abstention is a normal state with a documented fallback.
        render_loading_hint(decision.needs)      # optional, honest: "still learning about you"
        return host_default_next_item(learner_id)

    rec = decision.recommendations[0]
    item = map_recommendation_to_content(rec)     # host owns the mapping
    render(item, why=rec.why, confidence=rec.confidence.band)
    return item


def record_outcome(learner_id, decision_id, item, response):
    client.observe(learner=learner_id, events=[{
        "skill": item.skill, "item_id": item.id, "correct": item.grade(response),
        "ts": utcnow(), "latency_ms": response.latency_ms,
        "hint_used": response.hint_used, "assisted": response.assisted,
        "decision_id": decision_id,           # <- links outcome to decision
    }])
```

`decision_id` is the most important field in the integration. Without it the engine can never score its
own recommendations, L2 and L3 are impossible, and the abstention/coverage numbers mean nothing. The
conformance suite requires it on ≥95% of events that follow a decision.

### Five integration failures that look like engine bugs

| Failure | Symptom | Fix |
|---|---|---|
| A new `learner_id` per login | "the model never learns" | generate once, store forever; conformance check 2 |
| `correct` computed differently from the host's own definition | garbage mastery | one grading function, documented |
| Sending a week of events at once, hours late | stale decisions | batch per session, not per week |
| Re-ranking `recommendations` and blaming the engine | "its choices are bad" | serve `recommendations[0]` or report the rejection |
| Swallowing `abstain` and showing nothing | "it does nothing half the time" | implement the fallback branch above |

---

## 5. Render specs — the only part of the UI we ship

Probes are provided as **mechanic + item + accessibility requirements**; the host draws them. A render
spec looks like this:

```json
{
  "instrument": "ins_calibration_tap_v2",
  "construct": "cmp_calibration",
  "mechanic": "confidence_then_answer",
  "trials": [{"item_id": "itm_8821", "prompt": "…", "options": 3}],
  "accessibility": {
    "response_modes": ["touch", "audio", "switch", "partner_assisted"],
    "reading_level": "grade_6", "instruction_audio_required": true,
    "time_limit_adjustable": true, "memory_demand": "none",
    "max_targets_per_screen": 3, "resumable": true, "symbol_support": "host_supplied"
  },
  "licence": {"items": "adapt_owned", "symbols": "host_supplied"},
  "not_a_diagnosis": true
}
```

The host declares what it can render at connection setup
(`POST /v1/admin/connections { capabilities: { "response_modes": [...], "audio": true,
"max_targets_per_screen": 3 } }`). **If the host cannot meet an instrument's requirements, the engine
refuses to serve it** with `406 instrument_not_renderable` and names the missing capability. That is a
hard gate, not a warning: it is how we avoid claiming accessibility we have not delivered.

Symbols are the host's licence to hold — ARASAAC is non-commercial, Mulberry is share-alike, SymbolStix
and Widgit are paid (`UI.md` §6). Adapt ships no symbol assets.

---

## 6. Errors — all of them, and what to do

Codes are **lowercase snake_case and this table is authoritative** — not the HTTP status. A status
without a code here is a gap to report, not a licence to invent one; `check_openapi.py` fails the build
if the implementation emits a code that is not in this list.

The envelope is one shape everywhere, with **one optional object** and nothing else:

```json
{"error":{"code":"quota_exhausted","message":"…","request_id":"req_…","retryable":false,
          "details":{"resets_at":"2026-12-01T00:00:00Z"}}}
```

`details` is absent unless there is something structured to say, and the keys are documented here
rather than invented per endpoint — this is where `resets_at` and `tier_required` live, so that a host
can act on them without parsing English out of `message`:

| Code | `details` key | Type | Meaning |
|---|---|---|---|
| `quota_exhausted` | `resets_at` | RFC 3339 | when the allowance resets; also sent as `X-Adapt-Resets-At` |
| `not_entitled` | `tier_required` | `core` \| `plus` \| `deep` | the lowest tier that unlocks it |
| `payload_invalid` | `field` | string | the offending field, e.g. `events[1].learner_email` |
| `rate_limited` | `retry_after` | integer seconds | mirrors the header, for clients that cannot read headers |
| `not_capable` | `capability` | string | which capability is not built |

Everything else carries no `details`. A code that wants a new key is a doc change, not an endpoint
decision.

Three rules that follow from the table and are not negotiable:

* **401 is always `unauthorized`.** There is no `invalid_api_key`, no `key_expired`, no
  `key_revoked` — a host that is authenticating correctly never needs the distinction, and an attacker
  does not get to learn which part of a key was wrong. Scope failures are 403 `not_entitled`.
* **403 has two codes and the caller must choose.** A bare `403` means `not_entitled`; a paused
  connection is `connection_paused` and must be raised explicitly. Never infer one from the other.
* **404 has two codes and the caller must choose.** Framework route-miss is `route_unknown`; a
  domain lookup that found no such learner is `learner_unknown`. Answering "register the learner first"
  to a typo'd URL is a support ticket waiting to happen.

| Status | Code | Retry? | What it means for the host |
|---|---|---|---|
| 200 | — | — | serve `recommendations`, or run the fallback if `abstain` |
| 400 | `payload_invalid` | no | fix the request; the message names the field |
| 401 | `unauthorized` | no | rotate the key |
| 403 | `not_entitled` | no | `tier_required` says what to buy; **not** a quota problem |
| 403 | `connection_paused` | later | someone paused this connection deliberately |
| 404 | `learner_unknown` | no | the route exists but this learner id does not — register the learner first |
| 404 | `connection_unknown` | no | no such connection id for this tenant — check `GET /v1/admin/connections` |
| 404 | `route_unknown` | no | no such route — a typo'd URL or path version, not a learner problem |
| 404 | `decision_unknown` | no | no stored decision with that id for this tenant — a replay of something we never served, or another tenant's id |
| 406 | `instrument_not_renderable` | no | the host's declared capabilities do not cover it |
| 501 | `not_capable` | no | the capability is not built yet — our gap, not the request's |
| 409 | `conflict` | no | duplicate resource, e.g. too many open probe sessions |
| 413 | `payload_too_large` | no | batch ≤500 events / ≤1 MB |
| 422 | `payload_invalid` | no | allowlisted keys only; no PII |
| 422 | `minor_not_supported` | no | the date of birth implies under 18 — adults only, and this is not a validation typo |
| 429 | `rate_limited` | yes, honour `Retry-After` | free — no credits |
| 402 | `quota_exhausted` | no | credits/events exhausted; `resets_at` given; free |
| 410 | `gone` | no | the version was retired; read the `Link` header |
| any other | `unmapped_status` | no | an engine bug: a status with no code above. Report it with `request_id` |
| 500/502/503 | `internal` | yes, backoff | free — never billed |

One case is deliberately **not** an error: `not_validated` returns **200** with the lower `served` level
and `limited_by: "validation"` (`ADR-002`). The capability exists and the contract allows it; we have not
earned it yet. Treating that as a failure would push hosts to disable the construct instead of collecting
the data that validates it.

The distinction that matters for retry logic: **402 means stop and top up, 429 means slow down and
retry, 403 means buy or fix.** Merging them into "error" produces integrations that hammer us on an
empty balance and integrations that give up on a transient 429. The SDK raises three distinct types.

---

## 6A. Endpoint inventory — what exists, and who may call it

This is the surface as built, not as imagined. Three classes, and the class decides who is allowed to
hold the key at all:

| Route | Class | Scope | Notes |
|---|---|---|---|
| `GET /v1/health` | operational | none | 503 names the failed dependency; never carries data |
| `GET /v1/version` | operational | none | build identity; no secrets |
| `GET /v1/whoami` | host-facing | any of `observe`, `decide`, `read_model`, `admin` | identity is not a capability, so any valid scope answers it |
| `GET /v1/learners` | host-facing | `observe` | tenant-scoped list |
| `GET /v1/learners/{learner_id}` | host-facing | `observe` | unknown id → `404 learner_unknown` |
| `POST /v1/learners` | host-facing | `admin` | the only write on the learner surface; under-18 → `422 minor_not_supported` |
| `GET /v1/mastery?learner_id=` | host-facing | `read_model` | every construct with evidence for that learner |
| `GET /v1/mastery/{skill}?learner_id=` | host-facing | `read_model` | one construct; may answer `200` with `insufficient_evidence` |
| `GET /v1/credits/balance` | host-facing | `observe` | `granted = available + held + consumed + forfeited` |
| `GET /v1/admin/metrics/abstention` | **control-plane** | `admin` | the abstention/served pair, both grains, with exclusions counted (§6D) |
| `POST /v1/admin/credits/grants` | **control-plane** | `admin` | provisioning; `Idempotency-Key` required |
| `POST /v1/admin/keys` | **control-plane** | `admin` | issue; subset-of-own-scopes rule applies |
| `POST /v1/admin/keys/{id}/revoke` | **control-plane** | `admin` | immediate |
| `POST /v1/admin/keys/{id}/expiry` | **control-plane** | `admin` | audited; the only path that may shorten a key's life |
| `POST /v1/admin/connections` | **control-plane** | `admin` | creates a connection; the signing secret is shown once |
| `GET /v1/admin/connections` | **control-plane** | `admin` | list; never returns a secret |
| `PATCH /v1/admin/connections/{id}` | **control-plane** | `admin` | pause / resume; `paused` stops host-facing ingest, not the console |
| `DELETE /v1/admin/connections/{id}` | **control-plane** | `admin` | revoke, terminal; idempotent; un-revoking is `409 conflict` |
| `GET /v1/admin/connections/{id}/deliveries` | **control-plane** | `admin` | the delivery log — attempts, status, no bodies |

Two rules that follow from the table:

* **`/v1/admin/*` is never called with a host's runtime key.** It is the tenant's console surface —
  provisioned separately, held by a person or a control-plane job, and excluded from the host-facing
  conformance list. A host that finds these routes and uses them has misunderstood the contract, and
  the scope will let them, so say this out loud in onboarding.
* **The learner surface needs two scopes.** Reads are `observe`; `POST /v1/learners` is `admin`,
  because creating a learner is a provisioning act in most hosts' designs and lands in the audit
  trail. A host that registers learners at runtime therefore holds a key with both scopes — keep it
  server-side, and never in a browser.
* **Credits move by reservation, and reservations have no route.** The engine reserves internally as
  part of a `decide`; there is no endpoint to hold, settle or release credits by hand, and a request
  for one returns `404 route_unknown` — not `405`, not `501`. Money moves only through the loop.

## 6B. Mastery — what `value` means, and why it is not the model's belief

Two response shapes, and the absence of one field is the contract:

```json
{"learner_id":"…","skill":"…","value":0.62,
 "interval":{"low":0.44,"high":0.78,"mass":0.95},
 "estimator":"shrunk_rate","prior":{"source":"cohort_prior","strength":12},
 "n":34,
 "entitlement_level":"L1","capability_level":"L1","validation_level":"L1",
 "served":"L1","limited_by":null}
```

```json
{"learner_id":"…","skill":"…","abstain":true,
 "reason":"insufficient_evidence","needs":{"trials":5},
 "entitlement_level":"L1","capability_level":"L1","validation_level":"L1",
 "served":"L1","limited_by":null}
```

* **`value` is the estimated probability that this learner answers a comparable item correctly.**
  It is *not* the probability that they "know" the construct, and it is not the filter's latent belief
  — the filter is stored for item selection and saturates under repeated evidence, while the served
  number is a shrunk estimate of the observed rate. `estimator` names which one you are looking at, and
  it will change only with a documented version bump.
* **An abstention has no `value` field at all.** Not `null`, not a low number: absent. A host that
  renders "62% (low confidence)" for a learner we declined to score has invented a claim on our behalf.
* `needs.trials` is the number of additional observations that would clear the floor, so a host can
  tell the learner something true and actionable instead of nothing.
* The three axes travel on every response (§`ADR-002`): `entitlement_level`, `capability_level`,
  `validation_level`, `served = min(…)`, and `limited_by` naming the binding constraint.

  **Two kinds of gate, and only one of them is per tenant.** The **L1 gate is platform-level** — the
  estimator clears RMSE and coverage on synthetic learners and passes G2 — so when it passes, every
  tenant is served L1 and `validation_level` is `L1`. **L2 and L3 gates are per tenant**, measured on
  that tenant's own learners over time. That is why a served L1 response shows `limited_by: null`
  rather than `validation`: nothing is being withheld. An earlier draft of this section showed
  `validation_level: "L0"` alongside `served: "L1"`, which `min()` cannot produce — an example is a
  specification, and this one was wrong.

## 6C. Response headers that carry billing truth

Three headers, and they exist so a host never has to infer whether it was charged:

| Header | Value | Meaning |
|---|---|---|
| `X-Adapt-Billed` | integer credits | what this request cost. `0` for an abstention, an error, a retry, a read, a replay, or test traffic — those are the same statement, and none of them is an oversight |
| `X-Adapt-Test-Traffic` | `true` | echo of the `X-Adapt-Test` request header, so logs on both sides agree |
| `X-Adapt-Resets-At` | RFC 3339 | when the allowance resets, on `402 quota_exhausted` |

**Test traffic is a property of the request, not of the key.** Send `X-Adapt-Test: 1` and the request
is billed zero and recorded as test traffic; anything served by a deployment with `ADAPT_ENV=staging`
is treated the same way. There is no such thing as a "staging key" in production — a key is `live` or
`test`, and a test key is refused by a live deployment. An earlier draft said "staging keys set it
automatically", which described a thing that does not exist; the deployment is what is staging.

`data_quality` abstentions additionally carry `flag_evidence` — which flags fired and how many trials
each one covered — because "fix your instrumentation" is only actionable with the counts:

```json
{"abstain": true, "reason": "data_quality",
 "flag_evidence": {"rapid_guess": 7, "straight_line": 0, "tab_hidden": 0},
 "needs": {"clean_trials": 4}}
```

## 6D. The abstention report — one endpoint, two numbers, one denominator

```
GET /v1/admin/metrics/abstention?from=&to=&connection=&construct=&tier=&grain=
```

Returns `rate`, `numerator`, `denominator`, `interval`, `served`, `reasons`, `by_construct`,
`by_connection`, `exclusions`, `abstention_polling`, and `metric_version`.

Three rules, and each is a way this endpoint gets built wrong:

* **`served` is derived from the same denominator** — `denominator − numerator`, computed once, never a
  second query. Two numbers fetched separately agree on day one and disagree in a year, and the pair is
  the entire honesty claim (`METRICS.md` §1.8).
* **The denominator is a definition with counted exclusions**: valid 2xx `decide` calls, minus replays,
  minus test traffic, minus paused or revoked connections. Every exclusion returns its own count under
  `exclusions`, because "excluded" without a number is "missing".
* **An unmeasured series is `null`, never `0`.** `refusals`, `errors`, `entitlement_limits`,
  `not_validated` and `not_capable` have no store yet; they return `null` and are named in
  `unmeasured`. A console that renders a null as zero turns "we do not know" into "it never happened",
  which is the exact failure this product exists to avoid.

An empty window returns `null` for the rate with an explicit reason — **not 0%** — because a 0%
abstention rate in a window with no traffic is the most flattering lie the console could tell.

## 7. Idempotency, and the two keys

Every mutating call takes `Idempotency-Key`. **Derive it from the logical operation, never from the
attempt:**

```python
key = client.derive_key("session", session_id, event_batch_index)   # stable across retries
client.observe(learner=l, events=batch, idempotency_key=key)
```

A timeout followed by a retry must consume one credit, write one batch, return one result. The SDK
generates these by default; if you write your own HTTP client, this is the field to copy correctly.

**A replay is free and is counted.** `POST /v1/decisions/{id}/replay` returns the stored body without
recomputing it, writes no ledger row, and is excluded from the abstention denominator — but it is
recorded, because "replays are excluded" has to be a number in `exclusions`, not a promise.

Probe sessions carry a second key — `probe_session_id` — so a learner can resume across devices and
days without creating a duplicate session.

---

## 8. Webhooks

We follow the **Standard Webhooks** specification, so standard verification libraries work:

```
webhook-id: msg_2b7f…
webhook-timestamp: 1794892000
webhook-signature: v1,K5H8…
```

Signed value: `HMAC-SHA256(secret, "{webhook-id}.{webhook-timestamp}.{raw_body}")`, verified with a
constant-time comparison, `webhook-timestamp` rejected outside a **300-second** tolerance, and
`webhook-id` cached to reject replays inside the window. Sign the raw bytes — a framework that
re-serialises JSON before verification breaks the signature.

Events: `decision.created`, `consent.changed`, `quota.warning`, `learner.erased`, `instrument.not_renderable`.

**The body is a doorbell, not a payload:**

```json
{"id":"msg_2b7f…","type":"decision.created","created_at":"…","data":{"id":"…"}}
```

`data.id` is an opaque subject id. Never learner content, never an answer, never a construct name.
Fetch the detail by calling back with your key — which keeps the webhook out of the personal-data
path, and means a leaked webhook body leaks nothing worth having.

Delivery is **at-least-once**, with **five attempts over roughly 85 seconds** — at 0 s, then 1 s, 4 s,
16 s and 64 s of backoff, with jitter. A 4xx other than 429 is not retried. After the fifth attempt the
delivery is marked `failed` and stays **retrievable and replayable** at
`GET /v1/admin/connections/{id}/deliveries`, so a receiver that was down for an hour loses nothing:
reconcile from the delivery log rather than trusting the doorbell to arrive.

A longer retry schedule (to 24 h) is planned. It is a contract **upgrade** and will be announced like
one — it lands with the worker that can actually schedule it, not silently. Until then the paragraph
above is the promise, and a receiver that requires longer durability should reconcile from the log.

**Registration:** HTTPS only, resolved at registration time and rejected if it points at a private or
link-local address, no redirects followed (SSRF), 30-second timeout.

---

## 9. Versioning and deprecation

* URI major version, `/v1`. Additive changes ship inside it; anything breaking needs `/v2`.
* Deprecation is signalled in-band, per **RFC 9745** and **RFC 8594**:

```
Deprecation: @1798675200
Sunset:      Wed, 30 Jun 2027 23:59:59 GMT
Link:        <https://adapt.dev/docs/migrate-v1-v2>; rel="deprecation"
```

* **Twelve months** from deprecation notice to sunset; at most two majors live at once; after the sunset
  date the old version returns **410 Gone** with a `Link` to the migration guide — not 404, and never a
  silent redirect to a different contract.
* The SDK logs `Deprecation` and `Sunset` on every response and can be configured to fail a CI run when
  a sunset date falls inside a release horizon. That is the only mechanism that has ever made a
  deprecation notice get read.

---

## 10. The host's privacy duties

Adapt is the processor; the host is the controller. The integration must therefore:

1. Capture consent for the scopes it requests, in the host's own UI, and record it (`PRIVACY.md` §5).
2. Send `learner_id` values that are opaque and stable — no emails, no admission numbers.
3. Call `DELETE /v1/learners/{id}` when a learner is deleted on the host side, and show the returned
   deletion receipt in their own compliance record.
4. Stop sending events immediately when a consent scope is withdrawn; the engine will also refuse probes
   with `consent_withheld`, but the host should not rely on that as its only control.
5. Not attach learner data to any third-party analytics, advertising or session-replay tool.

---

## 11. Conformance — how a host proves it, and how we prove ourselves

`npx adapt-conformance --key … --env staging` runs fifteen checks and produces a pass/fail report.
Checks 1 and 9 apply to the **host-facing** rows of §6A only; `/v1/admin/*` is control-plane and is
never exercised with a host's runtime key.

| # | Check |
|---|---|
| 1 | auth works; scopes enforced per endpoint |
| 2 | `learner_id` stable across two simulated sessions |
| 3 | `observe` idempotent: replayed batch writes nothing |
| 4 | unknown payload keys rejected, not ignored |
| 5 | abstention handled: a learner with zero events gets the host's fallback, once |
| 6 | `decision_id` present on ≥95% of events following a decision |
| 7 | 402, 429, 403 handled as three different cases; `Retry-After` honoured |
| 8 | webhook signature verified; duplicate `webhook-id` ignored |
| 9 | probes refused when the scope is off, and the host does not retry it |
| 10 | deletion: after `DELETE`, export returns 404 and events are gone |
| 11 | no PII in any `learner_id` |
| 12 | staging traffic flagged `X-Adapt-Test` and billed zero |
| 13 | p95 host round-trip recorded and reported |
| 14 | declared render capabilities match the instruments served |
| 15 | deprecation headers monitored; a sunset inside the horizon warns |

Pass = integration badge on the connection in the console, and the report is exportable for the
customer's own procurement file.

**Our reference host scores 100% in CI, or the build fails.** A conformance suite we do not pass
ourselves is a marketing document.

---

## 12. Go-live checklist

- [ ] `learner_id` generated once per learner and persisted by the host
- [ ] consent scopes captured and mapped to the scopes requested at registration
- [ ] the abstention branch is implemented and tested with a zero-event learner
- [ ] `decision_id` threaded from `decide` into the following `observe`
- [ ] idempotency keys derived from logical operations in the host's retry path
- [ ] webhook signature verification with timestamp tolerance and replay cache
- [ ] 402/429/403 handled distinctly; backoff honours `Retry-After`
- [ ] grading function single-sourced and documented
- [ ] staging traffic flagged `X-Adapt-Test`; production separate key
- [ ] deletion and export wired to the host's own DSAR process
- [ ] `adapt-conformance` passing in the host's CI
- [ ] a dashboards review agreed: who reads abstention, and what they do when it moves

---

## 13. Seeing it work before writing code

Two ways, in this order.

**Sandbox demo — five minutes, no host code.**

```bash
adapt sandbox seed            # 3 synthetic learners: a guesser, a steady learner, an over-confident one
adapt sandbox demo            # opens the console on a prepared tenant
```

The walkthrough, in the order that tells the story:

1. **Overview** — credits used against plan with a projection date, connections with last event, and
   the abstention block with its rate, counts, reason mix and denominator.
2. **Learner inspector** — the steady learner at mastery 0.41 `[0.33–0.49]`, with 212 trials and the
   `L1` badge.
3. **The money shot** — open a learner with **zero** events, press *decide*: the engine abstains and
   names what it needs ("6 more trials, 1 session"). Send six events from the demo panel, press
   *decide* again: a recommendation appears, with its reason and its interval. Ninety seconds, and it
   is the whole product: it declines to guess, then earns the claim.
4. **Replay** — re-run the last decision and watch it produce an identical body. That is the audit story.
5. **Coverage** — capability vs validated vs serving, with validation debt named by failing gate. It is
   also the honest answer to "what does Deep actually get me today".
6. **The guesser** — the learner who answers correctly 20% of the time gets no mastery claim. Nobody
   else demos this, and it is the fastest way to earn a technical buyer's attention.

**Reference host — a running application.** `examples/reference-host/` is a small Next.js app that
implements the host side of this document end to end, including the fallback branch, the render spec
renderer, and the webhook receiver. It is deliberately plain: it exists to be read and copied, then
replaced.

---

## 14. What integration is not

* Not roster sync. The host owns classes, teachers and enrolment; Adapt never sees them.
* Not content. We return a decision about difficulty, spacing and method — never a question.
* Not a UI kit. The console is ours; the learner UI is theirs.
* Not a data export pipeline. If a host needs their events elsewhere, that is
  `GET /v1/learners/{id}/export`, on demand, not a nightly sync of learner records out of the engine.
