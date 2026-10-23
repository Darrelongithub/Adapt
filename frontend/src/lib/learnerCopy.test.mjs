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

test("the onboarding exemption is gone: the old copy now fails on every route", () => {
  // Phase A asked Phase D to replace this sentence and then delete the allowance, so that
  // a regression cannot hide behind it. Both halves are asserted here: the text is caught
  // anywhere, and the sentence is not in the page any more.
  const deferred = "Let a model read a summary when it would help";
  assert.ok(bannedHits(deferred, "/onboarding").includes("model"));
  assert.ok(bannedHits(deferred, "/settings").includes("model"));
  const onboarding = readFileSync(new URL("../app/onboarding/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(onboarding, /Let a model read a summary/);
  assert.ok(bannedHits("Lessons: simple mode").length === 0);
  assert.ok(bannedHits("GET /privacy").includes("get-path"));
  assert.ok(bannedHits("protection: not reported").length === 0);
});

test("the setup screen asks once: no developer panel and no duplicated question", () => {
  const onboarding = readFileSync(new URL("../app/onboarding/page.tsx", import.meta.url), "utf8");
  // The "Allowed preference keys — from the API" panel and its paragraph are gone, and
  // nothing prints a raw key tag at the learner.
  assert.doesNotMatch(onboarding, /Allowed preference keys/);
  assert.doesNotMatch(onboarding, /from the API/);
  assert.doesNotMatch(onboarding, /ignored_keys/);
  assert.doesNotMatch(onboarding, /no API key/i);
  // The thirteen mind-check questions are asked on the mind check, not here: the setup
  // screen must not carry their fields, or the learner is asked twice.
  assert.doesNotMatch(onboarding, /max_explanation_words/);
  assert.doesNotMatch(onboarding, /push_me/);
  assert.doesNotMatch(onboarding, /session_minutes/);
  assert.doesNotMatch(onboarding, /Which language/);
  // What it does keep: the account-level settings.
  assert.match(onboarding, /weekly_minutes/);
  assert.match(onboarding, /encourage/);
  assert.match(onboarding, /Allow a written-for-you summary when it would help/);
});

test("the journey shell and the sidebar both lead to the one path", () => {
  const chrome = readFileSync(new URL("../components/chrome.tsx", import.meta.url), "utf8");
  const journey = readFileSync(new URL("../app/journey/page.tsx", import.meta.url), "utf8");
  const home = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(chrome, /href: "\/journey", label: "Your path"/);
  assert.match(chrome, /label: "Mind check"/, "a returning learner keeps the mind check as its own destination");
  assert.match(journey, /useJourney/);
  assert.match(journey, /journey\/state|useJourney/);
  // Round 3 (Track T): a new learner is routed into the journey stage
  // flow rather than dropped into the mind check.  The journey page is
  // now the one-screen flow that runs each stage in turn.
  const gate = readFileSync(new URL("../components/account-gate.tsx", import.meta.url), "utf8");
  const dashboard = readFileSync(new URL("../components/home-dashboard.tsx", import.meta.url), "utf8");
  assert.match(gate, /router\.replace\(LEARNER_LANDING\)/);
  const landing = readFileSync(new URL("./landing.ts", import.meta.url), "utf8");
  assert.match(landing, /LEARNER_LANDING = "\/journey"/);
  // Round 6 (Track E): the SessionGate landing gate is GONE — it rendered
  // "Opening your path…" over every screen until the URL reached /journey and
  // looped with the account gate. If the server says who this browser is, the
  // children render. This assertion is the redirect-loop regression test.
  assert.doesNotMatch(chrome, /setLanding\(LEARNER_LANDING\)/, "the landing gate must not come back");
  assert.doesNotMatch(chrome, /Opening your path…/);
  assert.match(home, /HomeDashboard/);
  assert.match(dashboard, /href="\/journey/);
  assert.doesNotMatch(home, /onboarding\?\$\{carry\}/);
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

test("a signed-out session waits for an explicit entry instead of creating a guest", () => {
  const chrome = readFileSync(new URL("../components/chrome.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(chrome, /api<Me>\("students"/);
  assert.doesNotMatch(chrome, /as_guest:\s*true/);
  assert.match(chrome, /<AccountGate \/>/);
});

test("analytics is an optional entry choice, not a condition for using the app", () => {
  const gate = readFileSync(new URL("../components/account-gate.tsx", import.meta.url), "utf8");
  assert.match(gate, /Optional/);
  assert.match(gate, /Save choice and continue/);
  assert.doesNotMatch(gate, /disabled=\{!consent\}/);
  assert.doesNotMatch(gate, /busy \|\| !consent/);
  assert.doesNotMatch(gate, /consent_analytics:\s*true/);
});
