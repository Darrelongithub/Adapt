"use client";

/**
 * Your first lesson — the lesson writer's three moments, on one screen.
 *
 * Phase G seam note: Round 2 froze the lesson routes (`/api/v1/lesson/plan`,
 * `/lesson/explain-wrong`, `/lesson/reteach`) and Part C built the block renderer, but no
 * learner screen called them — the journey's lesson step linked at the older scene screen.
 * This page is that missing wiring: it asks for a lesson on the topic the learner is
 * working on, shows the checked plan, and offers the two moments that follow a struggle —
 * an explanation that starts from the answer actually chosen, and a re-teach that tries a
 * different way every time it is asked.
 *
 * One honest limit, stated plainly: nothing here grades the check question. The lesson's
 * answer key never comes to the browser (that is the leak rule), so the learner says which
 * happened — "I picked this and it was wrong" or "I had it" — and the app responds to what
 * they said. No invented verdict, no key on the wire.
 */

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import LessonRenderer, { type LessonValue } from "@/components/lesson/LessonRenderer";
import { api, get, post } from "@/lib/api";

type Concept = { id: string; name: string };
type PlanReply = { plan: LessonValue; job_id?: string | null };
type ExplanationReply = { explanation: LessonValue; job_id?: string | null };
type Moment = { key: number; value: LessonValue; jobId?: string | null; heading: string };
type CheckBody = { question?: string; item_id?: string; options?: { id: string; text: string }[] };

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
  const [moments, setMoments] = useState<Moment[]>([]);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [answeredRight, setAnsweredRight] = useState<Record<string, boolean>>({});
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
    let listed: { topic: string; concepts: Concept[] } | null = null;
    try {
      listed = await api<{ topic: string; concepts: Concept[] }>("lesson/concepts", {
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
    setPicked({});
    setAnsweredRight({});
    setSeq(1);
  }

  async function explainWrong(itemId: string, answer: string) {
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
    setMoments((rows) => [
      ...rows,
      { key, value: out.explanation, jobId: out.job_id ?? null, heading: "Why that answer feels right — and where it breaks" },
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

  const latest = moments.length ? moments[moments.length - 1] : null;
  const check = useMemo(() => (latest ? firstCheck(latest.value) : null), [latest]);
  const checkKey = check?.block.item_id ?? "";
  const chosen = checkKey ? picked[checkKey] : undefined;
  const done = checkKey ? answeredRight[checkKey] : false;

  return (
    <main className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Your first lesson</h1>
        <p className="text-sm text-[var(--muted)]">
          One idea, written for the way you work — and if it does not land, a different explanation
          every time you ask.
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
            placeholder="for example: fractions, photosynthesis, the French Revolution"
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
          <h2 className="text-sm font-medium">Which idea first?</h2>
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
        <p className="text-sm text-[var(--muted)]">
          Starting with <strong>{concepts[0].name || "the first idea"}</strong>.{" "}
          <button type="button" className="link-inline" data-testid="fl-concept" onClick={() => void writeLesson(concepts[0].id)}>
            Write the lesson
          </button>
        </p>
      ) : null}

      {moments.map((moment) => (
        <section key={moment.key} className="space-y-2" data-testid="fl-moment" aria-label={moment.heading}>
          <h2 className="text-sm font-medium">{moment.heading}</h2>
          <LessonRenderer value={moment.value} upgradeJobId={moment.jobId} />
        </section>
      ))}

      {check && !done ? (
        <section className="rounded-xl border border-[var(--line)] bg-[var(--bg)] p-4 space-y-2" aria-label="Your answer">
          <h2 className="text-sm font-medium">{check.block.question}</h2>
          <div className="grid gap-2" role="group" aria-label="Choose an option">
            {(check.block.options ?? []).map((option) => (
              <button
                key={option.id}
                type="button"
                data-testid="fl-option"
                aria-pressed={chosen === option.id}
                onClick={() => setPicked((rows) => ({ ...rows, [checkKey]: option.id }))}
                className={`rounded-lg border px-3 py-2 text-left text-sm ${
                  chosen === option.id ? "border-[var(--accent)] bg-[var(--panel)]" : "border-[var(--line)]"
                }`}
              >
                {option.text}
              </button>
            ))}
          </div>
          {chosen ? (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                data-testid="fl-wrong"
                disabled={busy}
                onClick={() => {
                  const option = (check.block.options ?? []).find((row) => row.id === chosen);
                  if (option) void explainWrong(checkKey, option.text);
                }}
                className="rounded-lg border border-[var(--line)] px-3 py-2 text-sm disabled:opacity-50"
              >
                I picked this and it was wrong — explain why
              </button>
              <button
                type="button"
                data-testid="fl-got-it"
                onClick={() => setAnsweredRight((rows) => ({ ...rows, [checkKey]: true }))}
                className="rounded-lg border border-[var(--line)] px-3 py-2 text-sm"
              >
                I had it right
              </button>
            </div>
          ) : null}
          <p className="text-xs text-[var(--muted)]">
            Nothing here marks your answer for you — the answer key stays on the server. You say what
            happened, and the lesson responds to that.
          </p>
        </section>
      ) : null}

      {done ? (
        <p className="text-sm text-[var(--muted)]" role="status">
          Good. The practice loop keeps this idea in rotation.
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
