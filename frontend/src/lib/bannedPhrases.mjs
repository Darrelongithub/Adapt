/** Phrases that must not appear in learner-facing text.
 *
 * The crawl fails on a case-insensitive substring, on every route, signed out and signed
 * in. There are no exemptions left: the onboarding screen used to carry three sentences
 * that named the machinery, and Phase D replaced that copy (twice over — the panel and
 * the consent line), so a route-specific allowance would now only be a hole a regression
 * could walk through. `learnerCopy.test.mjs` asserts that the exemption is gone.
 */

export const BANNED = [
  { id: "api-key", re: /API key/i },
  { id: "gemini", re: /Gemini/i },
  { id: "provider", re: /provider/i },
  { id: "model", re: /model/i },
  { id: "endpoint", re: /endpoint/i },
  { id: "get-path", re: /GET \//i },
  { id: "post-path", re: /POST \//i },
  { id: "uvicorn", re: /uvicorn/i },
  { id: "ignored-keys", re: /ignored_keys/i },
  { id: "ai-1", re: /AI 1/i },
  { id: "ai-2", re: /AI 2/i },
  { id: "ai-3", re: /AI 3/i },
  { id: "token", re: /token/i },
];

/** Banned-phrase ids present in `text`. `route` is kept for call sites that still pass it. */
export function bannedHits(text, route = "") {
  void route; // every route is held to the same rule now
  return BANNED.filter((item) => item.re.test(String(text || ""))).map((item) => item.id);
}
