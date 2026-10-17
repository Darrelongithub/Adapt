/**
 * Frontend registry unit tests (Round 6, Track B) — `npm run test:lib`.
 * The item scorers live in the components (DOM code); these pin the registry
 * half: plan validation, instruction word budget, and the parameter helpers.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  HINTS,
  INSTRUCTIONS,
  ITEM_TYPES,
  asInt,
  splitList,
  validatePlanItems,
} from "./tests/registry.ts";

test("every registered type has an instruction of at most 12 words and a hint", () => {
  for (const type of ITEM_TYPES) {
    const words = INSTRUCTIONS[type].split(/\s+/).filter(Boolean);
    assert.ok(words.length <= 12, `${type} instruction is ${words.length} words: ${INSTRUCTIONS[type]}`);
    assert.ok(HINTS[type].length > 0, `${type} has no hint`);
  }
});

test("validatePlanItems keeps only registered types and numbers the items", () => {
  const items = validatePlanItems([
    { item_type: "pattern_pick", skill: "p", difficulty: 3, parameters: { sequence: "a,b,c" } },
    { item_type: "self_rating", skill: "x", difficulty: 3, parameters: {} },
    { item_type: "truth_table", skill: "logic", difficulty: 99, parameters: { expression: "A" } },
  ]);
  assert.equal(items.length, 2);
  assert.equal(items[0].id, "item-0");
  assert.equal(items[1].id, "item-1");
  assert.ok(items[1].difficulty <= 5, "difficulty is clamped");
  assert.ok(items.every((i) => ITEM_TYPES.includes(i.item_type)));
});

test("validatePlanItems caps a plan at 8 items", () => {
  const many = Array.from({ length: 12 }, () => ({ item_type: "odd_one_out", skill: "s", difficulty: 2, parameters: {} }));
  assert.equal(validatePlanItems(many).length, 8);
});

test("validatePlanItems rejects junk shapes without throwing", () => {
  assert.deepEqual(validatePlanItems(null), []);
  assert.deepEqual(validatePlanItems([null, 3, "x", {}]), []);
});

test("splitList trims and drops empties; asInt rounds and falls back", () => {
  assert.deepEqual(splitList(" a, b ,,c "), ["a", "b", "c"]);
  assert.deepEqual(splitList(undefined), []);
  assert.equal(asInt("12.7", 0), 13);
  assert.equal(asInt("nope", 5), 5);
  assert.equal(asInt(undefined, 2), 2);
});
