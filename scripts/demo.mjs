#!/usr/bin/env node
/**
 * `node scripts/demo.mjs` — the learner loop, driven from the client side.
 *
 * Why this exists when `adapt demo` already does the same story server-side: the backend
 * demo proves the maths and the gate. This one proves the *contract the browser codes
 * against* — CSRF on the verbs that need it, the exact event body the allowlist accepts,
 * the nested `{item, presentation}` shape of a plan, the 409 you get when a proposal has
 * already been answered. Every one of those has been a real defect at some point, and a
 * server-side test could not see them because they live in the HTTP surface.
 *
 * It is honest by construction: it prints the server's own strings, it never asserts that
 * a proposal must appear, and it says "nothing was proposed" out loud rather than
 * manufacturing a pattern to make the demo look clever.
 *
 * Usage:
 *   node scripts/demo.mjs                       # against http://127.0.0.1:8000
 *   ADAPT_API=https://host node scripts/demo.mjs
 *   node scripts/demo.mjs --decide accept       # answer the first pending proposal
 *   node scripts/demo.mjs --wrong 4             # miss N answers so a pattern exists
 *   node scripts/demo.mjs --json                # machine-readable summary at the end
 */

const API = (process.env.ADAPT_API ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const args = process.argv.slice(2);
const has = (flag) => args.includes(flag);
const value = (flag, dflt) => {
  const i = args.indexOf(flag);
  return i >= 0 && args[i + 1] ? Number(args[i + 1]) : dflt;
};
const OUT = { steps: [], warnings: [] };
const verbose = !has("--quiet");

function say(title, body) {
  if (verbose) console.log(`\n\x1b[1m${title}\x1b[0m\n${body}`);
}
function warn(msg) {
  OUT.warnings.push(msg);
  if (verbose) console.log(`  \x1b[33m!\x1b[0m ${msg}`);
}
function record(step, detail) {
  OUT.steps.push({ step, ...detail });
}

const csrf = { token: null };
const cookies = new Map();

async function call(path, opts = {}) {
  // The app throttles a connection that moves faster than a person does, and a walk of the whole
  // loop *is* faster than a person. A throttle is a wait, not a failure, so obey the number the
  // server printed; anything else still throws and stops the run.
  for (let tries = 0; ; tries += 1) {
    let err = null;
    try {
      return await callOnce(path, opts);
    } catch (e) {
      if (e.status !== 429 || tries >= 5) throw e;
      err = e;
    }
    const waitMs = Math.min(20_000, (Number(err?.detail?.retry_after_s ?? 1) + 0.25) * 1000);
    console.log(`  \x1b[2m(rate limited: ${opts.method ?? "GET"} ${path} -> waiting ${(waitMs / 1000).toFixed(1)}s, try ${tries + 2})\x1b[0m`);
    await new Promise((r) => setTimeout(r, waitMs));
  }
}

async function callOnce(path, { method = "GET", json, headers = {}, form } = {}) {
  const url = path.startsWith("/api") || path.startsWith("/healthz") ? `${API}${path}` : `${API}/api/v1/${path.replace(/^\//, "")}`;
  const h = { accept: "application/json", ...headers };
  const body = form ? form : json !== undefined ? JSON.stringify(json) : undefined;
  if (body && !form) h["content-type"] = "application/json";
  if (method !== "GET" || /reveal/.test(path)) {
    if (csrf.token) h["x-csrf-token"] = csrf.token;
  }
  if (cookies.size) h.cookie = [...cookies].map(([k, v]) => `${k}=${v}`).join("; ");
  const res = await fetch(url, { method, headers: h, body });
  for (const raw of res.headers.getSetCookie?.() ?? []) {
    const [pair] = raw.split(";");
    const idx = pair.indexOf("=");
    cookies.set(pair.slice(0, idx).trim(), pair.slice(idx + 1).trim());
    if (pair.trim().startsWith("adapt_csrf=")) csrf.token = pair.slice(idx + 1).trim();
  }
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const err = new Error(`${method} ${url} -> ${res.status}`);
    err.status = res.status;
    err.detail = data;
    throw err;
  }
  return data;
}

const short = (s, n = 110) => (typeof s === "string" ? (s.length > n ? `${s.slice(0, n)}…` : s) : JSON.stringify(s));

async function main() {
  say("ADAPT demo driver", `API: ${API}`);
  let health;
  try {
    // A 429 here means the API is up and this connection is over its per-IP ceiling -- usually
    // because a probe or an earlier run just went through. Saying "cannot reach" would send the
    // reader off to restart a server that is fine, so wait the number it asked for, twice.
    for (let tries = 0; ; tries += 1) {
      try {
        health = await call("/api/v1/health");
        break;
      } catch (err) {
        if (err.status !== 429 || tries >= 2) throw err;
        const waitS = Math.min(30, Number(err.detail?.retry_after_s ?? 8) + 2);
        console.log(`  rate limited on this connection: the API is up, so waiting ${waitS}s and asking again`);
        await new Promise((r) => setTimeout(r, waitS * 1000));
      }
    }
  } catch (err) {
    console.error(
      `\nCannot reach the API at ${API} (${err.message}).${err.status === 429 ? "\n(429: this connection is over the per-IP ceiling -- wait a minute and run again.)" : ""}\n\nStart it first:\n  cd backend && ../.venv/bin/python -m uvicorn adapt.api.app:app --port 8000\n\nor point this script at one that is already running:\n  ADAPT_API=https://your-host node scripts/demo.mjs\n`,
    );
    process.exit(2);
  }
  say("Backend", `mode ${health.mode} · ${health.learners} learner(s) · local_only=${health.local_only} · v${health.version}`);
  record("health", { mode: health.mode });

  // 1. a learner of their own, so a rerun never collides with a previous one
  const stamp = Date.now().toString(36).slice(-5);
  const email = `demo+${stamp}@example.invalid`;
  const created = await call("students", {
    method: "POST",
    json: { display_name: `Demo ${stamp}`, email, password: "demo-passphrase-2026", age_band: "16-18" },
  });
  if (created.csrf_token) csrf.token = created.csrf_token;
  say("Signed in", `${created.student_id} (guest-capable account, created=${created.created})\n  onboarding_complete=${created.onboarding_complete} · session cookie + CSRF token issued`);
  record("register", { student_id: created.student_id, csrf_issued: Boolean(created.csrf_token) });

  // 2. onboarding: a stated preference, one unknown key on purpose (the server must refuse it)
  const onboard = await call("me/onboarding", {
    method: "POST",
    json: {
      preferences: { goal: "GCSE maths, higher tier", session_minutes: 15, max_explanation_words: 35, push_me: true, learning_style: "visual" },
      consent_analytics: true,
      consent_share_with_llm: false,
      voice_enabled: false,
    },
  });
  say(
    "Onboarding",
    `stored: ${Object.entries(onboard.preferences).map(([k, v]) => `${k}=${v}`).join(", ")}\n  refused: ${onboard.ignored_keys.map((k) => `${k} (${onboard.rejected_reasons[k]})`).join(", ") || "nothing"}\n  consent: analytics=${onboard.consent.analytics}, share_summary_with_ai=${onboard.consent.share_summary_with_ai}\n  note: ${short(onboard.note, 200)}`,
  );
  if (!onboard.ignored_keys.includes("learning_style")) warn("the server accepted a 'learning_style' preference — it is supposed to have no key for that");
  if (onboard.preferences?.max_explanation_words !== 35) warn("max_explanation_words did not reach the stored preferences");
  record("onboarding", { ignored_keys: onboard.ignored_keys, consent: onboard.consent });

  // 3. placement: answer every item, missing a fixed number so a pattern can exist
  const diag = await call("/api/v1/assessment/diagnostic");
  const session = await call("sessions", { method: "POST", json: { mode: "diagnostic", device_hint: "demo-driver" } });
  say("Placement", `${diag.items.length} questions · "${short(diag.purpose, 90)}"\n  session ${session.session_id} · accepts event types: ${session.event_types_accepted?.length ?? "?"}`);
  const missFirst = value("--wrong", 4);
  let placed = 0;
  const topicSeen = new Set();
  console.log(
    "  (the demo needs to be able to choose right *and* wrong answers; the question payload\n   carries no key, so it reads each one through the same reveal endpoint the UI\'s button uses)",
  );
  for (const [i, entry] of diag.items.entries()) {
    // Push the interaction log the way the browser does, then the attempt: analysis reads
    // the events, so a demo that skips them is not the same product as the app.
    await call(`sessions/${session.session_id}/events`, {
      method: "POST",
      json: {
        session_id: session.session_id,
        events: [
          { type: "question_shown", item_id: entry.item.id, topic_id: entry.topic_id, payload: { stem_words: entry.item.stem.split(/\s+/).length, variant: entry.item.id } },
          { type: "first_interaction", item_id: entry.item.id, topic_id: entry.topic_id, payload: { ms_since_shown: 1400 + i * 120 } },
        ],
      },
    });
    const ans = await answerFor(entry.item, i < missFirst);
    // Timings belong in the event log, not in the attempt: that is the split the server
    // enforces, and a demo that smuggled them into the attempt body would not match the app.
    await call(`sessions/${session.session_id}/events`, {
      method: "POST",
      json: {
        session_id: session.session_id,
        events: [
          { type: "answer_submitted", item_id: entry.item.id, topic_id: entry.topic_id, payload: { correct: i >= missFirst, changed_times: i % 3 === 0 ? 2 : 0, ms_since_shown: 4000 + i * 300 } },
        ],
      },
    });
    const receipt = await call("attempts", {
      method: "POST",
      json: { session_id: session.session_id, item_id: entry.item.id, answer: ans, teachback_text: entry.item.kind === "teachback" ? "The bottom number says how many equal parts there are, so you cannot add until both fractions are cut the same way." : null, attempt_no: i + 1 },
    });
    topicSeen.add(entry.topic_id);
    if (receipt.correct) placed++;
    if (i < 3 || i === diag.items.length - 1)
      console.log(`  ${String(i + 1).padStart(2)}. ${entry.item.kind.padEnd(9)} ${receipt.correct ? "\x1b[32mright\x1b[0m" : "\x1b[31mwrong\x1b[0m"}  p_know ${receipt.mastery.p_know_before?.toFixed(2) ?? "?"}→${receipt.mastery.p_know.toFixed(2)} (${receipt.mastery.band})${receipt.teachback ? ` teachback ${receipt.teachback.score}` : ""}`);
    else if (i === 4) console.log(`  …${diag.items.length - 5} more, same shape…`);
  }
  const finished = await call(`sessions/${session.session_id}/finish`, { method: "POST", json: {} });
  say("Placement done", `${placed}/${diag.items.length} correct across ${topicSeen.size} topics · active ${Math.round((finished.active_ms ?? 0) / 1000)}s\n  next per the server: ${short(finished.next, 160)}`);
  record("diagnostic", { items: diag.items.length, correct: placed, topics: topicSeen.size });

  const analysis = await call(`/api/v1/analyze?session_id=${session.session_id}`, { method: "POST", json: {} });
  say(
    "Analysis",
    `provider ${analysis.provider} · ${analysis.traces.length} traces, ${analysis.evidence.length} evidence rows, ${analysis.hypotheses.length} hypotheses, ${analysis.proposals.length} proposals\n  notes: ${analysis.notes.length ? analysis.notes.map((n) => short(n, 120)).join(" | ") : "(none)"}\n  loop: ${analysis.loop.map((l) => `${l.step}=${l.done}`).join(" · ")}`,
  );
  record("analyze", { provider: analysis.provider, traces: analysis.traces.length, hypotheses: analysis.hypotheses.length, proposals: analysis.proposals.length });

  // 4. the approval gate, from the learner's side
  // A proposal has to be *earned*: a pattern repeats before anything is worth asking a learner
  // about, and one pass of placement is one observation per topic. When the caller asked to see a
  // decision made, give the detectors a second look at the same struggle -- real attempts, all of
  // them wrong on purpose, through the same endpoints the app uses. Nothing is invented here.
  if (has("--decide") && (await call("adaptations/pending")).proposals.length === 0) {
    const more = await call("/api/v1/practice?count=10");
    const grind = await call("sessions", { method: "POST", json: { mode: "practice" } });
    let ground = 0;
    for (const entry of more.items ?? []) {
      const ans = await answerFor(entry.item, true);
      const out = await call("attempts", { method: "POST", json: { session_id: grind.session_id, item_id: entry.item.id, answer: ans, attempt_no: 1 } });
      if (out.correct === false) ground += 1;
    }
    await call(`sessions/${grind.session_id}/finish`, { method: "POST", json: {} });
    await call(`/api/v1/analyze?session_id=${grind.session_id}`, { method: "POST", json: {} });
    say("Second pass", `  ${ground}/${(more.items ?? []).length} practice answers wrong on purpose, so a pattern can repeat\n  analysis re-run on that session before anything is asked of the learner`);
  }
  const pending = await call("adaptations/pending");
  if (pending.proposals.length === 0) {
    say("Approval gate", "no proposal yet — and that is a valid outcome. A pattern has to repeat before anything is worth asking you about.");
    // Not a warning: a pattern has to *repeat* before a proposal is honest, so an empty
    // first pass is the designed outcome, and --strict must not fail on it.
    say("Approval gate", "note: no proposal yet after placement, which is the expected result for one observation per topic");
  } else {
    const p = pending.proposals[0];
    say(
      "A proposal is waiting",
      `  ask: ${short(p.ask, 150)}\n  observed: ${short(p.facts, 150)}\n  hypothesis: ${short(p.hypothesis, 150)}\n  alternatives: ${(p.alternatives ?? []).length}\n  would be judged on: ${p.review_metric} ${p.review_window_text ?? ""}\n  citations: ${(p.citations ?? []).map((c) => `${c.ref ?? c.title}${c.url ? ` (${c.url})` : ""}`).join("; ") || "none attached"}`,
    );
    if (!p.citations?.length) warn("this strategy ships without a citation, so it must be read as an experiment");
    if (has("--decide")) {
      const decision = ["accept", "reject", "modify", "later"].includes(args[args.indexOf("--decide") + 1]) ? args[args.indexOf("--decide") + 1] : "accept";
      const body = { decision, note: decision === "modify" ? "two at a time, not four" : null, modifications: decision === "modify" ? { questions_per_session: 2 } : {} };
      const out = await call(`adaptations/${p.id}/decision`, { method: "POST", json: body });
      say(`Decision: ${out.decision}`, `  status ${out.status} · active adaptation ${out.active_adaptation_id ?? "none"}\n  review: ${out.review?.note ?? "(none)"}\n  note: ${out.student_note ? `the learner wrote: ${short(out.student_note, 120)}` : out.note ? short(out.note, 160) : "(no note)"}`);
      record("decision", { decision: out.decision, status: out.status, active_adaptation_id: out.active_adaptation_id ?? null });
      // and the 409 the UI has to handle: a decision is one-shot
      try {
        await call(`adaptations/${p.id}/decision`, { method: "POST", json: { decision: "accept" } });
        warn("re-answering a proposal was allowed — the gate should have refused with 409");
      } catch (err) {
        if (err.status === 409) say("Re-answered", `refused as it must be: ${short(err.detail, 120)}`);
        else throw err;
      }
    } else {
      say("Not answered", "pass --decide accept|reject|modify|later to answer it and watch the gate (and the 409 on a second answer).");
    }
  }
  const active = await call("adaptations/active");
  console.log(`  active adaptations: ${active.active.length}${active.active.length ? ` (${active.active.map((a) => a.strategy).join(", ")})` : " — nothing is applied without your yes"}`);

  // 5. one practice turn on the plan, to show the queue is data-driven and answer-free
  const plan = await call("/api/v1/practice?count=3");
  say("Practice queue", plan.items.length ? plan.items.map((e, i) => `  ${i + 1}. ${e.item.kind} ${short(e.item.stem, 60)}  [${e.reason}] ${e.utility !== undefined ? `u=${e.utility.toFixed(2)}` : ""}`).join("\n") : "  empty queue");
  const leak = JSON.stringify(plan).match(/"(answer|correct_index|answer_spec)"/);
  if (leak) warn(`the practice payload contains ${leak[1]} — an answer key leaked into a learner-facing queue`);
  else console.log("  no answer key anywhere in the payload (checked by string scan)");
  let practice = null;
  if (plan.items.length) {
    practice = await call("sessions", { method: "POST", json: { mode: "practice" } });
    const e = plan.items[0];
    const hint = await call(`items/${e.item.id}/hint`, { method: "POST", json: { hints_used: 0, item_id: e.item.id } });
    const ex = await call(`items/${e.item.id}/worked-example`, { method: "POST", json: { step: 1 } });
    const explained = await call("explain", { method: "POST", json: { question: "why do the parts have to be the same size first?", topic_id: e.topic_id, item_id: e.item.id } });
    say(
      "Help on request",
      `  hint 1/${hint.max_hints ?? "?"}: ${short(hint.hint, 120)}\n  example: ${ex.steps.length} step(s) — ${short(ex.steps[0]?.text, 90)}\n  explanation: provider=${explained.provider} grounded=${explained.meta?.grounded} sources=${explained.sources.length} sent_to_model=${explained.privacy?.sent_to_model}\n  next action: ${short(explained.next_action, 120)}`,
    );
    record("help", { hint_level: hint.hint_no, example_steps: ex.steps.length, provider: explained.provider, grounded: explained.meta?.grounded ?? null, sources: explained.sources.length });
    const answered = await call("attempts", { method: "POST", json: { session_id: practice.session_id, item_id: e.item.id, answer: await answerFor(e.item, false) } });
    console.log(`  attempted after help: ${answered.correct ? "right" : "wrong"} · ${short(answered.feedback, 100)}`);
    await call(`sessions/${practice.session_id}/finish`, { method: "POST", json: {} });
  }

  // 6. what the learner can see about themselves
  const review = await call("/api/v1/review/due");
  const journey = await call("/api/v1/journey?days=7&limit=40");
  const dash = await call("dashboard");
  say(
    "Review + journey",
    `  due: ${review.count} (${short(review.rule, 90)})\n  journey: ${journey.nodes.length} nodes · ${journey.summary}\n  guardrails: ${Object.entries(dash.guardrails).map(([k, v]) => `${k}=${v}`).join(", ")}\n  measured_outcomes: ${JSON.stringify(dash.measured_outcomes)}\n  open_questions: ${dash.open_questions.length ? dash.open_questions.map((q) => short(q.question ?? q, 80)).join(" | ") : "none"}`,
  );
  record("journey", { nodes: journey.nodes.length, due_reviews: review.count });

  // 7. privacy surface, as the settings screen reads it
  const allow = await call("events/allowlist");
  const privacy = await call("privacy");
  const rejected = await call(`sessions/${session.session_id}/events`, {
    method: "POST",
    json: { session_id: session.session_id, events: [{ type: "keystroke_log", payload: { everything: true } }] },
  }).catch((err) => err.detail ?? err.message);
  say(
    "Privacy",
    `  allowed event types: ${Object.keys(allow.allowed).length} · forbidden payload keys: ${allow.forbidden_keys.length}\n  rule: ${short(allow.rule, 140)}\n  never recorded: ${(privacy.telemetry.never_recorded ?? []).slice(0, 5).map((n) => short(n, 40)).join(" | ")}\n  storage: ${JSON.stringify(privacy.storage).slice(0, 160)}\n  unlisted event type -> ${short(rejected, 120)}`,
  );
  record("privacy", { event_types: Object.keys(allow.allowed).length, forbidden_keys: allow.forbidden_keys.length });

  if (has("--signout")) {
    await call("logout", { method: "POST", json: {} });
    try {
      await call("me");
      warn("GET /me still answered after logout — the session should be revoked");
    } catch (err) {
      say("Signed out", `the revoked session now answers ${err.status}, which is the point`);
    }
  }

  say("Done", `learner ${created.student_id} · ${OUT.steps.length} recorded steps${has("--decide") ? "" : " · no proposal was answered (pass --decide accept)"}`);
  if (OUT.warnings.length) console.log(`\x1b[33m${OUT.warnings.length} warning(s):\x1b[0m\n${OUT.warnings.map((w) => `  - ${w}`).join("\n")}`);
  else console.log("\x1b[32mno contract violations found\x1b[0m");
  if (has("--json")) console.log(`\n${JSON.stringify(OUT, null, 1)}`);
  console.log(`\nOpen the app against this API and sign in as ${email} / demo-passphrase-2026 to see the same data in the UI.`);
  process.exit(OUT.warnings.length && has("--strict") ? 1 : 0);
}

/** The learner-facing payloads carry no answer key, so the demo asks the same endpoint the
 *  UI's "Reveal it" button asks. That keeps the key off the wire everywhere else, and it
 *  means the demo is proving a real property: reveal works, and revealing is allowed.
 */
const keyCache = new Map();
async function reveal(item) {
  if (!keyCache.has(item.id)) {
    keyCache.set(
      item.id,
      await call(`items/${item.id}/reveal`).catch(() => null),
    );
  }
  return keyCache.get(item.id);
}

async function answerFor(item, wantWrong) {
  const rv = await reveal(item);
  if (item.kind === "mcq") {
    const right = rv?.correct_index ?? (item.choices ?? []).findIndex((c) => c.correct);
    if (!Number.isInteger(right) || right < 0) return 0;
    return wantWrong ? (right + 1) % Math.max((item.choices ?? []).length, 1) : right;
  }
  if (item.kind === "numeric") {
    if (wantWrong) return "0.999";
    return rv?.answer ?? rv?.value ?? null;
  }
  if (item.kind === "teachback")
    return wantWrong
      ? "you just do the top numbers"
      : "The denominator says how many equal parts the whole is cut into, so both fractions have to be cut the same way before the parts can be added.";
  return wantWrong ? "not sure" : "the parts have to be the same size before you can add them";
}

main().catch((err) => {
  console.error(`\n\x1b[31mdemo failed\x1b[0m ${err.message}`);
  if (err.detail) console.error(JSON.stringify(err.detail, null, 1).slice(0, 800));
  console.error("\nThe demo asserts nothing about the app's cleverness; a failure here means a contract changed under it, which is exactly what it is for.");
  process.exit(1);
});
