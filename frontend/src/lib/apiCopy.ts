/** The sentence a refusal puts on screen.
 *
 * `ApiError.shown` used to hold this logic inline, which made the most learner-visible string
 * in the app the one part of `api.ts` that could not be tested without a bundler, a browser and
 * a backend (the module imports React and an extensionless `./mock`). It lives here instead —
 * the same reason `learnerCopy.ts` exists — so the rule can be tested in node, and so there is
 * one place to look when a refusal reads wrong.
 *
 * The rule, in order:
 *
 * 1. the backend's prose (`note`, `hint`, `detail`, `next`, `message`) wins over the machine code — a code
 *    is for a client to switch on, not for a learner to read. Reading only `error` is how a
 *    screen showed `model_budget_exhausted` while the sentence that teaching continues locally
 *    sat unused in `note`;
 * 2. a "not yet" answer carries its wait time, because the helpful next action is to wait that
 *    long, and a code without a number invites an immediate retry;
 * 3. the code alone is the last resort, then a truncated dump so something is always shown.
 */

/** Codes where the answer is "not yet" — the wait time is part of the sentence. */
export const PACING_CODES = [
  "rate_limited",
  "too_many_attempts",
  "too_many_concurrent",
  "model_budget_exhausted",
] as const;

export function apiErrorSentence(status: number, fallback: string, detail?: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    // `message` is in the list because the pacing refusals (the IP bucket and the in-flight
    // guard) write their sentence there: without it a learner saw the bare code
    // `rate_limited` while the explanation of what to do sat one key away.
    const prose = ["note", "hint", "detail", "next", "message"]
      .map((k) => (typeof d[k] === "string" ? (d[k] as string) : ""))
      .find((v) => v.length > 0);
    const code = typeof d.error === "string" ? d.error : "";
    const wait =
      typeof d.retry_after_s === "number" && d.retry_after_s > 0
        ? ` (about ${Math.ceil(d.retry_after_s)}s)`
        : "";
    if (prose) return `${prose}${(PACING_CODES as readonly string[]).includes(code) ? wait : ""}`;
    if (code) return wait ? `${code}${wait}` : code;
    return JSON.stringify(d).slice(0, 220);
  }
  if (fallback) return fallback;
  return `request failed (${status})`;
}
