"use client";

/** The review queue.
 *
 * Dates are computed from your own recall curve (BKT + the spacing model), not a fixed
 * 1/3/7 schedule: a topic comes back at the point your projected recall reaches about
 * 85%. The `rule` string from the server is shown verbatim rather than paraphrased, so
 * if the rule changes the sentence changes with it.
 *
 * Answering here is a self-report on purpose — it is the only way a review that happens
 * outside a practice session can move the schedule at all, and the screen says so instead
 * of pretending a tap is a test.
 *
 * The horizon is not decorative: the dot position is driven by the real `due_at` value.
 * If the API lacks `due_at`, the layout falls back to discrete lanes rather than
 * inventing a continuous position.
 */
import { useState } from "react";

import { Btn, Card, Err, Notice, SessionLog, Tag, Textarea, announce, useSessionLedger } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import type { ReviewComplete, ReviewDue } from "@/lib/types";

export default function Review() {
  const due = useApi<ReviewDue>("review/due", { within_hours: 72 });
  const plan = useApi<{ retention?: { checks?: number; calibration?: { n?: number; brier?: number; ece?: number; buckets?: { label?: string; predicted?: number; observed?: number; n?: number }[] } } }>("plan");
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<Record<string, ReviewComplete>>({});
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  /** Reviews rated in this session, newest first. The live region says each one once and is
   *  gone; the Changes screen keeps the same kind of ledger, and a self-report that leaves no
   *  trace on screen is a self-report the learner cannot check. */
  const ledger = useSessionLedger("adapt:ledger-reviews");

  const rows = due.data?.due ?? [];
  const cal = plan.data?.retention?.calibration;

  async function answer(topic: ReviewDue["due"][number], correct: boolean) {
    setBusy(topic.topic_id);
    setError(null);
    try {
      const out = await post<ReviewComplete>("review/complete", {
        topic_id: topic.topic_id,
        correct,
        score: correct ? 1 : 0,
        kind: "scheduled",
        ...(note.trim() ? { note: note.trim() } : {}),
      });
      setLog((l) => ({ ...l, [topic.topic_id]: out }));
      const next = out.scheduled ? when(out.scheduled.due_at) : "not rescheduled";
      announce(`${topic.title} rated ${correct ? "recalled" : "forgotten"} — next ${next}`);
      ledger.add(`${new Date().toLocaleTimeString()} — ${topic.title} rated “${correct ? "recalled" : "forgotten"}” → next ${next}`);
      due.reload();
    } catch (err) {
      const e = err as { status?: number; shown?: string };
      if (e?.status === 422) setError("that topic is not in the review set — reload and see what is actually scheduled");
      else setError(e?.shown ?? String(err));
    } finally {
      setBusy(null);
    }
  }

  function horizonLeft(due_at: string | null | undefined, overdue: boolean | undefined): string {
    if (!due_at) return overdue ? "28%" : "72%";
    const t = new Date(due_at).getTime();
    if (Number.isNaN(t)) return overdue ? "28%" : "72%";
    const deltaH = (t - Date.now()) / 3600000;
    const clamped = Math.max(-72, Math.min(72, deltaH));
    const pct = 50 + (clamped / 72) * 42;
    return `${pct}%`;
  }

  if (due.loading) return <Card title="Review" tone="muted"><p className="text-sm text-[var(--muted)]">checking what is due…</p></Card>;
  if (due.error) return <Err error={due.error} status={due.status} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-[var(--line)] pb-4">
        <div>
          <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>§ 04 · Review</div>
          <h1 className="text-xl" style={{ fontFamily: "var(--font-serif)" }}>Review queue</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">{due.data?.rule}</p>
        </div>
        <span className="text-xs uppercase tracking-[0.08em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>{rows.length ? `${rows.length} waiting` : "nothing due"}</span>
      </div>

      {rows.length === 0 ? (
        <Card title="Nothing scheduled" tone="good">
          <p className="text-sm text-[var(--muted)]">
            Every topic you have touched is still inside its own window. If that feels like it should be impossible, it is
            worth remembering the window is derived from what you actually recalled — a topic you keep getting right earns a
            long gap, and the app would rather ask you later than flatter you tomorrow.
          </p>
          <div className="mt-3 flex gap-2">
            <Btn kind="primary" href="/learn">Practise</Btn>
            <Btn href="/progress">See the estimates</Btn>
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {/* Horizon: spatial encoding of due position, driven by real due_at. */}
          <div className="horizon" aria-hidden="true">
            <span className="now-label">now</span>
            <div className="now-line" />
            {rows.map((r) => {
              const done = log[r.topic_id];
              const scheduledDue = done?.scheduled?.due_at ?? r.due_at;
              const left = horizonLeft(scheduledDue, r.overdue);
              const tone = done ? (done.correct ? "good" : "warn") : r.overdue ? "warn" : "ask";
              return (
                <div
                  key={r.topic_id}
                  className={`rv-item ${done ? "rated" : ""}`}
                  data-tone={tone}
                  style={{ left, position: "absolute", top: "28px", transform: "translateX(-50%)", transition: "left 420ms cubic-bezier(0.22,1,0.36,1)" }}
                  title={`${r.title} · due ${when(scheduledDue)}${done ? ` · rated ${done.correct ? "recalled" : "forgotten"}` : ""}`}
                >
                  <span className="rv-dot" style={{
                    display: "block",
                    width: 10, height: 10, borderRadius: "50%",
                    background: tone === "good" ? "var(--good)" : tone === "warn" ? "var(--warn)" : "var(--ask)",
                    border: "2px solid var(--panel)",
                    boxShadow: "0 0 0 1px var(--line)",
                  }} />
                  <span className="rv-label" style={{ fontFamily: "var(--font-mono)", fontSize: 10, whiteSpace: "nowrap", color: "var(--muted)", marginTop: 4, display: "block", textAlign: "center", maxWidth: 80, overflow: "hidden", textOverflow: "ellipsis" }}>{r.title}</span>
                </div>
              );
            })}
            <div className="horizon-axis" style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 1, background: "var(--line)" }} />
          </div>

          <div className="space-y-3">
            {rows.map((r) => {
              const done = log[r.topic_id];
              return (
                <Card
                  key={r.topic_id}
                  tone={r.overdue ? "warn" : "ask"}
                  title={<span className="normal-case">{r.title}</span>}
                  right={
                    <span className="flex items-center gap-2 text-xs">
                      {r.overdue ? <Tag tone="bad">overdue</Tag> : null}
                      <span>due {when(r.due_at)}</span>
                    </span>
                  }
                >
                  <p className="text-sm">
                    projected recall at this moment:{" "}
                    <strong>{Math.round((r.expected_recall_at_due ?? 0.85) * 100)}%</strong> · recall halves after{" "}
                    {Math.round(r.half_life_hours ?? 0)} h · {r.reps ?? 0} successful review{r.reps === 1 ? "" : "s"}
                    {r.lapses ? `, ${r.lapses} lapse${r.lapses === 1 ? "" : "s"}` : ""}
                  </p>
                  {done ? (
                    <Notice tone={done.scheduled ? "good" : "info"}>
                      logged {done.correct ? "as recalled" : "as forgotten"} — next check{" "}
                      {done.scheduled ? when(done.scheduled.due_at) : "not rescheduled"}
                      {done.scheduled ? ` (projected ${Math.round((done.scheduled.expected_recall_at_due ?? 0.85) * 100)}% then)` : ""}
                      . {done.correct && (done.scheduled?.reps ?? 0) > 1 ? "The gap grew because you were right twice in a row, not because a schedule said so." : ""}
                    </Notice>
                  ) : (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {/* Each rating is its own control with its own accessible name, and
                          each sends a distinct rating to review/complete. */}
                      <button
                        className="btn-ctl btn-secondary danger"
                        aria-label={`${r.title}: It went`}
                        disabled={busy === r.topic_id}
                        onClick={() => answer(r, false)}
                      >
                        It went
                      </button>
                      <button
                        className="btn-ctl btn-secondary good"
                        aria-label={`${r.title}: I still know it`}
                        disabled={busy === r.topic_id}
                        onClick={() => answer(r, true)}
                      >
                        I still know it
                      </button>
                      <Btn kind="ghost" href={`/learn`} title="a real retest through practice moves the same schedule">
                        do it properly instead
                      </Btn>
                    </div>
                  )}
                </Card>
              );
            })}
            <div className="mt-2">
              <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything you want the next session to know (optional, stored with this review)" />
            </div>
            {error ? <Notice tone="bad">{error}</Notice> : null}
          </div>
        </div>
      )}

      <SessionLog title="Reviews completed this session" entries={ledger.entries} empty="Nothing reviewed yet." />

      <Card title="How well your own predictions line up" tone="muted" right={<span>{cal?.n ?? 0} checks</span>}>
        {!cal || (cal.n ?? 0) < 3 ? (
          <p className="text-sm text-[var(--muted)]">
            Not enough delayed checks yet to say anything honest about calibration. With {cal?.n ?? 0} it would be noise with
            a decimal point, so the table stays empty until there is something in it.
          </p>
        ) : (
          <>
            <p className="text-sm">
              Brier score <strong>{cal.brier?.toFixed(3)}</strong> (lower is better, and answering 0.5 every time scores 0.25) ·
              expected calibration error <strong>{cal.ece?.toFixed(3)}</strong>
            </p>
            <div className="table-scroll">
              <table className="mt-2 w-full text-sm">
                <thead className="text-xs text-[var(--muted)]">
                  <tr>
                    <th className="text-left">predicted</th>
                    <th className="text-right">observed</th>
                    <th className="text-right">n</th>
                  </tr>
                </thead>
                <tbody>
                  {(cal.buckets ?? []).map((b, i) => (
                    <tr key={i} className="border-t border-[var(--line)]">
                      <td>{Math.round((b.predicted ?? 0) * 100)}%</td>
                      <td className="text-right">{Math.round((b.observed ?? 0) * 100)}%</td>
                      <td className="text-right text-[var(--muted)]">{b.n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function when(iso?: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return String(iso);
  const h = (t - Date.now()) / 3600000;
  if (h < -24) return `${Math.round(-h / 24)} d overdue`;
  if (h < 0) return "due now";
  if (h < 1) return `in ${Math.round(h * 60)} min`;
  if (h < 36) return `in ${h.toFixed(1)} h`;
  return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
}
