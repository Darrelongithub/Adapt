"use client";

import { Btn, Card, Err, Tag } from "./ui";
import { useApi, type Me } from "@/lib/api";
import type { Mastery } from "@/lib/types";

type Activity = { id: string; title: string; href: string; detail: string; updated_at: string };
type Dashboard = {
  mastery: Mastery[];
  pending_proposals: unknown[];
  last_7_days: { attempts: number; mean_score: number | null };
  data_volume: { attempts: number };
  learning_activity?: { lessons: Activity[]; tests: Activity[] };
};

export function HomeDashboard({ me }: { me: Me }) {
  const dash = useApi<Dashboard>("dashboard");
  const reviews = useApi<{ count: number }>("review/due");
  const data = dash.data;
  const activities = data?.learning_activity;
  const measured = (data?.mastery ?? []).filter(row => row.observations > 0);
  const resume = activities?.lessons[0] ?? activities?.tests[0];
  const primary = resume?.href ?? (me.diagnostic_complete ? "/learn" : "/diagnose");
  const label = activities?.lessons[0] ? "Continue lesson" : activities?.tests[0] ? "Resume assessment" : me.diagnostic_complete ? "Continue practising" : "Start learning";

  return (
    <div className="page-home space-y-6" data-dashboard>
      <header className="dashboard-welcome">
        <p className="home-greet">Welcome back, <strong>{me.display_name || "learner"}</strong>.</p>
        <h1 className="home-title">Pick up where you left off.</h1>
        <p className="mt-3 max-w-xl text-sm text-[var(--muted)]">Saved lessons and what your practice is showing. <a href="#what-this-is" className="link-inline">how the app sees you</a></p>
        <div className="home-cta-row mt-5">
          <Btn icon="play" kind="primary" href={primary}>{label}</Btn>
          <Btn icon="route" href="/journey">Your path</Btn>
          {!me.onboarding_complete ? <Btn icon="sliders" kind="ghost" href="/onboarding">Personalise your setup</Btn> : null}
        </div>
        <details id="what-this-is" className="r3-question-why-tap">
          <summary>how the app sees you</summary>
          <p className="r3-question-why">
            Estimates, not labels: nothing here is a fixed trait about you, and no lasting change is applied
            without you answering a question about it first.
          </p>
        </details>
      </header>

      {dash.error ? <Card title="Your progress could not load"><Err error={dash.error} status={dash.status} /><Btn icon="refresh" onClick={dash.reload}>Retry dashboard</Btn></Card> : null}
      <div className="home-grid" aria-busy={dash.loading || reviews.loading}>
        <Stat label="Recorded practice" value={data ? data.data_volume.attempts ? String(data.data_volume.attempts) : "No answers yet" : "—"} note="Answers saved across your learning sessions." />
        <Stat label="This week" value={data ? String(data.last_7_days.attempts) : "—"} note={data?.last_7_days.mean_score != null ? `${Math.round(data.last_7_days.mean_score * 100)}% average score in the last 7 days.` : "No scored answers this week yet."} />
        <Stat label="Due for review" value={reviews.data ? reviews.data.count ? String(reviews.data.count) : "Nothing due yet" : "—"} note={reviews.error ? "Your review queue could not be read." : "A short revisit helps keep what you have learned."} />
        <Stat label="Changes awaiting you" value={data ? data.pending_proposals.length ? String(data.pending_proposals.length) : "Nothing waiting" : "—"} note="Nothing changes until you answer the proposal." />
      </div>

      <div className="grid gap-5 md:grid-cols-2">
        <ActivityCard title="Continue your lessons" items={activities?.lessons} loading={dash.loading} empty="No unfinished lessons. Choose a lesson to begin." action="Continue lesson" />
        <ActivityCard title="Unfinished assessments" items={activities?.tests} loading={dash.loading} empty="No unfinished checks. Your saved results remain in Progress." action="Resume assessment" />
      </div>
      <Card title="Your learning progress" right={<Btn icon="bars" href="/progress">View all progress</Btn>}>
        {dash.loading ? <p role="status">Loading your progress…</p> : !data ? <p>Your progress is unavailable right now.</p> : measured.length ? (
          <ul className="divide-y divide-[var(--line)]">
            {measured.slice(0, 5).map(topic => <li key={topic.topic_id} className="py-3">
              <div className="flex flex-wrap justify-between gap-2 text-sm"><strong>{topic.title ?? topic.topic_id}</strong><span>{Math.round(topic.p_know * 100)}% estimated knowledge</span></div>
              <progress className="mt-2 w-full accent-[var(--accent)]" max={1} value={topic.p_know} aria-label={`${topic.title ?? "Topic"}: estimated knowledge`} />
              <p className="mt-1 text-xs text-[var(--muted)]">{topic.observations} observations · {topic.observations < 8 ? "early signal" : "measured estimate"}{topic.ci ? ` · range ${Math.round(topic.ci[0] * 100)}–${Math.round(topic.ci[1] * 100)}%` : ""}. An estimate, not a fixed label.</p>
            </li>)}
          </ul>
        ) : <p className="text-sm text-[var(--muted)]">Not measured yet. As you practise, your topic estimates and the evidence behind them will appear here.</p>}
      </Card>
      <div className="flex flex-wrap gap-3"><Btn icon="clock" href="/review">Review what is due</Btn><Btn icon="message" href="/proposals">Review proposed changes</Btn><Tag>Saved to your learner account</Tag></div>
    </div>
  );
}

function ActivityCard({ title, items, loading, empty, action }: { title: string; items?: Activity[]; loading: boolean; empty: string; action: string }) {
  return <Card title={title}>
    {loading ? <p role="status" className="text-sm text-[var(--muted)]">Loading saved activity…</p> : !items ? <p className="text-sm">Saved activity is unavailable right now.</p> : items.length ? (
      <ul className="space-y-4">{items.map(item => <li key={item.id} className="space-y-2">
        <strong className="text-sm">{item.title}</strong>
        <p className="text-xs text-[var(--muted)]">{item.detail}</p>
        <Btn icon="play" href={item.href} ariaLabel={`${action}: ${item.title}`}>{action}</Btn>
      </li>)}</ul>
    ) : <p className="text-sm text-[var(--muted)]">{empty}</p>}
  </Card>;
}
function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="stat-card"><div className="stat-label">{label}</div><div className="stat-value">{value}</div><p className="mt-1 text-xs text-[var(--muted)]">{note}</p></div>;
}
