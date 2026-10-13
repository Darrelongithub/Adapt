"use client";

/** Typed helpers for the skippable, resumable mind baseline. */
import { get, post } from "@/lib/api";

export type BaselineTask = {
  id: string;
  game: string;
  phase: "practice" | "measure" | "show" | string;
  prompt?: string;
  story?: string;
  kind?: string;
  action?: string;
  stage?: string;
  options?: string[];
  cue?: string;
  pairs?: { code: string; place: string }[];
  sequence?: number[];
  length?: number;
  direction?: "forward" | "backward" | string;
  answer_direction?: "forward" | "backward" | string;
  items?: string[];
  distractors?: { id: string; text: string }[];
  hint_shop?: { id: string; cost: number; text: string; level?: string }[];
  cases?: { n: number; value: number }[];
  study_ms?: number;
  max_latency_ms?: number;
  max_probes?: number;
  probes_used?: number;
  wrong_guesses?: number;
  max_wrong_guesses?: number;
  max_value?: number;
  [key: string]: unknown;
};

export type BaselineQuestion = {
  key: string;
  question: string;
  options: (string | number)[];
  multi?: boolean;
  free_text?: boolean;
  why?: string;
};

export type BaselineHistory = {
  task_id: string;
  game: string;
  phase: string;
  action: string;
  correct: boolean | null;
  latency_ms: number | null;
  wager: number | null;
  input?: number;
  output?: number;
  guess?: string;
  hint_id?: string;
  hint?: string;
  cost?: number;
  scored?: Record<string, unknown>;
};

export type BaselineStageReport = {
  stages: { stage: string; state: "complete" | "partial" | "not_started" | string; trials: number }[];
  complete: number;
  total: number;
  played: string[];
  not_measured: string[];
};

export type BaselineView = {
  run_id: string;
  student_id: string;
  status: "in_progress" | "complete" | string;
  stage: string;
  stage_title: string;
  kind: "task" | "format" | "questions" | "snapshot" | "done" | string;
  task: BaselineTask | null;
  progress: number;
  note?: string;
  format_pick?: string | null;
  format_options: { value: string; label: string; hint: string }[];
  questions: BaselineQuestion[];
  answers: Record<string, unknown>;
  timers_visible: boolean;
  history: BaselineHistory[];
  completion: BaselineStageReport;
  measured_trials: number;
  started_at?: string | null;
  resumed?: boolean;
  scored?: Record<string, unknown>;
  duplicate?: boolean;
};

export type BaselineFactor = {
  key: string;
  title: string;
  state: "measured" | "not_enough_data" | string;
  n: number;
  range: [number, number] | null;
  summary: string;
  statement: string;
  actions: string[];
  detail: Record<string, unknown>;
};

export type BaselineSnapshot = {
  student_id: string;
  run_id?: string | null;
  snapshot_id?: string | null;
  taken_at?: string | null;
  state: "sealed" | "in_progress" | "not_started" | string;
  factors: BaselineFactor[];
  measured?: number;
  total?: number;
  levers?: Record<string, { value: unknown; why: string; source: string }>;
  preferences?: Record<string, unknown>;
  preference_vs_performance?: { stated: string; measured: { format: string; n: number; accuracy: number }[]; sentence: string; note: string } | null;
  completion?: BaselineStageReport;
  note?: string;
};

export type StartBaseline = BaselineView & { resumed: boolean };
export type TrialInput = { task_id: string; response: Record<string, unknown>; latency_ms?: number; wager?: number };

export async function startBaseline(): Promise<StartBaseline> {
  return post<StartBaseline>("baseline", {});
}

export async function getBaseline(runId: string): Promise<BaselineView> {
  const result = await get<BaselineView>(`baseline/${encodeURIComponent(runId)}`);
  if (!result.data) throw new Error(result.error ?? "the baseline could not be opened");
  return result.data;
}

export async function chooseFormat(runId: string, value: string): Promise<BaselineView> {
  return post<BaselineView>(`baseline/${encodeURIComponent(runId)}/format`, { value });
}

export async function saveBaselineQuestions(runId: string, answers: Record<string, unknown>): Promise<BaselineView> {
  return post<BaselineView>(`baseline/${encodeURIComponent(runId)}/questions`, { answers });
}

export async function submitBaselineTrial(runId: string, body: TrialInput): Promise<BaselineView> {
  return post<BaselineView>(`baseline/${encodeURIComponent(runId)}/trial`, body);
}

export async function finishBaseline(runId: string): Promise<BaselineSnapshot> {
  return post<BaselineSnapshot>(`baseline/${encodeURIComponent(runId)}/finish`, {});
}

export async function getBaselineSnapshot(runId?: string): Promise<BaselineSnapshot> {
  const result = await get<BaselineSnapshot>("baseline/snapshot", runId ? { run_id: runId } : undefined);
  if (!result.data) throw new Error(result.error ?? "the snapshot could not be read");
  return result.data;
}

export function wagerAllowed(task: BaselineTask | null | undefined, action?: string | null): boolean {
  if (!task || task.phase !== "measure") return false;
  if (task.game === "machine") return action === "guess";
  if (task.game === "codewords_delayed" && (action === "predict" || task.action === "predict")) return false;
  return ["codewords_cued", "codewords_delayed", "quick", "shopkeeper", "trap", "vault"].includes(task.game);
}

export function factorRange(range: [number, number] | null): string {
  if (!range) return "not measured";
  const [low, high] = range;
  return `${trimNumber(low)}–${trimNumber(high)}`;
}

export function trimNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, "");
}

export function prettyKey(value: string): string {
  return value.replace(/_/g, " ");
}

export function secondsLabel(seconds: number): string {
  const safe = Math.max(0, Math.ceil(seconds));
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  return minutes ? `${minutes}:${String(rest).padStart(2, "0")}` : `${rest}s`;
}
