/**
 * Round 9 shared shapes: the check-in tiles and the syllabus checklist.
 *
 * The words a learner sees for an objective's origin live here, in one
 * place, because they are a honesty surface: "suggested" must never read
 * as "from the syllabus", and an objective the learner added themselves
 * says so. Ids travel in payloads only -- nothing here renders one.
 */

export const CURRICULA = [
  { id: "kenya_cbc", label: "Kenya CBC" },
  { id: "kcse", label: "KCSE" },
  { id: "igcse", label: "IGCSE" },
  { id: "university", label: "University" },
  { id: "self_study", label: "Self-study" },
] as const;

export type CurriculumId = (typeof CURRICULA)[number]["id"];

export type Objective = {
  id: string;
  title: string;
  strand: string;
  level?: string;
  source?: string;
  source_ref?: string;
  origin: "syllabus" | "outline" | "suggested" | "yours" | string;
  attribution?: string;
  licence?: string;
};

export type ObjectiveDraft = Objective & { keep: boolean };

export type ScanReply = {
  subject: string;
  subject_id: string;
  curriculum: string | null;
  grade: string | null;
  objectives: Objective[];
  source: string;
  source_label: string;
  attribution: string;
  licence: string;
  checked: boolean;
  from_cache?: boolean;
  notes?: string[];
  approved_version: number | null;
  proposal_pending?: boolean;
};

export type GuidelineReply = {
  guideline: {
    id: string;
    version: number;
    status: string;
    objectives: Objective[];
    source_label: string;
    attribution: string;
  };
};

export function originLabel(origin: string): string {
  switch (origin) {
    case "syllabus":
      return "From the syllabus";
    case "outline":
      return "From an open outline";
    case "yours":
      return "Added by you";
    default:
      return "Suggested";
  }
}

/** Move one row up (-1) or down (+1). At either edge the list is returned as-is. */
export function moveObjective<T>(rows: readonly T[], index: number, delta: -1 | 1): T[] {
  const next = index + delta;
  if (index < 0 || next < 0 || index >= rows.length || next >= rows.length) return [...rows];
  const out = [...rows];
  [out[index], out[next]] = [out[next], out[index]];
  return out;
}

export const EFFORT_WORDS: Record<number, string> = {
  1: "Very light",
  2: "Very light",
  3: "Light",
  4: "Steady",
  5: "Steady",
  6: "Demanding",
  7: "Heavy",
  8: "Heavy",
  9: "Very heavy",
};

export const EMOTIONS = [
  { id: "enjoyment", label: "Enjoying it" },
  { id: "pride", label: "Proud" },
  { id: "hope", label: "Hopeful" },
  { id: "anxiety", label: "Anxious" },
  { id: "frustration", label: "Frustrated" },
  { id: "boredom", label: "Bored" },
] as const;

export const HELP_CHOICES = [
  { id: "choice", label: "A choice of what is next" },
  { id: "win", label: "A small win" },
  { id: "together", label: "Learn it with someone" },
] as const;

export type CheckInReply = {
  id: string;
  context: string;
  tiles: { effort: number; emotion?: string | null; help?: string | null };
  note?: string;
  estimate: {
    primary_emotion?: string;
    intensity?: number;
    cognitive_load?: string;
    engagement?: string;
    frustration?: string;
    motivation?: string;
    recommended_action?: string;
    summary?: string;
  };
  metrics: { n_recent?: number; accuracy?: number | null };
  source: "simple" | "model" | string;
  next_lesson?: {
    changed: boolean;
    reason: string;
    break_suggested: boolean;
    length: string;
    challenge_delta: number;
  };
  read_more?: { title: string; url: string; attribution: string; licence?: string }[];
};
