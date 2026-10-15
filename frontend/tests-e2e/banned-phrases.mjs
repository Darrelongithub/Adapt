/**
 * Crawl every app page as a learner, both before and after a guest account is created.
 *
 * Run with the frontend and API available (the frontend's same-origin /api proxy is used):
 *
 *   cd frontend
 *   ADAPT_BASE_URL=http://127.0.0.1:3000 node tests-e2e/banned-phrases.mjs
 *
 * The route list is discovered from page.tsx files under src/app so a new page is covered without
 * editing this test. Visible text and visible title/aria-label copy are checked; source
 * comments and TypeScript identifiers are not learner-facing surfaces.
 */
import { chromium } from "playwright";
import { existsSync } from "node:fs";
import { readdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const APP_DIR = fileURLToPath(new URL("../src/app/", import.meta.url));
const DEFAULT_CHROMIUM = "/opt/pw-browsers/chromium";
const BANNED = [
  "API key",
  "Gemini",
  "provider",
  "model",
  "endpoint",
  "GET /",
  "POST /",
  "uvicorn",
  "ignored_keys",
  "AI 1",
  "AI 2",
  "AI 3",
  "token",
];

const failures = [];

function note(label, ok, detail = "") {
  console.log(`${ok ? "  ok  " : "FAIL  "}${label}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(`${label}${detail ? ` — ${detail}` : ""}`);
}

async function discoverRoutes(directory = APP_DIR, segments = []) {
  const entries = await readdir(directory, { withFileTypes: true });
  const routes = [];
  if (entries.some((entry) => entry.isFile() && entry.name === "page.tsx")) {
    routes.push(segments.length ? `/${segments.join("/")}` : "/");
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith(".")) continue;
    if (entry.name.startsWith("@")) continue; // parallel slots are not URL segments
    const part = entry.name.startsWith("(") && entry.name.endsWith(")") ? null : entry.name;
    const next = part ? [...segments, part] : segments;
    routes.push(...(await discoverRoutes(join(directory, entry.name), next)));
  }
  return routes;
}

async function learnerCopy(page) {
  return page.evaluate(() => {
    const parts = [document.body?.innerText ?? ""];
    for (const element of document.querySelectorAll("[aria-label], [title], [placeholder]")) {
      if (!element.getClientRects().length) continue;
      const style = getComputedStyle(element);
      if (style.visibility === "hidden" || style.display === "none") continue;
      const aria = element.getAttribute("aria-label");
      const title = element.getAttribute("title");
      const placeholder = element.getAttribute("placeholder");
      if (aria) parts.push(aria);
      if (title) parts.push(title);
      if (placeholder && (element.value ?? "") === "") parts.push(placeholder);
    }
    return parts.join("\n").normalize("NFKC").replace(/\s+/g, " ").toLocaleLowerCase("en");
  });
}

async function crawlContext(context, label, routes) {
  const page = await context.newPage();
  page.setDefaultTimeout(12_000);
  for (const route of routes) {
    try {
      const response = await page.goto(new URL(route, BASE).href, { waitUntil: "domcontentloaded" });
      // Let client-rendered requests settle without waiting on voice sockets or long polling.
      await page.waitForTimeout(300);
      const text = await learnerCopy(page);
      const hits = BANNED.filter((phrase) => text.includes(phrase.toLocaleLowerCase("en")));
      if (text.includes("protection: not reported")) hits.push("protection: not reported");
      const routeOk = response?.ok() ?? false;
      const detail = [
        hits.length ? `banned copy: ${hits.join(", ")}` : "",
        !routeOk ? `HTTP ${response?.status() ?? "n/a"}` : "",
      ].filter(Boolean).join("; ");
      note(`${label} ${route}`, routeOk && hits.length === 0, detail || `HTTP ${response.status()}`);
      if (label === "signed in" && route === "/contrast") {
        const hasUnhelpfulCounts = /stated,\s*n=0|planned from profile version\s+0,?\s+0 pieces of evidence/i.test(text);
        note("signed-in /contrast uses learner-safe demo evidence wording", !hasUnhelpfulCounts);
      }
    } catch (error) {
      note(`${label} ${route}`, false, `could not crawl: ${error?.message ?? error}`);
    }
  }
  return page;
}

async function createGuest(page) {
  await page.goto(new URL("/", BASE).href, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => sessionStorage.setItem("adapt:seen-opening", "1"));
  await page.reload({ waitUntil: "domcontentloaded" });
  // The account gate is consent-first (Phase D): agree, then continue as guest.
  try {
    await page.waitForSelector("[data-account-gate]", { timeout: 20_000 });
  } catch {
    throw new Error("the signed-out home page did not show the consent step");
  }
  const consent = page.locator("[data-account-gate] input[type='checkbox']").first();
  await consent.check();
  await page.getByRole("button", { name: /agree and continue/i }).first().click();
  const guestButton = page.getByRole("button", { name: /^continue as guest$/i }).first();
  if (!(await guestButton.count())) throw new Error("the signed-out home page did not offer guest access");
  await guestButton.click();
  await page.waitForURL(/\/(baseline|onboarding|learn)(?:\?|$)/, { timeout: 20_000 });
  const me = await page.evaluate(async () => {
    const response = await fetch("/api/v1/me", { credentials: "same-origin" });
    return { status: response.status, body: await response.json().catch(() => null) };
  });
  if (me.status !== 200 || !me.body?.student_id) {
    throw new Error(`guest session was not established (GET /api/v1/me returned ${me.status})`);
  }
  return me.body.student_id;
}

async function removeGuest(page) {
  try {
    await page.evaluate(async () => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("adapt_csrf="))
        ?.slice("adapt_csrf=".length);
      if (!csrf) return;
      await fetch("/api/v1/me", {
        method: "DELETE",
        credentials: "same-origin",
        headers: { "x-csrf-token": decodeURIComponent(csrf) },
      });
    });
  } catch {
    // The crawl's result does not depend on cleanup; the isolated browser context closes below.
  }
}

async function main() {
  const routes = [...new Set(await discoverRoutes())].sort((a, b) => a.localeCompare(b));
  if (!routes.includes("/")) throw new Error("route discovery did not find the home page");
  console.log(`Crawling ${routes.length} page routes: ${routes.join(", ")}`);

  const launchOptions = {};
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  } else if (existsSync(DEFAULT_CHROMIUM)) {
    launchOptions.executablePath = DEFAULT_CHROMIUM;
  }

  let browser;
  try {
    browser = await chromium.launch(launchOptions);
  } catch (error) {
    console.error(`Cannot start Chromium: ${error?.message ?? error}`);
    process.exitCode = 2;
    return;
  }

  try {
    const signedOut = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const signedOutPage = await crawlContext(signedOut, "signed out", routes);
    const privacy = await signedOutPage.evaluate(async () => {
      const response = await fetch("/api/v1/privacy", { credentials: "same-origin" });
      return { status: response.status, body: await response.json().catch(() => null) };
    });
    note("signed out GET /api/v1/privacy is public", privacy.status === 200, `HTTP ${privacy.status}`);
    await signedOut.close();

    const signedIn = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const signedInPage = await signedIn.newPage();
    try {
      await createGuest(signedInPage);
      // The creation screen is covered by the crawl once the guest session is established.
      const firstRoute = routes[0] === "/" ? routes : ["/", ...routes];
      await crawlContext(signedIn, "signed in", firstRoute);
      const privacySignedIn = await signedInPage.evaluate(async () => {
        const response = await fetch("/api/v1/privacy", { credentials: "same-origin" });
        return response.status;
      });
      note("signed in GET /api/v1/privacy is public", privacySignedIn === 200, `HTTP ${privacySignedIn}`);
    } catch (error) {
      note("signed-in crawl setup", false, error?.message ?? String(error));
    } finally {
      await removeGuest(signedInPage);
      await signedIn.close();
    }
  } finally {
    await browser.close();
  }

  console.log(`\n${failures.length ? `${failures.length} banned-copy check(s) failed` : "all banned-copy checks passed"}`);
  if (failures.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
