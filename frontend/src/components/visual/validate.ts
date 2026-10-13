/**
 * The client half of the visual contract (backend: `adapt/lesson/templates.py`).
 *
 * The same seven kinds, the same validation, in miniature: a spec that would
 * be rejected by the backend is rejected here too, and the caller falls back
 * to words with a note. This is defence in depth, not a second opinion — the
 * backend's verdict is the one that counts, and this exists so a stale or
 * hand-written fixture can never put an unvalidated picture on screen.
 */

export const VISUAL_KINDS = [
  "flow",
  "comparison_table",
  "timeline",
  "labelled_diagram",
  "number_line",
  "venn",
  "bar",
] as const;

export type VisualKind = (typeof VISUAL_KINDS)[number];

const ALIASES: Record<string, VisualKind> = {
  comparison: "comparison_table",
  table: "comparison_table",
  diagram: "labelled_diagram",
  labelled: "labelled_diagram",
  number_line_graph: "number_line",
  graph: "number_line",
  line: "number_line",
  set: "venn",
  sets: "venn",
  strip_model: "bar",
  strip: "bar",
  bars: "bar",
};

const MARKUP = /<\s*\/?\s*[a-zA-Z!]|javascript:|data:text\/html/i;
const PLACEHOLDER = /\{\{.*?\}\}|\$\{.*?\}|\[(TODO|TBD|XXX|placeholder)\b/i;
const MAX_ITEMS = 8;
const MAX_TEXT = 220;

export type VisualSpec = {
  kind: VisualKind;
  title: string;
  alt?: string;
  steps?: { label: string; text: string }[];
  columns?: string[];
  rows?: { aspect: string; left: string; right: string }[];
  events?: { when: string; text: string }[];
  whole?: string;
  parts?: { label: string; text: string }[];
  min?: number;
  max?: number;
  points?: { value: number; label: string }[];
  sets?: { label: string }[];
  regions?: Record<string, string>;
  bars?: { label: string; value: number }[];
  unit?: string;
  unfilled?: string[];
};

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value.replace(/\s+/g, " ").trim();
  if (!cleaned || cleaned.length > MAX_TEXT || MARKUP.test(cleaned) || PLACEHOLDER.test(cleaned)) return null;
  return cleaned;
}

function rows(value: unknown): Record<string, unknown>[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_ITEMS) return null;
  return value.filter((v) => v && typeof v === "object") as Record<string, unknown>[];
}

/** Returns the normalised spec, or `null` when it must fall back to words. */
export function validateVisualSpec(raw: unknown): VisualSpec | null {
  if (!raw || typeof raw !== "object") return null;
  const spec = raw as Record<string, unknown>;
  const kindRaw = typeof spec.kind === "string" ? spec.kind.trim() : "";
  const kind = (ALIASES[kindRaw] ?? kindRaw) as VisualKind;
  if (!VISUAL_KINDS.includes(kind)) return null;
  const title = text(spec.title) ?? "Picture";
  const out: VisualSpec = { kind, title, unfilled: [] };

  if (kind === "flow") {
    const items = rows(spec.steps ?? spec.nodes);
    if (!items) return null;
    out.steps = [];
    for (const [i, row] of items.entries()) {
      const body = text(row.text);
      if (!body) return null;
      out.steps.push({ label: text(row.label) ?? `Step ${i + 1}`, text: body });
    }
    if (out.steps.length < 2) return null;
  } else if (kind === "comparison_table") {
    const items = rows(spec.rows);
    if (!items) return null;
    out.columns = ["Aspect", "One side", "The other side"];
    out.rows = [];
    for (const row of items) {
      const aspect = text(row.aspect);
      const left = text(row.left);
      if (!aspect || left === null) return null;
      out.rows.push({ aspect, left, right: text(row.right) ?? "" });
    }
  } else if (kind === "timeline") {
    const items = rows(spec.events ?? spec.nodes);
    if (!items) return null;
    out.events = [];
    for (const row of items) {
      const when = text(row.when);
      const body = text(row.text);
      if (!when || !body) return null;
      out.events.push({ when, text: body });
    }
  } else if (kind === "labelled_diagram") {
    const items = rows(spec.parts ?? spec.labels);
    if (!items) return null;
    out.parts = [];
    for (const row of items) {
      const body = text(row.text);
      if (!body) return null;
      out.parts.push({ label: text(row.label) ?? "", text: body });
    }
    const whole = text(spec.whole);
    if (whole) out.whole = whole;
  } else if (kind === "number_line") {
    const min = spec.min;
    const max = spec.max;
    if (typeof min !== "number" || typeof max !== "number" || !(min < max)) return null;
    const items = rows(spec.points);
    if (!items) return null;
    out.min = min;
    out.max = max;
    out.points = [];
    for (const row of items) {
      const label = text(row.label);
      if (typeof row.value !== "number" || row.value < min || row.value > max || !label) return null;
      out.points.push({ value: row.value, label });
    }
  } else if (kind === "venn") {
    if (!Array.isArray(spec.sets) || spec.sets.length !== 2) return null;
    out.sets = [];
    for (const entry of spec.sets) {
      const label = text(entry && typeof entry === "object" ? (entry as Record<string, unknown>).label : entry);
      if (!label) return null;
      out.sets.push({ label });
    }
    out.regions = {};
    for (const key of ["left_only", "overlap", "right_only"]) {
      const value = text(spec[key]);
      if (value) out.regions[key] = value;
    }
  } else {
    const items = rows(spec.bars);
    if (!items) return null;
    out.bars = [];
    for (const row of items) {
      const label = text(row.label);
      if (!label || typeof row.value !== "number" || row.value < 0) return null;
      out.bars.push({ label, value: row.value });
    }
  }
  const unit = text(spec.unit);
  if (unit) out.unit = unit;
  const alt = text(spec.alt);
  if (alt) out.alt = alt;
  return out;
}

/** The words a rejected picture is replaced by (mirrors the backend note). */
export function visualFallbackText(raw: unknown): { text: string; note: string } {
  const readable: string[] = [];
  const harvest = (value: unknown) => {
    if (typeof value === "string" && value.trim() && !MARKUP.test(value)) readable.push(value.replace(/\s+/g, " ").trim());
    else if (Array.isArray(value)) value.forEach(harvest);
    else if (value && typeof value === "object") Object.values(value).forEach(harvest);
  };
  if (raw && typeof raw === "object") {
    const spec = raw as Record<string, unknown>;
    for (const key of ["title", "steps", "nodes", "rows", "events", "parts", "labels", "points", "bars"]) harvest(spec[key]);
  }
  return {
    text: readable.join(" ") || "This part was meant to be a picture, but there is nothing readable to show in its place yet.",
    note: "a picture was planned here, but it could not be drawn safely, so here it is in words instead",
  };
}
