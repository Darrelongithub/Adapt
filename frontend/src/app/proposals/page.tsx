"use client";

/** The approval gate, as a screen.
 *
 * This is the part of ADAPT that is not a feature but a boundary: AI 1 can write a
 * proposal and nothing else. Four answers are accepted — accept, reject, modify, later —
 * and each is recorded with the timestamp the *server* saw, because the baseline for
 * "did it help" is taken at the real decision time.
 *
 * The layout follows the payload's own separation of fact from guess: `facts` (observable
 * events only), `pattern`, `hypothesis`, `alternatives`, `change`, then how it will be
 * checked. A proposal that cannot state a disproof is not offered as a yes — the server
 * refuses to apply those, so the UI says so in the same words.
 */
import { useState } from "react";

import { Btn, Card, Err, Field, humanize, Ledger, LedgerEntry, Notice, Tag, Textarea, When, announce, useSession } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import type { ActiveAdaptation, DecisionReply, Proposal } from "@/lib/types";

const VERBS = [
  { id: "accept", label: "Yes, try it", tone: "good" as const, hint: "Turns it on now and measures it over the next few answers." },
  { id: "reject", label: "No", tone: "muted" as const, hint: "Recorded as a no. The same question will not come back tomorrow." },
  { id: "modify", label: "Yes, but change it", tone: "think" as const, hint: "Edit the numbers below; only known keys are applied." },
  { id: "later", label: "Not now", tone: "warn" as const, hint: "Kept, unapplied, and it will surface once more before it goes quiet." },
];

export default function Proposals() {
  const { me, loading: meLoading, unknown: meUnknown } = useSession();
  const pending = useApi<{ proposals: Proposal[]; explain?: string }>("adaptations/pending");
  const active = useApi<{ active: ActiveAdaptation[]; waiting_for_effect?: ActiveAdaptation[] }>("adaptations/active");
  const history = useApi<{ decisions: Proposal[] }>("adaptations/history");
  const [chosen, setChosen] = useState<string | null>(null);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [days, setDays] = useState(14);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DecisionReply | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rows = pending.data?.proposals ?? [];
  const live = active.data?.active ?? [];
  const waiting = active.data?.waiting_for_effect ?? [];
  const past = history.data?.decisions ?? [];
  const current = rows.find((p) => p.id === chosen) ?? null;

  async function decide(proposal: Proposal, decision: string) {
    setBusy(true);
    setError(null);
    try {
      const modifications: Record<string, unknown> = {};
      if (decision === "modify") {
        for (const [k, v] of Object.entries(editing)) {
          try {
            modifications[k] = JSON.parse(v);
          } catch {
            modifications[k] = v;
          }
        }
      }
      const body: Record<string, unknown> = { decision, note: note || null, modifications };
      if (decision === "modify" && proposal.review_window?.kind === "days") body.days = days;
      const out = await post<DecisionReply>(`adaptations/${proposal.id}/decision`, body);
      setResult(out);
      announce(`Decision recorded: ${decision} for ${proposal.strategy_label ?? proposal.strategy}`);
      pending.reload();
      active.reload();
      history.reload();
    } catch (err) {
      const e = err as { shown?: string; status?: number; detail?: unknown };
      if (e?.status === 409) setResult({ ok: false, status: "approved", proposal_id: proposal.id, reason: "already answered" });
      else if (e?.status === 403) setError(`the gate refused it: ${JSON.stringify(e.detail)}`);
      else setError(e?.shown ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(row: ActiveAdaptation) {
    setBusy(true);
    setError(null);
    try {
      await post(`adaptations/${row.id}/revoke`, {});
      announce(`Decision revoked for ${row.label ?? row.strategy}`);
      active.reload();
      history.reload();
    } catch (err) {
      setError((err as { shown?: string }).shown ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!meLoading && !me && !meUnknown)
    return <Err error="nobody is signed in, so there is nothing to approve" status={401} />;

  return (
    <div className="page-changes">
      <div className="cf-head">§ 03 · Changes</div>
      <h2 className="cf-sub">Nothing here takes effect until you decide.</h2>
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-[var(--line)] pb-4">
        <div>
          <h1 className="text-xl font-medium" style={{ fontFamily: "var(--font-serif)" }}>Changes waiting for you</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {pending.data?.explain ??
              "Each row separates what was observed from what we think it means. Nothing here has been applied."}
          </p>
        </div>
        <div className="flex gap-2">
          <Btn onClick={() => Promise.all([pending.reload(), active.reload(), history.reload()])}>Reload</Btn>
          <Btn
            onClick={async () => {
              setBusy(true);
              try {
                await post("adaptations/evaluate", {});
                active.reload();
              } catch (err) {
                setError((err as { shown?: string }).shown ?? String(err));
              } finally {
                setBusy(false);
              }
            }}
            title="asks AI 1 whether the approved changes are measurably helping"
          >
            Check the effect
          </Btn>
        </div>
      </div>

      {error ? <div className="mt-4"><Notice tone="bad">{error}</Notice></div> : null}

      {rows.length === 0 && !pending.loading ? (
        <Card title="Nothing to approve" tone="muted">
          <p className="text-sm text-[var(--muted)]">
            A proposal needs a pattern that repeats across enough answers to be worth your attention. If you have finished a
            session and no proposal appeared, that means the data did not support one — which is the normal outcome, not a
            failure to notice something.
          </p>
        </Card>
      ) : null}

      {rows.length ? (
        <Ledger>
          {rows.map((p) => {
            const open = chosen === p.id;
            const isDecided = result?.proposal_id === p.id;
            return (
              <LedgerEntry
                key={p.id}
                time={<When ts={p.created_at} />}
                title={p.ask ?? p.strategy_label ?? p.strategy}
                actions={
                  <div className="case-stamps" role="group" aria-label={`Decision for: ${p.ask ?? p.strategy_label ?? p.strategy}`}>
                    {VERBS.map((v) => {
                      const isActive = isDecided && result?.decision === v.id;
                      return (
                        <button
                          key={v.id}
                          className={`stamp-btn ${v.id}`}
                          aria-pressed={isActive}
                          disabled={busy}
                          title={v.hint}
                          onClick={() => {
                            if (v.id === "modify") {
                              setChosen(open ? null : p.id);
                              setEditing(flatten(p.action_payload ?? {}));
                              return;
                            }
                            void decide(p, v.id);
                          }}
                        >
                          {v.label}
                        </button>
                      );
                    })}
                  </div>
                }
              >
                <dl className="space-y-3 text-sm">
                  <Part label="What was observed" tone="good" body={p.facts} note="events only — no interpretation" />
                  {p.pattern ? <Part label="The pattern in them" body={p.pattern} /> : null}
                  <Part
                    label="The explanation on offer"
                    tone="think"
                    body={p.hypothesis}
                    note="a hypothesis, not a diagnosis. Nobody is told what they are; they are shown what might be going on."
                  />
                  {p.alternatives?.length ? (
                    <div>
                      <dt className="text-xs text-[var(--muted)] uppercase" style={{ fontFamily: "var(--font-mono)" }}>Other things that would produce this</dt>
                      <dd className="mt-1">
                        <ul className="list-disc space-y-0.5 pl-5">
                          {p.alternatives.map((a, i) => (
                            <li key={i} className="text-[var(--muted)]">
                              {typeof a === "string" ? a : humanize(a)}
                              {typeof a === "object" && (a as Record<string, unknown>).refuted_by ? <span className="opacity-70"> — refuted by {String((a as Record<string, unknown>).refuted_by)}</span> : null}
                            </li>
                          ))}
                        </ul>
                      </dd>
                    </div>
                  ) : null}
                  {p.caveat ? <Part label="What makes this shaky" tone="warn" body={p.caveat} /> : null}
                  <Part label="What saying yes would change" tone="accent" body={p.change ?? p.what_it_does} />
                  <div>
                    <dt className="text-xs text-[var(--muted)] uppercase" style={{ fontFamily: "var(--font-mono)" }}>How it would be judged</dt>
                    <dd className="mt-1 space-y-1">
                      <p>
                        <span className="text-[var(--accent)]">{p.review_metric ?? "the watch metric"}</span>{" "}
                        {p.review_window_text ?? `over ${p.review_window?.min_items ?? 6}-${p.review_window?.max_items ?? 14} more answers`}
                      </p>
                      {p.expected_effect ? <p className="text-[var(--muted)]">expected: {p.expected_effect}</p> : null}
                      {p.baseline && Object.keys(p.baseline).length ? (
                        <p className="text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
                          baseline now: {Object.entries(p.baseline).map(([k, v]) => `${k}=${typeof v === "number" ? round(v) : String(v)}`).join(", ")}
                        </p>
                      ) : null}
                    </dd>
                  </div>
                  {p.evidence_ids?.length ? (
                    <p className="text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
                      built from {p.evidence_ids.length} logged evidence row{p.evidence_ids.length === 1 ? "" : "s"}
                      {p.hypothesis_ref ? <> · hypothesis {p.hypothesis_ref.status} at {round(p.hypothesis_ref.posterior ?? 0)}</> : null}
                    </p>
                  ) : null}
                  {p.citations?.length ? (
                    <div>
                      <dt className="text-xs text-[var(--muted)] uppercase" style={{ fontFamily: "var(--font-mono)" }}>Why anyone believes this works</dt>
                      <dd className="mt-1 space-y-0.5 text-xs text-[var(--muted)]">
                        {p.citations.map((c, i) => (
                          <p key={c.ref ?? i} className="case-cite" style={{ display: "inline-block", margin: "2px 6px 2px 0", padding: "2px 6px", border: "1px solid var(--line)", background: "var(--panel-2)", borderRadius: "2px", fontFamily: "var(--font-mono)", fontSize: "11px" }}>
                            {c.url ? (
                              <a className="underline" href={c.url} target="_blank" rel="noreferrer">
                                {c.ref ?? c.title}
                              </a>
                            ) : (
                              <span>{c.ref ?? c.title}</span>
                            )}
                            {c.title && c.ref ? <span className="ml-1">— {c.title}</span> : null}
                          </p>
                        ))}
                      </dd>
                    </div>
                  ) : (
                    <p className="text-xs text-[var(--warn)]">
                      no published evidence is attached to this strategy, so treat it as an experiment rather than a known-good
                      technique.
                    </p>
                  )}
                </dl>

                {result?.proposal_id === p.id ? (
                  <div className="mt-3">
                    <Notice tone={result.ok ? "good" : "warn"}>
                      {result.ok
                        ? `${result.decision === "accept" || result.decision === "modify" ? "On." : result.decision === "later" ? "Kept, unapplied." : "Off, and noted."} ${result.note ?? ""} ${result.review?.note ?? ""}${result.active_adaptation_id ? ` (${result.active_adaptation_id})` : ""}`
                        : `${result.status}: ${result.reason ?? "not applied"}`}
                    </Notice>
                  </div>
                ) : null}

                {open ? (
                  <div className="mt-4 space-y-3 border-t border-[var(--line)] pt-4">
                    <p className="text-xs text-[var(--muted)]">
                      Only these keys are known to the strategy; anything else comes back in{" "}
                      <code>refused_keys</code> instead of being stored and ignored.
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {Object.entries(editing).map(([k, v]) => (
                        <Field key={k} label={k}>
                          <input
                            className="w-full rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 font-mono text-xs"
                            value={v}
                            onChange={(e) => setEditing((s) => ({ ...s, [k]: e.target.value }))}
                          />
                        </Field>
                      ))}
                      {Object.keys(editing).length === 0 ? (
                        <p className="text-xs text-[var(--muted)]">This strategy has no numbers to change — you can still add a note explaining what you meant.</p>
                      ) : null}
                    </div>
                    {current?.review_window?.kind === "days" ? (
                      <Field label="Re-check after (days)">
                        <input
                          type="number"
                          min={1}
                          max={365}
                          value={days}
                          onChange={(e) => setDays(Number(e.target.value))}
                          className="w-full rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm"
                        />
                      </Field>
                    ) : null}
                    <Field label="Say why (optional)" hint="It is stored with the decision and the tutor reads it.">
                      <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="two, not four — I lose the thread after that" />
                    </Field>
                    <button
                      className="stamp-btn yes"
                      aria-pressed="false"
                      disabled={busy}
                      onClick={() => current && void decide(current, "modify")}
                      style={{ background: "var(--ink)", color: "var(--bg)" }}
                    >
                      {busy ? "…" : "Send it with those changes"}
                    </button>
                  </div>
                ) : null}
                <div className="mt-2 text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
                  Status: {result?.proposal_id === p.id ? (result.ok ? result.decision : result.status) : "pending — nothing applied"}
                </div>
              </LedgerEntry>
            );
          })}
        </Ledger>
      ) : null}

      <div className="ledger-log mt-8">
        <h5>Decisions made this session</h5>
        <ul>
          {result ? <li>{new Date().toLocaleTimeString()} — “{rows.find((r) => r.id === result.proposal_id)?.strategy_label ?? result.proposal_id}” → {result.decision ?? result.status}</li> : <li className="empty">Nothing decided yet.</li>}
        </ul>
      </div>

      <div className="grid gap-4 md:grid-cols-2 mt-6">
        <Card
          title="On right now"
          tone="good"
          right={<span>{live.length ? `${waiting.length} not yet measured` : ""}</span>}
        >
          {live.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">Nothing is applied. Practice is running on your stated settings alone.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {live.map((a) => (
                <li key={a.id} className="flex items-start gap-2">
                  <div className="flex-1">
                    <p>
                      <strong>{a.label ?? a.strategy}</strong>{" "}
                      <span className="text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
                        since {a.started_at ? relative(a.started_at) : "—"} · watching {a.watch_metric ?? "the plan"}
                      </span>
                    </p>
                    {a.effect_summary ? (
                      <p className="text-xs text-[var(--muted)]">
                        {typeof a.effect_summary === "string" ? a.effect_summary : humanize(a.effect_summary)}
                      </p>
                    ) : (
                      <p className="text-xs text-[var(--think)]">too few answers so far to say whether it is helping</p>
                    )}
                    {a.mechanism ? <p className="text-xs text-[var(--muted)]">{a.mechanism}</p> : null}
                    {a.citations?.length ? <p className="text-xs text-[var(--muted)]">evidence: {a.citations.join(" · ")}</p> : null}
                  </div>
                  <Btn kind="ghost" onClick={() => revoke(a)} disabled={busy} title="turns it off now and records why">
                    turn off
                  </Btn>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="What you have already answered" tone="muted" right={<span>{past.length} decisions</span>}>
          {past.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">A no is a decision too, and it is kept: it stops the same proposal being re-asked.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {past.slice(0, 8).map((d) => (
                <li key={`${d.id}-${d.decided_at}`}>
                  <Tag tone={d.status === "approved" ? "good" : d.status === "rejected" ? "line" : "warn"}>{d.status}</Tag>{" "}
                  {d.strategy_label ?? d.strategy}
                  {d.student_note ? <span className="text-xs text-[var(--muted)]"> — you said: {d.student_note}</span> : null}
                  {d.outcome ? <span className="text-xs text-[var(--muted)]"> · {humanize(d.outcome)}</span> : null}
                  {d.evaluation_status === "insufficient_data" ? <span className="text-xs text-[var(--warn)]"> · not enough data yet</span> : null}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function Part({ label, body, note, tone }: { label: string; body?: string | null; note?: string; tone?: "good" | "warn" | "think" | "accent" }) {
  if (!body) return null;
  return (
    <div>
      <dt className={`text-xs uppercase ${tone ? `text-[var(--${tone})]` : "text-[var(--muted)]"}`} style={{ fontFamily: "var(--font-mono)" }}>{label}</dt>
      <dd className="mt-1">
        {body}
        {note ? <span className="mt-0.5 block text-xs text-[var(--muted)]">{note}</span> : null}
      </dd>
    </div>
  );
}

function flatten(obj: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj)) out[k] = typeof v === "string" ? v : JSON.stringify(v);
  return out;
}

function round(n: number): string {
  return n.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function relative(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}
