#!/usr/bin/env node
/**
 * Copy dump: render each screen in jsdom (the same way the probe does) and print the text a
 * learner would read, so punctuation and hyphens can be checked against real output instead of
 * against the source. Diagnostic tool, not part of the suite.
 *
 *   cd frontend && node scripts-copy-dump.mjs [--base http://127.0.0.1:3000]
 */
import { JSDOM, VirtualConsole } from "jsdom";

const BASE = (process.argv.find((a) => a.startsWith("--base="))?.slice(7) ?? "http://127.0.0.1:3000").replace(/\/$/, "");
const PATHS = ["/", "/?mock=1", "/onboarding?mock=1", "/diagnose?mock=1", "/learn?mock=1", "/lesson?mock=1", "/proposals?mock=1", "/review?mock=1", "/progress?mock=1", "/settings?mock=1", "/voice?mock=1"];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

for (const path of PATHS) {
  const vc = new VirtualConsole();
  const dom = await JSDOM.fromURL(`${BASE}${path}`, {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: vc,
  });
  await sleep(1800);
  // The opening overlay is on top of everything on a first visit and its text is not the page.
  dom.window.document.querySelectorAll(".opening-overlay").forEach((el) => el.remove());
  // Only what a person reads: scripts, styles and the next.js dev overlay are not copy.
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);
  const BLOCK = new Set(["DIV", "P", "LI", "UL", "OL", "H1", "H2", "H3", "H4", "SECTION", "HEADER", "FOOTER", "NAV", "ASIDE", "MAIN", "ARTICLE", "SUMMARY", "DT", "DD", "TR", "TABLE", "FIGURE", "BLOCKQUOTE", "BUTTON", "A", "SPAN"]);
  const parts = [];
  const walk = (el) => {
    if (SKIP.has(el.tagName)) return;
    if (el.nodeType === 3) {
      if (!SKIP.has(el.parentElement?.tagName ?? "")) parts.push(el.textContent ?? "");
      return;
    }
    if (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA") {
      const label = el.closest("label")?.textContent ?? el.getAttribute("placeholder") ?? "";
      if (label) parts.push(` [field: ${label.replace(/\s+/g, " ").trim()}] `);
      return;
    }
    // A space where the layout would put one, and none where the nodes are inline siblings:
    // this is what makes "it." not read as "it .".
    if (BLOCK.has(el.tagName) && el.tagName !== "SPAN" && el.tagName !== "A") parts.push("\n");
    el.childNodes.forEach(walk);
    if (BLOCK.has(el.tagName) && el.tagName !== "SPAN" && el.tagName !== "A") parts.push("\n");
  };
  walk(dom.window.document.body);
  const text = parts
    .join("")
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trim())
    .filter(Boolean)
    .join("\n");
  console.log(`\n===== ${path}\n${text}`);
  dom.window.close();
}
