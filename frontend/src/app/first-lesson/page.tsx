"use client";

/**
 * Your first lesson — the lesson writer's three moments, on one screen.
 *
 * Round 3 (Track S): clicking a check option now registers the answer
 * with the server, which grades it and returns the misconception matched
 * to the chosen distractor.  The answer key never reaches the browser; the
 * server's verdict drives the next step (a brief confirmation, or the
 * misconception-specific follow-up).  The old "I had it right" / "I picked
 * this and it was wrong" buttons are gone — the learner is no longer
 * asked to grade their own work.
 *
 * Phase G seam note: Round 2 froze the lesson routes (`/api/v1/lesson/plan`,
 * `/lesson/explain-wrong`, `/lesson/reteach`) and Part C built the block renderer, but no
 * learner screen called them — the journey's lesson step linked at the older scene screen.
 * This page is that missing wiring: it asks for a lesson on the topic the learner is
 * working on, shows the checked plan, and offers the two moments that follow a struggle —
 * an explanation that starts from the answer actually chosen, and a re-teach that tries a
 * different way every time it is asked.
 */

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import LessonRenderer, { type LessonValue } from "@/components/lesson/LessonRenderer";
import { api, get, post } from "@/lib/api";

type Concept = { id: string; name: string };
type PlanReply = { plan: LessonValue; job_id?: string | null };
type ExplanationReply = { explanation: LessonValue; job_id?: string | null };
type Moment = { key: number; value: LessonValue; jobId?: string | null; heading: string };
type CheckBody = { question?: string; item_id?: string; options?: { id: string; text: string }[] };
type Verdict = "correct" | "wrong" | "pending";

const METHOD_WORDS: Record<string, string> = {
  simpler_words: "plainer words",
  smaller_step: "a smaller step",
  example: "a worked example",
  visual: "a picture of the idea",
  misconception_response: "starting from your answer",
  initial_lesson: "the first explanation",
};

function methodWords(method?: string): string {
  if (!method) return "another way";
  return METHOD_WORDS[method] ?? method.replaceAll("_", " ");
}

function firstCheck(value: LessonValue): { block: CheckBody; index: number } | null {
  for (let index = 0; index < value.blocks.length; index += 1) {
    const block = value.blocks[index];
    if (block.type !== "check" && block.type !== "predict") continue;
    const body = block.body;
    if (typeof body === "string") continue;
    const options = Array.isArray(body.options) ? body.options : [];
    if (!body.item_id || options.length < 2) continue;
    return { block: body as CheckBody, index };
  }
  return null;
}

function FirstLesson() {
  const [topicDraft, setTopicDraft] = useState("");
  const [topic, setTopic] = useState<string | null>(null);
  const [concepts, setConcepts] = useState<Concept[] | null>(null);
  const [conceptId, setConceptId] = useState<string | null>(null);
  const [topicMatch, setTopicMatch] = useState<boolean | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [moments, setMoments] = useState<Moment[]>([]);
  // The option id the learner is sitting on while the server grades it.
  const [pending, setPending] = useState<string | null>(null);
  // Server verdict: `correct`, `wrong`, or `pending` (still grading).
  const [verdictByItem, setVerdictByItem] = useState<Record<string, Verdict>>({});
  // After a wrong answer, the server-sent misconception id — used to label
  // the follow-up so the page can say "starting from the answer you chose".
  const [misconceptionByItem, setMisconceptionByItem] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [seq, setSeq] = useState(1);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const wanted = (params.get("topic") ?? "").trim();
    if (wanted) {
      setTopicDraft(wanted);
      setTopic(wanted);
    }
  }, []);

  const loadConcepts = useCallback(async (wanted: string) => {
    setBusy(true);
    setNotice(null);
    setConcepts(null);
    setConceptId(null);
    setTopicMatch(null);
    setSource(null);
    setMoments([]);
    setStage("Checking what can be taught for this topic…");
    // Ask for the checked pack first: a typed topic has nothing to teach from until the
    // prepare job has built and validated one. The status surface is honest — it answers
    // "ready" or "fallback", never a pack payload.
    try {
      const started = await post<{ job_id: string }>("content/prepare", { topic: wanted });
      for (let attempt = 0; attempt < 90; attempt += 1) {
        const status = await get<{ state: string; pack_source: string | null }>(
          `content/prepare/${encodeURIComponent(started.job_id)}`,
        );
        if (status.data && status.data.state !== "running") break;
        await new Promise((resolve) => setTimeout(resolve, 700));
      }
    } catch {
      // Preparation is best-effort from this screen: a topic that cannot be prepared may
      // still have an authored lesson, and the concepts call below decides honestly.
    }
    setStage("Choosing an idea…");
    // this route is rate-limited, and the rate guard demands the CSRF token on every
    // method, so it is called through api() with the token rather than through get()
    let listed: { topic: string; concepts: Concept[]; source?: string; topic_match?: boolean } | null = null;
    try {
      listed = await api<{ topic: string; concepts: Concept[]; source?: string; topic_match?: boolean }>("lesson/concepts", {
        query: { topic: wanted },
        csrf: true,
      });
    } catch (err) {
      const e = err as { shown?: string };
      setBusy(false);
      setStage(null);
      setNotice(
        e.shown ??
          "That topic has no lesson ready yet. Try the subject step first, or come back after it has been prepared.",
      );
      return;
    }
    setBusy(false);
    setStage(null);
    setConcepts(listed.concepts);
    setTopicMatch(Boolean(listed.topic_match));
    setSource(listed.source ?? null);
  }, []);

  useEffect(() => {
    if (topic) void loadConcepts(topic);
  }, [topic, loadConcepts]);

  async function writeLesson(wantedConcept: string) {
    if (!topic) return;
    setBusy(true);
    setNotice(null);
    let out: PlanReply;
    try {
      out = await post<PlanReply>("lesson/plan", { topic, concept_id: wantedConcept });
    } catch (err) {
      setBusy(false);
      setNotice((err as Error).message || "The lesson could not be written just now.");
      return;
    }
    setBusy(false);
    setConceptId(wantedConcept);
    setMoments([{ key: 0, value: out.plan, jobId: out.job_id ?? null, heading: "Your lesson" }]);
    setVerdictByItem({});
    setMisconceptionByItem({});
    setPending(null);
    setSeq(1);
  }

  async function explainWrong(itemId: string, answer: string, misconceptionId?: string) {
    if (!topic) return;
    setBusy(true);
    setNotice(null);
    let out: ExplanationReply;
    try {
      out = await post<ExplanationReply>("lesson/explain-wrong", { topic, item_id: itemId, answer });
    } catch (err) {
      setBusy(false);
      setNotice((err as Error).message || "The explanation could not be written just now.");
      return;
    }
    setBusy(false);
    const key = seq;
    setSeq(seq + 1);
    const heading = misconceptionId
      ? "Why that answer feels right — and where it breaks"
      : "The answer the lesson marked";
    setMoments((rows) => [
      ...rows,
      { key, value: out.explanation, jobId: out.job_id ?? null, heading },
    ]);
  }

  async function reteach() {
    if (!topic || !conceptId) return;
    setBusy(true);
    setNotice(null);
    let out: ExplanationReply;
    try {
      out = await post<ExplanationReply>("lesson/reteach", { topic, concept_id: conceptId });
    } catch (err) {
      setBusy(false);
      setNotice((err as Error).message || "Another explanation could not be written just now.");
      return;
    }
    setBusy(false);
    const key = seq;
    setSeq(seq + 1);
    setMoments((rows) => [
      ...rows,
      {
        key,
        value: out.explanation,
        jobId: out.job_id ?? null,
        heading: `Another way: ${methodWords(out.explanation.method)}`,
      },
    ]);
  }

  /**
   * Round 3 (Track S): clicking a check option registers the answer with
   * the server, the server grades it, and the page reacts to the verdict.
   * The learner never has to label their own work.
   */
  async function pickOption(itemId: string, optionId: string, optionText: string) {
    if (!topic) return;
    if (pending === itemId) return;
    setPending(itemId);
    setNotice(null);
    let verdict: { correct: boolean; misconception_id?: string | null } | null = null;
    try {
      verdict = await post<{ correct: boolean; misconception_id?: string | null }>(
        "lesson/grade",
        { topic, item_id: itemId, selected: optionId, concept_id: conceptId ?? undefined },
      );
    } catch (err) {
      setPending(null);
      setNotice((err as Error).message || "The server could not grade that just now.");
      return;
    }
    setPending(null);
    if (verdict.correct) {
      setVerdictByItem((rows) => ({ ...rows, [itemId]: "correct" }));
      return;
    }
    setVerdictByItem((rows) => ({ ...rows, [itemId]: "wrong" }));
    if (verdict.misconception_id) {
      setMisconceptionByItem((rows) => ({ ...rows, [itemId]: verdict.misconception_id! }));
    }
    await explainWrong(itemId, optionText, verdict.misconception_id ?? undefined);
  }

  const latest = moments.length ? moments[moments.length - 1] : null;
  const check = useMemo(() => (latest ? firstCheck(latest.value) : null), [latest]);
  const checkKey = check?.block.item_id ?? "";
  const verdict = checkKey ? verdictByItem[checkKey] : undefined;
  const done = verdict === "correct";

  return (
    <main className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Your first lesson</h1>
        <p className="text-sm text-[var(--muted)]">
          One idea, written for you.
        </p>
      </header>

      {notice ? (
        <p data-testid="fl-notice" role="status" className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm">
          {notice}
        </p>
      ) : null}

      {stage ? (
        <p data-testid="fl-stage" role="status" className="text-sm text-[var(--muted)]">
          {stage}
        </p>
      ) : null}

      <section className="rounded-xl border border-[var(--line)] bg-[var(--bg)] p-4" aria-label="Choose a topic">
        <label htmlFor="fl-topic" className="text-sm font-medium">
          What are you working on?
        </label>
        <div className="mt-2 flex flex-wrap gap-2">
          <input
            id="fl-topic"
            data-testid="fl-topic"
            value={topicDraft}
            onChange={(event) => setTopicDraft(event.target.value)}
            placeholder="for example: photosynthesis"
            className="min-w-0 flex-1 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm"
          />
          <button
            type="button"
            data-testid="fl-go"
            disabled={busy || topicDraft.trim().length < 2}
            onClick={() => setTopic(topicDraft.trim())}
            className="rounded-lg border border-[var(--line)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Write my lesson
          </button>
        </div>
      </section>

      {concepts && concepts.length > 1 && !conceptId ? (
        <section className="rounded-xl border border-[var(--line)] bg-[var(--bg)] p-4" aria-label="Choose an idea">
          {source === "simple" ? (
            <p className="text-sm text-[var(--muted)]" data-testid="fl-fallback-notice">
              No detailed lesson for this topic offline — here is how to study it.
            </p>
          ) : null}
          <h2 className="mt-2 text-sm font-medium">Which idea first?</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {concepts.map((concept) => (
              <button
                key={concept.id}
                type="button"
                data-testid="fl-concept"
                onClick={() => void writeLesson(concept.id)}
                className="rounded-full border border-[var(--line)] px-3 py-1.5 text-sm hover:bg-[var(--panel)]"
              >
                {concept.name || concept.id}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {concepts && concepts.length === 1 && !conceptId && !busy ? (
        <div className="rounded-xl border border-[var(--line)] bg-[var(--bg)] p-4">
          {source === "simple" ? (
            <p className="text-sm text-[var(--muted)]" data-testid="fl-fallback-notice">
              No detailed lesson for this topic offline — here is how to study it.
            </p>
          ) : null}
          <p className="mt-2 text-sm text-[var(--muted)]">
            Starting with <strong>{concepts[0].name || "the first idea"}</strong>.{" "}
            <button type="button" className="link-inline" data-testid="fl-concept" onClick={() => void writeLesson(concepts[0].id)}>
              Write the lesson
            </button>
          </p>
        </div>
      ) : null}

      {moments.map((moment, momentIndex) => (
        <section
          key={moment.key}
          className="space-y-2"
          data-testid="fl-moment"
          aria-label={moment.heading}
        >
          <h2 className="text-sm font-medium">{moment.heading}</h2>
          <LessonRenderer
            value={moment.value}
            upgradeJobId={moment.jobId}
            collapseBlockWhy={momentIndex === 0}
          />
        </section>
      ))}

      {check && !done ? (
        <section
          className="rounded-xl border border-[var(--line)] bg-[var(--bg)] p-4 space-y-2"
          aria-label="Your answer"
        >
          <h2 className="text-sm font-medium">{check.block.question}</h2>
          <div className="r3-choices pill-grid" role="group" aria-label="Choose an option">
            {(check.block.options ?? []).map((option) => {
              const isPending = pending === checkKey;
              const chosenWrong = verdict === "wrong" && pending === null;
              return (
                <button
                  key={option.id}
                  type="button"
                  data-testid="fl-option"
                  data-option-id={option.id}
                  disabled={isPending || pending !== null}
                  onClick={() => void pickOption(checkKey, option.id, option.text)}
                  className={`choice-card${chosenWrong ? " is-selected" : ""}`}
                >
                  <span className="choice-body">
                    <span className="choice-title">{option.text}</span>
                  </span>
                </button>
              );
            })}
          </div>
          {verdict === "wrong" ? (
            <p className="text-sm" role="status" data-testid="fl-wrong-receipt">
              The lesson marked that one as wrong — see the explanation above.
            </p>
          ) : null}
        </section>
      ) : null}

      {done ? (
        <p className="text-sm text-[var(--muted)]" role="status" data-testid="fl-confirm-correct">
          Right. Practice keeps it in rotation.
        </p>
      ) : null}

      {conceptId && moments.length > 0 ? (
        <div>
          <button
            type="button"
            data-testid="fl-reteach"
            disabled={busy}
            onClick={() => void reteach()}
            className="rounded-lg border border-[var(--line)] px-3 py-2 text-sm disabled:opacity-50"
          >
            I still don&apos;t get it — try another way
          </button>
        </div>
      ) : null}
    </main>
  );
}

export default function FirstLessonPage() {
  return (
    <Suspense fallback={null}>
      <FirstLesson />
    </Suspense>
  );
}
