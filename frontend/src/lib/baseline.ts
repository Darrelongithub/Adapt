"use client";

/**
 * The mind baseline, from the browser's side (round brief section 5).
 *
 * The flow is the server's, not this file's: the API answers "what now?" and every screen here
 * renders what came back. That is deliberate -- a cursor kept in the browser is a cursor that can
 * disagree with the trial log, and then a learner who closes the tab is served game one again.
 * So this module is thin on purpose: typed calls, plus the few pure helpers (`offers_coins`,
 * `timers_visible`, `format_range`) that decide presentation and can be unit-tested without a DOM.
 *
 * Two promises the screens must keep, both asserted in the probe rather than only here:
 *
 * * **Stopping is normal.** Every screen has a way to finish now; a partial baseline stores what
 *   was measured and marks the rest "not measured" -- never zero.
 * * **Coins are a question, not a game.** A wager is offered only on scored question tasks, never
 *   on a practice trial, and never when the learner asked for timers off (which is about how the
 *   app behaves, and asking for coins while refusing a clock would be the same pressure in a
 *   different costume).
 */

import { api, post } from "./api";

export type Json = Record<string, unknown>;

/** One task, as the server describes it. Loose by design: each game reads its own fields. */
export type BaselineTask = {
  id: string;
  game: string;
  phase?: string;
  prompt?: string;
  practice?: boolean;
  max_latency_ms?: number;
} & Json;

export type FormatOption = { value: string; label: string; hint: string };

export type BaselineQuestion = {
  key: string;
  question: string;
  options?: unknown[];
  multi?: boolean;
  free_text?: boolean;
  why?: string;
};

export type StageRow = { stage: string; state: string; trials: number };

export type Completion = {
  stages?: StageRow[];
  complete?: number;
  total?: number;
  played?: string[];
  not_measured?: string[];
};

export type BaselineView = {
  run_id: string;
  student_id: string;
  status: string;
  stage: string;
  stage_title: string;
  kind: "format" | "questions" | "task" | "snapshot" | string;
  task: BaselineTask | null;
  progress: number;
  note: string;
  format_pick: string | null;
  format_options: FormatOption[];
  questions: BaselineQuestion[];
  answers?: Record<string, unknown>;
  completion?: Completion;
  /** The score of the trial just submitted, when there was one. */
  scored?: Json | null;
  duplicate?: boolean;
  refused?: string[];
  refused_note?: string;
};

export type Factor = {
  key: string;
  title: string;
  state: string;
  n: number;
  range: number[] | null;
  summary: string;
  statement: string;
  actions: string[];
  detail?: Json;
};

export type Lever = { value: unknown; why?: string; source?: string };

export type PreferenceVsPerformance = {
  lever?: string;
  preference?: string;
  performance?: string;
  statement?: string;
  note?: string;
} & Json;

export type BaselineSnapshot = {
  student_id: string;
  run_id: string | null;
  snapshot_id: string | null;
  taken_at: string | null;
  state: string;
  factors: Factor[];
  levers?: Record<string, Lever>;
  preferences?: Record<string, unknown>;
  preference_vs_performance?: PreferenceVsPerformance | null;
  completion?: Completion;
  measured?: number;
  total?: number;
  note?: string;
};

export type NextDay = {
  student_id: string;
  run_id?: string;
  tasks: BaselineTask[];
  studied?: boolean;
  /** True when the check is asked on the day of the sitting: a short gap, not the next-day one. */
  same_day?: boolean;
  note?: string;
};

export type TrialBody = {
  task_id: string;
  response: Json;
  latency_ms?: number | null;
  wager?: number | null;
};

/* ------------------------------------------------------------------- calls ---- */

/** Open the baseline, or hand back the run that is already open. */
export const start_baseline = () => post<BaselineView>("/api/v1/baseline", {});

export const load_baseline = (run_id: string) => api<BaselineView>(`/api/v1/baseline/${run_id}`);

export const pick_format = (run_id: string, value: string) =>
  post<BaselineView>(`/api/v1/baseline/${run_id}/format`, { value });

/** The stated questions go in one submit, and skipping is a submit with no answers. */
export const answer_questions = (run_id: string, answers: Record<string, unknown>) =>
  post<BaselineView>(`/api/v1/baseline/${run_id}/questions`, { answers });

export const submit_trial = (run_id: string, body: TrialBody) =>
  post<BaselineView>(`/api/v1/baseline/${run_id}/trial`, body);

/** Seal the run. A partial baseline is a finished baseline. */
export const finish_baseline = (run_id: string) => post<BaselineSnapshot>(`/api/v1/baseline/${run_id}/finish`, {});

export const load_snapshot = (run_id?: string | null) =>
  api<BaselineSnapshot>("/api/v1/baseline/snapshot", { query: run_id ? { run_id } : undefined });

/**
 * The codeword check. With a run id, that run's check. With `finished`, the check of the last sitting
 * the learner finished (the return visit). With neither, the newest run's (the server's default).
 */
export const load_next_day = (run_id?: string | null, finished = false) =>
  api<NextDay>("/api/v1/baseline/next-day", {
    query: run_id ? { run_id } : finished ? { finished: true } : undefined,
  });

/* ----------------------------------------------------------------- helpers ---- */

/** Games whose task is a single scored answer -- the only places coins are offered. */
export const COIN_TASKS: readonly string[] = ["quick", "trap", "codewords_cued", "codewords_next_day"];

/** A wager is a question about confidence in an answer, so it is only offered where there is one. */
export function offers_coins(view: BaselineView): boolean {
  const task = view.task;
  if (!task) return false;
  if (task.practice) return false;
  if (!COIN_TASKS.includes(task.game)) return false;
  return timers_visible(view);
}

/**
 * Whether the learner wants to see clocks.
 *
 * The app still measures latency quietly either way -- that is stated on the questions screen --
 * but a visible countdown is the thing they said no to, so it stays hidden.
 */
export function timers_visible(view: BaselineView): boolean {
  const answers = view.answers ?? {};
  return answers["timers_help"] !== "timers stress me";
}

/** "8-9" or "8", for a range that the server reports as a two-number list. */
export function format_range(range: number[] | null | undefined, unit = ""): string {
  if (!range || range.length === 0) return "not measured";
  const [lo, hi] = [range[0], range[range.length - 1]];
  const same = Math.abs(hi - lo) < 1e-9;
  const text = same ? `${round(lo)}` : `${round(lo)}-${round(hi)}`;
  return unit ? `${text} ${unit}` : text;
}

function round(n: number): string {
  if (Number.isInteger(n)) return String(n);
  return String(Math.round(n * 10) / 10);
}

export function source_label(source: string | undefined): string {
  if (source === "measured") return "measured";
  if (source === "stated") return "you told us";
  return "default";
}

/**
 * The eight factors, in the order the server reports them (`FACTORS` in backend/.../factors.py).
 * The keys must be the server's: a name the server never sends sorts to the end and the factor
 * is shown out of place. (The page used `reasoning_style` and `confidence_calibration`, so two
 * of the eight were always last.)
 */
export const FACTOR_ORDER: readonly string[] = [
  "prior_knowledge",
  "working_memory",
  "processing_pace",
  "attention_self_control",
  "long_term_memory",
  "reasoning",
  "metacognition",
  "persistence_help_seeking",
];

/** The thirteen steps the learner sees before the snapshot, in order (the snapshot is not a step). */
export const STEP_STAGES: readonly string[] = [
  "format",
  "questions",
  "codewords_study",
  "codewords_cued",
  "quick_a",
  "vault",
  "shopkeeper",
  "quick_b",
  "machine",
  "locked_box",
  "quick_c",
  "trap",
  "codewords_delayed",
];

/** "Step 4 of 13" for a stage. The stage of a finished or unknown view has no step. */
export function step_label(stage: string): string {
  const at = STEP_STAGES.indexOf(stage);
  return at === -1 ? "" : `Step ${at + 1} of ${STEP_STAGES.length}`;
}

/** The eight factors in the server's order. Anything unknown lands at the end. */
export function order_factors(factors: Factor[]): Factor[] {
  const order = FACTOR_ORDER;
  return [...factors].sort((a, b) => {
    const ia = order.indexOf(a.key);
    const ib = order.indexOf(b.key);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
}

/** The same rule as `timers_visible`, for a preferences map (the snapshot carries the answers). */
export function timers_visible_in(prefs: Record<string, unknown> | undefined): boolean {
  return (prefs ?? {})["timers_help"] !== "timers stress me";
}

/** Seconds left on a visible clock, for the study screen and the timed games. */
export function seconds_left(deadline_ms: number, now_ms: number): number {
  return Math.max(0, Math.ceil((deadline_ms - now_ms) / 1000));
}

/** What the learner types back for the shopkeeper's items: one line, comma separated. */
export function parse_items(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** A vault answer, given the order the tiles were tapped. */
export function vault_expected(sequence: number[], answer_direction: string | undefined): number[] {
  return answer_direction === "backward" ? [...sequence].reverse() : [...sequence];
}

/** The learner's own probes on a machine round, as the server recorded them (oldest first). */
export type MachineProbe = { x: number; out: unknown; falsifying: boolean };

/** True when the taps so far are the answer so far (a prefix check, so a wrong tap shows at once). */
export function vault_prefix_ok(taps: number[], expected: number[]): boolean {
  return taps.every((tap, i) => tap === expected[i]);
}
