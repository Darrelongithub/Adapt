# CONSOLE_STYLE — the palette, and why each colour is allowed where it is

Status: **normative for `engine/src/adapt_engine/console/`**. Where this file and a stylesheet
disagree, this file wins and the stylesheet is a bug.

The console is the one surface a buyer sees before they read a word of documentation. It has to look
like something a serious institution commissioned, and it has to survive `axe` — which means **a colour
that looks right and fails 4.5:1 is not allowed on text**, however good the mood board was.

---

## 1. The three-colour budget

Roughly **60% beige and off-white, 30% blue, 10% accent**. The discipline is what produces the look; the
hexes alone do not. A page that is 40% terracotta is not a warmer version of this design, it is a
different and worse one — accent everywhere highlights nothing.

| Share | Role | Where it lands |
|---|---|---|
| **60%** | ground and paper | `body` background, every card, table rows, the header bar |
| **30%** | structure and weight | H1/H2, the primary button, badges, the table header rule, focus rings, the level sentence |
| **≤10%** | the one thing to click, plus an occasional mark | the primary action, the active nav item, one number that matters, a chart mark |

Per screen, **at most three accent-coloured elements.** If a fourth appears, something in the first
three was not important enough to be there.

---

## 2. Tokens — the only place a hex may appear

`styles.css` holds these in `:root` and **nowhere else in the repository**. A hex literal anywhere else
in `console/` is a test failure (§6).

```css
:root {
  /* ground — 60% */
  --bg:           #F3ECDF;  /* beige page */
  --surface:      #FBF8F2;  /* cards, header bar */
  --surface-sunk: #EDE4D4;  /* table header, insets, disabled fill */
  --rule:         #DDD2BF;  /* borders, dividers */
  --rule-strong:  #C9BBA0;  /* rule under a heading that needs weight, never a lone affordance */

  /* ink and structure — 30% */
  --ink:          #1B2230;  /* headings, values */
  --ink-body:     #3A4356;  /* prose, sentences */
  --ink-muted:    #6B6357;  /* labels, captions, table headers */
  --blue:         #23395B;  /* primary fill, links, focus ring, level sentence */
  --blue-soft:    #E4E7EE;  /* badge/chip fill */

  /* accent — ≤10% */
  --accent:       #C4643F;  /* terracotta: marks, rules, ≥19px display figures, 3:1 UI only */
  --accent-fill:  #9E4A2B;  /* terracotta darkened: button fills that carry 13px label text */
  --accent-ink:   #8E3F22;  /* terracotta darkened: accent text at body size, if ever needed */
  --accent-soft:  #F2E2D8;  /* selected row, chip tint */

  /* state */
  --null:         #7A5A0F;  /* "no data" — mustard ink, never plain mustard */
  --ok:           #546E58;  /* validated / passed — sage ink, never plain sage */
  --alert:        #7A2E35;  /* a failing gate, a real error — oxblood */
}
```

---

## 3. Measured contrast, and the rules that follow

Every number below is computed, not estimated (WCAG 2.1 relative luminance). AA text needs **4.5:1**,
large text (≥18.66px bold or ≥24px) and UI marks need **3:1**.

| Pair | Ratio | Verdict |
|---|---|---|
| ink on beige / on card | 13.56 / 15.03 | body text, any size |
| ink-body on beige / card | 8.44 / 9.36 | prose |
| ink-muted on beige / card | 5.04 / 5.58 | labels — **passes**, so labels are not grey mush |
| blue on beige / card | 9.89 / 10.96 | headings, links, focus |
| surface on blue (primary button) | 10.96 | button text |
| oxblood on card | 8.73 | errors, failing gates |
| mustard ink `#7A5A0F` on card | 6.01 | "no data" |
| sage ink `#546E58` on card | 5.29 | validated |
| **terracotta `#C4643F` on card** | **3.78** | **large text and marks only — fails AA for body text** |
| surface on `accent-fill` `#9E4A2B` | 5.71 | terracotta buttons with 13px labels |
| **raw mustard `#C99A2E` on beige** | **2.19** | **never text** — retired from text roles |
| **raw sage `#7F9A83` on beige** | **2.61** | **never text, never a button fill** |
| border on beige | 1.27 | borders are not the sole affordance — a required one is blue at 3:1 |

**Four consequences, stated because they are where this palette gets built wrong:**

1. **Terracotta is a fill and a mark, not an ink.** At 3.78:1 it is legal for a 19px+ figure, a 2px
   rule, a chart bar, a 3:1 UI edge — and illegal for a 13px label, a table cell, or a sentence. When
   terracotta must carry small text, use `--accent-fill` (5.71 with `--surface` on it) or `--accent-ink`.
2. **Mustard and sage are moods, not text colours.** Raw mustard at 2.19:1 is the case you already
   suspected; raw sage at 2.61:1 fails just as hard. Use `--null` and `--ok`, which are the same hues
   darkened until they pass.
3. **Oxblood is the one accent that is safe as body text** (8.73:1). It is for "this gate failed", not
   for decoration — it says expensive and wrong, and both halves matter.
4. **A border is never the only thing that marks an input.** `--rule` at 1.27:1 is a hairline for
   grouping. A field that needs to be found gets `--blue` on focus and `--rule-strong` plus a label at
   rest.

---

## 4. Type, weight and space

Classy here means **serif for claims, sans for measurements**, and no decoration that the data cannot
justify.

- **Display / headings:** `Georgia, "Iowan Old Style", "Palatino Linotype", "Times New Roman", serif`.
  H1 26px/1.2, H2 15px uppercase with `letter-spacing: .06em` and `--ink-muted` — an editorial card
  label, not a browser default `<h2>`.
- **Body:** same system sans stack as today. 13px/1.55, `--ink-body`.
- **Numbers:** `font-variant-numeric: tabular-nums`, `--ink`, 600. A number is never in the serif face.
- **One type scale:** 26 / 19 / 15 / 13 / 12. No sizes between them; that is what makes it look drawn
  rather than assembled.
- **Space:** 4px base — 4 / 8 / 12 / 16 / 24 / 32 / 48. Card padding 20px 24px, card gap 16px, page
  gutter 32px, `max-width: 1120px`.
- **Radius:** 3px on inputs and buttons, 0 on cards and tables. A card with a shadow and a 12px radius
  is a different, softer product; the flat card with a hairline is the one that reads as paper.
- **No gradients, no shadows, no glow.** One 1px `--rule` and one 2px accent rule under the page title
  are the whole ornament budget.

---

## 5. Component grammar

| Element | Spec |
|---|---|
| Page background | `--bg`; card `--surface` + `1px solid --rule` |
| Header bar | `--surface`, `1px solid --rule` bottom, 24px 32px padding, H1 in serif `--ink`, `.sub` in `--ink-muted` |
| Primary button | `background: --blue`, `color: --surface`, 13px/600, 3px radius, `min-height: 36px` |
| Destructive / risky | `background: --alert`, otherwise identical — there is one alert colour and it means it |
| Input | `--surface` fill, `1px solid --rule`, `3px` radius; focus `outline: 2px solid --blue; outline-offset: 2px` |
| Card title | 15px, uppercase, `.06em`, `--ink-muted`, with a `1px --rule` beneath spanning the card |
| Label / value row | label `--ink-muted` 13px; value tabular 600 `--ink`; 6px between rows |
| Table | header `--surface-sunk` fill, `--ink-muted` 12px uppercase; rows separated by `1px --rule`; no vertical rules |
| **No data** | `--null`, **700**, *italic*, and the literal words — see §7 |
| Validated | `--ok` text plus a `✓` glyph, never colour alone |
| Accent rule | 2px `--accent` under H1, spanning only the H1's width |
| Selected row | `--accent-soft` fill with a 2px `--accent` left border |

---

## 6. How the tests hold this

The palette is only a spec if a machine can fail on it. Four gates, all in the existing suite:

1. **No stray hex.** Grep `console/` for `#[0-9a-fA-F]{3,8}`; only `:root` in `styles.css` may match.
2. **Contrast computed, not asserted.** A test parses `:root`, then checks every declared pair in §3
   against its threshold and fails with the pair and the ratio. Changing a hex and forgetting the doc is
   a red test, not a review comment.
3. **The accent budget.** `console_shot.mjs` counts rendered elements whose computed colour is `--accent`,
   `--accent-fill` or `--accent-ink`; more than three on a screen fails.
4. **Screenshots read the tokens.** `console_shot.py` and `console_shot.mjs` **parse `:root` from
   `styles.css`** — they must not carry their own copy of the palette. A screenshot that hardcodes
   `#f9fafb` while the console ships beige is a lie in the repository, which is exactly the class of
   defect this project keeps catching late.

`axe` still runs and still has to pass; these gates exist so that `axe` is not the *only* thing standing
between the design and a contrast regression.

---

## 7. "No data" must not look like "0", and colour cannot be the difference

An empty window reports `null` and the console prints **no data** — never `0%`, never `0`. This is the
product's central claim rendered as a pixel, so it gets three signals, not one:

1. the words (`no data`, not `0`);
2. weight and style (`--null`, 700, italic) against the tabular 600 upright of a real value;
3. the explainer sentence already written into the empty-window copy.

Colour is deliberately third, because a deuteranope sees none of it. A null that differs from a zero
only in hue is a null that half the room cannot see.
