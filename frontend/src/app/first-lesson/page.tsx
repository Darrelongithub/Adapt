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

import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import LessonRenderer, { type LessonValue } from "@/components/lesson/LessonRenderer";
import { SceneStage, type SceneSpec } from "@/components/animator";
import { useOptionalVoice } from "@/components/voice/context";
import { CheckInCard } from "@/components/check-in";
import { api, get, post } from "@/lib/api";
import { useJourney } from "@/lib/journey";

type Concept = { id: string; name: string };
type AdaptationNote = {
  applied: boolean;
  reason: string;
  break_suggested: boolean;
  difficulty: number;
  undone: boolean;
};
type MisconceptionRow = { wrong_belief: string; correction: string; concept: string };
type PracticeRow = {
  item_id: string;
  stem: string;
  kind: string;
  options: { id: string; text: string }[];
};
type PracticeVerdict = { correct: boolean; expected: string; feedback: string };
type PlanReply = {
  plan: LessonValue;
  job_id?: string | null;
  adaptation?: AdaptationNote;
  common_misconceptions?: MisconceptionRow[];
  suggested_practice?: PracticeRow[];
  guideline?: { version: number; source_label: string; attribution: string } | null;
};
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

function FirstLesson() {
  const [topicDraft, setTopicDraft] = useState("");
  const [topic, setTopic] = useState<string | null>(null);
  const [concepts, setConcepts] = useState<Concept[] | null>(null);
  const [conceptId, setConceptId] = useState<string | null>(null);
  const [topicMatch, setTopicMatch] = useState<boolean | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [moments, setMoments] = useState<Moment[]>([]);
  const [reflections, setReflections] = useState<Record<number, string>>({});
  const reflectionRestored = useRef(false);
  // The option id the learner is sitting on while the server grades it.
  const [selectedByItem, setSelectedByItem] = useState<Record<string, string>>({});
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
  const [scene, setScene] = useState<SceneSpec | null>(null);
  const [lessonFinished, setLessonFinished] = useState(false);
  const [subjectId, setSubjectId] = useState<string | null>(null);
  const [curriculumScope, setCurriculumScope] = useState<{ curriculum?: string | null; grade?: string | null }>({});
  const [adaptation, setAdaptation] = useState<AdaptationNote | null>(null);
  const [guidelineNote, setGuidelineNote] = useState<PlanReply["guideline"]>(null);
  const [misconceptions, setMisconceptions] = useState<MisconceptionRow[]>([]);
  const [practice, setPractice] = useState<PracticeRow[]>([]);
  const [practiceDrafts, setPracticeDrafts] = useState<Record<string, string>>({});
  const [practiceVerdicts, setPracticeVerdicts] = useState<Record<string, PracticeVerdict>>({});
  const [gateBlocked, setGateBlocked] = useState(false);
  const journey = useJourney(true);
  const voice = useOptionalVoice();

  useEffect(() => {
    if (voice?.enabled && (scene || moments.length)) void voice.speak(moments.at(-1)?.heading ?? "Your visual lesson is ready.", { interrupt: true });
  }, [voice?.enabled, scene?.template, moments.length]);

  useEffect(() => {
    const row = journey.state?.stages.find(item => item.id === "first_lesson");
    setLessonFinished(row?.status === "done");
    if (!journey.state || reflectionRestored.current || !moments.length) return;
    reflectionRestored.current = true;
    if (row?.note) setReflections({ [moments[0].key]: row.note });
  }, [journey.state, moments]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const wanted = (params.get("topic") ?? "").trim();
    const subjectId = (params.get("subject") ?? "").trim();
    if (subjectId) setSubjectId(subjectId);
    if (wanted) {
      setTopicDraft(wanted);
      setTopic(wanted);
    } else if (subjectId) {
      void get<{ title: string; curriculum_spec?: { curriculum?: string | null; grade?: string | null } | null }>(`subjects/${encodeURIComponent(subjectId)}`).then((result) => {
        const title = result.data?.title?.trim();
        if (result.data?.curriculum_spec) setCurriculumScope(result.data.curriculum_spec);
        if (title) { setTopicDraft(title); setTopic(title); }
      });
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
    setReflections({});
    const lower = wanted.toLowerCase();
    const template = lower.includes("triangle") || lower.includes("geometry") ? "angle-sum-of-triangle" : lower.includes("function") ? "function-plot" : lower.includes("graph") ? "graph-traversal" : lower.includes("language") || lower.includes("grammar") || lower.includes("kiswahili") ? "grammar-sentence-diagram" : "flowchart";
    setScene({ template, captions: [`A picture for ${wanted}.`, "Pause and predict the next step.", "Say what changed."], source: "simple" });
    setStage("Checking what can be taught for this topic…");
    // Ask for the checked pack first: a typed topic has nothing to teach from until the
    // prepare job has built and validated one. The status surface is honest — it answers
    // "ready" or "fallback", never a pack payload.
    try {
      const started = await post<{ job_id: string }>("content/prepare", {
        topic: wanted,
        ...(curriculumScope.curriculum ? { curriculum: curriculumScope.curriculum } : {}),
        ...(curriculumScope.grade ? { grade: curriculumScope.grade } : {}),
      });
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
    // This route is rate-limited. `csrf: true` is no longer load-bearing — the backend
    // checks CSRF only on mutating methods now — but it is kept so the call reads as
    // "a deliberate fetch", matching the reveal route. See lib/api.ts.
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
  }, [curriculumScope]);

  useEffect(() => {
    if (topic) void loadConcepts(topic);
  }, [topic, loadConcepts]);

  async function writeLesson(wantedConcept: string, undoAdaptation = false) {
    if (!topic) return;
    setBusy(true);
    setNotice(null);
    setGateBlocked(false);
    let out: PlanReply;
    try {
      out = await post<PlanReply>("lesson/plan", {
        topic,
        concept_id: wantedConcept,
        ...(subjectId ? { subject_id: subjectId } : {}),
        undo_adaptation: undoAdaptation,
      });
    } catch (err) {
      setBusy(false);
      const e = err as { shown?: string; detail?: { error?: string } };
      if (e.detail?.error === "syllabus_not_approved") setGateBlocked(true);
      setNotice(e.shown ?? (err as Error).message ?? "The lesson could not be written just now.");
      return;
    }
    setBusy(false);
    setConceptId(wantedConcept);
    setMoments([{ key: 0, value: out.plan, jobId: out.job_id ?? null, heading: "Your lesson" }]);
    setAdaptation(out.adaptation ?? null);
    setGuidelineNote(out.guideline ?? null);
    setMisconceptions(out.common_misconceptions ?? []);
    setPractice(out.suggested_practice ?? []);
    setPracticeDrafts({});
    setPracticeVerdicts({});
    setVerdictByItem({});
    setSelectedByItem({});
    setMisconceptionByItem({});
    setPending(null);
    setSeq(1);
  }

  async function scorePractice(row: PracticeRow, answer: string) {
    if (!topic || !answer.trim() || practiceVerdicts[row.item_id]) return;
    try {
      const verdict = await post<PracticeVerdict>("lesson-plan/practice/score", {
        topic,
        item_id: row.item_id,
        answer: answer.trim(),
      });
      setPracticeVerdicts((rows) => ({ ...rows, [row.item_id]: verdict }));
    } catch (err) {
      setNotice((err as { shown?: string }).shown ?? "That practice answer could not be checked just now.");
    }
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

  async function finishLesson() {
    if (lessonFinished || journey.busy) return;
    const reflection = moments.map(moment => reflections[moment.key]?.trim() ?? "").filter(Boolean).join("\n");
    if (reflection.length > 200) {
      setNotice("Shorten your reflections to 200 characters altogether before saving.");
      return;
    }
    const out = await journey.advance("first_lesson", "done", reflection || undefined);
    if (out.ok) setLessonFinished(true);
  }

  /**
   * Round 3 (Track S): clicking a check option registers the answer with
   * the server, the server grades it, and the page reacts to the verdict.
   * The learner never has to label their own work.
   */
  async function pickOption(itemId: string, optionId: string, optionText: string) {
    if (!topic) return;
    if (pending || busy || verdictByItem[itemId]) return;
    setSelectedByItem(rows => ({ ...rows, [itemId]: optionId }));
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

  return (
    <section className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
      <header className="space-y-1">
        <h1 className="t-h1">Your first lesson</h1>
        <p className="text-sm text-[var(--muted)]">
          One idea, written for you.
        </p>
      </header>

      {notice ? (
        <p data-testid="fl-notice" role="status" className="rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm">
          {notice}{" "}
          {gateBlocked ? (
            <a className="link-inline" href="/journey?step=subject">
              Choose what to cover
            </a>
          ) : null}
        </p>
      ) : null}

      {stage ? (
        <p data-testid="fl-stage" role="status" className="text-sm text-[var(--muted)]">
          {stage}
        </p>
      ) : null}
      {scene ? <SceneStage spec={scene} /> : null}

      <section className="rounded-[2px] border border-[var(--line)] bg-[var(--bg)] p-4" aria-label="Choose a topic">
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
            className="min-w-0 flex-1 rounded-[2px] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm"
          />
          <button
            type="button"
            data-testid="fl-go"
            disabled={busy || topicDraft.trim().length < 2}
            onClick={() => setTopic(topicDraft.trim())}
            className="rounded-[2px] border border-[var(--line)] bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Write my lesson
          </button>
        </div>
      </section>

      {concepts && concepts.length > 1 && !conceptId ? (
        <section className="rounded-[2px] border border-[var(--line)] bg-[var(--bg)] p-4" aria-label="Choose an idea">
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
                className="rounded-[2px] border border-[var(--line)] px-3 py-1.5 text-sm hover:bg-[var(--panel)]"
              >
                {concept.name || concept.id}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {concepts && concepts.length === 1 && !conceptId && !busy ? (
        <div className="rounded-[2px] border border-[var(--line)] bg-[var(--bg)] p-4">
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

      {adaptation?.applied ? (
        <section
          className="rounded-[2px] border border-[var(--line)] bg-[var(--panel)] p-4"
          aria-label="How this lesson was shaped"
          data-testid="fl-adaptation"
        >
          <p className="text-sm">
            {adaptation.reason}
            {adaptation.break_suggested ? " A pause might help too — nothing is paused for you." : ""}
          </p>
          <button
            type="button"
            className="btn-ctl btn-secondary"
            style={{ marginTop: "var(--s2)" }}
            disabled={busy || pending !== null || !conceptId}
            onClick={() => conceptId && void writeLesson(conceptId, true)}
          >
            Use the regular lesson instead
          </button>
        </section>
      ) : null}
      {guidelineNote ? (
        <p className="text-sm text-[var(--muted)]">
          Following your approved list{guidelineNote.attribution ? ` · Source: ${guidelineNote.attribution}` : ""}.
        </p>
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
            onUpgrade={value => setMoments(rows => rows.map(row => row.key === moment.key ? { ...row, value, jobId: null } : row))}
            collapseBlockWhy={momentIndex !== 0}
            onPick={pickOption}
            verdicts={verdictByItem}
            selected={selectedByItem}
            disabled={busy || pending !== null}
            reflection={reflections[moment.key] ?? ""}
            onReflectionChange={text => setReflections(rows => ({ ...rows, [moment.key]: text }))}
            excludeQuestions={moments.slice(0, momentIndex).flatMap(row => row.value.blocks.flatMap(block =>
              (block.type === "check" || block.type === "predict") && typeof block.body === "object" && block.body.question ? [block.body.question] : []
            ))}
            excludeItemIds={moments.slice(0, momentIndex).flatMap(row => row.value.blocks.flatMap(block =>
              (block.type === "check" || block.type === "predict") && typeof block.body === "object" && block.body.item_id ? [block.body.item_id] : []
            ))}
          />
        </section>
      ))}

      {misconceptions.length ? (
        <section className="rounded-[2px] border border-[var(--line)] bg-[var(--panel)] p-4" aria-label="Easy traps in this lesson">
          <h2 className="m-0 text-base font-semibold">Easy traps to watch for</h2>
          <ul className="mt-2 list-disc space-y-2 pl-5 text-sm">
            {misconceptions.map((row, i) => (
              <li key={i}>
                <span>{row.wrong_belief}</span>
                {row.correction ? <span className="text-[var(--muted)]"> — {row.correction}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {practice.length ? (
        <section className="rounded-[2px] border border-[var(--line)] bg-[var(--panel)] p-4" aria-label="A little practice from this lesson" data-testid="fl-practice">
          <h2 className="m-0 text-base font-semibold">A little practice</h2>
          <p className="mt-1 text-sm text-[var(--muted)]">Trying again from memory is what makes this stick.</p>
          <ol className="mt-3 space-y-4 pl-5">
            {practice.map((row) => {
              const verdict = practiceVerdicts[row.item_id];
              return (
                <li key={row.item_id} className="space-y-2">
                  <p className="m-0 text-sm">{row.stem}</p>
                  {verdict ? (
                    <p className="m-0 text-sm" role="status">
                      {verdict.correct
                        ? "Correct."
                        : verdict.expected
                          ? `Not quite. The answer is ${verdict.expected}.`
                          : "Not quite."}{" "}
                      {verdict.feedback}
                    </p>
                  ) : row.options.length ? (
                    <div className="flex flex-wrap gap-2">
                      {row.options.map((option) => (
                        <button
                          key={option.id}
                          type="button"
                          className="btn-ctl btn-secondary"
                          disabled={busy || pending !== null}
                          onClick={() => void scorePractice(row, option.text)}
                        >
                          {option.text}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      <input
                        value={practiceDrafts[row.item_id] ?? ""}
                        onChange={(event) =>
                          setPracticeDrafts((drafts) => ({ ...drafts, [row.item_id]: event.target.value }))
                        }
                        className="min-w-52 rounded-[2px] border border-[var(--line)] bg-[var(--card)] px-2 py-1 text-sm"
                        aria-label="Your answer"
                      />
                      <button
                        type="button"
                        className="btn-ctl btn-secondary"
                        disabled={!practiceDrafts[row.item_id]?.trim()}
                        onClick={() => void scorePractice(row, practiceDrafts[row.item_id] ?? "")}
                      >
                        Check
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}

      {conceptId && moments.length > 0 ? (
        <div>
          <button
            type="button"
            data-testid="fl-reteach"
            disabled={busy || pending !== null}
            onClick={() => void reteach()}
            className="btn-ctl btn-secondary"
          >
            I still don&apos;t get it — try another way
          </button>
        </div>
      ) : null}
      {moments.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3 border-t border-[var(--line)] pt-4">
          <button type="button" className="btn-ctl btn-primary" onClick={() => void finishLesson()} disabled={lessonFinished || journey.busy}>
            {lessonFinished ? "First lesson saved" : "Finish first lesson"}
          </button>
          {lessonFinished ? <a className="link-inline" href="/learn">Continue learning</a> : null}
        </div>
      ) : null}
      {moments.length > 0 ? (
        <CheckInCard
          context="after_lesson"
          subjectId={subjectId}
          autoPrompt={lessonFinished}
          title="How did that lesson feel?"
        />
      ) : null}
    </section>
  );
}

export default function FirstLessonPage() {
  return (
    <Suspense fallback={null}>
      <FirstLesson />
    </Suspense>
  );
}
