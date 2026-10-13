# The demo

Three minutes, spoken in plain language. The subject is **the loop** and nothing else: what the
app noticed, what it proposed, what the learner decided, and whether it worked. Everything in
`docs/` that is not in this file is deliberately not in this file.

Read the first column out loud and press the thing in the second when you get to it.

| Say | Do |
| --- | --- |
| "Nothing is created until you say so: two separate consents, neither pre-ticked, and the button is closed until the required one is answered." | open `/`, tick the required box in front of the guests, and let them watch the primary button unlock |
| "It doesn't ask you what kind of learner you are. It watches what happens and says so." | open `/onboarding`, point at the absence of a learning-styles question |
| "Eight unaided questions, no hints — this part is placement, not a test." | `/diagnose`, answer two |
| "Every question gets taught one way first, and which way is decided per learner, per attempt. Same question, different teaching — that's the thing being measured." | `/learn`, point at the panel above the question |
| "Short steps, one at a time, with a check between them, because a long paragraph is where attention goes." | type a short answer in the step check; the next step unlocks |
| "Now get one wrong." | answer wrong |
| "It doesn't blame the teaching yet. It checks whether the *prerequisite* is missing, and whether you're tired — and if either is true, the method is off the hook." | point at the reteach sentence on the receipt |
| "Then it teaches the same idea a different way and asks a matched question — same format, so the difference is the teaching." | show the next question |
| "Nothing persistent changes without you. Here is what it noticed, what it thinks, and what else could explain it." | `/proposals` |
| "And when you say yes, this is the number it moves — actual against what it expected from you, not raw marks." | `/progress`, the "Which teaching is working" card |
| "`n` is always on screen, and under eight observations it says early signal. Under five on either side of a change it says not enough evidence." | read the card |
| "And it isn't a maths app with the subject baked in. Here's a history concept: three ways of teaching it, all built from its own notes, and its answer keys are checked by whether they *occur in the notes* — arithmetic checking doesn't work for prose, so it doesn't pretend to." | `node scripts/demo.mjs`, step 7 ("Any subject, not only numbers"); in the UI, `/learn` whenever the rotation serves *Why the First World War began* |

## Say this once, and stop

Keystrokes, key timing, per-character edits, screenshots, other tabs: never recorded. A fixed
allowlist of event types, 48 forbidden payload keys, and an unlisted event type is *rejected* by
the server. Answers stay on the machine unless a model call is opted in.

That is the whole privacy story for a room. `/settings` shows the same thing in more words for
anybody who asks afterwards.

## Do not demo

* **Voice.** Real, works with a microphone, needs https — and it is a second subject.
* **Numbas import/export.** Real, and interesting to two people in the world.
* **Encryption internals** (SQLCipher, keyfiles, the HKDF layout). One sentence if asked:
  "field-level encryption with a key outside the database" — then move on.
* **The lesson engine** (`/lesson`). Good, and a different pitch. It gets one line if there is
  time: "there's also a scene player where you predict the next state of an algorithm".
* **The provider/resource settings.** Plumbing.

## Before you stand up

The console is the only place credentials and model/provider configuration belong. Never put a
key after a command name or in a source file; command-line arguments are kept in shell history.
Without a configured key, the deterministic tutor still runs in honest simple mode.

```bash
# 1. Optional live-model configuration. Input is hidden; status shows only configured yes/no
#    and the last four characters. A live test makes one minimal request.
adapt config set-key --service gemini
adapt config status
adapt config test

# 2. Build clearly labelled, deterministic synthetic demo histories (not learner evidence).
#    The helper creates two reserved .invalid accounts and displays a one-time password.
.venv/bin/python scripts/demo_seed.py

# 3. Rehearse the exact path against either seeded synthetic account. Keep the generated
#    password in an environment variable, not in the command arguments or source.
ADAPT_DEMO_PASSWORD='<password printed by demo_seed.py>' node scripts/demo.mjs --as demo-example@adapt.invalid

# 4. The interface, on the same database
cd frontend && npm run dev          # http://localhost:3000
```

The synthetic profiles' display names begin `SYNTHETIC DEMO`; every seeded attempt is flagged
as pilot data, and the existing evidence card labels those rows *"pilot data — not evidence of
efficacy"*. These are controlled examples, not collected responses, a study, or learner
measurements. Do not remove the label or present the contrast as a result. Use
`.venv/bin/python scripts/demo_seed.py --remove` to remove only the synthetic sessions/attempts;
if anyone added non-synthetic history to a demo account, cleanup deliberately keeps that account and reseeding refuses to overwrite it.

For a real rehearsal learner, `adapt pilot --student-id <id> --seed` remains available. The effect
check needs **five scored attempts on each side** of a change before it will say anything, and the
method comparison calls anything under **eight observations** an early signal. That is the point
of the gate — below it, a difference is noise. A live demo cannot manufacture five answers without
lowering the bar, and lowering the bar is how a demo turns into a claim. Seeded rows carry
`pilot: true` and the sentence *"pilot data — not evidence of efficacy"*, in the API payload and on
the screen.

The `adapt demo-seed` CLI alias and the explicit synthetic label in the method comparison panel
need shared-CLI/UI integration by the owning agent; until then, use the standalone script above,
keep the `SYNTHETIC DEMO` display name visible, and do not claim the alias exists.

## If something goes wrong

| Symptom | What it is | What to say |
| --- | --- | --- |
| "not enough evidence" on the effect line | fewer than five answers on one side | "That's the gate, not the app being coy" |
| `early signal` on every method | fewer than eight observations | "Right — it refuses to rank them yet" |
| A 429 | the per-IP rate limiter (a room is one IP) | `adapt host` raises it ×10 and prints the numbers |
| No proposal after placement | one observation per topic is not a pattern | "Nothing to propose yet — inventing one would be the failure mode" |
| The mic does nothing | `http://` is not a secure context for a microphone | "Voice needs the https link" |

## Rehearsal checks

```bash
cd backend && ../.venv/bin/python -m pytest -q     # local regression suite; no live model call
scripts/probe-fresh.sh all                        # the whole UI on fresh servers
node scripts/demo.mjs --as <email>                # the loop, over HTTP, on the demo learner
                                                  # (the app's door takes the username; the
                                                  # address a script made is accepted too)
```

Run all three on the machine and the database you will demo from, not a fresh one.
