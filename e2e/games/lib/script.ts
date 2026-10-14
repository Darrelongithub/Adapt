/**
 * The whole baseline, played screen by screen. Every game, every stage, in the order the server
 * serves them. The same script runs with the mouse, on two viewports, and with the keyboard only.
 *
 * Every action that the server records (a trial, a format pick, the questions, a stop) is made
 * through `act`, which waits for the server's reply to that very request. The ledger gets one entry
 * per trial, in the order the trials were sent, so the spec can match each entry with the verdict the
 * server returned for it.
 */
import { expect, type Page, type Response } from "@playwright/test";
import {
  focusByTab,
  installFlashLog,
  isFalsifying,
  newLedger,
  noHorizontalScroll,
  pickInGroup,
  press,
  recordResponses,
  screen,
  shot,
  typeIn,
  type Ledger,
  type Mode,
  type Screen,
} from "./driver";

export type RunOptions = {
  mode: Mode;
  /** The folder the screenshots go in: "desktop", "mobile" or "keyboard". */
  viewport: string;
  ledger?: Ledger;
  /** Stop when this screen is reached (the partial-run test uses this). */
  stopAt?: Screen;
  /** Probes sent in the machine's first round. Boundary inputs (0, negatives, 100+, decimals) count as falsifying. */
  machineProbes?: number[];
};

export const MACHINE_PROBES = [3, 0, 5, -2, 7];

/** Run one user action that makes the page send a baseline POST, and return the server's reply. */
async function act(page: Page, action: () => Promise<void>): Promise<Response> {
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === "POST" && /\/api\/v1\/baseline/.test(r.url()), { timeout: 30_000 }),
    action(),
  ]);
  return res;
}

/**
 * Is this a practice task? The tag says "practice — not scored", and the page shows it in capitals
 * (CSS), so read the source text (textContent is not affected by CSS) and ignore case.
 */
async function isPractice(page: Page): Promise<boolean> {
  const text = (await page.locator(".baseline-task").first().textContent()) ?? "";
  return /practice — not scored/i.test(text);
}

/** Play the baseline from the format pick to the snapshot. Returns the ledger of what was sent. */
export async function playBaseline(page: Page, opts: RunOptions): Promise<Ledger> {
  const ledger = opts.ledger ?? newLedger();
  recordResponses(page, ledger);
  const { mode, viewport } = opts;
  const probes = opts.machineProbes ?? MACHINE_PROBES;
  let shots = 0;
  const snap = async (name: string) => {
    shots += 1;
    await shot(page, viewport, `${String(shots).padStart(2, "0")}-${name}`);
  };
  let machineRound = 0;
  let machineProbe = 0;
  let cuedMeasured = 0;
  let quickMeasured = 0;
  let vaultSeen = 0;
  const submit = (name: string) => act(page, () => press(page, page.getByRole("button", { name }), mode));

  await page.addInitScript(installFlashLog);
  await page.goto("/baseline");
  let seen: Screen = "unknown";
  for (let guard = 0; guard < 400; guard++) {
    seen = await screen(page);
    if (process.env.ADAPT_E2E_TRACE) console.log(`[trace] ${guard} ${seen}`);
    if (seen === "snapshot") break;
    if (opts.stopAt === seen) break;

    if (seen === "format") {
      await snap("format");
      await act(page, () => pickInGroup(page, page.getByRole("group", { name: "format preference" }), 0, mode));
      await noHorizontalScroll(page, "format");
      continue;
    }

    if (seen === "questions") {
      await snap("questions");
      const rows = page.locator("fieldset.baseline-q-row");
      const count = await rows.count();
      for (let i = 0; i < count; i++) {
        const options = rows.nth(i).locator(".baseline-options");
        if ((await options.count()) > 0) await pickInGroup(page, options, 0, mode);
      }
      await act(page, () => press(page, page.getByRole("button", { name: "Continue", exact: true }), mode));
      continue;
    }

    if (seen === "study") {
      await snap("codewords-study");
      const items = page.locator("ul.baseline-pairs li");
      // Auto-waiting: the list is being rendered as the screen is read, so a bare count() can see zero.
      await expect(items, "the study screen shows eight pairs").toHaveCount(8);
      const n = await items.count();
      for (let i = 0; i < n; i++) {
        const text = (await items.nth(i).innerText()).replace(/\s+/g, " ").trim();
        const [code, place] = text.split("→").map((t) => t.trim());
        ledger.pairs[code] = place;
      }
      // The study screen is looking time: a trial is recorded, but nothing is scored.
      await act(page, () => press(page, page.getByRole("button", { name: "I have looked at them", exact: true }), mode));
      ledger.expect.push({ game: "study", correct: null });
      continue;
    }

    if (seen === "free") {
      const practice = await isPractice(page);
      const label = (await page.locator('label[for="baseline-free"]').innerText()).trim();
      if (/code words you remember/i.test(label)) {
        await snap("delayed-free-recall");
        const codes = Object.keys(ledger.pairs);
        expect(codes.length).toBe(8);
        await typeIn(page, page.locator("#baseline-free"), codes.join(", "), mode);
        await submit("Answer");
        ledger.freeCodes = codes;
        ledger.expect.push({ game: "codewords_delayed_free", correct: true });
        continue;
      }
      // The cue is a code word (ask for its place) or a place (ask for its code word): both are on the study screen.
      const cue = (await page.locator("p.baseline-cue").innerText()).trim();
      const back = Object.fromEntries(Object.entries(ledger.pairs).map(([code, where]) => [where, code]));
      const place = ledger.pairs[cue] ?? back[cue];
      expect(place, `the page asked about ${cue}, which the study screen did not show`).toBeTruthy();
      if (/Recognise/i.test(label)) {
        if (!practice) await snap("delayed-recognition");
        await typeIn(page, page.locator("#baseline-free"), place, mode);
        await submit("Answer");
        if (!practice) ledger.recognition.push({ code: cue, answer: place, correct: true });
        ledger.expect.push({ game: "codewords_recognition", correct: practice ? null : true });
      } else {
        if (!practice && cuedMeasured === 0) await snap("cued-recall");
        await typeIn(page, page.locator("#baseline-free"), place, mode);
        await submit("Answer");
        if (!practice) {
          cuedMeasured += 1;
          ledger.cued.push({ code: cue, answer: place, correct: true });
        }
        ledger.expect.push({ game: "codewords_cued", correct: practice ? null : true });
      }
      continue;
    }

    if (seen === "choice") {
      const practice = await isPractice(page);
      const group = page.locator('[aria-label="answers"]');
      const trap = (await page.locator(".baseline-story").count()) > 0;
      const value = (await group.locator("button").nth(0).innerText()).trim();
      if (trap) await snap("trap");
      else if (quickMeasured === 0) await snap("quick-tap");
      if (!trap && !practice) quickMeasured += 1;
      await act(page, () => pickInGroup(page, group, 0, mode));
      if (trap) ledger.traps.push({ value });
      else ledger.quick.push({ value, screen: practice ? "practice" : "measure" });
      // The first option is the learner's pick; whether it was right is not known to this suite.
      ledger.expect.push({ game: trap ? "trap" : "quick", correct: undefined, given: value });
      continue;
    }

    if (seen === "vault") {
      vaultSeen += 1;
      const countdown = page.getByText(/^Tapped 0 of \d+/);
      await countdown.waitFor({ timeout: 60_000 });
      const shownLength = Number((await countdown.innerText()).match(/of (\d+)/)![1]);
      const reverse = (await page.getByText(/Reverse order\./).count()) > 0;
      const flashed: number[] = await page.evaluate(() => (window as unknown as { __flash?: number[] }).__flash ?? []);
      const sequence = flashed.slice(-shownLength);
      expect(sequence.length, "the flash log should hold the whole sequence").toBe(shownLength);
      if (vaultSeen === 1) await snap("vault-watch");
      await expect(page.locator(".vault-grid")).toHaveAttribute("aria-busy", "false", { timeout: 60_000 });
      if (vaultSeen === 1) await snap("vault-your-turn");
      const taps = reverse ? [...sequence].reverse() : sequence;
      await act(page, () => tapVault(page, taps, mode));
      ledger.expect.push({ game: "vault", correct: true, given: taps });
      continue;
    }

    if (seen === "shopkeeper") {
      await snap("shopkeeper-hold");
      await expect(page.locator("ul.baseline-pairs li strong")).toHaveCount(3);
      const items = (await page.locator("ul.baseline-pairs li strong").allInnerTexts()).map((t) => t.trim());
      const rows = page.locator("fieldset.baseline-q-row .row");
      const count = await rows.count();
      for (let i = 0; i < count; i++) await pickInGroup(page, rows.nth(i), 0, mode);
      await snap("shopkeeper-ready");
      await typeIn(page, page.locator("#baseline-items"), items.join(", "), mode);
      ledger.shopkeeper.typed = items;
      await act(page, () => press(page, page.getByRole("button", { name: "Answer", exact: true }), mode));
      ledger.expect.push({ game: "shopkeeper", correct: true, given: items });
      continue;
    }

    if (seen === "predict") {
      await snap("predict");
      await pickInGroup(page, page.locator('[aria-label="how many will you remember"]'), 5, mode);
      ledger.predicted = 5;
      await act(page, () => press(page, page.getByRole("button", { name: "That is my guess", exact: true }), mode));
      ledger.expect.push({ game: "codewords_delayed_predict", correct: null, given: 5 });
      continue;
    }

    if (seen === "machine") {
      if (await isPractice(page)) {
        machineRound += 1;
        if (machineRound === 1) await snap("machine-practice");
        await typeIn(page, page.locator("#machine-in"), "4", mode);
        await act(page, () => press(page, page.getByRole("button", { name: "Send it in", exact: true }), mode));
        ledger.expect.push({ game: "machine-probe-practice", correct: null, given: 4 });
        continue;
      }
      if (machineRound === 1 && machineProbe < probes.length) {
        if (machineProbe === 0) await snap("machine-probing");
        const x = probes[machineProbe];
        machineProbe += 1;
        await typeIn(page, page.locator("#machine-in"), String(x), mode);
        await act(page, () => press(page, page.getByRole("button", { name: "Send it in", exact: true }), mode));
        ledger.probes.push(x);
        if (isFalsifying(x)) ledger.falsifyingExpected += 1;
        ledger.expect.push({ game: "machine-probe", correct: null, given: x });
        continue;
      }
      if (machineRound === 1 && machineProbe === probes.length) await snap("machine-after-probes");
      // Later rounds (and round one, after its probes): two wrong guesses close a round.
      await typeIn(page, page.locator("#machine-guess"), "no idea", mode);
      await act(page, () => press(page, page.getByRole("button", { name: "That is my answer", exact: true }), mode));
      ledger.expect.push({ game: "machine-guess", correct: false, given: "no idea" });
      continue;
    }

    if (seen === "locked_box") {
      await snap("locked-box");
      await openHintShop(page, mode);
      await snap("locked-box-hint-shop");
      await act(page, () => press(page, page.getByRole("button", { name: "buy (1)", exact: true }), mode));
      ledger.lockedHints.push("hint_nudge");
      ledger.expect.push({ game: "locked_box", correct: null, given: "hint_nudge" });
      await snap("locked-box-hint-bought");
      await typeIn(page, page.locator("#box-answer"), "-999", mode);
      await act(page, () => press(page, page.getByRole("button", { name: "Try this answer", exact: true }), mode));
      ledger.lockedAttempts += 1;
      ledger.expect.push({ game: "locked_box", correct: false, given: -999 });
      await snap("locked-box-wrong-attempt");
      await act(page, () => press(page, page.getByRole("button", { name: "Walk away", exact: true }), mode));
      ledger.expect.push({ game: "locked_box", correct: null, given: "walk_away" });
      continue;
    }

    await snap("unrecognised-screen");
    throw new Error(`the page showed a screen the suite does not know (last state: ${seen}); see the last screenshot`);
  }

  if (!opts.stopAt) {
    await expect.poll(() => screen(page), { timeout: 30_000 }).toBe("snapshot");
  }
  return ledger;
}

/** Wait until the codeword check shows a new question (or its end). Returns the question text, or "end". */
async function nextCheckState(page: Page, previous: string): Promise<string> {
  const until = Date.now() + 30_000;
  while (Date.now() < until) {
    if ((await page.getByText(/Recorded \d+ answers?\./).count()) > 0) return "end";
    // allTextContents does not wait for the element: the label is being replaced while we read it.
    const [text] = (await page.locator('label[for="next-day-answer"]').allTextContents()).map((t) => t.trim());
    if (text && text !== previous) return text;
    await page.waitForTimeout(100);
  }
  throw new Error("the codeword check did not move on to its next question");
}

/** Continue the codeword check that the snapshot offers, answering each one from the study pairs. */
export async function answerNextDayCheck(page: Page, ledger: Ledger, mode: Mode, viewport: string): Promise<void> {
  await press(page, page.getByRole("button", { name: "Show the codeword check", exact: true }), mode);
  let state = await nextCheckState(page, "");
  let answered = 0;
  while (state !== "end") {
    const prompt = state;
    const code = prompt.match(/went with (.+?)\?/)?.[1];
    expect(code, `could not read the code word in: ${prompt}`).toBeTruthy();
    const place = ledger.pairs[code!];
    expect(place, `the next-day check asked about ${code}, which the study did not show`).toBeTruthy();
    const field = page.locator("#next-day-answer");
    if (answered === 0) await shot(page, viewport, "next-day-check-first");
    await expect(field, `next-day answer ${answered + 1} (${prompt}): the field should be ready`).toBeEnabled({ timeout: 15_000 });
    await typeIn(page, field, place, mode);
    await act(page, () => press(page, page.getByRole("button", { name: "Answer", exact: true }), mode));
    ledger.nextDay.push({ code: code!, answer: place, correct: true });
    ledger.expect.push({ game: "codewords_next_day", correct: true, given: place });
    answered += 1;
    state = await nextCheckState(page, prompt);
    expect(answered, "the codeword check asked more questions than it should").toBeLessThanOrEqual(3);
  }
  expect(answered, "the codeword check should ask three questions").toBe(3);
  await expect(page.getByText(/Recorded 3 answers\./)).toBeVisible({ timeout: 30_000 });
}

/** Keyboard or mouse, the same thing: open the hint shop. */
async function openHintShop(page: Page, mode: Mode): Promise<void> {
  const summary = page.locator("details.baseline-hints summary");
  if (mode === "mouse") {
    await summary.click();
  } else {
    await focusByTab(page, summary);
    await page.keyboard.press("Enter");
  }
  await expect(page.getByRole("button", { name: "buy (1)", exact: true })).toBeVisible();
}

/** Tap the vault in order. The keyboard moves the roving focus with the arrows and taps with Enter. */
async function tapVault(page: Page, taps: number[], mode: Mode): Promise<void> {
  if (mode === "mouse") {
    for (const tile of taps) await page.getByRole("gridcell", { name: `tile ${tile + 1}` }).click();
    return;
  }
  // The vault component is reused from one task to the next, so the roving focus can be on any tile
  // (where the learner last was). Read the focused tile's index instead of assuming the first.
  const entry = page.locator('.vault-grid button[tabindex="0"]').first();
  await focusByTab(page, entry);
  let at = await page.evaluate(() => Array.from(document.querySelectorAll(".vault-grid button")).indexOf(document.activeElement as HTMLElement));
  expect(at, "the focused vault tile").toBeGreaterThanOrEqual(0);
  for (const tile of taps) {
    while (at < tile) {
      await page.keyboard.press("ArrowRight");
      at += 1;
    }
    while (at > tile) {
      await page.keyboard.press("ArrowLeft");
      at -= 1;
    }
    await page.keyboard.press("Enter");
  }
}
