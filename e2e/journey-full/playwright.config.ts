/**
 * Round 2, phase G: the full-journey browser suite.
 *
 * Run against a built web app on :3000 and the API on :8000 started with
 * `scripts/serve_with_fake_model.py` (the real app, a scripted fake model). The Chromium
 * binary comes from ADAPT_CHROMIUM (same convention as e2e/games); nothing here runs
 * `playwright install`.
 */
import { defineConfig } from "@playwright/test";

const chromium = process.env.ADAPT_CHROMIUM ?? "/opt/pw-browsers/chromium";
const base = process.env.ADAPT_E2E_BASE ?? "http://127.0.0.1:3000";
const extraArgs = (process.env.ADAPT_CHROMIUM_ARGS ?? "--no-sandbox --no-zygote --disable-gpu").split(" ").filter(Boolean);
const launchOptions = { executablePath: chromium, args: extraArgs };

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  timeout: 12 * 60 * 1000,
  expect: { timeout: 30_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    baseURL: base,
    trace: "retain-on-failure",
    actionTimeout: 30_000,
    screenshot: "off",
    launchOptions,
    reducedMotion: "reduce",
    locale: "en-GB",
  },
  projects: [
    {
      name: "desktop",
      use: { viewport: { width: 1280, height: 900 }, launchOptions },
    },
    {
      name: "mobile",
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, launchOptions },
    },
  ],
});
