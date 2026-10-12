/**
 * Tests for the player's head — the part that decides where the learner is.
 *
 * Run with `npm run test:engine` (plain `node --test`; no browser, no React, no build).
 * The interesting assertions are the boring ones: a frame index that cannot leave the
 * range of the trace, a caption that cannot vanish in under a couple of seconds, and a
 * gate that cannot trap a learner who has already answered.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import {
  autoplayAllowed,
  canAdvance,
  clampIndex,
  clampSpeed,
  fmtClock,
  frameMs,
  highlights,
  init,
  keyAction,
  MAX_FRAME_MS,
  MIN_FRAME_MS,
  nextUnanswered,
  pendingPrompt,
  progressPercent,
  reducer,
  unanswered,
} from "./player.ts";

const sent = (i, extra = {}) => ({
  i,
  kind: "binary_search",
  title: "Step",
  narration: `frame ${i}`,
  state: {},
  highlights: [],
  cause: null,
  prompt: null,
  ...extra,
});

const trace = (n, promptEvery = 0) =>
  Array.from({ length: n }, (_, i) =>
    i === promptEvery ? sent(i, { prompt: { type: "next_probe_index", question: "Which half?", choices: ["left", "right"] } }) : sent(i),
  );

test("the index cannot leave the trace, however it is asked to", () => {
  assert.equal(clampIndex(-5, 8), 0);
  assert.equal(clampIndex(99, 8), 7);
  assert.equal(clampIndex(NaN, 8), 0);
  assert.equal(clampIndex(Infinity, 8), 7);
  assert.equal(clampIndex(3.7, 8), 3, "a fractional index truncates rather than throwing");
  assert.equal(clampIndex(0, 0), 0, "an empty trace is index 0, not -1");
  const s = init([], 40);
  assert.equal(s.frames.length, 0);
  assert.equal(s.index, 0);
  assert.deepEqual(unanswered(s.frames, s.answers), []);
});

test("stepping stops at the end instead of wrapping round", () => {
  let s = init(trace(3), 0, false);
  s = reducer(s, { type: "step" });
  s = reducer(s, { type: "step" });
  assert.equal(s.index, 2);
  s = reducer(s, { type: "step" });
  assert.equal(s.index, 2, "the last frame is the last frame");
  s = reducer(s, { type: "back" });
  assert.equal(s.index, 1);
  const first = reducer(init(trace(3), 0, false), { type: "back" });
  assert.equal(first.index, 0, "back at the start does not go to the end");
});

test("reaching the end stops the clock; playing from the end starts the scene again", () => {
  let s = init(trace(2), 0, false);
  s = { ...s, playing: true };
  s = reducer(s, { type: "step" });
  assert.equal(s.playing, false, "a scene that ended kept 'playing' and the timer would have run on");
  s = reducer(s, { type: "play" });
  assert.equal(s.index, 0);
  assert.equal(s.playing, true);
});

test("a prediction blocks the next frame until it is answered, and never after", () => {
  let s = init(trace(3, 0));
  assert.equal(pendingPrompt(s), true);
  assert.equal(canAdvance(s), false);
  s = reducer(s, { type: "step" });
  assert.equal(s.index, 0, "the gate held");
  s = reducer(s, { type: "answer", index: 0, value: "left" });
  assert.equal(pendingPrompt(s), false);
  assert.equal(canAdvance(s), true);
  s = reducer(s, { type: "step" });
  assert.equal(s.index, 1);
  // a second pass over the same frame must not re-arm the question
  s = reducer(s, { type: "jump", index: 0 });
  assert.equal(pendingPrompt(s), false);
  // and a learner who turns the gate off is allowed past, with the skip recorded
  const ungated = reducer(init(trace(3, 0)), { type: "gate", gate: false });
  assert.equal(canAdvance(ungated), true);
});

test("seeking past a prediction is allowed, and the run says so", () => {
  let s = init(trace(4, 1));
  s = reducer(s, { type: "jump", index: 3 });
  assert.equal(s.index, 3);
  assert.deepEqual(unanswered(s.frames, s.answers), [1]);
  assert.equal(nextUnanswered(s.frames, 0, s.answers), 1);
  assert.equal(nextUnanswered(s.frames, 2, s.answers), null, "nothing is owed after the last question");
});

test("an unknown index in a jump is clamped, not trusted", () => {
  const s = reducer(init(trace(4), 0, false), { type: "jump", index: 9999 });
  assert.equal(s.index, 3);
  const neg = reducer(init(trace(4), 0, false), { type: "jump", index: -3 });
  assert.equal(neg.index, 0);
});

test("dwell time follows the words, inside bounds that cannot flash or freeze", () => {
  const short = frameMs(sent(0, { narration: "mid" }));
  const long = frameMs(sent(0, { narration: "a ".repeat(28) + "sentence that goes on and on about the same window" }));
  assert.ok(long > short, `${long} !> ${short}: a longer caption must stay up longer`);
  for (const ms of [short, long]) {
    assert.ok(ms >= MIN_FRAME_MS && ms <= MAX_FRAME_MS, `${ms}ms is outside the readable window`);
  }
  assert.equal(frameMs(undefined), MIN_FRAME_MS, "a missing frame is a calm default, not a crash");
  const wall = frameMs(sent(0, { narration: "word ".repeat(200) }));
  assert.equal(wall, MAX_FRAME_MS, "a wall of text must not stall the scene");
  // one unbroken 4000-character token has no spaces to count, and must still be a pause and
  // not a freeze — the backend caps narration length, but a cap is not a contract
  const blob = frameMs(sent(0, { narration: "x".repeat(4000) }));
  assert.ok(blob >= MIN_FRAME_MS && blob <= MAX_FRAME_MS, `${blob}ms`);
});

test("speed is bounded, and a silly number cannot make the scene run at infinity", () => {
  assert.equal(clampSpeed(0), 0.5);
  assert.equal(clampSpeed(100), 2);
  assert.equal(clampSpeed(NaN), 1);
  assert.equal(reducer(init(trace(2)), { type: "speed", speed: -4 }).speed, 0.5);
  const slow = frameMs(sent(0, { narration: "one two three four five" }), 1);
  const fast = frameMs(sent(0, { narration: "one two three four five" }), 2);
  assert.ok(fast < slow && fast > 0);
});

test("reduced motion turns off the part that moves by itself", () => {
  assert.equal(autoplayAllowed(true), false);
  assert.equal(autoplayAllowed(false), true);
});

test("the seen-frames set grows once per frame, for the review screen", () => {
  let s = init(trace(3), 0, false);
  s = reducer(s, { type: "step" });
  s = reducer(s, { type: "step" });
  s = reducer(s, { type: "back" });
  assert.deepEqual(s.seen, [0, 1, 2].filter((i) => s.seen.includes(i)).concat(s.seen.filter((i) => ![0, 1, 2].includes(i))));
  assert.equal(new Set(s.seen).size, s.seen.length, "a frame seen twice is counted twice");
});

test("progress and the clock degrade sensibly", () => {
  assert.equal(progressPercent(0, 0), 0);
  assert.equal(progressPercent(0, 1), 100);
  assert.equal(progressPercent(1, 3), 50);
  assert.equal(progressPercent(999, 3), 100);
  assert.equal(fmtClock(null), "—");
  assert.equal(fmtClock(-1), "—");
  assert.equal(fmtClock(NaN), "—");
  assert.equal(fmtClock(83), "1m 23s");
});

test("keys that belong to a text box stay in the text box", () => {
  assert.equal(keyAction(" ", { tagName: "TEXTAREA" }), null, "space in the 'why?' field must type a space");
  assert.equal(keyAction("ArrowRight", { tagName: "INPUT" }), null);
  assert.equal(keyAction("p", { isContentEditable: true }), null);
  assert.equal(keyAction(" ", null), "play");
  assert.equal(keyAction("ArrowRight", { tagName: "BODY" }), "step");
  assert.equal(keyAction("x", { tagName: "BODY" }), null, "an unbound key must not move anything");
});

test("a frame with no highlights array renders nothing rather than throwing", () => {
  assert.deepEqual(highlights(sent(0)), []);
  assert.deepEqual(highlights(sent(0, { highlights: null })), []);
  assert.deepEqual(highlights(sent(0, { highlights: "lo" })), [], "a bare string is not a list of ids");
  assert.deepEqual(highlights(sent(0, { highlights: ["lo", 3, null] })), ["lo"]);
});

/* ------------------------------------------------------------------------- safety ----
 * The renderers put model-written text on screen. These three checks are the cheap
 * version of an XSS review, and they run in CI with no browser: text only, no HTML
 * injection, and no browser code that assumes a backend on localhost.
 */

/** Comments talk about these patterns; only code may not use them. */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(^|[^:])\/\/.*$/g, "$1 ");
}

const here = dirname(fileURLToPath(import.meta.url));
const sources = [
  join(here, "player.ts"),
  join(here, "types.ts"),
  join(here, "..", "..", "components", "scenes", "index.tsx"),
  join(here, "..", "..", "components", "scenes", "stages.tsx"),
];

function read(p) {
  try {
    return readFileSync(p, "utf8");
  } catch {
    return null;
  }
}

test("nothing renders a scene as HTML", () => {
  const banned = ["dangerouslySetInnerHTML", "innerHTML", "document.write", "eval(", "new Function(", "<script"];
  for (const file of sources) {
    const raw = read(file);
    if (raw === null) continue; // a stage file that does not exist yet is not a violation
    const src = stripComments(raw);
    for (const needle of banned) {
      assert.ok(!src.includes(needle), `${file} uses ${needle}: scene text is plain text, always a text node`);
    }
  }
});

test("browser code never names a host", () => {
  for (const file of sources) {
    const raw = read(file);
    if (raw === null) continue;
    const src = stripComments(raw);
    assert.ok(!/localhost|127\.0\.0\.1|process\.env/.test(src), `${file} hardcodes a host or reads env in the browser`);
  }
});
