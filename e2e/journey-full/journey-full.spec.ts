/**
 * Round 2, Phase G: the whole journey in a real browser, against the scripted fake model.
 *
 * What this proves that no unit test can: that the eight-stage path laid out by Phase D,
 * the prepared-pack route from Phase B, and Phase C's three lesson moments work as one
 * product on a desktop and on a phone — account → preferences → games → subject →
 * subject_round → snapshot → first lesson (written by the lesson writer, upgraded in
 * place) → wrong-answer explanation that quotes the answer actually chosen → "I don't
 * get it" re-teach that changes method every time → practice.
 *
 * The API must be running with `scripts/serve_with_fake_model.py` (the real app; only the
 * provider client is a scripted fake) and the web app built and served on :3000.
 * Every stage fact is read back from the server (`GET /api/v1/journey/state`), so a page
 * that painted the right thing without recording it fails here.
 */
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const API = process.env.ADAPT_E2E_API ?? "http://127.0.0.1:8000";
const TOPIC = "kite building";
const SHOTS = resolve(process.cwd(), "..", "..", "docs", "screens", "G");

async function journeyState(context: BrowserContext): Promise<{
  stage: string;
  stages: { id: string; status: string }[];
}> {
  const response = await context.request.get(`${API}/api/v1/journey/state`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function stageStatus(context: BrowserContext, stage: string): Promise<string> {
  const state = await journeyState(context);
  const row = state.stages.find((entry) => entry.id === stage);
  return row?.status ?? "missing";
}

async function shot(page: Page, project: string, name: string): Promise<void> {
  mkdirSync(resolve(SHOTS, project), { recursive: true });
  await page.screenshot({ path: resolve(SHOTS, project, `${name}.png`), fullPage: false });
}

test("the full journey with the three lesson moments", async ({ page, context }, info) => {
  const project = info.project.name ?? "desktop";

  // -- 1. account: the same call the home screen makes; the context shares its cookies --
  const created = await context.request.post(`${API}/api/v1/students`, {
    data: { display_name: "Journey Tester", age_band: "16-18", as_guest: true },
  });
  expect(created.ok(), await created.text()).toBeTruthy();

  await page.goto("/journey", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("journey-card")).toBeVisible({ timeout: 60_000 });
  expect((await journeyState(context)).stage).toBe("preferences");
  await shot(page, project, "01-journey-preferences");

  // -- 2..6. every skippable stage: skipping is a recorded answer, not a failure --------
  for (const stage of ["preferences", "games", "subject", "subject_round", "snapshot"]) {
    await page.goto("/journey", { waitUntil: "domcontentloaded" });
    // the stage list's own row (the current-step card carries data-stage too)
    const row = page.locator(`[data-testid='journey-card'][data-stage='${stage}']`);
    await row.scrollIntoViewIfNeeded();
    await row.locator("button", { hasText: /^skip this step$/i }).first().click();
    await expect
      .poll(async () => stageStatus(context, stage), { timeout: 30_000 })
      .toBe("skipped");
  }
  await shot(page, project, "02-journey-skips-recorded");
  expect((await journeyState(context)).stage).toBe("first_lesson");

  // -- 7. first lesson: through the journey's own link, on a typed topic ---------------
  await page.goto("/journey", { waitUntil: "domcontentloaded" });
  await page.locator("[data-testid='journey-card'] a").first().click();
  await expect(page).toHaveURL(/\/first-lesson/, { timeout: 30_000 });

  await page.locator("[data-testid='fl-topic']").fill(TOPIC);
  await page.locator("[data-testid='fl-go']").click();
  // preparation (a real background job) then the concept list
  await expect(page.locator("[data-testid='fl-concept']").first()).toBeVisible({ timeout: 120_000 });
  await shot(page, project, "03-concepts");
  await page.locator("[data-testid='fl-concept']").first().click();

  // the deterministic lesson answers first; the checked lesson upgrades in place
  await expect(page.locator("[data-testid='fl-moment']").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Written for you").first()).toBeVisible({ timeout: 120_000 });
  await shot(page, project, "04-first-lesson-written-for-you");

  // -- the wrong-answer moment starts from the answer actually chosen ------------------
  // Round 3 (Track S): clicking an option now registers the answer with the
  // server, the server grades it, and the wrong-answer moment appears
  // automatically — there is no longer a "I picked this and it was wrong"
  // button to press.  The lesson is wrong-answer-by-default when a wrong
  // option is clicked; the test now expects the second moment to follow
  // the click without any further action.
  const options = page.locator("[data-testid='fl-option']");
  await expect(options.first()).toBeVisible({ timeout: 30_000 });
  const chosenText = (await options.nth(1).innerText()).trim();
  await options.nth(1).click();
  const explanation = page.locator("[data-testid='fl-moment']").nth(1);
  await expect(explanation).toBeVisible({ timeout: 60_000 });
  await expect(explanation).toContainText("Why that answer feels right", { timeout: 30_000 });
  await expect(explanation).toContainText(chosenText, { timeout: 30_000 });
  await shot(page, project, "05-wrong-answer-explanation");

  // -- "I don't get it": a different method every time --------------------------------
  await page.locator("[data-testid='fl-reteach']").click();
  const firstReteach = page.locator("[data-testid='fl-moment']").nth(2);
  await expect(firstReteach).toContainText("Another way", { timeout: 60_000 });
  await expect(firstReteach).toContainText("plainer words", { timeout: 30_000 });

  await page.locator("[data-testid='fl-reteach']").click();
  const secondReteach = page.locator("[data-testid='fl-moment']").nth(3);
  await expect(secondReteach).toContainText("smaller step", { timeout: 60_000 });
  await shot(page, project, "06-reteach-different-way");

  // -- mark the lesson done from the journey, then reach the practice loop -------------
  await page.goto("/journey", { waitUntil: "domcontentloaded" });
  await page.getByText("see every step", { exact: true }).click();
  await page.getByTestId("journey-mark-done").click();
  await expect.poll(async () => stageStatus(context, "first_lesson"), { timeout: 30_000 }).toBe("done");
  expect((await journeyState(context)).stage).toBe("practice");

  await page.goto("/learn", { waitUntil: "domcontentloaded" });
  await expect(page.locator("main")).toBeVisible({ timeout: 60_000 });
  await shot(page, project, "07-practice");

  // -- the server's record of the whole path -------------------------------------------
  const finalState = await journeyState(context);
  const status = Object.fromEntries(finalState.stages.map((row) => [row.id, row.status]));
  expect(status.account).toBe("done");
  for (const stage of ["preferences", "games", "subject", "subject_round", "snapshot"]) {
    expect(status[stage], stage).toBe("skipped");
  }
  expect(status.first_lesson).toBe("done");

  // -- phone honesty: nothing may overflow the viewport --------------------------------
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "horizontal scroll").toBeLessThanOrEqual(1);
});
