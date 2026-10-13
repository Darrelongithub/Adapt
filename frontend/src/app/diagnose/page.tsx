"use client";

/** Placement: a short adaptive sequence of unaided questions (four to twelve, eight by
 *  default), then the analysis that seeds your first band estimates. `aidless` is not
 *  cosmetic — a hint during placement would measure the wrong thing and every later
 *  decision would inherit that error.
 */
import { useEffect, useRef, useState } from "react";

import { Question, ReceiptView } from "@/components/question";
import { Btn, Card, Err, Notice, Tag } from "@/components/ui";
import { summarise, type AnalyzeRaw, type AnalyzeSummary } from "@/lib/analyze";
import { useFlow } from "@/lib/flow";
import type { DiagnosticResponse, Receipt } from "@/lib/types";
import { ApiError, get, post } from "@/lib/api";

export default function Diagnose() {
  const flow = useFlow("diagnostic");
  const [items, setItems] = useState<DiagnosticResponse["items"]>([]);
  const [i, setI] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Marks are kept per item, not "the last one". `last` alone meant that pressing Back threw
  // the mark away, which disabled Next: the learner could not get back to the question they
  // had just answered without answering the old one again — and the old answer was gone from
  // the box (`@/lib/drafts` now puts it back). A receipt is per item; keep it that way.
  const [receipts, setReceipts] = useState<Record<number, Receipt>>({});
  const [report, setReport] = useState<AnalyzeSummary | null>(null);
  const [framing, setFraming] = useState<{ purpose?: string; how?: string; time?: string }>({});
  const [started, setStarted] = useState<number | null>(null);
  const [shortfall, setShortfall] = useState<string | null>(null);

  // `if (loading)` returns a spinner, which unmounts the question card -- and the card holds the
  // answer the learner has just picked. React's dev-mode StrictMode runs this effect a second time,
  // so the blank used to happen after the first paint: a selection made in that window vanished and
  // Submit went back to disabled. The probe symptom was "one press of the submit control sends the
  // attempt" failing while every API call answered 200. Blank only a screen that is still empty.
  const painted = useRef(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!painted.current) setLoading(true);
      setError(null);
      try {
        await flow.ready();
        if (cancelled) return;
        setStarted(Date.now());
        const data = await get<DiagnosticResponse>("/api/v1/assessment/diagnostic");
        if (cancelled) return;
        if (data.error || !data.data) setError(data.error ?? "no response from the assessment endpoint");
        else {
          setItems(data.data.items);
          if (data.data.items.length) painted.current = true;
          setFraming({ purpose: data.data.purpose, how: data.data.how_it_is_used, time: data.data.note_on_time });
        }
      } catch (err) {
        if (!cancelled) setError(errMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(itemId: string, answer: unknown, extra: { teachback_text?: string; attempt_no?: number }): Promise<Receipt> {
    // Timings, first-action and change counts are added by useFlow.submit: this page only
    // says which item and what was typed, which is the whole reason the flow lives in one
    // hook instead of being re-implemented per screen.
    const receipt = await flow.submit(itemId, answer, extra);
    setReceipts((r) => ({ ...r, [i]: receipt }));
    return receipt;
  }

  async function finish() {
    setLoading(true);
    setError(null);
    setShortfall(null);
    // Close the session first, exactly as practice does: finishing is what tells the backend
    // the placement sequence ended, and it is the point at which "placed" is decided. The
    // answer used to be dropped on the floor, so a learner who stopped early was never told
    // that the band they expected had not been written.
    const done = (await flow.finish().catch(() => null)) as Record<string, unknown> | null;
    if (done && done.placed === false) setShortfall(typeof done.note === "string" ? done.note : "not enough of placement was answered to band you yet");
    try {
      const out = await post<AnalyzeRaw>(`/api/v1/analyze?session_id=${encodeURIComponent(flow.sessionId ?? "")}`, {});
      setReport(summarise(out));
    } catch (err) {
      // No loop yet is a real answer, not a crash: with one data point per topic there is
      // nothing to propose, and pretending otherwise would be the app flattering itself.
      if (err instanceof ApiError && err.status === 409) {
        setReport({ loop: [], hypotheses: 0, proposals: 0, note: errMessage(err), next: ["practise a few more sessions"] , evidence: 0, provider: null });
      } else setError(errMessage(err));
    }
    setLoading(false);
  }

  // question_shown is pushed here, on the transition, not inside an effect in Question:
  // the timestamp has to be the moment the learner actually saw this item.
  useEffect(() => {
    const e = items[i];
    if (e) flow.onShown({ item: e.item, topic_id: e.topic_id, reason: e.reason, presentation: e.presentation, utility: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [i, items.length]);

  if (loading)
    return (
      <Card title="Placement" tone="muted">
        <p className="text-sm text-[var(--muted)]">starting the session…</p>
      </Card>
    );

  if (report)
    return (
      <div className="space-y-4">
        <Card title="Placement done" tone={report.proposals ? "good" : "muted"}>
          <p className="text-sm">{report.note ?? `${report.hypotheses} hypotheses on file from ${report.evidence} evidence rows.`}</p>
          {shortfall ? (
            <p className="mt-2">
              <Notice tone="warn">{shortfall}</Notice>
            </p>
          ) : null}
          <ol className="mt-2 space-y-1 text-xs text-[var(--muted)]">
            {report.loop.map((l, i) => (
              <li key={i}>
                <span className="text-[var(--fg)]/70">{l.step}</span> — {l.done}
                {l.evidence ? <span className="opacity-70"> ({l.evidence})</span> : null}
              </li>
            ))}
          </ol>
          {report.next.length ? (
            <ul className="mt-2 list-disc pl-5 text-sm text-[var(--muted)]">
              {report.next.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : null}
          <div className="mt-3 flex gap-2">
            <Btn kind="primary" href="/learn">
              Start practising
            </Btn>
            <Btn href="/proposals">Changes waiting</Btn>
            <Btn href="/progress">See the estimates</Btn>
          </div>
        </Card>
      </div>
    );

  if (error && items.length === 0) return <Err error={error} status={null} />;

  const entry = items[i];
  const last = receipts[i] ?? null;
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--line)] pb-3">
        <h1 className="text-xl font-medium" style={{ fontFamily: "var(--font-serif)" }}>Placement</h1>
        <span className="text-sm text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)", fontSize: "11px", letterSpacing: "0.06em", textTransform: "uppercase" }}>
          question {Math.min(i + 1, items.length)} of {items.length}
        </span>
        {started ? <Tag tone="line">{Math.round((Date.now() - started) / 60000)} min in</Tag> : null}
        <div className="ml-auto flex gap-2">
          <Btn
            onClick={() => {
              if (i > 0) setI(i - 1);
            }}
            disabled={i === 0}
          >
            Back
          </Btn>
          {/* The branch used to be `i + 1 >= items.length || last`, which meant that as soon
              as a question was marked the button still labelled "Next" ran `finish` instead:
              answering question 1 of 12 ended placement and analysed a single attempt. A
              learner who did nothing wrong got a placement band built on one answer, and
              there was no way to reach question 2. "Next" now always advances; finishing
              early stays available on the button below it. */}
          {i + 1 >= items.length ? (
            <Btn kind="primary" onClick={finish}>
              Finish and analyse
            </Btn>
          ) : (
            <Btn
              kind="primary"
              onClick={() => setI(i + 1)}
              disabled={!last}
              title={last ? undefined : "answer this one first — the band needs a mark on every question it counts"}
            >
              Next
            </Btn>
          )}
          <Btn kind="ghost" onClick={finish}>
            stop here
          </Btn>
        </div>
      </div>
      <div className="h-1 w-full overflow-hidden rounded bg-[var(--line)]">
        <div className="h-full bg-[var(--accent)] transition-all" style={{ width: `${(i / Math.max(items.length, 1)) * 100}%` }} />
      </div>
      {flow.error ? <Notice tone="warn">session: {flow.error}</Notice> : null}
      <div className="space-y-1 text-xs text-[var(--muted)]">
        {framing.purpose ? <p>{framing.purpose} — and no hints or examples while it happens, because a placed band has to measure what you can do unaided.</p> : null}
        {framing.how ? <p>{framing.how}</p> : null}
        {framing.time ? <p>{framing.time}</p> : null}
      </div>
      {entry ? (
        <Question
          key={entry.item.id}
          entry={{
            item: entry.item,
            topic_id: entry.topic_id,
            reason: entry.reason,
            presentation: entry.presentation,
            utility: 0,
          }}
          aidless
          attemptNo={i + 1}
          submitted={receipts[i] ?? null}
          onSubmit={submit}
          onInteract={flow.onInteract}
          onChange={flow.onChange}
          onHint={flow.onHint}
          onExample={flow.onExample}
          onExplanation={flow.onExplanation}
        />
      ) : null}
      {last ? <ReceiptView receipt={last} /> : null}
      {error ? <Notice tone="warn">{error}</Notice> : null}
    </div>
  );
}

function errMessage(err: unknown): string {
  const e = err as { shown?: string; message?: string };
  return e?.shown ?? e?.message ?? String(err);
}
