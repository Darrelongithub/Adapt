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
 */
import { useState } from "react";

import { Btn, Card, Err, Notice, Tag, Textarea } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import type { ReviewComplete, ReviewDue } from "@/lib/types";

export default function Review() {
  const due = useApi<ReviewDue>("review/due", { within_hours: 72 });
  const plan = useApi<{ retention?: { checks?: number; calibration?: { n?: number; brier?: number; ece?: number; buckets?: { label?: string; predicted?: number; observed?: number; n?: number }[] } } }>("plan");
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<Record<string, ReviewComplete>>({});
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");

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
      due.reload();
    } catch (err) {
      const e = err as { status?: number; shown?: string };
      if (e?.status === 422) setError("that topic is not in the review set — reload and see what is actually scheduled");
      else setError(e?.shown ?? String(err));
    } finally {
      setBusy(null);
    }
  }

  if (due.loading) return <Card title="Review" tone="muted"><p className="text-sm text-[var(--muted)]">checking what is due…</p></Card>;
  if (due.error) return <Err error={due.error} status={due.status} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Review queue</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">{due.data?.rule}</p>
        </div>
        <span className="text-sm text-[var(--muted)]">{rows.length ? `${rows.length} waiting` : "nothing due"}</span>
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
                  <strong>{Math.round((r.expected_recall_at_due ?? 0.85) * 100)}%</strong> · half-life{" "}
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
                    <Btn kind="primary" disabled={busy === r.topic_id} onClick={() => answer(r, true)}>
                      I still know it
                    </Btn>
                    <Btn disabled={busy === r.topic_id} onClick={() => answer(r, false)}>
                      It went
                    </Btn>
                    <Btn kind="ghost" href={`/learn`} title="a real retest through practice moves the same schedule">
                      do it properly instead
                    </Btn>
                  </div>
                )}
              </Card>
            );
          })}
          <Fieldish>
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything you want the next session to know (optional, stored with this review)" />
          </Fieldish>
          {error ? <Notice tone="bad">{error}</Notice> : null}
        </div>
      )}

      <Card title="How well your own predictions line up" tone="muted" right={<span>{cal?.n ?? 0} checks</span>}>
        {!cal || (cal.n ?? 0) < 3 ? (
          <p className="text-sm text-[var(--muted)]">
            Not enough delayed checks yet to say anything honest about calibration. With {cal?.n ?? 0} it would be noise with
            a decimal point, so the table stays empty until there is something in it.
          </p>
        ) : (
          <>
            <p className="text-sm">
              Brier score <strong>{cal.brier?.toFixed(3)}</strong> (lower is better; always-guessing-0.5 scores 0.25) ·
              expected calibration error <strong>{cal.ece?.toFixed(3)}</strong>
            </p>
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
          </>
        )}
      </Card>
    </div>
  );
}

function Fieldish({ children }: { children: React.ReactNode }) {
  return <div className="mt-2">{children}</div>;
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
