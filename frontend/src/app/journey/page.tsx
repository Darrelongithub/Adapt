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
 * Steps: subject (big input + chips) → setup (level / style / goal tiles) →
 * the run (4–8 interactive items) → results (bars + two-line summary + lesson).
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
import type { LearningOverview } from "@/components/home-dashboard";
import "@/components/tests/tests.css";

type Chosen = {
  id: string;
  title: string;
  family: string;
  source: "model" | "simple";
  plan: { tests: unknown[]; source: "model" | "simple" };
  progress: { done: number; total: number };
};
type SubjectReply = { subject_id: string; plan: Chosen["plan"]; subject: Chosen };

const SUGGESTIONS = ["Maths", "Discrete structures", "Geometry", "Kiswahili", "Law", "Chemistry", "Coding"];

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
};

export default function Journey() {
  const { me, loading } = useSession();
  const router = useRouter();
  const path = useJourney(Boolean(me));
  const overview = useOverview(Boolean(me));

  const [step, setStep] = useState<"subject" | "setup" | "run" | "results">("subject");
  const [text, setText] = useState("");
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [level, setLevel] = useState<number | null>(null);
  const [style, setStyle] = useState<string | null>(null);
  const [goal, setGoal] = useState<string | null>(null);
  const [items, setItems] = useState<PlanItem[]>([]);
  const [index, setIndex] = useState(0);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const runId = useRef(`run-${Date.now().toString(36)}`);

  const state = path.state;
  const stage = state?.stage ?? "preferences";
  const active = overview.data?.subjects.find((item) => item.id === overview.data?.active_subject_id) ?? null;

  // A learner who arrives with ?step=subject starts at the subject step; otherwise
  // the path stage decides which card is shown (mind check first, then the wizard).
  const [forceSubject, setForceSubject] = useState(false);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("step") === "subject") setForceSubject(true);
  }, []);

  const wizard = forceSubject || stage === "subject" || stage === "subject_round" || Boolean(chosen);

  /* ---------------------------------------------------------- subject step */

  async function choose() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const out = await post<SubjectReply>("subjects/choose", { text: text.trim() });
      setChosen(out.subject);
      setStep("setup");
      await path.reload();
    } catch (error) {
      setNotice((error as { shown?: string }).shown ?? "That subject could not be checked just now.");
    } finally {
      setBusy(false);
    }
  }

  /* ------------------------------------------------------------- setup step */

  const setupReady = level !== null && style !== null && goal !== null;

  async function startTests() {
    if (!chosen || !setupReady || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const plan = await post<{ items: unknown[]; source: "model" | "simple" }>("tests/plan", {
        subject: chosen.title,
        subject_id: chosen.id,
        level,
        style,
        goal,
      });
      const validated = validatePlanItems(plan.items);
      if (validated.length < 4) {
        setNotice("The starting checks could not be built just now. Try again.");
        setBusy(false);
        return;
      }
      runId.current = `run-${Date.now().toString(36)}`;
      setItems(validated);
      setEvidence([]);
      setIndex(0);
      setStep("run");
      await post(`subjects/${chosen.id}/activate`, {});
      await path.advance("subject", "done");
    } catch (error) {
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
      <section>
        <PageHeader title="Your path" subtitle="One step at a time." />
        <p role="status">Opening your path…</p>
      </section>
    );
  }
  if (path.error && !state) {
    return (
      <section>
        <PageHeader title="Your path" subtitle="One step at a time." />
        <Err error={path.error} status={path.status} />
      </section>
    );
  }

  return (
    <section data-journey>
      <PageHeader
        title="Your path"
        subtitle="One step at a time. You can pause or skip any step."
      />
      {notice ? <Notice tone="warn">{notice}</Notice> : null}

      {wizard ? (
        <>
          {step === "subject" ? (
            <>
              {/* The wizard's empty state: classroom art BIG, in the hero, fully visible. */}
              <div className="wizard-hero card" data-tone="mint">
                <div className="wizard-hero-copy">
                  <h2 className="t-display">What do you want to learn?</h2>
                  <p className="t-body">Type a subject. We will build the starting checks.</p>
                </div>
                <div className="wizard-hero-art" aria-hidden="true">
                  <PageArt name="classroom" />
                </div>
              </div>
              <SubjectChooser text={text} setText={setText} choose={choose} busy={busy} />
            </>
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
            <ResultsStep chosen={chosen} evidence={evidence} onAddAnother={() => { setChosen(null); setStep("subject"); setText(""); }} />
          ) : null}
        </>
      ) : (
        <>
          {(stage === "preferences" || stage === "games") && (
            <div className={stage === "games" ? "mind-games-hero" : ""}>
            <Card title={stage === "preferences" ? "A few questions" : "Short mind games"} tone="sand" icon={<Icon name={stage === "games" ? "bulb" : "sliders"} />}>
              <p>{stage === "preferences" ? "Your choices shape the starting plan." : "Play the short games, or skip them."}</p>
              <div className="wrap-2">
                <Btn icon="play" kind="primary" href="/baseline">Open mind check</Btn>
                <Btn kind="ghost" disabled={busy} onClick={async () => { setBusy(true); await path.advance(stage, "skipped"); setBusy(false); }}>Skip this step</Btn>
              </div>
            </Card>
            </div>
          )}
          {stage === "first_lesson" ? (
            <Card title="Your first lesson" tone="mint" icon={<Icon name="play" />}>
              <p>Open the first lesson for your active subject.</p>
              <Btn icon="play" kind="primary" href={active?.next?.href ?? "/first-lesson"}>Open lesson</Btn>
            </Card>
          ) : null}
          {stage === "practice" ? (
            <Card title="Keep learning" tone="mint" icon={<Icon name="book" />}>
              <p>Your path is ready. Continue with a subject lesson.</p>
              <div className="wrap-2">
                <Btn icon="play" kind="primary" href="/learn">Learn</Btn>
                <Btn icon="plus" kind="secondary" href="/journey?step=subject">Add a subject</Btn>
              </div>
            </Card>
          ) : null}
          {stage === "snapshot" ? (
            <Card title="Your mind check is ready" tone="sand" icon={<Icon name="bars" />}>
              <p>See what was measured, then choose a subject.</p>
              <div className="wrap-2">
                <Btn kind="primary" href="/baseline">See the snapshot</Btn>
                <Btn kind="ghost" disabled={busy} onClick={async () => { setBusy(true); await path.advance("snapshot", "skipped"); setBusy(false); }}>Continue</Btn>
              </div>
            </Card>
          ) : null}
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ steps */

function SubjectChooser({ text, setText, choose, busy }: { text: string; setText: (v: string) => void; choose: () => void; busy: boolean }) {
  return (
    <Card title="Your subject" tone="blue" icon={<Icon name="search" />}>
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
          placeholder="Type a subject or topic"
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
        <Btn icon="search" kind="primary" type="submit" disabled={!text.trim() || busy}>
          {busy ? "Checking…" : "See the starting checks"}
        </Btn>
      </form>
    </Card>
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
