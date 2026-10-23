# Agent 1: LAN demo capacity and handoff

## 1. Changes (backend/config/scripts only)

- `.env.example`, `backend/src/adapt/config.py`: opt-in `ADAPT_DEMO_MODE=true` (default false).
- `backend/src/adapt/api/routes/auth.py`: `POST /api/v1/demo/session` creates a consent-only guest with the existing signed-in cookie and CSRF mechanism, marks onboarding complete, and never hashes a password. Explicit analytics consent is required; model-sharing consent defaults false. Disabled outside demo mode and refused in tunnel mode. Regular guest creation also ignores any supplied password instead of wasting time hashing it.
- `backend/src/adapt/security/demo_capacity.py`, `api/app.py`: demo-only, async single-request admission queue for HTTP `/api/` routes. It holds the slot through full ASGI completion, including dependency commits. This prevents SQLite single-writer contention and worker/connection-pool starvation. This is intentionally **one API worker**, not 50 database writers. Static/frontend traffic, health checks, and WebSockets are not serialized.
- `security/ratelimit.py`, `security/middleware.py`, `api/routes/system.py`: at multiplier 1, demo shared-IP allowance is 6,000 requests/minute (burst 6,000), guest creation 120/minute (burst 120). Other budgets are unchanged by the demo flag. Demo realtime buckets use a hash of the session cookie instead of grouping the whole room under one proxy IP. `/api/v1/quotas` includes the effective demo token budgets. No forwarding-header trust was loosened.
- `services/__init__.py`, `ai/llm.py`: demo mode does not construct the Gemini client; even an explicitly constructed Gemini client reports disabled. The deterministic local tutor and local analysis remain the execution path even if a key is configured. This avoids both quota outages and waiting behind a slow provider; **do not represent demo answers as live Gemini output**. Normal-mode Gemini behavior is unchanged.
- `baseline/flow.py`, `baseline/service.py`: existing HTTP baseline routes serve a short demo flow, skipping preference forms without fabricating answers. Default games: Quick taps (4 tasks), Vault lights (at most 4 tasks), Two questions to think about (2 tasks). No more than 10 answers. Unplayed/partially played full-baseline factors remain marked unmeasured/partial, not invented. Full baseline behavior is unchanged when the flag is false.
- `backend/tests/test_demo_capacity.py`: six regression tests.
- `scripts/load_demo.py`: synchronized 50-client real HTTP test, separate cookie jars and CSRF tokens, public task data only, no retries, asserts all three games, seals and reads back each analysis. Nonzero exit on any failed user.

## Launch for the pitch

Use a trusted LAN, not a public tunnel. Keep the normal encryption setting; do not disable encryption for performance. Use a separate demo data directory if desired; do not change an existing database's encryption policy or delete its key.

With dependencies installed, build/host through the existing frontend supervisor:

```bash
cd frontend
ADAPT_DEMO_MODE=true ADAPT_MODE=local ADAPT_VOICE_ENABLED=false npm run host
```

Share the printed **frontend LAN URL**, not port 8000. The existing frontend proxy keeps browser API requests same-origin. Open the web port through the laptop firewall and disable Wi-Fi client isolation. Stop older dev/host instances first. The existing host launcher raises its rate multiplier to at least 10; demo reliability was measured at the stricter multiplier **1**, not relying on that increase. The launcher’s rate-limit console table predates the demo overrides; `/api/v1/quotas` reports actual demo token budgets.

For a backend-only reproducible capacity measurement, use a new disposable data directory:

```bash
ADAPT_DATA_DIR="$PWD/var/demo-load-new" \
ADAPT_DB_ENCRYPTION=auto ADAPT_DEMO_MODE=true ADAPT_MODE=local \
ADAPT_RATE_LIMIT_MULTIPLIER=1 \
.venv/bin/uvicorn adapt.api.app:app --host 0.0.0.0 --port 8000 --no-access-log
# In another terminal (creates 50 learners; do not target an attendee database):
.venv/bin/python scripts/load_demo.py --base-url http://127.0.0.1:8000 --users 50
```

Do not add `--workers`, run two API instances against this database, or enable reload for the pitch. Admission control is process-local. Demo mode prioritizes the short local API flow; long-running API operations should not be offered in the demo UI because they would hold up the queue.

## Frontend contract — Agent 2

No frontend files were changed. The frontend must wire the consent screen to the new entry route; backend changes alone do not remove the current sign-up/preferences screens.

1. `POST /api/v1/demo/session` with JSON:
   ```json
   {"consent_analytics": true, "consent_share_with_llm": false, "display_name": "Guest"}
   ```
   `display_name` is optional. The response has the usual `student_id`, `csrf_token`, `expires_at`, `mode`, plus `demo: true`, `created: true`, `onboarding_complete: true`. Cookies are set as for `/students`. Never silently infer analytics consent.
2. `POST /api/v1/baseline`, echoing `X-CSRF-Token` with cookies. It immediately returns `kind: "task"` and `stage: "quick_a"`; no format/questions screens.
3. Render the returned public task. Submit `POST /api/v1/baseline/{run_id}/trial` with `task_id`, `response`, `latency_ms`, optional `wager`. Quick/trap response is `{"value": <chosen option>}`; vault response is `{"taps": [<tapped cells>]}`. Each reply contains the next task. Vault tasks can finish early depending on performance; don't hard-code a count.
4. On `kind: "snapshot"`, call `POST /api/v1/baseline/{run_id}/finish` and display the returned `factors`, `levers`, and honest completion labels. Read back via `GET /api/v1/baseline/snapshot?run_id=...` if needed. Keep normal CSRF/cookie handling.
5. Add the demo badge/disclaimer in your frontend area. Describe results as early signals, not a clinical assessment or proof of efficacy.

The three backend defaults are offered to make the flow immediately testable. If Agent 2 chooses different games, coordinate the backend selection with Agent 1; do not assume the full baseline is shortened by frontend hiding alone. Keep `ADAPT_DEMO_MODE` unchanged during a sitting; demo selection is deployment-level, not persisted per run. Do not resume a normal baseline as a demo run.

## 2. Verification actually run

Environment: sandbox Linux, Python 3.11, single Uvicorn process, 50 simultaneous real HTTP clients on loopback (same IP), no human think time and no request retries.

Initial existing suite before changes: **1,513 passed, 2 skipped**, 150.42 s.

The first load attempt (before admission control/session-key fixes) failed: **14 HTTP 500** (SQLite locked), **36 HTTP 429** (shared-IP realtime guard), zero completed users. Those failures drove the fixes; they were not hidden by load-client retries.

Final load results:

| Measurement | Field-only SQLite | Default `auto` / SQLCipher |
|---|---:|---:|
| Completed users | 50 / 50 | 50 / 50 |
| HTTP requests | 700 | 700 |
| HTTP 200 | 700 | 700 |
| Other statuses / errors | 0 | 0 |
| Wall time incl. client startup | 7.741 s | 8.808 s |
| Request median | 0.465 s | 0.549 s |
| Request p95 | 0.692 s | 0.769 s |
| Slowest request | 1.194 s | 1.155 s |
| Flow median | 6.888 s | 7.935 s |
| Flow p95 | 7.205 s | 8.223 s |
| Slowest flow | 7.256 s | 8.267 s |

The `auto` database had an opaque (non-SQLite-header) file, confirming the installed SQLCipher driver was used. Each flow created a guest, started baseline, submitted ten answers across three games, sealed an analysis, and fetched that same analysis. Test data/keys remain under ignored `var/`, not in Git.

Final full command: `.venv/bin/pytest backend/tests -q` — **1,519 passed, 2 skipped, 1 warning**, 145.80 s. This includes the existing model-failure/fallback tests, as well as six new demo regression tests. The warning is existing Starlette/httpx TestClient deprecation.

Intermediate full runs: the first exposed two new test-isolation issues (older tests clear the cached settings object); those new tests were fixed. The next had **1,518 passed, 2 skipped, 1 failed** in existing `test_two_learners_do_not_share_an_answer_key_by_position`: two independent random learner shuffles happened to have the same four-option order. No production code or other agent's test was changed for that unrelated probabilistic assertion; the final full rerun passed. Handler should be aware of this flaky test.

Also ran focused existing baseline/Gemini tests: **80 passed**; new demo tests in isolation: **6 passed**; Ruff on the three new Python files: **all checks passed**; `git diff --check`: clean. An earlier focused command named nonexistent `test_security.py` and collected no tests; it was corrected to the existing files.

## 3. Not verified

- Fifty physical phones, actual Wi-Fi/router/firewall behavior, or performance on the presentation laptop.
- Browser rendering/phone UX or the frontend's two-minute completion target; frontend integration is still Agent 2's work.
- Full production Next build/proxy load or projector/GenLink integration.
- Live Gemini with a real key (not needed in demo mode); provider failure behavior was exercised by existing mocked tests, not a live outage.
- Multiple API workers or long-running unrelated API operations under demo load; these are outside the supported pitch setup.

## 4. Merge/coordination notes

All work is on Arena's required `arena/51e7590a-adapt` branch, not `main` or the requested agent-named branch. No frontend or integration-module files were edited, and no API secrets were added. No commit/push/merge was needed to preserve the patch. Agent 4 should rerun the full suite and the load harness after integrating the frontend and integration endpoint. For the final rehearsal, verify the *phone-facing* URL and consent → games → analysis path on the actual laptop, with the final frontend build.
