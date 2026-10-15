# Round 2 — Phase B report

## What landed

- Added the frozen `adapt.content.skeleton.get_pack(topic, *, level, language, model=None, force_simple=False) -> Pack` interface and typed `Pack`, `Concept`, `Item`, facts, misconceptions, options, and verification records. The serialized Pack shape stays within the shared contract; runtime calibration is deliberately private and omitted from Pack JSON.
- Added a read-only adapter for authored content. Matching authored topics take precedence and their answer keys are retained; authored items are left `unverified` rather than silently promoted into scoring. When no checked content can be returned, `simple` packs contain four generic study concepts and no scoreable questions.
- Added strict, bounded parsing for model skeletons and item pairs: 4–10 concepts, unique IDs, an acyclic prerequisite graph with depth at most eight, bounded text, no markup/links/placeholders, real fact references, requested-language checks, consistent A/B format/rung/difficulty, three hints prompted as nudge/method/near-answer (with exact-key leaks rejected), three teach-back points, and misconception-linked distractors.
- Added item checks before scoring: computational items go through the exact symbolic checker; other items go through a separate verifier prompt limited to the cited facts. Rejections include fixed, content-free code/reason logs. Invalid, unsupported, and unverified items fail closed. IDs use `assessment.generated.content_id`; declared/observed difficulty and retirement use the shared `observed_difficulty` and `should_retire` rules.
- Added bounded model execution: constrained response schemas, per-purpose token ceilings, personal-data redaction, topic delimiters, one retry with backoff, 20-second calls, a 32-attempt per-Pack budget, a process-local circuit breaker, and count-only per-call audit records. Logs do not include prompts, outputs, topic text, or exception messages.
- Added an encrypted Pack cache keyed by the topic digest, level, normalized language, and spec version, plus content hashes, prepare-job records, and response-only item-calibration persistence. Migration `P3` creates the cache, job, and calibration tables and has a downgrade that drops them in dependency-safe order.
- Added the asynchronous `POST /api/v1/content/prepare` and `GET /api/v1/content/prepare/{job_id}` router. Status exposes only `running|ready|fallback` and a source label; neither topic text nor item keys are returned. Route registration is intentionally handed to Phase G.
- Added scripted fake-model tests for malformed/extra-key, cyclic, duplicate-ID, wrong-language, wrong-key, leaked-answer, wrong-verifier-key, injection, PII redaction, timeout, circuit-breaker, cache/job, calibration, and retirement behavior.

## B-owned files

- `backend/src/adapt/content/skeleton/` — public types, strict parsers, schemas, authored adapter, simple fallback, service, and cache/calibration persistence.
- `backend/src/adapt/content/symbolic.py` — bounded exact-checker entry point for generated computational keys; unsupported expressions are not accepted.
- `backend/src/adapt/api/routes/content_prepare.py` — background prepare and status endpoints.
- `backend/migrations/versions/P3_content_pack_cache.py` — cache, job, and calibration tables with downgrade.
- `backend/tests/content_skeleton/` — scripted fakes and regression tests.
- `docs/round2/report-b.md`, `docs/round2/handoff-b.md`, and `docs/round2/unverified-b.md` — report, integration requests, and residual verification notes.

No authored-topic source, `ai/lesson_writer`, frontend, app route registry, or migration-test owner files were edited.

## Verification

| Check | Result |
|---|---|
| Ruff format/check for the skeleton package, prepare route, P3 migration, B tests, and `content/symbolic.py` | **Passed** |
| `pytest -q tests/content_skeleton tests/test_generated_items.py` | **38 passed** |
| `pytest -q tests/test_content_engine.py` | **62 passed, 1 skipped** |
| Full backend `pytest -q` | **1142 passed, 1 skipped, 4 failed** |
| P3 migration in the full suite | Upgrade, migration drift check, and downgrade-to-base checks passed. The one migration failure is the existing-`create_all` bootstrap/schema expectation described in the Phase G handoff. |
| `git diff --check` | **Passed** |

The four full-suite failures are: (1) `tests/test_migrations.py::test_an_existing_create_all_database_can_be_stamped_then_upgraded`, because the migration-test bootstrap does not import the new cache models before `create_all`; (2–4) the unrelated Phase A secrecy-args, MCQ-nudge, and closing-prompt tests. The earlier B-owned injection assertion was corrected; the B test suite passes.

This is a backend-only phase, so no browser run or screenshots were produced. The router is tested directly but is not mounted until Phase G completes registration.

## Handoffs and limits

See [`handoff-b.md`](handoff-b.md) for required router registration, migration-test bootstrap updates, the symbolic-engine decision, and the upload boundary. See [`unverified-b.md`](unverified-b.md) for live-provider, token-audit, and integration caveats. In particular, the requested literal “SymPy path” is not implemented: this checkout's existing symbolic module documents an exact `Fraction` engine instead, and unsupported operations are rejected. No SymPy dependency was added.
