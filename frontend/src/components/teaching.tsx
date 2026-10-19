"use client";

/**
 * The teaching that comes *before* the question.
 *
 * The backend has decided how this concept should be taught to this learner — a method was
 * assigned at serve time from a seeded rotation, an approved change may have pinned it, and
 * the teaching payload is what that decision produces. Until this component existed, none of
 * it was on screen: `presentation.teaching` arrived on every plan entry and the practice card
 * rendered only the question. So "approving the visual strategy" changed a field in a payload
 * nobody read, and "segmented text" was invisible.
 *
 * Three rules, and they are the rules the payload advertises:
 *
 * - **one step at a time, with its check in between.** A step is short by construction (the
 *   backend groups whole sentences to a ~30-word budget and never cuts one), and the next step
 *   is behind the check — reading passively and clicking "next" is the behaviour segmenting
 *   exists to prevent;
 * - **the check is a check, not a mark.** It is compared against the words the step is about,
 *   it is not posted anywhere, and it cannot move the mastery estimate: only answers to items
 *   do that, and the copy says so. A learner who is told a recall prompt is being graded will
 *   start writing for the marker instead of for themselves;
 * - **a diagram never guesses.** A template with an unfilled slot says which slot is empty
 *   rather than filling it with something plausible.
 */

import { useMemo, useState } from "react";

import { Btn, Card, Notice, Tag } from "./ui";

type TeachingStep = { index: number; text: string; words?: number; check?: { question?: string; expect_any?: string[]; hint?: string } | null };

type TeachingVisual = {
  kind?: string;
  template?: boolean;
  title?: string;
  alt?: string;
  why?: string;
  nodes?: { index?: number; when?: string; text?: string }[];
  columns?: string[];
  rows?: { aspect?: string; left?: string; right?: string }[];
  labels?: { label?: string; text?: string }[];
  unfilled?: string[];
};

export type Teaching = {
  method?: string;
  kind?: string;
  text?: string;
  steps?: TeachingStep[];
  dropped?: string[];
  note?: string;
  visual?: TeachingVisual;
  why?: string;
  source?: string;
} | null;

const norm = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9\s']/g, " ")
    .split(/\s+/)
    .filter(Boolean);

/** Whether a learner's own words touch the idea the step is about. Deliberately generous:
 *  this is a nudge to keep reading actively, not a grader, and it says so on screen. */
function touches(answer: string, expectAny: string[] | undefined): boolean {
  const said = norm(answer);
  if (!said.length || said.length < 3) return false;
  return (expectAny ?? []).some((want) => {
    const w = String(want).toLowerCase();
    return w.length > 2 && (answer.toLowerCase().includes(w) || said.some((tok) => tok.startsWith(w.slice(0, Math.max(4, w.length - 2)))));
  });
}

export function TeachingPanel({ teaching, headline }: { teaching: Teaching; headline?: string | null }) {
  if (!teaching || !teaching.kind) return null;
  const kind = String(teaching.kind);
  if (kind === "no_material") {
    return (
      <Card title="Before the question" tone="muted" right={<Tag tone="line">no material yet</Tag>}>
        <p className="text-sm text-[var(--muted)]">{teaching.text}</p>
      </Card>
    );
  }
  if (kind === "attempt_first") {
    return (
      <Card title="Before the question" tone="ask" right={<Tag tone="think">attempt first</Tag>}>
        <p className="text-sm">{teaching.text}</p>
        <p className="mt-2 text-xs text-[var(--muted)]">
          Answering before being told is the point: a wrong first attempt you can compare against beats a paragraph you can only
          agree with.
        </p>
      </Card>
    );
  }
  if (kind === "segments") return <SegmentWalkthrough teaching={teaching} headline={headline} />;
  if (kind === "visual") return <VisualTemplate visual={teaching.visual ?? {}} headline={headline} />;
  if (kind === "worked_example") {
    const steps = (teaching as { steps?: { index?: number; text: string; why?: string }[] }).steps ?? [];
    return (
      <Card title="Worked example" tone="muted" right={<Tag tone="line">before the question</Tag>}>
        <ol className="list-decimal space-y-2 pl-5 text-sm">
          {steps.map((st, i) => (
            <li key={st.index ?? i}>
              {st.text}
              {st.why ? <span className="block text-xs text-[var(--muted)]">{st.why}</span> : null}
            </li>
          ))}
        </ol>
        <p className="mt-2 text-xs text-[var(--muted)]">
          Read it, then close it: the question next is of the same kind, and the example is not on screen while you answer.
        </p>
      </Card>
    );
  }
  return null;
}

/** Steps, one at a time, each behind its own check. */
function SegmentWalkthrough({ teaching, headline }: { teaching: NonNullable<Teaching>; headline?: string | null }) {
  const steps = teaching.steps ?? [];
  const [at, setAt] = useState(0);
  const [said, setSaid] = useState("");
  const [ack, setAck] = useState<null | { enough: boolean; hint?: string }>(null);

  const step = steps[at];
  const last = at >= steps.length - 1;
  const check = step?.check;
  const enough = useMemo(() => touches(said, check?.expect_any), [said, check]);

  if (!step) return null;
  return (
    <Card
      title={headline ? `Before the question — ${headline}` : "Before the question"}
      tone="muted"
      right={
        <span className="t-small text-[var(--muted)]">
          step {step.index} of {steps.length}
        </span>
      }
    >
      <p className="text-[15px] leading-relaxed">{step.text}</p>
      <div className="mt-3 flex gap-1" aria-hidden="true">
        {steps.map((s, i) => (
          <span key={s.index} className={`h-1 flex-1 rounded-[2px] ${i <= at ? "bg-[var(--accent)]" : "bg-[var(--line)]"}`} />
        ))}
      </div>

      {check?.question ? (
        <div className="mt-4 border-t border-[var(--line)] pt-3">
          <label className="block t-small font-medium">
            {check.question}
            <input
              className="mt-1 w-full rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
              value={said}
              placeholder="One sentence is enough."
              onChange={(e) => {
                setSaid(e.target.value);
                setAck(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && said.trim().length >= 3) setAck({ enough, hint: check.hint });
              }}
            />
          </label>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Btn onClick={() => setAck({ enough, hint: check.hint })} disabled={said.trim().length < 3}>
              Check my sentence
            </Btn>
            {last ? null : (
              <Btn
                kind="primary"
                onClick={() => {
                  setAt(at + 1);
                  setSaid("");
                  setAck(null);
                }}
                disabled={!ack?.enough}
                title={ack?.enough ? undefined : "answer the check first — the next step builds on this one"}
              >
                Next step
              </Btn>
            )}
          </div>
          {ack ? (
            <div className="mt-2 text-xs">
              {ack.enough ? (
                <Notice tone="good">
                  That has the idea in it{last ? " — you can go on to the question" : " — the next step is open"}.
                </Notice>
              ) : (
                <Notice tone="warn">
                  Not quite. {ack.hint ?? "Say what this step says about the idea, in your own words."}
                </Notice>
              )}
              <span className="mt-1 block text-[var(--muted)]">
                This check is not marked and is not sent anywhere: nothing here moves your estimate of what you know.
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      {!teaching.dropped?.length ? (
        <p className="mt-3 text-xs text-[var(--muted)]">Nothing was cut from the material to fit this screen.</p>
      ) : null}
    </Card>
  );
}

/** The four structural templates, filled from the concept's own material. */
function VisualTemplate({ visual, headline }: { visual: TeachingVisual; headline?: string | null }) {
  const kind = String(visual.kind ?? "flow");
  const columns =
    visual.columns?.length === 3
      ? visual.columns
      : ["aspect", ...(visual.columns ?? ["one side", "the other side"]).slice(-2)];
  return (
    <Card
      title={visual.title || headline || "Before the question"}
      tone="ask"
      right={<Tag tone="think">{TEMPLATE_NAMES[kind] ?? kind}</Tag>}
    >
      {kind === "flow" ? (
        <ol className="space-y-2">
          {(visual.nodes ?? []).map((n, i) => (
            <li key={i} className="flex gap-3 text-sm">
              <span
                className="mt-[2px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-[var(--line)] t-small"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                {i + 1}
              </span>
              <span>{n.text}</span>
            </li>
          ))}
        </ol>
      ) : null}

      {kind === "comparison_table" ? (
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} scope="col" className="border-b border-[var(--line)] p-2 text-left font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(visual.rows ?? []).map((r, i) => (
              <tr key={i}>
                <td className="border-b border-[var(--line)] p-2 text-[var(--muted)]">{r.aspect}</td>
                <td className="border-b border-[var(--line)] p-2">{r.left}</td>
                <td className="border-b border-[var(--line)] p-2">{r.right}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {kind === "timeline" ? (
        <ol className="space-y-3 border-l border-[var(--line)] pl-4">
          {(visual.nodes ?? []).map((n, i) => (
            <li key={i} className="text-sm">
              <span className="block t-small text-[var(--muted)]">
                {n.when || `point ${i + 1}`}
              </span>
              {n.text}
            </li>
          ))}
        </ol>
      ) : null}

      {kind === "labelled_diagram" ? (
        <dl className="grid gap-2 sm:grid-cols-2">
          {(visual.labels ?? []).map((l, i) => (
            <div key={i} className="rounded-[2px] border border-[var(--line)] bg-[var(--panel)] p-2">
              <dt className="t-small text-[var(--muted)]">
                {l.label || "unlabelled"}
              </dt>
              <dd className="text-sm">{l.text}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {visual.unfilled?.length ? (
        <div className="mt-3 text-xs">
          <Notice tone="warn">
            {visual.unfilled.length} slot{visual.unfilled.length === 1 ? "" : "s"} could not be filled from this concept&rsquo;s
            material ({visual.unfilled.join(", ")}). They are left empty on purpose: a guessed label looks exactly like a checked one.
          </Notice>
        </div>
      ) : null}

      {visual.why ? (
        <p className="mt-3 text-xs text-[var(--muted)]">
          <span aria-hidden="true">{visual.alt ? `${visual.alt}. ` : ""}</span>
          why this shape: {visual.why}
        </p>
      ) : null}
    </Card>
  );
}

const TEMPLATE_NAMES: Record<string, string> = {
  flow: "flow",
  comparison_table: "comparison table",
  timeline: "timeline",
  labelled_diagram: "labelled diagram",
};
