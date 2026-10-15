"use client";

/**
 * The contrast demo: one concept, two profiles, two lessons.
 *
 * This is the screen the adaptation layer exists for. Both columns teach the
 * same checked content; what differs — what leads, how big the steps are,
 * whether a picture comes first, how sure you are asked to be — is decided
 * from each profile's measurements and stated choices, and every difference
 * in the table above the columns is read off the two plans the backend
 * returned, not written about them. The "same profile twice" switch is the
 * honesty check: when the evidence does not separate two profiles, this
 * screen says so instead of manufacturing a contrast.
 *
 * The two profiles are demo profiles, labelled as such everywhere they
 * appear. They are made up so the difference is visible on first load; a
 * signed-in learner's own lesson is on the lesson screen.
 */
import { useEffect, useState } from "react";

import { VisualFigure } from "@/components/visual";
import { LessonRenderer, type LessonValue } from "@/components/lesson/LessonRenderer";
import { Btn, Card, Err, Tag } from "@/components/ui";
import { post } from "@/lib/api";
import { contrastFoot, evidenceMark } from "@/lib/learnerCopy";

/* ------------------------------------------------------------------ types ---- */

type Why = { text: string; lever: string; source: string; evidence_ids: string[]; n: number };
type Block = {
  block: number;
  chunk_id: string;
  kind: "text" | "visual" | "worked_example" | "check" | "predict" | "explain" | "reflect";
  content: Record<string, unknown>;
  why: Why;
};
type Plan = {
  concept: string;
  title: string;
  lead_format: string;
  ladder_rung: number;
  blocks: Block[];
  limits: { step_size: string; max_words_per_block: number; tone: string; language: string };
  why_panel: {
    title: string;
    rows: ({ decision: string } & Why)[];
    what_would_change_it: string[];
    fallbacks: string[];
    mode_note: string;
  };
  fallbacks: string[];
  provenance: { model_version: number | null; content_id: string | null; evidence_n: number };
};
type Contrast = {
  concept: string;
  title: string;
  lessons: { profile: string; plan: Plan }[];
  ai3_checked_output?: {
    source: "written_for_you" | "simple";
    lessons: { profile: string; label: string; plan: LessonValue }[];
  };
  differences: { aspect: string; a: unknown; b: unknown }[];
  visibly_different: boolean;
  note: string;
};

function evidenceLabel(source: string, n: number): string {
  const label = source.replaceAll("_", " ");
  if (label === "stated") return "stated";
  if (n === 0) return label === "default" ? "default" : `${label}; not measured`;
  return `${label}, n=${n}`;
}

/* ------------------------------------------------------- demo profiles ---- */

const lever = (value: unknown, why: string, source: string) => ({ value, why, source });

const PROFILE_A = {
  name: "Demo profile 1 — smaller steps, pictures first",
  description:
    "Made up for this screen: holds about 4 steps at once, chose diagrams for a new topic, tends to feel surer than their answers, recalls new pairs slowly.",
  levers: {
    step_size: lever("small", "holds about 4 steps at once (n=6)", "measured"),
    lead_format_prior: lever("visual", "chose diagrams for a new topic (stated, not measured)", "stated"),
    tone: lever("gentle", "chose gentle feedback (stated)", "stated"),
    language: lever("en", "chose English (stated)", "stated"),
    confidence_prompting: lever("before_each_answer", "felt sure 20% more often than they were right (n=10)", "measured"),
    review_halflife_prior: lever(18, "recalled 40% of new pairs straight away (n=8)", "measured"),
    hint_policy: lever("offer early", "asked for help after 20 seconds on a hard puzzle", "measured"),
  },
  ladder: { rung: 2 },
};

const PROFILE_B = {
  name: "Demo profile 2 — bigger steps, examples first",
  description:
    "Made up for this screen: holds about 7 steps at once, no stated format choice, well calibrated, recalls new pairs quickly, already explaining ideas back.",
  levers: {
    step_size: lever("large", "holds about 7 steps at once (n=6)", "measured"),
    lead_format_prior: lever("worked_example", "no stated preference, so the default", "default"),
    tone: lever("straight", "chose straight feedback (stated)", "stated"),
    language: lever("en", "chose English (stated)", "stated"),
    confidence_prompting: lever("off", "no calibration evidence yet", "default"),
    review_halflife_prior: lever(48, "recalled 80% of new pairs straight away (n=8)", "measured"),
    hint_policy: lever("on request", "default: hints are there when asked for", "default"),
  },
  ladder: { rung: 5 },
};

/* --------------------------------------------------------- demo content ---- */

const CONCEPTS: { id: string; label: string; pack: Record<string, unknown> }[] = [
  {
    id: "fractions",
    label: "Adding fractions",
    pack: {
      concept: "adding fractions",
      title: "Adding fractions",
      topic_id: "top_frac",
      source: "demo content written for this screen",
      explanation:
        "A fraction names parts of one whole. The parts must be the same size before you can add them. Find a denominator both fractions share. Rewrite each fraction over that denominator. Then add the numerators and keep the denominator.",
      worked_example: {
        steps: [
          { text: "1/2 = 3/6, because both parts are scaled by three.", why: "scaling keeps the value" },
          { text: "1/3 = 2/6, because both parts are scaled by two.", why: "scaling keeps the value" },
          { text: "3/6 + 2/6 = 5/6.", why: "add the numerators, keep the denominator" },
        ],
      },
      visual: {
        kind: "bar",
        title: "Adding fractions in sixths",
        bars: [
          { label: "1/2", value: 3 },
          { label: "1/3", value: 2 },
          { label: "total", value: 5 },
        ],
        unit: "sixths",
      },
      questions: [{ question: "What is 1/2 + 1/4?", format: "typed_number" }],
      answer: "5/6",
      facts: ["The denominator stays the same when you add fractions."],
    },
  },
  {
    id: "photosynthesis",
    label: "Photosynthesis",
    pack: {
      concept: "photosynthesis",
      title: "Photosynthesis",
      topic_id: "top_bio",
      source: "demo content written for this screen",
      explanation:
        "A plant makes its own food from light, air and water. The green part of the leaf catches the light. Carbon dioxide enters through tiny holes under the leaf. Water travels up from the roots. The plant builds sugar from these, and oxygen leaves as a spare product.",
      worked_example: {
        steps: [
          { text: "Light is caught by the green part of the leaf.", why: "this is where the energy enters" },
          { text: "Carbon dioxide and water are the raw materials.", why: "name the inputs before the output" },
          { text: "Sugar is built, and oxygen is released.", why: "the output and the spare product" },
        ],
      },
      visual: {
        kind: "labelled_diagram",
        title: "A leaf, labelled",
        whole: "A leaf",
        parts: [
          { label: "Green part", text: "Catches the light." },
          { label: "Tiny holes", text: "Carbon dioxide enters here." },
          { label: "Veins", text: "Water arrives from the roots." },
        ],
      },
      questions: [{ question: "Which gas enters the leaf, and which one leaves?", format: "typed_text" }],
      facts: ["Oxygen is released as a spare product of photosynthesis."],
    },
  },
  {
    id: "water-cycle",
    label: "The water cycle",
    pack: {
      concept: "the water cycle",
      title: "The water cycle",
      topic_id: "top_geo",
      source: "demo content written for this screen",
      explanation:
        "The sun heats water in seas, lakes and soil. The water rises as vapour you cannot see. High up, the vapour cools and gathers into clouds. When the drops grow heavy, they fall as rain. The rain collects in streams and seas, and the cycle begins again.",
      worked_example: {
        steps: [
          { text: "Heating comes first: the sun warms surface water.", why: "the energy source" },
          { text: "Vapour cools and gathers into clouds.", why: "the change of state" },
          { text: "Rain falls and collects, ready to be heated again.", why: "why it is a cycle" },
        ],
      },
      visual: {
        kind: "flow",
        title: "The water cycle in order",
        steps: [
          { label: "Heat", text: "The sun heats water in seas, lakes and soil." },
          { label: "Rise", text: "The water rises as vapour you cannot see." },
          { label: "Gather", text: "The vapour cools and gathers into clouds." },
          { label: "Fall", text: "Heavy drops fall as rain and collect." },
        ],
      },
      questions: [{ question: "What makes surface water rise, and what happens to it high up?", format: "typed_text" }],
      facts: ["Water vapour gathers into clouds when it cools."],
    },
  },
];

/* ---------------------------------------------------------------- screen ---- */

export default function Contrast() {
  const [conceptId, setConceptId] = useState(CONCEPTS[0].id);
  const [sameTwice, setSameTwice] = useState(false);
  const [data, setData] = useState<Contrast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const concept = CONCEPTS.find((c) => c.id === conceptId) ?? CONCEPTS[0];

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setError(null);
      setErrorStatus(null);
      try {
        const profiles = sameTwice ? [PROFILE_A, { ...PROFILE_A, name: "Demo profile 1 — again" }] : [PROFILE_A, PROFILE_B];
        const out = await post<Contrast>("/api/v1/lesson-plan/contrast", {
          content_pack: concept.pack,
          profiles: profiles.map(({ name, levers, ladder }) => ({ name, levers, ladder })),
        });
        if (alive) setData(out);
      } catch (err) {
        if (alive) {
          const status = err && typeof err === "object" && "status" in err ? Number((err as { status: number }).status) : null;
          setErrorStatus(Number.isFinite(status) ? status : null);
          setError(status === 401 ? "not signed in" : "The comparison could not be loaded just now.");
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [conceptId, sameTwice]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">The same lesson, taught two ways</h1>
        <p className="max-w-[75ch] text-[13px] text-[var(--muted)]">
          One concept, two made-up demo profiles. Both lessons below teach the same checked content — what changes is
          how it is laid out, and every change is decided from the profile next to it: what was measured, what the
          learner said they prefer, and what would change the decision. Nothing here is a real learner&apos;s data.
        </p>
      </header>

      <Card title="the concept">
        <div className="flex flex-wrap items-center gap-2">
          {CONCEPTS.map((c) => (
            <Btn key={c.id} kind={c.id === conceptId ? "primary" : "quiet"} pressed={c.id === conceptId} onClick={() => setConceptId(c.id)}>
              {c.label}
            </Btn>
          ))}
          <span className="ml-auto text-[11px] text-[var(--muted)]">demo content written for this screen</span>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {[PROFILE_A, sameTwice ? { ...PROFILE_A, name: "Demo profile 1 — again" } : PROFILE_B].map((p, i) => (
            <div key={`${p.name}-${i}`} className="rounded-md border border-[var(--line)] px-3 py-2">
              <p className="text-[13px] font-medium">{p.name}</p>
              <p className="mt-1 text-[12px] text-[var(--muted)]">{p.description}</p>
            </div>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Btn kind={sameTwice ? "quiet" : "primary"} pressed={!sameTwice} onClick={() => setSameTwice(false)}>
            two different profiles
          </Btn>
          <Btn kind={sameTwice ? "primary" : "quiet"} pressed={sameTwice} onClick={() => setSameTwice(true)}>
            the same profile twice (honesty check)
          </Btn>
        </div>
      </Card>

      {error ? (
        <Err error={error} status={errorStatus} />
      ) : null}
      {loading && !data ? (
        <Card title="building both lessons">
          <p className="text-[13px] text-[var(--muted)]">Preparing two versions of the same lesson for these example profiles.</p>
        </Card>
      ) : null}

      {data ? (
        <>
          <Card title="what actually differs" tone={data.visibly_different ? "ask" : "muted"}>
            {data.visibly_different ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-[13px]">
                  <thead>
                    <tr className="text-[var(--muted)]">
                      <th className="py-1 pr-3 font-medium">Difference</th>
                      <th className="py-1 pr-3 font-medium">{data.lessons[0].profile}</th>
                      <th className="py-1 font-medium">{data.lessons[1].profile}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.differences.map((d) => (
                      <tr key={d.aspect} className="border-t border-[var(--line)]">
                        <td className="py-1.5 pr-3 text-[var(--muted)]">{d.aspect}</td>
                        <td className="py-1.5 pr-3 font-medium">{String(d.a).replaceAll("_", " ")}</td>
                        <td className="py-1.5 font-medium">{String(d.b).replaceAll("_", " ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            <p className="mt-2 text-[12px] text-[var(--muted)]">{data.note}</p>
          </Card>

          {data.ai3_checked_output?.lessons?.length ? (
            <div className="grid items-start gap-4 lg:grid-cols-2">
              {data.ai3_checked_output.lessons.map((lesson) => (
                <Card key={lesson.profile} title={lesson.profile}>
                  <LessonRenderer value={lesson.plan} contrastLabel />
                </Card>
              ))}
            </div>
          ) : (
            <div className="grid items-start gap-4 lg:grid-cols-2">
              {data.lessons.map((lesson) => (
                <LessonColumn key={lesson.profile} profile={lesson.profile} plan={lesson.plan} />
              ))}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- pieces ---- */

function LessonColumn({ profile, plan }: { profile: string; plan: Plan }) {
  return (
    <Card
      title={profile}
      right={
        <span className="flex gap-1">
          <Tag tone="accent">{plan.lead_format.replaceAll("_", " ")} first</Tag>
          <Tag tone="line">{plan.limits.step_size} steps</Tag>
        </span>
      }
    >
      <div className="flex flex-col gap-3">
        {plan.blocks.map((block) => (
          <BlockView key={block.chunk_id} block={block} />
        ))}
        <WhyPanel plan={plan} />
      </div>
    </Card>
  );
}

function BlockView({ block }: { block: Block }) {
  const c = block.content;
  return (
    <section aria-label={`block ${block.block}: ${block.kind}`} className="rounded-md border border-[var(--line)] px-3 py-2">
      <div className="mb-1.5 flex items-center gap-2">
        <Tag tone="line">{block.kind.replaceAll("_", " ")}</Tag>
        <span className="text-[11px] text-[var(--muted)]">
          why: {block.why.text}{" "}
          <span className="font-mono">{evidenceMark(block.why.source, block.why.n, block.why.evidence_ids)}</span>
        </span>
      </div>
      {block.kind === "visual" ? (
        <VisualFigure spec={c.spec} fallbackNote={typeof c.note === "string" ? c.note : undefined} />
      ) : block.kind === "worked_example" ? (
        <ol className="flex list-decimal flex-col gap-1 pl-5 text-[13px]">
          {(Array.isArray(c.steps) ? (c.steps as { text: string; why?: string }[]) : []).map((step, i) => (
            <li key={i}>
              {step.text}
              {step.why ? <span className="block text-[11px] text-[var(--muted)]">{step.why}</span> : null}
            </li>
          ))}
        </ol>
      ) : block.kind === "check" || block.kind === "predict" || block.kind === "explain" ? (
        <div>
          <p className="text-[13px] font-medium">{String(c.question ?? "")}</p>
          {typeof c.hint === "string" && c.hint ? <p className="mt-1 text-[12px] text-[var(--muted)]">hint: {c.hint}</p> : null}
        </div>
      ) : (
        <div>
          <p className="whitespace-pre-wrap text-[13px]">{String(c.text ?? "")}</p>
          {typeof c.note === "string" && c.note ? <p className="mt-1 text-[11px] text-[var(--warn)]">{c.note}</p> : null}
          {typeof c.review_in_hours === "number" ? (
            <p className="mt-1 text-[11px] text-[var(--muted)]">comes round again in about {c.review_in_hours} hours</p>
          ) : null}
          {typeof c.evidence_of_success === "string" ? (
            <p className="mt-1 text-[12px] text-[var(--good)]">{c.evidence_of_success}</p>
          ) : null}
        </div>
      )}
    </section>
  );
}

function WhyPanel({ plan }: { plan: Plan }) {
  const panel = plan.why_panel;
  return (
    <section aria-label="why this lesson looks like this" className="rounded-md border border-[var(--accent)]/40 bg-[var(--panel)] px-3 py-2">
      <h3 className="text-[13px] font-semibold">{panel.title}</h3>
      <ul className="mt-2 flex flex-col gap-1.5 text-[12px]">
        {panel.rows.map((row, i) => (
          <li key={i}>
            <span className="font-medium">{row.decision}.</span> {row.text}{" "}
            <span className="font-mono text-[var(--muted)]">
              ({evidenceMark(row.source, row.n, row.evidence_ids, { includeSource: true })})
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[12px] font-medium">What would change it</p>
      <ul className="list-disc pl-5 text-[12px] text-[var(--muted)]">
        {panel.what_would_change_it.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      {panel.fallbacks.length ? (
        <ul className="mt-2 flex flex-col gap-1 text-[11px] text-[var(--warn)]">
          {panel.fallbacks.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      ) : null}
      <p className="mt-2 text-[11px] text-[var(--muted)]">
        {contrastFoot({ modelVersion: plan.provenance.model_version, evidenceN: plan.provenance.evidence_n })}
      </p>
    </section>
  );
}
