# HANDOFF_B — real tests (Round 6)

## What landed

**Backend** (`backend/src/adapt/subjects/test_items.py` + `api/routes/tests.py`
`/api/v1/tests/*`, models in `subjects/models.py`, migrations P4/P5, tests in
`tests/test_subject_test_items.py` — 18 tests):

- 12 interactive item types in one registry (`registry.ts` on the frontend is
  the mirror): each renderer submits `correct/incorrect + ms + hint_use`;
  `POST /tests/evidence` stores rows (idempotent per run_id+item_id, cap 64/run,
  `GET /tests/evidence` newest-first ≤200).
- `build_plan(subject, family, level 1-3, style, goal, llm, student_id)`:
  Gemini proposes `{item_type, skill, difficulty 1-5, parameters}` per item;
  `validate_plan` drops unknown types / unknown parameters / bad difficulty,
  caps 8 items, requires ≥4 items and ≥2 skills (else falls back to simple).
  `source` is `model` or `simple` — never a provider name.
- **Family choice**: the learner's own words win (`family_for_text(subject)`);
  the resolver's coarse taxonomy family is only the fallback. This is what
  makes "geometry" ≠ "discrete maths" through the whole API (a subject_id used
  to override the text with the coarse family and collapse both to `maths`).
- Simple mode is deterministic per family — geometry emphasises spatial items
  (geometry_angle/geometry_congruent/odd_one_out/pattern_pick), discrete
  emphasises logic/sets/proof-order (truth_table/venn_region/drag_order/
  drag_match), language/law lean cloze+match. Geometry vs discrete maths plans
  share **zero** item types; a fixed seed is byte-identical across calls.
- `test_plan` purpose registered in `ai/routing.py` TASKS (deep class) and
  `docs/EFFICIENCY.md` (the ast-walking test enforces both).

**Frontend**: `frontend/src/lib/tests/{registry.ts,api.ts}` +
`frontend/src/components/tests/{items.tsx,tests.css}` — 12 renderers
(pattern_pick, drag_order, drag_match, odd_one_out, truth_table, venn_region,
geometry_angle, geometry_congruent, number_line, fraction_bar, cloze_word_bank,
quick_recall) + `TestItemCard` (hint button counts `hints_used`, Check button,
verdict). `TestCard` (the fake self-rating) is deleted; `/subject` deleted.

**Tests**:
- `frontend/src/lib/registry.test.mjs` — 5 unit tests (registry validity,
  INSTRUCTIONS ≤12 words, splitList/asInt). `npm run test:lib` = 47 green.
- `frontend/tests-e2e/round6-items.mjs` — **the** click-through: every type
  clicked to its correct answer and asserted "Correct", then the journey runs
  twice ("Geometry" vs "Discrete maths") with a plan-JSON responder, asserting
  the item types visibly differ and evidence rows land. Command:

  ```
  cd frontend && PLAYWRIGHT_CHROMIUM_EXECUTABLE=/home/user/tools/pw/chromium \
    LD_LIBRARY_PATH=/home/user/tools/pw/al2023/lib node tests-e2e/round6-items.mjs
  ```

- `frontend/src/app/dev/test-lab/page.tsx` — renders all 12 types with the
  answers in `data-answer` (dev-only leak, acceptable; real items never expose
  the key).

## Contract notes for other tracks

- `TestEvidenceCard` (dashboard) reads `GET /tests/evidence`; rows carry
  `{id, run_id, subject_id, item_id, item_type, skill, difficulty, answer,
  correct, ms, hints_used, observed_at}`.
- AI 1 may read the same rows (already framed in the card copy).
- `TestItemCard` sections carry `data-item-type` / `data-item-id` /
  `data-answered` — e2e responders select `[data-answered="false"]` to avoid
  the verdict beat between items.
- The journey's wizard subject step posts `subject: title` + `subject_id`;
  level is 1|2|3 (beginner/some/confident), style see|try|hear|read,
  goal exam|skills|curiosity.
- `.option-tile` in `tests.css` collides with `pattern_pick`'s inert sequence
  tiles — always scope clicks to `.option-grid .option-tile`.
