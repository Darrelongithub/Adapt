/** G14: populated learner data downloads through the real Settings control. */
import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium", "/tmp/chromium"].find((p) => p && existsSync(p));
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });

function keys(value) {
  if (Array.isArray(value)) return value.flatMap(keys);
  if (value && typeof value === "object") return Object.entries(value).flatMap(([key, child]) => [key, ...keys(child)]);
  return [];
}

try {
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const opening = page.locator(".opening-overlay");
  if (await opening.count()) {
    await opening.click({ position: { x: 10, y: 10 } });
    await opening.waitFor({ state: "detached", timeout: 5000 });
  }
  await page.getByRole("button", { name: /Save choice and continue/i }).click();
  await page.getByRole("button", { name: /Continue as guest/i }).click();
  await page.locator("[data-account-gate]").waitFor({ state: "detached", timeout: 15000 });

  const populated = await page.evaluate(async () => {
    const token = decodeURIComponent(document.cookie.split("; ").find(part => part.startsWith("adapt_csrf="))?.split("=").slice(1).join("=") ?? "");
    const call = async (path, method = "GET", body) => {
      const response = await fetch(path, {
        method, credentials: "same-origin",
        headers: { "content-type": "application/json", "x-csrf-token": token },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(`${path}: ${response.status} ${JSON.stringify(payload)}`);
      return payload;
    };
    const me = await call("/api/v1/me");
    await call("/api/v1/me/onboarding", "POST", { preferences: {}, consent_analytics: true, consent_share_with_llm: false });
    await call("/api/v1/journey/advance", "POST", { stage: "first_lesson", status: "done", note: "Included in my downloaded data." });
    const session = await call("/api/v1/sessions", "POST", { mode: "practice" });
    const diagnostic = await call("/api/v1/assessment/diagnostic");
    const item = diagnostic.items[0].item;
    const attempt = await call("/api/v1/attempts", "POST", { session_id: session.session_id, item_id: item.id, answer: "0" });
    const events = await call("/api/v1/events", "POST", { session_id: session.session_id, events: [{ type: "pause", payload: { ms: 1200 } }] });
    return { student_id: me.student_id, session_id: session.session_id, item_id: item.id, attempt_id: attempt.attempt_id, accepted_events: events.accepted };
  });
  assert.equal(populated.accepted_events, 1);

  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  const downloadPromise = page.waitForEvent("download", { timeout: 15000 });
  await page.getByRole("link", { name: "Download JSON" }).click();
  const download = await downloadPromise;
  assert.match(download.suggestedFilename(), /^adapt-export-stu_.*\.json$/);
  const path = await download.path();
  assert.ok(path);
  const raw = readFileSync(path, "utf8");
  const body = JSON.parse(raw);
  assert.equal(body.student.id, populated.student_id);
  assert.ok(body.records.learning_sessions.some(row => row.id === populated.session_id));
  assert.ok(body.records.attempts.some(row => row.id === populated.attempt_id && row.item_id === populated.item_id));
  assert.ok(body.records.journey_stages.some(row => row.note === "Included in my downloaded data."));
  assert.ok(body.records.learner_events.some(row => row.type === "pause"));
  const forbidden = ["password_hash", "csrf_token", "token_hash", "grading_seal", "email_index"];
  assert.deepEqual(keys(body).filter(key => forbidden.includes(key)), []);
  const cookies = await page.context().cookies();
  for (const cookie of cookies) assert.equal(raw.includes(cookie.value), false, `download exposed ${cookie.name}`);

  const unauthenticated = await fetch(`${BASE}/api/v1/me/export`);
  assert.equal(unauthenticated.status, 401);
  const output = {
    check: "G14", outcome: "PASS", filename: download.suggestedFilename(), student_id: populated.student_id,
    attempt_id: populated.attempt_id, session_id: populated.session_id,
    exported_counts: Object.fromEntries(["learning_sessions", "attempts", "learner_events", "journey_stages", "student_preferences", "auth_sessions"].map(table => [table, body.records[table]?.length ?? 0])),
    reflection_exported: true, forbidden_keys_absent: forbidden, cookie_values_absent: true, unauthenticated_status: unauthenticated.status,
  };
  if (process.env.AUDIT_EVIDENCE) writeFileSync(process.env.AUDIT_EVIDENCE, JSON.stringify(output, null, 2) + "\n");
  console.log(JSON.stringify(output, null, 2));
} finally {
  await browser.close();
}
