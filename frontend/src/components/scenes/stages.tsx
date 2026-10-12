"use client";

/** The four built-in stages, plus the honest one.
 *
 * A renderer here never decides anything: it is given one frame of a trace the backend
 * computed and draws `state` as it stands. The only thing a stage is allowed to add is
 * emphasis, from `highlights` — which is why an unrecognised highlight id is reported on
 * screen instead of dropped, and why an unrecognised *kind* shows a readable table of the
 * same `state` rather than an empty box.
 *
 * `frame.state` is data the simulation produced, so it is put in as text nodes. No scene
 * content is ever assembled as markup (`player.test.mjs` fails the build if this file
 * starts using innerHTML).
 */
import type { Frame } from "@/lib/engine/types";

const CELL =
  "min-w-11 rounded border px-1.5 py-1 text-center font-mono text-[13px] leading-tight transition-colors";
const LINE = "border-[var(--line)]";
const MUTED = "text-[var(--muted)]";

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}
function list(v: unknown): number[] {
  return Array.isArray(v) ? v.filter((x): x is number => typeof x === "number") : [];
}
function has(hi: string[], id: string): boolean {
  return hi.includes(id);
}

/** One row of indexed boxes — the shape half of these simulations share. */
function Cells({
  values,
  mark,
}: {
  values: unknown[];
  mark: (index: number) => { tone?: "good" | "warn" | "bad"; dim?: boolean; ring?: boolean; label?: string };
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {values.map((v, i) => {
        const m = mark(i);
        const tone =
          m.tone === "good"
            ? "border-[var(--good)] bg-[var(--good)]/15"
            : m.tone === "warn"
              ? "border-[var(--warn)] bg-[var(--warn)]/10"
              : m.tone === "bad"
                ? "border-[var(--bad)] bg-[var(--bad)]/10"
                : LINE;
        return (
          <div key={i} className="flex flex-col items-center gap-0.5">
            <span
              className={`${CELL} ${tone} ${m.dim ? "opacity-30" : ""} ${m.ring ? "outline outline-2 outline-[var(--accent)]" : ""}`}
            >
              {String(v)}
            </span>
            <span className={`${MUTED} text-[10px]`} aria-hidden="true">
              {m.label ?? i}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Reg({ name, value, lit }: { name: string; value: unknown; lit?: boolean }) {
  return (
    <div
      className={`rounded border px-2 py-1 font-mono text-[12px] ${lit ? "border-[var(--accent)] bg-[var(--accent)]/10" : LINE}`}
    >
      <span className={MUTED}>{name}</span> <span className="font-semibold">{value === null || value === undefined ? "—" : String(value)}</span>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[13px]">
      <span className={`${MUTED} w-28 shrink-0 text-[11px] uppercase tracking-wide`}>{label}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ binary search ---- */

export function BinarySearchStage({ frame }: { frame: Frame }) {
  const st = frame.state ?? {};
  const values = Array.isArray(st.values) ? st.values : [];
  const lo = num(st.lo, 0);
  const hi = num(st.hi, Math.max(values.length - 1, 0));
  const mid = st.mid === null || st.mid === undefined ? null : num(st.mid, -1);
  const dropped = list(st.dropped);
  const compared = list(st.compared);
  const found = st.found === null || st.found === undefined ? null : num(st.found, -1);
  const inRange = (i: number) => i >= lo && i <= hi;
  return (
    <div className="flex flex-col gap-3">
      <Cells
        values={values}
        mark={(i) => ({
          dim: dropped.includes(i),
          tone: found === i ? "good" : mid === i ? "warn" : undefined,
          ring: (mid === i && has(frame.highlights ?? [], "mid")) || (found === i && has(frame.highlights ?? [], "found")),
        })}
      />
      <Row label="range still live">
        <span className="font-mono text-[13px]">
          lo {lo} … hi {hi}
        </span>
        <span className={`${MUTED} ml-2`}>
          {num(st.live, Math.max(hi - lo + 1, 0))} of {values.length} places left
        </span>
      </Row>
      <Row label="target / probes">
        <span className="font-mono text-[13px]">{st.target === undefined ? "—" : String(st.target)}</span>
        <span className={`${MUTED} ml-2`}>
          compared {compared.length ? compared.map((i) => `arr[${i}]`).join(", ") : "nothing yet"}
        </span>
      </Row>
    </div>
  );
}

/* -------------------------------------------------------------------------- sorting ---- */

export function SortingStage({ frame }: { frame: Frame }) {
  const st = frame.state ?? {};
  const values = Array.isArray(st.values) ? st.values : [];
  const holes = list(st.holes);
  const placed = num(st.placed_upto, -1);
  const i = num(st.i, -1);
  const j = num(st.j, -1);
  const held = st.held === null || st.held === undefined ? null : Number(st.held);
  const comparePair = has(frame.highlights ?? [], "compare");
  return (
    <div className="flex flex-col gap-3">
      {Number.isFinite(held ?? NaN) && held !== null ? (
        <div className="text-[13px]">
          <span className={MUTED}>holding</span> <span className="font-mono font-semibold">{held}</span>{" "}
          <span className={`${MUTED} text-[12px]`}>— walking it left until it fits</span>
        </div>
      ) : null}
      <Cells
        values={values}
        mark={(k) => ({
          tone: holes.includes(k) ? "warn" : k <= placed ? "good" : undefined,
          dim: holes.includes(k),
          ring: comparePair && (k === i || k === j || k === j + 1),
          label: k === i ? `i=${k}` : k === j ? `j=${k}` : undefined,
        })}
      />
      <Row label="sorted prefix">
        <span className="font-mono text-[13px]">
          0 … {placed < 0 ? "—" : placed}
        </span>
        <span className={`${MUTED} ml-2`}>
          {st.comparisons !== undefined ? `${String(st.comparisons)} comparisons, ` : ""}
          {st.shifts !== undefined ? `${String(st.shifts)} shifts` : "holes are where values were pushed right"}
        </span>
      </Row>
    </div>
  );
}

/* ---------------------------------------------------------------------- logic gates ---- */

export function LogicGatesStage({ frame }: { frame: Frame }) {
  const st = frame.state ?? {};
  const rows = Array.isArray(st.table) ? (st.table as Record<string, unknown>[]) : [];
  const current = st.row === null || st.row === undefined ? -1 : num(st.row, -1);
  const inputsLit = has(frame.highlights ?? [], "inputs");
  const outLit = has(frame.highlights ?? [], "output");
  const bit = (v: unknown) => (v === 1 || v === 0 ? String(v) : "—");
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Reg name="A" value={bit(st.a)} lit={inputsLit} />
        <Reg name="B" value={bit(st.b)} lit={inputsLit} />
        <span className={`${MUTED} rounded border border-dashed px-2 py-1 font-mono text-[12px] ${LINE}`}>
          {st.gate === undefined ? "gate" : String(st.gate)}
        </span>
        <Reg name="out" value={bit(st.out)} lit={outLit} />
      </div>
      <table className="w-full max-w-sm border-collapse text-[13px]">
        <thead>
          <tr className={`${MUTED} text-left`}>
            <th className="border-b py-1 font-medium">A</th>
            <th className="border-b py-1 font-medium">B</th>
            <th className="border-b py-1 font-medium">out</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map((r, k) => (
            <tr key={k} className={k === current ? "bg-[var(--accent)]/10" : undefined}>
              <td className="border-b py-1">{bit(r.a)}</td>
              <td className="border-b py-1">{bit(r.b)}</td>
              <td className="border-b py-1">
                {r.out === null || r.out === undefined ? <span className={MUTED}>unknown</span> : String(r.out)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------------ cpu cycle ---- */

export function CpuStage({ frame }: { frame: Frame }) {
  const st = frame.state ?? {};
  const cells = Array.isArray(st.cells) ? st.cells : [];
  const dataAt = num(st.data_at, 0);
  const hi = frame.highlights ?? [];
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-1.5">
        <Reg name="PC" value={st.pc} lit={has(hi, "pc")} />
        <Reg name="A" value={st.a} lit={has(hi, "a")} />
        <Reg name="Z" value={st.z === true ? "true" : st.z === false ? "false" : null} />
        <Reg name="MAR" value={st.mar} lit={has(hi, "mar")} />
        <Reg name="MDR" value={st.mdr} lit={has(hi, "mdr")} />
        <Reg name="IR" value={st.ir} lit={has(hi, "ir")} />
      </div>
      <Row label={`memory · cycle ${st.cycle === undefined ? "—" : String(st.cycle)}`}>
        <div className="flex flex-wrap gap-1">
          {cells.map((c, k) => (
            <span
              key={k}
              className={`font-mono text-[12px] ${k < dataAt ? "rounded px-1" : "rounded px-1"} ${
                k < dataAt ? "bg-[var(--panel)]" : "bg-[var(--bg)]"
              } ${has(hi, "cells") && k === num(st.mar, -1) ? "outline outline-2 outline-[var(--accent)]" : ""} border ${LINE}`}
              title={k < dataAt ? "instruction" : "data"}
            >
              <span className={MUTED}>{k}</span> {String(c)}
            </span>
          ))}
        </div>
      </Row>
      <div className={`${MUTED} text-[11px]`}>
        {cells.length ? `cells 0–${dataAt - 1} hold the program; ${dataAt}–${cells.length - 1} hold the data` : ""}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- the fallback ---- */

/**
 * What "no renderer" looks like, on purpose.
 *
 * A scene whose kind this file has never heard of is a real lesson with correct frames and a
 * missing picture. Blank stage would tell the learner the *lesson* was broken. So: the same
 * narration, the same `cause`, the machine's own state as text, and a sentence saying which
 * part is absent.
 */
export function UnknownStage({ frame, kind }: { frame: Frame; kind: string }) {
  const entries = Object.entries(frame.state ?? {});
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px] text-[var(--warn)]">
        There is no picture for a scene of kind <span className="font-mono">{kind}</span>. The frames below are the
        real ones — this is a renderer gap, not a broken lesson.
      </p>
      {entries.length ? (
        <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-2 border-b border-dashed py-0.5">
              <dt className={`${MUTED} w-28 shrink-0 font-mono text-[11px]`}>{k}</dt>
              <dd className="min-w-0 font-mono text-[12px] break-words">{render(v)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className={`${MUTED} text-[13px]`}>this frame carries no state</p>
      )}
    </div>
  );
}

/** A scalar, a list of scalars, or nothing worse than a compact list. Never markup. */
function render(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" || typeof v === "boolean" || typeof v === "string") return String(v);
  if (Array.isArray(v)) {
    if (v.every((x) => x === null || ["number", "boolean", "string"].includes(typeof x))) {
      return v.length > 12 ? `${v.slice(0, 12).map((x) => String(x)).join(", ")} … (${v.length})` : v.map((x) => String(x)).join(", ");
    }
    return `${v.length} rows`;
  }
  return "…";
}

export const STAGES: Record<string, (props: { frame: Frame }) => React.ReactElement> = {
  binary_search: BinarySearchStage,
  sorting: SortingStage,
  logic_gates: LogicGatesStage,
  cpu_cycle: CpuStage,
};
