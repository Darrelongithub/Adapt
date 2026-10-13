# Visual redesign pass — report (2026-10-13)

Against the brief at the repository root (`UI file.txt`, *Implement the ADAPT visual redesign
v2: navigation + consent fixed*), on top of `main` = `bace0ba` (PR #7).

Four commits, in the order the brief's Part 5 asks for (with the journey-check repair first
because a gate on `main` could not pass — see §2):

```
f8987a1  Fix unreachable placement assertion in journey_check
9d85971  Redesign the shell: button contract, permanent sidebar, consent, status home
a6764b6  Finish the journey screens, and record the pass in the docs
<this>   Keep the mock banner out of the fixed sidebar's column
```

```
20 files changed, 1185 insertions(+), 662 deletions(-)
 README.md                            |  18 +
 docs/AUDIT.md                        |  48 +-
 docs/DEMO.md                         |   1 +
 docs/UNVERIFIED.md                   |  40 +
 frontend/scripts-ui-probe.mjs        |  93 +-
 frontend/src/app/diagnose/page.tsx   |   7 +-
 frontend/src/app/globals.css         | 848 ++++++----
 frontend/src/app/lesson/page.tsx     |  15 +-
 frontend/src/app/onboarding/page.tsx |  32 +-
 frontend/src/app/page.tsx            | 478 +++++++---
 frontend/src/app/progress/page.tsx   |   4 +-
 frontend/src/app/proposals/page.tsx  |   9 +-
 frontend/src/app/review/page.tsx     |   8 +-
 frontend/src/app/settings/page.tsx   |   9 +-
 frontend/src/app/voice/page.tsx      |   2 +-
 frontend/src/components/chrome.tsx   | 171 +++--
 frontend/src/components/opening.tsx  |   2 +-
 frontend/src/components/question.tsx |  10 +-
 frontend/src/components/ui.tsx       |  40 +-
 scripts/journey_check.py             |  12 +-
```

No route was added, removed or renamed; every read and write the screens already made is
still made. `lib/flow.ts` is untouched, so the event queue's five call sites (question shown,
first interaction, hint opened, changed answer, submitted) still fire from the same places.

---

## 1. Dependency decision (Part 5, step 1)

**Nothing to delete, and the brief's count is wrong.** `frontend/package.json` declares six
dependencies — `next`, `react`, `react-dom` and the three `@fontsource-variable/*` families
that `app/layout.tsx` imports — plus seven devDependencies (`tailwindcss`,
`@tailwindcss/postcss`, `typescript`, three `@types/*`, `jsdom` for the probe). Every one is
imported by something that runs: `node_modules/.bin/next build` uses all three fonts and
React, and `jsdom` is the probe's DOM. There were not fifteen unused packages to find, so no
package was removed.

## 2. What was wrong before the redesign — stated because both of these were "green"

**A gate on `main` could never pass.** `scripts/journey_check.py` asserted `answered >= 10`, a
threshold written for the old fixed twelve-item diagnostic. PR #7 split placement from the
method probe and the default served count became eight, so the run reported
`58/59` with `FAIL answers are accepted and recorded -> 8/8 accepted` — a failure whose own
detail text reads like a pass. The assertion now says what it was always for: every item the
placement served was accepted, and the sequence is long enough to place somebody
(`answered == len(served) and answered >= 5`, matching the "placement serves a sequence"
check above it). The diagnostic was not lengthened; the threshold was not tuned to the
observed value. Fixed in its own commit before any interface work. **59/59 now.**

**The corner tab took two probe assertions with it.** Navigation is the permanent sidebar now
(brief §2.3 cancels the corner-tab/full-screen-index pattern), so `button.index-tab` no longer
exists. The checks that read it were rewritten rather than deleted, and the count is unchanged
on both screens: on `/progress`, "the corner tab names the section it is standing on" and "and
still says what pressing it would do" became "the sidebar names the section it is standing on"
(the one item with `aria-current="page"` is Progress) and "and offers every other section in
plain words" (all seven destinations present by name); on `/review` the same single check
became the same single check. Two assertions in, two assertions out — neither weaker: the old
pair proved a button said where you were, the new pair proves the navigation says where you
are *and* offers everything else.

## 3. What was built

* **Tokens (§2.1).** `--line` moved to the brief's `oklch(72% 0.02 75)`; the other nine
  variables and the three self-hosted font families with their offline fallbacks were already
  the specified palette and are untouched.
* **The button contract (§2.2).** `.btn-primary`, `.btn-secondary` (with `danger`/`good`
  modifiers and the `[aria-pressed]`/`[aria-checked]`/`[aria-selected]` selected state),
  `.btn-ghost`, `.option-card` + `.radio-dot` + `.option-letter`, `.check-row`, and the one
  permitted plain `.link-inline`. `Btn` now emits these instead of per-page Tailwind strings;
  the four existing `kind`s map onto the three variants (`danger` → a secondary in the refusal
  colour) and no call site changed its props. There is no `tone` prop on `Btn` — `tone` lives
  on `Card` and `Tag` — so the sentence in the brief that assumes one was not honoured
  literally. Migrated: MCQ options (`question.tsx`, keeping the hidden `sr-only` radio the
  probe ticks, the roving tabindex, and the render-time answer-state clear), the four decision
  verbs on `/proposals`, both ratings on `/review`, the frame tabs and prediction choices on
  `/lesson`, the `/progress` topic control, the opening skip, the trace "hide", the sign-out control,
  and two "retry"/"checking who you are" controls that were plain underlined text.
* **The permanent sidebar (§2.3).** `.sidebar` (232px, fixed, `aria-label="Main navigation"`),
  `.sidebar-brand`, numbered `.sidebar-item`s with `aria-current="page"`, and `StatusChip`
  moved into `.sidebar-status`. `NAV`'s items, order and routes are unchanged.
  Below 860px it is a drawer (`transform: translateX(-100%)`, `.open` to reveal) behind a
  visible labelled `☰ Menu` button in `.topbar`, with `aria-expanded`/`aria-controls` and
  Escape-to-close returning focus to the button; `.main-content` drops its 232px margin and
  takes a 52px top inset. `SECTION_LABELS` and the whole index overlay (markup + CSS) are gone,
  and so is the old fixed `bottom-left` status corner: the chip reads inline in
  `.sidebar-status` now, and the dead corner CSS was deleted so it cannot pull the chip back
  out of the sidebar on a desktop. The `?mock=1` banner moved inside `.main-content` for the
  same class of reason — full-width at the top of the page it would have run underneath a
  sidebar that starts at y=0.
* **Consent (§2.4).** On `/`, before any account-creating call — the email path *and* the
  guest path, which also writes a `POST /api/v1/students` row. Two separate `<input
  type="checkbox">` inside `.check-row` labels, **neither pre-ticked**; `Agree & continue` is
  `.btn-primary` and `disabled` until the required box is ticked; `Decline` is `.btn-ghost` and
  produces an honest `Notice` naming what will not work, creating nothing; "I already have an
  account" goes straight to the sign-in tab, because signing in creates no account and the
  question is about creation. The plain-language explanation quotes `GET /privacy` — the same
  endpoint `/settings` shows — so the screen and the server cannot drift apart. Consent
  recorded / declined is announced on the shared live region.
  **Which field carries which meaning:** the required box is `consent_analytics`, because that
  column means exactly what the box says (activity recorded and used to adapt what is shown).
  The optional **research** box is *not* written anywhere — in particular it is **not** written
  into `consent_share_with_llm`, whose name means "a model may read a summary"; writing a
  research decision into it would grant a permission the learner never gave. The screen says
  so in as many words.
* **Home (§2.5).** The word-field navigation is deleted (the sidebar is the only navigation
  surface). Signed in, home is a greeting, one primary CTA ("Continue practising" → `/learn`,
  or "Start learning" → `/diagnose` for a learner who has not been placed) and four status
  cards read from `dashboard`, `review/due` and `adaptations/pending`. A learner with nothing
  due is told "Nothing due yet", with a line saying when review appears — the zero is never
  dressed up as data. Signed out, the same page hosts the consent step and then the two-tab
  auth form, keeping every existing read (`health`, `students`, session bootstrap) and every
  affordance the probe depends on.

## 4. Probe checks updated, with reasoning

| Scenario | Before | After | Why |
|---|---|---|---|
| `home` | 11 | 20 | Six new assertions for the consent step (asked before a password field exists; neither box pre-ticked; the primary control closed until the required box is; ticking it opening the way forward; the optional research box separate and still unticked; declining explaining itself with no `POST /students`), three for the signed-in status home the brief asks for (§2.5: exactly one primary action and no word-field menu; that action being the next step in the journey for this learner; the four cards saying "Nothing due yet" / "Not measured yet" / "No answers yet" rather than printing zeros), and one replaced: "switching to email+password reveals email and password fields" became "the create-account tab asks for a name, an email and a password" — the same claim about the same form, keyed to the labels the redesign actually renders. |
| `progress` | 2 on the tab | 2 on the sidebar | The corner tab is cancelled; the replacement asserts the marked item *is* Progress and that all seven destinations are present. |
| `review` | 1 on the tab | 1 on the sidebar | Same rewrite, one screen. |
| `diagnose` | landing-page check read incidental words ("placed") | asserts the CTA | Rewritten to the design's own claim: home offers to continue practising, does not offer placement again, and links to neither `/diagnose`. |
| `lesson` | choice found by `button[class*="font-mono"]` | choice found by `button.option-card[role="radio"]` | A class-based selector was an implementation detail, not a claim; the replacement asserts presence, selectable state (`aria-checked`) and a non-empty label. |

No assertion was weakened or deleted to make a run green. Total: **199 → 208 checks**
(home 11 → 20: the consent step, the signed-in status view, the two keyboard-less states the
door can be left in, and — after the door became username + password (§8) — one check that the
create form asks for a username and a password and renders no email field at all).

## 5. Actual output, pasted

```
$ cd frontend && npm run build && cd .. && bash scripts/probe-fresh.sh all
  ok  the back-off retry finds the session again without being told to
  ok  no raw JSON object is shown to the learner on this screen
  ok  the changes screen does not announce a sign-out it was not told about
  ok  no raw JSON object is shown to the learner on this screen
  ok  being rate limited does not break the app (reads still answer)

208/208 checks passed  (224 API calls)
== all exit=0 (logs: /tmp/tmp.Vq2Z1K39sU)
```

Each scenario also run by name (the narrowest relevant one after each screen, and all of them
again on the final tree):

```
home        20/20 checks passed  (3 API calls)     == home exit=0
onboarding  12/12 checks passed  (5 API calls)     == onboarding exit=0
diagnose    24/24 checks passed  (4 API calls)     == diagnose exit=0
learn       26/26 checks passed  (3 API calls)     == learn exit=0
lesson      30/30 checks passed  (5 API calls)     == lesson exit=0
proposals   38/38 checks passed  (78 API calls)    == proposals exit=0
review       3/3  checks passed  (2 API calls)     == review exit=0
progress    20/20 checks passed  (5 API calls)     == progress exit=0
settings    12/12 checks passed  (4 API calls)     == settings exit=0
voice        8/8  checks passed  (3 API calls)     == voice exit=0
failures    14/14 checks passed  (130 API calls)   == failures exit=0
```

The per-scenario numbers add to 207 and the full run is 208 because one settings assertion
("a changed choice reaches the stored preferences") only fires when a `<select>` is present in
the preferences card, which the state an earlier scenario leaves behind produces in an `all`
run; standalone, that branch is not reached. No assertion differs in strength between the two
modes.

```
$ .venv/bin/python scripts/journey_check.py
  ok   the learner's journey is still there

59/59 journey checks passed
```

```
$ .venv/bin/python scripts/failure_matrix_check.py
25/25 failure-matrix checks passed
```

```
$ cd backend && ../.venv/bin/python -m pytest -q
645 passed, 1 warning in 48.72s
```

```
$ cd frontend && npm run lint
checked 74 backend routes against 54 frontend calls
note: 4 scene kinds: engine, stages and mock agree
contract ok: every path the frontend calls exists, ?mock=1 covers them, the scene kinds agree
across engine/stages/mock, route modules export what Next allows, no host or env misuse in
browser code
```

```
$ cd frontend && npm run test:engine     ->  # pass 15  # fail 0
$ cd frontend && npm run test:lib        ->  # pass 8   # fail 0
$ cd frontend && npm run test:server     ->  # pass 2   # fail 0
```

```
$ node scripts/demo.mjs --decide accept
learner stu_0766a52296124458b7dd · 11 recorded steps
no contract violations found
```

## 6. Design-to-data friction

Three places where the prototype's shape did not fit what the API actually holds, and what was
done instead of inventing a number. **Band**: the snapshot shows a single "current band" (Mid),
but the API has no learner-level band — it has a band per topic — so the card reports how many
topics carry an estimate and names the weakest one with its band and its `n` beside it, which
is the same information without claiming an aggregate the server never computed. **Streak**:
the snapshot's fourth card is a streak; nothing in `dashboard` or `review/due` produces one
that would survive scrutiny (and a streak counter is exactly the kind of thing that turns
absence into blame), so the fourth card is recorded practice volume with the last week's mean
score and its sample, both real fields. **Review position**: `/review` keeps discrete lanes
because the API reports a due list with `due_at` and a projected recall, not a continuous
position on a forgetting curve — the horizon puts each topic at its own due time and says
"now" once, rather than implying a precision the data does not have. The same rule covers the
consent screen: the optional research box has no field, so the box exists, is separable, and
the surrounding text states that nothing is stored for it yet.

## 7. Left unfinished

* **Research consent is not persisted.** There is no column for it (`Student` carries
  `consent_analytics` and `consent_share_with_llm` only), and it was deliberately not smuggled
  into the LLM-sharing field. The screen says so; making it real needs a new field.
* **"No decision" still means "consented" in the database.** `Student.consent_analytics`
  defaults to `True` and `OnboardingBody` defaults both consents to `True`, so a learner who
  is never asked is recorded as having agreed. Closing it needs a tri-state field or a
  migration backfilling un-asked learners as unknown. It is explicitly *not* a default flip:
  consent off makes the server reject the event log, which would silently disable proposals
  and review scheduling for every existing learner. This is the backend dependency the brief
  asked to be flagged rather than worked around, and the UI does not fake it — the door's
  answer is carried to `/onboarding` in the URL, which is the first screen with a session to
  store it on, and `/onboarding` no longer pre-ticks either box.
* **One control is left as it was, on purpose.** The `✕` dismiss inside `/lesson`'s notice
  strip is still an icon button with a hover colour and an `aria-label`, not one of the three
  contract classes: those are sized for a line of text, and a 44px padded ghost button inside a
  two-line notice would visually outweigh the message it dismisses. If the convention is to be
  absolute, it is a one-line change (`btn-ctl btn-ghost`) and the notice grows.
* **`.check-row` is scoped to consent.** `Check` on `/settings`, `/onboarding` and `/voice`
  keeps a plain native checkbox with `accent-color`; the brief defines `.check-row` under
  "CHECKBOX (consent)" and turning six preference toggles into bordered 14px-padded rows
  would restyle a screen the brief did not ask to restyle. The consent screen is the only
  `.check-row` user.
* **Found while running the gates, not fixed here:** `npm run test:server` (the launcher test)
  appends its temp dir to `frontend/tsconfig.json`'s `include` every time it runs, so a passing
  gate leaves the working tree dirty. It is one line of test hygiene in
  `frontend/test-loaner.mjs`/its siblings and outside this brief; the file was reverted after
  each run rather than committed.
* **Nothing about layout or motion has been seen.** See §8.

## 8. Accessibility and responsiveness — what is implemented versus verified

Implemented: the sidebar is a `<nav>` with an accessible name; the current item carries
`aria-current="page"`; the drawer's trigger is a labelled button with `aria-expanded` and
`aria-controls`; the drawer is focus-trapped while open and Escape returns focus to the button;
the option cards and filmstrip tabs keep the existing roving tabindex; every migrated control
is a real `<button>` (or a link where it navigates) with an accessible name; consent state
changes, decisions, ratings, answers and frame changes announce on the shared live region; and
the coarse-pointer rules now key off one list (`.btn-ctl, .option-card, .check-row,
.opening-skip`) so every control keeps a 44px minimum target.

**Not verified:** there is no browser in this environment, and jsdom applies no CSS. Nothing
above is evidence about paint, layout, contrast, hover affordances or tab order on glass —
including the fixed sidebar against a scrolling page, the drawer's off-screen transform, the
`860px` breakpoint itself, and whether the 72% `--line` still reads as a boundary at 100% zoom.
`docs/UNVERIFIED.md` carries this pass's list and a five-minute check script. The claim this
report makes is only the one the probe can make: state, rendering, fetch traffic, cookies,
CSRF, disabled logic and screen-reader-visible attributes.

## 9. Part 6 checklist

| Item | Result |
|---|---|
| Every interactive element passes the "clickable without hovering" test | **Pass as far as it can be checked here.** Every control that acts wears a contract class with a border or fill (asserted by class in the probe); two "retry" controls and four decision verbs that were plain text are now buttons. Whether a given fill reads as clickable is a paint question and is listed as unverified. |
| Sidebar is present and shows the current page on this screen | **Pass** — asserted per screen (`aria-current="page"` names the section) plus one assertion that all seven destinations are present. |
| No fabricated numbers introduced | **Pass** — home's cards read `dashboard`/`review/due`/`adaptations/pending` and say "nothing due yet"/"not measured yet" where there is no value; research consent has no number to fabricate and is stated as unpersisted. |
| No raw JSON visible | **Pass** — the `closePage` tripwire runs on `/learn /lesson /proposals /settings /voice /progress` and passed 208/208; the new home panel renders lists, not payloads. |
| Telemetry call sites unchanged | **Pass** — `lib/flow.ts` is untouched; the same five events fire from the same call sites, and the probe's event-log assertions still pass. |
| Keyboard operable, visible focus, accessible names | **Partly verifiable here.** Roles, names, states, `aria-current`, `aria-expanded`, roving tabindex and the focus trap are implemented and asserted; focus *order* and visible focus rings are not verifiable without a browser and are listed as unverified. |
| Relevant probe scenario passes at the same or higher count | **Pass** — 199 → 208 in total; per screen: home 11 → 20, others equal. |
| `npm run lint` passes | **Pass** — contract ok, 74 routes / 54 calls, `tsc` clean. |

## 10. Follow-up pass, same day — asked for after the report above was written

Four changes the user asked for after the redesign landed. The numbers in §4, §5 and §9 have
been updated in place to the ones here; this section says what changed and what it cost.

### 10.1 The door is username + password (no email)

* **Schema.** `students.username` (AES-GCM, `EncryptedText`) plus `username_index` (blind index,
  unique), migration `0006_username_accounts.py` — existence-aware in both directions, so a
  database that already has the columns is not re-migrated and a `create_all` database can be
  stamped and upgraded (both verified on copies of the dev database, one of them 21 students).
* **API.** `POST /api/v1/students` takes `{username, password, age_band}`; **email is still
  accepted** so `journey_check.py`, `demo.mjs --as` and every existing fixture keep working. A
  taken name is `409 {"error": "username_taken"}` with no session in the body; a refused name is
  a 422 that says in words why; an unknown name and a wrong password return the *same* 401
  (`wrong username or password`) so the endpoint cannot be used to enumerate accounts.
* **Case.** Stored lowercased (and blind-indexed on the lowercase form), displayed as typed:
  signing in as `Rowan.7` for an account made as `rowan.7` greets you as `Rowan.7`. Nothing is
  Title-cased for you.
* **The screen.** Consent → one form: username, password, age band. The sign-in hint reads
  "The one you chose when you made the account." and nothing on the page mentions email. The
  single field is still *tolerant*: a value containing `@` is also sent as `email`, so an account
  made before this build can sign in by typing its address — the code says so; the copy does not
  promise it, because the user asked for email to be dropped "for now".
* **Left unfinished, stated:** an existing **email-only account is not discoverable** through the
  UI (nothing tells the learner that typing the address works), and research consent is still
  unpersisted — unchanged from §7.

### 10.2 The opening is slower and the mark is blue

`.opening-mark` (`A·D·A·P·T`) is accent blue and monospaced at the top centre; `.opening-word`
reveals over **1.15 s** with a 0.14 s stagger, 10 px blur and 22 px rise; the overlay holds
**4200 ms** (900 ms under `prefers-reduced-motion`) before a 0.5 s exit. The hold is derived, not
guessed: `stagger × (words − 1) + 1.15 s` for the longest phrase is 1.99 s, and the timer leaves
room for the exit without cutting the last word off. The sidebar wordmark (`.sidebar-brand`) is
the same accent blue, so "the corner Adapt" and the sidebar agree. Values live in the component's
own comments; motion itself remains unverified here (no browser — §8).

### 10.3 The copy sweep (hyphens and punctuation)

Scope: **everything a learner reads**, including strings the backend serves — not only the JSX.
Measured, not eyeballed:

* `frontend/scripts-copy-dump.mjs` renders all 11 routes in jsdom and prints the text. Current
  dump: **zero** prose hyphens. The only hyphenated tokens left are `faster-whisper`,
  `espeak-ng` and `apt-get`, inside the voice screen's install block, where they are command
  names a learner could paste into a terminal.
* A live-API walk (13 endpoints plus the loop: answer, hint, worked example, teach back, reveal,
  analyse, train, validate) prints every hyphenated token in every JSON string. What remains is
  identifiers and notation only: `AES-256-CBC`, `HMAC-SHA512`, `faster-whisper`,
  `gemini-2.5-flash`, the keyfile path, `10^-3` / `x^-2`, ISO timestamps, and the historical
  proper nouns `Austria-Hungary` / `Austro-Hungarian` (the country's name; un-hyphenating it
  would be a factual change, not a style one).
* Typewriter dashes went the other way: ` -- ` became a real em dash (—) in 33 served strings,
  because that is the honest glyph for the parenthetical the sentence is doing.
* Punctuation pass: sentences that began mid-thought ("…in the backend; nothing here is a video")
  became full sentences; the 429 line and the backend-down line read as sentences now.
* Carve-outs kept deliberately: `?mock=1` route matches, the `learning-style` payload key,
  `worked-example` (a route), env var names, CSS class names. Renaming those would be a
  behaviour change wearing a copy change's clothes.

### 10.4 "What this screen refuses to ask" was removed, and the probe got stricter

The card on `/onboarding` is gone, at the user's instruction. The probe check that guarded the
same claim used to test only the form's fields ("no *input* asks for a learning style") because
the page itself discussed styles in that paragraph; with the paragraph removed the check can be
made against **the whole rendered screen** — a strictly stronger assertion, still 12/12.

### 10.5 A database from an earlier build no longer looks like a broken key

Found by running the live server against a database created before `0006`. The startup key check
sampled `students.username` on a file that does not have the column; SQLite answers an unknown
double-quoted identifier with **the column's own name as a string literal**, that value is not a
ciphertext, and the operator was told:

```
startup refused: the key this process loaded cannot open 4 of 33 encrypted value(s) it sampled.
Columns affected: students. Cipher said: TypeError.
```

The key was fine; the database was un-migrated. The check now reads the file's live schema first,
reports missing columns separately, and the app refuses with the remedy instead:

```
startup refused: this database was made by an earlier build and is missing 1 column(s) this
build declares: students.username. Migrate it rather than deleting it:
`alembic -c alembic.ini stamp 0002 && alembic -c alembic.ini upgrade head` (see README, Migrations).
```

`test_startup_key_check_ignores_a_model_column_the_file_has_not_got_yet` holds the line: it
builds a database, drops the column the file "does not have", and asserts the report says
`missing` rather than `unreadable`. Deleting the guard fails the test (checked by mutation, not
by inspection). This is why the suite is 645 and not 644.

### 10.6 Gates re-run after all of the above

```
$ cd frontend && npx tsc --noEmit && npm run lint && npm run build
contract ok: every path the frontend calls exists, ?mock=1 covers them, the scene kinds agree
across engine/stages/mock, route modules export what Next allows, no host or env misuse in browser code
 ✓ Compiled successfully

$ bash scripts/probe-fresh.sh all
208/208 checks passed  (224 API calls)

$ cd backend && ../.venv/bin/python -m pytest -q
645 passed, 1 warning in 48.72s

$ .venv/bin/python scripts/journey_check.py            # 59/59 journey checks passed
$ .venv/bin/python scripts/failure_matrix_check.py     # 25/25 failure-matrix checks passed
```
