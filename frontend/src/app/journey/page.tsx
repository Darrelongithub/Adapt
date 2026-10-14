"use client";

/**
 * One path, and where you are on it.
 *
 * This screen is a *shell*: it does not ask the thirteen questions, play the games,
 * run the subject round or write a lesson. Each of those is a step with its own screen
 * and its own contract, and the shell's job is to say which one is next, why, and what
 * happens if you skip it — then hand over. Reimplementing any of them here would be the
 * duplication this phase exists to delete.
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

import { ProgressBar, SignedOutPath, StageCard, StageList } from "@/components/journey";
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
  const { me, loading, unknown, refresh } = useSession();
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
    if (!out.ok) setNote(out.error ?? "that did not save");
  }

  if (loading && !me) {
    return (
      <div className="mx-auto max-w-3xl px-3 py-6" style={{ padding: "clamp(24px,4vw,48px) clamp(16px,4vw,32px)" }}>
        <h1 className="text-[clamp(24px,3.4vw,34px)] font-medium leading-tight" style={{ fontFamily: "var(--font-serif)" }}>
          Your path
        </h1>
        <p className="mt-3 text-sm text-[var(--muted)]">still checking who you are — nothing here has been lost</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-3 py-4" style={{ padding: "clamp(24px,4vw,48px) clamp(16px,4vw,32px)" }}>
      <div className="text-[10.5px] uppercase tracking-[0.14em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        § Your path
      </div>
      <h1 className="mt-2 text-[clamp(24px,3.4vw,34px)] font-medium leading-tight" style={{ fontFamily: "var(--font-serif)" }}>
        One path, eight steps
      </h1>
      <p className="mt-3 text-sm leading-relaxed text-[var(--muted)]">
        Everything below happens on the screens that already do it. This page says where you are, why it is next, and what
        happens if you skip it. Your place is stored against your account, so closing the browser — or opening it somewhere
        else — brings you back to the same step.
      </p>

      {!signedIn ? (
        <div className="mt-6 space-y-4">
          <Card title="Start at the top" tone="muted">
            <SignedOutPath />
            <div className="mt-3 flex flex-wrap gap-2">
              <Btn kind="primary" href="/">
                {STEPS.account.action}
              </Btn>
            </div>
            <p className="mt-2 text-xs text-[var(--muted)]">
              {unknown ? `the app could not check your session just now (${unknown})` : "Consent comes first on that screen, in plain words, before anything is asked of you."}
            </p>
          </Card>
        </div>
      ) : (
        <div className="mt-6 space-y-6">
          <Card title="How far you are" right={state ? <Tag tone="good">{state.stage === "practice" ? "the loop" : "next step"}</Tag> : null}>
            <ProgressBar state={state} />
            {journey.loading ? <p className="mt-2 text-sm text-[var(--muted)]">…</p> : null}
            {journey.error ? <Err error={journey.error} status={journey.status} /> : null}
            {note ? <Notice tone="warn">{note}</Notice> : null}
            <div className="mt-3 flex flex-wrap gap-2">
              <Btn onClick={journey.reload} kind="ghost">
                check again
              </Btn>
              <Btn onClick={refresh} kind="ghost">
                reload who I am
              </Btn>
            </div>
          </Card>

          {currentRow ? (
            <Card title={`Step ${STEPS[currentRow.id].n} of 8`} tone="ask">
              <StageCard row={currentRow} onSkip={skip} busy={journey.busy}>
                {currentRow.id === "first_lesson" ? (
                  <WhyThisLesson prefs={prefs.data?.preferences} snapshot={snapshot.data} error={snapshot.error} />
                ) : null}
                {currentRow.id === "practice" ? (
                  <p className="text-sm leading-relaxed">
                    The loop from here: answer, see why it was right or wrong, come back to what is due. A wrong answer is not
                    just marked — it gets the reason that the answer you gave was the tempting one, and then the idea again in a
                    different way.
                  </p>
                ) : null}
                {currentRow.id === "preferences" || currentRow.id === "games" ? (
                  <p className="text-xs text-[var(--muted)]">
                    These two steps are one screen: it asks the questions, then offers each game in turn. You can stop after any
                    game and finish early — what you did not play is recorded as not measured.
                  </p>
                ) : null}
                {currentRow.id === "snapshot" ? (
                  <p className="text-xs text-[var(--muted)]">
                    Ranges with the number of observations behind them. Where there is not enough evidence, it says so instead of
                    showing a number.
                  </p>
                ) : null}
              </StageCard>
              {currentRow.id !== "practice" && currentRow.id !== "account" ? (
                <div className="mt-3">
                  <Btn kind="ghost" onClick={() => markDone(currentRow.id)} disabled={journey.busy}>
                    I have already done this elsewhere
                  </Btn>
                </div>
              ) : null}
            </Card>
          ) : null}

          <Card title="Every step" right={<span>stored with your account</span>}>
            <StageList state={state} onSkip={skip} busy={journey.busy} />
          </Card>

          <Card title="The placement questions" tone="muted">
            <p className="text-sm leading-relaxed">
              Eight unaided questions, no hints and no worked examples. They place you on each topic with a confidence band —
              they are measurement rather than a step on this path, so they are reachable from here at any point rather than
              being something you have to pass through again.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Btn href="/diagnose" kind="ghost">
                answer the placement questions
              </Btn>
              <Btn href="/progress" kind="ghost">
                see the record so far
              </Btn>
            </div>
          </Card>

          {state?.not_measured?.length ? (
            <Card title="What you skipped" tone="warn">
              <p className="text-sm leading-relaxed">
                These steps were marked skipped, so whatever would have been measured there is recorded as <em>not measured</em>{" "}
                — not as zero, and not as a guess:
              </p>
              <div className="mt-2 flex flex-wrap gap-1">
                {state.not_measured.map((id) => (
                  <Tag key={id} tone="warn">
                    {STEPS[id as StageId]?.title ?? id}
                  </Tag>
                ))}
              </div>
            </Card>
          ) : null}
        </div>
      )}

      <p className="mt-6 text-[0.7rem] text-[var(--muted)]">
        The path is a route, not a rule: every step but the first and last can be skipped, and the practice loop is where the
        work actually happens.{" "}
        <Link href="/settings" className="link-inline">
          what is recorded about you
        </Link>
      </p>
    </div>
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
