/**
 * The current subject round through the real journey page.
 *
 * The retired /subject outline and subject-map selectors are not reproduced here.
 * These tests cover their current replacement: a relevant sealed plan with no
 * learner-facing answer key, a complete code-graded run, and evidence read-back.
 */
import { expect, test, type Page } from "@playwright/test";
import { newGuest, noHorizontalScroll, shot } from "./lib/driver";

type PlanItem = {
  id: string;
  item_type: string;
  run_id?: string;
  grading_seal?: string;
  parameters: Record<string, unknown>;
};
type Plan = { run_id: string; items: PlanItem[] };

const FORBIDDEN_GRADING_KEYS = new Set([
  "answer",
  "answer_index",
  "answer_indices",
  "answer_region",
  "answer_spec",
  "correct",
  "correct_index",
  "pairs",
  "target_cells",
]);

function expectPublicParameters(value: unknown, path = "parameters"): void {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => expectPublicParameters(entry, `${path}[${index}]`));
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    expect(FORBIDDEN_GRADING_KEYS.has(key), `${path}.${key} exposes grading data`).toBe(false);
    expectPublicParameters(child, `${path}.${key}`);
  }
}

async function startSubject(page: Page, topic: string): Promise<Plan> {
  await page.goto("/journey?step=subject");
  const input = page.getByRole("textbox", { name: "What do you want to learn?" });
  await input.fill(topic);
  const chosenResponse = page.waitForResponse(
    response => response.url().includes("/api/v1/subjects/choose") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "See the starting checks", exact: true }).click();
  expect((await chosenResponse).status()).toBe(200);
  await expect(page.getByRole("heading", { name: new RegExp(`Starting checks for ${topic}`, "i") })).toBeVisible();
  await page.getByRole("group", { name: "Level", exact: true }).getByRole("button").first().click();
  await page.getByRole("group", { name: "Learning style", exact: true }).getByRole("button").first().click();
  await page.getByRole("group", { name: "Goal", exact: true }).getByRole("button").first().click();
  const planResponse = page.waitForResponse(
    response => response.url().includes("/api/v1/tests/plan") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start the checks", exact: true }).click();
  const response = await planResponse;
  expect(response.status(), await response.text()).toBe(200);
  const plan = (await response.json()) as Plan;
  expect(plan.run_id).toBeTruthy();
  expect(plan.items.length).toBeGreaterThanOrEqual(4);
  expect(plan.items.length).toBeLessThanOrEqual(8);
  for (const item of plan.items) {
    expect(item.run_id).toBe(plan.run_id);
    expect(item.grading_seal).toBeTruthy();
    expectPublicParameters(item.parameters);
  }
  await expect(page.locator(`[data-item-id="${plan.items[0].id}"]`)).toBeVisible();
  return plan;
}

test("the current subject plan is relevant, sealed, and hides its scoring fields", async ({ page }) => {
  await newGuest(page);
  const plan = await startSubject(page, "geometry");
  const types = plan.items.map(item => item.item_type);
  expect(types).toContain("geometry_angle");
  expect(types).toContain("geometry_congruent");
  await noHorizontalScroll(page, "the current subject run");
});

test("a whole current subject round stores one readable evidence row per item", async ({ page }, info) => {
  await newGuest(page);
  const plan = await startSubject(page, "geometry");
  for (const item of plan.items) {
    const card = page.locator(`[data-item-id="${item.id}"]`);
    await expect(card).toHaveAttribute("data-item-type", item.item_type);
    await answerPublicItem(page, card, item);
    await expect(card.locator(".test-verdict")).toContainText(/Correct\.|Not this time\./);
    await expect(card).toHaveCount(0, { timeout: 20_000 });
  }
  await expect(page.getByText("Your starting picture", { exact: true })).toBeVisible();
  const response = await page.request.get("/api/v1/tests/evidence");
  expect(response.status(), await response.text()).toBe(200);
  const payload = (await response.json()) as { rows: Record<string, unknown>[] };
  const rows = payload.rows.filter(row => row.run_id === plan.run_id);
  expect(rows.map(row => row.item_id).sort()).toEqual(plan.items.map(item => item.id).sort());
  expect(new Set(rows.map(row => row.id)).size).toBe(plan.items.length);
  for (const row of rows) {
    expect(typeof row.correct).toBe("boolean");
    expect(typeof row.ms).toBe("number");
  }
  await noHorizontalScroll(page, "the subject results");
  await shot(page, info.project.name, "subject-results-current");
});

async function answerPublicItem(page: Page, card: import("@playwright/test").Locator, item: PlanItem): Promise<void> {
  const parameters = item.parameters;
  if (["pattern_pick", "odd_one_out", "geometry_angle", "geometry_congruent", "fraction_bar", "cloze_word_bank"].includes(item.item_type)) {
    await card.locator(".option-grid .option-tile").first().click();
    return;
  }
  if (item.item_type === "drag_order") {
    await card.getByRole("button", { name: "Check", exact: true }).click();
    return;
  }
  if (item.item_type === "drag_match") {
    const left = card.getByRole("group", { name: "left side" }).getByRole("button");
    const right = card.getByRole("group", { name: "right side" }).getByRole("button");
    const rightCount = await right.count();
    expect(rightCount).toBeGreaterThan(0);
    for (let index = 0; index < await left.count(); index += 1) {
      await left.nth(index).click();
      await right.nth(index % rightCount).click();
    }
    await card.getByRole("button", { name: "Check", exact: true }).click();
    return;
  }
  if (item.item_type === "truth_table") {
    await card.getByRole("button", { name: "Check", exact: true }).click();
    return;
  }
  if (item.item_type === "venn_region") {
    await card.locator(".venn-hit").first().locator("circle").click();
    return;
  }
  if (item.item_type === "number_line") {
    const value = Number(parameters.value ?? 50);
    const max = Math.max(1, Number(parameters.max ?? 100));
    const box = await card.locator(".numberline-svg").boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.click(box!.x + ((24 + (value / max) * 352) / 400) * box!.width, box!.y + box!.height / 2);
    return;
  }
  if (item.item_type === "quick_recall") {
    const check = card.getByRole("button", { name: "Check", exact: true });
    await expect(check).toBeVisible({ timeout: 15_000 });
    await card.locator(".option-grid .option-tile").first().click();
    await check.click();
    return;
  }
  throw new Error(`No public-control responder for ${item.item_type}`);
}
