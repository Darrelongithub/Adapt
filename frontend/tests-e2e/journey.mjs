/** Current journey shell: real server state, desktop/mobile layout and reload resume. */
import { existsSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const CHROMIUM = process.env.ADAPT_CHROMIUM ?? "/opt/pw-browsers/chromium";
const failures = [];
let checks = 0;

function ok(label, condition, detail = "") {
  checks += 1;
  console.log(`${condition ? "  ok  " : "FAIL  "}${label}${detail ? ` — ${detail}` : ""}`);
  if (!condition) failures.push(label);
}

async function guestContext(browser, viewport) {
  const context = await browser.newContext({ viewport });
  const response = await context.request.post(`${BASE}/api/v1/students`, { data: { as_guest: true, consent_analytics: false } });
  if (!response.ok()) throw new Error(`could not create guest: ${response.status()} ${await response.text()}`);
  const raw = response.headers()["set-cookie"] ?? "";
  const cookies = [...raw.matchAll(/(adapt_[a-z_]+)=([^;]+)/g)].map(([, name, value]) => ({ name, value, url: BASE }));
  if (!cookies.length) throw new Error("guest creation returned no session cookie");
  await context.addCookies(cookies);
  return context;
}

const state = async (context) => {
  const response = await context.request.get(`${BASE}/api/v1/journey/state`);
  if (!response.ok()) throw new Error(`journey state: ${response.status()}`);
  return response.json();
};

async function walk(browser, { label, viewport, mobile }) {
  console.log(`\n${label}`);
  const context = await guestContext(browser, viewport);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(String(error)));
  await page.goto(`${BASE}/journey`, { waitUntil: "networkidle" });
  const card = page.locator("[data-testid='journey-card']");
  await card.waitFor({ timeout: 20_000 });
  const initial = await state(context);
  ok(`${label}: initial server stage is preferences`, initial.stage === "preferences", initial.stage);
  ok(`${label}: visible card agrees with the server`, await card.getAttribute("data-stage") === initial.stage);
  ok(`${label}: current three-step overview is visible`, await page.locator("[aria-label='Path overview'] > *").count() === 3);
  const overflow = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
  ok(`${label}: no horizontal overflow`, overflow.scroll <= overflow.inner + 1, `${overflow.scroll}/${overflow.inner}`);

  if (mobile) {
    const toggle = page.locator("button[aria-controls='adapt-sidebar']");
    ok("mobile: drawer control exists", await toggle.count() === 1);
    await toggle.click();
    const current = page.locator("aside.sidebar.open a[href='/journey'][aria-current='page']");
    await current.waitFor({ timeout: 5000 });
    ok("mobile: drawer marks Your path current", await current.count() === 1);
    await page.keyboard.press("Escape");
    await page.locator("aside.sidebar:not(.open)").waitFor({ timeout: 5000 });
    ok("mobile: Escape closes the drawer", await toggle.getAttribute("aria-expanded") === "false");
  } else {
    const current = page.locator("aside.sidebar a[href='/journey'][aria-current='page']");
    ok("desktop: sidebar marks Your path current", await current.count() === 1);
  }

  await page.getByRole("button", { name: "Choose a subject", exact: true }).click();
  const subjectInput = page.locator("input[aria-label='What do you want to learn?']");
  await subjectInput.waitFor();
  ok(`${label}: subject choice opens the real subject form`, await subjectInput.isVisible());
  ok(`${label}: four authored suggestions are offered`, await page.locator("[aria-label='Suggestions'] button").count() === 4);

  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Skip this step", exact: true }).click();
  await page.locator("[data-testid='journey-card'][data-stage='games']").waitFor({ timeout: 15_000 });
  const games = await state(context);
  ok(`${label}: preferences skip is stored`, games.stages.find(row => row.id === "preferences")?.status === "skipped");
  ok(`${label}: preferences skip advances to games`, games.stage === "games", games.stage);

  await page.getByRole("button", { name: "Skip this step", exact: true }).click();
  await subjectInput.waitFor({ timeout: 15_000 });
  const subject = await state(context);
  ok(`${label}: games skip is stored`, subject.stages.find(row => row.id === "games")?.status === "skipped");
  ok(`${label}: games skip advances to subject`, subject.stage === "subject", subject.stage);
  ok(`${label}: skipped stages are not measured`, ["preferences", "games"].every(id => subject.not_measured.includes(id)));

  await page.reload({ waitUntil: "networkidle" });
  await subjectInput.waitFor({ timeout: 15_000 });
  const resumed = await state(context);
  ok(`${label}: reload resumes the stored subject stage`, resumed.stage === "subject" && await subjectInput.isVisible());
  ok(`${label}: no uncaught browser error`, errors.length === 0, errors.join(" | "));
  await context.close();
}

const browser = await chromium.launch({ executablePath: existsSync(CHROMIUM) ? CHROMIUM : undefined, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
try {
  await walk(browser, { label: "desktop", viewport: { width: 1280, height: 900 }, mobile: false });
  await walk(browser, { label: "mobile", viewport: { width: 390, height: 844 }, mobile: true });
} finally {
  await browser.close();
}
console.log(`\n${checks} checks: ${checks - failures.length} passed, ${failures.length} failed`);
process.exitCode = failures.length ? 1 : 0;
