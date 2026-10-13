"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Btn, Card, Err, Notice, Tag } from "@/components/ui";
import { finishBaseline, getBaselineSnapshot, startBaseline, type BaselineSnapshot, type BaselineStageReport } from "@/lib/baseline";

export default function BaselineHome() {
  const router = useRouter();
  const [snapshot, setSnapshot] = useState<BaselineSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getBaselineSnapshot()
      .then((data) => {
        if (live) setSnapshot(data);
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
    setError(null);
    try {
      const run = await startBaseline();
      router.push(`/baseline/${encodeURIComponent(run.run_id)}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  async function stopAndSnapshot() {
    if (!snapshot?.run_id) return;
    setBusy(true);
    setError(null);
    try {
      await finishBaseline(snapshot.run_id);
      router.push(`/baseline/snapshot?run_id=${encodeURIComponent(snapshot.run_id)}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  const inProgress = snapshot?.state === "in_progress" && Boolean(snapshot.run_id);
  const sealed = snapshot?.state === "sealed" && Boolean(snapshot.run_id);

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header className="space-y-3 border-b border-[var(--line)] pb-5">
        <div className="flex flex-wrap items-center gap-2">
          <Tag tone="accent">Mind check</Tag>
          <Tag>not a grade</Tag>
        </div>
        <h1 className="text-3xl font-medium" style={{ fontFamily: "var(--font-serif)" }}>A short check-in, on your terms</h1>
        <p className="max-w-2xl text-sm leading-relaxed text-[var(--muted)]">
          A few small games help the app choose useful step sizes, pacing and review prompts. They do not label you or compare
          you with anyone else. Every estimate shows how many answers it uses; anything without enough evidence stays “not
          measured yet.”
        </p>
        <p className="text-sm text-[var(--muted)]">
          You can stop on any screen and keep the part already measured. Your progress is saved as you go, so you can come back
          on another device.
        </p>
      </header>

      {loading ? <Card title="Checking your saved progress" tone="muted"><p className="text-sm text-[var(--muted)]">…</p></Card> : null}
      {!loading && error ? <Err error={error} status={null} /> : null}

      {!loading && inProgress ? (
        <Card title="Your check-in is saved" tone="ask">
          <div className="flex flex-wrap items-center gap-2">
            <Tag tone="good">in progress</Tag>
            <span className="text-xs text-[var(--muted)]">{snapshot?.measured ?? 0} of {snapshot?.total ?? 8} factors have a measured result so far</span>
          </div>
          <p className="mt-3 text-sm leading-relaxed">
            You are at <strong>{nextStage(snapshot?.completion?.stages)}</strong>. Continue from there, or stop now and see what
            was measured and what still needs more answers.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Btn kind="primary" onClick={continueRun} disabled={busy}>{busy ? "Opening…" : "Continue where I left off"}</Btn>
            <Btn kind="ghost" onClick={stopAndSnapshot} disabled={busy}>Stop here and see my snapshot</Btn>
          </div>
        </Card>
      ) : null}

      {!loading && sealed ? (
        <Card title="Your latest snapshot is ready" tone="good">
          <p className="text-sm leading-relaxed">
            It keeps the measurements from that sitting, including the parts you skipped. You can review it at any time.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Btn kind="primary" href={`/baseline/snapshot?run_id=${encodeURIComponent(snapshot!.run_id!)}`}>Review my snapshot</Btn>
            <Btn kind="ghost" onClick={continueRun} disabled={busy}>{busy ? "Opening…" : "Start another check-in"}</Btn>
          </div>
        </Card>
      ) : null}

      {!loading && !inProgress && !sealed ? (
        <Card title="Ready when you are" tone="muted">
          <p className="text-sm leading-relaxed text-[var(--muted)]">
            First you will choose a format for a new topic and answer a few optional context questions. Then the games begin.
            You can leave any answer blank, and there is a stop button on every screen.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Btn kind="primary" onClick={continueRun} disabled={busy}>{busy ? "Opening…" : "Start the mind check"}</Btn>
            <Btn href="/progress">See existing progress</Btn>
          </div>
        </Card>
      ) : null}

      {error && !loading ? <Notice tone="bad">{error}</Notice> : null}
      <p className="text-xs text-[var(--muted)]">
        The check uses code-scored answers. No live model is involved in marking these games. Questions about what you prefer
        stay separate from measurements of what happened.
      </p>
      <div className="text-sm"><Link className="link-inline" href="/">Back to home</Link></div>
    </div>
  );
}

function nextStage(stages: BaselineStageReport["stages"] | undefined): string {
  const next = stages?.find((row) => row.state === "partial" || row.state === "not_started");
  return next ? next.stage.replace(/_/g, " ") : "the next step";
}

function errorText(error: unknown): string {
  const err = error as { shown?: string; message?: string };
  return err?.shown ?? err?.message ?? String(error);
}
