# Demo — round 2 in three minutes

The subject is **a topic the app has never seen, taught end to end**: typed in, turned into a
checked pack, taught, gotten wrong, re-taught a different way. Everything below runs on this
machine with no model key and no network — that is a feature of the demo, and the script says
so out loud rather than letting the room assume a live model.

Three ways to run it, in descending order of truth:

1. **The fake-model path** (what the browser tests run): the real app, real routes, real
   validators, real database — only the provider client is a scripted fake.
2. **The simple path**: no key configured, the plain app. Typed topics still work; every
   lesson is the deterministic one and is labelled "Simple lesson".
3. **The mock path**: no backend at all, `?mock=1` fixtures, for looking at screens.

## Before you stand up (fake-model path)

```bash
# terminal 1 — the API with the scripted fake model (fresh data dir = a clean journey)
cd ~/Adapt
ADAPT_DATA_DIR=/tmp/adapt-demo backend/.venv/bin/python scripts/serve_with_fake_model.py

# terminal 2 — the web app (build once, then serve on :3000, proxying to the API)
cd ~/Adapt/frontend
npm run build && node server.mjs
```

For the simple path, skip the launcher script and run the real API instead
(`uvicorn adapt.api.app:app --port 8000` from `backend/`). For the mock path:
`ADAPT_API=offline npm run dev:web`, then open `http://127.0.0.1:3000/?mock=1`.

## Say / do

| Say | Do |
| --- | --- |
| "Nothing starts until consent is answered — two separate consents, neither pre-ticked." | open `/`, tick the required box, watch the button unlock; continue as guest |
| "One path, eight stages, held on the server. Skip anything and it is recorded as *not measured*, not as failure." | `/journey`; skip preferences and games; point at the stage list remembering it |
| "Type a topic nobody prepared." | journey → first lesson; type `kite building`; **Write my lesson** |
| "In the background the topic became a checked pack: concepts, facts, and questions whose answer keys were verified before anyone could be scored on them. That takes a few seconds — the screen says what it is doing." | wait for the concept list; pick the first concept |
| "This lesson was written for this learner — the label says which kind it is, and it upgrades in place when the checked version arrives." | point at **Written for you** (fake path) or **Simple lesson** (simple path) |
| "Now get the check wrong." | pick a wrong option, then **I picked this and it was wrong — explain why** |
| "The explanation starts from the answer actually chosen — why it feels right, and where it breaks — not from the right answer." | read the block under **Why that answer feels right**; it quotes the option you picked |
| "Still not landing? It re-teaches a different way. Every time. The screen remembers what it already tried." | **I still don't get it — try another way**; press it again; each block arrives under **Another way: …** naming the new approach (plainer words, then a smaller step) |
| "Same idea, two learners, visibly different lessons — same checked content underneath." | open `/contrast` in a second tab: one concept under two demo profiles |
| "And none of this needed a key: with nothing configured, every step you just saw still answers — plainer, and it says so." | (simple path) repeat the first-lesson flow and point at **Simple lesson** |

## Say this once, and stop

No learner-facing text names the machinery — no models, providers, keys or endpoints, on any
screen, signed out or in (there is an automated crawl that fails the build's checks if that
changes). The answer key for a lesson's check questions never leaves the server; that is why
the lesson asks the learner what happened and responds to the report. Model settings exist
only on the console: `adapt config set-key`, `adapt config test`.

## Do not demo

* **The live model check.** It has never been run — there is no key on this build. If asked:
  "the harness is written and tested against a fake; the owner runs
  `python scripts/live_model_check.py` with a key configured, and the report it writes is
  the result. Until then, claiming quality numbers would be inventing them."
* **Real-model lesson quality**, for the same reason. Everything shown here is the
  deterministic path plus validators, which is exactly what a learner gets with no key.
* Voice, Numbas, encryption internals — real, and a different pitch (`docs/DEMO.md`).

## If it breaks live

* Lesson stuck on "Simple lesson" on the fake path: the checked lesson upgrades in place;
  give it a few seconds. On the simple path, "Simple lesson" *is* the correct end state.
* Topic preparation fails: the app falls back to a simple pack and says so. That is the
  designed behaviour, not a crash — name it and carry on.
* Anything else: `?mock=1` (mock path above) keeps every screen openable with no backend.
