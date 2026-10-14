/**
 * The content engine (§7) as a screen: a topic in, what we would actually teach for it out.
 *
 * This module is the only place the UI talks to `/api/v1/content/*`, for the same reason
 * `subjects.ts` is the only place the subject round is talked to: the CSRF header, the error
 * shape and the mock flag are handled once, in `api.ts`, and every payload keeps the shape the
 * backend produced.
 *
 * Four rules from the backend are load-bearing here and are *not* re-decided in the browser:
 *
 * * A concept with no authored kit comes back `available: false` with the `reason` the engine
 *   wrote. The screen shows that sentence and does not fill the gap with a guess, a placeholder
 *   card or a "coming soon".
 * * `rejections` is not an error log. It is the engine saying a part of the pack failed its own
 *   gate — a worked example that could not be verified, a rung with no authored task — and it is
 *   rendered where that part would have been, because a missing part that is not named looks
 *   like a bug rather than a decision.
 * * The pair is matched on concept and format and explicitly **not** on difficulty. `pair.matched`
 *   says so in the engine's own words and is shown, so nobody reads the two legs as a
 *   like-for-like comparison.
 * * Grading is the server's job. The pack a browser holds has its options shuffled per learner
 *   and its key stripped, so this file sends the *text* of the option the learner picked and lets
 *   `POST /grade` decide. The browser never sees which option is right, and the explanation
 *   arrives with the verdict rather than with the question.
 *
 * `?mock=1`: `mock.ts` is the shared fixture table and is not this module's to extend, so the
 * fixtures live below, next to the shapes they imitate. They are recorded shapes — the calculus
 * entries are the strings the real engine produces for that authored kit — and the graded
 * response is the verdict the real scorer returns for that answer. Nothing here pretends a
 * fixture answer was measured.
 */
"use client";

import { ApiError, get, isMock, post } from "./api";

/* ------------------------------------------------------------------ types ---- */

/** One authored task, in the bank's item shape (`choices` + `answer_spec`). */
export type ContentTask = {
  id?: string;
  kind?: string;
  stem?: string;
  choices?: Array<{ text: string; correct?: boolean; why_wrong?: string }>;
  answer_spec?: { correct_index?: number };
  rung?: number;
  demand?: string;
  why?: string;
  steps?: string[];
  verified?: boolean;
  flags?: string[];
  spec?: unknown;
  /** only after `public_task` strips and shuffles it */
  options?: string[];
  prompt?: string;
};

/** The thing a screen shows: the three variants, the matched pair, the hints, the checklist. */
export type ConceptPack = {
  concept_id: string;
  concept_title: string;
  topic_key: string;
  topic_title: string;
  sub_area: string;
  available: boolean;
  reason: string;
  spec: string;
  variants: {
    written?: ContentTask;
    visual?: ContentTask;
    worked_example?: ContentTask;
  };
  pair: {
    format?: string;
    matched?: string;
    expected_accuracy_note?: string;
    A?: ContentTask | null;
    B?: ContentTask | null;
  };
  hints: Array<{ text?: string; rung?: number; source?: string }>;
  checklist: Array<Record<string, unknown>>;
  misconceptions: Array<{ claim?: string; contrast?: string; source?: string }>;
  objectives: Array<{ rung?: number; key?: string; demand?: string; task_stem?: string }>;
  notes: string[];
  rejections: Array<{ code?: string; detail?: string }>;
};

export type Breakdown = {
  topic: string;
  title: string;
  available: boolean;
  reason: string;
  concepts: Array<Record<string, unknown>>;
};

export type GradeVerdict = {
  correct: boolean;
  status: string;
  points: number;
  max_points: number;
  why: string;
  explanation: string;
  concept: string;
  leg: string;
  rung: number;
  demand: string;
};

/* --------------------------------------------------------------- fixtures ---- */
/*
 * The fixtures live in `mock.ts`, the shared table, so that `?mock=1` never 404s on a real route
 * and so that there is exactly one copy of each recorded shape. `mockGrade` below is the one part
 * that cannot live there: grading needs the answer the learner picked, and a mock row is handed
 * the URL, which a POST does not carry.
 */
import { contentBreakdown, contentPack } from "./mock";

const mockPack: ConceptPack = contentPack as unknown as ConceptPack;
const mockBreakdown: Breakdown = contentBreakdown as unknown as Breakdown;

/** What the server returns for the two answers the mock pair invites. */
function mockGrade(choice: string): GradeVerdict {
  const item = mockPack.pair.A;
  const options = item?.options ?? [];
  const explanation =
    "f(3) is 9 and f(1) is 1, so the change in f is 8 over a change in x of 2; dividing gives 4. Forgetting to divide is what gives 8.";
  const base = { explanation, concept: mockPack.concept_id, leg: "A" as const, rung: Number(item?.rung ?? 3), demand: String(item?.demand ?? "") };
  if (!options.includes(choice))
    return {
      ...base,
      correct: false,
      status: "unparseable",
      points: 0,
      max_points: 1,
      why: `${choice.slice(0, 60)} is not one of the options this question offers`,
    };
  const right = choice === "4";
  return {
    ...base,
    correct: right,
    status: "scored",
    points: right ? 1 : 0,
    max_points: 1,
    why: right ? "that is the average rate of change over that interval" : "that is the change in f, not the rate",
  };
}

/* ------------------------------------------------------------- fetchers ---- */

export async function fetchPack(topic: string, concept: string): Promise<ConceptPack> {
  if (isMock()) return mockPack;
  const { data, error } = await get<ConceptPack>("/api/v1/content/pack", { topic, concept });
  if (!data) throw new ApiError(0, error ?? "no pack could be built for this concept");
  return data;
}

export async function fetchBreakdown(topic: string): Promise<Breakdown> {
  if (isMock()) return mockBreakdown;
  const { data, error } = await get<Breakdown>("/api/v1/content/breakdown", { topic });
  if (!data) throw new ApiError(0, error ?? "no breakdown could be built for this topic");
  return data;
}

/**
 * Grade one leg of a matched pair.
 *
 * The text of the option goes, not its index: the served options are shuffled per learner, so an
 * index would mean a different option to the server than it did to the learner. The key stays
 * server-side, which is the whole point of asking at all.
 */
export async function gradeAnswer(
  topic: string,
  concept: string,
  leg: "A" | "B",
  choice: string,
): Promise<GradeVerdict> {
  if (isMock()) return mockGrade(choice);
  return post<GradeVerdict>("/api/v1/content/grade", { topic, concept, leg, choice });
}
