/**
 * Round 6 · Track G — screenshot every page, desktop and mobile, into docs/shots_r6/.
 *
 * One guest session, the 15 routes of the crawl, at 1440x900 and 390x844. Viewport
 * shots (not full page): the hero-fold claim is about what the first screen shows.
 * Also writes contact-sheet.html + contact-sheet.png so one picture can be judged.
 *
 *   cd frontend && PLAYWRIGHT_CHROMIUM_EXECUTABLE=… LD_LIBRARY_PATH=… \
 *     node tests-e2e/round6-shots.mjs
 */
import { chromium } from "playwright-core";
import { mkdirSync, writeFileSync } from "node:fs";

const BASE = process.env.BASE ?? "http://localhost:3000";
const OUT = new URL("../../docs/shots_r6/", import.meta.url).pathname;
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?? "/opt/pw-browsers/chromium";

const ROUTES = [
  ["home", "/"], ["learn", "/learn"], ["review", "/review"], ["progress", "/progress"],
  ["settings", "/settings"], ["lesson", "/lesson"], ["journey", "/journey"], ["proposals", "/proposals"],
  ["contrast", "/contrast"], ["baseline", "/baseline"], ["evidence", "/evidence"], ["voice", "/voice"],
  ["onboarding", "/onboarding"], ["diagnose", "/diagnose"], ["first-lesson", "/first-lesson"],
];
const SIZES = [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]];

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  executablePath: EXECUTABLE,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const context = await browser.newContext();
await context.addInitScript(() => sessionStorage.setItem("adapt:seen-opening", "1"));
const page = await context.newPage();

// One guest session for every page — the same learner state throughout.
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
const consent = page.locator('input[type="checkbox"]');
if (await consent.count()) {
  await consent.first().click();
  await page.getByRole("button", { name: /Agree and continue/i }).click();
  await page.waitForTimeout(300);
  await page.getByRole("button", { name: /Continue as guest/i }).click();
  await page.waitForTimeout(1200);
}

const files = [];
for (const [size, viewport] of SIZES) {
  await page.setViewportSize(viewport);
  for (const [name, route] of ROUTES) {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" }).catch(() => {});
    await page.waitForTimeout(900); // let the opening transition settle
    const file = `${name}-${size}.png`;
    await page.screenshot({ path: `${OUT}${file}` });
    files.push([name, size, file]);
    console.log("shot:", file);
  }
}

// Contact sheet: one grid picture of everything, thumbnails only.
const rows = ROUTES.map(([name]) => {
  const cells = SIZES.map(([size]) => {
    const ratio = size === "desktop" ? "1.6" : "0.46";
    return `<figure style="flex:${ratio}"><img src="${name}-${size}.png" alt=""><figcaption>${name} · ${size}</figcaption></figure>`;
  }).join("");
  return `<div class="row">${cells}</div>`;
}).join("");
writeFileSync(`${OUT}contact-sheet.html`, `<!doctype html><meta charset="utf-8"><style>
body{margin:24px;background:#f4eee0;font:14px/1.4 Georgia,serif;color:#123a8f}
h1{font-size:22px}
.row{display:flex;align-items:flex-start;gap:16px;margin:0 0 16px}
figure{margin:0}
img{width:100%;border:1px solid #e2d9c6;border-radius:10px;display:block}
figcaption{margin-top:4px;font-size:12px;color:#66739a}
</style><h1>Adapt · Round 6 · every page · 1440×900 and 390×844</h1>${rows}`);

await page.setViewportSize({ width: 1440, height: 900 });
await page.goto(`file://${OUT}contact-sheet.html`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.screenshot({ path: `${OUT}contact-sheet.png`, fullPage: true });
console.log("shot: contact-sheet.png");

await browser.close();
console.log(`round6-shots: ${files.length} page shots + contact sheet in docs/shots_r6/`);
