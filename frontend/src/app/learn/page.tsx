"use client";

/** The main practice loop.
 *
 * The queue comes from `GET /practice`, which is already ranked by the selector: expected
 * information gain, cost in your time, your stated preferences and the effect of whatever
 * adaptations you have approved. This page deliberately does not re-sort it, and it shows
 * `reason` + the decision trace next to every question so the ranking is inspectable
 * rather than magic. `GET /practice` never carries an answer key, so nothing here can peek.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { EffectLine, type EffectSummary } from "@/components/evidence";
import { Question } from "@/components/question";
import { Btn, Card, Err, Notice, Tag } from "@/components/ui";
import { summarise, type AnalyzeRaw, type AnalyzeSummary } from "@/lib/analyze";
import { useFlow } from "@/lib/flow";
import { get, post } from "@/lib/api";
import type { ActiveAdaptation, PlanResponse, Receipt } from "@/lib/types";

export default function Learn() {
  const flow = useFlow("practice");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [i, setI] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<ActiveAdaptation[]>([]);
  const [due, setDue] = useState(0);
  const [report, setReport] = useState<AnalyzeSummary | null>(null);
  // Per item, for the same reason as placement: paging back to a question should show the mark
  // it already has instead of an unanswered form.
  const [receipts, setReceipts] = useState<Record<number, Receipt>>({});
  const [finishing, setFinishing] = useState(false);

  const items = plan?.items ?? [];
  const entry = items[i];
  const last = receipts[i] ?? null;

  // same hazard as /diagnose: `loading` blanks the practice card, and the card is where a typed or
  // ticked answer lives. Only an empty screen may be blanked.
  const painted = useRef(false);
  useEffect(() => {
    let alive = true;
    (async () => {
      if (!painted.current) setLoading(true);
      try {
        const [planRes, act, dd] = await Promise.all([
          get<PlanResponse>("/api/v1/practice?count=4"),
          get<{ active: ActiveAdaptation[] }>("/api/v1/adaptations/active"),
          get<{ count: number }>("/api/v1/review/due"),
        ]);
        if (!alive) return;
        if (planRes.error) setError(planRes.error);
        else {
          setPlan(planRes.data);
          if ((planRes.data?.items ?? []).length) painted.current = true;
        }
        setActive(act.data?.active ?? []);
        setDue(dd.data?.count ?? 0);
      } catch (err) {
        if (alive) setError(msg(err));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (entry) flow.onShown({ ...entry });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [i, items.length]);

  const submit = useCallback(
    async (itemId: string, answer: unknown, extra: { teachback_text?: string; attempt_no?: number }) => {
      const receipt = await flow.submit(itemId, answer, { ...extra, attempt_no: extra.attempt_no ?? i + 1 });
      setReceipts((r) => ({ ...r, [i]: receipt }));
      return receipt;
    },
    [flow, i],
  );

  const finish = useCallback(async () => {
    setFinishing(true);
    setError(null);
    const done = await flow.finish();
    const sessionId = (done?.session_id as string | undefined) ?? flow.sessionId;
    try {
      const out = await post<AnalyzeRaw>(`/api/v1/analyze?session_id=${encodeURIComponent(sessionId ?? "")}`, {});
      setReport(summarise(out));
    } catch (err) {
      const e = err as { status?: number; shown?: string };
      if (e?.status === 409) setReport({ loop: [], hypotheses: 0, proposals: 0, note: e.shown ?? "not enough logged yet" , evidence: 0, provider: null, next: [] });
      else setError(msg(err));
    } finally {
      setFinishing(false);
    }
  }, [flow]);

  if (loading)
    return (
      <Card title="Practice" tone="muted">
        <p className="text-sm text-[var(--muted)]">building your queue from the last session&rsquo;s data…</p>
      </Card>
    );
  if (error && items.length === 0) return <Err error={error} status={null} />;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--line)] pb-3">
        <div>
          <div className="text-[10.5px] uppercase tracking-[0.14em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>§ 01 · Practise</div>
          <h1 className="text-[clamp(22px,3vw,30px)] font-medium" style={{ fontFamily: "var(--font-serif)" }}>Practice</h1>
        </div>
        {items.length ? (
          <span className="ml-4 text-sm text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }}>
            {Math.min(i + 1, items.length)} of {items.length}
          </span>
        ) : null}
        {due ? (
          <Btn kind="ghost" href="/review" title="spaced review is due; it is short">
            {due} to review
          </Btn>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          <Btn href="/progress" kind="quiet">
            estimates
          </Btn>
          <Btn kind="primary" onClick={finish} disabled={finishing}>
            {finishing ? "…" : "Finish session"}
          </Btn>
        </div>
      </div>

      {flow.error ? <Notice tone="warn">session: {flow.error}</Notice> : null}
      {report ? (
        <Card tone={report.proposals ? "ask" : "muted"} title={report.proposals ? "Something worth your attention" : "Nothing to propose"}>
          <p className="text-sm">{report.note ?? `${report.hypotheses} hypotheses on file.`}</p>
          {report.proposals ? (
            <p className="mt-2 text-sm">
              <Btn kind="primary" href="/proposals">
                {report.proposals} change{report.proposals === 1 ? "" : "s"} waiting for you
              </Btn>
            </p>
          ) : (
            <p className="mt-2 text-xs text-[var(--muted)]">
              That is the expected state after a normal session. A proposal needs a consistent pattern across enough items, and
              inventing one to look clever is the failure mode the gate exists to stop.
            </p>
          )}
        </Card>
      ) : null}

      {active.length ? (
        <Card title="Changes you have approved" right={<span>affect what you see now</span>}>
          <ul className="space-y-1 text-sm">
            {active.map((a) => (
              <li key={a.id} className="flex items-start gap-2">
                <Tag tone="good">on</Tag>
                <div className="min-w-0 flex-1">
                  <strong>{a.label ?? a.strategy}</strong>{" "}
                  {typeof a.effect_summary === "string" ? (
                    <span className="text-[var(--muted)]">{a.effect_summary}</span>
                  ) : a.effect_summary ? null : (
                    <span className="text-[var(--muted)]">being measured on {a.watch_metric ?? "the next few answers"}</span>
                  )}
                  {a.effect_summary && typeof a.effect_summary !== "string" ? (
                    <EffectLine effect={a.effect_summary as unknown as EffectSummary} />
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-[var(--muted)]">
            These stay on until you turn them off, and each one is measured against the next few items.
          </p>
        </Card>
      ) : null}

      {items.length === 0 ? (
        <Card title="No queue yet" tone="warn">
          <p className="text-sm">
            The planner had nothing to put in front of you — usually because placement has not happened, or every topic is
            waiting for a review date.
          </p>
          <div className="mt-3 flex gap-2">
            <Btn kind="primary" href="/diagnose">
              Start placement
            </Btn>
            <Btn href="/review">Review queue</Btn>
          </div>
        </Card>
      ) : null}

      {entry ? (
        <>
          <Question
            key={entry.item.id}
            entry={entry}
            submitted={last}
            onSubmit={submit}
            onInteract={flow.onInteract}
            onChange={flow.onChange}
            onHint={flow.onHint}
            onExample={flow.onExample}
            onExplanation={flow.onExplanation}
          />
          <div className="flex items-center gap-2">
            <Btn onClick={() => setI(Math.max(0, i - 1))} disabled={i === 0}>
              Previous
            </Btn>
            {i + 1 < items.length ? (
              <Btn kind="primary" onClick={() => setI(i + 1)}>
                Next question
              </Btn>
            ) : (
              <Btn kind="primary" onClick={finish} disabled={finishing}>
                {finishing ? "…" : "Finish and analyse"}
              </Btn>
            )}
            <span className="text-xs text-[var(--muted)]">
              nothing is timed and no streak breaks — leaving a question unanswered is a legitimate choice
            </span>
          </div>
        </>
      ) : null}

      {plan?.why ? (
        <Card title="Why this order" tone="muted">
          <p className="text-sm whitespace-pre-line">{plan.why}</p>
        </Card>
      ) : null}
      {error ? <Notice tone="warn">{error}</Notice> : null}
    </div>
  );
}

function msg(err: unknown): string {
  const e = err as { shown?: string; message?: string };
  return e?.shown ?? e?.message ?? String(err);
}
