import assert from "node:assert/strict";
import test from "node:test";

import { CURRICULA, EMOTIONS, HELP_CHOICES, moveObjective, originLabel } from "./round9.ts";
import { bannedHits } from "./bannedPhrases.mjs";

test("the curriculum chips are the five the wizard offers", () => {
  assert.deepEqual(
    CURRICULA.map((c) => c.label),
    ["Kenya CBC", "KCSE", "IGCSE", "University", "Self-study"],
  );
});

test("origin labels never dress a suggestion up as a syllabus", () => {
  assert.equal(originLabel("syllabus"), "From the syllabus");
  assert.equal(originLabel("outline"), "From an open outline");
  assert.equal(originLabel("yours"), "Added by you");
  assert.equal(originLabel("suggested"), "Suggested");
  assert.equal(originLabel("anything-else"), "Suggested");
});

test("reorder moves one row and stops at the edges", () => {
  assert.deepEqual(moveObjective(["a", "b", "c"], 1, -1), ["b", "a", "c"]);
  assert.deepEqual(moveObjective(["a", "b", "c"], 1, 1), ["a", "c", "b"]);
  assert.deepEqual(moveObjective(["a", "b"], 0, -1), ["a", "b"]);
  assert.deepEqual(moveObjective(["a", "b"], 1, 1), ["a", "b"]);
});

test("the check-in tiles name feelings about a sitting, never traits", () => {
  assert.equal(EMOTIONS.length, 6);
  assert.equal(HELP_CHOICES.length, 3);
  for (const row of [...EMOTIONS, ...HELP_CHOICES, ...CURRICULA]) {
    assert.deepEqual(bannedHits(row.label), [], row.label);
  }
});
