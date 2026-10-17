"use client";

/** Code-owned visual lessons. The lesson writer sends data; this component draws only primitives. */
import { useEffect, useMemo, useRef, useState } from "react";

export type SceneSpec = {
  template: string;
  params?: Record<string, unknown>;
  captions?: string[];
  source?: "model" | "simple";
  frames?: SceneFrame[];
};
export type SceneObject = {
  type: string;
  x?: number; y?: number; x1?: number; y1?: number; x2?: number; y2?: number;
  cx?: number; cy?: number; r?: number; width?: number; height?: number; size?: number;
  label?: string; text?: string; labels?: string[]; points?: string;
  kind?: string; a?: number; b?: number;
};
export type SceneFrame = { step: number; caption: string; objects: SceneObject[] };

const TEMPLATE_NAMES = [
  "angle-sum-of-triangle", "pythagoras", "similar-triangles", "number-line-operations", "fraction-bars",
  "function-plot", "vector-force", "car-motion", "probability-tree", "venn-sets", "graph-traversal",
  "binary-tree", "recursion-stack", "sorting", "binary-search", "logic-gates", "cpu-cycle", "cycle-diagram",
  "timeline", "flowchart", "cause-effect-chain", "grammar-sentence-diagram",
] as const;

function values(spec: SceneSpec): string[] {
  const p = spec.params ?? {};
  const first = Object.values(p).find((value) => Array.isArray(value));
  return Array.isArray(first) ? first.slice(0, 7).map((v) => Array.isArray(v) ? v.join("/") : String(v)) : [spec.template.replaceAll("-", " ")];
}

/** Deterministic fallback used when the API returns only a checked spec. */
export function renderScene(spec: SceneSpec): SceneFrame[] {
  if (spec.frames?.length) return spec.frames;
  const captions = spec.captions?.length ? spec.captions : ["Look at the whole picture.", "Pause and predict the next step.", "Say what changed."];
  const labels = values(spec);
  return captions.slice(0, 8).map((caption, step) => {
    const objects: SceneObject[] = [{ type: "text", x: 12, y: 17, text: caption }];
    if (["angle-sum-of-triangle", "pythagoras", "similar-triangles"].includes(spec.template)) {
      objects.push({ type: "triangle", x: 34, y: 30, size: 76, labels: ["A", "B", "C"] });
    } else if (spec.template === "venn-sets") {
      objects.push({ type: "circle", cx: 88, cy: 67, r: 30 }, { type: "circle", cx: 132, cy: 67, r: 30 });
    } else if (spec.template === "car-motion") {
      const speed = Number(spec.params?.speed ?? 20);
      objects.push({ type: "line", x1: 18, y1: 72, x2: 222, y2: 72 });
      objects.push({ type: "car", x: 18 + Math.min(190, (speed / 120) * 24 * (step + 1)), y: 58 });
    } else if (spec.template === "function-plot") {
      const a = Number(spec.params?.a ?? 1), b = Number(spec.params?.b ?? 0);
      objects.push({ type: "line", x1: 18, y1: 70, x2: 222, y2: 70 }, { type: "line", x1: 28, y1: 82, x2: 28, y2: 22 });
      objects.push({ type: "graph", kind: String(spec.params?.kind ?? "linear"), a, b });
    } else if (spec.template === "grammar-sentence-diagram") {
      labels.slice(0, 5).forEach((label, i) => objects.push({ type: "rectangle", x: 14 + i * 32, y: 44, width: 28, height: 22, label }));
      objects.push({ type: "person", x: 202, y: 48 });
    } else if (["number-line-operations", "timeline"].includes(spec.template)) {
      objects.push({ type: "line", x1: 18, y1: 70, x2: 222, y2: 70 });
      for (let i = 0; i < 5; i += 1) objects.push({ type: "line", x1: 38 + i * 40, y1: 64, x2: 38 + i * 40, y2: 76, label: String(i) });
    } else {
      labels.forEach((label, i) => objects.push({ type: "node", x: 30 + i * 34, y: 65 + ((i + step) % 2) * 18, label }));
    }
    return { step, caption, objects };
  });
}

function Shape({ object, index }: { object: SceneObject; index: number }) {
  const key = `${object.type}-${index}`;
  const common = { stroke: "var(--accent)", fill: "none", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (object.type === "triangle") {
    const x = object.x ?? 30, y = object.y ?? 30, size = object.size ?? 70;
    return <g key={key}><path {...common} d={`M ${x} ${y + size} L ${x + size} ${y + size} L ${x + size * .44} ${y} Z`} /><text className="scene-label" x={x - 3} y={y + size + 12}>A</text><text className="scene-label" x={x + size + 3} y={y + size + 12}>B</text><text className="scene-label" x={x + size * .44} y={y - 5}>C</text></g>;
  }
  if (object.type === "line") return <line key={key} {...common} x1={object.x1 ?? 10} y1={object.y1 ?? 60} x2={object.x2 ?? 220} y2={object.y2 ?? 60} markerEnd="url(#scene-arrow)" />;
  if (object.type === "circle") return <circle key={key} {...common} cx={object.cx ?? 80} cy={object.cy ?? 60} r={object.r ?? 24} />;
  if (object.type === "rectangle") return <g key={key}><rect {...common} x={object.x ?? 10} y={object.y ?? 40} width={object.width ?? 28} height={object.height ?? 24} rx={3} /><text className="scene-label" x={(object.x ?? 10) + 14} y={(object.y ?? 40) + 16} textAnchor="middle">{object.label}</text></g>;
  if (object.type === "text") return <text key={key} className="scene-caption" x={object.x ?? 10} y={object.y ?? 18}>{object.text}</text>;
  if (object.type === "node") return <g key={key}><circle {...common} cx={object.x ?? 40} cy={object.y ?? 60} r={16} fill="var(--accent-dim)" /><text className="scene-label" x={object.x ?? 40} y={(object.y ?? 60) + 4} textAnchor="middle">{object.label}</text></g>;
  if (object.type === "car") {
    const x = object.x ?? 40, y = object.y ?? 55;
    return (
      <g key={key}>
        <rect {...common} x={x} y={y} width={30} height={12} rx={5} />
        <path {...common} d={`M ${x + 7} ${y} L ${x + 10} ${y - 7} L ${x + 22} ${y - 7} L ${x + 25} ${y}`} />
        <circle {...common} cx={x + 8} cy={y + 13} r={3.5} fill="var(--accent-dim)" />
        <circle {...common} cx={x + 23} cy={y + 13} r={3.5} fill="var(--accent-dim)" />
      </g>
    );
  }
  if (object.type === "person") {
    const x = object.x ?? 40, y = object.y ?? 55;
    return (
      <g key={key}>
        <circle {...common} cx={x} cy={y} r={6} fill="var(--accent-dim)" />
        <path {...common} d={`M ${x} ${y + 6} L ${x} ${y + 20} M ${x} ${y + 10} L ${x - 8} ${y + 15} M ${x} ${y + 10} L ${x + 8} ${y + 15} M ${x} ${y + 20} L ${x - 7} ${y + 30} M ${x} ${y + 20} L ${x + 7} ${y + 30}`} />
      </g>
    );
  }
  if (object.type === "graph") {
    const a = Number(object.a ?? 1), b = Number(object.b ?? 0), kind = object.kind ?? "linear";
    const x0 = 28, y0 = 70, w = 180, h = 44;
    const fx = (t: number) => (kind === "quadratic" ? a * t * t + b * t : a * t + b);
    const points: string[] = [];
    for (let i = 0; i <= 16; i += 1) {
      const t = -1 + (2 * i) / 16;
      points.push(`${x0 + ((t + 1) / 2) * w},${y0 - (fx(t) / 10) * h}`);
    }
    return <polyline key={key} {...common} points={points.join(" ")} />;
  }
  return null;
}

export function SceneStage({ spec, className = "" }: { spec: SceneSpec; className?: string }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [reduced, setReduced] = useState(false);
  const frames = useMemo(() => renderScene(spec), [spec]);
  const frame = frames[Math.min(index, Math.max(frames.length - 1, 0))];
  const timer = useRef<number | null>(null);
  useEffect(() => { setIndex(0); setPlaying(false); }, [spec]);
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (query) { setReduced(query.matches); const handler = () => setReduced(query.matches); query.addEventListener?.("change", handler); return () => query.removeEventListener?.("change", handler); }
  }, []);
  useEffect(() => {
    if (reduced) { setPlaying(false); setIndex(Math.max(0, frames.length - 1)); return; }
    if (!playing || index >= frames.length - 1) { if (index >= frames.length - 1) setPlaying(false); return; }
    timer.current = window.setTimeout(() => setIndex((value) => Math.min(frames.length - 1, value + 1)), 1600 / speed);
    return () => { if (timer.current !== null) window.clearTimeout(timer.current); };
  }, [frames.length, index, playing, reduced, speed]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return; if (event.key === " ") { event.preventDefault(); setPlaying((v) => !v); } if (event.key === "ArrowRight") setIndex((v) => Math.min(frames.length - 1, v + 1)); if (event.key === "ArrowLeft") setIndex((v) => Math.max(0, v - 1)); };
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  }, [frames.length]);
  if (!frame) return null;
  return <figure className={`scene-stage ${className}`} data-template={spec.template} data-source={spec.source ?? "simple"}>
    <svg viewBox="0 0 240 112" role="img" aria-label={`${spec.template.replaceAll("-", " ")}: ${frame.caption}`}>
      <defs><marker id="scene-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" fill="var(--accent)" /></marker></defs>
      {frame.objects.map((object, item) => <Shape object={object} index={item} key={`${frame.step}-${item}`} />)}
    </svg>
    <figcaption className="scene-caption-under" aria-live="polite">{frame.caption}</figcaption>
    <div className="scene-controls" role="group" aria-label="Animation controls">
      <button type="button" onClick={() => setPlaying((v) => !v)} aria-label={playing ? "Pause animation" : "Play animation"}>{playing ? "Pause" : "Play"}</button>
      <button type="button" onClick={() => setIndex((v) => Math.max(0, v - 1))} aria-label="Previous step">Back</button>
      <button type="button" onClick={() => setIndex((v) => Math.min(frames.length - 1, v + 1))} aria-label="Next step">Step</button>
      <button type="button" onClick={() => { setIndex(0); setPlaying(false); }} aria-label="Replay animation">Replay</button>
      <input type="range" min={0} max={Math.max(0, frames.length - 1)} value={index} onChange={(event) => setIndex(Number(event.target.value))} aria-label="Animation progress" />
      <label>Speed <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}><option value={0.5}>0.5×</option><option value={1}>1×</option><option value={1.5}>1.5×</option><option value={2}>2×</option></select></label>
    </div>
  </figure>;
}

export { TEMPLATE_NAMES };
