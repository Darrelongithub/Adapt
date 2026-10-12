/**
 * The launcher's own behaviour, with no dev server of the user's in the way.
 *
 * `ADAPT_API=offline` is documented in `README.md` and in `server.mjs`'s header as "run the
 * interface against nothing" -- and it crashed the process at import time (`new URL("offline")`)
 * while the code below it was perfectly written to answer 503. That is what this file is for: the
 * offline mode is how the UI gets reviewed, screenshotted and reasoned about when the backend is
 * not there, so "it throws" is not a cosmetic bug, it removes the mode.
 *
 * It runs on an ephemeral port with its own Next output directory (`ADAPT_NEXT_DIST`), so it cannot
 * collide with a `next dev` someone else is already serving from this directory -- the same sharing
 * that makes `npm run build` break a live dev server.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

async function freePort() {
  const probe = createServer();
  probe.listen(0, "127.0.0.1");
  await once(probe, "listening");
  const { port } = probe.address();
  await new Promise((r) => probe.close(r));
  return port;
}

async function until(fn, { timeoutMs = 45_000, everyMs = 500 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await fn().catch(() => null);
    if (value) return value;
    if (Date.now() > deadline) return null;
    await new Promise((r) => setTimeout(r, everyMs));
  }
}

async function startOffline() {
  const port = await freePort();
  const out = mkdtempSync(join(tmpdir(), "adapt-next-offline-"));
  const child = spawn(process.execPath, ["server.mjs"], {
    cwd: HERE,
    env: { ...process.env, PORT: String(port), ADAPT_API: "offline", ADAPT_NEXT_DIST: out, HOST: "127.0.0.1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let logs = "";
  child.stdout.on("data", (b) => { logs += b; });
  child.stderr.on("data", (b) => { logs += b; });
  return { port, child, out, logs: () => logs };
}

test("ADAPT_API=offline answers 503 and stays up, and the UI still serves", async () => {
  const { port, child, out, logs } = await startOffline();
  try {
    const api = await until(async () => {
      const res = await fetch(`http://127.0.0.1:${port}/api/v1/health`);
      return res.status === 503 ? res : null;
    });
    assert.ok(api, `the api call never answered 503. output was:\n${logs()}`);
    const body = await api.json();
    assert.equal(body.error, "api_offline");
    assert.match(body.detail, /start the backend and reload/, "the refusal has to say what to do");
    assert.equal(child.exitCode, null, `the server died instead of refusing:\n${logs()}`);

    const page = await until(async () => {
      const res = await fetch(`http://127.0.0.1:${port}/`);
      return res.status === 200 ? res : null;
    });
    assert.ok(page, `the UI did not serve on its own:\n${logs()}`);
    const html = await page.text();
    assert.match(html, /ADAPT/, "the shell is the app's, not a blank page");
  } finally {
    child.kill("SIGTERM");
    await until(async () => (child.exitCode !== null ? true : null), { timeoutMs: 8_000 });
    if (child.exitCode === null) child.kill("SIGKILL");
    // and take the build output with us. A test that leaves a distDir behind is only safe while
    // os.tmpdir() happens to live outside the repository: with TMPDIR pointed inside a checkout,
    // the leftover was 1391 files of webpack cache that got committed with the next `git add`.
    // Best-effort tidying, deliberately not an assertion: Next writes its manifests on the way out,
    // so a sweep can remove the tree and still have a file land afterwards. What actually protects
    // the repository is `tmp/` in frontend/.gitignore -- this loop only keeps repeated runs from
    // stacking distDirs up. Never let the tidying itself decide the test's verdict.
    for (let n = 0; n < 10 && existsSync(out); n += 1) {
      rmSync(out, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      if (existsSync(out)) await sleep(200);
    }
    await new Promise((r) => setTimeout(r, 400)); // Next flushes its manifests on the way out; removing before that lands is
    rmSync(out, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 }); // how this test
    // learned to sweep in the first place.
  }
});
