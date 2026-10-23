/**
 * Fresh-context demo walk: consent → three games → analysis, at phone and desktop sizes.
 * Run against the real frontend/API with `npm run test:e2e:demo`.
 * Set ADAPT_DEMO_SHOTS=1 to leave final screens in /tmp for visual review.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const executablePath = [
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  process.env.PW_CHROMIUM,
  "/opt/pw-browsers/chromium",
  "/tmp/chromium",
].find((path) => path && existsSync(path));
const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : {}),
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

async function answerCurrentGame(page, gameNumber) {
  const game = page.locator("[data-demo-game]");
  await game.waitFor({ state: "visible", timeout: 20_000 });
  const kind = await game.getAttribute("data-demo-game");

  if (kind === "quick_recall") {
    const check = page.getByRole("button", { name: "Check", exact: true });
    await check.waitFor({ state: "visible", timeout: 12_000 });
    const options = game.locator(".option-grid .option-tile");
    const count = await options.count();
    for (let index = 0; index < count && !(await check.isEnabled()); index += 1) {
      await options.nth(index).click();
    }
    assert.equal(await check.isEnabled(), true, "recall could not reach its Check control");
    await check.click();
  } else {
    const option = game.locator(".option-grid .option-tile").first();
    await option.waitFor({ state: "visible", timeout: 10_000 });
    await option.click();
  }

  if (gameNumber === 3) {
    await page.locator("[data-demo-analysis]").waitFor({ state: "visible", timeout: 15_000 });
  } else {
    await page.waitForFunction(
      (next) => document.querySelector(".demo-game-head .demo-kicker")?.textContent?.includes(`Game ${next} of 3`),
      gameNumber + 1,
      { timeout: 15_000 },
    );
  }
}

async function walk({ name, viewport, mobile = false, forceFallback = false }) {
  const context = await browser.newContext({
    viewport,
    isMobile: mobile,
    hasTouch: mobile,
    deviceScaleFactor: mobile ? 2 : 1,
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15_000);
  const errors = [];
  const responses = [];
  const studentPayloads = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.url().includes("/api/v1/")) responses.push({ status: response.status(), method: response.request().method(), url: response.url() });
  });
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/students") && request.method() === "POST") studentPayloads.push(request.postDataJSON());
  });
  if (forceFallback) {
    await page.route("**/api/v1/tests/plan", async (route) => {
      // Cross the browser's eight-second preparation bound, then fail: this proves
      // the learner is already in the built-in games before the late refusal arrives.
      await new Promise((resolve) => setTimeout(resolve, 8_500));
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "deliberate delayed demo fallback check" }),
      });
    });
  }

  const started = Date.now();
  try {
    const response = await page.goto(`${BASE}/demo`, { waitUntil: "domcontentloaded" });
    assert.equal(response?.ok(), true, `demo entry returned ${response?.status()}`);
    await page.getByRole("button", { name: "I agree — start the demo" }).waitFor({ state: "visible" });
    assert.match(await page.locator(".demo-badge").innerText(), /DEMO[\s\S]*Demo version/);
    assert.equal(await page.locator("[data-account-gate], .sidebar, .voice-switch, .opening-overlay").count(), 0);

    await page.getByRole("button", { name: "I agree — start the demo" }).click();
    await page.locator("[data-demo-game]").waitFor({ state: "visible", timeout: 20_000 });
    const origin = await page.locator("[data-demo-game]").getAttribute("data-demo-origin");
    if (forceFallback) assert.equal(origin, "built-in", "a refused content request must open built-in games");

    for (let game = 1; game <= 3; game += 1) await answerCurrentGame(page, game);

    assert.equal(await page.locator(".demo-result-list > li").count(), 3, "analysis lists all three games");
    assert.equal(await page.locator(".demo-adaptations li").count(), 2, "analysis explains both adaptations");
    assert.match(await page.locator(".demo-next-card").innerText(), /next step/i);
    assert.match(await page.locator(".demo-badge").innerText(), /Demo version/);
    assert.equal(await page.locator("[data-account-gate], .sidebar, .voice-switch").count(), 0);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 1, `${name}: horizontal overflow was ${overflow}px`);
    if (mobile) {
      const smallTargets = await page.evaluate(() => [...document.querySelectorAll(".demo-page button")]
        .filter((element) => element.getClientRects().length)
        .map((element) => ({ text: element.textContent?.trim(), rect: element.getBoundingClientRect() }))
        .filter(({ rect }) => Math.min(rect.width, rect.height) < 40)
        .map(({ text, rect }) => `${text}: ${Math.round(rect.width)}×${Math.round(rect.height)}`));
      assert.deepEqual(smallTargets, [], "all visible phone controls need a 40px minimum target");
    }

    if (origin === "server") {
      await page.getByText("Run saved to this guest session.").waitFor({ state: "visible", timeout: 15_000 });
      const grades = responses.filter((row) => row.method === "POST" && /\/tests\/grade/.test(row.url));
      const evidence = responses.filter((row) => row.method === "POST" && /\/tests\/evidence/.test(row.url));
      assert.equal(grades.length, 3, "each server-provided game was graded once");
      assert.ok(grades.every((row) => row.status === 200), `grade statuses: ${grades.map((row) => row.status)}`);
      assert.equal(evidence.length, 1, "the complete run was saved once");
      assert.equal(evidence[0].status, 200);
    } else {
      assert.match(await page.locator(".demo-save").innerText(), /Built-in run/);
    }

    assert.equal(studentPayloads.length, 1, "one consent action creates at most one guest");
    assert.equal(studentPayloads[0].as_guest, true);
    assert.equal("password" in studentPayloads[0], false, "the demo never sends a password");
    assert.equal(studentPayloads[0].consent_share_with_llm, false);
    assert.deepEqual(errors, [], "no uncaught browser errors");

    const elapsedMs = Date.now() - started;
    assert.ok(elapsedMs < 120_000, `${name}: demo took ${elapsedMs}ms, over the two-minute budget`);
    if (process.env.ADAPT_DEMO_SHOTS === "1") {
      await page.screenshot({ path: `/tmp/adapt-demo-${name}.png`, fullPage: true });
    }
    return {
      name,
      viewport: `${viewport.width}x${viewport.height}`,
      origin,
      elapsedMs,
      games: 3,
      resultRows: 3,
      horizontalOverflowPx: overflow,
      browserErrors: errors.length,
      apiResponses: responses.map((row) => `${row.method} ${new URL(row.url).pathname} -> ${row.status}`),
    };
  } finally {
    await context.close();
  }
}

try {
  const runs = [];
  runs.push(await walk({ name: "mobile-server", viewport: { width: 390, height: 844 }, mobile: true }));
  runs.push(await walk({ name: "mobile-fallback", viewport: { width: 390, height: 844 }, mobile: true, forceFallback: true }));
  runs.push(await walk({ name: "desktop-server", viewport: { width: 1280, height: 900 } }));
  console.log(JSON.stringify({ check: "three-game demo", outcome: "PASS", budgetMs: 120_000, runs }, null, 2));
} finally {
  await browser.close();
}
