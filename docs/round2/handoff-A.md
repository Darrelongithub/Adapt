# Handoff from Phase A

Phase D must apply the onboarding replacements below. Phase G applies anything still open at integration, including the subject-round sentences owned by Phase F.

## Onboarding copy — Phase D

File: `frontend/src/app/onboarding/page.tsx`. Do not change the page flow. Replace only the copy below.

### 1. Consent checkbox

Replace the label:

```
Let a model read a summary when it would help
```

with:

```
Allow a written-for-you summary when it would help
```

Replace the hint:

```
The summary is topic mastery, hypotheses and approved changes — never your name or contact details. With no API key configured this changes nothing, because nothing is sent anyway.
```

with:

```
The summary is topic mastery, hypotheses and approved changes — never your name or contact details. Off means every reply is written from your own material. A summary is shared only when this is on and a written-for-you reply is available.
```

Settings already uses that label and a matching hint. Keep the two screens saying the same thing.

### 2. Remove the preference-key panel

Delete the whole card whose title is `Allowed preference keys` and whose badge is `from the API`. That includes the list of `allowed_keys` tags and this paragraph:

```
Anything else the client sends comes back in ignored_keys with a reason, rather than being stored as a string nobody can interpret.
```

Do not replace that card with another list of key names.

In the "Saved" card, remove the block that prints `not stored:` followed by raw key tags. If something was not stored, one sentence is enough and must not name a key:

```
Some of what was sent was not stored. Only the questions on this page are kept.
```

The word `ignored_keys` must not appear in rendered text, including inside a `<code>` tag.

### 3. After that copy lands

Delete the three strings in `ONBOARDING_DEFERRED` in `frontend/src/lib/bannedPhrases.mjs`. Those strings exist only so the crawl does not fail on a file Phase A was not allowed to edit. Once the page no longer contains them, the exemption must go, or a regression would pass.

## Subject-round sentences — Phase F

File: `backend/src/adapt/subjects/leanings.py`. Phase A did not edit it. These sentences are returned to the learner and contain the banned substring `model`:

- `no model was asked (none is configured here), so this is the generic profile, not a claim about your topic`
- `no model is configured on this machine, so nothing was asked`
- `the model's answer was not usable`
- `A model rated how much {title} leans on each skill, and the ratings were checked against the {n} skills in the {family} list before anything was chosen.`

Suggested replacements, same meaning, no service name:

- `Nothing extra was asked, so this is the generic profile, not a claim about your topic.`
- `A written-for-you rating is not available on this machine, so nothing was asked.`
- `The written-for-you rating was not usable.`
- `A written-for-you rating scored how much {title} leans on each skill, and the ratings were checked against the {n} skills in the {family} list before anything was chosen.`

Also check whether `source="model"` or `model_note` is rendered. If the page prints those keys or values, a signed-in crawl of `/subject` will fail on `model`.

## Not a required seam

`frontend/src/lib/mock.ts` still contains `no model is configured` and `the model's own prediction`. Those strings are fixtures for `?mock=1`. The required crawl does not use mock mode. Leave them unless a mock screen is shown to a learner.
