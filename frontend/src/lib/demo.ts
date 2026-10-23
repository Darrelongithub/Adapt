import type { ItemResult, PlanItem } from "./tests/registry";

/** The pitch demo is deliberately a separate, short flow — not a truncated baseline. */
export const DEMO_GAME_COUNT = 3;
export const DEMO_PREPARE_TIMEOUT_MS = 8_000;

/** One-tap checks only. Dragging and the pointer-only number line stay in the full app. */
const DEMO_KINDS = new Set([
  "pattern_pick",
  "quick_recall",
  "fraction_bar",
  "cloze_word_bank",
  "geometry_angle",
  "geometry_congruent",
]);

export type DemoPlay = {
  item: PlanItem;
  result: ItemResult;
};

export type DemoSet = {
  items: PlanItem[];
  runId: string | null;
  origin: "server" | "built-in";
};

/**
 * A bounded, known-good set for a slow or unavailable content request. These use the same
 * renderers and local scoring path as the rest of the app; they are not presented as saved evidence.
 */
const BUILT_IN_ITEMS: PlanItem[] = [
  {
    id: "demo-pattern",
    item_type: "pattern_pick",
    skill: "patterns.sequence",
    difficulty: 2,
    parameters: {
      sequence: "circle,square,circle,square",
      options: "circle,triangle,pentagon,line",
      answer_index: 0,
    },
  },
  {
    id: "demo-recall-easy",
    item_type: "quick_recall",
    skill: "memory",
    difficulty: 2,
    parameters: {
      shown: "red,green,blue",
      choices: "red,green,blue,yellow",
      answer_indices: "0,1,2",
      seconds: 3,
    },
  },
  {
    id: "demo-fraction-easy",
    item_type: "fraction_bar",
    skill: "number.fractions",
    difficulty: 1,
    parameters: {
      numerator: 1,
      denominator: 2,
      options: "1/2,1/3,1/4,1/5",
      answer_index: 0,
    },
  },
  {
    id: "demo-word-choice",
    item_type: "cloze_word_bank",
    skill: "vocabulary",
    difficulty: 3,
    parameters: {
      sentence: "She ___ (went) to the market yesterday.",
      bank: "go,goes,went,gone",
      answer_index: 2,
    },
  },
  {
    id: "demo-recall-hard",
    item_type: "quick_recall",
    skill: "memory",
    difficulty: 4,
    parameters: {
      shown: "sun,leaf,river,cloud",
      choices: "sun,leaf,river,cloud,stone",
      answer_indices: "0,1,2,3",
      seconds: 3,
    },
  },
  {
    id: "demo-fraction-hard",
    item_type: "fraction_bar",
    skill: "number.fractions",
    difficulty: 4,
    parameters: {
      numerator: 3,
      denominator: 4,
      options: "1/4,1/2,3/4,1/3",
      answer_index: 2,
    },
  },
];

export function builtInDemoSet(): DemoSet {
  return {
    items: BUILT_IN_ITEMS.map((item) => ({ ...item, parameters: { ...item.parameters } })),
    runId: null,
    origin: "built-in",
  };
}

/** Keep only short, phone-friendly items. An unusable plan falls back as a whole. */
export function validatedDemoItems(raw: unknown): PlanItem[] | null {
  const items: PlanItem[] = [];
  for (const entry of Array.isArray(raw) ? raw : []) {
    if (!entry || typeof entry !== "object") continue;
    const candidate = entry as Partial<PlanItem>;
    const itemType = String(candidate.item_type ?? "");
    if (!DEMO_KINDS.has(itemType)) continue;
    const numericDifficulty = Number(candidate.difficulty);
    const difficulty = Number.isFinite(numericDifficulty) ? Math.round(numericDifficulty) : 3;
    items.push({
      id: typeof candidate.id === "string" && candidate.id ? candidate.id : `demo-item-${items.length}`,
      run_id: typeof candidate.run_id === "string" ? candidate.run_id : undefined,
      grading_seal: typeof candidate.grading_seal === "string" ? candidate.grading_seal : undefined,
      item_type: itemType as PlanItem["item_type"],
      skill: String(candidate.skill ?? itemType),
      difficulty: Math.max(1, Math.min(5, difficulty)),
      parameters: candidate.parameters && typeof candidate.parameters === "object" && !Array.isArray(candidate.parameters)
        ? { ...candidate.parameters }
        : {},
    });
    if (items.length >= 8) break;
  }
  return items.length >= DEMO_GAME_COUNT && new Set(items.map((item) => item.id)).size === items.length ? items : null;
}

export function firstDemoItem(items: PlanItem[]): PlanItem | null {
  return items.find((item) => item.item_type === "pattern_pick") ?? items[0] ?? null;
}

/**
 * Select the next challenge from the plan the server already returned. A correct, unaided
 * answer selects the nearest harder step; otherwise the nearest gentler step wins. When no
 * such step exists, the closest level is used. A new game type is preferred throughout.
 */
export function nextDemoItem(items: PlanItem[], played: DemoPlay[]): PlanItem | null {
  const last = played[played.length - 1];
  if (!last || played.length >= DEMO_GAME_COUNT) return null;
  const usedIds = new Set(played.map((row) => row.item.id));
  const usedTypes = new Set(played.map((row) => row.item.item_type));
  const unused = items.filter((item) => !usedIds.has(item.id));
  const freshType = unused.filter((item) => !usedTypes.has(item.item_type));
  const pool = freshType.length ? freshType : unused;
  if (!pool.length) return null;

  const stepUp = last.result.correct && last.result.hints === 0;
  const directional = pool.filter((item) =>
    stepUp ? item.difficulty > last.item.difficulty : item.difficulty < last.item.difficulty,
  );
  const candidates = directional.length ? directional : pool;
  return [...candidates].sort((a, b) => {
    const distanceA = Math.abs(a.difficulty - last.item.difficulty);
    const distanceB = Math.abs(b.difficulty - last.item.difficulty);
    if (distanceA !== distanceB) return distanceA - distanceB;
    // With equal distances, preserve the plan's order rather than randomising a demo run.
    return items.indexOf(a) - items.indexOf(b);
  })[0];
}

const GAME_NAMES: Record<string, string> = {
  pattern_pick: "Patterns",
  quick_recall: "Quick recall",
  fraction_bar: "Fractions",
  cloze_word_bank: "Word choice",
  geometry_angle: "Angles",
  geometry_congruent: "Shapes",
};

export function demoGameName(item: PlanItem): string {
  return GAME_NAMES[item.item_type] ?? "Quick game";
}

export function demoTransition(previous: DemoPlay, next: PlanItem): string {
  if (next.difficulty > previous.item.difficulty) return "Adapt stepped the next challenge up.";
  if (next.difficulty < previous.item.difficulty) return "Adapt made the next challenge gentler.";
  return "Adapt changed the kind of challenge.";
}

export type DemoAnalysis = {
  correct: number;
  total: number;
  headline: string;
  nextStep: string;
  supportNote: string | null;
};

/** Describe only what these three observations show. No learner types or fixed traits. */
export function analyseDemo(played: DemoPlay[]): DemoAnalysis {
  const total = played.length;
  const correct = played.filter((row) => row.result.correct).length;
  const hints = played.reduce((sum, row) => sum + row.result.hints, 0);
  const missed = played.find((row) => !row.result.correct);
  const focus = missed?.item ?? played[played.length - 1]?.item;
  const focusName = focus ? demoGameName(focus).toLowerCase() : "quick";
  return {
    correct,
    total,
    headline: total ? `${correct} of ${total} games answered correctly.` : "No games answered yet.",
    nextStep: missed
      ? `A good next step is one gentler ${focusName} game, with a hint nearby.`
      : "A good next step is a slightly harder challenge.",
    supportNote: hints ? `You used ${hints === 1 ? "a hint" : `${hints} hints`}; Adapt would keep that support available.` : null,
  };
}

export function demoRunId(played: DemoPlay[]): string | null {
  if (played.length !== DEMO_GAME_COUNT) return null;
  const runId = played[0]?.item.run_id;
  if (!runId) return null;
  return played.every((row) => row.item.run_id === runId && row.item.grading_seal) ? runId : null;
}

/** The existing evidence contract, shared with the full subject-round flow. */
export function demoEvidenceRows(played: DemoPlay[]) {
  return played.map(({ item, result }) => ({
    item_id: item.id,
    grading_seal: item.grading_seal ?? "",
    item_type: item.item_type,
    skill: item.skill,
    difficulty: item.difficulty,
    answer: result.answer,
    correct: result.correct,
    ms: result.ms,
    hints_used: result.hints,
    parameters: item.parameters,
  }));
}

/** Bound preparation without making the result screen wait on a network request. */
export async function withDemoTimeout<T>(work: Promise<T>, timeoutMs = DEMO_PREPARE_TIMEOUT_MS): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error("Demo preparation timed out")), timeoutMs);
  });
  try {
    return await Promise.race([work, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}
