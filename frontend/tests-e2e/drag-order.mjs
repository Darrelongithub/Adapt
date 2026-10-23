/** Regression: a reorder must survive rendering and be graded as submitted. */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { writeFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium", "/tmp/chromium"].find((p) => p && existsSync(p));
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const consoleErrors = [];
page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });

try {
  await page.addInitScript(() => sessionStorage.setItem("adapt:seen-opening", "1"));
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /Save choice and continue/i }).click();
  await page.getByRole("button", { name: /Continue as guest/i }).click();
  await page.locator("[data-account-gate]").waitFor({ state: "detached", timeout: 15000 });
  await page.goto(`${BASE}/journey?step=subject`, { waitUntil: "networkidle" });
  await page.locator('input[aria-label="What do you want to learn?"]').fill("discrete maths");
  await page.getByRole("button", { name: /See the starting checks/i }).click();
  await page.locator(".tile-btn", { hasText: "Confident" }).first().click();
  await page.locator(".tile-btn", { hasText: "Try it" }).first().click();
  await page.locator(".tile-btn", { hasText: "Exam" }).first().click();
  const planResponse = page.waitForResponse((response) => response.url().includes("/api/v1/tests/plan") && response.request().method() === "POST");
  await page.getByRole("button", { name: /Start the checks/i }).click();
  const plan = await (await planResponse).json();
  assert.equal(plan.items[2]?.item_type, "drag_order");

  // Reach the third item. Wrong answers are intentional and are server-graded.
  const truth = page.locator('[data-item-type="truth_table"]');
  await truth.waitFor({ state: "visible", timeout: 20000 });
  await truth.getByRole("button", { name: /^Check$/ }).click();
  await truth.locator(".test-verdict").waitFor({ state: "visible" });
  const venn = page.locator('[data-item-type="venn_region"]');
  await venn.waitFor({ state: "visible", timeout: 15000 });
  await venn.locator(".venn-hit circle").first().click();
  await venn.locator(".test-verdict").waitFor({ state: "visible" });

  const card = page.locator('[data-item-type="drag_order"]');
  await card.waitFor({ state: "visible", timeout: 15000 });
  const readOrder = () => card.locator(".order-text").allTextContents();
  const before = await readOrder();
  await card.getByRole("button", { name: "move down" }).first().click();
  await page.waitForTimeout(250); // several React renders; the old effect reset the order here
  const after = await readOrder();
  assert.notDeepEqual(after, before, "the reorder was reset before submission");
  await card.getByRole("button", { name: /^Check$/ }).click();
  const verdict = card.locator(".test-verdict");
  await verdict.waitFor({ state: "visible", timeout: 15000 });
  const verdictText = (await verdict.textContent()) ?? "";
  assert.match(verdictText, /Not this time\./, "the changed order was falsely graded as the original order");
  const evidence = await page.evaluate(async (runId) => {
    const response = await fetch(`/api/v1/tests/evidence?run_id=${encodeURIComponent(runId)}`, { credentials: "same-origin" });
    return { status: response.status, body: await response.json() };
  }, plan.run_id);
  const row = evidence.body.rows?.find((entry) => entry.item_id === "item-2");
  assert.equal(evidence.status, 200);
  assert.equal(row?.correct, false);
  assert.equal(row?.answer, after.join(">"));

  const output = {
    check: "C05",
    outcome: "PASS",
    run_id: plan.run_id,
    order_before: before,
    order_after_clicking_move_down: after,
    verdict: verdictText.trim(),
    stored_evidence: row,
    console_errors: consoleErrors,
  };
  if (process.env.AUDIT_EVIDENCE) writeFileSync(process.env.AUDIT_EVIDENCE, JSON.stringify(output, null, 2) + "\n");
  console.log(JSON.stringify(output, null, 2));
} finally {
  await browser.close();
}
