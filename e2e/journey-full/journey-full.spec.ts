/**
 * Whole-journey browser integration against the scripted fake model.
 *
 * This is not real-model evidence. It drives the real routes, validators and
 * database with only the provider client replaced. The current journey card is
 * used where it offers a skip; stages without that control are advanced through
 * the same CSRF-protected server endpoint and read back from journey/state.
 */
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const API = process.env.ADAPT_E2E_API ?? "http://127.0.0.1:8000";
const TOPIC = "kite building";
const SHOTS = resolve(process.cwd(), "..", "..", "docs", "screens", "G");
const SKIPPED = ["preferences", "games", "subject", "subject_round", "snapshot"] as const;

type JourneyState = {
  stage: string;
  stages: { id: string; status: string }[];
};

async function journeyState(context: BrowserContext): Promise<JourneyState> {
  const response = await context.request.get(`${API}/api/v1/journey/state`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function stageStatus(context: BrowserContext, stage: string): Promise<string> {
  const state = await journeyState(context);
  return state.stages.find(entry => entry.id === stage)?.status ?? "missing";
}

async function advance(context: BrowserContext, stage: string): Promise<void> {
  const csrf = (await context.cookies(API)).find(cookie => cookie.name === "adapt_csrf")?.value;
  const response = await context.request.post(`${API}/api/v1/journey/advance`, {
    data: { stage, status: "skipped" },
    headers: csrf ? { "x-csrf-token": csrf } : {},
  });
  expect(response.status(), await response.text()).toBe(200);
}

async function shot(page: Page, project: string, name: string): Promise<void> {
  mkdirSync(resolve(SHOTS, project), { recursive: true });
  await page.screenshot({ path: resolve(SHOTS, project, `${name}.png`), fullPage: false });
}

test("the current whole journey with the three lesson moments", async ({ page, context }, info) => {
  const project = info.project.name ?? "desktop";
  const created = await context.request.post(`${API}/api/v1/students`, {
    data: { display_name: "Journey Tester", age_band: "16-18", as_guest: true, consent_analytics: false },
  });
  expect(created.status(), await created.text()).toBe(200);

  await page.goto("/journey", { waitUntil: "networkidle" });
  await expect(page.getByTestId("journey-card")).toHaveAttribute("data-stage", "preferences", { timeout: 60_000 });
  expect((await journeyState(context)).stage).toBe("preferences");
  await shot(page, project, "01-journey-preferences");

  for (let guard = 0; guard < 8 && (await journeyState(context)).stage !== "first_lesson"; guard += 1) {
    const state = await journeyState(context);
    await page.goto("/journey", { waitUntil: "networkidle" });
    const skip = page.getByRole("button", { name: "Skip this step", exact: true });
    if (await skip.count()) {
      const response = page.waitForResponse(
        row => row.url().includes("/api/v1/journey/advance") && row.request().method() === "POST",
      );
      await skip.click();
      expect((await response).status()).toBe(200);
    } else {
      await advance(context, state.stage);
    }
    await expect.poll(async () => stageStatus(context, state.stage), { timeout: 30_000 }).toBe("skipped");
  }
  expect((await journeyState(context)).stage).toBe("first_lesson");
  await shot(page, project, "02-journey-skips-recorded");

  await page.goto("/journey", { waitUntil: "networkidle" });
  await page.locator("[data-testid='journey-card'] a").first().click();
  await expect(page).toHaveURL(/\/first-lesson/, { timeout: 30_000 });
  await page.getByTestId("fl-topic").fill(TOPIC);
  await page.getByTestId("fl-go").click();
  await expect(page.getByTestId("fl-concept").first()).toBeVisible({ timeout: 120_000 });
  await shot(page, project, "03-concepts");
  await page.getByTestId("fl-concept").first().click();

  await expect(page.getByTestId("fl-moment").first()).toBeVisible({ timeout: 60_000 });
  // The deterministic plan is upgraded in place. This label comes from the
  // written_for_you payload, not from the static page heading.
  await expect(page.getByText("Written for you", { exact: true }).first()).toBeVisible({ timeout: 120_000 });
  await shot(page, project, "04-first-lesson-written-for-you");

  const options = page.getByTestId("fl-option");
  await expect(options.nth(1)).toBeVisible({ timeout: 30_000 });
  const chosenText = (await options.nth(1).innerText()).trim();
  const gradeResponse = page.waitForResponse(
    response => response.url().includes("/api/v1/lesson/grade") && response.request().method() === "POST",
  );
  await options.nth(1).click();
  const grade = await (await gradeResponse).json() as { correct: boolean };
  expect(grade.correct).toBe(false);
  const explanation = page.getByTestId("fl-moment").nth(1);
  await expect(explanation).toBeVisible({ timeout: 60_000 });
  await expect(explanation).toContainText("Why that answer feels right", { timeout: 30_000 });
  await expect(explanation).toContainText(chosenText, { timeout: 30_000 });
  await shot(page, project, "05-wrong-answer-explanation");

  await page.getByTestId("fl-reteach").click();
  const firstReteach = page.getByTestId("fl-moment").nth(2);
  await expect(firstReteach).toContainText("Another way", { timeout: 60_000 });
  await expect(firstReteach).toContainText("plainer words", { timeout: 30_000 });

  await page.getByTestId("fl-reteach").click();
  const secondReteach = page.getByTestId("fl-moment").nth(3);
  await expect(secondReteach).toContainText("smaller step", { timeout: 60_000 });
  expect(await firstReteach.getAttribute("aria-label")).not.toBe(await secondReteach.getAttribute("aria-label"));
  await shot(page, project, "06-reteach-different-way");

  await page.getByRole("button", { name: "Finish first lesson", exact: true }).click();
  await expect(page.getByRole("button", { name: "First lesson saved", exact: true })).toBeVisible();
  await expect.poll(async () => stageStatus(context, "first_lesson"), { timeout: 30_000 }).toBe("done");
  expect((await journeyState(context)).stage).toBe("practice");

  await page.goto("/learn", { waitUntil: "networkidle" });
  await expect(page.locator("main")).toBeVisible({ timeout: 60_000 });
  await shot(page, project, "07-practice");

  const finalState = await journeyState(context);
  const status = Object.fromEntries(finalState.stages.map(row => [row.id, row.status]));
  expect(status.account).toBe("done");
  for (const stage of SKIPPED) expect(status[stage], stage).toBe("skipped");
  expect(status.first_lesson).toBe("done");

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "horizontal scroll").toBeLessThanOrEqual(1);
});
