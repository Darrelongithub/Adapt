/** Regression: an account-owned subject run resumes in fresh browser profiles. */
import assert from "node:assert/strict";
import { existsSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium", "/tmp/chromium"].find((p) => p && existsSync(p));
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const username = `c06_${Date.now().toString(36)}`;
const password = "correct horse 42";
let planPosts = 0;
let runReads = 0;
const evidencePosts = [];
let page;

function watch(target) {
  target.on("request", (request) => {
    if (request.url().includes("/api/v1/tests/plan") && request.method() === "POST") planPosts += 1;
    if (request.url().includes("/api/v1/tests/run") && request.method() === "GET") runReads += 1;
  });
  target.on("response", (response) => {
    if (response.url().includes("/api/v1/tests/evidence") && response.request().method() === "POST") {
      void response.json().then((body) => evidencePosts.push(body)).catch(() => {});
    }
  });
}

async function freshProfile() {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage();
  watch(page);
  await page.addInitScript(() => sessionStorage.setItem("adapt:seen-opening", "1"));
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const opening = page.locator(".opening-overlay");
  if (await opening.count()) {
    await opening.click({ position: { x: 10, y: 10 } });
    await opening.waitFor({ state: "detached", timeout: 5000 });
  }
  const localKeys = await page.evaluate(() => Object.keys(localStorage));
  assert.equal(localKeys.filter((key) => key.startsWith("adapt:subject-run")).length, 0);
  return { context, localKeys };
}

async function enter(mode) {
  await page.getByRole("button", { name: /Save choice and continue/i }).click();
  if (mode === "create") await page.getByRole("tab", { name: /Create account/i }).click();
  await page.getByLabel(/^Username/i).fill(username);
  await page.getByLabel(/^Password/i).fill(password);
  await page.getByRole("button", { name: mode === "create" ? /^Create account$/i : /^Sign in$/i }).click();
  await page.locator("[data-account-gate]").waitFor({ state: "detached", timeout: 15000 });
  const me = await page.evaluate(async () => (await fetch("/api/v1/me", { credentials: "same-origin" })).json());
  assert.ok(me.student_id);
  return me.student_id;
}

async function openJourney() {
  await page.goto(`${BASE}/journey?step=subject`, { waitUntil: "networkidle" });
}

async function answer(item) {
  const card = page.locator(`.test-item[data-item-id="${item.id}"]`);
  await card.waitFor({ state: "visible", timeout: 20000 });
  const type = item.item_type;
  if (["pattern_pick", "odd_one_out", "geometry_angle", "geometry_congruent", "fraction_bar", "cloze_word_bank"].includes(type)) {
    await card.locator(".option-grid .option-tile").first().click();
  } else if (type === "number_line") {
    const box = await card.locator(".numberline-svg").boundingBox();
    assert.ok(box);
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  } else if (type === "quick_recall") {
    await card.getByRole("button", { name: /^Check$/ }).waitFor({ timeout: 15000 });
    const count = String(item.parameters.shown ?? "").split(",").filter(Boolean).length;
    for (let index = 0; index < count; index += 1) await card.locator(".option-grid .option-tile").nth(index).click();
    await card.getByRole("button", { name: /^Check$/ }).click();
  } else if (type === "venn_region") {
    await card.locator(".venn-hit circle").first().click();
  } else {
    await card.getByRole("button", { name: /^Check$/ }).click();
  }
  await card.locator(".test-verdict").waitFor({ state: "visible", timeout: 15000 });
  return (await card.locator(".test-verdict").textContent())?.trim() ?? "";
}

try {
  const first = await freshProfile();
  const studentId = await enter("create");
  await openJourney();
  await page.locator('input[aria-label="What do you want to learn?"]').fill("Biology");
  await page.getByRole("button", { name: /See the starting checks/i }).click();
  await page.locator(".tile-btn", { hasText: "Confident" }).first().click();
  await page.locator(".tile-btn", { hasText: "Try it" }).first().click();
  await page.locator(".tile-btn", { hasText: "Exam" }).first().click();
  const planResponse = page.waitForResponse((response) => response.url().includes("/api/v1/tests/plan") && response.request().method() === "POST");
  await page.getByRole("button", { name: /Start the checks/i }).click();
  const plan = await (await planResponse).json();
  assert.equal(planPosts, 1);
  assert.ok(plan.items.length >= 4);
  const verdicts = [await answer(plan.items[0])];
  await page.locator(`.test-item[data-item-id="${plan.items[1].id}"]`).waitFor({ timeout: 15000 });
  await first.context.close();

  const second = await freshProfile();
  assert.equal(await enter("signin"), studentId);
  await openJourney();
  await page.locator(`.test-item[data-item-id="${plan.items[1].id}"]`).waitFor({ timeout: 20000 });
  assert.equal(planPosts, 1, "fresh sign-in requested a replacement plan");
  const resumedType = await page.locator(".test-item").getAttribute("data-item-type");
  assert.equal(resumedType, plan.items[1].item_type);
  for (let index = 1; index < plan.items.length; index += 1) verdicts.push(await answer(plan.items[index]));
  await page.getByText("Your starting picture").waitFor({ timeout: 20000 });
  const resultsBefore = (await page.locator(".results-bars").innerText()).trim();
  const evidence = await page.evaluate(async (runId) => {
    const response = await fetch(`/api/v1/tests/evidence?run_id=${encodeURIComponent(runId)}`, { credentials: "same-origin" });
    return response.json();
  }, plan.run_id);
  assert.equal(evidence.rows.length, plan.items.length);
  await page.waitForTimeout(100);
  assert.equal(evidencePosts.at(-1)?.stored, 0, "final batch duplicated server-graded rows");
  await second.context.close();

  const third = await freshProfile();
  assert.equal(await enter("signin"), studentId);
  await openJourney();
  await page.getByText("Your starting picture").waitFor({ timeout: 20000 });
  const resultsAfter = (await page.locator(".results-bars").innerText()).trim();
  assert.equal(resultsAfter, resultsBefore);
  assert.equal(planPosts, 1, "fresh results restore requested a replacement plan");
  assert.ok(runReads >= 2);
  await third.context.close();

  const output = {
    check: "C06",
    outcome: "PASS",
    run_id: plan.run_id,
    student_id: studentId,
    plan_post_count: planPosts,
    server_run_read_count: runReads,
    fresh_profile_local_keys: [first.localKeys, second.localKeys, third.localKeys],
    resumed_at: { item_id: plan.items[1].id, item_type: resumedType, fresh_profile: true },
    verdicts,
    result_bars_before_fresh_restore: resultsBefore,
    result_bars_after_fresh_restore: resultsAfter,
    final_batch_response: evidencePosts.at(-1),
    stored_row_count: evidence.rows.length,
  };
  if (process.env.AUDIT_EVIDENCE) writeFileSync(process.env.AUDIT_EVIDENCE, JSON.stringify(output, null, 2) + "\n");
  console.log(JSON.stringify(output, null, 2));
} finally {
  await browser.close();
}
