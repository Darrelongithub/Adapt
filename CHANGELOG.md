# Changelog

## Round 6 — "make it alive, make the tests real, make it consistent"

### Design (Tracks A/C/D)

- **Design tokens** own every colour, type size, radius and spacing
  (`frontend/src/styles/tokens.css`); a grep lint fails black text/buttons and bare
  `font-bold` anywhere in `frontend/src`. Deep brand blue `--ink` on every heading.
- **Home** rebuilt to the CampusCode reference: study-art hero at the top, visible without
  scrolling at 1440×900 **and** 390×844 (proofs in `docs/shots_r6/home-*.png`), tinted
  rounded stat tiles with icon tiles, serif display headline, topo in hero corners behind
  no text.
- One `PageHeader` + one card tone per page; ad-hoc headers deleted. Sticky sidebar that
  stays full height on tall pages. Type floor raised (body 16px, labels sentence-case).
- `/journey` is the subject wizard (big "What do you want to learn?" input + chips); the
  standalone `/subject` page and nav entry are gone. The fake self-rating `TestCard` is
  deleted everywhere.
- `/lesson`/Learn show only chosen-subject lessons with a classroom-art empty state; no
  built-in lessons before a learner chooses. Progress = coloured skill bars + evidence +
  "how the app sees you". Settings = gears hero + grouped cards. Topo pattern visibly in
  the gutters of Home/wizard/Settings.

### Real tests (Track B)

- **12 interactive item types** (pattern pick, drag-to-order, drag-to-match, odd one out,
  truth-table grid, Venn region tap, geometry angle/congruence taps, number-line &
  fraction estimates, word-bank cloze, quick recall) each scoring
  `correct/incorrect + ms + hint use` into real evidence rows
  (`POST /tests/evidence`, read by the dashboard and AI 1).
- **Gemini plans, code validates**: the model proposes
  `{item_type, skill, difficulty 1–5, parameters}`; the registry drops unknown types and
  bad parameters, clamps difficulty, caps 8 items, requires ≥4 items and ≥2 skills. The
  payload says `source: model|simple` — never a provider name.
- **Deterministic simple mode** per family; the learner's own words pick the family, so
  "geometry" and "discrete maths" produce visibly different interactive scored tests
  (zero shared item types) — proven end-to-end by `tests-e2e/round6-items.mjs`.
- Pre-test screen: big tiles for Level, Style and Goal → into the plan request.
- `test_plan` model purpose registered in the routing table + `docs/EFFICIENCY.md`.

### Stability (Track E)

- Root `error.tsx`/`global-error.tsx` + per-route friendly error cards with Try again /
  Go home.
- **Zero-crash crawl** (`tests-e2e/round6.mjs`): 15 routes × 2 viewports × 3 sessions
  (signed-out, guest, signed-in) — no console errors, exceptions, non-auth 4xx/5xx, blank
  bodies, hydration warnings or error screens; rapid tab-switch (10 routes × 3 laps,
  <300ms) passes; SessionGate loop regression asserts included. Record:
  `docs/R6_CRASH_LOG.md` (includes the landing-gate removal that caused the loop).

### Alive (Track F)

- Scene frames now draw the full vocabulary — **shapes, person, car, triangle, number
  line, graph** — so each template looks like its subject (car-motion moves a car,
  function-plot plots a curve, grammar diagrams show a person). Code-owned primitives
  only; the author's art SVGs untouched.
- Verified **4 lessons' scenes** built from the template library and the **4 simulations**
  (binary search, insertion sort, logic gates, CPU cycle) validating deterministically
  with no answer leaks (`tests/test_track_f_scenes.py`, 276 sims+scenespec tests).
- **Voice**: greeting on entry, read-aloud on lesson frames and review questions
  (`ReadAloud`), listen-and-answer in the voice console, one app-wide toggle — silent
  when off. Honest table of what runs locally vs the optional engine:
  `docs/R6_VOICE_ANIMATOR_STATUS.md`.

### Track G — evidence

- `docs/shots_r6/`: every page at 1440×900 and 390×844 (30 shots) + `contact-sheet.png`
  + component shots (wizard, test-lab, journey). Regenerate with
  `frontend/tests-e2e/round6-shots.mjs`.
- **Topo gutter pixel test** (`tests-e2e/round6-topo.mjs`): luminance spread measured
  on 10 gutter strips across 5 pages (σ ≈ 4.6–5.1 where flat beige scores ~0) — the
  pattern is visibly present in ≥4 pages' gutters. It found the gutter columns
  collapsing to zero at 1440px and the grid now keeps a real minimum.
- `docs/DESIGN_SYSTEM.md` — the token/type/layout rules in one page.
- `scripts/live_model_check.py` was **not** run: no model key is configured in this
  sandbox (the script needs `GEMINI`/console key configuration).
- Gates: backend 1442 tests green · `npm run test:lib` 47 green · typecheck · lint
  (contract) · grep style lint · E2E crawl + item click-through green.
- One zip without `node_modules`/`.next` (see the release attachment),
  `/home/user/adapt-round6.zip` — 9.6 MB. It omits `docs/screens/` (the older
  rounds' screenshot archive, still in git) and `art_pkg.zip` to stay under 25 MB.

## Round 7 — repair and polish

- Centralized route illustration eligibility and placement metadata in `frontend/src/components/shell/artMap.ts`; pages use the route-checked `PageArt` renderer instead of artwork directly.
- Kept the Home study scene, baseline brain, Settings gears, and conditional classroom artwork limited to the wizard/Learn empty state. Other routes reject art.
- Repaired the welcome overlay layout with a centered display headline, supporting line, primary action, and small Skip link.
- Expanded the baseline content column and added a tinted top brain hero. Enlarged the journey short-games card.
- Added one friendly-label dictionary, hid unknown identifier-like labels in test, baseline, review, and progress output, and replaced setup choice icons with meaningful line icons.
- Changed the expanded sidebar toggle to “Less”. Added `frontend/tests-e2e/art-placement.mjs` and `docs/R7_PAGE_REVIEW.md`.
- Verified: backend tests 1442 passed / 2 skipped; frontend typecheck and lint passed; engine tests 15 passed; build passed; style grep clean. Browser crawl/art screenshots were blocked because Chromium is not installed in the environment.
