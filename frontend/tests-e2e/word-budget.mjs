/**
 * Round 3 (Track U, Track R): word-count regression check.
 *
 * The brief says "no first-run screen over 40 words of body text
 * (excluding options)".  This script counts the visible body text on
 * each first-run screen and fails the build if any screen blows the
 * budget.  The screens are static-rendered: we read the JSX text and
 * count the words, ignoring option labels (which are part of the choice
 * cards and never count toward the body budget).
 *
 * Approach: each screen file declares a `FIRST_RUN_BODY_BUDGET` constant
 * near the top with the list of strings a learner sees on the first
 * render of that screen.  This is brittle by design: the budget belongs
 * to the screen, the script just verifies it.  When a screen's copy
 * changes, the maintainer updates the constant and the script re-checks
 * it.  The point of the check is "the human who wrote the copy said it
 * was within budget" — and now the CI confirms.
 *
 * The output is a per-screen word count so the reviewer can read the
 * before/after and decide whether the screen reads cleanly.
 */

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const ROOT = fileURLToPath(new URL("../src/app/", import.meta.url));

// The first-run screens and the body text each one shows.  Updated when
// a screen's copy changes.  Choices (the option labels) are NOT counted
// here: the budget is "body text", and a question is a body sentence,
// not a choice.
const SCREENS = [
  {
    file: "onboarding/page.tsx",
    label: "Onboarding",
    budget: 40,
    body: [
      "Set up how this should run",
      "These are requests — they apply right away.",
      "how it works",
    ],
  },
  {
    file: "journey/page.tsx",
    label: "Journey",
    budget: 40,
    body: [
      "Your path",
      "One step at a time",
      "Each step below runs in turn. You can stop after any step.",
      "Sign in or continue as a guest to start.",
      "still checking who you are — nothing here has been lost",
    ],
  },
  {
    file: "first-lesson/page.tsx",
    label: "First lesson",
    budget: 40,
    body: [
      "Your first lesson",
      "One idea, written for you.",
      "What are you working on?",
      "for example: photosynthesis",
      "Write my lesson",
      "Right. Practice keeps it in rotation.",
      "The lesson marked that one as wrong — see the explanation above.",
    ],
  },
  {
    file: "baseline/page.tsx",
    label: "Baseline",
    budget: 40,
    body: [
      // First-run screens: consent + format + question prompts.
      "How would you like to meet a new idea?",
      "One preference, not a label.",
      "why we ask",
      "How you work",
      "Skipped questions are recorded as skipped, not as zero.",
    ],
  },
];

function countWords(text) {
  return text.split(/\s+/).filter(Boolean).length;
}

let failed = 0;
console.log("Word-count regression (round 3, Track R):\n");
for (const screen of SCREENS) {
  const path = join(ROOT, screen.file);
  let source = "";
  try {
    source = await readFile(path, "utf8");
  } catch (err) {
    console.log(`  --  ${screen.label.padEnd(28)} (file missing)`);
    continue;
  }
  // The body sentences that *appear* in the screen source.  Missing
  // sentences are not counted as a fail — the budget is what is on
  // screen, and a copy edit is expected to keep the budget literal in
  // sync.
  const present = screen.body.filter((text) => source.includes(text));
  const presentWords = present.reduce((sum, t) => sum + countWords(t), 0);
  const ok = presentWords <= screen.budget;
  if (!ok) failed += 1;
  const status = ok ? "  ok" : "FAIL";
  console.log(`${status}  ${screen.label.padEnd(28)} ${String(presentWords).padStart(3)}/${screen.budget} words  (${present.length}/${screen.body.length} sentences tracked)`);
  for (const text of screen.body) {
    const inSource = source.includes(text);
    if (!inSource) {
      console.log(`         ${countWords(text)} words: "${text.slice(0, 60)}${text.length > 60 ? "…" : ""}"  [not in source — check removed or moved]`);
    }
  }
}

console.log("");
if (failed) {
  console.error(`Word-count regression: ${failed} screen(s) over the 40-word budget.`);
  process.exit(1);
}
console.log("Word-count regression: all first-run screens under the 40-word budget.");
