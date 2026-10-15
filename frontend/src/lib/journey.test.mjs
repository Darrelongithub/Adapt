import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { bannedHits } from "./bannedPhrases.mjs";
import { STAGE_IDS, STEPS, isBehind, percent } from "./journeyPath.ts";

/** The backend's own copy of the path, read as source: one path means one order. */
function backendStages() {
  const src = readFileSync(new URL("../../../backend/src/adapt/journey/stages.py", import.meta.url), "utf8");
  const block = /STAGES: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/.exec(src);
  assert.ok(block, "stages.py should declare STAGES as a tuple of strings");
  return [...block[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

test("the frontend's stage ids are the backend's, in the backend's order", () => {
  const backend = backendStages();
  assert.deepEqual([...STAGE_IDS], backend);
  assert.equal(backend.length, 8, "eight stages, as the brief froze them");
});

test("every stage has learner-readable words and a route", () => {
  for (const id of STAGE_IDS) {
    const step = STEPS[id];
    assert.ok(step, `${id} has no step metadata`);
    assert.ok(step.title && step.title.length > 2, `${id} needs a title`);
    assert.ok(step.what && step.what.length > 20, `${id} needs a sentence saying what it is for`);
    assert.ok(step.action, `${id} needs an action label`);
    assert.ok(step.href.startsWith("/"), `${id} must point at an app route`);
    assert.ok(step.skipNote, `${id} needs to say what skipping costs`);
  }
  const numbers = Object.values(STEPS).map((s) => s.n);
  assert.deepEqual(numbers, [1, 2, 3, 4, 5, 6, 7, 8]);
});

test("no step's own words name the machinery", () => {
  for (const [id, step] of Object.entries(STEPS)) {
    const blob = [step.title, step.what, step.action, step.skipNote].join(" ");
    assert.deepEqual(bannedHits(blob), [], `${id}: ${bannedHits(blob).join(",")}`);
  }
});

test("a skip is behind you, and the bar counts it", () => {
  assert.equal(isBehind({ id: "games", status: "done" }), true);
  assert.equal(isBehind({ id: "games", status: "skipped" }), true);
  assert.equal(isBehind({ id: "games", status: "todo" }), false);
  const state = {
    stage: "subject",
    stages: [
      { id: "account", status: "done" },
      { id: "preferences", status: "done" },
      { id: "games", status: "skipped" },
      { id: "subject", status: "todo" },
    ],
  };
  assert.equal(percent(state), 75);
  assert.equal(percent(null), 0);
  assert.equal(percent({ stage: "account", stages: [] }), 0);
  // the server's own count wins when it sent one
  assert.equal(percent({ ...state, progress: { percent: 13 } }), 13);
});
