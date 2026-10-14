"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { Btn, Card, Err, Field, Input, Notice, Select, Tag, Textarea } from "@/components/ui";
import {
  chooseFormat,
  finishBaseline,
  getBaseline,
  prettyKey,
  saveBaselineQuestions,
  secondsLabel,
  submitBaselineTrial,
  wagerAllowed,
  type BaselineHistory,
  type BaselineQuestion,
  type BaselineTask,
  type BaselineView,
} from "@/lib/baseline";

const GAMES: Record<string, string> = {
  codewords_study: "Spy codewords · study",
  codewords_cued: "Spy codewords · straight after",
  quick: "Quick taps",
  vault: "Vault lights",
  shopkeeper: "Shopkeeper",
  machine: "Crack the machine",
  locked_box: "The locked box",
  trap: "Two questions to think about",
  codewords_delayed: "Spy codewords · later",
  codewords_next_day: "Next-day codeword check",
};

export default function BaselineRunPage() {
  const params = useParams<{ runId: string }>();
  const runId = decodeURIComponent(params.runId);
  const router = useRouter();
  const [view, setView] = useState<BaselineView | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lockedBoxStartedAt = useRef<number | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const next = await getBaseline(runId);
      if (next.status === "complete") {
        router.replace(`/baseline/snapshot?run_id=${encodeURIComponent(runId)}`);
        return;
      }
      setView(next);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [runId, router]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    if (view?.stage !== "locked_box") lockedBoxStartedAt.current = null;
    else if (view.task?.phase === "measure" && lockedBoxStartedAt.current === null) lockedBoxStartedAt.current = Date.now();
  }, [view?.stage, view?.task?.id, view?.task?.phase]);

  async function updateView(task: (v: BaselineView) => Promise<BaselineView>) {
    if (!view || busy) return;
    setBusy(true);
    setError(null);
    try {
      const next = await task(view);
      setView(next);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function stopHere() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await finishBaseline(runId);
      router.push(`/baseline/snapshot?run_id=${encodeURIComponent(runId)}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  async function submit(response: Record<string, unknown>, latencyMs: number, wager?: number) {
    if (!view?.task || busy) return;
    setBusy(true);
    setError(null);
    try {
      const next = await submitBaselineTrial(runId, {
        task_id: view.task.id,
        response,
        latency_ms: Math.max(0, Math.round(latencyMs)),
        ...(wager === undefined ? {} : { wager }),
      });
      setView(next);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function startPuzzleClock() {
    lockedBoxStartedAt.current = Date.now();
  }

  async function snapshotAfterComplete() {
    if (!busy) await stopHere();
  }

  if (loading) return <Card title="Opening your saved check-in" tone="muted"><p className="text-sm text-[var(--muted)]">…</p></Card>;
  if (!view) {
    return (
      <div className="mx-auto max-w-2xl space-y-3">
        <Err error={error ?? "This saved baseline could not be opened."} status={null} />
        <div className="flex gap-2"><Btn onClick={() => { setLoading(true); void reload(); }}>Try again</Btn><Btn href="/baseline">Mind check home</Btn></div>
      </div>
    );
  }

  const title = view.stage_title || "Mind check";
  const task = view.task;
  const hasSavedAnswers = view.history.length > 0 || Boolean(view.format_pick) || Boolean(view.answers && Object.keys(view.answers).length);

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <header className="flex flex-wrap items-center gap-3 border-b border-[var(--line)] pb-3">
        <div className="min-w-[12rem] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Tag tone="accent">Mind check</Tag>
            <span className="text-xs text-[var(--muted)]">saved as you go</span>
          </div>
          <h1 className="mt-2 text-2xl font-medium" style={{ fontFamily: "var(--font-serif)" }}>{title}</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {view.kind === "snapshot" ? (
            <Btn kind="primary" onClick={snapshotAfterComplete} disabled={busy}>{busy ? "Saving…" : "See my snapshot"}</Btn>
          ) : (
            <Btn kind="ghost" onClick={stopHere} disabled={busy}>{busy ? "Saving…" : "Stop here and see my snapshot"}</Btn>
          )}
        </div>
      </header>

      <div aria-label={`Baseline progress ${Math.round(view.progress * 100)} percent`} className="h-1.5 overflow-hidden rounded bg-[var(--line)]">
        <div className="h-full bg-[var(--accent)] transition-all" style={{ width: `${Math.max(2, Math.min(100, view.progress * 100))}%` }} />
      </div>
      {hasSavedAnswers ? (
        <p className="text-xs text-[var(--muted)]" role="status">Your earlier answers are saved. This screen continues from {prettyKey(view.stage)}.</p>
      ) : null}
      {error ? <Notice tone="bad">{error}</Notice> : null}

      {view.kind === "format" ? (
        <FormatStep view={view} busy={busy} onChoose={(value) => updateView(() => chooseFormat(runId, value))} />
      ) : null}
      {view.kind === "questions" ? (
        <QuestionsStep view={view} busy={busy} onSave={(answers) => updateView(() => saveBaselineQuestions(runId, answers))} />
      ) : null}
      {view.kind === "task" && task ? (
        <TaskScreen
          key={task.id}
          view={view}
          task={task}
          busy={busy}
          lockedBoxStartedAt={lockedBoxStartedAt}
          onStartPuzzle={startPuzzleClock}
          onSubmit={submit}
        />
      ) : null}
      {view.kind === "snapshot" ? (
        <Card title="All stages have an ending" tone="good">
          <p className="text-sm leading-relaxed">You have reached the end of the games. Your snapshot will keep the measurements above the “not measured yet” parts that need more answers.</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Btn kind="primary" onClick={snapshotAfterComplete} disabled={busy}>{busy ? "Saving…" : "Save and view snapshot"}</Btn>
            <Btn kind="ghost" onClick={stopHere} disabled={busy}>Stop and view snapshot</Btn>
          </div>
        </Card>
      ) : null}
      {!view.kind || view.kind === "done" ? <Notice tone="info">This check-in is saved. Open the snapshot or return to the Mind check home.</Notice> : null}
      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--line)] pt-3 text-xs text-[var(--muted)]">
        <span>Not a grade. No answer is compared with another learner.</span>
        <Btn href="/baseline">Leave this screen</Btn>
      </footer>
    </div>
  );
}

function FormatStep({ view, busy, onChoose }: { view: BaselineView; busy: boolean; onChoose: (value: string) => void }) {
  const [selected, setSelected] = useState(view.format_pick ?? "");
  return (
    <Card title="Before the games" tone="ask">
      <p className="text-base font-medium">For a brand-new topic, which would you choose first?</p>
      <p className="mt-1 text-sm text-[var(--muted)]">This is a stated preference, not a measurement. It can shape the first look at a new topic; what works is checked separately against your answers.</p>
      <fieldset className="mt-4 grid gap-3 sm:grid-cols-3">
        <legend className="sr-only">Choose a way to meet a new topic</legend>
        {view.format_options.map((option) => (
          <label key={option.value} className={`cursor-pointer rounded-[2px] border p-4 ${selected === option.value ? "border-[var(--accent)] bg-[var(--accent)]/5" : "border-[var(--line)] bg-[var(--panel-2)]"}`}>
            <span className="flex items-start gap-2">
              <input type="radio" name="baseline-format" value={option.value} checked={selected === option.value} onChange={() => setSelected(option.value)} />
              <span><strong>{option.label}</strong><span className="mt-1 block text-xs text-[var(--muted)]">{option.hint}</span></span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="mt-4 flex flex-wrap gap-2">
        <Btn kind="primary" onClick={() => onChoose(selected)} disabled={!selected || busy}>{busy ? "Saving…" : "Continue"}</Btn>
      </div>
    </Card>
  );
}

function QuestionsStep({ view, busy, onSave }: { view: BaselineView; busy: boolean; onSave: (answers: Record<string, unknown>) => void }) {
  const [answers, setAnswers] = useState<Record<string, unknown>>(() => ({ ...view.answers }));
  const set = (key: string, value: unknown) => setAnswers((old) => ({ ...old, [key]: value }));
  return (
    <Card title="A few questions about what suits you" tone="ask">
      <p className="mb-4 text-sm text-[var(--muted)]">Answer any you want to. These are things you tell us, not measurements; blanks stay blank.</p>
      <div className="space-y-4">
        {view.questions.map((question) => <PreferenceQuestion key={question.key} question={question} value={answers[question.key]} onChange={(value) => set(question.key, value)} />)}
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <Btn kind="primary" onClick={() => onSave(answers)} disabled={busy}>{busy ? "Saving…" : "Save answers and start"}</Btn>
        <Btn kind="ghost" onClick={() => onSave({})} disabled={busy}>Skip these questions</Btn>
      </div>
    </Card>
  );
}

function PreferenceQuestion({ question, value, onChange }: { question: BaselineQuestion; value: unknown; onChange: (value: unknown) => void }) {
  if (question.free_text) {
    return <Field label={question.question} hint="Optional. Up to one short line."><Input value={String(value ?? "")} maxLength={280} onChange={(e) => onChange(e.target.value)} /></Field>;
  }
  if (question.multi) {
    const picked = Array.isArray(value) ? value.map(String) : [];
    return (
      <fieldset className="rounded-[2px] border border-[var(--line)] p-3">
        <legend className="px-1 text-sm font-medium">{question.question}</legend>
        <div className="mt-1 grid gap-2 sm:grid-cols-2">
          {question.options.map((option) => {
            const item = String(option);
            return <label key={item} className="flex items-start gap-2 text-sm"><input type="checkbox" checked={picked.includes(item)} onChange={(e) => onChange(e.target.checked ? [...picked, item] : picked.filter((x) => x !== item))} /><span>{item}</span></label>;
          })}
        </div>
      </fieldset>
    );
  }
  return (
    <Field label={question.question}>
      <Select value={value === undefined || value === null ? "" : String(value)} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose an answer (optional)</option>
        {question.options.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
      </Select>
    </Field>
  );
}

function TaskScreen({
  view,
  task,
  busy,
  lockedBoxStartedAt,
  onStartPuzzle,
  onSubmit,
}: {
  view: BaselineView;
  task: BaselineTask;
  busy: boolean;
  lockedBoxStartedAt: { current: number | null };
  onStartPuzzle: () => void;
  onSubmit: (response: Record<string, unknown>, latency: number, wager?: number) => void;
}) {
  const [text, setText] = useState("");
  const [choice, setChoice] = useState("");
  const [wager, setWager] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [studyLeft, setStudyLeft] = useState(Math.ceil((task.study_ms ?? 0) / 1000));
  const [flashCell, setFlashCell] = useState<number | null>(null);
  const [vaultMode, setVaultMode] = useState<"showing" | "input">("showing");
  const [vaultTaps, setVaultTaps] = useState<number[]>([]);
  const [cursor, setCursor] = useState(0);
  const tileRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const taskStartedAt = useRef(Date.now());
  const [machineMode, setMachineMode] = useState<"probe" | "guess">("probe");
  const [shopMode, setShopMode] = useState<"remember" | "questions" | "recall">("remember");
  const [distractorIndex, setDistractorIndex] = useState(0);
  const [distractorAnswers, setDistractorAnswers] = useState<Record<string, boolean>>({});
  const [rememberedItems, setRememberedItems] = useState("");

  useEffect(() => {
    const interval = window.setInterval(() => setElapsed(Math.floor((Date.now() - taskStartedAt.current) / 1000)), 250);
    return () => window.clearInterval(interval);
  }, [task.id]);

  useEffect(() => {
    if (task.game !== "codewords_study") return;
    const duration = Math.ceil((task.study_ms ?? 45_000) / 1000);
    const began = Date.now();
    const interval = window.setInterval(() => {
      setStudyLeft(Math.max(0, duration - Math.floor((Date.now() - began) / 1000)));
    }, 250);
    return () => window.clearInterval(interval);
  }, [task.id, task.game, task.study_ms]);

  useEffect(() => {
    if (task.game !== "vault") return;
    const seq = task.sequence ?? [];
    let cancelled = false;
    const timers: number[] = [];
    setVaultMode("showing");
    setFlashCell(null);
    let i = 0;
    const showNext = () => {
      if (cancelled) return;
      if (i >= seq.length) {
        setFlashCell(null);
        setVaultMode("input");
        return;
      }
      setFlashCell(seq[i]);
      const current = i;
      timers.push(window.setTimeout(() => {
        setFlashCell(null);
        i = current + 1;
        timers.push(window.setTimeout(showNext, 260));
      }, 440));
    };
    timers.push(window.setTimeout(showNext, 350));
    return () => {
      cancelled = true;
      timers.forEach((id) => window.clearTimeout(id));
    };
  }, [task.id, task.game, task.sequence]);

  const latest = view.history.at(-1);
  const feedback = latest && latest.game === task.game && latest.task_id !== task.id ? latest : null;
  const actionForWager = task.game === "machine" ? machineMode : task.action;
  const wagerVisible = view.timers_visible && wagerAllowed(task, actionForWager);
  const title = GAMES[task.game] ?? "Baseline game";
  const send = (response: Record<string, unknown>, action?: string) => {
    const elapsedMs = task.game === "locked_box" && task.phase === "measure"
      ? Date.now() - (lockedBoxStartedAt.current ?? Date.now())
      : Date.now() - taskStartedAt.current;
    const confidence = view.timers_visible && wagerAllowed(task, action ?? String(response.action ?? task.action ?? ""))
      ? (wager ?? undefined)
      : undefined;
    onSubmit(response, Math.max(0, elapsedMs), confidence);
  };
  function answerDistractor(value: boolean) {
    const distractor = task.distractors?.[distractorIndex];
    if (!distractor) return;
    const updated = { ...distractorAnswers, [distractor.id]: value };
    setDistractorAnswers(updated);
    const count = task.distractors?.length ?? 0;
    if (distractorIndex + 1 < count) setDistractorIndex((i) => i + 1);
    else setShopMode("recall");
  }

  return (
    <div className="space-y-4">
      <Card title={title} right={<span>{task.phase === "practice" ? "practice · not measured" : task.phase === "show" ? "study" : "question"}</span>}>
        {task.story ? <p className="mb-2 text-sm leading-relaxed">{task.story}</p> : null}
        {task.prompt ? <p className="text-base font-medium leading-relaxed">{task.prompt}</p> : null}
        {task.phase === "practice" ? <p className="mt-2 text-xs text-[var(--muted)]">This practice round does not count in your snapshot.</p> : null}
        {view.timers_visible && task.game !== "codewords_study" ? <p className="mt-2 text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>Time so far: {secondsLabel(elapsed)}</p> : null}
      </Card>

      {feedback ? <Feedback game={task.game} row={feedback} /> : null}

      {task.game === "codewords_study" ? (
        <Card title="Learn these pairs" tone="ask">
          <p className="mb-3 text-sm text-[var(--muted)]">Read through each made-up code word and its place. The study window is 45 seconds.</p>
          <dl className="grid gap-2 sm:grid-cols-2">
            {(task.pairs ?? []).map((pair) => <div key={pair.code} className="flex items-center justify-between gap-3 rounded-[2px] border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2"><dt className="font-medium">{pair.code}</dt><dd className="text-right text-sm text-[var(--muted)]">{pair.place}</dd></div>)}
          </dl>
          {view.timers_visible ? <p className="mt-3 text-sm" role="timer" aria-live="off">Study time remaining: <strong>{secondsLabel(studyLeft)}</strong></p> : <p className="mt-3 text-sm text-[var(--muted)]" role="status">The study window is underway. The pairs remain here for 45 seconds.</p>}
          <div className="mt-4"><Btn kind="primary" onClick={() => send({ seconds_seen: Math.ceil((task.study_ms ?? 45_000) / 1000) })} disabled={busy || studyLeft > 0}>{busy ? "Saving…" : studyLeft > 0 ? "Ready" : "Ready — continue"}</Btn></div>
        </Card>
      ) : null}

      {task.game === "codewords_cued" ? (
        <Card title="Your answer">
          <Field label={task.phase === "practice" ? "Try a place name" : "Type your answer"}>
            <Input autoComplete="off" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) send({ text }); }} />
          </Field>
          <WagerControl visible={wagerVisible} value={wager} onChange={setWager} />
          <div className="mt-4"><Btn kind="primary" onClick={() => send({ text })} disabled={busy || !text.trim()}>{busy ? "Checking…" : "Check answer"}</Btn></div>
        </Card>
      ) : null}

      {task.game === "quick" ? (
        <Card title="Choose one">
          <ChoiceList name={`quick-${task.id}`} options={task.options ?? []} value={choice} onChange={setChoice} />
          <WagerControl visible={wagerVisible} value={wager} onChange={setWager} />
          <div className="mt-4"><Btn kind="primary" onClick={() => send({ value: choice })} disabled={busy || !choice}>{busy ? "Checking…" : "Choose"}</Btn></div>
        </Card>
      ) : null}

      {task.game === "trap" ? (
        <Card title={task.phase === "practice" ? "Try one first" : "Take a moment, then choose"}>
          <ChoiceList name={`trap-${task.id}`} options={task.options ?? []} value={choice} onChange={setChoice} />
          <WagerControl visible={wagerVisible} value={wager} onChange={setWager} />
          <div className="mt-4"><Btn kind="primary" onClick={() => send({ value: choice })} disabled={busy || !choice}>{busy ? "Checking…" : task.phase === "practice" ? "Try it" : "Submit answer"}</Btn></div>
        </Card>
      ) : null}

      {task.game === "vault" ? (
        <Card title="3 × 3 vault grid" tone="ask">
          <p className="text-sm text-[var(--muted)]" aria-live="polite">{vaultMode === "showing" ? "Watch the tiles light up. The grid will open for your answer next." : `Tap ${task.length ?? 0} tiles in ${task.answer_direction === "backward" ? "reverse" : "the same"} order. You have tapped ${vaultTaps.length}.`}</p>
          <div className="mx-auto mt-4 grid max-w-[18rem] grid-cols-3 gap-2" role="group" aria-label="Nine-tile vault. Use arrow keys to move and Enter to tap a tile.">
            {Array.from({ length: 9 }, (_, index) => (
              <button
                key={index}
                ref={(node) => { tileRefs.current[index] = node; }}
                type="button"
                tabIndex={cursor === index ? 0 : -1}
                aria-label={`Tile ${index + 1}${flashCell === index ? ", lighting up" : vaultTaps.includes(index) ? ", tapped" : ""}`}
                aria-pressed={vaultTaps.includes(index)}
                disabled={busy || vaultMode !== "input"}
                onFocus={() => setCursor(index)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    if (vaultMode !== "input") return;
                    event.preventDefault();
                    setVaultTaps((old) => [...old, index]);
                  } else {
                    moveVaultCursor(event, index, setCursor, tileRefs);
                  }
                }}
                onClick={() => {
                  if (vaultMode !== "input") return;
                  setCursor(index);
                  setVaultTaps((old) => [...old, index]);
                }}
                className={`aspect-square rounded-[2px] border text-lg font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${flashCell === index ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--paper)]" : vaultTaps.includes(index) ? "border-[var(--good)] bg-[var(--good)]/15" : "border-[var(--line)] bg-[var(--panel-2)]"}`}
              >{index + 1}</button>
            ))}
          </div>
          <p className="mt-2 text-center text-xs text-[var(--muted)]">Arrow keys move · Enter or Space taps · Tab enters and leaves the grid</p>
          {vaultMode === "input" ? <WagerControl visible={wagerVisible} value={wager} onChange={setWager} /> : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <Btn kind="primary" onClick={() => send({ taps: vaultTaps })} disabled={busy || vaultMode !== "input" || vaultTaps.length === 0}>{busy ? "Checking…" : "Check sequence"}</Btn>
            <Btn onClick={() => setVaultTaps([])} disabled={busy || vaultMode !== "input" || vaultTaps.length === 0}>Clear taps</Btn>
          </div>
        </Card>
      ) : null}

      {task.game === "shopkeeper" ? (
        <Card title="Shopkeeper · hold and recall" tone="ask">
          {shopMode === "remember" ? (
            <>
              <p className="mb-3 text-sm">Remember these three items. They will be hidden while you answer the quick questions.</p>
              <ul className="grid gap-2 sm:grid-cols-3">{(task.items ?? []).map((item) => <li key={item} className="rounded-[2px] border border-[var(--line)] bg-[var(--panel-2)] p-3 text-sm">{item}</li>)}</ul>
              <div className="mt-4"><Btn kind="primary" onClick={() => setShopMode("questions")}>I have them — start questions</Btn></div>
            </>
          ) : shopMode === "questions" ? (
            <>
              <p className="text-sm text-[var(--muted)]">Question {Math.min(distractorIndex + 1, task.distractors?.length ?? 0)} of {task.distractors?.length ?? 0}</p>
              <p className="mt-2 text-base font-medium">{task.distractors?.[distractorIndex]?.text ?? ""}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                <Btn kind="primary" onClick={() => answerDistractor(true)}>True</Btn>
                <Btn onClick={() => answerDistractor(false)}>False</Btn>
              </div>
            </>
          ) : (
            <>
              <p className="mb-2 text-sm">Type the three items you remember, separated by commas. Partial recall is okay.</p>
              <Field label="Items you remember"><Textarea value={rememberedItems} onChange={(e) => setRememberedItems(e.target.value)} /></Field>
              <WagerControl visible={wagerVisible} value={wager} onChange={setWager} />
              <div className="mt-4"><Btn kind="primary" onClick={() => send({ items: rememberedItems.split(/[,;\n]+/).map((x) => x.trim()).filter(Boolean), distractors: distractorAnswers })} disabled={busy}>{busy ? "Checking…" : "Check recall"}</Btn></div>
            </>
          )}
        </Card>
      ) : null}

      {task.game === "machine" ? (
        <div className="space-y-3">
          {task.phase === "practice" ? <Notice tone="info">Try an input to see what the machine does. This demonstration does not count as a measured trial.</Notice> : null}
          <Card title="Machine history">
            <ol className="space-y-2" aria-live="polite">
              {view.history.filter((row) => row.game === "machine" && row.action === "probe" && typeof row.input === "number").map((row) => <li key={row.task_id} className="flex items-center justify-between gap-3 border-b border-[var(--line)] py-1.5 text-sm"><span>Input <strong>{row.input}</strong></span><span>Output <strong>{row.output === undefined ? "not available" : String(row.output)}</strong></span></li>)}
              {!view.history.some((row) => row.game === "machine" && row.action === "probe") ? <li className="text-sm text-[var(--muted)]">No test inputs yet.</li> : null}
            </ol>
            <p className="mt-3 text-xs text-[var(--muted)]">{task.phase === "practice" ? "Practice input" : `${task.probes_used ?? 0} of ${task.max_probes ?? 5} test inputs used · ${task.wrong_guesses ?? 0} of ${task.max_wrong_guesses ?? 2} wrong guesses`}</p>
          </Card>
          <Card title={machineMode === "probe" ? "Test an input" : "Guess the rule"} tone="ask">
            {machineMode === "probe" ? (
              <>
                <Field label="Number to test"><Input type="number" step="any" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && text !== "") send({ action: "probe", value: Number(text) }, "probe"); }} /></Field>
                <div className="mt-4 flex flex-wrap gap-2"><Btn kind="primary" onClick={() => send({ action: "probe", value: Number(text) }, "probe")} disabled={busy || text.trim() === ""}>{busy ? "Testing…" : "Test input"}</Btn><Btn onClick={() => { setMachineMode("guess"); setText(""); }}>Guess instead</Btn></div>
              </>
            ) : (
              <>
                <Field label="What rule do you think the machine is using?"><Input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) send({ action: "guess", text }, "guess"); }} /></Field>
                <WagerControl visible={wagerVisible} value={wager} onChange={setWager} />
                <div className="mt-4 flex flex-wrap gap-2"><Btn kind="primary" onClick={() => send({ action: "guess", text }, "guess")} disabled={busy || !text.trim()}>{busy ? "Checking…" : "Submit rule guess"}</Btn><Btn onClick={() => { setMachineMode("probe"); setText(""); }}>Test another input</Btn></div>
              </>
            )}
          </Card>
        </div>
      ) : null}

      {task.game === "locked_box" ? (
        task.phase === "practice" ? (
          <Card title="Before the puzzle" tone="ask">
            <p className="text-sm leading-relaxed">This one is hard on purpose. You can ask for hints, try an answer, or walk away; nothing bad happens if you stop.</p>
            <div className="mt-4 flex flex-wrap gap-2"><Btn kind="primary" onClick={async () => { await onStartPuzzle(); send({ action: "start" }); }}>Start the puzzle</Btn><Btn kind="ghost" onClick={() => send({ action: "walk_away" })}>Walk away</Btn></div>
          </Card>
        ) : (
          <div className="space-y-3">
            <Card title="The sequence so far" tone="ask">
              <ol className="flex flex-wrap items-center gap-2 text-lg font-medium" aria-label="Known values in the sequence">
                {(task.cases ?? []).map((row) => <li key={row.n} className="rounded-[2px] border border-[var(--line)] px-3 py-2">{row.value}</li>)}
                <li aria-label="The missing fifth value" className="rounded-[2px] border border-dashed border-[var(--line)] px-3 py-2">?</li>
              </ol>
            </Card>
            <Card title="Hint shop">
              <p className="mb-3 text-xs text-[var(--muted)]">Each hint has a recorded coin cost. Use each hint at most once.</p>
              <div className="grid gap-2 sm:grid-cols-3">
                {(task.hint_shop ?? []).map((hint) => {
                  const used = view.history.some((row) => row.game === "locked_box" && row.action === "hint" && row.hint_id === hint.id);
                  return <button key={hint.id} type="button" disabled={busy || used} onClick={() => send({ action: "hint", hint_id: hint.id })} className="btn-ctl btn-secondary p-3 text-left disabled:opacity-50"><span className="block font-medium">{used ? "Used" : `Get ${hint.level ?? "hint"}`}</span><span className="mt-1 block text-xs">{hint.cost} coin{hint.cost === 1 ? "" : "s"}</span></button>;
                })}
              </div>
              {view.history.filter((row) => row.game === "locked_box" && row.action === "hint" && row.hint).map((row) => <Notice key={row.task_id} tone="info">Hint used ({row.cost} coin{row.cost === 1 ? "" : "s"}): {row.hint}</Notice>)}
            </Card>
            <Card title="Try an answer, or stop">
              <Field label="What number do you think comes next?"><Input type="number" step="any" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) send({ action: "attempt", value: Number(text) }); }} /></Field>
              <div className="mt-4 flex flex-wrap gap-2"><Btn kind="primary" onClick={() => send({ action: "attempt", value: Number(text) })} disabled={busy || !text.trim()}>{busy ? "Checking…" : "Try this answer"}</Btn><Btn kind="ghost" onClick={() => send({ action: "walk_away" })} disabled={busy}>Walk away</Btn></div>
            </Card>
          </div>
        )
      ) : null}

      {task.game === "codewords_delayed" ? (
        task.action === "predict" ? (
          <Card title="Your prediction" tone="ask">
            <Field label="How many of the eight code words do you think you will remember?"><Input type="number" min={0} max={8} step={1} value={text} onChange={(e) => setText(e.target.value)} /></Field>
            <div className="mt-4"><Btn kind="primary" onClick={() => send({ value: Number(text) })} disabled={busy || text === ""}>Save prediction</Btn></div>
          </Card>
        ) : task.kind === "recognition" ? (
          <Card title="Choose the place that matches">
            <ChoiceList name={`recognition-${task.id}`} options={task.options ?? []} value={choice} onChange={setChoice} />
            <WagerControl visible={wagerVisible} value={wager} onChange={setWager} />
            <div className="mt-4"><Btn kind="primary" onClick={() => send({ text: choice })} disabled={busy || !choice}>{busy ? "Checking…" : "Choose place"}</Btn></div>
          </Card>
        ) : (
          <Card title="Free recall" tone="ask">
            <p className="mb-2 text-sm text-[var(--muted)]">Type any code words you remember, separated by commas. Leave out ones you do not recall.</p>
            <Field label="Code words you remember"><Textarea value={text} onChange={(e) => setText(e.target.value)} /></Field>
            <WagerControl visible={wagerVisible} value={wager} onChange={setWager} />
            <div className="mt-4"><Btn kind="primary" onClick={() => send({ text })} disabled={busy}>{busy ? "Checking…" : "Check free recall"}</Btn></div>
          </Card>
        )
      ) : null}

      {task.game === "codewords_next_day" ? (
        <Card title="Next-day check">
          <Field label="Which place went with this code word?"><Input value={text} onChange={(e) => setText(e.target.value)} /></Field>
          <div className="mt-4"><Btn kind="primary" onClick={() => send({ text })} disabled={busy || !text.trim()}>Check answer</Btn></div>
        </Card>
      ) : null}

      {busy ? <p className="text-xs text-[var(--muted)]" role="status">Saving your answer…</p> : null}
    </div>
  );
}

function ChoiceList({ name, options, value, onChange }: { name: string; options: string[]; value: string; onChange: (value: string) => void }) {
  return (
    <fieldset className="mt-3 grid gap-2 sm:grid-cols-2">
      <legend className="sr-only">Choose one answer</legend>
      {options.map((option) => <label key={option} className={`flex cursor-pointer items-center gap-2 rounded-[2px] border p-3 text-sm ${value === option ? "border-[var(--accent)] bg-[var(--accent)]/5" : "border-[var(--line)] bg-[var(--panel-2)]"}`}><input type="radio" name={name} checked={value === option} onChange={() => onChange(option)} /><span>{option}</span></label>)}
    </fieldset>
  );
}

function WagerControl({ visible, value, onChange }: { visible: boolean; value: number | null; onChange: (value: number | null) => void }) {
  if (!visible) return null;
  return (
    <fieldset className="mt-4 rounded-[2px] border border-[var(--line)] p-3">
      <legend className="px-1 text-sm font-medium">Confidence wager (optional)</legend>
      <p className="mb-2 text-xs text-[var(--muted)]">Choose 0–3 coins before your answer. 0 keeps your coins; leave all blank to skip the wager.</p>
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {[0, 1, 2, 3].map((n) => <label key={n} className="flex items-center gap-1.5 text-sm"><input type="radio" name="baseline-wager" value={n} checked={value === n} onChange={() => onChange(n)} />{n}</label>)}
        {value !== null ? <button type="button" className="link-inline text-xs" onClick={() => onChange(null)}>Clear wager</button> : null}
      </div>
    </fieldset>
  );
}

function Feedback({ game, row }: { game: string; row: BaselineHistory }) {
  if (row.phase === "practice") return <Notice tone="info">Practice only — this answer is not part of the snapshot.</Notice>;
  const score = row.scored ?? {};
  if (game === "machine" && row.action === "probe") return null;
  if (game === "machine" && row.action === "guess") return <Notice tone={score.correct ? "good" : "warn"}>{score.correct ? "That guess was accepted." : "That guess was not accepted. You can test another input or try another rule."}</Notice>;
  if (game === "locked_box" && row.action === "attempt") return <Notice tone={score.correct ? "good" : "warn"}>{score.correct ? "The lock opened." : "That answer did not open the box. You can try again, ask for a hint, or walk away."}</Notice>;
  if (typeof score.correct === "boolean") return <Notice tone={score.correct ? "good" : "warn"}>{score.correct ? "That answer is right." : "That answer is not right."}{typeof score.expected === "string" ? ` The answer was ${score.expected}.` : ""}{typeof score.explain === "string" ? ` ${score.explain}` : ""}</Notice>;
  return null;
}

function moveVaultCursor(
  event: KeyboardEvent<HTMLButtonElement>,
  index: number,
  setCursor: (value: number) => void,
  refs: { current: (HTMLButtonElement | null)[] },
) {
  const row = Math.floor(index / 3);
  const col = index % 3;
  let next: number | null = null;
  if (event.key === "ArrowLeft" && col > 0) next = index - 1;
  if (event.key === "ArrowRight" && col < 2) next = index + 1;
  if (event.key === "ArrowUp" && row > 0) next = index - 3;
  if (event.key === "ArrowDown" && row < 2) next = index + 3;
  if (next !== null) {
    event.preventDefault();
    setCursor(next);
    refs.current[next]?.focus();
  }
}

function errorText(error: unknown): string {
  const err = error as { shown?: string; message?: string };
  return err?.shown ?? err?.message ?? String(error);
}
