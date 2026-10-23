# Pitch Demo & GenLink Engine — Frozen Cross-Agent Interfaces

Canonical schemas and deterministic helpers live in
[`backend/src/adapt/pitch_contracts.py`](../backend/src/adapt/pitch_contracts.py) and are
verified by [`backend/tests/test_pitch_contracts.py`](../backend/tests/test_pitch_contracts.py).

Merge order landed on `main` (`d2bbf49`):
1. **PR #41 (`agent-1-capacity`)** — `ADAPT_DEMO_MODE=true`, `DemoCapacityMiddleware`, `POST /api/v1/demo/session`, 50-user LAN rate budgets, local-only fallback, `scripts/load_demo.py`.
2. **PR #42 (`agent-2-demo-ui`)** — `/demo` phone-first 3-game experience (`POST /api/v1/students` → `POST /api/v1/tests/plan` → `3 × POST /api/v1/tests/grade` → `POST /api/v1/tests/evidence`, with built-in 8s fallback and persistent `DEMO` badge).
3. **PR #43 (`agent-3-integration`)** — Stateless HTTP engine endpoint `POST /api/v1/integration/adapt` (`docs/INTEGRATION.md`).

### Pitch Day Launch Summary
- **Start LAN stack**: `cd frontend && ADAPT_DEMO_MODE=true ADAPT_MODE=local ADAPT_VOICE_ENABLED=false npm run host`
- **Attendee phone URL / QR**: **`http://<lan-ip>:3000/demo`**
- **Projector (GenLink) URL**: **`POST http://<lan-ip>:3000/api/v1/integration/adapt`** (or `http://127.0.0.1:8000/api/v1/integration/adapt` on the same laptop)

---

## 1. File-Ownership Boundaries (Conflict Prevention)

| Branch | Owns | Must NOT Edit |
|---|---|---|
| `agent-1-capacity` | `backend/src/adapt/{config,hosting}.py`, `db/engine.py`, `api/deps.py`, `api/routes/{auth,system}.py`, `security/{ratelimit,middleware}.py`, `ai/llm.py`, `frontend/{dev,host,server}.mjs`, load tests | `frontend/src/**`, `README.md`, Agent 3's new route module |
| `agent-2-demo-ui` | `frontend/src/**` (and `frontend/scripts-ui-probe.mjs` if updating UI probes) | `backend/**`, `README.md` |
| `agent-3-integration` | One new self-contained route file (e.g. `backend/src/adapt/api/routes/integrate.py`), one `include_router(...)` line in `backend/src/adapt/api/app.py`, its own test file, and a short section at the end of `README.md` | Existing route files, `frontend/**`, `config.py`, `hosting.py` |
| `agent-4-handler` | `backend/src/adapt/pitch_contracts.py`, `backend/tests/test_pitch_contracts.py`, `docs/PITCH_INTERFACES.md`, merge conflict resolution | All files owned by Agents 1–3 |

**`app.py` single-touchpoint rule**: Agent 1 should avoid editing `backend/src/adapt/api/app.py`
unless strictly required; Agent 3 adds a single `app.include_router(integrate.router, prefix=prefix)`
at the bottom of the router list in `create_app()` (`backend/src/adapt/api/app.py` around line 204).

---

## 2. Seam 1 — Demo Mode Flag (`Agent 1` ↔ `Agent 2`)

- **Important bug on `main`**: `backend/src/adapt/api/routes/system.py:104` checks
  `svcs.settings.mode == "demo"`, while `backend/src/adapt/config.py:26` defines
  `Mode = Literal["local", "tunnel", "test"]` (setting `ADAPT_MODE=demo` crashes startup, and
  changing `mode` away from `"local"` disables local error details and cookie rules).
- **Contract**:
  1. Add boolean setting `demo_mode: bool = False` (`ADAPT_DEMO_MODE=1`, enabled automatically by
     `adapt host --demo` or `ADAPT_DEMO_MODE=1`) while keeping `ADAPT_MODE=local`.
  2. Return `"demo_mode": bool` on **both**:
     - `GET /api/v1/health` (public/signed-out — phones read this **before** guest creation)
     - `GET /api/v1/status` (signed-in — `Status.demo_mode` already exists in `frontend/src/lib/types.ts`)
  3. Frontend (`Agent 2`) treats Demo Mode as active when **any** of the following holds:
     `NEXT_PUBLIC_ADAPT_DEMO === "1"`, `?demo=1` in URL, `health.demo_mode === true`, or
     `status.demo_mode === true`.

```json
// GET /api/v1/health (200 OK, signed-out safe)
{
  "ok": true,
  "version": "0.2.0",
  "demo_mode": true,
  "database_reachable": true,
  "lessons": {
    "state": "simple mode",
    "says": "Lessons are running in simple mode using built-in templates."
  }
}
```

---

## 3. Seam 2 — Consent-Only Guest Session (`Agent 1` ↔ `Agent 2`)

Use the **existing** `POST /api/v1/students` endpoint so `scripts/check-contract.mjs` and
`frontend/src/lib/mock.ts` stay 100% in sync without adding a new auth URL.

### Request (`DemoGuestRequest`)

```http
POST /api/v1/students HTTP/1.1
Content-Type: application/json

{
  "as_guest": true,
  "consent_analytics": true,
  "consent_share_with_llm": false
}
```

### Response (`DemoGuestResponse` + `Set-Cookie: adapt_session=...; HttpOnly`, `adapt_csrf=...`)

```json
{
  "student_id": "stu_01ab23cd45ef",
  "display_name": "Guest learner",
  "role": "student",
  "onboarding_complete": false,
  "diagnostic_complete": false,
  "baseline_started": false,
  "consent_analytics": true,
  "consent_share_with_ai": false,
  "csrf_token": "urlsafe-random-token",
  "mode": "local",
  "demo_mode": true,
  "created": true
}
```

- **Agent 1 requirements**:
  - When `as_guest: true` (or `demo_mode: true` with no password), **never** invoke Argon2id
    (`auth.hash_password`).
  - Raise or bypass the per-IP `guest_create` bucket (`6/min, burst 6` on `main`) and `InFlight`
    cap (`max_inflight=4` on `main`) so **50 phones behind one LAN/proxy IP** (`frontend/server.mjs`
    forwards all requests from `127.0.0.1`) never receive `429`.

---

## 4. Seam 3 — Three-Game Phone Flow → Immediate Analysis (`Agent 1` ↔ `Agent 2`)

`adapt.baseline` enforces sequential task submission (`Baseline.submit` rejects out-of-order
`task_id`s with `422`), **and** already supports partial sealing via
`POST /api/v1/baseline/{run_id}/finish` at any point. Playing the **first 3 games in server order**
requires **zero backend flow changes** and finishes in ~60–90 seconds on mobile:

1. `POST /api/v1/baseline` `{}` → returns `run_id` (`stage: "format"`).
2. `POST /api/v1/baseline/{run_id}/format` `{"value": "diagram"}` (or learner's 1-tap pick; default `"diagram"`).
3. `POST /api/v1/baseline/{run_id}/questions` `{"answers": {}}` (skips the 13 self-report sliders cleanly).
4. **Game 1 (`codewords_study` → `codewords_cued`)**: Spy Codewords (`8` study + `8` cued trials) →
   measures **`long_term_memory`** (`n = 8 >= 5`).
5. **Game 2 (`quick_a`)**: Quick Taps (`4` trials, all scored) →
   measures **`attention_self_control`** (`n = 4 >= 4`).
   *(Confidence wagers across Games 1–2 also reach `n >= 8` and measure **`metacognition`**.)*
6. **Game 3 (`vault`)**: Vault Lights (`>= 3` span trials) →
   measures **`working_memory`** (`n >= 3 >= 3`).
7. **Stop & Seal**: As soon as `POST /api/v1/baseline/{run_id}/trial` returns
   `stage === "shopkeeper"` (`DEMO_STOP_STAGE`), call:
   `POST /api/v1/baseline/{run_id}/finish` `{}` → returns `200 OK` with `BaselineSnapshot`:
   - `state`: `"sealed"`
   - `factors`: 8 factor cards (`state: "measured"` with `range: [low, high]` and `summary` on the
     played games; `"not_enough_data"` — never zero — on skipped games)
   - `levers`: `step_size`, `lead_format_prior`, `review_halflife_prior`, `hint_policy`,
     `confidence_prompting`, `pace` (each `{value, why, source}`)
   - `completion`: `{stages, complete, total, played, not_measured}`

All 7 `/api/v1/baseline/*` routes already exist in `backend/src/adapt/api/routes/baseline.py` and
`frontend/src/lib/mock.ts`.

---

## 5. Seam 4 — External HTTP Adaptation Endpoint for GenLink (`Agent 3`)

- **Transport**: Plain HTTP `POST` JSON (not WebSocket). Recommended path:
  `POST /api/v1/integrate/adapt` (mounted via `APIRouter(prefix="/integrate", tags=["integration"])`
  under `prefix="/api/v1"`).
- **Auth / CSRF**: Must **not** require `adapt_session` cookies or `x-csrf-token` so external apps
  (GenLink) can call it statelessly with `Content-Type: application/json`.
- **No Gemini dependency on critical path**: Import `EngineAdaptRequest`, `EngineAdaptResponse`, and
  `adapt_external_content` from `adapt.pitch_contracts` (or call `adapt.ai.adapter.adapter.plan(...)`
  directly) so every call completes deterministically in `<50 ms` even with no Gemini key or
  exhausted quota.

### Example Request (`EngineAdaptRequest`)

```http
POST /api/v1/integrate/adapt HTTP/1.1
Content-Type: application/json

{
  "learner_id": "genlink-demo-user-01",
  "activity": [
    {"item_id": "q1", "topic_id": "fractions_add", "correct": false, "latency_ms": 1800, "confidence": 0.9, "hint_used": true, "method": "explanation"},
    {"item_id": "q2", "topic_id": "fractions_add", "correct": false, "latency_ms": 2100, "confidence": 0.85, "hint_used": true, "method": "explanation"},
    {"item_id": "q3", "topic_id": "fractions_add", "correct": true, "latency_ms": 9400, "confidence": 0.6, "hint_used": false, "method": "worked_example"}
  ],
  "learner": {
    "language": "en",
    "tone": "gentle",
    "reading_level": "plain"
  },
  "content": {
    "concept": "Adding fractions with unlike denominators",
    "title": "Adding Unlike Fractions",
    "topic_id": "fractions_add",
    "explanation": [
      "Fractions can only be added directly when their denominators match.",
      "Rewrite each fraction as an equivalent fraction over a common denominator.",
      "Add the numerators and keep the common denominator unchanged."
    ],
    "worked_steps": [
      "Start with 1/2 + 1/3 and find the common denominator 6.",
      "Rewrite 1/2 as 3/6 and 1/3 as 2/6.",
      "Add the numerators: 3/6 + 2/6 = 5/6."
    ],
    "questions": [
      {"question": "What is 1/4 + 1/2 written as a single fraction?", "answer": "3/4"}
    ],
    "hints": [
      "Rewrite 1/2 with denominator 4 before adding."
    ]
  }
}
```

### Example Response (`EngineAdaptResponse`)

```json
{
  "ok": true,
  "learner_id": "genlink-demo-user-01",
  "concept": "Adding fractions with unlike denominators",
  "title": "Adding Unlike Fractions",
  "topic_id": "fractions_add",
  "mode": "deterministic",
  "lead_format": "worked_example",
  "ladder_rung": 2,
  "limits": {
    "max_words_per_block": 30,
    "step_size": "small",
    "language": "en",
    "tone": "gentle",
    "reading_level": "plain",
    "timers_visible": true,
    "pace": "steady",
    "max_explanation_chunks": null
  },
  "learner_model": {
    "observations": 3,
    "accuracy": 0.333,
    "mean_latency_ms": 4433,
    "estimated_mastery": 0.4,
    "ladder_rung": 2,
    "lead_format": "worked_example",
    "step_size": "small",
    "max_words_per_block": 30,
    "signals": ["early_guessing", "needs_smaller_steps", "overconfident_errors"]
  },
  "blocks": [
    {"block": 1, "chunk_id": "...", "kind": "predict", "content": {"question": "..."}, "why": {"lever": "confidence_prompting", "source": "measured", "text": "..."}},
    {"block": 2, "chunk_id": "...", "kind": "worked_example", "content": {"steps": ["..."]}, "why": {"lever": "lead_format_prior", "source": "default", "text": "..."}},
    {"block": 3, "chunk_id": "...", "kind": "text", "content": {"text": "...", "part": 1, "of": 3}, "why": {"lever": "step_size", "source": "measured", "text": "..."}},
    {"block": 4, "chunk_id": "...", "kind": "check", "content": {"question": "...", "hint_shown": true}, "why": {"lever": "step_size", "source": "measured", "text": "..."}}
  ],
  "why_panel": {
    "title": "Why this lesson looks like this",
    "rows": ["..."],
    "what_would_change_it": ["..."],
    "fallbacks": [],
    "mode": "deterministic",
    "mode_note": "built by the template adapter from your measured profile (simple mode: no model was involved)"
  },
  "fallbacks": [],
  "provenance": {
    "model_version": null,
    "content_id": "...",
    "content_source": "external_caller",
    "evidence_ids": ["q1", "q2", "q3"],
    "evidence_n": 3
  }
}
```

---

## 6. Repo Guardrails Every Branch Must Satisfy Before Merge

1. **`ConfigurationSecrecyMiddleware` (`backend/src/adapt/security/secrecy.py`)**:
   Automatically deletes **any** response JSON key whose normalized name contains:
   - `provider`, `llm`, `quota`, `budget`, `headroom`, `secret`, `apikey`, `authorization`,
     `buckets`, `limiter`, `ratelimit`
   - `token` unless paired with `csrf` (`csrf_token` is kept; `session_token` or `tokens` is deleted)
   - `model` unless `model_version`, `sent_to_model`, `model_calls_saved`, or paired with
     `learner` / `learning` / `mathematical` / `conceptual` (`learner_model` is kept; bare `model` is deleted)
   And replaces any string value mentioning `"Gemini"` or `"Google Generative Language"` with `"[withheld]"`.
2. **`scripts/check-contract.mjs` (`npm run lint`)**:
   - Every frontend `api()` / `post()` / `patch()` / `del()` path must exist in both
     `backend/src/adapt/api/**/*.py` **and** `frontend/src/lib/mock.ts`.
   - Backend-only routes (such as Agent 3's `POST /api/v1/integrate/adapt`) do **not** require a
     `mock.ts` entry as long as `frontend/src` does not call them.
   - No `localhost` / `127.0.0.1` URLs, no bare `fetch("/api/...")`, and no non-`NEXT_PUBLIC_`
     `process.env.*` reads inside `frontend/src`.
3. **`scripts/check-frontend-style.mjs` & `static-banned-phrases.mjs`**:
   - Never use `#000`, `#000000`, `bg-black`, `text-black`, `border-black`, or `font-bold` in
     `frontend/src` (use design tokens and `font-semibold` / `font-medium`).
   - Never use banned internal words in learner-visible UI strings (including the DEMO sticker and
     bottom note): `model`, `token`, `API key`, `Gemini`, `provider`, `endpoint`, `AI 1`, `AI 2`, `AI 3`.
4. **`frontend/src/lib/learnerCopy.test.mjs`**:
   - Asserts `LEARNER_LANDING === "/journey"` in `frontend/src/lib/landing.ts` (keep constant value;
     route Demo Mode users dynamically to `/baseline` when `demo_mode` is active).
   - Asserts `<AccountGate />` in `chrome.tsx`, no literal `as_guest: true` inside `chrome.tsx`
     (keep the guest `POST /api/v1/students` call inside `account-gate.tsx`), and keeps the
     `Save choice and continue` / `Optional` strings tested in `account-gate.tsx` unless
     `learnerCopy.test.mjs` is updated in the same commit on `agent-2-demo-ui`.
5. **Pytest fixture isolation (`backend/tests/conftest.py`)**:
   - `conftest.py` initialises a session-scoped `client` (`adapt.api.app:app`), a process-wide
     `AppServices` singleton (`adapt.services.current()`), and a process-wide field cipher
     (`adapt.security.fields.configure_cipher`).
   - New backend tests must **reuse the `client` / `services` fixtures** from `conftest.py` (use
     `TestClient(client.app)` if a clean cookie jar is needed) rather than calling `create_app()`
     with a temporary `ADAPT_DATA_DIR`, which overwrites the global field cipher and breaks later
     integration tests (`test_round2_g_integration.py`).
