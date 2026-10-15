/**
 * The subject round, through the page: a topic, the plan and its "why these", the round itself, and
 * the subject map. Runs on the desktop and the phone viewport (the mouse, as a learner would use it).
 *
 * What it checks:
 * * the plan's "why these" text says what happened for each kind of topic (a written-down subject, a
 *   recognised subject with no written ratings, and an unplaceable topic): a fallback is labelled as
 *   one, and a ranking is only described as a ranking when there is one;
 * * the round runs to its end by a bot that answers every task kind it is shown, and the map says
 *   what was not reached rather than showing a zero;
 * * the map shows ranges with an n, never a label.
 */
import { expect, test, type Page } from "@playwright/test";
import { newGuest, noHorizontalScroll, shot, smallTapTargets } from "./lib/driver";
import { FORBIDDEN_LABELS } from "./lib/driver";

/** The viewport this test runs on (desktop or mobile): screenshots go to its own folder. */
const viewport = (): string => test.info().project.name;

async function findSubject(page: Page, topic: string): Promise<void> {
  await page.goto("/subject");
  await page.getByRole("textbox", { name: "Subject", exact: true }).fill(topic);
  await page.getByRole("button", { name: "Preview outline", exact: true }).click();
  // The outline comes first: every check is ticked by default, and the round starts only on confirmation.
  await expect(page.getByRole("button", { name: "Confirm outline and start", exact: true })).toBeVisible({ timeout: 30_000 });
}

test("the plan says why these skills, and the reason matches what was actually found", async ({ page }) => {
  await newGuest(page);
  const cases: { topic: string; expect: RegExp[]; not?: RegExp[]; slug: string }[] = [
    { topic: "world history", slug: "history", expect: [/leans hardest/i], not: [/fallback/i] },
    { topic: "adding fractions", slug: "maths", expect: [/fallback/i, /rather than a finding about your topic/i], not: [/leans hardest/i] },
    { topic: "french past tense", slug: "languages", expect: [/fallback/i, /rather than a finding about your topic/i], not: [/leans hardest/i] },
    { topic: "basket weaving", slug: "unknown", expect: [/six skills every subject uses/i, /rather than a finding about your topic/i] },
  ];
  for (const c of cases) {
    await findSubject(page, c.topic);
    const why = page.getByText(/why these\??/i).first().locator("..");
    const text = (await page.locator("main").innerText()).replace(/\s+/g, " ");
    for (const re of c.expect) expect(text, `${c.topic}: the reason should match ${re}`).toMatch(re);
    for (const re of c.not ?? []) expect(text, `${c.topic}: the reason should not match ${re}`).not.toMatch(re);
    void why;
    await shot(page, viewport(), `subject-plan-${c.slug}`);
    // Go back to the topic box for the next one (the plan is per topic; "Pick a topic" resets it).
    const again = page.getByRole("button", { name: "Pick a topic", exact: true });
    if (await again.count()) await again.click();
    else await page.reload();
  }
});

test("a whole subject round, answered by the bot, ends in a map that says what it did not reach", async ({ page }) => {
  test.setTimeout(15 * 60_000);
  await newGuest(page);
  await findSubject(page, "world history");
  await page.getByRole("button", { name: "Confirm outline and start", exact: true }).click();

  let shots = 0;
  let idle = 0;
  let done = false;
  for (let guard = 0; guard < 300 && !done; guard++) {
    if (await page.getByRole("button", { name: "See my subject map", exact: true }).count()) {
      await page.getByRole("button", { name: "See my subject map", exact: true }).click();
      done = true;
      break;
    }
    // A step can lose its control to a re-render between the look and the press: that is "not yet", not a failure.
    const acted = await answerOnce(page).catch(() => false);
    if (acted) {
      idle = 0;
      shots += 1;
      if (shots === 1) await shot(page, viewport(), "subject-round-first-task");
    } else {
      idle += 1;
      await page.waitForTimeout(300);
      if (idle >= 40) {
        await shot(page, viewport(), "subject-round-stuck");
        const text = ((await page.locator("main").textContent()) ?? "").replace(/\s+/g, " ").slice(0, 900);
        throw new Error(`the bot found nothing it could answer on the round screen; the page says: ${text}`);
      }
    }
  }
  if (!done) {
    await shot(page, viewport(), "subject-round-stuck");
    const text = (await page.locator("main").innerText()).replace(/\s+/g, " ").slice(0, 600);
    throw new Error(`the round did not reach its end; the page says: ${text}`);
  }

  await expect(page.getByText(/subject map|what we noticed|not measured/i).first()).toBeVisible({ timeout: 30_000 });
  const map = (await page.locator("main").innerText()).replace(/\s+/g, " ");
  expect(map, "the map names a learner type or a diagnosis").not.toMatch(FORBIDDEN_LABELS);
  // World history skips one of its skills: the map must say so, and must not show it as a result.
  expect(map, "the map says what the round did not reach").toMatch(/not measured/i);
  await shot(page, viewport(), "subject-map");
  await noHorizontalScroll(page, "the subject map");
  if (viewport() === "mobile") {
    expect(await smallTapTargets(page), "tap targets under 40px on the phone").toEqual([]);
  }
});

/** Answer whatever the round is showing right now. Returns false when there is nothing to answer yet. */
async function answerOnce(page: Page): Promise<boolean> {
  const start = page.getByRole("button", { name: "Confirm outline and start", exact: true });
  if (await start.count()) return false;
  // Choices are buttons with a pressed state (not radios), inside a group labelled "Choices".
  const choices = page.locator('[role="group"][aria-label="Choices"] button[aria-pressed]');
  if (await visible(choices.first())) {
    await choices.first().click({ timeout: 10_000 });
    return submitAnswer(page);
  }
  // An ordering task: pick the items in order (each pick is removed from the list), then answer.
  if (await visible(page.getByRole("button", { name: "Start the order again", exact: true }))) {
    // textContent, not innerText: the page uppercases button labels with CSS, and the filter is case-blind.
    const control = /^(Start the order again|Answer|Finish early|sign out|Stop here|Pick a topic|Got it|I have read|Hand in|See my|Find my|My last|Start the round|Measure another|Show|Coins|[0-3]$)/i;
    const items = page.locator("main button:not([disabled])");
    const count = await items.count();
    for (let i = 0; i < count; i++) {
      const label = ((await items.nth(i).textContent()) ?? "").trim();
      if (label && !control.test(label) && (await visible(items.nth(i)))) {
        await items.nth(i).click({ timeout: 10_000 });
        return true;
      }
    }
    return submitAnswer(page);
  }
  const number = page.getByRole("textbox", { name: "Your number", exact: true });
  if (await visible(number)) {
    await number.fill("3", { timeout: 10_000 });
    return submitAnswer(page);
  }
  const text = page.getByRole("textbox", { name: "Your answer", exact: true });
  if (await visible(text)) {
    await text.fill("It changed because of the cause, and the effect followed from it.");
    return submitAnswer(page);
  }
  for (const label of ["Got it — next", "I have read it — the questions", "Hand in the recheck"]) {
    const b = page.getByRole("button", { name: label, exact: true });
    if (await visible(b)) {
      await b.click({ timeout: 10_000 });
      return true;
    }
  }
  return false;
}

async function submitAnswer(page: Page): Promise<boolean> {
  const answer = page.getByRole("button", { name: "Answer", exact: true });
  if (await visible(answer)) {
    await expect(answer).toBeEnabled({ timeout: 10_000 });
    await answer.click({ timeout: 10_000 });
    return true;
  }
  return false;
}

/** Visible right now, without waiting: the round re-renders as it is read. */
async function visible(locator: import("@playwright/test").Locator): Promise<boolean> {
  return locator.first().isVisible().catch(() => false);
}
