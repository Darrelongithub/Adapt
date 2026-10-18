"use client";

/**
 * Home (Round 6, Track C), rebuilt against the CampusCode reference: a hero with
 * the study art at the TOP (visible without scrolling at 1440×900 and 390×844),
 * tinted stat tiles with icon chips, then one tile per subject to continue.
 * Copy is capped at 12 words per card. The topo pattern touches only the empty
 * hero corners — never behind words.
 */

import Link from "next/link";
import { Btn, Card, Err, IconTile, PageHeader } from "./ui";
import { Icon } from "./icons";
import { PageArt } from "./shell/PageArt";
import { useApi, type Me } from "@/lib/api";
import type { Mastery } from "@/lib/types";
import "./shell/dashboard.css";

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

const TONES = ["blue", "sand", "mint", "peach"] as const;

function familyIcon(family: string): "maths" | "language" | "science" | "book" {
  return family === "maths" ? "maths" : family === "languages" ? "language" : family === "sciences" ? "science" : "book";
}

export function HomeDashboard({ me }: { me: Me }) {
  const dash = useApi<Dashboard>("dashboard");
  const reviews = useApi<{ count: number }>("review/due");
  const overview = useApi<LearningOverview>("learning/overview");
  const data = dash.data;
  const subjects = overview.data?.subjects ?? [];
  const activities = data?.learning_activity;
  const active = subjects.find((s) => s.id === overview.data?.active_subject_id) ?? subjects[0] ?? null;
  const name = me.display_name || "learner";

  const primaryHref = active?.next?.href ?? (active ? "/learn" : "/journey?step=subject");
  const primaryLabel = active ? `Continue: ${active.title}` : "Choose a subject";

  return (
    <section data-dashboard>
      {/* ---------- Hero: headline left, study art right, at the TOP ---------- */}
      <div className="home-hero card" data-tone="blue" data-hero="home">
        <div className="home-hero-copy">
          <h1 className="t-display home-hero-title">Hi {name}, ready to learn?</h1>
          <p className="home-hero-line">Pick up where you left off.</p>
          <div className="wrap-2 home-hero-cta">
            <Btn icon="play" kind="primary" href={primaryHref}>{primaryLabel}</Btn>
            <Btn icon="plus" kind="secondary" href="/journey?step=subject">Add a subject</Btn>
          </div>
        </div>
        <div className="home-hero-art" aria-hidden="true">
          <PageArt name="study" />
        </div>
      </div>

      {dash.error || overview.error ? (
        <Card title="Your home could not load" tone="peach">
          {dash.error ? <Err error={dash.error} status={dash.status} /> : <Err error={overview.error} status={overview.status} />}
          <div className="wrap-2" style={{ marginTop: "var(--s2)" }}>
            <Btn icon="refresh" kind="secondary" onClick={() => { dash.reload(); overview.reload(); overview.reload(); }}>Try again</Btn>
          </div>
        </Card>
      ) : null}

      {/* ---------- Stat tiles ---------- */}
      <div className="home-stats" aria-busy={dash.loading || overview.loading || reviews.loading}>
        <StatTile icon="book" tone="blue" value={String(subjects.length)} label="Subjects" />
        <StatTile icon="play" tone="mint" value={overview.data?.active_subject_id ? "1" : "0"} label="Active" />
        <StatTile icon="clock" tone="sand" value={String(overview.data?.due.count ?? reviews.data?.count ?? 0)} label="Due" />
        <StatTile icon="message" tone="peach" value={String(overview.data?.proposals.count ?? data?.pending_proposals.length ?? 0)} label="Changes" />
      </div>

      {/* ---------- Continue learning ---------- */}
      <Card
        title="Continue learning"
        icon={<Icon name="play" />}
        right={<Link href="/journey?step=subject" className="link-inline">Add a subject</Link>}
      >
        {overview.loading ? (
          <p role="status">Loading subjects…</p>
        ) : subjects.length ? (
          <div className="subject-tiles">
            {subjects.map((subject, i) => <SubjectTile key={subject.id} subject={subject} tone={TONES[i % TONES.length]} />)}
          </div>
        ) : (
          <div className="home-empty">
            <IconTile icon={<Icon name="plus" />} tone="blue" size={48} />
            <p>Nothing chosen yet.</p>
            <Btn icon="plus" kind="primary" href="/journey?step=subject">Choose your first subject</Btn>
          </div>
        )}
      </Card>
    </section>
  );
}

function StatTile({ icon, value, label, tone }: { icon: "book" | "play" | "clock" | "message"; value: string; label: string; tone: "blue" | "sand" | "mint" | "peach" }) {
  return (
    <div className="stat-tile" data-tone={tone}>
      <IconTile icon={<Icon name={icon} />} tone={tone} />
      <div>
        <div className="stat-value">{value}</div>
        <div className="stat-label">{label}</div>
      </div>
    </div>
  );
}

function SubjectTile({ subject, tone }: { subject: LearningOverview["subjects"][number]; tone: "blue" | "sand" | "mint" | "peach" }) {
  const total = Math.max(1, subject.progress.total);
  const done = Math.max(0, Math.min(total, subject.progress.done));
  const pct = Math.round((done / total) * 100);
  const href = subject.next?.href ?? "/learn";
  return (
    <article className="subject-tile card" data-tone={tone} data-subject-id={subject.id}>
      <div className="subject-tile-head">
        <IconTile icon={<Icon name={familyIcon(subject.family)} />} tone={tone === "blue" ? "blue" : tone} />
        <ProgressRing percent={pct} />
      </div>
      <h3 className="subject-tile-title">{subject.title}</h3>
      <p className="t-small">{subject.stage === "tests" ? "Starting checks" : subject.stage === "done" ? "Finished" : "Learning"}</p>
      <Btn icon="play" kind="primary" href={href}>Continue: {subject.title}</Btn>
    </article>
  );
}

function ProgressRing({ percent }: { percent: number }) {
  const r = 18;
  const c = 2 * Math.PI * r;
  return (
    <svg width="48" height="48" viewBox="0 0 48 48" aria-label={`${percent} percent of checks complete`} role="img">
      <circle cx="24" cy="24" r={r} fill="none" stroke="var(--line)" strokeWidth="5" />
      <circle
        cx="24" cy="24" r={r} fill="none"
        stroke="var(--accent)" strokeWidth="5" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - percent / 100)}
        transform="rotate(-90 24 24)"
      />
      <text x="24" y="28" textAnchor="middle" fontSize="12" fontWeight="650" fill="var(--ink)">{percent}</text>
    </svg>
  );
}
