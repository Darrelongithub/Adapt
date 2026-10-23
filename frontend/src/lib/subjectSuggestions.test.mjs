import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";


test("subject chips offer only authored lesson topics", () => {
  const journey = readFileSync(new URL("../app/journey/page.tsx", import.meta.url), "utf8");
  const match = journey.match(/const SUGGESTIONS = \[([^\]]+)\]/);
  assert.ok(match);
  const suggestions = [...match[1].matchAll(/"([^"]+)"/g)].map(row => row[1]);
  assert.deepEqual(suggestions, ["Discrete structures", "Kiswahili", "Chemistry", "Biology"]);
  for (const unsupported of ["Geometry", "Maths", "Law", "Coding"]) assert.equal(suggestions.includes(unsupported), false);
});
