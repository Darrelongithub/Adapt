import assert from "node:assert/strict";
import test from "node:test";

import { bannedHits } from "./bannedPhrases.mjs";
import {
  DEMO_GAME_COUNT,
  analyseDemo,
  builtInDemoSet,
  demoEvidenceRows,
  demoGameName,
  demoRunId,
  demoTransition,
  firstDemoItem,
  nextDemoItem,
  validatedDemoItems,
  withDemoTimeout,
} from "./demo.ts";

const answer = (item, correct = true, hints = 0) => ({
  item,
  result: { correct, answer: "test answer", ms: 4_000, hints },
});

test("the built-in plan has at least three short games and survives validation", () => {
  const set = builtInDemoSet();
  assert.equal(set.origin, "built-in");
  assert.equal(set.runId, null);
  assert.ok(set.items.length >= DEMO_GAME_COUNT);
  assert.deepEqual(validatedDemoItems(set.items)?.map((item) => item.id), set.items.map((item) => item.id));
  assert.equal(firstDemoItem(set.items)?.item_type, "pattern_pick");
});

test("an invalid or slow-plan shape cannot leave a one-game dead end", () => {
  assert.equal(validatedDemoItems(null), null);
  assert.equal(validatedDemoItems([{ item_type: "unknown" }]), null);
  const longOnly = builtInDemoSet().items.slice(0, 2).map((item) => ({ ...item, item_type: "drag_order" }));
  assert.equal(validatedDemoItems(longOnly), null);
});

test("a correct unaided answer steps up; a miss steps down", () => {
  const items = builtInDemoSet().items;
  const first = firstDemoItem(items);
  assert.ok(first);
  const afterCorrect = nextDemoItem(items, [answer(first, true)]);
  assert.ok(afterCorrect);
  assert.ok(afterCorrect.difficulty > first.difficulty);
  assert.match(demoTransition(answer(first, true), afterCorrect), /stepped/);

  const afterMiss = nextDemoItem(items, [answer(first, false)]);
  assert.ok(afterMiss);
  assert.ok(afterMiss.difficulty < first.difficulty);
  assert.match(demoTransition(answer(first, false), afterMiss), /gentler/);
});

test("using a hint keeps support ahead of difficulty", () => {
  const items = builtInDemoSet().items;
  const first = firstDemoItem(items);
  assert.ok(first);
  const next = nextDemoItem(items, [answer(first, true, 1)]);
  assert.ok(next);
  assert.ok(next.difficulty < first.difficulty);
});

test("three selections are unique and then stop", () => {
  const items = builtInDemoSet().items;
  const first = firstDemoItem(items);
  assert.ok(first);
  const played = [answer(first, true)];
  while (played.length < DEMO_GAME_COUNT) {
    const next = nextDemoItem(items, played);
    assert.ok(next);
    played.push(answer(next, played.length % 2 === 0));
  }
  assert.equal(new Set(played.map((row) => row.item.id)).size, DEMO_GAME_COUNT);
  assert.equal(nextDemoItem(items, played), null);
});

test("analysis reports observations, a next step, and no fixed learner label", () => {
  const items = builtInDemoSet().items;
  const first = firstDemoItem(items);
  assert.ok(first);
  const second = nextDemoItem(items, [answer(first, false)]);
  assert.ok(second);
  const played = [answer(first, false), answer(second, true, 1), answer(items[4], true)];
  const analysis = analyseDemo(played);
  assert.equal(analysis.correct, 2);
  assert.equal(analysis.total, 3);
  assert.match(analysis.headline, /2 of 3/);
  assert.match(analysis.nextStep, /gentler/);
  assert.match(analysis.supportNote ?? "", /hint/);
  const copy = [analysis.headline, analysis.nextStep, analysis.supportNote, ...items.map(demoGameName)].join(" ");
  assert.deepEqual(bannedHits(copy), []);
  assert.doesNotMatch(copy, /visual learner|auditory|fixed trait|\biq\b/i);
});

test("evidence is offered only for one complete server-graded run", () => {
  const local = builtInDemoSet().items.slice(0, 3).map((item) => answer(item));
  assert.equal(demoRunId(local), null);

  const server = builtInDemoSet().items.slice(0, 3).map((item, index) => answer({
    ...item,
    id: `server-${index}`,
    run_id: "run-demo",
    grading_seal: `seal-${index}`,
  }));
  assert.equal(demoRunId(server), "run-demo");
  const rows = demoEvidenceRows(server);
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((row) => row.item_id), ["server-0", "server-1", "server-2"]);
  assert.ok(rows.every((row) => row.grading_seal && row.answer === "test answer"));

  assert.equal(demoRunId(server.slice(0, 2)), null);
  assert.equal(demoRunId(server.map((row, index) => index === 1 ? { ...row, item: { ...row.item, run_id: "other" } } : row)), null);
});

test("the preparation bound rejects late work and releases its timer", async () => {
  await assert.rejects(withDemoTimeout(new Promise(() => {}), 5), /timed out/);
  assert.equal(await withDemoTimeout(Promise.resolve("ready"), 50), "ready");
});
