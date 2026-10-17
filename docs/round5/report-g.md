# Round 5 integration report — Track G

Date: 2026-10-17 (Africa/Nairobi)

## Integrated surface

- Follow-up privacy/voice audit fixes now enforce learner sharing consent at the common external-model boundary (including the asynchronous content and lesson bridges), make server-side Voice OFF discard audio before decode/VAD/STT/recording and cancel speech, and preserve typed tutoring without TTS.
- Model responses from AI 1/AI 2/scene narration are accepted only after the new grounding, plain-text, shape, sentence-count, adaptation, and frame checks; deterministic/local fallbacks remain authoritative when a check fails.
- Added focused consent, Voice OFF, retention, content-bridge, and model-plumbing regression coverage.
- Added the learner subject spine and `P4` migration. The product contracts are:
  - `GET /api/v1/learning/overview`
  - `POST /api/v1/subjects/choose`
  - `GET /api/v1/subjects/{id}`
  - `POST /api/v1/subjects/{id}/activate`
  - `POST /api/v1/subjects/{id}/tests/complete`
- Wired the journey from consent/account through the subject-independent baseline, subject chooser,
  bounded plan preview, subject checks, first lesson, and practice/dashboard continuation.
- Added subject cards, active-subject practice gating, multiple-subject dashboard state, and
  subject-specific first-lesson routing.
- Added the code-owned scene-template library, strict template validation/dry-run helpers, SVG
  primitive `SceneStage`, timeline controls, and 22 registered templates while retaining the
  existing simulation engines.
- Added the app-wide `useVoice()` provider/context, browser speech and dictation integration,
  captions, command grammar, persisted on/off preference, top-bar control, baseline/lesson hooks,
  and voice-page synchronization. Voice off stops browser speech/recognition and disables the
  microphone control.
- Refined the fluid shell, artwork placement, dashboard cards, responsive subject grids, and
  animator controls. Existing dashboard activity/mastery cards remain available.

## Changed files

Backend:

- `backend/migrations/env.py`
- `backend/migrations/versions/P4_learner_subjects.py`
- `backend/src/adapt/api/routes/auth.py`
- `backend/src/adapt/api/routes/subjects.py`
- `backend/src/adapt/db/schema.py`
- `backend/src/adapt/journey/state.py`
- `backend/src/adapt/learning/scenespec.py`
- `backend/src/adapt/learning/scene_api.py`
- `backend/src/adapt/learning/template_library.py`
- `backend/src/adapt/subjects/catalog.py`
- `backend/src/adapt/subjects/models.py`
- `backend/src/adapt/subjects/resolve.py`
- `backend/src/adapt/subjects/service.py`
- `backend/src/adapt/subjects/topics.py`
- `backend/src/adapt/ai/ai1.py`
- `backend/src/adapt/ai/ai2.py`
- `backend/src/adapt/ai/llm.py`
- `backend/src/adapt/ai/prompts.py`
- `backend/src/adapt/ai/scene_narrator.py`
- `backend/src/adapt/api/routes/content_prepare.py`
- `backend/src/adapt/api/routes/lesson.py`
- `backend/src/adapt/api/routes/system.py`
- `backend/src/adapt/api/ws.py`
- `backend/src/adapt/services/__init__.py`
- `backend/src/adapt/voice/pipeline.py`
- `backend/tests/content_skeleton/test_cache_and_route.py`
- `backend/tests/test_api_learner_loop.py`
- `backend/tests/test_llm_consent.py`
- `backend/tests/test_master_prompts_4_5_6.py`
- `backend/tests/test_voice_pipeline.py`
- `backend/tests/test_round5_subject_spine.py`

Frontend:

- `frontend/src/app/baseline/page.tsx`
- `frontend/src/app/dev/animator/page.tsx`
- `frontend/src/app/first-lesson/page.tsx`
- `frontend/src/app/globals.css`
- `frontend/src/app/journey/page.tsx`
- `frontend/src/app/layout.tsx`
- `frontend/src/app/learn/page.tsx`
- `frontend/src/app/voice/page.tsx`
- `frontend/src/components/animator/index.tsx`
- `frontend/src/components/animator/animator.css`
- `frontend/src/components/chrome.tsx`
- `frontend/src/components/home-dashboard.tsx`
- `frontend/src/components/icons/index.tsx`
- `frontend/src/components/shell/PageShell.tsx`
- `frontend/src/components/shell/shell.css`
- `frontend/src/components/shell/dashboard.css`
- `frontend/src/components/ui.tsx`
- `frontend/src/components/voice/context.tsx`
- `frontend/src/components/voice/grammar.ts`
- `frontend/src/components/voice/voice.css`

## Verification

Passed:

- `python3 -m compileall -q backend/src/adapt` — passed.
- `cd backend && ../.venv/bin/pytest -q` — **1368 passed, 2 skipped**; the new
  consent/Voice OFF/retention/content-bridge regressions and the full legacy suite passed.
- `cd backend && ../.venv/bin/pytest -q tests/test_round5_subject_spine.py` — **4 passed**.
- `cd backend && ../.venv/bin/alembic check` — no new upgrade operations.
- Same-origin host smoke — backend/web startup, `/healthz`, and `/` through the Next proxy passed; an unauthenticated voice capability request correctly returned 401.
- `cd frontend && npm run typecheck` — passed.
- `cd frontend && npm run lint` — passed contract check and TypeScript.
- `cd frontend && npm run build` — passed production build.
- `cd frontend && npm run test:engine` — **15 passed**.
- `cd frontend && npm run test:lib` — **42 passed**.
- `cd frontend && npm run test:server` — **2 passed**.
- `cd frontend && npm run test:dev` — **5 passed**.
- Same-origin curl smoke through the Next proxy verified guest creation, CSRF, journey skips,
  subject choice, multiple-subject overview, activation, validated completion, and the first-lesson
  continuation payload. A final post-restart smoke also verified the exact public plan-test keys,
  overview top-level keys, health, and `/learn` through the proxy.
- Fresh database startup applied `P4` successfully and completed the encrypted-field startup check.
- Static learner-copy scan found no new visible source strings naming providers, model tiers, API
  keys, endpoints, or Pack terminology.

## Browser checks and screenshots

The required browser binary was not present in this sandbox: `/opt/pw-browsers/chromium` does not
exist, and the Playwright cache also has no executable. I did **not** run `playwright install`.
Consequently, the real Chromium owner-flow, mobile/desktop screenshots, keyboard traversal, dark-mode
rendering, responsive overflow, live speech APIs, and banned-phrase browser crawl are **unverified**.
The attempted checks were:

```text
node - <<'NODE' ... chromium.launch({ executablePath: '/opt/pw-browsers/chromium' }) ... NODE
ADAPT_BASE_URL=http://127.0.0.1:3000 npm run test:e2e:banned
```

The direct launch failed before navigation with `executable doesn't exist at /opt/pw-browsers/chromium`; no browser installation was attempted.

It discovered all 17 routes, then exited 2 because Playwright could not launch Chromium. The required
screenshot directory is present at `docs/screens/r5g/`; no screenshots are claimed there because
capturing fake images would make the report misleading.

## Limitations / follow-up

- No live Gemini/model-key run was available; all exercised plans used the deterministic `simple`
  path. Model-proposed plans remain schema- and taxonomy-validated, but live provider behavior is
  unverified.
- No physical microphone or secure tunnel was tested. The UI reports that browser microphone
  availability depends on a secure context; browser speech synthesis/dictation remain unverified.
- Dark mode and the 390/768/1280-pixel visual layouts need the required Chromium pass.
- The full backend suite's command must be run from `backend/` so `pytest-asyncio` discovers the
  backend `pyproject.toml`; running it from the repository root omits that configuration.
