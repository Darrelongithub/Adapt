/**
 * Every baseline game, played through the page with the mouse, on the desktop and the phone viewport.
 * The gate is: this passes headless, on both viewports, twice in a row.
 */
import { expect, test } from "@playwright/test";
import { newGuest, noHorizontalScroll, shot, smallTapTargets } from "./lib/driver";
import { playBaseline, answerNextDayCheck } from "./lib/script";
import { checkSnapshotHonesty, checkSnapshotNumbers, checkVerdicts } from "./lib/checks";

test("the whole baseline: every game, the next-day check, and what the server recorded", async ({ page }, testInfo) => {
  test.setTimeout(20 * 60_000);
  const viewport = testInfo.project.name;
  await newGuest(page);

  const ledger = await playBaseline(page, { mode: "mouse", viewport });
  await expect(page.locator(".baseline-snapshot")).toBeVisible();
  await checkSnapshotHonesty(page);
  await shot(page, viewport, "snapshot-what-we-noticed");
  await noHorizontalScroll(page, "snapshot");

  await answerNextDayCheck(page, ledger, "mouse", viewport);
  await shot(page, viewport, "snapshot-after-next-day");

  // Every trial the page sent is in the server's record with the verdict the learner should get.
  checkVerdicts(ledger);
  // The numbers the snapshot reports are the ones these answers imply.
  await checkSnapshotNumbers(page, ledger);
  await checkSnapshotHonesty(page);

  if (viewport === "mobile") {
    expect(await smallTapTargets(page), "tap targets under 40px on the phone").toEqual([]);
  }
});
