/**
 * What the server recorded against what the learner did. Two comparisons:
 *
 * 1. Trial by trial: the verdict the server returned for each POST, against the answer this suite
 *    knows to be right (where it knows one), and the choice it sent (what it sent is what it stored).
 * 2. Snapshot: the numbers the snapshot reports, against the numbers computed from the ledger with the
 *    same rules. A metric that reads the wrong field would show here as zero.
 */
import { expect, type Page } from "@playwright/test";
import { FORBIDDEN_LABELS, type Ledger } from "./driver";

type Verdict = { correct?: boolean | null; given?: unknown; duplicate?: boolean; scored?: Record<string, unknown> };

/** Trial by trial. Every POST the page made is in `ledger.responses`, in order, and has one `expect` entry. */
export function checkVerdicts(ledger: Ledger): void {
  expect(ledger.responses.length, "every trial the page sent should have a server reply").toBe(ledger.expect.length);
  ledger.expect.forEach((want, i) => {
    const reply = ledger.responses[i];
    expect(reply.status, `trial ${i} (${want.game}) was refused`).toBe(200);
    const body = reply.body as Verdict;
    expect(body.duplicate, `trial ${i} (${want.game}) was recorded as a duplicate`).not.toBe(true);
    const scored = body.scored ?? {};
    if (want.correct !== undefined && want.correct !== null) {
      expect(scored.correct, `trial ${i} (${want.game}): the server's verdict; sent ${JSON.stringify(want.given)}, server said ${JSON.stringify(scored)}`).toBe(want.correct);
    }
    if (want.game === "quick" || want.game === "trap") {
      expect(scored.given, `trial ${i} (${want.game}): the server stored a different choice`).toBe(want.given);
    }
    if (want.game === "codewords_delayed_predict") {
      expect(scored.predicted, `trial ${i}: the server stored a different prediction`).toBe(want.given);
    }
    if (want.game === "machine-probe") {
      expect(scored.value, `trial ${i}: the server stored a different probe`).toBe(want.given);
    }
  });
}

type Factor = { key: string; state: string; n: number; detail?: Record<string, unknown>; range?: number[] | null };

async function snapshotJson(page: Page): Promise<{ factors: Factor[]; completion?: unknown }> {
  const res = await page.request.get("/api/v1/baseline/snapshot");
  expect(res.status()).toBe(200);
  return (await res.json()) as { factors: Factor[] };
}

function near(actual: unknown, expected: number, what: string): void {
  expect(typeof actual, `${what} should be a number, got ${JSON.stringify(actual)}`).toBe("number");
  expect(Math.abs((actual as number) - expected), `${what}: page ${actual}, expected ${expected}`).toBeLessThan(0.01);
}

/** The snapshot's numbers, against the ledger. */
export async function checkSnapshotNumbers(page: Page, ledger: Ledger): Promise<void> {
  const { factors } = await snapshotJson(page);
  const by = Object.fromEntries(factors.map((f) => [f.key, f]));

  const ltm = by.long_term_memory;
  expect(ltm.state, "long-term memory should be measured after a full sitting").toBe("measured");
  const cuedRight = ledger.cued.filter((c) => c.correct).length / ledger.cued.length;
  near(ltm.detail?.immediate, cuedRight, "long_term_memory.immediate (cued recall, straight away)");
  const recogRight = ledger.recognition.filter((c) => c.correct).length / ledger.recognition.length;
  near(ltm.detail?.recognition, recogRight, "long_term_memory.recognition");
  // Every code word was typed back, so free recall after the delay is complete.
  near(ltm.detail?.delayed_free, ledger.freeCodes.length === 8 ? 1 : 0, "long_term_memory.delayed_free");
  if (ledger.nextDay.length) {
    const nextRight = ledger.nextDay.filter((c) => c.correct).length / ledger.nextDay.length;
    near(ltm.detail?.next_day, nextRight, "long_term_memory.next_day (the codeword check, asked later)");
  }

  const reasoning = by.reasoning;
  expect(reasoning.state).toBe("measured");
  near(reasoning.detail?.falsifying_rate, ledger.falsifyingExpected / ledger.probes.length, "reasoning.falsifying_rate");

  const persistence = by.persistence_help_seeking;
  expect(persistence.state).toBe("measured");
  expect(persistence.detail?.hints_used, "hints bought").toBe(ledger.lockedHints.length);
  expect(persistence.detail?.hint_cost, "coins spent on hints").toBe(ledger.lockedHints.length * 1);
  expect(persistence.detail?.wrong_attempts, "wrong attempts at the box").toBe(ledger.lockedAttempts);
  expect(persistence.detail?.walked_away, "the learner walked away from the box").toBe(true);
}

/** The snapshot shows ranges with an n, never a label, and says "not measured" rather than a number. */
export async function checkSnapshotHonesty(page: Page): Promise<void> {
  const body = (await page.locator(".baseline-snapshot").innerText()).replace(/\s+/g, " ");
  expect(body, "the snapshot names a learner type or a diagnosis").not.toMatch(FORBIDDEN_LABELS);
  const rows = page.locator("ul.baseline-factors > li");
  const count = await rows.count();
  expect(count, "every factor is listed, measured or not").toBe(8);
  for (let i = 0; i < count; i++) {
    const row = rows.nth(i);
    const text = (await row.innerText()).replace(/\s+/g, " ");
    if (/not measured/i.test(text)) {
      expect(text, "a factor that was not measured must not show an n").not.toMatch(/n=\d/);
      expect(text, "a factor that was not measured must not show a range").not.toMatch(/\d\s*[–-]\s*\d/);
    } else {
      expect(text, "a measured factor shows its n").toMatch(/n=\d+/);
      expect(text, "a measured factor shows a range").toMatch(/\d(\.\d+)?\s*[–-]\s*\d/);
    }
  }
}

/** A quick reader for the page-visible numbers, used by the screenshot notes. */
export async function visibleFactorTitles(page: Page): Promise<string[]> {
  return page.locator("ul.baseline-factors > li strong").allInnerTexts();
}
