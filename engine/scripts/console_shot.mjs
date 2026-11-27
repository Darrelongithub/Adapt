#!/usr/bin/env node
/* Render the console to an image, without a browser — and count its accents.
 *
 * The sandbox has no headless browser and CI cannot download one, so a "real"
 * screenshot is not available in this environment. What this does instead is
 * honest: it runs the *same renderer the browser runs*, resolves each element's
 * style against `console/styles.css` with a small cascade, and lays the result
 * out as SVG. `scripts/console_shot.py` draws the same block model to PNG.
 *
 * Three jobs, in this order:
 *
 *   1. **Read the palette from the stylesheet.** `CONSOLE_STYLE.md` §6 gate 4: the
 *      screenshot scripts must not carry their own copy of the palette. This file
 *      parses `:root` from `styles.css`; there is no hex literal below.
 *   2. **Render.** Every block carries the colour, size, weight and style the
 *      stylesheet gives it, so "no data" is mustard 700 italic in the picture for
 *      the same reason it is on screen.
 *   3. **Count the accent budget.** Gate 3: no screen may carry more than three
 *      elements coloured `--accent`, `--accent-fill` or `--accent-ink`. The count
 *      is per screen and it exits non-zero above three.
 *
 *   node scripts/console_shot.mjs --state empty.json --out empty.svg --json empty.json
 */

import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

function arg(name, fallback = null) {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
}

const here = new URL(".", import.meta.url).pathname;
const consoleDir = join(here, "..", "src", "adapt_engine", "console");
const stylesPath = arg("--styles", join(consoleDir, "styles.css"));
const appPath = join(consoleDir, "app.js");
const MAX_ACCENTS = Number(arg("--max-accent", "3"));

const cssText = readFileSync(stylesPath, "utf8");
const { tokens, rules } = parseStyles(cssText);

const scratch = join(mkdtempSync(join(tmpdir(), "adapt-shot-")), "app.mjs");
writeFileSync(scratch, readFileSync(appPath, "utf8"));
const { renderConsole, textOf } = await import(pathToFileURL(scratch).href);

/* ------------------------------------------------------------- the cascade */

/** `:root` declarations, plus every ordinary rule, with comments stripped. */
function parseStyles(source) {
  const css = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const tokens = {};
  const rootBlock = /:root\s*\{([^}]*)\}/g;
  let match;
  while ((match = rootBlock.exec(css))) {
    for (const [name, value] of declarations(match[1])) tokens[name] = value;
  }
  const rules = [];
  const body = css.replace(rootBlock, "");
  const rule = /([^{}]+)\{([^}]*)\}/g;
  while ((match = rule.exec(body))) {
    const selectors = match[1]
      .split(",")
      .map((selector) => selector.trim())
      .filter(Boolean);
    const decls = Object.fromEntries(declarations(match[2]));
    if (selectors.length) rules.push({ selectors, decls });
  }
  return { tokens, rules };
}

function declarations(block) {
  const out = [];
  for (const raw of block.replace(/\/\*[\s\S]*?\*\//g, "").split(";")) {
    const at = raw.indexOf(":");
    if (at > 0) out.push([raw.slice(0, at).trim(), raw.slice(at + 1).trim()]);
  }
  return out;
}

/** `var(--x)` resolved against `:root`; the fallback is left alone if unset. */
function resolveValue(value, seen = 0) {
  if (typeof value !== "string" || seen > 8) return value;
  return value.replace(/var\(\s*(--[A-Za-z0-9-]+)\s*\)/g, (whole, name) => {
    const token = tokens[name];
    return token === undefined ? whole : resolveValue(token, seen + 1);
  });
}

function matchCompound(element, compound) {
  const bare = compound.replace(/::?[a-z-]+(\([^)]*\))?/g, "").trim();
  // A selector with no tag and no class is not universal — it is a parse
  // accident (a number or a stray word), and matching everything with it is
  // exactly how a stylesheet's prose ends up styling the page.
  if (!/^[a-z*]|^\./.test(bare)) return false;
  const tag = /^[a-z][a-z0-9]*/.exec(bare);
  if (tag && element.tag !== tag[0]) return false;
  const classes = [...bare.matchAll(/\.([A-Za-z0-9_-]+)/g)].map((m) => m[1]);
  const own = String(element.attrs.class || "")
    .split(/\s+/)
    .filter(Boolean);
  return classes.every((name) => own.includes(name));
}

/** Right-to-left match of a descendant chain against [root … element]. */
function matches(chain, selector) {
  const parts = selector.split(/\s+/).filter(Boolean);
  if (!parts.length) return false;
  if (!matchCompound(chain[chain.length - 1], parts[parts.length - 1])) return false;
  let part = parts.length - 2;
  let node = chain.length - 2;
  while (part >= 0 && node >= 0) {
    if (matchCompound(chain[node], parts[part])) part -= 1;
    node -= 1;
  }
  return part < 0;
}

function specificity(selector) {
  const classes = (selector.match(/\.[A-Za-z0-9_-]+/g) || []).length;
  const tags = (selector.replace(/\.[A-Za-z0-9_-]+/g, "").match(/[a-z]+/g) || []).length;
  return classes * 10 + tags;
}

const ORDERED = rules
  .flatMap((rule, index) =>
    rule.selectors.map((selector) => ({
      selector,
      decls: rule.decls,
      weight: specificity(selector),
      index,
    })),
  )
  .sort((a, b) => a.weight - b.weight || a.index - b.index);

/** Declarations for an element, given its ancestor chain (root … element).
 *
 * Only the properties the block model needs, and only the ones CSS inherits —
 * plus the three that paint (`background`, `border-left`, `border-bottom`),
 * which do not. `parent` is the parent's computed style, so a bare `<span>`
 * inside a card is `--ink-body` 13px sans for the same reason it is on screen.
 */
function styleOf(chain, parent = null) {
  const merged = {};
  for (const rule of ORDERED) {
    if (matches(chain, rule.selector)) Object.assign(merged, rule.decls);
  }
  const value = (name) => {
    const raw = merged[name];
    return raw === undefined ? null : resolveValue(raw);
  };
  const shorthand = (name, part) => {
    const raw = merged[name];
    if (raw === undefined) return null;
    const pieces = resolveValue(raw).split(/\s+/).filter(Boolean);
    return part === "colour" ? pieces[pieces.length - 1] : pieces[0];
  };
  const family = value("font-family");
  const own = {
    color: value("color"),
    background: value("background-color") || value("background"),
    borderLeft:
      value("border-left-color") || (merged["border-left"] ? shorthand("border-left", "colour") : null),
    borderBottom: merged["border-bottom"] ? shorthand("border-bottom", "colour") : null,
    size: parseFloat(value("font-size") || "") || null,
    weight: Number(value("font-weight") || "") || null,
    italic: value("font-style") === null ? null : value("font-style") === "italic",
    family: family === null ? null : /serif/.test(family) && !/sans-serif/.test(family) ? "serif" : "sans",
  };
  const base = parent || {
    color: tokens["--ink"],
    background: null,
    borderLeft: null,
    borderBottom: null,
    size: 13,
    weight: 400,
    italic: false,
    family: "sans",
  };
  return {
    color: own.color ?? base.color,
    background: own.background,
    borderLeft: own.borderLeft,
    borderBottom: own.borderBottom,
    size: own.size ?? base.size,
    weight: own.weight ?? base.weight,
    italic: own.italic ?? base.italic,
    family: own.family ?? base.family,
  };
}

/* ------------------------------------------------------------- the accents */

const ACCENT_TOKENS = ["--accent", "--accent-fill", "--accent-ink"].map((name) =>
  String(tokens[name] || "").toUpperCase(),
);

const isAccent = (value) => Boolean(value) && ACCENT_TOKENS.includes(String(value).toUpperCase());

/** Elements painted in an accent token — §1's budget is three per screen. */
function countAccents(node, parent = null, chain = [], count = { n: 0 }) {
  if (typeof node === "string" || !node || !node.tag) return count;
  const next = [...chain, node];
  const style = styleOf(next, parent);
  if (isAccent(style.color) || isAccent(style.background) || isAccent(style.borderLeft)) {
    count.n += 1;
  }
  for (const child of node.children || []) countAccents(child, style, next, count);
  return count;
}

/* ------------------------------------------------------------ the block model */

const state = JSON.parse(readFileSync(arg("--state"), "utf8"));
const root = renderConsole(state);

const WIDTH = 1180;
const LEFT = 48;
const WRAP = 92;

const escapeXml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

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

const screenStack = []; // where a block goes while walking
const screenIds = []; // every screen that was walked, in document order
const blocks = [];

function push(block) {
  blocks.push(block);
  if (screenStack.length) screenStack[screenStack.length - 1].blocks.push(block);
}

function walk(node, chain, parent = null) {
  if (typeof node === "string" || !node || !node.tag) return;
  const next = [...chain, node];
  const style = styleOf(next, parent);
  const tag = node.tag;

  const isScreen = tag === "div" && (node.attrs.class || "").includes("screen");
  if (isScreen) screenStack.push({ id: node.attrs.id || "screen", blocks: [] });

  if (tag === "h2") {
    push({ kind: "title", text: textOf(node), style });
  } else if (tag === "div" && (node.attrs.class || "").includes("row")) {
    const label = node.children.find((c) => c.attrs && (c.attrs.class || "").includes("label"));
    const value = node.children.find((c) => c !== label);
    push({
      kind: "row",
      label: label ? textOf(label) : "",
      labelStyle: label ? styleOf([...next, label], style) : style,
      text: value ? textOf(value) : "",
      style: value ? styleOf([...next, value], style) : style,
    });
  } else if (tag === "p") {
    push({ kind: "sentence", text: textOf(node), style });
  } else if (tag === "table") {
    // A cell is a run of styled text, so the picture can show a chip as a chip:
    // the accent budget counts elements, and an artifact that draws a chip as
    // plain text would under-report the very thing gate 3 measures.
    const runs = (element) => {
      const collected = [];
      const collect = (node, chain, parent) => {
        if (typeof node === "string" || !node || !node.tag) return;
        const inner = [...chain, node];
        const own = styleOf(inner, parent);
        const children = node.children || [];
        if (!children.some((child) => child && child.tag)) {
          collected.push({ text: textOf(node), style: own });
          return;
        }
        for (const child of children) collect(child, inner, own);
      };
      collect(element, next, style);
      return collected;
    };
    const cell = (element) => ({
      text: textOf(element),
      style: styleOf([...next, element], style),
      runs: runs(element),
    });
    const headRow = (node.children || []).find((child) => child.tag === "thead");
    const header = headRow
      ? (headRow.children || []).flatMap((row) => row.children || []).map(cell)
      : [];
    const rows = (node.children || [])
      .filter((child) => child.tag === "tbody")
      .flatMap((body) => body.children || [])
      .filter((row) => row.tag === "tr")
      .map((row) => (row.children || []).map(cell));
    push({
      kind: "table",
      text: [...(header.length ? [header] : []), ...rows]
        .map((line) => line.map((item) => item.text).join("   |   "))
        .join("\n"),
      header,
      rows,
      style,
    });
    return;
  }

  for (const child of node.children || []) walk(child, next, style);
  if (isScreen) screenIds.push(screenStack.pop());
}

walk(root, []);

const accentCounts = {};
for (const screen of screenIds) {
  accentCounts[screen.id] = countAccents(screenRoot(root, screen.id)).n;
}

function screenRoot(node, id) {
  if (typeof node === "string" || !node || !node.tag) return null;
  if (node.attrs && node.attrs.id === id) return node;
  for (const child of node.children || []) {
    const found = screenRoot(child, id);
    if (found) return found;
  }
  return null;
}

const overBudget = Object.entries(accentCounts).filter(([, n]) => n > MAX_ACCENTS);

/* ------------------------------------------------------------------- the SVG */

const out = [];
let y = 56;

function textLine(text, style, x, gap) {
  out.push(
    `<text x="${x}" y="${y}" font-family="${style.family === "serif" ? "DejaVu Serif, Georgia, serif" : "DejaVu Sans, Helvetica, Arial, sans-serif"}" ` +
      `font-size="${style.size}" font-weight="${style.weight}"` +
      `${style.italic ? ' font-style="italic"' : ""} fill="${style.color}">${escapeXml(text)}</text>`,
  );
  y += gap;
}

const band = (from, height, fill) =>
  out.push(`<rect x="0" y="${from}" width="${WIDTH}" height="${height}" fill="${fill}"/>`);

const HEADING = {
  title: "Adapt console",
  subtitle: "rendered from the console's own renderer; every value traces to an endpoint",
};
const h1 = { family: "serif", size: 26, weight: 700, color: tokens["--ink"] };
const sub = { family: "sans", size: 13, weight: 400, color: tokens["--ink-muted"] };

const startY = y;
y += 4;
textLine(HEADING.title, h1, LEFT, 30);
out.push(
  `<rect x="${LEFT}" y="${y - 18}" width="196" height="2" fill="${tokens["--accent"]}"/>`,
);
y += 10;
textLine(HEADING.subtitle, sub, LEFT, 36);

for (const block of blocks) {
  if (block.kind === "title") {
    y += 14;
    out.push(
      `<rect x="${LEFT - 24}" y="${y - 26}" width="${WIDTH - 2 * (LEFT - 24)}" height="1" fill="${tokens["--rule"]}"/>`,
    );
    textLine(block.text.toUpperCase(), block.style, LEFT, 30);
  } else if (block.kind === "row") {
    textLine(block.label, block.labelStyle, LEFT, 18);
    textLine(block.text, block.style, LEFT + 300, 30);
  } else if (block.kind === "sentence") {
    for (const part of wrap(block.text, WRAP)) textLine(part, block.style, LEFT, 20);
    y += 6;
  } else if (block.kind === "table") {
    const columns = Math.max(block.header.length, ...block.rows.map((row) => row.length), 0);
    const widths = [];
    for (let column = 0; column < columns; column += 1) {
      const texts = [block.header[column], ...block.rows.map((row) => row[column])]
        .filter(Boolean)
        .map((item) => item.text.toUpperCase());
      widths.push(Math.max(...texts.map((text) => text.length * 6.9), 48) + 24);
    }
    const available = WIDTH - 2 * LEFT;
    const total = widths.reduce((sum, width) => sum + width, 0);
    const fitted = total > available ? widths.map((width) => (width * available) / total) : widths;
    const columnX = [];
    let x = LEFT;
    for (const width of fitted) {
      columnX.push(x);
      x += width;
    }

    const tableRow = (cells, lineHeight, fill, upper = false) => {
      if (fill) band(y - 16, lineHeight + 6, fill);
      for (const [index, cell] of cells.entries()) {
        let cursor = columnX[index];
        for (const run of cell.runs.length ? cell.runs : [cell]) {
          const text = upper ? run.text.toUpperCase() : run.text;
          const width = Math.max(2, text.length * (run.style.size || 13) * 0.52) + 8;
          if (run.style.background) {
            out.push(
              `<rect x="${cursor}" y="${y - 12}" width="${width}" height="22" fill="${run.style.background}"/>`,
            );
          }
          if (run.style.borderLeft) {
            out.push(
              `<rect x="${cursor}" y="${y - 12}" width="2" height="22" fill="${run.style.borderLeft}"/>`,
            );
          }
          out.push(
            `<text x="${cursor + (run.style.background ? 8 : 0)}" y="${y}" font-family="${run.style.family === "serif" ? "DejaVu Serif, Georgia, serif" : "DejaVu Sans, Helvetica, Arial, sans-serif"}" ` +
              `font-size="${run.style.size}" font-weight="${run.style.weight}"${run.style.italic ? ' font-style="italic"' : ""} fill="${run.style.color}">` +
              `${escapeXml(text)}</text>`,
          );
          cursor += width + 4;
        }
      }
      y += lineHeight;
    };

    if (block.header.length) tableRow(block.header, 26, tokens["--surface-sunk"], true);
    for (const row of block.rows) {
      y += 10;
      tableRow(row, 22, null);
      out.push(
        `<rect x="${LEFT}" y="${y - 6}" width="${available}" height="1" fill="${tokens["--rule"]}"/>`,
      );
      y += 4;
    }
  }
}

const height = y + 40;
const svg = [
  `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${height}" viewBox="0 0 ${WIDTH} ${height}">`,
  `<rect width="${WIDTH}" height="${height}" fill="${tokens["--bg"]}"/>`,
  `<rect x="24" y="${startY - 44}" width="${WIDTH - 48}" height="${height - startY + 24}" fill="${tokens["--surface"]}" stroke="${tokens["--rule"]}"/>`,
  ...out,
  "</svg>",
].join("\n");

writeFileSync(arg("--out"), svg + "\n");
if (arg("--json")) {
  writeFileSync(
    arg("--json"),
    JSON.stringify(
      {
        width: WIDTH,
        heading: HEADING,
        blocks,
        accents: accentCounts,
        maxAccents: MAX_ACCENTS,
        palette_source: stylesPath,
      },
      null,
      2,
    ),
  );
}

const summary = {
  ok: overBudget.length === 0,
  out: arg("--out"),
  blocks: blocks.length,
  height,
  accents: accentCounts,
  maxAccents: MAX_ACCENTS,
  palette_source: stylesPath,
};

if (overBudget.length) {
  console.error(
    `accent budget: ${overBudget
      .map(([screen, n]) => `${screen} has ${n} (max ${MAX_ACCENTS})`)
      .join(", ")} — CONSOLE_STYLE §1`,
  );
  process.exit(1);
}

console.log(JSON.stringify(summary));
