# Audit evidence index

The checklist and verdicts are in `../AUDIT_REPORT.md`; tickets are in `../AUDIT_FIXLIST.md`.

## Phase-2 current evidence

- `fix-a01-*`, `fix-a06-*`: explicit entry and analytics consent.
- `fix-c02-*`, `fix-c05-*`, `fix-c06-*`, `fix-c09-*`: sealed plans, server grading, drag order, resume, and evidence integrity.
- `fix-d07-*`, `fix-e02-*`, `fix-e04-*`: review direction, authored suggestions, and reflection persistence.
- `fix-f04-*`, `fix-g14-*`: built study tools and learner export.
- `fix-g04-*`, `fix-g09-*`: telemetry consent and trusted-proxy/rate-limit controls.
- `fix-i03-*`: current browser-suite counts and logs. The scripted-fake journey is not real-model evidence; Round 4 art verdicts are NOT RUN.
- `fix-phase2-backend-pytest.log`, `fix-phase2-frontend-tests.log`: final full unit/contract regression runs.

## Phase-1 baseline evidence

- `harness-corrections.md`: exploratory assertions that must not be mistaken for application verdicts, including phase-2 driver corrections.
- `db-final-verification.json`: authoritative offline SQLCipher snapshot for consent, fabricated evidence, soft deletion, review, and attempt counts. The host was stopped.
- `db-purge-final-before.json`, `db-purge-final-cli.log`, `db-purge-final-after.json`: authoritative host-stopped hard-purge sequence.
- `access-browser.json`: consent, account, session, and route checks.
- `subject-journey-browser.json`, `item-types-wrong-browser.json`, `drag-order-browser.json`: subject and item behavior.
- `learning-loop-browser.json`, `practice-browser.json`, `placement-browser.json`: practice, adaptation, progress, review, and placement.
- `lessons-browser.json`, `lesson-reflection-browser.json`, `lesson-player-browser.json`, `voice-browser.json`: lessons and voice.
- `security-browser-api.json`, `rate-limit-api.json`, `transport-security-api.json`: security/privacy checks.
- `robustness-browser.json`: delayed double-submit, offline/retry, and mobile route stress.
- `backend-pytest.log`, `frontend-suite-summary.log`, `games-e2e-all.log`: primary suite counts; other suite logs are named by suite.

For phase 1, `db-counts.log` and the smaller `baseline-*` files are earlier snapshots/reruns, not baseline-final totals; the phase-1 final database snapshot and `games-e2e-all.log` are authoritative for that baseline. For current counts, use the `fix-i03-*` and `fix-phase2-*` files above. Both `journey-full-fake-e2e.log` and `fix-i03-journey-full-scripted-fake.log` use scripted fake models and are not real-model evidence.

Selected screenshots are in `screenshots/`; the JSON/API/DB records establish the verdicts.
