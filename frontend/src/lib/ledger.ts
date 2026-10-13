/** The session ledger: what this browser has decided, newest first, still on screen after the
 *  announcement has been and gone.
 *
 * `announce()` says a decision once, to a screen reader, and then it is gone. The design
 * snapshot keeps a running list beside it ("Decisions made this session"), and the Changes
 * screen showed only the most recent one — so a learner who had answered three proposals and
 * then wanted to check what they already said had nothing on screen to read.
 *
 * It is `sessionStorage` rather than component state because the heading says *this session*:
 * in a multi-route app a component's state is per page load, and one navigation would empty a
 * log that claims to hold the session. The durable record is the server's own
 * `/api/v1/adaptations/history`; this is the readable one, and it is capped so a long session
 * cannot grow it without bound.
 *
 * Storage that refuses — private mode, a quota, a browser that blocks it — leaves the ledger
 * empty. That is a missing convenience and not an error, so nothing in here throws.
 */

/** Enough to read back a session, short enough that a long one cannot fill the quota. */
export const LEDGER_LIMIT = 40;

/** The slice of `Storage` this uses, so a test can hand it a throwing one. */
export type LedgerStore = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

/** Newest first, capped, and an empty entry is not an entry. */
export function pushEntry(previous: readonly string[], entry: string, limit: number = LEDGER_LIMIT): string[] {
  const kept = (previous ?? []).filter((x): x is string => typeof x === "string");
  const text = String(entry ?? "").trim();
  if (!text) return kept;
  return [text, ...kept].slice(0, Math.max(1, Math.floor(limit)));
}

/** What is stored, or nothing. Corrupt or refused storage reads as an empty ledger. */
export function readLedger(store: LedgerStore | null | undefined, key: string): string[] {
  if (!store) return [];
  let raw: string | null = null;
  try {
    raw = store.getItem(key);
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function writeLedger(store: LedgerStore | null | undefined, key: string, entries: readonly string[]): void {
  if (!store) return;
  try {
    store.setItem(key, JSON.stringify(entries));
  } catch {
    /* the ledger is a convenience; losing it must never lose the decision, which is on the server */
  }
}
