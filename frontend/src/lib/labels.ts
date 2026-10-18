/** Friendly names for internal skill, step, and factor identifiers. Unknown identifiers are omitted. */
export const LABELS: Record<string, string> = {
  codewords_study: "Word memory", codewords_cued: "Word recall", codewords_next_day: "Word recall",
  codewords_delayed: "Delayed recall", quick_a: "Quick thinking", quick_b: "Quick thinking", quick_c: "Quick thinking",
  locked_box: "Logic", vault: "Pattern memory", shopkeeper: "Number sense", machine: "Reasoning", trap: "Careful thinking",
  prior_knowledge: "What you already know", working_memory: "Working memory", processing_pace: "Thinking pace",
  attention_self_control: "Focus", long_term_memory: "Long-term memory", reasoning: "Reasoning",
  metacognition: "Learning awareness", persistence_help_seeking: "Persistence",
  "GEOMETRY.SPATIAL": "Geometry", geometry_spatial: "Geometry", spatial_reasoning: "Spatial reasoning",
  quick: "Quick thinking", angles: "Angles", logic: "Logic", fractions: "Fractions", geometry: "Geometry",
};
export function friendlyLabel(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const text = value.trim();
  if (LABELS[text]) return LABELS[text];
  if (/^[a-z]+_[a-z]+$/i.test(text) || /^\w+\.\w+$/.test(text)) return null;
  return text;
}
