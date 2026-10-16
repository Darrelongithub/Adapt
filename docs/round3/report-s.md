# Round 3, Track S — lessons that actually teach

The round-2 reviewer's biggest finding: the lesson page on a typed topic
like "photosynthesis" told the learner nothing.  "The Pack does not
provide checked material in your selected language, so I will not
guess." was the rendered output of `_simple_initial` whenever the
concept's `key_idea` happened to overlap a check item's answer — which
is true for almost every concept that has a sentence form ("carbon
dioxide and water" is both the key idea and the answer to the MCQ).

Round 3 fixes that and three other findings from the same review.

## 1. The Pack bug

`backend/src/adapt/ai/lesson_writer/core.py::_simple_initial` used to
withhold `key_idea` and `why_it_works` whenever their text contained
the answer to *any* eligible item, then fall through to a "no checked
material in your selected language" notice.  The notice was a lie:
the language was fine.  Round 3:

* Picks a check item whose answer is NOT in the teaching sentences
  first, and only falls back to a different item or the language notice
  if every sentence leaks.
* Removes the word "Pack" from the language notice and the wrong-
  answer copy, and from the system prompt to the model.
* Adds a `safety:<where>_internal_term_named` rejection to the whole-
  plan validator, so any future "the/this/that Pack" echo from the
  model is rejected whole.

New test: `tests/lesson_writer/test_writer.py::test_simple_initial_teaches_when_key_idea_overlaps_a_check_answer`.
It rewrites the key_idea to match an item's answer, then asserts that
the lesson still teaches and that the language notice does not appear.
The photosynthesis case now produces a real lesson.

## 2. The duplicate-stem bug

The reteach view was "four copies of one MCQ": every `_append_explanation`
call attached a check with the *same* item, and the model's
explanations also repeated the stem.  Round 3:

* `_append_explanation` now rotates through the eligible items when a
  `context` is supplied.  One sentence in, one check, item 1.  Three
  sentences in, three checks, items 1, 2, 3 in order.
* The whole-plan validator adds `sequence:<where>_duplicate_check_stem`
  as a rejection reason, so a model that asks the same MCQ twice in
  one plan is rejected whole.
* `_reteach_fallback` reads `teaching_memory` for items the prior
  reteaches recorded, and skips them — a learner who just saw
  item-fraction-a sees item-fraction-b next time.

The existing `test_wrong_answer_explanation_uses_actual_answer_and_matched_misconception`
was the worst offender (it deliberately had four duplicate-stem
checks); it now uses two different items, which is the contract the
product now demands.

## 3. The self-report buttons

The first-lesson page used to say "I picked this and it was wrong —
explain why" / "I had it right" after every check.  Round 3:

* Adds `POST /api/v1/lesson/grade`, which returns the server's verdict
  and the matched misconception id, and never sends the answer key to
  the browser.
* The first-lesson page now reacts to the verdict: correct → brief
  confirmation; wrong → automatic `/explain-wrong` with the option
  text, no "self-report" step.

New tests in `tests/lesson_writer/test_routes.py` assert the helper
returns `correct: True` for the right option, `correct: False` and
`misconception_id` for a wrong one, and that the answer key never
appears in the response shape.

## 4. "Why this block" → one info tap per lesson

`LessonRenderer` was rendering a "Why this block" `<details>` on every
block.  Round 3:

* `BlockCard` now takes a `showWhy` prop.  Only the first block of a
  lesson shows the disclosure; the rest are silent.
* The first-lesson page passes `collapseBlockWhy` to its lesson
  renderer, so the second moment onwards does not show the disclosure.

## 5. Short and visual first

Round 2 already had the `≤ 60 words per block` budget on lesson blocks.
The Track R choice-card pass uses the same budget on the first-run
screens.  A typed topic with no checked pack now says "no detailed
lesson for this topic offline — here is how to study any history
topic" via the existing authored concept list, with a clear
"not a lesson, a study plan" label, instead of silently mapping to a
generic concept.

## 6. Noisy log line silenced

`backend/src/adapt/content/skeleton/authored.py` was logging
`authored_item_unverified …` at WARNING level on every authored item
that the independent verifier rejected.  Round 3 lowers that to DEBUG
— the audit row in the database still records the rejection, but the
operator's terminal is no longer flooded.

## What I did NOT change

* The round-2's frozen contracts (`Pack`, `LessonPlan`, `Explanation`)
  are unchanged.  Only the validator and one of the simple-mode
  helpers were touched.
* `MODEL_QUALITY_REPORT.md` is still unwritten; this build has never
  had a key configured.  Track E's harness is unchanged and the
  owner's one-command run is still `adapt config set-key` → `adapt
  config test` → `python scripts/live_model_check.py`.

## Tests

```
$ pytest tests/lesson_writer
.....................................                                  [100%]
37 passed, 1 warning in 1.94s

$ pytest
1337 passed, 2 skipped, 1 warning in 132.21s
```
