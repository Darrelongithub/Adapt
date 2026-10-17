#!/usr/bin/env node
/**
 * The style grep (Round 6, Track A). Fails CI when the banned styling appears
 * anywhere under frontend/src:
 *
 *   text-black · bg-black · #000 · font-bold
 *
 * The design system in src/styles/tokens.css owns colour and type: headings and
 * bold are --ink (brand blue), body is --body (slate-blue), and bold text is the
 * `.strong` class — never `font-bold` in black. The only uppercase-mono labels
 * allowed are tiny status chips (`.status-chip`).
 *
 * Usage: node ../scripts/check-frontend-style.mjs   (from frontend/)
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const srcDir = join(root, "frontend", "src");

/** [regex, why it is banned] */
const BANNED = [
  [/\btext-black\b/, "no black text — headings/bold are --ink, body is --body"],
  [/\bbg-black\b/, "no black fills — buttons are brand blue or white"],
  [/#000\b/i, "no #000 — use a token from styles/tokens.css"],
  [/\bfont-bold\b/, "no font-bold without a colour token — use .strong (coloured with --ink)"],
];

const SKIP_DIRS = new Set(["node_modules", ".next", "dist", "build"]);

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (!SKIP_DIRS.has(entry)) yield* walk(full);
    } else if (/\.(tsx?|css|mjs|jsx)$/.test(entry)) {
      yield full;
    }
  }
}

const failures = [];
for (const file of walk(srcDir)) {
  const text = readFileSync(file, "utf8");
  let inBlockComment = false;
  text.split("\n").forEach((line, i) => {
    const trimmed = line.trim();
    // Documentation may name the banned patterns; only real code counts.
    if (inBlockComment) {
      if (trimmed.includes("*/")) inBlockComment = false;
      return;
    }
    if (trimmed.startsWith("/*") || trimmed.startsWith("//") || trimmed.startsWith("*")) {
      if (trimmed.startsWith("/*") && !trimmed.includes("*/")) inBlockComment = true;
      return;
    }
    for (const [pattern, why] of BANNED) {
      if (pattern.test(line)) {
        failures.push({ file: relative(root, file), line: i + 1, text: line.trim().slice(0, 120), why });
      }
    }
  });
}

if (failures.length) {
  console.error(`\ncheck-frontend-style: ${failures.length} violation(s) of the design system\n`);
  for (const f of failures) {
    console.error(`  ${f.file}:${f.line}  ${f.why}`);
    console.error(`      ${f.text}`);
  }
  console.error("\nColours and type live in frontend/src/styles/tokens.css. Nothing else may set them.\n");
  process.exit(1);
}
console.log("check-frontend-style: clean (no text-black, #000, bg-black or font-bold in frontend/src)");
