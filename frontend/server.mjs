/**
 * ADAPT's front door.
 *
 * One origin for the browser: Next.js serves the app, and `/api/*`, `/healthz` and the
 * `/ws/voice` socket are proxied to the Python backend on the same machine. That means
 * the learner's session cookie is first-party to this origin (no CORS, no cross-site
 * cookie rules to get wrong) and the API never has to expose itself publicly.
 *
 * `ADAPT_API` points at the backend (default http://127.0.0.1:8000). Set
 * `ADAPT_API=offline` to start the UI with the API closed: every call fails, and the
 * app shows what it could not reach instead of pretending.
 */
import { createServer } from "node:http";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import next from "next";

const dev = process.env.NODE_ENV !== "production";
const hostname = process.env.HOST ?? "0.0.0.0";
const port = Number(process.env.PORT ?? 3000);
const backend = process.env.ADAPT_API ?? "http://127.0.0.1:8000";
const offline = backend === "offline";

/* Next reports anonymous usage by default. This app's own stance is that nothing leaves the
 * machine unless the operator opts in -- `local_only` is a health field, not a slogan -- so the
 * dev/serve entry point opts out on the user's behalf and says so. Set
 * NEXT_TELEMETRY_DISABLED=0 to put the choice back. */
process.env.NEXT_TELEMETRY_DISABLED ??= "1";

const app = next({ dev, hostname });
const handle = app.getRequestHandler();
await app.prepare();

const API_PREFIXES = ["/api/", "/healthz"];
const WS_PREFIXES = ["/ws/"];

// `offline` means "there is no backend URL at all", so construction has to wait for that check:
// `new URL("offline")` throws at import time, which turned the documented flag into a crash instead
// of the honest 503 the proxy below is written to return.
const target = offline ? null : new URL(backend);
const transport = target?.protocol === "https:" ? httpsRequest : httpRequest;

function proxy(req, res) {
  if (offline) {
    res.writeHead(503, { "content-type": "application/json", "cache-control": "no-store" });
    res.end(
      JSON.stringify({
        error: "api_offline",
        detail: "the frontend was started with ADAPT_API=offline; start the backend and reload",
        backend,
      }),
    );
    return;
  }
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const body = Buffer.concat(chunks);
    const headers = { ...req.headers };
    delete headers.host;
    delete headers.connection;
    delete headers["accept-encoding"]; // keep the upstream body gzipped-free: we forward bytes verbatim
    if (target.port) headers.host = `${target.hostname}:${target.port}`;
    const upstream = transport(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port || (target.protocol === "https:" ? 443 : 80),
        method: req.method,
        path: req.originalUrl ?? req.url,
        headers,
      },
      (up) => {
        const out = { ...up.headers };
        // The session cookie is scoped to this origin by the browser; the backend sets
        // it without a Domain attribute, so it passes through unchanged.
        res.writeHead(up.statusCode ?? 502, out);
        up.pipe(res);
      },
    );
    upstream.on("error", (err) => {
      res.writeHead(502, { "content-type": "application/json", "cache-control": "no-store" });
      res.end(
        JSON.stringify({
          error: "api_unreachable",
          detail: `the backend at ${backend} did not answer (${err.code || err.message}). Start it with: uvicorn adapt.api.app:app --port 8000`,
        }),
      );
    });
    if (body.length) upstream.end(body);
    else upstream.end();
  });
}

const server = createServer((req, res) => {
  const url = req.url ?? "/";
  if (API_PREFIXES.some((p) => url.startsWith(p)) || url === "/healthz") return proxy(req, res);
  return handle(req, res);
});

server.on("upgrade", (req, socket, head) => {
  const url = req.url ?? "";
  if (offline || !WS_PREFIXES.some((p) => url.startsWith(p))) {
    socket.destroy();
    return;
  }
  const headers = { ...req.headers };
  delete headers.host;
  if (target.port) headers.host = `${target.hostname}:${target.port}`;
  const proxyReq = transport({
    protocol: target.protocol,
    hostname: target.hostname,
    port: target.port || 80,
    method: "GET",
    path: url,
    headers: { ...headers, connection: "Upgrade", upgrade: req.headers.upgrade ?? "websocket" },
  });
  proxyReq.on("upgrade", (upReq, upSocket, upHead) => {
    // The 101 is forwarded header for header, not rebuilt from three hand-picked ones.
    // Writing only Upgrade / Connection / Sec-WebSocket-Accept *dropped*
    // `Sec-WebSocket-Extensions`: the browser (which always offers permessage-deflate) was
    // told nothing was negotiated while uvicorn compressed the frames anyway, so it rejected
    // the first frame with "reserved bits must be zero" and the voice screen stayed silent --
    // through this proxy only, which is why the direct-API tests never saw it. `head` and
    // `upHead` carry whatever arrived in the same segment as a handshake and have to be
    // written before the pipes start, or the first frame is read from the wrong offset.
    const lines = ["HTTP/1.1 101 Switching Protocols"];
    for (const [name, value] of Object.entries(upReq.headers)) {
      lines.push(`${name}: ${Array.isArray(value) ? value.join(", ") : value}`);
    }
    socket.write(lines.join("\r\n") + "\r\n\r\n");
    socket.setTimeout(0);
    upSocket.setTimeout(0);
    if (head?.length) upSocket.write(head);
    if (upHead?.length) socket.write(upHead);
    upSocket.pipe(socket);
    socket.pipe(upSocket);
  });
  proxyReq.on("error", () => socket.destroy());
  // A refused handshake must reach the browser as a refusal. Without this the socket
  // simply hangs: the user sees "connecting forever" instead of "your session is not
  // valid for voice", which is the difference between a confusing app and a broken one.
  proxyReq.on("response", (up) => {
    const chunks = [];
    up.on("data", (c) => chunks.push(c));
    up.on("end", () => {
      const note =
        up.statusCode === 401 || up.statusCode === 403 || up.statusCode === 4401
          ? `the backend refused the voice socket (${up.statusCode}): sign in first, then reconnect`
          : `the backend answered the upgrade with HTTP ${up.statusCode}`;
      const body = JSON.stringify({ error: "ws_handshake_refused", detail: note });
      socket.write(
        `HTTP/1.1 ${up.statusCode === 101 ? 502 : up.statusCode} Bad Gateway\r\n` +
          "content-type: application/json\r\ncontent-length: " +
          Buffer.byteLength(body) +
          "\r\nconnection: close\r\n\r\n" +
          body,
      );
      socket.destroy();
    });
  });
  proxyReq.end();
});

server.listen(port, hostname, () => {
  console.log(`ADAPT web  http://${hostname}:${port}  (dev=${dev})`);
  console.log(`API + voice socket proxied to ${offline ? "offline (503 for every call)" : backend}`);
});
