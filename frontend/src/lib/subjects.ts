/**
 * The subject round (§6): resolving a topic, the plan it produces, the round itself, and the
 * subject map it seals into.
 *
 * This module is the only place the subject screen talks to `/subjects/*` and `/topics/resolve`,
 * for the same reason `api.ts` is the only place anything talks to the backend at all: the CSRF
 * header, the error shape and the mock flag are handled once, there.
 *
 * Two honesty rules from the backend are load-bearing here and are *not* re-decided in the
 * browser:
 *
 * * A skill the round did not test arrives with `status: "not_measured"`, `score: null` and
 *   `n: 0`. Nothing in this file converts that into a zero, a percentage or an empty bar — the
 *   screen greys it out and says "not measured yet", which is a different claim.
 * * Every payload that shows a skill set carries `reason` ("this topic leans on these four").
 *   The screen renders it wherever the skills are rendered; it never invents its own.
 *
 * `?mock=1`: `mock.ts` is the shared fixture table and is not this module's to extend, so the
 * fixtures for these endpoints live below, next to the shapes they imitate. They are *recorded
 * shapes* — the calculus and government plans are the sentences the real service produces —
 * and the small state machine under them exists so the round can be clicked through with no
 * backend at all. The layout banner already says the whole app is mocked; nothing here
 * pretends a fixture answer was measured.
 */
"use client";

import { get, isMock, post } from "./api";

/* ------------------------------------------------------------------ types ---- */

export type SubjectSkill = {
  id: string;
  family: string;
  title: string;
  measures: string;
  implies: string;
  grader?: string;
  seconds?: number | null;
  items?: number;
  taxonomy_version?: string;
  game?: { kind?: string; seconds?: number; items?: number; instructions?: string };
  rating?: number | null;
  rating_label?: string;
  role?: string;
  order?: number;
  reason?: string;
  status: string;
  /* snapshot-only fields */
  n?: number;
  observations?: number;
  correct?: number;
  accuracy?: number | null;
  evidence?: string;
  note?: string;
  score?: number | null;
  time_capped?: boolean;
};

export type SubjectPlan = {
  topic: { key: string | null; title: string; family: string; subject: string; source: string };
  reason: string;
  leanings_reason?: string;
  leanings_source?: string;
  taxonomy_version: string;
  budget_seconds: number;
  estimated_seconds: number;
  tested: SubjectSkill[];
  not_measured: SubjectSkill[];
  counts: { tested: number; not_measured: number };
  cross_check?: string | null;
  time_capped?: boolean;
  notes?: string[];
};

export type ResolvedTopic = {
  raw: string;
  normalised: string;
  topic_key: string | null;
  title: string;
  subject: string;
  family: string;
  family_title?: string;
  level_hint: string;
  source: string;
  note: string;
  alternatives: { title?: string; key?: string; match?: string }[];
};

export type ResolveResult = {
  resolved: ResolvedTopic;
  suggestions: { title?: string; key?: string; match?: string }[];
  leanings?: Record<string, unknown> | null;
};

export type ServedTask = {
  id: string;
  skill?: string;
  skill_title?: string;
  kind: string;
  phase?: string;
  stage?: string;
  stem?: string;
  instructions?: string;
  options?: string[];
  items?: string[]; // order tasks: the things to put in order
  left?: string[]; // match tasks
  right?: string[];
  passage?: string;
  data?: unknown;
  unit?: string;
  checklist?: string[];
  avoid?: string[];
  seconds?: number;
  wagerable?: boolean;
  /* lesson tasks (kind comes back as the round's "lesson" view kind instead) */
  method?: string;
  method_label?: string;
  body?: string;
  visual?: VisualSpec;
  steps?: { index: number; text: string; why?: string }[];
  why?: string;
};

export type VisualSpec = {
  kind: string;
  title: string;
  alt: string;
  why?: string;
  nodes?: { index: number; label: string; text: string; decision?: boolean }[];
  columns?: string[];
  rows?: string[][];
  labels?: string[];
};

export type LadderStepPublic = {
  step_id: string;
  node_id: string;
  node_title: string;
  sub_area: string;
  rung: number;
  rung_title: string;
  demand: string;
  index: number;
  of: number;
  task: ServedTask & { node?: string; rung?: number };
  why: string;
};

export type RoundView = {
  run_id: string;
  status: string;
  stage: "skills" | "ladder" | "method" | "snapshot";
  stage_title: string;
  kind: "task" | "lesson" | "done";
  task: (ServedTask | LadderStepPublic) | null;
  step_id: string | null;
  progress: Record<string, unknown> & { why?: string; rung?: number | null };
  note: string;
  topic: { key: string | null; title: string; family: string; level_hint: string; resolved_from: string };
  plan: SubjectPlan;
  reason: string;
  taxonomy_version: string;
  measured_trials: number;
  scored?: { correct?: boolean | null; status?: string; why?: string };
  duplicate?: boolean;
  ladder?: LadderPayload | null;
  method_test?: MethodPayload | null;
};

export type LadderPosition = {
  sub_area: string;
  nodes: { id: string; title: string }[];
  steady_rung: number | null;
  rung_title: string;
  placed: boolean;
  below_rung: number | null;
  n: number;
  observations: number;
  evidence: string;
  note: string;
  score: null;
};

export type LadderPayload = {
  available: boolean;
  note?: string;
  chain?: { chain_id: string; title: string; sub_areas: string[]; depth: number; max_depth: number };
  positions?: LadderPosition[];
  placed_sub_areas?: number;
  sub_areas?: number;
  items?: number;
  max_items?: number;
  rungs?: { rung: number; key: string; title: string; demand: string }[];
};

export type MethodLegResult = {
  leg: string;
  concept: string;
  concept_title: string;
  method: string;
  method_label: string;
  correct: number;
  n: number;
  observations: number;
  accuracy: number;
  range: [number, number];
  expected: number;
  residual: number;
  evidence: string;
  note: string;
  score: null;
};

export type MethodComparison = {
  available: boolean;
  legs?: MethodLegResult[];
  n?: number;
  evidence?: string;
  learner_style?: null;
  claim?: null;
  note?: string;
  expected_basis?: string;
};

export type MethodPayload = {
  available: boolean;
  note?: string;
  assignment?: { cell: number; of_cells: number; design: string; why: string };
  results?: MethodComparison;
  recheck_at?: string | null;
};

export type SubjectSnapshot = {
  available: boolean;
  snapshot_id?: string;
  run_id?: string;
  topic?: { key: string | null; title: string; family: string };
  taxonomy_version: string;
  taken_at?: string;
  skills?: SubjectSkill[];
  measured?: SubjectSkill[];
  not_measured?: SubjectSkill[];
  counts?: { measured: number; not_measured: number };
  reason?: string;
  ladder?: LadderPayload;
  method_test?: MethodPayload;
  completion?: { stages: { stage: string; done: string; evidence: string }[]; note?: string };
  note?: string;
};

export type RecheckDue = {
  method_test_id: string;
  topic_key: string;
  due_at: string;
  concepts: { concept: string; concept_title: string; method_first_taught: string; items: ServedTask[] }[];
  why: string;
};

/* ------------------------------------------------------------- real calls ---- */

export async function resolveTopic(text: string): Promise<ResolveResult> {
  if (isMock()) return mockResolve(text);
  return post<ResolveResult>("topics/resolve", { text });
}

export async function fetchPlan(topic: string): Promise<SubjectPlan> {
  if (isMock()) return mockPlan(topic);
  const { data, error } = await get<SubjectPlan>("subjects/plan", { topic });
  if (!data) throw new Error(error ?? "the plan did not come back");
  return data;
}

export async function startRound(topic: string): Promise<RoundView> {
  if (isMock()) return mockStartRound(topic);
  return post<RoundView>("subjects/round", { topic });
}

export async function fetchRound(runId: string): Promise<RoundView> {
  if (isMock()) return mockView();
  const { data, error } = await get<RoundView>(`subjects/round/${runId}`);
  if (!data) throw new Error(error ?? "the round did not come back");
  return data;
}

export async function submitTrial(
  runId: string,
  body: { task_id: string; response: Record<string, unknown>; latency_ms?: number; wager?: number },
): Promise<RoundView> {
  if (isMock()) return mockTrial(body);
  return post<RoundView>(`subjects/round/${runId}/trial`, body);
}

export async function ladderStep(
  step: string,
  body: { response?: Record<string, unknown>; latency_ms?: number; wager?: number } = {},
): Promise<RoundView> {
  if (isMock()) return mockLadder(body);
  return post<RoundView>(`subjects/ladder/${step}`, body);
}

export async function finishRound(runId: string): Promise<SubjectSnapshot> {
  if (isMock()) return mockFinish();
  return post<SubjectSnapshot>(`subjects/round/${runId}/finish`, {});
}

export async function fetchSnapshot(topic?: string): Promise<SubjectSnapshot> {
  if (isMock()) return mockSnapshot();
  const { data, error } = await get<SubjectSnapshot>("subjects/snapshot", topic ? { topic } : undefined);
  if (!data) throw new Error(error ?? "the subject map did not come back");
  return data;
}

export async function fetchRechecks(): Promise<{ due: RecheckDue[]; count: number }> {
  if (isMock()) return { due: [], count: 0 };
  const { data, error } = await get<{ due: RecheckDue[]; count: number }>("subjects/recheck");
  if (!data) throw new Error(error ?? "the rechecks did not come back");
  return data;
}

export async function submitRecheck(
  methodTestId: string,
  answers: Record<string, unknown>[][],
): Promise<Record<string, unknown>> {
  if (isMock()) return { duplicate: false, status: "rechecked", results: [] };
  return post<Record<string, unknown>>(`subjects/recheck/${methodTestId}`, { answers });
}

/* ============================================================== mock mode ====
 * Recorded shapes for `?mock=1`. The plan sentences for calculus and government are the
 * service's own; the tasks are short fixtures in the served shape (no answer keys leave
 * this module — the public copy is what the state machine scores against, held privately
 * in `mockKeys`, the same split the server keeps). */

const MOCK_TAXONOMY_VERSION = "subj-taxonomy-2026.10.1";

type FixtureSkill = {
  id: string;
  title: string;
  measures: string;
  kind: string;
  rating: number;
  role: "leaned" | "cross_check";
  reason: string;
};

const RATING_LABELS: Record<number, string> = { 0: "not used", 1: "sometimes", 2: "often", 3: "all the time" };

function fixturePlan(topicKey: string): SubjectPlan {
  const calculus: FixtureSkill[] = [
    { id: "maths.symbol_fluency", title: "Symbol fluency", measures: "whether two expressions that look different read as the same statement", kind: "choice", rating: 3, role: "leaned", reason: "Calculus leans on this all the time (3 of 3)" },
    { id: "maths.graph_sense", title: "Graph sense", measures: "whether a graph and the story it tells can be matched in both directions", kind: "choice", rating: 3, role: "leaned", reason: "Calculus leans on this all the time (3 of 3)" },
    { id: "maths.checking", title: "Checking somebody else's working", measures: "whether a wrong step can be found in working that is not their own", kind: "choice", rating: 3, role: "leaned", reason: "Calculus leans on this all the time (3 of 3)" },
    { id: "maths.pattern_structure", title: "Pattern and structure", measures: "whether the next term of a pattern can be produced and the rule stated", kind: "numeric", rating: 2, role: "cross_check", reason: "the cross-check: the same topic through a different kind of task (numeric), so a lucky run on the others has somewhere to show up" },
  ];
  const government: FixtureSkill[] = [
    { id: "hist.source_analysis", title: "Source analysis", measures: "whether a source can be read for who made it and why", kind: "choice", rating: 3, role: "leaned", reason: "Government and civics leans on this all the time (3 of 3)" },
    { id: "hist.argument", title: "Supporting a claim with evidence", measures: "whether a claim is matched to the evidence that actually supports it", kind: "choice", rating: 3, role: "leaned", reason: "Government and civics leans on this all the time (3 of 3)" },
    { id: "hist.perspective", title: "Seeing the other side", measures: "whether the strongest counter-argument can be stated fairly", kind: "choice", rating: 3, role: "leaned", reason: "Government and civics leans on this all the time (3 of 3)" },
    { id: "hist.cause_consequence", title: "Cause and consequence", measures: "whether a cause can be told from a condition and a trigger", kind: "choice", rating: 2, role: "cross_check", reason: "the cross-check: the same topic through a different kind of task (choice), so a lucky run on the others has somewhere to show up" },
  ];
  const isCalc = topicKey !== "government";
  const tested = (isCalc ? calculus : government).map((s, i) => skillRow(s, i));
  const skipped: [string, string, number, string][] = isCalc
    ? [
        ["maths.number_sense", "Number sense and estimation", 2, "Number sense and estimation: rated 2 of 3, but the round tests the strongest leanings plus one cross-check — not measured, not zero"],
        ["maths.translate_words", "Translating words and symbols", 2, "Translating words and symbols: rated 2 of 3, but the round tests the strongest leanings plus one cross-check — not measured, not zero"],
        ["maths.logic_chains", "Logic chains", 2, "Logic chains: rated 2 of 3, but the round tests the strongest leanings plus one cross-check — not measured, not zero"],
      ]
    : [["hist.chronology", "Chronology", 1, "Chronology: this topic uses it only sometimes (1 of 3), and the round spends its 4 games on what the topic leans on more — not measured, not zero"]];
  const title = isCalc ? "Calculus" : "Government and civics";
  const family = isCalc ? "maths" : "history";
  return {
    topic: { key: isCalc ? "calculus" : "government", title, family, subject: isCalc ? "Maths" : "History, humanities and government", source: "authored" },
    reason: isCalc
      ? "We'll check four things, because Calculus leans hardest on symbol fluency, graph sense and checking somebody else's working, plus pattern and structure as a cross-check. The other 3 skills in this subject are shown as not measured — the round did not test them, which is not the same as finding nothing."
      : "We'll check four things, because Government and civics leans hardest on source analysis, supporting a claim with evidence and seeing the other side, plus cause and consequence as a cross-check. The other skill in this subject is shown as not measured — the round did not test it, which is not the same as finding nothing.",
    leanings_source: "authored",
    taxonomy_version: MOCK_TAXONOMY_VERSION,
    budget_seconds: 240,
    estimated_seconds: 160,
    tested,
    not_measured: skipped.map(([id, t, rating, reason]) => ({
      id,
      family,
      title: t,
      measures: "",
      implies: "",
      rating,
      rating_label: RATING_LABELS[rating],
      role: "not_selected",
      status: "not_measured",
      reason,
      score: null,
      n: 0,
    })),
    counts: { tested: tested.length, not_measured: skipped.length },
    cross_check: tested.find((s) => s.role === "cross_check")?.id ?? null,
    time_capped: false,
    notes: [],
  };
}

function skillRow(s: FixtureSkill, order: number): SubjectSkill {
  return {
    id: s.id,
    family: s.id.split(".")[0] === "hist" ? "history" : "maths",
    title: s.title,
    measures: s.measures,
    implies: "",
    grader: "deterministic",
    seconds: 40,
    items: 3,
    taxonomy_version: MOCK_TAXONOMY_VERSION,
    game: { kind: s.kind, seconds: 40, items: 3 },
    rating: s.rating,
    rating_label: RATING_LABELS[s.rating],
    role: s.role,
    order: order + 1,
    reason: s.reason,
    status: "planned",
  };
}

/* A tiny served-round state machine. Only reachable under `?mock=1`. */
type MockState = {
  plan: SubjectPlan;
  stage: RoundView["stage"];
  skillIndex: number;
  itemIndex: number;
  ladderIndex: number;
  rung: number;
  methodIndex: number; // 0..7: lesson,q,q,q per leg
  answers: { skill: string; correct: boolean }[];
  ladderAnswers: { correct: boolean }[];
  methodAnswers: { correct: boolean }[];
  keys: Record<string, number>;
  startedAt: number;
};

let mock: MockState | null = null;

function mockPlan(topic: string): Promise<SubjectPlan> {
  const key = /gov|civic|politic/i.test(topic) ? "government" : "calculus";
  return Promise.resolve(fixturePlan(key));
}

function mockResolve(text: string): Promise<ResolveResult> {
  const gov = /gov|civic|politic/i.test(text);
  const key = gov ? "government" : "calculus";
  const plan = fixturePlan(key);
  return Promise.resolve({
    resolved: {
      raw: text,
      normalised: text.toLowerCase().trim(),
      topic_key: key,
      title: plan.topic.title,
      subject: plan.topic.subject,
      family: plan.topic.family,
      level_hint: /gcse|a level/i.test(text) ? "secondary" : "",
      source: "authored",
      note: "we have written-down skill ratings and a prerequisite chain for this topic",
      alternatives: [],
    },
    suggestions: [],
    leanings: null,
  });
}

function mockTaskFor(skill: SubjectSkill, item: number): ServedTask {
  const stems: Record<string, string[]> = {
    "maths.symbol_fluency": ["Are 2(x + 3) and 2x + 6 the same for every value of x?", "Are x² and 2x the same for every value of x?", "Are (x + 1)(x − 1) and x² − 1 the same for every value of x?"],
    "hist.source_analysis": ["A minister writes that a policy 'worked'. What is the first question to ask of this source?", "Two newspapers describe the same protest differently. What best explains the difference?", "A photograph from a campaign is used as evidence. What can it not show on its own?"],
  };
  const stem = (stems[skill.id] ?? [`${skill.title}: fixture question ${item + 1} of 3 — ${skill.measures}`])[item % 3];
  const id = `${skill.id}_${item}`;
  if (mock) mock.keys[id] = item % 3 === 2 ? 1 : 0;
  return {
    id,
    skill: skill.id,
    skill_title: skill.title,
    kind: "choice",
    phase: "measure",
    stage: "skills",
    stem,
    instructions: skill.measures,
    options: ["The first answer", "The second answer", "The third answer", "The fourth answer"],
    seconds: 40,
    wagerable: true,
  };
}

function mockView(): Promise<RoundView> {
  if (!mock) throw new Error("no mock round is open");
  const m = mock;
  const base: RoundView = {
    run_id: "subr_mock",
    status: "in_progress",
    stage: m.stage,
    stage_title: { skills: "Skills", ladder: "The ladder", method: "A quick lesson", snapshot: "Your subject map" }[m.stage],
    kind: "task",
    task: null,
    step_id: null,
    progress: { stage: m.stage, rung: m.stage === "ladder" ? m.rung : null, why: "" },
    note: "",
    topic: { key: m.plan.topic.key, title: m.plan.topic.title, family: m.plan.topic.family, level_hint: "", resolved_from: "authored" },
    plan: m.plan,
    reason: m.plan.reason,
    taxonomy_version: MOCK_TAXONOMY_VERSION,
    measured_trials: m.answers.length,
  };
  if (m.stage === "skills") {
    const skill = m.plan.tested[m.skillIndex];
    if (!skill) {
      m.stage = "ladder";
      return mockView();
    }
    base.task = mockTaskFor(skill, m.itemIndex);
    base.note = `skill ${m.skillIndex * 3 + m.itemIndex + 1} of ${m.plan.tested.length * 3}: ${skill.title}`;
    base.progress = {
      ...base.progress,
      skill_item: m.skillIndex * 3 + m.itemIndex + 1,
      skill_items: m.plan.tested.length * 3,
      skills_done: m.skillIndex,
      skills_planned: m.plan.tested.length,
    };
    return Promise.resolve(base);
  }
  if (m.stage === "ladder") {
    if (m.ladderIndex >= 5) {
      m.stage = "method";
      return mockView();
    }
    const step: LadderStepPublic = {
      step_id: `subr_mock.${m.ladderIndex}`,
      node_id: "calc_gradient",
      node_title: "The gradient of a straight line",
      sub_area: ["gradient", "tangents", "area", "optimisation"][m.ladderIndex % 4],
      rung: m.rung,
      rung_title: ["Have you met it?", "Recognise it", "Recall it", "Apply a standard case", "Apply it in a new situation", "Explain why"][m.rung],
      demand: "produce the key fact without a prompt",
      index: m.ladderIndex,
      of: 9,
      task: {
        id: `calculus:calc_gradient:${m.rung}`,
        kind: "choice",
        node: "calc_gradient",
        rung: m.rung,
        stem: `Ladder question ${m.ladderIndex + 1}: a fixture item at rung ${m.rung}.`,
        options: ["The first answer", "The second answer", "The third answer"],
      },
      why: m.ladderIndex === 0 ? "starting in the middle of the chain, at rung 2" : "the staircase moved on the last answer",
    };
    if (mock) mock.keys[step.task.id] = 0;
    base.task = step;
    base.step_id = step.step_id;
    base.progress = { ...base.progress, why: step.why, rung: m.rung, sub_area: step.sub_area, ladder_items: m.ladderIndex, ladder_max_items: 9 };
    return Promise.resolve(base);
  }
  if (m.stage === "method") {
    if (m.methodIndex >= 8) {
      m.stage = "snapshot";
      return mockView();
    }
    base.progress = { ...base.progress, method_item: m.methodIndex + 1, method_items: 8 };
    const leg = m.methodIndex < 4 ? 0 : 1;
    const within = m.methodIndex % 4;
    const method = leg === 0 ? "text" : "visual";
    if (within === 0) {
      base.kind = "lesson";
      base.task = {
        id: `method_${leg}_lesson`,
        kind: "lesson",
        method,
        method_label: method === "text" ? "Read it" : "See it",
        seconds: 90,
        body:
          method === "text"
            ? "The gradient of a straight line says how much y changes for each step across in x. Take any two points on the line and measure two things: the change in y between them, called the rise, and the change in x, called the run. The gradient is the rise divided by the run. A line that goes up 4 as x goes across 2 has a gradient of 2."
            : undefined,
        visual:
          method === "visual"
            ? {
                kind: "flow",
                title: "Finding a gradient from two points",
                alt: "Four steps: pick two points, measure the rise, measure the run, divide",
                nodes: [
                  { index: 1, label: "Step 1", text: "Pick any two points on the line" },
                  { index: 2, label: "Step 2", text: "Measure the rise: the change in y" },
                  { index: 3, label: "Step 3", text: "Measure the run: the change in x" },
                  { index: 4, label: "Step 4", text: "Divide rise by run" },
                ],
              }
            : undefined,
        why: "the way this concept is taught was dealt by a seeded step in code",
      };
    } else {
      const id = `method_${leg}_q${within}`;
      m.keys[id] = 0;
      base.task = {
        id,
        kind: "choice",
        stem: `Lesson question ${within} of 3 (fixture): what is the gradient of a line rising 4 over a run of 2?`,
        options: ["2", "4", "1/2", "8"],
        seconds: 40,
      };
    }
    return Promise.resolve(base);
  }
  base.kind = "done";
  base.note = "everything this round can measure has been measured; finish it to get the subject map";
  return Promise.resolve(base);
}

function mockStartRound(topic: string): Promise<RoundView> {
  return mockPlan(topic).then((plan) => {
    mock = {
      plan,
      stage: "skills",
      skillIndex: 0,
      itemIndex: 0,
      ladderIndex: 0,
      rung: 2,
      methodIndex: 0,
      answers: [],
      ladderAnswers: [],
      methodAnswers: [],
      keys: {},
      startedAt: Date.now(),
    };
    return mockView();
  });
}

function mockTrial(body: { task_id: string; response: Record<string, unknown> }): Promise<RoundView> {
  if (!mock) throw new Error("no mock round is open");
  const m = mock;
  if (m.stage === "skills") {
    const skill = m.plan.tested[m.skillIndex];
    const correct = Number(body.response.choice) === (m.keys[body.task_id] ?? 0);
    if (skill) m.answers.push({ skill: skill.id, correct });
    m.itemIndex += 1;
    if (m.itemIndex >= 3) {
      m.itemIndex = 0;
      m.skillIndex += 1;
    }
    return mockView().then((v) => ({ ...v, scored: { correct, status: "scored", why: correct ? "right" : "not this time" } }));
  }
  // method stage
  if (!body.task_id.endsWith("lesson")) {
    const correct = Number(body.response.choice) === (m.keys[body.task_id] ?? 0);
    m.methodAnswers.push({ correct });
    m.methodIndex += 1;
    return mockView().then((v) => ({ ...v, scored: { correct, status: "scored", why: correct ? "right" : "not this time" } }));
  }
  m.methodIndex += 1;
  return mockView();
}

function mockLadder(body: { response?: Record<string, unknown> }): Promise<RoundView> {
  if (!mock) throw new Error("no mock round is open");
  const m = mock;
  if (body.response) {
    const taskId = `calculus:calc_gradient:${m.rung}`;
    const correct = Number(body.response.choice) === (m.keys[taskId] ?? 0);
    m.ladderAnswers.push({ correct });
    m.rung = Math.max(0, Math.min(5, m.rung + (correct ? 1 : -1)));
    m.ladderIndex += 1;
    return mockView().then((v) => ({ ...v, scored: { correct, status: "scored", why: correct ? "right — one rung up" : "not this time — one rung down" } }));
  }
  return mockView();
}

function mockSnapshot(): Promise<SubjectSnapshot> {
  if (!mock) {
    return Promise.resolve({
      available: false,
      taxonomy_version: MOCK_TAXONOMY_VERSION,
      note: "no subject round has been finished yet, so there is no subject map. Nothing here is a zero and nothing is missing: it has not happened.",
    });
  }
  const m = mock;
  const rows: SubjectSkill[] = m.plan.tested.map((s) => {
    const mine = m.answers.filter((a) => a.skill === s.id);
    const correct = mine.filter((a) => a.correct).length;
    return mine.length
      ? {
          ...s,
          status: "measured",
          n: mine.length,
          observations: mine.length,
          correct,
          accuracy: Math.round((correct / mine.length) * 1000) / 1000,
          evidence: "early signal",
          note: `early signal: ${mine.length} answer(s) is under the 8 the app asks for before it calls an estimate measured`,
          score: null,
        }
      : { ...s, status: "not_measured", n: 0, observations: 0, accuracy: null, evidence: "not measured", note: "nothing was answered here, so this is not measured rather than failed", score: null };
  });
  const all = [...rows, ...m.plan.not_measured];
  const methodCorrect = m.methodAnswers.filter((a) => a.correct).length;
  return Promise.resolve({
    available: true,
    snapshot_id: "subj_mock",
    run_id: "subr_mock",
    topic: { key: m.plan.topic.key, title: m.plan.topic.title, family: m.plan.topic.family },
    taxonomy_version: MOCK_TAXONOMY_VERSION,
    taken_at: new Date().toISOString(),
    skills: all,
    measured: all.filter((r) => r.status === "measured"),
    not_measured: all.filter((r) => r.status !== "measured"),
    counts: { measured: all.filter((r) => r.status === "measured").length, not_measured: all.filter((r) => r.status !== "measured").length },
    reason: m.plan.reason,
    ladder: {
      available: true,
      positions: ["gradient", "tangents", "area", "optimisation"].map((sub_area, i) => ({
        sub_area,
        nodes: [{ id: "calc_gradient", title: "The gradient of a straight line" }],
        steady_rung: i === 0 && m.ladderAnswers.length ? m.rung : null,
        rung_title: i === 0 && m.ladderAnswers.length ? "Recall it" : "not reached",
        placed: i === 0 && m.ladderAnswers.length > 0,
        below_rung: null,
        n: i === 0 ? m.ladderAnswers.length : 0,
        observations: i === 0 ? m.ladderAnswers.length : 0,
        evidence: i === 0 && m.ladderAnswers.length ? "early signal" : "not measured",
        note: i === 0 ? "" : "the ladder did not reach this sub-area, so it is not measured — which is not the same as being at rung 0",
        score: null,
      })),
      placed_sub_areas: m.ladderAnswers.length ? 1 : 0,
      sub_areas: 4,
      items: m.ladderAnswers.length,
      max_items: 9,
    },
    method_test: {
      available: m.methodAnswers.length > 0,
      note: m.methodAnswers.length ? "" : "the pop-up lesson was not reached",
      results: m.methodAnswers.length
        ? {
            available: true,
            n: m.methodAnswers.length,
            evidence: "early signal",
            learner_style: null,
            claim: null,
            note: "six answers can say which way helped more this time; they cannot say how you learn",
            legs: [
              { leg: "a", concept: "calc_gradient_line", concept_title: "The gradient of a straight line", method: "text", method_label: "Read it", correct: methodCorrect, n: m.methodAnswers.length, observations: m.methodAnswers.length, accuracy: m.methodAnswers.length ? methodCorrect / m.methodAnswers.length : 0, range: [0, 1], expected: 0.5, residual: 0, evidence: "early signal", note: "", score: null },
            ],
          }
        : { available: false },
    },
    completion: {
      stages: [
        { stage: "skills", done: `${m.answers.length} skill answers`, evidence: `${m.answers.length} scored answer(s)` },
        { stage: "ladder", done: "the ladder finished", evidence: `${m.ladderAnswers.length} item(s)` },
        { stage: "method", done: "the pop-up lesson was answered", evidence: `${m.methodAnswers.length} answer(s)` },
      ],
      note: "A subject round places you; it does not grade you.",
    },
    note: "Every number here says how many answers are behind it. What the round did not test is listed as not measured, which is not the same as finding nothing.",
  });
}

function mockFinish(): Promise<SubjectSnapshot> {
  if (mock) mock.stage = "snapshot";
  return mockSnapshot();
}
