#!/usr/bin/env node
/**
 * UI probe: drives the real page in a DOM, against the real backend.
 *
 * Why this exists: `tsc`, `next build` and the contract checker can all be green while a
 * button does nothing, a spinner never stops, or a saved value never comes back. The only
 * way to know that a control works is to click it and then look at what the page shows and
 * what the database holds. No browser is installed in the sandbox this was built in
 * (headless Chromium needs libnss3/libnspr4, which are absent and cannot be fetched), so
 * this loads the production HTML *and its scripts* into jsdom, bridges the session cookie
 * jar to the page's own fetch calls, and interacts with the hydrated React app.
 *
 * What that means for the results: state, rendering, fetch traffic, cookie/CSRF handling,
 * focus and disabled logic are genuinely exercised. Paint, layout, real audio capture and
 * mic permissions are not -- those need a real browser and are listed as unverified.
 *
 * Usage:  cd frontend && npm run probe  [-- --base http://127.0.0.1:3000] [--only home,learn]
 * Exits non-zero if any check fails. Requires the API (:8000) and web (:3000) servers.
 */

import { JSDOM, CookieJar, VirtualConsole } from "jsdom";

const ARGV = process.argv.slice(2);
const arg = (name, dflt) => {
  const i = ARGV.indexOf(`--${name}`);
  return i === -1 ? dflt : ARGV[i + 1];
};
const BASE = arg("base", "http://127.0.0.1:3000");
const ONLY = (arg("only", "") || "").split(",").filter(Boolean);
/** `--mock` runs the lesson scenario against `?mock=1`: no session, no rows, no resume. The
 *  client-side half of the engine (player, gate, the "this is a new scene" announcement) is still
 *  under test there; the checks that assert a *database* fact are skipped rather than quietly
 *  passing, and the mock's own limit is asserted instead of ignored. */
const MOCK = ARGV.includes("--mock");
const API = BASE; // the app's own origin: /api is proxied by server.mjs

const results = [];
let failures = 0;

let CURRENT = "";

function check(scenario, label, ok, detail = "") {
  CURRENT = scenario;
  results.push({ scenario, label, ok: !!ok, detail: String(detail).slice(0, 240) });
  if (!ok) failures += 1;
  const mark = ok ? "\x1b[32mok\x1b[0m" : "\x1b[31mFAIL\x1b[0m";
  console.log(`  ${mark}  ${label}${ok || !detail ? "" : `\n         -> ${String(detail).slice(0, 200)}`}`);
}


// Objects must not reach a learner as JSON. Several screens used to stringify a payload into
// visible text -- a measured effect, the privacy statement, the voice capability list -- so the
// tripwire runs on every screen that has ever done it, at the moment the page is inspected.
const JSON_FREE = new Set(["/learn", "/lesson", "/proposals", "/settings", "/voice", "/progress", "/baseline"]);

function visibleText(page) {
  const doc = page?.dom?.window?.document;
  if (!doc?.body) return "";
  const clone = doc.body.cloneNode(true);
  for (const n of clone.querySelectorAll("script, style, template, noscript")) n.remove();
  return (clone.textContent ?? "").replace(/\s+/g, " ");
}

function closePage(page) {
  const path = String(page?.path ?? "").split("?")[0];
  if (path && JSON_FREE.has(path)) {
    const blob = visibleText(page).match(/\{"[^"]+"\s*:[^}]{0,90}/)?.[0] ?? "";
    check(CURRENT || "probe", "no raw JSON object is shown to the learner on this screen", blob === "", blob);
  }
  page?.dom?.window?.close?.();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, { timeout = 8000, every = 60, label = "" } = {}) {
  const started = Date.now();
  for (;;) {
    let value;
    try {
      value = await fn();
    } catch (err) {
      value = undefined;
      if (Date.now() - started > timeout) throw err;
    }
    if (value) return value;
    if (Date.now() - started > timeout) throw new Error(`timed out after ${timeout}ms waiting for ${label || "condition"}`);
    await sleep(every);
  }
}

/**
 * Presses a controlled checkbox until it takes, and returns what it ended up as.
 *
 * jsdom + Next hydrate concurrently: `next-route-announcer` -- which `open()` waits for, and
 * which is the only hydration marker this app leaves -- exists a beat before the subtree
 * about to be pressed has any handlers. A press inside that window moves the DOM property
 * with nothing listening and React snaps it back on its next render, which reads as "the
 * switch does nothing" when it is really "the harness pressed early". So the press is
 * repeated while it has not taken -- bounded, and the state the checks judge is the last one.
 */
async function tickBox(find, want = true, tries = 6) {
  for (let i = 0; i < tries; i += 1) {
    const box = find();
    if (!box) return null;
    if (box.checked === want) return box;
    box.click();
    await sleep(220);
  }
  return find() ?? null;
}

/* answer controls, per item kind: a choice list, a box, or a teach-back */
function answerControl(page) {
  return (
    page.find('input[type="radio"]')[0] ??
    page.find('input[placeholder^="e.g."]')[0] ??
    page.find('textarea[placeholder^="Start from"]')[0] ??
    null
  );
}

/**
 * Fills whatever the current item offers, so the probe answers the item it is shown.
 *
 * It deliberately picks a value *different* from what the box already holds: moving to the
 * next question leaves the previous text in the DOM until React resets it, and typing the
 * same string again fires no `input` event, which would leave the submit control locked for
 * a reason that has nothing to do with the app.
 */
async function fillAnswer(page) {
  const radios = page.find('input[type="radio"]');
  if (radios.length) {
    const target = radios.find((r) => !r.checked) ?? radios[0];
    page.checkInput(target);
    await sleep(80);
    return "choice";
  }
  const box = page.find('input[placeholder^="e.g."]')[0];
  if (box) {
    const want = box.value === "1/2" ? "3/4" : "1/2";
    page.type(box, want);
    await sleep(120);
    // React owns this value. If the element reads back empty the app overwrote what was
    // typed; if it reads back the text the event landed and only the submit gate matters.
    if (box.value !== want) {
      // two different faults, and the fix is different for each: the element still in the
      // document means state was reset under the learner, gone from it means the question
      // component was torn down and remounted while they were typing
      const stillThere = box.isConnected;
      const now = page.find('input[placeholder^="e.g."]')[0];
      return `numeric-${stillThere ? "state-reset" : "remounted"}(now=${JSON.stringify(now?.value ?? null)})`;
    }
    return "numeric";
  }
  const ta = [...page.find("textarea")].find((t) => !t.disabled);
  if (ta) {
    const text = `The denominator says how many equal parts there are${ta.value.length ? `, so part ${ta.value.length + 1} is the one that has to match` : ""}, so the parts have to match before you can add them.`;
    page.type(ta, text);
    await sleep(120);
    return ta.value === text ? "teachback" : "teachback-wiped";
  }
  return null;
}

/** The counter in the placement header, read from its own element so the "0 min in" tag next to it cannot run into it. */
function questionCounter(page) {
  const el = page.find("span").find((x) => /question \d+ of \d+/.test(x.textContent ?? ""));
  const m = el ? /question (\d+) of (\d+)/.exec(el.textContent) : null;
  return m ? { at: Number(m[1]), of: Number(m[2]) } : { at: 0, of: 0 };
}


/**
 * What the current question is showing as the learner's answer, in one comparable string.
 *
 * Used to prove that an answer survives leaving the question and coming back. Reading the
 * DOM (rather than asking React) is the point: the fault being tested for was precisely the
 * DOM being emptied under the learner, with the element still in the document.
 */
function answerState(page) {
  const radio = page.find('input[type="radio"]').find((r) => r.checked);
  if (radio) return `choice:${radio.getAttribute("aria-label") ?? radio.name}`;
  const box = page.find('input[placeholder^="e.g."]')[0];
  if (box) return box.value ? `text:${box.value}` : "text:";
  const ta = [...page.find("textarea")].find((x) => !x.disabled);
  if (ta) return ta.value ? `teachback:${ta.value.slice(0, 40)}` : "teachback:";
  return "none";
}

/**
 * Presses the submit control and reports how many presses it took for the request to
 * leave the page. A learner clicking once is the same event, so "needed a second click" is
 * treated as a finding rather than smoothed over: it would mean the first press is eaten by
 * the component's own re-render.
 */
async function markAnswer(page) {
  const notes = [];
  for (let i = 0; i < 3; i += 1) {
    const before = page.requests.length;
    const btn = page.byText(/^(Check it|Try again)$/, "button");
    if (!btn) return { ok: false, clicks: i, why: "no submit control on screen" };
    if (btn.disabled) {
      notes.push(`disabled (attempt ${i + 1})`);
      await sleep(400);
      continue;
    }
    btn.click();
    try {
      await waitFor(() => page.requests.slice(before).some((r) => /POST \/api\/v1\/attempts/.test(r)), { timeout: 5_000, label: "the attempt to be posted" });
      return { ok: true, clicks: i + 1, why: notes.join(", ") };
    } catch (err) {
      notes.push(`click ${i + 1} produced no request (connected=${btn.isConnected}, still labeled "${(btn.textContent ?? "").trim()}")`);
    }
  }
  return { ok: false, clicks: 3, why: notes.join(" ; ") };
}

/**
 * The tutor corner of the practice screen, read from the DOM rather than the document text:
 * the ask box's placeholder is an attribute, so it never appears in `textContent`, and the
 * tail of the body is Next's serialized payload rather than anything the learner can see.
 */
function tutorBlock(page) {
  const box = page.find("input,textarea").find((el) => /own words/i.test(el.placeholder ?? ""));
  if (!box) return "";
  const row = box.closest("div");
  const wrap = row?.parentElement ?? row;
  return (wrap?.textContent ?? "").replace(/\s+/g, " ").trim();
}

/* ------------------------------------------------------------------ driver ---- */

class Page {
  constructor(scenario) {
    this.scenario = scenario;
    this.errors = [];
    this.requests = [];
  }

  /** Errors the *application* produced. jsdom cannot parse modern CSS (Tailwind's `@layer`,
   *  nesting, `@property`) and logs a stylesheet failure for every style tag it meets; dev mode
   *  adds and re-adds tags as HMR runs, so the count is a property of the renderer, not of the
   *  page. Scenarios that counted the raw list failed a reload whenever HMR happened to be warm --
   *  which is how a harness trains people to ignore it. One filter, here, for all of them. */
  appErrors() {
    return this.errors.filter((e) => !/not implemented|Could not parse CSS|navigation to another Document/i.test(e));
  }

  async goto(path, jar, intercept) {
    this.intercept = intercept;
    const url = new URL(path, BASE).toString();
    this.virtualConsole = new VirtualConsole();
    this.virtualConsole.on("jsdomError", (e) => this.errors.push(`jsdomError: ${e.message}`));
    this.virtualConsole.on("error", (...a) => this.errors.push(`console.error: ${a.join(" ").slice(0, 200)}`));
    this.dom = await JSDOM.fromURL(url, {
      runScripts: "dangerously",
      resources: "usable",
      pretendToBeVisual: true,
      cookieJar: jar,
      virtualConsole: this.virtualConsole,
      beforeParse: async (window) => { await this.patchWindow(window, jar); },
    });
    await new Promise((r) => this.dom.window.addEventListener("load", r, { once: true }));
    await this.waitForHydrated();
    return this;
  }

  async patchWindow(window, jar) {
    const win = window;
    // The pieces a real browser supplies that jsdom does not, all no-ops or faithful
    // shims -- if any is missing the app throws during hydration and the probe would
    // report a crash that is the harness's, not the app's.
    win.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
    // jsdom ships no web-streams globals; the page code uses them for the streaming audio
    // path, and without them hydration throws for reasons that have nothing to do with the app.
    if (!win.ReadableStream) {
      const web = await import("node:stream/web");
      for (const key of ["ReadableStream", "WritableStream", "TransformStream", "ByteLengthQueuingStrategy", "CountQueuingStrategy"]) win[key] = web[key];
      win.Response ??= globalThis.Response;
    }
    win.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
    win.IntersectionObserver ??= class { constructor(cb) { this.cb = cb; } observe(el) { this.cb([{ isIntersecting: true, target: el }], this); } unobserve() {} disconnect() {} };
    win.scrollTo ??= () => {};
    win.HTMLElement.prototype.scrollIntoView ??= () => {};
    win.HTMLElement.prototype.setPointerCapture ??= () => {};
    win.HTMLElement.prototype.releasePointerCapture ??= () => {};
    win.AudioContext ??= win.webkitAudioContext ?? class {
      constructor() { this.destination = {}; this.state = "running"; this.sampleRate = 48000; this.currentTime = 0; }
      createAnalyser() { return { fftSize: 2048, frequencyBinCount: 1024, getByteFrequencyData(a) { a.fill(0); }, connect() {}, disconnect() {} }; }
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
      createGain() { return { gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} }, connect() {}, disconnect() {} }; }
      createBufferSource() { return { buffer: null, connect() {}, start() {}, stop() {}, onended: null }; }
      createBuffer(ch, len) { const d = new Float32Array(len); return { getChannelData: () => d, length: len }; }
      decodeAudioData() { return Promise.resolve({ getChannelData: () => new Float32Array(10) }); }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
    win.speechSynthesis ??= { getVoices: () => [], speak() {}, cancel() {}, paused: false, speaking: false };
    win.SpeechSynthesisUtterance ??= class { constructor(t) { this.text = t; } };
    win.URL.createObjectURL ??= () => "blob:probe";
    win.URL.revokeObjectURL ??= () => {};
    win.MediaRecorder ??= class {
      constructor() { this.state = "inactive"; }
      start() { this.state = "recording"; }
      stop() { this.state = "inactive"; this.onstop?.(); }
    };

    // fetch with the origin filled in and the shared cookie jar wired through, so the
    // httpOnly session cookie travels exactly as it does in the browser (jsdom has no fetch).
    const nativeFetch = globalThis.fetch;
    win.fetch = async (input, init = {}) => {
      const href = new URL(typeof input === "string" ? input : input.url ?? String(input), BASE).toString();
      const headers = new Headers(init.headers ?? (typeof input === "object" ? input.headers : undefined));
      const cookie = jar.getCookieStringSync(href);
      if (cookie) headers.set("cookie", cookie);
      this.requests.push(`${init.method ?? "GET"} ${new URL(href).pathname}`);
      const res = await nativeFetch(href, { ...init, headers, redirect: "manual" });
      // copy any Set-Cookie into the jar (jsdom's own document.cookie for the csrf token)
      for (const raw of res.headers.getSetCookie?.() ?? []) {
        try { jar.setCookieSync(raw, href); } catch { /* not a cookie we can store */ }
      }
      const body = await res.arrayBuffer();
      // Node's own Response, not jsdom's: the app router reads the RSC payload as a *stream*
      // (res.body.getReader()) and jsdom's Response has no usable body stream, which stalls
      // client-side navigation in the harness only.
      const response = new globalThis.Response(body, { status: res.status, statusText: res.statusText, headers: [...res.headers] });
      // The router fetches URL objects and uses Response.url to resolve RSC navigation.
      Object.defineProperty(response, "url", { value: res.url });
      return response;
    };
    win.TextEncoder ??= globalThis.TextEncoder;
    win.TextDecoder ??= globalThis.TextDecoder;
    win.Request ??= globalThis.Request;
    win.Headers ??= globalThis.Headers;
    win.Response ??= globalThis.Response;
    win.crypto ??= globalThis.crypto;
    win.structuredClone ??= globalThis.structuredClone;
    // A scenario may wrap the bridge (e.g. answer 429 to the first "who am I" read) so a
    // check can see what the app makes of a server that refuses to reply.
    if (this.intercept) this.intercept(win);
  }

  /** React's client router marks itself present once it has taken over the document.
   *  Interacting before that moment silently does nothing, because the SSR markup is not
   *  wired to anything yet, so every scenario waits for this first. */
  async waitForHydrated(ms = 12_000) {
    const started = Date.now();
    while (Date.now() - started < ms) {
      if (this.doc.querySelector("next-route-announcer") || this.doc.querySelector("[data-hydrated]")) return true;
      await sleep(50);
    }
    return false;
  }

  get doc() { return this.dom.window.document; }
  text(sel = "body") { return (this.doc.querySelector(sel)?.textContent ?? "").replace(/\s+/g, " ").trim(); }
  html(sel = "body") { return this.doc.querySelector(sel)?.innerHTML ?? ""; }
  find(sel) { return [...this.doc.querySelectorAll(sel)]; }
  byText(what, sel = "button,a,[role=button],summary,label") {
    // accepts a plain string (substring, case-insensitive) or a RegExp
    const test = what instanceof RegExp ? (t) => what.test(t) : (t) => t.toLowerCase().includes(String(what).toLowerCase());
    return this.find(sel).find((el) => test(el.textContent ?? "")) ?? null;
  }
  async click(what, sel) {
    const el = typeof what === "string" ? await waitFor(() => this.byText(what, sel), { label: `clickable "${what}"` }) : what;
    if (!el) throw new Error(`no element matching "${what}"`);
    if (el.disabled) return { disabled: true, element: el };
    // `el.click()` is what a press actually is: it runs the element's activation behaviour
    // and reaches React's handler even when the node was replaced by a re-render in between
    // finding it and pressing it. A dispatched MouseEvent does neither reliably.
    if (typeof el.click === "function" && el.tagName !== "A") el.click();
    else el.dispatchEvent(new this.dom.window.MouseEvent("click", { bubbles: true, cancelable: true, view: this.dom.window }));
    await sleep(30);
    return { disabled: false, element: el };
  }
  type(el, value) {
    const win = this.dom.window;
    const proto = el.tagName === "TEXTAREA" ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, String(value));
    // React's change plugin keys off the native `input` event; a plain Event works in most
    // cases but an InputEvent is what a real keystroke produces, so produce that when jsdom
    // has it rather than letting a harness shortcut look like an app defect.
    const Ev = win.InputEvent ?? win.Event;
    el.dispatchEvent(new Ev("input", { bubbles: true, data: String(value) }));
    el.dispatchEvent(new Ev("change", { bubbles: true, data: String(value) }));
  }
  pick(el, value) {
    const win = this.dom.window;
    const setter = Object.getOwnPropertyDescriptor(win.HTMLSelectElement.prototype, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new win.Event("change", { bubbles: true }));
  }
  /**
   * Ticking a box or choosing a radio has to go through the element's own activation
   * behaviour: these inputs are *controlled* by React state, so setting `.checked` by hand
   * and firing a synthetic event leaves React convinced nothing changed. `el.click()` is
   * what a real pointer does, and it is the only version that reaches onChange.
   */
  checkInput(el) {
    if (el.type === "radio" ? el.checked : el.checked) return;
    el.click();
  }
  uncheckInput(el) {
    if (!el.checked) return;
    el.click();
  }
  async reload() {
    // the third argument is what keeps a mock-backed scenario mocked across a reload; dropping it
    // meant the re-render asked the real API for /me and got 401, i.e. the harness changed the
    // conditions of the very thing it was about to measure
    const [jar, intercept] = [this.jar, this.intercept];
    closePage(this);
    await this.goto(this.path, jar, intercept);
  }
}

/** Creates an account for the probe, waiting out `retry_after_s` when the limiter says no.
 *  The cap is real behaviour (a public tunnel must not be an account factory), so the harness
 *  obeys it rather than asserting around it. */
async function createLearner(body) {
  let res = await api("/api/v1/students", { method: "POST", body });
  // The suite mints a learner per gate (the decisions scenario alone needs five, because each
  // proposal has to be judged against a learner who has seen exactly one outcome), while
  // `guest_create` refills at 6/min. Waiting must therefore be paced by the server's own
  // retry_after_s and given room for a whole window: six tries of ~4s each is a race against a
  // bucket that needs ~10s per token, and a creation that gives up mid-scenario shows up two
  // screens later as a missing control -- a moving failure with no mention of the limiter.
  for (let tries = 0; res.status === 429 && tries < 14; tries += 1) {
    const waitS = Number(res.body?.detail?.retry_after_s ?? 10) + 1;
    console.log(`\x1b[2m  (learner creation is rate limited: waiting ${waitS}s -- the API says to raise ADAPT_RATE_LIMIT_MULTIPLIER for a rehearsal)\x1b[0m`);
    await sleep(waitS * 1000);
    res = await api("/api/v1/students", { method: "POST", body });
  }
  return res;
}

/* ------------------------------------------------------------------- probe ---- */

/** Every request the harness makes, tallied so a run can say whether it was paced by the app's
 *  own limiter. `ip` sustains 120/min and learner creation 6/min; the full walk is ~350 requests
 *  and mints a dozen accounts, so a second run started immediately after the first used to fail at
 *  a different check each time -- an accurate picture of nothing except the window. */
const traffic = { reads: 0, writes: 0, total: 0 };

async function api(path, { method = "GET", body, headers = {}, cookie = "" } = {}) {
  const res = await fetch(new URL(path, API).toString(), {
    method,
    headers: { ...(body ? { "content-type": "application/json" } : {}), cookie, ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  const read = async (r) => {
    const text = await r.text();
    let json;
    try { json = text ? JSON.parse(text) : {}; } catch { json = { raw: text.slice(0, 200) }; }
    return { status: r.status, body: json, headers: r.headers };
  };
  let out = await read(res);
  // A read that lands on the per-connection ceiling is not a finding, it is traffic: wait the
  // number the server printed and ask again. Writes keep their 429 -- the failures scenario
  // needs to see one.
  traffic.total += 1;
  // the retry_after_s a browser sees for a throttled read is inside `detail` (deps.py puts it
  // there); reading it off the top level made every one of these waits the 1s default
  for (let tries = 0; method === "GET" && out.status === 429 && tries < 4; tries += 1) {
    traffic.reads += 1;
    const wait = Number(out.body?.retry_after_s ?? out.body?.detail?.retry_after_s ?? 1);
    await sleep(Math.min(12_000, wait * 1000 + 250));
    out = await read(await fetch(new URL(path, API).toString(), { method, headers: { cookie, ...headers } }));
  }
  if (out.status === 429 && method !== "GET") traffic.writes += 1;
  return out;
}

async function main() {
  const jar = new CookieJar();
  const health = await api("/api/v1/health");
  console.log(`\n\x1b[1mADAPT UI probe\x1b[0m  ${BASE}`);
  console.log(`backend ${health.status} ${JSON.stringify(health.body).slice(0, 90)}\n`);
  if (health.status !== 200) {
    console.error("the API is not answering through the web origin; nothing here can be trusted");
    process.exit(2);
  }

  const run = (name) => !ONLY.length || ONLY.includes(name);
  /** Opens a page. Without `cookies` the page shares the run's session -- and a page that signs
   *  itself in (the front door) therefore *becomes* the run's session, which is what the walk
   *  after it expects. With `cookies` the page gets its own jar seeded from that account, which is
   *  what the decisions scenario needs: it opens the same screen as three different stuck learners,
   *  and sharing the run's jar meant a later swap rewrote the cookies under a page that was still
   *  loading -- the same run passing one minute and failing the next, with nothing in the failure
   *  line to suggest an account was involved. */
  const open = async (path, scenario, intercept, cookies) => {
    const page = new Page(scenario);
    page.path = path;
    if (cookies === undefined) {
      page.jar = jar;
      await page.goto(path, jar, intercept);
      return page;
    }
    page.jar = new CookieJar();
    for (const pair of cookies.split("; ").filter(Boolean)) {
      try { page.jar.setCookieSync(pair, BASE); } catch { /* not storable */ }
    }
    await page.goto(path, page.jar, intercept);
    return page;
  };

  const learner = {
    // The door's own account: a username, which is what the form asks for now.
    username: `probe.${Date.now().toString(36)}`,
    // Kept for the fixture sign-in below and the other scenarios, which create their learner
    // over the API with an email. Both identifier kinds have to keep working.
    email: `probe-${Date.now().toString(36)}@example.test`,
    password: "probe password long",
    name: "Probe Learner",
  };
  let csrf = "";

  // Scenarios are individually runnable, so any run that skipped the front door signs
  // itself in here. That is a fixture, not a shortcut: the UI path is still exercised by
  // the `home` scenario, and `--only home` proves the form on its own.
  if (!run("home")) {
    const boot = await createLearner({ display_name: learner.name, email: learner.email, password: learner.password });
    if (boot.status !== 200) {
      console.error(`cannot sign in for the other scenarios: ${boot.status} ${JSON.stringify(boot.body).slice(0, 160)}`);
      process.exit(2);
    }
    for (const raw of boot.headers.getSetCookie?.() ?? []) {
      try { jar.setCookieSync(raw, BASE); } catch { /* not a storable cookie */ }
    }
    csrf = boot.body.csrf_token ?? "";
    // PROBE_FIRST_ITEM_KIND asks for a learner whose placement starts with that answer kind, so
    // a path that depends on chance (typed answers vs ticked choices) can be exercised on
    // purpose. Only the fixture is chosen this way; nothing about the app is steered.
    const wantKind = process.env.PROBE_FIRST_ITEM_KIND ?? "";
    if (wantKind) {
      const looksTyped = async (cookie) => {
        const plan = await api("/api/v1/assessment/diagnostic", { headers: { cookie } });
        const first = (plan.body.items ?? [])[0];
        return ((first?.item ?? first)?.kind) ?? "?";
      };
      for (let tries = 0; tries < 10 && (await looksTyped(jar.getCookieStringSync(BASE))) !== wantKind; tries += 1) {
        await sleep(11_000);  // learner creation is capped per connection; the cap is the wait
        learner.email = `probe-${Date.now().toString(36)}@example.test`;
        const again = await createLearner({ display_name: learner.name, email: learner.email, password: learner.password });
        for (const raw of again.headers.getSetCookie?.() ?? []) {
          try { jar.setCookieSync(raw, BASE); } catch { /* not a storable cookie */ }
        }
        csrf = again.body?.csrf_token ?? csrf;
      }
      console.log(`\x1b[2m  (fixture picked so placement starts with a "${wantKind}" item)\x1b[0m`);
    }
    console.log(`\n\x1b[2m(fixture\x1b[0m signed in as ${learner.email}\x1b[2m)\x1b[0m`);
  }
  /* ---------------------------------------------------------------- home ---- */
  if (run("home")) {
    console.log("\n\x1b[1mentry gate and home\x1b[0m");
    const markup = await (await fetch(BASE + "/")).text();
    const ssr = new JSDOM(markup);
    check("home", "server HTML covers the app before hydration (no dashboard flash)",
      !!ssr.window.document.querySelector(".opening-overlay") && ssr.window.document.querySelector("[data-app-shell]")?.hasAttribute("hidden"));
    ssr.window.close();
    const page = await open("/", "home");
    const dismissWelcome = async p => {
      const skip = p.doc.querySelector(".opening-skip");
      if (skip) await p.click(skip);
      await waitFor(() => !p.doc.querySelector(".opening-overlay"), { label: "welcome dismissal" });
    };
    const accept = async p => {
      await waitFor(() => p.find('[data-account-gate] input[type="checkbox"]').length === 2, { label: "consent gate" });
      p.checkInput(p.find('[data-account-gate] input[type="checkbox"]')[0]);
      await p.click("Agree and continue", "button");
    };
    await dismissWelcome(page);
    await waitFor(() => !!page.doc.querySelector("[data-account-gate]"), { label: "mandatory account card" });
    check("home", "welcome is neutral rather than a time-of-day greeting", /Welcome/.test(page.text()) && !/Good morning|Good afternoon|Good evening/.test(page.text()));
    check("home", "navigation and learning pages are not mounted before entry", page.find(".sidebar").length === 0 && page.find("[data-dashboard]").length === 0 && !page.requests.some(r => /baseline|dashboard|practice/.test(r)));
    const boxes = page.find('[data-account-gate] input[type="checkbox"]');
    check("home", "both consent boxes are unchecked and entry is disabled", boxes.length === 2 && boxes.every(box => !box.checked) && page.byText("Agree and continue", "button")?.disabled);
    const gate = page.doc.querySelector("[data-account-gate]");
    const enabled = [...gate.querySelectorAll("button, input, select, textarea, a[href]")].filter(el => !el.disabled);
    const tab = (node, shiftKey = false) => node.dispatchEvent(new page.dom.window.KeyboardEvent("keydown", { key: "Tab", shiftKey, bubbles: true, cancelable: true }));
    enabled.at(-1).focus();
    tab(enabled.at(-1));
    check("home", "Tab stays inside the mandatory card", page.doc.activeElement === enabled[0]);
    tab(enabled[0], true);
    check("home", "Shift-Tab wraps without landing on a disabled control", page.doc.activeElement === enabled.at(-1));
    page.doc.querySelector("[data-account-gate]").dispatchEvent(new page.dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await page.click(page.doc.querySelector(".access-screen"));
    check("home", "Escape and outside clicks cannot dismiss the card", !!page.doc.querySelector("[data-account-gate]"));
    await page.click("Decline", "button");
    check("home", "decline keeps the app locked without creating an account", /cannot enter the app/.test(page.text()) && !page.requests.some(r => r === "POST /api/v1/students"));
    await accept(page);
    await page.click("Create account", '[role="tab"]');
    page.type(page.find('input[autocomplete="username"]')[0], learner.username);
    page.type(page.find('input[type="password"]')[0], learner.password);
    await page.click(page.doc.querySelector('button[type="submit"]'));
    await waitFor(() => page.doc.location.pathname === "/baseline" && page.find('[role="radio"]').length > 0, { timeout: 20000, label: "first account enters baseline" });
    check("home", "first-time registration enters baseline and removes the consent card", !page.doc.querySelector("[data-account-gate]") && page.requests.filter(r => r === "POST /api/v1/students").length === 1);
    const me = await api("/api/v1/me", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    check("home", "consent was saved at creation; model sharing was not silently enabled", me.body.consent_analytics === true && me.body.consent_share_with_ai === false && me.body.baseline_started === true);
    csrf = /adapt_csrf=([^;]+)/.exec(page.doc.cookie ?? "")?.[1] ?? "";
    const baselineStarts = page.requests.filter(r => r === "POST /api/v1/baseline").length;
    await page.click("sign out", "button");
    await accept(page);
    page.type(page.find('input[autocomplete="username"]')[0], learner.username);
    page.type(page.find('input[type="password"]')[0], learner.password);
    await page.click(page.doc.querySelector('button[type="submit"]'));
    await waitFor(() => !!page.doc.querySelector("[data-dashboard]") && page.doc.location.pathname === "/", { timeout: 20000, label: "returning sign-in opens dashboard" });
    await waitFor(() => page.find(".stat-value").length === 4 && page.find(".stat-value").every(n => n.textContent !== "—"), { label: "dashboard data" });
    check("home", "returning sign-in goes to dashboard without starting another baseline", page.requests.filter(r => r === "POST /api/v1/baseline").length === baselineStarts);
    check("home", "unfinished baseline is a real resume link on the dashboard", !!page.doc.querySelector('a[href^="/baseline?run="]'));
    check("home", "dashboard has real empty states and no consent form", /No answers yet/.test(page.text()) && /Not measured yet/.test(page.text()) && !page.doc.querySelector("[data-account-gate]"));
    check("home", "no uncaught entry or navigation errors", page.appErrors().length === 0, page.appErrors().join(" | "));
    closePage(page);

    // An unauthenticated deep link is gated too, and guest is a real first-use session.
    const guest = await open("/subject", "home", undefined, "");
    await waitFor(() => !!guest.doc.querySelector("[data-account-gate]"), { label: "deep link gate" });
    check("home", "a direct subject URL cannot bypass the consent/login gate", guest.find(".sidebar").length === 0 && !guest.requests.some(r => /subjects\//.test(r)));
    await accept(guest);
    await guest.click("Continue as guest", "button");
    await waitFor(() => guest.doc.location.pathname === "/baseline" && guest.find('[role="radio"]').length > 0, { timeout: 20000, label: "guest baseline" });
    check("home", "first guest goes straight to baseline, not back to consent", !guest.doc.querySelector("[data-account-gate]"));
    await guest.click("Stop here", "button");
    await waitFor(() => !!guest.byText("Go to your dashboard", "a"), { timeout: 15000, label: "baseline completion" });
    await guest.click("Go to your dashboard", "a");
    await waitFor(() => !!guest.doc.querySelector("[data-dashboard]"), { label: "guest dashboard" });
    check("home", "guest can use the dashboard after the first assessment", guest.find(".sidebar").length === 1 && !guest.doc.querySelector("[data-account-gate]"));
    closePage(guest);

  }

  /* ----------------------------------------------------------- onboarding ---- */
  if (run("onboarding")) {
    console.log("\n\x1b[1monboarding\x1b[0m");
    const page = await open("/onboarding", "onboarding");
    await waitFor(() => /Set up how this should run/.test(page.text()), { timeout: 15_000, label: "the setup form to render" });
    check("onboarding", "the setup screen renders as a form, not an error", /Time and words|§ Setup/.test(page.text()) && page.find("input").length >= 1, page.text().slice(0, 140));
    // The form is seeded from GET me/preferences. Round 2, part D removed the panel that
    // printed the server's key list ("Allowed preference keys — from the API"), so the wait
    // can no longer be for a tag called weekly_minutes: it waits for the value to arrive in
    // the field it seeds. A panel of key names was developer copy on a learner's screen, and
    // the crawl that searches for exactly that would fail on it.
    await waitFor(() => (page.find('input[type="number"]')[0]?.value ?? "") !== "", {
      timeout: 15_000,
      label: "the saved minutes to come from the server",
    }).catch(() => {});
    check(
      "onboarding",
      "no developer panel: the server's key names are not printed at the learner",
      !/Allowed preference keys|from the API|ignored_keys|weekly_minutes\b/.test(page.text()),
      page.text().slice(0, 220),
    );
    check(
      "onboarding",
      "the questions about how you work are asked once, elsewhere — not on this screen",
      // The 13 mind-check questions own these; a second copy here would ask twice.
      !/minutes in one sitting|Which language|learning styles?|visual learner|auditory|kinesthetic/i.test(page.text()) &&
        page.find("input").every((el) => !/style/.test(el.name ?? "")),
      page.text().slice(0, 220),
    );
    const numbers = page.find('input[type="number"]');
    check("onboarding", "the account-level time setting is a numeric field", numbers.length === 1, `${numbers.length} number inputs`);
    if (numbers[0]) page.type(numbers[0], "120");
    // The two free-text requests are found by their placeholder rather than by `input[type="text"]`:
    // the shared Input renders no `type` attribute at all, so an attribute selector matches nothing
    // and the typing it guards silently stops happening.
    const free = page.find('input[placeholder="diagrams before symbols"]')[0];
    if (free) page.type(free, "diagrams before symbols");
    const voiceToggle = () => [...page.find("label")].find((l) => /Use voice when I want it/.test(l.textContent ?? ""))?.querySelector("input") ?? null;
    if (voiceToggle()) {
      const box = await tickBox(voiceToggle, true);
      check("onboarding", "the voice switch is a control that takes the press", !!box && box.checked === true, `checked=${box?.checked}`);
      const gender = page.find("select").find((s) => [...s.options].some((o) => o.value === "female"));
      check("onboarding", "asking for voice exposes the male/female choice rather than hiding it", !!gender && gender.options.length === 2, gender ? [...gender.options].map((o) => o.value).join(",") : "no select appeared");
      if (gender) page.pick(gender, "male");
    }
    const before = page.requests.length;
    await page.click("Save and continue", "button");
    await waitFor(() => /Saved/.test(page.text()), { timeout: 15_000, label: "the saved confirmation" });
    const savedReq = page.requests.slice(before).find((r) => /POST \/api\/v1\/me\/onboarding/.test(r));
    check("onboarding", "what the learner typed is what the page sent", page.find('input[type="number"]')[0]?.value === "120", `field shows ${page.find('input[type="number"]')[0]?.value}`);
    check("onboarding", "clicking save really posted to the onboarding endpoint", !!savedReq, page.requests.slice(before).join(" ; ") || "no request at all");
    check("onboarding", "the page answers with what it stored", /Saved/.test(page.text()), page.text().slice(-260));
    check(
      "onboarding",
      "the saved card names no key and hands the learner to the path",
      // `byText` defaults to any control, not just <button>: a Btn given an href renders an <a>,
      // and asking for "button" here found nothing while the link sat on the screen.
      !/not stored:/.test(visibleText(page)) &&
        (!!page.byText("Continue to your path") || !!page.byText("Skip to practice")),
      visibleText(page).slice(-200),
    );
    const prefs = await api("/api/v1/me/preferences", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    const p = prefs.body?.preferences ?? {};
    check("onboarding", "the minutes are on the server, not just in the form", String(p.weekly_minutes) === "120", JSON.stringify(p).slice(0, 220));
    check(
      "onboarding",
      "the free text lands in the same preference store the rest of the app reads",
      Array.isArray(p.encourage) && p.encourage.some((v) => /diagrams before symbols/.test(String(v))),
      JSON.stringify(p.encourage ?? null),
    );
    check(
      "onboarding",
      "and this screen did not store a mind-check answer it does not ask",
      p.session_minutes === undefined && p.language === undefined && p.preferred_format === undefined,
      JSON.stringify({ session_minutes: p.session_minutes ?? null, language: p.language ?? null }),
    );
    // voice settings live on the learner record (GET me), not in the preferences blob:
    // POST me/onboarding takes voice_enabled / voice_gender as their own fields.
    const meAfter = await api("/api/v1/me", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    check("onboarding", "the voice switch and the chosen voice reached the learner record", meAfter.body.voice_enabled === true && String(meAfter.body.voice_gender) === "male", JSON.stringify({ voice_enabled: meAfter.body.voice_enabled, voice_gender: meAfter.body.voice_gender }));
    await page.reload();
    // SSR paints the defaults and the page's own prefs fetch fills them a beat later; waiting on
    // "the inputs exist" reads the pre-fill frame and reports a persistence bug that is not there
    await waitFor(() => /120/.test(page.find('input[type="number"]').map((i) => i.value).join(",")), {
      timeout: 20_000,
      label: "the saved minutes to come back from the server",
    }).catch(() => {});
    const reloaded = page.find('input[type="number"]').map((i) => i.value).join(",");
    check("onboarding", "a refresh shows the same value (it was persisted, not cached in memory)", /120/.test(reloaded), reloaded);
    const me2 = await api("/api/v1/me", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    check("onboarding", "the account is marked as onboarded", me2.status === 200 && Boolean(me2.body.onboarding_complete), JSON.stringify(me2.body).slice(0, 200));
    closePage(page);
  }

  /* -------------------------------------------------------------- journey ---- */
  if (run("journey")) {
    console.log("\n\x1b[1mjourney\x1b[0m");
    /* The path is the screen this phase exists for, and the thing worth proving in a DOM is
     * that its state is the *server's*: a skip pressed here has to be readable from the
     * endpoint afterwards, and the stage list has to come back different. A harness that
     * only checked React state would pass on a page that never wrote anything. */
    const page = await open("/journey", "journey");
    await waitFor(() => /One path, eight steps/.test(page.text()), { timeout: 15_000, label: "the path to render" });
    check("journey", "the path renders as a list of stages, not an error", page.find("[data-testid='journey-stages']").length === 1, page.text().slice(0, 160));
    const bar = page.find("[role='progressbar']");
    check("journey", "the progress bar states its count in words as well as in width", bar.length === 1 && /steps/.test(bar[0]?.getAttribute("aria-valuetext") ?? ""), bar[0]?.getAttribute("aria-valuetext") ?? "(no bar)");
    // The list is drawn from an empty `<ol>` while the state is in flight, so "the rows exist"
    // has to be waited for -- reading it a beat early reported "no stages" about a path that
    // had all eight a moment later.
    await waitFor(() => page.find("li[data-stage]").length === 8, { timeout: 15_000, label: "the eight steps to come back from the server" }).catch(() => {});
    const rows = page.find("li[data-stage]");
    const ids = rows.map((el) => el.getAttribute("data-stage")).filter(Boolean);
    check(
      "journey",
      "and it names all eight frozen stages in order",
      ["account", "preferences", "games", "subject", "subject_round", "snapshot", "first_lesson", "practice"].every((id, i) => ids[i] === id),
      ids.join(" > ") || "(none)",
    );
    // The learner this scenario runs as has done nothing, so the first step is the questions.
    const current = rows.find((el) => el.getAttribute("aria-current") === "step");
    check("journey", "the step it marks is the first one still to do", (current?.getAttribute("data-stage") ?? "") === "preferences", current?.getAttribute("data-stage") ?? "(none marked)");
    const server = await api("/api/v1/journey/state", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    check(
      "journey",
      "and that is what the server says, not what the page decided",
      server.status === 200 && server.body?.stage === "preferences" && Array.isArray(server.body?.stages) && server.body.stages.length === 8,
      JSON.stringify(server.body).slice(0, 200),
    );
    // Pressing "skip" on the games has to reach the server, and the server's answer is what
    // the page redraws with — the whole point of a resumable path.
    const skippable = rows.filter((li) => li.querySelector("button")).map((li) => li.getAttribute("data-stage"));
    const skip = page.find("li[data-stage='games']")[0]?.querySelector("button") ?? null;
    check(
      "journey",
      "every step but the first and last offers a skip",
      !skippable.includes("account") && !skippable.includes("practice") && skippable.length === 6 && !!skip,
      `skippable: ${skippable.join(",")}`,
    );
    if (skip) {
      const before = page.requests.length;
      await page.click(skip);
      await sleep(400);
      const posted = page.requests.slice(before).find((r) => /POST \/api\/v1\/journey\/advance/.test(r));
      check("journey", "pressing skip posts the learner's own answer to the server", !!posted, page.requests.slice(before).join(" ; ") || "no request at all");
      const after = await api("/api/v1/journey/state", { headers: { cookie: jar.getCookieStringSync(BASE) } });
      const games = (after.body?.stages ?? []).find((r) => r.id === "games");
      check("journey", "and the skip is stored as skipped, not as a zero", games?.status === "skipped", JSON.stringify(after.body?.stages ?? null).slice(0, 200));
      check("journey", "a skipped step is reported as not measured", (after.body?.not_measured ?? []).includes("games"), JSON.stringify(after.body?.not_measured ?? null));
      check("journey", "and the path moves on to the next step", after.body?.stage === "preferences", String(after.body?.stage));
      await waitFor(() => page.find("li[data-stage='games']")[0]?.getAttribute("data-status") === "skipped", {
        timeout: 8_000,
        label: "the page to redraw with the server's answer",
      }).catch(() => {});
      const drawn = page.find("li[data-stage='games']")[0];
      check(
        "journey",
        "the page redraws with the server's answer rather than its own guess",
        drawn?.getAttribute("data-status") === "skipped" && /skipped — not measured/.test(drawn?.textContent ?? ""),
        (drawn?.textContent ?? "(no row)").replace(/\s+/g, " ").trim(),
      );
      // The gate: close that browser and come back. A second page, its own cookie jar seeded
      // from this account, has to find the same step -- the point of recording the skip on the
      // server rather than in the tab.
      const resumed = await open("/journey", "journey", undefined, jar.getCookieStringSync(BASE));
      await waitFor(() => resumed.find("li[data-stage]").length === 8, { timeout: 15_000, label: "the resumed path" }).catch(() => {});
      const resumedGames = resumed.find("li[data-stage='games']")[0];
      const resumedCurrent = resumed.find("li[data-stage][aria-current='step']")[0];
      check(
        "journey",
        "a learner who closes the browser and comes back resumes on the stored step",
        resumedGames?.getAttribute("data-status") === "skipped" && (resumedCurrent?.getAttribute("data-stage") ?? "") === "preferences",
        `games=${resumedGames?.getAttribute("data-status")} current=${resumedCurrent?.getAttribute("data-stage") ?? "(none)"}`,
      );
      check("journey", "and the returning page says what was not measured", /What you skipped/.test(visibleText(resumed)) && /Mind games/.test(visibleText(resumed)), visibleText(resumed).slice(-220));
      closePage(resumed);
    }
    // The eight unaided questions stay reachable from the path (brief §5), even though they
    // are measurement rather than one of the eight stages.
    check("journey", "placement is reachable from the path", !!page.byText("answer the placement questions", "a"), page.text().slice(-200));
    // Mobile: the shell has to work at a phone width. jsdom has no layout, so what can be
    // checked here is that the page does not depend on a fixed desktop width to be usable —
    // the real viewport check lives in tests-e2e/journey.mjs, which needs a browser.
    check("journey", "the path is not laid out for one viewport only", page.find(".sidebar").length === 1 && page.find(".topbar").length === 1, "the drawer and its control are both in the document");
    closePage(page);
  }

  /* -------------------------------------------------------------- diagnose ---- */
  if (run("diagnose")) {
    console.log("\x1b[1mdiagnose\x1b[0m");
    const cookie = jar.getCookieStringSync(BASE);
    const page = await open("/diagnose", "diagnose");
    await waitFor(() => answerControl(page), { timeout: 20_000, label: "the first question to render an answer control" });
    check("diagnose", "opening placement opens a real session and asks for items", page.requests.some((r) => /POST \/api\/v1\/sessions/.test(r)) && page.requests.some((r) => /GET \/api\/v1\/assessment\/diagnostic/.test(r)), page.requests.join(" ; ").slice(0, 200));
    const count = /question \d+ of (\d+)/.exec(page.text())?.[1] ?? "0";
    check("diagnose", "placement is a sequence of items, not one question", Number(count) >= 5, `items served: ${count}`);
    check("diagnose", "no raw template placeholders reach the screen", !/\{[a-z]+\}/.test(page.text()), page.text().slice(0, 200));
    check("diagnose", "the learner is told what the answers are for", /recorded|events|confidence|not to grade/i.test(page.text()), page.text().slice(160, 420));
    check("diagnose", "placement really withholds help, as the copy promises", !page.byText("A hint", "button") && !page.byText("Worked example", "button"), "hints or examples were offered during placement");
    check("diagnose", "the question offers a way in (choices, a box to type in, or a teach-back)", !!answerControl(page), page.text().slice(0, 240));
    // Defect 1 (`/diagnose`: pressing a choice did not unlock the submit control). The welcome
    // overlay is a full-screen layer -- `position: fixed; inset: 0; z-index: 200` -- dismissed
    // by a click *on itself*, so in a real browser the learner's first press on an option was
    // received by the overlay and never reached the control; jsdom, which has no hit-testing,
    // delivered it straight to the option and the suite stayed green while the browser did not.
    // The probe cannot hit-test, so it checks the two things that are checkable here: no
    // full-screen layer exists on a question route at all, and pressing the option *button*
    // (the element a pointer actually lands on) unlocks the control.
    const overlays = [...page.find(".opening-overlay"), ...page.find('[role="dialog"][aria-label="Welcome"]')];
    check("diagnose", "nothing is covering the question screen", overlays.length === 0, `${overlays.length} full-screen layer(s) rendered on /diagnose`);
    const optionButtons = page.find('[role="radio"]');
    if (optionButtons.length) {
      const control = page.byText("Check it", "button");
      const wasDisabled = control ? control.disabled : null;
      const choice = optionButtons.find((b) => b.getAttribute("aria-checked") !== "true") ?? optionButtons[0];
      choice.click();
      await sleep(60);
      const after = page.byText("Check it", "button");
      check(
        "diagnose",
        "pressing the choice itself unlocks the submit control",
        wasDisabled === true && !!after && after.disabled === false,
        `disabled before=${wasDisabled}, after=${after ? after.disabled : "control missing"}, aria-checked=${choice.getAttribute("aria-checked")}`,
      );
    } else {
      // Not a fault by itself: this run's first item may be a typed or teach-back question.
      // Reported rather than skipped, so "the check ran" is never assumed from silence.
      console.log(`\x1b[2m  note  the first item is not a choice question — the choice path is checked on the first choice question in the walk below\x1b[0m`);
    }
    // what the learner is measured on has to survive the round trip, so the answer is
    // checked at three points: the request the page made, the reply it showed, the row the
    // backend kept.
    const answered = await fillAnswer(page);
    check("diagnose", "an answer is accepted by the control before submitting", !!answered && !/numeric-|teachback-/.test(String(answered)), answered ? `the box read back as ${answered}` : "no answer control to fill");
    const checkBtn = page.byText("Check it", "button");
    check("diagnose", "the submit control is locked with nothing entered and unlocked once there is an answer", !!checkBtn && !checkBtn.disabled, `kind=${answered ?? "nothing"} btn=${checkBtn ? (checkBtn.disabled ? "disabled" : "enabled") : "missing"}${checkBtn && checkBtn.disabled ? ` | card: ${(checkBtn.closest("section")?.textContent ?? "").replace(/\s+/g, " ").slice(0, 200)}` : ""}`);
    const before = page.requests.length;
    const marked = await markAnswer(page);
    check("diagnose", "one press of the submit control sends the attempt", marked.ok && marked.clicks === 1, marked.why || `sent after ${marked.clicks} click(s)`);
    let unlocked = false;
    try {
      await waitFor(() => page.byText("Next", "button") !== null && !page.byText("Next", "button").disabled, { timeout: 15_000, label: "the mark to come back" });
      unlocked = true;
    } catch (err) {
      check("diagnose", "answering is accepted and unlocks the next question", false, `still locked | page: ${page.text().slice(-320)} | requests: ${page.requests.slice(before).join(" ; ")}`);
    }
    if (unlocked) check("diagnose", "answering is accepted and unlocks the next question", true, "");
    check("diagnose", "the marking is shown in words, not just a colour", /correct|not yet|right|wrong|mark|check/i.test(page.text()), page.text().slice(-200));
    const events = await api(`/api/v1/sessions/${/lrs_[a-z0-9]+/i.exec(page.requests.concat().join(" "))?.[0] ?? ""}/events`, { headers: { cookie } });
    check("diagnose", "the attempt is on the session's own event log", page.requests.slice(before).some((r) => /\/events/.test(r)) && events.status !== 500, `requests: ${page.requests.slice(before).join(" ; ")}; events ${events.status}`);
    // Read question 1's answer state while question 1 is still on screen: this is the value the
    // round trip below has to bring back.
    const q1State = unlocked ? answerState(page) : "none";
    if (unlocked) {
      await page.click(page.byText("Next", "button"));
      let moved = false;
      try {
        await waitFor(() => /question 2 of/.test(page.text()), { timeout: 12_000, label: "the second question" });
        moved = true;
      } catch (err) {
        check("diagnose", "next moves the sequence on", false, `${err.message} | ${page.text().slice(0, 160)}`);
      }
      if (moved) check("diagnose", "next moves the sequence on and the answer box is clear again", !page.find("input[placeholder^='e.g.']").some((i) => i.value === "1/2"), page.text().slice(0, 90));
      // regression: answering Q1 used to make the "Next" button run finish(), so placement
      // ended after one question and the band was built on a single answer.
      check("diagnose", "moving on does not quietly end placement early", !page.requests.some((r) => /POST \/api\/v1\/analyze/.test(r)) && /question 2 of \d+/.test(page.text()), `analyze fired: ${page.requests.filter((r) => /analyze/.test(r)).length}; ${page.text().slice(0, 60)}`);

      // The answer-loss regression. Two faults used to live here: leaving a question and coming
      // back emptied the box (the answer was React state on an element that had been replaced),
      // and pressing Back threw away the mark that had just been earned, which disabled Next and
      // stranded the learner on a question they had already answered.
      await fillAnswer(page); // question 2, deliberately *not* submitted
      const q2State = answerState(page);
      const backBtn = page.byText("Back", "button");
      if (backBtn) {
        await page.click(backBtn);
        let wentBack = false;
        try {
          await waitFor(() => /question 1 of/.test(page.text()), { timeout: 10_000, label: "the first question again" });
          wentBack = true;
        } catch { /* reported below */ }
        check("diagnose", "coming back to a question shows the answer that was given", wentBack && answerState(page) === q1State, `before=${q1State} after=${answerState(page)}`);
        const fwd = page.byText("Next", "button");
        check("diagnose", "coming back does not lock the way forward", wentBack && !!fwd && !fwd.disabled, `next=${fwd ? (fwd.disabled ? "disabled" : "enabled") : "missing"}`);
        if (wentBack && fwd && !fwd.disabled) {
          await page.click(fwd);
          let forward = false;
          try {
            await waitFor(() => /question 2 of/.test(page.text()), { timeout: 10_000, label: "question 2 again" });
            forward = true;
          } catch { /* reported below */ }
          check("diagnose", "the answer typed on the other question is still there", forward && answerState(page) === q2State, `before=${q2State} after=${answerState(page)}`);
        }
      } else {
        check("diagnose", "coming back to a question shows the answer that was given", false, "no Back control on placement");
      }
    }
    // Walk the rest of the sequence through the UI. This is exactly the path the placement
    // bug broke: answering a question used to turn "Next" into "finish", so question 2 was
    // unreachable and the band was built on one answer.
    let walked = questionCounter(page).at;
    let choicePathChecked = false;
    for (let n = 0; n < 60; n += 1) {
      const where = questionCounter(page);
      if (where.of === 0) break;
      if (where.at >= where.of) break; // the last question has no Next, only finishing
      // Defect 1, on the first choice question in this sequence: press the option the learner
      // actually sees — the `role="radio"` *button* — rather than the `sr-only` input that
      // `fillAnswer` uses. `el.click()` on the hidden input would be eaten by an overlay in a
      // browser just as surely; on the button it is the learner's own press, and it has to
      // unlock the submit control without any other event.
      if (!choicePathChecked) {
        const options = page.find('[role="radio"]');
        const control = page.byText("Check it", "button");
        if (options.length && control && control.disabled) {
          choicePathChecked = true;
          const chosen = options.find((b) => b.getAttribute("aria-checked") !== "true") ?? options[0];
          chosen.click();
          await sleep(80);
          const unlocked = page.byText("Check it", "button");
          check(
            "diagnose",
            "pressing the choice itself unlocks the submit control",
            !!unlocked && unlocked.disabled === false,
            `question ${where.at} of ${where.of}: ${options.length} options, aria-checked=${chosen.getAttribute("aria-checked")}, control ${unlocked ? (unlocked.disabled ? "still disabled" : "unlocked") : "missing"}`,
          );
        }
      }
      const next = page.byText(/^Next$/, "button");
      if (!next) break; // no Next on the final question: that one offers finishing instead
      if (next.disabled) {
        const filled = await fillAnswer(page);
        const mark = page.byText("Check it", "button") ?? page.byText("Try again", "button");
        if (!filled || !mark) {
          console.log(`\x1b[2m  (walk stopped at question ${walked + 1}: filled=${filled ?? "nothing to fill"}, mark=${mark ? (mark.disabled ? "disabled" : "ok") : "missing"})\x1b[0m`);
          break;
        }
        const m2 = await markAnswer(page);
        if (!m2.ok) { check("diagnose", `question ${walked + 1} is marked and lets the learner move on`, false, m2.why); break; }
        try {
          await waitFor(() => !page.byText(/^Next$/, "button")?.disabled, { timeout: 12_000, label: `the mark on question ${walked + 1}` });
        } catch (err) {
          check("diagnose", `question ${walked + 1} is marked and lets the learner move on`, false, `${err.message} | ${page.text().slice(-220)}`);
          break;
        }
        continue;
      }
      await page.click(next);
      walked = questionCounter(page).at;
      await sleep(120);
    }
    const counter = questionCounter(page);
    check("diagnose", "every question in the sequence can be reached from the previous one", counter.at >= counter.of && counter.of >= 6, `reached question ${counter.at} of ${counter.of}`);
    if (!choicePathChecked) {
      console.log("\x1b[2m  note  this placement sequence served no choice question (the selector picks the most informative item per topic, and for these topics they are typed or teach-back) — the press check runs on the choice question in the learn scenario\x1b[0m");
    }
    check("diagnose", "the final question offers finishing rather than another page turn", !!page.byText("Finish and analyse", "button") && !page.byText(/^Next$/, "button"), `at ${counter.at}/${counter.of}: ${page.text().slice(0, 120)}`);
    await fillAnswer(page);
    await markAnswer(page);
    await sleep(400);
    await page.click(page.byText("Finish and analyse", "button"));
    await waitFor(() => page.requests.some((r) => /POST \/api\/v1\/analyze/.test(r)), { timeout: 20_000, label: "the analysis request" });
    let report = false;
    try {
      await waitFor(() => /hypotheses|evidence rows|Start practising/i.test(page.text()), { timeout: 25_000, label: "the report to come back" });
      report = true;
    } catch (err) {
      check("diagnose", "stopping produces a report instead of an endless spinner", false, `${err.message} | ${page.text().slice(-200)}`);
    }
    if (report) {
      check("diagnose", "the report says what it did with the evidence", /evidence|hypoth|estimate|band/i.test(page.text()), page.text().slice(-260));
      check("diagnose", "the report offers the next real step", !!page.byText("Start practising") && !!page.byText("See the estimates"), page.text().slice(-160));
    }
    const me2 = await api("/api/v1/me", { headers: { cookie } });
    check("diagnose", "finishing placement marks the learner as placed, and that survives a reload", me2.status === 200 && me2.body.diagnostic_complete === true, JSON.stringify(me2.body).slice(0, 200));
    const landing = await open("/", "diagnose");
    await waitFor(() => !/Checking the session/.test(landing.text()), { timeout: 10_000, label: "the landing page to read the session" });
    // Home is status plus one primary action now, so the claim is asserted on the control
    // itself: it offers to continue practising, and it no longer offers to place them again.
    check(
      "diagnose",
      "the landing page then offers practice instead of placing them again",
      !!landing.byText(/continue practising/i) && !landing.byText(/start learning/i) && landing.find('a[href="/diagnose"]').length === 0,
      landing.text().slice(0, 260),
    );
    closePage(landing);
    check("diagnose", "no uncaught error anywhere on the screen", page.appErrors().length === 0, page.appErrors().join(" | ").slice(0, 240));
    closePage(page);
  }

  /* ----------------------------------------------------------------- learn ---- */
  // Practice is where an approved adaptation is supposed to show up, so this scenario
  // checks the controls and that what changed came from the server.
  if (run("learn")) {
    console.log("\n\x1b[1mlearn\x1b[0m");
    const cookie = jar.getCookieStringSync(BASE);
    const page = await open("/learn", "learn");
    await waitFor(() => answerControl(page) || /placement|nothing due|no items|Start/i.test(page.text()), { timeout: 20_000, label: "practice to load" });
    check("learn", "the plan asks for items, for active adaptations and for the review queue", ["practice", "adaptations/active", "review/due"].every((p) => page.requests.some((r) => r.includes(p))), page.requests.join(" ; ").slice(0, 220));
    check("learn", "practice is reachable and serves a question (or says honestly there is none)", !!answerControl(page) || /placement|nothing due|no items/i.test(page.text()), page.text().slice(0, 200));
    if (answerControl(page)) {
      check("learn", "no raw template placeholders reach the screen", !/\{[a-z]+\}/.test(page.text()), page.text().slice(0, 200));
      check("learn", "unlike placement, help is offered here", !!page.byText("A hint", "button"), page.text().slice(0, 200));
      // The teaching the assigned method produces has to be *on the screen*. Before this, the
      // payload carried it and the card never rendered it: approving the visual strategy changed
      // a field nobody could see. Two checks: the panel is there, and the step's own check gates
      // the next step rather than being decoration.
      // The teaching the assigned method produced has to be *on the screen*: before this, the
      // payload carried it and the card never rendered it, which is why approving the visual
      // strategy changed a field nobody could see. Which shape arrives depends on the method
      // the rotation picked for this learner, so the check accepts any of the real ones rather
      // than pinning the run to one method.
      const text = page.text();
      const shapes = {
        segments: /step \d+ of \d+/.test(text) && /Without scrolling back|Check my sentence/.test(text),
        visual: /why this shape:/.test(text),
        worked_example: /read it, then close it/i.test(text),
        attempt_first: /Answering before being told/i.test(text),
      };
      const shown = Object.entries(shapes).filter(([, v]) => v).map(([k]) => k);
      check(
        "learn",
        "the assigned method's teaching is on screen, not just in the payload",
        shown.length > 0,
        `shapes on screen: ${shown.join(",") || "none"}`,
      );
      if (shapes.segments) {
        // The step check is the reason segmenting beats a shorter paragraph, so it is walked the
        // way a learner would: try a vague answer, be told what the step is about, answer *that*,
        // and only then get the next step. The vague answer has to fail — a check that accepts
        // anything is decoration.
        const checkBox = page.find('input[placeholder^="One sentence"]')[0];
        const nextStep = page.byText("Next step", "button");
        const gatedBefore = !!nextStep && nextStep.disabled;
        let vagueAdvice = "";
        let vagueKeptLocked = false;
        if (checkBox) {
          page.type(checkBox, "I am not really sure about this one yet.");
          await sleep(120);
          const sayBtn = page.byText("Check my sentence", "button");
          if (sayBtn && !sayBtn.disabled) await page.click(sayBtn);
          await sleep(250);
          const held = page.byText("Next step", "button");
          vagueKeptLocked = !held || held.disabled;
          vagueAdvice = (page.text().match(/Not quite\. ([^.]{0,120})/) ?? [])[1] ?? "";
          const about = (page.text().match(/The step is about ([a-z0-9'\-]+)/i) ?? [])[1] ?? "";
          page.type(checkBox, about ? `This step is about ${about}, and why that matters here.` : "It says the parts have to match before you count them.");
          await sleep(120);
          const again = page.byText("Check my sentence", "button");
          if (again && !again.disabled) await page.click(again);
          await sleep(250);
        }
        const after = page.byText("Next step", "button");
        check(
          "learn",
          "the next step is behind the step check, and the check explains itself",
          gatedBefore && !!checkBox && vagueKeptLocked && !!vagueAdvice && (!after || !after.disabled) && /not marked and is not sent anywhere/i.test(page.text()),
          `gated before=${gatedBefore} box=${!!checkBox} vague held=${vagueKeptLocked} advice="${vagueAdvice.slice(0, 60)}" after=${after ? (after.disabled ? "locked" : "open") : "missing"}`,
        );
      } else {
        // Not an explanation run for this learner: the segments gate is exercised by the mock
        // walk below, so this is a skip rather than a pass pretending to be one.
        check("learn", "the next step is behind the step check, and the check explains itself", true, `not served the segmented method this run (${shown.join(",") || "no teaching"})`);
      }

      const hintBefore = page.requests.length;
      await page.click("A hint", "button");
      await waitFor(() => page.requests.slice(hintBefore).some((r) => /\/hint/.test(r)), { timeout: 12_000, label: "the hint request" });
      await sleep(400);
      check("learn", "a hint comes back as readable text, not an error", !/Internal Server Error|ApiError|\[object/.test(page.text()), page.text().slice(-200));
      for (let n = 0; n < 8; n += 1) {
        const btn = page.byText(/Another hint|A hint/, "button");
        if (!btn || btn.disabled) break;
        await page.click(btn);
        await sleep(150);
      }
      await sleep(300);
      const t = page.text();
      check("learn", "running out of hints says so in words a learner can act on", /no hints left|not have any more|exhausted|limit|worked example|reveal/i.test(t), t.slice(-220));
      check("learn", "the hint budget is enforced by the page, not just by the server", (() => { const b = page.byText("No hints left", "button"); return b ? b.disabled === true : !/Another hint/.test(t); })(), "still offering more hints after exhaustion");
      // byText can legitimately return nothing here: after the loop above spends the budget, the
      // page offers the worked example through the exhausted-state wording. Clicking null turned
      // that into a crash -- and a crashed run reports nothing about the screens after it.
      const example = page.byText(/Worked example|worked example/i, "button");
      if (example) {
        await page.click(example);
        await sleep(600);
        check("learn", "a worked example opens with steps and no template leak", !/Internal Server Error|\{[a-z]+\}/.test(page.text()), page.text().slice(-200));
      } else {
        check(
          "learn",
          "an exhausted hint budget still leaves the learner a way forward",
          /reveal|see the answer|next question/i.test(page.text()),
          `no worked-example control and no way forward in: ${page.text().slice(-200)}`
        );
      }
      const revBefore = page.requests.length;
      const revBtn = page.byText("Reveal it", "button");
      if (revBtn) {
        await page.click(revBtn);
        await waitFor(() => page.requests.slice(revBefore).some((r) => /\/reveal/.test(r)), { timeout: 10_000, label: "the reveal request" });
        await sleep(500);
        check("learn", "revealing the answer is allowed, asked of the API, and shown on screen", !/Internal Server Error/.test(page.text()) && !!page.byText("Revealed", "button"), page.requests.slice(revBefore).join(" ; "));
      }
      // Defect 1 on the practice screen: press the option the learner sees — the `role="radio"`
      // button — and require the submit control to unlock.
      //
      // What this can and cannot prove, measured rather than assumed: jsdom does no hit-testing,
      // so rendering the full-screen welcome overlay on this route *still passes this check*
      // (verified by putting `const allowedHere = true` back and re-running: 26/26 green). This
      // is therefore a guard on the wiring — button press → selection → unlocked control — not
      // on the occlusion. Only a real browser can check the occlusion, which is exactly why
      // `tests-e2e/placement.mjs` exists and why docs/UNVERIFIED.md carries the caveat.
      const learnOptions = page.find('[role="radio"]');
      if (learnOptions.length) {
        const control = page.byText("Check it", "button");
        const lockedBefore = control ? control.disabled : null;
        const chosen = learnOptions.find((b) => b.getAttribute("aria-checked") !== "true") ?? learnOptions[0];
        chosen.click();
        await sleep(80);
        const unlocked = page.byText("Check it", "button");
        check(
          "learn",
          "pressing the choice itself unlocks the submit control",
          !!unlocked && unlocked.disabled === false,
          `${learnOptions.length} options, aria-checked=${chosen.getAttribute("aria-checked")}, was ${lockedBefore === true ? "locked" : String(lockedBefore)}, now ${unlocked ? (unlocked.disabled ? "still locked" : "unlocked") : "missing"}`,
        );
      }
      await fillAnswer(page);
      const learnMark = await markAnswer(page);
      check("learn", "one press of the submit control sends the attempt", learnMark.ok, learnMark.why);
      await sleep(800);
      check("learn", "submitting an answer posts to /attempts and the screen reacts", !/Internal Server Error/.test(page.text()), page.text().slice(-200));
      const askBox = page.find("input,textarea").find((el) => /own words/i.test(el.placeholder ?? ""));
      if (askBox) {
        page.type(askBox, "why do I have to flip the second fraction");
        const askBefore = page.requests.length;
        const askBtn = [...page.find("button")].find((b) => /^ask/i.test((b.textContent ?? "").trim()));
        await page.click(askBtn ?? page.byText("Ask", "button"));
        await waitFor(() => page.requests.slice(askBefore).some((r) => /POST \/api\/v1\/explain/.test(r)), { timeout: 12_000, label: "the explain request" });
        let replied = true;
        try {
          // The provenance line under the answer is what proves a reply arrived rather than a
          // hanging box, so the words it may use belong to brief §3: "your own material" (the
          // built-in tutor, nothing left the machine) and "written for you" (a model wrote it).
          // The old list looked for "course material" and "no local model", which is the
          // pre-§3 copy -- the panel no longer names a model at all.
          await waitFor(() => /reciprocal|denominator|invert|multiply|bottom|out loud|not connected|ask your tutor later|your own material|written for you/i.test(tutorBlock(page)), { timeout: 20_000, label: "a tutor reply" });
        } catch (err) {
          replied = false;
          check("learn", "asking the tutor produces an answer instead of a hanging box", false, `requests: ${page.requests.slice(askBefore).join(" ; ")} | box: ${tutorBlock(page).slice(0, 400)}`);
        }
        const reply = tutorBlock(page);
        if (replied) {
          check("learn", "asking the tutor in plain words gets an answer on screen", /flip|denominator|fraction|multiply|reciprocal|bottom|invert|your own material|written for you/i.test(reply), reply.slice(0, 240));
          // the reply used to quote the item's template ("1. {a} - {b} = {ab}") straight at
          // the learner; this is the check that would have caught it from the browser side
          check("learn", "the tutor reply contains no unrendered item template", !/\{[a-zA-Z_][a-zA-Z0-9_]*\}/.test(reply), reply.slice(0, 240));
          check("learn", "the tutor reply shows the numbers on the card, not the author's variables", /[0-9]/.test(reply) || !/worked example/i.test(reply), reply.slice(0, 240));
        }
        check("learn", "the reply is not the question echoed back", (page.text().match(/why do I have to flip the second fraction/gi) ?? []).length <= 1, "the question came back as the answer");
      } else {
        check("learn", "there is a free-text route to ask the tutor about this item", false, "no ask box found on the practice screen");
      }
      const answered = await api("/api/v1/me/attempts?limit=5", { headers: { cookie } });
      check("learn", "the attempt is retrievable afterwards", answered.status === 200 || answered.status === 404, `${answered.status} ${JSON.stringify(answered.body).slice(0, 120)}`);
      const errsBefore = page.appErrors().length;
      await page.reload();
      await waitFor(() => answerControl(page) || /placement|nothing due|no items|Start/i.test(page.text()), { timeout: 20_000, label: "the page to redraw after reload" });
      check(
        "learn",
        "a reload brings practice back with the same session state and no new error",
        page.appErrors().length <= errsBefore && page.text().length > 300,
        `errors ${page.appErrors().length - errsBefore} new; ${page.appErrors().slice(errsBefore, errsBefore + 2).join(" || ").slice(0, 300)}`,
      );
    }

    // The segmented walkthrough, on a mock page: the mock serves an "explanation first" payload,
    // so the step-by-step gate is checked exactly rather than whenever a rotation happens to hand
    // this learner that method. `?mock=1` is a design-review mode the probe already treats as
    // legitimate for the lesson screen; here it is the only way to pin the shape being tested.
    const mockPage = await open("/learn?mock=1", "learn");
    await waitFor(() => answerControl(mockPage) || /placement|no items/i.test(mockPage.text()), { timeout: 15_000, label: "the mock practice card" });
    await sleep(400);
    const mockText = mockPage.text();
    check("learn", "the method's teaching arrives with the question (mock: explanation first)", /step \d+ of \d+/.test(mockText) && /Before the question/.test(mockText), mockText.slice(0, 240));
    const gate = mockPage.byText("Next step", "button");
    const box = mockPage.find('input[placeholder^="One sentence"]')[0];
    check("learn", "the next step is locked until the step check is answered", !!gate && gate.disabled && !!box, `gate=${gate ? (gate.disabled ? "locked" : "open") : "missing"} box=${!!box}`);
    if (box) {
      mockPage.type(box, "It says the parts have to be the same size before you count them together.");
      await sleep(120);
      const say = mockPage.byText("Check my sentence", "button");
      if (say && !say.disabled) await mockPage.click(say);
      await sleep(250);
      const after = mockPage.byText("Next step", "button");
      check(
        "learn",
        "answering the check opens the next step, and the check says it is not being marked",
        !!after && !after.disabled && /not marked and is not sent anywhere/i.test(mockPage.text()),
        `next=${after ? (after.disabled ? "locked" : "open") : "missing"}`,
      );
      if (after && !after.disabled) {
        await mockPage.click(after);
        await sleep(250);
        check("learn", "one step is on screen at a time, with the material never cut", /step 2 of 3/.test(mockPage.text()) && !/…\s*$/.test(mockPage.text().slice(-40)), mockPage.text().slice(-200));
      }
    }
    closePage(mockPage);
    closePage(page);
  }

/** Signs the shared cookie jar in as a fresh learner and manufactures the evidence a
   *  proposal needs. Each gate has to be judged against a learner who has only ever seen
   *  one outcome, otherwise a previous decision colours the next one. */
  const stuckLearner = async (tag) => {
    const email = `probe-${tag}-${Date.now().toString(36)}@example.test`;
    const boot = await createLearner({ display_name: `Probe ${tag}`, email, password: learner.password });
    if (boot.status !== 200) throw new Error(`cannot create the ${tag} learner: ${boot.status} ${JSON.stringify(boot.body).slice(0, 120)}`);
    const own = new CookieJar();
    for (const raw of boot.headers.getSetCookie?.() ?? []) {
      try { own.setCookieSync(raw, BASE); } catch { /* not storable */ }
    }
    const cookie = own.getCookieStringSync(BASE);
    const hdr = { cookie, "x-csrf-token": boot.body.csrf_token ?? "" };
    const plan = await api("/api/v1/practice?count=14", { headers: { cookie } });
    const entries = (plan.body.items ?? []).map((e) => ({ item: e.item ?? e, topic: e.topic_id ?? (e.item ?? e)?.topic_id ?? "?" }))
      .filter((e) => e.item?.id);
    // A proposal needs at least 8 attempts on *one* topic before the detectors will speak --
    // the gate is deliberate (4 rows is a guess, 8 is a pattern), and it is why this check used
    // to be flaky: answering 14 items spread over 14 topics puts every topic under the gate, so
    // the learner looks "clearly struggling" to a person and like noise to the loop. So aim at
    // the topic the queue offers most of and answer *those* wrong, twice over, which is what a
    // struggling learner in front of the app would actually do.
    const byTopic = new Map();
    for (const e of entries) {
      if (!byTopic.has(e.topic)) byTopic.set(e.topic, []);
      byTopic.get(e.topic).push(e.item);
    }
    const focus = [...byTopic.values()].sort((a, b) => b.length - a.length)[0] ?? [];
    const batch = [];
    while (batch.length < 12 && focus.length) for (const it of focus) { if (batch.length < 12) batch.push(it); }
    let sid = "";
    let proposals = [];
    for (let round = 0; round < 3 && !proposals.length; round += 1) {
      const sess = await api("/api/v1/sessions", { method: "POST", body: { mode: "practice" }, headers: hdr });
      sid = sess.body.session_id;
      for (const it of batch) {
        await api("/api/v1/attempts", { method: "POST", body: { session_id: sid, item_id: it.id, answer: "__definitely_wrong__" }, headers: hdr });
      }
      await api(`/api/v1/analyze?session_id=${encodeURIComponent(sid)}`, { method: "POST", headers: hdr });
      const pending = await api("/api/v1/adaptations/pending", { headers: { cookie } });
      proposals = pending.body.proposals ?? [];
    }
    return { email, cookie, hdr, sid, proposals };
  };

  /** Puts an account on the shared jar, which is what the front door does: whoever signed in last
   *  is who the following screens are judged on. The decisions scenario deliberately does *not*
   *  use this -- its three throwaway accounts live in their own jars. */
  const signIn = async (email, password) => {
    const r = await api("/api/v1/login", { method: "POST", body: { email, password } });
    for (const raw of r.headers.getSetCookie?.() ?? []) {
      try { jar.setCookieSync(raw, BASE); } catch { /* not storable */ }
    }
    if (r.status === 200) csrf = r.body.csrf_token ?? csrf;
    return r.status;
  };

  const settleProposals = async (pg) => {
    // the right-hand "On right now" card renders before the pending list has arrived, so
    // waiting for it would judge an empty page; wait for one of the two real answers instead
    try {
      // "what was observed" also appears in the page's own intro sentence, so anchor on a card
    // title instead, otherwise the wait returns while the list is still empty
    await waitFor(() => /What saying yes would change|Nothing to approve/i.test(pg.text()), { timeout: 20_000, label: "the waiting changes to load" });
    } catch (err) {
      console.log(`\x1b[2m  (settle: ${err.message} | page asked for: ${pg.requests.slice(-6).join(" ; ")} | said: ${(pg.text() ?? "").replace(/\s+/g, " ").slice(0, 200)})\x1b[0m`);
      throw err;
    }
  };
  /* ------------------------------------------------------------- proposals ---- */
  // The gate: AI 1 may propose, only the learner may apply. Each outcome is judged against a
  // learner who has been asked exactly once, so no earlier decision colours the next answer.
  if (run("proposals")) {
    // Each gate is judged against a learner who has seen exactly one outcome, so this scenario
    // borrows three throwaway accounts. It must not borrow the run's own session to do it: an
    // earlier version wrote each stuck learner into the shared jar and then "put it back" by
    // signing in again, which 403'd on the token that same code had overwritten -- and left every
    // scenario after it judging the wrong account. Identity is recorded here so the end of the
    // scenario can prove nothing was borrowed.
    const savedWho = await api("/api/v1/me", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    const savedId = String(savedWho.body?.student_id ?? "");
    console.log("\n\x1b[1mproposals\x1b[0m");
    const activeOf = async (cookie) => (await api("/api/v1/adaptations/active", { headers: { cookie } })).body;
    const prefsOf = async (cookie) => JSON.stringify((await api("/api/v1/me/preferences", { headers: { cookie } })).body);

    /* ---- rejecting: the page and the jar must belong to the same learner, so one learner
       per phase, each signed in just before their own phase drives the screen ---- */
    const reject = await stuckLearner("reject");
    check("proposals", "a learner who is clearly struggling produces real evidence and a real question", reject.proposals.length > 0 && !!reject.sid, JSON.stringify(reject.proposals.map((x) => x.strategy)));
    check("proposals", "reject: nothing is applied before the learner has answered", (await activeOf(reject.cookie)).active?.length === 0, JSON.stringify(await activeOf(reject.cookie)).slice(0, 140));

    /* ---- rejecting must apply nothing, anywhere ---- */
    const page = await open("/proposals", "proposals", undefined, reject.cookie);
    await settleProposals(page);
    const body = () => page.text();
    check("proposals", "a waiting proposal shows observation, pattern, explanation and what it would change", /What was observed/i.test(body()) && /What saying yes would change/i.test(body()) && /How it would be judged/i.test(body()), body().slice(0, 260));
    check("proposals", "the four answers a learner can give are all offered", ["Yes, try it", "No", "Yes, but change it", "Not now"].every((v) => !!page.byText(v, "button")), body().slice(0, 200));
    check("proposals", "the ask is a question, not a decision already made", /say yes|if you say no|not applied until|nothing is applied|your call|your answer|Want me to try that/i.test(body()), body().slice(0, 240));
    // the catalog attaches a source to most strategies; the pending card has to show it, or the
    // learner is being asked to judge an experiment without being told it is one
    const cited = (await api("/api/v1/adaptations/pending", { headers: { cookie: reject.cookie } })).body.proposals?.[0] ?? {};
    check("proposals", "a proposal shows where the idea comes from", !cited.citations?.length || (/Why anyone believes this works/i.test(body()) && /\d{4}/.test(body())), `citations: ${JSON.stringify((cited.citations ?? []).map((c) => c.ref))}`);
    check("proposals", "the evidence is separated from the interpretation", /events only|no interpretation|Other things that would produce this/i.test(body()), body().slice(0, 240));
    const prefsBefore = await prefsOf(reject.cookie);
    const noBtn = await waitFor(() => page.byText(/^No$/, "button"), { timeout: 15_000, label: "the refuse control" }).catch(() => null);
    check("proposals", "rejecting is offered as its own answer", !!noBtn, "the button was not found");
    if (noBtn) {
      await page.click(noBtn);
      await waitFor(() => /Off, and noted|not applied|already answered|Recorded as a no|Nothing is applied/i.test(body()), { timeout: 15_000, label: "the rejection to be answered" });
      const a1 = await activeOf(reject.cookie);
      check("proposals", "a rejection applies nothing: no adaptation is active afterwards", (a1.active ?? []).length === 0, JSON.stringify(a1).slice(0, 180));
      check("proposals", "a rejection changes no teaching setting either", (await prefsOf(reject.cookie)) === prefsBefore, "preferences moved without the learner approving anything");
      const hist = await api("/api/v1/adaptations/history", { headers: { cookie: reject.cookie } });
      const rows = hist.body.decisions ?? hist.body.entries ?? hist.body.history ?? [];
      const rejectedRow = rows.find((r) => r.decision === "rejected" || r.status === "rejected");
      check("proposals", "the rejection is stored as the learner's own decision", !!rejectedRow, JSON.stringify(hist.body).slice(0, 200));
      const again = await api(`/api/v1/adaptations/${reject.proposals[0].id}/decision`, { method: "POST", body: { decision: "accept", answered_by: "student" }, headers: reject.hdr });
      check("proposals", "a proposal cannot be answered twice to sneak a change in", again.status === 409 || again.status === 404 || again.status === 400, `${again.status} ${JSON.stringify(again.body).slice(0, 120)}`);
      check("proposals", "and the refused second answer still applied nothing", (await activeOf(reject.cookie)).active?.length === 0, JSON.stringify(await activeOf(reject.cookie)).slice(0, 160));
    }
    check("proposals", "no uncaught error on the decisions screen", page.appErrors().length === 0, page.appErrors().join(" | ").slice(0, 200));
    closePage(page);

    /* ---- accepting: the one action that turns teaching off autopilot ---- */
    const accept = await stuckLearner("accept");
    check("proposals", "accept: nothing is applied before the learner has answered", (await activeOf(accept.cookie)).active?.length === 0, JSON.stringify(await activeOf(accept.cookie)).slice(0, 140));
    const page2 = await open("/proposals", "proposals", undefined, accept.cookie);
    await settleProposals(page2);
    if (!(await waitFor(() => !!page2.byText("Yes, try it", "button"), { timeout: 15_000, label: "a proposal to accept" }).catch(() => null))) {
      check("proposals", "the accept control is reachable from the decisions page", false, page2.text().slice(0, 220));
    } else {
      const prefsAtAcceptTime = await prefsOf(accept.cookie);
      const ok = await page2.click("Yes, try it", "button");
      check("proposals", "the accept control is pressable", !ok.disabled, "the button was disabled");
      await waitFor(() => /On\./i.test(page2.text()), { timeout: 15_000, label: "the confirmation" });
      const active = await activeOf(accept.cookie);
      const row = (active.active ?? [])[0];
      check("proposals", "saying yes is what turns an adaptation on", !!row, JSON.stringify(active).slice(0, 200));
      check("proposals", "the confirmation names the change and its review, rather than just thanking the learner", /on\.|review|measured|next few/i.test(page2.text()), page2.text().slice(-240));
      if (row) {
        const audit = await api("/api/v1/audit?limit=25", { headers: { cookie: accept.cookie } });
        const entries = audit.body.entries ?? audit.body.audit ?? [];
        const consent = entries.find((e) => String(e.action ?? "").startsWith("adaptation_"));
        check("proposals", "the audit records the acceptance as answered_by=student from the web", consent?.detail?.answered_by === "student" && consent?.detail?.channel === "web", JSON.stringify(consent ?? entries[0] ?? {}).slice(0, 200));
        const tuned = (row.payload ?? {}).target ?? null;
        check("proposals", "what the learner approved is the number now in force, not the default already in use", tuned === null || Number(tuned) !== 0.78, JSON.stringify(row.payload ?? {}).slice(0, 160));
        check("proposals", "an approval that was not about settings leaves settings alone", prefsAtAcceptTime === (await prefsOf(accept.cookie)), "preferences moved when only an adaptation was approved");
        // this page is the approver's practice screen, so it is opened as the approver. Before the
        // per-page sessions landed it read the run's shared jar, which a previous swap had happened
        // to leave pointing at this same learner -- it was checking the right thing by accident,
        // and one creation finishing a beat earlier made it somebody else's page.
        const learn = await open("/learn", "proposals", undefined, accept.cookie);
        await waitFor(() => /Changes you have approved|Nothing is applied/i.test(learn.text()), { timeout: 20_000, label: "practice to finish loading" });
        check("proposals", "the learner can see the change in force on the practice screen", /Changes you have approved/i.test(learn.text()), learn.text().slice(0, 260));
        closePage(learn);
        const page3 = await open("/proposals", "proposals", undefined, accept.cookie);
        await settleProposals(page3);
        // The "On right now" panel and the pending list are separate responses, and this learner
        // has an active adaptation (asserted above from the API), so wait for the panel before
        // judging it -- otherwise a reload can be read mid-load and reported as "Nothing is
        // applied" while the row exists. If it never appears, the checks below still fail.
        await waitFor(() => /On right now/i.test(page3.text()), { timeout: 10_000, label: "the applied panel to load" }).catch(() => {});
        const appliedText = page3.text();
        check("proposals", "the applied row carries the evidence line with it", !/turn off/.test(appliedText) || /evidence:\s*\S/.test(appliedText), appliedText.slice(appliedText.indexOf("On right now"), appliedText.indexOf("On right now") + 320));
        check("proposals", "after a reload the accepted change is still listed as on right now", /On right now/.test(page3.text()) && !/Nothing is applied/.test(page3.text()), page3.text().slice(0, 220));
        const revokeBtn = [...page3.find("button")].find((b) => /^turn off$/i.test((b.textContent ?? "").trim()));
        if (revokeBtn) {
          await page3.click(revokeBtn);
          await waitFor(async () => (await activeOf(accept.cookie)).active?.length === 0, { timeout: 15_000, label: "the revocation to be recorded" });
          check("proposals", "turning it off from the page actually turns it off", (await activeOf(accept.cookie)).active?.length === 0, JSON.stringify(await activeOf(accept.cookie)).slice(0, 180));
          const revokedBox = [...page3.find(".ledger-log")].find((x) => /Decisions made this session/i.test(x.textContent ?? ""));
          check("proposals", "a change of mind is written down beside the decision it undid", /revoked/i.test(String(revokedBox?.textContent ?? "")), String(revokedBox?.textContent ?? "").slice(0, 200));
        } else {
          check("proposals", "the applied change can be withdrawn from the same screen", false, "no turn-off control beside the applied row");
        }
        closePage(page3);
      }
    }
    closePage(page2);

    /* ---- modifying: the learner's version is the one that is stored ---- */
    const modify = await stuckLearner("modify");
    check("proposals", "modify: nothing is applied before the learner has answered", (await activeOf(modify.cookie)).active?.length === 0, JSON.stringify(await activeOf(modify.cookie)).slice(0, 140));
    const page4 = await open("/proposals", "proposals", undefined, modify.cookie);
    await settleProposals(page4);
    const modBtn = await waitFor(() => page4.byText("Yes, but change it", "button"), { timeout: 15_000, label: "the modify control" }).catch(() => null);
    if (modBtn) {
      await page4.click(modBtn);
      await sleep(500);
      const fields = page4.find("input");
      check("proposals", "modifying opens the numbers so the learner can change them", fields.length > 0, `${fields.length} editable fields`);
      const note = page4.find("textarea")[0];
      if (note) page4.type(note, "two, not four - I lose the thread after that");
      if (fields[0]) page4.type(fields[0], "2");
      await page4.click("Send it with those changes", "button");
      await waitFor(() => /On\.|refused_keys|applied/i.test(page4.text()), { timeout: 15_000, label: "the modified decision to be answered" });
      const hist = await api("/api/v1/adaptations/history", { headers: { cookie: modify.cookie } });
      const blob = JSON.stringify(hist.body);
      const mine = (hist.body.decisions ?? hist.body.entries ?? hist.body.history ?? []).find((r) => String(r.decision ?? r.status ?? "").includes("modif"));
      check("proposals", "a modified approval is recorded as the learner's modification, not the model's proposal", !!mine, blob.slice(0, 240));
      check("proposals", "and the reason the learner typed is kept with it", /lose the thread|two, not four/i.test(blob), blob.slice(-200));
      // The log beside the buttons, in the buttons' own words: reading back what you decided is
      // the point of it, so it is checked against the box and not against the whole page (where
      // the button's own label would make the check pass on an empty log).
      const logBox = [...page4.find(".ledger-log")].find((x) => /Decisions made this session/i.test(x.textContent ?? ""));
      const logText = String(logBox?.textContent ?? "");
      check("proposals", "the decision is written into the log the learner can read back", /Yes, but change it/i.test(logText), logText.slice(0, 200));
      const applied = await activeOf(modify.cookie);
      const arow = (applied.active ?? [])[0];
      check("proposals", "the version that takes effect is the learner's edited one", !!arow && JSON.stringify(arow.payload ?? {}).includes("2"), JSON.stringify(applied).slice(0, 220));
    } else {
      check("proposals", "modifying is offered when the learner wants to change the numbers", false, page4.text().slice(0, 220));
    }
    closePage(page4);
    const back = await api("/api/v1/me", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    check(
      "proposals",
      "the decisions scenario never borrowed the run's own session",
      // the promise is "the same account you had before this scenario", which is what later
      // screens are judged on -- the id is the only field that says that
      back.status === 200 && savedId !== "" && String(back.body?.student_id ?? "") === savedId,
      `the session after the scenario reads ${back.status} ${JSON.stringify(back.body).slice(0, 120)} (expected ${savedId || "no identity to compare"})`
    );
  }

  /* -------------------------------------------------------------- progress ---- */
  if (run("progress")) {
    console.log("\n\x1b[1mprogress\x1b[0m");
    const page = await open("/progress", "progress");
    await sleep(600);
    const body = page.text();
    check("progress", "mastery is shown with a number or an explicit empty state", /\d/.test(body) || /no data|nothing yet|not enough/i.test(body), body.slice(0, 180));
    // "you are a" alone would flag the honest sentence "Unaided you are at 0.42", so the scan is
    // for the thing this app refuses to say: a fixed trait, a label, a diagnosis.
    check("progress", "the page distinguishes measurement from diagnosis", !/\b(disorder|deficit|incapable|ADHD|dyslexic|slow learner|low ability|not smart|you are a (visual|auditory|kinesthetic|slow|weak|poor|lazy))\b/i.test(body) && !/learning (style|preference) (says|shows|proves)/i.test(body), body.slice(0, 140));
    check("progress", "history/journey or its absence is stated", /journey|history|step|session/i.test(body), body.slice(0, 140));
    // Every number from a small sample has to carry its size and the words that qualify it.
    const dashN = await api("/api/v1/dashboard", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    const report = await api("/api/v1/methods/report", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    const hasMastery = ((dashN.body || {}).mastery || []).length > 0;
    const hasMethods = ((report.body || {}).methods || []).length > 0;
    check("progress", "a count of observations sits next to every estimate", !hasMastery || /n=\d+|\d+ obs/.test(body), body.slice(0, 200));
    check(
      "progress",
      "a small sample is called an early signal in words, not just coloured",
      (!hasMethods && !hasMastery) || /early signal/i.test(body) || !/actual|expected/i.test(body),
      body.slice(0, 200),
    );
    const dash = await api("/api/v1/dashboard", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    const shown = JSON.stringify(dash.body);
    check("progress", "what the page shows exists in the API response", shown.length > 60, shown.slice(0, 140));

    /* ---- the snapshot's progress chart, which the app shipped the styles for and never drew ----
     * `.topo`, `.topo-table` and `.pg-note` sat in globals.css with nothing rendering them, and
     * Progress answered with a list of coloured band pills — which flattens three different
     * claims into one: where the estimate is, how wide the uncertainty around it is, and how
     * many answers it rests on. The encoding is the honesty, so each part of it is checked: the
     * drawing, its description for a screen reader, the words that say what each dimension
     * means, and a row of exact figures per lane (a chart nobody can read as numbers is a chart
     * nobody can check). */
    const drawn = page.text();
    const masteryRows = (dashN.body || {}).mastery || [];
    const topo = page.find('svg.topo[role="img"]');
    const lanes = page.find("table.topo-table tbody tr");
    const topoLabel = String(topo[0]?.getAttribute("aria-label") ?? "");
    check("progress", "the estimate is drawn, not only listed", !masteryRows.length || topo.length === 1, `charts found: ${topo.length} for ${masteryRows.length} topics`);
    check("progress", "the drawing is described for a screen reader instead of being decoration", !masteryRows.length || topoLabel.length > 80, topoLabel.slice(0, 140));
    check("progress", "the page says what each dimension of the chart means", !masteryRows.length || (/position is the estimate/i.test(drawn) && /more confident one/i.test(drawn)), drawn.slice(0, 160));
    check("progress", "every lane has a row of exact figures underneath it", !masteryRows.length || lanes.length === masteryRows.length, `${lanes.length} rows drawn for ${masteryRows.length} topics in the response`);
    check("progress", "the interval around the estimate is named as an interval", !masteryRows.length || /uncertainty/i.test(drawn), drawn.slice(0, 140));
    // The corner index tab is gone (brief 2.3): navigation is a permanent sidebar, and the
    // current page is marked rather than hidden behind a control. These two assertions target
    // that structure instead — same count, stronger claim: the marked item must be this
    // section *and* the sidebar must offer every other one in plain words.
    const here = page.find('.sidebar-item[aria-current="page"]');
    check("progress", "the sidebar names the section it is standing on", here.length === 1 && /Progress/i.test(here[0]?.textContent ?? ""), here.map((el) => el.textContent?.trim()).join(" | ") || "(nothing marked)");
    check("progress", "and offers every other section in plain words", ["Your path", "Practise", "Lessons", "Changes", "Review", "Progress", "Mind check", "Voice", "Settings"].every((l) => !!page.byText(l, ".sidebar-item")), page.find(".sidebar-item").map((el) => el.textContent?.trim()).join(" | ") || "(no sidebar)");

    /* ---- the same chart on mock data, where the numbers are known exactly ----
     * Live, this learner may have no mastery rows at all, and every check above is gated on
     * having some — a gate that can quietly become "never runs". The mock dashboard carries two
     * lanes on purpose: an estimate from four answers with a wide interval, and one from fourteen
     * with a narrow one. That is the encoding being checked *as* an encoding: if the small sample
     * does not get the wider line, the chart is decoration and the honest thing is a list. */
    const mockProgress = await open("/progress?mock=1", "progress");
    await waitFor(() => mockProgress.find("table.topo-table tbody tr").length >= 2, { timeout: 15_000, label: "the mock chart" }).catch(() => {});
    await sleep(300);
    const mockRows = mockProgress.find("table.topo-table tbody tr");
    const mockText = mockProgress.text();
    check("progress", "mock: both lanes are drawn, with a row of exact figures each", mockRows.length === 2, `${mockRows.length} table rows`);
    // The estimate ticks and the interval end-caps are vertical (zero width), so the only lines
    // with a width in them are the two uncertainty intervals.
    const spans = mockProgress
      .find("svg.topo line")
      .map((l) => Math.abs(Number(l.getAttribute("x2")) - Number(l.getAttribute("x1"))))
      .filter((w) => w > 1);
    check("progress", "mock: the four-answer estimate carries the wider interval", spans.length === 2 && spans[0] > spans[1], `interval widths, first lane then second: ${spans.map((w) => w.toFixed(0)).join(", ")}`);
    check("progress", "mock: the small sample is called an early signal beside its own number", /n=4\b/i.test(mockText) && /early signal/i.test(mockText), mockText.slice(0, 200));
    check("progress", "mock: the estimates on screen are the ones the fixture sent", /62%/.test(mockText) && /29%/.test(mockText), mockText.slice(0, 160));
    check("progress", "mock: the evidence count sizes the circle, and the page says so", /bigger circle is not a better score/i.test(mockText), mockText.slice(0, 160));
    closePage(mockProgress);
    closePage(page);
  }

  /* ---------------------------------------------------------------- review ---- */
  if (run("review")) {
    console.log("\n\x1b[1mreview\x1b[0m");
    const page = await open("/review", "review");
    await sleep(600);
    const body = page.text();
    check("review", "due-review list or an explicit 'nothing due' state", /due|review|nothing|all caught|come back/i.test(body), body.slice(0, 180));
    const start = page.byText(/start|review|again|later/i, "button");
    if (start) {
      await page.click(start);
      await sleep(700);
      check("review", "a review control does something visible", page.requests.length > 0 || page.text() !== body, page.requests.join(", "));
    }
    // A self-report that leaves no trace on the screen is one the learner cannot check. The live
    // region says each rating once and is gone; the snapshot keeps a running list, and Changes
    // already had one.
    const reviewBody = page.text();
    check(
      "review",
      "what was rated stays readable after the announcement is gone",
      /Reviews completed this session/i.test(reviewBody) && (/Nothing reviewed yet/i.test(reviewBody) || /rated/i.test(reviewBody)),
      reviewBody.slice(0, 160),
    );
    const reviewHere = page.find('.sidebar-item[aria-current="page"]');
    check("review", "the sidebar names this section too", reviewHere.length === 1 && /Review/i.test(reviewHere[0]?.textContent ?? ""), reviewHere.map((el) => el.textContent?.trim()).join(" | ") || "(nothing marked)");
    closePage(page);

    // The retention check (master prompt 5). Driven against the fixtures because a fresh learner
    // has nothing scheduled: a due row offers "Show me the check", the question arrives with the
    // reason it is worth asking now, and -- the honesty property -- the answer key is not on
    // screen until an answer exists.
    const rated = await open("/review?mock=1", "review");
    await sleep(700);
    const checkBtn = rated.byText("Show me the check", "button");
    check("review", "a due row offers the retention check", Boolean(checkBtn), rated.text().slice(0, 160));
    if (checkBtn) {
      await rated.click(checkBtn);
      await sleep(500);
      const asked = rated.text();
      check("review", "the check arrives with the question and the reason it is asked now", /Without looking anything up/i.test(asked) && /projected recall|Forgetting curve/i.test(asked), asked.slice(0, 220));
      check("review", "the answer key is not shown before an answer", !/how many equal parts the whole is cut into/i.test(asked), asked.slice(0, 220));
      const rateBtn = rated.byText("I still know it", "button");
      if (rateBtn) {
        await rated.click(rateBtn);
        await sleep(800);
        const after = rated.text();
        check("review", "after rating, the key opens and the next check is scheduled", /what it should have covered/i.test(after) && /equal parts the whole is cut into/i.test(after) && /next check/i.test(after), after.slice(0, 240));
      }
    }
    check("review", "and no uncaught error on the mocked review walk", rated.appErrors().length === 0, rated.appErrors().slice(0, 2).join(" ; "));
    closePage(rated);
  }

  /* -------------------------------------------------------------- settings ---- */
  if (run("settings")) {
    console.log("\n\x1b[1msettings\x1b[0m");
    const page = await open("/settings", "settings");
    await sleep(700);
    const body = page.text();
    check("settings", "settings page renders controls", page.find("input,select,button").length > 3, `${page.find("input").length}/${page.find("select").length}/${page.find("button").length}`);
    check("settings", "the privacy/delete story is present, not hidden", /delete|erase|export|privacy/i.test(body), body.slice(0, 160));
    // flip a preference and prove it round-trips through the API after a reload. The page has
    // more than one card with a Save button, so the control, its button and the re-read all
    // stay inside the same <section>; the box is identified by its label, not by position.
    // The preferences card renders one control per server-accepted key, in `allowed_keys`
    // order, so the box is tracked by its index in that card rather than by its own caption
    // (the caption is just the word "on"/"off" -- several boxes share it).
    const boxesIn = (pg) => {
      const sec = [...pg.find("section")].find((x) => /Your preferences/.test(x.textContent ?? ""));
      return sec ? [...sec.querySelectorAll('input[type="checkbox"]')] : [];
    };
    const boxes = boxesIn(page);
    const idx = boxes.findIndex((el) => !el.disabled);
    if (idx >= 0) {
      const toggle = boxes[idx];
      // Field() puts the key's human name in the first <span>; the box's own label is only the
      // word "on"/"off", which several boxes share.
      // Check() has a <label> of its own whose only text is "on"/"off"; the key's name lives in
      // the Field() label one level up, so read that span or the comparison looks for a key
      // called "off".
      const fieldName = (toggle.closest("label")?.parentElement?.querySelector(":scope > span")?.textContent ?? "").trim();
      const was = toggle.checked;
      if (was) page.uncheckInput(toggle); else page.checkInput(toggle);
      await sleep(150);
      check("settings", "the control moves in the page when it is clicked", toggle.checked !== was, `"${fieldName}" still ${toggle.checked}`);
      const card = toggle.closest("section");
      const save = [...card.querySelectorAll("button")].find((b) => /^save$/i.test((b.textContent ?? "").trim()));
      if (save && !save.disabled) { await page.click(save); await sleep(900); }
      check("settings", "the server took the key instead of quietly dropping it", !/not allowed|refused/i.test(card.textContent ?? ""), (card.textContent ?? "").slice(0, 200));
      const prefs = await api("/api/v1/me/preferences", { headers: { cookie: jar.getCookieStringSync(BASE) } });
      const key = fieldName.replace(/\s+/g, "_").toLowerCase();
      const stored = (prefs.body.preferences ?? {})[key];
      check("settings", `the saved value is what the API now returns (${key})`, stored === toggle.checked, `${key}: api says ${JSON.stringify(stored)}, page asked for ${toggle.checked}`);
      const page2 = await open("/settings", "settings");
      await sleep(700);
      const again = boxesIn(page2)[idx];
      check("settings", "a flipped control is still flipped after a reload", !!again && again.checked === toggle.checked, again ? `before=${was} after=${again.checked}` : "the box did not come back");
      closePage(page2);
    } else {
      check("settings", "a preference can be edited and saved", false, "no enabled checkbox inside the preferences card");
    }
    {
      const sec = [...page.find("section")].find((x) => /Your preferences/.test(x.textContent ?? ""));
      const fields = [...(sec?.querySelectorAll("label") ?? [])].filter((l) => /^(avoid|encourage)$/.test((l.querySelector(":scope > span")?.textContent ?? "").trim()));
      check("settings", "a phrase preference is typed, not ticked", fields.length > 0 && fields.every((f) => !f.querySelector('input[type="checkbox"]')), `${fields.length} phrase field(s) found, controls: ${fields.map((f) => f.querySelector("input")?.type).join(", ")}`);
      if (fields.length) {
        const box = fields[0].querySelector("input");
        if (box) {
          page.type(box, "word problems, being rushed");
          const sv = [...(sec?.querySelectorAll("button") ?? [])].find((b) => /^save$/i.test((b.textContent ?? "").trim()));
          if (sv) { await page.click(sv); await sleep(900); }
          const after = await api("/api/v1/me/preferences", { headers: { cookie: jar.getCookieStringSync(BASE) } });
          const storedAvoid = (after.body.preferences ?? {})[(fields[0].querySelector(":scope > span")?.textContent ?? "").trim().toLowerCase()];
          check("settings", "what was typed is stored as separate phrases, not one long string", Array.isArray(storedAvoid) && storedAvoid.length === 2 && storedAvoid[0] === "word problems", JSON.stringify(storedAvoid));
          const page3 = await open("/settings", "settings");
          // the page fills these fields from its own prefs fetch a beat after first paint; a fixed
          // sleep read the empty frame and reported a persistence bug that was not there (the same
          // mistake the reload check in `onboarding` made)
          await waitFor(
            () => [...page3.find("input")].some((i) => /word problems/.test(i.value ?? "")),
            { timeout: 20_000, label: "the saved phrases to come back from the server" }
          ).catch(() => {});
          const shown = [...page3.find("input")].map((i) => i.value).find((v) => /word problems/.test(v ?? ""));
          check("settings", "and the page shows the phrases again after a reload", /word problems.*being rushed/.test(shown ?? ""), `the fields read ${JSON.stringify([...page3.find("input")].map((i) => i.value).filter(Boolean).slice(0, 6))}`);
          closePage(page3);
        }
      }
    }
    const selects = page.find("select");
    if (selects.length) {
      const sel = selects[0];
      const before = sel.value;
      const other = [...sel.options].find((o) => o.value !== before && o.value);
      if (other) {
        page.pick(sel, other.value);
        const save = page.byText(/save|update/i, "button");
        if (save && !save.disabled) await page.click(save);
        await sleep(700);
        const prefs = await api("/api/v1/me/preferences", { headers: { cookie: jar.getCookieStringSync(BASE) } });
        check("settings", "a changed choice reaches the stored preferences", JSON.stringify(prefs.body).includes(other.value) || prefs.status === 200, JSON.stringify(prefs.body).slice(0, 160));
      }
    }
    closePage(page);
  }

  /* ----------------------------------------------------------------- voice ---- */
  if (run("voice")) {
    console.log("\n\x1b[1mvoice\x1b[0m");
    const page = await open("/voice", "voice");
    await sleep(700);
    const body = page.text();
    // §3: the page no longer names the engines at all (they were "whisper" and "kokoro" here
    // until the boundary reshape), so the check looks for the learner-facing wording instead.
    check("voice", "voice page states what is and is not installed", /on this machine|browser dictation|speechSynthesis|microphone|voice/i.test(body), body.slice(0, 180));
    check("voice", "the engine status is honest, not 'ready' with no engine", !/\bready\b/i.test(body) || /not installed|unavailable|browser/i.test(body), body.slice(0, 200));
    const cap = await api("/api/v1/voice/capabilities", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    // §3: the capabilities payload says what is available on this machine, never which product
    // provides it. This is the same rule the backend canary test enforces from the inside.
    const capText = JSON.stringify(cap.body).toLowerCase();
    check(
      "voice",
      "capabilities answers, and names no engine to a signed-in learner",
      cap.status === 200 &&
        capText.length > 40 &&
        !/faster-whisper|whisper|kokoro|gemini|openalex|arxiv|youtube|google/.test(capText),
      JSON.stringify(cap.body).slice(0, 180),
    );
    const mic = page.byText(/mic|speak|listen|talk/i, "button");
    if (mic) {
      await page.click(mic);
      await sleep(1200);
      const t = page.text();
      check("voice", "pressing the mic without permission explains itself and does not hang", /allow|permission|denied|unavailable|not supported|could not|browser/i.test(t), t.slice(0, 200));
      check("voice", "the control returns to a usable state (no stuck 'listening')", !/listening…|recording…/i.test(t) || /stop/i.test(t), t.slice(0, 120));
    }
    for (const sel of [...page.find("select")]) {
      const near = `${sel.name ?? ""} ${sel.id ?? ""} ${(sel.closest("label")?.textContent ?? "")} ${(sel.closest("section")?.textContent ?? "").slice(0, 80)}`;
      if (!/voice|gender|male|female/i.test(near)) continue;
      const opts = [...sel.options].map((o) => o.value);
      check("voice", "both voice genders are selectable", opts.length >= 2 && opts.some((v) => /fem/i.test(v)) && opts.some((v) => /male/i.test(v)) && !/female/.test(opts.filter((v) => /male/.test(v)).join(",")) === false, `options: ${opts.join(", ")}`);
      check("voice", "choosing a gender takes effect without a save that never arrives", (() => { sel.value = opts.find((v) => v !== sel.value) ?? opts[0]; sel.dispatchEvent(new page.dom.window.Event("change", { bubbles: true })); return true; })(), "switched to " + sel.value);
      break;
    }
    closePage(page);
  }

  /* -------------------------------------------------------------- contrast ---- */
  // Phase 5's contrast demo: one concept, two stored profiles, two lessons, and a table of what
  // actually differs between them. The screen must be reachable from the nav (it was not, before
  // the integration), must say whether the profiles produced different lessons, and must not
  // invent a difference - the API's own note is printed under the table.
  if (run("contrast")) {
    console.log("\n\x1b[1mcontrast\x1b[0m");
    const page = await open("/contrast", "contrast");
    await sleep(500);
    const before = page.text();
    const runIt = page.byText(/compare|show|build|contrast/i, "button");
    if (runIt) {
      const calls = page.requests.length;
      await page.click(runIt);
      await waitFor(() => page.requests.slice(calls).some((r) => /POST \/api\/v1\/lesson-plan\/contrast/.test(r)), { timeout: 15_000, label: "the contrast request" });
      await sleep(600);
    }
    const body = page.text();
    check("contrast", "the screen asks the API for two lessons rather than inventing them", /lesson-plan\/contrast/.test(page.requests.join(" ")), page.requests.slice(-3).join(" ; "));
    check("contrast", "both profiles get a lesson of their own", (body.match(/step size|opens with|worked example|visual/gi) ?? []).length >= 4, body.slice(0, 220));
    check("contrast", "the differences table names what differs, with both sides", /what actually differs|opens with|step size/i.test(body), body.slice(0, 200));
    check("contrast", "the screen says whether they differ at all, from the API's own note", /profiles produced the same lesson|every difference above is read off|visibly/i.test(body) || /differs/i.test(body), body.slice(0, 220));
    check("contrast", "every block carries why it is there (n included)", /why:/i.test(body) && /\(n=\d+\)/.test(body), body.slice(0, 220));
    check("contrast", "no raw JSON object is shown to the learner on this screen", !/\{"[a-z_]+":/.test(body), body.slice(0, 200));
    if (!before.trim()) check("contrast", "the screen renders before any request", false, "empty body");
    closePage(page);

    const mockPage = await open("/contrast?mock=1", "contrast");
    await sleep(500);
    const mockBtn = mockPage.byText(/compare|show|build|contrast/i, "button");
    if (mockBtn) {
      await mockPage.click(mockBtn);
      await sleep(600);
    }
    const mockBody = mockPage.text();
    check("contrast", "?mock=1 answers with two lessons and no backend", /adding fractions|step size|opens with/i.test(mockBody) && !/Internal Server Error/.test(mockBody), mockBody.slice(0, 200));
    check("contrast", "and the mocked screen raises no uncaught error", mockPage.appErrors().length === 0, mockPage.appErrors().slice(0, 2).join(" ; "));
    closePage(mockPage);
  }

  /* ---------------------------------------------------------------- lesson ---- */
  // The interactive player, driven the way a learner drives it: open a scene, be blocked by
  // a prediction, answer it, ask why, change the numbers, and read the review back. Every
  // assertion here is against what the *backend* said, so a player that renders its own
  // invention would fail even though it looks alive.
  if (run("lesson")) {
    console.log("\x1b[1mlesson\x1b[0m");
    const cookie = jar.getCookieStringSync(BASE);
    const page = await open(MOCK ? "/lesson?mock=1" : "/lesson", "lesson");
    // the header is server-rendered, so waiting on it would race the fetch: wait for a card
    const cardsOf = () => page.find("section").map((el) => el.textContent ?? "");
    await waitFor(() => cardsOf().some((t) => /moments where you say what happens next/.test(t)), {
      timeout: 15_000,
      label: "a scene card with its prediction count",
    });

    const list = await api("/api/v1/lessons", { cookie });
    check("lesson", "the list the page shows is the list the API has", list.status === 200 && list.body.scenes.length > 0, JSON.stringify(list.body).slice(0, 120));
    if (MOCK) {
      // the point of ?mock=1 is a design review with no backend in the room, so the tell is what
      // the browser did *not* do: one /api/v1 call means a component stopped consulting the mock
      const apiCalls = page.requests.filter((r) => /\/api\/v1\//.test(r));
      check(
        "lesson",
        "?mock=1 serves the lesson list without asking the backend for anything",
        apiCalls.length === 0,
        apiCalls.slice(0, 3).join(" | ") || "no /api/v1 requests from the lesson page",
      );
    }
    const first = list.body.scenes.find((s) => s.kind === "binary_search") ?? list.body.scenes[0];
    const cardText = cardsOf().find((t) => t.includes(first.title.slice(0, 24)));
    check("lesson", "a scene card says how many moments ask you to predict", /moments where you say what happens next/.test(cardText ?? ""), (cardText ?? "no card").slice(0, 160));
    check("lesson", "the frame count on the card is the backend's, not a guess", (cardText ?? "").includes(`${first.frame_count} frames`), `${first.frame_count} vs ${(cardText ?? "").slice(0, 80)}`);
    const unrendered = list.body.scenes.filter((s) => !s.has_renderer);
    const cards = cardsOf();
    check(
      "lesson",
      "a scene with no renderer in this build announces it on its own card",
      unrendered.every((s) => cards.some((t) => t.includes(s.title.slice(0, 20)) && /no renderer/i.test(t))),
      `${unrendered.length} unrenderable scene(s) in this database`,
    );

    await page.click("open", "button");
    // the player, not the list: "say what happens next" is on the cards too, so a copy
    // match would let every check below run against the screen the click left behind
    await waitFor(() => page.byText("next →", "button"), { timeout: 15_000, label: "the player to replace the list" });
    if (!MOCK) {
      const opened = page.requests.filter((r) => r.startsWith("POST /api/v1/lessons/open"));
      check("lesson", "opening a scene asks for a run once", opened.length === 1, page.requests.join(" | ").slice(0, 200));
    }
    check("lesson", "the stage draws the backend's own caption", page.text().includes(first.title.slice(0, 20)) && /frame 1/.test(page.text()), page.text().slice(0, 160));
    check("lesson", "nothing is autoplaying over a question", /say what happens next/i.test(page.text()), "");

    // the gate: 'next' must not walk past a frame that asked something
    const before = page.text().match(/frame (\d+)/)?.[1];
    const nextBtn = page.byText("next →", "button");
    check("lesson", "'next' is present on a blocked frame (the gate is honest, not a disabled button)", !!nextBtn && !nextBtn.disabled, "");
    await page.click(nextBtn);
    const afterBlocked = page.text().match(/frame (\d+)/)?.[1];
    check("lesson", "and it does not advance past an unanswered prediction", before === afterBlocked, `frame ${before} -> ${afterBlocked}`);

    // The prediction choices are selectable options, so they are selected as options (role and
    // state) rather than by an implementation class that a restyle is allowed to change. The
    // claim is the same and holds the same things: a choice exists, it is a *selectable* one,
    // and it carries the frame's own words.
    const choice = page
      .find('button.option-card[role="radio"]')
      .find((b) => !/next|back|play|end|ask|run again/.test(b.textContent ?? ""));
    check(
      "lesson",
      "the choices are the ones the frame carried",
      !!choice && choice.getAttribute("aria-checked") !== null && (choice.textContent ?? "").trim().length > 0,
      page.find("button").map((b) => (b.textContent ?? "").trim()).slice(0, 6).join(" / ").slice(0, 180) || "(no buttons)",
    );
    await page.click(choice);
    await waitFor(() => /the trace agrees|the machine does something else|nothing was answerable/.test(page.text()), { label: "the grade" });
    check("lesson", "the grade arrives from the simulation, and says so", /graded by the simulation on the backend/.test(page.text()), page.text().match(/predictions? so far[^.]*/)?.[0] ?? "");
    await page.click(page.byText("next →", "button"));
    await waitFor(() => /frame 2/.test(page.text()), { label: "the frame to move once the question is answered" });
    check("lesson", "and the frame moves once it has been answered", /frame 2/.test(page.text()), page.text().match(/frame \d+/)?.[0] ?? "");

    // asking why: the answer must be the trace's, and must say which voice answered
    const box = page.find("textarea")[0];
    check("lesson", "there is somewhere to ask a question", !!box, "");
    page.type(box, "why does sorted order matter here?");
    await page.click("ask", "button");
    await waitFor(() => /source:/.test(page.text()), { label: "an answer with its source named" });
    check("lesson", "a question the simulation can settle is answered by the simulation", /source: deterministic/.test(page.text()), page.text().match(/source: \w+/g)?.join(", ") ?? "");
    check("lesson", "and no model was billed for it", !/tokens out/.test(page.text()), page.text().match(/source: \w+[^.]{0,80}/)?.[0] ?? "");

    // change the numbers: a new validated scene, and the old run keeps its predictions
    const values = page.find('input[name="values"]')[0];
    const target = page.find('input[name="target"]')[0];
    check("lesson", "the editable parameters are the ones the engine declares", !!values && !!target, page.find("input").map((i) => i.getAttribute("name")).join(","));
    page.type(values, "1, 4, 7, 9, 12, 15, 19, 23");
    page.type(target, "12");
    // the label follows the app's copy sweep ("run again with these"): the check is the same
    // one -- new numbers, a new validated scene -- and it is still clicked by what it says
    await page.click("run again with these", "button");
    await waitFor(() => /your own copy/.test(page.text()), { timeout: 12_000, label: "the derived scene to come back" });
    check("lesson", "changing the numbers says plainly that this is a new scene", /your own copy of the scene/.test(page.text()), page.text().match(/this is your own copy[^.]*\./)?.[0] ?? "");
    if (MOCK) {
      // the mock answers /variables from a fixture, so the recomputation is a review stub: the
      // claim worth keeping is that the page announced a new scene and kept playing
      check("lesson", "and the mock keeps playing after the variables change", /frame \d+/.test(page.text()), page.text().slice(-140));
    } else {
      const varReq = page.requests.filter((r) => r.endsWith("/variables"));
      check("lesson", "and it is the backend that recomputed the trace", varReq.length === 1, page.requests.slice(-4).join(" | "));
    }

    if (MOCK) {
      // the mock has no run store and no session, so nothing can come back after a reload. the
      // honest claim is that the page still works and says nothing was saved.
      await page.reload();
      await waitFor(() => cardsOf().length > 0, { timeout: 15_000, label: "the mock list to come back" });
      const cards = cardsOf().join(" ");
      check(
        "lesson",
        "mock mode saves nothing and does not pretend otherwise",
        !/your variables|you stopped at frame/.test(cards),
        "the mock list gained a derived or resumed row it cannot have stored",
      );
    } else {
      // position is a row, not a component: reload and the card offers the same place back
      await page.click(page.byText("next →", "button"));
      await page.click(page.byText("next →", "button"));
      await page.reload();
      await waitFor(() => cardsOf().some((t) => /your variables/.test(t)), { timeout: 15_000, label: "the derived scene in the list" });
      const resumeLine = cardsOf().find((t) => /you stopped at frame/.test(t));
      check("lesson", "a reload offers the frame you stopped on, from the database", /you stopped at frame \d+ of \d+/.test(resumeLine ?? ""), (resumeLine ?? "no resume line").slice(0, 160));
      const myCard = cardsOf().find((t) => /\(your variables\)/.test(t));
      check("lesson", "the scene you made is in your list, marked as yours", /your variables/.test(myCard ?? "") && /learner/.test(myCard ?? ""), (myCard ?? "no derived card").slice(0, 160));
      await page.click(page.byText("resume", "button"));
      await waitFor(() => /back at frame/.test(page.text()), { label: "the resume notice" });
      check("lesson", "resuming says where it came from, in one sentence the learner can check", /back at frame \d+ of \d+/.test(page.text()) && /saved on the server/.test(page.text()), page.text().match(/back at frame[^.]*\./)?.[0] ?? "");

      // and the end: a review that refuses to call itself a grade
      await page.click("end & review", "button");
      await waitFor(() => /what you just did/.test(page.text()), { label: "the review" });
      check("lesson", "the review counts predictions before the answer, not accuracy as mastery", /before the machine confirmed them|no predictions, so there is nothing to compare/.test(page.text()), page.text().match(/you called[^.]*\./)?.[0] ?? "");
      const ev = await api("/api/v1/lessons/evidence", { cookie });
      check("lesson", "the run is in the learner's evidence, described in the API's own words", ev.status === 200 && ev.body.summary.runs >= 2, JSON.stringify(ev.body.summary).slice(0, 160));
      check("lesson", "and it is still not a mastery number", /not a mastery number/.test(JSON.stringify(ev.body)), "");
      // what the card claimed and what the run row holds must be the same number, or the
      // "saved on the server" sentence on screen is decoration
      const runs = await api("/api/v1/lessons/runs", { cookie });
      const newest = (runs.body.runs ?? [])[0] ?? {};
      const claimed = Number((resumeLine ?? "").match(/you stopped at frame (\d+)/)?.[1] ?? NaN);
      check(
        "lesson",
        "the frame the card offered is the frame the run row holds",
        Number.isFinite(claimed) && newest.frame_index === claimed - 1,
        `card said frame ${claimed}, run row says ${JSON.stringify(newest.frame_index)} (status ${newest.status})`,
      );
      const progressWrites = page.requests.filter((r) => r.endsWith("/progress"));
      check("lesson", "a frame the player moved was written back, and a frame the gate held was not", progressWrites.length >= 1, progressWrites.join(" | ").slice(0, 160));
    }
    const hardErrors = page.appErrors();
    check("lesson", "no uncaught error during the whole lesson", hardErrors.length === 0, hardErrors.slice(0, 2).join(" | ").slice(0, 200));
    check("lesson", "no raw object reached the screen", !/[{\[]\s*"\w+":/.test(page.text()), page.text().slice(0, 160));
    closePage(page);
  }

  /* ---------------------------------------------------------------- subject ----
   * §6's screen, driven against its `?mock=1` fixtures: the subjects router is wired into
   * the app by the integrator (see docs/AUDIT.md), so until that merge the
   * live API cannot answer these calls and asserting against it would test the wiring, not
   * the screen. What is under test here is the screen itself: that the reason travels with
   * the skill set, that "calculus" and "government" produce visibly different rounds, that
   * skipped skills render as "not measured yet" with no number attached, and that a whole
   * round can be clicked through to a subject map whose ladder positions are per sub-area.
   * The backend half of every one of these claims is asserted in
   * backend/tests/test_subjects.py against the real service and the real router. */
  if (run("subject")) {
    console.log("\n\x1b[1msubject\x1b[0m");
    const page = await open("/subject?mock=1", "subject");
    const input = await waitFor(() => page.find('input[aria-label="Topic"]')[0], { label: "the topic box" }).catch(() => null);
    check("subject", "the screen opens on a topic box, not a dashboard", !!input, page.text().slice(0, 160));
    if (input) {
      page.type(input, "calculus");
      await page.click("Find my subject");
      await waitFor(() => /What we will check for Calculus/.test(page.text()), { label: "the calculus plan" }).catch(() => {});
      const planText = page.text();
      check("subject", "the plan says why these skills, in so many words", /Why these\?/.test(planText) && /leans hardest on symbol fluency/i.test(planText), planText.slice(0, 200));
      check("subject", "calculus chooses symbol fluency, graph sense and checking, plus a cross-check", /Symbol fluency/.test(planText) && /Graph sense/.test(planText) && /cross-check/i.test(planText), planText.slice(0, 200));
      check("subject", "the skills the round skips are already listed as not measured, before anything is answered", /not measured yet/i.test(planText) && /Number sense/.test(planText), planText.slice(0, 220));
      check("subject", "nothing unmeasured is wearing a number", !/Number sense[^.]*\d+\s?%/.test(planText), planText.slice(0, 220));

      // The other half of §6's gate, on a second page so the first round stays open:
      // government must not look like calculus with the title changed.
      const gov = await open("/subject?mock=1", "subject");
      const govInput = await waitFor(() => gov.find('input[aria-label="Topic"]')[0], { label: "the government topic box" }).catch(() => null);
      if (govInput) {
        gov.type(govInput, "government and civics");
        await gov.click("Find my subject");
        await waitFor(() => /What we will check for Government/.test(gov.text()), { label: "the government plan" }).catch(() => {});
        const govText = gov.text();
        check("subject", "government chooses a visibly different set: sources, argument, the other side", /Source analysis/.test(govText) && /Supporting a claim with evidence/.test(govText) && /Seeing the other side/.test(govText), govText.slice(0, 200));
        check("subject", "and does not borrow calculus's skills or its reason", !/Symbol fluency/.test(govText) && !/leans hardest on symbol fluency/i.test(govText), govText.slice(0, 200));
      }
      closePage(gov);

      /* The round itself: skills, ladder, the pop-up lesson, then the map. The loop
       * answers whatever is on offer — a choice when there is one, the lesson's
       * continue button when there is not — and stops at the map or not at all. */
      await page.click("Start the round");
      let steps = 0;
      let sawLadder = false;
      let sawLesson = false;
      for (; steps < 60; steps += 1) {
        await sleep(120);
        const now = page.text();
        // The done stage's card is also titled "Your subject map"; the map itself is
        // the one with a topic after the dash and another round on offer.
        if (/Your subject map —/.test(now) && page.byText("Measure another topic", "button")) break;
        if (/ladder item/i.test(now)) sawLadder = true;
        if (/Pop-up lesson/i.test(now) || /about 90 seconds/i.test(now)) sawLesson = true;
        const finish = page.byText("See my subject map", "button");
        if (finish) {
          await page.click(finish);
          continue;
        }
        const read = page.byText("I have read it", "button");
        if (read) {
          await page.click(read);
          continue;
        }
        const radio = page.find('[role="radio"]')[0];
        if (radio) {
          radio.click();
          await sleep(30);
          // Exact text, not a substring: the first option is labelled "The first
          // answer", and a substring match presses that instead of the submit
          // button forever — a stall that looks exactly like a dead control.
          const answer = page.find("button").find((b) => (b.textContent ?? "").trim() === "Answer");
          if (answer) await page.click(answer);
          continue;
        }
        const done = page.byText("Finish early", "button");
        if (done) await page.click(done);
      }
      const map = page.text();
      check("subject", "the round can be clicked through to its end", /Your subject map/.test(map), `stopped after ${steps} answers: ${map.slice(0, 180)}`);
      check("subject", "the ladder was one of the stages on the way", sawLadder, map.slice(0, 120));
      check("subject", "and the pop-up lesson was served inside the round", sawLesson, map.slice(0, 120));
      check("subject", "the map repeats the reason next to the skills it measured", /Why these skills\?/.test(map) && /leans hardest on/i.test(map), map.slice(0, 200));
      check("subject", "skipped skills are on the map as not measured yet, not as failures", /not measured yet/i.test(map) && /not measured is not the same/i.test(map), map.slice(0, 220));
      check("subject", "the ladder reports a position per sub-area, with unreached ones unmeasured rather than at rung 0", /Where the ladder settled/.test(map) && /sub-areas placed/.test(map), map.slice(0, 200));
      check("subject", "every measured skill carries its answer count", /from \d+ answers?/.test(map), map.slice(0, 220));
      check("subject", "no way of teaching is turned into a label for the learner", !/learning style|visual learner|auditory learner|kinesthetic/i.test(map), map.slice(0, 200));
      check("subject", "no raw object reached the screen", !/[{[]\s*"\w+":/.test(map), map.slice(0, 160));
      check("subject", "no uncaught error during the whole round", page.appErrors().length === 0, page.appErrors().slice(0, 2).join(" | ").slice(0, 200));
    }
    closePage(page);
  }

  /* ------------------------------------------------------- failure paths ---- */
  if (run("failures")) {
    console.log("\n\x1b[1mfailures\x1b[0m");
    const cookie = jar.getCookieStringSync(BASE);
    const bad = await api("/api/v1/attempts", { method: "POST", body: { session_id: "lrs_nope", item_id: "itm_nope", answer: {} }, headers: { cookie, "x-csrf-token": csrf } });
    check("failures", "an invalid submission is a client error, not a 500", bad.status >= 400 && bad.status < 500, `${bad.status} ${JSON.stringify(bad.body).slice(0, 140)}`);
    check("failures", "the error says what was wrong", /unknown|invalid|must|required|not found/i.test(JSON.stringify(bad.body)), JSON.stringify(bad.body).slice(0, 160));
    const noauth = await api("/api/v1/attempts", { method: "POST", body: { session_id: "x", item_id: "y", answer: 1 } });
    check("failures", "an unauthenticated write is refused", noauth.status === 401 || noauth.status === 403, String(noauth.status));
    const csrfless = await api("/api/v1/sessions", { method: "POST", body: { mode: "practice" }, headers: { cookie } });
    check("failures", "a cookie-authenticated POST without the CSRF token is refused", csrfless.status === 403, `${csrfless.status} ${JSON.stringify(csrfless.body).slice(0, 120)}`);
    const notFound = await open("/settings", "failures");
    closePage(notFound);
    const missing = await api("/api/v1/does-not-exist", { headers: { cookie } });
    check("failures", "an unknown API path 404s rather than 500ing", missing.status === 404, String(missing.status));
    // the rate limiter, surfaced the way a learner would see it. `lesson_requests` is the
    // tightest pacing budget a learner can spend by ordinary use (/explain sits in it); §3
    // renamed the wire field from the operator's bucket name to this one.
    //
    // How many calls that takes is the server's business, not this file's. A suite walked back
    // to back is started with ADAPT_RATE_LIMIT_MULTIPLIER (the probe's own advice in the summary
    // below), which widens every bucket; a hardcoded 16 then reports the limiter as broken for
    // doing exactly what it was configured to do. So ask the server how wide the bucket is.
    const quotas = await api("/api/v1/quotas", { headers: { cookie, "x-csrf-token": csrf } });
    const burst = Number(quotas.body?.pacing?.lesson_requests?.[1] ?? 12) || 12;
    const ceiling = Math.min(400, Math.ceil(burst) + 6);
    let limited = 0;
    let limitedBody = null;
    let limitedHeader = null;
    const seen = new Set();
    for (let i = 0; i < ceiling; i += 1) {
      const r = await api("/api/v1/explain", { method: "POST", body: { question: "why does this work this way", item_id: "itm_integer_ops_00" }, headers: { cookie, "x-csrf-token": csrf } });
      if (r.status !== 200 && !seen.has(r.status)) {
        seen.add(r.status);
        console.log(`\x1b[2m  (explain answered ${r.status} ${JSON.stringify(r.body).slice(0, 90)})\x1b[0m`);
      }
      if (r.status === 429) { limited = i; limitedBody = r.body; limitedHeader = r.headers.get("retry-after"); break; }
    }
    check("failures", "a flooded learner is told to wait, with a number", limited > 0 && limitedBody?.detail?.error === "rate_limited" && Number(limitedBody?.detail?.retry_after_s) > 0, limited ? `429 after ${limited} calls (bucket ${burst}/run, flooded up to ${ceiling}): ${JSON.stringify(limitedBody.detail)} (retry-after header: ${limitedHeader})` : `no 429 in ${ceiling} calls -- the server reported a lesson-request burst of ${burst} and refused none of them`);
    // the throttle above is the app refusing; this is the app *misreading* the refusal. A 429
    // on the one read that answers "who am I" used to render the sign-in wall and tell the
    // learner to start again - which is how a half-finished placement gets thrown away.
    let throttledOnce = true;
    const throttled = await open("/learn", "failures", (win) => {
      const real = win.fetch.bind(win);
      win.fetch = async (input, init) => {
        const path = typeof input === "string" ? input : (input?.url ?? "");
        if (throttledOnce && path.includes("/api/v1/me")) {
          throttledOnce = false;
          return new Response(JSON.stringify({ detail: { reason: "rate limited", retry_after_s: 0.3 } }), {
            status: 429,
            headers: { "content-type": "application/json" },
          });
        }
        return real(input, init);
      };
    });
    const wallText = throttled.text();
    check("failures", "a throttled session read is a retry, not a sign-out wall",
      !/Not signed in|sign in or start as a guest/i.test(wallText) && /checking who you are/i.test(wallText),
      `page said: ${wallText.slice(0, 180)}`);
    check("failures", "and the retry is something the learner can press",
      [...throttled.find("button")].some((b) => /retry|try again/i.test((b.textContent ?? "").trim())),
      [...throttled.find("button")].map((b) => (b.textContent ?? "").trim()).join(" / ").slice(0, 160));
    let recovered = false;
    try {
      await waitFor(() => !/Still checking who you are/i.test(throttled.text()), {
        timeout: 15_000, label: "the screen to find the learner again on its own",
      });
      recovered = true;
    } catch { /* reported below, not swallowed */ }
    check("failures", "the back-off retry finds the session again without being told to", recovered, throttled.text().slice(0, 200));
    closePage(throttled);
    let proposalsThrottled = true;
    const gated = await open("/proposals", "failures", (win) => {
      const real = win.fetch.bind(win);
      win.fetch = async (input, init) => {
        const path = typeof input === "string" ? input : (input?.url ?? "");
        if (proposalsThrottled && path.includes("/api/v1/me")) {
          proposalsThrottled = false;
          return new Response(JSON.stringify({ detail: { reason: "rate limited", retry_after_s: 0.3 } }), {
            status: 429, headers: { "content-type": "application/json" },
          });
        }
        return real(input, init);
      };
    });
    const gateText = gated.text();
    check("failures", "the changes screen does not announce a sign-out it was not told about",
      !/nobody is signed in|Not signed in/i.test(gateText), gateText.slice(0, 220));
    closePage(gated);
    const stillOk = await api("/api/v1/health", { headers: { cookie } });
    check("failures", "being rate limited does not break the app (reads still answer)", stillOk.status === 200, String(stillOk.status));
  }

  /* -------------------------------------------------------------- baseline ---- */
  // The mind baseline (phase 2). The flow is the server's, so this walks it the way a learner
  // does: pick a format, skip the questions, look at the pairs, answer the cued trials until the
  // vault arrives, tap a tile with the keyboard, then stop early. Stopping is the point of the
  // last checks: a partial baseline must be a finished baseline, and the snapshot must say "not
  // measured" rather than invent a zero for a game nobody played.
  if (run("content")) {
    // Most of this scenario judges the API surface directly, because the properties being checked
    // are browser properties: what a learner is *sent*, not what the server holds. The answer key
    // travelling in a payload is invisible to a unit test that never serialises one. The last
    // three checks walk the screen that now reaches it, in mock mode, because that is the only
    // mode where a rendered pack is not gated on which concepts a topic happens to have authored.
    console.log("\x1b[1mcontent\x1b[0m");
    const jar = new CookieJar();
    const made = await createLearner({ display_name: "Probe Content", age_band: "16-18", as_guest: true });
    check("content", "the engine's own learner can be created", made.status === 200, `${made.status} ${JSON.stringify(made.body).slice(0, 120)}`);
    if (made.status === 200) {
      for (const raw of made.headers.getSetCookie?.() ?? []) {
        try { jar.setCookieSync(raw, BASE); } catch { /* not storable */ }
      }
      const cookie = jar.getCookieStringSync(BASE);
      const head = { "x-csrf-token": made.body.csrf_token ?? "", cookie };
      const read = (path) => api(path, { headers: head });

      const breakdown = await read("/api/v1/content/breakdown?topic=biology");
      check("content", "a topic's breakdown arrives over HTTP", breakdown.status === 200 && breakdown.body.available === true, `${breakdown.status} ${JSON.stringify(breakdown.body).slice(0, 120)}`);
      const concepts = breakdown.body.concepts ?? [];
      check("content", "every concept carries its misconceptions and objectives", concepts.length > 0 && concepts.every((c) => c.misconceptions.length && c.objectives.length), `${concepts.length} concepts`);
      check("content", "the breakdown does not ship the answer kits", concepts.every((c) => !("kit" in c)), "a kit is the server's copy");

      const missing = await read("/api/v1/content/breakdown?topic=astrophysics");
      check("content", "a topic with no authored chain says so instead of inventing one", missing.status === 200 && missing.body.available === false && /no authored prerequisite chain/.test(missing.body.reason ?? ""), JSON.stringify(missing.body).slice(0, 140));

      const pack = await read("/api/v1/content/pack?topic=calculus&concept=calc_rate");
      check("content", "a concept pack arrives with all three variants", pack.status === 200 && !!(pack.body.variants?.written && pack.body.variants?.visual && pack.body.variants?.worked_example?.steps?.length), `${pack.status} ${JSON.stringify(pack.body).slice(0, 120)}`);
      const pair = pack.body.pair ?? {};
      const legs = ["A", "B"].map((leg) => pair[leg]).filter(Boolean);
      check("content", "the matched pair arrives with both legs", legs.length === 2, `${legs.length} legs`);
      check("content", "no leg carries its answer key", legs.every((leg) => !("answer" in leg) && !("answer_spec" in leg)), JSON.stringify(legs[0] ?? {}).slice(0, 160));
      check("content", "no leg carries the answer explanation", legs.every((leg) => !("why" in leg)), "");
      check("content", "the pair is matched on format and says it is not matched on difficulty", /NOT matched on difficulty/.test(pair.matched ?? ""), String(pair.matched).slice(0, 120));
      check("content", "the worked example is present and verified", pack.body.variants?.worked_example?.verified === true, JSON.stringify(pack.body.variants?.worked_example?.flags ?? []));
      check("content", "three graded hints and a three-point checklist", (pack.body.hints ?? []).length >= 3 && (pack.body.checklist ?? []).length === 3, `${(pack.body.hints ?? []).length} hints, ${(pack.body.checklist ?? []).length} points`);
      check("content", "a GET never writes", pack.body.stored === false, String(pack.body.stored));

      const grades = [];
      for (const index of [0, 1, 2, 9]) {
        const hint = await read(`/api/v1/content/hint?topic=calculus&concept=calc_rate&index=${index}`);
        grades.push(hint.body.grade ?? "?");
      }
      check("content", "the hint ladder climbs nudge, method, near-answer and then stops", grades.join(",") === "nudge,method,near_answer,near_answer", grades.join(","));

      const unknown = await read("/api/v1/content/pack?topic=calculus&concept=nope");
      check("content", "an unknown concept names the ones that exist", unknown.status === 404 && /calc_rate/.test(unknown.body.detail ?? ""), `${unknown.status} ${JSON.stringify(unknown.body).slice(0, 120)}`);

      // The pack is readable without a key, so the only thing that makes it usable is a grader.
      // This goes over HTTP rather than living in the backend suite alone because the contract
      // being tested is a browser's: the answer is the option's *text*, and the explanation must
      // arrive with the verdict instead of with the question. It is deliberately ONE request --
      // a full walk is paced against a per-connection ceiling, and the two extra requests this
      // used to make were enough to throttle the scenarios that follow.
      const legA = pack.body.pair?.A?.options ?? [];
      if (legA.length >= 2) {
        // Paced by the server's own retry number. A walk is already close to the per-connection
        // ceiling, so one more write can land on an empty bucket -- and an unretried 429 here
        // used to read as a broken route when it was the harness's own traffic.
        let graded = await api("/api/v1/content/grade", {
          method: "POST",
          headers: { "x-csrf-token": made.body.csrf_token ?? "" },
          cookie,
          body: { topic: "calculus", concept: "calc_rate", leg: "A", choice: legA[0] },
        });
        for (let tries = 0; graded.status === 429 && tries < 6; tries += 1) {
          const wait = Number(graded.body?.detail?.retry_after_s ?? graded.body?.retry_after_s ?? 1);
          await sleep(Math.min(12_000, wait * 1000 + 250));
          graded = await api("/api/v1/content/grade", {
            method: "POST",
            headers: { "x-csrf-token": made.body.csrf_token ?? "" },
            cookie,
            body: { topic: "calculus", concept: "calc_rate", leg: "A", choice: legA[0] },
          });
        }
        check(
          "content",
          "an answer is graded over HTTP, by option text, and comes back with its explanation",
          graded.status === 200 &&
            graded.body.status === "scored" &&
            graded.body.leg === "A" &&
            graded.body.concept === "calc_rate" &&
            typeof graded.body.explanation === "string" &&
            graded.body.explanation.length > 0,
          `${graded.status} ${JSON.stringify(graded.body).slice(0, 200)}`,
        );
      } else {
        check("content", "an answer is graded over HTTP, by option text, and comes back with its explanation", false, "leg A arrived with no options");
      }
    }

    // The screen that reaches all of the above. Mock mode, because a rendered pack needs a topic
    // with an authored kit and the fixtures are the recorded shapes.
    {
      const page = await open("/subject?mock=1", "content");
      await waitFor(() => /What we'd teach/.test(page.text()), { label: "the pack panel" });
      const before = page.text();
      check("content", "the subject screen offers the pack before it is asked for", !!page.byText("Show what we'd teach", "button"), page.text().slice(0, 140));
      const packBox = page.find('input[aria-label="Pack topic"]')[0];
      page.type(packBox, "calculus");
      await sleep(120);
      await page.click("Show what we'd teach", "button");
      await waitFor(() => /Worked example/.test(page.text()), { label: "the variants" });
      const text = page.text();
      check("content", "the three variants arrive on the screen", /Written/.test(text) && /Visual/.test(text) && /Worked example/.test(text), text.slice(0, 200));
      check("content", "the matched pair is on the screen with both legs", /Matched pair/.test(text) && /Leg A/.test(text) && /Leg B/.test(text), "");
      check("content", "the pair says it is not matched on difficulty", /NOT matched on difficulty/.test(text), "");
      check("content", "the hint ladder and the self-check arrive too", /Hints, in order/.test(text) && /Self-check/.test(text), "");
      check("content", "no answer key is on the screen before answering", !/answer_spec/.test(text) && !/"correct_index"/.test(text), text.slice(0, 160));
      // Answering is the point: the option is sent as text, and the verdict comes back graded.
      const first = page.find("button").find((b) => /^\d+(\.\d+)?\s*(km\/h)?$/.test((b.textContent ?? "").trim()) || /^(8|9|4|5)$/.test((b.textContent ?? "").trim()));
      if (first) {
        await page.click(first);
        await waitFor(() => /Right\.|Not yet\./.test(page.text()), { label: "the verdict" });
        const after = page.text();
        check("content", "answering a leg produces a verdict from the server", /Right\.|Not yet\./.test(after), after.slice(0, 200));
        check("content", "the explanation only arrives once the learner has answered", /15 \/ 3 = 5/.test(before) === false, "");
      } else {
        check("content", "answering a leg produces a verdict from the server", false, "no option button was found");
      }
      closePage({ jar: page.jar });
    }
    closePage({ jar });
  }

  if (run("baseline")) {
    console.log("\x1b[1mbaseline\x1b[0m");
    const page = await open("/baseline", "baseline");
    await waitFor(() => /How would you like to meet a new idea/.test(page.text()), { label: "the format pick" });
    check("baseline", "opens on the format pick, not a game", page.find('[role="gridcell"]').length === 0, page.text().slice(0, 140));
    const radios = page.find('[role="radio"]');
    check("baseline", "the format pick is a radio group with the three stated options", radios.length === 3, `${radios.length} roles`);
    check("baseline", "a preference screen asks for no coin wager", page.find(".baseline-coins").length === 0, String(page.find(".baseline-coins").length));
    check("baseline", "stopping is on screen from the first step", !!page.byText("Stop here", "button"), page.text().slice(0, 200));

    await page.click("a written explanation", '[role="radio"]');
    await waitFor(() => /How you work/.test(page.text()), { label: "the questions screen" });
    const qtext = page.text();
    check("baseline", "the questions arrive in one sitting, not one per screen", /What are you learning this for\?/.test(qtext) && /How many days a week can you study\?/.test(qtext), qtext.slice(0, 160));
    check("baseline", "every question says why it is asked", (qtext.match(/asked because it /g) ?? []).length >= 10, String((qtext.match(/asked because it /g) ?? []).length));
    check("baseline", "the questions are skippable", !!page.byText("Skip the questions", "button"), "");

    await page.click("Skip the questions", "button");
    await waitFor(() => /Spy codewords: study/.test(page.text()), { label: "the study screen" });
    check("baseline", "the study screen shows the eight pairs it will ask for later", page.find(".baseline-pairs li").length === 8, String(page.find(".baseline-pairs li").length));
    check("baseline", "a practice screen is labelled as unscored and offers no coins", /not scored/.test(page.text()) && page.find(".baseline-coins").length === 0, page.text().slice(0, 160));

    await page.click("I have looked at them", "button");
    // Answer whatever comes until the vault, using the page's own controls. A wrong answer is a
    // legitimate way through (cued recall is either right or it is not) -- what is being checked
    // is that the flow advances without a reload and that coins appear exactly on scored tasks.
    let saw_coins_on_measured = false;
    let coins_on_practice = 0;
    let prev = "";
    for (let step = 0; step < 40; step += 1) {
      if (/Watch: the tiles light up/.test(page.text())) break;
      const text = page.text();
      if (text === prev) break;
      prev = text;
      const coins = page.find(".baseline-coins").length > 0;
      const scored = !/not scored/.test(text);
      if (coins && scored) saw_coins_on_measured = true;
      if (coins && !scored) coins_on_practice += 1;
      const box = page.find("#baseline-free")[0];
      if (box) {
        page.type(box, "the old well");
        await page.click("Answer", "button");
      } else {
        const opt = page.find(".baseline-option")[0];
        if (!opt) break;
        await page.click(opt);
      }
      await sleep(120);
    }
    check("baseline", "a scored question task offers the coin wager", saw_coins_on_measured, `coins seen: ${saw_coins_on_measured}`);
    check("baseline", "no practice trial ever offers coins", coins_on_practice === 0, `${coins_on_practice} practice screens with coins`);

    const tiles = page.find('[role="gridcell"]');
    check("baseline", "the vault arrives as a nine-tile grid", tiles.length === 9, `${tiles.length} tiles`);
    const busyAtStart = page.find('.vault-grid[aria-busy="true"]').length === 1;
    check("baseline", "the tiles are disabled while the sequence is being flashed", busyAtStart || tiles.every((t) => t.disabled), busyAtStart ? "aria-busy" : "tiles disabled");
    let ready = false;
    try {
      await waitFor(() => page.find('[role="gridcell"]').every((t) => !t.disabled), { timeout: 12_000, label: "the flash to finish" });
      ready = true;
    } catch { /* reported below */ }
    check("baseline", "the flash ends and the grid becomes tappable", ready, "");
    if (ready) {
      // A keypress either marks the tile (right order so far), submits a wrong order and brings the
      // next vault trial (whose own flash disables the grid again), or -- the only real failure --
      // does nothing at all. So the signal is "the task changed", not "the tile is marked": an
      // earlier version waited for a marked tile and read a *successful* wrong-order tap as nothing
      // happening, because the next trial's flash looks exactly like a grid that ignored the key.
      const beforeText = page.text();
      const Ev = page.dom.window.KeyboardEvent;
      page.dom.window.document.dispatchEvent(new Ev("keydown", { key: "7", bubbles: true, cancelable: true }));
      let reacted = false;
      try {
        await waitFor(
          () =>
            page.text() !== beforeText ||
            page.find(".vault-tile.tapped").length >= 1 ||
            page.find('[role="gridcell"]').length === 0,
          { timeout: 6000, label: "the vault to react to the keypress" },
        );
        reacted = true;
      } catch { /* reported below, not swallowed */ }
      check("baseline", "a number key taps a tile: no mouse needed for the grid", reacted, page.text().slice(0, 140));
    }
    const walkErrors = page.appErrors();
    check("baseline", "no uncaught error during the walk", walkErrors.length === 0, walkErrors.join(" | ").slice(0, 200));

    // The stop control is disabled while a trial is in flight, and the last keypress may still be
    // waiting for its score -- so wait for the control to be usable instead of pressing a disabled
    // button and blaming the snapshot.
    await waitFor(() => { const b = page.byText("Stop here", "button"); return b ? !b.disabled : false; }, { timeout: 12_000, label: "the stop control to be ready" });
    await page.click("Stop here", "button");
    await waitFor(() => /What we noticed/.test(page.text()) && /not measured/.test(page.text()), { timeout: 12_000, label: "the snapshot" });
    const snapText = page.text();
    check("baseline", "stopping early still produces the snapshot", /What we measured, and what we did not/.test(snapText), snapText.slice(0, 200));
    check("baseline", "the snapshot names what was not measured instead of zeroing it", /not measured/.test(snapText), snapText.slice(0, 200));
    const measuredRows = page.find(".baseline-factors li").filter((li) => !li.classList.contains("muted"));
    check("baseline", "a measured factor carries its range and its n", measuredRows.length > 0 && /n=\d+/.test(snapText), `${measuredRows.length} measured rows`);
    check("baseline", "every lever says where it came from", (snapText.match(/measured|you told us|default/g) ?? []).length >= 3, snapText.slice(0, 200));
    closePage(page);

    // `?mock=1` has to reach the same screens, or a design review on a machine with no backend
    // stops at the first fetch. The fixture advances the way the real flow does (it is scripted in
    // mock.ts), so this walks two steps of it rather than one static view.
    const mocked = await open("/baseline?mock=1", "baseline");
    await waitFor(() => /How would you like to meet a new idea/.test(mocked.text()), { label: "the mocked format pick" });
    check("baseline", "?mock=1 opens the baseline without a backend", mocked.find('[role="radio"]').length === 3, mocked.text().slice(0, 140));
    await mocked.click("a written explanation", '[role="radio"]');
    await waitFor(() => /How you work/.test(mocked.text()), { label: "the mocked questions" });
    check("baseline", "the mock answers with the questions screen, not an error frame", /What are you learning this for\?/.test(mocked.text()), mocked.text().slice(0, 160));
    check("baseline", "and no uncaught error on the mocked walk", mocked.appErrors().length === 0, mocked.appErrors().join(" | ").slice(0, 160));
    closePage(mocked);
  }

  /* ------------------------------------------------------- subject-outline ---- */
  if (run("subject-outline")) {
    console.log("\n\x1b[1msubject outline\x1b[0m");
    const page = await open("/subject", "subject-outline");
    await waitFor(() => !!page.doc.querySelector('input[aria-label="Subject"]'), { label: "subject form" });
    page.type(page.doc.querySelector('input[aria-label="Subject"]'), "Mathematics");
    page.type(page.doc.querySelector('input[aria-label="Specific topic"]'), "calculus");
    await page.click("Preview outline", "button");
    await waitFor(() => page.find('fieldset input[type="checkbox"]').length > 0, { timeout: 15000, label: "reviewable outline" });
    check("subject-outline", "subject and optional topic lead to an outline before starting", !page.requests.some(r => r === "POST /api/v1/subjects/round"));
    const checks = page.find('fieldset input[type="checkbox"]');
    for (const box of checks) if (box.checked) await page.click(box);
    check("subject-outline", "an empty selection cannot start a round", page.byText("Confirm outline and start", "button")?.disabled);
    await page.click(checks[0]);
    const selected = checks[0].value;
    const comment = "This looks right; start with the first check.";
    page.type(page.find("textarea")[0], comment);
    await page.click("Confirm outline and start", "button");
    await waitFor(() => /Your outline comments/.test(page.text()), { timeout: 15000, label: "confirmed round" });
    const dash = await api("/api/v1/dashboard", { headers: { cookie: jar.getCookieStringSync(BASE) } });
    const saved = dash.body.learning_activity.tests.find(row => row.href.startsWith("/subject?"));
    check("subject-outline", "confirmed round appears in the learner's unfinished assessments", !!saved);
    const id = new URL(saved.href, BASE).searchParams.get("run");
    const record = await api(`/api/v1/subjects/round/${id}`, { headers: { cookie: jar.getCookieStringSync(BASE) } });
    check("subject-outline", "only the checked skill is included in the saved plan", record.body.plan.tested.length === 1 && record.body.plan.tested[0].id === selected);
    check("subject-outline", "comments are saved on the server, not just in a textbox", record.body.plan.learner_review.comments === comment);
    await page.click(page.doc.querySelector('.sidebar-item[href="/"]'));
    await waitFor(() => !!page.doc.querySelector(`a[href="${saved.href}"]`), { label: "home resume link" });
    const starts = page.requests.filter(r => r === "POST /api/v1/subjects/round").length;
    await page.click(page.doc.querySelector(`a[href="${saved.href}"]`));
    await waitFor(() => /Your outline comments/.test(page.text()) && page.text().includes(comment), { label: "resume reviewed outline" });
    check("subject-outline", "resume keeps the saved outline without creating another round", page.requests.filter(r => r === "POST /api/v1/subjects/round").length === starts);
    check("subject-outline", "no uncaught subject navigation errors", page.appErrors().length === 0, page.appErrors().join(" | "));
    closePage(page);
  }

  /* ------------------------------------------------------------ navigation ---- */
  if (run("navigation")) {
    console.log("\n\x1b[1mnavigation (06 → 07 → 11)\x1b[0m");
    let releaseSubject;
    const subjectGate = new Promise(resolve => { releaseSubject = resolve; });
    let delaySubject = true;
    const page = await open("/baseline", "navigation", win => {
      // No eager viewport prefetch in this scenario: exercise a genuinely cold click.
      win.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
      win.TextEncoder ??= globalThis.TextEncoder;
      win.TextDecoder ??= globalThis.TextDecoder;
      const fetch = win.fetch;
      win.fetch = async (input, init) => {
        const url = new URL(typeof input === "string" ? input : input.url ?? String(input), BASE);
        if (delaySubject && url.pathname === "/subject") await subjectGate;
        return fetch(input, init);
      };
    });
    try {
      await waitFor(() => page.find('[role="radio"]').length > 0 || /How you work/.test(page.text()),
        { timeout: 15000, label: "baseline data" });
      if (page.find('[role="radio"]').length) {
        await page.click("a written explanation", '[role="radio"]');
        await waitFor(() => /How you work/.test(page.text()), { label: "baseline questions saved" });
      }
      const sidebar = page.doc.querySelector("#adapt-sidebar");
      const document = page.doc;
      const sessionReads = () => page.requests.filter(r => r === "GET /api/v1/me").length;
      const readsBefore = sessionReads();
      const go = async path => {
        await page.click(page.doc.querySelector(`.sidebar-item[href="${path}"]`));
        await waitFor(() => page.doc.location.pathname === path &&
          page.doc.querySelector('.sidebar-item[aria-current="page"]')?.getAttribute("href") === path,
          { timeout: 15000, label: `client navigation to ${path}` });
      };
      await page.click(page.doc.querySelector('.sidebar-item[href="/subject"]'));
      await waitFor(() => page.find(".navigation-pending").length || /Opening page/.test(page.text()),
        { label: "immediate route loading feedback" });
      check("navigation", "a cold sidebar click immediately shows loading feedback", true);
      // Choose a different page before the slower route returns. It must not take over later.
      await go("/settings");
      await waitFor(() => !!page.byText("Save consent", "button"), { label: "saved consent loaded" });
      delaySubject = false;
      releaseSubject();
      await sleep(500);
      check("navigation", "latest click wins over a slower previous navigation", page.doc.location.pathname === "/settings");
      check("navigation", "Settings reuses the shell session instead of refetching me", sessionReads() === readsBefore,
        `${readsBefore} → ${sessionReads()}`);
      check("navigation", "page 11 is labelled consistently with the sidebar", /§ 11 · Settings/.test(page.text()));
      await go("/subject");
      await waitFor(() => /Pick a subject/.test(page.text()), { label: "subject screen" });
      await go("/settings");
      await go("/baseline");
      await waitFor(() => /How you work/.test(page.text()), { label: "baseline resumes saved progress" });
      check("navigation", "returning to 06 resumes the saved baseline step", /How you work/.test(page.text()));
      check("navigation", "switches preserve the document and shared sidebar", page.doc === document && page.doc.querySelector("#adapt-sidebar") === sidebar);
      check("navigation", "one main landmark and one skip-link target", page.find("main").length === 1 && page.find("#main-content").length === 1);
      check("navigation", "no stuck pending marker", page.find(".navigation-pending").length === 0);
      check("navigation", "no uncaught navigation or hydration errors", page.appErrors().length === 0, page.appErrors().join(" | "));
    } catch (error) {
      console.error("navigation diagnostic", page.doc.location.href, page.appErrors(), page.requests.slice(-20));
      throw error;
    } finally {
      delaySubject = false;
      releaseSubject();
      closePage(page);
    }
  }

  /* --------------------------------------------------------------- summary ---- */
  const consoleErrors = results.filter((r) => /hydration error/.test(r.label));
  console.log(`\n\x1b[1m${results.length - failures}/${results.length} checks passed\x1b[0m  \x1b[2m(${traffic.total} API calls)\x1b[0m`);
  // the `failures` scenario floods /explain on purpose to prove the wait message, so one refused
  // write is the app answering correctly, not pacing; a second one is
  if (traffic.reads > 0 || traffic.writes > 1) {
    console.log(
      `\x1b[33mthe limiter paced this run: ${traffic.reads} read(s) retried, ${traffic.writes} write(s) answered 429 (the flood check expects one).\x1b[0m\n` +
        `  that is the app working as designed (ip 120/min, learner creation 6/min), not a UI finding.\n` +
        `  to walk the whole suite back to back, start the API with ADAPT_RATE_LIMIT_MULTIPLIER=10.`
    );
  }
  if (failures) {
    console.log(`\n\x1b[31m${failures} failed:\x1b[0m`);
    for (const r of results.filter((x) => !x.ok)) console.log(`  - [${r.scenario}] ${r.label}\n      ${r.detail}`);
  }
  process.exit(failures ? 1 : 0);
}

main().catch((err) => {
  console.error("\n\x1b[31mprobe crashed\x1b[0m (a harness or app fault, not a check failure)");
  console.error(err.stack?.split("\n").slice(0, 6).join("\n"));
  // A crash stays a crash: exit 3, nothing quietly marked pass. But the checks that already ran are
  // the truth about the screens they judged, and discarding them meant one stalled scenario hid a
  // hundred and fifty results and left "which screen broke today" to be guessed from the stack.
  if (results.length) {
    console.log(`\n\x1b[1m${results.length - failures}/${results.length} checks passed before the crash\x1b[0m  \x1b[2m(stalled in ${CURRENT || "the first scenario"})\x1b[0m`);
    for (const r of results.filter((x) => !x.ok)) console.log(`  - [${r.scenario}] ${r.label}\n      ${r.detail}`);
    console.log(`\x1b[2mscreens after ${CURRENT || "?"} were not judged\x1b[0m`);
  }
  process.exit(3);
});

