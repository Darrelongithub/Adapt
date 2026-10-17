"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Btn, Card, Err, Notice, useSession } from "@/components/ui";
import { get, post } from "@/lib/api";
import { useJourney } from "@/lib/journey";
import type { LearningOverview } from "@/components/home-dashboard";

type Chosen = { id: string; title: string; family: string; source: "model" | "simple"; plan: { tests: Test[]; source: "model" | "simple" }; progress: { done: number; total: number } };
type Test = { id: string; title: string; why: string; level: number };
type SubjectReply = { subject_id: string; plan: Chosen["plan"]; subject: Chosen };

const suggestions = ["Mathematics", "Languages", "Sciences", "History", "Computer science", "Law", "Something else"];

export default function Journey() {
  const { me, loading } = useSession();
  const router = useRouter();
  const path = useJourney(Boolean(me));
  const overview = useOverview(Boolean(me));
  const [subjectRequested, setSubjectRequested] = useState(false);
  const [text, setText] = useState("");
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [index, setIndex] = useState(0);
  const [completed, setCompleted] = useState<string[]>([]);
  const [testsStarted, setTestsStarted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const state = path.state;
  const stage = state?.stage ?? "preferences";
  const subjectStage = stage === "subject" || stage === "subject_round";
  const active = overview.data?.subjects.find((item) => item.id === overview.data?.active_subject_id) ?? null;
  const subjectFlow = subjectStage || (subjectRequested && Boolean(active));

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setSubjectRequested(params.get("step") === "subject");
  }, []);

  useEffect(() => {
    if (subjectRequested) {
      setChosen(null);
      setCompleted([]);
      setIndex(0);
      setTestsStarted(false);
      return;
    }
    if (active && subjectStage) {
      void get<Chosen>(`subjects/${active.id}`).then((result) => {
        if (!result.data) return;
        const completedCount = Math.max(0, Math.min(result.data.progress.done, result.data.plan.tests.length));
        setChosen(result.data);
        setCompleted(result.data.plan.tests.slice(0, completedCount).map((item) => item.id));
        setIndex(completedCount);
        setTestsStarted(completedCount > 0);
      });
    }
  }, [active?.id, subjectRequested, subjectStage]);

  const currentTest = chosen?.plan.tests[index] ?? null;
  const markSkip = useCallback(async () => {
    if (!state || busy) return;
    setBusy(true); setNotice(null);
    const out = await path.advance(state.stage, "skipped");
    if (!out.ok) setNotice(out.error ?? "That did not save.");
    setBusy(false);
  }, [busy, path, state]);

  async function choose() {
    if (!text.trim() || busy) return;
    setBusy(true); setNotice(null);
    try {
      const out = await post<SubjectReply>("subjects/choose", { text: text.trim() });
      setChosen(out.subject); setIndex(0); setCompleted([]); setTestsStarted(false);
      await path.reload();
    } catch (error) { setNotice((error as { shown?: string }).shown ?? "That subject could not be checked just now."); }
    finally { setBusy(false); }
  }

  async function startTests() {
    if (!chosen || busy) return;
    setBusy(true); setNotice(null);
    try {
      await post(`subjects/${chosen.id}/activate`, {});
      setTestsStarted(true);
      await path.advance("subject", "done");
    } catch (error) { setNotice((error as { shown?: string }).shown ?? "That did not save."); }
    finally { setBusy(false); }
  }

  async function finishTest() {
    if (!chosen || !currentTest || busy) return;
    const next = Array.from(new Set([...completed, currentTest.id]));
    setCompleted(next);
    if (index + 1 < chosen.plan.tests.length) { setIndex(index + 1); return; }
    setBusy(true); setNotice(null);
    try {
      const out = await post<Chosen>(`subjects/${chosen.id}/tests/complete`, { completed_ids: next });
      setChosen(out);
      await path.advance("subject_round", "done");
      if (subjectRequested) router.push(`/first-lesson?subject=${encodeURIComponent(chosen.id)}`);
    }
    catch (error) { setNotice((error as { shown?: string }).shown ?? "The test result could not be saved."); }
    finally { setBusy(false); }
  }

  if (loading || path.loading || overview.loading) return <section className="journey-page"><p role="status">Opening your path…</p></section>;
  if (path.error && !state) return <section className="journey-page"><Err error={path.error} status={path.status} /></section>;

  return <section className="journey-page" data-journey>
    <header className="journey-header"><p className="eyebrow">Your path</p><h1>One step at a time.</h1><p>Choose a subject after the mind check. You can pause or skip any step.</p><div className="journey-dots" aria-label="Path progress">{state?.stages.map((item) => <span key={item.id} className={item.status === "todo" ? "" : "done"} title={item.id} />)}</div></header>
    {notice ? <Notice tone="warn">{notice}</Notice> : null}
    {stage === "preferences" || stage === "games" ? <BaselineStep stage={stage} onSkip={markSkip} busy={busy} /> : null}
    {subjectFlow && !chosen ? <SubjectChooser text={text} setText={setText} choose={choose} busy={busy} /> : null}
    {subjectFlow && chosen && !testsStarted ? <PlanCard chosen={chosen} onStart={startTests} busy={busy} /> : null}
    {subjectFlow && chosen && testsStarted && currentTest ? <TestCard key={currentTest.id} chosen={chosen} test={currentTest} position={index + 1} onAnswer={finishTest} busy={busy} /> : null}
    {!subjectStage && stage === "first_lesson" ? <Card title="Your first lesson"><p>Open the first lesson for your active subject.</p><Btn icon="play" kind="primary" href={active?.next?.href ?? "/first-lesson"}>Open lesson</Btn></Card> : null}
    {!subjectStage && stage === "practice" ? <Card title="Keep learning"><p>Your path is ready. Continue with a subject lesson or add another subject.</p><div className="flex flex-wrap gap-2"><Btn icon="play" kind="primary" href="/learn">Learn</Btn><Btn icon="plus" href="/journey?step=subject">Add a subject</Btn></div></Card> : null}
    {stage === "snapshot" ? <Card title="Your mind check is ready"><p>See what was measured, then choose a subject.</p><Btn kind="primary" href="/baseline">See the snapshot</Btn><Btn kind="ghost" onClick={markSkip} disabled={busy}>Continue</Btn></Card> : null}
    {stage === "subject" && !chosen ? <p className="journey-skip"><Btn kind="ghost" onClick={markSkip} disabled={busy}>Skip subject choice</Btn></p> : null}
  </section>;
}

function useOverview(enabled: boolean) { const [data, setData] = useState<LearningOverview | null>(null); const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(enabled); useEffect(() => { if (!enabled) return; let alive = true; setLoading(true); void get<LearningOverview>("learning/overview").then((result) => { if (!alive) return; setData(result.data); setError(result.error); setLoading(false); }); return () => { alive = false; }; }, [enabled]); return { data, error, loading }; }

function BaselineStep({ stage, onSkip, busy }: { stage: string; onSkip: () => void; busy: boolean }) { return <Card title={stage === "preferences" ? "A few questions" : "Short mind games"}><p>{stage === "preferences" ? "Answer one question at a time. Your choices shape the starting plan." : "Play the short games, or skip them. Skipped means not measured."}</p><div className="flex flex-wrap gap-2"><Btn icon="play" kind="primary" href="/baseline">Open mind check</Btn><Btn kind="ghost" onClick={onSkip} disabled={busy}>Skip this step</Btn></div></Card>; }

function SubjectChooser({ text, setText, choose, busy }: { text: string; setText: (value: string) => void; choose: () => void; busy: boolean }) { return <Card title="What do you want to learn?"><p className="subject-question-copy">Type a subject or topic. We will choose a short starting check.</p><form onSubmit={(event) => { event.preventDefault(); void choose(); }} className="subject-chooser"><input autoFocus value={text} onChange={(event) => setText(event.target.value)} placeholder="For example, discrete structures" aria-label="Subject or topic" maxLength={240} /><div className="suggestion-list" aria-label="Suggestions">{suggestions.map((item) => <button type="button" key={item} onClick={() => setText(item)}>{item}</button>)}</div><Btn icon="search" kind="primary" type="submit" disabled={!text.trim() || busy}>{busy ? "Checking…" : "See what we will check"}</Btn></form></Card>; }

function PlanCard({ chosen, onStart, busy }: { chosen: Chosen; onStart: () => void; busy: boolean }) { return <Card title={`Here's what we'll check for ${chosen.title}`} tone="muted"><p className="plan-source">{chosen.source === "simple" ? "Simple starting plan" : "Checked starting plan"}</p><ul className="plan-list">{chosen.plan.tests.map((test) => <li key={test.id}><span><strong>{test.title}</strong><small>{test.why}</small></span><span className="test-level">Level {test.level}</span></li>)}</ul><div className="flex flex-wrap gap-2"><Btn icon="play" kind="primary" onClick={onStart} disabled={busy}>Start</Btn><Btn kind="ghost" href="/journey?step=subject">Customise</Btn></div></Card>; }

function TestCard({ chosen, test, position, onAnswer, busy }: { chosen: Chosen; test: Test; position: number; onAnswer: () => void; busy: boolean }) { const [answer, setAnswer] = useState<string | null>(null); const options = useMemo(() => ["I can explain it", "I have seen it", "Not yet"], []); return <Card title={`Subject check ${position} of ${chosen.plan.tests.length}`}><h2 className="text-2xl" style={{ fontFamily: "var(--font-serif)" }}>{test.title}</h2><p className="mt-2 text-sm text-[var(--muted)]">{test.why}</p><div className="r3-choices pill-grid mt-5">{options.map((option) => <button type="button" key={option} className={`choice-card${answer === option ? " is-selected" : ""}`} aria-pressed={answer === option} onClick={() => setAnswer(option)}><span className="choice-title">{option}</span></button>)}</div><Btn icon="play" kind="primary" onClick={onAnswer} disabled={!answer || busy}>{position === chosen.plan.tests.length ? "Finish checks" : "Next check"}</Btn></Card>; }
