# Adapt design system (Round 6)

One page of rules. The tokens live in `frontend/src/styles/tokens.css` and nothing in
`frontend/src` may hardcode a colour or a type size — `scripts/check-frontend-style.mjs`
(the grep lint) fails `text-black`, `#000`, `bg-black` and bare `font-bold`, and CI runs it.

Proof pictures: `docs/shots_r6/` (every page × 1440×900 and 390×844 + `contact-sheet.png`),
`components-wizard.png`, `components-test-lab.png`, `r6-animator` scene kinds in
`docs/R6_VOICE_ANIMATOR_STATUS.md`.

## Colour

| token | value | use |
| --- | --- | --- |
| `--ink` | `#123a8f` | **all** headings, bold text, strong — never black |
| `--accent` | `#2456c8` | primary buttons (fill, white text), links |
| `--body` | `#3c4a68` | body copy (dark slate-blue) |
| `--muted` | `#66739a` | hints, captions, metadata |
| `--bg` / `--panel` | `#f4eee0` / `#fbf7ee` | warm page / cards |
| `--line` | `#e2d9c6` | hairlines, card borders |
| `--tint-blue` `--tint-sand` `--tint-mint` `--tint-peach` | soft fills | stat tiles, icon tiles, card tones |

Buttons: **primary** = blue fill + white text · **secondary** = white fill + blue border ·
never solid black.

## Type

display 40/44 serif · h1 30/36 serif · h2 22/28 · h3 18/24 · body 16/26 · small 14/20.
Minimum body 16px; nothing below 14px except legal footer. Labels are sentence-case 14px
medium — UPPERCASE mono only for tiny status chips. Classes: `.t-display`, `.t-h1`, `.t-h2`,
`.t-h3`, `.t-small`, `.t-label`, `.stat-label`.

## Shape & space

Cards 20px radius · buttons 14px · chips 999px. One soft shadow
(`0 12px 28px rgba(18,58,143,.10)`). Spacing 8/12/16/24/32/48 · gutters 32 (16 on mobile).
Content max 1180px centred; the test sheet stays ≥900px on desktop.

## Layout parts

- **PageShell / PageHeader** — one header per page: title (`.t-h1`), subtitle, optional art.
  No ad-hoc headers.
- **Card** — one tone per page (`data-tone="blue|sand|mint|peach|muted"`).
- **Sidebar** — sticky, full 100vh, including on tall pages (defect 8).
- **Topo pattern** (`topo-fill`) — only in wordless space: page gutters, hero corners behind
  no text, empty-state surrounds. ~520px tile, opacity 0.10–0.14, under content. Pixel-tested
  on Home/journey/Settings gutters.

## Art rules (test-enforced)

Big and above the fold. Classroom art only in learn/first-lesson empty states (gone once a
lesson starts). Person-writing art only in the Home hero. Brain = mind check · gears =
Settings · flower sits bottom-left of sign-in/consent/welcome. The SVGs in
`frontend/public/art/` are the author's own — never edit them.

## Components

- `ui.tsx` — Btn (primary/secondary/ghost), Card, Tag, Notice, Check, Textarea.
- `choice-cards.css` — the big pick tiles (Level / Style / Goal): icon tile + label + hint.
- `tests.css` / `components/tests` — item cards (option tiles, drag chips, truth grid, Venn
  hits, number line, fraction bar, cloze, quick recall) — see `components-test-lab.png`.
- `animator` — scene stage: shapes, person, car, triangle, number line, graph from safe
  primitives only.
- `route-error.tsx` — the one friendly error card (plain words, Try again / Go home).
