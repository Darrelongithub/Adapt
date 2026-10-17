# P → Y: beige / blue line-art shell

## Delivered
- New `components/art/Artwork.tsx`, `components/shell/{PageShell.tsx,shell.css}` and six static SVG masks in `public/art/`.
- Opaque max-width central sheet, separate patterned gutter cells, separate art cells/tails. No global texture or negative-z-index decoration behind content; former grain mount removed during Y integration.
- Native ~520px topo tile at opacity .12, faded with an intersected edge mask; `--art-ink` blue token with dark-media variant. The pattern is only instantiated in empty gutters, never as a viewport overlay. Pattern/art cannot share a cell.
- Access flower bottom-left at 340×560 on sufficiently wide screens; left pattern ends 24px before its box. At compact desktop widths the flower goes into a safe flow tail rather than squeezing beneath the form. On phones only the consent flower survives.
- Mind-check brain is 230×230 in a reserved 278px right gutter, left topo; hidden when games begin and when there is insufficient gutter space. Journey/home study, settings gears, and empty-lesson classroom use separate lower art regions. Classroom disappears when a lesson or practice item mounts. Other routes get gutter topo + a small header icon.
- `/art/*` has public cache headers. All decorative nodes are `aria-hidden`, pointer-events none, with no labels or interactive descendants.

## Integration / tests
Y applied root/chrome/account/opening wrappers and the active-practice marker. Automated conservative bounding-box guard (`tests-e2e/art-zones.mjs`) includes whole semantic boxes, cards, and **all visible text-node ranges**, with 16px clearance, including expanded disclosures. It checks pattern vs art too, and phone/tablet omission. The SVG art alone is 132,090 bytes gzip; combined feature bundle delta + art is recorded in `browser-results.json` under 150,000 bytes.

Additional access-width smoke checks at 1280, 1240, 1200, 1024, 900, 899 and 390 had no overlaps. All-route / active-lesson checks and screenshots are in README. Dark-media journey geometry/screenshots were tested; a comprehensive dark-theme contrast audit was not.
