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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import "./baseline.css";

import { Btn, Card, Notice, Tag, Trace, announce } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useRovingTabIndex } from "@/lib/a11y/useRovingTabIndex";
import {
  answer_questions,
  finish_baseline,
  format_range,
  load_next_day,
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
  type BaselineSnapshot,
  type BaselineTask,
  type BaselineView,
  type NextDay,
} from "@/lib/baseline";

export default function Baseline() {
  const [view, setView] = useState<BaselineView | null>(null);
  const [snap, setSnap] = useState<BaselineSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wager, setWager] = useState<number | null>(null);
  const [resumed, setResumed] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);
  // Yesterday's codeword check, if the last finished sitting has one waiting. It is loaded alongside
  // the run, never instead of it: a learner who came back gets the new baseline as well.
  const [pending, setPending] = useState<NextDay | null>(null);
  const [pending_hidden, setPendingHidden] = useState(false);

  // When the current task appeared. Latency is measured here because only the browser knows it;
  // the server clamps anything absurd, and the questions screen tells the learner it happens.
  const shown_at = useRef<number>(0);
  const opened = useRef(false);

  const advance = useCallback((next: BaselineView) => {
    shown_at.current = Date.now();
    setWager(null);
    setView(next);
  }, []);

  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    (async () => {
      try {
        const first = await start_baseline();
        if (first.kind !== "format" && first.kind !== "questions") {
          setResumed(
            first.completion?.complete != null && first.completion?.total != null
              ? `Picking up where you left off: stage "${first.stage_title}", ${first.completion.complete} of ${first.completion.total} steps already done.`
              : `Picking up where you left off: ${first.stage_title}.`,
          );
        }
        advance(first);
        if (first.kind === "snapshot") {
          setFinished(true);
          setSnap(await load_snapshot(first.run_id));
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.shown : (err as Error).message);
      }
    })();
    // The return visit's check is the last *finished* sitting's: never the sitting that is open now.
    load_next_day(null, true)
      .then(setPending)
      .catch(() => setPending(null));
  }, [advance]);

  const stop = useCallback(async () => {
    if (!view || busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await finish_baseline(view.run_id);
      setSnap(out);
      setFinished(true);
      announce("Baseline stopped. What was measured is saved.");
    } catch (err) {
      setError(err instanceof ApiError ? err.shown : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [view, busy]);

  const send = useCallback(
    async (task: BaselineTask, response: Record<string, unknown>) => {
      if (!view || busy) return;
      setBusy(true);
      setError(null);
      try {
        const next = await submit_trial(view.run_id, {
          task_id: task.id,
          response,
          latency_ms: Math.max(0, Date.now() - shown_at.current),
          wager: offers_coins(view) ? wager : null,
        });
        advance(next);
        if (next.duplicate) announce("That answer was already recorded; showing you the next step.");
        else if (next.scored && next.scored.correct === true) announce("Correct.");
        else if (next.scored && next.scored.correct === false) announce("Not this time. The next step is on screen.");
        if (next.kind === "snapshot") {
          setFinished(true);
          setSnap(await load_snapshot(next.run_id));
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.shown : (err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [view, busy, wager, advance],
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

  if (error && !view) {
    return (
      <div className="wrap baseline-page">
        <Card title="The baseline could not open">
          <Notice tone="bad">{error}</Notice>
          <p className="muted">Nothing was recorded. If you are signed out, the front door will ask you in.</p>
        </Card>
      </div>
    );
  }

  if (!view) {
    return (
      <div className="wrap baseline-page" aria-busy="true">
        <Card title="Opening the baseline">
          <p className="muted">One sitting of short games. Nothing here is a grade.</p>
        </Card>
      </div>
    );
  }

  const task = view.task;
  const show_snapshot = finished || view.kind === "snapshot";
  const progress = Math.round((view.progress ?? 0) * 100);

  return (
    <div className="wrap baseline-page">
      <header className="baseline-head">
        <div>
          <p className="muted small" style={{ margin: 0 }}>
            {show_snapshot ? "What we noticed" : step_label(view.stage) || "The baseline"} · {progress}% through
          </p>
          <h1 style={{ margin: "2px 0 0" }}>{show_snapshot ? "What we noticed" : view.stage_title}</h1>
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
      {view.note && !show_snapshot ? <p className="muted small">{view.note}</p> : null}

      {pending && pending.tasks.length > 0 && pending.run_id && pending.run_id !== view.run_id && !pending_hidden && !show_snapshot ? (
        <ReturnCheck
          check={pending}
          timers={timers_visible(view)}
          onChanged={setPending}
          onHide={() => setPendingHidden(true)}
        />
      ) : null}

      {show_snapshot ? (
        <SnapshotScreen
          snap={snap}
          view={view}
          onChanged={async () => {
            if (snap) setSnap(await load_snapshot(snap.run_id));
          }}
        />
      ) : view.kind === "format" ? (
        <FormatScreen view={view} onPick={choose_format} busy={busy} />
      ) : view.kind === "questions" ? (
        <QuestionsScreen view={view} onDone={answer_all} busy={busy} />
      ) : task ? (
        <TaskScreen task={task} view={view} busy={busy} onSend={send} />
      ) : (
        <Card title="Nothing left to show">
          <p>
            This run has no more tasks. Press <strong>Stop here</strong> to seal it, and the snapshot will
            show what was measured.
          </p>
        </Card>
      )}

      <p className="muted small">
        You can stop at any point: a partial baseline keeps what was measured and marks the rest
        &ldquo;not measured&rdquo;. Nothing here is a grade.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------- format ---- */

function FormatScreen({ view, onPick, busy }: { view: BaselineView; onPick: (v: string) => void; busy: boolean }) {
  const [picked, setPicked] = useState<string | null>(view.format_pick);
  const opener = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    opener.current?.focus();
  }, []);
  return (
    <Card title="How would you like to meet a new idea?">
      <p>
        This is a preference, not a diagnosis: it sets where the app starts, and later it is compared
        with what actually worked. Changing your mind is allowed.
      </p>
      <div className="baseline-options" role="group" aria-label="format preference">
        {view.format_options.map((opt, i) => (
          <button
            key={opt.value}
            ref={i === 0 ? opener : undefined}
            type="button"
            aria-pressed={picked === opt.value}
            className={`baseline-option${picked === opt.value ? " on" : ""}`}
            onClick={() => {
              setPicked(opt.value);
              onPick(opt.value);
            }}
            disabled={busy}
          >
            <strong>{opt.label}</strong>
            <span className="muted small">{opt.hint}</span>
          </button>
        ))}
      </div>
    </Card>
  );
}

/* ---------------------------------------------------------------- questions ---- */

function QuestionsScreen({ view, onDone, busy }: { view: BaselineView; onDone: (a: Record<string, unknown>) => void; busy: boolean }) {
  const [answers, setAnswers] = useState<Record<string, unknown>>(() => ({ ...(view.answers ?? {}) }));
  const [free, setFree] = useState<string>("");

  const set = (key: string, value: unknown) => setAnswers((a) => ({ ...a, [key]: value }));
  const toggle = (key: string, value: unknown) =>
    setAnswers((a) => {
      const list = Array.isArray(a[key]) ? [...(a[key] as unknown[])] : [];
      const at = list.indexOf(value);
      if (at === -1) list.push(value);
      else list.splice(at, 1);
      return { ...a, [key]: list };
    });

  const timers = timers_visible(view);

  return (
    <Card title="How you work">
      <p>
        {view.questions.length} short questions, in one go. They set the shape of the app around you
        {timers ? "" : ", including keeping clocks off your screen"}. Your timing is still measured
        quietly while you play the games — that is evidence about the games, not a score.
      </p>
      <div className="baseline-q">
        {view.questions.map((q) => {
          const value = answers[q.key];
          const multi = Boolean(q.multi);
          const options = q.options ?? [];
          return (
            <fieldset key={q.key} className="baseline-q-row">
              <legend>
                {q.question} {q.free_text ? <span className="muted small">(optional)</span> : null}
              </legend>
              {q.why ? <p className="muted small" style={{ marginTop: 0 }}>asked because it {q.why}</p> : null}
              {q.free_text ? (
                <input
                  className="input"
                  aria-label={q.question}
                  value={typeof value === "string" ? value : ""}
                  disabled={busy}
                  onChange={(e) => set(q.key, e.target.value)}
                />
              ) : (
                // Choices are buttons with a pressed state, not radios or checkboxes inside labels:
                // one control kind across the baseline, and a keyboard path that is the same everywhere.
                <div className="baseline-options">
                  {options.map((opt) => {
                    const on = multi ? Array.isArray(value) && value.includes(opt) : value === opt;
                    return (
                      <button
                        key={String(opt)}
                        type="button"
                        className={`baseline-option${on ? " on" : ""}`}
                        aria-pressed={on}
                        disabled={busy}
                        onClick={() => (multi ? toggle(q.key, opt) : set(q.key, opt))}
                      >
                        <strong>{String(opt)}</strong>
                      </button>
                    );
                  })}
                </div>
              )}
            </fieldset>
          );
        })}
      </div>
      <div className="row">
        <Btn kind="primary" onClick={() => onDone(answers)} disabled={busy}>
          Continue
        </Btn>
        <Btn kind="ghost" onClick={() => onDone({})} disabled={busy} title="skip these and go straight to the games">
          Skip the questions
        </Btn>
      </div>
      <p className="muted small">
        Skipped questions are stored as skipped, not as answers, and the snapshot says which ones were
        not measured.
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

function TaskScreen({
  task,
  view,
  busy,
  onSend,
}: {
  task: BaselineTask;
  view: BaselineView;
  busy: boolean;
  onSend: (task: BaselineTask, response: Record<string, unknown>) => void;
}) {
  const timers = timers_visible(view);
  const deadline = useRef<number>(Date.now() + Number(task.max_latency_ms ?? 60_000));
  const [now, set_now] = useState(Date.now());
  const [left, set_left] = useState<number | null>(null);

  useEffect(() => {
    deadline.current = Date.now() + Number(task.max_latency_ms ?? 60_000);
    set_now(Date.now());
  }, [task.id, task.max_latency_ms]);

  useEffect(() => {
    if (!timers) {
      set_left(null);
      return;
    }
    const t = setInterval(() => {
      set_now(Date.now());
      set_left(seconds_left(deadline.current, Date.now()));
    }, 500);
    set_left(seconds_left(deadline.current, Date.now()));
    return () => clearInterval(t);
  }, [timers, task.id]);

  return (
    <div className="baseline-task">
      <div className="baseline-task-head">
        {/* "Scored" is the server's word, and it is not the same as "has a practice flag": a
            `phase: "show"` task (the study screen) is looking time, and calling it "measured"
            would claim a trial that was never scored. */}
        <Tag tone="accent">{task.phase === "show" ? "looking time — not scored" : task.practice ? "practice — not scored" : "measured"}</Tag>
        {timers && left !== null ? <span className="muted small" aria-live="off">about {left}s left</span> : null}
      </div>
      {task.prompt ? <h2 className="baseline-prompt">{String(task.prompt)}</h2> : null}
      {task.game === "codewords_study" ? <Study task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "codewords_cued" || task.game === "codewords_next_day" ? (
        <FreeText task={task} busy={busy} onSend={onSend} label="which one went with it?" />
      ) : null}
      {task.game === "quick" ? <Choice task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "trap" ? <Trap task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "vault" ? <Vault task={task} busy={busy} timers={timers} onSend={onSend} /> : null}
      {task.game === "shopkeeper" ? <Shopkeeper task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "machine" ? <Machine task={task} view={view} busy={busy} onSend={onSend} /> : null}
      {task.game === "locked_box" ? <LockedBox task={task} busy={busy} onSend={onSend} /> : null}
      {task.game === "codewords_delayed" ? <Delayed task={task} busy={busy} onSend={onSend} /> : null}
      <p className="muted small">
        {task.game === "vault"
          ? "Arrow keys move, Enter taps, or press 1 to 9 for a tile. The tiles you saw are flashed once, then it is your turn."
          : "Tab moves between the controls; Enter or Space presses the one in focus."}
      </p>
      {now ? null : null}
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
  onSend: (task: BaselineTask, response: Record<string, unknown>) => void;
  label: string;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const box = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    box.current?.focus();
    setText("");
  }, [task.id]);
  const cue = task.cue ? <p className="baseline-cue">{String(task.cue)}</p> : null;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) onSend(task, { text: text.trim() });
      }}
    >
      {cue}
      <label className="baseline-label" htmlFor="baseline-free">
        {label}
      </label>
      <input
        id="baseline-free"
        ref={box}
        className="input"
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

function Choice({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => void }) {
  const options = (task.options as string[]) ?? [];
  const roving = useRovingTabIndex(options.length, "horizontal");
  return (
    <div className="baseline-options" role="group" aria-label="answers">
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
          onClick={() => onSend(task, { value: opt })}
          disabled={busy}
        >
          <strong>{opt}</strong>
        </button>
      ))}
    </div>
  );
}

function Trap({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => void }) {
  return (
    <div>
      {task.story ? <p className="baseline-story">{String(task.story)}</p> : null}
      <Choice task={task} busy={busy} onSend={onSend} />
    </div>
  );
}

/** The study screen: eight pairs, a clock if clocks help, and a ready button. */
function Study({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => void }) {
  const pairs = (task.pairs as { code: string; place: string }[]) ?? [];
  const [left, set_left] = useState<number>(Math.round(Number(task.study_ms ?? 45_000) / 1000));
  const ready = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const until = Date.now() + Number(task.study_ms ?? 45_000);
    const t = setInterval(() => set_left(Math.max(0, seconds_left(until, Date.now()))), 500);
    ready.current?.focus();
    return () => clearInterval(t);
  }, [task.id, task.study_ms]);
  return (
    <div>
      <ul className="baseline-pairs">
        {pairs.map((p) => (
          <li key={p.code}>
            <strong>{p.code}</strong> <span className="muted">→</span> {p.place}
          </li>
        ))}
      </ul>
      <div className="row">
        <Btn
          kind="primary"
          disabled={busy}
          onClick={() => onSend(task, {})}
          title="go on when you are ready; the pairs come back later"
        >
          I have looked at them
        </Btn>
        <span className="muted small" aria-live="polite">
          about {left}s of looking time, and stopping early is allowed
        </span>
      </div>
    </div>
  );
}

/** The vault: a 3x3 grid, flashed, then tapped. Arrow keys move; Enter taps. */
function Vault({
  task,
  busy,
  timers,
  onSend,
}: {
  task: BaselineTask;
  busy: boolean;
  timers: boolean;
  onSend: (t: BaselineTask, r: Record<string, unknown>) => void;
}) {
  const sequence = useMemo(() => (task.sequence as number[]) ?? [], [task]);
  const expected = useMemo(() => vault_expected(sequence, task.answer_direction as string | undefined), [sequence, task]);
  const [showing, set_showing] = useState(true);
  const [flash, set_flash] = useState<number | null>(null);
  const [taps, setTaps] = useState<number[]>([]);
  const [wrong, set_wrong] = useState(false);
  const grid = useRovingTabIndex(9, "horizontal");

  // The flash: one tile at a time, in the order the learner must remember. `prefers-reduced-motion`
  // is respected by shortening the flash rather than removing it -- the sequence *is* the task.
  useEffect(() => {
    set_showing(true);
    set_flash(null);
    setTaps([]);
    set_wrong(false);
    const reduced =
      typeof window !== "undefined" && window.matchMedia
        ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
        : false;
    const on = reduced ? 320 : 620;
    const gap = reduced ? 120 : 240;
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    sequence.forEach((tile, i) => {
      timers.push(
        setTimeout(() => {
          if (!cancelled) set_flash(tile);
        }, i * (on + gap)),
      );
    });
    timers.push(
      setTimeout(() => {
        if (cancelled) return;
        set_flash(null);
        set_showing(false);
        announce(`Your turn. ${sequence.length} tiles, ${task.answer_direction === "backward" ? "in reverse order" : "in the same order"}.`);
      }, sequence.length * (on + gap) + 200),
    );
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id]);

  // Number keys, on the document, while the grid is tappable: 1-9 taps that tile. Arrow keys and
  // Enter already work through the tiles themselves; this is the fast path for a keyboard learner
  // (and the path a harness can drive without pretending to be a mouse).
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
    if (showing || busy) return;
    const next = [...taps, tile];
    if (!vault_prefix_ok(next, expected)) {
      set_wrong(true);
      announce("That is not the order. The answer was sent as it stands.");
      onSend(task, { taps: next });
      return;
    }
    setTaps(next);
    if (next.length === expected.length) onSend(task, { taps: next });
  };

  tapRef.current = tap;

  return (
    <div>
      <div className="vault-grid" role="grid" aria-label="vault tiles" aria-busy={showing}>
        {Array.from({ length: 9 }, (_, i) => (
          <button
            key={i}
            ref={grid.registerRef(i)}
            type="button"
            role="gridcell"
            aria-label={`tile ${i + 1}`}
            tabIndex={grid.getTabIndex(i)}
            className={`vault-tile${flash === i ? " flash" : ""}${taps.includes(i) ? " tapped" : ""}`}
            // Enter and Space tap through the native click; the grid handles the arrows only, so a
            // keyboard tap is recorded once.
            onKeyDown={(e) => grid.onKeyDown(e, i)}
            onClick={() => tap(i)}
            disabled={showing || busy}
          >
            {i + 1}
          </button>
        ))}
      </div>
      <p className="muted small" aria-live="polite">
        {showing
          ? "Watch: the tiles light up in the order to remember."
          : wrong
            ? "That tap was out of order — it is recorded honestly, and the next task is coming."
            : `Tapped ${taps.length} of ${expected.length}. ${task.answer_direction === "backward" ? "Reverse order." : "Same order."}`}
      </p>
      {timers ? null : null}
    </div>
  );
}

/** The shopkeeper: hold three items, answer three quick statements, type the items back. */
function Shopkeeper({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => void }) {
  const items = (task.items as string[]) ?? [];
  const distractors = (task.distractors as { id: string; text: string; answer: boolean }[]) ?? [];
  const [answers, set_answers] = useState<Record<string, boolean>>({});
  const [text, set_text] = useState("");
  const done = distractors.length > 0 && distractors.every((d) => answers[d.id] !== undefined);
  return (
    <div>
      <p className="baseline-label">Hold these three items:</p>
      <ul className="baseline-pairs">
        {items.map((it) => (
          <li key={it}>
            <strong>{it}</strong>
          </li>
        ))}
      </ul>
      <fieldset className="baseline-q-row">
        <legend>Quick answers while you hold them</legend>
        {distractors.map((d) => (
          <div key={d.id} className="row">
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
          onSend(task, { items: parse_items(text), distractors: answers });
        }}
      >
        <label className="baseline-label" htmlFor="baseline-items">
          Now type the three items back, separated by commas
        </label>
        <input
          id="baseline-items"
          className="input"
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
  onSend: (t: BaselineTask, r: Record<string, unknown>) => void;
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

  useEffect(() => {
    set_input("");
    set_guess("");
  }, [task.id]);

  return (
    <div>
      <p className="muted small">
        Probes used: {probes_used} of {max_probes}. Wrong guesses: {wrong_guesses} of {max_wrong}.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const value = Number(input);
          if (input.trim() === "" || Number.isNaN(value) || at_limit) return;
          onSend(task, { action: "probe", value });
          set_input("");
        }}
      >
        <label className="baseline-label" htmlFor="machine-in">
          Put a number in
        </label>
        <input
          id="machine-in"
          className="input"
          inputMode="decimal"
          autoComplete="off"
          value={input}
          onChange={(e) => set_input(e.target.value)}
          disabled={busy || at_limit}
        />
        <div className="row">
          <Btn type="submit" disabled={busy || input.trim() === "" || at_limit}>
            Send it in
          </Btn>
        </div>
      </form>
      <ul className="baseline-machine-out" aria-live="polite">
        {history.map((p, i) => (
          <li key={`${String(p.x)}-${i}`}>
            {String(p.x)} → <strong>{String(p.out)}</strong>
          </li>
        ))}
        {history.length === 0 ? <li className="muted">Nothing in yet: the machine answers every number you send.</li> : null}
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (guess.trim()) onSend(task, { action: "guess", text: guess.trim() });
        }}
      >
        <label className="baseline-label" htmlFor="machine-guess">
          What is the rule? Say it in words (&ldquo;times 3, add 2&rdquo;).
        </label>
        <input
          id="machine-guess"
          className="input"
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
function LockedBox({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => void }) {
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
    <div>
      <p className="baseline-story">
        The first four numbers: {cases.map((c) => c.value).join(", ")}. What is the fifth?
      </p>
      <p className="muted small">
        This one is hard on purpose. You can buy a hint, try again, or walk away — all three are
        useful information and none of them is a failure. Attempts: {attempts_used} of {max_attempts}.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim() && !out_of_attempts) onSend(task, { action: "attempt", value: Number(value) });
        }}
      >
        <label className="baseline-label" htmlFor="box-answer">
          Your answer for the fifth number
        </label>
        <input
          id="box-answer"
          className="input"
          inputMode="numeric"
          value={value}
          onChange={(e) => set_value(e.target.value)}
          disabled={busy || out_of_attempts}
        />
        <div className="row">
          <Btn type="submit" kind="primary" disabled={busy || out_of_attempts || value.trim() === ""}>
            Try this answer
          </Btn>
          <Btn kind="ghost" disabled={busy} onClick={() => onSend(task, { action: "walk_away" })} title="stop here; walking away is recorded as what it is">
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
                  onClick={() => onSend(task, { action: "hint", hint_id: h.id })}
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
function Delayed({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => void }) {
  if (task.action === "predict") {
    return <Predict task={task} busy={busy} onSend={onSend} />;
  }
  if (task.kind === "free") {
    return <FreeText task={task} busy={busy} onSend={onSend} label="Type the code words you remember, separated by commas. The places can wait." placeholder="type the words here" />;
  }
  return <FreeText task={task} busy={busy} onSend={onSend} label={String(task.prompt ?? "which code word went with it?")} />;
}

function Predict({ task, busy, onSend }: { task: BaselineTask; busy: boolean; onSend: (t: BaselineTask, r: Record<string, unknown>) => void }) {
  const max = Number(task.max_value ?? 8);
  const [value, set_value] = useState<number | null>(null);
  return (
    <div>
      <div className="baseline-options" role="group" aria-label="how many will you remember">
        {Array.from({ length: max + 1 }, (_, n) => (
          <button
            key={n}
            type="button"
            aria-pressed={value === n}
            className={`baseline-option${value === n ? " on" : ""}`}
            onClick={() => set_value(n)}
            disabled={busy}
          >
            <strong>{n}</strong>
          </button>
        ))}
      </div>
      <div className="row">
        <Btn kind="primary" disabled={busy || value === null} onClick={() => onSend(task, { value })}>
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
          await submit_trial(run_id, {
            task_id: task.id,
            response: { text: text.trim() },
            latency_ms: Math.max(0, Date.now() - shown_at.current),
            wager: timers ? wager : null,
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
  onChanged,
}: {
  snap: BaselineSnapshot | null;
  view: BaselineView;
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
          {factors.map((f) => (
            <li key={f.key} className={f.state === "measured" ? "" : "muted"}>
              <div className="row">
                <strong style={{ flex: 1 }}>{f.title}</strong>
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
            Recorded as not measured, not as zero: {snap.completion.not_measured.join(", ")}.
          </p>
        </Card>
      ) : null}

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
