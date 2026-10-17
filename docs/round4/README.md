# ADAPT Round 4 — integration and verification

Verified 2026-10-17 against the real production-built frontend and local API (Chromium 153.0.8010.0). Work is on `arena/ebdaedb8-adapt`; the session cannot create/switch to the requested track branches. Separate parallel agents/branch merges were **not** performed. Track handoffs: [V](handoff-v.md), [X](handoff-x.md), [P](handoff-p.md), [Q](handoff-q.md), [Y](handoff-y.md).

## Results

| Gate | Result |
|---|---|
| Full backend suite | **1,353 passed, 2 skipped** (154s); original reported missing-level preparation test passes unchanged |
| Frontend production build / TS + contract lint | Passed; 117 backend routes / 103 frontend calls |
| Frontend unit suites | **64 passed** (42 lib, 15 engine, 2 server, 5 dev) |
| Banned-copy crawl | All 16 routes, signed out + signed in, plus public privacy endpoint; passed |
| New Round 4 Chromium gate | Passed: 6 auth entries, 4 fresh journeys, 2 returning journeys, 48 route screenshots |
| Legacy complete-games/subject/keyboard suite | **9 passed, 2 intentional mobile wording skips**, twice consecutively (9.1m and 9.2m); second run includes final game/pref CSS |
| Legacy scripted-provider full journey | **2 passed**, desktop + mobile; all three lesson moments; **not a live-provider test** |
| Art bounds | No intersections with content, text, controls or other art, inflated by 16px; all-route disclosures also expanded |
| Art / new code budget | **134,657 bytes gzip** vs 150,000 byte cap |

The two backend skips and the two mobile wording-test skips are existing suite behavior, not new exclusions. A Starlette/httpx deprecation warning remains. No tests were removed or marked skipped to obtain these results.

### Auth: functional interaction within 3 seconds

Measured from submit click to `/journey` current card **and opening/closing the disclosure** (not merely URL change). Fresh consent/signup/login are performed through the actual UI, beginning at the root welcome. Returning accounts resume their persisted practice stage, follow its link, and can write another lesson.

| Viewport | Flow | Interactive |
|---|---|---:|
| 1280×800 | guest | 479 ms |
| 1280×800 | register | 1005 ms |
| 1280×800 | login | 643 ms |
| 390×844 | guest | 314 ms |
| 390×844 | register | 361 ms |
| 390×844 | login | 952 ms |

### Journey coverage, without overstating it

Four fresh guest/account paths at desktop + phone answer all 13 preferences through the UI, revisit Question 1 with Back, verify progress, Back-preserved selection, all 13 submitted answers and the exact persisted free-text preference, use the supported early game-stop action, and explicitly skip optional stages with real server receipts. Each writes a checked photosynthesis lesson, submits a specific wrong option, uses the keyboard for two distinct reteaches, marks the lesson done through the actual disclosure control, and reaches practice. Returning login resumes that persisted stage on both viewports.

These four paths **do not play every game or complete every optional subject round**. The separate legacy games suite does play the entire baseline, next-day check and subject round on desktop/mobile, plus a complete keyboard-only sitting; it retains all trial, verdict, snapshot and number assertions. The scripted-provider suite additionally exercises prepared content and three lesson moments.

## Before / after copy

Measured against base commit `7045ede` using a separately built archive. The old frozen gate is bypassed **only** to measure old copy, never in the new auth tests. Word counts use visible text nodes, exclude closed disclosures; “body” excludes headings, labels/options and controls. Total visible words are also included so exclusions are auditable.

| Screen | Before body / all visible | After body / all visible |
|---|---:|---:|
| Consent | 36 / 84 | 19 / 46 |
| Sign in | 51 / 83 | 15 / 43 |
| Journey | 123 / 167 | 17 / 31 |
| Format | 34 / 94 | 11 / 74 |
| Preference 1 | 37 / 83 | 19 / 65 |

Journey above-fold copy is **31 words**, under 60. All measured first-run screens (all 13 preferences, guest and account on both viewports) are under the 40-body-word budget; maximum measured body is 19. This is the same constrained state at the same viewport, not a claim that every baseline state had the reviewer's approximate 244 words.

## Art, bounds and performance

- Beige is retained. Artwork and topo are monochrome blue external masks, not photos or filled illustrations. Opaque central sheet and separate gutter/tail cells prevent any pattern behind content.
- The guard measures conservative full artwork rectangles (including transparent pixels), all requested semantic elements, cards and all visible text ranges. Both decoration classes must have `aria-hidden=true` and pointer-events none. Expanded disclosures are rechecked; hidden/inert/closed-disclosure content is correctly excluded.
- Topo opacity .12; ~520px native repeat; faded edges; absent below 900px and whenever a safe gutter is unavailable. At 1024px authenticated routes have no topo. The only phone hero is the consent flower. Active teaching/practice removes classroom art.
- Access flower occupies a genuine desktop viewport corner at 340×560; at compact desktop widths it uses a separate empty flow tail. The 230×230 brain gets a dedicated right gutter only when it fits. No artwork is squeezed onto content to meet a nominal size.
- SVG gzip: **132,090 bytes**. Sum of production JS/CSS gzip grew from 407,676 to 410,243 bytes: **2,567 bytes net feature code**. Add SVGs for the 134,657 byte combined budget. Compression is Node `gzipSync` default, summing each built static resource, excluding source maps.
- Local unthrottled LCP across the 48 captured routes: **36–452 ms** (all non-null, below 2,500ms). Largest encoded resource sum: **607,916 bytes**, below the explicit 2MB gate. This is a local smoke budget, not field/Core Web Vitals evidence.
- Dark-media journey bounds and screenshots were checked at all three viewports. A complete dark-theme/contrast audit **was not performed**.

## Screenshots and machine-readable evidence

[All measurements](browser-results.json). [Screenshot index](screenshots.md). [Recorded command output](verification-logs/).

- `docs/screens/round4/{1280x800,1024x768,390x844}/`: every route from `frontend/src/app/**/page.tsx` (16 routes × 3 sizes), plus dark-media journey.
- `docs/screens/round4/`: root welcome, consent/auth (including 1024px), and first/last preference screenshots.
- `docs/screens/x/`: desktop/mobile initial lesson and wrong-choice + two-reteach screenshots for guest, account, returning login.
- `docs/screens/F/`: legacy full games, keyboard sitting, subject and snapshot evidence.
- `docs/screens/G/`: separate scripted-provider prepared-content journey.

## Reproduce

Use an installed backend dev environment (`pip install -e backend[dev]`) and `npm ci` in frontend and each legacy e2e directory. Use the supplied Chromium binary; **do not run `playwright install`**.

```sh
# Backend, from repo root
python -m pytest backend/tests -q
# Local API; field-only is sandbox-only. Production should use its encryption setup.
ADAPT_DATA_DIR=.data_dev/r4 ADAPT_DB_ENCRYPTION=field-only ADAPT_MODE=local \
  ADAPT_RATE_LIMIT_MULTIPLIER=60 uvicorn adapt.api.app:app --host 0.0.0.0 --port 8000

# Separate terminal, frontend/
npm run lint
npm run test:lib && npm run test:engine && npm run test:server && npm run test:dev
npm run build
NODE_ENV=production node server.mjs
# Separate terminal, frontend/
npm run test:e2e:round4
npm run test:e2e:banned
# Complete baseline + subject + keyboard, e2e/games/
ADAPT_CHROMIUM=/opt/pw-browsers/chromium npm test
```

Optional before comparison needs a separate frontend built from `7045ede` and served on port 3002 against the local API. Then, from frontend/:

```sh
ADAPT_BEFORE_URL=http://127.0.0.1:3002 \
ADAPT_BEFORE_STATIC_DIR=/path/to/base/frontend/.next/static \
  npm run test:e2e:round4
```

Those optional arguments enable before-copy and **combined new-code-plus-art** budget verification; without a base static directory only the absolute art, route-weight and LCP budgets are tested. The saved JSON here includes the combined check.

The current live preview uses `ADAPT_NEXT_DIST=build/round4` (an ignored alternate build directory so building did not disturb the running game tests). A normal fresh `npm run build` / production start works without this override.

The scripted-provider journey needs `scripts/serve_with_fake_model.py` as a separate API and a web process proxying to it (`PORT=8001`, web `PORT=3003 ADAPT_API=http://127.0.0.1:8001`). Run in `e2e/journey-full` with `ADAPT_E2E_BASE=http://127.0.0.1:3003 ADAPT_E2E_API=http://127.0.0.1:8001 ADAPT_CHROMIUM=/opt/pw-browsers/chromium npm test`.

## Explicit limitations

- **Live Gemini is unverified.** No live provider was called; this sandbox run uses local fallback and a clearly separated scripted fixture. No API/provider/model selector was added to learner UI.
- The reported original backend failure was **not reproduced**; neither its invariant nor scoring verification was weakened. See V handoff.
- Separate track branches/parallel workers/merges were not possible in this fixed-branch session. Cross-owner changes were integrated here; the handoffs do not imply parallel branch work occurred.
- Local LCP is not a production performance study; dark-media screenshots are not a full dark contrast audit. Preference/example cards remain semantic labelled controls, never icon-only guesses.
