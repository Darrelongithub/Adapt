/**
 * The same baseline, every action taken from the keyboard: Tab, Shift+Tab, arrows inside the roving
 * groups, Enter and Space, and typing. No click is made. Every trial must be sent exactly once (a
 * key that both fired a handler and a native click would send it twice).
 */
import { expect, test } from "@playwright/test";
import { newGuest, shot } from "./lib/driver";
import { playBaseline, answerNextDayCheck } from "./lib/script";
import { checkSnapshotHonesty, checkSnapshotNumbers, checkVerdicts } from "./lib/checks";

test("the whole baseline with the keyboard alone, each trial sent once", async ({ page }) => {
  test.setTimeout(20 * 60_000);
  await newGuest(page);
  const ledger = await playBaseline(page, { mode: "keyboard", viewport: "keyboard" });
  await expect(page.locator(".baseline-snapshot")).toBeVisible();
  await answerNextDayCheck(page, ledger, "keyboard", "keyboard");
  await shot(page, "keyboard", "snapshot-after-next-day");
  checkVerdicts(ledger);
  await checkSnapshotNumbers(page, ledger);
  await checkSnapshotHonesty(page);
});
