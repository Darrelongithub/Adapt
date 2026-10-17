/**
 * Round 6 · acceptance 7 — the topo pattern is visibly present in the gutters
 * of ≥4 pages. "Visible" is measured, not eyeballed: each gutter strip is
 * screenshotted and its luminance spread computed; a flat beige strip would
 * score ~0, the 0.12-opacity topo lines score several grey levels.
 *
 *   cd frontend && PLAYWRIGHT_CHROMIUM_EXECUTABLE=… LD_LIBRARY_PATH=… \
 *     node tests-e2e/round6-topo.mjs
 */
import { chromium } from "playwright-core";
import { inflateSync } from "node:zlib";
import assert from "node:assert/strict";

const BASE = process.env.BASE ?? "http://localhost:3000";
const EXECUTABLE = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?? "/opt/pw-browsers/chromium";
const PAGES = ["/", "/journey", "/settings", "/progress", "/learn"];

/** Decode an 8-bit non-interlaced PNG to grey-level samples (chromium's output). */
function greyLevels(png) {
  assert.equal(png.readUInt32BE(0), 0x89504e47, "not a png");
  let pos = 8, width = 0, height = 0, bitDepth = 0, colorType = 0;
  const idat = [];
  while (pos < png.length) {
    const len = png.readUInt32BE(pos);
    const type = png.toString("ascii", pos + 4, pos + 8);
    const data = png.subarray(pos + 8, pos + 8 + len);
    if (type === "IHDR") {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      bitDepth = data[8]; colorType = data[9];
    } else if (type === "IDAT") idat.push(data);
    pos += 12 + len;
    if (type === "IEND") break;
  }
  assert.equal(bitDepth, 8, "expected 8-bit png");
  const raw = inflateSync(Buffer.concat(idat));
  const bpp = colorType === 6 ? 4 : 3; // RGBA or RGB
  const stride = width * bpp;
  const out = [];
  let prev = Buffer.alloc(stride);
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (stride + 1)];
    const row = Buffer.from(raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1)));
    for (let x = 0; x < stride; x += 1) {
      const a = x >= bpp ? row[x - bpp] : 0, b = prev[x], c = x >= bpp ? prev[x - bpp] : 0;
      if (filter === 1) row[x] = (row[x] + a) & 255;
      else if (filter === 2) row[x] = (row[x] + b) & 255;
      else if (filter === 3) row[x] = (row[x] + ((a + b) >> 1)) & 255;
      else if (filter === 4) {
        const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        row[x] = (row[x] + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c)) & 255;
      }
    }
    for (let x = 0; x < width; x += 1) {
      const i = x * bpp;
      out.push(0.299 * row[i] + 0.587 * row[i + 1] + 0.114 * row[i + 2]);
    }
    prev = row;
  }
  return out;
}

function spread(samples) {
  const mean = samples.reduce((s, v) => s + v, 0) / samples.length;
  const varr = samples.reduce((s, v) => s + (v - mean) ** 2, 0) / samples.length;
  return Math.sqrt(varr);
}

const browser = await chromium.launch({
  executablePath: EXECUTABLE,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.addInitScript(() => sessionStorage.setItem("adapt:seen-opening", "1"));
const page = await context.newPage();
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
const consent = page.locator('input[type="checkbox"]');
if (await consent.count()) {
  await consent.first().click();
  await page.getByRole("button", { name: /Agree and continue/i }).click();
  await page.waitForTimeout(300);
  await page.getByRole("button", { name: /Continue as guest/i }).click();
  await page.waitForTimeout(1200);
}

const scored = [];
for (const route of PAGES) {
  await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(700);
  for (const side of ["left", "right"]) {
    const zone = page.locator(`.pattern-zone[data-pattern="${side}"]`);
    if (!(await zone.count())) continue;
    const box = await zone.boundingBox();
    if (!box || box.width < 8 || box.height < 40) continue;
    const png = await zone.screenshot({ type: "png" });
    const sigma = spread(greyLevels(png));
    scored.push({ route, side, sigma });
    console.log(`  ${route} gutter-${side}: luminance σ = ${sigma.toFixed(2)}`);
  }
}

await browser.close();
const pages = new Set(scored.filter((s) => s.sigma > 1.2).map((s) => s.route));
console.log(`round6-topo: ${scored.length} gutter strips sampled; pattern visible on ${pages.size} pages.`);
assert.ok(pages.size >= 4, `topo pattern visible on only ${pages.size} pages: ${[...pages].join(", ")}`);
console.log("round6-topo: PASS — pattern visible in ≥4 pages' gutters.");
