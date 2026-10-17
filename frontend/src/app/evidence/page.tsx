"use client";

import { useEffect, useState } from "react";
import { TeachingPanel, type Teaching } from "@/components/teaching";
import { Btn, Card, Err, Field, Input, Notice, Select, Tag } from "@/components/ui";
import { get, post, useApi } from "@/lib/api";

type Method = {
  method: string; label: string; n: number; effective_n: number;
  actual: number; expected: number; residual: number; ci: [number, number]; evidence: string;
  sources: { source: string; weight: number; n: number; effective_n: number }[];
};
type Report = {
  methods: Method[]; note: string; excluded_n?: number; attributed_elsewhere_n?: number;
  strategies: { strategy: string; n: number; p_helps: number; ci: [number, number] }[];
  preference_vs_performance: { stated: string; sentence: string; note: string } | null;
};
type Recovery = {
  loop_id: string; stage: "prerequisite" | "matched_b" | "complete";
  note: string; suggest_break: boolean; method?: string;
  teaching?: Teaching;
  question?: { id: string; kind: string; stem: string; choices?: { text: string }[] };
  outcome?: { cause: string; n: number; interval: [number, number]; residual_difference: number;
    after_correct: boolean; source: string; source_weight: number; multiplier: number; effective_weight: number; evidence: string };
};
const percent = (v: number) => `${(v * 100).toFixed(0)}%`;
const words = (s: string) => s.replaceAll("_", " ");

export default function Evidence() {
  const report = useApi<Report>("evidence");
  const [attempt, setAttempt] = useState("");
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const receipt = new URLSearchParams(window.location.search).get("attempt");
    if (!receipt) return;
    let alive = true;
    setAttempt(receipt); setBusy(true);
    get<Recovery>(`evidence/failure/${encodeURIComponent(receipt)}`).then(response => {
      if (!alive) return;
      if (response.data) setRecovery(response.data);
      else setError(response.error ?? "Could not resume this check");
    }).catch(e => { if (alive) setError(String(e)); })
      .finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, []);

  async function action(resume = false) {
    setBusy(true); setError(null);
    try {
      let result: Recovery;
      if (resume) {
        const response = await get<Recovery>(`evidence/failure/${encodeURIComponent(attempt)}`);
        if (!response.data) throw new Error(response.error ?? "Could not resume this check");
        result = response.data;
      } else {
        result = await post<Recovery>("evidence/failure", { attempt_id: attempt });
      }
      setRecovery(result); setDraft("");
    } catch (e) { setError((e as { shown?: string }).shown ?? String(e)); }
    finally { setBusy(false); }
  }
  async function answer() {
    if (!recovery?.question) return;
    setBusy(true); setError(null);
    try {
      const result = await post<Recovery>(`evidence/failure/${encodeURIComponent(recovery.loop_id)}/answer`, {
        question_id: recovery.question.id,
        answer: recovery.question.kind === "mcq" ? Number(draft) : draft,
      });
      setRecovery(result); setDraft("");
      if (result.stage === "complete") report.reload();
    } catch (e) { setError((e as { shown?: string }).shown ?? String(e)); }
    finally { setBusy(false); }
  }

  return <div className="space-y-4">
    <header>
      <h1 className="t-h1">Preference and performance</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">What you asked for is a preference, not a measurement. Here is what your own answers suggest across all topics.</p>
      <Btn href="/progress">Back to progress</Btn>
    </header>
    {report.loading ? <Notice>Reading your evidence…</Notice> : null}
    {report.error ? <Err error={report.error} status={report.status} /> : null}
    {report.data ? <>
      {report.data.excluded_n ? <Notice>{report.data.excluded_n} incomplete or invalid evidence rows were excluded from these estimates.</Notice> : null}
      {report.data.attributed_elsewhere_n ? <Notice>{report.data.attributed_elsewhere_n} method outcomes were attributed to a prerequisite instead; they did not penalise a teaching method.</Notice> : null}
      <Card title="What you chose, next to what happened">
        <p>{report.data.preference_vs_performance?.sentence ?? "Insufficient evidence to compare your preference with performance. We need at least two methods with eight effective observations each."}</p>
        <p className="mt-2 text-sm text-[var(--muted)]">{report.data.note}</p>
      </Card>
      <div className="grid gap-3 md:grid-cols-2">{report.data.methods.map(m => <Card key={m.method} title={m.label}>
        <Tag tone={m.evidence === "insufficient evidence" ? "warn" : "line"}>{m.evidence}</Tag>
        <p className="mt-2">Actual {percent(m.actual)} · expected {percent(m.expected)}</p>
        <p>Difference {(m.residual * 100).toFixed(0)} points · range {(m.ci[0] * 100).toFixed(0)} to {(m.ci[1] * 100).toFixed(0)}</p>
        <p>n={m.n} · weighted n={m.effective_n}</p>
        {m.sources.map(s => <p key={s.source} className="mt-1 text-sm text-[var(--muted)]">{s.source} · source weight {s.weight} · n={s.n} · weighted n={s.effective_n}</p>)}
      </Card>)}</div>
      <Card title="Strategy estimates">
        {!report.data.strategies.length ? <p>No strategy outcomes yet.</p> : report.data.strategies.map(s => <p key={s.strategy}>
          {words(s.strategy)} · posterior mean {percent(s.p_helps)} · range {percent(s.ci[0])}–{percent(s.ci[1])} · n={s.n}
        </p>)}
        <p className="mt-2 text-sm text-[var(--muted)]">These estimates guide the next suggestion, not a permanent change. Lasting changes still need your approval.</p>
      </Card>
    </> : null}
    <Card title="Understand a missed question">
      <p className="mb-3 text-sm">Check the direct prerequisite, consider tiredness, then try the same idea another way with a matched question. If a checked pair is unavailable, we say so rather than inventing a comparison.</p>
      <Field label="Failed attempt receipt ID"><Input value={attempt} onChange={e => setAttempt(e.target.value)} /></Field>
      <div className="mt-3 flex gap-2"><Btn onClick={() => action()} disabled={busy || !attempt.trim()}>Check this attempt</Btn><Btn onClick={() => action(true)} disabled={busy || !attempt.trim()}>Resume saved check</Btn></div>
    </Card>
    {error ? <Notice tone="bad">{error}</Notice> : null}
    {recovery ? <Card title={recovery.stage === "prerequisite" ? "First, one prerequisite check" : recovery.stage === "matched_b" ? "The same idea, another way" : "What happened"}>
      {recovery.suggest_break ? <Notice>Recent answers slowed down while accuracy fell. You may want a break; this evidence counts at half its usual weight.</Notice> : null}
      {recovery.stage === "matched_b" ? <div className="mb-4">
        <Tag>{words(recovery.method ?? "alternate method")}</Tag>
        <TeachingPanel key={recovery.loop_id} teaching={recovery.teaching ?? null} />
      </div> : null}
      {recovery.question ? <form onSubmit={e => { e.preventDefault(); void answer(); }}>
        <p className="mb-3 whitespace-pre-wrap">{recovery.question.stem}</p>
        <Field label="Your answer">{recovery.question.kind === "mcq"
          ? <Select value={draft} onChange={e => setDraft(e.target.value)}><option value="">Choose an answer</option>{recovery.question.choices?.map((c, i) => <option key={i} value={i}>{c.text}</option>)}</Select>
          : <Input value={draft} onChange={e => setDraft(e.target.value)} />}</Field>
        <Btn type="submit" disabled={busy || !draft.trim()}>{busy ? "Checking…" : "Check answer"}</Btn>
      </form> : null}
      {recovery.outcome ? <div aria-live="polite">
        <p>{recovery.outcome.after_correct ? "The matched question was correct." : "The matched question still needs work."}</p>
        <p>Attribution: {words(recovery.outcome.cause)}. {recovery.outcome.cause === "prerequisite" ? "We attribute the miss to the prerequisite, not the teaching method." : null}</p>
        <p>Adjusted difference {(recovery.outcome.residual_difference * 100).toFixed(0)} points · range {(recovery.outcome.interval[0] * 100).toFixed(0)} to {(recovery.outcome.interval[1] * 100).toFixed(0)} · n={recovery.outcome.n}</p>
        <p>{recovery.outcome.evidence} · {recovery.outcome.source} · source weight {recovery.outcome.source_weight} × attribution {recovery.outcome.multiplier} = {recovery.outcome.effective_weight}</p>
      </div> : null}
      <p className="mt-3 text-sm text-[var(--muted)]">{recovery.note}</p>
    </Card> : null}
  </div>;
}
