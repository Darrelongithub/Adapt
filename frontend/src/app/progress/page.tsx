"use client";

/** Progress, i.e. the part of the system that has to be legible or is not trustworthy.
 *
 * Everything here is an estimate with an observation count attached. The bands are BKT
 * posteriors over the log, the gaps come from the topic graph, and the journey is the
 * loop's own steps read back from the data. Where the app has too little data it says so
 * in the same voice it uses when it has enough — no smoothing, no "keep it up".
 */
import { useState } from "react";

import { MethodReport, type MethodRow } from "@/components/evidence";
import { TopicTopo, TopoNote } from "@/components/topo";
import { Btn, Card, Err, humanize, Notice, Tag } from "@/components/ui";
import { get, post, useApi } from "@/lib/api";
import type { Journey } from "@/lib/types";

type Dashboard = {
  mastery?: {
    topic_id: string;
    title: string;
    p_know: number;
    band?: string;
    observations?: number;
    /** The 90% interval the server computes from the observation count — the chart's width. */
    ci?: [number, number] | null;
    due_at?: string | null;
  }[];
  band_summary?: Record<string, number>;
  weakest?: { topic_id: string; title: string; p_know: number }[];
  knowledge_gaps?: { topic_id: string; topic_title?: string; blocking_prerequisite_title?: string; severity?: number }[];
  hypotheses?: { id: string; statement: string; status: string; confidence?: number; disproof?: string | null }[];
  last_7_days?: { attempts?: number; mean_score?: number; hints_used?: number; mean_seconds_on_question?: number };
  data_volume?: Record<string, number>;
  measured_outcomes?: Record<string, number>;
  guardrails?: Record<string, boolean>;
  notes?: string[];
  open_questions?: { topic_id?: string; question?: string; why?: string }[];
};

export default function Progress() {
  const dash = useApi<Dashboard>("dashboard");
  const journey = useApi<Journey>("journey", { days: 30, limit: 400 });
  const strat = useApi<{ strategies: Record<string, unknown>[]; note?: string }>("strategies");
  const [kind, setKind] = useState<string | null>(null);
  const [training, setTraining] = useState(false);
  const [trainOut, setTrainOut] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const methodReport = useApi<{ methods: MethodRow[]; min_n_for_a_finding?: number }>("methods/report");
  const methods = { rows: methodReport.data?.methods ?? [], minN: methodReport.data?.min_n_for_a_finding ?? 8 };
  const d = dash.data ?? {};
  const nodes = (journey.data?.nodes ?? []).filter((n) => !kind || n.kind === kind);
  const counts = journey.data?.counts ?? {};

  async function retrain() {
    setTraining(true);
    setErr(null);
    try {
      const out = await post<{ updated?: number; note?: string; reason?: string }>("learning/train", {});
      setTrainOut(out.note ?? `${out.updated ?? 0} topic estimates recomputed from the log`);
      dash.reload();
    } catch (e) {
      const x = e as { status?: number; shown?: string; detail?: unknown };
      setErr(x?.status === 409 ? humanize(x.detail ?? x.shown ?? e) : (x?.shown ?? String(e)));
    } finally {
      setTraining(false);
    }
  }

  if (dash.loading) return <Card title="Progress" tone="muted"><p className="text-sm text-[var(--muted)]">reading the log…</p></Card>;
  if (dash.error) return <Err error={dash.error} status={dash.status} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>§ 05 · Progress</div>
          <h1 className="text-xl" style={{ fontFamily: "var(--font-serif)" }}>What the data says</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {journey.data?.window_days ?? 30}-day window · {journey.data?.stats?.attempts ?? 0} answers ·{" "}
            {journey.data?.stats?.events ?? 0} logged interactions ·{" "}
            {journey.data?.stats?.mean_score !== undefined && journey.data?.stats?.mean_score !== null
              ? `mean score ${journey.data.stats.mean_score.toFixed(2)}`
              : "no mean yet"}
          </p>
        </div>
        <div className="flex gap-2">
          <Btn onClick={() => Promise.all([dash.reload(), journey.reload(), methodReport.reload()])}>Reload</Btn>
          <Btn onClick={retrain} disabled={training} title="re-derive the estimates from the event log rather than trusting the cached ones">
            {training ? "…" : "Recompute estimates"}
          </Btn>
        </div>
      </div>

      {err ? <Notice tone="bad">{err}</Notice> : null}
      {trainOut ? <Notice tone="good">{trainOut}</Notice> : null}

      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
        <Card title="Topic estimates" right={<span>{(d.mastery ?? []).length} topics with data</span>}>
          {(d.mastery ?? []).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">
              Nothing measured yet. One placement session is enough to put a number on each topic it touches — with the
              observation count next to it, because one data point is not a level.
            </p>
          ) : (
            <>
              {/* Position is the estimate, width is the uncertainty the server sent, and the
                  circle is the evidence count: three things a coloured band pill flattened into
                  one. The count and its caveat still travel together, in the table underneath. */}
              <TopoNote />
              <TopicTopo rows={d.mastery ?? []} />
            </>
          )}
          {Object.keys(d.band_summary ?? {}).length ? (
            <div className="mt-3 flex flex-wrap gap-1 border-t border-[var(--line)] pt-2 text-xs text-[var(--muted)]">
              {Object.entries(d.band_summary ?? {}).map(([b, n]) => (
                <Tag key={b} tone={b === "acquired" ? "good" : b === "not_started" ? "line" : "think"}>
                  {b}: {n}
                </Tag>
              ))}
            </div>
          ) : null}
        </Card>

        <Card
          title="Which teaching is working for you"
          tone="muted"
          right={<span>actual vs expected, never raw marks</span>}
        >
          <MethodReport rows={methods.rows} minN={methods.minN} />
          <p className="mt-3 text-xs text-[var(--muted)]">
            Each method is compared with what the model expected from you on those very questions, so a method that met harder
            material is not punished for it. Under {methods.minN} observations it is called an early signal, and it stays one until
            there are enough to say otherwise.
          </p>
        </Card>

        <Card title="Blocking prerequisites" tone={d.knowledge_gaps?.length ? "warn" : "muted"} right={<span>from the topic graph</span>}>
          {(d.knowledge_gaps ?? []).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">
              No topic is being asked for while something it depends on is still unlearned. That is a statement about the
              topics the syllabus here covers, not about you.
            </p>
          ) : (
            <ul className="space-y-2 text-sm">
              {(d.knowledge_gaps ?? []).map((g, i) => (
                <li key={i}>
                  <strong>{g.topic_title ?? g.topic_id}</strong> is ahead of <em>{g.blocking_prerequisite_title}</em>
                  {g.severity !== undefined ? <span className="text-xs text-[var(--muted)]"> · gap {g.severity.toFixed(2)}</span> : null}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs text-[var(--muted)]">
            The planner is asked to teach the blocker first. When it does, the change is recorded in the plan&rsquo;s own
            <code> path</code> so you can check whether it actually happened.
          </p>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
        <Card title="Hypotheses about how you learn" tone="ask" right={<span>not diagnoses</span>}>
          {(d.hypotheses ?? []).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">None yet — and none will be invented to fill this box.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {(d.hypotheses ?? []).map((h) => (
                <li key={h.id}>
                  <p>{h.statement}</p>
                  <p className="text-xs text-[var(--muted)]">
                    <Tag tone={h.status === "supported" ? "good" : h.status === "refuted" ? "bad" : "think"}>{h.status}</Tag>{" "}
                    {h.confidence !== undefined ? `posterior ${h.confidence.toFixed(2)} · ` : ""}
                    {h.disproof ? <>would be disproved if: {h.disproof}</> : <>no disproof written for this one, so treat it loosely</>}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Last 7 days" tone="muted">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <Stat label="answers" value={d.last_7_days?.attempts} />
            <Stat label="mean score" value={d.last_7_days?.mean_score} />
            <Stat label="hints opened" value={d.last_7_days?.hints_used} />
            <Stat label="median seconds per question" value={d.last_7_days?.mean_seconds_on_question} />
          </dl>
          <div className="mt-3 flex flex-wrap gap-1 text-xs text-[var(--muted)]">
            {Object.entries(d.data_volume ?? {}).map(([k, v]) => (
              <span key={k}>
                {k.replace(/_/g, " ")} {v}
              </span>
            ))}
          </div>
          <p className="mt-2 text-xs text-[var(--muted)]">
            Time on a question is not a measure of effort and is never scored. It is here because a pattern in it is sometimes
            a sign of what to change, and you should be able to see the same number the proposal was built from.
          </p>
        </Card>
      </div>

      <Card
        title="Your story"
        right={
          <span className="flex flex-wrap items-center gap-1">
            {Object.entries(counts).map(([k, n]) => (
              <button
                key={k}
                type="button"
                onClick={() => setKind(kind === k ? null : k)}
                className={`rounded border px-1.5 text-xs ${kind === k ? "border-[var(--accent)] text-[var(--accent)]" : "border-[var(--line)] text-[var(--muted)]"}`}
              >
                {k} {n}
              </button>
            ))}
          </span>
        }
      >
        {journey.error ? <Err error={journey.error} status={journey.status} /> : null}
        {journey.data?.summary ? <p className="mb-3 text-sm">{journey.data.summary}</p> : null}
        {journey.data?.phases?.length ? (
          <div className="mb-3 flex flex-wrap gap-2 text-xs">
            {journey.data.phases.map((p, i) => (
              <span key={i} className="rounded border border-[var(--line)] px-2 py-1 text-[var(--muted)]" title={String(p.why ?? "")}>
                <span className="text-ink-2">{String(p.phase ?? p.label ?? "?")}</span> {String(p.title ?? "")}
                {p.why ? <span className="opacity-70"> — {String(p.why)}</span> : null}
              </span>
            ))}
          </div>
        ) : null}
        {nodes.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No nodes in this window.</p>
        ) : (
          <ol className="space-y-2">
            {nodes.slice(0, 120).map((n, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="mt-1 w-2 shrink-0">
                  <span
                    className="block h-2 w-2 rounded-full"
                    style={{
                      background:
                        n.tone === "good"
                          ? "var(--good)"
                          : n.tone === "warn"
                            ? "var(--warn)"
                            : n.tone === "bad"
                              ? "var(--bad)"
                              : n.tone === "think"
                                ? "var(--think)"
                                : "var(--line)",
                    }}
                  />
                </span>
                <span className="flex-1">
                  <span className="text-[var(--muted)]">{n.ts ? new Date(n.ts).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</span>{" "}
                  <strong>{n.title}</strong>
                  {n.detail ? <span className="block text-xs text-[var(--muted)]">{n.detail}</span> : null}
                </span>
              </li>
            ))}
          </ol>
        )}
        {journey.data?.milestones?.length ? (
          <div className="mt-3 border-t border-[var(--line)] pt-2 text-xs">
            {journey.data.milestones.map((m: Record<string, unknown>, i: number) => (
              <span key={i} className="mr-3 text-[var(--muted)]">
                {String(m.label ?? m.title ?? m.kind ?? "milestone")}
                {m.at || m.ts ? ` · ${new Date(String(m.at ?? m.ts)).toLocaleDateString()}` : ""}
              </span>
            ))}
          </div>
        ) : null}
      </Card>

      <Card title="Guardrails the server says are on" tone="good" right={<span>{(strat.data?.strategies ?? []).length} strategies in the catalog</span>}>
        <ul className="space-y-1 text-sm">
          {Object.entries(d.guardrails ?? {}).map(([k, v]) => (
            <li key={k}>
              <Tag tone={v ? "good" : "bad"}>{v ? "on" : "off"}</Tag> {k.replace(/_/g, " ")}
            </li>
          ))}
        </ul>
        {d.notes?.length ? (
          <ul className="mt-2 space-y-1 text-xs text-[var(--muted)]">
            {d.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        ) : null}
        <p className="mt-2 text-xs text-[var(--muted)]">{strat.data?.note}</p>
        <div className="mt-2 flex gap-2">
          <Btn href="/proposals">Changes</Btn>
          <Btn
            kind="ghost"
            onClick={async () => {
              const r = await get<{ csv?: string }>("audit", { limit: 50 });
              setTrainOut(r.data?.csv ? "audit rows returned" : "no audit rows for this learner yet");
            }}
          >
            Check the audit log
          </Btn>
        </div>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value?: number | null }) {
  return (
    <div>
      <dt className="text-xs text-[var(--muted)]">{label}</dt>
      <dd className="text-lg font-medium">{value === undefined || value === null ? "—" : typeof value === "number" && value < 1 && value > 0 ? value.toFixed(2) : String(Math.round(Number(value) * 10) / 10)}</dd>
    </div>
  );
}
