/**
 * Tests for the session ledger — the readable record of what this browser decided.
 *
 * Run with `npm run test:lib` (plain `node --test`; no browser, no React, no build). The module
 * is pure on purpose so this can run at all: the interesting failures are a log that grows
 * without bound, one that loses the newest entry, and one that throws because a browser refused
 * to store anything — which on screen would look like a broken page rather than an empty log.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { LEDGER_LIMIT, pushEntry, readLedger, writeLedger } from "./ledger.ts";

function memoryStore(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    _map: map,
  };
}

test("the newest entry is the first one read back", () => {
  let entries = [];
  entries = pushEntry(entries, "10:00:01 — “Ratios” → Approved");
  entries = pushEntry(entries, "10:04:20 — “Batch length” decision revoked");
  assert.equal(entries.length, 2);
  assert.match(entries[0], /revoked/);
  assert.match(entries[1], /Approved/);
});

test("it cannot grow without bound over a long session", () => {
  let entries = [];
  for (let i = 0; i < LEDGER_LIMIT * 3; i += 1) entries = pushEntry(entries, `entry ${i}`);
  assert.equal(entries.length, LEDGER_LIMIT);
  assert.equal(entries[0], `entry ${LEDGER_LIMIT * 3 - 1}`, "the most recent decisions are the ones kept");
});

test("an empty or whitespace entry is not an entry", () => {
  const before = ["one decision"];
  assert.deepEqual(pushEntry(before, ""), before);
  assert.deepEqual(pushEntry(before, "   \n "), before);
  assert.deepEqual(pushEntry(before, undefined), before);
});

test("what is written is what is read back", () => {
  const store = memoryStore();
  const entries = pushEntry([], "10:00:01 — “Fractions” → Not now");
  writeLedger(store, "adapt:ledger-decisions", entries);
  assert.deepEqual(readLedger(store, "adapt:ledger-decisions"), entries);
});

test("a ledger that was never written reads as empty, not as an error", () => {
  assert.deepEqual(readLedger(memoryStore(), "adapt:ledger-decisions"), []);
  assert.deepEqual(readLedger(null, "adapt:ledger-decisions"), []);
  assert.deepEqual(readLedger(undefined, "adapt:ledger-decisions"), []);
});

test("corrupt storage reads as an empty ledger", () => {
  const store = memoryStore({ "adapt:ledger-decisions": "{not json" });
  assert.deepEqual(readLedger(store, "adapt:ledger-decisions"), []);
  const wrongShape = memoryStore({ "adapt:ledger-decisions": '{"a":1}' });
  assert.deepEqual(readLedger(wrongShape, "adapt:ledger-decisions"), []);
  const mixed = memoryStore({ "adapt:ledger-decisions": '["kept", 3, null, "also kept"]' });
  assert.deepEqual(readLedger(mixed, "adapt:ledger-decisions"), ["kept", "also kept"]);
});

test("a browser that refuses storage loses the ledger and nothing else", () => {
  const hostile = {
    getItem() {
      throw new Error("denied");
    },
    setItem() {
      throw new Error("quota");
    },
  };
  assert.deepEqual(readLedger(hostile, "adapt:ledger-decisions"), []);
  assert.doesNotThrow(() => writeLedger(hostile, "adapt:ledger-decisions", ["a decision"]));
});

test("entries that survive a reload are the entries a learner can read back", () => {
  // The heading on screen says "this session", so the list has to outlive the component that
  // wrote it: write, then read from a *fresh* view of the same storage.
  const store = memoryStore();
  writeLedger(store, "adapt:ledger-reviews", pushEntry([], "10:02:00 — Ratios rated “recalled” → next in 4 days"));
  const afterNavigation = readLedger(store, "adapt:ledger-reviews");
  assert.equal(afterNavigation.length, 1);
  assert.match(afterNavigation[0], /rated “recalled”/);
  writeLedger(store, "adapt:ledger-reviews", pushEntry(afterNavigation, "10:09:00 — Fractions rated “forgotten” → next in 10 minutes"));
  assert.equal(readLedger(store, "adapt:ledger-reviews").length, 2);
});
