/**
 * Fixtures for `?mock=1`, and nothing else.
 *
 * These exist so the interface can be reviewed on a machine with no backend, no model
 * key and no encrypted database -- not so the app can be demoed as if it had learned
 * something. Every payload below is a *recorded shape* from the real API (the tests in
 * ``backend/tests/test_api_learner_loop.py`` assert against the same keys), and every
 * screen that renders them shows the mock banner. If you are reading a screenshot and
 * there is no red bar at the top, this file is not involved.
 */

type Row = {
  method: string;
  match: RegExp;
  /** A recorded payload, or a function for the two cases that genuinely have state (the baseline
   *  run advances; see `baselineView`). A static row for a stateful flow would let a broken "what
   *  comes next" wiring look fine under `?mock=1`. */
  body: unknown | ((url: string, method: string) => unknown);
  status?: number;
  error?: { message: string; detail?: unknown };
};

const learner = {
  student_id: "stu_mock_0001",
  display_name: "Mock learner",
  role: "student",
  onboarding_complete: true,
  diagnostic_complete: true,
  voice_enabled: false,
  voice_gender: "female",
  language: "en",
  consent_analytics: true,
  consent_share_with_ai: true,
  sessions: 3,
  mock: true,
};

const item = {
  id: "itm_fraction_add_01",
  topic_id: "top_21f47f97804945afa7a5",
  kind: "mcq",
  stem: "What is 1/2 + 1/3?",
  prompt_context: null,
  visual_spec: null,
  choices: [
    { index: 0, text: "2/5" },
    { index: 1, text: "5/6" },
    { index: 2, text: "1/5" },
    { index: 3, text: "5/5" },
  ],
  experience_mode: "read",
  difficulty: 0.5,
  est_seconds: 70,
  hint_count: 2,
  has_worked_example: true,
};

const planEntry = {
  item,
  topic_id: item.topic_id,
  reason: "you have not been placed on this topic yet",
  utility: 0.62,
  presentation: {
    max_words: 90,
    hints_allowed: 2,
    require_teachback: false,
    attach_worked_example: false,
    method: "explanation",
    method_label: "Explanation first",
    method_assignment: { method: "explanation", design: "randomised", arm: "explanation_first", assigned_by: "rotation", why: "method explanation for attempt 1 on this concept (seeded rotation per learner, so it differs from learner to learner)" },
    // The teaching the assigned method produces: short steps, a check after each, nothing cut.
    teaching: {
      method: "explanation",
      kind: "segments",
      steps: [
        { index: 1, text: "A fraction only names parts of one whole, so the parts have to be the same size before you can count them together.", words: 24, check: { question: "Without scrolling back: what does this step say about size?", kind: "recall", expect_any: ["size", "parts", "fraction"], hint: "One sentence is enough. The step is about size." } },
        { index: 2, text: "One half and one third name differently sized parts, so 2/5 comes from adding tops and bottoms — which changes what the fraction means.", words: 24, check: { question: "Without scrolling back: what does this step say about fraction?", kind: "recall", expect_any: ["fraction", "parts", "tops"], hint: "One sentence is enough. The step is about fraction." } },
        { index: 3, text: "Rewrite both over a common denominator first, then add only the numerators and keep that denominator.", words: 17, check: { question: "Without scrolling back: what does this step say about denominator?", kind: "recall", expect_any: ["denominator", "numerators", "add"], hint: "One sentence is enough. The step is about denominator." } },
      ],
      dropped: [],
      source_words: 65,
      note: "",
    },
  },
  decision_trace: ["no trace for this topic yet", "chosen for information per attempt"],
  position: 1,
  of: 12,
};

const mastery = {
  topic_id: item.topic_id,
  title: "Adding and subtracting fractions",
  p_know: 0.62,
  band: "developing",
  observations: 4,
  correct: 3,
  ci: [0.31, 0.86],
  half_life_hours: 26.5,
  last_practice: "2026-10-10T18:04:11",
  stale: false,
};

/** A second lane, so the progress chart in mock mode shows what it actually claims: one
 *  estimate with enough answers behind it to be a number, and one that is still an early
 *  signal with a wide interval. One row would render as a chart that cannot be wrong. */
const masterySecond = {
  topic_id: "top_mock_ratios",
  title: "Ratios and proportional reasoning",
  p_know: 0.29,
  band: "new",
  observations: 14,
  correct: 4,
  ci: [0.21, 0.38],
  half_life_hours: 51.0,
  last_practice: "2026-10-09T09:12:00",
  stale: false,
};

const loopSteps = [
  { step: "test", done: "what the learner just answered", evidence: "4 topic traces" },
  { step: "observe", done: "metrics derived from the event log", evidence: "3 evidence rows" },
  { step: "understand", done: "hypotheses with status and disproof conditions", evidence: "2 hypotheses" },
  { step: "personalize", done: "proposals awaiting a decision - not applied", evidence: "1 proposals" },
  { step: "learn", done: "measured effect of what was approved before", evidence: "0 evaluated" },
];

const proposal = {
  id: "adp_mock_0001",
  student_id: learner.student_id,
  strategy: "worked_examples",
  strategy_label: "Worked example before the next attempt",
  what_it_does: "Show a fully solved, step labelled example of the same problem type immediately before the question.",
  citations: [{ ref: "Sweller & Cooper 1985", claim: "Worked examples beat equivalent practice for novice schema acquisition." }],
  action: "insert_worked_example",
  action_payload: { steps_before_answer: 1, fade_after: 3 },
  facts: "On 4 of your last 5 first attempts you changed your answer after writing it, and each of those started from a step that was not the first.",
  pattern: "The three items where you got there without changing anything all had an example on screen first.",
  hypothesis: "Showing one solved example before the attempt reduces the load of holding the procedure while doing it.",
  caveat: "Other explanations we cannot rule out: those three items may simply have been easier",
  alternatives: [{ explanation: "those three items may simply have been easier" }],
  change: "Insert a worked example before the next attempt on this topic, and fade it after 3 correct answers.",
  ask: "Want me to try that? You can say no, or change it - I will not change how you learn without asking.",
  expected_effect: "Fewer changed answers on the first attempt of each item.",
  review_metric: "accuracy_after_read",
  review_window: { kind: "next_items", metric: "accuracy_after_read", min_items: 6, max_items: 14 },
  review_window_text: "your next 6-14 answers",
  status: "pending",
  student_note: null,
  modifications: null,
  created_at: "2026-10-11T09:12:44",
  decided_at: null,
  baseline: null,
  outcome: null,
  evaluation_status: "awaiting_decision",
  hypothesis_ref: "hyp_mock_0001",
  evidence_ids: ["evd_mock_1", "evd_mock_2", "evd_mock_3"],
};

const receipt = {
  attempt_id: "att_mock_0001",
  item_id: item.id,
  topic_id: item.topic_id,
  correct: true,
  score: 1,
  grading_method: "mcq_index",
  feedback: "Correct. The denominators have to match because a fraction names equal parts, and halves and thirds are not the same size.",
  mastery: {
    topic_id: item.topic_id,
    lag_hours_since_practice: 2.4,
    p_know_before: 0.5,
    p_know: 0.62,
    delta: 0.12,
    band: "developing",
    observations: 4,
    expected_recall: 0.86,
    half_life_hours: 26.5,
    due_at: "2026-10-12T14:40:00",
    first_observation: false,
    params: { p_init: 0.2, p_learn: 0.2, p_guess: 0.2, p_slip: 0.1, p_forget: 0.02, n_observations: 4 },
  },
  derived_metrics: { attempts: 1, correct: true, score: 1 },
  teachback: null,
  retention: null,
  flags: [],
  proposals_expected: true,
};

/* --------------------------------------------------- recorded lesson payloads ----
 * Captured from a live `mode=local` backend on 2026-10-12 with no model key, so every
 * "why?" answer below is the honest deterministic sentence rather than a model's prose.
 * They are long on purpose: a lesson is a real 8-frame trace, and a mock that showed two
 * frames would be reviewing a player nobody has actually seen.
 *
 * The one thing a fixture cannot do is *compute*. `/variables` below returns the same
 * recorded frames under new parameters, because re-running the simulation is exactly what
 * the backend does and this file must not pretend otherwise. If you are reviewing what
 * happens when the numbers change, you need the backend running.
 */
const lessonFrames = [
    {
      kind: "init",
      title: "12 sorted values, and one target",
      narration: "Looking for 37. The list is sorted, so the only thing we need to know at any moment is the range where it could still be: index 0 to 11.",
      state: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        lo: 0,
        hi: 11,
        mid: null,
        target: 37,
        compared: [],
        dropped: [],
        found: null,
        live: 12,
      },
      highlights: ["range"],
      cause: null,
      prompt: {
        type: "comparison_count",
        question: "For 12 sorted values, how many comparisons does a search like this need, worst case?",
        choices: ["about n/2", "about log2(n)", "all n of them"],
      },
      i: 0,
    },
    {
      kind: "probe",
      title: "arr[5] = 35, target 37",
      narration: "The live range is index 0 to 11. Its middle is 5, which holds 35. Because 35 is smaller than 37, the target can only be to the right.",
      state: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        lo: 0,
        hi: 11,
        mid: 5,
        target: 37,
        compared: [5],
        dropped: [],
        found: null,
        live: 12,
      },
      highlights: ["mid"],
      cause: "arr[5] < target",
      prompt: {
        type: "next_branch",
        question: "arr[5] is 35 and the target is 37. Which half survives this comparison?",
        choices: ["keep the left half", "keep the right half", "stop: found it"],
      },
      i: 1,
    },
    {
      kind: "discard",
      title: "half the range removed",
      narration: "arr[5] = 35 < 37, so index 5 and everything left of it is out. The range goes from 12 places to 6.",
      state: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        lo: 6,
        hi: 11,
        mid: null,
        target: 37,
        compared: [5],
        dropped: [0, 1, 2, 3, 4, 5],
        found: null,
        live: 6,
      },
      highlights: ["dropped"],
      cause: "arr[5] = 35 < 37, so index 5 and everything left of it is out",
      prompt: {
        type: "next_probe_index",
        question: "With that half gone, which index gets looked at next?",
        choices: ["index 8", "index 5", "index 9"],
      },
      i: 2,
    },
    {
      kind: "probe",
      title: "arr[8] = 44, target 37",
      narration: "The live range is index 6 to 11. Its middle is 8, which holds 44. Because 44 is larger than 37, the target can only be to the left.",
      state: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        lo: 6,
        hi: 11,
        mid: 8,
        target: 37,
        compared: [5, 8],
        dropped: [0, 1, 2, 3, 4, 5],
        found: null,
        live: 6,
      },
      highlights: ["mid"],
      cause: "arr[8] > target",
      prompt: {
        type: "next_branch",
        question: "arr[8] is 44 and the target is 37. Which half survives this comparison?",
        choices: ["keep the left half", "stop: found it", "keep the right half"],
      },
      i: 3,
    },
    {
      kind: "discard",
      title: "half the range removed",
      narration: "arr[8] = 44 > 37, so index 8 and everything right of it is out. The range goes from 6 places to 2.",
      state: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        lo: 6,
        hi: 7,
        mid: null,
        target: 37,
        compared: [5, 8],
        dropped: [0, 1, 2, 3, 4, 5, 8, 9, 10, 11],
        found: null,
        live: 2,
      },
      highlights: ["dropped"],
      cause: "arr[8] = 44 > 37, so index 8 and everything right of it is out",
      prompt: {
        type: "next_probe_index",
        question: "With that half gone, which index gets looked at next?",
        choices: ["index 6", "index 8", "index 7"],
      },
      i: 4,
    },
    {
      kind: "probe",
      title: "arr[6] = 37",
      narration: "The middle of the live range is index 6, holding 37.",
      state: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        lo: 6,
        hi: 7,
        mid: 6,
        target: 37,
        compared: [5, 8, 6],
        dropped: [0, 1, 2, 3, 4, 5, 8, 9, 10, 11],
        found: null,
        live: 2,
      },
      highlights: ["mid"],
      cause: "arr[6] == 37",
      prompt: {
        type: "next_branch",
        question: "arr[6] is 37. What happens now?",
        choices: ["keep the left half", "stop: found it", "keep the right half"],
      },
      i: 5,
    },
    {
      kind: "found",
      title: "Found at index 6",
      narration: "37 is at index 6. Every comparison in this search removed half of what was left, which is why 12 sorted values take 3 comparisons instead of up to 12 looks.",
      state: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        lo: 6,
        hi: 7,
        mid: 6,
        target: 37,
        compared: [5, 8, 6],
        dropped: [0, 1, 2, 3, 4, 5, 8, 9, 10, 11],
        found: 6,
        live: 2,
      },
      highlights: ["found"],
      cause: "arr[6] == 37",
      prompt: null,
      i: 6,
    },
    {
      kind: "summary",
      title: "3 comparisons for 12 values",
      narration: "log2(12) is about 3.58, and this search used 3 comparisons. Halving is why sorted data is worth keeping.",
      state: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        lo: 6,
        hi: 7,
        mid: null,
        target: 37,
        compared: [5, 8, 6],
        dropped: [0, 1, 2, 3, 4, 5, 8, 9, 10, 11],
        found: 6,
        live: 2,
        steps: 3,
        log2: 3.585,
      },
      highlights: [],
      cause: null,
      prompt: null,
      i: 7,
    },
  ];

const lessonScene = {
    id: "scene_b8ef64f6c7054a18ad20",
    spec_id: "scn_879e37655218ab4dc7c3",
    kind: "binary_search",
    title: "Binary search: one look at the middle, half the list gone",
    origin: "builtin",
    intent: "predict which half survives each comparison, and say why sorted order makes that safe",
    framing: {
      intro: "Pause any time and change the target: the search recomputes, and you can watch whether the number of comparisons moves.",
      outro: "If you can say, before the animation does, which half gets thrown away — you have the idea.",
    },
    concepts: [
      "sorted order as a precondition",
      "loop invariant on the range",
      "logarithmic steps",
    ],
    citations: [
      {
        ref: "CLRS 2.3.1",
        claim: "binary search on a sorted array runs in Theta(lg n) comparisons.",
      },
    ],
    params: {
      values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
      target: 37,
      seed: 7,
    },
    frame_count: 8,
    prediction_count: 6,
    has_renderer: true,
    last_run: null,
  };

/** A scene this build has no renderer for, so the fallback is part of the tour.
 *  The shape is the recorded one; the `kind` is invented, because no built-in kind is unrenderable. */
/** A scene of a kind this build cannot draw: the degenerate case the audit's no-renderer review
 *  exists to keep honest. Its `id` and `spec_id` are its own -- sharing a neighbour's would put
 *  two rows under one React key and make "open" ambiguous. */
const lessonUnrendered = {
    id: "scene_5c0afe9d1b7746f2a4c9",
    spec_id: "scn_4f2b9c6170ad4d3e8b15",
    kind: "monty_hall",
    title: "Monty Hall: the door you did not pick (no renderer in this build)",
    origin: "builtin",
    intent: "predict which half survives each comparison, and say why sorted order makes that safe",
    framing: {
      intro: "Pause any time and change the target: the search recomputes, and you can watch whether the number of comparisons moves.",
      outro: "If you can say, before the animation does, which half gets thrown away — you have the idea.",
    },
    concepts: [
      "sorted order as a precondition",
      "loop invariant on the range",
      "logarithmic steps",
    ],
    citations: [
      {
        ref: "CLRS 2.3.1",
        claim: "binary search on a sorted array runs in Theta(lg n) comparisons.",
      },
    ],
    params: {
      world: "two doors, one prize",
    },
    frame_count: 6,
    prediction_count: 3,
    has_renderer: false,
    last_run: null,
  };

const lessonList = {
  scenes: [lessonScene, lessonUnrendered],
  kinds: {
    binary_search: {
      title: "Binary search on a sorted list",
      summary: "Watch the search range halve at every comparison, and see why sorted order is what makes one look at the middle worth two looks at the ends.",
      params: {
        values: {
          type: "ints",
          min_items: 3,
          max_items: 24,
          min: 0,
          max: 9999,
          sorted: true,
          unique: true,
          default: null,
          label: "The list",
          help: "sorted, no repeats, 3 to 24 numbers. Left empty, the lesson generates one from the seed.",
        },
        n: {
          type: "int",
          min: 3,
          max: 24,
          default: 12,
          label: "List size",
          help: "how many values to search through",
        },
        target: {
          type: "int",
          min: 0,
          max: 9999,
          default: null,
          label: "Target",
          help: "the value to look for. Any number works, including one that is not in the list.",
        },
        seed: {
          type: "int",
          min: 1,
          max: 10000000,
          default: 7,
          label: "Seed",
          help: "same seed, same list",
          ui: "hidden",
        },
      },
      max_frames: 200,
      prompts: ["comparison_count", "next_branch", "next_probe_index"],
      answers_why: true,
    },
    cpu_cycle: {
      title: "The CPU instruction cycle: fetch, decode, execute",
      summary: "Step a program through a one accumulator machine and watch PC, MAR, MDR and IR take their turn in every instruction — one memory for code and data, and no way to stop but HLT.",
      params: {
        program: {
          type: "lines",
          min_items: 1,
          max_items: 8,
          default: null,
          label: "Program",
          help: "one instruction per line, up to 8: LOAD, STORE, ADD, SUB, JMP, JZ, HLT",
        },
        data: {
          type: "ints",
          min_items: 1,
          max_items: 32,
          min: -999,
          max: 999,
          default: null,
          label: "Data cells",
          help: "the numbers the program reads and writes, in cells right after the program",
        },
        max_steps: {
          type: "int",
          min: 1,
          max: 40,
          default: 12,
          label: "Stop after this many instructions",
          help: "a loop that never ends is cut off, and says so",
        },
      },
      max_frames: 240,
      prompts: ["next_register", "accumulator_after", "memory_after", "pc_after"],
      answers_why: true,
    },
    logic_gates: {
      title: "Logic gates: inputs, rule, output",
      summary: "Drive the inputs of one gate and watch the rule decide the output, with the whole truth table beside it so you can predict the next row before it is shown.",
      params: {
        gate: {
          type: "choice",
          values: ["AND", "BUFFER", "NAND", "NOR", "NOT", "OR", "XNOR", "XOR"],
          default: "NAND",
          label: "Gate",
          help: "which gate to drive",
        },
        walk: {
          type: "bool",
          default: true,
          label: "Walk the whole truth table",
          help: "off: only the inputs you set. on: every row, in counting order.",
        },
        a: {
          type: "bool",
          default: 1,
          label: "Input A",
          help: "0 or 1",
        },
        b: {
          type: "bool",
          default: 0,
          label: "Input B",
          help: "ignored by a single input gate",
        },
      },
      max_frames: 60,
      prompts: ["output", "next_row"],
      answers_why: true,
    },
    sorting: {
      title: "Insertion sort: build a sorted prefix one card at a time",
      summary: "Watch each value get walked back into the sorted part of the list by comparison, and count how many comparisons and shifts the whole sort really costs.",
      params: {
        values: {
          type: "ints",
          min_items: 2,
          max_items: 20,
          min: 0,
          max: 999,
          default: null,
          label: "The list",
          help: "any order, repeats allowed — 2 to 20 numbers",
        },
        n: {
          type: "int",
          min: 2,
          max: 20,
          default: 8,
          label: "List size",
          help: "how many values to sort",
        },
        seed: {
          type: "int",
          min: 1,
          max: 10000000,
          default: 11,
          label: "Seed",
          help: "same seed, same list",
          ui: "hidden",
        },
      },
      max_frames: 400,
      prompts: ["where_it_lands"],
      answers_why: true,
    },
  },
  counts: {
    scenes: 2,
    engine_kinds: 4,
  },
  note: "every scene here was validated before it was stored: the frames are computed by the backend simulation, and a scene carries nothing to execute",
};

const lessonOpen = {
  ...lessonScene,
  id: undefined,
  run_id: "lesson_mock_run_0001",
  scene_id: lessonScene.id,
  frames: lessonFrames,
  frame_index: 0,
  resumed: false,
  renderer: { available: true, note: "rendered frame by frame from the simulation's trace" },
  ok: true,
};

const lessonProgress = {
    ok: true,
    frame_index: 2,
    furthest_frame: 2,
    frames_viewed: 1,
    frame_count: 8,
    at_end: false,
  };
const lessonPredict = {
    ok: true,
    correct: true,
    expected: "index 8",
    said: "index 8",
    why: "",
    frame: 2,
    prompt: {
      type: "next_probe_index",
      question: "With that half gone, which index gets looked at next?",
      choices: ["index 8", "index 5", "index 9"],
    },
    totals: {
      predictions: 1,
      correct: 1,
    },
  };
const lessonExplain = {
    ok: true,
    asked_for_extra_help: false,
    text: "arr[5] = 35 < 37, so index 5 and everything left of it is out. Sorted order is what lets us be sure: if the middle value is on one side of the target, everything on the other side of the middle is on the wrong side too, so it cannot be hiding.",
    note: "",
    source: "deterministic",
    frame: 2,
    cached: true,
    needs_extra_help: false,
    question: "why does sorted order matter here?",
    model: null,
    tokens_in: 0,
    tokens_out: 0,
    citations: [
      {
        ref: "CLRS 2.3.1",
        claim: "binary search on a sorted array runs in Theta(lg n) comparisons.",
      },
    ],
  };
const lessonVariables = {
  ...lessonOpen,
  run_id: "lesson_mock_run_0002",
  scene_id: "scene_mock_derived",
  spec_id: "scn_mock_derived",
  title: "Binary search: one look at the middle, half the list gone (your variables)",
  derived_from: lessonScene.spec_id,
  warnings: [],
  changed: ["params"],
  params: {
    values: [1, 4, 7, 9, 12, 15, 19, 23],
    target: 12,
    seed: 7,
  },
};
const lessonFinish = {
    ok: true,
    already: true,
    run_id: "lesson_54bdd28c77ce498293d9",
    status: "stopped",
    review: {
      run_id: "lesson_54bdd28c77ce498293d9",
      scene: {
        id: "scene_b8ef64f6c7054a18ad20",
        spec_id: "scn_879e37655218ab4dc7c3",
        kind: "binary_search",
        title: "Binary search: one look at the middle, half the list gone",
        origin: "builtin",
        intent: "predict which half survives each comparison, and say why sorted order makes that safe",
        framing: {
          intro: "Pause any time and change the target: the search recomputes, and you can watch whether the number of comparisons moves.",
          outro: "If you can say, before the animation does, which half gets thrown away — you have the idea.",
        },
        concepts: [
          "sorted order as a precondition",
          "loop invariant on the range",
          "logarithmic steps",
        ],
        citations: [
          {
            ref: "CLRS 2.3.1",
            claim: "binary search on a sorted array runs in Theta(lg n) comparisons.",
          },
        ],
        params: {
          values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
          target: 37,
          seed: 7,
        },
        frame_count: 8,
        prediction_count: 6,
        has_renderer: true,
        last_run: {
          run_id: "lesson_54bdd28c77ce498293d9",
          status: "stopped",
          frame_index: 2,
          furthest_frame: 2,
          frames_viewed: 1,
          predictions: 1,
          correct: 1,
          param_changes: 0,
          seconds_watched: 0,
          last_seen_at: "2026-10-12T09:26:03.012474",
        },
      },
      status: "stopped",
      frame_index: 2,
      furthest_frame: 2,
      frames_viewed: 1,
      frame_count: 8,
      predictions: [
        {
          frame: 2,
          prompt: "next_probe_index",
          said: "index 8",
          expected: "index 8",
          correct: true,
          at: "2026-10-12T09:26:02",
          question: "With that half gone, which index gets looked at next?",
          choices: ["index 8", "index 5", "index 9"],
        },
      ],
      totals: {
        predictions: 1,
        correct: 1,
        accuracy: 1.0,
        chance: 0.3333333333333333,
        replays: 0,
        pauses: 0,
        param_changes: 0,
        seconds_watched: 0,
      },
      skipped_predictions: [0, 1, 3, 4, 5],
    },
  };
const lessonReview = {
    ok: true,
    run_id: "lesson_54bdd28c77ce498293d9",
    scene: {
      id: "scene_b8ef64f6c7054a18ad20",
      spec_id: "scn_879e37655218ab4dc7c3",
      kind: "binary_search",
      title: "Binary search: one look at the middle, half the list gone",
      origin: "builtin",
      intent: "predict which half survives each comparison, and say why sorted order makes that safe",
      framing: {
        intro: "Pause any time and change the target: the search recomputes, and you can watch whether the number of comparisons moves.",
        outro: "If you can say, before the animation does, which half gets thrown away — you have the idea.",
      },
      concepts: [
        "sorted order as a precondition",
        "loop invariant on the range",
        "logarithmic steps",
      ],
      citations: [
        {
          ref: "CLRS 2.3.1",
          claim: "binary search on a sorted array runs in Theta(lg n) comparisons.",
        },
      ],
      params: {
        values: [2, 4, 13, 20, 27, 35, 37, 41, 44, 52, 55, 57],
        target: 37,
        seed: 7,
      },
      frame_count: 8,
      prediction_count: 6,
      has_renderer: true,
      last_run: {
        run_id: "lesson_54bdd28c77ce498293d9",
        status: "stopped",
        frame_index: 2,
        furthest_frame: 2,
        frames_viewed: 1,
        predictions: 1,
        correct: 1,
        param_changes: 0,
        seconds_watched: 0,
        last_seen_at: "2026-10-12T09:26:03.012474",
      },
    },
    status: "stopped",
    frame_index: 2,
    furthest_frame: 2,
    frames_viewed: 1,
    frame_count: 8,
    predictions: [
      {
        frame: 2,
        prompt: "next_probe_index",
        said: "index 8",
        expected: "index 8",
        correct: true,
        at: "2026-10-12T09:26:02",
        question: "With that half gone, which index gets looked at next?",
        choices: ["index 8", "index 5", "index 9"],
      },
    ],
    totals: {
      predictions: 1,
      correct: 1,
      accuracy: 1.0,
      chance: 0.3333333333333333,
      replays: 0,
      pauses: 0,
      param_changes: 0,
      seconds_watched: 0,
    },
    skipped_predictions: [0, 1, 3, 4, 5],
  };
const lessonKinds = {
    kinds: {
      binary_search: {
        title: "Binary search on a sorted list",
        summary: "Watch the search range halve at every comparison, and see why sorted order is what makes one look at the middle worth two looks at the ends.",
        params: {
          values: {
            type: "ints",
            min_items: 3,
            max_items: 24,
            min: 0,
            max: 9999,
            sorted: true,
            unique: true,
            default: null,
            label: "The list",
            help: "sorted, no repeats, 3 to 24 numbers. Left empty, the lesson generates one from the seed.",
          },
          n: {
            type: "int",
            min: 3,
            max: 24,
            default: 12,
            label: "List size",
            help: "how many values to search through",
          },
          target: {
            type: "int",
            min: 0,
            max: 9999,
            default: null,
            label: "Target",
            help: "the value to look for. Any number works, including one that is not in the list.",
          },
          seed: {
            type: "int",
            min: 1,
            max: 10000000,
            default: 7,
            label: "Seed",
            help: "same seed, same list",
            ui: "hidden",
          },
        },
        max_frames: 200,
        prompts: ["comparison_count", "next_branch", "next_probe_index"],
        answers_why: true,
      },
      cpu_cycle: {
        title: "The CPU instruction cycle: fetch, decode, execute",
        summary: "Step a program through a one accumulator machine and watch PC, MAR, MDR and IR take their turn in every instruction — one memory for code and data, and no way to stop but HLT.",
        params: {
          program: {
            type: "lines",
            min_items: 1,
            max_items: 8,
            default: null,
            label: "Program",
            help: "one instruction per line, up to 8: LOAD, STORE, ADD, SUB, JMP, JZ, HLT",
          },
          data: {
            type: "ints",
            min_items: 1,
            max_items: 32,
            min: -999,
            max: 999,
            default: null,
            label: "Data cells",
            help: "the numbers the program reads and writes, in cells right after the program",
          },
          max_steps: {
            type: "int",
            min: 1,
            max: 40,
            default: 12,
            label: "Stop after this many instructions",
            help: "a loop that never ends is cut off, and says so",
          },
        },
        max_frames: 240,
        prompts: ["next_register", "accumulator_after", "memory_after", "pc_after"],
        answers_why: true,
      },
      logic_gates: {
        title: "Logic gates: inputs, rule, output",
        summary: "Drive the inputs of one gate and watch the rule decide the output, with the whole truth table beside it so you can predict the next row before it is shown.",
        params: {
          gate: {
            type: "choice",
            values: ["AND", "BUFFER", "NAND", "NOR", "NOT", "OR", "XNOR", "XOR"],
            default: "NAND",
            label: "Gate",
            help: "which gate to drive",
          },
          walk: {
            type: "bool",
            default: true,
            label: "Walk the whole truth table",
            help: "off: only the inputs you set. on: every row, in counting order.",
          },
          a: {
            type: "bool",
            default: 1,
            label: "Input A",
            help: "0 or 1",
          },
          b: {
            type: "bool",
            default: 0,
            label: "Input B",
            help: "ignored by a single input gate",
          },
        },
        max_frames: 60,
        prompts: ["output", "next_row"],
        answers_why: true,
      },
      sorting: {
        title: "Insertion sort: build a sorted prefix one card at a time",
        summary: "Watch each value get walked back into the sorted part of the list by comparison, and count how many comparisons and shifts the whole sort really costs.",
        params: {
          values: {
            type: "ints",
            min_items: 2,
            max_items: 20,
            min: 0,
            max: 999,
            default: null,
            label: "The list",
            help: "any order, repeats allowed — 2 to 20 numbers",
          },
          n: {
            type: "int",
            min: 2,
            max: 20,
            default: 8,
            label: "List size",
            help: "how many values to sort",
          },
          seed: {
            type: "int",
            min: 1,
            max: 10000000,
            default: 11,
            label: "Seed",
            help: "same seed, same list",
            ui: "hidden",
          },
        },
        max_frames: 400,
        prompts: ["where_it_lands"],
        answers_why: true,
      },
    },
    limits: {
      title: 120,
      intent: 400,
      framing: 600,
      concepts: 6,
      citations: 6,
      narration_chars_per_frame: 320,
    },
  };
const lessonEvidence = {
    student_id: "stu_218239d8a2be4f5789d8",
    summary: {
      runs: 2,
      finished: 0,
      predictions: 1,
      correct: 1,
      accuracy: 1.0,
      chance: 0.3333333333333333,
      by_kind: {
        binary_search: {
          runs: 2,
          finished: 0,
          predictions: 1,
          correct: 1,
          frames: 1,
        },
      },
      note: "accuracy here is measured on frames before the simulation shows what happens next. It is not a mastery number and is not fed into one.",
    },
    narration_cache: {
      entries: 2,
      hits: 1,
      by_source: {
        deterministic: 2,
      },
      model_calls_saved: 1,
      ttl_days: 30,
    },
    not_evidence: [
      "prediction accuracy is not fed into BKT or into a mastery number",
      "frames watched is attention proxy at best; it is not reported as understanding",
    ],
  };
const lessonValidate = {
  ok: false,
  errors: ["params.target: 42 is not in the list, and that is allowed — but say so in the intent"],
  warnings: ["title is 96 characters; 120 is the limit"],
  spec_id: null,
  note: "the validator reports; it does not store. a learner editing a scene sees these reasons verbatim.",
};
const lessonAuthor = {
  ok: true,
  source: "builtin",
  note: "no model is configured, so this is the closest scene already in the app for the request, not a new one",
  draft: lessonScene,
  errors: [],
  warnings: [],
  stored: null,
  already_existed: false,
};


/* --------------------------------------------------------------- the baseline ----
 * `?mock=1` walks the real flow: the run advances one step per trial, so the design can be
 * reviewed screen by screen without a backend. Payloads are recorded shapes from the phase 2
 * API (see `backend/tests/test_baseline.py`), not invented ones.
 */
const baselineQuestions = [
  { key: "goal", question: "What are you learning this for?", options: ["an exam with a date", "a course I am taking", "work or a job", "just curious"], why: "decides what the app calls progress" },
  { key: "session_minutes", question: "How long would you like one sitting to be?", options: [10, 20, 30, 45], why: "sizes a lesson to a sitting you actually have" },
  { key: "study_days", question: "How many days a week can you study?", options: [1, 2, 3, 4, 5, 6, 7], why: "sets the review schedule" },
  { key: "study_when", question: "When do you usually study?", options: ["early morning", "midday", "afternoon", "evening", "late night", "it varies"], why: "times reminders, and never sends them at the wrong hour" },
  { key: "motivation", question: "What keeps you going?", options: ["seeing progress", "a streak", "competing", "curiosity", "finishing things"], multi: true, why: "picks what the progress screen shows first" },
  { key: "quit_trigger", question: "What makes you stop?", options: ["boredom", "being stuck", "no time", "feeling behind"], multi: true, why: "tells the app what to notice and work around" },
  { key: "feedback_style", question: "How do you want to hear that you got something wrong?", options: ["straight", "gentle", "just show the answer"], why: "sets the tone of every correction" },
  { key: "challenge_first", question: "Would you rather start with something hard, or something you can do?", options: ["challenge me first", "safe success first"], why: "sets the opening difficulty of a lesson" },
  { key: "when_stuck", question: "What do you do when you are stuck?", options: ["ask someone", "search for it", "keep pushing", "move on and come back"], why: "compared later with what you actually did in the locked box" },
  { key: "device_context", question: "Where and on what do you study?", options: ["phone", "laptop", "shared device", "limited data", "often offline"], multi: true, why: "decides how light the pages are and whether things must work offline" },
  { key: "language", question: "Which language should explanations be in?", options: ["English", "Kiswahili", "mixed"], why: "the language of every explanation" },
  { key: "timers_help", question: "Do you like seeing a timer, or does it stress you?", options: ["timers help", "timers stress me"], why: "shows or hides the clock; the app still measures quietly either way" },
  { key: "anything_else", question: "Anything else about how you learn? (one line, optional)", options: [], free_text: true, why: "read by you later, and shown next to your settings" },
];

const baselineStudyPairs = [
  { code: "Mebra", place: "the old well" },
  { code: "Sarn", place: "the salt market" },
  { code: "Pirel", place: "a stone tower" },
  { code: "Quilo", place: "a red roof" },
  { code: "Harn", place: "the river bend" },
  { code: "Tuvik", place: "a low hill" },
  { code: "Solva", place: "a quiet bay" },
  { code: "Neril", place: "a green bridge" },
];

const baselineSteps: { stage: string; stage_title: string; task: unknown }[] = [
  { stage: "codewords_study", stage_title: "Spy codewords: study", task: { id: "codewords_study", game: "codewords_study", phase: "show", pairs: baselineStudyPairs, study_ms: 45000, prompt: "Learn these eight pairs. You will be asked for them again later.", max_latency_ms: 75000 } },
  { stage: "codewords_cued", stage_title: "Spy codewords: straight after", task: { id: "codewords_cued_2", game: "codewords_cued", phase: "measure", prompt: "Which place went with Pirel?", cue: "Pirel", answer: "a stone tower", practice: false, max_latency_ms: 30000 } },
  { stage: "quick_a", stage_title: "Quick taps", task: { id: "quick_a_0", game: "quick", phase: "measure", prompt: "tap the bigger number", options: ["7", "3"], answer: "7", max_latency_ms: 15000, stage: "quick_a" } },
  { stage: "vault", stage_title: "Vault lights", task: { id: "vault_forward_3_1", game: "vault", phase: "measure", direction: "forward", length: 3, sequence: [6, 8, 0], answer_direction: "forward", prompt: "3 tiles. Tap them in the same order.", practice: false, max_latency_ms: 30000 } },
  { stage: "shopkeeper", stage_title: "Vault lights: shopkeeper", task: { id: "vault_shop_1", game: "shopkeeper", phase: "measure", items: ["a brass key", "a coil of rope", "a tin cup"], distractors: [ { id: "d1_0", text: "The month after March is April.", answer: true }, { id: "d1_1", text: "A week has nine days.", answer: false }, { id: "d1_2", text: "Half of 20 is 10.", answer: true } ], prompt: "Hold these three items. Answer the quick questions. Then type the items back.", practice: false, max_latency_ms: 60000 } },
  { stage: "machine", stage_title: "Crack the machine", task: { id: "machine_r1_step1", game: "machine", round: 1, phase: "measure", prompt: "Test another input, or guess the rule.", max_probes: 5, max_wrong_guesses: 2, probes_used: 0, wrong_guesses: 0, max_latency_ms: 120000 } },
  { stage: "locked_box", stage_title: "The locked box", task: { id: "locked_box", game: "locked_box", phase: "measure", prompt: "A lock opens on a sequence of numbers. The first four are below. Work out the fifth. This one is hard on purpose \u2014 you can buy a hint, try again, or walk away and nothing bad happens.", cases: [{ n: 1, value: 3 }, { n: 2, value: 8 }, { n: 3, value: 18 }, { n: 4, value: 33 }], answer: 53, hint_shop: [ { id: "hint_nudge", cost: 1, text: "Write down what changes and what does not. One of them is the key.", level: "nudge" }, { id: "hint_method", cost: 2, text: "Try the smallest case first, then the next one up, and look at the difference between them.", level: "method" }, { id: "hint_near", cost: 3, text: "The gaps are growing by five each time.", level: "near answer" } ], max_latency_ms: 120000 } },
  { stage: "trap", stage_title: "Two questions to think about", task: { id: "trap_bat_ball", game: "trap", phase: "measure", story: "A bat and a ball cost 1.10 together. The bat costs 1.00 more than the ball.", prompt: "How much does the ball cost, in the currency's units?", options: ["0.10", "0.05", "1.00", "0.55"], answer: "0.05", intuitive: "0.10", max_latency_ms: 60000 } },
  { stage: "codewords_delayed", stage_title: "Spy codewords: later", task: { id: "codewords_predict", game: "codewords_delayed", phase: "measure", action: "predict", prompt: "Before we check: how many of the eight pairs do you think you will remember, out of 8?", max_value: 8, max_latency_ms: 30000 } },
  { stage: "codewords_delayed", stage_title: "Spy codewords: later", task: { id: "codewords_free", game: "codewords_delayed", phase: "measure", action: "recall", kind: "free", prompt: "Write the pairs you remember.", max_latency_ms: 90000 } },
];

let baselineAt = 0;

function baselineView(step?: "format" | "questions" | "next"): Record<string, unknown> {
  const base = { run_id: "base_mock_0001", student_id: learner.student_id, status: "in_progress", format_pick: null, notes: undefined };
  if (step === "format" || step === undefined) {
    return { ...base, stage: "format", stage_title: "How you like to meet a new idea", kind: "format", task: null, progress: 0, note: "one question before anything else", format_options: [ { value: "diagram", label: "a diagram", hint: "a picture or graph of the idea" }, { value: "text", label: "a written explanation", hint: "a short paragraph that explains it" }, { value: "worked_example", label: "a worked example", hint: "one worked through, step by step" } ], questions: [] };
  }
  if (step === "questions") {
    return { ...base, format_pick: "diagram", stage: "questions", stage_title: "How you work", kind: "questions", task: null, progress: 0.07, note: "", format_options: [], questions: baselineQuestions };
  }
  if (baselineAt >= baselineSteps.length) {
    return { ...base, format_pick: "diagram", stage: "snapshot", stage_title: "What we noticed", kind: "snapshot", task: null, progress: 1, note: "every game has an ending", format_options: [], questions: [] };
  }
  const at = baselineSteps[baselineAt];
  baselineAt += 1;
  return { ...base, format_pick: "diagram", stage: at.stage, stage_title: at.stage_title, kind: "task", task: at.task, progress: Math.min(0.95, 0.15 + baselineAt * 0.08), note: "", format_options: [], questions: [], scored: { correct: true } };
}

const baselineSnapshotBody = {
  student_id: learner.student_id,
  run_id: "base_mock_0001",
  snapshot_id: "mind_mock_0001",
  taken_at: "2026-10-12T09:40:00",
  state: "in_progress",
  measured: 5,
  total: 8,
  factors: [
    { key: "prior_knowledge", title: "What you already know", state: "not_enough_data", n: 0, range: null, summary: "not measured yet (0 of 5 needed)", statement: "Not enough data yet \u2014 the subject round (next step) would fill this in.", actions: [], detail: { needed: 5, have: 0 } },
    { key: "working_memory", title: "How much you hold at once", state: "measured", n: 28, range: [8, 9], summary: "about 8\u20139 steps at once, 0 sequence(s) missed", statement: "You currently hold about 8 steps at once, so lessons will use smaller steps and check between them.", actions: ["keep explanation blocks to about 8 ideas before a check"], detail: { forward_span: 9, backward_span: 9, failures: 0 } },
    { key: "processing_pace", title: "How fast you work", state: "measured", n: 13, range: [0.9, 1.1], summary: "middle 0.9s per quick tap, 100% right", statement: "Most quick answers take you about a second, so the app will pace new material at that speed and not chase a fastest time.", actions: ["size checks to about this pace rather than to a clock"], detail: { median_ms: 900 } },
    { key: "attention_self_control", title: "Staying on it", state: "measured", n: 13, range: [0.8, 1], summary: "100% right early, 80% right late, 1/1 traps spotted", statement: "Your accuracy held up toward the end of the session, so blocks can stay long.", actions: ["keep blocks long"], detail: { first_third_accuracy: 1, last_third_accuracy: 0.8 } },
    { key: "long_term_memory", title: "Remembering over time", state: "measured", n: 9, range: [0.6, 0.9], summary: "100% recalled straight away", statement: "You recalled the pairs straight away, so review will be scheduled around the default spacing.", actions: ["seed your review spacing from this, with a range"], detail: { immediate: 1 } },
    { key: "reasoning_style", title: "How you work things out", state: "not_enough_data", n: 1, range: null, summary: "not measured yet (1 of 3 needed)", statement: "Not measured yet.", actions: [] },
    { key: "persistence_help_seeking", title: "Sticking with something hard", state: "not_enough_data", n: 0, range: null, summary: "not measured yet", statement: "Not measured yet.", actions: [] },
    { key: "confidence_calibration", title: "Knowing what you know", state: "measured", n: 12, range: [-0.2, 0.1], summary: "close to right on average", statement: "Your coin bets are close to your actual results, so confidence is used as a light signal.", actions: [], detail: {} },
  ],
  levers: {
    step_size: { value: "large", why: "holds about 9 steps at once (n=28)", source: "measured" },
    lead_format_prior: { value: "diagram", why: "you chose diagram for a new topic (stated, not measured)", source: "stated" },
    pace: { value: "steady", why: "median 900ms per quick tap (n=8)", source: "measured" },
    hint_policy: { value: "on request", why: "default: hints are there when asked for", source: "default" },
  },
  preferences: { preferred_format: "diagram", goal: "just curious" },
  preference_vs_performance: null,
  completion: { stages: [], complete: 6, total: 14, played: ["codewords_study", "codewords_cued", "quick_a", "vault", "shopkeeper", "machine", "locked_box", "trap"], not_measured: ["prior_knowledge", "reasoning_style", "persistence_help_seeking"] },
};

const baselineNextDayBody = {
  student_id: learner.student_id,
  run_id: "base_mock_0001",
  tasks: [{ id: "codewords_next_day_0", game: "codewords_next_day", phase: "measure", prompt: "Which place went with Mebra?", cue: "Mebra", answer: "the old well", practice: false, max_latency_ms: 30000 }],
  studied: true,
  note: "these are the pairs you learned last time, asked again with no new study",
};

/* ----------------------------------------------------- the content engine ---- */
/*
 * §7's pack and breakdown, in the recorded shapes: `variants` carries written/visual/
 * worked_example, the pair is `A`/`B` with the key stripped and the options shuffled, and
 * `rejections` carries the one part that failed its own gate. The strings are the ones the engine
 * produces for the authored calculus kit.
 *
 * Exported because `lib/content.ts` short-circuits `?mock=1` with these same fixtures -- and, for
 * grading, with the answer the learner actually picked, which a row cannot know: `mockLookup`
 * hands a row the URL, and a POST carries no URL. That is also why the grade row below answers the
 * only thing it honestly can.
 */
export const contentBreakdown = {
  topic: "calculus",
  title: "Calculus",
  available: true,
  reason: "an authored prerequisite chain exists for this topic",
  concepts: [
    {
      id: "calc_rate",
      title: "Rate of change",
      misconceptions: ["the rate is the change in f without dividing"],
      objectives: ["compute a rate from two values"],
    },
  ],
};

export const contentPack = {
  concept_id: "calc_rate",
  concept_title: "Rate of change",
  topic_key: "calculus",
  topic_title: "Calculus",
  sub_area: "calculus",
  available: true,
  reason: "built from the authored chain kit for Rate of change (4 of 5 rungs authored)",
  spec: "adapt.concept_pack.v1",
  variants: {
    written: {
      id: "pk_written",
      kind: "written",
      stem: "A rate of change says how much one quantity moves per unit of another. On a straight line it never changes, so the slope is the rate.",
      rung: 1,
      demand: "recall",
    },
    visual: {
      id: "pk_visual",
      kind: "visual",
      stem: "Two points on one curve, with the rise and the run marked between them.",
      spec: { kind: "number_line", marks: [{ at: 0, label: "0" }, { at: 1, label: "1" }, { at: 2, label: "2" }] },
      rung: 2,
      demand: "procedural",
    },
    worked_example: {
      id: "pk_worked",
      kind: "worked_example",
      stem: "Find the average rate of change of f(x) = x² between x = 1 and x = 4.",
      steps: [
        "Write what changed: f(4) - f(1) = 16 - 1 = 15.",
        "Write what it changed over: 4 - 1 = 3.",
        "Divide: 15 / 3 = 5.",
        "So the average rate of change is 5 units of f per unit of x.",
      ],
      verified: true,
      flags: [],
      rung: 2,
      demand: "procedural",
      why: "the numerator is the change in f and the denominator is the change in x; swapping them is the usual slip",
    },
  },
  pair: {
    format: "choice",
    matched:
      "matched on concept and on question format; NOT matched on difficulty, because rung 3 and rung 4 are different demands by design. Compare the legs on expected performance",
    expected_accuracy_note:
      "compare the legs with subjects.methodtest.expected_accuracy(p_know, difficulty) — the same expected-performance rule the method test uses",
    A: {
      id: "pk_A",
      kind: "choice",
      stem: "What is the average rate of change of f(x) = x² between x = 1 and x = 3?",
      options: ["8", "9", "4", "5"],
      rung: 3,
      demand: "procedural",
    },
    B: {
      id: "pk_B",
      kind: "choice",
      stem: "A car covers 120 km in 2 hours. What is its average speed?",
      options: ["60 km/h", "240 km/h", "30 km/h", "118 km/h"],
      rung: 4,
      demand: "transfer",
    },
  },
  hints: [
    { rung: 1, text: "Write the change in f first, then the change in x.", source: "the authored rung-1 near-miss" },
    { rung: 2, text: "Divide the two changes, and keep the units in the answer.", source: "the authored rung-2 task" },
    { rung: 3, text: "f(3) is 9 and f(1) is 1, so the change is 8 over 2.", source: "the authored rung-3 task" },
  ],
  checklist: [
    { point: "the change in f is in the numerator", rung: 1 },
    { point: "the change in x is in the denominator", rung: 2 },
    { point: "the units are carried into the answer", rung: 3 },
  ],
  misconceptions: [
    {
      claim: "the rate is f(3) - f(1), without dividing",
      contrast: "a change in f over no change in x is not a rate",
      source: "the authored rung-1 near-miss",
    },
  ],
  objectives: [
    { rung: 1, key: "recall", demand: "recall", task_stem: "Say what a rate of change measures." },
    { rung: 2, key: "procedural", demand: "procedural", task_stem: "Compute a rate from two values." },
    { rung: 3, key: "procedural", demand: "procedural", task_stem: "Average rate of change of a curve." },
    { rung: 4, key: "transfer", demand: "transfer", task_stem: "A rate in a context with units." },
  ],
  notes: ["the worked example passed its own gate"],
  rejections: [],
};

const rows: Row[] = [
  /* Phase 5's contrast screen renders two plans side by side. The fixtures keep the same shape the
     adapter returns: ordered blocks each carrying a chunk_id and a why with its n, a why-panel, and
     provenance. The two profiles must differ in a way the page can *read off* the plans. */
  { method: "POST", match: /^\/api\/v1\/lesson-plan\/contrast$/, body: {
    concept: "adding fractions",
    title: "Adding fractions",
    visibly_different: true,
    note: "both lessons teach the same checked content; what differs is how it is laid out, and every difference above is read off the two plans, not written about them",
    differences: [
      { aspect: "opens with", a: "visual", b: "worked_example" },
      { aspect: "step size", a: "small", b: "large" },
      { aspect: "number of blocks", a: 4, b: 3 },
    ],
    lessons: [
      { profile: "Smaller steps, pictures first", plan: {
        concept: "adding fractions", title: "Adding fractions", lead_format: "visual", ladder_rung: 2,
        limits: { step_size: "small", max_words_per_block: 45, tone: "warm", language: "en" },
        subject: { p_know: 0.42, ci: [0.3, 0.55], n: 9, band: "developing" },
        fallbacks: [], mode: "deterministic",
        blocks: [
          { block: 1, chunk_id: "blk_mock_v1", kind: "visual", content: { spec: { kind: "number_line", marks: [{ at: 0, label: "0" }, { at: 0.5, label: "1/2" }, { at: 0.75, label: "3/4" }] } }, why: { text: "the profile asks for the structure first", evidence_ids: ["ev_mock_1"], n: 4 } },
          { block: 2, chunk_id: "blk_mock_t1", kind: "text", content: { text: "A fraction only adds to another when the parts are the same size. Draw both on one line before you add anything." }, why: { text: "one sentence of teaching, then a check", evidence_ids: ["ev_mock_1"], n: 4 } },
          { block: 3, chunk_id: "blk_mock_c1", kind: "check", content: { question: "On the line above, which two marks are closest together?", hint: "compare the gaps, not the labels" }, why: { text: "a check after every explanation chunk", evidence_ids: ["ev_mock_2"], n: 3 } },
          { block: 4, chunk_id: "blk_mock_x1", kind: "explain", content: { question: "Say in your own words why you cannot add fifths to thirds directly." }, why: { text: "explaining back is the check that sticks", evidence_ids: ["ev_mock_2"], n: 3 } },
        ],
        why_panel: { title: "Why this lesson looks like this", rows: [{ decision: "pictures first", text: "the profile asks for structure before symbols", evidence_ids: ["ev_mock_1"], n: 4 }], what_would_change_it: ["more answers under a different teaching method, measured against expectation, would change what leads"], fallbacks: [], mode: "deterministic", mode_note: "built by the template adapter from the measured profile (simple mode: no model was involved)" },
        provenance: { model_version: 4, content_id: "cpk_mock_fractions", content_source: "authored_bank", evidence_ids: ["ev_mock_1", "ev_mock_2"], evidence_n: 2 },
      } },
      { profile: "Bigger steps, examples first", plan: {
        concept: "adding fractions", title: "Adding fractions", lead_format: "worked_example", ladder_rung: 3,
        limits: { step_size: "large", max_words_per_block: 90, tone: "direct", language: "en" },
        subject: { p_know: 0.42, ci: [0.3, 0.55], n: 9, band: "developing" },
        fallbacks: [], mode: "deterministic",
        blocks: [
          { block: 1, chunk_id: "blk_mock_w1", kind: "worked_example", content: { steps: [{ text: "Rewrite 1/2 and 1/3 with a common denominator of 6.", why: "sixths measure both halves and thirds exactly" }, { text: "1/2 becomes 3/6 and 1/3 becomes 2/6.", why: "multiply top and bottom by the same number" }, { text: "3/6 + 2/6 = 5/6.", why: "same-sized parts can be added" }] }, why: { text: "the profile asks for a full example first", evidence_ids: ["ev_mock_3"], n: 5 } },
          { block: 2, chunk_id: "blk_mock_c2", kind: "check", content: { question: "Why does 1/2 turn into 3/6 and not 3/5?", hint: "what happens to the bottom when you multiply the top" }, why: { text: "a check after every explanation chunk", evidence_ids: ["ev_mock_2"], n: 3 } },
          { block: 3, chunk_id: "blk_mock_x2", kind: "explain", content: { question: "Explain the common-denominator step to someone who has not seen it." }, why: { text: "explaining back is the check that sticks", evidence_ids: ["ev_mock_2"], n: 3 } },
        ],
        why_panel: { title: "Why this lesson looks like this", rows: [{ decision: "a full example first", text: "the profile asks for a worked run-through", evidence_ids: ["ev_mock_3"], n: 5 }], what_would_change_it: ["a working-memory measure would resize the steps (they are at most 90 words now, from the large step size)"], fallbacks: [], mode: "deterministic", mode_note: "built by the template adapter from the measured profile (simple mode: no model was involved)" },
        provenance: { model_version: 4, content_id: "cpk_mock_fractions", content_source: "authored_bank", evidence_ids: ["ev_mock_2", "ev_mock_3"], evidence_n: 2 },
      } },
    ],
  } },
  { method: "GET", match: /^\/api\/v1\/review\/check/, body: { topic_id: "top_mock", title: "what a fraction means", question: "Last time you worked on what a fraction means. Without looking anything up: which number names the parts you took?", rationale: "Forgetting curve: projected recall is about 62% after 3.0 days. Checking here tests the memory at its most informative point.", lag_hours: 72, expected_recall: 0.62, grounded_item_id: null, source: "rules", notes: "what a good answer covers arrives with with_notes=1, after you have answered", expected_answer_notes: "a denominator (the bottom number) names how many equal parts the whole is cut into" } },
  { method: "GET", match: /^\/api\/v1\/evidence$/, body: {
    methods: [], strategies: [], preference_vs_performance: null,
    excluded_n: 0, attributed_elsewhere_n: 0, format_comparable: true,
    note: "Demo mode has no measured method outcomes. Insufficient evidence; no comparison was invented.",
  } },
  { method: "GET", match: /^\/api\/v1\/evidence\/failure\//,
    body: null, status: 422, error: { message: "Recovery requires a real graded attempt and checked matched content; unavailable in demo mode." } },
  { method: "POST", match: /^\/api\/v1\/evidence\/failure/,
    body: null, status: 422, error: { message: "Recovery requires a real graded attempt and checked matched content; unavailable in demo mode." } },
  /* the baseline first: `/baseline/snapshot` and `/baseline/next-day` must be matched before the
     run-id row, or a GET of the snapshot would be read as a run view */
  { method: "POST", match: /^\/api\/v1\/baseline$/, body: () => baselineView("format") },
  { method: "GET", match: /^\/api\/v1\/baseline\/snapshot/, body: baselineSnapshotBody },
  { method: "GET", match: /^\/api\/v1\/baseline\/next-day/, body: baselineNextDayBody },
  { method: "GET", match: /^\/api\/v1\/baseline\/[^/]+$/, body: () => baselineView("next") },
  { method: "POST", match: /^\/api\/v1\/baseline\/[^/]+\/format$/, body: () => baselineView("questions") },
  { method: "POST", match: /^\/api\/v1\/baseline\/[^/]+\/questions$/, body: () => baselineView("next") },
  { method: "POST", match: /^\/api\/v1\/baseline\/[^/]+\/trial$/, body: () => baselineView("next") },
  { method: "POST", match: /^\/api\/v1\/baseline\/[^/]+\/finish$/, body: baselineSnapshotBody },
  { method: "GET", match: /^\/api\/v1\/health/, body: { ok: true, mode: "local", learners: 1, mock: true } },
  { method: "GET", match: /^\/api\/v1\/me$/, body: learner },
  { method: "POST", match: /^\/api\/v1\/students/, body: { ...learner, csrf_token: "mock-csrf-token", expires_at: "2026-10-12T03:00:00", created: true, onboarding_complete: false } },
  { method: "POST", match: /^\/api\/v1\/login/, body: { ...learner, csrf_token: "mock-csrf-token", created: false } },
  { method: "POST", match: /^\/api\/v1\/logout/, body: { ok: true } },
  { method: "POST", match: /^\/api\/v1\/me\/onboarding/, body: { student_id: learner.student_id, preferences: { goal: "GCSE maths", weekly_minutes: 150 }, ignored_keys: [], rejected_reasons: {}, consent: { analytics: true, share_summary_with_ai: true }, note: "No learning styles question was asked. 'How you learn best' is measured from what happens during practice, and any lasting change needs your approval." } },
  { method: "GET", match: /^\/api\/v1\/me\/preferences/, body: { preferences: { goal: "GCSE maths", weekly_minutes: 150, max_explanation_words: 90 }, allowed_keys: ["avoid", "encourage", "goal", "language", "max_explanation_words", "push_me", "quiet_hours", "session_minutes", "want_review_reminders", "want_voice", "weekly_minutes"] } },
  { method: "POST", match: /^\/api\/v1\/me\/preferences/, body: { accepted: { goal: "GCSE maths" }, rejected_keys: [], rejected_reasons: {} } },
  { method: "DELETE", match: /^\/api\/v1\/me$/, body: { deleted: true, hard_purge_available_via: "adapt db purge-student --student-id <id> --confirm", note: "learning history is removed from every query path immediately" } },
  { method: "GET", match: /^\/api\/v1\/assessment\/diagnostic/, body: { purpose: "place you on each topic with a confidence band, not to grade you", items: [planEntry, { ...planEntry, position: 2 }], how_it_is_used: "Answers, timings, hint use and answer changes are recorded as events.", note_on_time: "If you need longer, say so - time spent is evidence about the interaction, not a score." } },
  { method: "POST", match: /^\/api\/v1\/sessions$/, body: { session_id: "lrs_mock_0001", mode: "practice", started_at: "2026-10-11T09:00:00", event_types_accepted: ["question_shown", "first_interaction", "hint_opened", "answer_submitted"] } },
  { method: "POST", match: /^\/api\/v1\/sessions\/[^/]+\/finish/, body: { ok: true, session_id: "lrs_mock_0001", duration_seconds: 640, active_ms: 592000, mode: "practice", next: "run the analysis to update mastery, hypotheses and any proposed adaptation" } },
  { method: "POST", match: /^\/api\/v1\/sessions\/[^/]+\/events/, body: { accepted: 4, rejected: 0, duplicate: 0, dropped_fields: 0, reasons: [], session_id: "lrs_mock_0001", event_types: ["question_shown", "first_interaction", "hint_opened", "answer_submitted"] } },
  { method: "GET", match: /^\/api\/v1\/sessions\/[^/]+\/events/, body: { events: [{ seq: 1, ts: "2026-10-11T09:00:04", type: "question_shown", item_id: item.id, payload: { stem_words: 9 } }] } },
  { method: "POST", match: /^\/api\/v1\/attempts/, body: receipt },
  { method: "GET", match: /^\/api\/v1\/practice/, body: { items: [planEntry], why: "two topics are due for a look, and one has a gap underneath it" } },
  {
    method: "GET",
    match: /^\/api\/v1\/methods\/report/,
    body: {
      methods: [
        { method: "worked_example", label: "Worked example first", n: 7, actual: 0.71, expected: 0.62, residual: 0.09, evidence: "early_signal", note: "early signal: too few observations to call this a finding; treat it as a hint" },
        { method: "explanation", label: "Explanation first", n: 7, actual: 0.43, expected: 0.6, residual: -0.17, evidence: "early_signal", note: "early signal: too few observations to call this a finding; treat it as a hint" },
      ],
      n_scored: 14,
      min_n_for_a_finding: 8,
      labels: { unit: "actual minus expected success rate", expected: "the model's own prediction for the same items" },
      note: "each method is judged on actual minus expected success, so a method that met harder material is not punished for it; under 8 observations is labelled an early signal",
    },
  },
  { method: "POST", match: /^\/api\/v1\/items\/[^/]+\/hint/, body: { hint: "Write the two fractions over the same denominator before you add anything.", hint_no: 1, max_hints: 2, level: "developing", hints_available: 2, exhausted: false, item_id: item.id } },
  { method: "GET", match: /^\/api\/v1\/items\/[^/]+\/worked-example/, body: { steps: [{ index: 1, text: "Make the denominators equal: 2 and 3 both divide 6.", why: "a fraction only names parts of one whole" }, { index: 2, text: "Scale both numerators: 1/2 = 3/6, 1/3 = 2/6.", why: "scaling both sides keeps the value" }], item_id: item.id, note: "worked examples are faded on purpose: we add steps back when you ask, and remove them when you no longer need them" } },
  { method: "POST", match: /^\/api\/v1\/items\/[^/]+\/worked-example/, body: { steps: [{ index: 1, text: "Make the denominators equal: 2 and 3 both divide 6.", why: "a fraction only names parts of one whole" }], item_id: item.id, note: "opening this is recorded, because the fading decision is measured on it" } },
  { method: "GET", match: /^\/api\/v1\/items\/[^/]+\/reveal/, body: { steps: [{ index: 1, text: "6/12 is not the way; use 3/6 + 2/6." }], answer: null, correct_index: 1, choices: [{ text: "2/5", correct: false, why_wrong: "that adds tops and bottoms separately" }, { text: "5/6", correct: true, why_wrong: null }, { text: "1/5", correct: false, why_wrong: "that is 1/2 - 3/5" }, { text: "5/5", correct: false, why_wrong: "one whole - the pieces are smaller than that" }], why_it_is_ok_to_look: "you asked; nothing here is a persistent change" } },
  { method: "POST", match: /^\/api\/v1\/items\/[^/]+\/teachback/, body: { score: 0.67, coverage: 0.67, method: "checklist_3_point", matched: ["common denominator", "equal parts"], missing: ["numerator scales too"], followup: "Which part of the fraction changes when you scale it up?", words: 38, note: "scored on ideas covered, not fluency or spelling", feedback: "You are right about the common denominator. Still to show: numerator scales too - say the sentence that shows it and you have it.", misconceptions: [], feedback_source: "rules" } },
  { method: "POST", match: /^\/api\/v1\/explain/, body: { answer: "You can only add or subtract like units, so the parts have to be the same size first. Two fractions are equivalent when they name the same point on the number line.", served_by: "your own material", sections: [{ label: "explanation", text: "You can only add or subtract like units, so the parts have to be the same size first." }], next_action: "Try 1/2 + 1/4 and say out loud which denominator you chose.", adaptations_applied: ["segmented_text"], sources: [{ kind: "material", ref: "mat_mock:0", title: "Fractions - adding", material_id: "mat_mock" }], teachback_prompt: null, resource_hint: null, level: "developing", message_id: "msg_mock_0001", intent: null, meta: { grounded: true, served_by: "your own material" }, privacy: { sent_to_model: false, redacted_fields: 0, what_was_sent: "nothing left this machine" } } },
  { method: "POST", match: /^\/api\/v1\/analyze/, body: { student_id: learner.student_id, session_id: "lrs_mock_0001", traces: [mastery], evidence: [{ id: "evd_mock_1", kind: "answer_change_pattern" }], hypotheses: [{ id: "hyp_mock_0001", detector: "answer_change_after_first_step", statement: "You get there when the first step is visible, and not when it has to be recalled.", status: "active", confidence: 0.61, citations: [{ ref: "Renkl 2014", claim: "fading worked examples sustains gains" }], falsification: "If accuracy stays flat with examples shown, this was not the constraint." }], proposals: [proposal], evaluated: [], gaps: [{ topic_id: item.topic_id, topic_title: mastery.title, blocking_prerequisite_title: "What a fraction means", severity: 0.42 }], plan: { steps: [{ topic_id: item.topic_id, topic_title: mastery.title, status: "next_up", mastery: 0.62, action: "teach_then_practice" }] }, bkt: {}, retention: { checks: 2, calibration: { n: 2, brier: 0.11, ece: 0.04, buckets: [] }, due_now: [] }, notes: [], served_by: "your own material", loop: loopSteps } },
  { method: "GET", match: /^\/api\/v1\/adaptations\/pending/, body: { proposals: [proposal], explain: "Each row separates what was observed from what we think it means. Nothing here has been applied. Saying no is recorded too, and stops us asking again tomorrow." } },
  {
    method: "GET",
    match: /^\/api\/v1\/adaptations\/active/,
    body: {
      active: [
        {
          id: "act_mock_0001",
          strategy: proposal.strategy,
          label: "Try before you look",
          action: "presentation",
          payload: proposal.action_payload,
          source: "student_approved",
          active: true,
          started_at: "2026-10-11T09:20:00",
          ends_at: null,
          ended_at: null,
          end_reason: null,
          proposal_id: proposal.id,
          // A change that has been live for a few answers and has not been judged: the server
          // stores the count and the sentence with the measurement, so the screen can say what
          // it is waiting for instead of saying nothing.
          effect_summary: {
            baseline_n: 5,
            after_n: 3,
            baseline_mean: 0.4,
            after_mean: 0.67,
            mean_delta: 0.27,
            ci_low: -0.2,
            ci_high: 0.73,
            verdict: "insufficient_evidence",
            explanation: "fewer than 5 scored answers in one of the windows",
            verdict_words: "Not enough scored answers yet to judge this — 3 so far, about 3 more to go.",
            status: "gathering_evidence",
            pilot: false,
            label: null,
          },
          effect_raw: {},
          effect_status: "gathering_evidence",
          effect_measured: false,
          watch_metric: "accuracy_after_read",
          mechanism: "Answering before the explanation is open forces retrieval.",
          citations: ["Renkl 2014"],
        },
      ],
      waiting_for_effect: [
        {
          id: "act_mock_0001",
          strategy: proposal.strategy,
          effect_status: "gathering_evidence",
          effect_measured: false,
          effect_summary: null,
        },
      ],
    },
  },
  { method: "POST", match: /^\/api\/v1\/adaptations\/evaluate/, body: { evaluated: [], note: "small samples are labelled insufficient rather than smoothed over" } },
  { method: "GET", match: /^\/api\/v1\/adaptations\/history/, body: { decisions: [{ ...proposal, status: "approved", decided_at: "2026-10-11T09:20:00", student_note: "yes, but fade them after two" }] } },
  { method: "POST", match: /^\/api\/v1\/adaptations\/[^/]+\/decision/, body: { proposal_id: proposal.id, strategy: proposal.strategy, decision: "accept", active_adaptation_id: "act_mock_0001", payload: proposal.action_payload, review: { metric: "accuracy_after_read", window: proposal.review_window, note: "this gets compared against the baseline either way, and the result is shown to you." }, note: null, ok: true, status: "approved" } },
  { method: "POST", match: /^\/api\/v1\/adaptations\/[^/]+\/revoke/, body: { ok: true, revoked: 1, strategies: [proposal.strategy] } },
  { method: "GET", match: /^\/api\/v1\/plan/, body: { gaps: [], remediation: [], path: { steps: [{ topic_id: item.topic_id, topic_title: mastery.title, status: "next_up", mastery: 0.62, action: "teach_then_practice" }] }, bkt: {}, retention: {}, notes: [] } },
  { method: "GET", match: /^\/api\/v1\/topics/, body: [({ id: item.topic_id, code: "fraction_add", title: mastery.title, difficulty: 0.5, items: 5, p_know: 0.62 } as unknown) as Row["body"]] },
  { method: "GET", match: /^\/api\/v1\/review\/due/, body: { due: [{ topic_id: item.topic_id, title: mastery.title, due_at: "2026-10-11T14:40:00", overdue: true, half_life_hours: 26.5, expected_recall_at_due: 0.85, reps: 3, lapses: 1 }], count: 1, rule: "scheduled at the point your own recall is projected to reach ~85%" } },
  { method: "POST", match: /^\/api\/v1\/review\/complete/, body: { lag_hours: 26.4, expected_recall: 0.84, correct: true, scheduled: { due_at: "2026-10-15T09:10:00", half_life_hours: 62.1, reps: 4, lapses: 1, expected_recall_at_due: 0.85 }, was_due_at: "2026-10-11T14:40:00" } },
  { method: "GET", match: /^\/api\/v1\/dashboard/, body: { student_id: learner.student_id, generated_at: "2026-10-11T09:30:00", profile_taken_at: "2026-10-11T09:29:40", mastery: [mastery, masterySecond], band_summary: { developing: 1, new: 1 }, weakest: [mastery], knowledge_gaps: [{ topic_id: item.topic_id, topic_title: mastery.title, blocking_prerequisite_title: "What a fraction means", severity: 0.42 }], hypotheses: [{ id: "hyp_mock_0001", statement: "You get there when the first step is visible.", status: "active", confidence: 0.61, disproof: "accuracy stays flat with examples shown" }], pending_proposals: [proposal], active_adaptations: [], decided_adaptations: [], reviews: { due_now: [{ topic_id: item.topic_id, title: mastery.title, due_at: "2026-10-11T14:40:00" }], checks_total: 2, checks_mean_score: 0.75 }, last_7_days: { attempts: 11, mean_score: 0.68, hints_used: 4, mean_seconds_on_question: 63 }, data_volume: { attempts: 11, events: 84, evidence: 3, retention_checks: 2 }, open_questions: [], measured_outcomes: { evaluated: 0, applied: 1 }, strategy_evidence: [], student_preferences: { goal: "GCSE maths" }, guardrails: { no_fixed_trait_labels: true, hypotheses_are_not_facts: true }, notes: [] } },
  { method: "GET", match: /^\/api\/v1\/journey/, body: { student_id: learner.student_id, generated_at: "2026-10-11T09:31:00", window_days: 90, nodes: [{ ts: "2026-10-11T09:12:44", kind: "proposal", tone: "ask", title: "Proposed change: worked examples (pending)", detail: proposal.change, meta: {} }, { ts: "2026-10-11T09:20:00", kind: "adaptation", tone: "good", title: "Now applying: worked example before the next attempt", detail: "you approved it", meta: {} }, { ts: "2026-10-10T18:04:11", kind: "attempt", tone: "good", title: "Answered correctly: Adding and subtracting fractions", detail: "63s, no hints", meta: {} }], counts: { attempt: 1, proposal: 1, adaptation: 1 }, phases: [{ name: "practise", from: "2026-10-10", to: "2026-10-11", nodes: 3 }], summary: "11 answers in the last 7 days, 4 of them after opening a hint.", milestones: [{ ts: "2026-10-11T09:20:00", title: "First adaptation approved", detail: "worked examples" }], stats: { sessions: 3, attempts: 11, mean_score: 0.68, events: 84 } } },
  { method: "GET", match: /^\/api\/v1\/strategies/, body: { strategies: [{ id: "worked_examples", label: "Worked example before the next attempt", family: "load_reduction", what: "Show a fully solved, step labelled example of the same problem type immediately before the question.", mechanism: "Reduces intrinsic load on first exposure.", watch_metric: "accuracy", n: 0, p_helps: 0.5 }] } },
  { method: "GET", match: /^\/api\/v1\/voice\/capabilities/, body: { stt: { local_stt: false, detail: "local transcription is not installed in this fixture", fallback: "browser Web Speech API dictation (free, on your device)", model_size: "base" }, tts: { local_tts: false, detail: "the local voice engine is not installed in this fixture", fallback: "browser speechSynthesis with male/female voice selection", sample_rate: 24000 }, voices: { female: { a: "af_heart", b: "bf_violet" }, male: { a: "am_fenrir", b: "bm_ola" } }, barge_in: true, transport: "websocket /ws/voice (binary s16le frames + JSON control messages)", path: "/ws/voice", note: "transcription accuracy and teaching accuracy are measured separately" } },
  { method: "POST", match: /^\/api\/v1\/voice\/speak/, body: { available: false, reason: "fixtures: no local TTS installed", fallback: "browser speechSynthesis" } },
  { method: "POST", match: /^\/api\/v1\/voice\/transcribe/, body: { text: "", backend: "browser", language: "en", confidence: null, duration_s: 0.5, latency_ms: 0, model: null, segments: 0, note: "browser STT supplies text, not audio" } },
  { method: "GET", match: /^\/api\/v1\/status/, body: { ok: true, version: "0.1.0-mock", demo_mode: false, lessons: { state: "simple mode", says: "Lessons: simple mode", note: "fixtures: every explanation is assembled from the material by the built-in tutor" }, data_protection: { file_encrypted: false, underlying_engine: "sqlite + field-level encryption", file_level: "the file itself is not encrypted; the answers and notes listed below are", protected: ["learner answers + feedback"], not_protected: ["study preferences"], note: "fixtures: no database is open" } } },
  { method: "GET", match: /^\/api\/v1\/quotas/, body: { pacing: { requests: [120, 240], lesson_requests: [12, 20], writes: [30, 60] }, note: "fixtures: how often the app will accept a request" } },
  { method: "GET", match: /^\/api\/v1\/privacy/, body: { telemetry: { recorded: ["the order of events", "timings", "first vs final answer"], never_recorded: ["keystrokes, key timing, or per character edit content", "screenshots, screen content, other tabs"], why: "Timing and edit behaviour are evidence about the interaction, not proof of a mental state.", allowed_event_types: ["question_shown", "first_interaction", "hint_opened", "answer_submitted"], forbidden_keys: ["keystrokes", "clipboard", "screenshot"] }, sent_to_model: { when: "only when a written-for-you answer is available and you consented", identity: "a keyed pseudonym, never your name or email", redaction: "emails, phone numbers, URLs and anything shaped like an ID are stripped first", on_refusal: "the answer is written locally instead and the reply says so" }, data_protection: { file_level: "the file itself is not encrypted; the answers and notes listed below are", protected: ["learner answers + feedback"], not_protected: ["study preferences"] }, voice: { stored: "transcript text only while voice is on" }, storage: { at_rest: "column level AES-256-GCM", key: "a keyfile outside the database" }, learner_controls: ["export your data", "turn analytics off at any time", "delete your history (DELETE /api/v1/me)"] } },
  { method: "GET", match: /^\/api\/v1\/audit/, body: { entries: [{ ts: "2026-10-11T09:20:00", actor_id: learner.student_id, action: "adaptation_approved", object: `adaptation_proposal:${proposal.id}`, outcome: "ok", detail: { strategy: proposal.strategy } }] } },
  { method: "GET", match: /^\/api\/v1\/events\/allowlist/, body: { allowed: { question_shown: ["stem_words", "variant"], first_interaction: ["ms_since_shown"], hint_opened: ["hint_no"], answer_submitted: ["correct", "value"] }, forbidden_keys: ["keystrokes", "clipboard"], rule: "fields outside this list are dropped, and the client is told which" } },
  { method: "GET", match: /^\/api\/v1\/numbas\/export/, body: { exported: 0, imported: 0, skipped: 0, warnings: ["mock: no export"] } },
  { method: "GET", match: /^\/api\/v1\/resources\/status/, body: { sources: [{ kind: "papers and citations", ok: false, source: "the open catalogue", note: "fixtures: no network calls are made", count: 0 }], cache_days: 7, note: "fixtures: no network calls were made" } },
  { method: "GET", match: /^\/api\/v1\/syllabus/, body: { boards: [{ board: "GCSE", tier: "foundation", topics: 21, note: "mock" }] } },
  { method: "GET", match: /^\/api\/v1\/materials/, body: { materials: [{ id: "mat_mock", title: "Fractions - adding", topic_id: item.topic_id, words: 240, source: "uploaded" }], note: "mock" } },
  // --- endpoints the screens can reach but do not depend on -------------------------
  // Recorded here so `?mock=1` never 404s on a real route: a fixture that says "no
  // fixture" would look like a backend bug to anyone reviewing the interface.
  { method: "GET", match: /^\/api\/v1\/items\/[^/]+$/, body: { ...item, answer_key: undefined, mock: true } },
  { method: "GET", match: /^\/api\/v1\/profile$/, body: { student_id: learner.student_id, taken_at: "2026-10-11T09:29:40", mastery: [mastery], hypotheses: [], directives: [], note: "profile snapshot: the current estimates, with the evidence counts behind them" } },
  { method: "POST", match: /^\/api\/v1\/events$/, body: { accepted: 0, rejected: 0, duplicate: 0, dropped_fields: 0, reasons: [], session_id: "lrs_mock_0001", event_types: [] } },
  { method: "POST", match: /^\/api\/v1\/learning\/train$/, body: { trained: 1, scope: "global", note: "estimates derived again from the event log; nothing was invented to fill a gap" } },
  { method: "POST", match: /^\/api\/v1\/tutor\/message$/, body: { answer: "Like units have to match before you add, so find a denominator both parts share. Sixths work for halves and thirds.", served_by: "your own material", sections: [{ label: "explanation", text: "Make the parts the same size, then add the counts." }], next_action: "Try 1/3 + 1/4 the same way.", sources: [], privacy: { sent_to_model: false, what_was_sent: "nothing left this machine" }, message_id: "msg_mock_0002" } },
  { method: "POST", match: /^\/api\/v1\/resources\/search$/, body: { results: [], sources: [], note: "fixtures: no network calls are made" } },
  { method: "POST", match: /^\/api\/v1\/numbas\/import$/, body: { imported: 0, skipped: [], note: "a paste of Numbas XML; nothing was accepted because the mock has no file" } },
  { method: "POST", match: /^\/api\/v1\/adaptations\/strategy\/[^/]+\/revoke$/, body: { ok: true, revoked: 1, strategies: ["retrieval_practice"], note: "turned off by strategy name" } },
  /* lessons: the interactive engine (?mock=1 reviews the player, it does not run it) */
  { method: "GET", match: /^\/api\/v1\/lessons$/, body: lessonList },
  { method: "GET", match: /^\/api\/v1\/lessons\/kinds$/, body: lessonKinds },
  { method: "GET", match: /^\/api\/v1\/lessons\/evidence$/, body: lessonEvidence },
  { method: "GET", match: /^\/api\/v1\/lessons\/runs$/, body: { runs: [], total: 0, note: "the mock cannot store a run, so there is no history to show" } },
  { method: "POST", match: /^\/api\/v1\/lessons\/open$/, body: lessonOpen },
  { method: "POST", match: /^\/api\/v1\/lessons\/validate$/, body: lessonValidate },
  { method: "POST", match: /^\/api\/v1\/lessons\/author$/, body: lessonAuthor },
  { method: "POST", match: /^\/api\/v1\/lessons\/run\/[^/]+\/progress$/, body: lessonProgress },
  { method: "POST", match: /^\/api\/v1\/lessons\/run\/[^/]+\/predict$/, body: lessonPredict },
  { method: "POST", match: /^\/api\/v1\/lessons\/run\/[^/]+\/explain$/, body: lessonExplain },
  { method: "POST", match: /^\/api\/v1\/lessons\/run\/[^/]+\/variables$/, body: lessonVariables },
  { method: "POST", match: /^\/api\/v1\/lessons\/run\/[^/]+\/finish$/, body: lessonFinish },
  { method: "GET", match: /^\/api\/v1\/lessons\/run\/[^/]+$/, body: lessonReview },
  /* the content engine (§7). The grade row cannot read a POST body, so it says the one true
     thing available to it; `lib/content.ts` grades the mock answer properly, because it does know
     which option was picked. */
  { method: "GET", match: /^\/api\/v1\/content\/breakdown/, body: contentBreakdown },
  { method: "GET", match: /^\/api\/v1\/content\/pack/, body: contentPack },
  { method: "POST", match: /^\/api\/v1\/content\/grade/, body: { correct: false, status: "unparseable", points: 0, max_points: 1, why: "this fixture never saw the answer, so it cannot grade it — use the screen, which does know which option was picked", explanation: "", concept: "calc_rate", leg: "A", rung: 3, demand: "procedural" } },
];

export function mockLookup(method: string, url: string): { found: boolean; body?: unknown; status?: number; error?: { message: string; detail?: unknown } } {
  for (const row of rows) {
    if (row.method !== method) continue;
    if (!row.match.test(url)) continue;
    if (row.error) return { found: true, error: row.error, status: row.status };
    const body = typeof row.body === "function" ? (row.body as (url: string, method: string) => unknown)(url, method) : row.body;
    return { found: true, body, status: row.status ?? 200 };
  }
  return { found: false };
}
