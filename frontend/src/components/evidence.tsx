"use client";

/**
 * One component for the app's most-repeated honesty problem: a number computed from four
 * observations, shown as if it were a finding.
 *
 * Three surfaces were doing this — the active-change panel (`effect_summary`), the method
 * comparison, and the mastery bars in progress — and each one invented its own wording. The
 * rule is now in one place:
 *
 * - **n is always shown.** Not "a few answers", not a colour: the count.
 * - **under the evidence threshold it is called an early signal**, in words, next to the
 *   number it qualifies, so a screenshot of the number carries the caveat with it.
 * - **pilot data says so**, because the seeded pre-demo rows are real rows and nothing about
 *   them looks different downstream.
 *
 * Nothing here decides anything. It renders what the backend sent, and it defaults to the
 * cautious reading when a field is missing.
 */

import { Tag } from "./ui";

/** The same threshold the backend uses (`EARLY_SIGNAL_N`): one vocabulary for small samples. */
export const EARLY_SIGNAL_N = 8;

export type EffectSummary = {
  baseline_n?: number;
  after_n?: number;
  baseline_mean?: number | null;
  after_mean?: number | null;
  mean_delta?: number | null;
  ci_low?: number | null;
  ci_high?: number | null;
  verdict?: string;
  explanation?: string;
  pilot?: boolean;
  label?: string | null;
  /** The sentence the server stored with the measurement (`verdict_words`), when there is one. */
  verdict_words?: string | null;
  /** `measured` | `gathering_evidence` | `not_measured` (see `learning/effect.effect_payload`). */
  status?: string | null;
};

export function EvidenceBadge({ n, threshold = EARLY_SIGNAL_N, pilot = false }: { n: number; threshold?: number; pilot?: boolean }) {
  const early = n < threshold;
  return (
    <span className="inline-flex flex-wrap items-center gap-1 align-middle">
      <span className="text-[11px] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        n={n}
      </span>
      {early ? <Tag tone="warn">early signal</Tag> : null}
      {pilot ? <Tag tone="think">pilot data</Tag> : null}
    </span>
  );
}

/** The effect of an approved change, with its sample size attached to the sentence. */
export function EffectLine({ effect }: { effect: EffectSummary }) {
  const before = Number(effect.baseline_n ?? 0);
  const after = Number(effect.after_n ?? 0);
  const n = Math.min(before, after);
  const early = effect.verdict === "insufficient_evidence" || n < EARLY_SIGNAL_N;
  const delta = effect.mean_delta;
  const pilot = Boolean(effect.pilot);

  const number =
    delta === null || delta === undefined
      ? "not enough answers yet to put a number on it"
      : `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(0)} points`;
  // The window is still filling up. That is a state the server stores with the measurement, and it
  // is worth more than "nothing yet": the learner gets the count so far and how many more it needs.
  const gathering = effect.status === "gathering_evidence" || effect.verdict === "insufficient_evidence";

  return (
    <p className="text-xs text-[var(--muted)]">
      {gathering ? (
        <>
          <span className="text-[var(--warn)]">being measured</span>: {number} from {before} answer{before === 1 ? "" : "s"} before
          and {after} after. Under {EARLY_SIGNAL_N} on either side this is a direction, not a result.{" "}
        </>
      ) : early ? (
        <>
          <span className="text-[var(--warn)]">early signal</span>: {number} from {before} answer{before === 1 ? "" : "s"} before
          and {after} after. Under {EARLY_SIGNAL_N} on either side this is a direction, not a result.{" "}
        </>
      ) : (
        <>
          {number} against the change ({before} before, {after} after).{" "}
        </>
      )}
      {pilot ? <span className="text-[var(--think)]">{effect.label ?? "pilot data, not evidence of efficacy"}. </span> : null}
      {effect.verdict_words ? <span className="opacity-80">{effect.verdict_words}</span> : null}
      {!effect.verdict_words && effect.explanation ? (
        <span className="opacity-80">{effect.explanation}</span>
      ) : null}
    </p>
  );
}

/** Per-method actual-vs-expected, as the method report returns it. */
export type MethodRow = {
  method: string;
  label?: string;
  n: number;
  actual?: number;
  expected?: number;
  residual?: number;
  ci?: [number, number];
  effective_n?: number;
  sources?: { source: string; weight: number; n: number }[];
  evidence?: string;
  note?: string;
  /** How many of `n` came from the pre-demo pilot, and the sentence that has to be shown with them. */
  n_pilot?: number;
  pilot?: boolean;
  pilot_label?: string | null;
};

export function MethodReport({ rows, minN = EARLY_SIGNAL_N }: { rows: MethodRow[]; minN?: number }) {
  if (!rows.length) {
    return (
      <p className="text-sm text-[var(--muted)]">
        No teaching has been measured yet. Each method is judged on actual minus expected on the same questions, so this fills in as
        you practise.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {rows.map((row) => {
        const early = row.evidence ? row.evidence === "early_signal" : row.n < minN;
        const residual = Number(row.residual ?? 0);
        return (
          <li key={row.method} className="text-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <strong>{row.label ?? row.method}</strong>
              <EvidenceBadge n={row.n} threshold={minN} pilot={Boolean(row.pilot)} />
            </div>
            <p className="text-xs text-[var(--muted)]">
              expected {Math.round(Number(row.expected ?? 0) * 100)}% · actual {Math.round(Number(row.actual ?? 0) * 100)}% ·{" "}
              <span className={residual >= 0 ? "text-[var(--good)]" : "text-[var(--warn)]"}>
                {residual > 0 ? "+" : ""}
                {(residual * 100).toFixed(0)} points against expectation
              </span>
            </p>
            {row.ci ? <p className="text-xs text-[var(--muted)]">
              range {(row.ci[0] * 100).toFixed(0)} to {(row.ci[1] * 100).toFixed(0)} points
              {row.effective_n !== undefined ? ` · weighted n=${row.effective_n}` : ""}
            </p> : null}
            {row.sources?.filter(s => s.n > 0).map(s => <p key={s.source} className="text-xs text-[var(--muted)]">
              {s.source} · weight {s.weight} · n={s.n}
            </p>)}
            {early ? <p className="text-xs text-[var(--warn)]">early signal: {row.note || `under ${minN} observations`}</p> : null}
            {row.pilot ? (
              <p className="text-xs text-[var(--think)]">
                {row.pilot_label ?? "pilot data — not evidence of efficacy"}: {row.n_pilot ?? row.n} of these {row.n} answers were
                written before the demo, so they show the loop working rather than proving anything about teaching.
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
