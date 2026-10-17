"use client";

/** Stage dispatcher: pick a renderer by kind, and say so when there isn't one.
 *
 * The rule the whole engine is built on — a scene the browser does not understand is still a
 * lesson — is enforced here and nowhere else, so that one place is worth reading:
 * `has_renderer === false` or an unknown kind both land in `UnknownStage`, which shows the
 * frame's own narration and state. Only the picture is missing; nothing is invented to fill it.
 */
import type { Frame } from "@/lib/engine/types";
import { STAGES, UnknownStage } from "./stages";

/** Which emphasis ids each built-in stage actually draws. Anything else is reported, not dropped. */
const KNOWN: Record<string, string[]> = {
  binary_search: ["range", "mid", "found", "dropped"],
  sorting: ["prefix", "held", "compare", "hole", "placed"],
  logic_gates: ["inputs", "output"],
  cpu_cycle: ["pc", "a", "mar", "mdr", "ir", "cells"],
};

export function SceneStage({
  frame,
  kind,
  hasRenderer = true,
}: {
  frame: Frame | undefined;
  kind: string;
  hasRenderer?: boolean;
}) {
  const Stage = STAGES[kind];
  if (!frame) {
    return <p className="t-small text-[var(--muted)]">no frame to draw — the trace was empty.</p>;
  }
  const wanted = Array.isArray(frame.highlights) ? frame.highlights.filter((x): x is string => typeof x === "string") : [];
  const unrendered = wanted.filter((h) => !(KNOWN[kind] ?? []).includes(h));
  return (
    <figure className="m-0 flex flex-col gap-3">
      <figcaption className="flex flex-wrap items-baseline gap-2 t-small">
        <span className="rounded-[2px] border border-[var(--line)] px-1.5 py-0.5 font-mono t-small text-[var(--muted)]">
          frame {frame.i + 1}
        </span>
        <span className="font-semibold t-small">{frame.title}</span>
        <span className="t-small text-[var(--muted)]">
          {kind}
          {hasRenderer && Stage !== undefined ? "" : " · no renderer"}
        </span>
      </figcaption>

      {hasRenderer && Stage ? <Stage frame={frame} /> : <UnknownStage frame={frame} kind={kind} />}

      <p className="max-w-[62ch] text-[15px] leading-relaxed">{frame.narration}</p>

      {frame.cause ? (
        <p className="border-l-2 border-l-[var(--think)] pl-3 t-small text-[var(--muted)]">
          <span className="font-mono t-small uppercase tracking-wide">because</span> {frame.cause}
        </p>
      ) : null}

      {unrendered.length ? (
        <p className="t-small text-[var(--warn)]">
          this frame also pointed at{" "}
          {unrendered.map((h, k) => (
            <span key={h}>
              {k ? ", " : ""}
              <code className="rounded-[2px] bg-[var(--panel)] px-1 font-mono">{h}</code>
            </span>
          ))}{" "}
          — this stage does not draw that id.
        </p>
      ) : null}
    </figure>
  );
}

export { UnknownStage };
