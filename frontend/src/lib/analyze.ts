/** One interpretation of `POST /api/v1/analyze`, shared by both loop screens.
 *
 * The response is a pile of parallel lists — `loop`, `hypotheses`, `proposals`, `notes` —
 * and the pages need "is there anything for the learner to decide, and in whose words".
 * Deriving that in one place is the difference between the two screens agreeing about what
 * a session produced.
 */
export type AnalyzeRaw = {
  loop?: { step: string; done: string; evidence?: string }[];
  hypotheses?: Record<string, unknown>[];
  proposals?: Record<string, unknown>[];
  evidence?: Record<string, unknown>[];
  traces?: Record<string, unknown>[];
  evaluated?: Record<string, unknown>[];
  gaps?: Record<string, unknown>[];
  notes?: string[];
  retention?: Record<string, unknown>;
  plan?: { steps?: Record<string, unknown>[] };
  provider?: string;
  llm?: { used?: boolean; [k: string]: unknown };
  model_ms?: number;
  session_id?: string;
  student_id?: string;
};

export type AnalyzeSummary = {
  loop: { step: string; done: string; evidence?: string }[];
  hypotheses: number;
  proposals: number;
  evidence: number;
  note: string | null;
  provider: string | null;
  next: string[];
};

export function summarise(raw: AnalyzeRaw | null | undefined): AnalyzeSummary {
  const loop = raw?.loop ?? [];
  const notes = raw?.notes ?? [];
  const proposals = raw?.proposals ?? [];
  return {
    loop,
    hypotheses: (raw?.hypotheses ?? []).length,
    proposals: proposals.length,
    evidence: (raw?.evidence ?? []).length,
    // The server writes the honest sentence when there is nothing to propose; inventing a
    // cheerful one here would be the app talking about the learner instead of to them.
    note: notes[0] ?? (proposals.length ? `${proposals.length} change${proposals.length === 1 ? "" : "s"} waiting for your answer` : null),
    provider: raw?.provider ?? null,
    next: loop
      .filter((l) => String(l.done).startsWith("proposals awaiting"))
      .map((l) => l.done)
      .concat(proposals.length ? ["answer the proposal: yes, no, change it, or later"] : []),
  };
}
