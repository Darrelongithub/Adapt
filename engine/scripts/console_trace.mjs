#!/usr/bin/env node
/* Traceability harness for the console.
 *
 * Renders `app.js` — the same module the browser loads — against two states that
 * differ in every leaf, and asserts that every value the console shows changes
 * with the data. A hardcoded number cannot survive that: it renders the same text
 * for both states, and this script fails.
 *
 * It also checks the other half of the contract: every `data-metric` id the
 * renderer can emit is declared in `data-map.json`, so every number on screen
 * names the endpoint it came from.
 *
 * Usage:
 *   node scripts/console_trace.mjs --state s1.json --mutated s2.json --map data-map.json
 *   node scripts/console_trace.mjs --state null.json --expect-null
 *
 * Exit 0 on success, 1 on the first broken rule, with the failing id named.
 */

import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

function arg(name, fallback = null) {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
}

const here = new URL(".", import.meta.url).pathname;
const appPath = join(here, "..", "src", "adapt_engine", "console", "app.js");

// Node treats a bare `.js` file as CommonJS unless a package.json says otherwise.
// Copying to `.mjs` keeps the browser file free of Node-only packaging.
const source = readFileSync(appPath, "utf8");
const scratch = join(mkdtempSync(join(tmpdir(), "adapt-console-")), "app.mjs");
writeFileSync(scratch, source);
const { renderConsole, metricNodes } = await import(pathToFileURL(scratch).href);

function load(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

// `*` in a declared id is a wildcard for one row identity, and row identities
// contain dots (a skill is `skill.fractions`), so the match is a regex on the
// whole id rather than a segment comparison.
function declaredBy(id) {
  for (const key of Object.keys(map)) {
    const pattern = "^" + key.replaceAll(".", "\\.").replaceAll("*", ".+") + "$";
    if (new RegExp(pattern).test(id)) return key;
  }
  return null;
}

const statePath = arg("--state");
if (!statePath) {
  console.error("missing --state");
  process.exit(2);
}
const state = load(statePath);
const rendered = metricNodes(renderConsole(state));

if (process.argv.includes("--expect-null")) {
  const byId = new Map(rendered.map((m) => [m.metric, m.text]));
  const rate = byId.get("overview.abstention.rate");
  if (rate !== "no data") {
    console.error(`null state: rate rendered as ${JSON.stringify(rate)}, expected "no data"`);
    process.exit(1);
  }
  if (byId.get("overview.served.rate") !== "no data") {
    console.error("null state: served rate must also read no data");
    process.exit(1);
  }
  const sentence = byId.get("overview.denominator.sentence") || "";
  if (!sentence.includes("No decisions in this window")) {
    console.error(`null state: sentence is ${JSON.stringify(sentence)}`);
    process.exit(1);
  }
  if (rendered.some((m) => /0\.0%/.test(m.text) && m.metric.startsWith("overview.abstention"))) {
    console.error("null state: a 0.0% leaked into the abstention block");
    process.exit(1);
  }
  console.log(JSON.stringify({ ok: true, mode: "null", rendered }));
  process.exit(0);
}

const mapPath = arg("--map", join(here, "..", "src", "adapt_engine", "console", "data-map.json"));
const map = load(mapPath).metrics;
const mutated = metricNodes(renderConsole(load(arg("--mutated"))));

const ids = new Set(rendered.map((m) => m.metric));
for (const id of ids) {
  if (!declaredBy(id)) {
    console.error(`untraceable value: ${id} is rendered but not declared in data-map.json`);
    process.exit(1);
  }
}

function group(nodes) {
  const out = new Map();
  for (const { metric, text } of nodes) {
    const key = declaredBy(metric) || metric;
    if (!out.has(key)) out.set(key, []);
    out.get(key).push(text);
  }
  for (const list of out.values()) list.sort();
  return out;
}

// The rule: the multiset of texts for each declared id must move when the data
// moves. A literal renders the same texts for both states; a value that is only
// ever "0" because a list is empty also renders the same, which is why the
// mutation duplicates rows rather than only editing them.
const a = group(rendered);
const b = group(mutated);
for (const key of new Set([...a.keys(), ...b.keys()])) {
  const left = JSON.stringify(a.get(key) || []);
  const right = JSON.stringify(b.get(key) || []);
  if (left === right) {
    console.error(`hardcoded value: ${key} rendered ${left} for both states`);
    process.exit(1);
  }
}

const changed = rendered.length;
console.log(JSON.stringify({ ok: true, mode: "trace", values: changed, ids: [...ids].sort() }));
