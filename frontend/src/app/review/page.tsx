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
import { friendlyLabel } from "@/lib/labels";
import { useState } from "react";

import { Btn, Card, Err, Notice, PageHeader, SessionLog, Tag, Textarea, announce, useSessionLedger } from "@/components/ui";
import { get, post, useApi } from "@/lib/api";
import type { RetentionCheck, ReviewComplete, ReviewDue } from "@/lib/types";
import { ReadAloud } from "@/components/voice/ReadAloud";

export default function Review() {
  const due = useApi<ReviewDue>("review/due", { within_hours: 72 });
  const plan = useApi<{ retention?: { checks?: number; calibration?: { n?: number; brier?: number; ece?: number; buckets?: { label?: string; predicted?: number; observed?: number; n?: number }[] } } }>("plan");
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<Record<string, ReviewComplete>>({});
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  /** The retention check per topic (prompt 5's screen): the question and why now, asked only
   *  when the learner presses for it, and the answer key fetched only *after* they have answered. */
  const [checks, setChecks] = useState<Record<string, RetentionCheck>>({});
  const [checkNotes, setCheckNotes] = useState<Record<string, string>>({});
  /** Reviews rated in this session, newest first. The live region says each one once and is
   *  gone; the Changes screen keeps the same kind of ledger, and a self-report that leaves no
   *  trace on screen is a self-report the learner cannot check. */
  const ledger = useSessionLedger("adapt:ledger-reviews");

  const rows = due.data?.due ?? [];
  const cal = plan.data?.retention?.calibration;

  async function checkFor(topic: ReviewDue["due"][number], withNotes = false) {
    setBusy(`check:${topic.topic_id}`);
    setError(null);
    try {
      const out = await get<RetentionCheck>("review/check", {
        topic_id: topic.topic_id,
        ...(withNotes ? { with_notes: 1 } : {}),
      });
      if (!out.data) {
        setError(out.error ?? "no check is scheduled for that topic");
        return;
      }
      if (withNotes && out.data.expected_answer_notes) {
        setCheckNotes((n) => ({ ...n, [topic.topic_id]: out.data!.expected_answer_notes! }));
      } else {
        setChecks((c) => ({ ...c, [topic.topic_id]: out.data! }));
      }
    } finally {
      setBusy(null);
    }
  }

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
      // now that an answer exists, the key may be shown
      if (checks[topic.topic_id]) void checkFor(topic, true);
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

  if (due.loading) return (
    <div className="space-y-5">
      <PageHeader title="Review queue" subtitle="Short revisits when something is due." />
      <div className="card loading-card" role="status" aria-busy="true" aria-label="Loading review queue">
        <div className="loading-spinner" aria-hidden="true" />
        <p className="loading-text">Checking what is due…</p>
      </div>
      <div className="skeleton-grid">
        <div className="skeleton-card"><div className="skeleton-line medium large" /><div className="skeleton-line full" /><div className="skeleton-line short" /></div>
        <div className="skeleton-card"><div className="skeleton-line medium large" /><div className="skeleton-line full" /><div className="skeleton-line short" /></div>
      </div>
    </div>
  );
  if (due.error) return (
    <div className="space-y-4">
      <PageHeader title="Review queue" subtitle="Short revisits when something is due." />
      <Err error={due.error} status={due.status} />
    </div>
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Review queue"
        subtitle={due.data?.rule ?? "Short revisits when something is due."}
        actions={<Btn onClick={() => due.reload()}>Reload</Btn>}
      />

      {rows.length === 0 ? (
        <Card
          title="All caught up — nothing due"
          sub="Every topic is still inside its review window."
          tone="mint"
          data-empty="review"
        >
          <div className="empty-state">
            <p className="empty-state-text">
              A topic you keep getting right earns a longer gap — the app would rather ask you later than flatter you tomorrow.
            </p>
            <div className="wrap-2">
              <Btn icon="play" kind="primary" href="/learn">Practise</Btn>
              <Btn kind="secondary" href="/lesson">Open a lesson</Btn>
            </div>
          </div>
        </Card>
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-end justify-between gap-3 border-b border-[var(--line)] pb-3">
            <h2 className="t-h2" style={{ margin: 0 }}>{`${rows.length} waiting`}</h2>
          </div>
          {/* Horizon: spatial encoding of due position, driven by real due_at. */}
          <div className="horizon" aria-hidden="true" style={{ marginTop: "var(--s3)" }}>
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
                  <span className="rv-dot" />
                  <span className="rv-label">{friendlyLabel(r.title) ?? "Review topic"}</span>
                </div>
              );
            })}
            <div className="horizon-axis" />
          </div>

          <div className="space-y-5">
            {rows.map((r) => {
              const done = log[r.topic_id];
              return (
                <Card
                  key={r.topic_id}
                  tone={r.overdue ? "warn" : "ask"}
                  title={<span className="normal-case">{friendlyLabel(r.title) ?? "Review topic"}</span>}
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
                  {checks[r.topic_id] ? (
                    <div className="mt-2 rounded-[2px] border border-[var(--line)] px-3 py-2 text-sm">
                      <p>{checks[r.topic_id].question}</p>
                      <p className="mt-1"><ReadAloud text={checks[r.topic_id].question} label="Read the question aloud" /></p>
                      <p className="mt-1 text-xs text-[var(--muted)]">{checks[r.topic_id].rationale}</p>
                      {checkNotes[r.topic_id] ? (
                        <p className="mt-1 text-xs">
                          <span className="text-[var(--muted)]">what it should have covered:</span>{" "}
                          {checkNotes[r.topic_id]}
                        </p>
                      ) : done ? (
                        <Btn kind="ghost" onClick={() => checkFor(r, true)} disabled={busy === `check:${r.topic_id}`}>
                          what should it have covered?
                        </Btn>
                      ) : (
                        <p className="mt-1 text-xs text-[var(--muted)]">
                          answer it in your head (or say it out loud), then rate below — the answer key opens after that.
                        </p>
                      )}
                    </div>
                  ) : null}
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
                      <Btn
                        kind="ghost"
                        onClick={() => checkFor(r)}
                        disabled={busy === `check:${r.topic_id}`}
                        title="the question the schedule says is worth asking right now"
                      >
                        {busy === `check:${r.topic_id}` ? "…" : "Show me the check"}
                      </Btn>
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

      <div className="grid items-start gap-4 md:grid-cols-2">
        <div style={{ marginTop: 0 }}>
          <SessionLog title="Reviews completed this session" entries={ledger.entries} empty="Nothing reviewed yet." />
        </div>

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
