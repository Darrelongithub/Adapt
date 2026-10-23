"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import { TestItemCard } from "@/components/tests/items";
import { isMock, post, type Me } from "@/lib/api";
import {
  DEMO_GAME_COUNT,
  analyseDemo,
  builtInDemoSet,
  demoEvidenceRows,
  demoGameName,
  demoRunId,
  demoTransition,
  firstDemoItem,
  nextDemoItem,
  validatedDemoItems,
  withDemoTimeout,
  type DemoPlay,
  type DemoSet,
} from "@/lib/demo";
import type { ItemResult, PlanItem } from "@/lib/tests/registry";

import "@/components/tests/tests.css";
import "./demo.css";

type Stage = "welcome" | "preparing" | "games" | "analysis";
type SaveState = "idle" | "saving" | "saved" | "unsaved" | "built-in";
type PlanReply = { items: unknown[]; run_id: string };

export default function DemoPage() {
  const [stage, setStage] = useState<Stage>("welcome");
  const [demoSet, setDemoSet] = useState<DemoSet | null>(null);
  const [current, setCurrent] = useState<PlanItem | null>(null);
  const [played, setPlayed] = useState<DemoPlay[]>([]);
  const [transition, setTransition] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const guestReady = useRef(false);
  const saveRequest = useRef(0);

  const openSet = useCallback((set: DemoSet) => {
    const first = firstDemoItem(set.items);
    // Both the validated server plan and the built-in set guarantee three items.
    if (!first) return;
    setDemoSet(set);
    setCurrent(first);
    setPlayed([]);
    setTransition(null);
    setSaveState(set.origin === "built-in" ? "built-in" : "idle");
    setStage("games");
  }, []);

  const prepare = useCallback(async () => {
    saveRequest.current += 1;
    setStage("preparing");
    setCurrent(null);
    setTransition(null);
    try {
      const set = await withDemoTimeout((async (): Promise<DemoSet> => {
        // The mock tour stays completely local. The real path creates no password and
        // never opts the learner into sharing their answers outside this installation.
        if (isMock()) return builtInDemoSet();
        if (!guestReady.current) {
          await post<Me>("students", {
            as_guest: true,
            display_name: "Demo guest",
            consent_analytics: true,
            consent_share_with_llm: false,
          });
          guestReady.current = true;
        }
        const plan = await post<PlanReply>("tests/plan", {
          subject: "Learning skills",
          level: 2,
          style: "see",
          goal: "skills",
        });
        const items = validatedDemoItems(plan.items);
        if (!items || !plan.run_id) throw new Error("The demo plan was not usable");
        return { items, runId: plan.run_id, origin: "server" };
      })());
      openSet(set);
    } catch {
      // A slow or unavailable content request must not strand a room at a spinner.
      openSet(builtInDemoSet());
    }
  }, [openSet]);

  const saveEvidence = useCallback(async (rows: DemoPlay[]) => {
    const runId = demoRunId(rows);
    if (!runId) {
      setSaveState("built-in");
      return;
    }
    const request = ++saveRequest.current;
    setSaveState("saving");
    try {
      await post("tests/evidence", {
        run_id: runId,
        subject_id: null,
        rows: demoEvidenceRows(rows),
      });
      if (request === saveRequest.current) setSaveState("saved");
    } catch {
      if (request === saveRequest.current) setSaveState("unsaved");
    }
  }, []);

  const onDone = useCallback((result: ItemResult) => {
    if (!current || !demoSet || stage !== "games") return;
    // One item contributes exactly once, even if a browser repeats a delayed callback.
    if (played.some((row) => row.item.id === current.id)) return;
    const row = { item: current, result };
    const nextPlayed = [...played, row];
    setPlayed(nextPlayed);
    if (nextPlayed.length >= DEMO_GAME_COUNT) {
      setCurrent(null);
      setTransition(null);
      setStage("analysis");
      void saveEvidence(nextPlayed);
      return;
    }
    const next = nextDemoItem(demoSet.items, nextPlayed);
    if (!next) {
      // Defensive only: validated sets always have an unused third item.
      setCurrent(null);
      setStage("analysis");
      return;
    }
    setTransition(demoTransition(row, next));
    setCurrent(next);
  }, [current, demoSet, played, saveEvidence, stage]);

  const analysis = useMemo(() => analyseDemo(played), [played]);

  return (
    <div className="demo-page" data-demo-screen={stage}>
      <header className="demo-topline">
        <span className="demo-wordmark" aria-label="Adapt">ad<b>apt</b></span>
        <span>Three-game demo</span>
      </header>

      <main id="main-content" className="demo-main">
        {stage === "welcome" ? (
          <section className="demo-welcome" aria-labelledby="demo-title">
            <p className="demo-kicker">See Adapt respond to you</p>
            <h1 id="demo-title">Three games. One snapshot built around you.</h1>
            <p className="demo-intro">
              Play for about 90 seconds. Adapt uses each answer to choose what challenge comes next.
            </p>
            <ol className="demo-steps" aria-label="How the demo works">
              <li><span>1</span>Start with a quick pattern.</li>
              <li><span>2</span>Get two challenges chosen from your answers.</li>
              <li><span>3</span>See what Adapt noticed and would do next.</li>
            </ol>
            <div className="demo-consent">
              <h2>Before you start</h2>
              <p>
                By continuing, you agree to Adapt using your game answers, response times and hint use for this demo analysis.
              </p>
              <p className="t-small">No name, email or password. You can stop by closing the page.</p>
              <button type="button" className="btn btn-ctl btn-primary demo-start" onClick={() => void prepare()}>
                I agree — start the demo
              </button>
            </div>
          </section>
        ) : null}

        {stage === "preparing" ? (
          <section className="demo-preparing" role="status" aria-busy="true">
            <span className="demo-spinner" aria-hidden="true" />
            <p className="demo-kicker">Getting your games ready</p>
            <h1>A short set, chosen for this demo.</h1>
            <p>If content is slow to arrive, the built-in games will open automatically.</p>
          </section>
        ) : null}

        {stage === "games" && current && demoSet ? (
          <section className="demo-game" data-demo-game={current.item_type} data-demo-origin={demoSet.origin}>
            <div className="demo-game-head">
              <div>
                <p className="demo-kicker">Game {played.length + 1} of {DEMO_GAME_COUNT}</p>
                <h1>{demoGameName(current)}</h1>
              </div>
              <span className="demo-level">Level {current.difficulty}</span>
            </div>
            <div className="demo-progress" role="progressbar" aria-label="Demo progress" aria-valuemin={0} aria-valuemax={DEMO_GAME_COUNT} aria-valuenow={played.length}>
              {Array.from({ length: DEMO_GAME_COUNT }, (_, index) => (
                <span key={index} className={index < played.length ? "done" : index === played.length ? "now" : ""} />
              ))}
            </div>
            {transition ? <p className="demo-transition" role="status">{transition}</p> : null}
            <TestItemCard
              key={current.id}
              item={current}
              position={played.length + 1}
              total={DEMO_GAME_COUNT}
              onDone={onDone}
            />
          </section>
        ) : null}

        {stage === "analysis" ? (
          <section className="demo-analysis" data-demo-analysis aria-labelledby="analysis-title">
            <p className="demo-kicker">Your early snapshot</p>
            <h1 id="analysis-title">Here’s what Adapt noticed.</h1>
            <p className="demo-intro">{analysis.headline} Three games show what happened here, not a fixed label about how you learn.</p>

            <div className="demo-analysis-grid">
              <section className="demo-result-card" aria-labelledby="demo-results-title">
                <div className="demo-score" aria-label={`${analysis.correct} correct out of ${analysis.total}`}>
                  <strong>{analysis.correct}</strong><span>/ {analysis.total} correct</span>
                </div>
                <h2 id="demo-results-title">Your three games</h2>
                <ol className="demo-result-list">
                  {played.map((row, index) => (
                    <li key={row.item.id}>
                      <div>
                        <span>Game {index + 1} · {demoGameName(row.item)}</span>
                        <strong>{row.result.correct ? "Correct" : "Not this time"}</strong>
                      </div>
                      <span>{formatSeconds(row.result.ms)}{row.result.hints ? ` · ${row.result.hints} ${row.result.hints === 1 ? "hint" : "hints"}` : ""}</span>
                    </li>
                  ))}
                </ol>
              </section>

              <div className="demo-insights">
                <section className="demo-next-card">
                  <p className="demo-kicker">What Adapt would do next</p>
                  <h2>{analysis.nextStep}</h2>
                  {analysis.supportNote ? <p>{analysis.supportNote}</p> : null}
                </section>
                <section className="demo-adaptations">
                  <h2>How Adapt responded</h2>
                  <ol>
                    {played.slice(1).map((row, index) => (
                      <li key={row.item.id}><span>Game {index + 2}</span>{demoTransition(played[index], row.item)}</li>
                    ))}
                  </ol>
                </section>
              </div>
            </div>

            <div className="demo-save" aria-live="polite">
              {saveState === "saving" || saveState === "idle" ? <span>Saving this run…</span> : null}
              {saveState === "saved" ? <span>Run saved to this guest session.</span> : null}
              {saveState === "built-in" ? <span>Built-in run · this analysis stays on this screen.</span> : null}
              {saveState === "unsaved" ? (
                <><span>The analysis is ready, but the run was not saved.</span><button type="button" className="btn btn-ctl btn-ghost" onClick={() => void saveEvidence(played)}>Retry saving</button></>
              ) : null}
            </div>
            <button type="button" className="btn btn-ctl btn-secondary" onClick={() => void prepare()}>Play a fresh set</button>
          </section>
        ) : null}
      </main>

      <footer className="demo-badge" aria-label="Demo version">
        <strong>DEMO</strong>
        <span>Demo version · early snapshot.</span>
      </footer>
    </div>
  );
}

function formatSeconds(ms: number): string {
  return `${Math.max(1, Math.round(ms / 1_000))} sec`;
}
