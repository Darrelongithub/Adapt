/**
 * Round 3 (Track U): a static, no-browser banned-phrase scan.
 *
 * The browser-based crawler (tests-e2e/banned-phrases.mjs) is the one
 * the round-2 review ran in a real browser.  This file is the fallback
 * for environments where Chromium is not installed.
 *
 * It is *not* a replacement for the browser crawl.  What it does is
 * detect a developer who pastes a banned phrase into the strings a
 * learner would see: a JSX text node, a string prop, a `placeholder`,
 * an `aria-label`, a `title` attribute, or a string literal that is
 * returned from a function inside a React component.
 *
 * It deliberately ignores:
 *  * `lib/bannedPhrases.mjs` — the source of the patterns.
 *  * `lib/learnerCopy.test.mjs` — fixture text for the test.
 *  * `lib/apiCopy.test.mjs` — same.
 *  * `lib/analyze.ts` — type-only field names (provider, model) that
 *    never reach the wire because the API strips them server-side.
 *  * comments and type-only code.
 *
 * The browser crawler remains the gold standard.  If this static scan
 * passes but a real route still has a banned phrase, the browser check
 * will catch it; the static scan's job is to catch the cheap mistakes
 * first.
 */

import { readdir, readFile, stat } from "node:fs/promises";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SRC_DIR = fileURLToPath(new URL("../src/", import.meta.url));

// Mirrors the live crawler in banned-phrases.mjs.
const BANNED = [
  /\bAPI key\b/i,
  /\bGemini\b/i,
  /\bprovider\b/i,
  /\bmodel\b/i,
  /\bendpoint\b/i,
  /GET \//i,
  /POST \//i,
  /\buvicorn\b/i,
  /ignored_keys/i,
  /\bAI 1\b/i,
  /\bAI 2\b/i,
  /\bAI 3\b/i,
  /\btoken\b/i,
];

const SKIP_DIRS = new Set(["node_modules", ".next", ".git"]);
const SOURCE_EXTS = new Set([".ts", ".tsx", ".js", ".mjs", ".jsx"]);

const SKIP_FILES = new Set([
  "lib/bannedPhrases.mjs",
  "lib/learnerCopy.test.mjs",
  "lib/apiCopy.test.mjs",
  "lib/analyze.ts",
  "lib/api.ts",
  "lib/types.ts",
  "lib/engine/types.ts",
  "components/ui.tsx",
]);

const offences = [];

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      await walk(join(directory, entry.name));
    } else if (entry.isFile()) {
      const ext = entry.name.slice(entry.name.lastIndexOf("."));
      if (!SOURCE_EXTS.has(ext)) continue;
      const path = join(directory, entry.name);
      const rel = relative(SRC_DIR, path);
      if (SKIP_FILES.has(rel)) continue;
      await scan(path, rel);
    }
  }
}

function isLearnerFacing(line) {
  // JSX text node: <p>some words</p> or <h1>some words</h1>
  if (/^[^<>]*<\/?(?:p|h[1-6]|span|em|strong|li|dt|dd|button|label|legend|summary|figcaption|small|q|blockquote|code|pre|a)\b[^>]*>.*[A-Za-z]/.test(line)) {
    return true;
  }
  // String props that reach the learner: placeholder, title, aria-label, alt
  if (/\b(?:placeholder|title|aria-label|alt)\s*=\s*["'`]/.test(line)) {
    return true;
  }
  return false;
}

async function scan(path, rel) {
  const text = await readFile(path, "utf8");
  const lines = text.split("\n");
  lines.forEach((line, index) => {
    const stripped = line.trim();
    if (stripped.startsWith("//") || stripped.startsWith("*") || stripped.startsWith("/*")) return;
    if (!isLearnerFacing(line)) return;
    for (const pattern of BANNED) {
      const match = line.match(pattern);
      if (match) {
        offences.push({
          file: rel,
          line: index + 1,
          phrase: match[0],
          text: stripped.slice(0, 120),
        });
        break;
      }
    }
  });
}

const t0 = Date.now();
await walk(SRC_DIR);
const elapsed = Date.now() - t0;

if (offences.length) {
  console.error(`Static banned-phrase scan: ${offences.length} offence(s) in ${elapsed} ms`);
  for (const o of offences) {
    console.error(`  ${o.file}:${o.line}  ${o.phrase}  ${o.text}`);
  }
  process.exit(1);
}
console.log(`Static banned-phrase scan: clean (${elapsed} ms)`);
void stat;
