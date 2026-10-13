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
 * - the answer goes to the server in the POST body and nowhere else; the *draft* (what is in
 *   the box before it is submitted) is kept client-side so a re-render cannot eat it — see
 *   `@/lib/drafts`. It is cleared when the session ends, and it never holds a correct answer,
 *   because the client is never given one:
 * - worked examples are opened through the POST endpoint, because the *opening* is what
 *   the fading decision is measured on.
 */
import { useMemo, useRef, useState } from "react";

import { Band, Btn, Card, Field, Input, Notice, Receipt, Tag, Textarea, announce } from "./ui";
import { EvidenceBadge } from "./evidence";
import { TeachingPanel, type Teaching } from "./teaching";
import { api, post } from "@/lib/api";
import { loadDraft, saveDraft, type Draft } from "@/lib/drafts";
import type { ExplainReply, PlanEntry, Presentation, Receipt as ReceiptType } from "@/lib/types";
import { useRovingTabIndex } from "@/lib/a11y/useRovingTabIndex";

type Props = {
  entry: PlanEntry;
  presentation?: Presentation;
  /** What the page does with a submitted answer. Returns nothing: the receipt is shown here. */
  onSubmit: (itemId: string, answer: unknown, extra: { teachback_text?: string; attempt_no?: number }) => Promise<ReceiptType>;
  onInteract: (item: PlanEntry["item"]) => void;
  onChange: (item: PlanEntry["item"], from: unknown, to: unknown) => void;
  onHint: (item: PlanEntry["item"], hintNo: number) => void;
  onExample: (item: PlanEntry["item"]) => void;
  onExplanation: (item: PlanEntry["item"], chars: number) => void;
  /** The receipt this item already has, when the learner has come back to it. Without this,
   *  going back and forward again showed an answered question as if nothing had happened. */
  submitted?: ReceiptType | null;
  /** Diagnostic mode: no hints, no examples -- placement has to measure what they can do
   *  unaided, and a hint here would corrupt the estimate the whole plan is built on. */
  aidless?: boolean;
  attemptNo?: number;
};

export function Question({ entry, presentation, onSubmit, onInteract, onChange, onHint, onExample, onExplanation, submitted = null, aidless, attemptNo = 1 }: Props) {
  const item = entry.item;
  const pres = { ...(entry.presentation ?? {}), ...(presentation ?? {}) };
  const teaching = (pres as { teaching?: Teaching }).teaching ?? null;
  const choices = item.choices ?? [];
  const isChoice = item.kind === "mcq" && choices.length > 0;
  const wantTeachback = item.kind === "teachback" || Boolean(pres.require_teachback);

  // The learner's in-progress answer is loaded from the draft store, not invented here -- see
  // `@/lib/drafts` for why it does not live in this component. `initial` is read once, on
  // mount: re-reading it on every render would fight the keystrokes below.
  const [picked, setPicked] = useState<number | null>(() => loadDraft(item.id).picked);
  const [typed, setTyped] = useState(() => loadDraft(item.id).typed);
  const [teach, setTeach] = useState(() => loadDraft(item.id).teach);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ReceiptType | null>(submitted);
  const [hints, setHints] = useState<{ no: number; text: string }[]>([]);
  const [hintState, setHintState] = useState<{ available: number; exhausted: boolean; max: number | null }>({ available: item.hint_count ?? 0, exhausted: false, max: null });
  const [example, setExample] = useState<{ index: number; text: string; why?: string | null }[] | null>(null);
  const [revealed, setRevealed] = useState<Record<string, unknown> | null>(null);
  const [question, setQuestion] = useState("");
  const [reply, setReply] = useState<ExplainReply | null>(null);
  const [askBusy, setAskBusy] = useState(false);
  const startedItemIdRef = useRef<string | null>(null);
  const roving = useRovingTabIndex(choices.length, "vertical");

  // Deliberate fix: per-item state is reconciled synchronously during render via React's
  // derived-key state reset pattern, never in a passive useEffect (`numeric-state-reset` wiped
  // fast input inside the commit→effect window) and without mutating refs during render. The
  // answer itself comes from `@/lib/drafts` so returning to an item restores what was typed.
  const itemKey = `${item.id}|${item.hint_count ?? 0}`;
  const [shownKey, setShownKey] = useState(itemKey);
  if (shownKey !== itemKey) {
    const draft = loadDraft(item.id);
    setShownKey(itemKey);
    setPicked(draft.picked);
    setTyped(draft.typed);
    setTeach(draft.teach);
    setReceipt(submitted);
    setHints([]);
    setHintState({ available: item.hint_count ?? 0, exhausted: false, max: null });
    setExample(null);
    setRevealed(null);
    setQuestion("");
    setReply(null);
    setError(null);
    roving.setActiveIndex(0);
  }

  const answer = useMemo(() => {
    if (isChoice) return picked;
    const text = typed.trim();
    if (item.kind === "numeric") return text;
    return text;
  }, [isChoice, picked, typed, item.kind]);

  const canSubmit = isChoice ? picked !== null : item.kind === "worked_example" ? true : typed.trim().length > 0;

  /** Mirror every edit into the draft store. Cheap (a synchronous write) and it is the
   *  difference between "the box lost my answer" and "the box showed it again". */
  const keep = (patch: Partial<Draft>) => {
    saveDraft(item.id, patch);
  };

  const touch = () => {
    if (startedItemIdRef.current === item.id) return;
    startedItemIdRef.current = item.id;
    onInteract(item);
  };

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const out = await onSubmit(item.id, answer, wantTeachback ? { teachback_text: teach, attempt_no: attemptNo } : { attempt_no: attemptNo });
      setReceipt(out);
      // The draft is *not* cleared here. Submitting sends the answer to the server, and the
      // copy in the box is what makes returning to this question show the answer and the mark
      // rather than an empty form (`adapt:draft:` entries go when the session ends -- see
      // `useFlow.finish`).
      announce(out.correct ? "Answer recorded as correct." : "Answer recorded as incorrect.");
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
    <div className="page-practice" style={{ padding: "clamp(24px,4vw,48px) clamp(16px,4vw,32px) 40px" }}>
      {/* The title the learner chose, never the topic id. `top_a6062406` is a primary key;
          printing it at a learner is the app talking to itself out loud. When a pick arrives
          without a title (an older client, a mock fixture) the line simply omits it rather
          than falling back to the id. */}
      <div className="pz-label">
        {item.topic_title ? `${item.topic_title} · ` : ""}
        {entry.position && entry.of ? `question ${entry.position} of ${entry.of}` : "practice"}
      </div>

      {/* The method that was assigned for *this* attempt, before the question it teaches.
          Placement shows none of it: a hint during placement would measure the wrong thing. */}
      {!aidless && teaching ? (
        <div className="mb-5 max-w-[660px]">
          <p className="mb-2 text-[10.5px] uppercase tracking-[0.14em] text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
            {(pres as { method_label?: string }).method_label ?? "before the question"}
            {pres.max_words ? ` · ≤${pres.max_words} words a step` : ""}
          </p>
          <TeachingPanel teaching={teaching} />
        </div>
      ) : null}

      <h2 className="pz-q">{item.stem}</h2>
      <div className="pz-rule" aria-hidden="true" />

      {/* Placement says "no hints", so placement gets no prompt_context either. The text is
          a hint in all but name ("More digits does not mean a bigger decimal…"), and it was
          being printed above the very questions the screen promises are unaided. */}
      {!aidless && item.prompt_context ? (
        <p className="mb-3 max-w-[660px] text-sm italic text-[var(--muted)]">{item.prompt_context}</p>
      ) : null}
      {item.visual_spec ? (
        <pre className="mb-6 max-w-[660px] overflow-x-auto rounded-[2px] border border-[var(--line)] bg-[var(--panel)] p-3 text-xs" aria-label={String((item.visual_spec as Record<string, unknown>).alt ?? "diagram")}>
          {typeof item.visual_spec === "string" ? item.visual_spec : JSON.stringify(item.visual_spec, null, 1)}
        </pre>
      ) : null}

      {isChoice ? (
        <div className="pz-opts" role="radiogroup" aria-label={item.stem}>
          {choices.map((c, idx) => {
            const isSelected = picked === c.index;
            const isCorrect = receipt ? (receipt as unknown as { correct_choice?: number })?.correct_choice === c.index : false;
            const correctnessClass = receipt ? (isCorrect ? "correct" : isSelected ? "incorrect" : "") : "";
            return (
              <button
                key={c.index}
                type="button"
                role="radio"
                aria-checked={isSelected}
                aria-label={`${String.fromCharCode(65 + idx)}: ${c.text}`}
                ref={roving.registerRef(idx)}
                tabIndex={roving.getTabIndex(idx)}
                onKeyDown={(e) => roving.onKeyDown(e, idx)}
                onFocus={() => roving.setActiveIndex(idx)}
                disabled={!!receipt}
                onClick={() => {
                  if (receipt) return;
                  touch();
                  onChange(item, picked, c.index);
                  setPicked(c.index);
                  keep({ picked: c.index });
                  roving.setActiveIndex(idx);
                }}
                className={`option-card ${correctnessClass}`}
              >
                {/* The selection indicator is a shape, not a colour: aria-checked drives it,
                    so what a learner sees and what a screen reader announces cannot drift. */}
                <span className="radio-dot" aria-hidden="true" />
                <span className="option-letter">{String.fromCharCode(65 + idx)}</span>
                <span className="flex-1">{c.text}</span>
                {/* keep hidden radio for probe compatibility */}
                <input type="radio" name={`choice-${item.id}`} checked={isSelected} onChange={() => {}} className="sr-only" aria-hidden="true" tabIndex={-1} />
              </button>
            );
          })}
        </div>
      ) : (
        <div className="max-w-[520px]">
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
                  keep({ typed: e.target.value });
                }}
              />
            )}
          </Field>
        </div>
      )}

      {wantTeachback && item.kind !== "teachback" ? (
        <div className="mt-4 max-w-[520px]">
          <Field label="Say it back in one or two sentences" hint="Optional here, and it does not change the mark: it is evidence that the idea is yours.">
            <Textarea
              value={teach}
              placeholder="Why does that work? What would break if you skipped it?"
              onChange={(e) => {
                touch();
                setTeach(e.target.value);
                keep({ teach: e.target.value });
              }}
            />
          </Field>
        </div>
      ) : null}

      {item.kind === "teachback" ? (
        <div className="max-w-[520px]">
          <Field label="Your explanation" hint="Scored on the ideas in it. Spelling and length are not the mark.">
            <Textarea
              autoFocus
              value={typed}
              onChange={(e) => {
                touch();
                onChange(item, typed, e.target.value);
                setTyped(e.target.value);
                keep({ typed: e.target.value });
              }}
              placeholder="Start from what the denominator means…"
            />
          </Field>
        </div>
      ) : null}

      {/* What the learner can act on: which topic, why this question now (in plain words),
          and how the question was set up. No ids, no belief values, no metric names. */}
      <div className="marginalia" aria-label="about this question">
        {item.topic_title ? (
          <div className="tag-corner">
            topic<b>{item.topic_title}</b>
          </div>
        ) : null}
        {entry.reason_plain ? (
          <div className="tag-vert">
            why this question<b>{entry.reason_plain}</b>
          </div>
        ) : null}
        <div className="tag-vert">
          how it is set up
          <b>
            {[
              pres.max_words ? `≤${pres.max_words} words a step` : null,
              pres.hints_allowed === 0 ? "no hints" : pres.hints_allowed ? `up to ${pres.hints_allowed} hints` : null,
              (pres as { method_label?: string }).method_label ?? null,
            ]
              .filter(Boolean)
              .join(" · ") || "the usual way"}
          </b>
        </div>
      </div>

      <div className="pz-bottom">
        <div className="pz-due" style={{ fontFamily: "var(--font-mono)" }}>
          {entry.position ? `item ${entry.position} of ${entry.of ?? "?"}` : "practice"} · {item.est_seconds ? `~${item.est_seconds}s` : "untimed"}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={submit}
            disabled={busy || !canSubmit}
            className="btn-ctl btn-primary"
            title={!canSubmit ? "enter an answer first" : undefined}
            aria-describedby={!canSubmit ? "submit-hint" : undefined}
          >
            {busy ? "…" : receipt ? "Try again" : "Check it"}
          </button>
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
        </div>
      </div>
      {!canSubmit ? <span id="submit-hint" className="sr-only">Enter an answer to enable checking</span> : null}

      {error ? (
        <div className="mt-4 max-w-[660px]">
          <Notice tone="bad">{error}</Notice>
        </div>
      ) : null}

      {hints.length ? (
        <div className="mt-4 max-w-[620px] space-y-2">
          {hints.map((h) => (
            <p key={h.no} className="rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm">
              <span className="mr-2 text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>hint {h.no}</span>
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
        <div className="mt-6 max-w-[660px]">
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
        </div>
      ) : null}

      {revealed ? (
        <div className="mt-4 max-w-[660px]">
          <Card title="Revealed" tone="muted" right={<span> </span>}>
            <RevealBody body={revealed} />
          </Card>
        </div>
      ) : null}

      {receipt ? (
        <div className="mt-6 max-w-[660px]">
          <Receipt tone={receipt.correct ? "good" : "bad"}>
            <ReceiptView receipt={receipt} />
          </Receipt>
        </div>
      ) : null}

      {aidless ? null : (
        <div className="mt-6 max-w-[660px] border-t border-[var(--line)] pt-4">
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
              <p className="text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
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

export function ReceiptView({ receipt }: { receipt: ReceiptType }) {
  const tb = receipt.teachback;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold" style={{ fontFamily: "var(--font-serif)" }}>{receipt.correct ? "That's it" : "Not yet"}</h3>
        <Tag>{receipt.grading_method}</Tag>
      </div>
      <p className="text-sm leading-relaxed">{receipt.feedback}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-[var(--muted)]" style={{ fontFamily: "var(--font-mono)" }}>
        {/* The interval the server computed, not `undefined`: this row used to print a bare
            estimate next to an observation count, which reads as a measurement. It now shows
            the range the evidence supports and, while there are fewer than eight answers, says
            so in words next to the number. */}
        <Band band={receipt.mastery.band} pKnow={receipt.mastery.p_know} ci={receipt.mastery.ci ?? undefined} />
        <span>
          {receipt.mastery.p_know_before !== undefined
            ? `${receipt.mastery.p_know_before.toFixed(2)} → ${receipt.mastery.p_know.toFixed(2)}`
            : `p ${receipt.mastery.p_know.toFixed(2)}`}
        </span>
        <EvidenceBadge n={receipt.mastery.observations} />
        {receipt.mastery.due_at ? <span>review {new Date(receipt.mastery.due_at).toLocaleDateString()}</span> : null}
        {receipt.mastery.first_observation ? <Tag tone="think">first data point on this topic</Tag> : null}
      </div>
      {tb ? (
        <p className="mt-2 text-xs text-[var(--muted)]">
          teach back: {(tb.matched ?? []).length} of {(tb.matched ?? []).length + (tb.missing ?? []).length} ideas
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
    </div>
  );
}

function errMessage(err: unknown): string {
  const e = err as { shown?: string; message?: string; status?: number };
  if (e?.shown) return e.shown;
  return e?.message ?? String(err);
}
