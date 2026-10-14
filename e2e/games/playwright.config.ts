/**
 * Round 2, phase F: the browser suite for the baseline games and the subject round.
 *
 * Run against a built web app and a running API (see README.md in this folder). The Chromium binary
 * is taken from ADAPT_CHROMIUM (default /home/user/.cache/chromium-bin/chromium, Chromium 153 from the
 * @sparticuz/chromium npm package; it needs LD_LIBRARY_PATH=/home/user/.cache/chromium-bin/lib); nothing here runs `playwright install`.
 *
 * Projects:
 *   desktop   1280x900, the mouse
 *   mobile    390x844, touch, the same games
 *   keyboard  1280x900, every action by Tab, Enter, Space, arrows and typing: no clicks
 */
import { defineConfig } from "@playwright/test";

const chromium = process.env.ADAPT_CHROMIUM ?? "/home/user/.cache/chromium-bin/chromium";
const base = process.env.ADAPT_E2E_BASE ?? "http://127.0.0.1:3000";
const api = process.env.ADAPT_E2E_API ?? "http://127.0.0.1:8000";
const serve = process.env.ADAPT_E2E_SERVE === "1";
const extraArgs = (process.env.ADAPT_CHROMIUM_ARGS ?? "--no-sandbox --no-zygote --disable-gpu").split(" ").filter(Boolean);
const dataDir = process.env.ADAPT_E2E_DATA ?? "/tmp/adapt-e2e-data";

const launchOptions = { executablePath: chromium, args: extraArgs };

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  // A full baseline run is minutes long (the vault flashes its sequences); a stuck run must still end.
  timeout: 20 * 60 * 1000,
  expect: { timeout: 20_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "results.json" }]],
  outputDir: "test-results",
  webServer: serve
    ? [
        {
          command: "/home/user/.venv-adapt/bin/uvicorn adapt.api.app:app --host 0.0.0.0 --port 8000",
          cwd: "../../backend",
          url: `${api}/healthz`,
          reuseExistingServer: true,
          timeout: 120_000,
          env: { ADAPT_DATA_DIR: dataDir, ADAPT_RATE_LIMIT_MULTIPLIER: "60", ADAPT_LOG_LEVEL: "WARNING" },
        },
        {
          command: "node server.mjs",
          cwd: "../../frontend",
          url: base,
          reuseExistingServer: true,
          timeout: 120_000,
          env: { NODE_ENV: "production", PORT: "3000", ADAPT_API: api },
        },
      ]
    : undefined,
  use: {
    baseURL: base,
    trace: "retain-on-failure",
    screenshot: "off",
    launchOptions,
    // The vault flash is shorter under reduced motion; the sequence itself is the same.
    reducedMotion: "reduce",
    locale: "en-GB",
  },
  projects: [
    {
      name: "desktop",
      testIgnore: [/keyboard\.spec/],
      use: { viewport: { width: 1280, height: 900 }, launchOptions },
    },
    {
      name: "mobile",
      testIgnore: [/keyboard\.spec/],
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, launchOptions },
    },
    {
      name: "keyboard",
      testMatch: /keyboard\.spec\.ts/,
      use: { viewport: { width: 1280, height: 900 }, launchOptions },
    },
  ],
});
