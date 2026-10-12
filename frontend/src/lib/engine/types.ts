/**
 * The shapes the interactive engine exchanges with the browser.
 *
 * These mirror what the backend actually returns today — `POST /api/v1/lessons/open` and the
 * frame objects from `learning/sims/*`, the scene cards from `GET /api/v1/lessons`, and the
 * validated-spec rules in `learning/scenespec.py` — rather than inventing a second vocabulary.
 * `backend/tests/test_lessons_api.py` asserts the same key sets from the other side.
 *
 * Two things a browser may never hold, and so appear nowhere below:
 * - an answer key. A prediction frame carries the *question* and its choices; what the machine
 *   will do next is in `state`, and the grade of a prediction is the backend simulation's to
 *   give (`POST /lessons/run/{id}/predict`), never a lookup here;
 * - rendered markup. `narration`, `cause`, `title` and `text` are plain strings and go in as
 *   text nodes, so nothing a model wrote is ever parsed as HTML.
 */

/** A frame's question. `type` is the simulation's own vocabulary (`next_probe_index`, …). */
export type Prompt = {
  type: string;
  question: string;
  choices?: string[] | null;
};

/** One state of the machine, with the reason it got there. */
export type Frame = {
  i: number;
  /** the *stage* within the scene: init | compare | discard | decode | done | … */
  kind: string;
  title: string;
  narration: string;
  state: Record<string, unknown>;
  highlights?: string[];
  cause?: string | null;
  prompt?: Prompt | null;
};

export type Framing = { intro?: string | null; outro?: string | null };

export type Citation = { ref: string; claim: string };

/** The learner's own last run of a scene, as the list reports it. */
export type LastRun = {
  run_id: string;
  status: string;
  frame_index: number;
  furthest_frame: number;
  frames_viewed: number;
  predictions: number;
  correct: number;
  param_changes: number;
  seconds_watched: number;
  last_seen_at: string | null;
};

export type Scene = {
  id: string;
  spec_id: string;
  kind: string;
  title: string;
  origin: string;
  intent: string;
  framing?: Framing;
  concepts?: string[];
  citations?: Citation[];
  params?: Record<string, unknown>;
  frame_count: number;
  prediction_count: number;
  has_renderer: boolean;
  last_run?: LastRun | null;
  derived_from?: string | null;
};

export type SceneList = {
  scenes: Scene[];
  kinds: Record<string, { title: string; summary: string }>;
  counts: { scenes: number; engine_kinds: number };
  note: string;
};

/** One editable parameter, as the simulation declares it. `ui: "hidden"` means "not for learners". */
export type ParamSpec = {
  type: "int" | "ints" | "lines" | "bool" | "str" | "text";
  label?: string;
  help?: string;
  default?: unknown;
  min?: number;
  max?: number;
  min_items?: number;
  max_items?: number;
  sorted?: boolean;
  unique?: boolean;
  ui?: string;
};

export type KindSpec = {
  title: string;
  summary: string;
  params: Record<string, ParamSpec>;
  max_frames: number;
  prompts: string[];
  answers_why: boolean;
};

export type Kinds = {
  kinds: Record<string, KindSpec>;
  limits: {
    title: number;
    intent: number;
    framing: number;
    concepts: number;
    citations: number;
    narration_chars_per_frame: number;
  };
};

/** What `POST /lessons/open` hands the player — everything it needs, so playback is offline. */
export type OpenedRun = {
  ok: boolean;
  run_id: string;
  scene_id: string;
  spec_id: string;
  kind: string;
  title: string;
  intent?: string;
  framing?: Framing;
  citations?: Citation[];
  params: Record<string, unknown>;
  frames: Frame[];
  frame_count: number;
  prediction_frames: number[];
  frame_index: number;
  resumed: boolean;
  origin?: string;
  renderer: { available: boolean; note?: string | null };
  derived_from?: string | null;
};

export type Progress = {
  ok: boolean;
  frame_index: number;
  furthest_frame: number;
  frames_viewed: number;
  frame_count: number;
  at_end: boolean;
};

/** A prediction, graded by the simulation. `expected` is the only answer the client ever sees. */
export type Graded = {
  ok: boolean;
  correct: boolean;
  expected?: string | null;
  said?: string | null;
  why?: string | null;
  frame: number;
  prompt?: Prompt | null;
  totals?: { predictions: number; correct: number };
};

/**
 * The answer to "why this frame?".
 *
 * One key set for both ways it can arrive — from the trace, or from the model (or from the
 * honest sentence that means neither could) — which is what
 * `test_both_ways_an_ask_is_answered_return_the_same_keys` guards.
 */
export type Explanation = {
  ok: boolean;
  text: string;
  note?: string;
  source: "deterministic" | "cache" | "template" | "llm";
  frame: number;
  cached?: boolean;
  needs_model?: boolean;
  tried_model?: boolean;
  question?: string;
  model?: string | null;
  tokens_in?: number;
  tokens_out?: number;
  citations?: Citation[];
};

export type PredictionRow = {
  frame: number;
  prompt: string;
  question?: string;
  choices?: string[];
  said?: string | null;
  expected?: string | null;
  correct: boolean;
  at?: string | null;
};

export type Review = {
  ok: boolean;
  run_id: string;
  status: string;
  frame_index: number;
  furthest_frame: number;
  frames_viewed: number;
  frame_count: number;
  predictions: PredictionRow[];
  skipped_predictions: number[];
  totals: {
    predictions: number;
    correct: number;
    accuracy: number | null;
    chance: number;
    replays: number;
    pauses: number;
    param_changes: number;
    seconds_watched: number;
  };
  scene: Scene;
};

export type Evidence = {
  student_id: string;
  summary: {
    runs: number;
    finished: number;
    predictions: number;
    correct: number;
    accuracy: number | null;
    chance: number;
    by_kind: Record<string, { runs: number; finished: number; predictions: number; correct: number; frames: number }>;
    note: string;
  };
  narration_cache: {
    entries: number;
    hits: number;
    by_source: Record<string, number>;
    model_calls_saved: number;
    ttl_days: number;
  };
  not_evidence: string[];
};
