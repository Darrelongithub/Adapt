# Handoff A → Phase D / Phase G

Part A may not edit the onboarding page (Phase D owns it) or other phases'
backend files. The items below are written to be applied verbatim.

## 1. Onboarding copy — `frontend/src/app/onboarding/page.tsx` (Phase D)

These are the last visible banned phrases the crawl
(`frontend/tests-e2e/banned_phrases.mjs`) will find. Until D applies them,
run the crawl with `BANNED_PHRASES_KNOWN=/onboarding`.

**(a) Consent checkbox (~line 194).**
Replace label `Let a model read a summary when it would help` with:

> Let a summary be shared when it would help

Replace the hint
`…With no API key configured this changes nothing, because nothing is sent anyway.`
with:

> The summary is topic mastery, hypotheses and approved changes — never your
> name or contact details. If sharing is not available, nothing is sent and
> your lessons are put together here instead.

**(b) "Allowed preference keys" panel (~line 230).**
Remove the whole panel (`title="Allowed preference keys"`,
`right={<span>from the API</span>}`, the raw key tags, and the
`ignored_keys` sentence). If a replacement panel is wanted, use:

> title: `What you can set here`
> body: `Everything on this screen is a choice you can change later in
> Settings. Anything this screen does not ask about is not stored from here.`

**(c) "Saved" card rejected-keys line (~line 220).**
`not stored: {k} — {reasons[k]}` prints raw preference key names. Replace with
a count only:

> Some things that were sent were not stored, because this screen does not
> ask about them.

## 2. Backend strings other phases own, which can surface developer wording

Part A's frontend no longer renders these raw, but the payloads still carry
them; the owning phase may want to clean them at the source.

- `backend/src/adapt/api/routes/system.py` `voice_capabilities()` returns
  `transport: "websocket /ws/voice (…)"` and the STT/TTS `capabilities()`
  `fallback` strings name the "Web Speech API". The voice page now shows
  plain wording instead; owners: voice (E) / G.
- Lesson answers render `answer.note` from the lesson-writing path (C). If
  that note ever names a model/provider, it will reach the learner verbatim;
  the lesson page no longer renders `answer.model` or token counts.
- Voice error frames render their `detail` on the voice page; keep those
  strings learner-safe at the source (E).

## 3. Notes for G

- The jsdom UI probe (`frontend/scripts-ui-probe.mjs`) contrast check
  "every block carries why it is there (n included)" still passes: measured
  items keep their `(n=…)` counts; only stated items lost the meaningless
  `(n=0)`.
- `frontend/src/lib/mock.ts` fixture payloads contain demo-only wording
  ("fixtures: …"). They only render under `?mock=1`; the banned-phrase crawl
  does not use mock mode. Clean them if mock mode is ever learner-visible.
- Probe stalls observed while verifying Part A (not Part A code): the full
  probe timed out in the `baseline` scenario ("waiting for the study
  screen") and a targeted run stalled in the `lesson` scene player
  ("waiting for the player to replace the list"). All Part A screens
  (home, settings, voice, contrast) passed before those points.
