/**
 * Banned-phrase crawl, end to end, in a real browser (Round 2, Part A, task 2).
 *
 * Developer language must never reach a learner's screen. This crawl visits
 * every app route twice — signed out, then signed in as a guest — reads the
 * text a learner can actually see (`innerText`, so code, comments and type
 * names do not count), and fails if any of it names the machinery:
 *
 *     API key · Gemini · provider · model · endpoint · GET / · POST / ·
 *     uvicorn · ignored_keys · AI 1 · AI 2 · AI 3 · token
 *
 * It also pins task 1 in the browser: signed out on `/`, the "What is
 * recorded about you" list must be readable — the privacy list is public —
 * and must not fall back to "could not be read just now".
 *
 * Every route fails hard. (`BANNED_PHRASES_KNOWN=/some/route` downgrades a
 * route to report-only; the list is empty by default and Phase D removed the
 * onboarding exemption this paragraph used to advertise.)
 *
 * Run it (needs a Chromium — never `playwright install` in CI sandboxes;
 * point `PW_CHROMIUM` at an existing binary if the default path is absent):
 *
 *     cd frontend
 *     node tests-e2e/banned_phrases.mjs
 *
 * `ADAPT_BASE_URL` overrides the app origin (default http://127.0.0.1:3000).
 * Exit 2 means the browser could not start here at all: record that in
 * docs/UNVERIFIED.md rather than claiming the crawl passed.
 */
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const KNOWN = new Set((process.env.BANNED_PHRASES_KNOWN ?? "").split(",").map((s) => s.trim()).filter(Boolean));
const HERE = path.dirname(fileURLToPath(import.meta.url));

const BANNED = [
  /API key/i,
  /Gemini/i,
  /provider/i,
  /\bmodel\b/i,
  /endpoint/i,
  /\bGET \//,
  /\bPOST \//,
  /uvicorn/i,
  /ignored_keys/i,
  /\bAI [123]\b/,
  /\btoken\b/i,
];

/** Every page route in the app, from the file tree — no hand-kept list to drift. */
function routes() {
  const appDir = path.join(HERE, "..", "src", "app");
  const found = [];
  const walk = (dir, prefix) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(path.join(dir, entry.name), `${prefix}/${entry.name}`);
      else if (entry.name === "page.tsx") found.push(prefix || "/");
    }
  };
  walk(appDir, "");
  // Dynamic segments cannot be visited without knowing an id; the crawl
  // covers every static route, which is where shared chrome renders.
  return found.filter((r) => !r.includes("[")).sort();
}

const failures = [];
const knownOnly = [];

function check(route, signedIn, text) {
  for (const pattern of BANNED) {
    const match = text.match(pattern);
    if (!match) continue;
    const at = Math.max(0, (match.index ?? 0) - 40);
    const detail = `${signedIn ? "signed-in" : "signed-out"} ${route}: ${pattern} in “…${text.slice(at, at + 90).replace(/\s+/g, " ")}…”`;
    if (KNOWN.has(route)) knownOnly.push(detail);
    else failures.push(detail);
  }
}

async function visibleText(page, route) {
  await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
  // Let client fetches settle so fetched copy is on screen before reading it.
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  return page.evaluate(() => document.body?.innerText ?? "");
}

async function main() {
  let chromium;
  try {
    ({ chromium } = await import("playwright"));
  } catch {
    console.error(
      "Playwright is not installed here, so the banned-phrase crawl cannot run.\n" +
        "Record it as unverified (docs/UNVERIFIED.md); do not claim the crawl passed.",
    );
    process.exit(2);
  }
  const executablePath = [process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium"].find((p) => p && existsSync(p));
  let browser;
  try {
    browser = await chromium.launch(executablePath ? { executablePath } : {});
  } catch (err) {
    console.error(
      `\nCannot start Chromium: ${err.message}\n\n` +
        "If no browser is available in this environment, record the crawl as unverified\n" +
        "in docs/UNVERIFIED.md rather than claiming it passed.\n",
    );
    process.exit(2);
  }

  const list = routes();
  console.log(`crawling ${list.length} routes at ${BASE}`);

  // ---- signed out -------------------------------------------------------
  const anon = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const anonPage = await anon.newPage();
  for (const route of list) {
    const text = await visibleText(anonPage, route);
    check(route, false, text);
    if (route === "/") {
      const ok = (label, cond) => {
        console.log(`${cond ? "  ok  " : "FAIL  "}${label}`);
        if (!cond) failures.push(label);
      };
      ok("signed-out home shows the recorded-fields list", /What is recorded about you/.test(text));
      ok("signed-out home does not say the list could not be read", !/could not be read just now/.test(text));
    }
  }
  await anon.close();

  // ---- signed in (guest) -------------------------------------------------
  const member = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const memberPage = await member.newPage();
  const made = await memberPage.request.post(`${BASE}/api/v1/students`, { data: { as_guest: true } });
  if (!made.ok()) {
    failures.push(`could not create a guest learner to sign in with: HTTP ${made.status()}`);
  } else {
    for (const route of list) {
      const text = await visibleText(memberPage, route);
      check(route, true, text);
    }
  }
  await member.close();
  await browser.close();

  for (const detail of knownOnly) console.log(`known (other phase): ${detail}`);
  if (failures.length) {
    console.error(`\n${failures.length} banned-phrase failure(s):`);
    for (const f of failures) console.error(`  FAIL  ${f}`);
    process.exit(1);
  }
  console.log(`\nbanned-phrase crawl passed on ${list.length} routes, signed out and signed in.`);
}

await main();
