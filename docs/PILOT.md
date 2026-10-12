# Running a pilot

Four to six weeks, 12–30 learners, one subject. The point is not "did scores go up" — with
that n it cannot mean anything — it is whether the loop is **trustworthy and used**: do the
proposals describe learners accurately, do learners exercise the gate, and does the measured
effect ever disagree with the app's optimism.

## Before anyone uses it

1. `pip install -e ./backend`, `cd frontend && npm ci`.
2. Decide the deployment: `ADAPT_MODE=local` on the school network, or `tunnel` with
   `ADAPT_DEMO_PASSPHRASE` set (registration then needs the phrase, and the app says so
   instead of failing with a 403).
3. `GET /api/v1/status` — check `mode`, `encryption.field_level_encryption`, and whether
   `database.file_encrypted` is what you intended. If `voice.stt.local_stt` is false, either
   install the engines (`docs/VOICE.md`) or say out loud that voice is browser-only.
4. Back up `ADAPT_DATA_DIR` including `keys/`. **The encrypted columns cannot be recovered
   without that key file**, and the app will refuse to start rather than serve a half-readable
   database — which is the failure you want, but only if you can restore the key.
5 `node scripts/demo.mjs --json` against the deployment, so the environment is known good
   before a learner touches it.

## Consent, in the order it is asked

* What is recorded: the order of events and timings. Show `GET /api/v1/privacy` on the screen —
  the same list the server enforces, not a paraphrase.
* Two toggles, both reversible: the interaction log, and whether a model may read a summary.
  Turning the first off removes the evidence base and the app says so rather than guessing.
* The consent text must include the sentence that nothing is graded, selected for, or reported
  to a third party from this data, and the app has no code path that does it.
* Nobody is asked how they learn. If a learner offers one (a learning style, an IQ, a label),
  it is stored as a stated preference and acted on as a request — the onboarding response's
  `note` says this in the app's own words; read it out.

## What to collect

| measure | where | what it tells you |
|---|---|---|
| proposal acceptance rate | `GET /api/v1/adaptations/history` | whether the proposals describe learners at all |
| rejection reasons | the `note` on each decision | where the reasoning is wrong, in their words |
| revocation rate | `active` vs `decided_adaptations` | proposals accepted out of politeness get revoked |
| calibration | `plan → retention.calibration` (Brier, ECE) | whether the app's confidence means anything |
| measured effect | `dashboard → measured_outcomes`, `POST /adaptations/evaluate` | how often a "helpful" change was not |
| time-on-task | `last_7_days` | whether the loop is a burden |
| disproof rate | hypothesis `status` transitions | whether the system changes its mind |

Ask each learner two questions at the end: *did any proposal describe something you already
knew?* and *did any proposal you said yes to turn out not to help?* The second one is the
honesty test for the whole design.

## What would count as this not working

* proposals are accepted at ~100% and never revoked → the gate is ceremonial; make the ask
  harder (it already refuses to re-ask a rejected pattern)
* `evaluated` never leaves 0 → the review window is too long to ever close, or too few
  learners are answering enough items; shorten the window, do not lower the bar
* calibration ECE above ~0.15 after a few weeks → the estimates are theatre; stop tuning
  difficulty on them
* any learner who cannot say what a proposal was offering → the wording failed, which is a
  product defect, not a comprehension problem

## Deliberate non-goals

No teacher dashboard, no grades, no attainment labelling, no streaks or badges, no
password reset (whoever runs the instance resets it), no notifications. Each of those is a
way for a system like this to stop being the learner's and start being the institution's.
