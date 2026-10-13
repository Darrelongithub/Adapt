"use client";

/** A lesson: a machine you can pause, argue with, and re-run on your own numbers.
 *
 * This screen is deliberately not a video player with quiz popups. Three decisions shape it:
 *
 * 1. **The trace is the backend's, the picture is ours.** Every frame arrives from
 *    `POST /lessons/open`, computed by `learning/sims/*`. This file recomputes nothing and
 *    stores nothing it was not given, so a refresh and a restart land on the same frame —
 *    `lesson_runs.frame_index` is the memory, not React state.
 * 2. **A prediction is answered before the frame that confirms it.** The gate blocks "next"
 *    on a prompt frame, because a learner who watches the machine discard half the list has
 *    seen an explanation; a learner who *said* which half survives has made a claim the trace
 *    can agree or disagree with. That disagreement is the only thing AI 1 is allowed to learn
 *    from here, and it is not a grade — the review screen says so in the API's own words.
 * 3. **"Why?" is a question, not a hint button.** It asks the simulation first. When the
 *    simulation has no answer and no model is configured, you get that sentence, not a
 *    confident paragraph.
 *
 * Nothing here evaluates writing. Free text goes to `/explain` and comes back as an answer
 * about the machine; it is never scored, and `consent_share_with_ai` still decides whether a
 * model sees any of it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { get, post } from "@/lib/api";
import { SceneStage } from "@/components/scenes";
import { Btn, Card, Err, Field, Notice, Tag, Textarea, Trace, announce } from "@/components/ui";
import { useRovingTabIndex } from "@/lib/a11y/useRovingTabIndex";
import {
  autoplayAllowed,
  canAdvance,
  fmtClock,
  frameMs,
  init,
  keyAction,
  pendingPrompt,
  progressPercent,
  reducer,
  unanswered,
  type Action,
  type PlayerState,
} from "@/lib/engine/player";
import type {
  Evidence,
  Explanation,
  Frame,
  Graded,
  Kinds,
  OpenedRun,
  Prompt,
  Review,
  Scene,
  SceneList,
} from "@/lib/engine/types";

const SESSION_KEY = "adapt:lesson:session";

export default function LessonPlayer() {
  const [catalogue, setCatalogue] = useState<SceneList | null>(null);
  const [kinds, setKinds] = useState<Kinds | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [opened, setOpened] = useState<OpenedRun | null>(null);
  const [player, setPlayer] = useState<PlayerState>(() => init([]));
  const [grades, setGrades] = useState<Record<number, Graded>>({});
  const [why, setWhy] = useState<Explanation | null>(null);
  const [whyDraft, setWhyDraft] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reduced, setReduced] = useState(false);
  const sessionRef = useRef<string | null>(null);
  const savedIndex = useRef<number>(-1);
  const openedRef = useRef<OpenedRun | null>(null);
  openedRef.current = opened;

  const scene = useMemo(
    () => (catalogue?.scenes ?? []).find((s) => s.id === opened?.scene_id) ?? null,
    [catalogue, opened],
  );
  const frame: Frame | undefined = player.frames[player.index];
  const asked: Prompt | null = frame?.prompt ?? null;
  const filmRoving = useRovingTabIndex(Math.max(player.frames.length, 1), "horizontal");

  useEffect(() => {
    filmRoving.setActiveIndex(player.index);
  }, [player.index]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (opened && frame) announce(`Frame ${player.index + 1} of ${player.frames.length}: ${frame.title ?? frame.kind ?? ""}`);
  }, [player.index, opened, frame, player.frames.length]);

  /* ---------------------------------------------------------------- catalogue ---- */

  useEffect(() => {
    let alive = true;
    (async () => {
      const [list, k, ev] = await Promise.all([
        get<SceneList>("/api/v1/lessons"),
        get<Kinds>("/api/v1/lessons/kinds"),
        get<Evidence>("/api/v1/lessons/evidence"),
      ]);
      if (!alive) return;
      if (list.error) setLoadError(list.error);
      setCatalogue(list.data);
      setKinds(k.data);
      setEvidence(ev.data);
      if (typeof window !== "undefined") {
        const wanted = new URLSearchParams(window.location.search).get("scene");
        if (wanted) void open(wanted, true);
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  /** A lesson session, so scene_* events land somewhere the learner can read back. */
  const ensureSession = useCallback(async (): Promise<string | null> => {
    if (sessionRef.current) return sessionRef.current;
    if (typeof window !== "undefined") {
      const stored = window.sessionStorage.getItem(SESSION_KEY);
      if (stored) {
        sessionRef.current = stored;
        return stored;
      }
    }
    try {
      const out = await post<{ session_id: string }>("/api/v1/sessions", { mode: "lesson" });
      sessionRef.current = out.session_id;
      if (typeof window !== "undefined") window.sessionStorage.setItem(SESSION_KEY, out.session_id);
      return out.session_id;
    } catch (err) {
      // A lesson without a session still plays; it just measures nothing, and saying so
      // is better than refusing to start.
      setError(`could not open a session for this lesson (${(err as Error).message}) — it will still play, but nothing will be recorded`);
      return null;
    }
  }, []);

  /* ---------------------------------------------------------------------------- open ---- */

  const open = useCallback(
    async (sceneId: string, deepLink = false) => {
      setBusy("open");
      setError(null);
      setWhy(null);
      setGrades({});
      setReview(null);
      try {
        const session_id = await ensureSession();
        const out = await post<OpenedRun>("/api/v1/lessons/open", { scene_id: sceneId, session_id });
        setOpened(out);
        savedIndex.current = out.frame_index;
        const gate = true;
        setPlayer({ ...init(out.frames, out.frame_index, gate), playing: !out.resumed && autoplayAllowed(reduced) });
        setNotice(
          out.resumed
            ? `back at frame ${out.frame_index + 1} of ${out.frame_count} — that is where you stopped, saved on the server, not in this tab`
            : deepLink
              ? "opened from the link you were given"
              : null,
        );
        if (!out.renderer?.available) {
          setError(out.renderer?.note ?? "this scene's kind has no renderer here; the frames are still the real ones");
        }
        if (typeof window !== "undefined") {
          const url = new URL(window.location.href);
          url.searchParams.set("scene", sceneId);
          window.history.replaceState(null, "", url.toString());
        }
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(null);
      }
    },
    [ensureSession, reduced],
  );

  /* ------------------------------------------------------------------- the player ---- */

  const dispatch = useCallback((action: Action) => {
    const current = openedRef.current;
    setPlayer((prev) => {
      const next = reducer(prev, action);
      if (next.index !== prev.index && current) {
        // the write is fire-and-forget on purpose: a slow database must not delay a frame,
        // and the last accepted index is what a resume reads, so only the newest matters
        if (next.index !== savedIndex.current) {
          savedIndex.current = next.index;
          void post(`/api/v1/lessons/run/${current.run_id}/progress`, {
            frame_index: next.index,
            action: action.type === "jump" ? "seek" : action.type === "back" ? "back" : "step",
            ms_taken: 1500,
          }).catch((err: Error) => setError(`your place was not saved (${err.message})`));
        }
      }
      return next;
    });
  }, []);

  useEffect(() => {
    if (!player.playing) return;
    const ms = frameMs(player.frames[player.index], player.speed);
    const timer = window.setTimeout(() => {
      if (pendingPrompt(player) && !canAdvance(player)) {
        setPlayer((p) => ({ ...p, playing: false }));
        setNotice("the machine knows what happens next. say it first — that is the whole point of this frame.");
        return;
      }
      dispatch({ type: "step" });
    }, ms);
    return () => window.clearTimeout(timer);
  }, [player]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!openedRef.current) return;
      const action = keyAction(e.key, e.target as HTMLElement | null);
      if (!action) return;
      e.preventDefault();
      if (action === "play") dispatch({ type: "toggle" });
      if (action === "step") dispatch({ type: "step" });
      if (action === "back") dispatch({ type: "back" });
      if (action === "jump-end") dispatch({ type: "jump", index: player.frames.length - 1 });
      if (action === "jump-start") dispatch({ type: "jump", index: 0 });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dispatch, player.frames.length]);

  const answer = useCallback(
    async (choice: string) => {
      const current = openedRef.current;
      if (!current || !frame) return;
      setBusy(`predict:${frame.i}`);
      try {
        const graded = await post<Graded>(`/api/v1/lessons/run/${current.run_id}/predict`, {
          frame_index: frame.i,
          choice,
          ms_taken: 3000,
        });
        setGrades((g) => ({ ...g, [frame.i]: graded }));
        dispatch({ type: "answer", index: frame.i, value: choice });
      } catch (err) {
        setError(`that prediction could not be graded (${(err as Error).message})`);
      } finally {
        setBusy(null);
      }
    },
    [dispatch, frame],
  );

  const askWhy = useCallback(async () => {
    const current = openedRef.current;
    if (!current || !frame) return;
    setBusy("why");
    setWhy(null);
    try {
      setWhy(await post<Explanation>(`/api/v1/lessons/run/${current.run_id}/explain`, { frame_index: frame.i, question: whyDraft }));
    } catch (err) {
      setError(`no answer came back (${(err as Error).message})`);
    } finally {
      setBusy(null);
    }
  }, [frame, whyDraft]);

  const finish = useCallback(async () => {
    const current = openedRef.current;
    if (!current) return;
    setBusy("finish");
    try {
      const out = await post<{ review: Review }>(`/api/v1/lessons/run/${current.run_id}/finish`, {});
      setReview(out.review);
      setPlayer((p) => ({ ...p, playing: false }));
      const ev = await get<Evidence>("/api/v1/lessons/evidence");
      setEvidence(ev.data);
      setCatalogue((c) => (c ? { ...c, scenes: c.scenes } : c));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }, []);

  const changeVariables = useCallback(
    async (params: Record<string, unknown>, note: string) => {
      const current = openedRef.current;
      if (!current) return;
      setBusy("variables");
      try {
        const out = await post<OpenedRun & { ok: boolean; warnings?: string[]; derived_from?: string | null }>(
          `/api/v1/lessons/run/${current.run_id}/variables`,
          { params, note },
        );
        if (out.ok === false) throw new Error("those numbers were refused");
        setOpened(out);
        savedIndex.current = out.frame_index;
        setPlayer(init(out.frames, out.frame_index, true));
        setGrades({});
        setWhy(null);
        setReview(null);
        setNotice(
          `this is your own copy of the scene now (${out.frames.length} frames) — the run with the old numbers is kept, with the predictions you made in it`,
        );
        const list = await get<SceneList>("/api/v1/lessons");
        setCatalogue(list.data);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  /* ---------------------------------------------------------------------------- view ---- */

  const percent = progressPercent(player.index, player.frames.length);
  const owed = opened ? unanswered(opened.frames, player.answers) : [];

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Interactive lessons</h1>
          <p className="max-w-[70ch] text-[13px] text-[var(--muted)]">
            {catalogue?.note ??
              "every scene is computed by a simulation in the backend; nothing here is a video, and nothing here is graded into a mastery number"}
          </p>
        </div>
        <div className="flex items-center gap-2 text-[12px] text-[var(--muted)]">
          <Tag tone="line">{catalogue?.counts.scenes ?? 0} scenes</Tag>
          <Tag tone="line">{catalogue?.counts.engine_kinds ?? 0} engines</Tag>
        </div>
      </header>

      {loadError ? <Err error={loadError} /> : null}
      {error ? <Err error={error} /> : null}
      {notice ? (
        <p className="flex items-start justify-between gap-3 rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-[13px]">
          <span>{notice}</span>
          <button type="button" className="text-[var(--muted)] hover:text-[var(--ink)]" onClick={() => setNotice(null)} aria-label="dismiss">
            ✕
          </button>
        </p>
      ) : null}

      {!opened ? (
        <div className="grid gap-3 md:grid-cols-2">
          {!catalogue && !loadError ? (
            <Card title="lessons">
              <p className="text-[13px] text-[var(--muted)]">
                asking the backend which scenes it has validated… the frames are computed there, so there is nothing to
                show until it answers.
              </p>
            </Card>
          ) : null}
          {(catalogue?.scenes ?? []).map((s) => (
            <SceneCard
              key={s.id}
              scene={s}
              summary={kinds?.kinds[s.kind]?.summary}
              busy={busy === "open"}
              onOpen={() => void open(s.id)}
            />
          ))}
          {!catalogue?.scenes.length ? (
            <Card title="nothing seeded">
              <p className="text-[13px] text-[var(--muted)]">
                the backend has stored no scenes. they are seeded at startup from validated specifications, so an empty
                list means the seed step did not run.
              </p>
              <p className="mt-2 text-[12px] text-[var(--muted)]">
                restart the backend: seeding runs once at startup, and it refuses to store a spec the validator
                rejects.
              </p>
            </Card>
          ) : null}
          {evidence ? <EvidencePanel evidence={evidence} /> : null}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <Card title={opened.title} right={<Tag tone="line">{opened.kind}</Tag>}>
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center gap-2">
                <Btn kind="primary" onClick={() => dispatch({ type: "toggle" })}>
                  {player.playing ? "pause" : player.index >= player.frames.length - 1 ? "play again" : "play"}
                </Btn>
                <Btn onClick={() => dispatch({ type: "back" })} disabled={player.index === 0} title="left arrow">
                  ← back
                </Btn>
                <Btn
                  onClick={() => dispatch({ type: "step" })}
                  disabled={player.index >= player.frames.length - 1}
                  title="right arrow"
                >
                  next →
                </Btn>
                <Btn onClick={() => void finish()} disabled={busy === "finish"}>
                  end &amp; review
                </Btn>
                <label className="ml-auto flex items-center gap-2 text-[12px] text-[var(--muted)]">
                  speed
                  <select
                    className="rounded border border-[var(--line)] bg-transparent px-1 py-0.5 text-[12px]"
                    value={player.speed}
                    onChange={(e) => dispatch({ type: "speed", speed: Number(e.target.value) })}
                  >
                    <option value={0.5}>0.5×</option>
                    <option value={1}>1×</option>
                    <option value={1.5}>1.5×</option>
                    <option value={2}>2×</option>
                  </select>
                </label>
              </div>

              <div className="h-1 w-full overflow-hidden rounded bg-[var(--panel)]" aria-hidden="true">
                <div className="h-full bg-[var(--accent)]" style={{ width: `${percent}%` }} />
              </div>
              <input
                type="range"
                min={0}
                max={Math.max(player.frames.length - 1, 0)}
                value={player.index}
                onChange={(e) => dispatch({ type: "jump", index: Number(e.target.value) })}
                aria-label="frame"
                className="w-full"
              />

              {player.frames.length ? (
                <div className="film" role="tablist" aria-label="Lesson frames">
                  {player.frames.map((f, idx) => (
                    <button
                      key={f.i}
                      role="tab"
                      aria-selected={player.index === idx}
                      tabIndex={filmRoving.getTabIndex(idx)}
                      ref={filmRoving.registerRef(idx)}
                      onKeyDown={(e) => filmRoving.onKeyDown(e, idx)}
                      onFocus={() => filmRoving.setActiveIndex(idx)}
                      onClick={() => dispatch({ type: "jump", index: idx })}
                      className="btn-ctl btn-secondary frame"
                      title={f.title ?? `frame ${idx + 1}`}
                    >
                      {String(idx + 1).padStart(2, "0")} {(f.title ?? f.kind ?? "frame").slice(0, 14)}
                    </button>
                  ))}
                </div>
              ) : null}

              <div className="frame-main" role="tabpanel">
                <SceneStage frame={frame} kind={opened.kind} hasRenderer={opened.renderer?.available !== false} />
              </div>

              {asked ? (
                <PredictionPanel
                  frame={frame!}
                  prompt={asked}
                  graded={grades[frame!.i]}
                  answer={player.answers[frame!.i]}
                  busy={busy === `predict:${frame!.i}`}
                  onAnswer={(choice) => void answer(choice)}
                />
              ) : owed.length ? (
                <p className="text-[12px] text-[var(--warn)]">
                  you passed {owed.length} moment{owed.length === 1 ? "" : "s"} that asked you to say what happens next.
                  <button
                    type="button"
                    className="btn-ctl btn-ghost ml-2"
                    onClick={() => dispatch({ type: "jump", index: owed[0] })}
                  >
                    go to the first one
                  </button>
                </p>
              ) : null}

              <WhyPanel
                value={whyDraft}
                onChange={setWhyDraft}
                onAsk={() => void askWhy()}
                busy={busy === "why"}
                answer={why}
                frameIndex={player.index}
              />

              {reduced ? (
                <p className="text-[11px] text-[var(--muted)]">
                  your system asks for reduced motion, so nothing starts moving on its own — play is a button, and the
                  arrows still step.
                </p>
              ) : null}
            </div>
          </Card>

          <div className="flex flex-col gap-4">
            {opened.framing?.intro ? <Card title="before you watch"><p className="text-[13px]">{opened.framing.intro}</p></Card> : null}
            {scene?.last_run ? (
              <Card title="this run">
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[12px]">
                  <dt className="text-[var(--muted)]">status</dt>
                  <dd className="font-mono">{scene.last_run.status}</dd>
                  <dt className="text-[var(--muted)]">frames seen</dt>
                  <dd className="font-mono">
                    {scene.last_run.frames_viewed} / {opened.frame_count}
                  </dd>
                  <dt className="text-[var(--muted)]">predictions</dt>
                  <dd className="font-mono">
                    {scene.last_run.correct}/{scene.last_run.predictions} right
                  </dd>
                  <dt className="text-[var(--muted)]">time</dt>
                  <dd className="font-mono">{fmtClock(scene.last_run.seconds_watched)}</dd>
                  <dt className="text-[var(--muted)]">numbers changed</dt>
                  <dd className="font-mono">{scene.last_run.param_changes}</dd>
                </dl>
              </Card>
            ) : null}
            <VariablesPanel
              kind={opened.kind}
              kinds={kinds}
              params={opened.params}
              busy={busy === "variables"}
              onSubmit={changeVariables}
            />
            {review ? <ReviewPanel review={review} frames={opened.frames} /> : null}
            <Trace title="what this screen is allowed to know">
              <p className="text-[12px]">
                positions, which choice you made, how long you took, and whether the simulation agreed. no keystrokes,
                no screenshots, and prediction accuracy is never fed into a mastery number.{" "}
                <a className="link-inline" href="/progress">
                  see what was recorded
                </a>
                .
              </p>
            </Trace>
            <Btn kind="ghost" onClick={() => setOpened(null)}>
              ← back to the list
            </Btn>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------- pieces ---- */

function SceneCard({
  scene,
  summary,
  busy,
  onOpen,
}: {
  scene: Scene;
  summary?: string;
  busy: boolean;
  onOpen: () => void;
}) {
  const resume = scene.last_run && scene.last_run.status !== "finished" ? scene.last_run : null;
  return (
    <Card
      title={scene.title}
      tone={resume ? "ask" : undefined}
      right={
        <span className="flex items-center gap-1">
          <Tag tone="line">{scene.kind}</Tag>
          <Tag tone={scene.origin === "builtin" ? "line" : "accent"}>{scene.origin}</Tag>
        </span>
      }
    >
      <div className="flex flex-col gap-2">
        <p className="text-[13px] text-[var(--muted)]">{summary ?? scene.intent}</p>
        <p className="text-[12px] text-[var(--muted)]">
          {scene.frame_count} frames · {scene.prediction_count} moments where you say what happens next
          {scene.citations?.length ? ` · ${scene.citations.length} source${scene.citations.length === 1 ? "" : "s"}` : ""}
        </p>
        {!scene.has_renderer ? (
          <p className="text-[12px] text-[var(--warn)]">
            no renderer for this kind — it will play as narration and state values, not as a picture.
          </p>
        ) : null}
        {resume ? (
          <p className="text-[12px] text-[var(--accent)]">
            you stopped at frame {resume.frame_index + 1} of {scene.frame_count}
            {resume.predictions ? ` · ${resume.correct}/${resume.predictions} predictions right` : ""}
          </p>
        ) : null}
        <div className="mt-1">
          <Btn kind="primary" onClick={onOpen} disabled={busy}>
            {resume ? "resume" : scene.has_renderer ? "open" : "open anyway"}
          </Btn>
        </div>
      </div>
    </Card>
  );
}

function PredictionPanel({
  frame,
  prompt,
  graded,
  answer,
  busy,
  onAnswer,
}: {
  frame: Frame;
  prompt: Prompt;
  graded?: Graded;
  answer?: string;
  busy: boolean;
  onAnswer: (choice: string) => void;
}) {
  const choices = Array.isArray(prompt.choices) ? prompt.choices.filter((c): c is string => typeof c === "string") : [];
  return (
    <Card title="say what happens next" tone={graded ? (graded.correct ? "good" : "warn") : "ask"}>
      <div className="flex flex-col gap-2">
        <p className="text-[14px]">{prompt.question}</p>
        {choices.length ? (
          <div className="flex flex-wrap gap-2">
            {choices.map((c, ci) => {
              const chosen = answer === c;
              const tone = graded && chosen ? (graded.correct ? "correct" : "incorrect") : "";
              return (
                <button
                  key={`${c}#${ci}`}
                  type="button"
                  role="radio"
                  aria-checked={chosen}
                  disabled={busy || !!graded}
                  onClick={() => onAnswer(c)}
                  className={`option-card ${tone}`}
                  style={{ width: "auto" }}
                >
                  <span className="radio-dot" aria-hidden="true" />
                  {c}
                </button>
              );
            })}
          </div>
        ) : (
          <p className="text-[12px] text-[var(--muted)]">
            this frame asks for something this build does not have a control for (prediction type{" "}
            <span className="font-mono">{prompt.type}</span>), so it will not block you.
          </p>
        )}
        {busy ? <p className="text-[12px] text-[var(--muted)]">asking the simulation…</p> : null}
        {graded ? (
          <div className="text-[13px]">
            {graded.correct ? (
              <p className="text-[var(--good)]">
                the trace agrees — {graded.expected !== null && graded.expected !== undefined ? <span className="font-mono">{graded.expected}</span> : "you had it"}.
              </p>
            ) : (
              <p className="text-[var(--warn)]">
                the machine does something else: <span className="font-mono">{graded.expected ?? "nothing was answerable here"}</span>
                {graded.why ? <span className="mt-1 block text-[var(--muted)]">{graded.why}</span> : null}
              </p>
            )}
            <p className="mt-1 text-[11px] text-[var(--muted)]">
              frame {frame.i + 1} · graded by the simulation on the backend, not by this page ·{" "}
              {graded.totals?.predictions ?? 0} prediction{graded.totals?.predictions === 1 ? "" : "s"} so far, {graded.totals?.correct ?? 0} right
            </p>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function WhyPanel({
  value,
  onChange,
  onAsk,
  busy,
  answer,
  frameIndex,
}: {
  value: string;
  onChange: (v: string) => void;
  onAsk: () => void;
  busy: boolean;
  answer: Explanation | null;
  frameIndex: number;
}) {
  return (
    <Card title="why this frame" tone="muted">
      <div className="flex flex-col gap-2">
        <Textarea
          rows={2}
          value={value}
          placeholder={`frame ${frameIndex + 1}: why does it do that? (empty asks the narration)`}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              onAsk();
            }
          }}
        />
        <div className="flex items-center gap-2">
          <Btn kind="primary" onClick={onAsk} disabled={busy}>
            {busy ? "asking" : "ask"}
          </Btn>
          <span className="text-[11px] text-[var(--muted)]">⌘/ctrl + enter</span>
        </div>
        {answer ? (
          <div className="flex flex-col gap-1">
            <p className="text-[13px] whitespace-pre-wrap">{answer.text}</p>
            <p className="text-[11px] text-[var(--muted)]">
              source: <span className="font-mono">{answer.source}</span>
              {answer.cached ? " · from the cache, so no model was asked" : ""}
              {answer.model ? ` · ${answer.model}` : ""}
              {answer.tokens_out ? ` · ${answer.tokens_out} tokens out` : ""}
            </p>
            {answer.note ? <p className="text-[12px] text-[var(--warn)]">{answer.note}</p> : null}
            {answer.citations?.length ? (
              <ul className="mt-1 flex flex-col gap-0.5 text-[11px] text-[var(--muted)]">
                {answer.citations.map((c, ci) => (
                  <li key={`${c.ref}#${ci}`}>
                    <span className="font-mono">{c.ref}</span> — {c.claim}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function VariablesPanel({
  kind,
  kinds,
  params,
  busy,
  onSubmit,
}: {
  kind: string;
  kinds: Kinds | null;
  params: Record<string, unknown>;
  busy: boolean;
  onSubmit: (params: Record<string, unknown>, note: string) => void;
}) {
  const spec = kinds?.kinds?.[kind]?.params ?? {};
  const editable = Object.entries(spec).filter(([, p]) => p.ui !== "hidden");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const field = (name: string) => draft[name] ?? format(params[name] ?? spec[name]?.default ?? "");
  const setField = (name: string, v: string) => {
    setProblem(null);
    setDraft((d) => ({ ...d, [name]: v }));
  };

  const submit = () => {
    const out: Record<string, unknown> = {};
    for (const [name, p] of editable) {
      const raw = (draft[name] ?? format(params[name] ?? p.default ?? "")).trim();
      if (raw === "" && p.default === null) continue;
      if (p.type === "int") {
        const n = Number(raw);
        if (!Number.isFinite(n)) {
          setProblem(`${p.label ?? name} has to be a number — got "${raw}"`);
          return;
        }
        out[name] = Math.trunc(n);
      } else if (p.type === "ints" || p.type === "lines") {
        const parts = raw.split(/[,\s]+/).filter((x) => x.length > 0);
        if (p.type === "ints") {
          const nums = parts.map((x) => Number(x));
          if (nums.some((n) => !Number.isFinite(n))) {
            setProblem(`${p.label ?? name}: "${parts.find((x) => !Number.isFinite(Number(x)))}" is not a number`);
            return;
          }
          out[name] = nums;
        } else {
          out[name] = parts;
        }
      } else if (p.type === "bool") {
        out[name] = raw === "true" || raw === "1" || raw === "yes";
      } else {
        out[name] = raw;
      }
    }
    onSubmit(out, note);
  };

  return (
    <Card title="your numbers" tone="muted">
      <div className="flex flex-col gap-2">
        <p className="text-[12px] text-[var(--muted)]">
          changing these makes a <em>new</em> scene, validated before it is stored, and starts a fresh run — the old
          one keeps its predictions instead of being rewritten.
        </p>
        {editable.map(([name, p]) => (
          <Field key={name} label={p.label ?? name} hint={p.help}>
            <input
              className="w-full rounded border border-[var(--line)] bg-transparent px-2 py-1 font-mono text-[12px]"
              name={name}
              value={field(name)}
              onChange={(e) => setField(name, e.target.value)}
            />
          </Field>
        ))}
        <Field label="why did you change it?" hint="kept with your copy; it is how a scene of yours can be recognised as yours">
          <input
            className="w-full rounded border border-[var(--line)] bg-transparent px-2 py-1 text-[12px]"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
        {problem ? <Notice tone="bad">{problem}</Notice> : null}
        <Btn onClick={submit} disabled={busy || editable.length === 0}>
          {busy ? "re-validating" : "re-run with these"}
        </Btn>
        {!editable.length ? (
          <p className="text-[12px] text-[var(--warn)]">
            the backend tells us nothing about the parameters of a <span className="font-mono">{kind}</span> scene, so
            there is nothing safe to edit.
          </p>
        ) : null}
      </div>
    </Card>
  );
}

function ReviewPanel({ review, frames }: { review: Review; frames: Frame[] }) {
  const skipped = review.skipped_predictions ?? [];
  return (
    <Card title="what you just did" tone="good">
      <div className="flex flex-col gap-2 text-[13px]">
        <p className="text-[12px] text-[var(--muted)]">
          {review.totals.accuracy === null
            ? "you made no predictions, so there is nothing to compare — the frames still count as seen"
            : `you called ${review.totals.correct} of ${review.totals.predictions} before the machine confirmed them (one third would be guessing)`}
        </p>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[12px]">
          <dt className="text-[var(--muted)]">status</dt>
          <dd className="font-mono">{review.status}</dd>
          <dt className="text-[var(--muted)]">frames</dt>
          <dd className="font-mono">
            {review.frames_viewed} seen, furthest {review.furthest_frame + 1} of {review.frame_count}
          </dd>
          <dt className="text-[var(--muted)]">time</dt>
          <dd className="font-mono">{fmtClock(review.totals.seconds_watched)}</dd>
          <dt className="text-[var(--muted)]">your numbers</dt>
          <dd className="font-mono">{review.totals.param_changes} change{review.totals.param_changes === 1 ? "" : "s"}</dd>
        </dl>
        {skipped.length ? (
          <p className="text-[12px] text-[var(--warn)]">
            {skipped.length} frame{skipped.length === 1 ? "" : "s"} asked and you moved past:{" "}
            {skipped.map((i) => frames[i]?.prompt?.type ?? `#${i}`).join(", ")}
          </p>
        ) : null}
        {review.predictions?.length ? (
          <ul className="flex flex-col gap-1 text-[12px]">
            {review.predictions.map((p, pi) => (
              <li key={`${p.frame}#${pi}`} className="flex items-baseline gap-2">
                <span className="font-mono text-[var(--muted)]">f{p.frame + 1}</span>
                <span className={p.correct ? "text-[var(--good)]" : "text-[var(--bad)]"}>{p.correct ? "✓" : "✗"}</span>
                <span className="font-mono">{p.said}</span>
                {p.correct ? null : <span className="font-mono text-[var(--muted)]">not {p.expected}</span>}
              </li>
            ))}
          </ul>
        ) : null}
        {review.scene.framing?.outro ? <p className="text-[12px] text-[var(--muted)]">{review.scene.framing.outro}</p> : null}
      </div>
    </Card>
  );
}

function EvidencePanel({ evidence }: { evidence: Evidence }) {
  const s = evidence.summary;
  return (
    <Card title="what has been recorded" tone="muted">
      <div className="flex flex-col gap-1 text-[12px]">
        <p>
          {s.runs} run{s.runs === 1 ? "" : "s"} · {s.predictions} prediction{s.predictions === 1 ? "" : "s"}
          {s.accuracy === null ? "" : ` · ${Math.round(s.accuracy * 100)}% right before the answer was shown`}
        </p>
        <p className="text-[var(--muted)]">{s.note}</p>
        <p className="text-[var(--muted)]">
          narration cache: {evidence.narration_cache.entries} entries, {evidence.narration_cache.model_calls_saved} model
          calls avoided
        </p>
        <ul className="mt-1 flex flex-col gap-0.5 text-[var(--muted)]">
          {evidence.not_evidence.map((line, li) => (
            <li key={`ne-${li}`}>· {line}</li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

function format(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.map((x) => String(x)).join(", ");
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}
