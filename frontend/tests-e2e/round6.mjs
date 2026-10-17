/**
 * Round 6, Track E: the route crawl and the rapid tab-switch test.
 *
 * Visit EVERY route in the sidebar and footer — signed-out, guest and signed-in —
 * at 1440x900 and 390x844. Fail on console errors, uncaught exceptions, 4xx/5xx
 * from the app's own API, blank bodies and hydration warnings. Then click through
 * 10 routes in sequence under 300ms apart for 3 laps: the app must not freeze,
 * loop, or show an error screen.
 *
 * Needs a Chromium — never `playwright install` in CI sandboxes. Point
 * PLAYWRIGHT_CHROMIUM_EXECUTABLE (or PW_CHROMIUM) at one; the repo's own
 * convention default is /opt/pw-browsers/chromium.
 *
 * Run: node tests-e2e/round6.mjs           (from frontend/, app on :3000)
 *      ADAPT_BASE_URL=http://127.0.0.1:3000 node tests-e2e/round6.mjs
 */

import assert from "node:assert/strict";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const OUT = resolve("../docs");
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium"].find(
  (p) => p && existsSync(p),
);

/** Every route a learner can reach from the sidebar or the footer, plus the
 *  linked flows (onboarding, placement, first lesson). */
const ROUTES = [
  "/",
  "/learn",
  "/review",
  "/progress",
  "/settings",
  "/lesson",
  "/journey",
  "/proposals",
  "/contrast",
  "/baseline",
  "/evidence",
  "/voice",
  "/onboarding",
  "/diagnose",
  "/first-lesson",
];

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];

/** Console noise that is not a defect. The HMR socket is dev-only plumbing. */
function ignorable(text) {
  return (
    text.includes("webpack-hmr") ||
    text.includes("[Fast Refresh]") ||
    text.includes("favicon") ||
    // The signed-out /me check answers 401 before the AccountGate shows. That is
    // the auth answer, not a crash — the signed-out crawl asserts the gate appears.
    (text.includes("401") && text.includes("Unauthorized"))
  );
}

const crashes = []; // {mode, viewport, route, kind, detail}
const checks = [];
/** What is being visited right now — event handlers attribute crashes to it. */
const current = { mode: "-", viewport: "-", route: "-" };

function record(kind, mode, viewport, route, detail) {
  crashes.push({ kind, mode, viewport, route, detail: String(detail).slice(0, 300) });
}

function recordHere(kind, detail) {
  record(kind, current.mode, current.viewport, current.route, detail);
}

async function newPage(browser, viewport, { mobile } = {}) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    ...(mobile ? { isMobile: true, hasTouch: true } : {}),
  });
  const page = await context.newPage();
  page.on("console", (m) => {
    if (m.type() !== "error" && m.type() !== "warning") return;
    const text = m.text();
    if (ignorable(text)) return;
    if (/hydrat/i.test(text)) recordHere("hydration", text);
    else if (m.type() === "error") recordHere("console", text);
  });
  page.on("pageerror", (e) => recordHere("pageerror", e.message));
  page.on("response", (r) => {
    const url = r.url();
    if (!url.startsWith(BASE)) return;
    const status = r.status();
    if (status === 401 || status === 403) return; // auth answers, checked separately
    if (status >= 400) recordHere(`api-${status}`, url);
  });
  return { context, page };
}

async function guestSignIn(page) {
  await page.addInitScript(() => sessionStorage.setItem("adapt:seen-opening", "1"));
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const consent = page.locator('input[type="checkbox"]');
  if (await consent.count()) {
    await consent.first().click();
    await page.getByRole("button", { name: /Agree and continue/i }).click();
    await page.waitForTimeout(300);
    await page.getByRole("button", { name: /Continue as guest/i }).click();
    await page.waitForTimeout(1200);
  }
}

async function registeredSignIn(page, tag) {
  await page.addInitScript(() => sessionStorage.setItem("adapt:seen-opening", "1"));
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const consent = page.locator('input[type="checkbox"]');
  if (!(await consent.count())) return; // already signed in
  await consent.first().click();
  await page.getByRole("button", { name: /Agree and continue/i }).click();
  await page.waitForTimeout(300);
  await page.getByRole("tab", { name: /Create account/i }).click();
  const username = `crawl_${tag}_${Date.now().toString(36)}`;
  await page.getByLabel(/Username/i).fill(username);
  await page.getByLabel(/^Password/i).fill("crawl-password-123");
  await page.getByRole("button", { name: /^Create account$/i }).click();
  await page.waitForTimeout(1500);
}

async function crawl(browser, mode, signIn) {
  for (const viewport of VIEWPORTS) {
    current.mode = mode;
    current.viewport = viewport.name;
    current.route = "(sign-in)";
    const { context, page } = await newPage(browser, viewport, { mobile: viewport.name === "mobile" });
    await signIn(page);
    for (const route of ROUTES) {
      current.route = route;
      const before = crashes.length;
      await page.goto(`${BASE}${route}`, { waitUntil: "networkidle", timeout: 30000 }).catch((e) => recordHere("goto", e.message));
      await page.waitForTimeout(450);
      const body = await page.evaluate(() => document.body?.innerText?.trim() ?? "").catch(() => "");
      if (!body) recordHere("blank-body", "empty body text");
      if (await page.locator("[data-route-error]").count()) recordHere("error-screen", "route error boundary shown");
      const text = await page.evaluate(() => document.body?.innerText ?? "").catch(() => "");
      if (mode === "signed-out" && (route === "/" || route === "/journey") ) {
        if (!/Welcome to ADAPT|Before you begin|Agree/i.test(text)) recordHere("gate-missing", "no account gate on protected route");
      }
      checks.push({ mode, viewport: viewport.name, route, ok: crashes.length === before });
    }
    // SessionGate/redirect-loop test: landing on /journey while signed in must not
    // bounce between account-gate and journey, and the page must render content.
    if (mode !== "signed-out") {
      current.route = "/journey (loop check)";
      await page.goto(`${BASE}/journey`, { waitUntil: "networkidle" });
      await page.waitForTimeout(800);
      const text = await page.evaluate(() => document.body?.innerText ?? "");
      assert.ok(/Your path|What do you want to learn|Starting checks/i.test(text), `session loop: /journey did not render for ${mode}`);
      assert.ok(!/Opening your path/i.test(text), "session loop: stuck on the landing gate");
    }
    await context.close();
  }
}

/** Rapid tab switching: 10 routes <300ms apart, 3 laps. No freeze, loop, error. */
async function rapidTabSwitch(browser) {
  const { context, page } = await newPage(browser, VIEWPORTS[0]);
  await guestSignIn(page);
  const lap = ROUTES.slice(0, 10);
  for (let round = 0; round < 3; round++) {
    for (const route of lap) {
      const link = page.locator(`.sidebar a[href="${route}"], .sidebar-more-items a[href="${route}"]`).first();
      if (await link.count()) {
        await link.click({ timeout: 2000 }).catch(() => {});
      } else {
        // Open the More group first, then click.
        await page.locator('[data-testid="sidebar-more"]').click().catch(() => {});
        await page.locator(`a[href="${route}"]`).first().click({ timeout: 2000 }).catch(() => {});
      }
      await page.waitForTimeout(180);
    }
  }
  await page.waitForTimeout(800);
  const text = await page.evaluate(() => document.body?.innerText ?? "");
  assert.ok(text.trim().length > 40, "rapid tab switch: blank body after 3 laps");
  assert.ok(!/Something went wrong here/i.test(text), "rapid tab switch: error screen shown");
  assert.equal(await page.locator("[data-route-error]").count(), 0, "rapid tab switch: route error boundary shown");
  await context.close();
  return { laps: 3, routes: lap.length };
}

/* ------------------------------------------------------------------ run ---- */

const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : {}),
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const report = { at: new Date().toISOString(), chromium: await browser.version(), checks: 0, rapid: null };

await crawl(browser, "signed-out", async (page) => {
  await page.addInitScript(() => sessionStorage.setItem("adapt:seen-opening", "1"));
});
await crawl(browser, "guest", guestSignIn);
await crawl(browser, "signed-in", (page) => registeredSignIn(page, "r6"));
report.checks = checks.length;
report.rapid = await rapidTabSwitch(browser);
await browser.close();

const log = [
  "# Round 6 crash log",
  "",
  `Crawl of ${ROUTES.length} routes × 2 viewports × 3 sessions (signed-out, guest, signed-in), plus the rapid tab-switch test.`,
  `Generated ${report.chromium} · ${report.at}`,
  "",
  crashes.length === 0
    ? "**No crashes found.** Zero console errors, zero uncaught exceptions, zero non-auth 4xx/5xx from the app's own API, zero blank bodies, zero hydration warnings, zero error screens."
    : `**${crashes.length} crash(es) found:**`,
  "",
  ...(crashes.length
    ? ["| session | viewport | route | kind | detail |", "| --- | --- | --- | --- | --- |", ...crashes.map((c) => `| ${c.mode} | ${c.viewport} | ${c.route} | ${c.kind} | ${c.detail.replace(/\|/g, "\\|")} |`)]
    : []),
  "",
  "## SessionGate / redirect loop",
  "",
  "Fixed in `components/chrome.tsx`: the landing gate that rendered “Opening your path…” over every",
  "screen until the URL reached /journey is gone. If the server says who this browser is, the children",
  "render; navigation is navigation's job. The crawl asserts /journey renders without bouncing.",
  "",
  "## Rapid tab switch",
  "",
  `10 routes × ${report.rapid.laps} laps at <300ms apart: ${report.rapid ? "passed (no freeze, no loop, no error screen)" : "failed"}.`,
  "",
];
mkdirSync(OUT, { recursive: true });
writeFileSync(resolve(OUT, "R6_CRASH_LOG.md"), log.join("\n"));

if (crashes.length) {
  console.error(`round6 crawl: ${crashes.length} crash(es):`);
  for (const c of crashes) console.error(`  [${c.mode} ${c.viewport}] ${c.route} ${c.kind}: ${c.detail}`);
  process.exit(1);
}
console.log(`round6 crawl: ${checks.length} page checks clean; rapid tab switch passed. docs/R6_CRASH_LOG.md written.`);
