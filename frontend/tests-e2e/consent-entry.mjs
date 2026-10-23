/** Regression: analytics is off unless the learner explicitly opts in. */
import assert from "node:assert/strict";
import { existsSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium", "/tmp/chromium"].find((p) => p && existsSync(p));
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });

async function journey(optIn) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const creations = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/students") && request.method() === "POST") creations.push(request.postDataJSON());
  });
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const opening = page.locator(".opening-overlay");
  if (await opening.count()) {
    await opening.click({ position: { x: 10, y: 10 } });
    await opening.waitFor({ state: "detached", timeout: 5000 });
  }
  await page.locator("[data-account-gate]").waitFor({ state: "visible", timeout: 15000 });
  const checkbox = page.locator('[data-account-gate] input[type="checkbox"]').first();
  assert.equal(await checkbox.isChecked(), false);
  const proceed = page.getByRole("button", { name: /Save choice and continue/i });
  assert.equal(await proceed.isEnabled(), true);
  assert.equal(creations.length, 0);
  const unauthenticated = await page.evaluate(async () => (await fetch("/api/v1/me", { credentials: "same-origin" })).status);
  assert.equal(unauthenticated, 401);
  assert.equal(creations.length, 0);
  if (optIn) await checkbox.check();
  await proceed.click();
  const createdResponse = page.waitForResponse((response) => response.url().includes("/api/v1/students") && response.request().method() === "POST");
  await page.getByRole("button", { name: /Continue as guest/i }).click();
  const response = await createdResponse;
  assert.equal(response.status(), 200);
  const created = await response.json();
  await page.locator("[data-account-gate]").waitFor({ state: "detached", timeout: 15000 });
  const me = await page.evaluate(async () => (await fetch("/api/v1/me", { credentials: "same-origin" })).json());
  assert.equal(created.consent_analytics, optIn);
  assert.equal(me.consent_analytics, optIn);
  assert.equal(creations.length, 1);
  assert.equal(creations[0].consent_analytics, optIn);
  assert.equal("research" in creations[0], false);
  await context.close();
  return { opt_in: optIn, creation_requests_before_choice: 0, creation_payload: creations[0], response_consent: created.consent_analytics, me_consent: me.consent_analytics, student_id: me.student_id };
}

try {
  const output = { check: "A01", outcome: "PASS", default_off: await journey(false), explicit_on: await journey(true) };
  if (process.env.AUDIT_EVIDENCE) writeFileSync(process.env.AUDIT_EVIDENCE, JSON.stringify(output, null, 2) + "\n");
  console.log(JSON.stringify(output, null, 2));
} finally {
  await browser.close();
}
