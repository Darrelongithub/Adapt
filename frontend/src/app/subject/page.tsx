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

  const canSubmit =
    kind === "choice"
      ? choice !== null
      : kind === "order"
        ? order.length === (task.items?.length ?? 0)
        : kind === "match"
          ? Object.keys(pairs).length === (task.left?.length ?? 0)
          : textValue.trim().length > 0;

  function submit() {
    if (kind === "choice") onAnswer({ choice });
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

      {kind === "choice" && task.options ? (
        <div className="space-y-2" role="radiogroup" aria-label="Choices">
          {task.options.map((opt, i) => (
            <button
              key={i}
              type="button"
              role="radio"
              aria-checked={choice === i}
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
