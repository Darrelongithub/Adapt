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
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const TSCONFIG_PATH = join(HERE, "tsconfig.json");

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

async function startServer({ api, prefix }) {
  const port = await freePort();
  const out = mkdtempSync(join(tmpdir(), prefix));
  const tsconfigBefore = readFileSync(TSCONFIG_PATH, "utf8");
  const child = spawn(process.execPath, ["server.mjs"], {
    cwd: HERE,
    env: { ...process.env, PORT: String(port), ADAPT_API: api, ADAPT_NEXT_DIST: out, HOST: "127.0.0.1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let logs = "";
  child.stdout.on("data", (b) => { logs += b; });
  child.stderr.on("data", (b) => { logs += b; });
  return { port, child, out, tsconfigBefore, logs: () => logs };
}

async function startOffline() {
  return startServer({ api: "offline", prefix: "adapt-next-offline-" });
}

/** Stop a launcher, take its build output with it, and restore tsconfig.json. */
async function stopAndSweep(child, out, tsconfigBefore) {
  child.kill("SIGTERM");
  await until(async () => (child.exitCode !== null ? true : null), { timeoutMs: 8_000 });
  if (child.exitCode === null) child.kill("SIGKILL");
  for (let n = 0; n < 10 && existsSync(out); n += 1) {
    rmSync(out, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    if (existsSync(out)) await sleep(200);
  }
  await sleep(400); // Next flushes its manifests on the way out; see the note in the first test.
  rmSync(out, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  if (typeof tsconfigBefore === "string" && readFileSync(TSCONFIG_PATH, "utf8") !== tsconfigBefore) {
    writeFileSync(TSCONFIG_PATH, tsconfigBefore, "utf8");
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/** One request with a Host header of our choosing -- what a phone on the wifi sends. */
function requestWithHost({ port, host, path: requestPath, upgrade = false, headers = {} }) {
  return new Promise((resolve, reject) => {
    const extra = upgrade
      ? { Connection: "Upgrade", Upgrade: "websocket", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version": "13" }
      : {};
    const req = httpRequest(
      { host: "127.0.0.1", port, path: requestPath, headers: { ...headers, ...extra, Host: host } },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString() }));
      },
    );
    req.on("upgrade", (res, socket) => {
      socket.destroy();
      resolve({ status: res.statusCode, headers: res.headers, body: "" });
    });
    req.on("error", reject);
    req.end();
  });
}

test("ADAPT_API=offline answers 503 and stays up, and the UI still serves", async () => {
  const { port, child, out, tsconfigBefore, logs } = await startOffline();
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
    await stopAndSweep(child, out, tsconfigBefore);
  }
  assert.equal(readFileSync(TSCONFIG_PATH, "utf8"), tsconfigBefore, "tsconfig.json must remain byte-identical after offline test");
});

test("a LAN Host header reaches the API as 127.0.0.1, and the 101 keeps its own headers", async () => {
  // Why these two things and why here: everyone in a room talks to :3000 only, so the proxy's
  // Host rewrite is what keeps the API's untrusted-host refusal from ever being aimed at a
  // learner (and what makes `adapt host` possible without trusting a subnet). And the upgrade
  // path used to rebuild the 101 from three headers, dropping `Sec-WebSocket-Extensions` while
  // uvicorn went on compressing the frames -- a browser then rejects the stream with "reserved
  // bits must be zero" and the voice screen never hears anything. Both are invisible to a
  // direct-API test, which is exactly why they belong to the launcher's own test file.
  const upstream = createServer((req, res) => {
    seen.push(req.headers.host);
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true, host: req.headers.host }));
  });
  const seen = [];
  const sockets = new Set(); // the upgraded socket outlives the request; the test has to end it
  let seenUpgrade;
  upstream.on("upgrade", (req, socket) => {
    seenUpgrade = req.headers.host;
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
    socket.write(
      "HTTP/1.1 101 Switching Protocols\r\n" +
        "Upgrade: websocket\r\n" +
        "Connection: Upgrade\r\n" +
        `Sec-WebSocket-Accept: ${req.headers["sec-websocket-key"] ?? ""}\r\n` +
        "Sec-WebSocket-Extensions: permessage-deflate; server_max_window_bits=12\r\n\r\n",
    );
  });
  await new Promise((r) => upstream.listen(0, "127.0.0.1", r));
  const apiPort = upstream.address().port;
  const { port, child, out, tsconfigBefore, logs } = await startServer({ api: `http://127.0.0.1:${apiPort}`, prefix: "adapt-next-proxy-" });
  try {
    const answer = await until(async () => {
      const res = await requestWithHost({ port, host: "192.168.1.50:3000", path: "/api/v1/health" }).catch(() => null);
      return res && res.status === 200 ? res : null;
    });
    assert.ok(answer, `the proxied request never answered 200:\n${logs()}`);
    assert.equal(JSON.parse(answer.body).host, `127.0.0.1:${apiPort}`, "the proxy must not forward the browser's Host");
    assert.deepEqual(seen, [`127.0.0.1:${apiPort}`]);

    const upgrade = await requestWithHost({
      port,
      host: "192.168.1.50:3000",
      path: "/ws/voice",
      upgrade: true,
      headers: { "Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits" },
    });
    assert.equal(upgrade.status, 101, `the upgrade was not answered with 101:\n${logs()}`);
    assert.equal(seenUpgrade, `127.0.0.1:${apiPort}`, "the upgrade must be forwarded with the API's own Host");
    assert.match(String(upgrade.headers["sec-websocket-extensions"]), /permessage-deflate/);
    assert.equal(String(upgrade.headers.connection).toLowerCase(), "upgrade");
  } finally {
    await stopAndSweep(child, out, tsconfigBefore);
    // No awaiting upstream.close(): it only calls back once every connection is gone, and a
    // socket that is merely open (never closed by either side) is not a reason to hang a test.
    for (const socket of sockets) socket.destroy();
    upstream.closeAllConnections?.();
    upstream.close();
  }
  assert.equal(readFileSync(TSCONFIG_PATH, "utf8"), tsconfigBefore, "tsconfig.json must remain byte-identical after proxy test");
});
