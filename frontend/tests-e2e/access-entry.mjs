/** Regression: protected routes wait for an explicit entry decision. */
import assert from "node:assert/strict";
import { existsSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const routes = ["/", "/baseline", "/diagnose", "/evidence", "/first-lesson", "/journey", "/learn", "/lesson", "/onboarding", "/progress", "/proposals", "/review", "/settings", "/voice", "/dev/animator", "/dev/test-lab"];
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium", "/tmp/chromium"].find((p) => p && existsSync(p));
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
const creations = [];
page.on("request", (request) => {
  if (request.url().includes("/api/v1/students") && request.method() === "POST") creations.push(request.postDataJSON());
});

try {
  const visited = [];
  for (const route of routes) {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
    const opening = page.locator(".opening-overlay");
    if (await opening.count()) {
      await opening.click({ position: { x: 10, y: 10 } });
      await opening.waitFor({ state: "detached", timeout: 5000 });
    }
    await page.locator("[data-account-gate]").waitFor({ state: "visible", timeout: 15000 });
    assert.equal(creations.length, 0, `${route} created a learner before entry`);
    visited.push({ route, gated: true, auth_step: await page.locator("[data-account-gate]").getAttribute("data-auth-step") });
  }

  await page.locator('[data-account-gate] input[type="checkbox"]').first().check();
  await page.getByRole("button", { name: /Agree and continue/i }).click();
  const createdResponse = page.waitForResponse((response) => response.url().includes("/api/v1/students") && response.request().method() === "POST");
  await page.getByRole("button", { name: /Continue as guest/i }).click();
  assert.equal((await createdResponse).status(), 200);
  await page.locator("[data-account-gate]").waitFor({ state: "detached", timeout: 15000 });
  assert.equal(creations.length, 1);
  await page.goto(`${BASE}/learn`, { waitUntil: "networkidle" });
  assert.equal(await page.locator("[data-account-gate]").count(), 0);
  const me = await page.evaluate(async () => (await fetch("/api/v1/me", { credentials: "same-origin" })).json());
  assert.ok(me.student_id);

  const output = {
    check: "A06",
    outcome: "PASS",
    routes: visited,
    creation_requests_before_entry: 0,
    creation_requests_after_explicit_guest: creations.length,
    explicit_guest_student_id: me.student_id,
    unlocked_route: "/learn",
  };
  if (process.env.AUDIT_EVIDENCE) writeFileSync(process.env.AUDIT_EVIDENCE, JSON.stringify(output, null, 2) + "\n");
  console.log(JSON.stringify(output, null, 2));
} finally {
  await browser.close();
}
