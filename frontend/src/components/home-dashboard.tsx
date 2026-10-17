"use client";

import Link from "next/link";
import { Btn, Card, Err, Tag } from "./ui";
import { Icon } from "./icons";
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
export type LearningOverview = {
  active_subject_id: string | null;
  subjects: Array<{ id: string; title: string; family: string; stage: "tests" | "learning" | "done"; progress: { done: number; total: number }; next: { label: string; href: string } | null }>;
  due: { count: number };
  proposals: { count: number };
};

export function HomeDashboard({ me }: { me: Me }) {
  const dash = useApi<Dashboard>("dashboard");
  const reviews = useApi<{ count: number }>("review/due");
  const overview = useApi<LearningOverview>("learning/overview");
  const data = dash.data;
  const subjects = overview.data?.subjects ?? [];
  const activities = data?.learning_activity;
  const measured = (data?.mastery ?? []).filter((row) => row.observations > 0);
  const resume = activities?.lessons[0] ?? activities?.tests[0];
  const primary = overview.data?.active_subject_id ? "/learn" : resume?.href ?? "/journey";
  const label = overview.data?.active_subject_id
    ? "Continue learning"
    : activities?.lessons[0]
      ? "Continue lesson"
      : activities?.tests[0]
        ? "Resume assessment"
        : "Choose what to learn";

  return (
    <section className="dashboard-page" data-dashboard>
      <header className="dashboard-welcome">
        <p className="home-greet">Welcome back, <strong>{me.display_name || "learner"}</strong>.</p>
        <h1 className="home-title">Pick up where you left off.</h1>
        <p className="dashboard-copy">Saved lessons, subject progress, and what your practice is showing.</p>
        <div className="home-cta-row">
          <Btn icon="play" kind="primary" href={primary}>{label}</Btn>
          <Btn icon="plus" href="/journey?step=subject">Add a subject</Btn>
          <Btn icon="route" href="/journey">Your path</Btn>
          {!me.onboarding_complete ? <Btn icon="sliders" kind="ghost" href="/onboarding">Personalise your setup</Btn> : null}
        </div>
        <details id="what-this-is" className="r3-question-why-tap">
          <summary>how the app sees you</summary>
          <p className="r3-question-why">Estimates, not labels: nothing here is a fixed trait about you, and nothing changes without your answer.</p>
        </details>
      </header>

      {dash.error || overview.error ? (
        <Card title="Your progress could not load">
          {dash.error ? <Err error={dash.error} status={dash.status} /> : <Err error={overview.error} status={overview.status} />}
          <Btn icon="refresh" onClick={() => { dash.reload(); overview.reload(); }}>Try again</Btn>
        </Card>
      ) : null}

      <div className="dashboard-stats" aria-busy={dash.loading || overview.loading || reviews.loading}>
        <Stat icon="book" value={String(subjects.length)} label="Subjects" />
        <Stat icon="play" value={overview.data?.active_subject_id ? "1" : "0"} label="Active now" />
        <Stat icon="clock" value={String(overview.data?.due.count ?? reviews.data?.count ?? 0)} label="Due for review" />
        <Stat icon="message" value={String(overview.data?.proposals.count ?? data?.pending_proposals.length ?? 0)} label="Changes waiting" />
      </div>

      <div className="dashboard-columns">
        <Card title="Continue learning" icon={<Icon name="play" />} right={<Link href="/journey?step=subject" className="link-inline">Add a subject</Link>}>
          {overview.loading ? <p role="status">Loading subjects…</p> : subjects.length ? <div className="subject-cards">{subjects.map((subject) => <SubjectCard key={subject.id} subject={subject} />)}</div> : <div className="empty-learning"><p>Choose a subject to get a checked starting plan.</p><Btn icon="plus" kind="primary" href="/journey?step=subject">Choose a subject</Btn></div>}
        </Card>
        <div className="dashboard-side">
          <Card title="Due for review" icon={<Icon name="clock" />}><p className="dashboard-number">{overview.data?.due.count ?? reviews.data?.count ?? 0}</p><p className="text-sm text-[var(--muted)]">Short revisits appear here when something is due.</p><Btn icon="clock" href="/review">Open review</Btn></Card>
          <Card title="Changes awaiting you" icon={<Icon name="message" />}><p className="dashboard-number">{overview.data?.proposals.count ?? data?.pending_proposals.length ?? 0}</p><p className="text-sm text-[var(--muted)]">Nothing changes without your answer.</p><Btn icon="message" href="/proposals">Review changes</Btn></Card>
        </div>
      </div>

      <div className="grid gap-5 md:grid-cols-2">
        <ActivityCard title="Continue your lessons" items={activities?.lessons} loading={dash.loading} empty="No unfinished lessons. Choose a lesson to begin." action="Continue lesson" />
        <ActivityCard title="Unfinished assessments" items={activities?.tests} loading={dash.loading} empty="No unfinished checks. Your saved results remain in Progress." action="Resume assessment" />
      </div>
      <Card title="Your learning progress" right={<Btn icon="bars" href="/progress">View all progress</Btn>}>
        {dash.loading ? <p role="status">Loading your progress…</p> : !data ? <p>Your progress is unavailable right now.</p> : measured.length ? (
          <ul className="divide-y divide-[var(--line)]">
            {measured.slice(0, 5).map((topic) => <li key={topic.topic_id} className="py-3">
              <div className="flex flex-wrap justify-between gap-2 text-sm"><strong>{topic.title ?? topic.topic_id}</strong><span>{Math.round(topic.p_know * 100)}% estimated knowledge</span></div>
              <progress className="mt-2 w-full accent-[var(--accent)]" max={1} value={topic.p_know} aria-label={`${topic.title ?? "Topic"}: estimated knowledge`} />
              <p className="mt-1 text-xs text-[var(--muted)]">{topic.observations} observations · {topic.observations < 8 ? "early signal" : "measured estimate"}{topic.ci ? ` · range ${Math.round(topic.ci[0] * 100)}–${Math.round(topic.ci[1] * 100)}%` : ""}. An estimate, not a fixed label.</p>
            </li>)}
          </ul>
        ) : <p className="text-sm text-[var(--muted)]">Not measured yet. As you practise, your topic estimates and the evidence behind them will appear here.</p>}
      </Card>
      <div className="flex flex-wrap gap-3"><Btn icon="clock" href="/review">Review what is due</Btn><Btn icon="message" href="/proposals">Review proposed changes</Btn><Tag>Saved to your learner account</Tag></div>
      <p className="dashboard-footnote">Your progress is an estimate with evidence behind it, not a fixed label.</p>
    </section>
  );
}

function SubjectCard({ subject }: { subject: LearningOverview["subjects"][number] }) {
  const total = Math.max(1, subject.progress.total);
  const done = Math.max(0, Math.min(total, subject.progress.done));
  const percentage = Math.round(done / total * 100);
  const href = subject.next?.href ?? "/learn";
  return <article className="subject-card" data-subject-id={subject.id}>
    <div className="subject-card-heading"><span className="subject-family"><Icon name={subject.family === "maths" ? "maths" : subject.family === "languages" ? "language" : subject.family === "sciences" ? "science" : "book"} />{subject.family}</span><span className="stage-label">{subject.stage === "tests" ? "Starting checks" : subject.stage === "done" ? "Done" : "Learning"}</span></div>
    <h3>{subject.title}</h3>
    <div className="subject-progress"><span style={{ width: `${percentage}%` }} /></div><p className="progress-copy">{done} of {subject.progress.total} checks complete</p>
    <Btn icon="play" kind="primary" href={href}>Continue: {subject.title}</Btn>
  </article>;
}

function ActivityCard({ title, items, loading, empty, action }: { title: string; items?: Activity[]; loading: boolean; empty: string; action: string }) {
  return <Card title={title}>
    {loading ? <p role="status" className="text-sm text-[var(--muted)]">Loading saved activity…</p> : !items ? <p className="text-sm">Saved activity is unavailable right now.</p> : items.length ? (
      <ul className="space-y-4">{items.map((item) => <li key={item.id} className="space-y-2"><strong className="text-sm">{item.title}</strong><p className="text-xs text-[var(--muted)]">{item.detail}</p><Btn icon="play" href={item.href} ariaLabel={`${action}: ${item.title}`}>{action}</Btn></li>)}</ul>
    ) : <p className="text-sm text-[var(--muted)]">{empty}</p>}
  </Card>;
}

function Stat({ icon, value, label }: { icon: "book" | "play" | "clock" | "message"; value: string; label: string }) {
  return <div className="stat-card"><Icon name={icon} /><div className="stat-value">{value}</div><div className="stat-label">{label}</div></div>;
}
