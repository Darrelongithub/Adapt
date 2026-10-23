"use client";

/**
 * The mind baseline, as screens (round brief section 5).
 *
 * One page, because the flow is one sitting: the server answers "what now?" and this renders it,
 * so there is no cursor here that can disagree with the trial log. A learner who closes the tab
 * and comes back gets whatever the log says is next, and the header says so in words.
 *
 * Three rules the screens keep, and why:
 *
 * * **Stopping is normal.** "Stop here" is on every screen and goes straight to the snapshot; a
 *   partial baseline stores what was measured and calls the rest "not measured" (never zero).
 * * **The clock is information, not pressure.** A visible countdown appears only when the learner
 *   said timers help. Latency is measured either way, quietly, and the questions screen says so.
 * * **Nothing here grades in place of the server.** The vault checks taps against the sequence the
 *   learner saw (so a wrong tap shows immediately), but the score, the factor, the lever and the
 *   coins are the server's verdict -- this file never invents a result.
 *
 * Keyboard: buttons are real buttons; the vault grid is a roving-tabindex grid (arrows move,
 * Enter/Space taps); every text input is a labelled input. The probe asserts the keyboard path.
 */

import { GameCards } from "@/components/icons/GameCards";
import { PageArt } from "@/components/shell/PageArt";
import { friendlyLabel } from "@/lib/labels";
import { useOptionalVoice } from "@/components/voice/context";
import { Icon, gameIcon } from "@/components/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import "./baseline.css";

import { Btn, Card, Notice, Tag, Trace, announce } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useRovingTabIndex } from "@/lib/a11y/useRovingTabIndex";
import {
  answer_questions,
  finish_baseline,
  format_range,
  load_history,
  load_next_day,
  load_baseline,
  load_snapshot,
  offers_coins,
  order_factors,
  parse_items,
  pick_format,
  seconds_left,
  source_label,
  start_baseline,
  step_label,
  submit_trial,
  timers_visible,
  timers_visible_in,
  vault_expected,
  vault_prefix_ok,
  type BaselineHistory,
  type BaselineSnapshot,
  type BaselineTask,
  type BaselineView,
  type GameResult,
  type NextDay,
  type Scored,
} from "@/lib/baseline";

export default function Baseline() {
  const [view, setView] = useState<BaselineView | null>(null);
  const [snap, setSnap] = useState<BaselineSnapshot | null>(null);
  const [history, setHistory] = useState<BaselineHistory | null>(null);
  /** Set only when a learner comes back after a finished sitting: their results, not a restart. */
  const [previous, setPrevious] = useState<BaselineHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wager, setWager] = useState<number | null>(null);
  const [resumed, setResumed] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);
  // Yesterday's codeword check, if the last finished sitting has one waiting. It is loaded alongside
  // the run, never instead of it: a learner who came back gets the new baseline as well.
  const [pending, setPending] = useState<NextDay | null>(null);
  const [pending_hidden, setPendingHidden] = useState(false);
  // The verdict on the answer just sent, shown on the next screen so every game says right or wrong.
  const [last, setLast] = useState<{ game: string; scored: Scored } | null>(null);
  const voice = useOptionalVoice();

  // When the current task appeared. Latency is measured here because only the browser knows it;
  // the server clamps anything absurd, and the questions screen tells the learner it happens.
  const shown_at = useRef<number>(0);
  const opened = useRef(false);
  // One submission at a time. A double tap used to send the same trial twice; the server's
  // idempotency rule caught the row, but the second reply could be a 422 that looked like a freeze.
  const sending = useRef(false);

  const refresh_history = useCallback(async () => {
    try {
      setHistory(await load_history());
    } catch {
      /* the panel is a view of the record, never a reason to block a game */
    }
  }, []);

  const advance = useCallback((next: BaselineView) => {
    shown_at.current = Date.now();
    setWager(null);
    setView(next);
  }, []);

  useEffect(() => {
    if (voice?.enabled && view?.note) void voice.speak(view.note, { interrupt: true });
  }, [voice?.enabled, view?.run_id, view?.task?.id]);

  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    (async () => {
      try {
        const params = new URLSearchParams(window.location.search);
        const run = params.get("run");
        if (!run) {
          // What has already been stored decides the first screen. A learner whose last sitting was
          // sealed sees their results (and can play again); one who never played, or who left a run
          // open, goes straight into the flow. `start_baseline()` used to be called unconditionally,
          // which opened a fresh run and hid a finished sitting behind a new format pick.
          const hist = await load_history().catch(() => null);
          if (hist) setHistory(hist);
          const finished_before = Boolean(hist?.runs.some((r) => r.status === "complete"));
          if (hist && !hist.has_open_run && finished_before && params.get("again") !== "1") {
            setPrevious(hist);
            setSnap(await load_snapshot().catch(() => null));
            return;
          }
        }
        const first = run ? await load_baseline(run) : await start_baseline();
        // A finished sitting opened by id is not a sitting in progress: it says "what we noticed",
        // it does not tell the learner they are picking up where they left off.
        if (first.kind !== "format" && first.kind !== "questions" && first.kind !== "snapshot") {
          setResumed(
            first.completion?.complete != null && first.completion?.total != null
              ? `Picking up where you left off: ${friendlyLabel(first.stage_title) ?? "your mind check"}, ${first.completion.complete} of ${first.completion.total} steps already done.`
              : `Picking up where you left off: ${friendlyLabel(first.stage_title) ?? "your mind check"}.`,
          );
        }
        advance(first);
        if (first.kind === "snapshot") {
          setFinished(true);
          setSnap(await load_snapshot(first.run_id));
        }
        void refresh_history();
      } catch (err) {
        setError(err instanceof ApiError ? err.shown : (err as Error).message);
      }
    })();
    // The return visit's check is the last *finished* sitting's: never the sitting that is open now.
    load_next_day(null, true)
      .then(setPending)
      .catch(() => setPending(null));
  }, [advance, refresh_history]);

  const play_again = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const first = await start_baseline();
      setPrevious(null);
      setFinished(false);
      setSnap(null);
      setResumed(null);
      // The verdict strip belongs to the last answer of the last sitting; a new sitting starts
      // without one.
      setLast(null);
      advance(first);
      void refresh_history();
    } catch (err) {
      setError(err instanceof ApiError ? err.shown : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [busy, advance, refresh_history]);

  const stop = useCallback(async () => {
    if (!view || busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await finish_baseline(view.run_id);
      setSnap(out);
      setFinished(true);
      announce("Baseline stopped. What was measured is saved.");
      void refresh_history();
    } catch (err) {
      setError(err instanceof ApiError ? err.shown : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [view, busy, refresh_history]);

  const send = useCallback(
    async (task: BaselineTask, response: Record<string, unknown>): Promise<Scored | null> => {
      if (!view || sending.current) return null;
      sending.current = true;
      setBusy(true);
      setError(null);
      try {
        const next = await submit_trial(view.run_id, {
          task_id: task.id,
          response,
          latency_ms: Math.max(0, Date.now() - shown_at.current),
          wager: offers_coins(view) ? wager : null,
        });
        const scored = (next.scored ?? {}) as Scored;
        setLast({ game: task.game, scored });
        advance(next);
        if (next.duplicate) announce("That answer was already recorded; showing you the next step.");
        else if (scored.correct === true) announce(scored.points ? `Correct. ${scored.points} points.` : "Correct.");
        else if (scored.correct === false) announce("Not this time.");
        if (next.kind === "snapshot") {
          setFinished(true);
          setSnap(await load_snapshot(next.run_id));
        }
        // The history panel is re-read when a game ends, not on every answer: a quick lap is
        // dozens of taps, and a full history read per tap is a request the learner waits behind.
        if (next.task?.game !== "quick") void refresh_history();
        return scored;
      } catch (err) {
        const message = err instanceof ApiError ? err.shown : (err as Error).message;
        // The clock can close a lap between the tap and the request. That answer belongs to a screen
        // that no longer exists; the right response is to show where the sitting actually is, not
        // to put an error over a finished game.
        const detail_message =
          err instanceof ApiError && err.detail && typeof err.detail === "object"
            ? String((err.detail as Record<string, unknown>).message ?? "")
            : "";
        if (err instanceof ApiError && err.status === 422 && /not the task on offer/i.test(`${message} ${detail_message}`)) {
          try {
            advance(await load_baseline(view.run_id));
          } catch {
            setError(message);
          }
          return null;
        }
        setError(message);
        return null;
      } finally {
        sending.current = false;
        setBusy(false);
      }
    },
    [view, wager, advance, refresh_history],
  );

  const answer_all = useCallback(
    async (answers: Record<string, unknown>) => {
      if (!view || busy) return;
      setBusy(true);
      setError(null);
      try {
        advance(await answer_questions(view.run_id, answers));
      } catch (err) {
        setError(err instanceof ApiError ? err.shown : (err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [view, busy, advance],
  );

  const choose_format = useCallback(
    async (value: string) => {
      if (!view || busy) return;
      setBusy(true);
      setError(null);
      try {
        advance(await pick_format(view.run_id, value));
      } catch (err) {
        setError(err instanceof ApiError ? err.shown : (err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [view, busy, advance],
  );

  if (error && !view && !previous) {
    return (
      <section className="wrap baseline-page">
        <Card title="The baseline could not open">
          <Notice tone="bad">{error}</Notice>
          <p className="muted">Nothing was recorded. If you are signed out, the front door will ask you in.</p>
        </Card>
      </section>
    );
  }

  if (previous && !view) {
    return (
      <section className="wrap baseline-page">
        <WelcomeBack history={previous} snap={snap} busy={busy} onPlayAgain={play_again} error={error} />
      </section>
    );
  }

  if (!view) {
    return (
      <section className="wrap baseline-page" aria-busy="true">
        <Card title="Opening the baseline">
          <p className="muted">One sitting of short games. Nothing here is a grade.</p>
        </Card>
      </section>
    );
  }

  const task = view.task;
  const show_snapshot = finished || view.kind === "snapshot";
  const progress = Math.round((view.progress ?? 0) * 100);

  return (
    <section className="wrap baseline-page">
      <header className="baseline-head baseline-hero">
        <PageArt name="brain" className="baseline-brain-art" />
        <div>
          <p className="muted small" style={{ margin: 0 }}>
            {show_snapshot ? "What we noticed" : step_label(view.stage) || "The baseline"} · {progress}% through
          </p>
          <h1 style={{ margin: "2px 0 0" }}>{show_snapshot ? "What we noticed" : friendlyLabel(view.stage_title) ?? "Your mind check"}</h1>
        </div>
        {!show_snapshot ? (
          <div className="baseline-actions">
            {task && offers_coins(view) ? (
              <Coins value={wager} onChange={setWager} />
            ) : null}
            <Btn kind="ghost" onClick={stop} disabled={busy} title="finish now and keep what was measured">
              Stop here
            </Btn>
          </div>
        ) : null}
      </header>

      <div className="baseline-bar" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100} aria-label="baseline progress">
        <span style={{ width: `${Math.max(2, progress)}%` }} />
      </div>

      {resumed && !show_snapshot ? <Notice tone="info">{resumed}</Notice> : null}
      {error ? <Notice tone="bad">{error}</Notice> : null}
      {view.note && !show_snapshot && !["format", "questions"].includes(view.kind) ? <p className="muted small">{view.note}</p> : null}

      {pending && pending.tasks.length > 0 && pending.run_id && pending.run_id !== view.run_id && !pending_hidden && !show_snapshot ? (
        <ReturnCheck
          check={pending}
          timers={timers_visible(view)}
          onChanged={setPending}
          onHide={() => setPendingHidden(true)}
        />
      ) : null}

      {show_snapshot ? (
        <><SnapshotScreen snap={snap} view={view} history={history} onChanged={async () => { if (snap) setSnap(await load_snapshot(snap.run_id)); }} /><div className="mt-5 flex flex-wrap gap-3"><Btn href="/" kind="primary">Go to your dashboard</Btn><Btn href="/journey?step=subject">Choose a subject</Btn><Btn kind="ghost" onClick={play_again} disabled={busy}>{busy ? "Opening…" : "Play the games again"}</Btn></div></>
      ) : view.kind === "format" ? (
        <FormatScreen view={view} onPick={choose_format} busy={busy} />
      ) : view.kind === "questions" ? (
        <QuestionsScreen view={view} onDone={answer_all} busy={busy} />
      ) : task ? (
        <TaskScreen task={task} view={view} busy={busy} last={last} onSend={send} />
      ) : (
        <Card title="Nothing left to show">
          <p>
            This run has no more tasks. Press <strong>Stop here</strong> to seal it, and the snapshot will
            show what was measured.
          </p>
        </Card>
      )}

      <p className="muted small">
        Stop anytime. Unplayed games stay unmeasured.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------- format ---- */

/* Tiny inline icons for the format pick — three small glyphs that read at a glance. */
function FormatIcon({ value }: { value: string }) {
  if (value === "diagram") {
    return (
      <svg viewBox="0 0 36 36" aria-hidden="true" width="32" height="32">
        <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
          <path d="M4 28 L4 6" />
          <path d="M4 28 L32 28" />
          <rect x="9" y="20" width="3.5" height="8" />
          <rect x="15" y="14" width="3.5" height="14" />
          <rect x="21" y="10" width="3.5" height="18" />
          <rect x="27" y="6" width="3.5" height="22" />
        </g>
      </svg>
    );
  }
  if (value === "text") {
    return (
      <svg viewBox="0 0 36 36" aria-hidden="true" width="32" height="32">
        <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
          <line x1="6" y1="10" x2="30" y2="10" />
          <line x1="6" y1="16" x2="30" y2="16" />
          <line x1="6" y1="22" x2="22" y2="22" />
          <line x1="6" y1="28" x2="26" y2="28" />
        </g>
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 36 36" aria-hidden="true" width="32" height="32">
      <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <circle cx="6" cy="8" r="2" fill="currentColor" />
        <line x1="11" y1="8" x2="30" y2="8" />
        <circle cx="6" cy="18" r="2" fill="currentColor" />
        <line x1="11" y1="18" x2="30" y2="18" />
        <circle cx="6" cy="28" r="2" fill="currentColor" />
        <line x1="11" y1="28" x2="24" y2="28" />
      </g>
    </svg>
  );
}

function FormatScreen({ view, onPick, busy }: { view: BaselineView; onPick: (v: string) => void; busy: boolean }) {
  const [picked, setPicked] = useState<string | null>(view.format_pick);
  const opener = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    opener.current?.focus();
  }, []);
  return (
    <Card title="How would you like to meet a new idea?">
      <p>One preference, not a label. <a href="#why-format" className="link-inline">why we ask</a></p>
      <div className="r3-choices tall-grid" role="group" aria-label="format preference">
        {view.format_options.map((opt, i) => (
          <button
            key={opt.value}
            ref={i === 0 ? opener : undefined}
            type="button"
            aria-pressed={picked === opt.value}
            data-testid="format-option"
            data-format={opt.value}
            className={`choice-card tall${picked === opt.value ? " is-selected" : ""}`}
            onClick={() => {
              setPicked(opt.value);
              onPick(opt.value);
            }}
            disabled={busy}
          >
            <span className="choice-icon" aria-hidden="true">
              <FormatIcon value={opt.value} />
            </span>
            <span className="choice-body">
              <span className="choice-title">{opt.label}</span>
              <span className="choice-hint">{opt.hint}</span>
            </span>
          </button>
        ))}
      </div>
      <details id="why-format" className="r3-question-why-tap">
        <summary>why we ask</summary>
        <p className="r3-question-why">
          This is a preference, not a diagnosis. It is where the app starts; later the app compares it with
          what actually worked, so a single early guess is never treated as a label about how you learn.
        </p>
      </details>
      <GameCards />
    </Card>
  );
}

/* ---------------------------------------------------------------- questions ---- */

function QuestionsScreen({ view, onDone, busy }: { view: BaselineView; onDone: (a: Record<string, unknown>) => void; busy: boolean }) {
  // Round 3 (Track R): the wall of 13 questions became a card stack — one at a
  // time, a thin progress bar, and a back button.  The answers are still sent
  // as a single payload so the server's contract is unchanged, but the
  // learner's experience is one question on a screen.
  const [answers, setAnswers] = useState<Record<string, unknown>>(() => ({ ...(view.answers ?? {}) }));
  const [index, setIndex] = useState(0);
  const questions = view.questions ?? [];
  const current = questions[index];
  const total = questions.length;
  const last = index === total - 1;

  const set = (key: string, value: unknown) => setAnswers((a) => ({ ...a, [key]: value }));
  const toggle = (key: string, value: unknown) =>
    setAnswers((a) => {
      const list = Array.isArray(a[key]) ? [...(a[key] as unknown[])] : [];
      const at = list.indexOf(value);
      if (at === -1) list.push(value);
      else list.splice(at, 1);
      return { ...a, [key]: list };
    });

  if (!current) {
    return (
      <Card title="How you work">
        <p className="muted small">No questions in this run. Continue to the games.</p>
        <div className="row">
          <Btn kind="primary" onClick={() => onDone(answers)} disabled={busy}>
            Continue
          </Btn>
        </div>
      </Card>
    );
  }

  const value = answers[current.key];
  const multi = Boolean(current.multi);
  const options = current.options ?? [];
  const hasAnswer = multi
    ? Array.isArray(value) && value.length > 0
    : current.free_text
      ? typeof value === "string" && value.trim().length > 0
      : value !== undefined && value !== null;
  const progress = Math.round(((index + 1) / Math.max(1, total)) * 100);

  return (
    <Card title="How you work" right={<span className="muted small">Question {index + 1} of {total}</span>}>
      <div className="r3-question-stack">
        <div
          className="r3-question-progress"
          role="progressbar"
          aria-valuenow={progress}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="questions progress"
        >
          <span style={{ width: `${Math.max(2, progress)}%` }} />
        </div>
        <h2 className="r3-question-prompt" data-testid="r3-question-prompt">
          {current.question}
          {current.free_text ? <span className="muted small"> (optional)</span> : null}
        </h2>
        {current.free_text ? (
          <input
            className="input"
            aria-label={current.question}
            value={typeof value === "string" ? value : ""}
            disabled={busy}
            onChange={(e) => set(current.key, e.target.value)}
          />
        ) : (
          <div className="r3-choices pill-grid" role="group" aria-label={current.question}>
            {options.map((opt) => {
              const on = multi ? Array.isArray(value) && value.includes(opt) : value === opt;
              return (
                <button
                  key={String(opt)}
                  type="button"
                  data-testid="r3-question-option"
                  aria-pressed={on}
                  className={`choice-card${on ? " is-selected" : ""}`}
                  disabled={busy}
                  onClick={() => (multi ? toggle(current.key, opt) : set(current.key, opt))}
                >
                  <span className="choice-body">
                    <span className="choice-title">{String(opt)}</span>
                  </span>
                </button>
              );
            })}
          </div>
        )}
        {current.why ? (
          <details className="r3-question-why-tap">
            <summary>why we ask</summary>
            <p className="r3-question-why">Asked because it {current.why}.</p>
          </details>
        ) : null}
        <div className="r3-question-foot">
          <div className="flex flex-wrap gap-2">
            <Btn kind="ghost" onClick={() => setIndex((i) => Math.max(0, i - 1))} disabled={busy || index === 0}>
              Back
            </Btn>
            {!last ? (
              <Btn
                kind="primary"
                onClick={() => setIndex((i) => Math.min(total - 1, i + 1))}
                disabled={busy}
              >
                Next
              </Btn>
            ) : (
              <Btn kind="primary" onClick={() => onDone(answers)} disabled={busy}>
                Finish
              </Btn>
            )}
          </div>
          <Btn kind="ghost" onClick={() => onDone({})} disabled={busy} title="skip these and go straight to the games">
            Skip the questions
          </Btn>
        </div>
      </div>
      <p className="muted small">
        Skipped questions are recorded as skipped, not as zero.
      </p>
    </Card>
  );
}

/* ------------------------------------------------------------------- coins ---- */

function Coins({ value, onChange }: { value: number | null; onChange: (v: number | null) => void }) {
  return (
    <div className="baseline-coins" role="group" aria-label="how confident are you, in coins">
      <span className="muted small">Coins</span>
      {[0, 1, 2, 3].map((n) => (
        <button
          key={n}
          type="button"
          className={`coin${value === n ? " on" : ""}`}
          aria-pressed={value === n}
          title={n === 0 ? "no coins: a guess" : `${n} coin${n > 1 ? "s" : ""} says you are sure`}
          onClick={() => onChange(value === n ? null : n)}
        >
          {n}
        </button>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------- tasks ---- */

/** What each game is called on screen. The server uses the same names in the history view. */
const GAME_NAMES: Record<string, string> = {
  codewords_study: "Word memory",
  codewords_cued: "Word recall",
  codewords_next_day: "Codeword check",
  quick: "Quick taps",
  vault: "Vault lights",
  shopkeeper: "Shopkeeper",
  machine: "Crack the machine",
  locked_box: "The locked box",
  trap: "Two questions to think about",
  codewords_delayed: "Delayed recall",
};

function game_name(game: string): string {
  return GAME_NAMES[game] ?? friendlyLabel(game) ?? "Last answer";
}

/** A verdict the next screen shows, so a tap never vanishes without saying right or wrong. */
function ResultStrip({ last }: { last: { game: string; scored: Scored } | null }) {
  if (!last) return null;
  const s = last.scored;
  const who = <span className="result-game">{game_name(last.game)}:</span>;
  if (s.action === "studied") {
    return (
      <p className="result-strip neutral" role="status">
        {who} looking time recorded{s.seconds_seen ? ` — ${s.seconds_seen}s with the pairs` : ""}.
      </p>
    );
  }
  if (s.action === "timeout" || s.lap_over) {
    return (
      <p className="result-strip neutral" role="status">
        {who} that lap is over. Everything you tapped is stored.
      </p>
    );
  }
  if (s.action === "predict") {
    return (
      <p className="result-strip neutral" role="status">
        {who} guess saved{s.predicted != null ? `: ${String(s.predicted)} of ${String(s.of ?? 8)}` : ""}.
      </p>
    );
  }
  if (s.action === "hint") {
    return (
      <p className="result-strip neutral" role="status">
        {who} hint bought — it is in the shop list below.
      </p>
    );
  }
  if (s.action === "walk_away") {
    return (
      <p className="result-strip neutral" role="status">
        {who} walking away is recorded as what it is. Next game.
      </p>
    );
  }
  if (s.action === "probe" && s.error) {
    return (
      <p className="result-strip bad" role="status">
        {who} that probe was not a number, so it was not recorded.
      </p>
    );
  }
  if (s.action === "probe" && s.output != null) {
    return (
      <p className="result-strip neutral" role="status">
        {who} {String(s.value)} → <strong>{String(s.output)}</strong>
        {s.falsifying ? " · that was a boundary probe" : ""}
      </p>
    );
  }
  if (s.correct === true) {
    return (
      <p className="result-strip good" role="status">
        {who} <span aria-hidden="true">✓</span> Correct
        {s.points ? ` · +${s.points} points` : ""}
        {s.streak && s.streak > 1 ? ` · ${s.streak} in a row` : ""}
        {s.explain ? <span className="result-why"> {String(s.explain)}</span> : null}
      </p>
    );
  }
  if (s.correct === false) {
    return (
      <p className="result-strip bad" role="status">
        {who} <span aria-hidden="true">✗</span> Not quite
        {s.prefix_len != null && Number(s.prefix_len) > 0 ? (
          <span className="result-why"> — the first {String(s.prefix_len)} were in place</span>
        ) : null}
        {s.expected && s.expected !== "" ? (
          <span className="result-why"> — the answer was <strong>{String(s.expected)}</strong></span>
        ) : null}
        {s.explain ? <span className="result-why"> {String(s.explain)}</span> : null}
      </p>
    );
  }
  return null;
}

/** The frame every game sits in: the tag, the lap/level chips, the prompt, the last verdict. */
function TaskScreen({
  task,
  view,
  busy,
  last,
  onSend,
}: {
  task: BaselineTask;
  view: BaselineView;
  busy: boolean;
  last: { game: string; scored: Scored } | null;
  onSend: (task: BaselineTask, response: Record<string, unknown>) => Promise<Scored | null>;
}) {
  const timers = timers_visible(view);
  const expected_len = ((task.sequence as number[]) ?? []).length;
  return (
    <div className={`baseline-task game-stage game-${task.game}`} data-game={task.game}>
      <div className="baseline-task-head game-top">
        {/* "Scored" is the server's word, and it is not the same as "has a practice flag": a
            `phase: "show"` task (the study screen) is looking time, and calling it "measured"
            would claim a trial that was never scored. */}
        <Tag tone="accent">{task.phase === "show" ? "looking time — not scored" : task.practice ? "practice — not scored" : "measured"}</Tag>
        {task.lap_title ? <span className="game-chip">{task.lap_title}</span> : null}
        {task.level ? (
          <span className="game-chip level">
            level {Number(task.level)} · {String(task.level_name ?? "")}
          </span>
        ) : null}
        {task.game === "quick" ? <span className="game-chip ghost">wrong taps cost 2s</span> : null}
        {task.game === "vault" ? <span className="game-chip">{String(task.length ?? expected_len)} tiles</span> : null}
        {task.game === "vault" ? (
          <span className="game-chip ghost">
            {task.answer_direction === "backward" ? "reverse order" : "same order"}
          </span>
        ) : null}
      </div>
      {task.prompt ? (
        <h2 className="baseline-prompt game-prompt">
          <Icon name={gameIcon(task.game)} />
          {friendlyLabel(task.prompt) ?? "A short mind game"}
        </h2>
      ) : null}
      <ResultStrip last={last} />
      {task.game === "codewords_study" ? <Study task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "codewords_cued" || task.game === "codewords_next_day" ? (
        <FreeText task={task} busy={busy} onSend={onSend} label="Which one went with it? Type the place (or the code word)." />
      ) : null}
      {task.game === "quick" ? <Quick task={task} busy={busy} timers={timers} onSend={onSend} /> : null}
      {task.game === "trap" ? <Trap task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "vault" ? <Vault task={task} busy={busy} timers={timers} onSend={onSend} /> : null}
      {task.game === "shopkeeper" ? <Shopkeeper task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "machine" ? <Machine task={task} view={view} busy={busy} onSend={onSend} /> : null}
      {task.game === "locked_box" ? <LockedBox task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "codewords_delayed" ? <Delayed task={task} busy={busy} onSend={onSend} /> : null}
      <p className="muted small game-help">
        {task.game === "vault"
          ? "Arrow keys move, Enter taps, or press 1 to 9 for a tile. The tiles you saw are flashed once, then it is your turn."
          : task.game === "quick"
            ? "Tap an answer. The next one arrives straight away, and the ladder gets one rung harder as the lap goes on."
            : "Tab moves between the controls; Enter or Space presses the one in focus."}
      </p>
    </div>
  );
}

function FreeText({
  task,
  busy,
  onSend,
  label,
  placeholder,
}: {
  task: BaselineTask;
  busy: boolean;
  onSend: (task: BaselineTask, response: Record<string, unknown>) => Promise<Scored | null>;
  label: string;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const box = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    box.current?.focus();
    setText("");
  }, [task.id]);
  const cue = task.cue ? <p className="baseline-cue cue-card">{String(task.cue)}</p> : null;
  return (
    <form
      className="free-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) void onSend(task, { text: text.trim() });
      }}
    >
      {cue}
      <label className="baseline-label" htmlFor="baseline-free">
        {label}
      </label>
      <input
        id="baseline-free"
        ref={box}
        className="input game-input"
        autoComplete="off"
        value={text}
        placeholder={placeholder ?? "type it"}
        onChange={(e) => setText(e.target.value)}
        disabled={busy}
      />
      <div className="row">
        <Btn type="submit" kind="primary" disabled={busy || !text.trim()}>
          Answer
        </Btn>
      </div>
    </form>
  );
}

/** "Quick taps": a lap with a clock, a climbing ladder, points and a streak.
 *
 * The lap is the game: thirty seconds, a new tap every second or so, and a rung added every three
 * answers. Two rules keep it playable:
 *
 * * **A tap is never dropped silently.** The tap you made is drawn immediately, the HUD says the
 *   answer is on its way, and if the request fails the exact tap stays on screen with a button that
 *   sends it again under the same answer.
 * * **The clock cannot be cheated and cannot be lost.** It is restarted once per lap, not per item,
 *   a wrong tap moves the start back by the server's stated penalty, and the closing `timeout` is
 *   retried while this task is still the one on offer -- including when it fired during a tap.
 */
function Quick({
  task,
  busy,
  timers,
  onSend,
}: {
  task: BaselineTask;
  busy: boolean;
  timers: boolean;
  onSend: (task: BaselineTask, response: Record<string, unknown>) => Promise<Scored | null>;
}) {
  const stage = String(task.stage ?? task.id.split("_").slice(0, 2).join("_"));
  const budget = Number(task.lap_seconds ?? 30);
  const [started, setStarted] = useState(() => Date.now());
  const [left, set_left] = useState(budget);
  const [score, setScore] = useState(0);
  const [streak, setStreak] = useState(0);
  const [tap, setTap] = useState<{ id: string; value: string; correct: boolean | null } | null>(null);
  // A tap whose request did not land. It stays on screen, with a button, until it does.
  const [failed, setFailed] = useState<{ id: string; value: string; elapsed: number } | null>(null);
  const done = useRef(false);
  // The timer effect outlives a render, so it reads the task that is on offer *now* from here.
  const current = useRef(task);
  current.current = task;

  // A new lap gets a new clock. The same lap's next item keeps the clock running.
  useEffect(() => {
    setStarted(Date.now());
    set_left(budget);
    setScore(0);
    setStreak(0);
    setTap(null);
    setFailed(null);
    done.current = false;
  }, [stage, budget]);

  // A tap belongs to one task. This also clears a stale one if the server re-serves the same
  // screen, which used to leave the next item's buttons disabled -- the "it won't let me click" bug.
  useEffect(() => {
    setTap((t) => (t && t.id !== task.id ? null : t));
    setFailed((f) => (f && f.id !== task.id ? null : f));
  }, [task.id]);

  const end_lap = useCallback(
    async (ending: BaselineTask) => {
      for (let attempt = 0; attempt < 3; attempt++) {
        // A tap may be in flight when the whistle goes; it is retried, not dropped. If the reply
        // already moved the sitting on, this task is no longer the one on offer and the loop stops.
        if (current.current.id !== ending.id) return;
        const scored = await onSend(ending, { action: "timeout", elapsed_ms: budget * 1000 });
        if (scored) return;
        if (current.current.id !== ending.id) return;
        await new Promise((resolve) => setTimeout(resolve, 600));
      }
    },
    [budget, onSend],
  );

  useEffect(() => {
    const t = setInterval(() => {
      const remaining = Math.max(0, budget - (Date.now() - started) / 1000);
      set_left(remaining);
      if (remaining <= 0 && !done.current) {
        done.current = true;
        void end_lap(current.current);
      }
    }, 120);
    return () => clearInterval(t);
  }, [started, budget, end_lap]);

  const expired = done.current || left <= 0;
  const locked = tap?.id === task.id || failed?.id === task.id || busy || expired;

  const settle = (value: string, scored: Scored) => {
    if (scored.correct !== true && scored.correct !== false) {
      // No verdict in the reply (a replay of an already-recorded trial, or a fixture). Say nothing
      // rather than inventing a "wrong". 
      setTap(null);
      return;
    }
    const correct = scored.correct;
    setTap({ id: task.id, value, correct });
    if (correct) {
      setScore((n) => n + Number(scored.points ?? 0));
      setStreak(Number(scored.streak ?? 0));
    } else {
      setStreak(0);
      // A wrong tap costs two seconds: move the lap's start back so the clock agrees with the
      // server, which was told when the tap happened.
      const penalty = Number(scored.penalty_seconds ?? 0);
      if (penalty) setStarted((s) => s - penalty * 1000);
    }
    if (scored.lap_over || scored.action === "timeout") done.current = true;
  };

  const choose = async (value: string) => {
    if (locked) return;
    const elapsed = Math.max(0, Date.now() - started);
    setFailed(null);
    setTap({ id: task.id, value, correct: null });
    const scored = await onSend(task, { value, elapsed_ms: elapsed });
    if (!scored) {
      // Keep the choice visible and give it a button: a failed request must not look like a tap
      // that was ignored.
      setFailed({ id: task.id, value, elapsed });
      return;
    }
    settle(value, scored);
  };

  const retry = async () => {
    if (!failed || failed.id !== task.id) return;
    const pending = failed;
    setFailed(null);
    const scored = await onSend(task, { value: pending.value, elapsed_ms: pending.elapsed });
    if (!scored) {
      setFailed(pending);
      return;
    }
    settle(pending.value, scored);
  };

  const fraction = Math.max(0, Math.min(1, left / budget));
  const options = (task.options as string[]) ?? [];
  const checking = tap?.id === task.id && tap?.correct === null;
  return (
    <div className="quick-stage">
      <div className="game-hud">
        <div className={`hud-score${streak > 1 ? " hot" : ""}`}>
          <span className="hud-label">score</span>
          <strong className="hud-value">{score}</strong>
        </div>
        <div className="hud-streak" aria-live="polite">
          {streak > 1 ? `🔥 ${streak} in a row` : "streak starts on a second right tap"}
        </div>
        <div className={`hud-clock${left <= 5 ? " low" : ""}`}>
          <span className="hud-label">{timers ? "time left" : "lap"}</span>
          <strong className="hud-value">{timers ? Math.ceil(left) : Math.round(fraction * 100) + "%"}</strong>
        </div>
      </div>
      <div className={`timer-track${left <= 5 ? " low" : ""}`} aria-hidden="true">
        <span style={{ width: `${Math.round(fraction * 100)}%` }} />
      </div>
      <div className="quick-rung" aria-live="polite">
        <span className="quick-rung-track" aria-hidden="true">
          {[1, 2, 3, 4, 5].map((n) => (
            <span key={n} className={n <= Number(task.level ?? 1) ? "on" : ""} />
          ))}
        </span>
        level {String(task.level ?? 1)} · {String(task.level_name ?? "warming up")}
      </div>
      <div className="baseline-options quick-options" role="group" aria-label="answers">
        {options.map((opt, i) => {
          const is_tap = tap?.id === task.id && tap?.value === opt;
          const tone =
            is_tap && tap?.correct === true ? " is-right" : is_tap && tap?.correct === false ? " is-wrong" : is_tap ? " is-chosen" : "";
          return (
            <button
              key={opt}
              type="button"
              className={`baseline-option quick-option${tone}`}
              style={{ animationDelay: `${i * 40}ms` }}
              onClick={() => void choose(opt)}
              disabled={locked}
              aria-busy={is_tap && tap?.correct === null ? true : undefined}
            >
              <strong>{opt}</strong>
              {is_tap && tap?.correct === true ? <span className="tap-mark good" aria-hidden="true">✓</span> : null}
              {is_tap && tap?.correct === false ? <span className="tap-mark bad" aria-hidden="true">✗</span> : null}
            </button>
          );
        })}
      </div>
      {failed && failed.id === task.id ? (
        <div className="row quick-retry">
          <Btn kind="secondary" onClick={() => void retry()} disabled={busy}>
            Send that tap again
          </Btn>
          <span className="muted small">Nothing was recorded — your tap is still here.</span>
        </div>
      ) : null}
      {expired && !failed ? (
        <div className="row quick-retry">
          {/* The clock's own closing request is retried automatically; this is the way out if the
              network never let any of those through. */}
          <Btn kind="secondary" onClick={() => void end_lap(task)} disabled={busy}>
            Close the lap
          </Btn>
        </div>
      ) : null}
      <p className="muted small" aria-live="polite">
        {expired && !tap
          ? "Time — that lap is closing."
          : checking
          ? "Sending…"
          : tap?.id === task.id && tap?.correct === true
            ? "Right — next one."
            : tap?.id === task.id && tap?.correct === false
              ? "Not this time — the right answer is above."
              : `Lap ${String(task.lap ?? 1)} of 3 · level ${String(task.level ?? 1)} (${String(task.level_name ?? "warming up")}).`}
      </p>
    </div>
  );
}

function Choice({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null> }) {
  const options = (task.options as string[]) ?? [];
  const roving = useRovingTabIndex(options.length, "horizontal");
  return (
    <div className="baseline-options trap-options" role="group" aria-label="answers">
      {options.map((opt, i) => (
        <button
          key={opt}
          ref={roving.registerRef(i)}
          type="button"
          tabIndex={roving.getTabIndex(i)}
          // Enter and Space press the button natively (one click, one answer); the roving handler is
          // for the arrows only. Handling Enter here as well sent every keyboard answer twice.
          onKeyDown={(e) => roving.onKeyDown(e, i)}
          className="baseline-option"
          onClick={() => void onSend(task, { value: opt })}
          disabled={busy}
        >
          <strong>{opt}</strong>
        </button>
      ))}
    </div>
  );
}

function Trap({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null> }) {
  return (
    <div className="trap-stage">
      {task.story ? <p className="baseline-story story-card">{String(task.story)}</p> : null}
      <Choice task={task} busy={busy} onSend={onSend} />
    </div>
  );
}

/** Word memory: eight cards, dealt face up, each one tappable to hide it and check yourself. */
function Study({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null> }) {
  const pairs = (task.pairs as { code: string; place: string }[]) ?? [];
  const study_ms = Number(task.study_ms ?? 45_000);
  const [left, set_left] = useState<number>(Math.round(study_ms / 1000));
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const started = useRef(Date.now());
  const ready = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    started.current = Date.now();
    const until = Date.now() + study_ms;
    set_left(Math.max(0, Math.round(study_ms / 1000)));
    setHidden({});
    const t = setInterval(() => set_left(Math.max(0, seconds_left(until, Date.now()))), 250);
    ready.current?.focus();
    return () => clearInterval(t);
  }, [task.id, study_ms]);
  const hidden_count = Object.values(hidden).filter(Boolean).length;
  const fraction = Math.max(0, Math.min(1, left / Math.max(1, Math.round(study_ms / 1000))));
  return (
    <div className="study-stage">
      <div className="study-hud">
        <div className="study-ring" aria-hidden="true">
          <svg viewBox="0 0 44 44">
            <circle cx="22" cy="22" r="18" className="ring-track" />
            <circle cx="22" cy="22" r="18" className="ring-fill" style={{ strokeDashoffset: 113 - 113 * fraction }} />
          </svg>
          <span>{left}</span>
        </div>
        <p className="study-hint">
          <strong>{pairs.length} pairs.</strong> Tap a card to cover it and test yourself; tap again to look.
          {hidden_count ? ` ${hidden_count} covered.` : ""}
        </p>
      </div>
      <ul className="baseline-pairs study-grid">
        {pairs.map((p, i) => {
          const is_hidden = Boolean(hidden[p.code]);
          return (
            <li key={p.code} style={{ animationDelay: `${i * 55}ms` }}>
              <button
                type="button"
                className={`study-card${is_hidden ? " is-covered" : ""}`}
                aria-pressed={is_hidden}
                data-index={String(i + 1).padStart(2, "0")}
                aria-label={`${p.code} goes with ${p.place}${is_hidden ? " (covered)" : ""}`}
                onClick={() => setHidden((h) => ({ ...h, [p.code]: !h[p.code] }))}
              >
                {/* The card's number is a CSS pseudo-element, not text: the Playwright suite reads
                    each card's text as "code → place" and a prefix would become part of the code. */}
                <strong className="study-code">{p.code}</strong>
                <span className="study-arrow" aria-hidden="true">→</span>
                <span className="study-place">{p.place}</span>
              </button>
            </li>
          );
        })}
      </ul>
      <div className="row">
        <Btn
          kind="primary"
          disabled={busy}
          onClick={() => void onSend(task, { seconds_seen: Math.round((Date.now() - started.current) / 1000) })}
          title="go on when you are ready; the pairs come back later"
        >
          I have looked at them
        </Btn>
        <span className="muted small" aria-live="polite">
          about {left}s of looking time, and stopping early is allowed
        </span>
        {hidden_count ? (
          <Btn kind="ghost" disabled={busy} onClick={() => setHidden({})}>
            Show all
          </Btn>
        ) : null}
      </div>
    </div>
  );
}

/** Vault lights: a 3x3 grid flashes a sequence; the learner taps it back. */
function Vault({
  task,
  busy,
  timers,
  onSend,
}: {
  task: BaselineTask;
  busy: boolean;
  timers: boolean;
  onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null>;
}) {
  const sequence = useMemo(() => (task.sequence as number[]) ?? [], [task]);
  const expected = useMemo(() => vault_expected(sequence, task.answer_direction as string | undefined), [sequence, task]);
  const [showing, set_showing] = useState(true);
  const [flash, set_flash] = useState<number | null>(null);
  const [taps, setTaps] = useState<number[]>([]);
  const [wrong, setWrong] = useState<number | null>(null);
  const [failed_taps, set_failed_taps] = useState<number[] | null>(null);
  // Tapped while the lights were still going. The tap is not an answer, but it is not ignored
  // either: the screen says why, which is the difference between "watch" and "broken".
  const [early, setEarly] = useState(false);
  const grid = useRovingTabIndex(9, "horizontal");

  // The flash: one tile at a time, in the order the learner must remember. `prefers-reduced-motion`
  // is respected by shortening the flash rather than removing it -- the sequence *is* the task.
  useEffect(() => {
    set_showing(true);
    set_flash(null);
    setTaps([]);
    setWrong(null);
    set_failed_taps(null);
    setEarly(false);
    const reduced =
      typeof window !== "undefined" && window.matchMedia
        ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
        : false;
    const on = reduced ? 300 : Math.max(380, 620 - sequence.length * 30);
    const gap = reduced ? 110 : Math.max(150, 240 - sequence.length * 10);
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    sequence.forEach((tile, i) => {
      timers.push(
        setTimeout(() => {
          if (!cancelled) set_flash(tile);
        }, i * (on + gap)),
      );
      timers.push(
        setTimeout(() => {
          if (!cancelled) set_flash(null);
        }, i * (on + gap) + on),
      );
    });
    timers.push(
      setTimeout(() => {
        if (cancelled) return;
        set_flash(null);
        set_showing(false);
        announce(`Your turn. ${sequence.length} tiles, ${task.answer_direction === "backward" ? "in reverse order" : "in the same order"}.`);
      }, sequence.length * (on + gap) + 180),
    );
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id]);

  // Number keys, on the document, while the grid is tappable: 1-9 taps that tile.
  const tapRef = useRef<(tile: number) => void>(() => {});
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const n = Number(e.key);
      if (!Number.isInteger(n) || n < 1 || n > 9) return;
      tapRef.current(n - 1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const tap = (tile: number) => {
    // While the sequence is flashing, or while the answer is on its way, a tap is not an answer.
    // (`busy` used to freeze the whole grid after the last tap; it is only a guard here because the
    // answer is already complete at that point.)
    if (showing) {
      // Not an answer, but not silence either: a disabled grid with no explanation reads as a bug.
      setEarly(true);
      announce("Watch the lights first, then tap the order.");
      return;
    }
    if (busy && taps.length === expected.length) return;
    const next = [...taps, tile];
    if (!vault_prefix_ok(next, expected)) {
      setWrong(tile);
      setTaps(next);
      announce("That is not the order. The answer was sent as it stands.");
      void send_taps(next);
      return;
    }
    setTaps(next);
    if (next.length === expected.length) void send_taps(next);
  };

  // A send that fails leaves the exact answer on screen and a way to send it again. Without this,
  // one dropped request looked like a dead grid — the learner could tap and nothing happened.
  const send_taps = async (answer: number[]) => {
    const out = await onSend(task, { taps: answer });
    if (!out) set_failed_taps(answer);
  };

  tapRef.current = tap;
  const progress = expected.length ? Math.round((taps.length / expected.length) * 100) : 0;

  return (
    <div className="vault-stage">
      <div className="vault-head">
        <span className={`vault-state${showing ? " watching" : ""}`} aria-live="polite">
          {showing ? "Watch the lights…" : `Your turn — tap ${expected.length} in ${task.answer_direction === "backward" ? "reverse" : "this"} order`}
        </span>
        <div className="vault-dots" aria-hidden="true">
          {expected.map((_, i) => (
            <span key={i} className={i < taps.length ? "on" : ""} />
          ))}
        </div>
        {timers ? <span className="muted small">{expected.length} tiles</span> : null}
      </div>
      <div className="vault-grid" role="grid" aria-label="vault tiles" aria-busy={showing}>
        {Array.from({ length: 9 }, (_, i) => (
          <button
            key={i}
            ref={grid.registerRef(i)}
            type="button"
            role="gridcell"
            aria-label={`tile ${i + 1}`}
            tabIndex={grid.getTabIndex(i)}
            className={`vault-tile${flash === i ? " flash" : ""}${taps.includes(i) ? " tapped" : ""}${wrong === i ? " wrong" : ""}`}
            // Enter and Space tap through the native click; the grid handles the arrows only, so a
            // keyboard tap is recorded once.
            onKeyDown={(e) => grid.onKeyDown(e, i)}
            onClick={() => tap(i)}
            // `aria-disabled` rather than `disabled`: a disabled button cannot be focused, and the
            // grid has to keep its keyboard cursor while the lights run.
            aria-disabled={showing}
            disabled={busy && taps.length === expected.length}
          >
            {i + 1}
          </button>
        ))}
      </div>
      {failed_taps ? (
        <div className="row">
          <Btn kind="secondary" disabled={busy} onClick={() => void send_taps(failed_taps)}>
            Send that answer again
          </Btn>
          <span className="muted small">Nothing was recorded — the tap order is still here.</span>
        </div>
      ) : null}
      <div className="vault-progress" aria-hidden="true">
        <span style={{ width: `${progress}%` }} />
      </div>
      <p className="muted small" aria-live="polite">
        {showing
          ? early
            ? "Not yet — watch the lights, then tap. Nothing was sent."
            : "Watch: the tiles light up in the order to remember."
          : wrong !== null
            ? "That tap was out of order — it is recorded honestly, and the next task is coming."
            : `Tapped ${taps.length} of ${expected.length}. ${task.answer_direction === "backward" ? "Reverse order." : "Same order."}`}
      </p>
    </div>
  );
}

/** The shopkeeper: hold three items, answer three quick statements, type the items back. */
function Shopkeeper({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null> }) {
  const items = (task.items as string[]) ?? [];
  const distractors = (task.distractors as { id: string; text: string }[]) ?? [];
  const [answers, set_answers] = useState<Record<string, boolean>>({});
  const [text, set_text] = useState("");
  const done = distractors.length > 0 && distractors.every((d) => answers[d.id] !== undefined);
  const answered = Object.keys(answers).length;
  return (
    <div className="shop-stage">
      <p className="baseline-label">Hold these three items:</p>
      <ul className="baseline-pairs shop-items">
        {items.map((it) => (
          <li key={it}>
            <strong>{it}</strong>
          </li>
        ))}
      </ul>
      <fieldset className="baseline-q-row">
        <legend>
          Quick answers while you hold them <span className="muted small">({answered} of {distractors.length})</span>
        </legend>
        {distractors.map((d) => (
          <div key={d.id} className="row shop-row">
            <span style={{ flex: 1 }}>{d.text}</span>
            <Btn pressed={answers[d.id] === true} disabled={busy} onClick={() => set_answers((a) => ({ ...a, [d.id]: true }))}>
              true
            </Btn>
            <Btn pressed={answers[d.id] === false} disabled={busy} onClick={() => set_answers((a) => ({ ...a, [d.id]: false }))}>
              false
            </Btn>
          </div>
        ))}
      </fieldset>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void onSend(task, { items: parse_items(text), distractors: answers });
        }}
      >
        <label className="baseline-label" htmlFor="baseline-items">
          Now type the three items back, separated by commas
        </label>
        <input
          id="baseline-items"
          className="input game-input"
          autoComplete="off"
          value={text}
          onChange={(e) => set_text(e.target.value)}
          disabled={busy}
        />
        <div className="row">
          <Btn type="submit" kind="primary" disabled={busy || !done || text.trim().length === 0}>
            Answer
          </Btn>
        </div>
      </form>
    </div>
  );
}

/** The machine: probe it, watch the outputs, then name the rule. */
function Machine({
  task,
  view,
  busy,
  onSend,
}: {
  task: BaselineTask;
  view: BaselineView;
  busy: boolean;
  onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null>;
}) {
  const [input, set_input] = useState("");
  const [guess, set_guess] = useState("");
  const max_probes = Number(task.max_probes ?? 5);
  const max_wrong = Number(task.max_wrong_guesses ?? 2);
  // Everything the page shows about the machine comes from the server's record: the probes used
  // and what the machine said to each (`history`). A reload therefore shows the same round, and a
  // probe is never counted twice. The last reply only decides the guess message below.
  const last = view.scored ?? null;
  const wrong_guesses = Number(task.wrong_guesses ?? 0);
  const probes_used = Number(task.probes_used ?? 0);
  const history = (task.history as { x: unknown; out: unknown; falsifying?: boolean }[] | undefined) ?? [];
  const at_limit = probes_used >= max_probes;
  const box = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    set_input("");
    set_guess("");
    box.current?.focus();
  }, [task.id]);

  return (
    <div className="machine-stage">
      <div className="machine-meter" aria-hidden="true">
        <span className="meter-label">probes</span>
        {Array.from({ length: max_probes }, (_, i) => (
          <span key={i} className={`meter-dot${i < probes_used ? " on" : ""}`} />
        ))}
        <span className="meter-label">wrong guesses</span>
        {Array.from({ length: max_wrong }, (_, i) => (
          <span key={i} className={`meter-dot bad${i < wrong_guesses ? " on" : ""}`} />
        ))}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const value = Number(input);
          if (input.trim() === "" || Number.isNaN(value) || at_limit) return;
          void onSend(task, { action: "probe", value });
          set_input("");
        }}
      >
        <label className="baseline-label" htmlFor="machine-in">
          Put a number in
        </label>
        <div className="machine-in-row">
          <input
            id="machine-in"
            ref={box}
            className="input game-input"
            inputMode="decimal"
            autoComplete="off"
            value={input}
            onChange={(e) => set_input(e.target.value)}
            disabled={busy || at_limit}
          />
          <Btn type="submit" kind="secondary" disabled={busy || input.trim() === "" || at_limit}>
            Send it in
          </Btn>
        </div>
      </form>
      <ul className="baseline-machine-out machine-history" aria-live="polite">
        {history.map((p, i) => (
          <li key={`${String(p.x)}-${i}`} className={p.falsifying ? "is-edge" : ""}>
            <span>{String(p.x)}</span> <span aria-hidden="true">→</span> <strong>{String(p.out)}</strong>
          </li>
        ))}
        {history.length === 0 ? <li className="muted">Nothing in yet: the machine answers every number you send.</li> : null}
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (guess.trim()) void onSend(task, { action: "guess", text: guess.trim() });
        }}
      >
        <label className="baseline-label" htmlFor="machine-guess">
          What is the rule? Say it in words (&ldquo;times 3, add 2&rdquo;).
        </label>
        <input
          id="machine-guess"
          className="input game-input"
          autoComplete="off"
          value={guess}
          onChange={(e) => set_guess(e.target.value)}
          disabled={busy || wrong_guesses >= max_wrong}
        />
        <div className="row">
          <Btn type="submit" kind="primary" disabled={busy || !guess.trim()}>
            That is my answer
          </Btn>
        </div>
      </form>
      {last && last.action === "guess" ? (
        <Notice tone={last.correct ? "good" : "warn"}>
          {last.correct
            ? "That is the rule."
            : `Not the rule this time. A wrong guess is counted honestly, and you can probe again if you have probes left.`}
        </Notice>
      ) : null}
    </div>
  );
}

/** The locked box: one hard puzzle, a hint shop with coin costs, and a door marked "walk away". */
function LockedBox({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null> }) {
  const cases = (task.cases as { n: number; value: number }[]) ?? [];
  const shop = (task.hint_shop as { id: string; cost: number; text: string; level: string }[]) ?? [];
  // The attempts, the hints bought and the coins left are the server's (the task carries them), so a
  // reload shows the same box. Only the answer being typed is local.
  const attempts_used = Number(task.attempts_used ?? 0);
  const max_attempts = Number(task.max_attempts ?? 3);
  const bought = (task.bought_hints as string[] | undefined) ?? [];
  const coins = Number(task.coins_left ?? 0);
  const [value, set_value] = useState("");
  useEffect(() => {
    set_value("");
  }, [task.id]);
  const out_of_attempts = attempts_used >= max_attempts;
  return (
    <div className="box-stage">
      <div className="box-cases" aria-label="the first four numbers">
        {cases.map((c) => (
          <span key={c.n} className="box-case">
            <span className="box-n">{c.n}</span>
            <strong>{c.value}</strong>
          </span>
        ))}
        <span className="box-case unknown">
          <span className="box-n">5</span>
          <strong>?</strong>
        </span>
      </div>
      <p className="muted small">
        This one is hard on purpose. You can buy a hint, try again, or walk away — all three are
        useful information and none of them is a failure. Attempts: {attempts_used} of {max_attempts}.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim() && !out_of_attempts) void onSend(task, { action: "attempt", value: Number(value) });
        }}
      >
        <label className="baseline-label" htmlFor="box-answer">
          Your answer for the fifth number
        </label>
        <input
          id="box-answer"
          className="input game-input"
          inputMode="numeric"
          value={value}
          onChange={(e) => set_value(e.target.value)}
          disabled={busy || out_of_attempts}
        />
        <div className="row">
          <Btn type="submit" kind="primary" disabled={busy || out_of_attempts || value.trim() === ""}>
            Try this answer
          </Btn>
          <Btn kind="ghost" disabled={busy} onClick={() => void onSend(task, { action: "walk_away" })} title="stop here; walking away is recorded as what it is">
            Walk away
          </Btn>
        </div>
      </form>
      <details className="baseline-hints">
        <summary>
          Hint shop — you have {coins} coin{coins === 1 ? "" : "s"}
        </summary>
        <ul>
          {shop.map((h) => {
            const got = bought.includes(h.id);
            return (
              <li key={h.id} className="row">
                <span style={{ flex: 1 }}>{got ? h.text : `${h.level} hint, ${h.cost} coin${h.cost > 1 ? "s" : ""}`}</span>
                <Btn
                  kind={got ? "ghost" : "quiet"}
                  disabled={busy || got || coins < h.cost}
                  onClick={() => void onSend(task, { action: "hint", hint_id: h.id })}
                >
                  {got ? "bought" : `buy (${h.cost})`}
                </Btn>
              </li>
            );
          })}
        </ul>
      </details>
    </div>
  );
}

/** Delayed codewords: a prediction, then free recall, then recognition. */
function Delayed({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null> }) {
  if (task.action === "predict") {
    return <Predict task={task} busy={busy} onSend={onSend} />;
  }
  if (task.kind === "free") {
    return <FreeText task={task} busy={busy} onSend={onSend} label="Type the code words you remember, separated by commas. The places can wait." placeholder="type the words here" />;
  }
  if (task.kind === "recognition") {
    // Recognition offers places to tap, and tapping is the measurement: a typed answer here was
    // asking for production in a test of recognition, which is the one thing this half of the
    // delayed check exists to tell apart.
    return (
      <div className="delayed-stage">
        {/* The cue is the code word on its own: the question is in the prompt above it, and the
            screen-reader text of a sentence here would become part of the cue. */}
        <p className="baseline-cue cue-card">{String(task.cue ?? "")}</p>
        <Choice task={task} busy={busy} onSend={onSend} />
      </div>
    );
  }
  return <FreeText task={task} busy={busy} onSend={onSend} label={String(task.prompt ?? "which code word went with it?")} />;
}

function Predict({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => Promise<Scored | null> }) {
  const max = Number(task.max_value ?? 8);
  const [value, set_value] = useState<number | null>(null);
  return (
    <div className="predict-stage">
      <div className="baseline-options predict-options" role="group" aria-label="how many will you remember">
        {Array.from({ length: max + 1 }, (_, n) => (
          <button
            key={n}
            type="button"
            aria-pressed={value === n}
            className={`baseline-option predict-option${value === n ? " on" : ""}`}
            onClick={() => set_value(n)}
            disabled={busy}
          >
            <strong>{n}</strong>
          </button>
        ))}
      </div>
      <div className="row">
        <Btn kind="primary" disabled={busy || value === null} onClick={() => void onSend(task, { value })}>
          That is my guess
        </Btn>
      </div>
      <p className="muted small">A guess that turns out wrong is useful: it is how over- and under-confidence is measured.</p>
    </div>
  );
}

/* ------------------------------------------------------------ codeword check ---- */

/**
 * The codeword check, one answer at a time, from the server's list. It is the same whether it is
 * reached from the snapshot or from a return visit: the server says what is still owed, and each
 * answer is recorded against the run it belongs to (which may be an earlier sitting).
 */
function CodewordCheck({
  check,
  timers,
  onChanged,
}: {
  check: NextDay;
  timers: boolean;
  onChanged: (next: NextDay) => void | Promise<void>;
}) {
  const task = check.tasks[0] ?? null;
  const run_id = check.run_id ?? null;
  const [text, set_text] = useState("");
  const [wager, set_wager] = useState<number | null>(null);
  const [busy, set_busy] = useState(false);
  const [err, set_err] = useState<string | null>(null);
  const [recorded, set_recorded] = useState(0);
  // The check is a memory question like any other, so it answers with a verdict instead of moving
  // on in silence.
  const [verdict, set_verdict] = useState<{ correct: boolean | null; expected?: string } | null>(null);
  const shown_at = useRef<number>(Date.now());
  const box = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    set_text("");
    set_wager(null);
    shown_at.current = Date.now();
    box.current?.focus();
  }, [task?.id]);

  if (!task || !run_id) {
    return (
      <p>
        {check.note ?? "Nothing left to re-check."}
        {recorded ? ` Recorded ${recorded} answer${recorded === 1 ? "" : "s"}.` : ""}
      </p>
    );
  }

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (!text.trim() || busy) return;
        set_busy(true);
        set_err(null);
        try {
          const reply = await submit_trial(run_id, {
            task_id: task.id,
            response: { text: text.trim() },
            latency_ms: Math.max(0, Date.now() - shown_at.current),
            wager: timers ? wager : null,
          });
          set_verdict({
            correct: reply.scored?.correct ?? null,
            expected: typeof reply.scored?.expected === "string" ? reply.scored.expected : undefined,
          });
          set_recorded((n) => n + 1);
          const after = await load_next_day(run_id);
          await onChanged({ ...after, run_id });
          announce("Recorded.");
        } catch (error) {
          set_err(error instanceof ApiError ? error.shown : (error as Error).message);
        } finally {
          set_busy(false);
        }
      }}
    >
      {check.same_day ? (
        <Notice tone="info">
          You are doing this on the day of the sitting, so this measures a short gap, not the next-day one.
        </Notice>
      ) : null}
      {timers ? <Coins value={wager} onChange={set_wager} /> : null}
      <label className="baseline-label" htmlFor="next-day-answer">
        {String(task.prompt ?? "Which place went with it?")}
      </label>
      <input
        id="next-day-answer"
        ref={box}
        className="input"
        autoComplete="off"
        value={text}
        placeholder="type it"
        onChange={(e) => set_text(e.target.value)}
        disabled={busy}
      />
      <div className="row">
        <Btn type="submit" kind="primary" disabled={busy || !text.trim()}>
          Answer
        </Btn>
      </div>
      {verdict ? (
        <p className={`result-strip ${verdict.correct === true ? "good" : verdict.correct === false ? "bad" : "neutral"}`} role="status">
          {verdict.correct === true ? (
            <>
              <span aria-hidden="true">✓</span> Right
            </>
          ) : verdict.correct === false ? (
            <>
              <span aria-hidden="true">✗</span> Not this time
            </>
          ) : (
            "Recorded."
          )}
        </p>
      ) : null}
      <p className="muted small">{check.tasks.length} left to answer.</p>
      {err ? <Notice tone="warn">{err}</Notice> : null}
    </form>
  );
}

/** Yesterday's check, offered at the top of a return visit. It can be skipped, and it never blocks the baseline. */
function ReturnCheck({
  check,
  timers,
  onChanged,
  onHide,
}: {
  check: NextDay;
  timers: boolean;
  onChanged: (next: NextDay) => void;
  onHide: () => void;
}) {
  return (
    <Card title="From your last sitting: a codeword check" tone="ask">
      <p className="muted small">
        These are pairs you learned last time, asked again with no new study. It is optional: answer now,
        or leave it for later.
      </p>
      <CodewordCheck check={check} timers={timers} onChanged={onChanged} />
      <div className="row">
        <Btn kind="ghost" onClick={onHide}>
          Not now
        </Btn>
      </div>
    </Card>
  );
}

/* ----------------------------------------------------------------- snapshot ---- */

function SnapshotScreen({
  snap,
  view,
  history,
  onChanged,
}: {
  snap: BaselineSnapshot | null;
  view: BaselineView;
  history: BaselineHistory | null;
  onChanged: () => Promise<void>;
}) {
  const [next, set_next] = useState<NextDay | null>(null);
  const [busy, set_busy] = useState(false);
  const [err, set_err] = useState<string | null>(null);

  if (!snap) {
    return (
      <Card title="Sealing the run">
        <p className="muted">Saving what was measured…</p>
      </Card>
    );
  }

  const factors = order_factors(snap.factors ?? []);
  const measured = factors.filter((f) => f.state === "measured");

  return (
    <div className="baseline-snapshot">
      <Card title="What we measured, and what we did not">
        <p>
          {measured.length} of {factors.length} things were measurable from this sitting. Everything else says
          &ldquo;not measured&rdquo; on purpose: a game you did not play is unknown, not a zero.
        </p>
        <ul className="baseline-factors">
          {factors.filter((f) => friendlyLabel(f.title) !== null).map((f) => (
            <li key={f.key} className={f.state === "measured" ? "" : "muted"}>
              <div className="row">
                <strong style={{ flex: 1 }}>{friendlyLabel(f.title)}</strong>
                {f.state === "measured" ? (
                  <Tag tone="good">{format_range(f.range)}</Tag>
                ) : (
                  <Tag tone="line">not measured</Tag>
                )}
                {f.state === "measured" ? <span className="muted small">n={f.n}</span> : null}
              </div>
              <p style={{ margin: "4px 0 0" }}>{f.statement}</p>
              {f.actions && f.actions.length ? (
                <ul className="muted small">
                  {f.actions.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      </Card>

      {snap.levers ? (
        <Card title="What we will do differently">
          <ul className="baseline-levers">
            {Object.entries(snap.levers).map(([key, lever]) => (
              <li key={key}>
                <div className="row">
                  <strong style={{ flex: 1 }}>{key.replace(/_/g, " ")}</strong>
                  <Tag tone={lever.source === "measured" ? "good" : lever.source === "stated" ? "think" : "line"}>
                    {source_label(lever.source)}
                  </Tag>
                  <span>{String(lever.value)}</span>
                </div>
                {lever.why ? <p className="muted small" style={{ margin: "2px 0 0" }}>{lever.why}</p> : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {snap.preference_vs_performance ? (
        <Card title="What you said, and what happened">
          <p>{String(snap.preference_vs_performance.sentence ?? snap.preference_vs_performance.statement ?? "")}</p>
          <Btn href="/evidence">View method ranges and evidence sources</Btn>
          {snap.preference_vs_performance.note ? (
            <p className="muted small">{String(snap.preference_vs_performance.note)}</p>
          ) : null}
        </Card>
      ) : null}

      {snap.completion?.not_measured?.length ? (
        <Card title="Steps that did not happen">
          <p className="muted small">
            Recorded as not measured, not as zero: {snap.completion.not_measured.map(friendlyLabel).filter(Boolean).join(", ") || "Some steps"}.
          </p>
        </Card>
      ) : null}

      <GameHistoryPanel history={history} />

      <Card title="Come back later">
        {next ? (
          <CodewordCheck
            check={next}
            timers={timers_visible_in(snap.preferences)}
            onChanged={async (after) => {
              set_next(after);
              await onChanged();
            }}
          />
        ) : (
          <div>
            <p>
              The codeword check is meant for another day. You can ask for it now: it measures a shorter gap
              than the one it is designed for, and the check says so when you answer.
            </p>
            <Btn
              disabled={busy}
              onClick={async () => {
                set_busy(true);
                set_err(null);
                try {
                  set_next(await load_next_day(snap.run_id));
                } catch (e) {
                  set_err(e instanceof ApiError ? e.shown : (e as Error).message);
                } finally {
                  set_busy(false);
                }
              }}
            >
              Show the codeword check
            </Btn>
            {err ? <Notice tone="warn">{err}</Notice> : null}
          </div>
        )}
      </Card>

      <Trace title="where this comes from">
        Run {snap.run_id ?? view.run_id} · {snap.state} · {snap.measured ?? measured.length} of {snap.total ?? factors.length} measured.
        Games are scored by the server from the trial log; nothing on this screen is estimated by the page.
      </Trace>
    </div>
  );
}

/* --------------------------------------------------------------- the record ---- */

/**
 * What has been stored, in words. The trials were always saved; this is the part that lets a
 * learner see them after the snapshot has scrolled away — and the proof that "stored" is not a
 * promise the page only makes.
 */
function GameHistoryPanel({ history }: { history: BaselineHistory | null }) {
  if (!history || history.games.length === 0) {
    return (
      <Card title="Your games are saved">
        <p className="muted small">
          Every tap is written to your own record as it happens. Play a game and its best run shows up here.
        </p>
      </Card>
    );
  }
  const totals = history.totals;
  return (
    <Card
      title="Your games, saved"
      sub={`${totals.sittings} sitting${totals.sittings === 1 ? "" : "s"} · ${totals.measured} scored answers stored`}
    >
      <ul className="history-list">
        {history.games.map((g: GameResult) => (
          <li key={g.game} className="history-row">
            <span className="history-icon" aria-hidden="true">
              <Icon name={gameIcon(g.game)} />
            </span>
            <span className="history-main">
              <strong>{g.title}</strong>
              <span className="muted small">
                {g.plays} play{g.plays === 1 ? "" : "s"}
                {g.answered ? ` · ${g.correct} of ${g.answered} right` : ""}
                {g.detail && typeof g.detail.accuracy === "number"
                  ? ` · ${Math.round(Number(g.detail.accuracy) * 100)}% accuracy`
                  : ""}
              </span>
            </span>
            <span className="history-best">{g.best_label}</span>
          </li>
        ))}
      </ul>
      {history.runs.length > 1 ? (
        <p className="muted small">
          Last sitting
          {history.runs[0].started_at
            ? ` started ${new Date(history.runs[0].started_at).toLocaleDateString()}`
            : ""}{" "}
          · {history.runs[0].trials} trials recorded in it.
        </p>
      ) : null}
    </Card>
  );
}

/** A learner who has finished before sees what they scored, not a fresh format pick. */
function WelcomeBack({
  history,
  snap,
  busy,
  onPlayAgain,
  error,
}: {
  history: BaselineHistory;
  snap: BaselineSnapshot | null;
  busy: boolean;
  onPlayAgain: () => void;
  error: string | null;
}) {
  const last_run = history.runs.find((r) => r.status === "complete") ?? history.runs[0] ?? null;
  const measured = snap?.factors.filter((f) => f.state === "measured").length ?? 0;
  return (
    <div className="baseline-welcome">
      <header className="baseline-head baseline-hero">
        <PageArt name="brain" className="baseline-brain-art" />
        <div>
          <p className="muted small" style={{ margin: 0 }}>The baseline · stored</p>
          <h1 style={{ margin: "2px 0 0" }}>Welcome back</h1>
        </div>
      </header>
      {error ? <Notice tone="bad">{error}</Notice> : null}
      <Card title="Your last sitting is still here">
        <p>
          {last_run?.finished_at
            ? `You finished on ${new Date(last_run.finished_at).toLocaleDateString()}`
            : "You finished a sitting"}{" "}
          — {last_run?.trials ?? 0} trials recorded, {measured} of {snap?.factors.length ?? 8} things measurable.
          Nothing was thrown away when you left.
        </p>
        <div className="row">
          <Btn href={`/baseline?run=${encodeURIComponent(last_run?.run_id ?? "")}`} kind="primary">
            See what we noticed
          </Btn>
          <Btn kind="secondary" onClick={onPlayAgain} disabled={busy}>
            {busy ? "Opening…" : "Play the games again"}
          </Btn>
          <Btn kind="ghost" href="/">Back to the dashboard</Btn>
        </div>
      </Card>
      <GameHistoryPanel history={history} />
      <p className="muted small">
        Playing again opens a new sitting. The old one stays in the record, so a change over time is
        visible rather than overwritten.
      </p>
    </div>
  );
}
