#!/usr/bin/env node
/**
 * The frontend/backend contract, checked from the outside.
 *
 * Four things go wrong in a two-app repo like this one, and each one is silent until a
 * learner meets it: a page calls an endpoint that does not exist (404 rendered as "no data");
 * `?mock=1` has no fixture for a real path (the mock review tour shows an error where the
 * design was supposed to shine); a page module exports something Next forbids (the build
 * fails, and a dev who only ran `next dev` never sees it); and browser code reaches for
 * `localhost:8000` or a `process.env` that is undefined in the client (works on this
 * machine, broken behind a tunnel).
 *
 * Nothing here needs a server, a key, or the network: both sides are read as source.
 * Run it as `npm run lint` (frontend) or `node scripts/check-contract.mjs`.
 */

import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BACKEND = join(ROOT, "backend", "src", "adapt");
const FE_SRC = join(ROOT, "frontend", "src");
const FAILURES = [];
const NOTES = [];

function walk(dir, filter) {
  const out = [];
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walk(full, filter));
    else if (filter(entry)) out.push(full);
  }
  return out;
}

const read = (file) => readFileSync(file, "utf8");

/* ---------------------------------------------------------------- backend ---- */

/** Every (METHOD, /full/path) the API declares, prefixes included. */
function backendRoutes() {
  const routes = new Set();
  const files = walk(join(BACKEND, "api"), (n) => n.endsWith(".py"));
  for (const file of files) {
    const src = read(file);
    const prefix = /APIRouter\(\s*prefix="([^"]+)"/.exec(src)?.[1] ?? "";
    // `("")` is how a router's own prefix is mounted — `@router.get("")` on
    // `/api/v1/lessons` *is* the collection route — so the path may be empty. Requiring
    // one character here hid every list endpoint in the app from both checks below.
    for (const m of src.matchAll(/@router\.(get|post|put|patch|delete)\(\s*"([^"]*)"/g)) {
      const path = (prefix + m[2]).replace(/\/+$/, "") || "/";
      routes.add(`${m[1].toUpperCase()} ${path}`);
    }
    // routers mounted with a url_prefix, e.g. the websocket
    for (const m of src.matchAll(/\.mount\(\s*"([^"]+)"[\s\S]{0,400}?@router\.(get|post)\(\s*"([^"]+)"/g)) {
      routes.add(`${m[2].toUpperCase()} ${m[1]}${m[3]}`);
    }
  }
  // the websocket endpoint is not an HTTP route and cannot be found by path matching
  for (const m of read(join(BACKEND, "api", "ws.py")).matchAll(/router\.(websocket|route)\(\s*"([^"]+)"/g)) {
    routes.add(`WS ${m[2]}`);
  }
  return routes;
}

/** Paths the frontend asks the backend for, from the api helpers and raw fetches. */
function frontendCalls() {
  const calls = [];
  for (const file of walk(FE_SRC, (n) => /\.(ts|tsx)$/.test(n) && !n.includes("mock"))) {
    const src = read(file);
    const rel = relative(ROOT, file);
    // api<T>(path, { method: "POST", ... }) / apiPost(path, ...) / apiVoid(path, ...) /
    // useApi<T>(path, query). The options object is usually several lines long, so the
    // scan takes the balanced-ish run up to the closing brace rather than one line.
    // `get`/`post`/`del` are the wrappers every page actually uses, so a scan of
    // `api|useApi` alone left most of the app unchecked: the paths below `post(` were
    // neither matched against the API nor against a fixture row.
    // The generic-argument part is deliberately narrow (`<[^()<>]*>`): it used to be a
    // paren-balanced run, which is greedy, so `post<BaselineView>("/api/v1/baseline", {})`
    // followed by another generic call on a later line matched as ONE call whose "path" was the
    // later string and whose method was the earlier function. A type argument in this repo is
    // always a bare name, so nothing legitimate is lost by refusing to cross a bracket.
    const CALL = /\b(api|apiPost|apiVoid|useApi|get|post|del)\s*(?:<[^()<>]*>)?\s*\(\s*(`[^`]*`|"[^"]*"|'[^']*')/g;
    for (const m of src.matchAll(CALL)) {
      const fn = m[1];
      const literal = m[2].slice(1, -1);
      const tail = src.slice(m.index + m[0].length, m.index + m[0].length + 400);
      const opts = /^\s*,\s*\{([\s\S]{0,300}?)\}/.exec(tail)?.[1] ?? "";
      let method = /method:\s*"(\w+)"/.exec(opts)?.[1];
      if (!method) method = fn === "apiVoid" || fn === "del" ? "DELETE" : fn === "apiPost" || fn === "post" ? "POST" : "GET";
      const csrfOnGet = /csrf:\s*true/.test(opts);
      calls.push({ rel, method, path: literal, fn, csrfOnGet });
    }
    // a bare fetch of an API path is how a page skips CSRF; flag the shape only
    for (const m of src.matchAll(/\bfetch\(\s*(`[^`]*`|"[^"]*")/g)) {
      const literal = m[1].slice(1, -1);
      if (literal.startsWith("/api/")) calls.push({ rel, method: "GET", path: literal, raw: m[1], bare: true });
    }
    // query hooks read through the same helpers, so nothing else to scan
  }
  return calls;
}

function normalise(path) {
  // /items/${id}/reveal -> /items/*/reveal ; drop any query string
  return path
    .split("?")[0]
    .replace(/\$\{[^}]*\}/g, "*")
    .replace(/\/+$/, "") || "/";
}

function matches(template, concrete) {
  const t = template.split("/").filter(Boolean);
  const c = concrete.split("/").filter(Boolean);
  if (t.length !== c.length) return false;
  return t.every((seg, i) => seg.startsWith("{") || seg === "*" || seg === c[i] || (seg === c[i] && seg !== ""));
}

/* ------------------------------------------------------------------ checks ---- */

const routes = backendRoutes();
if (routes.size < 40) FAILURES.push(`backend route scan found only ${routes.size} routes: the scanner is broken, not the API`);

const templateOf = (method, path) => `${method} ${path}`;

for (const call of frontendCalls()) {
  const path = normalise(call.path);
  if (!path.startsWith("/api/")) continue;
  const candidates = [...routes].filter((r) => r.startsWith(`${call.method} `));
  const hit = candidates.some((r) => matches(r.slice(call.method.length + 1), path));
  if (!hit) {
    FAILURES.push(`${call.rel}: ${call.method} ${path} is not an endpoint the backend declares`);
  }
  if (call.bare) FAILURES.push(`${call.rel}: a bare fetch() to ${path} skips api(): the CSRF header and the mock flag both live there`);
}

/* mock parity: every path the app can request must have a fixture, or `?mock=1` lies */
const mockSrc = existsSync(join(FE_SRC, "lib", "mock.ts")) ? read(join(FE_SRC, "lib", "mock.ts")) : "";
if (!mockSrc) {
  NOTES.push("no src/lib/mock.ts: skipping mock parity (the ?mock=1 tour is not defined)");
} else {
  // mockLookup() tests each row's `match` RegExp against "METHOD url", so parity means
  // "some row's regex accepts a concrete sample of this path" -- not a string equality.
  const rows = [];
  // Read each row's literal by hand: a `/` inside a character class (`[^/]*`) is not the
  // end of the regex, which is exactly the pattern most of these rows use.
  const rowRe = /method:\s*"(\w+)",\s*match:\s*\//g;
  for (const m of mockSrc.matchAll(rowRe)) {
    let i = m.index + m[0].length;
    let body = "";
    let inClass = false;
    while (i < mockSrc.length) {
      const ch = mockSrc[i];
      if (ch === "\\") {
        body += mockSrc[i] + (mockSrc[i + 1] ?? "");
        i += 2;
        continue;
      }
      if (ch === "[") inClass = true;
      else if (ch === "]") inClass = false;
      else if (ch === "/" && !inClass) break;
      body += ch;
      i += 1;
    }
    const flags = /^[gimsuy]+/.exec(mockSrc.slice(i + 1))?.[0] ?? "";
    try {
      rows.push({ method: m[1], re: new RegExp(body, flags) });
    } catch (err) {
      FAILURES.push(`mock: could not parse row /${body}/${flags} (${err.message})`);
    }
  }
  if (rows.length < 10) FAILURES.push(`mock: only ${rows.length} rows parsed from src/lib/mock.ts -- the parity check is reading the wrong shape`);
  const wanted = new Map();
  for (const call of frontendCalls()) {
    if (!call.path.startsWith("/api/")) continue;
    const sample = normalise(call.path).replace(/\*/g, "1");
    wanted.set(`${call.method} ${sample}`, call);
  }
  for (const [key, call] of wanted) {
    const method = key.slice(0, key.indexOf(" "));
    const url = key.slice(key.indexOf(" ") + 1);
    if (!rows.some((r) => r.method === method && r.re.test(url))) {
      FAILURES.push(`${call.rel}: ${method} ${normalise(call.path)} has no mock row -- ?mock=1 would 404 exactly there`);
    }
  }
}

/* Next's export rule for route modules: a page/layout may default-export the component
   and only a named allow-list otherwise. Breaking it fails `next build`, not `next dev`. */
const ALLOWED_ROUTE_EXPORTS = new Set(["default", "dynamic", "metadata", "revalidate", "fetchCache", "runtime", "viewport", "generateMetadata", "generateStaticParams"]);
for (const file of walk(join(FE_SRC, "app"), (n) => /page\.tsx$|layout\.tsx$/.test(n))) {
  const src = read(file);
  for (const m of src.matchAll(/^export\s+(?:async\s+)?(?:const|function|type|interface|class)\s+(\w+)/gm)) {
    if (!ALLOWED_ROUTE_EXPORTS.has(m[1])) {
      FAILURES.push(`${relative(ROOT, file)}: route modules may not export \`${m[1]}\` (next build rejects it; next dev does not)`);
    }
  }
  if (!/export default/.test(src)) FAILURES.push(`${relative(ROOT, file)}: no default export -- Next has nothing to render`);
}

/* browser code must not name a host or read a non-public env var */
for (const file of walk(FE_SRC, (n) => /\.(ts|tsx)$/.test(n))) {
  const src = read(file);
  for (const m of src.matchAll(/https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?/g)) {
    FAILURES.push(`${relative(ROOT, file)}: hard-coded origin ${m[0]} -- behind a tunnel this is the user's own laptop`);
  }
  for (const m of src.matchAll(/process\.env\.([A-Z0-9_]+)/g)) {
    if (!m[1].startsWith("NEXT_PUBLIC_")) {
      FAILURES.push(`${relative(ROOT, file)}: process.env.${m[1]} is undefined in the browser; only NEXT_PUBLIC_* is inlined at build`);
    }
  }
  // a secret-shaped literal in client source ships in the bundle
  for (const m of src.matchAll(/(AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,})/g)) {
    FAILURES.push(`${relative(ROOT, file)}: looks like a real API key in browser-shipped source`);
  }
}

/* the dev/preview server: the WS proxy must be allowed through the rewrite list */
// The /ws proxy has to exist somewhere between the browser and uvicorn. In this repo it
// is the custom server (server.mjs), because Next's rewrites cannot upgrade a websocket.
const proxyFiles = ["next.config.mjs", "server.mjs"].filter((f) => existsSync(join(ROOT, "frontend", f)));
const proxySrc = proxyFiles.map((f) => read(join(ROOT, "frontend", f))).join("\n");
if (!/\/ws/.test(proxySrc)) {
  FAILURES.push("frontend: nothing proxies /ws/voice -- the browser would ask Next for the websocket handshake, which answers 405");
}
// The dev server refuses a page whose origin it does not know — but only for /_next/*, so
// the HTML arrives, the screen paints, and no handler is wired. Checked here because nothing
// else in the build notices: `next build` has no host check, and `next dev` prints a warning
// that nobody reads when the page looks fine.
const devSrc = ["next.config.mjs", "next.config.js", "next.config.ts"].map((f) => (existsSync(join(ROOT, "frontend", f)) ? read(join(ROOT, "frontend", f)) : "")).join("\n");
if (devSrc) {
  const allowed = /allowedDevOrigins:\s*\[([\s\S]{0,600}?)\]/.exec(devSrc)?.[1] ?? "";
  const covers = ["localhost", "127.0.0.1", "e2b.app"];
  const missing = covers.filter((h) => !allowed.includes(h));
  if (missing.length) {
    FAILURES.push(`frontend next.config: allowedDevOrigins does not cover ${missing.join(", ")} — a page opened from that origin paints and never hydrates (set ADAPT_DEV_ORIGIN for others)`);
  }
}
for (const file of proxyFiles) {
  const src = read(join(ROOT, "frontend", file));
  if (/destination:\s*"http:\/\/127\.0\.0\.1:8000(?!\")/.test(src)) {
    // server.mjs reads ADAPT_API; a note naming some other variable would send someone to set a
    // lever that is attached to nothing
    NOTES.push(`${file}: the proxy target is 127.0.0.1:8000 with no override -- read it from process.env.ADAPT_API, as server.mjs does, for a non-default backend port`);
  }
}

/* scene kinds: the engine's set, the stages that draw it, and the mock that reviews it */
{
  /** the identifier keys one level inside an object literal, brace-depth by hand */
  const keysInside = (block) => {
    if (!block) return [];
    const keys = [];
    let depth = 0;
    let i = 0;
    while (i < block.length) {
      const ch = block[i];
      if (ch === '"' || ch === "'" || ch === "`") {
        i += 1;
        while (i < block.length && !(block[i] === ch && block[i - 1] !== "\\")) i += 1;
        i += 1;
        continue;
      }
      if (ch === "{" || ch === "[" || ch === "(") depth += 1;
      else if (ch === "}" || ch === "]" || ch === ")") depth -= 1;
      else if (depth === 1) {
        const m = /^([A-Za-z_][A-Za-z0-9_]*)\s*:/.exec(block.slice(i, i + 64));
        const before = block.slice(0, i).replace(/\s+$/, "").slice(-1);
        if (m && (before === "{" || before === ",")) {
          keys.push(m[1]);
          i += m[0].length;
          continue;
        }
      }
      i += 1;
    }
    return keys;
  };
  /** the `{ ... }` that `marker`'s value opens, balanced by hand (nested literals included) */
  const objectAfter = (src, marker) => {
    const at = src.indexOf(marker);
    if (at === -1) return null;
    const open = src.indexOf("{", at);
    if (open === -1) return null;
    let depth = 0;
    for (let i = open; i < src.length; i += 1) {
      if (src[i] === "{") depth += 1;
      else if (src[i] === "}" && (depth -= 1) === 0) return src.slice(open, i + 1);
    }
    return null;
  };

  const simsDir = join(ROOT, "backend", "src", "adapt", "learning", "sims");
  const engineKinds = existsSync(simsDir)
    ? readdirSync(simsDir).filter((f) => f.endsWith(".py") && !f.startsWith("_")).map((f) => f.slice(0, -3)).sort()
    : [];
  if (engineKinds.length < 2) {
    FAILURES.push(`sims: ${engineKinds.length} kind module(s) under ${relative(ROOT, simsDir)} -- the kind scan is reading the wrong directory, not a two-kind engine`);
  } else {
    // backend/src/adapt/learning/sims/__init__.py tells the reader that "the contract check
    // asserts the frontend's renderer registry agrees with this set". A comment describing a
    // gate that does not run is worse than no comment, so this is that gate.
    const stagesSrc = existsSync(join(FE_SRC, "components", "scenes", "index.tsx"))
      ? read(join(FE_SRC, "components", "scenes", "index.tsx"))
      : "";
    const drawn = keysInside(objectAfter(stagesSrc, "const KNOWN")).sort();
    if (!drawn.length) {
      FAILURES.push("stages: could not read the KNOWN registry out of components/scenes/index.tsx -- this check is not running");
    } else {
      const notDrawn = engineKinds.filter((k) => !drawn.includes(k));
      const stale = drawn.filter((k) => !engineKinds.includes(k));
      if (notDrawn.length) FAILURES.push(`stages: the engine computes ${notDrawn.join(", ")} and no built-in stage draws it -- the page falls back to narration for a shipped kind (add the stage, or drop the kind)`);
      if (stale.length) FAILURES.push(`stages: KNOWN lists ${stale.join(", ")} with no sim module behind it -- emphasis ids for a kind nobody computes`);
    }
    if (mockSrc) {
      const mocked = keysInside(objectAfter(objectAfter(mockSrc, "const lessonKinds"), "kinds:")).sort();
      if (!mocked.length) {
        FAILURES.push("mock: could not read lessonKinds.kinds out of src/lib/mock.ts -- the mock lesson tour is not the shape this check expects");
      } else {
        const absent = engineKinds.filter((k) => !mocked.includes(k));
        const ghost = mocked.filter((k) => !engineKinds.includes(k));
        if (absent.length) FAILURES.push(`mock: ?mock=1 advertises no kind entry for ${absent.join(", ")} -- a scene that cannot be reviewed without a backend is a scene that gets shipped unreviewed`);
        if (ghost.length) FAILURES.push(`mock: lessonKinds carries ${ghost.join(", ")}, which the engine no longer computes -- the review tour would validate a design that cannot run`);
      }
      // two fixture scenes sharing an id is invisible until React warns about one key for two
      // rows -- and "open" then silently always hits the first of them
      const list = objectAfter(mockSrc, "const lessonList");
      const names = ((list ?? "").match(/scenes:\s*\[([^\]]*)\]/)?.[1] ?? "").split(",").map((s) => s.trim()).filter(Boolean);
      const ids = names.map((n) => (mockSrc.match(new RegExp(`const ${n} =\\s*\\{[\\s\\S]{0,400}?\\bid:\\s*"([^"]+)"`)) ?? [])[1]);
      const dupes = ids.filter((v, i2) => v && ids.indexOf(v) !== i2);
      if (!names.length) FAILURES.push("mock: no scene list found in lessonList -- the id uniqueness check is reading the wrong shape");
      else if (dupes.length) FAILURES.push(`mock: fixture scenes share the id ${dupes.join(", ")} -- two rows under one React key, and "open" always opens the first`);
    }
    if (drawn.length && engineKinds.length === drawn.length) NOTES.push(`${engineKinds.length} scene kinds: engine, stages and mock agree`);
  }
}

/* ------------------------------------------------------------------ report ---- */

console.log(`checked ${routes.size} backend routes against ${frontendCalls().length} frontend calls`);
for (const note of NOTES) console.log(`note: ${note}`);
if (FAILURES.length) {
  console.error(`\n${FAILURES.length} contract problem(s):`);
  for (const f of FAILURES) console.error(`  - ${f}`);
  process.exit(1);
}
console.log("contract ok: every path the frontend calls exists, ?mock=1 covers them, the scene kinds agree across engine/stages/mock, route modules export what Next allows, no host or env misuse in browser code");
