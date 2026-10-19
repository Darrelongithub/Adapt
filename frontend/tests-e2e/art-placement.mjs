import { chromium } from 'playwright';

const base = process.env.ADAPT_BASE_URL || 'http://127.0.0.1:3000';
const routes = ['/', '/consent', '/signin', '/baseline', '/learn', '/journey', '/lesson', '/review', '/progress', '/settings', '/proposals', '/evidence', '/voice', '/onboarding', '/diagnose', '/first-lesson'];
// Mirrors ART_MAP in src/components/shell/artMap.ts: one header illustration per page.
const expected = new Map([
  ['/', ['study']], ['/baseline', ['brain']], ['/settings', ['gears']], ['/learn', ['classroom']],
  ['/journey', ['stepping-stones']], ['/proposals', ['balance-scale']], ['/review', ['road-ahead']],
  ['/progress', ['knowledge-tree']], ['/lesson', ['desk-path']], ['/voice', ['open-book']],
]);
const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
let failed = false;
for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  const page = await browser.newPage({ viewport });
  await page.addInitScript(() => sessionStorage.setItem('adapt:seen-opening', '1'));
  await page.goto(`${base}/`, { waitUntil: 'domcontentloaded' });
  const consent = page.locator('input[type="checkbox"]');
  if (await consent.count()) {
    await consent.first().check().catch(() => consent.first().click());
    const agree = page.getByRole('button', { name: /Agree and continue/i });
    if (await agree.count()) await agree.click();
    await page.waitForTimeout(250);
    const guest = page.getByRole('button', { name: /Continue as guest/i });
    if (await guest.count()) await guest.click();
    await page.waitForTimeout(500);
  }
  for (const route of routes) {
    await page.goto(`${base}${route}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(350);
    const arts = await page.locator('[data-art]').evaluateAll(nodes => [...new Set(nodes.map(n => n.getAttribute('data-art')))]);
    const wanted = expected.get(route) ?? [];
    // Every signed-in page owns exactly one header illustration. Pages with no entry (consent,
    // sign-in, onboarding, ...) show none except the access-screen flower.
    const observed = arts.filter(a => a !== 'flower');
    if (observed.length !== wanted.length || wanted.some(a => !observed.includes(a))) {
      console.error(`${route} at ${viewport.width}: expected ${wanted.join(', ') || 'no art'}, saw ${observed.join(', ') || 'none'}`); failed = true;
    }
    if (route !== '/' && arts.includes('flower')) { console.error(`${route}: unexpected flower`); failed = true; }
    for (const art of await page.locator('[data-art]').all()) {
      const box = await art.boundingBox();
      // Header art must be fully visible without scrolling. (The bottom scene is `data-scene`, not `data-art`.)
      if (!box || box.width < 1 || box.height < 1 || box.x < 0 || box.y < 0 || box.x + box.width > viewport.width || box.y + box.height > viewport.height) {
        console.error(`${route}: art outside first viewport`, box); failed = true;
      }
    }
    const text = await page.locator('body').innerText();
    if (/[a-z]+_[a-z]+|\b\w+\.\w+\b/i.test(text)) {
      console.error(`${route}: possible internal identifier visible`); failed = true;
    }
  }
  await page.close();
}
await browser.close();
if (failed) process.exit(1);
console.log('Art placement and visible-identifier checks passed');
