/**
 * The browser half of "one request per intention" (see `debounce.ts`). Plain node, no DOM:
 * the helpers are ordinary functions with a clock, and the whole point of keeping them out of
 * the components is that they can be tested like this.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createDeduper, createInflight, debounce, throttle } from "./debounce.ts";

test("a repeat inside the window is refused, and a fresh key is not", () => {
  const dedupe = createDeduper(900);
  assert.equal(dedupe.seen("item_1"), false, "the first call is never a repeat");
  assert.equal(dedupe.seen("item_1"), true, "the second call inside the window is");
  assert.equal(dedupe.seen("item_2"), false, "a different key is a different intention");
  assert.equal(dedupe.suppressed(), 1);
});

test("the window closes and the learner is allowed to repeat themselves", () => {
  const dedupe = createDeduper(900);
  assert.equal(dedupe.seen("same text", 1_000), false);
  // Two seconds later, the same words are a *new* question, not a double-click. A person who
  // asks twice on purpose deserves two answers; only the sub-second repeat is an artefact.
  assert.equal(dedupe.seen("same text", 3_000), false);
  // ...and a held key keeps refreshing the window, so every repeat inside it is refused.
  assert.equal(dedupe.seen("same text", 3_100), true);
  assert.equal(dedupe.seen("same text", 3_200), true);
});

test("forget() reopens a key -- a cleared box is not a repeat", () => {
  const dedupe = createDeduper(900);
  assert.equal(dedupe.seen("k"), false);
  dedupe.forget("k");
  assert.equal(dedupe.seen("k"), false, "the remembered repeat was cleared on purpose");
  assert.equal(dedupe.suppressed(), 0);
});

test("in-flight returns one answer and refuses the second caller", async () => {
  const guard = createInflight();
  let calls = 0;
  let release = () => {};
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const first = guard.run("attempt:item_1", async () => {
    calls += 1;
    return pending;
  });
  assert.equal(guard.busy("attempt:item_1"), true);
  const second = await guard.run("attempt:item_1", async () => {
    calls += 1;
    return "second";
  });
  assert.equal(second, undefined, "the second submission must not run");
  assert.equal(calls, 1, "exactly one request was made");
  release("first");
  assert.equal(await first, "first");
  assert.equal(guard.busy("attempt:item_1"), false, "the slot is released when the request ends");
});

test("share() gives a second click the first request's answer", async () => {
  const guard = createInflight();
  let calls = 0;
  const first = guard.share("attempt:k", async () => {
    calls += 1;
    return "receipt";
  });
  const second = guard.share("attempt:k", async () => {
    calls += 1;
    return "another";
  });
  assert.equal(await first, "receipt");
  assert.equal(await second, "receipt", "the second caller joins the first request");
  assert.equal(calls, 1, "exactly one request was made");
  assert.equal(guard.busy("attempt:k"), false, "and the key is free again afterwards");
});

test("a rejected request still releases its slot", async () => {
  const guard = createInflight();
  await assert.rejects(
    guard.run("k", async () => {
      throw new Error("boom");
    }),
  );
  assert.equal(guard.busy("k"), false, "a failure must not leave a key permanently busy");
  assert.equal(await guard.run("k", async () => "ok"), "ok");
});

test("debounce waits for the last call and cancel() drops it", async () => {
  const seen = [];
  const burst = debounce((n) => seen.push(n), 10);
  burst(1);
  burst(2);
  burst(3);
  assert.deepEqual(seen, [], "nothing runs until the burst stops");
  await new Promise((r) => setTimeout(r, 40));
  assert.deepEqual(seen, [3], "only the last value in the burst is used");
  burst(4);
  burst.cancel();
  await new Promise((r) => setTimeout(r, 40));
  assert.deepEqual(seen, [3], "cancel() drops the pending call");
});

test("throttle runs on the leading edge and reports what it dropped", () => {
  const seen = [];
  const limited = throttle((s) => seen.push(s), 50);
  assert.equal(limited("a"), true);
  assert.equal(limited("b"), false, "inside the window the call is dropped, not queued");
  assert.deepEqual(seen, ["a"]);
});

test("the voice screen and the flow layer actually use the guards", () => {
  // A helper nobody calls is a helper that has not fixed anything. Both of these were
  // flood paths: the socket sent a frame per keystroke, and the practice screen could submit
  // the same answer twice.
  const voice = readFileSync(new URL("../app/voice/page.tsx", import.meta.url), "utf8");
  assert.match(voice, /createDeduper/, "the voice screen has to dedupe identical sends");
  assert.match(voice, /debounce/, "config frames have to be debounced, not sent per keystroke");

  const flow = readFileSync(new URL("./flow.ts", import.meta.url), "utf8");
  assert.match(flow, /createInflight/, "the shared submit path has to refuse a second in-flight attempt");
  assert.match(flow, /createDeduper/, "a double-click on submit must not record two attempts");
});
