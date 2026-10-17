"use client";

/**
 * The seven visual templates as React SVG (brief §8, Phase 5).
 *
 * One dispatcher, one job: take a spec, validate it with the same rules the
 * backend uses (`./validate`), and either draw it or — with a note saying
 * so — show the same content in words. A picture that cannot be drawn
 * safely is never drawn at all, and the fallback keeps the facts: only the
 * picture is lost.
 *
 * Every figure is inline SVG with a `<title>`; there are no external
 * images, no scripts and nothing a spec could smuggle in as markup — JSX
 * escapes every string it is handed, which is the point of drawing with
 * elements instead of `dangerouslySetInnerHTML`.
 */
import type { ReactNode } from "react";

import { validateVisualSpec, visualFallbackText, type VisualSpec } from "./validate";

export { VISUAL_KINDS, validateVisualSpec } from "./validate";
export type { VisualSpec } from "./validate";

const INK = "var(--ink)";
const ACCENT = "#33507e";
const WARM = "#b3552e";
const FAINT = "var(--muted)";

function Frame({ spec, children, height }: { spec: VisualSpec; children: ReactNode; height: number }) {
  return (
    <figure className="m-0">
      <svg viewBox={`0 0 640 ${height}`} role="img" aria-label={spec.alt ?? spec.title} className="w-full max-w-[640px]">
        <title>{spec.title}</title>
        {children}
      </svg>
      <figcaption className="mt-1 t-small text-[var(--muted)]">
        {spec.title}
        {spec.unfilled?.length ? ` · ${spec.unfilled.length} part${spec.unfilled.length === 1 ? "" : "s"} left empty rather than guessed` : ""}
      </figcaption>
    </figure>
  );
}

function T({ x, y, children, size = 13, anchor = "start", bold = false, fill = INK }: {
  x: number; y: number; children: ReactNode; size?: number; anchor?: string; bold?: boolean; fill?: string;
}) {
  return (
    <text x={x} y={y} fontSize={size} textAnchor={anchor as never} fontWeight={bold ? "bold" : "normal"} fill={fill}>
      {children}
    </text>
  );
}

function wrap(text: string, chars = 52): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    if (current && current.length + 1 + word.length > chars) {
      lines.push(current);
      current = word;
    } else {
      current = current ? `${current} ${word}` : word;
    }
  }
  if (current) lines.push(current);
  return lines.slice(0, 3);
}

/* ------------------------------------------------------------- templates ---- */

function FlowFigure({ spec }: { spec: VisualSpec }) {
  const steps = spec.steps ?? [];
  let y = 14;
  const boxes: ReactNode[] = [];
  steps.forEach((step, i) => {
    const lines = wrap(step.text);
    const h = 26 + 16 * lines.length;
    boxes.push(
      <g key={i}>
        <rect x={16} y={y} width={608} height={h} rx={4} fill="#f4f6fb" stroke="#9db2d8" />
        <T x={28} y={y + 20} size={12} bold fill={ACCENT}>{step.label}</T>
        {lines.map((line, j) => (
          <T key={j} x={28} y={y + 38 + 16 * j}>{line}</T>
        ))}
      </g>,
    );
    y += h;
    if (i < steps.length - 1) {
      boxes.push(
        <g key={`a${i}`}>
          <line x1={320} y1={y} x2={320} y2={y + 14} stroke={ACCENT} strokeWidth={2} />
          <polygon points={`315,${y + 14} 325,${y + 14} 320,${y + 22}`} fill={ACCENT} />
        </g>,
      );
      y += 22;
    }
  });
  return <Frame spec={spec} height={y + 12}>{boxes}</Frame>;
}

function ComparisonFigure({ spec }: { spec: VisualSpec }) {
  const rows = spec.rows ?? [];
  let y = 40;
  const body: ReactNode[] = [
    <g key="head">
      {(spec.columns ?? []).slice(0, 3).map((col, j) => (
        <T key={j} x={[20, 174, 394][j]} y={24} size={12} bold fill={ACCENT}>{col}</T>
      ))}
      <line x1={16} y1={32} x2={624} y2={32} stroke="#9db2d8" />
    </g>,
  ];
  rows.forEach((row, i) => {
    const cells = [row.aspect, row.left, row.right].map((c, j) => wrap(c, [18, 26, 28][j]));
    const h = 14 + 16 * Math.max(...cells.map((c) => c.length));
    body.push(
      <g key={i}>
        {cells.map((lines, j) =>
          lines.map((line, k) => (
            <T key={`${j}-${k}`} x={[20, 174, 394][j]} y={y + 14 + 16 * k} size={12}>{line}</T>
          )),
        )}
        <line x1={16} y1={y + h} x2={624} y2={y + h} stroke="#e3e8f2" />
      </g>,
    );
    y += h + 6;
  });
  return <Frame spec={spec} height={y + 8}>{body}</Frame>;
}

function TimelineFigure({ spec }: { spec: VisualSpec }) {
  const events = spec.events ?? [];
  let y = 22;
  const body: ReactNode[] = [<line key="axis" x1={40} y1={18} x2={40} y2={18 + 44 * events.length} stroke="#9db2d8" strokeWidth={2} />];
  events.forEach((event, i) => {
    body.push(
      <g key={i}>
        <circle cx={40} cy={y} r={6} fill={ACCENT} />
        <T x={58} y={y - 2} size={12} bold fill={ACCENT}>{event.when}</T>
        {wrap(event.text, 64).map((line, k) => (
          <T key={k} x={58} y={y + 14 + 15 * k} size={12}>{line}</T>
        ))}
      </g>,
    );
    y += 44;
  });
  return <Frame spec={spec} height={y + 8}>{body}</Frame>;
}

function LabelledFigure({ spec }: { spec: VisualSpec }) {
  const parts = spec.parts ?? [];
  let y = spec.whole ? 40 : 14;
  const body: ReactNode[] = [];
  if (spec.whole) {
    body.push(<T key="whole" x={320} y={22} size={14} bold anchor="middle" fill={ACCENT}>{spec.whole}</T>);
  }
  body.push(
    <g key="hub">
      <rect x={230} y={y} width={180} height={30} rx={15} fill="#f4f6fb" stroke="#9db2d8" />
      <T x={320} y={y + 20} size={12} bold anchor="middle">{spec.title.slice(0, 28)}</T>
    </g>,
  );
  y += 52;
  parts.forEach((part, i) => {
    body.push(
      <g key={i}>
        <rect x={16} y={y} width={608} height={34} rx={4} fill="#fbfcfe" stroke="#e3e8f2" />
        <T x={28} y={y + 21} size={12} bold fill={ACCENT}>{`${part.label || "part"}:`}</T>
        <T x={28 + 9 * (part.label.length + 1) + 14} y={y + 21} size={12}>{wrap(part.text, 58)[0]}</T>
      </g>,
    );
    y += 42;
  });
  return <Frame spec={spec} height={y + 8}>{body}</Frame>;
}

function NumberLineFigure({ spec }: { spec: VisualSpec }) {
  const low = spec.min ?? 0;
  const high = spec.max ?? 1;
  const span = high - low || 1;
  const px = (v: number) => 48 + ((v - low) / span) * 544;
  return (
    <Frame spec={spec} height={150}>
      <line x1={48} y1={92} x2={592} y2={92} stroke={ACCENT} strokeWidth={2} />
      <polygon points={`592,92 583,87 583,97`} fill={ACCENT} />
      <T x={48} y={118} size={11} anchor="middle">{low}</T>
      <T x={592} y={118} size={11} anchor="middle">{high}</T>
      {(spec.points ?? []).map((point, i) => (
        <g key={i}>
          <circle cx={px(point.value)} cy={92} r={5} fill={WARM} />
          <T x={px(point.value)} y={i % 2 === 0 ? 78 : 132} size={11} anchor="middle" bold>{point.label}</T>
          <T x={px(point.value)} y={i % 2 === 0 ? 118 : 66} size={10} anchor="middle" fill={FAINT}>{point.value}</T>
        </g>
      ))}
      {spec.unit ? <T x={320} y={30} size={11} anchor="middle" fill={FAINT}>{`unit: ${spec.unit}`}</T> : null}
    </Frame>
  );
}

function VennFigure({ spec }: { spec: VisualSpec }) {
  const sets = spec.sets ?? [];
  const regions = spec.regions ?? {};
  return (
    <Frame spec={spec} height={208}>
      <circle cx={252} cy={110} r={78} fill="#dbe7f7" fillOpacity={0.75} stroke={ACCENT} />
      <circle cx={388} cy={110} r={78} fill="#f7e3d3" fillOpacity={0.75} stroke={WARM} />
      <T x={182} y={52} size={13} bold fill={ACCENT}>{sets[0]?.label}</T>
      <T x={458} y={52} size={13} bold anchor="end" fill="#8a3d1c">{sets[1]?.label}</T>
      {regions.left_only ? <T x={214} y={112} size={11} anchor="middle">{wrap(regions.left_only, 16)[0]}</T> : null}
      {regions.overlap ? <T x={320} y={112} size={11} anchor="middle" bold>{wrap(regions.overlap, 14)[0]}</T> : null}
      {regions.right_only ? <T x={426} y={112} size={11} anchor="middle">{wrap(regions.right_only, 16)[0]}</T> : null}
    </Frame>
  );
}

function BarFigure({ spec }: { spec: VisualSpec }) {
  const bars = spec.bars ?? [];
  const top = Math.max(...bars.map((b) => b.value), 1);
  let y = 14;
  const body: ReactNode[] = [];
  bars.forEach((bar, i) => {
    const w = Math.max(2, (bar.value / top) * 420);
    body.push(
      <g key={i}>
        <T x={16} y={y + 14} size={12}>{bar.label}</T>
        <rect x={170} y={y} width={w} height={18} fill={ACCENT} />
        <T x={178 + w} y={y + 14} size={11}>{`${bar.value}${spec.unit ? ` ${spec.unit}` : ""}`}</T>
      </g>,
    );
    y += 28;
  });
  return <Frame spec={spec} height={y + 10}>{body}</Frame>;
}

const RENDERERS: Record<VisualSpec["kind"], (props: { spec: VisualSpec }) => ReactNode> = {
  flow: FlowFigure,
  comparison_table: ComparisonFigure,
  timeline: TimelineFigure,
  labelled_diagram: LabelledFigure,
  number_line: NumberLineFigure,
  venn: VennFigure,
  bar: BarFigure,
};

/**
 * Draw a spec, or say in words why it was not drawn.
 *
 * `fallbackNote` from the backend wins when present (it carries the actual
 * validation reasons); the local validator is the second line of defence.
 */
export function VisualFigure({ spec, fallbackNote }: { spec: unknown; fallbackNote?: string }) {
  const validated = validateVisualSpec(spec);
  if (!validated) {
    const fallback = visualFallbackText(spec);
    return (
      <div className="rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
        <p className="t-small">{fallback.text}</p>
        <p className="mt-1 t-small text-[var(--warn)]">{fallbackNote ?? fallback.note}</p>
      </div>
    );
  }
  const Renderer = RENDERERS[validated.kind];
  return <Renderer spec={validated} />;
}
