/** Regression: a Biology reflection survives completion and reload. */
import assert from "node:assert/strict";
import { existsSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const text = `Cells have specialised parts; reflection ${Date.now().toString(36)}.`;
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium", "/tmp/chromium"].find((p) => p && existsSync(p));
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
let advancePosts = 0;
page.on("request", (request) => { if (request.url().includes("/api/v1/journey/advance") && request.method() === "POST") advancePosts += 1; });

async function openLesson() {
  await page.goto(`${BASE}/first-lesson?topic=Biology`, { waitUntil: "domcontentloaded" });
  await page.locator('[data-testid="fl-concept"]').first().waitFor({ state: "visible", timeout: 90000 });
  await page.locator('[data-testid="fl-concept"]').first().click();
  await page.locator('[data-testid="fl-moment"]').first().waitFor({ state: "visible", timeout: 60000 });
  await page.getByLabel("Your reflection (not marked)").waitFor({ state: "visible", timeout: 30000 });
}

try {
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const opening = page.locator(".opening-overlay");
  if (await opening.count()) {
    await opening.click({ position: { x: 10, y: 10 } });
    await opening.waitFor({ state: "detached", timeout: 5000 });
  }
  await page.getByRole("button", { name: /Save choice and continue/i }).click();
  await page.getByRole("button", { name: /Continue as guest/i }).click();
  await page.locator("[data-account-gate]").waitFor({ state: "detached", timeout: 15000 });
  await openLesson();
  const input = page.getByLabel("Your reflection (not marked)");
  await input.fill(text);
  assert.equal(await input.inputValue(), text);
  await page.getByRole("button", { name: /Finish first lesson/i }).click();
  await page.getByRole("button", { name: /First lesson saved/i }).waitFor({ timeout: 15000 });
  assert.equal(advancePosts, 1);

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator('[data-testid="fl-concept"]').first().waitFor({ state: "visible", timeout: 90000 });
  await page.locator('[data-testid="fl-concept"]').first().click();
  await page.getByLabel("Your reflection (not marked)").waitFor({ state: "visible", timeout: 60000 });
  await page.waitForFunction((expected) => document.querySelector('input[aria-label="Your reflection (not marked)"]')?.value === expected, text, { timeout: 15000 });
  const state = await page.evaluate(async () => (await fetch("/api/v1/journey/state", { credentials: "same-origin" })).json());
  const saved = state.stages.find((row) => row.id === "first_lesson");
  assert.equal(saved.note, text);
  const repeated = await page.evaluate(async (note) => {
    const token = document.cookie.split("; ").find((part) => part.startsWith("adapt_csrf="))?.split("=").slice(1).join("=") ?? "";
    const response = await fetch("/api/v1/journey/advance", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json", "x-csrf-token": decodeURIComponent(token) }, body: JSON.stringify({ stage: "first_lesson", status: "done", note }) });
    return { status: response.status, body: await response.json() };
  }, text);
  assert.equal(repeated.status, 200);
  assert.equal(repeated.body.stages.find((row) => row.id === "first_lesson").note, text);

  const output = {
    check: "E04",
    outcome: "PASS",
    reflection: text,
    ui_completion_post_count: 1,
    total_completion_posts_including_repeat: advancePosts,
    restored_input_after_reload: await input.inputValue(),
    state_note: saved.note,
    repeat_finish_status: repeated.status,
  };
  if (process.env.AUDIT_EVIDENCE) writeFileSync(process.env.AUDIT_EVIDENCE, JSON.stringify(output, null, 2) + "\n");
  console.log(JSON.stringify(output, null, 2));
} finally {
  await browser.close();
}
