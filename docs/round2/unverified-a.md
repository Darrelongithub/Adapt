# Part A items not verified

- **Real-browser route crawl and visual review:** `frontend/tests-e2e/banned-phrases.mjs` is syntax-checked and covers discovered page routes in signed-out and guest-signed-in contexts, but no DOM crawl or screenshots were produced. The configured Chromium executable `/opt/pw-browsers/chromium` is absent. No Playwright browser was installed, as instructed.
- **Cross-owner onboarding copy:** `frontend/src/app/onboarding/page.tsx` remains untouched because Phase D owns it. The route still contains the flagged consent/configuration and preference-key copy until Phase D applies `docs/round2/handoff-A.md`; therefore the all-routes banned-phrase crawl cannot yet be reported clean.
- **Real model-written output:** no model pass was run because no model key is configured. The teaching-quality evaluator reports zero gate flags on the deterministic path; this does not verify any live model response.
- **Integration branch operations:** this workspace is pinned to `arena/7efe7537-adapt`. No rebase, merge to `main`, or push was performed.
