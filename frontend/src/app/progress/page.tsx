"use client";

/** Progress, i.e. the part of the system that has to be legible or is not trustworthy.
 *
 * Everything here is an estimate with an observation count attached. The bands are BKT
 * posteriors over the log, the gaps come from the topic graph, and the journey is the
 * loop's own steps read back from the data. Where the app has too little data it says so
 * in the same voice it uses when it has enough — no smoothing, no "keep it up".
 */
import { friendlyLabel } from "@/lib/labels";
import { useEffect, useState } from "react";

import { MethodReport, type MethodRow } from "@/components/evidence";
import { TopicTopo, TopoNote } from "@/components/topo";
import { Btn, Card, Err, humanize, Notice, PageHeader, Tag } from "@/components/ui";
import { PageArt } from "@/components/shell/PageArt";
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

  if (dash.loading) return (
    <div className="space-y-6 page-dense">
      <PageHeader
        title="What the data says"
        subtitle="Your estimates, gaps and story — all derived from what you did."
        art={<PageArt name="knowledge-tree" />}
      />
      <div className="card loading-card" role="status" aria-busy="true" aria-label="Loading progress">
        <div className="loading-card-art" aria-hidden="true"><PageArt name="knowledge-tree" /></div>
        <p className="loading-text">Loading your progress…</p>
        <div className="loading-spinner" aria-hidden="true" />
      </div>
      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <div className="skeleton-card"><div className="skeleton-line large medium" /><div className="skeleton-line full" /><div className="skeleton-line full" /><div className="skeleton-line short" /></div>
        <div className="skeleton-card"><div className="skeleton-line large medium" /><div className="skeleton-line full" /><div className="skeleton-line short" /></div>
        <div className="skeleton-card"><div className="skeleton-line medium" /><div className="skeleton-line full" /><div className="skeleton-line full" /></div>
        <div className="skeleton-card"><div className="skeleton-line medium" /><div className="skeleton-line short" /><div className="skeleton-line full" /></div>
      </div>
    </div>
  );
  if (dash.error) return (
    <div className="space-y-4">
      <PageHeader title="What the data says" subtitle="Your estimates and story." art={<PageArt name="knowledge-tree" />} />
      <Err error={dash.error} status={dash.status} />
    </div>
  );

  return (
    <div className="space-y-6 page-dense">
      <PageHeader
        title="What the data says"
        subtitle={`${journey.data?.window_days ?? 30}-day window · ${journey.data?.stats?.attempts ?? 0} answers · ${journey.data?.stats?.events ?? 0} logged interactions`}
        art={<PageArt name="knowledge-tree" />}
        actions={
          <>
            <Btn kind="secondary" onClick={() => Promise.all([dash.reload(), journey.reload(), methodReport.reload()])}>Reload</Btn>
            <Btn kind="secondary" onClick={retrain} disabled={training} title="derive the estimates again from the event log rather than trusting the cached ones">
              {training ? "…" : "Recompute estimates"}
            </Btn>
          </>
        }
      />

      {/* Round 6: moved here from Home — the one place that reads like a person. */}
      <details className="card" data-tone="plain" id="what-this-is">
        <summary className="t-label" style={{ cursor: "pointer" }}>How the app sees you</summary>
        <p style={{ marginTop: "var(--s2)" }}>
          Estimates, not labels: nothing here is a fixed trait about you, and nothing changes without your answer.
        </p>
      </details>

      <TestEvidenceCard />

      {err ? <Notice tone="bad">{err}</Notice> : null}
      {trainOut ? <Notice tone="good">{trainOut}</Notice> : null}

      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
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
            Each method is compared with what your earlier answers suggested you would get on those very questions, so a method that met harder
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
                  <strong>{friendlyLabel(g.topic_title ?? g.topic_id) ?? "A topic"}</strong> is ahead of <em>{g.blocking_prerequisite_title}</em>
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

      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <Card title="Hypotheses about how you learn" tone="ask" right={<span>not diagnoses</span>}>
          {(d.hypotheses ?? []).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">None yet — and none will be invented to fill this box.</p>
          ) : (
            <ul className="space-y-3 text-sm">
              {(d.hypotheses ?? []).map((h) => (
                <li key={h.id} className="border-b border-[var(--line)] pb-2 last:border-0">
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
                aria-pressed={kind === k}
                className="btn-ctl btn-secondary"
                style={{ padding: "6px 10px", fontSize: 10.5 }}
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
              <span key={i} className="rounded-[2px] border border-[var(--line)] px-2 py-1 text-[var(--muted)]" title={String(p.why ?? "")}>
                <span className="text-ink-2">{String(p.phase ?? p.label ?? "?")}</span> {String(p.title ?? "")}
                {p.why ? <span className="opacity-70"> — {String(p.why)}</span> : null}
              </span>
            ))}
          </div>
        ) : null}
        {nodes.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">No nodes in this window.</p>
        ) : (
          <ol className="space-y-3">
            {nodes.slice(0, 120).map((n, i) => (
              <li key={i} className="flex gap-3 text-sm border-b border-[var(--line)]/50 pb-2 last:border-0">
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
        <ul className="space-y-2 text-sm">
          {Object.entries(d.guardrails ?? {}).map(([k, v]) => (
            <li key={k} className="border-b border-[var(--line)]/50 pb-1.5 last:border-0">
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
        <div className="mt-3 flex gap-2">
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


/** The real-test evidence rows (Round 6, Track B): item, answer, correct, ms. */
type TestEvidenceRow = {
  id: string;
  run_id: string;
  subject_id: string | null;
  item_id: string;
  item_type: string;
  skill: string;
  difficulty: number;
  answer: string | null;
  correct: boolean;
  ms: number;
  hints_used: number;
  observed_at: string | null;
};

function TestEvidenceCard() {
  const [rows, setRows] = useState<TestEvidenceRow[] | null>(null);
  useEffect(() => {
    let alive = true;
    void get<{ rows: TestEvidenceRow[] }>("tests/evidence").then((result) => {
      if (alive) setRows(result.data?.rows ?? []);
    });
    return () => {
      alive = false;
    };
  }, []);
  return (
    <Card title="Test evidence" tone="mint" right={<span>{rows?.length ?? 0} rows</span>}>
      {!rows ? (
        <div className="loading-card" role="status" aria-busy="true" style={{ padding: "var(--s3)" }}>
          <div className="loading-spinner" aria-hidden="true" />
          <p className="loading-text">Reading your evidence…</p>
        </div>
      ) : rows.length === 0 ? (
        <p>No checks yet. Your starting checks will show here.</p>
      ) : (
        <ul className="stack-2">
          {rows.slice(0, 12).map((row) => (
            <li key={row.id} className="wrap-3" style={{ alignItems: "baseline" }}>
              <Tag tone={row.correct ? "good" : "bad"}>{row.correct ? "right" : "wrong"}</Tag>
              <span className="t-small" style={{ color: "var(--body)" }}>
                {row.item_type.replaceAll("_", " ")} · {row.skill.replaceAll(".", " ")} · {row.ms} ms
                {row.hints_used ? ` · ${row.hints_used} hint${row.hints_used === 1 ? "" : "s"}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
