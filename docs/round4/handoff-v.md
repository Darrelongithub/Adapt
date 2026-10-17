# V → Y: routing, authored feedback and first-run content

## Delivered
- `lib/landing.ts` exports the single `/journey` landing. Both account completion and SessionGate use it; the gate cancels stale session generations and no longer waits forever for the competing `/baseline` route. Loading copy is neutral.
- `ai/lesson_writer/core.py` guards check IDs and normalized question stems across a plan and recent lesson moments; never repeats an attempted check as a prediction, recall or wrap-up. Checks are attached to explanation chunks, not padded into a final repeated question. If no fresh verified item remains, no duplicate is fabricated.
- `content/distractors.py`: 333 explicit, option-specific belief/temptation/correction triples, covering 37 concepts. Authored A/B lesson items reference the appropriate reasons; no generic "this is the checked answer" fill. Cache spec is `pack-skeleton/2` so existing caches acquire the new authored data.
- Public unverified-item scoring refusal remains intact, including short mathematical keys that cannot pass the quotation verifier.
- Twelve new regression cases cover all authored topics, every distractor, one-item plans and repeated model check IDs/stems.

## Reported backend failure
`tests/test_round2_g_integration.py::test_prepare_without_a_level_still_teaches_the_prepared_pack` passed on the untouched checkout in isolation, and in the initial full run (1,341 passed, 2 skipped). It also passes in the final full run. Neither this assertion nor the original production cache lookup was changed to "fix" a failure that could not be reproduced. This is **unreproduced**, not evidence that the test was wrong. The original route already searches nearby cached levels before falling back; the missing-level lookup invariant is preserved.

## Y integration applied
Shared `chrome.tsx` SessionGate change; concise journey/current-step disclosure; account PageShell wrapper (P dependency); first-run format/card spacing; optional onboarding copy; public topic label; 13-question browser regression. The preferences flow was already one-at-a-time in the baseline: verified and retained, not converted into a wall. Back, progress, single prompt, persistence and all 13 screens are exercised.

See [README](README.md) for commands, measured copy and final results. API configuration remains console-only; no live external provider was verified.
