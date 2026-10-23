/** F04: notes and flashcards save, reload, reveal, export and delete for real. */
import assert from "node:assert/strict";
import { existsSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.ADAPT_BASE_URL ?? "http://127.0.0.1:3000";
const suffix = Math.random().toString(36).slice(2, 10);
const note = `F04 note ${suffix}: compare the control and experimental groups.`;
const front = `F04 card ${suffix}: what is the independent variable?`;
const answer = "The variable the experimenter deliberately changes.";
const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, process.env.PW_CHROMIUM, "/opt/pw-browsers/chromium", "/tmp/chromium"].find((p) => p && existsSync(p));
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

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
  await page.goto(`${BASE}/tools`, { waitUntil: "networkidle" });

  await page.getByRole("textbox", { name: "Note", exact: true }).fill(note);
  await page.getByRole("button", { name: "Save note" }).click();
  await page.getByText(note, { exact: true }).waitFor({ timeout: 10000 });
  await page.getByRole("textbox", { name: "Flashcard front" }).fill(front);
  await page.getByRole("textbox", { name: "Flashcard answer" }).fill(answer);
  await page.getByRole("button", { name: "Save flashcard" }).click();
  await page.getByText(front, { exact: true }).waitFor({ timeout: 10000 });

  await page.reload({ waitUntil: "networkidle" });
  await page.getByText(note, { exact: true }).waitFor();
  await page.getByText(front, { exact: true }).click();
  await page.getByLabel("Flashcard revealed answer").waitFor({ state: "visible" });
  assert.equal(await page.getByLabel("Flashcard revealed answer").textContent(), answer);

  const beforeDelete = await page.evaluate(async () => {
    const [itemsResponse, exportResponse] = await Promise.all([fetch("/api/v1/study/items"), fetch("/api/v1/me/export")]);
    return { items: await itemsResponse.json(), exported: await exportResponse.json() };
  });
  const noteRow = beforeDelete.items.notes.find(row => row.text === note);
  const cardRow = beforeDelete.items.cards.find(row => row.text === front);
  assert.ok(noteRow && cardRow);
  assert.ok(beforeDelete.exported.records.study_items.some(row => row.id === noteRow.id && row.text === note));
  assert.ok(beforeDelete.exported.records.study_items.some(row => row.id === cardRow.id && row.answer === answer));

  await page.getByRole("button", { name: `Delete note: ${note}`, exact: true }).click();
  await page.getByText("Note deleted.", { exact: true }).waitFor();
  await page.reload({ waitUntil: "networkidle" });
  await page.getByText(front, { exact: true }).waitFor();
  assert.equal(await page.getByText(note, { exact: true }).count(), 0);
  const afterDelete = await page.evaluate(async () => (await (await fetch("/api/v1/study/items")).json()));
  assert.equal(afterDelete.notes.length, 0);
  assert.equal(afterDelete.cards.length, 1);
  await page.getByRole("textbox", { name: "Note", exact: true }).fill("   ");
  assert.equal(await page.getByRole("button", { name: "Save note" }).isDisabled(), true);

  const output = {
    check: "F04", outcome: "PASS", note_id: noteRow.id, card_id: cardRow.id,
    note_saved_and_restored: true, card_saved_and_restored: true, exact_answer_revealed: true,
    both_present_in_export: true, note_deleted_and_absent_after_reload: true, blank_save_disabled: true,
  };
  if (process.env.AUDIT_EVIDENCE) writeFileSync(process.env.AUDIT_EVIDENCE, JSON.stringify(output, null, 2) + "\n");
  console.log(JSON.stringify(output, null, 2));
} finally {
  await browser.close();
}
