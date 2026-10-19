/** Conservative rectangles include transparent artwork. Require 16px clearance. */
export function inspectArtZones() {
  const visible = (n) => {
    const d = n.closest("details:not([open])");
    if (d && !d.querySelector(":scope > summary")?.contains(n)) return false;
    const s = getComputedStyle(n),
      r = n.getBoundingClientRect();
    return (
      r.width > 0 &&
      r.height > 0 &&
      s.display !== "none" &&
      s.visibility !== "hidden" &&
      !n.closest("[hidden],[inert]")
    );
  };
  const box = (n) => {
    const r = n.getBoundingClientRect();
    return {
      x: r.x,
      y: r.y,
      right: r.right,
      bottom: r.bottom,
      width: r.width,
      height: r.height,
    };
  };
  const intersects = (a, b, p = 0) =>
    a.x < b.right + p &&
    a.right > b.x - p &&
    a.y < b.bottom + p &&
    a.bottom > b.y - p;
  const content = [
    ...document.querySelectorAll(
      "input,button,a,p,h1,h2,h3,h4,h5,h6,label,li,[role=radio],[role=button],.choice-card,[data-account-gate],.lesson-block",
    ),
  ].filter(visible);
  const patterns = [...document.querySelectorAll("[data-pattern]")].filter(
      visible,
    ),
    art = [...document.querySelectorAll("[data-art]")].filter(visible),
    failures = [];
  const textBoxes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode,
      p = n.parentElement;
    if (
      !p ||
      !n.textContent.trim() ||
      !visible(p) ||
      p.closest("[aria-hidden=true], .sr-only, script, style")
    )
      continue;
    const r = document.createRange();
    r.selectNodeContents(n);
    for (const b of r.getClientRects())
      if (b.width && b.height) textBoxes.push(b);
  }
  for (const n of [...patterns, ...art]) {
    if (n.getAttribute("aria-hidden") !== "true")
      failures.push("Decoration not aria-hidden");
    if (getComputedStyle(n).pointerEvents !== "none")
      failures.push("Decoration intercepts pointers");
    for (const b of textBoxes)
      if (intersects(box(n), b, 16))
        failures.push(`${n.dataset.art ?? "pattern"} touches a text node`);
    for (const c of content)
      if (intersects(box(n), box(c), 16))
        failures.push(
          `${n.dataset.art ?? "pattern"} touches ${c.tagName}: ${c.textContent?.slice(0, 60)}`,
        );
  }
  for (const p of patterns)
    for (const a of art)
      if (intersects(box(p), box(a), 16)) failures.push("Pattern touches art");
  if (innerWidth < 900 && patterns.length) failures.push("Pattern below 900px");
  // Phones may carry header art, but only the small version (it must never push the content down).
  if (innerWidth < 600 && art.some((n) => n.dataset.art !== "flower" && n.getBoundingClientRect().height > 160))
    failures.push("Phone art larger than the small header size");
  return {
    failures,
    patternCount: patterns.length,
    art: art.map((n) => ({ name: n.dataset.art, box: box(n) })),
  };
}
/** Visible text nodes only; closed disclosures never contribute. Body excludes
 * headings, form labels, controls and options. visibleWords includes those too. */
export function screenWords() {
  const root =
    document.querySelector(
      "[data-account-gate],[data-journey],.baseline-page",
    ) ??
    document.querySelector("[data-sheet]") ??
    document.querySelector("#main-content");
  let body = "",
    all = "",
    above = "";
  if (!root) return { bodyWords: 0, visibleWords: 0, aboveFoldWords: 0 };
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode,
      p = n.parentElement;
    if (!p || !n.textContent.trim() || p.closest("[aria-hidden=true],.sr-only"))
      continue;
    const closed = p.closest("details:not([open])");
    if (closed && !closed.querySelector(":scope > summary")?.contains(p))
      continue;
    const r = document.createRange();
    r.selectNodeContents(n);
    const rects = [...r.getClientRects()];
    if (
      !rects.some((r) => r.width && r.height) ||
      getComputedStyle(p).visibility === "hidden"
    )
      continue;
    all += " " + n.textContent;
    if (rects.some((r) => r.top < innerHeight && r.bottom > 0))
      above += " " + n.textContent;
    if (
      !p.closest(
        "button,a,input,select,textarea,label,summary,h1,h2,h3,h4,h5,h6,[role=group],.choice-card,.choice-icon,.access-step,.baseline-head,.baseline-bar,.r3-question-foot,[role=progressbar]",
      )
    )
      body += " " + n.textContent;
  }
  const count = (t) => t.trim().split(/\s+/).filter(Boolean).length;
  return {
    bodyWords: count(body),
    visibleWords: count(all),
    aboveFoldWords: count(above),
  };
}
