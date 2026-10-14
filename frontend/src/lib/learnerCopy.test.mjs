import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { bannedHits } from "./bannedPhrases.mjs";
import { contrastFoot, evidenceMark, showPrivacyUnread } from "./learnerCopy.ts";

test("stated items do not show n=0", () => {
  assert.equal(evidenceMark("stated", 0, []), "stated");
  assert.equal(evidenceMark("stated", 0, ["ev_1"], { includeSource: true }), "stated");
  assert.equal(evidenceMark("measured", 0, []), "(n=0)");
  assert.match(evidenceMark("measured", 4, ["ev_1"], { includeSource: true }), /measured, n=4/);
});

test("demo profiles do not mention a profile version", () => {
  const foot = contrastFoot({ modelVersion: 0, evidenceN: 0 });
  assert.match(foot, /demo profile/i);
  assert.match(foot, /Lessons: simple mode/);
  assert.equal(bannedHits(foot).length, 0, bannedHits(foot).join(","));
  assert.doesNotMatch(foot, /profile version/i);
  assert.doesNotMatch(foot, /n=0/);
  assert.doesNotMatch(foot, /piece/);
});

test("contrast page no longer prints the old footer or the raw mode note", () => {
  const src = readFileSync(new URL("../app/contrast/page.tsx", import.meta.url), "utf8");
  assert.match(src, /contrastFoot/);
  assert.match(src, /evidenceMark/);
  assert.doesNotMatch(src, /planned from profile version/);
  assert.doesNotMatch(src, /\{panel\.mode_note\}/);
  assert.doesNotMatch(src, /\(err as Error\)\.message/);
});

test("onboarding deferred copy is ignored only on that route", () => {
  const deferred = "Let a model read a summary when it would help";
  assert.deepEqual(bannedHits(deferred, "/onboarding"), []);
  assert.ok(bannedHits(deferred, "/settings").includes("model"));
  assert.ok(bannedHits("Lessons: simple mode").length === 0);
  assert.ok(bannedHits("GET /privacy").includes("get-path"));
  assert.ok(bannedHits("protection: not reported").length === 0);
});

test("status chip and home copy no longer use the old developer sentences", () => {
  const chrome = readFileSync(new URL("../components/chrome.tsx", import.meta.url), "utf8");
  const home = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  const settings = readFileSync(new URL("../app/settings/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(chrome, /protection: not reported/);
  assert.doesNotMatch(home, /GET \/privacy/);
  assert.doesNotMatch(home, /the same endpoint the server/);
  assert.doesNotMatch(home, /sent to a\s+model/s);
  assert.doesNotMatch(settings, /Allow a model summary/);
  assert.doesNotMatch(settings, />GET \/privacy/);
  assert.doesNotMatch(settings, /to the model/);
});

test("a signed-out privacy list is not called unread while it is loading or after it arrives", () => {
  assert.equal(showPrivacyUnread(true, 0), false);
  assert.equal(showPrivacyUnread(false, 5), false);
  assert.equal(showPrivacyUnread(false, 0), true);
  const home = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  // Consent moved out of Home into the global entry card. That card uses a readable
  // fallback while its privacy request loads, never an inaccurate "could not read" state.
  assert.match(home, /HomeDashboard/);
  const gate = readFileSync(new URL("../components/account-gate.tsx", import.meta.url), "utf8");
  assert.match(gate, /privacy\.data\?\.telemetry\?\.note \?\?/);
  assert.doesNotMatch(gate, /could not be read just now/);
});
