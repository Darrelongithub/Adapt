"use client";

/**
 * Round 3 (Track T): the journey is now a one-screen flow rather than a
 * list page that described the flow.  The page reads the server's stage
 * state, renders the current stage's card, and auto-advances when the
 * learner marks it done or skipped.  Returning learners can still reach
 * the menu, but a new learner lands here from /onboarding and walks
 * through the stages one at a time, not by clicking through a list.
 *
 * Three rules it holds to:
 *
 * * **The server says where you are.** `useJourney` reads `GET /api/v1/journey/state`,
 *   so the same answer appears on a phone, a laptop and in another tab, and a closed
 *   browser resumes instead of restarting.
 * * **Skipping is an answer, not a failure.** A skipped stage is stored and shown as
 *   "skipped — not measured"; nothing downstream gets a zero for it.
 * * **Nothing is claimed that was not recorded.** The "why this lesson looks like
 *   this" panel prints the learner's own stated answers and the measured levers, each
 *   with the words the server wrote for it, and says which of the two it is.
 */

import Link from "next/link";
import { useState } from "react";

import { ProgressBar, StageList } from "@/components/journey";
import { Btn, Card, Err, Notice, Tag, useSession } from "@/components/ui";
import { useApi } from "@/lib/api";
import { STEPS, useJourney, type StageId } from "@/lib/journey";

type Prefs = { preferences: Record<string, unknown> };
type Snapshot = {
  state?: string;
  levers?: Record<string, { value?: unknown; why?: string; source?: string }>;
};

/** The levers worth showing first: the ones that change what a lesson looks like. */
const WHY_LEVERS = ["step_size", "lead_format_prior", "tone", "language", "hint_policy", "timers_visible"] as const;

function valueInWords(value: unknown): string {
  if (value === true) return "on";
  if (value === false) return "off";
  if (value === null || value === undefined || value === "") return "not set";
  return String(value).replace(/_/g, " ");
}

export default function Journey() {
  const { me, loading } = useSession();
  const signedIn = Boolean(me);
  const journey = useJourney(signedIn);
  const prefs = useApi<Prefs>(signedIn ? "me/preferences" : null);
  const snapshot = useApi<Snapshot>(signedIn ? "baseline/snapshot" : null);
  const [note, setNote] = useState<string | null>(null);

  const state = journey.state;
  const current = state?.stage ?? null;
  const currentRow = state?.stages?.find((r) => r.id === current) ?? null;

  async function skip(stage: StageId) {
    setNote(null);
    const out = await journey.advance(stage, "skipped");
    if (!out.ok) setNote(out.error ?? "that did not save");
  }

  async function markDone(stage: StageId) {
    setNote(null);
    const out = await journey.advance(stage, "done");
    if (!out.ok) setNote(out.error ?? "That did not save.");
  }

  return (
    <section className="mx-auto max-w-3xl space-y-5 px-4 py-6" data-journey>
      <h1 className="text-3xl font-medium" style={{ fontFamily: "var(--font-serif)" }}>Your path</h1>
      <p className="text-sm text-[var(--muted)]">One step at a time. Stop whenever you like.</p>
      <ProgressBar state={state} />
      {journey.error ? <Err error={journey.error} status={journey.status} /> : null}
      {note ? <Notice tone="warn">{note}</Notice> : null}
      {currentRow ? <section data-testid="journey-current-stage" className="rounded border border-[var(--line)] bg-[var(--panel)] p-5">
        <h2 className="text-xl font-medium">{STEPS[currentRow.id].title}</h2>
        <div data-testid="journey-card" data-stage={currentRow.id} className="mt-4 flex flex-wrap gap-3">
          <Btn kind="primary" href={STEPS[currentRow.id].href}>{STEPS[currentRow.id].action}</Btn>
          {!["account", "practice"].includes(currentRow.id) ? <Btn kind="ghost" disabled={journey.busy} onClick={() => skip(currentRow.id)}>Skip this step</Btn> : null}
        </div>
      </section> : <p role="status">{loading || journey.loading ? "Opening your path…" : "Your path is ready."}</p>}
      <details className="r3-question-why-tap">
        <summary>see every step</summary>
        <div className="mt-4 space-y-4">
          <StageList state={state} onSkip={skip} busy={journey.busy} />
          {currentRow ? <p>{STEPS[currentRow.id].what} Skipping: {STEPS[currentRow.id].skipNote}.</p> : null}
          <WhyThisLesson prefs={prefs.data?.preferences} snapshot={snapshot.data} error={snapshot.error} />
          {currentRow && !["account", "practice"].includes(currentRow.id) ? <button type="button" className="btn-ctl btn-secondary" data-testid="journey-mark-done" disabled={journey.busy} onClick={() => markDone(currentRow.id)}>I have already done this</button> : null}
          <Btn onClick={journey.reload}>Check again</Btn>
          <Link href="/settings" className="link-inline">What is recorded</Link>
        </div>
      </details>
    </section>
  );
}

/**
 * "Why this lesson looks like this" — printed from the learner's own answers and the
 * levers the mind check measured, never from a claim about them.
 *
 * The two kinds are labelled separately (`you told us` vs `measured`) because they are
 * not the same kind of evidence, and this app's whole premise is that stating a
 * preference and being measured on it are different facts. When neither exists, the
 * panel says so and offers the step that produces them.
 */
function WhyThisLesson({
  prefs,
  snapshot,
  error,
}: {
  prefs?: Record<string, unknown>;
  snapshot?: Snapshot | null;
  error?: string | null;
}) {
  const levers = snapshot?.levers ?? {};
  const measured = WHY_LEVERS.map((k) => [k, levers[k]] as const).filter(
    ([, lever]) => lever && (lever.source === "measured" || (lever.source === "stated" && lever.why)),
  );
  const stated = prefs ?? {};
  const statedRows = [
    ["preferred_format", "how you like to meet a new idea"],
    ["session_minutes", "how long a sitting you asked for"],
    ["feedback_style", "how you want to hear that something was wrong"],
    ["language", "the language you asked for"],
    ["anything_else", "the line you wrote yourself"],
  ].filter(([k]) => stated[k] !== undefined && stated[k] !== null && stated[k] !== "");

  if (!measured.length && !statedRows.length) {
    return (
      <p className="text-xs text-[var(--muted)]">
        There is nothing recorded to plan a lesson from yet
        {error ? ` (${error})` : ""}. The screen will say it is working from a simple plan and why, rather than pretending it
        knows something about you.
      </p>
    );
  }

  return (
    <div className="rounded-[2px] border border-[var(--line)] p-3" data-testid="why-this-lesson">
      <div className="text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        why this lesson looks like this
      </div>
      <ul className="mt-2 space-y-1 text-sm">
        {statedRows.map(([k, label]) => (
          <li key={String(k)} className="flex flex-wrap items-baseline gap-2">
            <Tag tone="line">you told us</Tag>
            <span>
              {String(label)}: <strong>{valueInWords(stated[k as string])}</strong>
            </span>
          </li>
        ))}
        {measured.map(([k, lever]) => (
          <li key={k} className="flex flex-wrap items-baseline gap-2">
            <Tag tone={lever?.source === "measured" ? "good" : "line"}>{lever?.source === "measured" ? "measured" : "you told us"}</Tag>
            <span>
              {valueInWords(lever?.value)} — <span className="text-[var(--muted)]">{lever?.why}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[0.7rem] text-[var(--muted)]">
        This is everything the lesson is planned from. Nothing here is a label about how you learn, and no preference decides
        anything on its own — it sets a starting point that gets compared with what you actually do.
      </p>
    </div>
  );
}
