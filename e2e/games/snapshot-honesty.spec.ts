/**
 * "Not measured" is a state, never a zero, and a stopped sitting says what it did not reach.
 * Desktop only: these check wording and structure, which do not change with the viewport.
 */
import { expect, test } from "@playwright/test";
import { newGuest, press, screen, shot } from "./lib/driver";
import { playBaseline } from "./lib/script";
import { checkSnapshotHonesty } from "./lib/checks";

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "wording and structure: checked once, on the desktop");
});

test("stopping straight away records every game as not measured, and no number", async ({ page }) => {
  await newGuest(page);
  await playBaseline(page, { mode: "mouse", viewport: "desktop", stopAt: "questions" });
  await press(page, page.getByRole("button", { name: "Stop here", exact: true }), "mouse");
  await expect(page.locator(".baseline-snapshot")).toBeVisible();
  const rows = page.locator("ul.baseline-factors > li");
  for (let i = 0; i < (await rows.count()); i++) {
    const text = (await rows.nth(i).innerText()).replace(/\s+/g, " ");
    expect(text).toMatch(/not measured/i); // the page uppercases the tag, so the match ignores case
    expect(text).not.toMatch(/n=\d/);
    // Not a zero: no percentage, no band starting at zero, no "0" as the value of the factor.
    expect(text).not.toMatch(/\b0%|\b0\s*[–-]\s*\d|\bscore\b/);
  }
  await expect(page.getByText(/Recorded as not measured, not as zero/i)).toBeVisible();
  await checkSnapshotHonesty(page);
  await shot(page, "desktop", "snapshot-stopped-at-start");
});

test("stopping part-way keeps what was measured, with its n, and names what was not reached", async ({ page }) => {
  await newGuest(page);
  const ledger = await playBaseline(page, { mode: "mouse", viewport: "desktop", stopAt: "machine" });
  expect(await screen(page)).toBe("machine");
  await press(page, page.getByRole("button", { name: "Stop here", exact: true }), "mouse");
  await expect(page.locator(".baseline-snapshot")).toBeVisible();
  const text = (await page.locator(".baseline-snapshot").innerText()).replace(/\s+/g, " ");
  expect(text).toMatch(/steps that did not happen/i);
  expect(text).toMatch(/locked box/i); // learner-facing name of the step that was not reached
  expect(text).not.toMatch(/locked_box/); // internal stage keys are not learner-facing copy
  expect(text).toMatch(/not measured/i); // an unreached step is not a zero measurement
  expect(text).toMatch(/n=\d+/); // the games that were played report their n
  expect(ledger.cued.length).toBeGreaterThan(0);
  await shot(page, "desktop", "snapshot-stopped-part-way");
});
