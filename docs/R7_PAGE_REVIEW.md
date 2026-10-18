# Round 7 page review

Visual screenshot capture could not be completed in this environment: Playwright's Chromium executable is not installed, and the Round 6 crawl exits before opening a page. The Round 6 reference captures remain in `docs/shots_r6/`; no screenshots are represented here as newly captured.

| Route / page | Review against art map and tokens | Repair status |
|---|---|---|
| Home `/` | Study illustration belongs in the top hero; flower is reserved for the welcome overlay. Keep display-serif headline and blue token palette. | Route-owned PageArt; existing hero placement retained. |
| Learn `/learn` | Classroom only in the empty state; remove after subject/lesson exists. | Existing conditional empty-state placement retained behind the route map. |
| Lessons `/lesson` | No illustration; PageHeader and lesson cards only. | Art renderer rejects this route. |
| Review `/review` | No illustration; consistent PageHeader/cards. Avoid opaque topic identifiers. | Topic labels now hide unknown id-shaped values. |
| Progress `/progress` | No illustration; chart labels must not expose topic ids. | Topic chart and gap labels now use the friendly-label dictionary. |
| Settings `/settings` | Gears belong in the top PageHeader hero. | Route-owned PageArt retained. |
| Changes `/proposals` | No illustration; PageHeader and cards. | Art renderer rejects this route. |
| Compare `/contrast` | No illustration; PageHeader and cards. | Art renderer rejects this route. |
| Evidence `/evidence` | No illustration; PageHeader and cards. | Art renderer rejects this route. |
| Voice `/voice` | No illustration; PageHeader and cards. | Art renderer rejects this route. |
| Mind check `/baseline` | Brain belongs in a large, tinted top hero; content should use full width. | Brain hero added; content max-width removed. |
| Wizard `/journey` | Classroom only while choosing a subject; no artwork in setup, tests, or results. | Map-check renderer used only in subject selection; short-games card is enlarged and tinted. |

The formal desktop/mobile captures and contact sheet remain pending a Chromium-capable environment. `frontend/tests-e2e/art-placement.mjs` includes 1440×900 and 390×844 route assertions plus visible-identifier scanning for that follow-up run.
