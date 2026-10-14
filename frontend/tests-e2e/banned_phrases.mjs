/**
 * Visit every learner route signed-out and signed-in, and fail if visible text
 * (including title, aria, placeholder and alt) contains a developer phrase.
 *
 * Chromium must already be at /opt/pw-browsers/chromium. This script never
 * downloads a browser.
 *
 *     ADAPT_BASE_URL=http://127.0.0.1:3000 node tests-e2e/banned_phrases.mjs
 *
 * The app should be running with the API proxied (frontend `node server.mjs`,
 * backend on :8000, ADAPT_MODE=local). Onboarding may still contain the three
 * sentences listed in docs/round2/handoff-A.md; any other hit fails.
 */
import { chromium } from "playwright";

import { bannedHits } from "../src/lib/bannedPhrases.mjs";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const EXECUTABLE = process.env.ADAPT_CHROMIUM ?? "/opt/pw-browsers/chromium";

const ROUTES = [
  "/",
  "/learn",
  "/lesson",
  "/proposals",
  "/review",
  "/progress",
  "/baseline",
  "/subject",
  "/contrast",
  "/evidence",
  "/voice",
  "/settings",
  "/onboarding",
  "/diagnose",
];

function collect(pageText, attrs) {
  return [pageText, ...attrs].filter(Boolean).join("\n");
}

async function visible(page) {
  const text = await page.locator("body").innerText();
  const attrs = await page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll("[title], [aria-label], [placeholder], [alt]")) {
      for (const name of ["title", "aria-label", "placeholder", "alt"]) {
        const value = el.getAttribute(name);
        if (value) out.push(value);
      }
    }
    if (document.title) out.push(document.title);
    return out;
  });
  return collect(text, attrs);
}

async function walk(page, label) {
  const failures = [];
  for (const route of ROUTES) {
    const response = await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(400);
    const body = await visible(page);
    const hits = bannedHits(body, route);
    const status = response ? response.status() : 0;
    if (hits.length) {
      const snippet = body.replace(/\s+/g, " ").slice(0, 240);
      failures.push(`${label} ${route} (${status}) hit ${hits.join(", ")} — ${snippet}`);
      console.log(`FAIL  ${label} ${route}: ${hits.join(", ")}`);
    } else {
      console.log(`  ok  ${label} ${route}`);
    }
  }
  return failures;
}

const browser = await chromium.launch({
  executablePath: EXECUTABLE,
  headless: true,
});
const failures = [];
try {
  const signedOut = await browser.newContext();
  failures.push(...(await walk(await signedOut.newPage(), "signed-out")));
  await signedOut.close();

  const signedIn = await browser.newContext();
  const created = await signedIn.request.post(BASE + "/api/v1/students", {
    data: { as_guest: true, display_name: "Phrase check" },
  });
  if (!created.ok()) {
    failures.push(`signed-in setup failed: ${created.status()} ${await created.text()}`);
  } else {
    failures.push(...(await walk(await signedIn.newPage(), "signed-in")));
  }
  await signedIn.close();
} finally {
  await browser.close();
}

if (failures.length) {
  console.error("\nBanned phrases:\n" + failures.map((line) => "  " + line).join("\n"));
  process.exit(1);
}
console.log("\nNo banned phrases on the listed routes.");
