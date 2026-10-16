"use client";

/**
 * The journey's own furniture: one progress bar, one stage list, one card per step.
 *
 * Kept as components rather than inlined into the page because three screens need
 * them: the path itself, the home screen's "where am I" line, and the "why this
 * lesson looks like this" note on the lesson screen. A progress bar drawn three times
 * is three bars that disagree the first time somebody edits one of them.
 *
 * Two things the markup is careful about:
 *
 * * **A skip is not a failure.** Skipped stages read "skipped — not measured", in the
 *   same words the snapshot uses for an absent measurement, because a screen that says
 *   "incomplete" about a choice the learner made on purpose would be nagging.
 * * **The bar carries words, not just a fill.** `role="progressbar"` with
 *   `aria-valuetext` means the count is readable without seeing the colour, which is
 *   also what makes it checkable in the jsdom probe.
 */

import Link from "next/link";

import { Btn } from "./ui";
import { type JourneyState, type StageId, type StageRow, isBehind, percent, STEPS } from "@/lib/journey";

const STATUS_WORD: Record<StageRow["status"], string> = {
  done: "done",
  skipped: "skipped — not measured",
  todo: "still to do",
};

export function ProgressBar({ state }: { state: JourneyState | null }) {
  const pct = percent(state);
  const progress = state?.progress ?? {};
  const done = progress.done ?? 0;
  const skipped = progress.skipped ?? 0;
  const total = progress.total ?? (state?.stages?.length ?? 8);
  const words = `${done} done, ${skipped} skipped, ${total} steps`;
  return (
    <div className="space-y-1">
      <div
        role="progressbar"
        aria-label="Your path"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={words}
        className="h-2 w-full overflow-hidden rounded-[2px] border border-[var(--line)] bg-[var(--panel)]"
      >
        <div
          data-testid="journey-fill"
          className="h-full bg-[var(--accent)] transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-[0.7rem] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        <span data-testid="journey-percent">{pct}%</span>
        <span>{words}</span>
      </div>
    </div>
  );
}

/** The whole path at a glance: eight numbered rows, marked done / skipped / next. */
export function StageList({ state, onSkip, busy }: { state: JourneyState | null; onSkip: (stage: StageId) => void; busy: boolean }) {
  const rows = state?.stages ?? [];
  const current = state?.stage ?? null;
  return (
    <ol className="space-y-1" data-testid="journey-stages">
      {rows.map((row) => {
        const step = STEPS[row.id];
        if (!step) return null;
        const here = row.id === current && row.status === "todo";
        return (
          <li
            key={row.id}
            data-stage={row.id}
            data-status={row.status}
            aria-current={here ? "step" : undefined}
            className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-[2px] border px-3 py-2 text-sm ${
              here ? "border-[var(--accent)] bg-[var(--panel)]" : "border-transparent"
            }`}
          >
            <span className="text-[0.7rem] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
              {String(step.n).padStart(2, "0")}
            </span>
            <Link href={step.href} className="link-inline">
              {step.title}
            </Link>
            <span className="text-[0.7rem] text-[var(--muted)]">{STATUS_WORD[row.status]}</span>
            {row.status === "todo" && row.id !== "account" && row.id !== "practice" ? (
              <button
                type="button"
                className="btn-ctl btn-ghost ml-auto text-[0.7rem]"
                disabled={busy}
                onClick={() => onSkip(row.id)}
                aria-label={`skip ${step.title}`}
              >
                skip
              </button>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * One stage, as a card: what it is for, the button that goes there, and what skipping
 * costs. Shown for the stage the learner is on, so the path is never a dead end.
 *
 * Round 3 (Track T): the card now also has a "I have done this" action that calls
 * `onComplete` rather than relying on a follow-up server call.  That is the auto-
 * advance button: the journey screen reads back from the server after every
 * `markDone` and renders the next stage's card in the same place.
 */
export function StageCard({
  row,
  onSkip,
  onComplete,
  busy,
  primaryLabel,
  href,
  children,
}: {
  row: StageRow;
  onSkip?: (stage: StageId) => void;
  onComplete?: (stage: StageId) => void;
  busy?: boolean;
  /** Overrides the default action label — used where another phase's screen is the real target. */
  primaryLabel?: string;
  href?: string;
  children?: React.ReactNode;
}) {
  const step = STEPS[row.id];
  if (!step) return null;
  const isLast = row.id === "practice" || row.id === "account";
  return (
    <div data-testid="journey-card" data-stage={row.id} className="space-y-2">
      <div>
        <h2 className="text-[clamp(18px,2.4vw,24px)] font-medium leading-tight" style={{ fontFamily: "var(--font-serif)" }}>
          {step.title}
        </h2>
        <p className="mt-1 text-sm leading-relaxed text-[var(--muted)]">{step.what}</p>
      </div>
      {children}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Btn kind="primary" href={href ?? step.href}>
          {primaryLabel ?? step.action}
        </Btn>
        {onComplete && row.status === "todo" && !isLast ? (
          <Btn
            kind="ghost"
            onClick={() => onComplete(row.id)}
            disabled={busy}
            data-testid="journey-mark-done"
          >
            I have already done this
          </Btn>
        ) : null}
        {onSkip && row.status === "todo" && !isLast ? (
          <Btn kind="ghost" onClick={() => onSkip(row.id)} disabled={busy}>
            skip this step
          </Btn>
        ) : null}
      </div>
      {row.status === "todo" && !isLast ? (
        <p className="text-[0.7rem] text-[var(--muted)]">
          Skipping: {step.skipNote}
        </p>
      ) : null}
    </div>
  );
}

/** Signed-out readers get the first step, and a link that starts it. Nothing else is claimed. */
export function SignedOutPath() {
  return (
    <ol className="space-y-1" data-testid="journey-stages">
      <li data-stage="account" data-status="todo" aria-current="step" className="flex items-baseline gap-3 rounded-[2px] border border-[var(--accent)] bg-[var(--panel)] px-3 py-2 text-sm">
        <span className="text-[0.7rem] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
          01
        </span>
        <Link href="/" className="link-inline">
          {STEPS.account.title}
        </Link>
        <span className="text-[0.7rem] text-[var(--muted)]">still to do</span>
      </li>
      <li className="px-3 py-2 text-sm text-[var(--muted)]">
        The rest of the path appears when the app knows who you are. It is not hidden behind the account: a guest account is
        an account, and it starts the same path.
      </li>
    </ol>
  );
}

export { isBehind };
