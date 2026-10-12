"use client";

/** One question, exactly as the backend described it.
 *
 * This component is where the app's promises are kept or broken, so the rules here are
 * the rules the API advertises:
 *
 * - the *presentation* from the plan decides the input (word budget, hints allowed,
 *   whether a teach-back is required), never a hard-coded guess;
 * - hints are asked for from the server (`hints_used` counts up) and the button stops
 *   when the server says the ladder is exhausted -- the client never keeps asking and
 *   never shows a repeated sentence as if it were new;
 * - the answer is not stored anywhere but the POST body. No localStorage of responses,
 *   no "undo" that reads a cached answer key;
 * - worked examples are opened through the POST endpoint, because the *opening* is what
 *   the fading decision is measured on.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { Band, Btn, Card, Check, Field, Input, Notice, Tag, Textarea, Trace } from "./ui";
import { api, post } from "@/lib/api";
import type { ExplainReply, PlanEntry, Presentation, Receipt } from "@/lib/types";

type Props = {
  entry: PlanEntry;
  presentation?: Presentation;
  /** What the page does with a submitted answer. Returns nothing: the receipt is shown here. */
  onSubmit: (itemId: string, answer: unknown, extra: { teachback_text?: string; attempt_no?: number }) => Promise<Receipt>;
  onInteract: (item: PlanEntry["item"]) => void;
  onChange: (item: PlanEntry["item"], from: unknown, to: unknown) => void;
  onHint: (item: PlanEntry["item"], hintNo: number) => void;
  onExample: (item: PlanEntry["item"]) => void;
  onExplanation: (item: PlanEntry["item"], chars: number) => void;
  /** Diagnostic mode: no hints, no examples -- placement has to measure what they can do
   *  unaided, and a hint here would corrupt the estimate the whole plan is built on. */
  aidless?: boolean;
  attemptNo?: number;
};

export function Question({ entry, presentation, onSubmit, onInteract, onChange, onHint, onExample, onExplanation, aidless, attemptNo = 1 }: Props) {
  const item = entry.item;
  const pres = { ...(entry.presentation ?? {}), ...(presentation ?? {}) };
  const choices = item.choices ?? [];
  const isChoice = item.kind === "mcq" && choices.length > 0;
  const wantTeachback = item.kind === "teachback" || Boolean(pres.require_teachback);

  const [picked, setPicked] = useState<number | null>(null);
  const [typed, setTyped] = useState("");
  const [teach, setTeach] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [hints, setHints] = useState<{ no: number; text: string }[]>([]);
  const [hintState, setHintState] = useState<{ available: number; exhausted: boolean; max: number | null }>({ available: item.hint_count ?? 0, exhausted: false, max: null });
  const [example, setExample] = useState<{ index: number; text: string; why?: string | null }[] | null>(null);
  const [revealed, setRevealed] = useState<Record<string, unknown> | null>(null);
  const [question, setQuestion] = useState("");
  const [reply, setReply] = useState<ExplainReply | null>(null);
  const [askBusy, setAskBusy] = useState(false);
  const startedRef = useRef(false);

  useEffect(() => {
    // A new question is a new answer: clearing everything is the difference between
    // "one item per screen" and "the previous item's text quietly submitted again".
    setPicked(null);
    setTyped("");
    setTeach("");
    setReceipt(null);
    setHints([]);
    setHintState({ available: item.hint_count ?? 0, exhausted: false, max: null });
    setExample(null);
    setRevealed(null);
    setReply(null);
    setError(null);
    startedRef.current = false;
  }, [item.id, item.hint_count]);

  const answer = useMemo(() => {
    if (isChoice) return picked;
    const text = typed.trim();
    if (item.kind === "numeric") return text;
    return text;
  }, [isChoice, picked, typed, item.kind]);

  const canSubmit = isChoice ? picked !== null : item.kind === "worked_example" ? true : typed.trim().length > 0;

  const touch = () => {
    if (startedRef.current) return;
    startedRef.current = true;
    onInteract(item);
  };

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const out = await onSubmit(item.id, answer, wantTeachback ? { teachback_text: teach, attempt_no: attemptNo } : { attempt_no: attemptNo });
      setReceipt(out);
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function askForHint() {
    setBusy(true);
    setError(null);
    try {
      const out = await api<{ hint: string; hint_no: number; hints_available: number; exhausted: boolean; max_hints: number | null }>(
        `/api/v1/items/${item.id}/hint`,
        { method: "POST", json: { hints_used: hints.length, item_id: item.id } },
      );
      setHints((h) => [...h, { no: out.hint_no, text: out.hint }]);
      setHintState({ available: out.hints_available, exhausted: out.exhausted, max: out.max_hints });
      onHint(item, out.hint_no);
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function openExample() {
    setBusy(true);
    try {
      // POST, not GET: opening a worked example is the event the fading rule measures.
      const out = await post<{ steps: { index: number; text: string; why?: string | null }[] }>(`items/${item.id}/worked-example`, { step: 1 });
      setExample(out.steps ?? []);
      onExample(item);
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function ask() {
    if (question.trim().length < 3) return;
    setAskBusy(true);
    setError(null);
    try {
      const out = await post<ExplainReply>("explain", { question, topic_id: entry.topic_id, item_id: item.id });
      setReply(out);
      setQuestion("");
      onExplanation(item, (out.answer ?? "").length);
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setAskBusy(false);
    }
  }

  const hintsAllowed = typeof pres.hints_allowed === "number" ? pres.hints_allowed : pres.hints_allowed === false ? 0 : 99;
  const outOfBudget = hints.length >= hintsAllowed || hintState.exhausted;
  const budgetNote =
    typeof pres.hints_allowed === "number" && pres.hints_allowed < (item.hint_count ?? 0)
      ? `capped at ${pres.hints_allowed} by an approved change`
      : hintState.max !== null
        ? `up to ${hintState.max}`
        : null;

  return (
    <Card
      tone={receipt ? (receipt.correct ? "good" : "warn") : "ask"}
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span className="normal-case">{item.stem}</span>
        </span>
      }
      right={
        <span className="flex items-center gap-2">
          {entry.position ? (
            <span>
              {entry.position}/{entry.of}
            </span>
          ) : null}
          <Tag>{item.kind}</Tag>
          {item.est_seconds ? <span title="this item's own estimate">~{item.est_seconds}s</span> : null}
        </span>
      }
    >
      {item.prompt_context ? <p className="mb-3 text-sm text-[var(--muted)] italic">{item.prompt_context}</p> : null}
      {item.visual_spec ? (
        <pre className="mb-3 overflow-x-auto rounded-md border border-[var(--line)] bg-black/25 p-3 text-xs" aria-label={String(item.visual_spec.alt ?? "diagram")}>
          {typeof item.visual_spec === "string" ? item.visual_spec : JSON.stringify(item.visual_spec, null, 1)}
        </pre>
      ) : null}

      {isChoice ? (
        <div className="space-y-2" role="radiogroup" aria-label="choices">
          {choices.map((c) => (
            <label
              key={c.index}
              className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm ${
                picked === c.index ? "border-[var(--accent)] bg-[var(--accent)]/10" : "border-[var(--line)] hover:border-[var(--accent)]/50"
              }`}
            >
              <input
                type="radio"
                name={`choice-${item.id}`}
                checked={picked === c.index}
                onChange={() => {
                  touch();
                  onChange(item, picked, c.index);
                  setPicked(c.index);
                }}
                className="mt-0.5 accent-[var(--accent)]"
              />
              <span>
                {c.text}
                {receipt && (receipt.flags ?? []).length === 0 ? null : null}
              </span>
            </label>
          ))}
        </div>
      ) : (
        <Field
          label={item.kind === "numeric" ? "Your answer" : item.kind === "teachback" ? "Explain it out loud, then write what you said" : "Your answer"}
          hint={
            item.kind === "numeric"
              ? "Fractions, decimals and simple expressions are all fine (2/3, 0.67, 12/4*3)."
              : wantTeachback
                ? "Ideas, not word count: the rubric looks for the pieces, and says which one is missing."
                : undefined
          }
        >
          {item.kind === "teachback" && !typed ? null : (
            <Input
              autoFocus
              value={typed}
              placeholder={item.kind === "numeric" ? "e.g. 5/6" : "e.g. 2/3"}
              onChange={(e) => {
                touch();
                onChange(item, typed, e.target.value);
                setTyped(e.target.value);
              }}
            />
          )}
        </Field>
      )}

      {wantTeachback && item.kind !== "teachback" ? (
        <Field label="Say it back in one or two sentences" hint="Optional here, and it does not change the mark: it is evidence that the idea is yours.">
          <Textarea
            value={teach}
            placeholder="Why does that work? What would break if you skipped it?"
            onChange={(e) => {
              touch();
              setTeach(e.target.value);
            }}
          />
        </Field>
      ) : null}

      {item.kind === "teachback" ? (
        <Field label="Your explanation" hint="Scored on the ideas in it. Spelling and length are not the mark.">
          <Textarea
            autoFocus
            value={typed}
            onChange={(e) => {
              touch();
              onChange(item, typed, e.target.value);
              setTyped(e.target.value);
            }}
            placeholder="Start from what the denominator means…"
          />
        </Field>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Btn kind="primary" onClick={submit} disabled={busy || !canSubmit}>
          {busy ? "…" : receipt ? "Try again" : "Check it"}
        </Btn>
        {!aidless ? (
          <Btn onClick={askForHint} disabled={busy || outOfBudget} title={budgetNote ?? undefined}>
            {outOfBudget ? "No hints left" : hints.length ? `Another hint (${hints.length + 1})` : "A hint"}
          </Btn>
        ) : null}
        {!aidless && (item.has_worked_example ?? true) ? (
          <Btn onClick={openExample} disabled={busy || example !== null}>
            {example ? "Example open" : "Worked example"}
          </Btn>
        ) : null}
        {!aidless ? (
          <Btn
            kind="ghost"
            onClick={async () => {
              setBusy(true);
              try {
                setRevealed(await api<Record<string, unknown>>(`/api/v1/items/${item.id}/reveal`, { csrf: true }));
              } catch (err) {
                setError(errMessage(err));
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy || revealed !== null}
            title="This one shows the answer. The endpoint needs the CSRF token even though it is a GET, because revealing an answer is not a read you would want forged by another site."
          >
            {revealed ? "Revealed" : "Reveal it"}
          </Btn>
        ) : null}
        <span className="ml-auto flex items-center gap-2 text-xs text-[var(--muted)]">
          {pres.max_words ? <span title="from your settings or an approved change">≤{pres.max_words} words</span> : null}
          {entry.reason ? <Tag tone="think">{entry.reason}</Tag> : null}
        </span>
      </div>

      {error ? (
        <p className="mt-3">
          <Notice tone="bad">{error}</Notice>
        </p>
      ) : null}

      {hints.length ? (
        <div className="mt-3 space-y-2">
          {hints.map((h) => (
            <p key={h.no} className="rounded-md border border-[var(--line)] bg-black/20 px-3 py-2 text-sm">
              <span className="mr-2 text-xs text-[var(--muted)]">hint {h.no}</span>
              {h.text}
            </p>
          ))}
          {outOfBudget ? (
            <p className="text-xs text-[var(--muted)]">
              That is the end of this item&apos;s hints{budgetNote ? ` (${budgetNote})` : ""}. The worked example or the reveal are
              the next things, and both are your call.
            </p>
          ) : null}
        </div>
      ) : null}

      {example ? (
        <Card title="Worked example" tone="muted" right={<span>opening this was recorded, on purpose</span>}>
          {example.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">This item has no written steps. Reveal it, or ask a question below.</p>
          ) : (
            <ol className="list-decimal space-y-2 pl-5 text-sm">
              {example.map((st) => (
                <li key={st.index}>
                  {st.text}
                  {st.why ? <span className="block text-xs text-[var(--muted)]">{st.why}</span> : null}
                </li>
              ))}
            </ol>
          )}
        </Card>
      ) : null}

      {revealed ? (
        <Card title="Revealed" tone="muted" right={<When ts={null} />}>
          <RevealBody body={revealed} />
        </Card>
      ) : null}

      {receipt ? <ReceiptView receipt={receipt} /> : null}

      {aidless ? null : (
        <div className="mt-4 border-t border-[var(--line)] pt-3">
          <div className="flex gap-2">
            <Input
              value={question}
              placeholder="Stuck on something about this? Ask in your own words."
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void ask();
              }}
            />
            <Btn onClick={ask} disabled={askBusy || question.trim().length < 3}>
              {askBusy ? "…" : "Ask"}
            </Btn>
          </div>
          {reply ? (
            <div className="prose-tutor mt-3 text-sm">
              <p>{reply.answer}</p>
              {reply.next_action ? <p className="text-[var(--accent)]">Next: {reply.next_action}</p> : null}
              <p className="text-xs text-[var(--muted)]">
                {reply.provider === "local" ? "from your course material, no model call" : `model: ${reply.provider}`}
                {reply.meta?.grounded ? " · grounded" : " · not grounded in your material"} ·{" "}
                {reply.privacy?.sent_to_model ? "your question was sent to the model" : "nothing left this machine"}
              </p>
              {reply.sources?.length ? (
                <ul className="mt-1 space-y-0.5 text-xs text-[var(--muted)]">
                  {reply.sources.map((src) => (
                    <li key={src.ref}>
                      {src.url ? (
                        <a className="underline" href={src.url} target="_blank" rel="noreferrer">
                          {src.title || src.ref}
                        </a>
                      ) : (
                        <span>{src.title || src.ref}</span>
                      )}
                      <span className="ml-1 opacity-70">({src.kind})</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      )}

      {entry.decision_trace ? (
        <div className="mt-3">
          <Trace title="why this question, now">
            <TraceBody trace={entry.decision_trace} />
          </Trace>
        </div>
      ) : null}
    </Card>
  );
}

function TraceBody({ trace }: { trace: NonNullable<PlanEntry["decision_trace"]> }) {
  if (Array.isArray(trace))
    return (
      <ul className="list-disc space-y-1 pl-4">
        {trace.map((t, i) => (
          <li key={i}>{String(t)}</li>
        ))}
      </ul>
    );
  const rows = trace as { chosen?: string[]; skipped?: { id: string; why: string }[]; why?: string };
  return (
    <div className="space-y-1">
      {rows.why ? <p>{rows.why}</p> : null}
      {rows.chosen?.length ? <p>considered: {rows.chosen.join(", ")}</p> : null}
      {rows.skipped?.length ? (
        <ul className="list-disc pl-4">
          {rows.skipped.map((s) => (
            <li key={s.id}>
              {s.id} — {s.why}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function RevealBody({ body }: { body: Record<string, unknown> }) {
  const choices = (body.choices ?? []) as { text: string; correct?: boolean; why_wrong?: string | null }[];
  const steps = (body.steps ?? []) as { index?: number; text: string; why?: string }[];
  return (
    <div className="space-y-2 text-sm">
      {body.answer !== null && body.answer !== undefined ? (
        <p>
          answer: <strong>{String(body.answer)}</strong>
        </p>
      ) : null}
      {choices.length ? (
        <ul className="space-y-1">
          {choices.map((c, i) => (
            <li key={i} className={c.correct ? "text-[var(--good)]" : "text-[var(--muted)]"}>
              {c.correct ? "✓" : "·"} {c.text}
              {c.why_wrong ? <span className="block pl-5 text-xs">{c.why_wrong}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {steps.length ? (
        <ol className="list-decimal space-y-1 pl-5">
          {steps.map((st, i) => (
            <li key={st.index ?? i}>
              {st.text}
              {st.why ? <span className="block text-xs text-[var(--muted)]">{st.why}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
      {body.why_it_is_ok_to_look ? <p className="text-xs text-[var(--muted)]">{String(body.why_it_is_ok_to_look)}</p> : null}
    </div>
  );
}

export function ReceiptView({ receipt }: { receipt: Receipt }) {
  const tb = receipt.teachback;
  return (
    <div className="mt-4 space-y-3">
      <Card tone={receipt.correct ? "good" : "warn"} title={receipt.correct ? "That's it" : "Not yet"} right={<Tag>{receipt.grading_method}</Tag>}>
        <p className="text-sm">{receipt.feedback}</p>
        <p className="mt-2 flex flex-wrap items-center gap-3 text-xs text-[var(--muted)]">
          <Band band={receipt.mastery.band} pKnow={receipt.mastery.p_know} />
          <span>
            {receipt.mastery.p_know_before !== undefined
              ? `${receipt.mastery.p_know_before.toFixed(2)} → ${receipt.mastery.p_know.toFixed(2)}`
              : `p ${receipt.mastery.p_know.toFixed(2)}`}
          </span>
          <span>
            {receipt.mastery.observations} observation{receipt.mastery.observations === 1 ? "" : "s"}
          </span>
          {receipt.mastery.due_at ? <span>review {new Date(receipt.mastery.due_at).toLocaleDateString()}</span> : null}
          {receipt.mastery.first_observation ? <Tag tone="think">first data point on this topic</Tag> : null}
        </p>
        {tb ? (
          <p className="mt-2 text-xs text-[var(--muted)]">
            teach-back: {(tb.matched ?? []).length} of {(tb.matched ?? []).length + (tb.missing ?? []).length} ideas
            {tb.missing?.length ? <> — missing <em>{tb.missing.join(", ")}</em></> : <> — all there</>}
            {tb.followup ? <>. {tb.followup}</> : null}. {tb.note}
          </p>
        ) : null}
        {receipt.proposals_expected ? (
          <p className="mt-2 text-xs text-[var(--think)]">
            Something in this session also looks like a pattern worth checking. Nothing has been changed — see the Changes
            screen when you finish.
          </p>
        ) : null}
      </Card>
    </div>
  );
}

function When({ ts }: { ts: string | null }) {
  return ts ? <span className="text-xs text-[var(--muted)]">{ts}</span> : null;
}

function errMessage(err: unknown): string {
  const e = err as { shown?: string; message?: string; status?: number };
  if (e?.shown) return e.shown;
  return e?.message ?? String(err);
}
