/** Regression: every suggested subject has an authored, usable first lesson. */
import assert from "node:assert/strict";
import { existsSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const expectedText = {
  "Discrete structures": /set|membership/i,
  Kiswahili: /word|agreement|maneno|maana|ngeli|noun/i,
  Chemistry: /atom|molecule|ion/i,
  Biology: /cell|living/i,
};
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium", "/tmp/chromium"].find((p) => p && existsSync(p));
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

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
  await page.goto(`${BASE}/journey?step=subject`, { waitUntil: "networkidle" });
  const chips = (await page.locator('[aria-label="Suggestions"] button').allTextContents()).map(text => text.trim());
  assert.deepEqual(chips, Object.keys(expectedText));
  assert.equal(chips.includes("Geometry"), false);

  const lessons = [];
  for (const topic of chips) {
    await page.locator('[aria-label="Suggestions"] button', { hasText: topic }).click();
    const result = await page.evaluate(async (wanted) => {
      const conceptsResponse = await fetch(`/api/v1/lesson/concepts?topic=${encodeURIComponent(wanted)}`, { credentials: "same-origin" });
      const concepts = await conceptsResponse.json();
      if (!conceptsResponse.ok || !concepts.concepts?.length) return { conceptsStatus: conceptsResponse.status, concepts };
      const token = document.cookie.split("; ").find(part => part.startsWith("adapt_csrf="))?.split("=").slice(1).join("=") ?? "";
      const planResponse = await fetch("/api/v1/lesson/plan", {
        method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json", "x-csrf-token": decodeURIComponent(token) },
        body: JSON.stringify({ topic: wanted, concept_id: concepts.concepts[0].id }),
      });
      return { conceptsStatus: conceptsResponse.status, concepts, planStatus: planResponse.status, plan: await planResponse.json() };
    }, topic);
    assert.equal(result.conceptsStatus, 200);
    assert.equal(result.concepts.topic_match, true, topic);
    assert.equal(result.concepts.source, "authored", topic);
    assert.equal(result.planStatus, 200, JSON.stringify(result.plan));
    const text = JSON.stringify(result.plan.plan.blocks);
    assert.match(text, expectedText[topic], `${topic} returned generic text`);
    assert.ok(result.plan.plan.blocks.some(block => ["check", "predict"].includes(block.type)), `${topic} has no check/reflection`);
    lessons.push({ topic, concept: result.concepts.concepts[0], topic_match: result.concepts.topic_match, source: result.concepts.source, block_types: result.plan.plan.blocks.map(block => block.type), text_sample: text.slice(0, 500) });
  }

  const output = { check: "E02", outcome: "PASS", geometry_suggestion_offered: false, suggestions: chips, lessons };
  if (process.env.AUDIT_EVIDENCE) writeFileSync(process.env.AUDIT_EVIDENCE, JSON.stringify(output, null, 2) + "\n");
  console.log(JSON.stringify(output, null, 2));
} finally {
  await browser.close();
}
