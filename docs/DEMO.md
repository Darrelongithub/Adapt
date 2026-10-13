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

```bash
# 1. the pilot: real rows, written before the demo, labelled as pilot data
adapt pilot --student-id <the learner you will show> --seed

# 2. rehearse the exact path on that same learner, so the pilot rows are the ones on screen
node scripts/demo.mjs --as <that learner's email>

# 3. the interface, on the same database
cd frontend && npm run dev          # http://localhost:3000
```

Why the pilot: the effect check needs **five scored attempts on each side** of a change before it
will say anything, and the method comparison calls anything under **eight observations** an early
signal. That is the point of the gate — below it, a difference is noise. A live demo cannot
manufacture five answers without lowering the bar, and lowering the bar is how a demo turns into
a claim. So the rows exist before the demo and are flagged: every number derived from them carries
`pilot: true` and the sentence *"pilot data — not evidence of efficacy"*, in the API payload and on
the screen.

If the pilot was not run, **say so on stage** rather than seeding it live: "this is one session, so
the app will say early signal — which is what it should say". That is a stronger demo than a
number that pretends to be a result.

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
cd backend && ../.venv/bin/python -m pytest -q     # 640 tests, no network, no model call
scripts/probe-fresh.sh all                        # the whole UI on fresh servers
node scripts/demo.mjs --as <email>                # the loop, over HTTP, on the demo learner
                                                  # (the app's door takes the username; the
                                                  # address a script made is accepted too)
```

Run all three on the machine and the database you will demo from, not a fresh one.
