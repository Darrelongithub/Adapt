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

type Row = { method: string; match: RegExp; body: unknown; status?: number; error?: { message: string; detail?: unknown } };

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
  presentation: { max_words: 90, hints_allowed: 2, require_teachback: false, attach_worked_example: false },
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
  what_it_does: "Show a fully solved, step-labelled example of the same problem type immediately before the question.",
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

const rows: Row[] = [
  { method: "GET", match: /^\/api\/v1\/health/, body: { ok: true, mode: "local", learners: 1, mock: true } },
  { method: "GET", match: /^\/api\/v1\/me$/, body: learner },
  { method: "POST", match: /^\/api\/v1\/students/, body: { ...learner, csrf_token: "mock-csrf-token", expires_at: "2026-10-12T03:00:00", created: true, onboarding_complete: false } },
  { method: "POST", match: /^\/api\/v1\/login/, body: { ...learner, csrf_token: "mock-csrf-token", created: false } },
  { method: "POST", match: /^\/api\/v1\/logout/, body: { ok: true } },
  { method: "POST", match: /^\/api\/v1\/me\/onboarding/, body: { student_id: learner.student_id, preferences: { goal: "GCSE maths", weekly_minutes: 150 }, ignored_keys: [], rejected_reasons: {}, consent: { analytics: true, share_summary_with_ai: true }, note: "No learning-style question was asked. 'How you learn best' is measured from what happens during practice, and any lasting change needs your approval." } },
  { method: "GET", match: /^\/api\/v1\/me\/preferences/, body: { preferences: { goal: "GCSE maths", weekly_minutes: 150, max_explanation_words: 90 }, allowed_keys: ["avoid", "encourage", "goal", "language", "max_explanation_words", "push_me", "quiet_hours", "session_minutes", "want_review_reminders", "want_voice", "weekly_minutes"] } },
  { method: "POST", match: /^\/api\/v1\/me\/preferences/, body: { accepted: { goal: "GCSE maths" }, rejected_keys: [], rejected_reasons: {} } },
  { method: "DELETE", match: /^\/api\/v1\/me$/, body: { deleted: true, hard_purge_available_via: "adapt db purge-student --student-id <id> --confirm", note: "learning history is removed from every query path immediately" } },
  { method: "GET", match: /^\/api\/v1\/assessment\/diagnostic/, body: { purpose: "place you on each topic with a confidence band, not to grade you", items: [planEntry, { ...planEntry, position: 2 }], how_it_is_used: "Answers, timings, hint use and answer changes are recorded as events.", note_on_time: "If you need longer, say so - time spent is evidence about the interaction, not a score." } },
  { method: "POST", match: /^\/api\/v1\/sessions$/, body: { session_id: "lrs_mock_0001", mode: "practice", started_at: "2026-10-11T09:00:00", event_types_accepted: ["question_shown", "first_interaction", "hint_opened", "answer_submitted"] } },
  { method: "POST", match: /^\/api\/v1\/sessions\/[^/]+\/finish/, body: { ok: true, session_id: "lrs_mock_0001", duration_seconds: 640, active_ms: 592000, mode: "practice", next: "run AI 1 analysis to update mastery, hypotheses and any proposed adaptation" } },
  { method: "POST", match: /^\/api\/v1\/sessions\/[^/]+\/events/, body: { accepted: 4, rejected: 0, duplicate: 0, dropped_fields: 0, reasons: [], session_id: "lrs_mock_0001", event_types: ["question_shown", "first_interaction", "hint_opened", "answer_submitted"] } },
  { method: "GET", match: /^\/api\/v1\/sessions\/[^/]+\/events/, body: { events: [{ seq: 1, ts: "2026-10-11T09:00:04", type: "question_shown", item_id: item.id, payload: { stem_words: 9 } }] } },
  { method: "POST", match: /^\/api\/v1\/attempts/, body: receipt },
  { method: "GET", match: /^\/api\/v1\/practice/, body: { items: [planEntry], why: "two topics are due for a look, and one has a gap underneath it" } },
  { method: "POST", match: /^\/api\/v1\/items\/[^/]+\/hint/, body: { hint: "Write the two fractions over the same denominator before you add anything.", hint_no: 1, max_hints: 2, level: "developing", hints_available: 2, exhausted: false, item_id: item.id } },
  { method: "GET", match: /^\/api\/v1\/items\/[^/]+\/worked-example/, body: { steps: [{ index: 1, text: "Make the denominators equal: 2 and 3 both divide 6.", why: "a fraction only names parts of one whole" }, { index: 2, text: "Scale both numerators: 1/2 = 3/6, 1/3 = 2/6.", why: "scaling both sides keeps the value" }], item_id: item.id, note: "worked examples are faded on purpose: we add steps back when you ask, and remove them when you no longer need them" } },
  { method: "POST", match: /^\/api\/v1\/items\/[^/]+\/worked-example/, body: { steps: [{ index: 1, text: "Make the denominators equal: 2 and 3 both divide 6.", why: "a fraction only names parts of one whole" }], item_id: item.id, note: "opening this is recorded, because the fading decision is measured on it" } },
  { method: "GET", match: /^\/api\/v1\/items\/[^/]+\/reveal/, body: { steps: [{ index: 1, text: "6/12 is not the way; use 3/6 + 2/6." }], answer: null, correct_index: 1, choices: [{ text: "2/5", correct: false, why_wrong: "that adds tops and bottoms separately" }, { text: "5/6", correct: true, why_wrong: null }, { text: "1/5", correct: false, why_wrong: "that is 1/2 - 3/5" }, { text: "5/5", correct: false, why_wrong: "one whole - the pieces are smaller than that" }], why_it_is_ok_to_look: "you asked; nothing here is a persistent change" } },
  { method: "POST", match: /^\/api\/v1\/items\/[^/]+\/teachback/, body: { score: 0.67, coverage: 0.67, method: "rubric_concept_coverage", matched: ["common denominator", "equal parts"], missing: ["numerator scales too"], followup: "Which part of the fraction changes when you scale it up?", words: 38, note: "scored on ideas covered, not fluency or spelling" } },
  { method: "POST", match: /^\/api\/v1\/explain/, body: { answer: "You can only add or subtract like units, so the parts have to be the same size first. Two fractions are equivalent when they name the same point on the number line.", provider: "local", sections: [{ label: "explanation", text: "You can only add or subtract like units, so the parts have to be the same size first." }], next_action: "Try 1/2 + 1/4 and say out loud which denominator you chose.", adaptations_applied: ["segmented_text"], sources: [{ kind: "material", ref: "mat_mock:0", title: "Fractions - adding", material_id: "mat_mock" }], teachback_prompt: null, resource_hint: null, level: "developing", model_ms: 0, llm: {}, message_id: "msg_mock_0001", intent: null, meta: { grounded: true, provider_detail: "local BM25 over course materials" }, provider_note: "answer extracted from your course material (no model call needed)", privacy: { sent_to_model: false, redacted_fields: 0, what_was_sent: "nothing - no model was called" } } },
  { method: "POST", match: /^\/api\/v1\/analyze/, body: { student_id: learner.student_id, session_id: "lrs_mock_0001", traces: [mastery], evidence: [{ id: "evd_mock_1", kind: "answer_change_pattern" }], hypotheses: [{ id: "hyp_mock_0001", detector: "answer_change_after_first_step", statement: "You get there when the first step is visible, and not when it has to be recalled.", status: "active", confidence: 0.61, citations: [{ ref: "Renkl 2014", claim: "fading worked examples sustains gains" }], falsification: "If accuracy stays flat with examples shown, this was not the constraint." }], proposals: [proposal], evaluated: [], gaps: [{ topic_id: item.topic_id, topic_title: mastery.title, blocking_prerequisite_title: "What a fraction means", severity: 0.42 }], plan: { steps: [{ topic_id: item.topic_id, topic_title: mastery.title, status: "next_up", mastery: 0.62, action: "teach_then_practice" }] }, bkt: {}, retention: { checks: 2, calibration: { n: 2, brier: 0.11, ece: 0.04, buckets: [] }, due_now: [] }, notes: [], provider: "local", llm: { used: false }, model_ms: 0, loop: loopSteps } },
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
          effect_summary: null,
          watch_metric: "accuracy_after_read",
          mechanism: "Answering before the explanation is open forces retrieval.",
          citations: ["Renkl 2014"],
        },
      ],
      waiting_for_effect: [{ id: "act_mock_0001", strategy: proposal.strategy, effect_summary: null }],
    },
  },
  { method: "POST", match: /^\/api\/v1\/adaptations\/evaluate/, body: { evaluated: [], note: "small samples are labelled insufficient rather than smoothed over" } },
  { method: "GET", match: /^\/api\/v1\/adaptations\/history/, body: { decisions: [{ ...proposal, status: "approved", decided_at: "2026-10-11T09:20:00", student_note: "yes, but fade them after two" }] } },
  { method: "POST", match: /^\/api\/v1\/adaptations\/[^/]+\/decision/, body: { proposal_id: proposal.id, strategy: proposal.strategy, decision: "accept", active_adaptation_id: "act_mock_0001", payload: proposal.action_payload, review: { metric: "accuracy_after_read", window: proposal.review_window, note: "AI 1 compares this against the baseline and reports it to you either way." }, note: null, ok: true, status: "approved" } },
  { method: "POST", match: /^\/api\/v1\/adaptations\/[^/]+\/revoke/, body: { ok: true, revoked: 1, strategies: [proposal.strategy] } },
  { method: "GET", match: /^\/api\/v1\/plan/, body: { gaps: [], remediation: [], path: { steps: [{ topic_id: item.topic_id, topic_title: mastery.title, status: "next_up", mastery: 0.62, action: "teach_then_practice" }] }, bkt: {}, retention: {}, notes: [] } },
  { method: "GET", match: /^\/api\/v1\/topics/, body: [({ id: item.topic_id, code: "fraction_add", title: mastery.title, difficulty: 0.5, items: 5, p_know: 0.62 } as unknown) as Row["body"]] },
  { method: "GET", match: /^\/api\/v1\/review\/due/, body: { due: [{ topic_id: item.topic_id, title: mastery.title, due_at: "2026-10-11T14:40:00", overdue: true, half_life_hours: 26.5, expected_recall_at_due: 0.85, reps: 3, lapses: 1 }], count: 1, rule: "scheduled at the point your own recall is projected to reach ~85%" } },
  { method: "POST", match: /^\/api\/v1\/review\/complete/, body: { lag_hours: 26.4, expected_recall: 0.84, correct: true, scheduled: { due_at: "2026-10-15T09:10:00", half_life_hours: 62.1, reps: 4, lapses: 1, expected_recall_at_due: 0.85 }, was_due_at: "2026-10-11T14:40:00" } },
  { method: "GET", match: /^\/api\/v1\/dashboard/, body: { student_id: learner.student_id, generated_at: "2026-10-11T09:30:00", profile_taken_at: "2026-10-11T09:29:40", mastery: [mastery], band_summary: { developing: 1 }, weakest: [mastery], knowledge_gaps: [{ topic_id: item.topic_id, topic_title: mastery.title, blocking_prerequisite_title: "What a fraction means", severity: 0.42 }], hypotheses: [{ id: "hyp_mock_0001", statement: "You get there when the first step is visible.", status: "active", confidence: 0.61, disproof: "accuracy stays flat with examples shown" }], pending_proposals: [proposal], active_adaptations: [], decided_adaptations: [], reviews: { due_now: [{ topic_id: item.topic_id, title: mastery.title, due_at: "2026-10-11T14:40:00" }], checks_total: 2, checks_mean_score: 0.75 }, last_7_days: { attempts: 11, mean_score: 0.68, hints_used: 4, mean_seconds_on_question: 63 }, data_volume: { attempts: 11, events: 84, evidence: 3, retention_checks: 2 }, open_questions: [], measured_outcomes: { evaluated: 0, applied: 1 }, strategy_evidence: [], student_preferences: { goal: "GCSE maths" }, guardrails: { no_fixed_trait_labels: true, hypotheses_are_not_facts: true }, notes: [] } },
  { method: "GET", match: /^\/api\/v1\/journey/, body: { student_id: learner.student_id, generated_at: "2026-10-11T09:31:00", window_days: 90, nodes: [{ ts: "2026-10-11T09:12:44", kind: "proposal", tone: "ask", title: "Proposed change: worked examples (pending)", detail: proposal.change, meta: {} }, { ts: "2026-10-11T09:20:00", kind: "adaptation", tone: "good", title: "Now applying: worked example before the next attempt", detail: "you approved it", meta: {} }, { ts: "2026-10-10T18:04:11", kind: "attempt", tone: "good", title: "Answered correctly: Adding and subtracting fractions", detail: "63s, no hints", meta: {} }], counts: { attempt: 1, proposal: 1, adaptation: 1 }, phases: [{ name: "practise", from: "2026-10-10", to: "2026-10-11", nodes: 3 }], summary: "11 answers in the last 7 days, 4 of them after opening a hint.", milestones: [{ ts: "2026-10-11T09:20:00", title: "First adaptation approved", detail: "worked examples" }], stats: { sessions: 3, attempts: 11, mean_score: 0.68, events: 84 } } },
  { method: "GET", match: /^\/api\/v1\/strategies/, body: { strategies: [{ id: "worked_examples", label: "Worked example before the next attempt", family: "load_reduction", what: "Show a fully solved, step-labelled example of the same problem type immediately before the question.", mechanism: "Reduces intrinsic load on first exposure.", watch_metric: "accuracy", n: 0, p_helps: 0.5 }] } },
  { method: "GET", match: /^\/api\/v1\/voice\/capabilities/, body: { stt: { local_stt: false, detail: "faster-whisper not importable in this mock", fallback: "browser Web Speech API dictation (free, on-device)", model_size: "base" }, tts: { local_tts: false, detail: "kokoro not importable in this mock", fallback: "browser speechSynthesis with male/female voice selection", sample_rate: 24000 }, voices: { female: { a: "af_heart", b: "bf_violet" }, male: { a: "am_fenrir", b: "bm_ola" } }, barge_in: true, transport: "websocket /ws/voice (binary s16le frames + JSON control messages)", path: "/ws/voice", note: "transcription accuracy and teaching accuracy are measured separately" } },
  { method: "POST", match: /^\/api\/v1\/voice\/speak/, body: { ok: false, provider: "none", reason: "no local TTS installed", text: "Try one more question.", use_browser: true, fallback: "speechSynthesis" } },
  { method: "POST", match: /^\/api\/v1\/voice\/transcribe/, body: { text: "", backend: "browser", language: "en", confidence: null, duration_s: 0.5, latency_ms: 0, model: null, segments: 0, note: "browser STT supplies text, not audio" } },
  { method: "GET", match: /^\/api\/v1\/status/, body: { mode: "local", version: "0.1.0-mock", database: { path: ":memory:", file_encrypted: false, plaintext_header: false, driver: { driver: "sqlite", file_encrypted: false, key_source: "mock", detail: "mock" } }, llm: { configured: false, provider: "none", note: "mock: no API key" }, resources: { providers: [] }, voice: { stt: { local_stt: false }, tts: { local_tts: false } }, rate_limits: { scopes: {} }, encryption: { requested: "field-only", file_encrypted: false, driver: "sqlite", driver_detail: "mock", key_source: "mock", field_level_encryption: true, fields_encrypted: ["learner answers + feedback"] } } },
  { method: "GET", match: /^\/api\/v1\/quotas/, body: { day: "2026-10-11", budget: { calls: 120, tokens: 2000000, models: ["gemini-2.5-flash"] }, used: { calls: 0, tokens: 0 }, by_purpose: {}, calls_left: 120, buckets: {}, rate_limit_multiplier: 1, free_tiers: { gemini: { model_ai1: "gemini-2.5-flash", daily_call_budget: 120, on_exhaustion: "fall back to the local tutor" } }, rate_limiter: {} } },
  { method: "GET", match: /^\/api\/v1\/privacy/, body: { telemetry: { recorded: ["the order of events", "timings", "first vs final answer"], never_recorded: ["keystrokes, key timing, or per-character edit content", "screenshots, screen content, other tabs"], why: "Timing and edit behaviour are evidence about the interaction, not proof of a mental state.", allowed_event_types: ["question_shown", "first_interaction", "hint_opened", "answer_submitted"], forbidden_keys: ["keystrokes", "clipboard", "screenshot"] }, sent_to_gemini: { fields: ["topic mastery", "hypotheses", "the question text"], never: ["name", "email"] }, voice: { stored: "transcript text only while voice is on" }, storage: { at_rest: "column-level AES-256-GCM", key: "a keyfile outside the database" }, learner_controls: ["export your data", "turn analytics off at any time", "delete your history (DELETE /api/v1/me)"] } },
  { method: "GET", match: /^\/api\/v1\/audit/, body: { entries: [{ ts: "2026-10-11T09:20:00", actor_id: learner.student_id, action: "adaptation_approved", object: `adaptation_proposal:${proposal.id}`, outcome: "ok", detail: { strategy: proposal.strategy } }] } },
  { method: "GET", match: /^\/api\/v1\/events\/allowlist/, body: { allowed: { question_shown: ["stem_words", "variant"], first_interaction: ["ms_since_shown"], hint_opened: ["hint_no"], answer_submitted: ["correct", "value"] }, forbidden_keys: ["keystrokes", "clipboard"], rule: "fields outside this list are dropped, and the client is told which" } },
  { method: "GET", match: /^\/api\/v1\/numbas\/export/, body: { exported: 0, imported: 0, skipped: 0, warnings: ["mock: no export"] } },
  { method: "GET", match: /^\/api\/v1\/resources\/status/, body: { providers: [{ provider: "openalex", enabled: true, configured: false, purpose: "mock", auth: "none", quota_note: "mock", cache_ttl_hours: 168 }], note: "mock: no network calls were made" } },
  { method: "GET", match: /^\/api\/v1\/syllabus/, body: { boards: [{ board: "GCSE", tier: "foundation", topics: 21, note: "mock" }] } },
  { method: "GET", match: /^\/api\/v1\/materials/, body: { materials: [{ id: "mat_mock", title: "Fractions - adding", topic_id: item.topic_id, words: 240, source: "uploaded" }], note: "mock" } },
  // --- endpoints the screens can reach but do not depend on -------------------------
  // Recorded here so `?mock=1` never 404s on a real route: a fixture that says "no
  // fixture" would look like a backend bug to anyone reviewing the interface.
  { method: "GET", match: /^\/api\/v1\/items\/[^/]+$/, body: { ...item, answer_key: undefined, mock: true } },
  { method: "GET", match: /^\/api\/v1\/profile$/, body: { student_id: learner.student_id, taken_at: "2026-10-11T09:29:40", mastery: [mastery], hypotheses: [], directives: [], note: "profile snapshot: what AI 1 currently believes, with the evidence counts behind it" } },
  { method: "POST", match: /^\/api\/v1\/events$/, body: { accepted: 0, rejected: 0, duplicate: 0, dropped_fields: 0, reasons: [], session_id: "lrs_mock_0001", event_types: [] } },
  { method: "POST", match: /^\/api\/v1\/learning\/train$/, body: { trained: 1, scope: "global", note: "estimates re-derived from the event log; nothing was invented to fill a gap" } },
  { method: "POST", match: /^\/api\/v1\/tutor\/message$/, body: { answer: "Like units have to match before you add, so find a denominator both parts share. Sixths work for halves and thirds.", provider: "local", sections: [{ label: "explanation", text: "Make the parts the same size, then add the counts." }], next_action: "Try 1/3 + 1/4 the same way.", sources: [], privacy: { sent_to_model: false, what_was_sent: "nothing - no model was called" }, message_id: "msg_mock_0002", llm: { used: false } } },
  { method: "POST", match: /^\/api\/v1\/resources\/search$/, body: { results: [], provider: "none", note: "no network egress allowed from this instance, and no OpenAlex key is configured" } },
  { method: "POST", match: /^\/api\/v1\/numbas\/import$/, body: { imported: 0, skipped: [], note: "a paste of Numbas XML; nothing was accepted because the mock has no file" } },
  { method: "POST", match: /^\/api\/v1\/adaptations\/strategy\/[^/]+\/revoke$/, body: { ok: true, revoked: 1, strategies: ["retrieval_practice"], note: "turned off by strategy name" } },
];

export function mockLookup(method: string, url: string): { found: boolean; body?: unknown; status?: number; error?: { message: string; detail?: unknown } } {
  for (const row of rows) {
    if (row.method !== method) continue;
    if (!row.match.test(url)) continue;
    if (row.error) return { found: true, error: row.error, status: row.status };
    return { found: true, body: row.body, status: row.status ?? 200 };
  }
  return { found: false };
}
