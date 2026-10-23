# Connectors: the only outside doors, all shut by default

Round 9 adds a small connector layer (`backend/src/adapt/connectors/`) for
two jobs: finding **curriculum requirements** for the subject wizard's
checklist, and offering optional **wellbeing reading** after a hard
check-in. Connectors never write lessons (lessons are still written by the
existing lesson writer from checked material), never receive learner data,
and are **off by default**: a fresh install makes no new outbound calls.
`adapt config status` prints every connector's state, licence and (with
`--check`) a live health probe; `adapt config status` is the one place an
operator answers "what can this install talk to?".

Shared rules (`connectors/http.py`): an allowlist of hosts, short timeouts,
no redirects, cached responses in the encrypted store, and silent failure —
a dead connector degrades to the local path, never to an error in front of
a learner. The learner UI never names a provider; where a source's titles
appear, the required attribution appears with them.

| Connector | Job | On when | Licence / attribution |
| --- | --- | --- | --- |
| Oak National Academy | scanner (UK key-stage unit titles) | a key is configured via `adapt config set-key --service oak` | Open Government Licence; "Oak National Academy" attribution shown with its titles |
| Wikimedia | scanner ("Outline of …" headings) | `ADAPT_CONNECTOR_WIKIMEDIA_ENABLED=1` | CC BY-SA 4.0; "Wikipedia contributors (CC BY-SA 4.0)" |
| Psychologie et Sérénité | wellbeing reading links only | `ADAPT_CONNECTOR_WELLBEING_ENABLED=1` | CC BY 4.0; "Psychologie et Sérénité (CC BY 4.0)" + link |
| OpenStax | — | **not wired** | CC BY-NC-SA 4.0 (recorded for completeness) |

Timeouts: `ADAPT_CONNECTOR_TIMEOUT_S` (default 8s) bounds scanner calls;
the wellbeing connector is hard-capped at 3s and caches for 24h.

## The scanner's order

Given `{subject, curriculum, grade}` the scanner tries, in order:

1. the team-maintained local syllabus map
   (`curriculum/data/syllabus_map.json` — short factual strand/sub-strand
   titles compiled from KICD curriculum designs, with provenance and date);
2. Oak unit titles, if a key exists;
3. Wikimedia outline headings, if enabled;
4. a model merge/rank of what was found (model-added rows are labelled
   "suggested", never "from the syllabus");
5. a deterministic simple list, labelled **"Not checked against a
   syllabus"**.

Code validates the result (dedupe, caps, max ~30 objectives), caches it
encrypted with source/date/licence, and shows it as a checklist the learner
can untick, reorder and extend with their own rows. Nothing downstream —
test plan, skeleton, lesson — may use an objective before the learner taps
"Use this plan"; later rescans that differ arrive as proposals, not edits.
Material the learner brings themselves outranks every external source.

## Verification notes (2026-10-23)

- **Oak** — verified against the published API docs: base
  `https://open-api.thenational.academy/api/v1`, bearer key issued
  out-of-band, free, OGL content, 1000 req/hr/key, and a `/rate-limit`
  probe that does not count against the allowance (used as the health
  check). No key exists in the development sandbox, so the client is tested
  with a fake transport only.
- **Wikimedia** — verified live: the Action API's `prop=tocdata` on
  "Outline of …" pages works keyless with a descriptive User-Agent;
  section headings become outline objectives (reference/see-also sections
  dropped).
- **Psychologie et Sérénité** — verified live: `GET /api/articles` returns
  article metadata keyless (100 req/h unauthenticated), CC BY 4.0. It is a
  single-author CBT site, not a clinical authority, so it is used strictly
  as optional "Read more" reading after a high-frustration check-in. Its
  tests endpoint is never called; nothing from it is scored or stored as a
  judgement about a learner. Article URLs are validated to its own https
  host before display.
- **OpenStax — skipped, with reason.** No documented public TOC/outline
  API exists (the CMS catalog lists books without tables of contents; the
  archive JSON is internal and version-fragile). Rather than scrape, it is
  listed in the registry as `wired=False` and reported as "not wired (no
  documented API)" by `adapt config status`.
