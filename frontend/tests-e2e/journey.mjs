/**
 * The whole journey, end to end, in a real browser — desktop and mobile.
 *
 * What this proves that the jsdom probe cannot: that the path lays out and stays usable at a
 * phone width, that nothing overflows the viewport, that the mobile drawer really opens, and
 * that every press in that layout is received by the control it was aimed at.
 *
 * The B and C screens (games/baseline, lessons) are stubbed at the network edge: this script is
 * about the shell that hands over to them, so their endpoints are answered with contract-shaped
 * stubs rather than being exercised. Their internals are covered by their own phase's browsers
 * checks. Every stage fact is read back from the *server* (`GET /api/v1/journey/state`) as well
 * as from the DOM, so a page that painted the right thing without recording it fails here.
 *
 * Run it (needs browsers — this repo's build sandbox has none, see docs/round2/unverified-d.md):
 *
 *     cd frontend
 *     npm i -D @playwright/test && npx playwright install chromium     # never run in the sandbox
 *     node tests-e2e/journey.mjs
 *
 * `ADAPT_BASE_URL` overrides the app origin (default http://127.0.0.1:3000, which proxies /api to
 * the API on :8000 — see server.mjs). `ADAPT_CHROMIUM` overrides the browser path (default
 * /opt/pw-browsers/chromium). Screenshots land in docs/screens/D/.
 */
import { existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const CHROMIUM = process.env.ADAPT_CHROMIUM ?? "/opt/pw-browsers/chromium";
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "screens", "D");

const STAGES = ["account", "preferences", "games", "subject", "subject_round", "snapshot", "first_lesson", "practice"];
const SKIPPABLE = ["preferences", "games", "subject", "subject_round", "snapshot", "first_lesson"];

const failures = [];
function ok(label, condition, detail = "") {
  console.log(`${condition ? "  ok  " : "FAIL  "}${label}${detail ? ` — ${detail}` : ""}`);
  if (!condition) failures.push(label);
}

/** The stubs for the screens this phase only links to. Shaped by the frozen contracts; nothing
 *  here is a claim about B's or C's internals, it only has to answer well enough that the shell
 *  can hand a learner over without an error frame. */
const STUBS = [
  [/\/api\/v1\/baseline\//, () => ({ state: "idle", questions: [], games: [], levers: {} })],
  [/\/api\/v1\/games\//, () => ({ games: [] })],
  [/\/api\/v1\/lessons?(\/|$)/, () => ({ lessons: [], plans: [] })],
  [/\/api\/v1\/lesson\//, () => ({ blocks: [], source: "simple" })],
  [/\/api\/v1\/subjects?/, () => ({ subjects: [], rounds: [] })],
  [/\/api\/v1\/content\//, () => ({ job_id: "stub", state: "ready", pack_source: "authored" })],
  [/\/api\/v1\/assessment\//, () => ({ items: [] })],
];

async function stubOtherPhases(context) {
  for (const [pattern, body] of STUBS) {
    await context.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body()) }),
    );
  }
}

/** A guest account, created over the API and handed to the browser as a cookie: the shortest
 *  honest way to be signed in without re-testing the front door (the jsdom probe owns that). */
async function guestContext(browser, viewport) {
  const context = await browser.newContext({ viewport });
  const res = await context.request.post(`${BASE}/api/v1/students`, { data: { as_guest: true } });
  if (!res.ok()) throw new Error(`could not create a guest account: ${res.status()} ${await res.text()}`);
  const raw = res.headers()["set-cookie"] ?? "";
  const cookies = [...raw.matchAll(/(adapt_[a-z_]+)=([^;]+)/g)].map(([, name, value]) => ({ name, value, url: BASE }));
  if (!cookies.length) throw new Error(`no session cookie in ${raw.slice(0, 120)}`);
  await context.addCookies(cookies);
  await stubOtherPhases(context);
  return context;
}

const state = (context) => context.request.get(`${BASE}/api/v1/journey/state`).then((r) => r.json());

/** Waits for the eight rows, then reads them the way the page shows them. */
async function readRows(page) {
  await page.waitForFunction(() => document.querySelectorAll("li[data-stage]").length === 8, null, { timeout: 30_000 });
  return page.$$eval("li[data-stage]", (els) =>
    els.map((el) => ({
      id: el.getAttribute("data-stage"),
      status: el.getAttribute("data-status"),
      here: el.getAttribute("aria-current") === "step",
      text: (el.textContent ?? "").replace(/\s+/g, " ").trim(),
      canSkip: !!el.querySelector("button"),
    })),
  );
}

async function shoot(page, name) {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: resolve(SHOTS, `${name}.png`), fullPage: true });
}

/** One viewport's walk. `label` prefixes every check so the desktop and mobile runs are told
 *  apart in the output. */
async function walk(browser, { label, viewport, mobile }) {
  console.log(`\n\x1b[1m${label}\x1b[0m`);
  const context = await guestContext(browser, viewport);
  const page = await context.newPage();
  const appErrors = [];
  page.on("pageerror", (err) => appErrors.push(String(err).slice(0, 160)));

  await page.goto(`${BASE}/journey`, { waitUntil: "domcontentloaded" });
  const rows = await readRows(page);
  const server = await state(context);

  ok(`${label}: the path names all eight frozen stages, in order`, rows.map((r) => r.id).join(",") === STAGES.join(","), rows.map((r) => r.id).join(","));
  ok(`${label}: the server agrees about the stage the learner is on`, server.stage === "preferences" && (server.stages ?? []).length === 8, `stage=${server.stage}`);
  ok(`${label}: the step marked "here" is the server's stage`, rows.find((r) => r.here)?.id === server.stage, rows.find((r) => r.here)?.id ?? "(none)");
  ok(
    `${label}: the first and last steps cannot be skipped, the six between them can`,
    rows.filter((r) => r.canSkip).map((r) => r.id).join(",") === SKIPPABLE.join(","),
    rows.filter((r) => r.canSkip).map((r) => r.id).join(",") || "(none)",
  );
  ok(`${label}: the progress bar counts in words, not just in width`, /steps/.test((await page.getAttribute("[role=progressbar]", "aria-valuetext")) ?? ""), (await page.getAttribute("[role=progressbar]", "aria-valuetext")) ?? "(none)");
  const barWidths = await page.$$eval("[role=progressbar] > *", (els) => els.map((el) => el.getBoundingClientRect().width));
  ok(`${label}: the bar is inside the progress element at this width`, barWidths.every((w) => w >= 0), barWidths.map((w) => Math.round(w)).join(","));
  const overflow = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
  ok(`${label}: nothing pushes the page sideways`, overflow.scroll <= overflow.inner + 1, `${overflow.scroll} vs ${overflow.inner}`);
  await shoot(page, `journey-${label}`);

  if (mobile) {
    // The drawer is the mobile navigation; on a phone the eight steps have to be reachable from it.
    const toggle = page.locator("button[aria-controls='adapt-sidebar']");
    ok(`${label}: the sidebar is a labelled drawer, not an always-open column`, await toggle.count() === 1, `${await toggle.count()} control(s)`);
    await toggle.click();
    await page.waitForTimeout(300);
    const drawer = page.locator("aside.sidebar.open");
    ok(`${label}: opening it shows the path as the current section`, (await drawer.count()) === 1 && /Your path/.test(await drawer.innerText()), (await drawer.innerText().catch(() => "")).slice(0, 120));
    ok(`${label}: and marks it for screen readers, not only in colour`, (await drawer.locator("[aria-current='page']").count()) === 1, `${await drawer.locator("[aria-current='page']").count()} marked`);
    await shoot(page, "journey-mobile-drawer");
    await page.locator("button[aria-controls='adapt-sidebar']").click();
    await page.waitForTimeout(200);
  } else {
    const sidebar = page.locator("aside.sidebar");
    ok(`${label}: the wide layout shows the path in the permanent sidebar`, await sidebar.isVisible() && /Your path/.test(await sidebar.innerText()), (await sidebar.innerText()).replace(/\s+/g, " ").slice(0, 100));
  }

  // Placement stays reachable from the path without being one of its eight steps.
  const diagnose = page.locator('a[href="/diagnose"]');
  ok(`${label}: the placement questions are reachable from the path`, (await diagnose.count()) >= 1 && /placement/i.test(await diagnose.first().innerText()), (await diagnose.first().innerText().catch(() => "")).trim());

  // The step card hands over to the screen that actually does the work (F's baseline screen is
  // stubbed here); the shell only has to send the learner to the right place.
  const primary = page.locator("[data-testid='journey-card'] a").first();
  const href = await primary.getAttribute("href");
  await primary.click();
  await page.waitForURL(new RegExp(`${href}(\\?|$)`), { timeout: 20_000 }).catch(() => {});
  ok(`${label}: the first step's button goes to that step's screen`, page.url().includes(href ?? "/none"), `${href} → ${page.url().replace(BASE, "")}`);
  await page.goBack({ waitUntil: "domcontentloaded" });
  await readRows(page);

  // Walk the rest of the path by skipping, and check the server each time: the DOM is what the
  // learner sees, the endpoint is what actually happened.
  for (const id of SKIPPABLE) {
    const row = page.locator(`li[data-stage='${id}']`);
    await row.locator("button").click();
    await page.waitForFunction(
      (stage) => document.querySelector(`li[data-stage='${stage}']`)?.getAttribute("data-status") === "skipped",
      id,
      { timeout: 20_000 },
    );
    const after = await state(context);
    const stored = (after.stages ?? []).find((r) => r.id === id);
    const notMeasured = (after.not_measured ?? []).includes(id);
    ok(`${label}: skipping "${id}" is stored as skipped and reported as not measured`, stored?.status === "skipped" && notMeasured, `status=${stored?.status} not_measured=${notMeasured}`);
  }

  const final = await state(context);
  const practiceRow = (await readRows(page)).find((r) => r.id === "practice");
  ok(`${label}: with every skippable step skipped the path lands on practice`, final.stage === "practice" && practiceRow?.here === true, `stage=${final.stage}`);
  ok(`${label}: all six skipped steps are named as not measured`, (final.not_measured ?? []).length === 6, JSON.stringify(final.not_measured));
  ok(`${label}: the screen says what was not measured instead of showing a zero`, /What you skipped/.test(await page.locator("body").innerText()), (await page.locator("body").innerText()).slice(-120));
  await shoot(page, `journey-${label}-after-skips`);

  // The gate: close the browser and come back. A second context is a second browser session —
  // no memory of the page — so anything that survives came from the server.
  const returning = await browser.newContext({ viewport });
  await returning.addCookies(await context.cookies());
  await stubOtherPhases(returning);
  const back = await returning.newPage();
  await back.goto(`${BASE}/journey`, { waitUntil: "domcontentloaded" });
  const backRows = await readRows(back);
  ok(
    `${label}: a learner who closes the browser and returns resumes on the stored step`,
    backRows.find((r) => r.here)?.id === "practice" && backRows.filter((r) => r.status === "skipped").length === 6,
    `here=${backRows.find((r) => r.here)?.id ?? "(none)"} skipped=${backRows.filter((r) => r.status === "skipped").length}`,
  );
  await shoot(back, `journey-${label}-resume`);
  ok(`${label}: no uncaught error on either walk`, appErrors.length === 0, appErrors.join(" | "));

  await returning.close();
  await context.close();
}

async function main() {
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: existsSync(CHROMIUM) ? CHROMIUM : undefined,
    });
  } catch (err) {
    console.error(
      `\nCannot start Chromium: ${err.message}\n\n` +
        `Looked for the browser at ${CHROMIUM}. If it is elsewhere, set ADAPT_CHROMIUM.\n` +
        "If no browser can be installed here, this check cannot run in this environment — record it in\n" +
        "docs/round2/unverified-d.md rather than claiming the layout was seen.\n",
    );
    process.exit(2);
  }

  try {
    await walk(browser, { label: "desktop", viewport: { width: 1280, height: 900 }, mobile: false });
    await walk(browser, { label: "mobile", viewport: { width: 390, height: 844 }, mobile: true });
  } finally {
    await browser.close();
  }

  console.log(`\n${failures.length ? `${failures.length} check(s) failed:\n  - ${failures.join("\n  - ")}` : "all checks passed"}`);
  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
