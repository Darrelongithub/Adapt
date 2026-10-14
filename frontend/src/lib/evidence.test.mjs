/** Rendering-only regressions: use real React SSR, no browser or new dependencies.
 * The UI Tag wrapper is isolated; all evidence decisions and copy come from the actual TSX.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire, Module } from "node:module";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const file = fileURLToPath(new URL("../components/evidence.tsx", import.meta.url));
const realRequire = createRequire(file);
const compiled = ts.transpileModule(readFileSync(file, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loaded = new Module(file);
loaded.filename = file;
loaded.require = (name) => name === "./ui"
  ? { Tag: ({ children }) => React.createElement("span", null, children) }
  : realRequire(name);
loaded._compile(compiled, file);
const { EvidenceBadge, MethodReport } = loaded.exports;
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));

test("raw n cannot conceal insufficient weighted evidence in the badge", () => {
  const html = render(EvidenceBadge, { n: 10, effectiveN: 4 });
  assert.match(html, /n=10/);
  assert.match(html, /early signal/);
});

test("a method row prints range and observational source weight next to its number", () => {
  const html = render(MethodReport, { rows: [{
    method: "visual", label: "Visual", n: 10, effective_n: 4, residual: 0.1,
    actual: 0.6, expected: 0.5, ci: [-0.2, 0.4],
    // Even legacy server copy that says signal must not override the weighted count.
    evidence: "signal", sources: [{ source: "observational", weight: 0.4, n: 10 }],
  }] });
  assert.match(html, /early signal/);
  assert.match(html, /weighted n=4/);
  assert.match(html, /range -20 to 40 points/);
  assert.match(html, /observational/);
  assert.match(html, /weight 0\.4/);
});

test("adequately measured controlled data is not labelled early", () => {
  const html = render(MethodReport, { rows: [{
    method: "explanation", n: 12, effective_n: 12, evidence: "signal",
    sources: [{ source: "controlled", weight: 1, n: 12 }],
  }] });
  assert.doesNotMatch(html, /early signal/);
  assert.match(html, /controlled/);
  assert.match(html, /weight 1/);
});

test("a pilot label remains visible even with adequate effective n", () => {
  const html = render(MethodReport, { rows: [{
    method: "visual", n: 30, effective_n: 30, pilot: true, n_pilot: 30,
    pilot_label: "pilot data — not evidence of efficacy",
  }] });
  assert.match(html, /pilot data/);
  assert.match(html, /not evidence of efficacy/);
});

test("missing measurements are unavailable, not invented zero-percent results", () => {
  const html = render(MethodReport, { rows: [{ method: "visual", n: 10, effective_n: 4 }] });
  assert.match(html, /Estimate unavailable/);
  assert.doesNotMatch(html, /expected 0%|actual 0%|0 points against expectation/);
});
