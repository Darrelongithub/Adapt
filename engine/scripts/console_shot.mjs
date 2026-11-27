#!/usr/bin/env node
/* Render the console to an image, without a browser.
 *
 * The sandbox has no headless browser and CI cannot download one, so a "real"
 * screenshot is not available in this environment. What this does instead is
 * honest: it runs the *same renderer the browser runs*, walks the tree it
 * produces, and lays the result out as SVG — the page's own text, in the page's
 * own order, from the state the caller supplies. `convert` turns the SVG into a
 * PNG when ImageMagick is installed.
 *
 * What it is not: a pixel-accurate bitmap of the browser rendering. `KNOWN_GAPS`
 * says so, and the test asserts the text, not the pixels.
 *
 *   node scripts/console_shot.mjs --state empty.json --out empty.svg --json empty.json
 *
 * The JSON sidecar is the canonical layout: `scripts/console_shot.py` draws the
 * same blocks to PNG with Pillow, and the test asserts the text from the JSON
 * rather than from pixels, so a font change cannot fail a correctness test.
 */

import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

function arg(name) {
  const i = process.argv.indexOf(name);
  return i === -1 ? null : process.argv[i + 1];
}

const here = new URL(".", import.meta.url).pathname;
const appPath = join(here, "..", "src", "adapt_engine", "console", "app.js");
const scratch = join(mkdtempSync(join(tmpdir(), "adapt-shot-")), "app.mjs");
writeFileSync(scratch, readFileSync(appPath, "utf8"));
const { renderConsole, textOf } = await import(pathToFileURL(scratch).href);

const state = JSON.parse(readFileSync(arg("--state"), "utf8"));
const root = renderConsole(state);

const WIDTH = 1180;
const LEFT = 32;
const WRAP = 96;

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function wrap(text, width) {
  const words = String(text).split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";
  for (const word of words) {
    if ((line + " " + word).trim().length > width) {
      if (line) lines.push(line);
      line = word;
    } else {
      line = (line + " " + word).trim();
    }
  }
  if (line) lines.push(line);
  return lines.length ? lines : [""];
}

const blocks = [];
function walk(node, depth) {
  if (typeof node === "string") return;
  const tag = node.tag;
  if (tag === "h2") blocks.push({ kind: "title", text: textOf(node) });
  else if (tag === "div" && (node.attrs.class || "").includes("row")) {
    const label = node.children.find((c) => c.attrs && (c.attrs.class || "").includes("label"));
    const value = node.children.find((c) => c !== label);
    blocks.push({
      kind: "row",
      label: label ? textOf(label) : "",
      text: value ? textOf(value) : "",
    });
  } else if (tag === "p") blocks.push({ kind: "sentence", text: textOf(node) });
  else if (tag === "tr") {
    const cells = node.children.filter((c) => c.tag === "td" || c.tag === "th");
    blocks.push({ kind: "table", text: cells.map((c) => textOf(c)).join("   |   ") });
    return;
  } else if (tag === "thead") {
    const cells = node.children
      .flatMap((row) => row.children || [])
      .filter((c) => c.tag === "th");
    if (cells.length) {
      blocks.push({ kind: "header", text: cells.map((c) => textOf(c)).join("   |   ") });
    }
    return;
  }
  for (const child of node.children || []) walk(child, depth + 1);
}
walk(root, 0);

const out = [];
let y = 0;
const line = (text, size, weight, fill, x, gap) => {
  out.push(
    `<text x="${x}" y="${y}" font-family="DejaVu Sans, Helvetica, Arial, sans-serif" ` +
      `font-size="${size}" font-weight="${weight}" fill="${fill}">${escapeXml(text)}</text>`,
  );
  y += gap;
};

y = 44;
line("Adapt console", 26, 700, "#111827", LEFT, 26);
line(
  "rendered from the console's own renderer; every value traces to an endpoint",
  13,
  400,
  "#6b7280",
  LEFT,
  30,
);

for (const block of blocks) {
  if (block.kind === "title") {
    y += 18;
    out.push(
      `<rect x="${LEFT - 12}" y="${y - 22}" width="${WIDTH - 2 * LEFT + 24}" height="1" fill="#e5e7eb"/>`,
    );
    line(block.text, 17, 700, "#111827", LEFT, 24);
  } else if (block.kind === "row") {
    line(block.label, 13, 400, "#6b7280", LEFT, 20);
    line(block.text, 15, 600, "#111827", LEFT, 24);
  } else if (block.kind === "sentence") {
    for (const part of wrap(block.text, WRAP)) line(part, 12, 400, "#4b5563", LEFT, 18);
    y += 4;
  } else if (block.kind === "header") {
    line(block.text, 12, 700, "#111827", LEFT, 20);
    y += 2;
  } else if (block.kind === "table") {
    for (const part of wrap(block.text, WRAP + 10)) line(part, 12, 500, "#374151", LEFT, 18);
    y += 4;
  }
}

const height = y + 24;
const svg = [
  `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${height}" ` +
    `viewBox="0 0 ${WIDTH} ${height}">`,
  `<rect width="${WIDTH}" height="${height}" fill="#ffffff"/>`,
  ...out,
  "</svg>",
].join("\n");

writeFileSync(arg("--out"), svg + "\n");
if (arg("--json")) {
  writeFileSync(arg("--json"), JSON.stringify({ width: WIDTH, blocks }, null, 2));
}
console.log(JSON.stringify({ ok: true, out: arg("--out"), blocks: blocks.length, height }));
