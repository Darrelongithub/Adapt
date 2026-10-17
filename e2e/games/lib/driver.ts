/**
 * The baseline, driven through the page the way a learner would drive it.
 *
 * One game script, two ways of pressing things: `mouse` clicks and types; `keyboard` moves focus
 * with Tab / Shift+Tab (and arrows inside the roving groups) and presses Enter, Space or types. The
 * keyboard mode never calls `click()`, so a control that cannot be reached without a mouse fails the
 * run instead of passing quietly.
 *
 * The driver knows what the learner can see, not the answer key. Where the learner can know an
 * answer (the study screen shows each code word's place) the driver answers from that, so the
 * server's record can be checked against an answer computed from the page. Where it cannot (a quick
 * tap, a trap) it takes the first option and checks only what the page sent.
 */
import { expect, type Locator, type Page, type Response } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";

export type Mode = "mouse" | "keyboard";

/** Where screenshots go: docs/screens/F/<viewport>/ at the repository root. */
export const SCREEN_ROOT = path.resolve(__dirname, "..", "..", "..", "docs", "screens", "F");

const cleared = new Set<string>();

/**
 * The folder for one viewport's screenshots. The first use in a run empties it, so a file in the
 * folder is always from the run that is being reviewed, never from an earlier one.
 */
export function shotDir(viewport: string): string {
  const dir = path.join(SCREEN_ROOT, viewport);
  fs.mkdirSync(dir, { recursive: true });
  if (!cleared.has(viewport)) {
    cleared.add(viewport);
    for (const f of fs.readdirSync(dir)) if (f.endsWith(".png")) fs.unlinkSync(path.join(dir, f));
  }
  return dir;
}

/** A guest learner. The browser context keeps the session cookie; the CSRF token is returned for API calls. */
export async function newGuest(page: Page, name = "Round Two Tester"): Promise<{ csrf: string; student_id: string }> {
  const res = await page.request.post("/api/v1/students", {
    data: { display_name: name, age_band: "16-18", as_guest: true },
  });
  expect(res.status(), await res.text()).toBe(200);
  const body = (await res.json()) as { csrf_token: string; student_id: string };
  return { csrf: body.csrf_token, student_id: body.student_id };
}

/** Answers from the study screen: code word -> place. Filled in once the study screen is seen. */
export type Pairs = Record<string, string>;

export type Recorded = {
  game: string;
  correct: boolean | null;
  expected?: boolean;
  given?: unknown;
};

/** What the learner did with one trial: the game, the answer this suite knows to be right (if any), what it sent. */
export type Expectation = { game: string; correct?: boolean | null; given?: unknown };

/** Everything the driver learned and sent; the spec compares these with the server afterwards. */
export type Ledger = {
  /** One entry per trial POST, in the order sent. `responses` lines up with it. */
  expect: Expectation[];
  pairs: Pairs;
  cued: { code: string; answer: string; correct: boolean }[];
  recognition: { code: string; answer: string; correct: boolean }[];
  freeCodes: string[];
  probes: number[];
  falsifyingExpected: number; // probes that the boundary rule should count as falsifying
  lockedHints: string[];
  lockedAttempts: number;
  predicted: number | null;
  nextDay: { code: string; answer: string; correct: boolean }[];
  quick: { value: string; screen: string }[];
  traps: { value: string }[];
  shopkeeper: { typed: string[] };
  responses: { status: number; url: string; body: unknown }[];
};

export function newLedger(): Ledger {
  return {
    expect: [],
    pairs: {},
    cued: [],
    recognition: [],
    freeCodes: [],
    probes: [],
    falsifyingExpected: 0,
    lockedHints: [],
    lockedAttempts: 0,
    predicted: null,
    nextDay: [],
    quick: [],
    traps: [],
    shopkeeper: { typed: [] },
    responses: [],
  };
}

/**
 * Records the order in which the vault's tiles light up. Installed before the page loads, so the
 * whole flash of a task is in the log; the vault reads the last N entries after the flash ends.
 */
export const installFlashLog = (): void => {
  const w = window as unknown as { __flash: number[] };
  w.__flash = [];
  const log = new MutationObserver((mutations) => {
    for (const m of mutations) {
      const el = m.target as HTMLElement;
      if (!(el instanceof HTMLElement) || !el.classList.contains("vault-tile")) continue;
      const was = (m.oldValue ?? "").split(/\s+/).includes("flash");
      if (el.classList.contains("flash") && !was) w.__flash.push(Number(el.textContent) - 1);
    }
  });
  log.observe(document, { subtree: true, attributes: true, attributeFilter: ["class"], attributeOldValue: true });
};

/** The boundary rule the server uses for "falsifying" (games.is_falsifying, before any probe count). */
export function isFalsifying(x: number): boolean {
  return x === 0 || x < 0 || Math.abs(x) > 100 || x !== Math.trunc(x);
}

/* ------------------------------------------------------------------ input ---- */

/** Tab (or Shift+Tab, if the target is earlier in the page) until `target` has focus. */
export async function focusByTab(page: Page, target: Locator, maxPresses = 120): Promise<void> {
  const handle = await target.elementHandle();
  if (!handle) throw new Error("focusByTab: the target is not in the DOM");
  for (let i = 0; i < maxPresses; i++) {
    const done = await page.evaluate((el) => document.activeElement === el, handle);
    if (done) return;
    const forward = await page.evaluate((el) => {
      const active = document.activeElement;
      if (!active || active === document.body) return true;
      return (active.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    }, handle);
    await page.keyboard.press(forward ? "Tab" : "Shift+Tab");
  }
  const label = await target.evaluate((el) => el.textContent?.trim() ?? el.getAttribute("aria-label") ?? el.tagName);
  throw new Error(`keyboard could not reach: ${label}`);
}

/** Press a button: click (mouse) or focus by Tab and press Enter (keyboard). */
export async function press(page: Page, target: Locator, mode: Mode): Promise<void> {
  if (mode === "mouse") {
    await target.click();
    return;
  }
  await focusByTab(page, target);
  await page.keyboard.press("Enter");
}

/** Toggle a pressed-state button the same way. */
export const pressToggle = press;

/** Type into a text field: fill (mouse) or focus by Tab then type (keyboard). */
export async function typeIn(page: Page, target: Locator, text: string, mode: Mode): Promise<void> {
  if (mode === "mouse") {
    await target.fill(text);
    return;
  }
  await focusByTab(page, target);
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");
  await page.keyboard.type(text, { delay: 5 });
}

/**
 * Pick the n-th option of a roving group (choices, the predict scale, the vault grid). The keyboard
 * path reaches the group with Tab, lands on its one tabbable item, then moves with the arrow keys.
 */
export async function pickInGroup(
  page: Page,
  group: Locator,
  index: number,
  mode: Mode,
  arrow: "ArrowRight" | "ArrowDown" = "ArrowRight",
): Promise<void> {
  if (mode === "mouse") {
    await group.locator("button").nth(index).click();
    return;
  }
  // A roving group has exactly one tabbable item (tabindex 0); a plain group has none, so the first button.
  // A roving group has exactly one tabbable item (tabindex 0): reach it, then arrow to the target.
  // A plain group (the predict scale, the format picks) has no arrow handling, so Tab to the target.
  const roving = group.locator('button[tabindex="0"]');
  if ((await roving.count()) > 0) {
    await focusByTab(page, roving.first());
    for (let i = 0; i < index; i++) await page.keyboard.press(arrow);
  } else {
    await focusByTab(page, group.locator("button").nth(index));
  }
  await page.keyboard.press("Enter");
}

/* ----------------------------------------------------------------- screens ---- */

export type Screen =
  | "format"
  | "questions"
  | "study"
  | "free"
  | "choice"
  | "vault"
  | "shopkeeper"
  | "predict"
  | "machine"
  | "locked_box"
  | "snapshot"
  | "unknown";

/** Which screen the page is showing now. Waits for one to appear. */
export async function screen(page: Page): Promise<Screen> {
  await page.waitForFunction(
    () =>
      document.querySelector(
        '.baseline-snapshot, [aria-label="format preference"], .r3-question-stack, .baseline-task',
      ) !== null,
    null,
    { timeout: 30_000 },
  );
  // The screen can change under us while it is being read; read it again until it is recognised.
  let last: Screen = "unknown";
  for (let i = 0; i < 6; i++) {
    last = await page.evaluate((): Screen => {
      if (document.querySelector(".baseline-snapshot")) return "snapshot";
      if (document.querySelector('[aria-label="format preference"]')) return "format";
      // The shopkeeper's quick answers are fieldsets too, so a task screen is checked before the questions.
      const task = document.querySelector(".baseline-task");
      if (!task && document.querySelector(".r3-question-stack")) return "questions";
      if (!task) return "unknown";
      if (document.querySelector(".vault-grid")) return "vault";
      if (document.querySelector("#machine-in")) return "machine";
      if (document.querySelector("#box-answer")) return "locked_box";
      if (document.querySelector("#baseline-items")) return "shopkeeper";
      if (document.querySelector('[aria-label="how many will you remember"]')) return "predict";
      if (document.querySelector("#baseline-free")) return "free";
      if (task.textContent?.includes("I have looked at them")) return "study";
      if (document.querySelector('[aria-label="answers"]')) return "choice";
      return "unknown";
    });
    if (last !== "unknown") break;
    await page.waitForTimeout(150);
  }
  return last;
}

/** The response to the POST the page makes when `action` runs (format, questions, trial, finish). */
export async function afterPost(page: Page, action: () => Promise<void>): Promise<Response> {
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === "POST" && /\/api\/v1\/baseline/.test(r.url()), { timeout: 30_000 }),
    action(),
  ]);
  return res;
}

/** The trial bodies the page sent, and the verdicts it got, for the ledger. */
export function recordResponses(page: Page, ledger: Ledger): void {
  page.on("response", async (res) => {
    if (!/\/api\/v1\/baseline\/.+\/trial/.test(res.url()) || res.request().method() !== "POST") return;
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    ledger.responses.push({ status: res.status(), url: res.url(), body });
  });
}

/** Screenshot one state into docs/screens/F/<viewport>/. */
export async function shot(page: Page, viewport: string, name: string): Promise<void> {
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(shotDir(viewport), `${name}.png`), fullPage: true });
}

/** Fail if the page is wider than the phone or desktop viewport: a horizontal scroll is a layout bug. */
export async function noHorizontalScroll(page: Page, where: string): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, `${where}: the page is wider than the viewport by ${overflow}px`).toBeLessThanOrEqual(1);
}

/** Tap targets on a touch screen: every visible button must be at least 40 css px on its short side. */
export async function smallTapTargets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    document.querySelectorAll<HTMLElement>(".baseline-task button, .baseline-snapshot button, .r3-question-stack button").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      if (Math.min(r.width, r.height) < 40) out.push(`${(el.textContent ?? "").trim().slice(0, 30)} ${Math.round(r.width)}x${Math.round(r.height)}`);
    });
    return out;
  });
}

/** Strings that label a learner rather than describe a measurement. None may appear on the snapshot. */
export const FORBIDDEN_LABELS = /learning style|visual learner|auditory|kinesthetic|dyslex|adhd|\biq\b|gifted|slow learner|low ability|high ability|a \w+ learner/i;
