"use client";

/**
 * Your path (Round 6, Track B+C+D): the wizard where a learner picks what to
 * learn and proves what they know with REAL tests.
 *
 * The self-rating TestCard ("I can explain it / I have seen it / Not yet") is
 * gone. Every test item now is an interaction scored by code — pattern picker,
 * drag order, odd one out, truth table, Venn region, geometry, number line,
 * fraction bar, cloze, quick recall — and each one writes an evidence row
 * (item, answer, correct, ms, hints) the dashboard and AI 1 can read.
 *
 * Steps: subject (big input + chips, optional curriculum + grade) → what
 * to cover (the scanned syllabus checklist -- untick, reorder, add your
 * own, then "Use this plan"; nothing runs before that tap) → setup
 * (level / style / goal tiles) → the run (4–8 interactive items) →
 * results (bars + two-line summary + lesson), with a 10-second
 * check-in after the run.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Btn, Card, Err, Notice, PageHeader, useSession } from "@/components/ui";
import { Icon } from "@/components/icons";
import { PageArt } from "@/components/shell/PageArt";
import { get, post } from "@/lib/api";
import { useJourney } from "@/lib/journey";
import { TestItemCard } from "@/components/tests/items";
import { validatePlanItems, type ItemResult, type PlanItem } from "@/lib/tests/registry";
import { CheckInCard } from "@/components/check-in";
import {
  CURRICULA,
  moveObjective,
  originLabel,
  type CurriculumId,
  type GuidelineReply,
  type ObjectiveDraft,
  type ScanReply,
} from "@/lib/round9";
import type { LearningOverview } from "@/components/home-dashboard";
import "@/components/tests/tests.css";

type Chosen = {
  id: string;
  title: string;
  family: string;
  source: "model" | "simple";
  plan: { tests: unknown[]; source: "model" | "simple" };
  curriculum_spec?: { curriculum: string | null; grade: string | null } | null;
  progress: { done: number; total: number };
};
type SubjectReply = { subject_id: string; plan: Chosen["plan"]; subject: Chosen };

const SUGGESTIONS = ["Discrete structures", "Kiswahili", "Chemistry", "Biology"];

const LEVELS = [
  { id: 1, label: "Beginner", hint: "Starting fresh" },
  { id: 2, label: "Some knowledge", hint: "I know a bit" },
  { id: 3, label: "Confident", hint: "Test the edges" },
];
const STYLES = [
  { id: "see", label: "See it", hint: "Pictures first" },
  { id: "try", label: "Try it", hint: "Learn by doing" },
  { id: "hear", label: "Hear it", hint: "Spoken explanation" },
  { id: "read", label: "Read it", hint: "Written steps" },
];
const GOALS = [
  { id: "exam", label: "Exam", hint: "Prepare for a test" },
  { id: "skills", label: "Skills", hint: "Use it in life" },
  { id: "curiosity", label: "Curiosity", hint: "For the joy of it" },
];

type EvidenceRow = {
  item_id: string;
  item_type: string;
  skill: string;
  difficulty: number;
  answer: string | null;
  correct: boolean;
  ms: number;
  hints_used: number;
  parameters: Record<string, unknown>;
  grading_seal?: string;
};

type SavedRun = {
  version: 1;
  run_id: string;
  step: "run" | "results";
  chosen: Chosen;
  level: number | null;
  style: string | null;
  goal: string | null;
  items: PlanItem[];
  index: number;
  evidence: EvidenceRow[];
};

export default function Journey() {
  const { me, loading } = useSession();
  const router = useRouter();
  const path = useJourney(Boolean(me));
  const overview = useOverview(Boolean(me));

  const [step, setStep] = useState<"subject" | "syllabus" | "setup" | "run" | "results">("subject");
  const [text, setText] = useState("");
  const [curriculum, setCurriculum] = useState<CurriculumId | null>(null);
  const [grade, setGrade] = useState("");
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [scan, setScan] = useState<ScanReply | null>(null);
  const [objectives, setObjectives] = useState<ObjectiveDraft[]>([]);
  const [ownObjective, setOwnObjective] = useState("");
  const [scanBusy, setScanBusy] = useState(false);
  const [level, setLevel] = useState<number | null>(null);
  const [style, setStyle] = useState<string | null>(null);
  const [goal, setGoal] = useState<string | null>(null);
  const [items, setItems] = useState<PlanItem[]>([]);
  const [index, setIndex] = useState(0);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const runId = useRef(`run-${Date.now().toString(36)}`);
  const restoreAttempted = useRef<string | null>(null);
  const studentId = typeof me?.student_id === "string" ? me.student_id : null;

  const state = path.state;
  const stage = state?.stage ?? "preferences";
  const active = overview.data?.subjects.find((item) => item.id === overview.data?.active_subject_id) ?? null;
  const activeSubjectId = active?.id ?? null;

  // A learner who arrives with ?step=subject starts at the subject step; otherwise
  // the path stage decides which card is shown (mind check first, then the wizard).
  const [forceSubject, setForceSubject] = useState(false);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("step") === "subject") setForceSubject(true);
  }, []);

  useEffect(() => {
    if (!studentId || !activeSubjectId || chosen || items.length) return;
    const restoreKey = `${studentId}:${activeSubjectId}`;
    if (restoreAttempted.current === restoreKey) return;
    restoreAttempted.current = restoreKey;
    let cancelled = false;
    get<SavedRun>("tests/run", { subject_id: activeSubjectId }).then(({ data: saved }) => {
      if (cancelled || !saved || saved.version !== 1 || saved.chosen?.id !== activeSubjectId || !saved.run_id) return;
      const restoredItems = validatePlanItems(saved.items);
      if (restoredItems.length < 4 || restoredItems.length !== saved.items.length) return;
      if (restoredItems.some((item) => item.run_id !== saved.run_id || !item.grading_seal)) return;
      const restoredEvidence: EvidenceRow[] = [];
      for (const [position, row] of (Array.isArray(saved.evidence) ? saved.evidence : []).entries()) {
        if (!row || row.item_id !== restoredItems[position]?.id || typeof row.correct !== "boolean") break;
        restoredEvidence.push(row);
      }
      const savedIndex = Number.isInteger(saved.index) ? saved.index : restoredEvidence.length;
      runId.current = saved.run_id;
      setChosen(saved.chosen);
      setLevel(saved.level);
      setStyle(saved.style);
      setGoal(saved.goal);
      setItems(restoredItems);
      setEvidence(restoredEvidence);
      setIndex(Math.min(restoredItems.length - 1, Math.max(0, savedIndex, restoredEvidence.length)));
      setStep(saved.step === "results" && restoredEvidence.length === restoredItems.length ? "results" : "run");
    }).catch(() => {
      // No saved run (or a temporary read failure) must not block a fresh one.
    });
    return () => { cancelled = true; };
  }, [studentId, activeSubjectId, chosen, items.length]);

  const wizard = forceSubject || stage === "subject" || stage === "subject_round" || Boolean(chosen);

  /* ---------------------------------------------------------- subject step */

  async function choose() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const out = await post<SubjectReply>("subjects/choose", {
        text: text.trim(),
        curriculum,
        grade: grade.trim() || null,
      });
      setChosen(out.subject);
      setStep("syllabus");
      await path.reload();
      await loadScan(out.subject.id, false);
    } catch (error) {
      setNotice((error as { shown?: string }).shown ?? "That subject could not be checked just now.");
    } finally {
      setBusy(false);
    }
  }

  /* ------------------------------------------------- what to cover (gate) */

  async function loadScan(subjectId: string, skipSources: boolean) {
    setScanBusy(true);
    setScan(null);
    try {
      const out = await post<ScanReply>("curriculum/scan", {
        subject_id: subjectId,
        curriculum,
        grade: grade.trim() || null,
        goal,
        skip_sources: skipSources,
      });
      setScan(out);
      setObjectives(out.objectives.map((row) => ({ ...row, keep: true })));
    } catch (error) {
      setNotice((error as { shown?: string }).shown ?? "The syllabus list could not be built just now.");
    } finally {
      setScanBusy(false);
    }
  }

  async function approveSyllabus() {
    if (!chosen || busy) return;
    const kept = objectives.filter((row) => row.keep);
    if (!kept.length) return;
    setBusy(true);
    setNotice(null);
    try {
      await post<GuidelineReply>("curriculum/approve", {
        subject_id: chosen.id,
        objectives: kept.map((row) => ({ id: row.id, title: row.title, strand: row.strand })),
        curriculum,
        grade: grade.trim() || null,
      });
      setStep("setup");
    } catch (error) {
      setNotice((error as { shown?: string }).shown ?? "That plan could not be saved just now.");
    } finally {
      setBusy(false);
    }
  }

  async function keepCurrentPlan() {
    if (!chosen || busy) return;
    // A rescan that differs is only a proposal: saying no clears it and
    // the approved list keeps running untouched.
    if (scan?.proposal_pending) {
      setBusy(true);
      try {
        await post("curriculum/reject-proposal", { subject_id: chosen.id });
      } catch (error) {
        setNotice((error as { shown?: string }).shown ?? "That choice could not be saved just now.");
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    setStep("setup");
  }

  /* ------------------------------------------------------------- setup step */

  const setupReady = level !== null && style !== null && goal !== null;

  async function startTests() {
    if (!chosen || !setupReady || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const plan = await post<{ items: unknown[]; run_id: string; source: "model" | "simple" }>("tests/plan", {
        subject: chosen.title,
        subject_id: chosen.id,
        level,
        style,
        goal,
        curriculum,
        grade: grade.trim() || null,
      });
      const validated = validatePlanItems(plan.items);
      if (validated.length < 4) {
        setNotice("The starting checks could not be built just now. Try again.");
        setBusy(false);
        return;
      }
      runId.current = plan.run_id;
      setItems(validated);
      setEvidence([]);
      setIndex(0);
      setStep("run");
      await post(`subjects/${chosen.id}/activate`, {});
      await path.advance("subject", "done");
    } catch (error) {
      const detail = (error as { detail?: { error?: string } }).detail;
      if (detail?.error === "syllabus_not_approved" && chosen) {
        // The gate, in the learner's face only as a friendly step back:
        // choose what to cover first, then the checks can start.
        setStep("syllabus");
        if (!scan) void loadScan(chosen.id, false);
      }
      setNotice((error as { shown?: string }).shown ?? "The test could not start just now.");
    } finally {
      setBusy(false);
    }
  }

  /* --------------------------------------------------------------- the run */

  const onItemDone = useCallback(
    async (result: ItemResult) => {
      const item = items[index];
      if (!item) return;
      const row: EvidenceRow = {
        item_id: item.id,
        item_type: item.item_type,
        skill: item.skill,
        difficulty: item.difficulty,
        answer: result.answer,
        correct: result.correct,
        ms: result.ms,
        hints_used: result.hints,
        parameters: item.parameters,
        grading_seal: item.grading_seal,
      };
      const nextEvidence = [...evidence, row];
      setEvidence(nextEvidence);
      if (index + 1 < items.length) {
        setIndex(index + 1);
        return;
      }
      // Last item: store the run, seal the subject checks, show the results.
      setBusy(true);
      try {
        await post("tests/evidence", { run_id: runId.current, subject_id: chosen?.id ?? null, rows: nextEvidence });
        if (chosen) {
          await post(`subjects/${chosen.id}/tests/complete`, { completed_ids: items.map((i) => i.id) });
        }
        await path.advance("subject_round", "done");
        setStep("results");
      } catch (error) {
        setNotice((error as { shown?: string }).shown ?? "The test result could not be saved.");
      } finally {
        setBusy(false);
      }
    },
    [items, index, evidence, chosen, path],
  );

  /* ---------------------------------------------------------------- render */

  if (loading || path.loading || overview.loading) {
    return (
      <section className="space-y-6">
        <PageHeader title="Your path" subtitle="One step at a time." art={<PageArt name="stepping-stones" />} />
        <div className="card loading-card" role="status" aria-busy="true" aria-label="Loading your path">
          <div className="loading-spinner" aria-hidden="true" />
          <p className="loading-text">Opening your path…</p>
        </div>
        <div className="skeleton-card" aria-hidden="true">
          <div className="skeleton-line large medium" />
          <div className="skeleton-line full" />
          <div className="skeleton-line short" />
        </div>
      </section>
    );
  }
  if (path.error && !state) {
    return (
      <section className="space-y-4">
        <PageHeader title="Your path" subtitle="One step at a time." art={<PageArt name="stepping-stones" />} />
        <Err error={path.error} status={path.status} />
      </section>
    );
  }

  const existingSubjects = overview.data?.subjects ?? [];

  return (
    <section data-journey className="space-y-6">
      <PageHeader
        title="Your path"
        subtitle="One step at a time. You can pause or skip any step."
        art={<PageArt name="stepping-stones" />}
      />
      {notice ? <Notice tone="warn">{notice}</Notice> : null}

      {wizard ? (
        <>
          {step === "subject" ? (
            <SubjectChooser
              text={text}
              setText={setText}
              curriculum={curriculum}
              setCurriculum={setCurriculum}
              grade={grade}
              setGrade={setGrade}
              choose={choose}
              busy={busy}
              subjects={existingSubjects}
            />
          ) : null}
          {step === "syllabus" && chosen ? (
            <SyllabusStep
              chosen={chosen}
              scan={scan}
              objectives={objectives}
              setObjectives={setObjectives}
              ownObjective={ownObjective}
              setOwnObjective={setOwnObjective}
              scanBusy={scanBusy}
              busy={busy}
              onApprove={() => void approveSyllabus()}
              onKeepCurrent={() => void keepCurrentPlan()}
              onSkipSources={() => void loadScan(chosen.id, true)}
              onBack={() => setStep("subject")}
            />
          ) : null}
          {step === "setup" && chosen ? (
            <SetupStep
              chosen={chosen}
              level={level} setLevel={setLevel}
              style={style} setStyle={setStyle}
              goal={goal} setGoal={setGoal}
              ready={setupReady}
              onStart={startTests}
              busy={busy}
            />
          ) : null}
          {step === "run" && items[index] ? (
            <div className="test-run">
              <div className="test-progress" aria-label="test progress">
                {items.map((_, i) => (
                  <span key={i} className={i < index ? (evidence[i]?.correct ? "done" : "miss") : i === index ? "now" : ""} />
                ))}
              </div>
              <TestItemCard
                key={items[index].id}
                item={items[index]}
                position={index + 1}
                total={items.length}
                onDone={onItemDone}
              />
            </div>
          ) : null}
          {step === "results" && chosen ? (
            <ResultsStep
              chosen={chosen}
              evidence={evidence}
              onAddAnother={() => {
                setChosen(null);
                setScan(null);
                setObjectives([]);
                setItems([]);
                setEvidence([]);
                setIndex(0);
                setStep("subject");
                setText("");
              }}
            />
          ) : null}
        </>
      ) : (
        <div className="stack-4" data-testid="journey-card" data-stage={stage}>
          {(stage === "preferences" || stage === "games") && (
            <div className={stage === "games" ? "mind-games-hero" : ""}>
              <Card title={stage === "preferences" ? "A few questions" : "Short mind games"} tone="sand" icon={<Icon name={stage === "games" ? "bulb" : "sliders"} />}>
                <p>{stage === "preferences" ? "Your choices shape the starting plan." : "Play the short games, or skip them."}</p>
                <div className="wrap-2">
                  <Btn icon="play" kind="primary" href="/baseline">Open mind check</Btn>
                  <Btn kind="secondary" onClick={() => setForceSubject(true)}>Choose a subject</Btn>
                  <Btn kind="ghost" disabled={busy} onClick={async () => { setBusy(true); await path.advance(stage, "skipped"); setBusy(false); }}>Skip this step</Btn>
                </div>
              </Card>
            </div>
          )}
          {stage === "first_lesson" ? (
            <Card title="Your first lesson" tone="mint" icon={<Icon name="play" />}>
              <p>Open the first lesson for your active subject.</p>
              <div className="wrap-2">
                <Btn icon="play" kind="primary" href={active?.next?.href ?? "/first-lesson"}>Open lesson</Btn>
                <Btn kind="secondary" onClick={() => setForceSubject(true)}>Add a subject</Btn>
              </div>
            </Card>
          ) : null}
          {stage === "practice" ? (
            <Card title="Keep learning" tone="mint" icon={<Icon name="book" />}>
              <p>Your path is ready. Continue with a subject lesson.</p>
              <div className="wrap-2">
                <Btn icon="play" kind="primary" href="/learn">Learn</Btn>
                <Btn icon="plus" kind="secondary" onClick={() => setForceSubject(true)}>Add a subject</Btn>
              </div>
            </Card>
          ) : null}
          {stage === "snapshot" ? (
            <Card title="Your mind check is ready" tone="sand" icon={<Icon name="bars" />}>
              <p>See what was measured, then choose a subject.</p>
              <div className="wrap-2">
                <Btn kind="primary" href="/baseline">See the snapshot</Btn>
                <Btn kind="secondary" onClick={() => setForceSubject(true)}>Choose a subject</Btn>
                <Btn kind="ghost" disabled={busy} onClick={async () => { setBusy(true); await path.advance("snapshot", "skipped"); setBusy(false); }}>Continue</Btn>
              </div>
            </Card>
          ) : null}

          <div className="path-steps grid gap-3 sm:grid-cols-3" aria-label="Path overview">
            <div className="card" data-tone="sand" style={{ padding: "var(--s3) var(--s4)" }}>
              <div className="kicker">Step 01</div>
              <h3 className="t-h3" style={{ marginTop: "4px" }}>Mind check</h3>
            </div>
            <div className="card" data-tone="blue" style={{ padding: "var(--s3) var(--s4)" }}>
              <div className="kicker">Step 02</div>
              <h3 className="t-h3" style={{ marginTop: "4px" }}>Choose a subject</h3>
            </div>
            <div className="card" data-tone="mint" style={{ padding: "var(--s3) var(--s4)" }}>
              <div className="kicker">Step 03</div>
              <h3 className="t-h3" style={{ marginTop: "4px" }}>First tailored lesson</h3>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ steps */

function SubjectChooser({
  text,
  setText,
  curriculum,
  setCurriculum,
  grade,
  setGrade,
  choose,
  busy,
  subjects,
}: {
  text: string;
  setText: (v: string) => void;
  curriculum: CurriculumId | null;
  setCurriculum: (v: CurriculumId | null) => void;
  grade: string;
  setGrade: (v: string) => void;
  choose: () => void;
  busy: boolean;
  subjects: LearningOverview["subjects"];
}) {
  return (
    <div className="stack-4">
      <Card title="What do you want to learn?" sub="Pick a subject, then choose what to cover in it." tone="blue" icon={<Icon name="search" />}>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void choose();
          }}
          className="stack-3"
        >
          <input
            autoFocus
            className="input subject-input"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Type a subject or topic (e.g. Algebra, Spanish, Biology)"
            aria-label="What do you want to learn?"
            maxLength={240}
          />
          <div className="wrap-2" aria-label="Suggestions">
            {SUGGESTIONS.map((item) => (
              <button key={item} type="button" className="tap-chip" aria-pressed={text === item} onClick={() => setText(item)}>
                {item}
              </button>
            ))}
          </div>
          <div className="stack-2">
            <p className="t-small" style={{ margin: 0 }}>
              Following a syllabus? <span style={{ color: "var(--muted)" }}>(optional)</span>
            </p>
            <div className="wrap-2" role="group" aria-label="Curriculum">
              {CURRICULA.map((row) => (
                <button
                  key={row.id}
                  type="button"
                  className="tap-chip"
                  aria-pressed={curriculum === row.id}
                  onClick={() => setCurriculum(curriculum === row.id ? null : row.id)}
                >
                  {row.label}
                </button>
              ))}
            </div>
            <input
              className="input"
              value={grade}
              onChange={(event) => setGrade(event.target.value)}
              placeholder="Grade or year (optional), e.g. Grade 8"
              aria-label="Grade or year"
              maxLength={24}
            />
          </div>
          <div className="wrap-2" style={{ paddingTop: "var(--s1)" }}>
            <Btn icon="search" kind="primary" type="submit" disabled={!text.trim() || busy}>
              {busy ? "Checking…" : "Choose what to cover"}
            </Btn>
            <Btn kind="secondary" href="/baseline">
              Open mind check
            </Btn>
          </div>
        </form>
      </Card>

      <div className="path-steps grid gap-3 sm:grid-cols-3" aria-label="Path steps">
        <div className="card" data-tone="sand" style={{ padding: "var(--s3) var(--s4)" }}>
          <div className="kicker">Step 01</div>
          <h3 className="t-h3" style={{ marginTop: "4px" }}>Choose a subject</h3>
        </div>
        <div className="card" data-tone="mint" style={{ padding: "var(--s3) var(--s4)" }}>
          <div className="kicker">Step 02</div>
          <h3 className="t-h3" style={{ marginTop: "4px" }}>Choose what to cover</h3>
        </div>
        <div className="card" data-tone="peach" style={{ padding: "var(--s3) var(--s4)" }}>
          <div className="kicker">Step 03</div>
          <h3 className="t-h3" style={{ marginTop: "4px" }}>Short starting checks</h3>
        </div>
      </div>

      {subjects.length > 0 ? (
        <Card title="Your active subjects" tone="plain" icon={<Icon name="book" />}>
          <div className="wrap-2">
            {subjects.map((s) => (
              <Btn key={s.id} kind="secondary" href={s.next?.href ?? `/learn?subject=${encodeURIComponent(s.id)}`}>
                {s.title}
              </Btn>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function SyllabusStep({
  chosen,
  scan,
  objectives,
  setObjectives,
  ownObjective,
  setOwnObjective,
  scanBusy,
  busy,
  onApprove,
  onKeepCurrent,
  onSkipSources,
  onBack,
}: {
  chosen: Chosen;
  scan: ScanReply | null;
  objectives: ObjectiveDraft[];
  setObjectives: (rows: ObjectiveDraft[]) => void;
  ownObjective: string;
  setOwnObjective: (v: string) => void;
  scanBusy: boolean;
  busy: boolean;
  onApprove: () => void;
  onKeepCurrent: () => void;
  onSkipSources: () => void;
  onBack: () => void;
}) {
  const kept = objectives.filter((row) => row.keep).length;

  function addOwn() {
    const title = ownObjective.trim();
    if (!title) return;
    setObjectives([
      ...objectives,
      { id: `yours-${Date.now().toString(36)}`, title: title.slice(0, 160), strand: "", origin: "yours", keep: true },
    ]);
    setOwnObjective("");
  }

  return (
    <div className="stack-4" data-testid="syllabus-step">
      <Card title={`What to cover in ${chosen.title}`} tone="blue" icon={<Icon name="book" />}>
        <p>Tick what you want to learn. Nothing starts until you tap “Use this plan”.</p>
        {scan ? (
          <div className="stack-2" style={{ marginTop: "var(--s2)" }}>
            <p className="t-h3" style={{ margin: 0 }}>{scan.source_label}</p>
            {scan.attribution ? (
              <p className="t-small" style={{ color: "var(--muted)", margin: 0 }}>
                Source: {scan.attribution}
                {scan.from_cache ? " · shown from a saved copy" : ""}
              </p>
            ) : null}
            {scan.proposal_pending ? (
              <Notice tone="info">A newer list was found. Your current plan stays until you choose this one.</Notice>
            ) : null}
          </div>
        ) : scanBusy ? (
          <p role="status">Looking for the syllabus list…</p>
        ) : (
          <p role="status">The list is not ready yet.</p>
        )}
      </Card>

      <Card title="Your list" tone="sand">
        <div className="stack-3">
          {objectives.map((row, index) => (
            <div key={row.id} className="wrap-2" style={{ alignItems: "center" }}>
              <label className="wrap-2" style={{ alignItems: "center", flex: "1 1 260px" }}>
                <input
                  type="checkbox"
                  checked={row.keep}
                  onChange={(event) =>
                    setObjectives(objectives.map((item) => (item.id === row.id ? { ...item, keep: event.target.checked } : item)))
                  }
                />
                <span>
                  <strong>{row.title}</strong>
                  {row.strand ? <span className="t-small" style={{ color: "var(--muted)" }}> · {row.strand}</span> : null}
                  <span className="t-small" style={{ display: "block", color: "var(--muted)" }}>
                    {originLabel(row.origin)}
                  </span>
                </span>
              </label>
              <button
                type="button"
                className="tap-chip"
                aria-label={`Move ${row.title} up`}
                disabled={index === 0}
                onClick={() => setObjectives(moveObjective(objectives, index, -1))}
              >
                ↑
              </button>
              <button
                type="button"
                className="tap-chip"
                aria-label={`Move ${row.title} down`}
                disabled={index === objectives.length - 1}
                onClick={() => setObjectives(moveObjective(objectives, index, 1))}
              >
                ↓
              </button>
            </div>
          ))}
          <form
            className="wrap-2"
            onSubmit={(event) => {
              event.preventDefault();
              addOwn();
            }}
          >
            <input
              className="input"
              value={ownObjective}
              onChange={(event) => setOwnObjective(event.target.value)}
              placeholder="Add your own, e.g. Draw better diagrams"
              aria-label="Add your own objective"
              maxLength={160}
            />
            <Btn icon="plus" kind="secondary" type="submit" disabled={!ownObjective.trim()}>
              Add
            </Btn>
          </form>
        </div>
      </Card>

      <div className="wrap-2">
        <Btn icon="play" kind="primary" disabled={!kept || busy || scanBusy} onClick={onApprove}>
          {busy ? "Saving…" : "Use this plan"}
        </Btn>
        {scan?.approved_version ? (
          <Btn kind="secondary" onClick={onKeepCurrent}>
            Keep my current plan
          </Btn>
        ) : null}
        {scan?.checked ? (
          <Btn kind="ghost" disabled={busy || scanBusy} onClick={onSkipSources}>
            Skip the syllabus check
          </Btn>
        ) : null}
        <Btn kind="ghost" onClick={onBack}>
          Back
        </Btn>
      </div>
      {!kept ? <p className="t-small">Keep at least one ticked, or add your own.</p> : null}
    </div>
  );
}

function SetupStep({
  chosen, level, setLevel, style, setStyle, goal, setGoal, ready, onStart, busy,
}: {
  chosen: Chosen;
  level: number | null; setLevel: (v: number) => void;
  style: string | null; setStyle: (v: string) => void;
  goal: string | null; setGoal: (v: string) => void;
  ready: boolean; onStart: () => void; busy: boolean;
}) {
  return (
    <div className="stack-4">
      <Card title={`Starting checks for ${chosen.title}`} tone="blue">
        <p>Three taps, then the checks.</p>
      </Card>
      <Card title="How much do you know?" tone="sand">
        <div className="tile-choice" role="group" aria-label="Level">
          {LEVELS.map((l) => (
            <button key={l.id} type="button" className="tile-btn" aria-pressed={level === l.id} onClick={() => setLevel(l.id)}>
              <Icon name={l.id === 1 ? "sprout" : l.id === 2 ? "half" : "mountain"} />
              <span className="tile-btn-label">{l.label}</span>
              <span className="tile-btn-hint">{l.hint}</span>
            </button>
          ))}
        </div>
      </Card>
      <Card title="How do you like to learn?" tone="mint">
        <div className="tile-choice" role="group" aria-label="Learning style">
          {STYLES.map((s) => (
            <button key={s.id} type="button" className="tile-btn" aria-pressed={style === s.id} onClick={() => setStyle(s.id)}>
              <Icon name={s.id === "hear" ? "ear" : s.id === "try" ? "hand" : s.id === "see" ? "eye" : "book"} />
              <span className="tile-btn-label">{s.label}</span>
              <span className="tile-btn-hint">{s.hint}</span>
            </button>
          ))}
        </div>
      </Card>
      <Card title="Why are you learning?" tone="peach">
        <div className="tile-choice" role="group" aria-label="Goal">
          {GOALS.map((g) => (
            <button key={g.id} type="button" className="tile-btn" aria-pressed={goal === g.id} onClick={() => setGoal(g.id)}>
              <Icon name={g.id === "exam" ? "pencil" : g.id === "skills" ? "wrench" : "bulb"} />
              <span className="tile-btn-label">{g.label}</span>
              <span className="tile-btn-hint">{g.hint}</span>
            </button>
          ))}
        </div>
      </Card>
      <div className="wrap-2">
        <Btn icon="play" kind="primary" disabled={!ready || busy} onClick={onStart}>
          {busy ? "Building your checks…" : "Start the checks"}
        </Btn>
      </div>
    </div>
  );
}

function ResultsStep({ chosen, evidence, onAddAnother }: { chosen: Chosen; evidence: EvidenceRow[]; onAddAnother: () => void }) {
  const bySkill = useMemo(() => {
    const map = new Map<string, { done: number; correct: number }>();
    for (const row of evidence) {
      const entry = map.get(row.skill) ?? { done: 0, correct: 0 };
      entry.done += 1;
      if (row.correct) entry.correct += 1;
      map.set(row.skill, entry);
    }
    return [...map.entries()];
  }, [evidence]);
  const total = evidence.length || 1;
  const right = evidence.filter((r) => r.correct).length;
  const summary = `${right} of ${total} checks right.`;
  const weakest = bySkill.find(([, v]) => v.correct < v.done)?.[0];

  return (
    <div className="stack-4">
      <Card title="Your starting picture" tone="blue" icon={<Icon name="bars" />}>
        <p className="t-body">{summary} {weakest ? `Weakest area: ${weakest.replace(".", " ")}.` : "Even across the skills."}</p>
        <div className="results-bars" role="group" aria-label="results by skill">
          {bySkill.map(([skill, v]) => (
            <div key={skill} className="results-bar">
              <span className="t-small">{skill.replace(".", " ")}</span>
              <span className="results-bar-track">
                <span className="results-bar-fill" style={{ width: `${Math.round((v.correct / Math.max(1, v.done)) * 100)}%` }} />
              </span>
              <span className="t-small">
                {v.correct}/{v.done}
              </span>
            </div>
          ))}
        </div>
        <div className="wrap-2" style={{ marginTop: "var(--s3)" }}>
          <Btn icon="play" kind="primary" href={`/first-lesson?subject=${encodeURIComponent(chosen.id)}`}>
            Start lesson
          </Btn>
          <Btn icon="plus" kind="secondary" onClick={onAddAnother}>
            Add a subject
          </Btn>
        </div>
      </Card>
      {/* After a test, ask once -- it shapes the next lesson, nothing else. */}
      <CheckInCard context="after_test" subjectId={chosen.id} autoPrompt title="How did that feel?" />
    </div>
  );
}

function useOverview(enabled: boolean) {
  const [data, setData] = useState<LearningOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setLoading(true);
    void get<LearningOverview>("learning/overview").then((result) => {
      if (!alive) return;
      setData(result.data);
      setError(result.error);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [enabled]);
  return { data, error, loading };
}
