/**
 * The path itself: the eight stage ids, what each step is for, and the two pure
 * helpers a progress bar needs.
 *
 * Imports nothing on purpose. The frozen order and the learner-facing copy can then be
 * unit-tested without a DOM, a React runtime or a backend, and the data cannot quietly
 * acquire a dependency on the fetch layer that reads it. The live half — reading the
 * server's answer and writing a skip — is ``./journey.ts``.
 *
 * The ids are the frozen contract and the order is the path. Nothing here may reorder
 * them, and nothing anywhere should write the sequence out a second time: one path with
 * two ideas of "step 4" is how a learner ends up asked to do work twice.
 */

/** The eight stage ids, in path order. Frozen by the shared header. */
export const STAGE_IDS = [
  "account",
  "preferences",
  "games",
  "subject",
  "subject_round",
  "snapshot",
  "first_lesson",
  "practice",
] as const;

export type StageId = (typeof STAGE_IDS)[number];
export type StageStatus = "todo" | "done" | "skipped";

/** `evidence` is the server's extra key: `recorded` (a row proves it) or `stated` (the learner said so). */
export type StageRow = { id: StageId; status: StageStatus; evidence?: "recorded" | "stated" | "none" };

export type JourneyState = {
  student_id?: string;
  stage: StageId;
  stages: StageRow[];
  progress?: { done?: number; skipped?: number; todo?: number; total?: number; percent?: number };
  not_measured?: string[];
  updated_at?: string | null;
  generated_at?: string;
};

/**
 * What each stage *is*, in the learner's words, and where it happens.
 *
 * `href` points at the screen that does the work. Two stages share one route on
 * purpose: the questions and the games are both steps of the mind check, which is one
 * screen walking its own server-driven flow — linking to a second copy of it here
 * would be the duplication this phase exists to remove.
 *
 * `skip` is what "skip" means for that particular step, because "skip" on its own is
 * not honest: skipping the games leaves the snapshot's factors unmeasured, and the
 * learner should be told that before they press it.
 */
export const STEPS: Record<
  StageId,
  { n: number; title: string; href: string; action: string; what: string; skipNote: string }
> = {
  account: {
    n: 1,
    title: "Your account",
    href: "/",
    action: "create or continue",
    what: "A name to sign back in with — or continue as a guest. Either way it is what your work is stored against.",
    skipNote: "not possible: without an account there is nowhere to keep the answers",
  },
  preferences: {
    n: 2,
    title: "How you work",
    href: "/baseline",
    action: "answer the questions",
    what: "Thirteen short questions, asked once, in one place. They set the shape of everything after them.",
    skipNote: "the questions you leave are stored as skipped, and later screens say “not measured” rather than guessing",
  },
  games: {
    n: 3,
    title: "Mind games",
    href: "/baseline",
    action: "play the games",
    what: "Six short games that measure how you work — recall, attention, what happens when you get stuck.",
    skipNote: "the factors those games measure are then “not measured”, and nothing is invented in their place",
  },
  subject: {
    n: 4,
    title: "Choose a subject",
    href: "/subject",
    action: "choose a subject",
    what: "Type what you are working on. The app says what it can and cannot tell from it before anything is chosen.",
    skipNote: "no subject means no subject round; the ladder stays unanswered rather than guessed",
  },
  subject_round: {
    n: 5,
    title: "Subject round",
    href: "/subject",
    action: "start the round",
    what: "A short round on the skills your subject leans on, with the reason each test was chosen.",
    skipNote: "skill estimates stay “not measured”; practice still adapts from what you answer here",
  },
  snapshot: {
    n: 6,
    title: "What we noticed",
    href: "/baseline",
    action: "see what we noticed",
    what: "The measured picture, as ranges with the number of observations behind them — never a label.",
    skipNote: "you can look at it any time; skipping only means this screen does not stop you",
  },
  first_lesson: {
    n: 7,
    title: "Your first lesson",
    href: "/lesson",
    action: "open the lesson",
    what: "One idea, explained for the way you answered — with the reasons it looks the way it does.",
    skipNote: "no lesson yet: the practice screen still works from the questions you answer there",
  },
  practice: {
    n: 8,
    title: "Practice and review",
    href: "/learn",
    action: "start practising",
    what: "The loop from here on: answer, see why, come back to what is due. Wrong answers get explained, not just marked.",
    skipNote: "this is the loop rather than a step — there is nothing after it to skip to",
  },
};

/** Only these two stages really are optional in the sense a skip button implies. */
export const SKIPPABLE: StageId[] = ["preferences", "games", "subject", "subject_round", "snapshot", "first_lesson"];

/** Done and skipped both count as "behind you"; a skip is not a failure, it is an answer. */
export function isBehind(row: StageRow): boolean {
  return row.status === "done" || row.status === "skipped";
}

/** The percentage for the bar: done + skipped over the whole path. */
export function percent(state: JourneyState | null): number {
  if (!state) return 0;
  if (typeof state.progress?.percent === "number") return Math.max(0, Math.min(100, state.progress.percent));
  const rows = state.stages ?? [];
  if (!rows.length) return 0;
  return Math.round((rows.filter(isBehind).length / rows.length) * 100);
}
