"use client";

/** Safe renderer for the frozen Part C LessonPlan and Explanation blocks.
 *
 * Text is rendered as React text (never HTML); visual JSON goes through the existing
 * client-side visual validator and SVG templates. A simple plan stays visible while
 * an optional, fully validated background result is polled and swapped in-place.
 */
import { useEffect, useMemo, useState } from "react";

import { VisualFigure } from "@/components/visual";
import { api } from "@/lib/api";

type Choice = { id: string; text: string };
type Body = string | {
  question?: string;
  options?: Choice[];
  item_id?: string;
  steps?: { text: string; why?: string }[];
  final_answer?: string;
  kind?: string;
  [key: string]: unknown;
};
type Block = {
  type: "text" | "visual" | "worked_example" | "check" | "predict" | "explain" | "reflect";
  body: Body;
  fact_ids: string[];
  why: { evidence_ids: string[]; n: number };
  length: number;
};
export type LessonValue = {
  blocks: Block[];
  source: "written_for_you" | "simple";
  concept_id?: string;
  method?: string;
  analogy_id?: string | null;
};
type UpgradeReply = {
  state: "running" | "ready" | "fallback";
  plan?: LessonValue;
  explanation?: LessonValue;
};

type Props = {
  value: LessonValue;
  upgradeJobId?: string | null;
  contrastLabel?: boolean;
  className?: string;
  collapseBlockWhy?: boolean;
};

function textValues(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(textValues);
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !["id", "item_id", "kind"].includes(key))
      .flatMap(([, child]) => textValues(child));
  }
  return [];
}

function BlockBody({ block, index }: { block: Block; index: number }) {
  const body = block.body;
  const object = typeof body === "string" ? { text: body } : body;
  if (block.type === "visual") {
    return <VisualFigure spec={body} />;
  }
  if (block.type === "check" || block.type === "predict") {
    const question = typeof body === "string" ? body : String(object.question ?? "");
    const options = Array.isArray(object.options) ? object.options : [];
    return (
      <fieldset className="mt-2 rounded-[2px] border border-[var(--line)] bg-[var(--panel)] p-3">
        <legend className="px-1 text-sm font-medium">{question}</legend>
        {options.length ? (
          <div className="mt-2 grid gap-2">
            {options.map((option, choiceIndex) => (
              <label key={option.id || choiceIndex} className="flex items-start gap-2 text-sm">
                <input type="radio" name={`lesson-${index}`} value={option.id} className="mt-1 accent-[var(--accent)]" />
                <span>{option.text}</span>
              </label>
            ))}
          </div>
        ) : (
          <input
            aria-label="Your answer"
            className="mt-2 w-full rounded-[2px] border border-[var(--line)] bg-[var(--bg)] px-3 py-2 text-sm"
            type="text"
            autoComplete="off"
          />
        )}
      </fieldset>
    );
  }
  if (block.type === "worked_example") {
    const steps = Array.isArray(object.steps) ? object.steps : [];
    return (
      <ol className="grid gap-2">
        {steps.map((step, stepIndex) => (
          <li key={stepIndex} className="rounded-[2px] bg-[var(--panel)] px-3 py-2 text-sm">
            <p>{step.text}</p>
            {step.why ? <p className="mt-1 text-xs text-[var(--muted)]">{step.why}</p> : null}
          </li>
        ))}
        {object.final_answer ? (
          <li className="rounded-[2px] border border-[var(--line)] px-3 py-2 text-sm">
            <span className="font-medium">Checked result: </span>{object.final_answer}
          </li>
        ) : null}
      </ol>
    );
  }
  const content = typeof body === "string" ? body : String(object.text ?? "");
  return <p className="whitespace-pre-wrap text-sm leading-6">{content}</p>;
}

function BlockCard({ block, index, showWhy }: { block: Block; index: number; showWhy: boolean }) {
  const bodyText = useMemo(() => textValues(block.body).join(" "), [block.body]);
  return (
    <article className="rounded-[2px] border border-[var(--line)] bg-[var(--bg)] p-4">
      <BlockBody block={block} index={index} />
      {showWhy ? (
        <details className="mt-3 text-[11px] text-[var(--muted)]" data-testid="block-why">
          <summary className="cursor-pointer">Why this block</summary>
          <p className="mt-1">{block.why.n ? `Evidence n=${block.why.n}` : "No recent evidence was used."}</p>
          {block.why.evidence_ids.length ? <p>Evidence ids: {block.why.evidence_ids.join(", ")}</p> : null}
          {block.fact_ids.length ? <p>Pack facts: {block.fact_ids.join(", ")}</p> : null}
          <span className="sr-only">{bodyText}</span>
        </details>
      ) : null}
    </article>
  );
}

export function LessonRenderer({ value, upgradeJobId, contrastLabel = false, className = "", collapseBlockWhy = false }: Props) {
  const [lesson, setLesson] = useState(value);

  useEffect(() => setLesson(value), [value]);

  useEffect(() => {
    if (!upgradeJobId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let tries = 0;
    const poll = async () => {
      if (!alive || tries >= 45) return;
      tries += 1;
      try {
        const result = await api<UpgradeReply>(`lesson/upgrade/${encodeURIComponent(upgradeJobId)}`);
        if (!alive) return;
        if (result.state === "ready") {
          const upgraded = result.plan ?? result.explanation;
          if (upgraded?.source === "written_for_you") setLesson(upgraded);
          return;
        }
        if (result.state === "fallback") return;
      } catch {
        if (!alive) return;
      }
      timer = setTimeout(poll, 900);
    };
    timer = setTimeout(poll, 450);
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [upgradeJobId]);

  const label = lesson.source === "written_for_you"
    ? (contrastLabel ? "Written for this profile, checked" : "Written for you")
    : (contrastLabel ? "Template lesson" : "Simple lesson");

  return (
    <section className={`grid gap-3 ${className}`} aria-label="Lesson">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-[2px] border border-[var(--line)] px-2 py-1 text-[11px] text-[var(--muted)]">{label}</span>
        {lesson.method ? <span className="text-[11px] text-[var(--muted)]">Method: {lesson.method.replaceAll("_", " ")}</span> : null}
      </div>
      {lesson.blocks.map((block, index) => (
        <BlockCard
          key={`${index}-${block.type}`}
          block={block}
          index={index}
          showWhy={!collapseBlockWhy && index === 0}
        />
      ))}
    </section>
  );
}

export default LessonRenderer;
