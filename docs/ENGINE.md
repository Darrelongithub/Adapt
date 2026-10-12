# The interaction engine

What the learning engine is, where each of its rules lives, and what this project is **not**
allowed to claim about it. The product framing stays in `README.md`, the repair history in
`docs/AUDIT.md`. This file is the single description of the mechanism, because the mechanism is
spread over four directories and a migration, and every one of them can be edited without
noticing the others.

```
  sims/<kind>.py                learning/scenespec.py              api + services
  ─ run(params) ──frames──▶ validate() · spec_id() ──▶ LessonScene ──open──▶ LessonRun
    each frame carries its      (content hash, limits,        (row id, spec_id,     │
    own prompt, or None          plain-text only)              origin, derived_from)│
        │                                                                    ▼
        └────────── grade_prediction(kind, params, frame_index, choice) ◀── /predict
                                                     │
                                                     ▼
                          frontend/src/lib/engine/player.ts (the gate) → stages → review
```

## 1. A trace is data; the prompt sits inside it

`backend/src/adapt/learning/sims/` holds one module per scene kind — `binary_search`, `sorting`,
`logic_gates`, `cpu_cycle` — and every module exports the same shape: `KIND`, `TITLE`, `PROMPTS`,
`MAX_FRAMES`, `normalize(params)`, `run(params)`, `expected(params, frame_index)`,
`grade(params, frame_index, choice)`, `truth(params)`, and an optional
`explain(params, frame_index, question)`. `KINDS` is not a list someone maintains: it is
`tuple(sorted(_MODULES))`, built from the modules that exist, and `scenespec.validate` refuses
anything else with `kind must be one of <the set>`.

`run()` returns frames — plain dicts of `{i, kind, title, narration, state, highlights, cause,
prompt}` — and **the prompt is part of the frame**, written where the next state has become
decidable from what the learner can already see:

```python
# sims/binary_search.py, the last frame
# no prompt here: this frame has just stated the answer, so asking it again would be
"prompt": None,
```

That placement is the whole design decision. A question is not bolted on by a scheduler, an
LLM, or a "roughly the middle of the animation" heuristic, so it cannot drift out of step with
the thing being animated, and `expected()` can be handed the same `params` the browser was given
without the browser being able to see the future. Nothing in the frontend computes an answer
either: `player.ts` says so in its own header — `sims/*` owns what the machine does next, and the
grade is the backend's to give.

Real sizes, because "the trace is the lesson" is only credible with numbers on the page:
`binary_search` 8 frames of which 6 carry a prompt, `logic_gates` 10/7, `cpu_cycle` 14/7,
`sorting` 56/7 — `sorting` being long is deliberate, and `MAX_FRAMES = 200` is where the engine
stops rather than truncating silently. A frame is not HTML, not a duration, not a component; the
animation is one reader of it (`stages.tsx`) and narration is another.

## 2. A scene is a validated spec with a content address

`learning/scenespec.py` is the only way a spec becomes a row. `validate(raw)` returns
`Result(errors, warnings, spec)`; the limits are its own constants (`TITLE_MAX`, `INTENT_MAX`,
`FRAMING_MAX`), every text field goes through `_plain_text`, which normalises whitespace and
rejects a structure where a sentence belongs — so a payload cannot reach a learner as
`{"text": "..."}`, and a `None` becomes the empty string rather than the word "None" in a caption.

`spec_id(kind, params, title)` is `scn_` + `sha256` of `json.dumps(..., sort_keys=True)` over
exactly those three, truncated to 20 hex characters. The title is in the fingerprint on purpose
(its docstring is the authority): re-titling is a *new authored artifact*, and letting it overwrite
a row would strand every `LessonRun` that refers to the old id. `sort_keys` is why two authors
writing the same lesson get the same id — which is also what made the authorship bug in §4
possible, and what its fix had to preserve.

The three origins a scene can have are `builtin`, `ai2` and `learner`. `builtin_specs()` seeds the
shipped scenes at startup (`ensure_scenes()`, idempotent, and it refuses to store a spec the
validator rejects); `ai2` is a scene proposed by the curriculum generator, which must survive
validation before it can be played at all; `learner` is a copy of a scene with different `params`,
and it is the only origin whose rows are private to their author.

There is no "practice scene earns mastery" path, and that is a decision, not an omission:
`review()` and `evidence_summary()` report predictions, and the summary says in its own words
*"It is not a mastery number and is not fed into one."* `BKT` is still driven only by item attempts.
Inventing a second mastery signal off an animation would be the audit's F5 (a number that measures
the wrong thing) in new clothes.

## 3. The gate, in one reducer with no DOM in it

`frontend/src/lib/engine/player.ts` is the head of the player: `PlayerState` is
`{frames, index, playing, speed, answers, seen, gate}` and the only way to change it is `reducer`
with one of `play · pause · toggle · step · back · jump · answer · speed · gate · restart`. The
rules worth knowing before editing anything:

- **`step` is the only gated action.** `canAdvance(state)` is `!(state.gate && pendingPrompt(state))`
  and `pendingPrompt` asks `promptOn(frames[index])` whether that frame carries a prompt the learner
  has not answered. So an unanswered prediction blocks *advancing*, and never blocks leaving:
  `back`, `jump` and the slider are ungated — "seeking is allowed past a prediction, that is the
  point of a slider — but the run's own counters will say which moments were skipped", and they do
  (`unanswered()` feeds the review's "you moved past these").
- **The gate is a learner-controlled setting, not a wall.** `gate` defaults on and
  `{type: "gate"}` turns it off; the reason it is a toggle in state rather than a constant is on the
  line above the function: *a gate the learner can be stuck by is worse than a gate*.
- **`play` at the last frame means start again**, so no button can do nothing.
- **Timing is derived from the narration, then clamped.** `frameMs(frame, speed)` estimates read-
  aloud time at `WORDS_PER_SECOND = 2.6` and clamps into `[MIN_FRAME_MS 2600, MAX_FRAME_MS 11000]`
  before dividing by `clampSpeed(speed)` ∈ `[0.5, 2]`: a floor so a caption is not a flash, a
  ceiling so a 56-frame trace is not a frozen screen, and the ceiling is what `speed` is for.
- **`autoplayAllowed(prefersReducedMotion)` is false under reduce-motion**, which is why autoplay is
  a prop consumed by the stage and not a `useEffect` that always starts a clock.

`npm run test:engine` runs `player.test.mjs` (15 tests, no browser). Two of them read *source*
rather than behaviour — over `player.ts`, `types.ts`, `scenes/index.tsx` and `scenes/stages.tsx` —
because those invariants are invisible when they hold: nothing may render a scene as HTML (no
`dangerouslySetInnerHTML`, `innerHTML`, `document.write`, `eval(`, `new Function(`, `<script`:
scene text is a text node, always), and browser code may not name a host or read `process.env`. A
scene's narration is written by whoever authored the spec, so "we only ever put plain text in" is a
claim about four files, and it is checked as one. `scripts-ui-probe.mjs`'s `lesson` scenario then
walks the real frame on screen, and `scripts/check-contract.mjs` fails if the sim modules, the stage
registry and the `?mock=1` fixtures disagree about which kinds exist — the assertion
`sims/__init__.py`'s comment had been promising for two phases before anyone wrote it.

## 4. Position, provenance and privacy are rows

`LessonRun.frame_index` is written by the browser (`POST /lessons/run/{id}/progress`) and read
back on `GET /api/v1/lessons`; that is the resume path and the only place a position lives. The
browser keeps no copy: no `sessionStorage`, no URL parameter, and the "you stopped at frame *n* of
*m*" line on a card is rendered from the row. The probe asserts the two agree — the card's *n* must
equal `runs[0].frame_index + 1` from the API — because otherwise the sentence on screen is
decoration.

Four decisions hold that up:

1. **`store_spec` returns the row, and `open_run` uses its id.** `scene_id` is `lesson_scenes.id`
   (the row), `spec_id` is the content hash — two different things, deliberately: the row id is
   what every telemetry `scene_*` event carries, so an event stays joinable to a scene even after a
   learner's copy of the same content hash exists. A `spec_id` alone could not tell the two apart.
2. **Two learners may make the same copy.** `spec_id` used to be unique per database, so the second
   learner to change `n` from 12 to 13 was silently handed the first learner's row: they resumed
   inside each other's frames, and a learner who reset their device could not re-derive their own
   scene. Migration `0003` replaces that one constraint with the two that say what was meant —
   `uq_scene_spec_shared` (partial unique, `author_student_id IS NULL`) and `uq_scene_spec_author`
   (`spec_id`, `author_student_id`) — plus `ix_scene_kind_origin` for the list query. `origin` is
   *not* in the author index: a learner's scene is still the same scene whether or not it has been
   rendered. Guarded by
   `test_two_learners_who_make_the_same_copy_each_have_a_copy` (two clients, two row ids for one
   content hash, one derived card each) and by the probe.
3. **A copy keeps its parent.** A derived row holds the parent's title, framing and `derived_from`,
   and the parent is never rewritten — which is what makes "start over from the original numbers" a
   true sentence rather than a re-authoring.
4. **An answer belongs to a scene, not to a frame number.** `predict()` grades through
   `sims.grade_prediction(kind, params, frame_index, choice)` using *that row's* spec, logs
   `{frame, prompt, said, expected, correct, at}` into `run.payload["predictions"]` (an
   `EncryptedJSON` column: the learner's own words stay private) capped at `MAX_PREDICTION_LOG`, and
   increments `predictions_total`/`predictions_correct`. Changing the parameters produced a new row
   with a new id, so an old answer can never be scored against a new copy of the scene.

## 5. Encryption is a policy, and migrations run on it

`db/engine.py` resolves `db_encryption` (`Literal["auto", "sqlcipher", "field-only", "off"]`,
default `auto`) once, at startup: `auto` asks for file-level encryption and needs both a working
`sqlcipher3`/`pysqlcipher3` driver and a key from exactly one of `ADAPT_DB_KEY` / `ADAPT_KEYFILE`.
When either is missing it falls back to field-only and **logs the reason**, because a silent
downgrade is indistinguishable from a promise kept:

```
sqlite file at .../var/adapt.db not encrypted at the file level; falling back to
field-only (column encryption only). driver=sqlite; reason: no key configured
```

`sqlcipher3-binary` is a declared dependency for x86_64/aarch64, so an environment built by the
README's own `pip install -e "./backend[dev]"` gets the file-level mode: a database written here
begins with random bytes, `GET /api/v1/status` reports `driver: sqlcipher` +
`file_encrypted: true`, and startup prints the key check
(`key check ok (22 encrypted value(s) across 6 column(s) read)`) — which is also how you notice a
newly encrypted column that is silently *not* being encrypted, because the count goes up or it does not.
Which of the two modes a machine is in is a fact about the install, not about this code, so neither
is claimed by default: the log line above is the answer, and `docs/UNVERIFIED.md` records what was
measured where.

`migrations/env.py` therefore hands Alembic **the application's own engine** (`connectable =
_engine`) instead of rebuilding one from `alembic.ini`. `engine_from_config(url=str(_engine.url))`
looks equivalent and is not: the URL carries neither `module=sqlcipher3` nor the `connect` listener
that issues `PRAGMA key` and the cipher pragmas. Against an existing encrypted database that fails
loudly (`file is not a database`); against a fresh `ADAPT_DATA_DIR` it writes a **plaintext**
database that the application can never open, and nothing on disk announces the change.
`tests/test_migrations.py` keeps both directions honest, and its subprocesses pin
`ADAPT_DB_ENCRYPTION=auto` explicitly — the test session runs field-only, and a guard that inherits
that policy inspects a plaintext scratch file and passes no matter what `env.py` does. That single
line of test hygiene is the reason the bug was findable at all.

## 6. What the engine refuses to do

Worth naming, because the refusals are what a later contributor is most tempted to remove:

- **A lesson never needs a model.** `/lessons/run/{id}/explain` looks in that order: the
  `NarrationCache` row (keyed `cache_key(spec_id, frame_index, question)`, `source: "cache"`),
  then the sim's own `explain()` (`source: "deterministic"`), then nothing — and "nothing" is an
  `Explanation` with empty text and a sentence saying the question goes to the tutor instead. That
  ordering is what keeps a 56-frame lesson from becoming 56 requests to Gemini; the probe asserts
  the answer arrived and no model was billed.
- **Nothing raw reaches a learner.** `_plain_text` at the boundary, `PRIVATE_ITEM_KEYS` on every
  learner-facing payload, and two probe checks that fail the *screen* (`no raw object reached the
  screen`, `no raw JSON object is shown to the learner on this screen`) rather than the JSON.
- **Nothing is inferred silently.** `evidence_summary()` returns `not_evidence[]` — the reasons a
  run is not a mastery signal — and `POST /lessons/run/{id}/variables` answers with the parameter
  names that actually changed plus `derived_from`, so "this is now your own copy of the scene" comes
  from the server and not from a client's guess. When nothing you typed moved a value, it says so
  (`the scene was not changed: nothing you typed was applied`) instead of silently re-using the
  parent row.
- **The list cannot be poisoned.** `visible_scenes` is `shared OR mine`, with `kind` filtered in
  SQL: `LessonScene.origin.in_(["builtin", "ai2"]) & author_student_id.is_(None)` unioned with the
  learner's own rows. There is no path by which learner A resumes or even *sees* learner B's copy.

## Operations

```bash
cd backend
PYTHONPATH=$PWD/src ADAPT_DATA_DIR=/path/to/data ../.venv/bin/alembic -c alembic.ini upgrade head
PYTHONPATH=$PWD/src ADAPT_DATA_DIR=/path/to/data ../.venv/bin/alembic -c alembic.ini current
```

A database built by `db/init_db.py`'s `init()` (`database.create_all()`) has **no `alembic_version`
row**, so a bare `upgrade head` replays `0001` and dies on `table audit_log already exists`. Stamp
first — at the revision the file actually matches, which is not always the same answer:

* a database created by *this* build has every column and index already, so it is at head:
  `stamp head`, and nothing needs to run;
* a database created by an earlier build matches `0002`: `stamp 0002` then `upgrade head` applies
  the `0003` constraints it is missing. `stamp head` on that file is the version of this that does
  damage — the database looks migrated and still has `lesson_scenes.spec_id` unique, which is the
  collision in §4.

Those two cases are hard to tell apart from the outside, so `0003` does not have to guess: both of
its directions read `sa.inspect(op.get_bind()).get_indexes("lesson_scenes")` and change only what
is absent or has the wrong shape. Replaying it onto a file that already matches is a no-op instead
of `index uq_scene_spec_shared already exists` — which is what it was, on the documented recovery
step for a fresh clone, until the test below made the two shapes collide on purpose. And
`test_an_upgraded_database_has_the_indexes_create_all_builds` compares an upgraded database with a
`create_all` one on **every** index name and uniqueness flag, not just columns, so a model index
with no revision behind it fails here rather than in a listing query at 3am. Read a result back
through the app's engine, never a bare `sqlite://` URL:

```bash
alembic -c alembic.ini stamp 0002 && alembic -c alembic.ini upgrade head
python -c "from sqlalchemy import inspect; from adapt.db.engine import create_db_engine, get_settings; \
e,_ = create_db_engine(get_settings()); print(sorted(inspect(e).get_indexes('lesson_scenes')))"
```

`ensure_scenes()` runs at every API startup, seeds nothing that already exists and reports the
truth: `scenes=8 (0 new)` is idempotence being announced, not a bug. If the key comes from
`ADAPT_KEYFILE`, the process must be able to read it (the dev key lives at `var/keys/adapt.key`) —
a missing or rotated keyfile is the usual reason a migrated database suddenly looks like garbage,
and startup stops with a sentence naming the file rather than serving 500s.
