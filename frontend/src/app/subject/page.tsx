"use client";

/** The subject round (§6): name a topic, see why these skills, sit the round, read the map.
 *
 * Three claims this screen is built to keep honest:
 *
 * * The reason travels with the skill set. Wherever the chosen skills are listed — before
 *   the round, during it, and on the map afterwards — the sentence that says why *these*
 *   four sits next to them, because a list of tests with no reason reads as a verdict.
 * * "Not measured yet" is a state, not a score. Skills the round skipped are rendered
 *   greyed, with their reason and no number: the payload gives them `score: null` and
 *   `n: 0`, and this page never turns that into 0%.
 * * The ladder position is per sub-area. A sub-area the staircase never reached says so;
 *   it is not drawn at rung 0, which would be a placement nobody measured.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { Btn, Card, Err, Field, Input, Notice, Tag } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  fetchBreakdown,
  fetchPack,
  gradeAnswer,
  type ConceptPack,
  type GradeVerdict,
} from "@/lib/content";
import { VisualFigure } from "@/components/visual";
import {
  fetchPlan,
  fetchRechecks,
  fetchSnapshot,
  finishRound,
  ladderStep,
  resolveTopic,
  startRound,
  submitRecheck,
  submitTrial,
  type LadderStepPublic,
  type MethodLegResult,
  type RecheckDue,
  type ResolveResult,
  type RoundView,
  type ServedTask,
  type SubjectPlan,
  type SubjectSnapshot,
  type SubjectSkill,
} from "@/lib/subjects";

function shown(err: unknown): string {
  if (err instanceof ApiError) return err.shown;
  return err instanceof Error ? err.message : String(err);
}

function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`;
}

export default function Subject() {
  const [text, setText] = useState("");
  const [resolved, setResolved] = useState<ResolveResult | null>(null);
  const [plan, setPlan] = useState<SubjectPlan | null>(null);
  const [view, setView] = useState<RoundView | null>(null);
  const [snapshot, setSnapshot] = useState<SubjectSnapshot | null>(null);
  const [rechecks, setRechecks] = useState<RecheckDue[]>([]);
  const [pack, setPack] = useState<ConceptPack | null>(null);
  const [packBusy, setPackBusy] = useState(false);
  // The panel has its own topic field rather than borrowing the resolve card's: the pack is about
  // a topic, not about the round, and a learner who has not resolved anything yet can still ask
  // what we would teach. It follows the resolve card when that one has an answer.
  const [packTopic, setPackTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const shownAt = useRef<number>(Date.now());

  useEffect(() => {
    shownAt.current = Date.now();
  }, [view?.task, view?.step_id, view?.stage]);

  const refreshRechecks = useCallback(async () => {
    try {
      const out = await fetchRechecks();
      setRechecks(out.due);
    } catch {
      /* the recheck list is an extra; its absence is not an error on this screen */
    }
  }, []);

  useEffect(() => {
    void refreshRechecks();
  }, [refreshRechecks]);

  async function onResolve(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSnapshot(null);
    setView(null);
    try {
      const out = await resolveTopic(text.trim());
      setResolved(out);
      if (out.resolved.title) setPackTopic(out.resolved.title);
      const p = await fetchPlan(out.resolved.title || text.trim());
      setPlan(p);
    } catch (err) {
      setError(shown(err));
      setResolved(null);
      setPlan(null);
    } finally {
      setBusy(false);
    }
  }

  async function onStart() {
    const topic = resolved?.resolved.title || text.trim();
    setBusy(true);
    setError(null);
    try {
      setView(await startRound(topic));
      setSnapshot(null);
    } catch (err) {
      setError(shown(err));
    } finally {
      setBusy(false);
    }
  }

  async function act(fn: () => Promise<RoundView>) {
    setBusy(true);
    setError(null);
    try {
      const next = await fn();
      setView(next);
      if (next.scored?.why) setNotice(next.scored.why);
    } catch (err) {
      setError(shown(err));
    } finally {
      setBusy(false);
    }
  }

  async function onFinish() {
    if (!view) return;
    setBusy(true);
    setError(null);
    try {
      setSnapshot(await finishRound(view.run_id));
      setView(null);
      await refreshRechecks();
    } catch (err) {
      setError(shown(err));
    } finally {
      setBusy(false);
    }
  }

  async function onShowMap() {
    setBusy(true);
    setError(null);
    try {
      setSnapshot(await fetchSnapshot(resolved?.resolved.title || text.trim() || undefined));
    } catch (err) {
      setError(shown(err));
    } finally {
      setBusy(false);
    }
  }

  async function onShowPack() {
    const topic = (packTopic || resolved?.resolved.title || text.trim()).trim();
    if (!topic) return;
    setPackBusy(true);
    setError(null);
    try {
      // The breakdown first: it is what names the concepts this topic has, and a topic with no
      // authored chain says so rather than leaving the button looking broken.
      const out = await fetchBreakdown(topic);
      const first = (out.concepts ?? [])[0];
      if (!out.available || !first) {
        setPack(null);
        setNotice(out.reason);
        return;
      }
      setPack(await fetchPack(topic, String(first.id)));
      setNotice(null);
    } catch (err) {
      setError(shown(err));
    } finally {
      setPackBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
          § 06 · Subject round
        </div>
        <h1 className="text-xl" style={{ fontFamily: "var(--font-serif)" }}>
          Pick a subject, and we measure what it actually asks of you
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">
          Name a topic. We will show you the handful of skills it leans on and why those ones, test just
          those, climb a short ladder of prerequisites, and hand you a map that also lists — greyed
          out — everything we did <em>not</em> measure. Not measured is not the same as not there.
        </p>
      </div>

      {error ? <Err error={error} /> : null}
      {notice && !view ? <Notice tone="info">{notice}</Notice> : null}

      {!view && !snapshot ? (
        <Card title="Your topic">
          <form onSubmit={onResolve} className="flex flex-wrap items-end gap-3">
            <div className="min-w-64 flex-1">
              <Field label="What are you studying?" hint="Free text is fine: “GCSE government and politics”, “calculus”, “sarufi”.">
                <Input
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="e.g. calculus, or government and civics"
                  aria-label="Topic"
                />
              </Field>
            </div>
            <Btn type="submit" disabled={busy || !text.trim()}>
              {busy ? "…" : "Find my subject"}
            </Btn>
            <Btn type="button" onClick={onShowMap} disabled={busy} title="the map from your last finished round">
              My last subject map
            </Btn>
          </form>

          {resolved ? (
            <div className="mt-4 space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <strong>{resolved.resolved.title}</strong>
                <Tag>{resolved.resolved.subject}</Tag>
                {resolved.resolved.level_hint ? <Tag>{resolved.resolved.level_hint.replaceAll("_", " ")}</Tag> : null}
                <span className="text-[var(--muted)]">{resolved.resolved.note}</span>
              </div>
              {resolved.suggestions.length ? (
                <div className="flex flex-wrap gap-2">
                  {resolved.suggestions.map((s, i) => (
                    <Btn
                      key={i}
                      type="button"
                      onClick={() => {
                        setText(s.title ?? "");
                      }}
                    >
                      {s.title}
                    </Btn>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </Card>
      ) : null}

      {!view && !snapshot ? (
        <Card title="What we'd teach">
          <p className="text-sm text-[var(--muted)]">
            Before any of this is measured, here is the material the topic has authored for it: one
            worked example, one question, and a matched pair that asks the same thing twice in the
            same shape. Where a concept has no authored kit, this says so instead of showing
            something made up.
          </p>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <div className="min-w-56 flex-1">
              <Field label="Which topic?">
                <Input
                  value={packTopic}
                  onChange={(e) => setPackTopic(e.target.value)}
                  placeholder="e.g. calculus"
                  aria-label="Pack topic"
                />
              </Field>
            </div>
            <Btn type="button" onClick={onShowPack} disabled={packBusy || !packTopic.trim()}>
              {packBusy ? "…" : "Show what we'd teach"}
            </Btn>
            {pack ? <span className="text-xs text-[var(--muted)]">{pack.concept_title}</span> : null}
          </div>
          {pack ? <PackPanel pack={pack} /> : null}
        </Card>
      ) : null}

      {plan && !view && !snapshot ? (
        <PlanCard plan={plan} busy={busy} onStart={onStart} />
      ) : null}

      {view ? (
        <RoundRunner
          view={view}
          busy={busy}
          notice={notice}
          latency={() => Date.now() - shownAt.current}
          onTrial={(taskId, response, wager) =>
            act(() => submitTrial(view.run_id, { task_id: taskId, response, latency_ms: Date.now() - shownAt.current, wager }))
          }
          onLadder={(response, wager) =>
            act(() => ladderStep(String(view.step_id), { response, latency_ms: Date.now() - shownAt.current, wager }))
          }
          onFinish={onFinish}
        />
      ) : null}

      {snapshot ? (
        <SnapshotView
          snapshot={snapshot}
          rechecks={rechecks}
          onAgain={() => {
            setSnapshot(null);
            setResolved(null);
            setPlan(null);
            setText("");
          }}
          onRechecked={refreshRechecks}
        />
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ plan ---- */

function PlanCard({ plan, busy, onStart }: { plan: SubjectPlan; busy: boolean; onStart: () => void }) {
  return (
    <Card
      title={`What we will check for ${plan.topic.title}`}
      right={<span>about {Math.round(plan.estimated_seconds / 60) || 1}–{Math.ceil(plan.budget_seconds / 60)} minutes</span>}
    >
      <p className="text-sm">
        <strong>Why these?</strong> {plan.reason}
      </p>
      <ol className="mt-3 space-y-2">
        {plan.tested.map((s) => (
          <li key={s.id} className="flex flex-wrap items-baseline gap-2 text-sm">
            <span className="font-medium">{s.title}</span>
            <Tag tone={s.role === "cross_check" ? "think" : "accent"}>{s.role === "cross_check" ? "cross-check" : `leans ${s.rating_label}`}</Tag>
            <span className="text-[var(--muted)]">
              {s.measures} · {s.items ?? 3} quick tasks, about {s.game?.seconds ?? s.seconds ?? 40}s
            </span>
            <span className="w-full text-xs text-[var(--muted)]">{s.reason}</span>
          </li>
        ))}
      </ol>
      {plan.not_measured.length ? (
        <div className="mt-4 border-t border-dashed pt-3">
          <p className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
            Not measured in this round
          </p>
          <ul className="mt-2 space-y-1">
            {plan.not_measured.map((s) => (
              <li key={s.id} className="flex flex-wrap items-baseline gap-2 text-sm opacity-55">
                <span>{s.title}</span>
                <Tag>not measured yet</Tag>
                <span className="text-xs">{s.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="mt-4">
        <Btn onClick={onStart} disabled={busy}>
          Start the round
        </Btn>
      </div>
    </Card>
  );
}

/* ----------------------------------------------------------------- round ---- */

function RoundRunner({
  view,
  busy,
  notice,
  latency,
  onTrial,
  onLadder,
  onFinish,
}: {
  view: RoundView;
  busy: boolean;
  notice: string | null;
  latency: () => number;
  onTrial: (taskId: string, response: Record<string, unknown>, wager?: number) => void;
  onLadder: (response: Record<string, unknown>, wager?: number) => void;
  onFinish: () => void;
}) {
  const [wager, setWager] = useState(0);
  const progress = view.progress ?? {};
  const stageLabel =
    view.stage === "skills"
      ? `Skill ${Number(progress.skill_item ?? 1)} of ${Number(progress.skill_items ?? 0)}`
      : view.stage === "ladder"
        ? `Ladder item ${Number(progress.ladder_items ?? 0) + 1} of ${Number(progress.ladder_max_items ?? 9)}`
        : view.stage === "method"
          ? `Pop-up lesson, step ${Number(progress.method_item ?? 1)} of ${Number(progress.method_items ?? 0)}`
          : "Done";

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      <Card title={view.stage_title} right={<span>{stageLabel}</span>}>
        {notice ? <Notice tone="info">{notice}</Notice> : null}
        {view.kind === "done" || !view.task ? (
          <div className="space-y-3">
            <p className="text-sm">{view.note || "That is everything this round can measure."}</p>
            <Btn onClick={onFinish} disabled={busy}>
              See my subject map
            </Btn>
          </div>
        ) : view.stage === "ladder" ? (
          <LadderTask
            step={view.task as LadderStepPublic}
            busy={busy}
            wager={wager}
            setWager={setWager}
            onAnswer={(response) => onLadder(response, wager)}
          />
        ) : view.kind === "lesson" ? (
          <LessonTask task={view.task as ServedTask} busy={busy} onDone={(t) => onTrial(t.id, { seconds_seen: t.seconds ?? 90 })} />
        ) : (
          <TaskCard
            task={view.task as ServedTask}
            busy={busy}
            wager={wager}
            setWager={setWager}
            onAnswer={(response) => onTrial((view.task as ServedTask).id, response, wager)}
          />
        )}
        <div className="mt-4 flex items-center justify-between border-t border-dashed pt-3">
          <span className="text-xs text-[var(--muted)]">{latency() >= 0 ? view.note : ""}</span>
          <Btn onClick={onFinish} disabled={busy} title="stop here and keep what has been measured">
            Finish early and see the map
          </Btn>
        </div>
      </Card>

      <Card title="Why these skills" tone="muted">
        <p className="text-sm">{view.reason}</p>
        <ul className="mt-3 space-y-1 text-sm">
          {view.plan.tested.map((s) => (
            <li key={s.id}>
              {s.title} <span className="text-[var(--muted)]">({s.role === "cross_check" ? "cross-check" : s.rating_label})</span>
            </li>
          ))}
          {view.plan.not_measured.map((s) => (
            <li key={s.id} className="opacity-55">
              {s.title} <span className="text-[var(--muted)]">— not measured yet</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ tasks ---- */

function WagerPicker({ wager, setWager }: { wager: number; setWager: (n: number) => void }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-[var(--muted)]">Sure of it? Bet coins:</span>
      {[0, 1, 2, 3].map((n) => (
        <button
          key={n}
          type="button"
          aria-pressed={wager === n}
          onClick={() => setWager(n)}
          className={`rounded border px-2 py-0.5 ${wager === n ? "border-[var(--accent)] font-semibold" : ""}`}
        >
          {n}
        </button>
      ))}
    </div>
  );
}

function TaskCard({
  task,
  busy,
  wager,
  setWager,
  onAnswer,
}: {
  task: ServedTask;
  busy: boolean;
  wager: number;
  setWager: (n: number) => void;
  onAnswer: (response: Record<string, unknown>) => void;
}) {
  const [choice, setChoice] = useState<number | null>(null);
  const [textValue, setTextValue] = useState("");
  const [order, setOrder] = useState<number[]>([]);
  const [pairs, setPairs] = useState<Record<number, number>>({});
  const kind = task.kind;

  useEffect(() => {
    setChoice(null);
    setTextValue("");
    setOrder([]);
    setPairs({});
  }, [task.id]);

  if (task.phase === "show" || kind === "study") {
    return (
      <div className="space-y-3">
        <p className="text-sm">{task.stem || task.instructions}</p>
        <Btn disabled={busy} onClick={() => onAnswer({})}>
          Got it — next
        </Btn>
      </div>
    );
  }

  // "met" is the ladder's first question (have you met this idea?): two options, answered like a choice.
  // It had no control at all before, so a learner could not get past the first rung of a ladder.
  const isChoice = kind === "choice" || kind === "met";
  const canSubmit =
    isChoice
      ? choice !== null
      : kind === "order"
        ? order.length === (task.items?.length ?? 0)
        : kind === "match"
          ? Object.keys(pairs).length === (task.left?.length ?? 0)
          : textValue.trim().length > 0;

  function submit() {
    if (isChoice) onAnswer({ choice });
    else if (kind === "numeric") onAnswer({ value: textValue });
    else if (kind === "order") onAnswer({ order });
    else if (kind === "match") onAnswer({ pairs: Object.entries(pairs).map(([l, r]) => [Number(l), r]) });
    else onAnswer({ text: textValue });
  }

  return (
    <div className="space-y-3">
      {task.instructions ? <p className="text-sm text-[var(--muted)]">{task.instructions}</p> : null}
      {task.passage ? <blockquote className="border-l-2 pl-3 text-sm">{task.passage}</blockquote> : null}
      <p className="text-base">{task.stem}</p>
      {task.checklist?.length ? (
        <ul className="list-disc pl-5 text-sm text-[var(--muted)]">
          {task.checklist.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      ) : null}

      {isChoice && task.options ? (
        <div className="space-y-2" role="group" aria-label="Choices">
          {task.options.map((opt, i) => (
            <button
              key={i}
              type="button"
              aria-pressed={choice === i}
              onClick={() => setChoice(i)}
              className={`block w-full rounded border px-3 py-2 text-left text-sm ${choice === i ? "border-[var(--accent)] font-medium" : ""}`}
            >
              {opt}
            </button>
          ))}
        </div>
      ) : null}

      {kind === "order" && task.items ? (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {task.items.map((item, i) => (
              <button
                key={i}
                type="button"
                disabled={order.includes(i)}
                onClick={() => setOrder([...order, i])}
                className="rounded border px-2 py-1 text-sm disabled:opacity-40"
              >
                {order.includes(i) ? `${order.indexOf(i) + 1}. ` : ""}
                {item}
              </button>
            ))}
          </div>
          <Btn type="button" onClick={() => setOrder([])} disabled={!order.length}>
            Start the order again
          </Btn>
        </div>
      ) : null}

      {kind === "match" && task.left && task.right ? (
        <div className="space-y-2">
          {task.left.map((left, li) => (
            <label key={li} className="flex items-center gap-2 text-sm">
              <span className="min-w-40">{left}</span>
              <select
                aria-label={`Match for ${left}`}
                value={pairs[li] ?? ""}
                onChange={(e) => setPairs({ ...pairs, [li]: Number(e.target.value) })}
                className="rounded border px-2 py-1"
              >
                <option value="" disabled>
                  choose…
                </option>
                {task.right!.map((r, ri) => (
                  <option key={ri} value={ri}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      ) : null}

      {kind === "numeric" || kind === "text" ? (
        kind === "numeric" ? (
          <Input
            inputMode="decimal"
            value={textValue}
            onChange={(e) => setTextValue(e.target.value)}
            aria-label="Your number"
            placeholder={task.unit ? `answer in ${task.unit}` : "your answer"}
          />
        ) : (
          <textarea
            value={textValue}
            onChange={(e) => setTextValue(e.target.value)}
            aria-label="Your answer"
            rows={4}
            className="w-full rounded border px-3 py-2 text-sm"
            placeholder="Write it in your own words"
          />
        )
      ) : null}

      {task.wagerable ? <WagerPicker wager={wager} setWager={setWager} /> : null}
      <Btn onClick={submit} disabled={busy || !canSubmit}>
        {busy ? "…" : "Answer"}
      </Btn>
    </div>
  );
}

function LadderTask({
  step,
  busy,
  wager,
  setWager,
  onAnswer,
}: {
  step: LadderStepPublic;
  busy: boolean;
  wager: number;
  setWager: (n: number) => void;
  onAnswer: (response: Record<string, unknown>) => void;
}) {
  const rungs = ["Have you met it?", "Recognise it", "Recall it", "Apply a standard case", "Apply it in a new situation", "Explain why"];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Tag tone="accent">{step.sub_area}</Tag>
        <strong>{step.node_title}</strong>
        <span className="text-[var(--muted)]">
          rung {step.rung} of 5 — {step.rung_title ?? rungs[step.rung]}
        </span>
      </div>
      <div className="flex gap-1" aria-label={`on rung ${step.rung} of 5`}>
        {rungs.slice(1).map((label, i) => (
          <span
            key={label}
            title={label}
            className={`h-2 flex-1 rounded ${i + 1 <= step.rung ? "bg-[var(--accent)]" : "bg-[var(--line)]"}`}
          />
        ))}
      </div>
      {step.why ? <p className="text-sm text-[var(--muted)]">{step.why}</p> : null}
      <TaskCard task={step.task} busy={busy} wager={wager} setWager={setWager} onAnswer={onAnswer} />
    </div>
  );
}

function LessonTask({ task, busy, onDone }: { task: ServedTask; busy: boolean; onDone: (t: ServedTask) => void }) {
  const visual = task.visual;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Tag tone="think">{task.method_label ?? "A quick lesson"}</Tag>
        <span className="text-sm text-[var(--muted)]">about {task.seconds ?? 90} seconds — then three questions on it</span>
      </div>
      {task.body ? <p className="max-w-2xl text-sm leading-6">{task.body}</p> : null}
      {visual ? (
        <figure className="rounded border p-3">
          <figcaption className="text-sm font-medium">{visual.title}</figcaption>
          <p className="sr-only">{visual.alt}</p>
          {visual.nodes?.length ? (
            <ol className="mt-2 space-y-1">
              {visual.nodes.map((n) => (
                <li key={n.index} className="flex gap-2 text-sm">
                  <Tag>{n.label}</Tag>
                  <span>{n.text}</span>
                </li>
              ))}
            </ol>
          ) : null}
          {visual.rows?.length ? (
            <table className="mt-2 text-sm">
              {visual.columns?.length ? (
                <thead>
                  <tr>
                    {visual.columns.map((c) => (
                      <th key={c} className="pr-3 text-left">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
              ) : null}
              <tbody>
                {visual.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j} className="pr-3">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <p className="mt-2 text-xs text-[var(--muted)]">{visual.alt}</p>
        </figure>
      ) : null}
      {task.steps?.length ? (
        <ol className="space-y-2">
          {task.steps.map((s) => (
            <li key={s.index} className="text-sm">
              <strong>Step {s.index}.</strong> {s.text}
              {s.why ? <span className="block text-xs text-[var(--muted)]">{s.why}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
      {task.why ? <p className="text-xs text-[var(--muted)]">{task.why}</p> : null}
      <Btn disabled={busy} onClick={() => onDone(task)}>
        I have read it — the questions
      </Btn>
    </div>
  );
}

/* -------------------------------------------------------------- snapshot ---- */

function SnapshotView({
  snapshot,
  rechecks,
  onAgain,
  onRechecked,
}: {
  snapshot: SubjectSnapshot;
  rechecks: RecheckDue[];
  onAgain: () => void;
  onRechecked: () => Promise<void>;
}) {
  if (!snapshot.available) {
    return (
      <Card title="Your subject map">
        <p className="text-sm text-[var(--muted)]">{snapshot.note}</p>
        <div className="mt-3">
          <Btn onClick={onAgain}>Pick a topic</Btn>
        </div>
      </Card>
    );
  }
  const legs: MethodLegResult[] = snapshot.method_test?.results?.legs ?? [];
  return (
    <div className="space-y-4">
      <Card title={`Your subject map — ${snapshot.topic?.title ?? ""}`} right={<span>{snapshot.taken_at ? new Date(snapshot.taken_at).toLocaleDateString() : ""}</span>}>
        <p className="text-sm">
          <strong>Why these skills?</strong> {snapshot.reason}
        </p>
        <p className="mt-1 text-xs text-[var(--muted)]">{snapshot.note}</p>
      </Card>

      <Card title="Skills">
        <ul className="divide-y">
          {(snapshot.skills ?? []).map((s) => (
            <SkillRow key={s.id} skill={s} />
          ))}
        </ul>
      </Card>

      {snapshot.ladder?.available && snapshot.ladder.positions ? (
        <Card title="Where the ladder settled" right={<span>{snapshot.ladder.placed_sub_areas ?? 0} of {snapshot.ladder.sub_areas ?? 0} sub-areas placed</span>}>
          <ul className="space-y-2">
            {snapshot.ladder.positions.map((p) => (
              <li key={p.sub_area} className={`flex flex-wrap items-baseline gap-2 text-sm ${p.placed ? "" : "opacity-55"}`}>
                <span className="min-w-32 font-medium">{p.sub_area}</span>
                {p.placed ? (
                  <>
                    <Tag tone="accent">{p.rung_title}</Tag>
                    <span className="text-[var(--muted)]">
                      steady at rung {p.steady_rung} · {p.n} item{p.n === 1 ? "" : "s"} · {p.evidence}
                    </span>
                  </>
                ) : (
                  <>
                    <Tag>not measured yet</Tag>
                    <span className="text-xs">{p.note}</span>
                  </>
                )}
              </li>
            ))}
          </ul>
        </Card>
      ) : snapshot.ladder ? (
        <Card title="Where the ladder settled" tone="muted">
          <p className="text-sm text-[var(--muted)]">{snapshot.ladder.note}</p>
        </Card>
      ) : null}

      {snapshot.method_test?.available ? (
        <Card title="The pop-up lesson" right={<span>{snapshot.method_test.results?.evidence ?? ""}</span>}>
          {legs.length ? (
            <ul className="space-y-2">
              {legs.map((leg) => (
                <li key={leg.leg} className="text-sm">
                  <strong>{leg.concept_title}</strong> — taught by <em>{leg.method_label.toLowerCase()}</em>: {pct(leg.accuracy)} right
                  from {leg.n} answers, against {pct(leg.expected)} expected for you, so {leg.residual >= 0 ? "+" : ""}
                  {leg.residual.toFixed(2)} against expectation ({leg.evidence}).
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-[var(--muted)]">{snapshot.method_test.note}</p>
          )}
          <p className="mt-2 text-xs text-[var(--muted)]">
            {snapshot.method_test.results?.note ??
              "Two short lessons, taught two different ways, dealt between learners by code. This compares the ways of teaching — it does not label how you learn."}
          </p>
          {snapshot.method_test.recheck_at ? (
            <p className="mt-1 text-xs">Two of those questions come back tomorrow, to check the gain is still there.</p>
          ) : null}
        </Card>
      ) : snapshot.method_test ? (
        <Card title="The pop-up lesson" tone="muted">
          <p className="text-sm text-[var(--muted)]">{snapshot.method_test.note}</p>
        </Card>
      ) : null}

      {rechecks.length ? <RecheckCard due={rechecks[0]} onDone={onRechecked} /> : null}

      {snapshot.completion?.stages?.length ? (
        <Card title="What the round reached" tone="muted">
          <ul className="space-y-1 text-sm">
            {snapshot.completion.stages.map((s, i) => (
              <li key={i}>
                {s.done} <span className="text-[var(--muted)]">({s.evidence})</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <Btn onClick={onAgain}>Measure another topic</Btn>
    </div>
  );
}

function SkillRow({ skill }: { skill: SubjectSkill }) {
  const measured = skill.status === "measured";
  return (
    <li className={`flex flex-wrap items-baseline gap-2 py-2 text-sm ${measured ? "" : "opacity-55"}`}>
      <span className="min-w-44 font-medium">{skill.title}</span>
      {measured ? (
        <>
          <Tag tone="accent">{skill.evidence ?? "measured"}</Tag>
          <span>
            {pct(skill.accuracy)} from {skill.n} answer{skill.n === 1 ? "" : "s"}
            {skill.correct !== undefined ? ` (${skill.correct} right)` : ""}
          </span>
        </>
      ) : (
        <>
          <Tag>not measured yet</Tag>
          <span className="text-xs">{skill.reason}</span>
        </>
      )}
      {measured && skill.note ? <span className="w-full text-xs text-[var(--muted)]">{skill.note}</span> : null}
    </li>
  );
}

function RecheckCard({ due, onDone }: { due: RecheckDue; onDone: () => Promise<void> }) {
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const items = due.concepts.flatMap((c, ci) => c.items.map((item, ii) => ({ key: `${ci}:${ii}`, item })));
  async function submit() {
    setBusy(true);
    try {
      const grouped: Record<string, unknown>[][] = due.concepts.map((c, ci) =>
        c.items.map((_, ii) => ({ choice: answers[`${ci}:${ii}`] ?? 0 })),
      );
      await submitRecheck(due.method_test_id, grouped);
      setDone("Recheck handed in. A gain that is still there tomorrow is worth more than one that was only there on the day.");
      await onDone();
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Yesterday's lesson, asked again" tone="warn">
      <p className="text-sm">{due.why || "Two of yesterday's questions, asked again: a gain that has gone by today was not a gain."}</p>
      {done ? (
        <Notice tone="good">{done}</Notice>
      ) : (
        <div className="mt-3 space-y-4">
          {items.map(({ key, item }) => (
            <fieldset key={key}>
              <legend className="text-sm">{item.stem}</legend>
              <div className="mt-1 flex flex-wrap gap-2">
                {(item.options ?? []).map((opt, i) => (
                  <button
                    key={i}
                    type="button"
                    aria-pressed={answers[key] === i}
                    onClick={() => setAnswers({ ...answers, [key]: i })}
                    className={`rounded border px-2 py-1 text-sm ${answers[key] === i ? "border-[var(--accent)] font-medium" : ""}`}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            </fieldset>
          ))}
          <Btn onClick={submit} disabled={busy}>
            Hand in the recheck
          </Btn>
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------- what we'd teach ---- */
/*
 * The §7 pack on a screen. Four things matter here, and they are why this is not a plain list of
 * cards:
 *
 * 1. `available: false` is shown, not worked around. The engine's own `reason` is the sentence on
 *    screen, because "we have nothing authored for this concept" is the honest answer and a
 *    placeholder would be a claim we cannot back.
 * 2. `rejections` is rendered where the missing part would have been. A worked example that
 *    failed its own gate is a decision the engine made, and hiding it makes the pack look broken.
 * 3. The pair's own note about *not* being matched on difficulty is shown, so the two legs are
 *    not read as a like-for-like comparison.
 * 4. The pair is answerable, and graded by the server. The browser sends the option text, never an
 *    index, and never learns which option is right until it has answered — the explanation
 *    arrives with the verdict.
 */
function PackPanel({ pack }: { pack: ConceptPack }) {
  if (!pack.available) {
    return (
      <p className="mt-3 rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm">
        {pack.reason}
      </p>
    );
  }
  return (
    <div className="mt-4 space-y-4">
      <p className="text-[13px] text-[var(--muted)]">{pack.reason}</p>
      <PackRejections rejections={pack.rejections} />
      <div className="grid gap-3 md:grid-cols-2">
        <VariantCard part="Written" item={pack.variants.written} />
        <VariantCard part="Visual" item={pack.variants.visual} />
        <VariantCard part="Worked example" item={pack.variants.worked_example} wide />
      </div>
      <MatchedPair pack={pack} />
      <div className="grid gap-3 md:grid-cols-2">
        <HintLadder hints={pack.hints} />
        <Checklist checklist={pack.checklist} />
      </div>
    </div>
  );
}

function PackRejections({ rejections }: { rejections: ConceptPack["rejections"] }) {
  if (!rejections.length) return null;
  return (
    <ul className="space-y-1">
      {rejections.map((r, i) => (
        <li key={i} className="rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-[13px]">
          <span className="text-[var(--warn)]">{r.code}</span> {r.detail}
        </li>
      ))}
    </ul>
  );
}

function VariantCard({
  part,
  item,
  wide,
}: {
  part: string;
  item?: ConceptPack["variants"]["written"];
  wide?: boolean;
}) {
  if (!item) {
    return (
      <div className={`rounded-md border border-[var(--line)] bg-[var(--panel)] p-3 text-sm text-[var(--muted)]${wide ? " md:col-span-2" : ""}`}>
        <div className="text-xs uppercase tracking-[0.12em]" style={{ fontFamily: "var(--font-mono)" }}>
          {part}
        </div>
        <p className="mt-2">Nothing authored for this part.</p>
      </div>
    );
  }
  const spec = (item as { spec?: unknown }).spec;
  return (
    <div className={`rounded-md border border-[var(--line)] bg-[var(--panel)] p-3${wide ? " md:col-span-2" : ""}`}>
      <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        {part}
        {item.rung ? <span className="ml-2 normal-case tracking-normal">rung {item.rung}</span> : null}
      </div>
      <p className="mt-2 text-sm">{item.stem}</p>
      {item.steps?.length ? (
        <ol className="mt-2 space-y-1 text-sm">
          {item.steps.map((s, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-[var(--muted)]">{i + 1}.</span>
              <span>{s}</span>
            </li>
          ))}
        </ol>
      ) : null}
      {spec ? (
        <div className="mt-2">
          <VisualFigure spec={spec} fallbackNote="this figure was not authored" />
        </div>
      ) : null}
      {item.verified === false ? (
        <p className="mt-2 text-[13px] text-[var(--warn)]">
          This worked example failed its own gate, so it is shown as written rather than as checked.
        </p>
      ) : null}
    </div>
  );
}

function MatchedPair({ pack }: { pack: ConceptPack }) {
  const { A, B } = pack.pair;
  return (
    <div className="rounded-md border border-[var(--line)] bg-[var(--panel)] p-3">
      <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        Matched pair
      </div>
      <p className="mt-2 text-[13px] text-[var(--muted)]">{pack.pair.matched}</p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <PairLeg topic={pack.topic_key} concept={pack.concept_id} leg="A" item={A} />
        <PairLeg topic={pack.topic_key} concept={pack.concept_id} leg="B" item={B} />
      </div>
      <p className="mt-3 text-[12px] text-[var(--muted)]">{pack.pair.expected_accuracy_note}</p>
    </div>
  );
}

function PairLeg({
  topic,
  concept,
  leg,
  item,
}: {
  topic: string;
  concept: string;
  leg: "A" | "B";
  item?: ConceptPack["pair"]["A"];
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<GradeVerdict | null>(null);
  const [busy, setBusy] = useState(false);

  async function onPick(choice: string) {
    setPicked(choice);
    setBusy(true);
    setVerdict(null);
    try {
      setVerdict(await gradeAnswer(topic, concept, leg, choice));
    } catch (err) {
      setVerdict({
        correct: false,
        status: "error",
        points: 0,
        max_points: 1,
        why: shown(err),
        explanation: "",
        concept,
        leg,
        rung: 0,
        demand: "",
      });
    } finally {
      setBusy(false);
    }
  }

  if (!item) return <p className="text-sm text-[var(--muted)]">Nothing authored for this leg.</p>;
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.1em] text-[var(--muted)]">
        Leg {leg}
        {item.rung ? <span className="ml-2 normal-case tracking-normal">rung {item.rung}</span> : null}
      </div>
      <p className="mt-1 text-sm">{item.stem}</p>
      <div className="mt-2 space-y-1">
        {(item.options ?? []).map((o, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onPick(o)}
            disabled={busy || picked !== null}
            className={`block w-full rounded-md border px-3 py-2 text-left text-sm disabled:opacity-60 ${
              picked === o ? "border-[var(--accent)]" : "border-[var(--line)]"
            }`}
          >
            {o}
          </button>
        ))}
      </div>
      {verdict ? (
        <div className="mt-2 rounded-md border border-[var(--line)] px-3 py-2 text-sm">
          <strong>{verdict.correct ? "Right." : "Not yet."}</strong> {verdict.why}
          {verdict.explanation ? (
            <p className="mt-1 text-[13px] text-[var(--muted)]">{verdict.explanation}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function HintLadder({ hints }: { hints: ConceptPack["hints"] }) {
  return (
    <div className="rounded-md border border-[var(--line)] bg-[var(--panel)] p-3">
      <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        Hints, in order
      </div>
      <ol className="mt-2 space-y-1 text-sm">
        {hints.map((h, i) => (
          <li key={i} className="flex gap-2">
            <span className="text-[var(--muted)]">{i + 1}.</span>
            <span>{h.text}</span>
          </li>
        ))}
      </ol>
      <p className="mt-2 text-[12px] text-[var(--muted)]">
        {hints.length ? `Rung ${hints[hints.length - 1].rung} is the last one; there is no near-answer step after it.` : "No hints are authored for this concept."}
      </p>
    </div>
  );
}

function Checklist({ checklist }: { checklist: ConceptPack["checklist"] }) {
  return (
    <div className="rounded-md border border-[var(--line)] bg-[var(--panel)] p-3">
      <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        Self-check
      </div>
      <ul className="mt-2 space-y-1 text-sm">
        {checklist.map((c, i) => (
          <li key={i} className="flex gap-2">
            <span className="text-[var(--muted)]">□</span>
            <span>{String((c as { point?: string }).point ?? "")}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
