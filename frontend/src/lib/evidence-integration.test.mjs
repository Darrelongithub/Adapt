/** Real receipt SSR and mock routing; deliberately not a browser interaction claim. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire, Module } from "node:module";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

function load(relative, overrides = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const realRequire = createRequire(file);
  const compiled = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const loaded = new Module(file);
  loaded.filename = file;
  loaded.require = name => name in overrides ? overrides[name] : realRequire(name);
  loaded._compile(compiled, file);
  return loaded.exports;
}

const wrapper = ({ children }) => React.createElement("span", null, children);
const { ReceiptView } = load("../components/question.tsx", {
  "./ui": {
    Notice: wrapper, Tag: wrapper, Band: () => null,
    Btn: ({ href, children }) => React.createElement("a", { href }, children),
  },
  "./evidence": { EvidenceBadge: ({ n }) => React.createElement("span", null, `n=${n}`) },
  "./teaching": {}, "@/lib/api": {}, "@/lib/drafts": {}, "@/lib/a11y/useRovingTabIndex": {},
});
const base = {
  attempt_id: "receipt with spaces", correct: false, grading_method: "rules", feedback: "Not yet",
  mastery: { p_know: 0.2, band: "developing", observations: 1 },
};
const render = next_step => renderToStaticMarkup(React.createElement(ReceiptView, {
  receipt: { ...base, next_step },
}));

test("failed receipt links directly to its resumable recovery", () => {
  const html = render({ stage: "prerequisite", loop_id: base.attempt_id, note: "Check the prerequisite" });
  assert.match(html, /href="\/evidence\?attempt=receipt%20with%20spaces"/);
  assert.match(html, /Continue the recovery check/);
  assert.match(html, /Check the prerequisite/);
});

test("missing checked content is a notice, not a made-up recovery", () => {
  const html = render({ stage: "unavailable", loop_id: base.attempt_id, note: "No checked matched B" });
  assert.match(html, /No checked matched B/);
  assert.match(html, /No method comparison was recorded/);
  assert.doesNotMatch(html, /Continue the recovery check|\?attempt=/);
});

test("the fatigue break suggestion remains visible on the receipt", () => {
  const html = render({ stage: "matched_b", loop_id: base.attempt_id, note: "Fatigue check", suggest_break: true });
  assert.match(html, /short break/);
});

const { mockLookup } = load("./mock.ts");
test("demo evidence contains no invented measurements", () => {
  const result = mockLookup("GET", "/api/v1/evidence");
  assert.equal(result.found, true);
  assert.deepEqual(result.body.methods, []);
  assert.deepEqual(result.body.strategies, []);
  assert.equal(result.body.preference_vs_performance, null);
  assert.match(result.body.note, /no measured method outcomes/);
});

test("demo recovery refuses starts, resumes and answers honestly", () => {
  for (const [method, path] of [
    ["POST", "/api/v1/evidence/failure"],
    ["GET", "/api/v1/evidence/failure/test-receipt"],
    ["POST", "/api/v1/evidence/failure/test-receipt/answer"],
  ]) {
    const result = mockLookup(method, path);
    assert.equal(result.found, true);
    assert.equal(result.status, 422);
    assert.match(result.error.message, /unavailable in demo mode/);
  }
});
