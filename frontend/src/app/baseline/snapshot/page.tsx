"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Btn, Card, Err, Tag } from "@/components/ui";
import {
  factorRange,
  getBaselineSnapshot,
  prettyKey,
  startBaseline,
  type BaselineFactor,
  type BaselineSnapshot,
} from "@/lib/baseline";

export default function BaselineSnapshotPage() {
  const router = useRouter();
  const [data, setData] = useState<BaselineSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    const runId = new URLSearchParams(window.location.search).get("run_id") ?? undefined;
    getBaselineSnapshot(runId)
      .then((snapshot) => {
        if (live) setData(snapshot);
      })
      .catch((err) => {
        if (live) setError(errorText(err));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  async function continueRun() {
    setBusy(true);
    try {
      const run = await startBaseline();
      router.push(`/baseline/${encodeURIComponent(run.run_id)}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <header className="space-y-2 border-b border-[var(--line)] pb-5">
        <div className="flex flex-wrap items-center gap-2">
          <Tag tone="accent">Your mind check</Tag>
          {data?.state === "sealed" ? <Tag tone="good">saved snapshot</Tag> : null}
          {data?.state === "in_progress" ? <Tag tone="warn">partial · still in progress</Tag> : null}
        </div>
        <h1 className="text-3xl font-medium" style={{ fontFamily: "var(--font-serif)" }}>What we have measured so far</h1>
        <p className="max-w-3xl text-sm leading-relaxed text-[var(--muted)]">
          These are estimates from your answers in this check-in, not grades or labels. Every measured result has a sample count
          and a range. “Not measured yet” means there is not enough evidence to show a number.
        </p>
      </header>

      {loading ? <Card title="Loading your saved snapshot" tone="muted"><p className="text-sm text-[var(--muted)]">…</p></Card> : null}
      {!loading && error ? <Err error={error} status={null} /> : null}
      {!loading && data?.state === "not_started" ? (
        <Card title="Nothing measured yet" tone="muted">
          <p className="text-sm text-[var(--muted)]">Start when you are ready. You can stop at any time and see a partial snapshot.</p>
          <div className="mt-3"><Btn kind="primary" onClick={continueRun} disabled={busy}>Start the mind check</Btn></div>
        </Card>
      ) : null}

      {!loading && data && data.state !== "not_started" ? (
        <>
          <Card title="How to read this">
            <p className="text-sm leading-relaxed">{data.note ?? "Every number comes from your own answers, with the count shown. Nothing is compared with other learners."}</p>
            <p className="mt-2 text-xs text-[var(--muted)]">
              {typeof data.measured === "number" && typeof data.total === "number"
                ? `${data.measured} of ${data.total} factors currently have enough evidence for a measured estimate.`
                : data.state === "in_progress"
                  ? "This check-in is still open; the saved measurements can change if you continue."
                  : "This is the snapshot saved when you stopped."}
            </p>
          </Card>

          <section aria-label="Baseline factors" className="grid gap-3 md:grid-cols-2">
            {data.factors.map((factor) => <FactorCard key={factor.key} factor={factor} />)}
          </section>

          {data.preference_vs_performance ? (
            <Card title="What you chose, and what the evidence says" tone="ask">
              <p className="text-sm leading-relaxed">{data.preference_vs_performance.sentence}</p>
              <p className="mt-2 text-xs text-[var(--muted)]">{data.preference_vs_performance.note}</p>
              <ul className="mt-3 flex flex-wrap gap-2">
                {data.preference_vs_performance.measured.map((row) => (
                  <li key={row.format}><Tag>{prettyKey(row.format)} · {Math.round(row.accuracy * 100)}% from {row.n} questions</Tag></li>
                ))}
              </ul>
            </Card>
          ) : null}

          {data.levers && Object.keys(data.levers).length ? (
            <Card title="What we will do differently">
              <ul className="grid gap-3 sm:grid-cols-2">
                {Object.entries(data.levers).map(([key, lever]) => (
                  <li key={key} className="rounded-[2px] border border-[var(--line)] bg-[var(--panel-2)] p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h2 className="text-sm font-medium">{prettyKey(key)}</h2>
                      <Tag tone={lever.source === "measured" ? "good" : lever.source === "stated" ? "think" : "line"}>{lever.source}</Tag>
                    </div>
                    <p className="mt-1 text-sm">{valueText(lever.value)}</p>
                    <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">{lever.why}</p>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          {data.preferences && Object.keys(data.preferences).length ? (
            <Card title="What you told us" tone="muted">
              <p className="mb-3 text-xs text-[var(--muted)]">These are stated preferences, kept separate from measured results.</p>
              <dl className="grid gap-2 sm:grid-cols-2">
                {Object.entries(data.preferences).map(([key, value]) => (
                  <div key={key} className="border-b border-[var(--line)] pb-2">
                    <dt className="text-xs text-[var(--muted)]">{prettyKey(key)}</dt>
                    <dd className="mt-0.5 text-sm">{valueText(value)}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          ) : null}

          {data.completion ? (
            <Card title="What was and was not measured">
              <ul className="grid gap-2 sm:grid-cols-2">
                {data.completion.stages.map((stage) => (
                  <li key={stage.stage} className="flex items-center justify-between gap-3 border-b border-[var(--line)] py-1.5">
                    <span className="text-sm">{prettyKey(stage.stage)}</span>
                    <span className="text-right text-xs text-[var(--muted)]">
                      {stage.state === "complete" ? "done" : stage.trials ? "partly done" : "not measured"}
                      {stage.trials ? ` · ${stage.trials} task${stage.trials === 1 ? "" : "s"}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          <div className="flex flex-wrap gap-2 border-t border-[var(--line)] pt-4">
            {data.state === "in_progress" ? (
              <>
                <Btn kind="primary" onClick={continueRun} disabled={busy}>{busy ? "Opening…" : "Continue the check-in"}</Btn>
                <p className="self-center text-xs text-[var(--muted)]">Continue is available from the Mind check page; this view does not seal your run.</p>
              </>
            ) : (
              <Btn kind="primary" onClick={continueRun} disabled={busy}>{busy ? "Opening…" : "Start another check-in"}</Btn>
            )}
            <Btn href="/baseline">Mind check home</Btn>
            <Btn href="/learn">Go to practice</Btn>
          </div>
        </>
      ) : null}
      <p className="text-xs text-[var(--muted)]">A short run can leave several factors unmeasured. That is more honest than filling the gaps with zeros.</p>
      <Link className="link-inline text-sm" href="/">Back to home</Link>
    </div>
  );
}

function FactorCard({ factor }: { factor: BaselineFactor }) {
  const measured = factor.state === "measured" && factor.range !== null;
  const needed = typeof factor.detail.needed === "number" ? factor.detail.needed : null;
  return (
    <article className="rounded-[2px] border border-[var(--line)] bg-[var(--panel)] p-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-medium">{factor.title}</h2>
          <p className="mt-1 text-xs text-[var(--muted)]">{factor.summary}</p>
        </div>
        <Tag tone={measured ? "good" : "warn"}>{measured ? "measured" : "not measured yet"}</Tag>
      </header>
      {measured ? (
        <div className="mt-3 rounded-[2px] bg-[var(--panel-2)] px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>estimated range</p>
          <p className="mt-1 text-lg font-medium" aria-label={`${factorRange(factor.range)}; based on ${factor.n} answers`}>
            {formatFactorRange(factor.key, factor.range!)}
          </p>
          <p className="text-xs text-[var(--muted)]">based on {factor.n} {factor.n === 1 ? "answer" : "answers"}</p>
        </div>
      ) : (
        <div className="mt-3 rounded-[2px] border border-dashed border-[var(--line)] px-3 py-2">
          <p className="text-sm">Not measured yet</p>
          <p className="mt-1 text-xs text-[var(--muted)]">
            {needed !== null ? `${factor.n} of ${needed} needed so far. ` : factor.n ? `${factor.n} task${factor.n === 1 ? "" : "s"} so far. ` : "No usable answers yet. "}
            {factor.statement}
          </p>
        </div>
      )}
      {measured ? <p className="mt-3 text-sm leading-relaxed">{factor.statement}</p> : null}
      {factor.actions.length ? (
        <ul className="mt-3 space-y-1 border-t border-[var(--line)] pt-2 text-xs text-[var(--muted)]">
          {factor.actions.map((action) => <li key={action}>• {action}</li>)}
        </ul>
      ) : null}
    </article>
  );
}

function formatFactorRange(key: string, range: [number, number]): string {
  const [low, high] = range;
  const number = (v: number) => Number.isInteger(v) ? String(v) : v.toFixed(1).replace(/\.0$/, "");
  if (key === "working_memory") return `${number(low)}–${number(high)} steps`;
  if (key === "processing_pace") return `${number(low)}–${number(high)} seconds per quick tap`;
  if (key === "persistence_help_seeking") return `${number(low)}–${number(high)} hint(s)`;
  return `${Math.round(low * 100)}–${Math.round(high * 100)}%`;
}

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "not recorded";
  if (Array.isArray(value)) return value.map(valueText).join(", ");
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return "saved";
  return String(value).replace(/_/g, " ");
}

function errorText(error: unknown): string {
  const err = error as { shown?: string; message?: string };
  return err?.shown ?? err?.message ?? String(error);
}
