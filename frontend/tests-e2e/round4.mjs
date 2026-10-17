/** Real production web/API only. Never installs browsers. Optional ADAPT_BEFORE_URL
 * measures the base-commit UI (its broken gate is bypassed only for copy counts). */
import assert from "node:assert/strict";
import { mkdir, readdir, writeFile, readFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { gzipSync } from "node:zlib";
import { chromium } from "playwright";
import { inspectArtZones, screenWords } from "./art-zones.mjs";
const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000",
  BEFORE = process.env.ADAPT_BEFORE_URL;
const OUT = resolve("../docs/round4"),
  SHOTS = resolve("../docs/screens/round4"),
  X = resolve("../docs/screens/x");
const result = {
  auth: [],
  words: [],
  routes: [],
  journeys: [],
  returningJourneys: [],
  errors: [],
  budgets: {},
};
const browser = await chromium.launch({
  executablePath:
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?? "/opt/pw-browsers/chromium",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
Object.assign(result, {
  meta: {
    verifiedAt: new Date().toISOString(),
    chromium: await browser.version(),
    environment: "production build; local unthrottled; local API",
    baseCommit: "7045ede73f85349c55f5a7ef1ab54a5c3ea723ad",
  },
});
await Promise.all([OUT, SHOTS, X].map((d) => mkdir(d, { recursive: true })));
async function request(context, path, data) {
  const csrf = (await context.cookies(BASE)).find(
    (c) => c.name === "adapt_csrf",
  )?.value;
  const headers = csrf ? { "x-csrf-token": csrf } : {};
  const r =
    data === undefined
      ? await context.request.get(BASE + "/api/v1/" + path)
      : await context.request.post(BASE + "/api/v1/" + path, { data, headers });
  assert(r.ok(), `${path} ${r.status()} ${await r.text()}`);
  return r.json();
}
async function settled(p) {
  await p.locator("[data-sheet]").waitFor();
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(200);
}
async function zones(p, label) {
  const o = await p.evaluate(inspectArtZones);
  assert.deepEqual(o.failures, [], `${label}: ${o.failures.join("; ")}`);
  return o;
}
async function words(p, label, before = false) {
  const o = await p.evaluate(screenWords);
  result.words.push({ screen: label, before, ...o });
  if (!before)
    assert(o.bodyWords <= 40, `${label}: ${o.bodyWords} body words (max 40)`);
  return o;
}
async function frontDoor(p, base = BASE) {
  await p.goto(base + "/");
  const skip = p.locator(".opening-skip");
  if (await skip.isVisible()) {
    if (base === BASE) {
      await p.screenshot({
        path: join(SHOTS, `welcome-${p.viewportSize().width}.png`),
      });
      await zones(p, "welcome");
    }
    await skip.click();
  }
  await p.locator(".opening-overlay").waitFor({ state: "detached" });
  await p.locator("[data-account-gate]").waitFor();
  if (base === BASE) await settled(p);
}
async function agree(p) {
  await p.getByRole("checkbox").first().check();
  await p.getByRole("button", { name: "Agree and continue" }).click();
}
async function enter(p, mode, username, password) {
  await frontDoor(p);
  await words(p, `consent-${mode}`);
  await p.screenshot({
    path: join(SHOTS, `consent-${p.viewportSize().width}.png`),
    fullPage: true,
  });
  await zones(p, `consent-${mode}`);
  await agree(p);
  if (mode === "register")
    await p.getByRole("tab", { name: "Create account", exact: true }).click();
  await words(p, `auth-${mode}`);
  await p.screenshot({
    path: join(SHOTS, `auth-${mode}-${p.viewportSize().width}.png`),
    fullPage: true,
  });
  await zones(p, `auth-${mode}`);
  if (mode !== "guest") {
    await p.getByLabel("Username").fill(username);
    await p.locator("input[type=password]").first().fill(password);
  }
  const start = performance.now();
  await p
    .getByRole("button", {
      name:
        mode === "guest"
          ? "Continue as guest"
          : mode === "register"
            ? "Create account"
            : "Sign in",
      exact: true,
    })
    .click();
  await p
    .locator('[data-journey] [data-testid="journey-card"] a')
    .waitFor({ timeout: 3000 });
  assert.equal(new URL(p.url()).pathname, "/journey");
  await p.getByText("see every step", { exact: true }).click();
  assert.notEqual(
    await p.locator("[data-journey] details").first().getAttribute("open"),
    null,
  );
  await p.getByText("see every step", { exact: true }).click();
  const ms = Math.round(performance.now() - start);
  assert(ms <= 3000, `${mode} interactive in ${ms}ms (max 3000)`);
  result.auth.push({ mode, viewport: p.viewportSize(), interactiveMs: ms });
  const c = await words(p, `journey-${mode}`);
  assert(
    c.aboveFoldWords <= 60,
    `journey ${c.aboveFoldWords} above-fold words`,
  );
}
async function questions(p, label, base = BASE, before = false) {
  await p.goto(base + "/baseline");
  const f = p.getByTestId("format-option");
  await f.first().waitFor();
  await words(p, `${label}-format`, before);
  if (!before) {
    await zones(p, label + "-format");
    const cards = await f.evaluateAll((ns) =>
      ns.map((n) => {
        const r = n.getBoundingClientRect();
        return { x: r.x, y: r.y, height: r.height };
      }),
    );
    assert(
      cards.every((c) => c.height >= 48),
      "48px format targets",
    );
    const axis = p.viewportSize().width >= 900 ? "y" : "x";
    assert(
      cards.every((c) => Math.abs(c[axis] - cards[0][axis]) < 1),
      "format layout",
    );
  }
  await f.first().click();
  for (let i = 1; i <= 13; i++) {
    await p.getByTestId("r3-question-prompt").waitFor();
    assert.equal(await p.getByTestId("r3-question-prompt").count(), 1);
    assert(
      (await p.locator("#main-content").innerText()).includes(
        `Question ${i} of 13`,
      ),
    );
    await words(p, `${label}-question-${i}`, before);
    if (!before) await zones(p, `${label}-question-${i}`);
    if (!before && (i === 1 || i === 13))
      await p.screenshot({
        path: join(SHOTS, `${label}-question-${i}.png`),
        fullPage: true,
      });
    const opts = p.getByTestId("r3-question-option");
    if (await opts.count()) await opts.first().click();
    else
      await p
        .locator(".r3-question-stack input")
        .fill("A little practice every day");
    if (i === 2) {
      await p.getByRole("button", { name: "Back", exact: true }).click();
      assert(
        (await p.locator("#main-content").innerText()).includes(
          "Question 1 of 13",
        ),
      );
      assert.equal(
        await p
          .getByTestId("r3-question-option")
          .first()
          .getAttribute("aria-pressed"),
        "true",
        "Back preserves answer",
      );
      assert.equal(
        await p
          .getByRole("progressbar", { name: "questions progress" })
          .getAttribute("aria-valuenow"),
        "8",
      );
      await p.getByRole("button", { name: "Next", exact: true }).click();
    }
    const submitted =
      i === 13
        ? p.waitForRequest(
            (r) =>
              r.method() === "POST" &&
              /baseline\/[^/]+\/questions$/.test(r.url()),
          )
        : null;
    await p
      .getByRole("button", { name: i === 13 ? "Finish" : "Next", exact: true })
      .click();
    if (submitted)
      assert.equal(
        Object.keys((await submitted).postDataJSON().answers).filter(
          (k) => k !== "preferred_format",
        ).length,
        13,
        "all 13 preferences sent (separate format pick is retained)",
      );
  }
  await p.getByTestId("r3-question-prompt").waitFor({ state: "detached" });
  if (!before) {
    const o = await request(p.context(), "me/preferences");
    assert(Object.keys(o.preferences).length > 0);
    assert.equal(
      o.preferences.anything_else,
      "A little practice every day",
      "free-text preference persisted",
    );
  }
}
async function tabTo(p, target) {
  const handle = await target.elementHandle();
  assert(handle, "keyboard target exists");
  for (let i = 0; i < 120; i++) {
    if (await p.evaluate((el) => document.activeElement === el, handle)) return;
    const forward = await p.evaluate(
      (el) =>
        !document.activeElement ||
        Boolean(
          document.activeElement.compareDocumentPosition(el) &
          Node.DOCUMENT_POSITION_FOLLOWING,
        ),
      handle,
    );
    await p.keyboard.press(forward ? "Tab" : "Shift+Tab");
  }
  assert.fail("Keyboard could not reach lesson control");
}
async function lesson(p, label) {
  await p.goto(BASE + "/first-lesson");
  await p.getByTestId("fl-topic").fill("photosynthesis");
  await p.getByTestId("fl-go").click();
  await p
    .getByTestId("fl-concept")
    .filter({ hasText: "makes its own food" })
    .click();
  await p.getByTestId("fl-option").first().waitFor();
  assert.equal(
    await p.locator("[data-item-id]").count(),
    1,
    "unique checked item",
  );
  assert(
    await p
      .getByTestId("fl-option")
      .evaluateAll((ns) =>
        ns.every((n) => n.getBoundingClientRect().height >= 48),
      ),
    "48px lesson targets",
  );
  await zones(p, label + "-lesson");
  await p.evaluate(() => scrollTo(0, 0));
  await p.screenshot({ path: join(X, label + "-lesson.png"), fullPage: true });
  await tabTo(p, p.getByTestId("fl-option").nth(1));
  const response = p.waitForResponse(
    (r) => r.url().endsWith("/lesson/grade") && r.request().method() === "POST",
  );
  await p.keyboard.press("Space");
  assert.equal((await (await response).json()).correct, false);
  await p.getByTestId("fl-moment").nth(1).waitFor();
  assert.equal(await p.locator(".is-incorrect").count(), 1);
  const text = await p.getByTestId("fl-moment").nth(1).innerText();
  assert(text.includes("Oxygen is familiar"), "specific distractor");
  assert(!text.includes("no checked explanation"));
  const ids = await p
    .locator("[data-item-id]")
    .evaluateAll((ns) => ns.map((n) => n.dataset.itemId));
  assert.equal(ids.length, new Set(ids).size, "no repeated check after wrong");
  const button = p.getByTestId("fl-reteach"),
    methods = [];
  await p.route("**/api/v1/lesson/reteach", async (route) => {
    // Delay transport, not the server result, to observe the in-flight guard.
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.continue();
  });
  for (let i = 0; i < 2; i++) {
    await tabTo(p, button);
    const pending = p.waitForRequest(
      (r) => r.url().endsWith("/lesson/reteach") && r.method() === "POST",
    );
    await p.keyboard.press("Enter");
    await pending;
    assert(await button.isDisabled(), "reteach disabled in flight");
    await p
      .getByTestId("fl-moment")
      .nth(i + 2)
      .waitFor();
    methods.push(
      await p
        .getByTestId("fl-moment")
        .nth(i + 2)
        .getAttribute("aria-label"),
    );
  }
  assert.notEqual(methods[0], methods[1]);
  const finalIds = await p
    .locator("[data-item-id]")
    .evaluateAll((ns) => ns.map((n) => n.dataset.itemId));
  assert.equal(
    finalIds.length,
    new Set(finalIds).size,
    "no repeats after reteaches",
  );
  assert.equal(await p.getByTestId("block-why").count(), 1);
  await zones(p, label + "-feedback");
  await p.evaluate(() => scrollTo(0, 0));
  await p.screenshot({
    path: join(X, label + "-feedback.png"),
    fullPage: true,
  });
}
async function journey(p, label) {
  await questions(p, label);
  await p.getByRole("button", { name: "Stop here", exact: true }).click();
  await p.getByRole("link", { name: "Go to your dashboard" }).waitFor();
  await p.goto(BASE + "/journey");
  let state = await request(p.context(), "journey/state");
  for (
    let guard = 0;
    guard < 8 && !["first_lesson", "practice"].includes(state.stage);
    guard++
  ) {
    await p
      .getByRole("button", { name: "Skip this step", exact: true })
      .click();
    await p.waitForTimeout(100);
    const next = await request(p.context(), "journey/state");
    assert.notEqual(next.stage, state.stage);
    state = next;
  }
  await p.locator('[data-journey] [data-testid="journey-card"] a').click();
  await lesson(p, label);
  await p.goto(BASE + "/journey");
  await p.getByText("see every step", { exact: true }).click();
  await p.getByTestId("journey-mark-done").click();
  await p
    .locator('[data-testid="journey-card"][data-stage="practice"]')
    .waitFor();
  assert.equal((await request(p.context(), "journey/state")).stage, "practice");
  await p.locator('[data-testid="journey-card"] a').click();
  await settled(p);
  await zones(p, label + "-practice");
  result.journeys.push({
    label,
    preferences: "13 answered via UI",
    games: "stopped early; unplayed not measured",
    optionalStages: "skipped with server receipts",
    lesson: "wrong choice + two reteaches",
    practice: "mounted",
  });
}
async function routes(dir = "src/app", parts = []) {
  const es = await readdir(dir, { withFileTypes: true }),
    out = es.some((e) => e.name === "page.tsx") ? ["/" + parts.join("/")] : [];
  for (const e of es.filter((e) => e.isDirectory()))
    out.push(...(await routes(join(dir, e.name), [...parts, e.name])));
  return out;
}
try {
  for (const viewport of [
    { width: 1280, height: 800 },
    { width: 390, height: 844 },
  ]) {
    const c = await browser.newContext({ viewport }),
      p = await c.newPage();
    p.setDefaultTimeout(15000);
    p.on("pageerror", (e) => result.errors.push(e.message));
    const label = viewport.width === 390 ? "mobile" : "desktop";
    await enter(p, "guest");
    await journey(p, label + "-guest");
    await c.close();
    const a = await browser.newContext({ viewport }),
      ap = await a.newPage(),
      username = `r4_${viewport.width}_${Date.now()}`,
      password = "Round4-test-password!";
    ap.on("pageerror", (e) => result.errors.push(e.message));
    await enter(ap, "register", username, password);
    await journey(ap, label + "-account");
    await request(a, "logout", {});
    await enter(ap, "login", username, password);
    assert.equal(
      (await request(a, "journey/state")).stage,
      "practice",
      "returning progress persists",
    );
    await ap.locator('[data-testid="journey-card"] a').click();
    await ap.waitForURL("**/learn");
    assert.equal(new URL(ap.url()).pathname, "/learn");
    await settled(ap);
    await zones(ap, label + " returning practice");
    await lesson(ap, label + "-returning");
    result.returningJourneys.push({
      label,
      resumedStage: "practice",
      lesson: "wrong + two reteaches via keyboard",
    });
    await ap.goto(BASE + "/learn");
    await settled(ap);
    await a.close();
  }
  const extra = await browser.newContext({
    viewport: { width: 1024, height: 768 },
  });
  const ep = await extra.newPage();
  await frontDoor(ep);
  await zones(ep, "1024 consent");
  await ep.screenshot({
    path: join(SHOTS, "consent-1024.png"),
    fullPage: true,
  });
  await agree(ep);
  await ep.screenshot({ path: join(SHOTS, "auth-1024.png"), fullPage: true });
  await zones(ep, "1024 auth");
  await extra.close();
  const all = await routes();
  for (const viewport of [
    { width: 1280, height: 800 },
    { width: 1024, height: 768 },
    { width: 390, height: 844 },
  ]) {
    const c = await browser.newContext({ viewport });
    await request(c, "students", {
      as_guest: true,
      age_band: "16-18",
      consent_analytics: true,
    });
    const p = await c.newPage();
    await p.addInitScript(() => {
      sessionStorage.setItem("adapt:seen-opening", "1");
      new PerformanceObserver((l) => {
        window.__lcp = l.getEntries().at(-1)?.startTime;
      }).observe({ type: "largest-contentful-paint", buffered: true });
    });
    const dir = join(SHOTS, `${viewport.width}x${viewport.height}`);
    await mkdir(dir, { recursive: true });
    for (const path of all) {
      await p.goto(BASE + path);
      await settled(p);
      if (path === "/baseline")
        await p.getByTestId("format-option").first().waitFor();
      const zone = await zones(p, `${viewport.width} ${path}`);
      if (viewport.width === 1280) {
        const hero = {
          "/": "study",
          "/journey": "study",
          "/baseline": "brain",
          "/first-lesson": "classroom",
          "/learn": "classroom",
          "/settings": "gears",
        }[path];
        const occupied = await p
          .locator('[data-practice-item],[data-testid="fl-moment"]')
          .count();
        if (hero === "classroom" && occupied)
          assert(
            !zone.art.some((a) => a.name === hero),
            "classroom hidden during teaching",
          );
        else if (hero)
          assert(
            zone.art.some((a) => a.name === hero),
            `${path} missing ${hero}`,
          );
      }
      await p
        .locator("[data-sheet] details")
        .evaluateAll((ns) => ns.forEach((n) => (n.open = true)));
      await zones(p, `${viewport.width} ${path} disclosures open`);
      await p
        .locator("[data-sheet] details")
        .evaluateAll((ns) => ns.forEach((n) => (n.open = false)));
      const perf = await p.evaluate(() => ({
        lcp: window.__lcp ?? null,
        bytes: performance
          .getEntriesByType("resource")
          .reduce((s, r) => s + r.encodedBodySize, 0),
        overflow: document.documentElement.scrollWidth > innerWidth,
      }));
      assert(!perf.overflow, `${path} overflow at ${viewport.width}`);
      assert(perf.bytes < 2_000_000, `${path} over 2MB: ${perf.bytes}`);
      if (perf.lcp !== null) assert(perf.lcp < 2500, `${path} LCP ${perf.lcp}`);
      result.routes.push({ path, viewport, ...zone, ...perf });
      await p.screenshot({
        path: join(dir, (path === "/" ? "home" : path.slice(1)) + ".png"),
        fullPage: true,
      });
    }
    await p.emulateMedia({ colorScheme: "dark" });
    await p.goto(BASE + "/journey");
    await settled(p);
    await zones(p, "dark " + viewport.width);
    await p.screenshot({ path: join(dir, "journey-dark.png"), fullPage: true });
    await c.close();
  }
  if (BEFORE) {
    const c = await browser.newContext({
        viewport: { width: 1280, height: 800 },
      }),
      p = await c.newPage();
    await frontDoor(p, BEFORE);
    await words(p, "before-consent", true);
    await agree(p);
    await words(p, "before-auth", true);
    await c.request.post(BEFORE + "/api/v1/students", {
      data: { as_guest: true, age_band: "16-18", consent_analytics: true },
    });
    await p.goto(BEFORE + "/journey");
    await p.locator('[data-testid="journey-card"]').waitFor();
    await words(p, "before-journey", true);
    await questions(p, "before", BEFORE, true);
    await c.close();
  }
  const assets = await readdir("public/art"),
    sizes = await Promise.all(
      assets.map(
        async (n) => gzipSync(await readFile("public/art/" + n)).length,
      ),
    );
  result.budgets = {
    artGzipBytes: sizes.reduce((a, b) => a + b, 0),
    maxArtGzipBytes: 150_000,
    localLcpMs: 2500,
    maxEncodedResourceBytes: 2_000_000,
  };
  assert(result.budgets.artGzipBytes < 150_000);
  if (process.env.ADAPT_BEFORE_STATIC_DIR) {
    const staticGzip = async (dir) => {
      let bytes = 0;
      for (const e of await readdir(dir, { withFileTypes: true })) {
        const path = join(dir, e.name);
        if (e.isDirectory()) bytes += await staticGzip(path);
        else if (/\.(js|css)$/.test(e.name))
          bytes += gzipSync(await readFile(path)).length;
      }
      return bytes;
    };
    const before = await staticGzip(process.env.ADAPT_BEFORE_STATIC_DIR);
    const after = await staticGzip(
      join(process.env.ADAPT_NEXT_DIST ?? ".next", "static"),
    );
    Object.assign(result.budgets, {
      beforeJsCssGzipBytes: before,
      afterJsCssGzipBytes: after,
      newCodeGzipBytes: Math.max(0, after - before),
      newCodeAndArtGzipBytes:
        Math.max(0, after - before) + result.budgets.artGzipBytes,
      maxNewCodeAndArtGzipBytes: 150_000,
    });
    assert(
      result.budgets.newCodeAndArtGzipBytes < 150_000,
      "new code + art exceed 150KB gzip",
    );
  }
  assert.deepEqual(result.errors, []);
  console.log("Round 4 browser gates passed");
} catch (e) {
  result.failure = e.stack;
  console.error(e);
  process.exitCode = 1;
} finally {
  await writeFile(
    join(OUT, "browser-results.json"),
    JSON.stringify(result, null, 2) + "\n",
  );
  await browser.close();
}
