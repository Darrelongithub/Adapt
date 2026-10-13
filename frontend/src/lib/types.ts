/** The shapes the backend actually returns, as the UI needs them.
 *
 * Written from the API's own payloads (see ``backend/tests/test_api_learner_loop.py``,
 * which asserts the same key sets) rather than from wishful naming: a field that does
 * not exist here does not exist in the response, and a page that reads it would render
 * "undefined" to a learner. Optional fields are optional because the source omits them
 * when there is nothing to say -- ``as_dict`` drops empty values on purpose.
 */

import type { EffectSummary } from "@/components/evidence";
export type Kind = "mcq" | "numeric" | "text" | "teachback" | "worked_example" | "visual";

export type Item = {
  id: string;
  topic_id: string;
  kind: Kind;
  stem: string;
  prompt_context?: string | null;
  visual_spec?: Record<string, unknown> | null;
  choices?: { index: number; text: string }[] | null;
  units?: string | null;
  experience_mode?: string;
  difficulty?: number;
  est_seconds?: number;
  hint_count?: number;
  has_worked_example?: boolean;
  answer_available?: boolean;
};

export type Presentation = {
  max_words?: number;
  hints_allowed?: number | boolean;
  require_teachback?: boolean;
  attach_worked_example?: boolean;
  interleaving_ratio?: number;
  target_success_rate?: number;
  re_read_allowed?: boolean;
  items_per_session?: number;
  feedback_delay_s?: number;
  ask_confidence?: boolean;
  voice_mode?: boolean;
  experience_mode?: string;
};

export type DecisionTrace = string[] | { chosen?: string[]; skipped?: { id: string; why: string }[]; why?: string };

export type PlanEntry = {
  item: Item;
  topic_id: string;
  reason?: string;
  utility?: number;
  presentation?: Presentation;
  decision_trace?: DecisionTrace;
  position?: number;
  of?: number;
};

export type Mastery = {
  topic_id: string;
  title?: string;
  p_know: number;
  band: string;
  observations: number;
  correct?: number;
  ci?: [number, number];
  half_life_hours?: number | null;
  last_practice?: string | null;
  stale?: boolean;
};

export type Receipt = {
  attempt_id: string;
  item_id: string;
  topic_id: string;
  correct: boolean;
  score: number;
  grading_method: string;
  feedback: string;
  mastery: {
    topic_id: string;
    p_know_before?: number;
    p_know: number;
    delta?: number;
    band: string;
    observations: number;
    expected_recall?: number | null;
    half_life_hours?: number | null;
    due_at?: string | null;
    first_observation?: boolean;
  };
  derived_metrics?: Record<string, unknown>;
  teachback?: {
    score: number;
    coverage?: number;
    method: string;
    matched: string[];
    missing: string[];
    followup?: string | null;
    words?: number;
    note?: string;
  } | null;
  retention?: Record<string, unknown> | null;
  flags?: string[];
  proposals_expected?: boolean;
};

export type Proposal = {
  id: string;
  strategy: string;
  strategy_label?: string;
  what_it_does?: string | null;
  facts?: string | null;
  pattern?: string | null;
  hypothesis?: string | null;
  change?: string | null;
  ask?: string | null;
  caveat?: string | null;
  expected_effect?: string | null;
  review_metric?: string | null;
  review_window?: { kind?: string; min_items?: number; max_items?: number; days?: number } | null;
  review_window_text?: string | null;
  /** The gate requires alternatives to be stated, and a citation may be a bare ref, a
   *  paper with a link, or a line of prose. All three shapes occur in the catalog. */
  alternatives?: (string | { explanation?: string; refuted_by?: string | null; [k: string]: unknown })[];
  citations?: { ref?: string; claim?: string; title?: string; url?: string | null; doi?: string | null }[];
  action?: string;
  action_payload?: Record<string, unknown> | null;
  status: string;
  student_note?: string | null;
  created_at?: string;
  decided_at?: string | null;
  baseline?: Record<string, unknown> | null;
  outcome?: Record<string, unknown> | null;
  evaluation_status?: string | null;
  modifications?: { applied?: Record<string, unknown>; refused_keys?: string[] } | null;
  evidence_ids?: string[];
  hypothesis_ref?: { id?: string; status?: string; posterior?: number } | null;
};

export type ActiveAdaptation = {
  id: string;
  strategy: string;
  label?: string;
  action?: string;
  payload?: Record<string, unknown>;
  source?: string;
  active?: boolean;
  started_at?: string;
  ends_at?: string | null;
  ended_at?: string | null;
  end_reason?: string | null;
  proposal_id?: string | null;
  /** The measurement, flattened by the API (`EffectSummary`), or null when nothing has run. */
  effect_summary?: string | EffectSummary | null;
  /** The stored payload, untouched: the audit record. */
  effect_raw?: Record<string, unknown> | null;
  /** `measured` | `gathering_evidence` | `not_measured`. */
  effect_status?: string | null;
  effect_measured?: boolean;
  watch_metric?: string | null;
  mechanism?: string | null;
  citations?: string[];
};

export type JourneyNode = {
  ts: string;
  kind: string;
  tone: "neutral" | "good" | "warn" | "bad" | "think" | "ask" | "muted" | string;
  title: string;
  detail?: string | null;
  meta?: Record<string, unknown>;
};

export type ExplainReply = {
  answer: string;
  provider: string;
  sections?: { label: string; text: string }[];
  next_action?: string | null;
  adaptations_applied?: string[];
  sources?: { kind: string; ref: string; title: string; url?: string; material_id?: string }[];
  teachback_prompt?: string | null;
  resource_hint?: string | null;
  level?: string;
  model_ms?: number;
  message_id?: string;
  intent?: string | null;
  meta?: { grounded?: boolean; provider_detail?: string };
  provider_note?: string;
  privacy?: { sent_to_model?: boolean; redacted_fields?: number; what_was_sent?: string };
};

export type Health = { ok: boolean; mode: string; learners?: number; [k: string]: unknown };

export type Status = {
  mode: string;
  version?: string;
  database: { path?: string; file_encrypted?: boolean; plaintext_header?: boolean; driver?: { driver?: string; detail?: string; key_source?: string } };
  llm: { configured: boolean; provider?: string; note?: string };
  encryption?: { requested?: string; file_encrypted?: boolean; driver?: string; driver_detail?: string; field_level_encryption?: boolean; fields_encrypted?: string[] };
  rate_limits?: { scopes?: Record<string, { rate_per_min: number; burst: number }> };
  voice?: { stt?: Record<string, unknown>; tts?: Record<string, unknown> };
};

/** The reply from `POST /api/v1/adaptations/{id}/decision` (services/adaptations.decide).
 *  Every verb returns the same keys, and `ok:false` + `reason` is how "already answered"
 *  arrives -- a 409 with a body, not an error page.
 */
export type DecisionReply = {
  ok: boolean;
  status: string;
  proposal_id: string;
  strategy?: string;
  decision?: string;
  active_adaptation_id?: string | null;
  payload?: Record<string, unknown> | null;
  review?: { metric?: string; window?: Record<string, unknown>; note?: string } | null;
  note?: string | null;
  reason?: string;
};

/** `GET /review/due` and `POST /review/complete` (services/sessions -> due_reviews,
 *  ai1.record_retention_check). `scheduled` is only present because the self-report
 *  route reschedules on purpose -- an answered review that left the topic due forever
 *  would be a queue you stop trusting.
 */
export type ReviewDue = {
  due: {
    topic_id: string;
    title: string;
    due_at: string | null;
    overdue?: boolean;
    half_life_hours?: number | null;
    expected_recall_at_due?: number | null;
    reps?: number;
    lapses?: number;
  }[];
  count: number;
  rule: string;
};

export type ReviewComplete = {
  lag_hours: number | null;
  expected_recall: number | null;
  correct: boolean;
  scheduled?: {
    due_at: string | null;
    half_life_hours?: number | null;
    reps?: number;
    lapses?: number;
    expected_recall_at_due?: number | null;
  } | null;
  was_due_at?: string | null;
};


/** `GET /api/v1/practice` — a ranked queue, and the only place the learner is told why.
 *  Entries never carry an answer key (the server strips it), so `item` here is the same
 *  shape the diagnostic sends.
 */
export type PlanResponse = {
  items: PlanEntry[];
  why?: string;
  student_id?: string;
  mode?: string;
  note?: string;
  plan?: { steps?: { topic_id: string; status?: string; action?: string; needs_first?: boolean }[] };
};

/** `GET /api/v1/assessment/diagnostic` — nested `{item, ...}` on purpose: the plan's
 *  presentation and the bank item travel together so the client cannot rebuild either.
 */
export type DiagnosticResponse = {
  items: {
    item: Item;
    topic_id: string;
    reason?: string;
    presentation?: Presentation;
    answer_key?: never;
  }[];
  /** The server writes the framing for this screen; the client quotes it rather than
   *  paraphrasing a promise about measurement. */
  purpose?: string;
  how_it_is_used?: string;
  note_on_time?: string;
};

/** `GET /api/v1/journey` — services/journey.build + milestones + stats. */
export type Journey = {
  student_id?: string;
  generated_at?: string;
  window_days?: number;
  nodes: JourneyNode[];
  counts?: Record<string, number>;
  phases?: { phase?: string; label?: string; count?: number; [k: string]: unknown }[];
  summary?: string;
  milestones?: { label?: string; title?: string; kind?: string; at?: string; ts?: string; [k: string]: unknown }[];
  stats?: { sessions?: number; attempts?: number; mean_score?: number | null; events?: number };
};
