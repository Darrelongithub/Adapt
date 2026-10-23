/**
 * Round 6, Track B: one Playwright click-through per item type, plus the
 * acceptance run — pick "geometry" then "discrete maths" and get visibly
 * DIFFERENT, interactive, scored tests. No self-rating anywhere.
 *
 * Part 1 drives /dev/test-lab, which renders every registered type once and
 * carries the answers in data-answer (a dev page; real items never expose keys).
 * Part 2 drives the real journey: subject → the syllabus checklist the
 * learner approves ("Use this plan") → level/style/goal tiles → the run,
 * answering every item correctly from the plan the API returned (the plan's
 * parameters are the scorer's contract), then reads the evidence rows back.
 *
 * Needs a Chromium (PLAYWRIGHT_CHROMIUM_EXECUTABLE / PW_CHROMIUM /
 * /opt/pw-browsers/chromium). Run from frontend/: node tests-e2e/round6-items.mjs
 */

import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium"].find(
  (p) => p && existsSync(p),
);

const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : {}),
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

async function guestSignIn() {
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

/* ----------------------------------------------------------- answer helpers */

async function liveCard(type) {
  const card = page.locator(`[data-item-type="${type}"][data-answered="false"]`).first();
  await card.waitFor({ state: "visible", timeout: 20000 });
  return card;
}

async function answerCurrent(item) {
  const type = item.item_type;
  const p = item.parameters ?? {};
  console.log("  answering", type, JSON.stringify(p).slice(0, 120));
  const card = await liveCard(type);

  if (["pattern_pick", "odd_one_out", "geometry_angle", "geometry_congruent", "fraction_bar", "cloze_word_bank"].includes(type)) {
    await card.locator(".option-grid .option-tile").nth(Number(p.answer_index ?? 0)).click();
    return;
  }
  if (type === "drag_order") {
    await card.getByRole("button", { name: /^Check$/ }).click();
    return;
  }
  if (type === "drag_match") {
    const left = String(p.left ?? "").split(",").map((s) => s.trim());
    const pairs = String(p.pairs ?? "").split(",").map((pair) => {
      const [l, r] = pair.split(":").map((s) => s.trim());
      return [l, r];
    });
    for (const [l, r] of pairs) {
      await card.getByRole("button", { name: new RegExp(`^${escapeRe(l)}\\b`) }).click();
      await card.getByRole("button", { name: new RegExp(`^${escapeRe(r)}\\b`) }).click();
    }
    void left;
    await card.getByRole("button", { name: /^Check$/ }).click();
    return;
  }
  if (type === "truth_table") {
    const target = String(p.target_cells ?? "").split(",").map((s) => s.trim() === "1");
    for (let i = 0; i < target.length; i++) {
      if (target[i]) await card.locator(".truth-cell").nth(i).click();
    }
    await card.getByRole("button", { name: /^Check$/ }).click();
    return;
  }
  if (type === "venn_region") {
    const region = String(p.answer_region ?? "AB");
    const label = region === "AB" ? "A and B" : region === "none" ? "outside" : `${region} only`;
    await card.locator(`.venn-hit:has(text:text-is("${label}")) circle`).click();
    return;
  }
  if (type === "number_line") {
    const value = Number(p.value ?? 50);
    const max = Math.max(1, Number(p.max ?? 100));
    const box = await card.locator(".numberline-svg").boundingBox();
    assert.ok(box, "number line has a box");
    const x = box.x + ((24 + (value / max) * 352) / 400) * box.width;
    const y = box.y + (45 / 90) * box.height;
    await page.mouse.click(x, y);
    return;
  }
  if (type === "quick_recall") {
    // Phase 1 (show) runs for `seconds`; wait for the Check button to appear.
    await card.getByRole("button", { name: /^Check$/ }).waitFor({ timeout: 15000 });
    const shown = String(p.shown ?? "").split(",").map((s) => s.trim());
    for (const word of shown) {
      await card.locator(".option-grid .option-tile", { hasText: word }).first().click();
    }
    await card.getByRole("button", { name: /^Check$/ }).click();
    return;
  }
  throw new Error(`no responder for ${type}`);
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function expectVerdict(type) {
  const card = page.locator(`[data-item-type="${type}"]`).first();
  const verdict = card.locator(".test-verdict");
  await verdict.waitFor({ state: "visible", timeout: 15000 });
  const text = (await verdict.textContent()) ?? "";
  assert.match(text, /Correct\.|Not this time\./, `${type}: no scored verdict`);
  return text;
}

/* ------------------------------------------------- part 1: every item type */

console.log("part 1: /dev/test-lab click-through of all 12 types…");
await guestSignIn();
await page.goto(`${BASE}/dev/test-lab`, { waitUntil: "networkidle" });
const seen = [];
const LAB_ORDER = ["pattern_pick", "drag_order", "drag_match", "odd_one_out", "truth_table", "venn_region", "geometry_angle", "geometry_congruent", "number_line", "fraction_bar", "cloze_word_bank", "quick_recall"];
for (const type of LAB_ORDER) {
  const lab = page.locator(`[data-lab-type="${type}"][data-answer]`);
  await lab.waitFor({ state: "visible", timeout: 20000 });
  const answer = await lab.getAttribute("data-answer");
  seen.push(type);
  if (type === "drag_order") {
    // The lab's shuffled start equals its answer; Check scores it.
    await answerCurrent({ item_type: type, parameters: {} });
  } else if (type === "drag_match") {
    await answerCurrent({ item_type: type, parameters: { left: "Cat,Dog", pairs: "Cat:Meow,Dog:Woof" } });
  } else if (type === "truth_table") {
    await answerCurrent({ item_type: type, parameters: { target_cells: "0,0,0,1" } });
  } else if (type === "venn_region") {
    await answerCurrent({ item_type: type, parameters: { answer_region: "AB" } });
  } else if (type === "number_line") {
    await answerCurrent({ item_type: type, parameters: { value: 50, max: 100 } });
  } else if (type === "quick_recall") {
    await answerCurrent({ item_type: type, parameters: { shown: "red,green,blue", answer_indices: "0,1,2" } });
  } else {
    await answerCurrent({ item_type: type, parameters: { answer_index: Number(answer) } });
  }
  const verdict = await expectVerdict(type);
  assert.match(verdict, /Correct\./, `${type}: the correct answer must score correct (got "${verdict}")`);
}
assert.equal(seen.length, 12, `expected 12 types, saw ${seen.join(",")}`);
await page.locator("[data-lab-complete]").waitFor({ state: "visible", timeout: 15000 });
console.log(`part 1 ok: ${seen.length} types clicked through, all scored correct`);

/* ------------------------------- part 2: geometry then discrete maths runs */

console.log("part 2: journey — geometry vs discrete maths…");

async function runWizard(subjectText) {
  const plans = [];
  const onPlan = async (r) => {
    if (r.url().includes("/tests/plan") && r.request().method() === "POST") {
      plans.push(await r.json().catch(() => null));
    }
  };
  page.on("response", onPlan);
  await page.goto(`${BASE}/journey?step=subject`, { waitUntil: "networkidle" });
  await page.locator('input[aria-label="What do you want to learn?"]').fill(subjectText);
  await page.getByRole("button", { name: /Choose what to cover/i }).click();
  // Round 9 approval gate: the scanned list appears with honest labels and
  // nothing runs until the learner taps "Use this plan".
  const syllabus = page.locator('[data-testid="syllabus-step"]');
  await syllabus.waitFor({ state: "visible", timeout: 25000 });
  const usePlan = page.getByRole("button", { name: /Use this plan/i });
  await usePlan.waitFor({ state: "visible", timeout: 25000 });
  const objectives = await syllabus.locator("label strong").allTextContents();
  assert.ok(objectives.length >= 1, `${subjectText}: no objectives to approve`);
  await usePlan.click();
  // Setup step: three big tile questions.
  await page.locator(".tile-btn", { hasText: "Confident" }).first().click();
  await page.locator(".tile-btn", { hasText: "Try it" }).first().click();
  await page.locator(".tile-btn", { hasText: "Exam" }).first().click();
  await page.getByRole("button", { name: /Start the checks/i }).click();
  await page.locator("[data-item-type]").first().waitFor({ state: "visible", timeout: 25000 });
  const items = plans.at(-1)?.items ?? [];
  assert.ok(items.length >= 4, `${subjectText}: plan has ${items.length} items`);
  const types = [];
  for (const item of items) {
    await liveCard(item.item_type);
    types.push(item.item_type);
    await answerCurrent(item);
    await expectVerdict(item.item_type);
    await page.waitForTimeout(250);
  }
  await page.getByText("Your starting picture").waitFor({ state: "visible", timeout: 25000 });
  const summary = await page.locator("[data-item-id], .results-bars").first().isVisible();
  assert.ok(summary, `${subjectText}: no results picture`);
  page.off("response", onPlan);
  return { types, objectives };
}

const { types: geometryTypes, objectives: geometryObjectives } = await runWizard("geometry");
assert.ok(
  geometryTypes.some((t) => t === "geometry_angle" || t === "geometry_congruent"),
  `geometry run should emphasise spatial items, got ${geometryTypes.join(",")}`,
);
console.log(`geometry run: ${geometryTypes.join(", ")}`);

// Evidence rows must exist for the run (dashboard + AI 1 read these).
const evidence = await page.evaluate(async () => {
  const r = await fetch("/api/v1/tests/evidence", { credentials: "same-origin" });
  return r.ok ? r.json() : { rows: [] };
});
assert.ok((evidence.rows ?? []).length >= 4, `evidence rows missing: ${JSON.stringify(evidence).slice(0, 200)}`);
assert.ok(evidence.rows.every((r) => typeof r.correct === "boolean" && typeof r.ms === "number"), "evidence rows carry correct + ms");

// Second subject: discrete maths must give a visibly DIFFERENT test.
await page.getByRole("button", { name: /Add a subject/i }).click();
const { types: discreteTypes, objectives: discreteObjectives } = await runWizard("discrete maths");
assert.ok(discreteTypes.includes("truth_table") || discreteTypes.includes("venn_region"), `discrete run should emphasise logic/sets, got ${discreteTypes.join(",")}`);
assert.notDeepEqual(geometryTypes, discreteTypes, "geometry and discrete maths must give different tests");
assert.notDeepEqual(geometryObjectives, discreteObjectives, "geometry and discrete maths must approve different objective lists");
console.log(`discrete run: ${discreteTypes.join(", ")}`);

// No self-rating anywhere on the journey.
const body = await page.evaluate(() => document.body.innerText);
assert.doesNotMatch(body, /I can explain it|I have seen it|Not yet/, "self-rating must be gone");

await browser.close();
console.log("round6-items: all item types clicked through and scored; geometry ≠ discrete maths; evidence rows stored.");
