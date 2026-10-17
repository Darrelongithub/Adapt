/**
 * The item registry (Round 6, Track B) — the browser half of "AI proposes, code
 * decides". Mirrors `backend/src/adapt/subjects/test_items.py`: every type the
 * backend can propose is a type these components can render and score.
 *
 * Every item is an interaction scored by CODE — never a self-report. Each scorer
 * returns `{ correct, answer, ms, hints }` and the journey page stores that as an
 * evidence row (item, answer, correct, ms, hints).
 */

export type ItemTypeName =
  | "pattern_pick"
  | "drag_order"
  | "drag_match"
  | "odd_one_out"
  | "truth_table"
  | "venn_region"
  | "geometry_angle"
  | "geometry_congruent"
  | "number_line"
  | "fraction_bar"
  | "cloze_word_bank"
  | "quick_recall";

export type TestItem = {
  id?: string;
  item_type: ItemTypeName;
  skill: string;
  difficulty: number;
  parameters: Record<string, unknown>;
};

export type ItemResult = {
  correct: boolean;
  answer: string;
  ms: number;
  hints: number;
};

export type PlanItem = TestItem & { id: string };

export const ITEM_TYPES: ItemTypeName[] = [
  "pattern_pick",
  "drag_order",
  "drag_match",
  "odd_one_out",
  "truth_table",
  "venn_region",
  "geometry_angle",
  "geometry_congruent",
  "number_line",
  "fraction_bar",
  "cloze_word_bank",
  "quick_recall",
];

export function splitList(value: unknown): string[] {
  return String(value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function asInt(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? Math.round(n) : fallback;
}

/** Instruction copy: max ~12 words per screen. */
export const INSTRUCTIONS: Record<ItemTypeName, string> = {
  pattern_pick: "Tap what comes next.",
  drag_order: "Put these in order.",
  drag_match: "Match each one to its pair.",
  odd_one_out: "Tap the odd one out.",
  truth_table: "Tap the true rows.",
  venn_region: "Tap the right region.",
  geometry_angle: "Tap the correct angle.",
  geometry_congruent: "Tap the congruent shape.",
  number_line: "Tap where the number goes.",
  fraction_bar: "Tap the matching fraction.",
  cloze_word_bank: "Tap the word that fits.",
  quick_recall: "Remember these, then pick them.",
};

/** Hints: one per type, revealed on request and counted in the evidence row. */
export const HINTS: Record<ItemTypeName, string> = {
  pattern_pick: "Look at how each step changes.",
  drag_order: "What must come first?",
  drag_match: "Start with the pair you know.",
  odd_one_out: "Compare every option to the others.",
  truth_table: "Check each row one at a time.",
  venn_region: "Inside both circles means both.",
  geometry_angle: "Compare with a right angle.",
  geometry_congruent: "Same side lengths, same shape.",
  number_line: "Halves of the line help.",
  fraction_bar: "Count filled parts over total.",
  cloze_word_bank: "Read the whole sentence first.",
  quick_recall: "Say the words to yourself.",
};

export function validatePlanItems(raw: unknown): PlanItem[] {
  const items: PlanItem[] = [];
  const list = Array.isArray(raw) ? raw : [];
  for (const entry of list) {
    if (items.length >= 8) break;
    const e = entry as Partial<TestItem> & { parameters?: Record<string, unknown> };
    if (!e || typeof e !== "object") continue;
    const type = String(e.item_type ?? "") as ItemTypeName;
    if (!ITEM_TYPES.includes(type)) continue;
    const parameters = e.parameters && typeof e.parameters === "object" ? { ...e.parameters } : {};
    items.push({
      id: `item-${items.length}`,
      item_type: type,
      skill: String(e.skill ?? type),
      difficulty: Math.max(1, Math.min(5, asInt(e.difficulty, 3))),
      parameters,
    });
  }
  return items;
}
