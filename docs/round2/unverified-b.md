# Phase B — residual verification notes

## Hint grading

The schema requires three plain-text hints, and the item prompt explicitly requests nudge, method, then near-answer. Code rejects an exact answer copied into a hint, but it does not independently classify the semantic specificity of each hint; that progression still depends on the model and was not evaluated with live output.

## Live model/provider path

No real model key was configured, so no live Gemini output was generated or independently judged. Tests inject scripted synchronous fakes through the frozen callable interface. The prepare router's bridge to the configured async LLM client is implemented, but provider response-schema behavior, real latency, provider-side cancellation, and live language quality have not been verified.

Model audit `tokens_in`/`tokens_out` are count-only estimates based on text size (roughly characters divided by four), not provider-reported token counts, billing usage, or cost. The audit does not record raw prompts, outputs, topic text, answers, or model settings.

## Symbolic checker scope

The repository's checker is an exact rational implementation, not SymPy. It is tested through the computational fake and content-engine suite. It rejects unsupported syntax and returns an unsupported/rejected result instead of claiming verification. Whether that satisfies the Phase B document's literal “SymPy path” requirement remains an explicit Phase G decision; no SymPy dependency or broad operations such as integration/trigonometric identities were added.

## Fallback language coverage

The deterministic simple pack has authored generic text for English, Swahili, French, Spanish, Portuguese, and German. Other language tags currently receive the English simple copy. Model output in supported languages is checked before acceptance, but a model-unavailable fallback outside those six localizations does not meet the requested language; expand the simple translations before promising fallback coverage for additional languages.

## Endpoint and inputs

The route module is tested directly, including route shapes, job state, cache-hit handling, and the fact that status records contain no raw topic. It is not yet included in the FastAPI application router registry, so no end-to-end HTTP/browser check of these paths was possible in Phase B. Phase G must register it before deployment.

The API accepts topic text only; uploads are not implemented. Redaction and injection handling are tested for topic strings and model-generated text passed to the verifier, not for file extraction or OCR.

## Full backend suite

The final full-suite run completed with **1142 passed, 1 skipped, 4 failed**. One failure is the out-of-scope migration-test bootstrap mismatch documented in `handoff-b.md`; the other three are Phase A test failures listed there. No B-owned tests failed. Ruff checks passed for the B package, route, migration, tests, and symbolic checker; focused B and content-engine tests passed.

No screenshots were generated because Phase B does not own UI work.
